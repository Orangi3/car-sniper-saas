# Deployment test reports — disposable host

## Go-live rehearsal, 2026-10-31 (PR #16, `golive.sh`)

A fresh disposable Ubuntu 24.04 host (SIMULATED server and services). The
steps:
1. `install.sh` and `sync.sh --apply` at `7a24672`.
2. The checks below.
3. The two `golive.sh` fixes the run found.
4. `sync.sh --apply` of the final commit `732ab60` over the old release,
   with `golive.sh` and the restore drill run again on it.

Test-only stand-ins:
- Resend: an SMTP server that requires STARTTLS, with a certificate from a
  throwaway CA the host trusts, then AUTH;
- B2: an S3-compatible server (moto);
- Healthchecks.io: a local HTTP server;
- the restore drill's computer: the same host, holding a test age key.

| Check | Result |
|---|---|
| `golive.sh` with nothing configured | stops at gate 1 and lists the missing settings by name, values never printed |
| `golive.sh` with a test-only SSH rule still open | gates 1–7 pass, then it stops at gate 8: "ufw allows more than 80 and 443: 22/tcp" |
| `golive.sh --test-email` after removing the rule, at `732ab60` | all 9 gates pass; admin present |
| Test email | delivered over STARTTLS + AUTH, with the certificate verified |
| Health pings | web, pipeline and backup |
| Backup | encrypted dump uploaded to the S3 stand-in; nightly timer on |
| Timers | reconcile disabled; backup and health on |
| Restore drill (`restore.sh`) | download, off-host decrypt, restore into a fresh database, 0 new migrations, row counts, drop: PASS |
| Seller confirmation email over STARTTLS, contact by plan | PASS |
| Reboot (`docker restart`) | systemd running; PostgreSQL, Caddy, web, worker, scheduler active; reconcile still disabled; HTTPS with the verified certificate; both health probes pinged |
| Memory | ~350–450 MB for the whole stack (RSS: two gunicorn workers ~54 MB each, worker and scheduler ~45 MB each, Caddy ~43 MB, PostgreSQL) |

Found and fixed during this rehearsal:
- **`golive.sh`, admin check.** It counted admins through a raw database
  connection that has no `execute`, and hid the error. It reported "no
  admin account yet" although one existed. It now uses the app's
  `db.session()` and stops on an error (`732ab60`).
- **`backup.sh` and `restore.sh`, B2 region.**
  - `backup.sh` signed every request for `us-east-005`; `restore.sh` set
    no region.
  - Both now take the region from `B2_ENDPOINT`, so a bucket in any B2
    region works. The default is unchanged.
  - Not proven against real B2; tested with sample endpoints.
- **Test setup, not product.**
  - The first post-reboot probe passed with no pings. The reboot had
    cleared `/tmp`, removing the stand-in ping server.
  - `healthcheck.sh` still exits 0 when a ping can't be delivered. That's
    fine for a dead-man check: a missing ping is what raises the alert.
  - With the stand-in restarted, both pings arrived.

Not tested here:
- a real VPS;
- Let's Encrypt;
- Tailscale (stub);
- Resend, B2 and Healthchecks.io themselves;
- the B2 lifecycle rule.

### After the PR #16 security review (`9cbbab1`, fresh disposable host)

The run at `9cbbab1` repeated everything above, and every check passed.
New checks:

| Check | Result |
|---|---|
| A Healthchecks URL that doesn't answer | stops at gate 7: "HEALTHCHECK_BACKUP_URL doesn't accept a ping", without printing the URL |
| `ufw allow in on tailscale0` | accepted; all gates pass |
| `--test-email` without an address | exits 2 at once (it used to loop) |
| All gates, with an admin present | 9 gates, plus "an admin account exists" and the test email |
| After the reboot, with the stand-in ping server restarted | both probes pinged |

### Hardening round (`f344f26`, fresh disposable host)

The run at `f344f26` repeated everything above, and every check passed. New
checks:

| Check | Result |
|---|---|
| `B2_ENDPOINT=http://s3.us-east-005.backblazeb2.com` | `golive.sh` stops at gate 1 ("B2_ENDPOINT must start with https://"). `sniper-backup.service` also refuses it, before any request |
| Admin account demoted to an ordinary user | `golive.sh` stops at gate 9: "no admin account" |
| `golive.sh` with no `--test-email` | exits 2 with the usage |
| Loopback stand-in endpoints (`http://127.0.0.1:…`) | accepted, as intended for tests |
| After the reboot, with the stand-in ping server restarted | both probes pinged |

## Release rehearsal, 2026-10-31 (final release heads)

The release as it will ship, on a fresh **disposable** Ubuntu 24.04 host
(Docker, systemd as PID 1). The server, people and cars are SIMULATED, and
nothing touched real infrastructure. The host was first installed and
deployed at PR #12's head `5c6b2f2` (the state after merging #11 and #12),
then upgraded to PR #14's head `b3084a2` (the state after all four merges).

