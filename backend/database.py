import csv
import logging
import os
import sqlite3
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT,
    content TEXT,
    published_at TEXT,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    language TEXT,
    category TEXT NOT NULL,
    title_fingerprint TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL UNIQUE,
    collected_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_news_articles_recent
    ON news_articles (published_at DESC, collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_articles_category_recent
    ON news_articles (category, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_articles_title_fingerprint
    ON news_articles (title_fingerprint, published_at);

CREATE TABLE IF NOT EXISTS news_article_assets (
    article_id INTEGER NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL REFERENCES assets(symbol),
    PRIMARY KEY (article_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_news_article_assets_symbol
    ON news_article_assets (symbol, article_id);

CREATE TABLE IF NOT EXISTS social_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN (
        'official_project', 'official_exchange', 'regulator', 'news',
        'analyst', 'influencer', 'community', 'unknown'
    )),
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    reputation_score REAL NOT NULL DEFAULT 50 CHECK (reputation_score BETWEEN 0 AND 100),
    supported_assets TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_social_sources_active
    ON social_sources (active, category, username);

CREATE TABLE IF NOT EXISTS social_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_post_id TEXT NOT NULL UNIQUE,
    source_id INTEGER REFERENCES social_sources(id),
    text TEXT NOT NULL,
    post_url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    language TEXT,
    is_repost INTEGER NOT NULL DEFAULT 0 CHECK (is_repost IN (0, 1)),
    raw_payload TEXT NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (processing_status IN ('pending', 'processed', 'failed', 'irrelevant')),
    collected_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_social_posts_recent
    ON social_posts (published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_social_posts_processing
    ON social_posts (processing_status, collected_at);

CREATE TABLE IF NOT EXISTS social_post_assets (
    post_id INTEGER NOT NULL REFERENCES social_posts(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL REFERENCES assets(symbol),
    PRIMARY KEY (post_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_social_post_assets_symbol
    ON social_post_assets (symbol, post_id);

CREATE TABLE IF NOT EXISTS social_event_clusters (
    cluster_key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    symbol TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    post_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS social_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    post_id INTEGER NOT NULL REFERENCES social_posts(id) ON DELETE CASCADE,
    cluster_key TEXT NOT NULL REFERENCES social_event_clusters(cluster_key),
    symbol TEXT,
    category TEXT NOT NULL,
    sentiment TEXT NOT NULL CHECK (sentiment IN ('positive', 'negative', 'neutral', 'mixed')),
    impact_score REAL NOT NULL CHECK (impact_score BETWEEN 0 AND 100),
    confidence_score REAL NOT NULL CHECK (confidence_score BETWEEN 0 AND 100),
    source_reputation REAL NOT NULL CHECK (source_reputation BETWEEN 0 AND 100),
    priority TEXT NOT NULL CHECK (priority IN ('critical', 'high', 'medium', 'low')),
    priority_score REAL NOT NULL CHECK (priority_score BETWEEN 0 AND 100),
    unverified_claim INTEGER NOT NULL CHECK (unverified_claim IN (0, 1)),
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_social_events_recent
    ON social_events (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_social_events_symbol_category
    ON social_events (symbol, category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_events_priority
    ON social_events (priority, created_at DESC);

CREATE TABLE IF NOT EXISTS social_collection_state (
    provider TEXT PRIMARY KEY,
    since_id TEXT,
    next_token TEXT,
    status TEXT NOT NULL CHECK (status IN ('disabled', 'not_configured', 'idle', 'running', 'success', 'failed')),
    last_started_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    minimum_score REAL NOT NULL DEFAULT 60 CHECK (minimum_score BETWEEN 0 AND 100),
    assets TEXT NOT NULL DEFAULT '[]',
    categories TEXT NOT NULL DEFAULT '[]',
    cooldown_seconds INTEGER NOT NULL DEFAULT 900 CHECK (cooldown_seconds BETWEEN 0 AND 604800),
    quiet_hours_start TEXT,
    quiet_hours_end TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES social_events(id) ON DELETE CASCADE,
    rule_id INTEGER REFERENCES social_alert_rules(id) ON DELETE SET NULL,
    alert_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'sent', 'partial', 'failed', 'suppressed', 'not_configured')
    ),
    created_at TEXT NOT NULL,
    acknowledged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_social_alert_history_recent
    ON social_alert_history (created_at DESC, status);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL REFERENCES social_alert_history(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('ntfy', 'telegram')),
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'sent', 'failed', 'not_configured', 'disabled')
    ),
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    delivered_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (alert_id, channel)
);

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_status
    ON notification_deliveries (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS auth_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('ADMIN', 'USER', 'READ_ONLY')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
    ON auth_sessions (user_id, revoked_at, expires_at);

CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
    consumed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_session
    ON auth_refresh_tokens (session_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES auth_users(id) ON DELETE SET NULL,
    target_user_id INTEGER REFERENCES auth_users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure')),
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_recent
    ON audit_logs (occurred_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS ntfy_command_nonces (
    nonce TEXT PRIMARY KEY,
    actor_user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    command TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ntfy_command_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES auth_users(id) ON DELETE SET NULL,
    command TEXT NOT NULL,
    nonce TEXT UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'rejected')),
    error_code TEXT,
    result_summary TEXT,
    received_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ntfy_command_audit_recent
    ON ntfy_command_audit (received_at DESC, id DESC);
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
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES (?, ?)",
            ("2026-11-social-intelligence-v1", added_at),
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES (?, ?)",
            ("2026-11-authentication-v1", added_at),
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES (?, ?)",
            ("2026-11-ntfy-command-center-v1", added_at),
        )
        for symbol, (name, asset_class, currency) in ASSET_METADATA.items():
            connection.execute(
                "INSERT OR IGNORE INTO assets (symbol, name, asset_class, currency) "
                "VALUES (?, ?, ?, ?)",
                (symbol, name, asset_class, currency),
            )
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
        connection.execute(
            "INSERT OR IGNORE INTO social_alert_rules "
            "(name, minimum_score, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("default-social-alert", _social_minimum_score(), added_at, added_at),
        )
        connection.commit()
    finally:
        connection.close()


def _social_minimum_score() -> float:
    try:
        score = float(os.getenv("SOCIAL_ALERT_MIN_SCORE", "60"))
    except ValueError:
        LOGGER.warning("invalid_social_alert_min_score; using default")
        return 60.0
    if not 0 <= score <= 100:
        LOGGER.warning("invalid_social_alert_min_score; using default")
        return 60.0
    return score


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


def store_news_articles(
    db_path: Path,
    articles: list[dict[str, object]],
) -> int:
    if not articles:
        return 0

    connection = connect(db_path)
    inserted = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        for article in articles:
            published_at = article["published_at"]
            if connection.execute(
                "SELECT 1 FROM news_articles WHERE canonical_url = ? "
                "OR content_fingerprint = ? LIMIT 1",
                (article["canonical_url"], article["content_fingerprint"]),
            ).fetchone():
                continue
            if published_at is not None and connection.execute(
                "SELECT 1 FROM news_articles "
                "WHERE title_fingerprint = ? AND date(published_at) = date(?) "
                "LIMIT 1",
                (article["title_fingerprint"], published_at),
            ).fetchone():
                continue

            cursor = connection.execute(
                "INSERT INTO news_articles "
                "(canonical_url, title, summary, content, published_at, source, "
                "source_url, language, category, title_fingerprint, "
                "content_fingerprint, collected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    article["canonical_url"],
                    article["title"],
                    article["summary"],
                    article["content"],
                    published_at,
                    article["source"],
                    article["source_url"],
                    article["language"],
                    article["category"],
                    article["title_fingerprint"],
                    article["content_fingerprint"],
                    article["collected_at"],
                ),
            )
            article_id = cursor.lastrowid
            for symbol in article["related_assets"]:
                connection.execute(
                    "INSERT OR IGNORE INTO news_article_assets (article_id, symbol) "
                    "VALUES (?, ?)",
                    (article_id, symbol),
                )
            inserted += 1
        connection.commit()
        return inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _news_article_with_assets(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, object]:
    article = dict(row)
    article["related_assets"] = [
        asset["symbol"]
        for asset in connection.execute(
            "SELECT symbol FROM news_article_assets "
            "WHERE article_id = ? ORDER BY symbol",
            (article["id"],),
        ).fetchall()
    ]
    return article


def get_news_articles(
    db_path: Path,
    *,
    query: str | None = None,
    category: str | None = None,
    asset: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, object]], int]:
    clauses: list[str] = []
    parameters: list[object] = []
    if query:
        escaped_query = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        clauses.append(
            "(title LIKE ? ESCAPE '!' OR summary LIKE ? ESCAPE '!' "
            "OR content LIKE ? ESCAPE '!')"
        )
        parameters.extend([f"%{escaped_query}%"] * 3)
    if category:
        clauses.append("category = ?")
        parameters.append(category)
    if asset:
        clauses.append(
            "EXISTS (SELECT 1 FROM news_article_assets na "
            "WHERE na.article_id = news_articles.id AND na.symbol = ?)"
        )
        parameters.append(asset)

    where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    connection = connect(db_path)
    try:
        count = connection.execute(
            f"SELECT COUNT(*) FROM news_articles{where_clause}",
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT id, canonical_url AS url, title, summary, content, "
            "published_at, source, language, category, collected_at "
            f"FROM news_articles{where_clause} "
            "ORDER BY published_at DESC, collected_at DESC, id DESC LIMIT ? OFFSET ?",
            [*parameters, limit, offset],
        ).fetchall()
        return [_news_article_with_assets(connection, row) for row in rows], count
    finally:
        connection.close()


def get_news_article(db_path: Path, article_id: int) -> dict[str, object] | None:
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT id, canonical_url AS url, title, summary, content, "
            "published_at, source, language, category, collected_at "
            "FROM news_articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        return _news_article_with_assets(connection, row) if row else None
    finally:
        connection.close()