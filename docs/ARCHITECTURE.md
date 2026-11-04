# Architecture

## Runtime shape

```mermaid
flowchart LR
  CG[CoinGecko public API] --> C[Provider collector]
  AV[Alpha Vantage daily API] --> C
  RSS[Configured RSS/Atom feeds] --> NC[News collector]
  CSV[Existing raw CSVs] --> I[Idempotent CSV importer]
  C --> V[Validation and normalization]
  I --> V
  V --> DB[(SQLite WAL store)]
  DB --> API[FastAPI on loopback]
  API --> UI[React + Vite dashboard]
  DB --> ML[Time-ordered baseline]
  ML --> ART[Versioned model and report]
  ART --> API
  DB --> ALERT[Threshold alert evaluator]
  ALERT --> NTFY[ntfy]
  NC --> DB
  TIMER[systemd user timer] --> C
```

The deployment is local-first: no PostgreSQL, Docker, GPU, Redis, or external queue is required. FastAPI and the production static dashboard share port 8000 after `npm run build`; development uses Vite on 5173. Both services bind to `127.0.0.1` by default.

## Modules

- `backend/database.py`: SQLite schema, CSV import, freshness metadata, job and alert history.
- `backend/collector.py`: CoinGecko/Alpha Vantage adapters, bounded HTTP retries/timeouts, per-provider failure isolation.
- `backend/analysis.py` and `backend/train_model.py`: leakage-aware features, chronological splits, baseline comparison, versioned artifacts.
- `backend/alerts.py`: interval-move rule, deduplication, ntfy delivery status.
- `backend/news.py` and `backend/run_news_collector.py`: configurable RSS/Atom feed adapter, bounded fetch, normalization, deterministic categorization, asset matching and deduplication.
- `backend/api.py`: health, overview, assets, bars, job history, alerts, news, model report, and built frontend.
- `frontend/src/`: typed API client, navigation, tables, chart, market/service/AI/alert/news states.
- `deploy/`: systemd user service/timer templates.

## Data model

```mermaid
erDiagram
  ASSETS ||--o{ PRICE_BARS : has
  ASSETS ||--o{ ALERT_EVENTS : triggers
  NEWS_ARTICLES ||--o{ NEWS_ARTICLE_ASSETS : mentions
  ASSETS ||--o{ NEWS_ARTICLE_ASSETS : mentioned_in
  ASSETS {
    text symbol PK
    text name
    text asset_class
    text currency
  }
  PRICE_BARS {
    text symbol FK
    text interval
    text timestamp
    real open
    real high
    real low
    real close
    real volume
    text source
    text ingested_at
  }
  COLLECTION_RUNS {
    integer id PK
    text provider
    text symbol
    text status
    text started_at
    text completed_at
    integer rows_received
    integer rows_inserted
    text error_summary
  }
  ALERT_EVENTS {
    text event_key PK
    text symbol
    text interval
    text rule
    text timestamp
    real change_pct
    text delivery_status
    text delivery_error
  }
  NEWS_ARTICLES {
    integer id PK
    text canonical_url UK
    text title
    text summary
    text content
    text published_at
    text source
    text category
    text content_fingerprint UK
  }
  NEWS_ARTICLE_ASSETS {
    integer article_id FK
    text symbol FK
  }
```

Prices use UTC ISO-8601 timestamps. The key `(symbol, interval, timestamp, source)` makes ingestion append-only and idempotent; conflicts are ignored rather than overwriting historical values. SQLite WAL and a busy timeout support concurrent dashboard reads while a scheduled writer runs.

News tables are added idempotently by the existing `CREATE TABLE IF NOT EXISTS` initialization; no existing market rows are rewritten. News deduplication uses canonical URLs, content fingerprints, and same-day normalized-title matching. It is not a versioned migration framework.

## Failure and recovery behavior

Provider failures are logged by provider and do not prevent another provider from being attempted. HTTP GET retries are bounded and respect `Retry-After`; all requests have connect/read timeouts. A missing stock key is recorded as a skipped job. Raw CSVs are read but never rewritten by the new backend. SQLite and model artifacts live under `data/processed/` and should be backed up before restore/retraining.

News feed failures are isolated per feed. RSS/Atom requests require HTTPS, have a 2 MiB response cap, a fixed timeout, and do not follow redirects. News collection does not call model inference. AI summaries, sentiment, event extraction, and scheduled news collection are not implemented.

## Security boundary

The API has no login/authentication and is safe only as a local research tool while loopback-bound. CORS permits the two local Vite origins and GET only. Provider secrets are loaded from environment/`.env`, not source or responses. Parameterized SQL is used for request-controlled filters. Do not expose port 8000 to a LAN or public network without adding authentication, authorization, TLS, request limits, and security review.
