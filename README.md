# FlipFirst

*Find it first. Flip it better.* — consumer brand of this codebase (`car-sniper-saas`;
internal names, environment variables and service units keep the `sniper` prefix).

Finds underpriced used cars, explains what each one is worth and why, estimates
realistic flip profit, and alerts each subscriber when a listing meets **their**
criteria — including when a car they're watching drops in price.

Runs two ways:

- **Personal / local** (macOS, SQLite): double-click `START HERE.command`.
- **Paid SaaS** (Linux, PostgreSQL, systemd): see `LAUNCH_CHECKLIST.md`,
  `DEPLOY.md` and `RUNBOOK.md`. Requires `SNIPER_COMMERCIAL_MODE=1`.
  `python -m preflight` says what a deployment is actually ready for.
- Business docs: `CURRENT_STATE.md` (start here), `DATA_PROVIDER_DECISION.md`,
  `UNIT_ECONOMICS.md`, `COMPETITIVE_ANALYSIS.md`, `OWNER_ACTIONS.md`,
  `EVALUATION.md`.
- Engineering process: `AGENTS.md`, `OWNERSHIP.md`; live data check:
  `python -m verify_sources`.

> **Data licensing matters.** Several sources this tool can read (Craigslist,
> RSS feeds, scraped sold-price pages, the owner's own browser and inbox) are
> **not** licensed for a paid multi-user product. Commercial mode switches them
> off. Read `SOURCES.md` before charging anyone.

## Architecture

```
sources/*  ──poll──▶  jobs/scheduler.py ──ingest()──▶  listings + listing_prices
 (health.py records every request: healthy/blocked/rate-limited/…)   │
                                                                     ▼ durable "score" job
                                            jobs/worker.py ── valuation.py ── profit.py
                                                                     │  one transaction:
                                                                     ├─ near_misses (valuation + evidence)
                                                                     ├─ alerts (owner's trusted feed)
                                                                     ├─ user_matches (each subscriber's searches)
                                                                     └─ durable "notify" jobs ─▶ ntfy/Pushover/Twilio/
                                                                                                 Discord/webhook (safe_http)
server.py (Flask) ── dashboard.html (desktop) · mobile.html (/m) · JSON API
```

- **One database** (`db.py`): SQLite locally, PostgreSQL in production —
  listings, valuations, alerts, users, searches and jobs all live together.
  Schema comes only from `migrations/`.
- **Durable jobs** (`jobqueue.py`): scoring and notifications survive
  restarts; retries with backoff; dead-letter after bounded attempts.
- **Shared work, personal results**: each listing is ingested and valued once;
  every subscriber's saved searches are then evaluated against it.

## Sources

See `SOURCES.md` for the full coverage + licensing table. Summary:

| Source | Status | Commercial mode |
|---|---|---|
| Craigslist (by owner) | implemented, terms prohibit automated collection | off |
| Bring a Trailer, Hemmings, GovDeals RSS | implemented, redistribution permission unverified | off |
| eBay Motors (Browse API) | implemented, needs `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | on |
| **MarketCheck private sellers (FSBO API, paid)** | implemented, fixture-tested; needs `MARKETCHECK_API_KEY` **and a signed agreement** (`MARKETCHECK_COMMERCIAL_AGREEMENT`) | off until agreement |
| MarketCheck dealer inventory (retail asks, paid) | implemented; same agreement requirement | off until agreement |
| Browser extension / email digests | owner's personal data only | off |
| Cars & Bids, Copart, IAA, GSA, PublicSurplus | stubs (no code) | — |

Each source is polled no more often than its minimum interval
(`DEFAULT_MIN_INTERVAL_S` in `sniper.py`, overridable via
`source_min_interval_s`) and backs off automatically when blocked or
rate-limited. **The scheduler tick is not a discovery-speed guarantee** — how
quickly a new listing is seen depends on the source and these budgets. The
dashboard's source panel shows each source's real status.

## How a listing becomes an alert

1. **Ingest** (`sniper.ingest`) — upsert; price changes are kept in
   `listing_prices`; VINs (check digit verified) identify the same car across
   sources; relists are matched only by VIN or by same source/region/title/
   odometer/photo after the old post disappears. Salvage/rebuilt/parts,
   needs-work, dealer and payment-teaser prices are detected for every source.
2. **Value** (`valuation.py`) — evidence is kept in separate classes:
   `confirmed_sales` (dated completed sales), `asking_comparables` (what
   sellers ask), `historical_incomplete` (undated/old/last-asking records),
   `insufficient`. Comparables exclude the car itself, its cross-posts, stale
   and damaged cars, auction bids, and other vehicles that share the model
   name (a Silverado 2500HD is not a 1500, a Wrangler Unlimited is not a
   two-door); prefer same trim and ±1 model year; mileage-adjust only with
   enough data; robust statistics. Confidence (high/medium/low) comes with
   the reasons behind it, and is capped at low when most comparables are a
   different stated trim or the car has far more miles than any of them. It
   is **not** calibrated against real sale outcomes yet.
3. **Assess** — risk flags (scam signals, too good to be true — over 50%
   under — auction bid, branded title, needs work or damage, dealer, mileage
   unknown, description not checked, relisted, low confidence) → `trusted`
   or `risk`. Risky cars stay visible in a labeled feed; ended auctions are
   hidden.
4. **Match** (`saved_search_alerts.py`) — every entitled subscriber's own
   criteria: min % under, min profit, confidence floor, conservative /
   aggressive, condition, price/year/miles/make/model/state. One notification
   per car per event; price drops are new events.
5. **Deliver** (`notifications.py`) — durable jobs; destinations read at send
   time; user webhooks pass connect-time SSRF checks (`safe_http.py`).

## Profit estimates

`profit.py` separates known facts (asking price, value range) from
assumptions (negotiation, repairs, transport, selling fees, and — only if the
user sets them — sales tax and title fees for their state). Asking-price-based
values get a labeled asking-to-sale haircut. Users edit assumptions without
touching the valuation. Every figure is shown as an estimate, not a promise.

## Plans (enforced server-side — `plans.py`)

| | Lite (`starter`) | Pro (`pro`) |
|---|---|---|
| Target price | $2.99/mo | $4.99/mo |
| Saved searches | 3 | 15 |
| Alerts per day | 25 | 100 |
| Price-drop alerts | – | ✓ |
| Aggressive (risk-labeled) mode | – | ✓ |
| Profit scenarios | base case | conservative / base / optimistic |
| SMS (needs Twilio) | – | 30/month |

Prices live in Stripe (`STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PRO`); limits can
be tuned with `PLAN_LIMITS_JSON`. Usage per account: `/api/admin/usage`.

## Local setup (macOS)

Double-click **`START HERE.command`** (first time: right-click → Open). It
installs dependencies, applies migrations, starts the scheduler, the job
worker and the dashboard at `http://127.0.0.1:8765` (mobile view: `/m`).
`STOP.command` stops everything. See `QUICKSTART.txt`.

## Terminal commands

```bash
source .venv/bin/activate
python -m migrations.runner          # apply schema migrations
python -m jobs.scheduler --once      # poll due sources once
python -m jobs.worker --once         # process queued scoring/alert jobs
python sniper.py sources             # which sources are on/off
python sniper.py recent 25           # owner's recent alerts
python comps.py "2015 Mazda Miata"   # sold-comp lookup (dated sales only)
python benchmarks/run_benchmark.py   # labeled edge-case benchmark
python server.py                     # dashboard
python -m pytest -q                  # tests (SQLite)
SNIPER_TEST_PG_URL=postgresql://postgres@127.0.0.1:5432/postgres python -m pytest -q   # tests on Postgres
```

## Alert channels

| Channel | Settings |
|---|---|
| ntfy push (free) | owner: `NTFY_TOPIC`; subscribers: their own topic from the app |
| Pushover | `PUSHOVER_TOKEN`, `PUSHOVER_USER` (owner) |
| Twilio SMS | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` (+ phone) |
| Discord / webhook | `DISCORD_WEBHOOK_URL`, `ALERT_WEBHOOK_URL`; subscribers: their own https webhook |
| iMessage | macOS only, owner phone `NOTIFY_PHONE` |

Delivery is at-least-once: a crash between the provider accepting a message
and the job being marked done can send one duplicate. Alerts are not lost.

## File map

```
config.py               knobs (env + overrides.json); commercial_mode
db.py                   one connection layer for SQLite + Postgres
migrations/             schema (0000–0010), applied by migrations/runner.py
sniper.py               ingestion, scoring, source scheduling + health, CLI
sources/                source adapters (auto-discovered) + health.py
valuation.py            comparables, evidence classes, confidence
comps.py                sold-price lookups (dated sales only count as sales)
profit.py               profit scenarios with explicit assumptions
saved_search_alerts.py  per-subscriber matching + plan limits
plans.py                Lite / Pro entitlements
jobqueue.py             durable jobs (claim, lease, retry, dead-letter)
jobs/scheduler.py       polling process      jobs/worker.py   job process
notifications.py        alert channels       safe_http.py     SSRF-safe webhooks
server.py               Flask API + pages    mobile.html      /m mobile app
benchmarks/             labeled edge cases + run_benchmark.py
verify_sources.py       one-request live probe of the licensed API sources
validate_deals.py       isolated real-data deal validation runs (EVALUATION.md)
evaluation.py           human-labeled outcomes, precision and error metrics
analyze.py              FlipFirst Analyze: any car the user found → identity, value, profit, risks, decision
workspace.py            the user's private deal workspace (notes, stages, recorded outcomes)
accounts.py             owner tool: delete an account on request
pilot_report.py         weekly pilot counts (testers, actions, alerts, supply)
tests/                  pytest suite (SQLite or Postgres) + browser/ smoke test
SOURCES.md              data coverage + licensing audit
EBAY_SETUP.md           eBay application package (price-estimate permission)
FIRST_LIVE_TEST.md      first real-data run, step by step
COMPLIANCE.md           provider and privacy obligations, status, open questions
LAUNCH_CHECKLIST.md     paid-beta deployment checklist
RELEASE_READINESS.md    audit, results and GO / NO-GO
```

## Known limits

- Title parsing is rule-based; cars without a parseable year/make/model are
  stored but not valued. A seller who mislabels the trim can fool the
  valuation (kept as a measured case in the benchmark). Body style, cab and
  drivetrain are matched only from structured provider fields, so a
  title-only source (eBay item summaries) can still mix them.
- Damage and title problems are found only in the text we receive. eBay item
  summaries carry no description, so those listings are labeled "description
  not checked" rather than screened. Detection is keyword-based with simple
  negation rules: "no rust, minor front end damage" still reads as a denial,
  so a person must read every listing before acting on it.
- Valuation confidence is evidence-based but **not yet calibrated** against
  real sale outcomes; the benchmark is synthetic.
- In commercial mode there is no licensed confirmed-sales feed yet, so values
  come from asking-price comparables (labeled as such).
