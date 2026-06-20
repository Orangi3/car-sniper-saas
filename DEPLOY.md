# Sniper SaaS — Deployment

Phase-1 production-safe deploy procedure. The application **does not**
auto-run migrations at import time — that was a multi-worker race hazard.
Migrations are an explicit, one-shot deploy step.

## Local development

```bash
cd ~/Desktop/sniper
source .venv/bin/activate            # create with: python3 -m venv .venv
pip install -r requirements.txt
python3 -m migrations.runner         # idempotent — safe to re-run
python3 server.py                    # boots on :8765
```

Run tests:

```bash
pytest -q
```

The migration runner picks up `SNIPER_DB_PATH` (SQLite path) or
`DATABASE_URL` (Postgres). With neither set, SQLite at
`./listings.db` is used.

## Production deploy (Hetzner / DigitalOcean / any single-VPS Linux)

Order matters. Do these steps in sequence — never run gunicorn before
migrations have finished.

### 1. Configure environment

```bash
export DATABASE_URL="postgresql://sniper:STRONG_PW@127.0.0.1:5432/sniper"
export ALLOW_REGISTRATION="0"        # close open registration in prod
# optional:
export SECRET_KEY="…"                # 32+ bytes for cookie signing
```

For Postgres support, install the driver once:

```bash
pip install 'psycopg2-binary>=2.9'
```

### 2. Run migrations exactly once per deploy

```bash
python3 -m migrations.runner
```

Idempotent — already-applied migrations are skipped. The command prints
the list of new IDs and exits 0 on success. **Do not** start gunicorn
until this command returns cleanly.

### 3. Start workers

```bash
gunicorn server:app \
    --workers 4 \
    --bind 0.0.0.0:8765 \
    --access-logfile - \
    --timeout 30
```

On boot each worker verifies `schema_migrations` lists every expected
migration ID. If a migration is missing the worker prints
`[server] STARTUP ABORT: …` and exits — your orchestrator will surface
the failure instead of silently serving 500s.

### 4. (one-time) Bootstrap an admin

```bash
python3 -c "
import os, sys, types
# auth.py imports flask at module level; provide a minimal stub for CLI use
fk = types.ModuleType('flask')
class _G: pass
fk.g=_G(); fk.jsonify=lambda *a,**k:None
class _R: headers={}; remote_addr=''; cookies={}; is_secure=False
fk.request=_R()
sys.modules['flask']=fk
import auth
u = auth.create_user(sys.argv[1], sys.argv[2], role='admin', plan='pro')
print('admin', u)
" admin@yourdomain.com '<strong-password>'
```

After that, normal users sign up via `/login` (if `ALLOW_REGISTRATION=1`)
or you create them via the same CLI snippet with `role='user'`.

### 5. Override the migration check (use rarely)

```bash
export SNIPER_SKIP_MIGRATION_CHECK=1
```

Skips the schema_migrations verification at worker boot. Only legitimate
use is the first-boot bootstrap script that runs the migrations and the
server in a single process. Never leave this set in steady-state prod.

## Systemd unit (reference)

