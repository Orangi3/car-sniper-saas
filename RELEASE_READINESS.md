# Release readiness — controlled paid beta

## Update 3 (branch `feat/market-intelligence-v2`, 2026-10-28)

**Verdict: GO FOR PRIVATE INTERNAL TESTING. NO-GO for free or paid beta.**
Unchanged from update 2, because the deciding blocker is still external: no
licensed source has returned a real listing. This round built the path to
verify that in one manual workflow run once keys exist.

`python -m preflight` (scratch DB, `SNIPER_COMMERCIAL_MODE=1`, after a real
scheduler tick) → **READY FOR: PRIVATE INTERNAL TESTING**; free beta blocked
by no HTTPS origin and no fresh licensed data; paid beta additionally by
SQLite, backups, Stripe.

| Criterion | Status |
|---|---|
| Tests | ✓ SQLite 311 passed / 2 skipped; Postgres 16: 313 passed (before docs commit; final numbers in the PR) |
| Benchmark (synthetic) | TP=10 FP=1 TN=18 FN=0, unchanged by valuation v2; per-case baseline now enforced |
| Live source verification | ✗ never run — needs eBay/MarketCheck keys (`OWNER_ACTIONS.md` B/C) |
| Real-world performance | ✗ unmeasured |
| Unit economics | Base break-even **~138** subscribers (mixed tiers) now that AI development ($100) and monitoring ($10) are included; ~96 at a $149 data fee |
| Automation | Nightly maintenance ready; agent runs inactive; **branch protection off** |

Launch stages A/B/C with measurable criteria: `CURRENT_STATE.md` §5.

## Update 2 (branch `feat/commercial-launch-readiness`, 2026-10-28)

**Verdict: GO FOR PRIVATE INTERNAL TESTING.** Not yet a free or paid beta.

`python -m preflight` (real run, scratch DB, after a real scheduler tick)
reports **READY FOR: PRIVATE INTERNAL TESTING**, blocked from a controlled
free beta by: no source licensed for commercial use has delivered listings
(MarketCheck needs credentials **and** a signed agreement; eBay needs
credentials), commercial mode off, no HTTPS origin.

| Criterion | Status |
|---|---|
| CI green on GitHub | ✓ run 40730382632 (SQLite, Postgres, browser smoke) for the CI fix; later commits verified in the PR |
| Infrastructure (DB, migrations, scheduler, worker, recovery) | ✓ tested on SQLite + PostgreSQL 16 |
| Licensed private-party data | ✗ MarketCheck FSBO adapter built and fixture-tested; fail-closed until agreement + key |
| Real-world deal-finding performance | ✗ unmeasured — harness built (`EVALUATION.md`), no real observations yet |
| Unit economics | Base break-even ~107 subscribers in one territory (`UNIT_ECONOMICS.md`) |
| Payments | test-mode only; live products are an owner action |

Changes this round: safe_http dual-stack fix (CI), MarketCheck FSBO adapter
+ provider call budgets + `requires_agreement` licensing, eBay Browse
geography fix, ZIP/radius territories, explainable risk-adjusted ranking,
dealer-comparable haircut, evaluation harness, preflight, owner actions.
See `DATA_PROVIDER_DECISION.md`, `UNIT_ECONOMICS.md`, `OWNER_ACTIONS.md`.

---

## Original assessment (branch `feat/production-beta`)

Branch `feat/production-beta` (from `fix/alert-pipeline-upgrade` @ `49cbd78`).
Date: 2026-10-28.

## Verdict: **NO-GO** for a paid beta today — **conditional GO** once §6 is cleared

The engineering release blockers (single database, durable jobs, honest source
status, webhook SSRF, Postgres billing bugs) are fixed and tested against real
PostgreSQL 16. The remaining blockers are **external**: there is no licensed
listing feed configured or verified, so in commercial mode the product would
have no listings to sell. Nothing here can be fixed by more code.

## 1. Architecture audit (as found)

