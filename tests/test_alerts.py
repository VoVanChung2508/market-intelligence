from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import requests

from backend.alerts import build_price_move_alert, publish_alert
from backend.database import get_alert_events, initialize_database
from src.notify import send_notification


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