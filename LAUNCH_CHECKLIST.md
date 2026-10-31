# Paid-beta launch checklist

Companion to `DEPLOY.md` (host build), `RUNBOOK.md` (operations) and
`STAGING_RUNBOOK.md`. Work top to bottom; every box is a go/no-go gate.

## 1. Data licensing (blocking)

- [ ] Counsel has reviewed `SOURCES.md` and `DATA_PROVIDER_DECISION.md`.
- [ ] MarketCheck: only after a **signed** agreement covering central polling,
      stored listings/price history and pushed alerts, set
      `MARKETCHECK_COMMERCIAL_AGREEMENT=<agreement reference>`; set
      `MARKETCHECK_FSBO_MONTHLY_CALL_BUDGET` (default 4,000) to stay inside the plan.
- [ ] `SNIPER_COMMERCIAL_MODE=1` in `/etc/sniper/env`.
- [ ] At least one licensed listing feed is contracted and configured
      (eBay: keys + `EBAY_COMMERCIAL_AGREEMENT` after eBay's Buy API
      production approval, `EBAY_SETUP.md`; and/or MarketCheck: key +
      `MARKETCHECK_COMMERCIAL_AGREEMENT`), and its terms (display, caching,
      refresh, retention, redistribution) are met.
- [ ] `/api/diagnostics` shows each licensed source `healthy` from the
      production host, with a real `last_genuine_success_at`.
- [ ] Unlicensed sources show `disabled — not licensed for commercial use`.

## 2. Database + migrations

- [ ] `DATABASE_URL` points at PostgreSQL; `python -m migrations.runner`
      applied 0000–0012 (`SELECT id FROM schema_migrations ORDER BY id`).
- [ ] Full test suite passes against Postgres on the release commit:
      `SNIPER_TEST_PG_URL=postgresql://…/postgres python -m pytest -q`.
- [ ] Nightly encrypted backup job (`sniper-backup.timer`) has run, and a
      restore was rehearsed off-host (`deploy/restore.sh`). Backups now also
      contain the `jobs`, `user_matches`, `listing_prices` and
      `marketplace_inbox` tables — nothing important lives in side files
      except the comps HTTP cache (`comps_cache.sqlite`, rebuildable).

## 3. Processes (systemd)

| Unit | Role | Instances |
|---|---|---|
| `sniper-migrate.service` | applies migrations before others start | oneshot |
| `sniper-web.service` | Flask/gunicorn API + pages | 1+ |
| `sniper-scheduler.service` | polls due sources, ingests | exactly 1 (DB lease) |
| `sniper-worker.service` | scoring + notification jobs | 1+ (safe to scale) |
| `sniper-reconcile.timer`, `sniper-backup.timer`, `sniper-healthcheck.timer` | billing reconcile, backups, pings | timers |

- [ ] `systemctl enable --now sniper-worker.service` (new — without it
      nothing is scored or delivered).
- [ ] `deploy/sync.sh` restarts the worker on deploy and rollback (it does).

## 4. Environment variables

Required: `DATABASE_URL`, `SNIPER_COMMERCIAL_MODE=1`, `BILLING_ORIGIN`,
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_STARTER` (Lite),
`STRIPE_PRICE_PRO` (Pro), `ALLOW_REGISTRATION`.
Licensed data: `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_EPN_CAMPAIGN_ID`,
`EBAY_COMMERCIAL_AGREEMENT`, `MARKETCHECK_API_KEY`, `MARKETCHECK_COMMERCIAL_AGREEMENT`.
Alerts: `NTFY_SERVER` (+`NTFY_TOKEN` if self-hosted with auth); optional
Twilio trio for Pro SMS; `COST_SMS_CENTS` from the real invoice.
Never set in production: `SNIPER_TEST_PG_URL`.

- [ ] Stripe products created **by the owner** at $2.99 (Lite) and $4.99
      (Pro); price IDs pasted into env. (Nothing in this repo creates live
      products or charges.)

## 5. Security

- [ ] Outbound webhooks: if the host routes egress through an HTTP proxy,
      the proxy must block private/link-local/metadata destinations itself
      (`safe_http.py` pins IPs only on direct connections).
- [ ] `/api/admin/*` reachable only by `role=admin` accounts.
- [ ] TLS terminated by Caddy (`deploy/Caddyfile.staging` → production host).

## 6. Monitoring + recovery

- [ ] Job queue health: `GET /api/admin/usage` → `jobs.totals` (pending,
      running, done, dead) and `oldest_pending_run_after`. Alert if pending
      grows or dead > 0.
- [ ] Dead letters: inspect with `jobqueue.dead_letters()`; after fixing the
      cause, `jobqueue.requeue_dead(id)`.
- [ ] Source outages: `/api/diagnostics`; blocked / rate-limited sources back
      off automatically (`next_poll_after`).
- [ ] Worker crash: restart is enough — leased jobs are reclaimed after the
      lease (default 120 s); unfinished alerts are delivered, rarely twice.
- [ ] Logs: `journalctl -u sniper-worker -u sniper-scheduler -u sniper-web`.

## 7. Preflight gate

- [ ] `python -m preflight --target free` exits 0 before inviting any beta user.
- [ ] `python -m preflight --target paid` exits 0 before enabling live billing.
      It checks configuration, migrations, scheduler heartbeat, worker
      backlog, dead letters, backups, each source's verified health,
      licensing blockers, licensed data freshness and live Stripe config.

## 8. Territory and budget controls

- [ ] Launch territory set (`zip`/`radius_mi` in config or overrides) and
      beta signups limited to users inside it (data cost is per territory,
      see `UNIT_ECONOMICS.md`).
- [ ] `/api/admin/usage` shows provider calls (`provider:marketcheck_fsbo`)
      within budget after the first week.

## 9. Product checks on the production host

- [ ] Browser smoke test passes against staging (`tests/browser/`).
- [ ] Create a Lite and a Pro test account; run onboarding at `/m`; confirm
      a test push arrives on each channel ("Send Test").
- [ ] `python benchmarks/run_benchmark.py` on the release commit; record the
      numbers (they are synthetic — not an accuracy claim).
