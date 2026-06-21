# Staging Runbook

End-to-end operator guide for the Phase 3 staging deployment of Car
Sniper. Test mode only — no live Stripe, no real customer email, no tax
collection. Hostname pattern: `staging.<your-production-domain>`.

> Secret-handling rule (recurring): **secrets never travel through chat,
> commit messages, screenshots, or copy-paste**. They go directly into
> `/etc/sniper/env` on the host (owned `root:sniper`, mode `0600`).
> The pre-commit scanner in every `GIT_*.command` script refuses to
> commit `sk_…`, `whsec_…`, PEM private keys, AWS keys, and high-entropy
> secret-named assignments.

---

## 0. Pre-flight (on your Mac)

```bash
# Full test suite green
bash "RUN PHASE1 TESTS.command"      # expect: 121 passed
git log --oneline -3                  # expect HEAD == d913b58 (or later)
git status --short                    # expect empty
```

If anything is red here, fix it before touching the VPS.

### 0.1 Source-control recovery checklist (REQUIRED before provisioning)

The B2 nightly backup recovers the **database**. The application **code**
is recovered from a private Git remote, not from the VPS or from B2.
Before provisioning a single byte of cloud infra, push the committed
history to a private remote you control.

Run these commands on your Mac. The first two are inspections; nothing
mutates until step 4.

```bash
cd ~/Desktop/sniper

# 1. Confirm working tree is clean (nothing uncommitted).
git status --short
# expect: empty output

# 2. Sanity-check that no ignored / local-only files are tracked.
#    The list MUST come back empty. If anything appears, STOP and tell
#    me — it means .gitignore has a gap.
git ls-files | grep -E '^(\.env$|\.env\.|overrides\.json$|.*\.db$|.*\.db-shm$|.*\.db-wal$|.*\.sqlite|.*\.log$|^\.venv/|__pycache__|^cloudflared$|^\.DS_Store$)' || \
    echo "▸ clean — no ignored files are tracked"

# 3. Belt-and-suspenders secret scan over the committed tree (not just
#    the latest diff). Same regex family the per-phase commit scripts use.
git grep -nE 'sk_live_[A-Za-z0-9]{20,}|sk_test_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----' \
    -- ':!*.example' ':!STRIPE_TESTING.md' ':!DEPLOY.md' ':!PHASE2_PLAN.md' \
    ':!GIT_*.command' ':!STAGING_RUNBOOK.md' \
    || echo "▸ clean — no secret values in tracked content"
```

If both checks come back clean, only then run step 4:

```bash
# 4. Create a PRIVATE empty repo at your provider of choice.
#
#    GitHub: https://github.com/new
#       - Owner: your personal account
#       - Repo name: car-sniper-saas  (or whatever; private)
#       - VISIBILITY: PRIVATE (radio button)
#       - Do NOT initialize with README / .gitignore / license — we
#         already have all three locally.
#       Click Create repository.
#
#    GitLab: https://gitlab.com/projects/new
#       - Same: private, no init.
#
#    The provider then shows a "push existing repo" snippet. The two
#    commands you actually need are below. Substitute the URL the
#    provider gave you (the one ending in .git).

# 5. Add the remote LOCALLY only (no network call yet).
git remote add origin git@github.com:<your-username>/<your-repo>.git
git remote -v                              # double-check the URL

# 6. Push history + tags. This is the only network-touching command in
#    this section.
git push -u origin main
git push origin --tags                     # v0.1.0-phase1, v0.2.0-billing-core
```

After step 6, the GitHub / GitLab page should show three commits and
two tags. Take a screenshot of the repo settings showing **"Private"**
for your records.

> **Recovery model:**
> - **Code** → restored by `git clone <private-remote>` onto a new VPS,
>   then `bash deploy/install.sh && bash deploy/sync.sh --apply …`
> - **Database** → restored by `bash deploy/restore.sh` on your Mac
>   (off-host), see §9. The private age key never lives on a VPS.

If step 2 or 3 surfaces ANY hit, stop and tell me. Do not push. We fix
`.gitignore` / scrub history before the remote sees the repo.

---

## 1. Provision the VPS

