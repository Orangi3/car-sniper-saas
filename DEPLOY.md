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
