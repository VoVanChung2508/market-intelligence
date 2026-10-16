import logging
import os
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.alerts import evaluate_and_publish_latest
from backend.database import connect, get_crypto_watchlist, initialize_database
from backend.settings import ALPHAVANTAGE_API_KEY, DB_PATH

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


def create_http_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def parse_coingecko_chart(payload: dict[str, Any]) -> list[dict[str, Any]]:
    prices = payload.get("prices")
    if not isinstance(prices, list):
        raise ValueError("Missing price series")
    volumes = {
        int(point[0]): float(point[1])
        for point in payload.get("total_volumes", [])
        if isinstance(point, list) and len(point) == 2
    }

    bars = []
    for point in prices:
        if not isinstance(point, list) or len(point) != 2:
            continue
        timestamp_ms = int(point[0])
        close = float(point[1])
        if timestamp_ms <= 0 or close <= 0:
            continue
        timestamp = datetime.fromtimestamp(
            timestamp_ms / 1000, timezone.utc
        ).isoformat(timespec="seconds")
        bars.append(
            {
                "timestamp": timestamp,
                "close": close,
                "volume": volumes.get(timestamp_ms),
            }
        )
    if not bars:
        raise ValueError("No valid price points returned")
    return bars


def parse_alpha_vantage_daily(payload: dict[str, Any]) -> list[dict[str, Any]]:
    series = payload.get("Time Series (Daily)")
    if not isinstance(series, dict):
        raise ValueError("No daily time series in response")

    bars = []
    for day, values in series.items():
        try:
            parsed_date = date.fromisoformat(day)
            market_close = datetime.combine(
                parsed_date,
                time(16, 0),
                tzinfo=ZoneInfo("America/New_York"),
            )
            close = float(values["4. close"])
            open_price = float(values["1. open"])
            high = float(values["2. high"])
            low = float(values["3. low"])
            volume = float(values["5. volume"])
        except (KeyError, TypeError, ValueError):
            continue
        if close <= 0 or low <= 0 or high < low:
            continue
        bars.append(
            {
                "timestamp": market_close.astimezone(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    if not bars:
        raise ValueError("No valid daily bars returned")
    return sorted(bars, key=lambda bar: bar["timestamp"])


def _record_run(
    connection,
    provider: str,
    symbol: str,
    status: str,
    started_at: str,
    rows_received: int = 0,
    rows_inserted: int = 0,
    error_summary: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO collection_runs "
        "(provider, symbol, status, started_at, completed_at, rows_received, "
        "rows_inserted, error_summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            provider,
            symbol,
            status,
            started_at,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            rows_received,
            rows_inserted,
            error_summary,
        ),
    )


def _store_bars(
    db_path: Path,
    symbol: str,
    name: str,
    asset_class: str,
    interval: str,
    source: str,
    bars: list[dict[str, Any]],
    started_at: str,
) -> int:
    connection = connect(db_path)
    try:
        connection.execute(
            "INSERT INTO assets (symbol, name, asset_class, currency) VALUES (?, ?, ?, 'USD') "
            "ON CONFLICT(symbol) DO UPDATE SET name = excluded.name, "
            "asset_class = excluded.asset_class",
            (symbol, name, asset_class),
        )
        inserted = 0
        ingested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for bar in bars:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO price_bars "
                "(symbol, interval, timestamp, open, high, low, close, volume, "
                "source, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    symbol,
                    interval,
                    bar["timestamp"],
                    bar.get("open"),
                    bar.get("high"),
                    bar.get("low"),
                    bar["close"],
                    bar.get("volume"),
                    source,
                    ingested_at,
                ),
            )
            inserted += cursor.rowcount
        _record_run(
            connection,
            source,
            symbol,
            "success",
            started_at,
            rows_received=len(bars),
            rows_inserted=inserted,
        )
        connection.commit()
        return inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _collect_crypto(
    session: requests.Session,
    coin_id: str,
    symbol: str,
    name: str,
    db_path: Path,
) -> None:
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        response = session.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": "30"},
            timeout=(5, 20),
        )
        response.raise_for_status()
        bars = parse_coingecko_chart(response.json())
        inserted = _store_bars(
            db_path,
            symbol,
            name,
            "crypto",
            "1h",
            "CoinGecko",
            bars,
            started_at,
        )
        if inserted:
            evaluate_and_publish_latest(symbol, "1h", db_path)
        LOGGER.info("collection_success provider=CoinGecko symbol=%s inserted=%s", symbol, inserted)
    except (requests.RequestException, ValueError, KeyError) as exc:
        connection = connect(db_path)
        try:
            _record_run(
                connection,
                "CoinGecko",
                symbol,
                "failed",
                started_at,
                error_summary=type(exc).__name__,
            )
            connection.commit()
        finally:
            connection.close()
        LOGGER.warning("collection_failed provider=CoinGecko symbol=%s error=%s", symbol, type(exc).__name__)


