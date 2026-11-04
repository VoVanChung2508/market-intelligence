# Market Intelligence

Educational and research workspace for market data. It is not financial advice, does not promise returns, and does not place real-money trades.

## Current status

This repository has a local FastAPI/SQLite data API, a React dashboard, idempotent CSV import, market provider adapters, a time-ordered AAPL baseline, ntfy price-move alerts, RSS/Atom news collection and a heuristic crypto social monitor. Social Intelligence includes official X API v2 recent search, source/rule management APIs, event labeling, notification delivery history and a dashboard feed. Live X collection is disabled by default; AI analysis, portfolio simulation, backtesting, broad watchlists, and production security/performance certification remain incomplete.

The existing CSVs remain in `data/raw/`. The new SQLite database, model, and evaluation artifacts are written under the ignored `data/processed/` directory. The current boundary-purged AAPL baseline failed its validation gate, so the dashboard withholds a direction signal.

## Prerequisites

- Ubuntu/Linux, Python 3.14, Node.js 22, npm 9 or compatible.
- No GPU or external database is required.
- Network access is needed for market/news collection and optional ntfy delivery.

## Local setup

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm --prefix frontend ci
```

The current workspace already has a private `.env`. Do not overwrite it. On a new checkout, copy `.env.example` only when `.env` does not exist, then set permissions to `600` and add provider settings as needed. Never commit `.env`.

```bash
cp .env.example .env   # only if .env is absent
chmod 600 .env
npm --prefix frontend run build
```

## Run in development

Start the API in one terminal:

```bash
source .venv/bin/activate
uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

Start the dashboard in another terminal:

```bash
npm --prefix frontend run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`. The API listens on `http://127.0.0.1:8000`; it is intentionally bound to loopback and has no authentication layer.

## Collect and train

```bash
source .venv/bin/activate
python -m backend.run_collector
python -m backend.train_model AAPL
```

The collector stores crypto data from CoinGecko. Stock data is collected only when `ALPHAVANTAGE_API_KEY` is configured; the free endpoint is guarded by a 20-hour cooldown. The legacy `src/collect.py` remains for compatibility and still uses yfinance/Yahoo Finance terms; the new scheduler runs `backend.run_collector` instead.

Model training refuses to overwrite an existing versioned artifact. `baseline-logistic-v1` is retained as a superseded pre-purge evaluation; `baseline-logistic-v2` is the current boundary-purged evaluation. Create a new explicit version before retraining.

To collect news, configure `MARKET_NEWS_FEEDS` in the private `.env` as a JSON array, for example:

```dotenv
MARKET_NEWS_FEEDS='[{"name":"Example Wire","url":"https://example.com/feed.xml"}]'
```

Then run `python -m backend.run_news_collector`. Only HTTPS RSS/Atom feeds are accepted; the command uses bounded requests, does not follow redirects, and stores feed-provided text only. Categories are deterministic keyword labels, not AI analysis. The news collector is manual and has no systemd timer yet.

For social monitoring, review [docs/SOCIAL_INTELLIGENCE.md](docs/SOCIAL_INTELLIGENCE.md) and configure the X API and optional notification channels in `.env`. X and Telegram are disabled unless explicitly enabled, and no credentials are included in this repository. To run the worker manually, use `python -m backend.run_social_monitor`. Event labels are heuristics, claims are unverified, and the AI provider is not implemented. The social systemd unit is provided but is not enabled automatically.

## Verify

```bash
source .venv/bin/activate
python -m pytest -q
python -m pip check
npm --prefix frontend run build
npm --prefix frontend audit --omit=dev
```

Latest verification results are recorded in [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

## Operations and recovery

Use the user-level systemd units in `deploy/` after reviewing them. The collector timer runs every 30 minutes; the stock provider cooldown prevents the free Alpha Vantage quota from being consumed on every run. Commands, logs, SQLite backup/restore and limitations are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

The repository is Git-managed. No backup archive is present in the current checkout; create and verify a SQLite backup before relying on recovery procedures.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [API contract](docs/API.md)
- [Data sources and limits](docs/DATA_SOURCES.md)
- [News Intelligence](docs/NEWS_INTELLIGENCE.md)
- [Crypto Social Intelligence](docs/SOCIAL_INTELLIGENCE.md)
- [AI model report](docs/AI_MODEL_REPORT.md)
- [Test report](docs/TEST_REPORT.md)
- [Deployment and recovery](docs/DEPLOYMENT.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
