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

Do not replace an existing `.env`. For a new install, copy `.env.example` only if `.env` does not exist, edit it locally, and run `chmod 600 .env`. The Alpha Vantage API key is optional; without it, AAPL network collection is skipped. ntfy is optional; without a topic, alerts are recorded as `not_configured` and no request is sent.

## Development

```bash
source .venv/bin/activate
uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

For the hot-reload frontend, run `npm --prefix frontend run dev -- --host 127.0.0.1` separately and open `http://127.0.0.1:5173`. In production, build the frontend and use the API service; FastAPI serves `frontend/dist` at `http://127.0.0.1:8000`.

## User systemd

Review `deploy/market-api.service`, `deploy/market-collector.service`, and `deploy/market-collector.timer` before installing. These run as the logged-in user and keep the API loopback-only.

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

Model artifacts and reports are separate files under `data/processed/models/`; back them up alongside SQLite. Raw CSVs under `data/raw/` are retained separately and are not changed by the new pipeline. A raw-only snapshot at `backups/raw_snapshot_20261016.tar.gz` was created and SHA-256-verified against the current CSVs. The older pre-existing tar archive contains `.env` and predates the current raw data; treat it as secret material and do not use it as the latest data restore point or publish it.

## Limitations

This is a personal-machine service. Sleep, power loss, network interruptions and user-session policy stop collection. systemd persistent timers can catch up after downtime, but provider rate limits still apply. No remote access, public deployment, authentication, HTTPS termination, log rotation, or independent off-device backup is configured.