def _collect_equity(
    session: requests.Session,
    symbol: str,
    api_key: str,
    db_path: Path,
) -> None:
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        response = session.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": symbol,
                "outputsize": "compact",
                "apikey": api_key,
            },
            timeout=(5, 20),
        )
        response.raise_for_status()
        payload = response.json()
        if "Note" in payload or "Information" in payload or "Error Message" in payload:
            raise ValueError("Provider returned an API notice")
        bars = parse_alpha_vantage_daily(payload)
        inserted = _store_bars(
            db_path,
            symbol,
            symbol,
            "equity",
            "1d",
            "Alpha Vantage",
            bars,
            started_at,
        )
        if inserted:
            evaluate_and_publish_latest(symbol, "1d", db_path)
        LOGGER.info("collection_success provider=AlphaVantage symbol=%s inserted=%s", symbol, inserted)
    except (requests.RequestException, ValueError, KeyError) as exc:
        connection = connect(db_path)
        try:
            _record_run(
                connection,
                "Alpha Vantage",
                symbol,
                "failed",
                started_at,
                error_summary=type(exc).__name__,
            )
            connection.commit()
        finally:
            connection.close()
        LOGGER.warning("collection_failed provider=AlphaVantage symbol=%s error=%s", symbol, type(exc).__name__)


def _equity_attempt_is_recent(db_path: Path, cooldown_hours: int = 20) -> bool:
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT completed_at FROM collection_runs "
            "WHERE provider = 'Alpha Vantage' AND symbol = 'AAPL' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return False
    try:
        completed_at = datetime.fromisoformat(row["completed_at"].replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - completed_at).total_seconds() < cooldown_hours * 3600


def collect_once(
    db_path: Path = DB_PATH,
    session: requests.Session | None = None,
    alpha_vantage_key: str | None = None,
) -> None:
    initialize_database(db_path)
    own_session = session is None
    client = session or create_http_session()
    try:
        for coin in get_crypto_watchlist(db_path):
            _collect_crypto(
                client,
                coin["coin_id"],
                coin["symbol"],
                coin["name"],
                db_path,
            )

        key = (
            alpha_vantage_key
            if alpha_vantage_key is not None
            else ALPHAVANTAGE_API_KEY
        ).strip()
        if key:
            if _equity_attempt_is_recent(db_path):
                connection = connect(db_path)
                try:
                    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    _record_run(
                        connection,
                        "Alpha Vantage",
                        "AAPL",
                        "skipped",
                        started_at,
                        error_summary="daily_request_cooldown",
                    )
                    connection.commit()
                finally:
                    connection.close()
                LOGGER.info("collection_skipped provider=AlphaVantage symbol=AAPL reason=daily_request_cooldown")
            else:
                _collect_equity(client, "AAPL", key, db_path)
        else:
            connection = connect(db_path)
            try:
                started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                _record_run(
                    connection,
                    "Alpha Vantage",
                    "AAPL",
                    "skipped",
                    started_at,
                    error_summary="missing_api_key",
                )
                connection.commit()
            finally:
                connection.close()
            LOGGER.info("collection_skipped provider=AlphaVantage symbol=AAPL reason=missing_api_key")
    finally:
        if own_session:
            client.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    collect_once()


if __name__ == "__main__":
    main()