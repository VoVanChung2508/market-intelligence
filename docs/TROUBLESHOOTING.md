# Troubleshooting

## Dashboard reports API unavailable

Check `systemctl --user status market-api.service` and `journalctl --user -u market-api.service -n 100 --no-pager`. Verify `curl --fail http://127.0.0.1:8000/healthz`. In development, start `uvicorn backend.api:app --host 127.0.0.1 --port 8000`; in a second terminal start Vite on port 5173.

## No assets or bars

Check that `data/raw/*.csv` files exist and have valid timestamps/prices. On startup the importer reads these CSVs into SQLite without replacing existing rows. Inspect `GET /api/v1/overview`, `GET /api/v1/system/jobs`, and collector logs. Existing rows are ignored by the unique source/timestamp key.

## Crypto stale or collector errors

Provider quotas/network access can change. Check collector job records and logs, then retry later. CoinGecko calls use bounded retries/timeouts. A failed CoinGecko call does not mark retained historical rows as fresh.

## AAPL is skipped

Set `ALPHAVANTAGE_API_KEY` in the private `.env`. A missing key is a deliberate skipped job. After a success or failure, another stock API attempt is suppressed for 20 hours to stay below the free request quota.

## Alerts show not configured

Set a private `NTFY_TOPIC` and optional `NTFY_SERVER`; restart the API/collector process to reload environment values. Topics are bearer-like secrets on public ntfy servers. Do not paste them into logs, screenshots, or issue reports. Without a topic, alert events are retained with delivery state `not_configured`.

## AI Analysis shows no validated signal

This is expected for the current AAPL baseline v2: measured accuracy did not beat the majority baseline on both chronological validation and holdout test. Do not infer direction or trading value from the raw model output. The model CLI also refuses to overwrite an existing version artifact.

## Port or permissions problems

The services bind to loopback ports 8000 and 5173. Check `ss -ltnp` before selecting another port and update the Vite API base if necessary. The API service needs write access only to `data/processed`; inspect user systemd sandbox/logs if SQLite reports read-only or locked errors.
