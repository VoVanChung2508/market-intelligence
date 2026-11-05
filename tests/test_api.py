import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from backend.api import create_app


def write_market_csv(data_dir, name, content):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / name).write_text(content, encoding="utf-8")


def get_responses(app, *paths):
    async def send_requests():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return [await client.get(path) for path in paths]

    return asyncio.run(send_requests())


def post_json(app, path, payload):
    async def send_request():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.post(path, json=payload)

    return asyncio.run(send_request())


def test_health_and_overview_import_local_csv(tmp_path):
    data_dir = tmp_path / "raw"
    db_path = tmp_path / "processed" / "market.sqlite3"
    now = datetime.now(timezone.utc)
    latest = now.isoformat(timespec="seconds")
    previous = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    write_market_csv(
        data_dir,
        "crypto_bitcoin.csv",
        "timestamp_ms,price_usd,timestamp\n"
        f"1,100,{previous}\n"
        f"2,105,{latest}\n",
    )

    health, response = get_responses(
        create_app(db_path, data_dir),
        "/healthz",
        "/api/v1/overview",
    )

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "database": "ok"}
    payload = response.json()
    assert payload["data_status"] == "stale"
    btc = next(asset for asset in payload["assets"] if asset["symbol"] == "BTC")
    eth = next(asset for asset in payload["assets"] if asset["symbol"] == "ETH")
    assert btc["price"] == 105
    assert btc["change_pct"] == 5
    assert btc["freshness"] == "fresh"
    assert eth["price"] is None
    assert eth["freshness"] == "unavailable"


def test_import_is_idempotent_and_bars_are_bounded(tmp_path):
    data_dir = tmp_path / "raw"
    db_path = tmp_path / "market.sqlite3"
    now = datetime.now(timezone.utc)
    timestamps = [
        (now - timedelta(hours=offset)).isoformat(timespec="seconds")
        for offset in range(4, 0, -1)
    ]
    rows = "".join(
        f"{index},{100 + index},{timestamp}\n"
        for index, timestamp in enumerate(timestamps, start=1)
    )
    write_market_csv(
        data_dir,
        "crypto_bitcoin.csv",
        f"timestamp_ms,price_usd,timestamp\n{rows}",
    )

    first, second = get_responses(
        create_app(db_path, data_dir),
        "/api/v1/assets/BTC/bars?interval=1h&limit=2",
        "/api/v1/assets/BTC/bars?interval=1h&limit=2",
    )

    assert first.status_code == 200
    assert len(first.json()["bars"]) == 2
    assert second.json() == first.json()


def test_bars_reject_invalid_limits_and_missing_symbols(tmp_path):
    invalid_limit, missing_symbol, invalid_symbol = get_responses(
        create_app(tmp_path / "market.sqlite3", tmp_path / "raw"),
        "/api/v1/assets/BTC/bars?limit=501",
        "/api/v1/assets/UNKNOWN/bars",
        "/api/v1/assets/../bars",
    )

    assert invalid_limit.status_code == 422
    assert missing_symbol.status_code == 404
    assert invalid_symbol.status_code in (404, 422)


def test_analysis_endpoint_returns_stored_evaluation_without_signal(tmp_path):
    models_dir = tmp_path / "models"
    report_path = models_dir / "baseline-logistic-v1" / "aapl.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        '{"symbol":"AAPL","model_version":"baseline-logistic-v1",'
        '"gate_passed":false,"latest_direction":null}',
        encoding="utf-8",
    )

    response, missing = get_responses(
        create_app(tmp_path / "market.sqlite3", tmp_path / "raw", models_dir),
        "/api/v1/analysis/AAPL",
        "/api/v1/analysis/BTC",
    )

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["report"]["gate_passed"] is False
    assert response.json()["report"]["latest_direction"] is None
    assert missing.status_code == 200
    assert missing.json()["available"] is False
    assert missing.json()["symbol"] == "BTC"


def test_alert_endpoint_and_frontend_shell(tmp_path):
    app = create_app(tmp_path / "market.sqlite3", tmp_path / "raw", tmp_path / "models")
    alerts, shell = get_responses(app, "/api/v1/alerts", "/")

    assert alerts.status_code == 200
    assert alerts.json()["items"] == []
    assert 0 < alerts.json()["threshold_pct"] <= 100
    assert isinstance(alerts.json()["ntfy_configured"], bool)
    assert isinstance(alerts.json()["telegram_configured"], bool)
    assert shell.status_code == 200
    assert "Market Intelligence" in shell.text


def test_crypto_watchlist_add_is_validated_and_visible_before_collection(tmp_path):
    app = create_app(tmp_path / "market.sqlite3", tmp_path / "raw", tmp_path / "models")
    payload = {"coin_id": "solana", "symbol": "SOL", "name": "Solana"}

    added = post_json(app, "/api/v1/watchlist/crypto", payload)
    duplicate = post_json(app, "/api/v1/watchlist/crypto", payload)
    watchlist, overview = get_responses(
        app,
        "/api/v1/watchlist/crypto",
        "/api/v1/overview",
    )

    assert added.status_code == 201
    assert duplicate.status_code == 409
    assert watchlist.json()["max_items"] == 10
    assert any(item["coin_id"] == "solana" for item in watchlist.json()["items"])
    solana = next(asset for asset in overview.json()["assets"] if asset["symbol"] == "SOL")
    assert solana["price"] is None
    assert solana["freshness"] == "unavailable"
    assert solana["source"] == "Awaiting first collection"