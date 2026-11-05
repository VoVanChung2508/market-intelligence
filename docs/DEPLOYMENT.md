# Deployment and Recovery

## Install

Use the existing Python 3.14 and Node 22 environment, or create one:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

Do not replace an existing `.env`. For a new install, copy `.env.example` only if `.env` does not exist, edit it locally, and run `chmod 600 .env`. Set `AUTH_TOKEN_SECRET` to a private random value of at least 32 characters before using the API. Create the first ADMIN with `python -m backend.auth bootstrap-admin --username admin`; the CLI securely prompts for a password and stores only an Argon2 hash. The Alpha Vantage API key is optional; without it, AAPL network collection is skipped. ntfy is optional; without a topic, alerts are recorded as `not_configured` and no request is sent.

## Development

```bash
source .venv/bin/activate
uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

For the hot-reload frontend, run `npm --prefix frontend run dev -- --host 127.0.0.1` separately and open `http://127.0.0.1:5173`. In production, build the frontend and use the API service; FastAPI serves `frontend/dist` at `http://127.0.0.1:8000`.

## User systemd

Review `deploy/market-api.service`, `deploy/market-collector.service`, `deploy/market-collector.timer`, and (if needed) `deploy/market-social-monitor.service` before installing. These run as the logged-in user; the API stays loopback-only.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/market-api.service ~/.config/systemd/user/
cp deploy/market-collector.service ~/.config/systemd/user/
cp deploy/market-collector.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now market-api.service market-collector.timer
systemctl --user status market-api.service market-collector.timer
```

If any destination unit already exists, inspect it before copying. For operation after logout/boot, configure systemd lingering for the user if permitted by the machine administrator.

```bash
systemctl --user list-timers
systemctl --user start market-collector.service
journalctl --user -u market-api.service -n 100 --no-pager
journalctl --user -u market-collector.service -n 100 --no-pager
systemctl --user restart market-api.service
systemctl --user stop market-collector.timer market-api.service
```

The collector timer runs every 30 minutes. Alpha Vantage attempts are additionally guarded by a 20-hour cooldown. ntfy alerts only trigger when a new adjacent bar crosses the configured absolute move threshold; delivery is deduplicated per source bar.

The social monitor unit is separate and is not included in the commands above. Install and start it only after reviewing [SOCIAL_INTELLIGENCE.md](./SOCIAL_INTELLIGENCE.md), confirming the `.env` permissions, and explicitly configuring X access. `X_API_ENABLED` defaults to false. The monitor polls at its configured interval and locks against duplicate worker processes. The API requires authentication for data and management routes; health, login and refresh are public. Test-notification requests can send real messages; do not expose the API beyond loopback.

The optional ntfy command listener is a separate service and remains disabled until its topics and signing secret are configured. Review [COMMAND_CENTER.md](./COMMAND_CENTER.md) before installing it. It supports read-only status commands plus fixed collection/backup jobs only; it cannot start or stop services.

## Backup and restore

Create a consistent SQLite backup while the service is running using SQLite's backup command. Choose a new filename; do not overwrite an existing backup.

```bash
mkdir -p backups
sqlite3 data/processed/market_intelligence.sqlite3 ".backup 'backups/market_$(date +%Y%m%d_%H%M%S).sqlite3'"
```

To restore, stop the API and collector timer first, preserve the current database under a different name, copy the chosen backup into place, then restart and verify `/healthz`.

```bash
systemctl --user stop market-collector.timer market-api.service
cp data/processed/market_intelligence.sqlite3 "backups/pre_restore_$(date +%Y%m%d_%H%M%S).sqlite3"
cp backups/CHOSEN_BACKUP.sqlite3 data/processed/market_intelligence.sqlite3
systemctl --user start market-api.service market-collector.timer
curl --fail http://127.0.0.1:8000/healthz
```

Model artifacts and reports are separate files under `data/processed/models/`; back them up alongside SQLite. Raw CSVs under `data/raw/` are retained separately and are not changed by the new pipeline. The current checkout does not contain a `backups/` directory, so create and verify backups before relying on recovery procedures. Any archive that contains `.env` must be treated as secret material and must not be published.

## Limitations

This is a personal-machine service. Sleep, power loss, network interruptions and user-session policy stop collection. systemd persistent timers can catch up after downtime, but provider rate limits still apply. News collection is a separate manual command and has no systemd timer. Social collection is a separate optional worker. No remote access, public deployment, HTTPS termination, log rotation, or independent off-device backup is configured. API rate limiting is process-local and should be supplemented by a reviewed reverse proxy before multi-worker or remote deployment.