| Check | Result |
|---|---|
| Fresh install (`install.sh` from `5c6b2f2`) | PASS — PostgreSQL 16.15, Caddy 2.6.2 (substitute, below), Python 3.12.3; ufw allows only 80 and 443 publicly |
| Deploy `5c6b2f2`, then upgrade to `b3084a2` (`sync.sh --apply`) | PASS — migrations 0019, 0020 and 0021 applied on PostgreSQL; web, worker, scheduler, Caddy and PostgreSQL active after each flip |
| HTTPS through Caddy | PASS — `/health` 200 on both releases. Headers: HSTS `max-age=31536000`, CSP, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` |
| Static isolation | PASS — `/deploy/env.staging.example` 404 |
| `/etc/sniper/env` | `root:sniper 0600`; install seeds `ALLOW_REGISTRATION=0`, `SNIPER_COMMERCIAL_MODE=1`, `SNIPER_TRUSTED_PROXIES` |
| Database dump, restore into a throwaway database | PASS |
| Backup job without B2 keys | fails loudly: `B2_KEY_ID required` |
| Rollback to `5c6b2f2` on the 0021 schema | PASS — `/health` 200 |
| Roll forward to `b3084a2` | PASS |

The first buyer-and-seller test (`FIRST_REAL_TEST.md`) ran over HTTPS with
the real worker. Email went to a local SMTP sink and health pings to a local
HTTP sink, test-only stand-ins for Resend and Healthchecks.io.

| Step | Result |
|---|---|
| Register without an invitation | 403 |
| Owner issues invitations (CLI) | PASS |
| Seller registers, receives the confirmation email, confirms | PASS |
| Seller lists a car; the worker scores it | PASS — listing active and scored |
| Buyer finds it under FlipFirst sellers | PASS |
| No invented market value; provenance says seller-reported | PASS |
| Buyer analyzes it with their own resale estimate | PASS — basis `user_estimate`; "Worth investigating"; break-even $7,055; maximum offer $6,145 (SIMULATED inputs) |
| Buyer saves two deals, changes a stage and notes | PASS |
| Buyer reports the listing; owner sees it (`seller_listings reports`) | PASS |
| `pilot_report` | counts only; no emails or car details |
| Server logs | no tracebacks |
| Seller removes the listing; owner deletes an account | PASS — rows gone |
| Seller contact by plan | Free buyer: "upgrade to see the seller's contact". After step 4's `auth.set_plan(…, 'starter')`, verbatim: the seller's verified email. Back to `'free'`: hidden |
| Billing reconcile without Stripe keys | refuses to run (exit 1); the hand-set plan is kept |
| Health timer | both pings (web, pipeline) |

Found during the rehearsal:
- **Test setup, not product.**
  - The first run had no certificate for the `.test` name. The 2026-10-30
    run had applied the local-CA edit by hand; the script now does it.
  - The script then trusted a second local CA that `caddy validate` had
    created as root. The health job withheld the web ping and logged
    `web /health FAILED`: the dead-man alert behaving as designed.
  - With the right CA trusted, both pings arrived.
- **Documentation, fixed in `FIRST_DEPLOY.md` (now handled by `golive.sh` gate 4).** `install.sh`
  enables `sniper-reconcile.timer`. Without Stripe keys that job refuses to
  run (`missing required env var(s): STRIPE_SECRET_KEY`). In a pilot with no
  billing, disable the timer and skip the reconcile check, or the check
  alerts every hour.

Not tested here (unchanged from 2026-10-30, table below):
- Caddy from Caddy's own repository;
- a Let's Encrypt certificate (needs real DNS);
- Tailscale (stub);
- Resend and Healthchecks.io themselves;
- B2 upload and a restore from B2;
- a 1–2 vCPU VPS.

## First deployment test, 2026-10-30

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
| Backups to B2, restore drill | not run (no B2 bucket or age key) | `FIRST_DEPLOY.md`, Backups; rehearsed against an S3 stand-in on 2026-10-31 (above) |
| Email (Resend), Healthchecks pings | 2026-10-30: not run. 2026-10-31: local SMTP and HTTP sinks (test-only) | the real services, steps 10 and 13 |
| Hardware | container on a 4-CPU sandbox | 1–2 vCPU / 2 GB VPS (measured footprint fits) |

## Found and fixed during the test

- App logs were never rotated: `/var/log/sniper/sniper-requests.log` (request
  paths, client addresses, the signed-in user's email) and gunicorn's logs
  would grow without limit. `deploy/logrotate/sniper` now keeps 14 days;
  `install.sh` installs it (test in `tests/test_deploy_config.py`).

Test scaffolding (Dockerfile, proxy forwarder, runner script) lived in the
session scratchpad and is not part of the repository.
