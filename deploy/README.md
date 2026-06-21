# `deploy/` — staging deployment artifacts

Everything under this directory is configuration + tooling for the
**staging** VPS. Nothing in here grants entitlement, holds a secret, or
runs automatically. Operator-driven, reviewable, idempotent.

## Files

| File | Purpose |
|------|---------|
| `install.sh`           | Bootstrap a fresh Ubuntu 24.04 host. Installs Python 3.12, Postgres 16, Caddy, Tailscale, creates the `sniper` user and `/opt/sniper` tree, installs the systemd units, opens 80/443, leaves SSH gated by Tailscale. Idempotent: safe to re-run on an already-bootstrapped host. |
| `sync.sh`              | rsync deploy from your Mac to `sniper@<host>:/opt/sniper/releases/<git-sha>/`, flips the `current` symlink, restarts `sniper-web` + `sniper-scheduler` (the scheduler only restarts if it was already enabled). Dry-run by default; pass `--apply` to actually push. Records the deployed git revision in `/opt/sniper/current_revision` so `--rollback` can revert. |
| `backup.sh`            | Nightly `pg_dump` of the staging Postgres, encrypted with `age` to the PUBLIC recipient in `AGE_RECIPIENT`, uploaded to Backblaze B2. Runs on the VPS from `sniper-backup.timer`. The VPS has the **public** recipient only and cannot decrypt anything it produces. |
| `restore.sh`           | **OFF-HOST** decryption + restore. Runs on your Mac (refuses to run on the VPS). Pulls a named or newest backup from B2 with a read-only B2 key, decrypts locally using the age **private** key (which never leaves your Mac), restores into an isolated local verification database, runs migrations against it, prints row counts. Used for the "backup verified" acceptance drill. |
| `Caddyfile.staging`    | Caddy reverse-proxy template. Substitutes `${STAGING_HOSTNAME}` at install time. |
| `env.staging.example`  | Template for `/etc/sniper/env` on the staging host. **Never** filled in from chat — all secrets typed directly on the host. |
| `systemd/sniper-migrate.service`   | One-shot: runs `python3 -m migrations.runner`, must succeed before web/scheduler start. |
| `systemd/sniper-web.service`       | Gunicorn, bound to `127.0.0.1:8765`. Cannot start until migrate succeeds. **Web never owns the scheduler.** |
| `systemd/sniper-scheduler.service` | `python3 -m jobs.scheduler --interval 60`. **Disabled by default** — operator enables manually after approving source config. |
| `systemd/sniper-reconcile.service` | Oneshot: `python3 -m jobs.reconcile_billing`. |
| `systemd/sniper-reconcile.timer`   | Hourly trigger for the above, with jitter. |
| `systemd/sniper-backup.service`    | Oneshot: `deploy/backup.sh`. |
| `systemd/sniper-backup.timer`      | Daily trigger for the above. |
| `systemd/sniper-healthcheck.service` | Oneshot curl to Healthchecks.io ping URL after web is verified responsive. |
| `systemd/sniper-healthcheck.timer` | Every 5 minutes. |

## Recovery model (two independent halves)

| What's being recovered | Source of truth | How |
|------------------------|-----------------|-----|
| Application code       | **Private Git remote** (GitHub / GitLab, private repo, owned by you) | `git clone <remote>` onto a new VPS, then `bash deploy/install.sh && bash deploy/sync.sh --apply`. Never restore code from the VPS itself or from B2. |
| Database               | **Backblaze B2** (encrypted nightly `pg_dump`) | `bash deploy/restore.sh` from your Mac. **The age private key never lives on the VPS.** See `STAGING_RUNBOOK.md` §9. |

## age private-key boundary

The VPS encrypts backups using the **public** age recipient stored in
`/etc/sniper/env` as `AGE_RECIPIENT`. The corresponding **private** key
stays on your Mac (or a dedicated recovery host) — never on the VPS,
never in `git`, never in `/etc/sniper/`, never in chat or screenshots,
never in iCloud-Drive-synced folders.

`backup.sh` enforces this with two checks that abort with exit 2:

1. `AGE_RECIPIENT` value must look like `age1…`. If it looks like
   `AGE-SECRET-KEY-…` (a private key), backup refuses to run.
2. If any of `/etc/sniper/backup_age.key`, `/etc/sniper/age.key`,
   `/opt/sniper/backup_age.key`, `/opt/sniper/.age.key`,
   `/root/.config/age.key`, `/home/sniper/.age.key` exist, backup
   refuses to run — that file shape on this host is treated as an
   incident.

If you ever find a `*.age.key` file on the VPS: remove it, generate a
new keypair on your Mac, update `AGE_RECIPIENT` on the VPS, drop old
backup objects from B2 (they're now bound to a leaked private key).

## Non-goals (deliberately not built here)

- No live-Stripe configuration. Test mode only for staging.
- No real customer email sending.
- No multi-node orchestration / k8s / docker-compose. One host, three
  services, systemd as the supervisor — that's it for staging.
- No public Git remote. Deploys are rsync from your Mac.

## Hostname

Every place this directory would need a hostname uses the
`${STAGING_HOSTNAME}` shell variable, filled in by `install.sh` from the
prompt. If you want to switch hostnames later, edit
`/etc/caddy/Caddyfile` on the host and reload Caddy; the rest of the
stack is hostname-agnostic.
