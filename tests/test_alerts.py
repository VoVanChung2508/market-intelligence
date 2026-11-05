from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import requests

from backend.alerts import build_price_move_alert, publish_alert
from backend.database import get_alert_events, initialize_database
from src.notify import (
    is_telegram_configured,
    is_telegram_enabled,
    send_notification,
    send_telegram_message,
)


def test_price_alert_requires_threshold_and_adjacent_timestamps():
    latest = datetime.now(timezone.utc)
    previous = latest - timedelta(hours=1)
    alert = build_price_move_alert(
        "BTC",
        "1h",
        previous.isoformat(),
        100,
        latest.isoformat(),
        106,
        threshold_pct=5,
    )
    assert alert is not None
    assert alert["direction"] == "up"
    assert alert["change_pct"] == 6

    stale_previous = latest - timedelta(hours=3)
    assert build_price_move_alert(
        "BTC", "1h", stale_previous.isoformat(), 100, latest.isoformat(), 106
    ) is None


def test_alert_event_is_deduplicated_and_ntfy_is_mocked(tmp_path, monkeypatch):
    import backend.alerts as alerts_module

    monkeypatch.setenv("NTFY_TOPIC", "")
    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)
    timestamp = datetime.now(timezone.utc).isoformat()
    alert = {
        "event_key": f"BTC:1h:move:{timestamp}",
        "symbol": "BTC",
        "interval": "1h",
        "rule": "single_interval_move_5pct",
        "timestamp": timestamp,
        "change_pct": 6.0,
        "direction": "up",
    }
    calls = []
    monkeypatch.setattr(alerts_module, "send_notification", lambda *args, **kwargs: calls.append(args) or True)

    assert publish_alert(alert, db_path) is False
    assert publish_alert(alert, db_path) is False
    assert calls == []
    events = get_alert_events(db_path)
    assert len(events) == 1
    assert events[0]["delivery_status"] == "not_configured"


def test_ntfy_sender_uses_sequence_and_redacts_request_error(monkeypatch, capsys):
    monkeypatch.setenv("NTFY_SERVER", "https://ntfy.sh")
    monkeypatch.setenv("NTFY_TOPIC", "private_topic_name")
    captured = {}

    def successful_post(self, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(requests.Session, "post", successful_post)
    assert send_notification(
        "Research alert",
        "Test message",
        priority="high",
        sequence_id="f" * 32,
    )
    assert captured["url"].endswith("/private_topic_name/" + "f" * 32)
    assert captured["headers"]["Priority"] == "high"

    def failing_post(self, url, **kwargs):
        raise requests.ConnectionError(f"failed to post to {url}")

    monkeypatch.setattr(requests.Session, "post", failing_post)
    assert not send_notification("Research alert", "Test message")
    assert "private_topic_name" not in capsys.readouterr().out


def test_telegram_price_alert_sent_and_deduplicated(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NTFY_TOPIC", "")
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret_bot_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123456789")

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

    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)
    timestamp = datetime.now(timezone.utc).isoformat()
    alert = {
        "event_key": f"BTC:1h:move:{timestamp}",
        "symbol": "BTC",
        "interval": "1h",
        "rule": "single_interval_move_5pct",
        "timestamp": timestamp,
        "change_pct": 6.25,
        "direction": "up",
    }

    assert publish_alert(alert, db_path) is True
    # Deduplication test: second call with same event_key must be skipped
    assert publish_alert(alert, db_path) is False

    assert "secret_bot_token" in captured["url"]
    assert "<b>Market move: BTC</b>" in captured["json"]["text"]
    assert "+6.25%" in captured["json"]["text"]
    assert captured["json"]["chat_id"] == "-100123456789"
    assert captured["json"]["parse_mode"] == "HTML"
    # Never log secret credentials to stdout/stderr
    assert "secret_bot_token" not in capsys.readouterr().out

    events = get_alert_events(db_path)
    assert len(events) == 1
    assert events[0]["delivery_status"] == "sent"


def test_parallel_alert_delivery_isolates_channel_failures(tmp_path, monkeypatch):
    import backend.alerts as alerts_module

    monkeypatch.setenv("NTFY_TOPIC", "test-ntfy")
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake_chat")

    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)

    # Scenario 1: ntfy fails, telegram succeeds -> overall alert succeeds
    monkeypatch.setattr(
        alerts_module,
        "send_notification",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectionError("offline")),
    )
    monkeypatch.setattr(
        alerts_module,
        "send_telegram_message",
        lambda *args, **kwargs: (True, None),
    )

    alert1 = {
        "event_key": "ETH:1h:move:1",
        "symbol": "ETH",
        "interval": "1h",
        "rule": "single_interval_move_5pct",
        "timestamp": "2026-11-05T10:00:00+00:00",
        "change_pct": -5.5,
        "direction": "down",
    }
    assert publish_alert(alert1, db_path) is True
    events = get_alert_events(db_path)
    assert events[0]["delivery_status"] == "sent"
    assert "ntfy:ConnectionError" in (events[0]["delivery_error"] or "")

    # Scenario 2: telegram fails, ntfy succeeds -> overall alert succeeds
    monkeypatch.setattr(
        alerts_module,
        "send_notification",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        alerts_module,
        "send_telegram_message",
        lambda *args, **kwargs: (False, "provider_rejected"),
    )

    alert2 = {
        "event_key": "AAPL:1d:move:2",
        "symbol": "AAPL",
        "interval": "1d",
        "rule": "single_interval_move_5pct",
        "timestamp": "2026-11-05T11:00:00+00:00",
        "change_pct": 7.1,
        "direction": "up",
    }
    assert publish_alert(alert2, db_path) is True
    events = get_alert_events(db_path)
    aapl_event = next(e for e in events if e["symbol"] == "AAPL")
    assert aapl_event["delivery_status"] == "sent"
    assert "telegram:provider_rejected" in (aapl_event["delivery_error"] or "")

    # Scenario 3: both fail -> alert marked failed
    monkeypatch.setattr(
        alerts_module,
        "send_notification",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        alerts_module,
        "send_telegram_message",
        lambda *args, **kwargs: (False, "invalid_token"),
    )

    alert3 = {
        "event_key": "SOL:1h:move:3",
        "symbol": "SOL",
        "interval": "1h",
        "rule": "single_interval_move_5pct",
        "timestamp": "2026-11-05T12:00:00+00:00",
        "change_pct": 8.0,
        "direction": "up",
    }
    assert publish_alert(alert3, db_path) is False
    events = get_alert_events(db_path)
    sol_event = next(e for e in events if e["symbol"] == "SOL")
    assert sol_event["delivery_status"] == "failed"