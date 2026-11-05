import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
import requests

from backend.auth import create_user
from backend.api import create_app
from backend.database import connect, initialize_database
from backend.social import (
    SocialSource,
    XApiProvider,
    analyze_social_post,
    build_search_query,
    collect_social_once,
    create_social_source,
    get_collection_status,
    get_notification_history,
    get_social_alerts,
    get_social_events,
    get_social_posts,
    retry_failed_deliveries,
    send_telegram_notification,
)

TEST_PASSWORD = "market-intelligence-test-password"


def _x_payload(post_id: str, text: str, *, username: str = "crypto_news") -> dict:
    return {
        "data": [
            {
                "id": post_id,
                "author_id": "author-1",
                "text": text,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "lang": "en",
                "public_metrics": {"like_count": 1},
            }
        ],
        "includes": {
            "users": [
                {
                    "id": "author-1",
                    "username": username,
                    "name": "Crypto News",
                    "verified": True,
                }
            ]
        },
    }


class PaginatedProvider:
    def __init__(self):
        self.calls = []

    def search(self, query, *, since_id, pagination_token):
        self.calls.append((query, since_id, pagination_token))
        if pagination_token is None:
            return {
                **_x_payload("100", "Coinbase will list BTC today. Confirmed exchange listing."),
                "meta": {"next_token": "page-2"},
            }
        return {
            **_x_payload("101", "Bitcoin network upgrade announcement for BTC."),
            "meta": {},
        }


def test_search_query_uses_watchlist_symbols_without_fixed_universe():
    query = build_search_query(
        [
            {"symbol": "BTC", "name": "Bitcoin"},
            {"symbol": "SOL", "name": "Solana"},
            {"symbol": "XYZ", "name": "Example Coin"},
        ]
    )
    assert "$BTC" in query
    assert '"Solana"' in query
    assert "$XYZ" in query
    assert "-is:retweet" in query


def test_rule_engine_marks_rumors_and_whale_posts_unverified():
    assets = [{"symbol": "BTC", "name": "Bitcoin"}]
    rumor = analyze_social_post(
        "Rumored Coinbase will list BTC, but this is unconfirmed.",
        datetime.now(timezone.utc).isoformat(),
        assets,
    )
    whale = analyze_social_post(
        "A whale moved a large transfer of BTC.",
        datetime.now(timezone.utc).isoformat(),
        assets,
    )

    assert rumor is not None
    assert rumor["events"][0]["category"] == "EXCHANGE_LISTING"
    assert rumor["events"][0]["unverified_claim"] is True
    assert rumor["events"][0]["priority"] != "critical"
    assert whale is not None
    assert whale["events"][0]["category"] == "WHALE_ACTIVITY"
    assert whale["events"][0]["unverified_claim"] is True
    assert whale["ai_status"] == "not_configured"
    assert whale["summary"] is None


def test_social_collection_paginates_deduplicates_and_persists_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("X_API_ENABLED", "true")
    monkeypatch.setenv("X_API_BEARER_TOKEN", "")
    monkeypatch.setenv("NTFY_ENABLED", "false")
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    db_path = tmp_path / "market.sqlite3"
    provider = PaginatedProvider()

    first = collect_social_once(db_path, provider, enabled=True, max_pages=1)
    state_after_first = get_collection_status(db_path)
    second_provider = PaginatedProvider()
    second = collect_social_once(db_path, second_provider, enabled=True, max_pages=1)
    duplicate = collect_social_once(db_path, PaginatedProvider(), enabled=True, max_pages=1)

    assert first["posts_inserted"] == 1
    assert first["next_page_available"] is True
    assert state_after_first["status"] == "success"
    assert state_after_first["last_success_at"] is not None
    assert second_provider.calls[0][1:] == (None, "page-2")
    assert second["posts_inserted"] == 1
    assert second["next_page_available"] is False
    assert duplicate["posts_inserted"] == 0

    posts, post_count = get_social_posts(db_path, asset="BTC", limit=10)
    unknown_source_posts, unknown_source_count = get_social_posts(
        db_path,
        source_type="unknown",
        limit=10,
    )
    events, event_count = get_social_events(db_path, asset="BTC", limit=10)
    alerts, alert_count = get_social_alerts(db_path)
    assert post_count == 2
    assert event_count == 2
    assert all("BTC" in post["assets"] for post in posts)
    assert unknown_source_count == 2
    assert all(post["verification_status"] == "unverified" for post in unknown_source_posts)
    assert events[0]["category"] == "NETWORK_UPGRADE"
    assert {delivery["status"] for delivery in alerts[0]["deliveries"]} <= {
        "disabled",
        "not_configured",
    }
    assert alert_count >= 1
    deliveries, delivery_count = get_notification_history(db_path)
    assert delivery_count >= 2
    assert len(provider.calls) == 1


def test_x_collection_reports_disabled_and_not_configured(tmp_path, monkeypatch):
    db_path = tmp_path / "market.sqlite3"
    monkeypatch.setenv("X_API_ENABLED", "false")
    disabled = collect_social_once(db_path)
    assert disabled["status"] == "disabled"

    monkeypatch.setenv("X_API_ENABLED", "true")
    monkeypatch.setenv("X_API_BEARER_TOKEN", "")
    missing = collect_social_once(db_path)
    assert missing["status"] == "not_configured"
    assert get_collection_status(db_path)["configured"] is False


