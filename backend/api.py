import logging
import json
import os
import re
import sqlite3
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlite3 import IntegrityError

from backend.auth import (
    AccountConflictError,
    authenticate,
    authenticate_access_token,
    create_user,
    list_audit_logs,
    list_ntfy_command_audit,
    list_users,
    refresh_session,
    revoke_session,
    update_user,
)
from backend.database import (
    get_alert_events,
    get_collection_runs,
    get_crypto_watchlist,
    get_asset_summaries,
    get_news_article,
    get_news_articles,
    add_crypto_watchlist_item,
    get_price_bars,
    import_csv_data,
    initialize_database,
)
from backend.alerts import configured_threshold_pct
from backend.news import ARTICLE_CATEGORIES
from backend.social import (
    SOCIAL_EVENT_CATEGORIES,
    SOCIAL_PRIORITIES,
    SOCIAL_SENTIMENTS,
    SOURCE_CATEGORIES,
    create_social_alert_rule,
    create_social_source,
    deactivate_social_source,
    get_collection_status,
    get_notification_history,
    get_social_alert_rules,
    get_social_alerts,
    get_social_events,
    get_social_posts,
    get_social_source,
    get_social_sources,
    get_social_statistics,
    get_social_watchlist,
    test_social_notification,
    update_social_source,
)
from backend.settings import DATA_DIR, DB_PATH

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9._:-]{1,24}$")
MAX_TRACKED_CRYPTO = 10


class CryptoWatchlistRequest(BaseModel):
    coin_id: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    symbol: str = Field(min_length=2, max_length=12, pattern=r"^[A-Z0-9]+$")
    name: str = Field(min_length=1, max_length=80)


class SocialSourceRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_]+$")
    display_name: str = Field(min_length=1, max_length=120)
    category: Literal[
        "official_project",
        "official_exchange",
        "regulator",
        "news",
        "analyst",
        "influencer",
        "community",
        "unknown",
    ] = "unknown"
    verified: bool = False
    reputation_score: float = Field(default=50, ge=0, le=100)
    supported_assets: list[str] = Field(default_factory=list, max_length=50)
    active: bool = True


class SocialSourcePatch(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_]+$")
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    category: Literal[
        "official_project",
        "official_exchange",
        "regulator",
        "news",
        "analyst",
        "influencer",
        "community",
        "unknown",
    ] | None = None
    verified: bool | None = None
    reputation_score: float | None = Field(default=None, ge=0, le=100)
    supported_assets: list[str] | None = Field(default=None, max_length=50)
    active: bool | None = None


class SocialAlertRuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    minimum_score: float = Field(default=60, ge=0, le=100)
    assets: list[str] = Field(default_factory=list, max_length=50)
    categories: list[str] = Field(default_factory=list, max_length=50)
    cooldown_seconds: int = Field(default=900, ge=0, le=604800)
    quiet_hours_start: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    quiet_hours_end: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="UTC", min_length=1, max_length=80)


class SocialTestNotificationRequest(BaseModel):
    channel: Literal["ntfy", "telegram"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=48)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=200)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=48)
    password: str = Field(min_length=12, max_length=128)
    role: Literal["ADMIN", "USER", "READ_ONLY"] = "USER"


