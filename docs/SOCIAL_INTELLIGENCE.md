# Crypto Social Intelligence

## Scope and safeguards

The social monitor collects public posts through the official X API v2 recent-search endpoint. It does not scrape X pages, place trades, or treat platform verification as proof that a claim is true. Post/event responses are marked `unverified`; rumor claims and whale-activity labels are explicitly unverified. Event classes, sentiment, confidence, impact and priority are deterministic keyword heuristics, not forecasts.

The AI adapter contract and response schema are present, but no model provider is implemented. The API reports AI as `not_configured` when enabled (the example setting) or `disabled` when `SOCIAL_AI_ENABLED=false`; it does not fabricate summaries. X collection and notification channels are disabled or unconfigured until explicitly configured.

The API is unauthenticated and intended for loopback use only. The test-notification endpoint sends a real notification when the selected channel is enabled and configured. Do not expose this service to a network or public internet without authentication, access control, and a reviewed deployment design.

## Configuration

Set values in the private `.env`; do not commit credentials:

```dotenv
X_API_ENABLED=false
X_API_BEARER_TOKEN=
SOCIAL_MONITOR_INTERVAL_SECONDS=300
SOCIAL_MAX_PAGES=5
SOCIAL_AI_ENABLED=true
SOCIAL_ALERT_MIN_SCORE=60

NTFY_ENABLED=true
NTFY_BASE_URL=
NTFY_TOPIC=
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

The watchlist comes from the existing configured assets and is used to form recent-search queries. X recent search only covers the provider's recent window (up to seven days). The monitor paginates results, stores provider cursors and deduplicates posts. A page cap leaves the continuation token available for the next run. Provider retry/backoff honors retry timing where supplied; respect X access level, quotas, terms and retention requirements.

No live collection is enabled by default. Configure a valid X bearer token and explicitly set `X_API_ENABLED=true` to collect. Start a single collection manually with:

```bash
source .venv/bin/activate
python -c 'from backend.social import collect_social_once; print(collect_social_once())'
```

The long-running worker validates its interval (30–86400 seconds), takes a database-adjacent exclusive lock, and handles SIGTERM/SIGINT:

```bash
python -m backend.run_social_monitor
```

`deploy/market-social-monitor.service` is provided but is not enabled automatically. Review the unit and database path before installing or starting it. Collection is intentionally not triggered by API reads.

## API and dashboard

- `GET /api/social/health` reports collection, AI and notification configuration states without returning credentials.
- `GET /api/social/posts` supports asset, source type, event category, sentiment, priority, time range and bounded pagination filters.
- `GET /api/social/events` supports event filters and pagination.
- `GET /api/social/statistics` returns stored post/event counts and rule distributions.
- `GET /api/social/alerts` and `GET /api/social/notification-history` show alert and channel-delivery history.
- `/api/social/sources` supports listing, creation, patching and deactivation of source records.
- `/api/social/alert-rules` supports listing and creating local notification rules.
- `POST /api/social/test-notification` sends a real test through an enabled, configured channel; it never simulates success.

The Social Intelligence dashboard displays the post feed, event timeline, delivery history, collection status and counts. Its management sections create/deactivate sources and create alert rules through the API. It labels claims unverified and displays only content returned by the local API; no demo posts are inserted.

## Alerts and delivery

Rule processing supports asset mentions, event categories, sentiment and priority thresholds, quiet hours and cooldowns. Default rules are local configuration and do not imply independent fact-checking. ntfy and Telegram dispatch are isolated per channel; delivery attempts and outcomes are recorded, and retryable failures are retried with a delay and a bounded attempt count. Disabled or missing credentials are reported as `disabled` or `not_configured`, not as successful delivery.

Telegram messages use HTML escaping. Credentials are not included in API responses or application log messages. Treat the `.env`, SQLite database and backups as sensitive operational data.

## Limits

- Classification and sentiment are keyword-based and can be wrong or miss context, sarcasm, impersonation and multilingual content.
- A provider's verified-account indicator is not independent source validation. Default source reputation is neutral and must not be interpreted as a credibility guarantee.
- Whale activity is never marked verified by this system; independent on-chain evidence is not integrated.
- No on-chain verification, autonomous AI analysis, recommendation engine, trade execution or live provider validation is implemented.
- Systemd behavior and live X/Telegram/ntfy delivery must be separately validated by an operator; automated tests use fake providers and never send external notifications.
