# Test Report

Run date: 2026-11-05. This records checks actually run in this workspace; it is not a security certification.

| Check | Observed | Result |
|---|---|---|
| Python suite (`PYTHONPATH=. .venv/bin/pytest -q`) | 45 passed, including authentication, RBAC, refresh-token replay, ntfy command signing/replay protection, command authorization, read-only commands, and bounded jobs | PASS |
| Python dependency consistency (`.venv/bin/python -m pip check`) | No broken requirements | PASS |
| Frontend strict production build (`npm --prefix frontend run build`) | TypeScript and Vite production build completed with sign-in/session UI | PASS |
| Patch whitespace check (`git diff --check`) | No errors | PASS |
| API auth integration tests | Health/login boundaries, protected APIs, read-only mutation denial, admin audit access, last-admin protection, token rotation/replay revocation, and login throttling | PASS |
| ntfy command listener tests | HMAC/timestamp validation, nonce replay rejection, active-ADMIN checks, rate limits, read-only queries, documented JSON poll parameters, fixed collector invocation, timeout handling, and SQLite backup | PASS |
| News and social API regression tests | Authenticated API requests with deterministic fixtures; no external provider contacted | PASS |
| Production npm dependency audit (`npm --prefix frontend audit --omit=dev`, 2026-11-04) | 0 vulnerabilities; frontend dependencies were unchanged in this phase | PASS (previous run) |
| systemd social and command-listener service units (`systemd-analyze verify`) | Unit syntax validated; neither service was enabled or started | PASS |
| Retry behavior | Failed notification retried after configured delay; attempt and delivery status verified | PASS (previous run) |
| Browser dashboard/API smoke test (2026-11-04) | Social page loaded from an isolated empty SQLite DB; Feed, Events, Sources, Alert rules and Deliveries tabs rendered; at 375px, body width matched viewport | PASS (previous run) |
| Live X/news collection or notification delivery | Not run; credentials/providers were not exercised and no real messages were sent | NOT RUN |
| Live ntfy JSON polling/publish behavior | Not run against a live ntfy server; no real messages were sent | NOT RUN |
| systemd worker runtime or reboot behavior | Units were syntax-checked only; services were not enabled or started | NOT RUN |
| Visual browser test of the new sign-in flow | Build and API integration verified; no interactive browser sign-in test in this phase | NOT RUN |
| Full security, load, and recovery audit | Not run; this is not a security certification | NOT RUN |

API access now requires a bearer token except for `/healthz`, login and refresh. `AUTH_TOKEN_SECRET` and a bootstrapped ADMIN account must be configured locally before the dashboard can sign in. The API is still intended for loopback use only. Tests use an isolated SQLite database and test-only credentials; no real user credentials, live provider, or notification channel were exercised. The suite does not certify external provider quotas, terms, or delivery behavior.
