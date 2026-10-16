# API

Base URL: `http://127.0.0.1:8000`. JSON timestamps are UTC ISO-8601. The API is local-only and currently unauthenticated.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | API and SQLite readiness |
| GET | `/api/v1/overview` | Asset summary, services and global freshness |
| GET | `/api/v1/assets?q=&asset_class=&limit=` | Filtered current asset list; limit 1–500 |
| GET | `/api/v1/assets/{symbol}/bars?interval=1h&limit=96` | Bounded historical bars; interval `1h` or `1d`, limit 1–500 |
| GET | `/api/v1/system/jobs?limit=20` | Recent collection outcomes; limit 1–100 |
| GET | `/api/v1/alerts?limit=100` | Alert history, configured threshold and ntfy configured boolean |
| GET | `/api/v1/analysis/{symbol}` | Stored model report or `{available:false,...}` when none exists |

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
- `500`: unreadable stored model report.
- `503`: local database unavailable on `/healthz`.

A missing model evaluation is a successful response with `available: false`; it is not a prediction failure. No provider error text or credentials are returned in API responses.