| Area | Finding (file / function) |
|---|---|
| Data layer | `sniper.db()` opened the local SQLite file unconditionally (`sqlite3.connect`) and ran `CREATE TABLE`/`ALTER` on every connection; `server.py` read listings/alerts through it; users, saved searches, billing, `user_alerts` went through `db.py` (Postgres-capable). In Postgres mode listings and alerts silently stayed in SQLite. |
| Background work | `_SCORE_QUEUE` (in-memory `Queue`) + 6 daemon threads in `sniper.py`; `notifications._OUTBOX` + daemon thread. Restart = lost work. |
| Source health | sources caught every exception and returned `[]`; `poll_once` recorded "no exception" as success; `/api/diagnostics` read in-process `SOURCE_DIAG`, empty in the web process in production. |
| Webhooks | `server._safe_webhook` validated at save time only; delivery used `requests.post` (follows redirects, re-resolves DNS). |
| Valuation | `comps._filter_to_recent` put undated records into "recent sales"; MarketCheck/Hemmings last-asking prices averaged as sales; `instant_estimate` included the target itself, applied a universal 8%/yr aging and a flat 7% haircut. |
| Alerting | subscribers only received alerts that had passed the owner's global gate (`saved_search_alerts.fan_out` ran off `alerts`). |
| Ingestion | `INSERT … ON CONFLICT DO NOTHING` on composite id: price changes were discarded. |

## 2. Issues, severity-ranked

Confirmed = reproduced or proven by a test. Inferred = reasoned risk not
reproduced.

| # | Severity | Status | Issue |
|---|---|---|---|
| 1 | Critical | Confirmed (live demo) | Postgres mode wrote listings/alerts to SQLite — listing ingested with `DATABASE_URL=postgres` landed in SQLite, 0 rows in Postgres. |
| 2 | Critical | Confirmed (PG test run) | Billing on Postgres: `refresh_entitlement` compared datetime to str (crash for cancel-at-period-end), admin billing health used `boolean = integer`. 12/149 tests failed on Postgres before fixes. |
| 3 | Critical | Confirmed (code + test) | Pending scores/alerts lost on restart (in-memory queues). |
| 4 | High | Confirmed (live) | Blocked sources reported healthy: Craigslist regions all ProxyError, diagnostics `error: null`. |
| 5 | High | Confirmed (tests) | Webhook SSRF via DNS rebinding and redirects to internal/metadata addresses. |
| 6 | High | Confirmed (external terms) | Craigslist, scraped eBay sold pages and the owner's personal browser/inbox data are not licensed for a paid multi-user product (`SOURCES.md`). |
| 7 | High | Confirmed (code) | Undated / last-asking records counted as sales; target included in its own comps. |
| 8 | High | Confirmed (test) | Subscribers with criteria different from the owner never got alerts. |
| 9 | High | Confirmed (test) | Price drops discarded as duplicates. |
| 10 | Medium | Confirmed | Age filter used SQLite `datetime('now')` (non-portable, mis-sorted vs ISO timestamps). |
| 11 | Medium | Confirmed | MarketCheck ISO sale dates never parsed (`comps._parse_sale_date`). |
| 12 | Medium | Confirmed | Empty scrape results cached 24h (a short outage blanked comps for a day). |
| 13 | Medium | Confirmed | Parser glued mileage onto short model names ("Saab 9-3 105k"). |
| 14 | Medium | Confirmed (browser) | Relist detection flagged two live look-alike posts; zero mileage slope shown as "$-0.000/mile". |
| 15 | Medium | Confirmed | `jobs` table had no retention (unbounded growth). |
| 16 | Medium | Inferred | Local `comps_cache.sqlite` is per-host: multiple workers on different hosts fetch independently. |
| 17 | Low | Inferred | Desktop `dashboard.html` predates evidence/risk fields (mobile `/m` is the new UI). |

## 3. Improvements — problem, cause, change, proof