def test_x_api_adapter_uses_bearer_auth_and_pagination():
    class FakeSession:
        called = None

        def get(self, url, **kwargs):
            self.called = (url, kwargs)
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"data": [], "meta": {}},
            )

    session = FakeSession()
    provider = XApiProvider("test-bearer", session)
    result = provider.search(
        "(BTC OR $BTC) -is:retweet",
        since_id="10",
        pagination_token="next",
    )

    url, options = session.called
    assert result["meta"] == {}
    assert url.startswith("https://api.x.com/2/")
    assert options["headers"]["Authorization"] == "Bearer test-bearer"
    assert options["params"]["since_id"] == "10"
    assert options["params"]["next_token"] == "next"
    assert options["timeout"] == (5, 20)


def test_telegram_sender_escapes_html_and_never_logs_token(monkeypatch, capsys):
    import backend.social as social_module

    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-1")
    captured = {}

    def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"ok": True},
        )

    monkeypatch.setattr(requests.Session, "post", fake_post)
    event = {
        "event_key": "test",
        "symbol": "BTC",
        "category": "GENERAL_CRYPTO_NEWS",
        "sentiment": "neutral",
        "priority": "low",
        "priority_score": 30,
        "unverified_claim": True,
        "username": "sample",
        "text": "Potential <hack> & rumor",
        "post_url": "https://x.com/sample/status/1",
    }

    assert send_telegram_notification(event) == (True, None)
    assert "secret-token" in captured["url"]
    assert "&lt;hack&gt; &amp;" in captured["json"]["text"]
    assert captured["json"]["parse_mode"] == "HTML"
    assert "secret-token" not in capsys.readouterr().out
    assert social_module.SOCIAL_PRIORITIES == ("critical", "high", "medium", "low")


def test_notification_channels_fail_independently(tmp_path, monkeypatch):
    import backend.social as social_module

    monkeypatch.setenv("X_API_ENABLED", "true")
    monkeypatch.setenv("NTFY_ENABLED", "true")
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("SOCIAL_ALERT_MIN_SCORE", "0")
    monkeypatch.setattr(
        social_module,
        "send_notification",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectionError()),
    )
    monkeypatch.setattr(
        social_module,
        "send_telegram_notification",
        lambda _event: (True, None),
    )
    db_path = tmp_path / "market.sqlite3"
    provider = PaginatedProvider()

    collect_social_once(db_path, provider, enabled=True, max_pages=1)
    deliveries, _count = get_notification_history(db_path)

    assert {item["status"] for item in deliveries} == {"failed", "sent"}
    alerts, _count = get_social_alerts(db_path)
    assert alerts[0]["status"] == "partial"

    connection = connect(db_path)
    try:
        connection.execute(
            "UPDATE notification_deliveries SET updated_at = '2000-01-01T00:00:00+00:00' "
            "WHERE channel = 'ntfy' AND status = 'failed'"
        )
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(social_module, "send_notification", lambda *args, **kwargs: True)

    assert retry_failed_deliveries(db_path) == 1
    deliveries, _count = get_notification_history(db_path)
    ntfy_delivery = next(item for item in deliveries if item["channel"] == "ntfy")
    assert ntfy_delivery["status"] == "sent"
    assert ntfy_delivery["attempts"] == 2


def test_social_source_and_alert_rule_api_validation(tmp_path):
    app = create_app(
        tmp_path / "market.sqlite3",
        tmp_path / "raw",
        tmp_path / "models",
    )

    async def request():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
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
                source = await client.post(
                    "/api/social/sources",
                    headers=headers,
                    json={
                        "account_id": "x-account-1",
                        "username": "official_project",
                        "display_name": "Official Project",
                        "category": "official_project",
                        "verified": True,
                        "reputation_score": 70,
                        "supported_assets": ["BTC"],
                    },
                )
                duplicate = await client.post(
                    "/api/social/sources",
                    headers=headers,
                    json={
                        "account_id": "x-account-1",
                        "username": "official_project",
                        "display_name": "Official Project",
                    },
                )
                patched = await client.patch(
                    f"/api/social/sources/{source.json()['id']}",
                    headers=headers,
                    json={"reputation_score": 80},
                )
                rule = await client.post(
                    "/api/social/alert-rules",
                    headers=headers,
                    json={
                        "name": "btc-security",
                        "assets": ["BTC"],
                        "categories": ["EXPLOIT"],
                        "quiet_hours_start": "22:00",
                        "quiet_hours_end": "06:00",
                        "timezone": "UTC",
                    },
                )
                invalid_rule = await client.post(
                    "/api/social/alert-rules",
                    headers=headers,
                    json={"name": "invalid", "categories": ["NOT_A_CATEGORY"]},
                )
                source_list = await client.get("/api/social/sources", headers=headers)
                health = await client.get("/api/social/health", headers=headers)
                return source, duplicate, patched, rule, invalid_rule, source_list, health

    source, duplicate, patched, rule, invalid_rule, source_list, health = asyncio.run(request())
    assert source.status_code == 201
    assert duplicate.status_code == 409
    assert patched.json()["reputation_score"] == 80
    assert rule.status_code == 201
    assert invalid_rule.status_code == 422
    assert source_list.json()["count"] == 1
    assert health.status_code == 200
    assert health.json()["ai_status"] == "not_configured"


def test_social_database_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)
    create_social_source(
        db_path,
        {
            "account_id": "manual-1",
            "username": "analyst",
            "display_name": "Analyst",
            "category": "analyst",
            "verified": True,
            "reputation_score": 65,
            "supported_assets": ["BTC"],
        },
    )
    initialize_database(db_path)
    connection = connect(db_path)
    try:
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '2026-11-social-intelligence-v1'"
        ).fetchone()[0]
        source_count = connection.execute(
            "SELECT COUNT(*) FROM social_sources WHERE account_id = 'manual-1'"
        ).fetchone()[0]
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert migration_count == 1
    assert source_count == 1
    assert {"social_posts", "social_events", "notification_deliveries"} <= tables
