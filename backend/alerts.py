import concurrent.futures
import logging
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from backend.database import connect
from backend.settings import DB_PATH
from src.notify import (
    is_telegram_configured,
    is_telegram_enabled,
    send_notification,
    send_telegram_message,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_CHANGE_THRESHOLD_PCT = 5.0


def configured_threshold_pct() -> float:
    try:
        threshold = float(
            os.getenv("MARKET_ALERT_CHANGE_THRESHOLD_PCT", DEFAULT_CHANGE_THRESHOLD_PCT)
        )
    except (TypeError, ValueError):
        LOGGER.warning("invalid_alert_threshold; using default")
        return DEFAULT_CHANGE_THRESHOLD_PCT
    return threshold if 0 < threshold <= 100 else DEFAULT_CHANGE_THRESHOLD_PCT


def build_price_move_alert(
    symbol: str,
    interval: str,
    previous_timestamp: str,
    previous_close: float,
    latest_timestamp: str,
    latest_close: float,
    threshold_pct: float = DEFAULT_CHANGE_THRESHOLD_PCT,
) -> dict[str, object] | None:
    if not 0 < threshold_pct <= 100 or previous_close <= 0 or latest_close <= 0:
        return None
    try:
        previous_time = datetime.fromisoformat(previous_timestamp.replace("Z", "+00:00"))
        latest_time = datetime.fromisoformat(latest_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    gap_seconds = (latest_time - previous_time).total_seconds()
    maximum_gap = 90 * 60 if interval == "1h" else 4 * 24 * 60 * 60
    if gap_seconds <= 0 or gap_seconds > maximum_gap:
        return None

    change_pct = round((latest_close / previous_close - 1) * 100, 6)
    if abs(change_pct) < threshold_pct:
        return None
    direction = "up" if change_pct > 0 else "down"
    return {
        "event_key": f"{symbol}:{interval}:move:{latest_timestamp}",
        "symbol": symbol,
        "interval": interval,
        "rule": f"single_interval_move_{threshold_pct:g}pct",
        "timestamp": latest_timestamp,
        "change_pct": change_pct,
        "direction": direction,
    }


def evaluate_latest_price_move(
    symbol: str,
    interval: str,
    db_path: Path = DB_PATH,
    threshold_pct: float | None = None,
) -> dict[str, object] | None:
    threshold = threshold_pct if threshold_pct is not None else configured_threshold_pct()
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT timestamp, close FROM price_bars "
            "WHERE symbol = ? AND interval = ? "
            "ORDER BY timestamp DESC LIMIT 2",
            (symbol.upper(), interval),
        ).fetchall()
    finally:
        connection.close()

    if len(rows) < 2:
        return None
    return build_price_move_alert(
        symbol.upper(),
        interval,
        rows[1]["timestamp"],
        rows[1]["close"],
        rows[0]["timestamp"],
        rows[0]["close"],
        threshold,
    )


def publish_alert(
    alert: dict[str, object],
    db_path: Path = DB_PATH,
) -> bool:
    connection = connect(db_path)
    try:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO alert_events "
            "(event_key, symbol, interval, rule, timestamp, change_pct, "
            "delivery_status) VALUES (?, ?, ?, ?, ?, ?, 'failed')",
            (
                alert["event_key"],
                alert["symbol"],
                alert["interval"],
                alert["rule"],
                alert["timestamp"],
                alert["change_pct"],
            ),
        )
        if cursor.rowcount == 0:
            return False
        connection.commit()
    finally:
        connection.close()

    topic = os.getenv("NTFY_TOPIC", "").strip()
    ntfy_enabled = os.getenv("NTFY_ENABLED", "true").strip().casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }
    ntfy_active = bool(topic) and ntfy_enabled
    tg_active = is_telegram_enabled() and is_telegram_configured()

    if not ntfy_active and not tg_active:
        _update_delivery(db_path, str(alert["event_key"]), "not_configured", None)
        LOGGER.info("alert_saved symbol=%s delivery=not_configured", alert["symbol"])
        return False

    move = float(alert["change_pct"])
    title = f"Market move: {alert['symbol']}"
    message = (
        f"{alert['symbol']} moved {move:+.2f}% over {alert['interval']}. "
        "Research alert only; not a trading instruction."
    )
    sequence_id = hashlib.sha256(str(alert["event_key"]).encode("utf-8")).hexdigest()[:32]

    sent_ntfy = False
    sent_tg = False
    errors: list[str] = []

    def _deliver_ntfy() -> None:
        nonlocal sent_ntfy
        try:
            ok = send_notification(
                title,
                message,
                priority="high",
                sequence_id=sequence_id,
            )
            if ok:
                sent_ntfy = True
            else:
                errors.append("ntfy_request_failed")
        except Exception as exc:
            errors.append(f"ntfy:{type(exc).__name__}")
            LOGGER.warning("alert_delivery_failed symbol=%s channel=ntfy error=%s", alert["symbol"], type(exc).__name__)

    def _deliver_tg() -> None:
        nonlocal sent_tg
        try:
            ok, err = send_telegram_message(
                title,
                message,
            )
            if ok:
                sent_tg = True
            else:
                errors.append(f"telegram:{err or 'failed'}")
        except Exception as exc:
            errors.append(f"telegram:{type(exc).__name__}")
            LOGGER.warning("alert_delivery_failed symbol=%s channel=telegram error=%s", alert["symbol"], type(exc).__name__)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = []
        if ntfy_active:
            futures.append(executor.submit(_deliver_ntfy))
        if tg_active:
            futures.append(executor.submit(_deliver_tg))
        for future in futures:
            future.result()

    any_sent = sent_ntfy or sent_tg
    if any_sent:
        status = "sent"
        error_summary = "; ".join(errors) if errors else None
        _update_delivery(db_path, str(alert["event_key"]), status, error_summary)
        LOGGER.info("alert_saved symbol=%s delivery=%s", alert["symbol"], status)
        return True
    else:
        status = "failed"
        error_summary = "; ".join(errors) if errors else "all_channels_failed"
        _update_delivery(db_path, str(alert["event_key"]), status, error_summary)
        LOGGER.info("alert_saved symbol=%s delivery=%s", alert["symbol"], status)
        return False


def _update_delivery(
    db_path: Path,
    event_key: str,
    status: str,
    error: str | None,
) -> None:
    connection = connect(db_path)
    try:
        connection.execute(
            "UPDATE alert_events SET delivery_status = ?, delivery_error = ? "
            "WHERE event_key = ?",
            (status, error, event_key),
        )
        connection.commit()
    finally:
        connection.close()


def evaluate_and_publish_latest(symbol: str, interval: str, db_path: Path = DB_PATH) -> None:
    try:
        alert = evaluate_latest_price_move(symbol, interval, db_path)
        if alert:
            publish_alert(alert, db_path)
    except Exception as exc:
        LOGGER.warning("alert_evaluation_failed symbol=%s error=%s", symbol, type(exc).__name__)