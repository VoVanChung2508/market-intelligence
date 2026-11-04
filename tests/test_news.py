import asyncio

import httpx
import pytest

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
    parse_news_feed,
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
                listing = await client.get(
                    "/api/v1/news",
                    params={"q": "quarterly", "asset": "aapl", "category": "earnings"},
                )
                missing = await client.get("/api/v1/news/999")
                invalid_category = await client.get(
                    "/api/v1/news",
                    params={"category": "untrusted"},
                )
                detail = await client.get(f"/api/v1/news/{listing.json()['items'][0]['id']}")
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
