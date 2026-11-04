# Test Report

Run date: 2026-11-04. This records checks actually run in this workspace; it is not a security certification.

| Check | Observed | Result |
|---|---|---|
| Python suite (`PYTHONPATH=. .venv/bin/pytest -q`) | 34 passed, including market, alerts, news and social coverage | PASS |
| Python dependency consistency (`.venv/bin/python -m pip check`) | No broken requirements | PASS |
| Frontend strict production build (`npm --prefix frontend run build`) | TypeScript and Vite build completed | PASS |
| Production npm dependency audit (`npm --prefix frontend audit --omit=dev`) | 0 vulnerabilities | PASS |
| Patch whitespace check (`git diff --check`) | No errors | PASS |
| systemd social service unit (`systemd-analyze verify`) | Unit syntax validated | PASS |
| News and social parser/provider/API tests | Deterministic fixtures and mocked providers; no external provider was contacted | PASS |
| Retry behavior | Failed notification retried after configured delay; attempt and delivery status verified | PASS |
| Browser dashboard/API smoke test | Social page loaded from an isolated empty SQLite DB; Feed, Events, Sources, Alert rules and Deliveries tabs rendered; at 375px, body width matched viewport | PASS |
| Live X/news collection or notification delivery | Not run; credentials/providers were not exercised and no real messages were sent | NOT RUN |
| systemd worker runtime or reboot behavior | Unit was syntax-checked only; service was not enabled or started | NOT RUN |
| Full security, load, and recovery audit | Not run | NOT RUN |

Browser smoke testing used the expected API-unavailable state; successful live API-to-browser rendering was not tested. The API remains unauthenticated and is intended for loopback use only. The suite does not certify external provider quotas, terms, or delivery behavior.
