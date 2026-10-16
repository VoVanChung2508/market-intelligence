from datetime import datetime, timezone

import requests

from backend.collector import parse_alpha_vantage_daily, parse_coingecko_chart
from backend.collector import collect_once
from backend.database import connect, get_collection_runs, initialize_database


def test_parse_coingecko_chart_includes_volume():
    timestamp_ms = 1792180800000
    result = parse_coingecko_chart(
        {
            "prices": [[timestamp_ms, 67000.5]],
            "total_volumes": [[timestamp_ms, 123456.0]],
        }
    )

    assert result == [
        {
            "timestamp": datetime.fromtimestamp(
                timestamp_ms / 1000, timezone.utc
            ).isoformat(timespec="seconds"),
            "close": 67000.5,
            "volume": 123456.0,
        }
    ]


def test_parse_alpha_vantage_daily_normalizes_market_close_to_utc():
    result = parse_alpha_vantage_daily(
        {
            "Time Series (Daily)": {
                "2026-10-15": {
                    "1. open": "10.00",
                    "2. high": "12.00",
                    "3. low": "9.00",
                    "4. close": "11.00",
                    "5. volume": "1234",
                }
            }
        }
    )

    assert result[0]["timestamp"].endswith("+00:00")
    assert result[0]["close"] == 11
    assert result[0]["volume"] == 1234


def test_collector_isolates_provider_failures(tmp_path):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"prices": [[1792180800000, 67000]], "total_volumes": []}

    class FakeSession:
        def get(self, url, **_kwargs):
            if "/bitcoin/" in url:
                return FakeResponse()
            raise requests.Timeout("provider timeout")

    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)
    collect_once(db_path, session=FakeSession(), alpha_vantage_key="")
    runs = get_collection_runs(db_path)

    assert {(run["symbol"], run["status"]) for run in runs} == {
        ("BTC", "success"),
        ("ETH", "failed"),
        ("AAPL", "skipped"),
    }


def test_legacy_collector_has_requests_dependency():
    import src.collect as legacy_collector

    assert legacy_collector.requests is not None


def test_alpha_vantage_key_obeys_daily_request_cooldown(tmp_path):
    class FakeSession:
        calls = []

        def get(self, url, **_kwargs):
            self.calls.append(url)
            raise requests.Timeout("offline test")

    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)
    connection = connect(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute(
            "INSERT INTO collection_runs "
            "(provider, symbol, status, started_at, completed_at, error_summary) "
            "VALUES ('Alpha Vantage', 'AAPL', 'success', ?, ?, NULL)",
            (now, now),
        )
        connection.commit()
    finally:
        connection.close()

    session = FakeSession()
    collect_once(db_path, session=session, alpha_vantage_key="test-key")
    runs = get_collection_runs(db_path, limit=10)
    aapl_runs = [run for run in runs if run["symbol"] == "AAPL"]

    assert not any("alphavantage.co" in url for url in session.calls)
    assert any(
        run["status"] == "skipped"
        and run["error_summary"] == "daily_request_cooldown"
        for run in aapl_runs
    )