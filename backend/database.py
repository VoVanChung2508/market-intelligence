import csv
import sqlite3
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset_class TEXT NOT NULL CHECK (asset_class IN ('crypto', 'equity')),
    currency TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crypto_watchlist (
    coin_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_bars (
    symbol TEXT NOT NULL REFERENCES assets(symbol),
    interval TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL NOT NULL,
    volume REAL,
    source TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (symbol, interval, timestamp, source)
);

CREATE INDEX IF NOT EXISTS idx_price_bars_lookup
    ON price_bars (symbol, interval, timestamp DESC);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    rows_received INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_collection_runs_recent
    ON collection_runs (completed_at DESC);

CREATE TABLE IF NOT EXISTS alert_events (
    event_key TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    rule TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    change_pct REAL NOT NULL,
    delivery_status TEXT NOT NULL CHECK (delivery_status IN ('sent', 'failed', 'not_configured')),
    delivery_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_alert_events_recent
    ON alert_events (timestamp DESC);
"""

ASSET_METADATA = {
    "BTC": ("Bitcoin", "crypto", "USD"),
    "ETH": ("Ethereum", "crypto", "USD"),
    "AAPL": ("Apple Inc.", "equity", "USD"),
}
DEFAULT_CRYPTO_WATCHLIST = (
    ("bitcoin", "BTC", "Bitcoin"),
    ("ethereum", "ETH", "Ethereum"),
)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def initialize_database(db_path: Path) -> None:
    connection = connect(db_path)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        added_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for coin_id, symbol, name in DEFAULT_CRYPTO_WATCHLIST:
            connection.execute(
                "INSERT OR IGNORE INTO crypto_watchlist (coin_id, symbol, name, added_at) "
                "VALUES (?, ?, ?, ?)",
                (coin_id, symbol, name, added_at),
            )
            connection.execute(
                "INSERT OR IGNORE INTO assets (symbol, name, asset_class, currency) "
                "VALUES (?, ?, 'crypto', 'USD')",
                (symbol, name),
            )
        connection.commit()
    finally:
        connection.close()


def get_crypto_watchlist(db_path: Path) -> list[dict[str, str]]:
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT coin_id, symbol, name, added_at FROM crypto_watchlist "
            "ORDER BY added_at, symbol"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def add_crypto_watchlist_item(
    db_path: Path,
    coin_id: str,
    symbol: str,
    name: str,
    maximum_items: int = 10,
) -> None:
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute(
            "SELECT 1 FROM crypto_watchlist WHERE coin_id = ? OR symbol = ?",
            (coin_id, symbol),
        ).fetchone():
            raise ValueError("This coin ID or symbol is already tracked")
        count = connection.execute(
            "SELECT COUNT(*) FROM crypto_watchlist"
        ).fetchone()[0]
        if count >= maximum_items:
            raise ValueError(f"The local free-source watchlist is limited to {maximum_items} coins")
        added_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute(
            "INSERT INTO crypto_watchlist (coin_id, symbol, name, added_at) "
            "VALUES (?, ?, ?, ?)",
            (coin_id, symbol, name, added_at),
        )
        connection.execute(
            "INSERT INTO assets (symbol, name, asset_class, currency) "
            "VALUES (?, ?, 'crypto', 'USD')",
            (symbol, name),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _number(row: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number == number and abs(number) != float("inf"):
            return number
    return None


def _utc_timestamp(row: dict[str, str], asset_class: str) -> str | None:
    timestamp = row.get("timestamp")
    if timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            pass

    timestamp_ms = _number(row, "timestamp_ms")
    if timestamp_ms is not None:
        try:
            return datetime.fromtimestamp(
                timestamp_ms / 1000, timezone.utc
            ).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None

    date_value = row.get("Date") or row.get("Datetime") or row.get("index")
    if date_value:
        try:
            parsed_date = date.fromisoformat(date_value[:10])
        except ValueError:
            return None
        if asset_class == "equity":
            market_close = datetime.combine(
                parsed_date,
                time(16, 0),
                tzinfo=ZoneInfo("America/New_York"),
            )
            return market_close.astimezone(timezone.utc).isoformat(timespec="seconds")
        return datetime.combine(parsed_date, time.min, timezone.utc).isoformat(
            timespec="seconds"
        )
    return None


def import_csv_data(data_dir: Path, db_path: Path) -> dict[str, int | list[str]]:
    imported = 0
    skipped = 0
    errors: list[str] = []
    connection = connect(db_path)
    try:
        for path in sorted(data_dir.glob("*.csv")):
            stem = path.stem.lower()
            if stem == "crypto_bitcoin":
                symbol, asset_class = "BTC", "crypto"
                source, interval = "CoinGecko", "1h"
            elif stem == "crypto_ethereum":
                symbol, asset_class = "ETH", "crypto"
                source, interval = "CoinGecko", "1h"
            elif stem.startswith("stock_"):
                symbol, asset_class = path.stem.removeprefix("stock_").upper(), "equity"
                source, interval = "Yahoo Finance", "1d"
            else:
                skipped += 1
                continue

            name, _, currency = ASSET_METADATA.get(
                symbol, (symbol, asset_class, "USD")
            )
            ingested_at = datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).isoformat(timespec="seconds")
            connection.execute(
                "INSERT INTO assets (symbol, name, asset_class, currency) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "name = excluded.name, asset_class = excluded.asset_class, "
                "currency = excluded.currency",
                (symbol, name, asset_class, currency),
            )

            try:
                with path.open("r", newline="", encoding="utf-8-sig") as file:
                    for row in csv.DictReader(file):
                        timestamp = _utc_timestamp(row, asset_class)
                        close = _number(row, "price_usd", "Close", "close")
                        if timestamp is None or close is None or close <= 0:
                            skipped += 1
                            continue

                        open_price = _number(row, "Open", "open")
                        high = _number(row, "High", "high")
                        low = _number(row, "Low", "low")
                        volume = _number(row, "Volume", "volume")
                        if (
                            high is not None
                            and low is not None
                            and high < low
                        ):
                            skipped += 1
                            continue

                        cursor = connection.execute(
                            "INSERT OR IGNORE INTO price_bars "
                            "(symbol, interval, timestamp, open, high, low, close, "
                            "volume, source, ingested_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                symbol,
                                interval,
                                timestamp,
                                open_price,
                                high,
                                low,
                                close,
                                volume,
                                source,
                                ingested_at,
                            ),
                        )
                        imported += cursor.rowcount
            except (OSError, csv.Error, UnicodeError) as exc:
                errors.append(f"{path.name}: {type(exc).__name__}")
        connection.commit()
    finally:
        connection.close()

    return {"imported": imported, "skipped": skipped, "errors": errors}


def _freshness(timestamp: str, asset_class: str) -> str:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return "unavailable"
    age_seconds = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
    max_age = 2 * 60 * 60 if asset_class == "crypto" else 5 * 24 * 60 * 60
    return "fresh" if age_seconds <= max_age else "stale"


def get_asset_summaries(db_path: Path) -> list[dict[str, object]]:
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "WITH ranked_bars AS ("
            "SELECT price_bars.*, ROW_NUMBER() OVER ("
            "PARTITION BY symbol ORDER BY timestamp DESC, ingested_at DESC, source"
            ") AS rank FROM price_bars) "
            "SELECT a.symbol, a.name, a.asset_class, a.currency, "
            "b.interval, b.timestamp, b.close, b.volume, b.source "
            "FROM assets a LEFT JOIN ranked_bars b "
            "ON b.symbol = a.symbol AND b.rank = 1 "
            "ORDER BY a.asset_class, a.symbol"
        ).fetchall()
        summaries: list[dict[str, object]] = []
        for row in rows:
            previous = connection.execute(
                "SELECT close FROM price_bars WHERE symbol = ? AND interval = ? "
                "AND source = ? AND timestamp < ? ORDER BY timestamp DESC LIMIT 1",
                (row["symbol"], row["interval"], row["source"], row["timestamp"]),
            ).fetchone() if row["timestamp"] else None
            previous_close = previous["close"] if previous else None
            change_pct = (
                round((row["close"] / previous_close - 1) * 100, 6)
                if row["close"] is not None and previous_close not in (None, 0)
                else None
            )
            summaries.append(
                {
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "asset_class": row["asset_class"],
                    "price": row["close"],
                    "change_pct": change_pct,
                    "volume": row["volume"],
                    "currency": row["currency"],
                    "source": row["source"] or "Awaiting first collection",
                    "timestamp": row["timestamp"],
                    "freshness": (
                        _freshness(row["timestamp"], row["asset_class"])
                        if row["timestamp"]
                        else "unavailable"
                    ),
                }
            )
        return summaries
    finally:
        connection.close()


def get_price_bars(
    db_path: Path,
    symbol: str,
    interval: str,
    limit: int,
) -> list[dict[str, object]]:
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT timestamp, close FROM price_bars "
            "WHERE symbol = ? AND interval = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (symbol, interval, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]
    finally:
        connection.close()


def get_collection_runs(db_path: Path, limit: int = 20) -> list[dict[str, object]]:
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT provider, symbol, status, started_at, completed_at, "
            "rows_received, rows_inserted, error_summary "
            "FROM collection_runs ORDER BY completed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_alert_events(db_path: Path, limit: int = 100) -> list[dict[str, object]]:
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT symbol, interval, rule, timestamp, change_pct, "
            "delivery_status, delivery_error FROM alert_events "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()