**P0.1 One database.** Cause: `sniper.db()` hard-wired `sqlite3`. Change:
`db.session()` portable wrapper (placeholders, ISO timestamps, 0/1 booleans,
UTC session), schema only via migrations, portable SQL in `sniper.py` /
`server.py`, Postgres-only billing bugs fixed, per-user `saved_v2` joins.
Files: `db.py`, `sniper.py`, `server.py`, `billing.py`, `tests/conftest.py`.
Tested: whole suite on SQLite **and** real PostgreSQL 16 (one database per
test); `tests/test_pg_end_to_end.py` asserts every row is in Postgres via raw
psycopg2. Real environment: local PostgreSQL 16.15 server. Limit: comps HTTP
cache and VIN cache remain per-host SQLite caches (rebuildable).

**P0.2 Durable jobs.** Cause: in-memory queues. Change: `jobs` table +
`jobqueue.py` (idempotent enqueue, `SKIP LOCKED`/`BEGIN IMMEDIATE` claim,
leases, attempt counting, fencing, backoff + jitter, dead-letter, requeue,
depth caps, stats, retention), `jobs/worker.py`, alert + deliveries in one
transaction, quiet hours defer instead of drop. Files: `jobqueue.py`,
`jobs/worker.py`, `notifications.py`, `sniper.py`, migration 0008.
Tested: SIGKILL of a real worker mid-delivery then a restarted worker
delivers (attempts=2); two worker processes deliver 30 jobs exactly once;
atomicity, fencing, backoff, dead-letter, backpressure, pruning. Real
environment: real subprocesses + local HTTP server, both engines. Limit:
delivery is at-least-once — providers expose no idempotency key; a crash
after the provider accepts but before `done` can duplicate one message.

**P0.3 Honest source health.** Cause: swallowed errors. Change:
`sources/health.py` per-request outcomes → healthy / degraded / blocked /
rate_limited / timeout / auth_config / disabled / unverified; per-source
minimum intervals; persisted exponential backoff honoring `Retry-After`;
`/api/diagnostics` reads the table. Files: `sources/*.py`, `sniper.py`,
`server.py`, migration 0009. Tested: simulated 403, proxy refusal,
challenge page, 429, timeouts, malformed, valid-empty, partial failure,
missing credentials. Real environment: a live poll from this sandbox
(whose proxy refuses these hosts) reported bat / craigslist / govdeals /
hemmings as `blocked`. Limit: success against the real sites not verified
(network blocked here).

**P0.4 Webhook SSRF.** Cause: save-time-only validation. Change:
`safe_http.post_json` — https + approved ports, no userinfo, resolve at send
time and reject any non-public address, connect to the validated IP (SNI /
cert on hostname), never follow redirects, size limits; payload allowlist;
destinations resolved per user at send time. Tested: 28 tests incl.
rebinding between save and send, redirect to 169.254.169.254 (never
requested), IPv4-mapped IPv6, cross-user isolation. Limit: if egress goes
through an HTTP proxy, the proxy must enforce the same policy.

**P1 Valuation.** Change: `valuation.py` evidence classes; comparable
rules (self/VIN/cross-post exclusion, stale weighting, ±1 year preferred,
trim, private-party preference, data-gated Theil–Sen mileage, MAD outliers,
weighted percentiles); confidence label with reasons; `comps.py` only counts
dated completed sales as sales. Tested: `tests/test_valuation.py` (16).
Limit: confidence is not calibrated against realized sale prices.

**P1.4 Benchmark.** `benchmarks/cases.json` (29 synthetic, engineer-labeled
cases across 15 categories) + `run_benchmark.py`. Current: **TP 10, FP 1,
TN 18, FN 0 — precision 0.909, recall 1.0, FPR 0.053, FNR 0.0.** The FP is a
seller mislabeling trim (kept on purpose). **Not an accuracy claim**: the
labels are my judgments on synthetic data.

**P2 Personalized alerts.** Every valuation is matched against each
entitled subscriber's own searches; dedupe per user/event; cross-post
suppression; plan limits; lapsed plans / deleted searches / removed
channels handled. Tested: `tests/test_matching_and_history.py` incl. the
required "user criteria differ from owner threshold" test.

