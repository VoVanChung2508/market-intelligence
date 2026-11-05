import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.auth import bootstrap_admin, create_user, list_users
from backend.api import create_app
from backend.database import connect, initialize_database

TEST_PASSWORD = "market-intelligence-test-password"


def test_admin_bootstrap_is_one_time_and_stores_only_password_hash(tmp_path):
    db_path = tmp_path / "market.sqlite3"
    initialize_database(db_path)

    account = bootstrap_admin(db_path, "first-admin", TEST_PASSWORD)

    assert account["role"] == "ADMIN"
    assert account["username"] == "first-admin"
    connection = connect(db_path)
    try:
        stored_hash = connection.execute(
            "SELECT password_hash FROM auth_users WHERE id = ?",
            (account["id"],),
        ).fetchone()["password_hash"]
    finally:
        connection.close()
    assert stored_hash != TEST_PASSWORD
    assert stored_hash.startswith("$argon2")
    with pytest.raises(ValueError, match="Users already exist"):
        bootstrap_admin(db_path, "second-admin", TEST_PASSWORD)


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
                if not list_users(app.state.db_path):
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
                assert login.status_code == 200
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                return [
                    await client.get(path, headers=headers) if path.startswith("/api/")
                    else await client.get(path)
                    for path in paths
                ]

    return asyncio.run(send_requests())


def post_json(app, path, payload):
    async def send_request():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                if not list_users(app.state.db_path):
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
                assert login.status_code == 200
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                return await client.post(path, json=payload, headers=headers)

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


def test_command_audit_endpoint_requires_admin(tmp_path):
    app = create_app(tmp_path / "market.sqlite3", tmp_path / "raw")

    response, = get_responses(app, "/api/auth/command-audit")

    assert response.status_code == 200
    assert response.json()["items"] == []


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


def test_api_authentication_rbac_refresh_rotation_and_security_headers(tmp_path):
    app = create_app(tmp_path / "market.sqlite3", tmp_path / "raw")

    async def exercise_auth():
        async with app.router.lifespan_context(app):
            create_user(app.state.db_path, "admin-user", TEST_PASSWORD, "ADMIN")
            create_user(app.state.db_path, "read-user", TEST_PASSWORD, "READ_ONLY")
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                health = await client.get("/healthz")
                unauthorized = await client.get("/api/v1/overview")
                admin_login = await client.post(
                    "/api/auth/login",
                    json={"username": "admin-user", "password": TEST_PASSWORD},
                )
                read_login = await client.post(
                    "/api/auth/login",
                    json={"username": "read-user", "password": TEST_PASSWORD},
                )
                read_headers = {
                    "Authorization": f"Bearer {read_login.json()['access_token']}",
                }
                read_access = await client.get("/api/v1/overview", headers=read_headers)
                read_mutation = await client.post(
                    "/api/v1/watchlist/crypto",
                    json={"coin_id": "solana", "symbol": "SOL", "name": "Solana"},
                    headers=read_headers,
                )
                admin_headers = {
                    "Authorization": f"Bearer {admin_login.json()['access_token']}",
                }
                admin_user_list = await client.get("/api/auth/users", headers=admin_headers)
                last_admin_update = await client.patch(
                    "/api/auth/users/1",
                    headers=admin_headers,
                    json={"role": "READ_ONLY"},
                )
                audit = await client.get("/api/auth/audit", headers=admin_headers)
                old_refresh = read_login.json()["refresh_token"]
                refreshed = await client.post(
                    "/api/auth/refresh",
                    json={"refresh_token": old_refresh},
                )
                replay = await client.post(
                    "/api/auth/refresh",
                    json={"refresh_token": old_refresh},
                )
                revoked_access = await client.get(
                    "/api/v1/overview",
                    headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
                )
                return (
                    health,
                    unauthorized,
                    admin_login,
                    read_access,
                    read_mutation,
                    admin_user_list,
                    last_admin_update,
                    audit,
                    refreshed,
                    replay,
                    revoked_access,
                )

    (
        health,
        unauthorized,
        admin_login,
        read_access,
        read_mutation,
        admin_user_list,
        last_admin_update,
        audit,
        refreshed,
        replay,
        revoked_access,
    ) = asyncio.run(exercise_auth())

    assert health.status_code == 200
    assert unauthorized.status_code == 401
    assert admin_login.status_code == 200
    assert admin_login.headers["cache-control"] == "no-store"
    assert read_access.status_code == 200
    assert read_mutation.status_code == 403
    assert admin_user_list.status_code == 200
    assert len(admin_user_list.json()["items"]) == 2
    assert "password_hash" not in admin_user_list.text
    assert last_admin_update.status_code == 422
    assert audit.status_code == 200
    assert audit.json()["count"] >= 2
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != ""
    assert replay.status_code == 401
    assert revoked_access.status_code == 401


def test_login_rate_limit_and_cors_allowlist(tmp_path):
    app = create_app(tmp_path / "market.sqlite3", tmp_path / "raw")

    async def exercise_limits():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                responses = [
                    await client.post("/api/auth/login", json={})
                    for _ in range(11)
                ]
                allowed_origin = await client.get(
                    "/healthz",
                    headers={"Origin": "http://localhost:5173"},
                )
                rejected_origin = await client.get(
                    "/healthz",
                    headers={"Origin": "https://untrusted.example"},
                )
                return responses, allowed_origin, rejected_origin

    responses, allowed_origin, rejected_origin = asyncio.run(exercise_limits())
    assert [response.status_code for response in responses[:10]] == [422] * 10
    assert responses[10].status_code == 429
    assert responses[10].headers["retry-after"] == "60"
    assert allowed_origin.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in rejected_origin.headers