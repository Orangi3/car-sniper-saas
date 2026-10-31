# First deployment — the short path

Owner checklist for the first live server. Every command below was run on
a clean Ubuntu 24.04 systemd host (Mission 12) except where marked
**not yet proven**. Detailed background: `STAGING_RUNBOOK.md`.

Nothing here is automatic. Each step is yours to approve and run.

## What you need first (accounts; ~45 min total, mostly waiting)

| Account | Cost | Used for |
|---|---|---|
| The domain `tryflipfirst.com` (planned app host: `app.tryflipfirst.com`) | ~$11–20/yr if not yet registered | `app` → server |
| VPS: Ubuntu 24.04, ≥1 vCPU, ≥2 GB RAM, ≥25 GB disk, US region | ~$10–12/mo | everything |
| Tailscale (free Personal plan; check its terms fit your use — costs below) | $0 | SSH without an open port |
| Resend (free plan: 3,000/mo, 100/day) | $0 | seller verification email |
| Backblaze B2 | $0 under 10 GB stored | encrypted off-server backups |
| Healthchecks.io (free plan: 20 checks) | $0 | alerts when something stops |

On your Mac: `brew install rsync age` (macOS's built-in rsync may lack
options `sync.sh` uses), plus Tailscale.

Measured footprint: the whole idle stack (PostgreSQL, Caddy, web with 2
workers, worker, scheduler, fail2ban) used ~450 MB RAM and ~150 MB disk;
the app under a 20-client load used ~220 MB. 2 GB leaves headroom; add a
1 GB swap file. This is sized for a pilot, not for 1,000 concurrent users.

## Monthly cost for the Tuscaloosa pilot (ESTIMATED)

Nothing has been purchased. Prices are list prices from the providers'
pricing pages as summarized by web search on 2026-10-31; the pages
themselves could not be opened from the build sandbox. They are not quotes:
open each page before you sign up.

| Item | Per month | Free-plan limit and overage | Note |
|---|---|---|---|
| VPS, 2 GB RAM, 1 vCPU, 50 GB disk, US region | $10–12 | 2 TB transfer included; then $0.005–0.01/GB | DigitalOcean Basic and Akamai/Linode $12, Vultr Regular $10. The pilot uses ~10–30 GB a month. Hetzner's US prices reportedly rose in June 2026 (~$20 for 2 GB); recheck before choosing it |
| Domain (.com) | ~$0.95–1.70 | — | $11.17–19.98 a year at renewal: at-cost registrars (Cloudflare, Porkbun) ~$11–12; Namecheap renews at ~$20. The .com wholesale price rises on 1 Nov 2026 |
| Resend email | $0 | 3,000 a month **and 100 a day**; no overage — extra mail is refused until Pro ($20/mo) | the only email FlipFirst sends is the seller contact confirmation (at most 5 per seller a day) |
| Backblaze B2 encrypted backups | $0 | first 10 GB free; then $6.95/TB-month; downloads free up to 3× what you store | nightly dumps at pilot size stay far under 10 GB; the lifecycle rule (step 11) keeps it bounded |
| Healthchecks.io | $0 | 20 checks (you use 3) | — |
| Tailscale | $0, or ~$8 | Personal plan is free; one Standard seat ~$8/mo | the Personal plan may be for personal, non-business use: read its terms. If it doesn't fit, pay for one seat |
| Let's Encrypt certificate | $0 | 5 identical certificates per week | keep Caddy's data directory if you rebuild the server, or a rebuild loop can be refused for days |
| eBay Browse API | $0 | — | no fee published; production approval required (`EBAY_SETUP.md`) |
| **Total, pilot with no paid data** | **~$11–14** (~$19–22 with a Tailscale seat) | | the first real test (`FIRST_REAL_TEST.md`) uses only FlipFirst seller listings |
| MarketCheck private sellers — only if chosen | ≥ $299 + per-call fees | — | unverified; get a written quote and permission first |
| Twilio SMS | not needed | — | push alerts use ntfy (free) |

Not included: the provider's own server backups (optional; DigitalOcean
charges +20%, ~$2.40/mo; the B2 dumps already cover the database), legal
review, and your time for support (testers email you directly).

## Pilot posture (settings in `/etc/sniper/env`)

- `ALLOW_REGISTRATION=0` — testers join by invitation
  (`python -m invites issue --note "pilot" --count 10 --days 14`).
- `SNIPER_COMMERCIAL_MODE=1` — only licensed sources run; eBay stays off
  until `EBAY_COMMERCIAL_AGREEMENT` records eBay's approval
  (`EBAY_SETUP.md` step 7).
- Invited accounts are on Free: no alerts until Gate 2
  (`PILOT_TUSCALOOSA.md`). One exception: in the first real test
  (`FIRST_REAL_TEST.md`) you set the buyer to Lite by hand, because only
  paid plans show a seller's contact email. Alerts still need a market
  value, and a one-seller test with no licensed data has none.
