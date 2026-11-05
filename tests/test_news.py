import asyncio

import httpx
import pytest

from backend.auth import create_user, list_users
from backend.api import create_app
from backend.database import (
    get_news_articles,
    initialize_database,
    store_news_articles,
)
from backend.news import (
    NewsFeed,
    RSSAtomProvider,
    collect_news,
    configured_news_feeds,
    format_news_telegram_message,
    parse_news_feed,
    send_news_telegram_notification,
)


RSS_DOCUMENT = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Apple reports quarterly results</title>
      <link>https://news.example/story?utm_source=feed</link>
      <description><![CDATA[<p>AAPL revenue increased.<script>hidden text</script></p>]]></description>
      <pubDate>Tue, 03 Nov 2026 10:30:00 -0500</pubDate>
    </item>
  </channel>
</rss>
"""

TEST_PASSWORD = "market-intelligence-test-password"

ATOM_DOCUMENT = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en">
  <entry>
    <title>Central bank holds interest rate</title>
    <link href="/policy" />
    <summary>The central bank announced its rate decision.</summary>
    <content type="html">&lt;p&gt;Policy statement&lt;/p&gt;</content>
    <updated>2026-11-04T10:00:00+02:00</updated>
  </entry>
</feed>
"""


def test_rss_parser_normalizes_fields_and_detects_assets():
    articles = parse_news_feed(
        RSS_DOCUMENT,
        NewsFeed("Research Wire", "https://news.example/feed.xml"),
        {"AAPL": "Apple Inc."},
    )

    assert len(articles) == 1
    article = articles[0]
    assert article["canonical_url"] == "https://news.example/story"
    assert article["published_at"] == "2026-11-03T15:30:00+00:00"
    assert article["category"] == "earnings"
    assert article["related_assets"] == ["AAPL"]
    assert "hidden text" not in article["summary"]


def test_atom_parser_extracts_content_language_and_utc_date():
    articles = parse_news_feed(
        ATOM_DOCUMENT,
        NewsFeed("Policy Desk", "https://macro.example/feed.xml"),
        {},
    )

    assert articles[0]["canonical_url"] == "https://macro.example/policy"
    assert articles[0]["category"] == "monetary_policy"
    assert articles[0]["content"] == "Policy statement"
    assert articles[0]["language"] == "en"
    assert articles[0]["published_at"] == "2026-11-04T08:00:00+00:00"


def test_feed_config_requires_https_and_unique_source_names():
    assert configured_news_feeds(
        '[{"name":"Wire","url":"https://news.example/rss"}]'
    ) == [NewsFeed("Wire", "https://news.example/rss")]
    with pytest.raises(ValueError, match="HTTPS"):
        configured_news_feeds('[{"name":"Wire","url":"http://news.example/rss"}]')
    with pytest.raises(ValueError, match="unique"):
        configured_news_feeds(
            '[{"name":"Wire","url":"https://a.example/rss"},'
            '{"name":"wire","url":"https://b.example/rss"}]'
        )
    with pytest.raises(ValueError, match="publicly routable"):
        configured_news_feeds('[{"name":"Local","url":"https://127.0.0.1/feed"}]')


def test_rss_provider_uses_bounded_non_redirecting_requests():
    class FakeResponse:
        status_code = 200
        headers = {"Content-Length": "4"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size == 8192
            yield b"feed"

        def close(self):
            self.closed = True

    class FakeSession:
        response = FakeResponse()
        kwargs = None

        def get(self, url, **kwargs):
            self.kwargs = kwargs
            return self.response

    session = FakeSession()
    result = RSSAtomProvider().fetch(
        NewsFeed("Wire", "https://news.example/feed"),
        session,
    )
    assert result == b"feed"
    assert session.kwargs["allow_redirects"] is False
    assert session.kwargs["timeout"] == (3, 10)
    assert session.response.closed is True


def test_rss_parser_rejects_document_type_declarations():
    with pytest.raises(ValueError, match="not accepted"):
        parse_news_feed(
            b'<!DOCTYPE rss [<!ENTITY x "x">]><rss><channel/></rss>',
            NewsFeed("Wire", "https://news.example/feed"),
            {},
        )


def test_news_storage_deduplicates_canonical_urls_and_same_day_titles(tmp_path):
    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)
    first = parse_news_feed(
        RSS_DOCUMENT,
        NewsFeed("Source One", "https://news.example/feed.xml"),
        {"AAPL": "Apple Inc."},
    )
    second_document = RSS_DOCUMENT.replace(
        b"https://news.example/story?utm_source=feed",
        b"https://different.example/another-story",
    ).replace(b"AAPL revenue increased.", b"AAPL revenue grew.")
    second = parse_news_feed(
        second_document,
        NewsFeed("Source Two", "https://other.example/feed.xml"),
        {"AAPL": "Apple Inc."},
    )

    assert store_news_articles(db_path, first) == 1
    canonical_duplicate = parse_news_feed(
        RSS_DOCUMENT,
        NewsFeed("Duplicate Feed", "https://different.example/rss"),
        {"AAPL": "Apple Inc."},
    )
    assert store_news_articles(db_path, canonical_duplicate) == 0
    assert store_news_articles(db_path, second) == 0
    articles, count = get_news_articles(db_path, asset="AAPL")
    assert count == 1
    assert articles[0]["related_assets"] == ["AAPL"]


