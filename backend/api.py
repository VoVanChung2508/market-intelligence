import logging
import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.database import (
    get_alert_events,
    get_collection_runs,
    get_crypto_watchlist,
    get_asset_summaries,
    add_crypto_watchlist_item,
    get_price_bars,
    import_csv_data,
    initialize_database,
)
from backend.alerts import configured_threshold_pct
from backend.settings import DATA_DIR, DB_PATH

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9._:-]{1,24}$")
MAX_TRACKED_CRYPTO = 10


class CryptoWatchlistRequest(BaseModel):
    coin_id: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    symbol: str = Field(min_length=2, max_length=12, pattern=r"^[A-Z0-9]+$")
    name: str = Field(min_length=1, max_length=80)


def create_app(
    db_path: Path | None = None,
    data_dir: Path | None = None,
    models_dir: Path | None = None,
) -> FastAPI:
    resolved_db_path = db_path or DB_PATH
    resolved_data_dir = data_dir or DATA_DIR
    resolved_models_dir = models_dir or ROOT / "data" / "processed" / "models"
    import_result: dict[str, int | list[str]] = {
        "imported": 0,
        "skipped": 0,
        "errors": [],
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal import_result
        initialize_database(resolved_db_path)
        import_result = import_csv_data(resolved_data_dir, resolved_db_path)
        if import_result["errors"]:
            logger.warning("CSV import had file errors: %s", import_result["errors"])
        yield

    app = FastAPI(
        title="Market Intelligence API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.db_path = resolved_db_path
    app.state.data_dir = resolved_data_dir
    app.state.import_result = import_result
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        try:
            connection = sqlite3.connect(resolved_db_path, timeout=2)
            connection.execute("SELECT 1").fetchone()
            connection.close()
        except sqlite3.Error as exc:
            logger.error("Database health check failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Database unavailable") from exc
        return {"status": "ok", "database": "ok"}

    @app.get("/api/v1/overview")
    def overview() -> dict[str, object]:
        assets = get_asset_summaries(resolved_db_path)
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        statuses = {str(asset["freshness"]) for asset in assets}
        if not assets:
            data_status = "unavailable"
        elif statuses == {"fresh"}:
            data_status = "fresh"
        else:
            data_status = "stale"
        error_count = len(import_result["errors"])
        services = [
            {
                "name": "SQLite store",
                "status": "healthy",
                "detail": "Local historical store is available.",
                "updated_at": generated_at,
            },
            {
                "name": "CSV import",
                "status": "degraded" if error_count else "healthy",
                "detail": (
                    f"{error_count} source file(s) could not be imported."
                    if error_count
                    else f"{import_result['imported']} rows imported; existing rows are retained."
                ),
                "updated_at": generated_at,
            },
        ]
        return {
            "generated_at": generated_at,
            "data_status": data_status,
            "assets": assets,
            "services": services,
        }

    @app.get("/api/v1/assets")
    def assets(
        q: str | None = Query(default=None, max_length=100),
        asset_class: Literal["crypto", "equity"] | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        results = get_asset_summaries(resolved_db_path)
        if q:
            needle = q.casefold()
            results = [
                asset
                for asset in results
                if needle in str(asset["symbol"]).casefold()
                or needle in str(asset["name"]).casefold()
            ]
        if asset_class:
            results = [asset for asset in results if asset["asset_class"] == asset_class]
        return {"items": results[:limit], "count": min(len(results), limit)}

    @app.get("/api/v1/watchlist/crypto")
    def crypto_watchlist() -> dict[str, object]:
        return {
            "items": get_crypto_watchlist(resolved_db_path),
            "max_items": MAX_TRACKED_CRYPTO,
        }

    @app.post("/api/v1/watchlist/crypto", status_code=201)
    def add_crypto_watchlist(request: CryptoWatchlistRequest) -> dict[str, object]:
        try:
            add_crypto_watchlist_item(
                resolved_db_path,
                request.coin_id,
                request.symbol,
                request.name,
                MAX_TRACKED_CRYPTO,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "coin_id": request.coin_id,
            "symbol": request.symbol,
            "name": request.name,
            "status": "added",
        }

    @app.get("/api/v1/assets/{symbol}/bars")
    def bars(
        symbol: str,
        interval: Literal["1h", "1d"] = "1h",
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, object]:
        normalized_symbol = symbol.upper()
        if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
            raise HTTPException(status_code=422, detail="Invalid asset symbol")
        rows = get_price_bars(resolved_db_path, normalized_symbol, interval, limit)
        if not rows:
            raise HTTPException(status_code=404, detail="No bars found for this asset and interval")
        return {"symbol": normalized_symbol, "interval": interval, "bars": rows}

    @app.get("/api/v1/system/jobs")
    def system_jobs(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, object]:
        return {"items": get_collection_runs(resolved_db_path, limit)}

    @app.get("/api/v1/alerts")
    def alerts(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, object]:
        return {
            "items": get_alert_events(resolved_db_path, limit),
            "threshold_pct": configured_threshold_pct(),
            "ntfy_configured": bool(os.getenv("NTFY_TOPIC", "").strip()),
        }

    @app.get("/api/v1/analysis/{symbol}")
    def analysis_report(symbol: str) -> dict[str, object]:
        normalized_symbol = symbol.upper()
        if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
            raise HTTPException(status_code=422, detail="Invalid asset symbol")
        reports = sorted(resolved_models_dir.glob(f"*/{normalized_symbol.lower()}.json"))
        if not reports:
            return {
                "available": False,
                "symbol": normalized_symbol,
                "detail": f"No stored baseline evaluation exists for {normalized_symbol}.",
            }
        try:
            return {
                "available": True,
                "report": json.loads(reports[-1].read_text(encoding="utf-8")),
            }
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Model report unavailable for %s: %s", normalized_symbol, type(exc).__name__)
            raise HTTPException(status_code=500, detail="Model report unavailable") from exc

    frontend_dist = ROOT / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()