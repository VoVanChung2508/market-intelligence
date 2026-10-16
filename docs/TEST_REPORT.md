# Test Report

Run date: 2026-10-16. Only listed checks were run; this is not a security certification.

| Check | Expected | Observed | Result |
|---|---|---|---|
| Python API/collector/alert/analysis suite (`.venv/bin/python -m pytest -q`) | Unit and integration tests pass offline | 16 passed, no warnings | PASS |
| Python dependency consistency (`.venv/bin/python -m pip check`) | No broken requirements | No broken requirements found | PASS |
| Frontend strict production build (`npm --prefix frontend run build`) | TypeScript and Vite build pass | Build completed | PASS |
| Production npm dependency audit (`npm --prefix frontend audit --omit=dev`) | No known production dependency advisories | 0 vulnerabilities | PASS |
| AppTest on existing Streamlit dashboard | Renders without app exceptions | 3 metrics, dataframe and selector; deprecated `use_container_width` warning | PASS with warning |
| Live local API smoke test | Health and real imported data respond | `/healthz` 200; BTC/ETH/AAPL bars returned; stale/fresh state matched timestamps | PASS |
| Live CoinGecko collector | Store current crypto values and log job | BTC and ETH each inserted 3 new bars; API showed fresh; raw CSV unchanged | PASS |
| Browser desktop/mobile smoke | No overflow, real API values, analysis gate visible | 1280px and 390px body widths match viewport; AAPL gate/empty model state correct; no page or console errors | PASS |
| ntfy delivery | Mock or test endpoint | Mocked/unconfigured path only; no real notification sent | NOT RUN live |
| systemd timer after reboot | Persists and executes on schedule | Unit files supplied; service has not been enabled/reboot-tested | NOT RUN |
| systemd unit syntax | Service/timer files parse | `systemd-analyze verify` returned no diagnostics | PASS |
| Full security/performance/E2E audit | Auth, CORS, XSS, dependency, load, restore | Static review and limited local integration only | NOT RUN |

The suite count includes API, collector/provider parser, stock rate cooldown, alert threshold/deduplication, mocked ntfy sender/redaction, feature/label alignment, purged chronological split and insufficient-history cases. The timer has not been enabled or reboot-tested. Final browser checks covered Overview, AI Analysis and Alerts at desktop/mobile widths with no failed requests, console errors, or uncaught page errors.