def test_collector_isolates_failed_feeds_and_keeps_successful_articles(tmp_path):
    class FakeProvider:
        def fetch(self, feed, _session):
            if feed.name == "Broken source":
                raise ValueError("invalid test response")
            return RSS_DOCUMENT

    db_path = tmp_path / "market.sqlite3"
    result = collect_news(
        [
            NewsFeed("Broken source", "https://broken.example/feed"),
            NewsFeed("Working source", "https://news.example/feed"),
        ],
        db_path=db_path,
        provider=FakeProvider(),
    )

    assert result == {
        "sources_configured": 2,
        "sources_succeeded": 1,
        "sources_failed": 1,
        "articles_received": 1,
        "articles_inserted": 1,
    }
    articles, count = get_news_articles(db_path)
    assert count == 1
    assert articles[0]["source"] == "Working source"


def test_news_api_search_asset_category_and_detail(tmp_path):
    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)
    article = parse_news_feed(
        RSS_DOCUMENT,
        NewsFeed("Research Wire", "https://news.example/feed?api_key=never-return-this"),
        {"AAPL": "Apple Inc."},
    )
    store_news_articles(db_path, article)
    app = create_app(db_path, tmp_path / "raw", tmp_path / "models")

    async def request():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                if not list_users(app.state.db_path):
                    create_user(
                        app.state.db_path,
                        "test-admin",
                        TEST_PASSWORD,
                        "ADMIN",
                    )
                login = await client.post(
                    "/api/auth/login",
                    json={"username": "test-admin", "password": TEST_PASSWORD},
                )
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                listing = await client.get(
                    "/api/v1/news",
                    params={"q": "quarterly", "asset": "aapl", "category": "earnings"},
                    headers=headers,
                )
                missing = await client.get("/api/v1/news/999", headers=headers)
                invalid_category = await client.get(
                    "/api/v1/news",
                    params={"category": "untrusted"},
                    headers=headers,
                )
                detail = await client.get(
                    f"/api/v1/news/{listing.json()['items'][0]['id']}",
                    headers=headers,
                )
                return listing, missing, invalid_category, detail

    listing, missing, invalid_category, detail = asyncio.run(request())
    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    assert listing.json()["items"][0]["related_assets"] == ["AAPL"]
    assert "source_url" not in listing.json()["items"][0]
    assert "never-return-this" not in listing.text
    assert detail.status_code == 200
    assert detail.json()["title"] == "Apple reports quarterly results"
    assert missing.status_code == 404
    assert invalid_category.status_code == 422


def test_news_telegram_notification_formatting_and_matching(monkeypatch, capsys):
    from types import SimpleNamespace
    import requests

    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-news-bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100987654321")

    captured = {}

    def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json", {})
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"ok": True},
        )

    monkeypatch.setattr(requests.Session, "post", fake_post)

    matched_article = {
        "title": "Fed rate cut & <policy> shifts",
        "source": "Macro Wire",
        "category": "monetary_policy",
        "related_assets": ["BTC", "ETH"],
        "summary": "Policy statement with <markup> text",
        "canonical_url": "https://macro.example/rate-cut",
    }

    ok, err = send_news_telegram_notification(matched_article)
    assert ok is True
    assert err is None
    assert "secret-news-bot-token" in captured["url"]
    assert captured["json"]["chat_id"] == "-100987654321"
    assert captured["json"]["parse_mode"] == "HTML"
    text = captured["json"]["text"]
    assert "&lt;policy&gt;" in text
    assert "Macro Wire" in text
    assert "monetary_policy" in text
    assert "BTC, ETH" in text
    assert "https://macro.example/rate-cut" in text
    # Verify no credentials in stdout/stderr
    assert "secret-news-bot-token" not in capsys.readouterr().out

    # Article without matched assets must be skipped
    unmatched_article = {
        "title": "General news without tracked ticker",
        "source": "General Wire",
        "category": "other",
        "related_assets": [],
        "canonical_url": "https://general.example/news",
    }
    ok, err = send_news_telegram_notification(unmatched_article)
    assert ok is False
    assert err == "no_matched_assets"

    # When TELEGRAM_ENABLED is false
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    ok, err = send_news_telegram_notification(matched_article)
    assert ok is False
    assert err == "disabled"


def test_collect_news_sends_telegram_for_new_matched_articles_only(tmp_path, monkeypatch):
    import backend.news as news_module

    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)

    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_bot_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake_chat_id")

    notified_articles = []
    monkeypatch.setattr(
        news_module,
        "send_news_telegram_notification",
        lambda art: notified_articles.append(art) or (True, None),
    )

    class FakeProvider:
        def fetch(self, feed, _session):
            return RSS_DOCUMENT

    feeds = [NewsFeed("Wire", "https://news.example/feed.xml")]

    # First run: RSS_DOCUMENT has "Apple" matching AAPL in seed assets
    result1 = collect_news(feeds, db_path=db_path, provider=FakeProvider())
    assert result1["articles_inserted"] == 1
    assert len(notified_articles) == 1
    assert notified_articles[0]["related_assets"] == ["AAPL"]

    # Second run with exact same feed: duplicate articles must not trigger notification
    result2 = collect_news(feeds, db_path=db_path, provider=FakeProvider())
    assert result2["articles_inserted"] == 0
    assert len(notified_articles) == 1  # Still 1, no duplicate notification