```ini
[Unit]
Description=sniper saas
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=sniper
WorkingDirectory=/opt/sniper
EnvironmentFile=/etc/sniper/env
ExecStartPre=/opt/sniper/.venv/bin/python3 -m migrations.runner
ExecStart=/opt/sniper/.venv/bin/gunicorn server:app -w 4 -b 127.0.0.1:8765 --access-logfile -
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`ExecStartPre` is the one-shot migrate step; `ExecStart` is gunicorn.
Systemd guarantees `ExecStartPre` completes (exit 0) before `ExecStart`
runs, and a single systemd-managed process means there's no worker race.

Put `DATABASE_URL`, `SECRET_KEY`, `ALLOW_REGISTRATION` in
`/etc/sniper/env` (0600, owned by sniper).

## Reverse proxy

Terminate TLS at Caddy or nginx; the dashboard uses `Secure` cookies
when `request.is_secure` is true. Caddy is the lowest-config option:

```caddy
sniper.yourdomain.com {
    reverse_proxy 127.0.0.1:8765
}
```

Caddy fetches Let's Encrypt certs on first request.

## Scheduled billing reconciliation

Reconciliation is an **internal** command, not a public HTTP endpoint. A
systemd timer runs the command directly on the application host. The
gunicorn workers never participate; if the web app is down, the timer
still runs and corrects local subscription state from Stripe.

The command:

```bash
/opt/sniper/.venv/bin/python3 -m jobs.reconcile_billing
```

Exit codes: `0` success · `1` operational failure (missing env, DB
unreachable, import error) · `2` lock not acquired (another instance is
already running, no work done) · `3` ran but reported errors talking to
Stripe (corrections applied where possible — investigate `last_error`).

A DB-backed advisory lock (`billing_job_locks` table, populated by
migration 0005) prevents two timers / two hosts / a manual run + a cron
run from racing each other. The lock has a 10-minute lease and is stolen
after 1 hour of idleness — so a crashed reconciler never permanently
blocks the next one.

### Dry-run mode

```bash
/opt/sniper/.venv/bin/python3 -m jobs.reconcile_billing --dry-run
```

Walks the same subscription rows, queries Stripe, reports mismatches
that WOULD be corrected, then exits without writing. Useful to verify a
new Stripe price ID rollout, or before re-enabling a timer that was
paused during incident response. Dry-run still acquires the lock, so it
won't run concurrently with a real reconcile.

### systemd service (oneshot)

`/etc/systemd/system/sniper-reconcile-billing.service`:

```ini
[Unit]
Description=sniper billing reconciliation (internal)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=oneshot
User=sniper
WorkingDirectory=/opt/sniper
EnvironmentFile=/etc/sniper/env
ExecStart=/opt/sniper/.venv/bin/python3 -m jobs.reconcile_billing
# Reasonable safety limits — reconcile should never need more
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/sniper
```

### systemd timer

`/etc/systemd/system/sniper-reconcile-billing.timer`:

```ini
[Unit]
Description=run sniper billing reconciliation hourly

[Timer]
# At minute 17 of every hour, with a 5-minute random jitter so
# multi-host fleets don't all hit Stripe at the same second.
OnCalendar=*-*-* *:17:00
RandomizedDelaySec=300
Persistent=true
Unit=sniper-reconcile-billing.service

[Install]
WantedBy=timers.target
```

Enable + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sniper-reconcile-billing.timer
systemctl list-timers sniper-reconcile-billing.timer
journalctl -u sniper-reconcile-billing.service --since '1 hour ago'
```

The timer triggers the oneshot service; the service runs the Python
command in a 30-second-ish wall-clock window (bounded by
`--time-budget`). Each invocation's outcome is one JSON line in
`journalctl`, suitable for alerting:

```bash
# Alert if last run had Stripe errors
journalctl -u sniper-reconcile-billing.service -o cat -n 1 \
  | jq 'select(.errors > 0)'

# Alert if no successful run in the last 3 hours
last=$(journalctl -u sniper-reconcile-billing.service -o cat -n 1 \
        | jq -r '.finished_at // empty')
```

### Why no `/api/admin/billing/reconcile` route

An internet-facing scheduled action would mean:
- A long-lived admin token (or service account) stored somewhere on the
  cron host, with all the rotation/leak pain of a real secret.
- An adversary who reaches the route can fan it out and exhaust your
  Stripe API quota or skew the audit log.
- The timer can't run when the web tier is down — which is exactly when
  a stuck subscription state most needs fixing.

The internal CLI sidesteps all three. Operators inspect billing-health
results through the read-only `GET /api/admin/billing/health` endpoint
(login-required, admin-only) which includes the current job-lock state.

## Pre-live tax decision checklist

Sales-tax / VAT obligations are jurisdiction-specific and change with
revenue, customer location, and product taxability. Phase 2 does **not**
configure Stripe Tax. Before flipping to live mode:

1. **Choose tax posture.** Decide whether to collect sales-tax / VAT at
   checkout (Stripe Tax), to absorb tax into the listed price, or to
   stay flat-priced and reconcile separately. Document the choice.
2. **Confirm registrations and collection requirements with a qualified
   tax professional.** Don't infer obligations from revenue thresholds
   alone, and don't rely on this document. Low revenue does NOT
   automatically remove tax obligations — many jurisdictions have $0
   thresholds for digital services sold to local consumers.
3. **Implement the chosen posture.** Either: enable Stripe Tax for the
   live-mode account, mark Starter/Pro prices' Tax Behavior, and verify
   on a real test charge that tax line items appear correctly — OR
   document why you're handling tax outside of Stripe and how you'll
   meet the filing/remittance schedule.
4. **Re-check before each new market.** If you start selling into a new
   country or US state, re-run step 2 before turning on collection
   there.

Do not enable live billing until step 1 has a written decision and
step 2 is signed off by your tax advisor.
