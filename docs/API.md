# API

Base URL: `http://127.0.0.1:8000`. JSON timestamps are UTC ISO-8601. The service defaults to loopback and requires bearer authentication for data and management APIs. Only `/healthz`, login and refresh are public.

## Endpoints

### Authentication

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/login` | Verify username/password and issue access + refresh tokens |
| POST | `/api/auth/refresh` | Rotate a refresh token and issue a new access token |
| GET | `/api/auth/me` | Return the authenticated account |
| POST | `/api/auth/logout` | Revoke the current session |
| GET/POST | `/api/auth/users` | ADMIN: list or create accounts |
| PATCH | `/api/auth/users/{user_id}` | ADMIN: change role, active state or password |
| GET | `/api/auth/audit` | ADMIN: paginated authentication/account audit records |
| GET | `/api/auth/command-audit` | ADMIN: paginated ntfy command execution history |

`POST /api/auth/login` accepts `{"username":"...","password":"..."}`. Include `Authorization: Bearer <access_token>` on protected requests. Access tokens expire after 15 minutes; refresh tokens rotate on use and expire after 14 days. Reuse of a previously consumed refresh token revokes that session. Passwords are Argon2-hashed. Roles are `ADMIN`, `USER` and `READ_ONLY`; read-only accounts can read but cannot make API mutations. Only ADMIN can manage accounts. The first ADMIN must be created locally with `python -m backend.auth bootstrap-admin --username <name>`; there is no public registration.

The API fails closed when `AUTH_TOKEN_SECRET` is absent or shorter than 32 characters. Generate a private value (for example `openssl rand -hex 32`) and store it in `.env`. `CORS_ALLOWED_ORIGINS` is a comma-separated allowlist and defaults to the local Vite origins. API rate limiting is process-local (10 login attempts and 120 API requests per client IP per minute), so it is not a substitute for an edge proxy when running multiple workers.

### Market and intelligence

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | API and SQLite readiness |
| GET | `/api/v1/overview` | Asset summary, services and global freshness |
| GET | `/api/v1/assets?q=&asset_class=&limit=` | Filtered current asset list; limit 1–500 |
| GET | `/api/v1/assets/{symbol}/bars?interval=1h&limit=96` | Bounded historical bars; interval `1h` or `1d`, limit 1–500 |
| GET | `/api/v1/system/jobs?limit=20` | Recent collection outcomes; limit 1–100 |
| GET | `/api/v1/alerts?limit=100` | Alert history, configured threshold, ntfy and telegram configured booleans |
| GET | `/api/v1/news?q=&category=&asset=&limit=50&offset=0` | Search/filter collected RSS/Atom articles; limit 1–100 |
| GET | `/api/v1/news/{article_id}` | Retrieve one stored article |
| GET | `/api/v1/analysis/{symbol}` | Stored model report or `{available:false,...}` when none exists |
| GET | `/api/social/health` | Social collection, AI and notification configuration state |
| GET | `/api/social/watchlist` | Assets used to form social search queries |
| GET | `/api/social/posts?asset=&source_type=&category=&sentiment=&priority=&since=&until=&limit=50&offset=0` | Filtered collected posts and their event labels |
| GET | `/api/social/events?asset=&category=&sentiment=&priority=&since=&until=&limit=50&offset=0` | Filtered classified events |
| GET | `/api/social/statistics` | Post/event/source counts and rule distributions |
| GET | `/api/social/alerts?limit=50&offset=0` | Social alert history and per-channel delivery outcomes |
| GET/POST/PATCH/DELETE | `/api/social/sources[/{source_id}]` | Manage local source registry; delete deactivates the source |
| GET/POST | `/api/social/alert-rules` | List or add local alert rules |
| GET | `/api/social/notification-history?limit=50&offset=0` | Per-channel delivery attempts |
| POST | `/api/social/test-notification` | ADMIN: send a real test to an enabled, configured channel |

`/` and frontend assets are served from `frontend/dist/` when that directory exists.

## Overview shape

```json
{
  "generated_at": "2026-10-16T16:00:00+00:00",
  "data_status": "fresh",
  "assets": [{
    "symbol": "BTC",
    "name": "Bitcoin",
    "asset_class": "crypto",
    "price": null,
    "change_pct": null,
    "volume": null,
    "currency": "USD",
    "source": "CoinGecko",
    "timestamp": "2026-10-16T15:30:00+00:00",
    "freshness": "fresh"
  }],
  "services": []
}
```

This is a schema example, not live market data. `price`/`volume` may be null; `freshness` is `fresh`, `stale`, or `unavailable`. Crypto freshness currently expires after two hours; equity daily bars after five days.

## Errors

- `422`: invalid symbol or query bound.
- `404`: no stored bar for the requested symbol/interval.
- `404`: news article ID does not exist.
- `422`: invalid news category, asset symbol, or query bound.
- `500`: unreadable stored model report.
- `503`: local database unavailable on `/healthz`.
- `401`: missing, invalid or expired authentication token.
- `403`: account role does not permit the requested mutation.
- `429`: API or login rate limit exceeded.
- `503`: authentication signing configuration or database unavailable.

A missing model evaluation is a successful response with `available: false`; it is not a prediction failure. No provider error text or credentials are returned in API responses.

The news list response includes `items`, total `count`, `limit`, `offset`, and supported `categories`. Articles expose source-provided title, summary/content, publication time (UTC when available), source label, original article URL, language when present, rule-based category, and matched tracked assets. It does not expose configured feed URLs. The detail endpoint returns the same fields for one article. Search covers title, summary, and source-provided content; `asset` is an exact symbol filter.

News collection is a separate command (`python -m backend.run_news_collector`) configured by the `MARKET_NEWS_FEEDS` JSON environment variable. It is not run by an API request and does not depend on AI/model inference.

Social collection is separate from API reads and runs through `python -m backend.run_social_monitor` or `collect_social_once`. `POST /api/social/test-notification` can cause an external side effect if the requested channel is configured and enabled; it is not a dry run. The authenticated API should remain loopback-only unless deployment security is separately reviewed. When `GEMINI_API_KEY` is configured, `GeminiAIProvider` acts as the active AI provider, generating summaries and sentiment labels with automatic heuristic fallback when quota is unavailable.

The optional ntfy listener is a separate worker. Its exact command allowlist, HMAC envelope, nonce policy and operations are documented in [COMMAND_CENTER.md](./COMMAND_CENTER.md). It is disabled by default, does not execute arbitrary commands, and does not expose service start/stop/restart operations.

## Telegram Bot Interface

An interactive command interface is available via `python -m backend.telegram_bot` (or `market-telegram-bot.service`), strictly restricted to requests coming from the configured `TELEGRAM_CHAT_ID`:
- `/status`: Returns `/healthz` readiness, overall data freshness, and most recent collector job outcomes.
- `/overview`: Lists watchlist asset prices and 24h change percentages directly from `GET /api/v1/overview`.
- `/news [symbol]`: Lists top 3 latest articles matching the specified asset.
- `/briefing`: Generates a real-time summary of portfolio status and recent market news.