- Validation runs go in `/opt/sniper/shared/validation/` (writable by the
  app user, outside the release directory, not served by Caddy); purge each
  one after its review.
- What runs: `sniper-web` (gunicorn, 2 workers), `sniper-scheduler`
  (polling), `sniper-worker` (valuation and notification jobs),
  `sniper-backup.timer` (nightly), `sniper-healthcheck.timer`,
  `sniper-reconcile.timer` (billing state; off until you add Stripe
  keys, step 13), and Caddy for HTTPS. All are
  systemd units installed by `deploy/install.sh`; the scheduler and timers
  are enabled by hand (steps 7, 11, 13).

## The 13 steps

1. **Server.** Create the VPS (Ubuntu 24.04, your SSH key, US region).
   Note its IP.
2. **DNS.** At your registrar add an `A` record `app` → the IP. Wait until
   `dig +short app.tryflipfirst.com` prints it.
3. **Connect + copy the release** (STAGING_RUNBOOK §2, "Repository to
   host"): `ssh root@<IP>`; from the Mac, `tar` + `scp` the tree.
4. **Install** (as root on the server, keep this session open):
   `STAGING_HOSTNAME=app.tryflipfirst.com bash /tmp/sniper-bootstrap/deploy/install.sh`
   then `tailscale up --ssh --hostname=sniper-staging`.
   From the Mac, in a *second* terminal: `ssh root@sniper-staging` must
   work before you close the first. (ufw now blocks public SSH.)
   **Not yet proven:** the Tailscale steps (unavailable in the test
   environment). If the second session fails, fix it from the first one
   or the provider's web console; don't close the first session.
5. **Secrets** — on the server: `nano /etc/sniper/env` (file is
   `root:sniper 0600`; one `KEY=value` per line, comments on their own
   line). `DATABASE_URL` and `BILLING_ORIGIN` are already filled in.
   Add now: `SMTP_*` (step 10), `B2_*` + `AGE_RECIPIENT` (step 11),
   `HEALTHCHECK_*` (step 13). Never paste these into chat or git.
6. **Deploy + migrate** — from the Mac, in the repo at the merged `main`:
   `bash deploy/sync.sh --apply root@sniper-staging`.
   It installs dependencies, migrates PostgreSQL, health-checks a canary,
   flips `current`, restarts and waits for `sniper-web healthy`. Any
   failure stops before the flip (proven with a wrong DB password).
7. **Start background services:**
   `systemctl enable --now sniper-worker sniper-scheduler`
8. **HTTPS** — Caddy fetches the certificate on the first request once DNS
   points at the server. Check: `curl -I https://app.tryflipfirst.com/health`
   → `200` and a `strict-transport-security` header.
   **Not yet proven:** real certificate issuance (needs real DNS).
9. **Health + admin** — `bash deploy/sync.sh --status root@sniper-staging`;
   create your admin account (STAGING_RUNBOOK §7, proven); sign in at
   `https://app.tryflipfirst.com/m`.
10. **Email** — see "Email setup" below; then Sell →
    "Email me a confirmation link" must arrive in Gmail *and* Outlook.
11. **Backups** — create the B2 bucket + keys and an age keypair (below),
    then `systemctl enable --now sniper-backup.timer` and run one now:
    `systemctl start sniper-backup.service && journalctl -u sniper-backup -n 5`
    → `backup OK`. Then do one restore drill from the Mac (below).
12. **First real seller** — `SELLER_PILOT.md`. Switch to mode B first
    (`DEPLOY.md` → Operating modes): `SNIPER_SELLER_SUBMISSIONS=1`,
    restart `sniper-web`.
13. **Monitoring** — create 3 checks in Healthchecks.io (web, pipeline:
    period 5 min, grace 15 min; backup: 1 day), put the ping URLs in
    `/etc/sniper/env`, then
    `systemctl enable --now sniper-healthcheck.timer`. A missing ping
    emails you.
    Billing is off in the pilot, so also run
    `systemctl disable --now sniper-reconcile.timer`. `install.sh` enables
    it, but without Stripe keys the job refuses to run (seen on the
    disposable host), so the timer would fail every hour. When you add
    Stripe keys, re-enable it and add a fourth check (reconcile: 1 h).

Rollback at any time: `bash deploy/sync.sh --rollback root@sniper-staging`
(proven; older code runs on the newer schema because migrations are
additive). Rolling back to a release from before session hashing
(migration 0018) signs everyone out once; they sign in again.

## Sign-in security (nothing to set up, two things to know)

- `/etc/sniper/env` comes with `SNIPER_TRUSTED_PROXIES=127.0.0.1,::1`.
  Behind Caddy it makes the sign-in limit (10 failed attempts per visitor
  per 5 minutes) count each visitor separately; without it every visitor
  would share one budget. `python -m preflight` and the Operations page
  show a failed "client addresses" check if it's missing.
- Sign someone out everywhere (lost phone, suspected break-in), as root:
  ```
  systemd-run --pipe --wait --quiet --collect --uid=sniper -p EnvironmentFile=/etc/sniper/env \
      -p WorkingDirectory=/opt/sniper/current /opt/sniper/.venv/bin/python3 -c \
      "import auth; print(auth.revoke_sessions(auth.get_user_by_email('pat@example.com').id))"
  ```
  It prints how many sessions ended. The database stores only hashes of
  session tokens, so a copy of it can't be used to sign in.

## Email setup (Resend, ~15 min, $0)

1. Sign up at resend.com → Domains → Add `app.tryflipfirst.com` (a subdomain
   keeps your main domain's email reputation separate).
2. Copy the DNS records it shows (SPF `TXT`, DKIM `TXT`, MX for bounces)
   into your registrar; add a DMARC record
   `_dmarc.app` `TXT` `v=DMARC1; p=none; rua=mailto:<you>`.
   Wait for Resend to show the domain as verified.
3. API Keys → create a key with **sending access only**, for that domain.
4. In `/etc/sniper/env`:
   ```
   SMTP_HOST=smtp.resend.com
   SMTP_PORT=587
   SMTP_USER=resend
   SMTP_PASSWORD=<the API key>
   SMTP_FROM=FlipFirst <no-reply@app.tryflipfirst.com>
   ```
   `systemctl restart sniper-web`. Many VPS providers block outbound ports
   25, 465 and 587 on new accounts; if yours does, use `SMTP_PORT=2587`
   (also STARTTLS).
5. Test (step 10). In Gmail, "Show original" must say `SPF: PASS`,
   `DKIM: PASS`. A seller seeing "we couldn't send the email right now"
   means the server couldn't reach or log in to Resend:
   `journalctl -u sniper-web | grep "verification email"` shows why
   (never the password).

The app always verifies the server's TLS certificate and refuses to send
a password unencrypted. Free-plan limits (100/day) are far above pilot
needs; the per-seller limit is 5 confirmation emails a day.
**Unverified:** Resend's current plan terms — check their pricing page
before relying on them. Fallback if Resend doesn't suit: Postmark (paid
from ~$15/mo, strong deliverability) or Amazon SES (cheapest per email,
slower account approval).

## Backups and disaster recovery

What runs: nightly at 04:23 UTC, `pg_dump` → zstd → age (public key
only) → B2; the last 3 nights also stay on the server. A failed backup
withholds the Healthchecks ping, which alerts you.

One-time setup:
- B2: create bucket `staging-sniper` (private). Lifecycle rule: hide
  files 30 days after upload, delete 1 day after hiding (each night's
  backup is a new file, so without a rule they accumulate). Two application keys: **write-only** for the
  server (`B2_KEY_ID`/`B2_APPLICATION_KEY` in `/etc/sniper/env`), **read
  only** for restores (kept on your Mac).
- On the Mac: `age-keygen -o ~/.config/sniper-staging.age.key`; keep a
  copy in your password manager. Put only the `age1…` public line in the
  server's `AGE_RECIPIENT`. The server can encrypt but never decrypt.

Restore drill (monthly; proven): on the Mac with the read-only B2 key
exported, a local PostgreSQL 16, and `brew install awscli zstd`:
`bash deploy/restore.sh` → restores the newest backup into a throwaway
database, runs migrations, prints row counts, drops it.

Full server loss:
1. New VPS → steps 1–4 above (same hostname; update DNS if the IP changed).
2. Put your saved `/etc/sniper/env` values back (keep a copy in your
   password manager). Keep the `DATABASE_URL` line install.sh generated.
3. `bash deploy/sync.sh --apply root@sniper-staging` (creates the schema).
4. From the Mac, fetch and decrypt the newest backup, then load it:
   ```
   aws --endpoint-url "$B2_ENDPOINT" s3 ls "s3://$B2_BUCKET/"       # pick the newest
   aws --endpoint-url "$B2_ENDPOINT" s3 cp "s3://$B2_BUCKET/<name>.age" .
   age -d -i ~/.config/sniper-staging.age.key <name>.age | zstd -d > dump.bin
   scp dump.bin root@sniper-staging:/tmp/ && rm dump.bin
   ```
   On the server:
   ```
   systemctl stop sniper-web sniper-worker sniper-scheduler
   sudo -u postgres pg_restore --clean --if-exists --no-owner --role=sniper -d sniper /tmp/dump.bin
   shred -u /tmp/dump.bin
   systemctl start sniper-migrate sniper-web sniper-worker sniper-scheduler
   ```

Never run a restore against the live database to "test" it.