class UserUpdateRequest(BaseModel):
    role: Literal["ADMIN", "USER", "READ_ONLY"] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)


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
    allowed_origins = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOWED_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173",
        ).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Accept", "Authorization", "Content-Type"],
    )

    rate_buckets: dict[tuple[str, str], deque[float]] = {}

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        response: JSONResponse
        path = request.url.path
        if path.startswith("/api/"):
            now = time.monotonic()
            client_host = request.client.host if request.client else "unknown"
            bucket_name = "login" if path == "/api/auth/login" else "api"
            bucket_key = (client_host, bucket_name)
            bucket = rate_buckets.setdefault(bucket_key, deque())
            window_seconds = 60.0
            while bucket and now - bucket[0] >= window_seconds:
                bucket.popleft()
            if len(bucket) >= (10 if bucket_name == "login" else 120):
                response = JSONResponse(
                    {"detail": "Too many requests; try again later"},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
            else:
                bucket.append(now)
                if len(rate_buckets) > 10_000:
                    for key in list(rate_buckets)[:1_000]:
                        rate_buckets.pop(key, None)

                public_auth_paths = {"/api/auth/login", "/api/auth/refresh"}
                if request.method == "OPTIONS" or path in public_auth_paths:
                    response = await call_next(request)
                else:
                    authorization = request.headers.get("authorization", "")
                    scheme, _, token = authorization.partition(" ")
                    if scheme.casefold() != "bearer" or not token:
                        response = JSONResponse(
                            {"detail": "Authentication required"},
                            status_code=401,
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    else:
                        try:
                            principal = authenticate_access_token(resolved_db_path, token)
                        except RuntimeError as exc:
                            logger.error("Authentication configuration error: %s", str(exc))
                            response = JSONResponse(
                                {"detail": "Authentication is not configured"},
                                status_code=503,
                            )
                        except sqlite3.Error as exc:
                            logger.error("Authentication database error: %s", type(exc).__name__)
                            response = JSONResponse(
                                {"detail": "Authentication service unavailable"},
                                status_code=503,
                            )
                        else:
                            if principal is None:
                                response = JSONResponse(
                                    {"detail": "Invalid or expired access token"},
                                    status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"},
                                )
                            elif (
                                request.method not in {"GET", "HEAD", "OPTIONS"}
                                and principal["role"] == "READ_ONLY"
                                and path != "/api/auth/logout"
                            ):
                                response = JSONResponse(
                                    {"detail": "Read-only accounts cannot modify data"},
                                    status_code=403,
                                )
                            else:
                                request.state.principal = principal
                                response = await call_next(request)
        else:
            response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; object-src 'none'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'",
        )
        if path.startswith("/api/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_admin(request: Request) -> dict[str, object]:
        principal = getattr(request.state, "principal", None)
        if principal is None or principal["role"] != "ADMIN":
            raise HTTPException(status_code=403, detail="Administrator role required")
        return principal

    @app.post("/api/auth/login")
    def login(request: LoginRequest) -> dict[str, object]:
        try:
            tokens = authenticate(resolved_db_path, request.username, request.password)
        except RuntimeError as exc:
            logger.error("Authentication configuration error: %s", str(exc))
            raise HTTPException(status_code=503, detail="Authentication is not configured") from exc
        if tokens is None:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        return tokens

    @app.post("/api/auth/refresh")
    def refresh(request: RefreshRequest) -> dict[str, object]:
        try:
            tokens = refresh_session(resolved_db_path, request.refresh_token)
        except RuntimeError as exc:
            logger.error("Authentication configuration error: %s", str(exc))
            raise HTTPException(status_code=503, detail="Authentication is not configured") from exc
        if tokens is None:
            raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
        return tokens

    @app.get("/api/auth/me")
    def current_user(request: Request) -> dict[str, object]:
        principal = request.state.principal
        return {
            "id": principal["id"],
            "username": principal["username"],
            "role": principal["role"],
        }

    @app.post("/api/auth/logout")
    def logout(request: Request) -> dict[str, str]:
        principal = request.state.principal
        revoke_session(
            resolved_db_path,
            str(principal["session_id"]),
            int(principal["id"]),
        )
        return {"status": "logged_out"}

    @app.get("/api/auth/users")
    def users(request: Request) -> dict[str, object]:
        require_admin(request)
        return {"items": list_users(resolved_db_path)}

    @app.post("/api/auth/users", status_code=201)
    def add_user(request: Request, body: UserCreateRequest) -> dict[str, object]:
        principal = require_admin(request)
        try:
            return create_user(
                resolved_db_path,
                body.username,
                body.password,
                body.role,
                actor_user_id=int(principal["id"]),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AccountConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.patch("/api/auth/users/{user_id}")
    def patch_user(
        user_id: int,
        request: Request,
        body: UserUpdateRequest,
    ) -> dict[str, object]:
        principal = require_admin(request)
        values = body.model_dump(exclude_unset=True)
        try:
            updated = update_user(
                resolved_db_path,
                user_id,
                **values,
                actor_user_id=int(principal["id"]),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if updated is None:
            raise HTTPException(status_code=404, detail="User not found")
        return updated

    @app.get("/api/auth/audit")
    def audit_logs(
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        require_admin(request)
        items, count = list_audit_logs(resolved_db_path, limit=limit, offset=offset)
        return {"items": items, "count": count, "limit": limit, "offset": offset}

    @app.get("/api/auth/command-audit")
    def command_audit_logs(
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        require_admin(request)
        items, count = list_ntfy_command_audit(resolved_db_path, limit=limit, offset=offset)
        return {"items": items, "count": count, "limit": limit, "offset": offset}

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

    @app.get("/api/social/health")
    def social_health() -> dict[str, object]:
        try:
            connection = sqlite3.connect(resolved_db_path, timeout=2)
            connection.execute("SELECT 1").fetchone()
            connection.close()
        except sqlite3.Error as exc:
            logger.error("Social health check failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Database unavailable") from exc
        state = get_collection_status(resolved_db_path)
        state["database"] = "ok"
        state["notifications"] = {
            "ntfy": (
                "disabled"
                if os.getenv("NTFY_ENABLED", "true").strip().casefold() in {"0", "false", "no", "off"}
                else "configured"
                if os.getenv("NTFY_TOPIC", "").strip()
                else "not_configured"
            ),
            "telegram": (
                "disabled"
                if os.getenv("TELEGRAM_ENABLED", "false").strip().casefold() not in {"1", "true", "yes", "on"}
                else "configured"
                if os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
                and os.getenv("TELEGRAM_CHAT_ID", "").strip()
                else "not_configured"
            ),
        }
        return state

    @app.get("/api/social/watchlist")
    def social_watchlist() -> dict[str, object]:
        return {"items": get_social_watchlist(resolved_db_path)}

    @app.get("/api/social/posts")
    def social_posts(
        asset: str | None = Query(default=None, max_length=24),
        source_type: str | None = Query(default=None),
        category: str | None = Query(default=None, max_length=40),
        sentiment: str | None = Query(default=None),
        priority: str | None = Query(default=None),
        since: str | None = Query(default=None, max_length=40),
        until: str | None = Query(default=None, max_length=40),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        normalized_asset = asset.upper() if asset else None
        if normalized_asset and not SYMBOL_PATTERN.fullmatch(normalized_asset):
            raise HTTPException(status_code=422, detail="Invalid asset symbol")
        if category and category not in SOCIAL_EVENT_CATEGORIES:
            raise HTTPException(status_code=422, detail="Invalid social event category")
        if source_type and source_type not in SOURCE_CATEGORIES:
            raise HTTPException(status_code=422, detail="Invalid source type")
        if sentiment and sentiment not in SOCIAL_SENTIMENTS:
            raise HTTPException(status_code=422, detail="Invalid sentiment")
        if priority and priority not in SOCIAL_PRIORITIES:
            raise HTTPException(status_code=422, detail="Invalid priority")
        try:
            items, count = get_social_posts(
                resolved_db_path,
                asset=normalized_asset,
                source_type=source_type,
                category=category,
                sentiment=sentiment,
                priority=priority,
                since=since,
                until=until,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid date range") from exc
        return {"items": items, "count": count, "limit": limit, "offset": offset}

    @app.get("/api/social/events")
    def social_events(
        asset: str | None = Query(default=None, max_length=24),
        category: str | None = Query(default=None, max_length=40),
        sentiment: str | None = Query(default=None),
        priority: str | None = Query(default=None),
        since: str | None = Query(default=None, max_length=40),
        until: str | None = Query(default=None, max_length=40),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        normalized_asset = asset.upper() if asset else None
        if normalized_asset and not SYMBOL_PATTERN.fullmatch(normalized_asset):
            raise HTTPException(status_code=422, detail="Invalid asset symbol")
        if category and category not in SOCIAL_EVENT_CATEGORIES:
            raise HTTPException(status_code=422, detail="Invalid social event category")
        if sentiment and sentiment not in SOCIAL_SENTIMENTS:
            raise HTTPException(status_code=422, detail="Invalid sentiment")
        if priority and priority not in SOCIAL_PRIORITIES:
            raise HTTPException(status_code=422, detail="Invalid priority")
        try:
            items, count = get_social_events(
                resolved_db_path,
                asset=normalized_asset,
                category=category,
                sentiment=sentiment,
                priority=priority,
                since=since,
                until=until,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid date range") from exc
        return {"items": items, "count": count, "limit": limit, "offset": offset}

    @app.get("/api/social/alerts")
    def social_alerts(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        items, count = get_social_alerts(resolved_db_path, limit, offset)
        return {"items": items, "count": count, "limit": limit, "offset": offset}

    @app.get("/api/social/sources")
    def social_sources(
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        items, count = get_social_sources(resolved_db_path, limit, offset)
        return {"items": items, "count": count, "limit": limit, "offset": offset}

    @app.post("/api/social/sources", status_code=201)
    def add_social_source(request: SocialSourceRequest) -> dict[str, object]:
        _validate_social_assets(request.supported_assets)
        try:
            return create_social_source(
                resolved_db_path,
                request.model_dump(),
            )
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Social source already exists") from exc

    @app.patch("/api/social/sources/{source_id}")
    def patch_social_source(
        source_id: int,
        request: SocialSourcePatch,
    ) -> dict[str, object]:
        values = request.model_dump(exclude_unset=True)
        if "supported_assets" in values and values["supported_assets"] is not None:
            _validate_social_assets(values["supported_assets"])
        source = update_social_source(resolved_db_path, source_id, values)
        if source is None:
            raise HTTPException(status_code=404, detail="Social source not found")
        return source

    @app.delete("/api/social/sources/{source_id}")
    def remove_social_source(source_id: int) -> dict[str, str]:
        if not deactivate_social_source(resolved_db_path, source_id):
            raise HTTPException(status_code=404, detail="Social source not found")
        return {"status": "inactive"}

    @app.post("/api/social/alert-rules", status_code=201)
    def add_social_alert_rule(request: SocialAlertRuleRequest) -> dict[str, object]:
        _validate_social_assets(request.assets)
        invalid_categories = set(request.categories) - set(SOCIAL_EVENT_CATEGORIES)
        if invalid_categories:
            raise HTTPException(status_code=422, detail="Invalid social event category")
        if bool(request.quiet_hours_start) != bool(request.quiet_hours_end):
            raise HTTPException(status_code=422, detail="Both quiet-hour bounds are required")
        try:
            ZoneInfo(request.timezone)
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=422, detail="Invalid timezone") from exc
        try:
            return create_social_alert_rule(resolved_db_path, request.model_dump())
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Alert rule name already exists") from exc

    @app.get("/api/social/alert-rules")
    def social_alert_rules() -> dict[str, object]:
        return {"items": get_social_alert_rules(resolved_db_path)}

    @app.post("/api/social/test-notification")
    def social_test_notification(
        request: Request,
        body: SocialTestNotificationRequest,
    ) -> dict[str, object]:
        require_admin(request)
        result = test_social_notification(resolved_db_path, body.channel)
        if result["status"] == "not_configured":
            raise HTTPException(status_code=409, detail=f"{body.channel} is not configured")
        if result["status"] == "disabled":
            raise HTTPException(status_code=409, detail=f"{body.channel} notifications are disabled")
        if result["status"] != "sent":
            raise HTTPException(status_code=502, detail=f"{body.channel} delivery failed")
        return result

    @app.get("/api/social/notification-history")
    def social_notification_history(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        items, count = get_notification_history(resolved_db_path, limit, offset)
        return {"items": items, "count": count, "limit": limit, "offset": offset}

    @app.get("/api/social/statistics")
    def social_statistics() -> dict[str, object]:
        return get_social_statistics(resolved_db_path)

    @app.get("/api/v1/news")
    def news(
        q: str | None = Query(default=None, max_length=200),
        category: str | None = Query(default=None, max_length=32),
        asset: str | None = Query(default=None, max_length=24),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        if category and category not in ARTICLE_CATEGORIES:
            raise HTTPException(status_code=422, detail="Invalid news category")
        normalized_asset = asset.upper() if asset else None
        if normalized_asset and not SYMBOL_PATTERN.fullmatch(normalized_asset):
            raise HTTPException(status_code=422, detail="Invalid asset symbol")
        items, count = get_news_articles(
            resolved_db_path,
            query=q.strip() if q and q.strip() else None,
            category=category,
            asset=normalized_asset,
            limit=limit,
            offset=offset,
        )
        return {
            "items": items,
            "count": count,
            "limit": limit,
            "offset": offset,
            "categories": list(ARTICLE_CATEGORIES),
        }

    @app.get("/api/v1/news/{article_id}")
    def news_article(article_id: int) -> dict[str, object]:
        if article_id < 1:
            raise HTTPException(status_code=422, detail="Invalid article ID")
        article = get_news_article(resolved_db_path, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="News article not found")
        return article

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


def _validate_social_assets(symbols: list[str]) -> None:
    if any(not SYMBOL_PATTERN.fullmatch(symbol.upper()) for symbol in symbols):
        raise HTTPException(status_code=422, detail="Invalid asset symbol")


app = create_app()