# Deployment test report — disposable host, 2026-10-30

The documented deployment (`FIRST_DEPLOY.md`) was run end to end on a
**disposable local host**: an Ubuntu 24.04 container with systemd as PID 1,
in Docker, on the build sandbox. It is a SIMULATED server, not the VPS.
Nothing touched real infrastructure, DNS or a firewall outside the
container.

## What ran

| Step | Command (as documented) | Result |
|---|---|---|
| Fresh host | Ubuntu 24.04 + systemd, root SSH | systemd `running` |
| Install | `STAGING_HOSTNAME=… bash deploy/install.sh` | PASS (with the two sandbox substitutes below) |
| First deploy | `bash deploy/sync.sh --apply root@sniper-staging` at `main` `b0d6ee4` | PASS — 19 migrations, canary, flip; web, worker, scheduler, Caddy, PostgreSQL 16.15 active |
| Upgrade | same, at PR #11 `ae5308d`, PR #12 `ec9d845`, then this branch's head | PASS each time; services active after every flip |
| Rollback | `bash deploy/sync.sh --rollback root@sniper-staging` | PASS — `current` back to the previous release; HTTPS health 200 |
| HTTPS through Caddy | `/health`, `/login`, `/m` | 200, 200, 302 → sign-in |
| Security headers | `curl -I /login` | HSTS `max-age=31536000`, CSP, `nosniff` |
| HTTP → HTTPS | `http://…/login` | 308 to https |
| Static isolation | `/deploy/env.staging.example`, `/.env`, `/server.py`, `/listings.db`, `/static/../server.py`, `/etc/sniper/env`, `/.git/config` | all 404 |
| Admin + sign-in | create admin (`STAGING_RUNBOOK` §7), sign in through Caddy | 200; cookie `Secure; HttpOnly`, 30 days; Operations page 200 for the admin |
| Invitations | `python -m invites issue --note pilot --count 2` | links use the configured origin; tokens shown once |
| Account deletion | `python -m accounts delete …` (dry run, `--yes`, last admin) | dry run lists, delete removes, last admin refused |
| Pilot report | `python -m pilot_report` | counts only |
| Readiness | `python -m preflight` | `READY FOR: PRIVATE INTERNAL TESTING` (free beta needs licensed data, HTTPS origin, etc.) |
| Database dump/restore | `pg_dump -Fc` → restore into a throwaway database → row count → drop | PASS |
| Backup job without B2 keys | `systemctl start sniper-backup.service` | fails loudly: `B2_KEY_ID required` (no silent success) |
| Log rotation | `/etc/logrotate.d/sniper`, `logrotate -d` and `-f` | parses; rotates as `sniper`; web stays up |

## Differences from the real VPS

| Area | Here | On the VPS |
|---|---|---|
| Caddy | Ubuntu archive package 2.6.2 — Caddy's own apt repository (`dl.cloudsmith.io`) is blocked from this sandbox | `install.sh` installs from Caddy's repository (newer); **not tested here** |
| TLS certificate | Caddy's local CA (`local_certs`, test-only edit) for `app.tryflipfirst.test` | Let's Encrypt for `app.tryflipfirst.com` once DNS points at the server — **not testable without DNS** |
| Tailscale | stub command — `tailscale.com` is blocked from this sandbox | real install + `tailscale up --ssh`; **not tested** |
| SSH after ufw | test allowed SSH from the Docker bridge only, standing in for Tailscale | public SSH stays blocked; you reach the server over Tailscale |
| Backups to B2, restore drill | not run (no B2 bucket or age key) | `FIRST_DEPLOY.md` step 11 |
| Email (Resend), Healthchecks pings | not run | steps 10 and 13 |
| Hardware | container on a 4-CPU sandbox | 1–2 vCPU / 2 GB VPS (measured footprint fits) |

## Found and fixed during the test

- App logs were never rotated: `/var/log/sniper/sniper-requests.log` (request
  paths, client addresses, the signed-in user's email) and gunicorn's logs
  would grow without limit. `deploy/logrotate/sniper` now keeps 14 days;
  `install.sh` installs it (test in `tests/test_deploy_config.py`).

Test scaffolding (Dockerfile, proxy forwarder, runner script) lived in the
session scratchpad and is not part of the repository.