**P3 Price history.** Upsert ingestion, `listing_prices`, price-change
re-scoring, material-drop events, VIN identity, cautious relists. Tested:
the required $9,000 → $6,500 case (Pro gets `price_drop`, Lite gets it as a
first match, owner gets a drop alert), unchanged re-poll, look-alikes not
merged, VIN cross-post notifies once.

**P4–P6 Profit, UI, plans.** `profit.py` (known vs assumed; tax/title only
if the user sets them; labeled asking haircut); deals API; `/m` mobile app
with onboarding; Lite/Pro limits enforced server-side; usage metering + SMS
cap; admin usage with estimated cost. Tested: `tests/test_deals_api.py`
(12) + Playwright smoke at 390×844 (`tests/browser/`). Real environment:
real server + headless Chromium; screenshots reviewed.

**P7 Licensing.** `SOURCES.md` classification; `SNIPER_COMMERCIAL_MODE`
gate for sources and comp feeds; README corrected. Tested:
`tests/test_licensing.py`.

## 4. Test commands and results (this session)

| Command | Result |
|---|---|
| `python -m pytest -q` (SQLite) | 253 passed, 1 skipped (the Postgres-only e2e test) |
| `SNIPER_TEST_PG_URL=postgresql://postgres@127.0.0.1:55432/postgres python -m pytest -q` (PostgreSQL 16.15) | 254 passed |
| `python benchmarks/run_benchmark.py` | 29 cases, TP 10 / FP 1 / TN 18 / FN 0 |
| `node tests/browser/smoke_mobile.js` against a seeded local server | SMOKE OK |
| Live `poll_once` from this sandbox | all reachable sources correctly `blocked` (network policy) |
| Baseline before changes | 149 passed on SQLite; 137 passed / 12 failed on PostgreSQL |

Mocked vs real: HTTP to listing sites and notification providers is
simulated in tests (local HTTP servers or monkeypatched clients). Database,
worker processes, SIGKILL recovery, the web server and the browser are real.
CI (`.github/workflows/ci.yml`) is written but **has not run on GitHub yet**.

## 5. Blocked integrations / external dependencies

| Dependency | Needed for | Status |
|---|---|---|
| eBay Browse API keys + license review | licensed listings | not configured |
| MarketCheck contract + key | licensed listings + (incomplete) sold history | not configured; pricing not verified |
| Licensed confirmed-sales feed | `confirmed_sales` valuations in commercial mode | none identified/contracted |
| Written permission (Craigslist, BaT, Hemmings, GovDeals) | using those in a paid product | not obtained |
| Stripe live products ($2.99 Lite / $4.99 Pro) | charging | owner action; not created by this work |
| Twilio account | Pro SMS | optional; not configured |
| Production host network | live source verification | not available from this sandbox |

## 6. Conditions to flip to GO

1. At least one licensed feed (eBay API and/or MarketCheck) configured with
   `SNIPER_COMMERCIAL_MODE=1`, showing `healthy` with real listings from the
   production host for 48h.
2. Counsel sign-off on `SOURCES.md`; terms of the chosen feeds met.
3. `LAUNCH_CHECKLIST.md` completed on staging, including restore rehearsal
   and the browser smoke test.
4. CI green on GitHub for the release commit (SQLite + Postgres + smoke).
5. Beta copy states that values and profits are estimates and that
   confidence is not yet calibrated.

## 7. Not done (remaining work, in priority order)

1. Valuation calibration against real outcomes (needs a sold-price feed and
   labeled follow-ups); model-generation and trim tables.
2. Periodic re-valuation when the market moves (today: on new listing and
   on price change only).
3. Per-user *private* ingestion for the browser extension / email so those
   paths can be offered without redistribution.
4. Distance (zip radius) matching; today geography is by state.
5. Desktop `dashboard.html` evidence/risk badges (mobile `/m` has them).
6. Shared comps cache for multi-host workers.