You do this in the Hetzner Cloud Console — I cannot click that button
for you. ~3 minutes.

1. https://console.hetzner.cloud → Servers → **+ Add Server**
2. Location: **Ashburn, VA (ash)**
3. Image: **Ubuntu 24.04**
4. Type: **CCX13** (Dedicated vCPU · 2 cores · 8 GB · 80 GB NVMe)
5. SSH key: Hetzner will offer "Add new SSH key" — paste your `~/.ssh/id_ed25519.pub` (it's the `.pub` file, non-secret)
6. Name: `sniper-staging`
7. Networking: leave IPv4 + IPv6 on; **no firewall yet** (we'll configure ufw inside the box)
8. Click **Create & Buy now**

Record the public IPv4 address Hetzner gives you. That's the only thing
this step produces.

---

## 2. First SSH (still over public IP, briefly)

```bash
ssh root@<VPS_IP>
```

If that prompts for a password, your key wasn't installed — fix the key
in the Hetzner console and try again. Don't fall back to passwords.

Inside the VPS:

```bash
# Get this repo onto the host. The cleanest path is a one-shot scp
# from your Mac — see the section "Repository to host" below.
exit  # back to your Mac
```

### Repository to host

From your Mac:

```bash
# scp the current tree (uses git ls-files, so .env / *.db / .venv etc.
# are NOT included). One-time only — after this, sync.sh handles deploys.
cd ~/Desktop/sniper
tar --files-from <(git ls-files) -czf /tmp/sniper-bootstrap.tgz .
scp /tmp/sniper-bootstrap.tgz root@<VPS_IP>:/tmp/
rm /tmp/sniper-bootstrap.tgz

ssh root@<VPS_IP>
mkdir -p /tmp/sniper-bootstrap && cd /tmp/sniper-bootstrap
tar -xzf /tmp/sniper-bootstrap.tgz
```

---

## 3. Run `install.sh`

Still as root on the VPS:

```bash
cd /tmp/sniper-bootstrap
bash deploy/install.sh
# prompt: staging hostname (e.g. staging.car-sniper.com):
# type the hostname you'll use, e.g. staging.<your-production-domain>
```

The script will:

- `apt update && upgrade && install` base packages
- install Tailscale (does NOT bring it up yet)
- install Postgres 16, create `sniper` DB + role with a random password,
  bind Postgres to localhost only
- create the `sniper` system user and `/opt/sniper/{releases,current,shared}`
- create `/opt/sniper/.venv` with Python 3.12 + `psycopg2-binary`
- install Caddy with your hostname baked into the Caddyfile
- install the seven systemd units; enable web + migrate + reconcile.timer;
  leave scheduler, backup.timer, healthcheck.timer **disabled** by design
- configure `ufw` to allow only 80 and 443 publicly; deny SSH on the
  public interface (Tailscale handles SSH from here on)
- seed `/etc/sniper/env` from the template, with `DATABASE_URL` and
  `BILLING_ORIGIN` pre-filled

Read every line the script prints. If any step fails it aborts loudly.

---

## 4. Tailscale up (replace public SSH)

Still as root on the VPS:

```bash
tailscale up --ssh --hostname=sniper-staging
# Follow the URL it prints, authorize in your tailnet
```

On your Mac (install Tailscale once if you haven't):

```bash
# https://tailscale.com/download/macos -- one-click installer
tailscale status
# Should now list "sniper-staging" with an IP like 100.x.y.z
ssh sniper@sniper-staging
# Tailscale SSH auths via your tailnet identity. NO password prompt.
```

**Verify a second SSH session works** before disabling public-IP SSH.
Open a second terminal, repeat the ssh, confirm it works.

Then on the VPS as root:

```bash
# Lock public SSH down. ufw already denies it; this also disables sshd
# from binding 0.0.0.0. Optional but recommended after Tailscale works:
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh
# IMPORTANT: do NOT do this until your Tailscale SSH session is proven.
# Restore an exit-node session in another window first.
```

---

## 5. First deploy (sync.sh from your Mac)

### 5a. Release layout & delete-scope guarantee

Each deploy lands in an immutable per-sha directory and only the symlink
flips:

```
/opt/sniper/
├── .venv/                       (PERSISTENT — shared across releases)
├── releases/
│   ├── <sha-A>/                 (IMMUTABLE)
│   ├── <sha-B>/                 (IMMUTABLE)
│   └── <sha-C>/                 (IMMUTABLE — last 5 + the rollback target are kept)
├── current        -> releases/<sha-X>  (symlink; flipped atomically AFTER canary OK)
├── current_revision               (text)
└── previous_revision              (text — rollback target)

/etc/sniper/             (PERSISTENT — env file, age recipient, ...)
/var/log/sniper/         (PERSISTENT — gunicorn / scheduler / reconcile logs)
/var/backups/sniper/     (PERSISTENT — encrypted dump staging area)
```

`sync.sh` is structurally safe against persistent-data loss:

- The rsync destination is **always** `/opt/sniper/releases/<sha>/`,
  built by a validator that accepts only pure lowercase hex 7–40 chars
  and rejects `current`, slashes, dots, uppercase, embedded newlines,
  empty strings, and anything that would resolve to a persistent path.
- A defensive deny-list (also in the validator) explicitly rejects
  `/opt/sniper`, `/opt/sniper/current`, `/opt/sniper/.venv`,
  `/opt/sniper/releases`, `/etc/sniper`, `/var/log/sniper`,
  `/var/backups/sniper`.
- The transfer set comes only from `git ls-files`. No `--exclude`
  patterns exist in the script.
- A pre-flight check on the host refuses to proceed if
  `/opt/sniper/current` ever stops being a symlink.
- The flip is `mv -Tf current.new current` — atomic, never replaces a
  real directory.

### 5b. Prove --delete scope (run before each deploy)

```bash
bash deploy/sync.sh --verify-delete-scope sniper@sniper-staging
```

This runs `rsync --dry-run --delete --itemize-changes` against the new
release directory and prints exactly which paths would be deleted (none,
on a first deploy; rebuilt files on re-deploys of the same sha). It
then asserts that none of `/etc/sniper`, `/var/log/sniper`,
`/var/backups/sniper`, `/opt/sniper/.venv`, `/opt/sniper/current`,
`/opt/sniper/current_revision`, or `/opt/sniper/previous_revision`
appears in the deletion plan. Exits 6 if any of those would be touched.

### 5c. First deploy + canary verify

```bash
cd ~/Desktop/sniper
bash deploy/sync.sh                                   # dry-run preview
bash deploy/sync.sh --verify-delete-scope sniper@sniper-staging
bash deploy/sync.sh --apply sniper@sniper-staging
```

The `--apply` path does, in order:

1. rsync the tracked files to `/opt/sniper/releases/<sha>/`.
2. `pip install -r requirements.txt` into the shared venv.
3. `python3 -m migrations.runner` against the live DB from the new
   release (migrations 0000–0006 are forward-only and additive, so the
   prior code release continues to work against the new schema until
   the flip).
4. Start a **canary** `gunicorn` on `127.0.0.1:18765` from the new
   release, `curl /health`, kill it.
5. **Only on canary HTTP 200**: snapshot the prior revision to
   `/opt/sniper/previous_revision`, atomically flip
   `/opt/sniper/current` → new release, restart `sniper-web` (and
   `sniper-scheduler` if it was enabled).
6. Prune older release dirs (keep last 5 AND the rollback target).

A canary failure leaves `/opt/sniper/current` pointing at the previous
release untouched. The script exits non-zero with the canary log
attached.

### 5d. Verify

```bash
bash deploy/sync.sh --status sniper@sniper-staging
ssh sniper@sniper-staging "curl -fsS http://127.0.0.1:8765/health"
# expect: {"port":8765,"service":"sniper","status":"ok"}
```

### 5e. Rollback

```bash
bash deploy/sync.sh --rollback sniper@sniper-staging
```

Flips the symlink back to whatever `previous_revision` holds, swaps
`previous_revision` ↔ `current_revision` (so rolling back the rollback
also works), restarts services. No deletion ever.

---

## 6. DNS + TLS

In your registrar (Cloudflare / Namecheap / Porkbun / whatever):

```
A    staging       <VPS_IP>     TTL 300
```

Wait 1–2 minutes (`dig +short staging.<your-production-domain>`),
then on your Mac:

```bash
curl -fsS https://staging.<your-production-domain>/health
# expect the same {"status":"ok"} JSON.
# Caddy will fetch a Let's Encrypt cert automatically on first request.
```

If you get a TLS handshake error, the DNS hasn't propagated yet — wait
another minute and retry.

---

## 7. Bootstrap the admin account

```bash
ssh sniper@sniper-staging
cd /opt/sniper/current
sudo -u sniper /opt/sniper/.venv/bin/python3 -c "
import os, sys, types
fk=types.ModuleType('flask'); fk.g=type('G',(),)(); fk.jsonify=lambda *a,**k:None
class _R: headers={}; remote_addr=''; cookies={}; is_secure=False
fk.request=_R()
sys.modules['flask']=fk
import auth
import getpass
email = input('admin email: ').strip().lower()
pw    = getpass.getpass('admin password (won't echo): ')
u = auth.create_user(email, pw, role='admin', plan='pro')
print('created:', u)
"
```

You'll be prompted for the email + password. The password never echoes
to the terminal and never appears in any log.

Open `https://staging.<your-production-domain>/login` and sign in.

---

## 8. Stripe test-mode wiring

Follow `STRIPE_TESTING.md` (it's in the repo and now on the host) for
the dashboard side: create the two TEST-mode prices, copy the
`price_…` IDs, generate the test `sk_test_…`.

Then on the VPS:

```bash
ssh sniper@sniper-staging
sudo -e /etc/sniper/env
# fill in:
#   STRIPE_SECRET_KEY=sk_test_…
#   STRIPE_PRICE_STARTER=price_…
#   STRIPE_PRICE_PRO=price_…
# leave STRIPE_WEBHOOK_SECRET blank for now (next step generates it)
sudo systemctl restart sniper-web.service
```

### Permanent Stripe TEST-mode webhook (replaces the dev CLI)

In the Stripe Dashboard (top-left toggle: **Test mode**):

1. Developers → Webhooks → **+ Add endpoint**
2. URL: `https://staging.<your-production-domain>/api/billing/webhook`
3. Listen to: select **these events only** (the application handles
   exactly these — see `billing.py:_HANDLERS`):

   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.paid`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
   - `charge.refunded`
   - `charge.dispute.created`
   - `charge.dispute.closed`

4. Save. Click into the new endpoint → **Reveal signing secret**.
5. On the VPS:

```bash
sudo -e /etc/sniper/env
# add: STRIPE_WEBHOOK_SECRET=whsec_…
sudo systemctl restart sniper-web.service
```

6. Back in the Stripe Dashboard, click **Send test webhook** on the
   endpoint → pick `checkout.session.completed` → Send. Stripe should
   show **2xx received**. If you get 400, the secret is wrong.

7. The dashboard's Subscription card on staging now shows the two tiers
   with active **Upgrade** buttons.

---

## 9. Backups + restore drill (split-key, off-host decryption)

The backup pipeline is intentionally **split-key**:

| Where it lives                     | Role            | What it can do | What it can NOT do |
|------------------------------------|-----------------|----------------|--------------------|
| VPS `/etc/sniper/env` `AGE_RECIPIENT` | PUBLIC age recipient (`age1…`) | encrypt nightly backups | decrypt anything |
| Your Mac `~/.config/sniper-staging.age.key` | PRIVATE key | decrypt backups during restore | (never reaches the VPS) |
| Backblaze B2 bucket                | encrypted storage | hold the encrypted blobs | decrypt them — they're age-encrypted |

> **Hard rules — read these even if you've used age before:**
> - The PRIVATE key file (`*.age.key` — contains `AGE-SECRET-KEY-…`) must
>   **never** appear on the VPS, in `/etc/sniper/env`, in `git`, in
>   logs, in chat, in screenshots, in iCloud Drive sync folders, or in
>   any password manager attachment that auto-syncs across devices you
>   don't fully control. Treat it the way you treat your SSH private key.
> - If you ever find one on the VPS (`find / -name '*.age.key'`), that's
>   an incident: wipe it, generate a new keypair, update `AGE_RECIPIENT`
>   on the VPS, drop the old backup objects from B2 (they're now bound
>   to a leaked key).
> - `backup.sh` aborts with exit 2 if it sees a private-key-shaped file
>   in any of the usual mistake locations, OR if `AGE_RECIPIENT` value
>   itself looks like a private key.

### 9a. One-time: generate the keypair on your Mac

```bash
mkdir -p ~/.config && chmod 700 ~/.config
age-keygen -o ~/.config/sniper-staging.age.key
chmod 600 ~/.config/sniper-staging.age.key
# Back this file up to your password manager (1Password Secure Note,
# Bitwarden file attachment, encrypted USB) BEFORE you lose your Mac.
# Without this file, you cannot decrypt any backup.

# Extract the PUBLIC recipient line — this is the ONLY part that
# leaves your Mac.
grep -E "^# public key:" ~/.config/sniper-staging.age.key | awk '{print $NF}'
# Output: age1…  ← copy this string for the next step.
```

### 9b. On the VPS — wire encryption + B2 credentials (PUBLIC RECIPIENT ONLY)

```bash
ssh sniper@sniper-staging
sudo -e /etc/sniper/env
# Fill in (PUBLIC recipient only — no private key, ever):
#   AGE_RECIPIENT=age1…
#   B2_BUCKET=staging-sniper
#   B2_KEY_ID=…             (B2 application key with WRITE scope on this bucket)
#   B2_APPLICATION_KEY=…
#   B2_ENDPOINT=https://s3.us-east-005.backblazeb2.com

# Defensive sanity check — must return NOTHING:
sudo find /etc/sniper /opt/sniper /root /home -name '*.age.key' \
    -o -name 'backup_age*' 2>/dev/null

sudo systemctl enable --now sniper-backup.timer
sudo systemctl start sniper-backup.service     # run a backup now
journalctl -u sniper-backup.service --since '2 minutes ago'
# expect to see: "uploaded to B2 staging-sniper/sniper-…sql.zst.age"
```

If `backup.sh` exits with code 2, it printed exactly why — fix and retry.

### 9c. Restore drill — run on your Mac, NEVER on the VPS

The B2 read access for restores is a **separate** application key from
the write key the VPS uses. Create a second B2 key in the B2 UI with
**read-only** scope on `staging-sniper`. Export those locally before
running the drill:

```bash
# On your Mac (NOT on the VPS):
cd ~/Desktop/sniper

# Off-host env — keep these in your shell or a file you DON'T commit
# (e.g. ~/.config/sniper-staging.restore.env, mode 0600, sourced manually).
export B2_KEY_ID="…"               # read-only B2 application key id
export B2_APPLICATION_KEY="…"      # corresponding application key
export B2_BUCKET="staging-sniper"
export B2_ENDPOINT="https://s3.us-east-005.backblazeb2.com"
# AGE_KEY_FILE defaults to ~/.config/sniper-staging.age.key
# VERIFICATION_DATABASE_URL defaults to postgresql://localhost/sniper_restore_…

# You need a local Postgres for the verification DB. The easiest
# throwaway path on macOS is Postgres.app (https://postgresapp.com),
# or a Docker container:
#   docker run --rm -d --name pg-restore -p 5433:5432 -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16
# Then prefix:
#   export VERIFICATION_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/restore

bash deploy/restore.sh
# The script:
#   1. lists newest sniper-*.sql.zst.age in your B2 bucket
#   2. downloads it
#   3. decrypts LOCALLY using AGE_KEY_FILE (private key never leaves)
#   4. decompresses
#   5. creates a fresh verification DB and pg_restores into it
#   6. runs `python3 -m migrations.runner` against the restored DB
#      (must report 0 new migrations — schema is already current)
#   7. prints row counts for the tables the app cares about
#   8. drops the verification DB (use --keep to leave it for inspection)
#
# It refuses to run if:
#   - you accidentally ran it on the VPS (detected via /opt/sniper + uname)
#   - AGE_KEY_FILE permissions are world-readable
#   - VERIFICATION_DATABASE_URL points anywhere but localhost / 127.0.0.1
```

A backup is "verified" only after at least one off-host restore drill
has succeeded end-to-end. Record the result in the **Restore drill
log** at the bottom of this runbook (date, object name, size, sha256
first 16 chars, wall-clock seconds, outcome).

---

## 10. Monitoring

Create three checks at https://healthchecks.io (free tier):

| Check name                | Schedule (Cron-like)          | Grace |
|---------------------------|-------------------------------|-------|
| `sniper-staging-web`      | `*/5 * * * *`                 | 5 min |
| `sniper-staging-reconcile`| Hourly                        | 30 min |
| `sniper-staging-backup`   | Daily                         | 6 h   |

Copy the ping URL of each. On the VPS:

```bash
sudo -e /etc/sniper/env
# add:
#   HEALTHCHECK_WEB_URL=https://hc-ping.com/<web-uuid>
#   HEALTHCHECK_RECONCILE_URL=https://hc-ping.com/<reconcile-uuid>
#   HEALTHCHECK_BACKUP_URL=https://hc-ping.com/<backup-uuid>
sudo systemctl enable --now sniper-healthcheck.timer
```

Configure each healthchecks.io check's notifications to send email
(and/or Slack/Pushover/PagerDuty) on **down** transitions.

Also set up a billing-anomaly alert (separate from the healthchecks):
admins can query `/api/admin/billing/health` and alert on
`unresolved_anomalies_by_severity.critical > 0`. For staging soak,
manual daily check is acceptable; for production hook this into your
alert provider.

---

## 11. Acceptance test list

Run each. Mark ✓/✗ as you go. All must be green AND the system stable
for **48 h with synthetic test events** before Phase 4 live is even
considered.

| # | Test | How | Status |
|---|------|-----|--------|
| 1 | Fresh Postgres migrations | `sudo systemctl restart sniper-migrate.service; journalctl -u sniper-migrate.service -n 30` | |
| 2 | Health endpoint over TLS | `curl -fsS https://<host>/health` | |
| 3 | Dashboard loads, login works | browser, sign in as your admin | |
| 4 | Free user → tier picker w/ Upgrade buttons | register a 2nd test user via /login | |
| 5 | Scheduler service DISABLED | `systemctl status sniper-scheduler.service` → `Loaded: disabled` | |
| 6 | Reconciliation dry run | `sudo systemctl start sniper-reconcile.service; journalctl -u sniper-reconcile.service --since '1m ago'` → exit 0, JSON line | |
| 7 | Permanent webhook signature | Stripe dashboard → Send test → 2xx | |
| 8 | Browser Stripe Checkout (test card 4242…) | from the dashboard, click Upgrade → Starter, pay, return | |
| 9 | Return → pending state → activation | watch the dashboard right after Stripe redirects you back; "Updating subscription" then "Active" | |
| 10 | Starter entitlement | save a deal — should succeed (not 402) | |
| 11 | Pro plan switch | use the Stripe Customer Portal → swap to Pro → wait for webhook → `/api/billing/me` shows pro | |
| 12 | VIN endpoint gated to pro | `/api/vin/check` returns 200 for pro user, 402 for starter | |
| 13 | Cancel at period end | in portal, "Cancel subscription" → access retained, banner shows the end date | |
| 14 | Failed payment | `stripe trigger invoice.payment_failed` (still works against permanent endpoint) → access drops | |
| 15 | Refund (full) | `stripe trigger charge.refunded` with full refund → access drops, anomaly recorded | |
| 16 | Dispute opened | `stripe trigger charge.dispute.created` → access suspended | |
| 17 | Duplicate webhook | resend any event via dashboard → 2xx, `stripe_webhook_events` row count unchanged | |
| 18 | Reconciliation recovery | manually `UPDATE subscriptions SET status='canceled' WHERE…` then `systemctl start sniper-reconcile.service` → status corrected | |
| 19 | Backup → B2 | `journalctl -u sniper-backup.service -n 20` shows "uploaded" | |
| 20 | Restore drill | `deploy/restore.sh` end-to-end | |
| 21 | Mobile billing UI | iPhone Safari → /login → dashboard renders, Subscription card readable + tap targets ≥ 44 pt | |
| 22 | Desktop billing UI | Chrome at 1440px → no horizontal scroll, no layout shift | |
| 23 | Two-host scheduler race | (only if you spin up a 2nd VPS pointed at the same DB) — start scheduler on both → one exits with `lock_held` | |
| 24 | 48-hour soak | `journalctl --since '48 hours ago' -u sniper-web -u sniper-reconcile` → no `UNCAUGHT`, no missed healthchecks | |

---

## Operator command cheat sheet

```bash
# Tail logs
journalctl -u sniper-web.service -f
journalctl -u sniper-scheduler.service -f
journalctl -u sniper-reconcile.service -n 50
journalctl -u sniper-backup.service -n 50
journalctl -u caddy -n 50
sudo -u postgres journalctl -u postgresql -n 50

# Deploy
bash deploy/sync.sh                                # dry-run
bash deploy/sync.sh --apply sniper@sniper-staging  # deploy
bash deploy/sync.sh --status sniper@sniper-staging # current revision + service state

# Rollback to previous release (atomic symlink flip)
bash deploy/sync.sh --rollback sniper@sniper-staging

# Restart services after manual env edit
sudo systemctl restart sniper-web.service
sudo systemctl restart sniper-scheduler.service     # only if enabled

# Enable scheduler (DO NOT DO until you've approved a source config)
sudo systemctl enable --now sniper-scheduler.service
sudo systemctl disable --now sniper-scheduler.service   # turn off again

# Trigger ad-hoc reconcile / dry-run reconcile
sudo systemctl start sniper-reconcile.service
sudo -u sniper /opt/sniper/.venv/bin/python3 -m jobs.reconcile_billing --dry-run

# Inspect job-lock state
sudo -u postgres psql -d sniper -c \
  "SELECT job_name, holder, acquired_at, expires_at FROM billing_job_locks;"

# Inspect unresolved anomalies
sudo -u postgres psql -d sniper -c \
  "SELECT id, type, severity, created_at FROM billing_anomalies \
   WHERE resolved=false ORDER BY created_at DESC LIMIT 20;"

# Show the last reconciliation summary
sudo -u postgres psql -d sniper -c \
  "SELECT * FROM billing_reconciliation_runs ORDER BY started_at DESC LIMIT 5;"

# Incident: stop everything fast
sudo systemctl stop sniper-web.service sniper-scheduler.service \
                    sniper-reconcile.timer sniper-backup.timer \
                    sniper-healthcheck.timer
# (Caddy stays up serving 502s; DNS stays valid. Restart in reverse order.)
```

---

## Restore drill log

| Date (UTC) | Backup object | Size | SHA256 (first 16 chars) | Restore wall-clock | Result |
|------------|--------------|-----:|-------------------------|--------------------:|--------|
| _fill in_  | _fill in_    |      | _fill in_               | _fill in_           | _fill in_ |

A backup is verified by a successful restore. A backup that has never
been restored is, for engineering purposes, **not a backup**.

---

## Phase 4 live launch — blockers list

These must all be ✓ before any consideration of live mode. Phase 3 (this
document) does **not** unlock Phase 4 on its own.

- [ ] Every row of section 11 above is green
- [ ] 48-hour soak passed with no `UNCAUGHT` log lines and no missed
      healthcheck pings
- [ ] Restore drill log has at least one successful entry
- [ ] You've made a written **tax-posture decision** (Stripe Tax on /
      off / hybrid) and a qualified tax professional has signed off on
      the registrations you need — see `DEPLOY.md` "Pre-live tax
      decision checklist"
- [ ] Live-mode webhook endpoint registered in Stripe Dashboard live
      mode, with its own separate `whsec_…` in production env (not
      copied from staging)
- [ ] Live Starter + Pro prices created in live mode with reviewed
      pricing
- [ ] Customer Portal activated in live mode (separate toggle from test
      mode)
- [ ] Email provider chosen + integrated for receipts / failed-payment
      warnings (Resend, Postmark, SES) — Phase 4 work
- [ ] Production VPS sized + provisioned separately from staging
- [ ] Production runbook (this document, copied + edited for live
      values) reviewed line-by-line
- [ ] Rollback rehearsed at least once
