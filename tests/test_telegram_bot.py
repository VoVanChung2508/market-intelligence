from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.database import initialize_database, store_news_articles
from backend.telegram_bot import (
    build_daily_briefing,
    handle_command,
    is_authorized_chat,
    poll_updates_once,
    send_daily_briefing,
)


def test_telegram_bot_authorization(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-10011223344")

    assert is_authorized_chat("-10011223344") is True
    assert is_authorized_chat(-10011223344) is True
    assert is_authorized_chat("999999999") is False
    assert is_authorized_chat("attacker_chat") is False


def test_telegram_bot_unauthorized_commands_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-10011223344")
    db_path = tmp_path / "market.sqlite3"

    # Command from stranger chat must return None
    assert handle_command("/status", chat_id="stranger_id", db_path=db_path) is None
    assert handle_command("/overview", chat_id="stranger_id", db_path=db_path) is None


def test_telegram_bot_status_and_overview_commands(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-10011223344")
    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)

    # /status
    status_resp = handle_command("/status", chat_id="-10011223344", db_path=db_path)
    assert status_resp is not None
    assert "System Status" in status_resp
    assert "Database: ✅ OK" in status_resp

    # /overview
    overview_resp = handle_command("/overview", chat_id="-10011223344", db_path=db_path)
    assert overview_resp is not None
    assert "Market Overview" in overview_resp


def test_telegram_bot_news_command(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-10011223344")
    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)

    # Insert a dummy news article
    article = {
        "canonical_url": "https://news.example/test-news-item",
        "title": "Bitcoin reaches adoption milestone",
        "summary": "BTC sees increased enterprise usage.",
        "content": "Full content text",
        "published_at": "2026-11-05T12:00:00+00:00",
        "source": "Crypto Wire",
        "source_url": "https://news.example/feed",
        "language": "en",
        "category": "markets",
        "title_fingerprint": "fp1",
        "content_fingerprint": "fp2",
        "collected_at": "2026-11-05T12:05:00+00:00",
        "related_assets": ["BTC"],
    }
    store_news_articles(db_path, [article])

    # Query /news BTC
    news_btc = handle_command("/news BTC", chat_id="-10011223344", db_path=db_path)
    assert news_btc is not None
    assert "Bitcoin reaches adoption milestone" in news_btc
    assert "Crypto Wire" in news_btc

    # Query /news for asset with no articles
    news_eth = handle_command("/news ETH", chat_id="-10011223344", db_path=db_path)
    assert "Không có tin tức mới" in news_eth


def test_telegram_bot_briefing(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-10011223344")
    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)

    title, msg = build_daily_briefing(db_path)
    assert "Daily Market Briefing" in title or "Bản tin Thị trường" in title
    assert "Market Overview" in msg


def test_poll_updates_once_filters_and_dispatches(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-10011223344")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "valid-token")
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)

    replies = []

    def fake_get(url, **kwargs):
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "ok": True,
                "result": [
                    # Message 1 from stranger: should be ignored
                    {
                        "update_id": 101,
                        "message": {"chat": {"id": 999999}, "text": "/status"},
                    },
                    # Message 2 from authorized chat: should be answered
                    {
                        "update_id": 102,
                        "message": {"chat": {"id": -10011223344}, "text": "/status"},
                    },
                ],
            },
        )

    def fake_post(url, **kwargs):
        replies.append(kwargs.get("json", {}))
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"ok": True},
        )

    session = MagicMock()
    session.get = fake_get
    session.post = fake_post

    next_offset = poll_updates_once(offset=0, session=session, db_path=db_path)
    assert next_offset == 103
    # Exactly one reply sent (for the authorized user, stranger dropped)
    assert len(replies) == 1
    assert "System Status" in replies[0]["text"]

