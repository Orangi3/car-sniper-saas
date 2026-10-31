#!/usr/bin/env bash
# deploy/golive.sh — turn a deployed server into the private pilot and prove
# it works. Run as root ON THE SERVER after `deploy/sync.sh --apply`:
#
#   sudo bash /opt/sniper/current/deploy/golive.sh --test-email you@example.com
#
# Nine gates, in order. Each one passes or stops with what to fix; fix it and
# run the script again (every step is idempotent). It never prints a value
# from /etc/sniper/env and never edits that file. What it changes, all on
# this server: it enables and restarts the app's services, turns billing
# reconciliation off (the free pilot has no Stripe), runs one backup and one
# health probe, and turns on the nightly backup and the 5-minute probe.
set -uo pipefail

ENV_FILE=/etc/sniper/env
APP=/opt/sniper/current
PY=/opt/sniper/.venv/bin/python3
TEST_EMAIL=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --test-email) [[ $# -ge 2 && -n $2 ]] || { echo "--test-email needs an address"; exit 2; }
                      TEST_EMAIL=$2; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown option: $1 (see --help)"; exit 2 ;;
    esac
done
[[ -n $TEST_EMAIL ]] || { echo "usage: sudo bash $0 --test-email YOUR_ADDRESS (email delivery is one of the gates)"; exit 2; }
[[ $EUID -eq 0 ]] || { echo "run as root: sudo bash $0"; exit 2; }

gate() { echo; echo "▸ $*"; }
pass() { echo "  ✓ $*"; }
fail() { echo "  ✗ $*"; echo; echo "STOPPED. Fix the item above, then run golive.sh again."; exit 1; }
# a setting's value as systemd reads it (last line wins); never echoed
val() { local v; v=$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -1); v=${v%[\"\']}; v=${v#[\"\']}; printf '%s' "$v"; }
# keys and secret-bearing URLs only over HTTPS (a loopback stand-in in tests excepted)
secure_url() { case "$1" in https://*|http://127.0.0.1|http://127.0.0.1[:/]*|http://localhost|http://localhost[:/]*|http://\[::1\]|http://\[::1\][:/]*) return 0 ;; *) return 1 ;; esac; }
# does a Healthchecks URL accept a ping? (the URL reaches curl on stdin, not argv)
ping_ok() { local u; u=$(val "$1"); curl -fsS -m 10 -o /dev/null -K - <<<"url = \"$u\"" 2>/dev/null; }
# `ufw status` rules other than 80 and 443; rules bound to tailscale0 are
# reachable only over the tailnet, so they don't count
extra_rules() { sed -n '/^--/,$p' | tail -n +2 | grep -v ' on tailscale0' | awk 'NF {print $1}' \
    | grep -vxE '(80|443)(/tcp)?' | sort -u | tr '\n' ' '; }
app() {     # python as the app user, with the app's environment
    systemd-run --pipe --wait --quiet --collect --uid=sniper -p EnvironmentFile="$ENV_FILE" \
        -p WorkingDirectory="$APP" "$PY" "$@"
}

gate "1/9 settings in $ENV_FILE (names only; values are never printed)"
[[ -f $ENV_FILE ]] || fail "$ENV_FILE is missing: run deploy/install.sh first"
[[ "$(stat -c '%U:%G %a' "$ENV_FILE")" == "root:sniper 600" ]] \
    || fail "$ENV_FILE must be root:sniper, mode 600: chown root:sniper $ENV_FILE && chmod 600 $ENV_FILE"
missing=()
for k in DATABASE_URL BILLING_ORIGIN SNIPER_TRUSTED_PROXIES \
         SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM \
         B2_BUCKET B2_KEY_ID B2_APPLICATION_KEY B2_ENDPOINT AGE_RECIPIENT \
         HEALTHCHECK_WEB_URL HEALTHCHECK_PIPELINE_URL HEALTHCHECK_BACKUP_URL; do
    [[ -n "$(val "$k")" ]] || missing+=("$k")
done
(( ${#missing[@]} == 0 )) || fail "not set: ${missing[*]} (FIRST_DEPLOY.md: email, backups, monitoring)"
for k in B2_ENDPOINT HEALTHCHECK_WEB_URL HEALTHCHECK_PIPELINE_URL HEALTHCHECK_BACKUP_URL; do
    secure_url "$(val "$k")" || fail "$k must start with https:// (keys and ping URLs never travel in clear text)"
done
[[ "$(val ALLOW_REGISTRATION)" == 0 ]] || fail "ALLOW_REGISTRATION must be 0: the pilot is invitation-only"
[[ "$(val SNIPER_COMMERCIAL_MODE)" == 1 ]] || fail "SNIPER_COMMERCIAL_MODE must be 1: only licensed sources may run"
[[ "$(val SNIPER_SELLER_SUBMISSIONS)" == 1 ]] || fail "SNIPER_SELLER_SUBMISSIONS must be 1 for the seller pilot"
[[ -z "$(val STRIPE_SECRET_KEY)" ]] \
    || fail "STRIPE_SECRET_KEY is set, but the free pilot runs without billing: remove the STRIPE_* lines"
origin=$(val BILLING_ORIGIN); origin=${origin%/}
[[ $origin == https://* ]] || fail "BILLING_ORIGIN must be https://<your hostname>"
host=${origin#https://}; host=${host%%/*}
pass "everything set; invitation-only; licensed sources only; seller listings on; no billing"

gate "2/9 release"
[[ -L $APP && -f $APP/server.py ]] || fail "no release at $APP: run deploy/sync.sh --apply from your computer first"
pass "release $(basename "$(readlink -f "$APP")")"

gate "3/9 services (restarted so they read the current settings)"
systemctl enable -q sniper-web sniper-worker sniper-scheduler \
    && systemctl restart sniper-web sniper-worker sniper-scheduler \
    || fail "could not start the app: journalctl -u sniper-web -n 50"
up=""
for _ in $(seq 1 30); do
    curl -fsS -m 5 http://127.0.0.1:8765/health >/dev/null 2>&1 && { up=1; break; }; sleep 2
done
[[ -n $up ]] || fail "sniper-web doesn't answer on 127.0.0.1:8765: journalctl -u sniper-web -n 50"
for u in postgresql caddy sniper-web sniper-worker sniper-scheduler; do
    systemctl is-active -q "$u" || fail "$u is not running: journalctl -u $u -n 50"
    systemctl is-enabled -q "$u" || fail "$u won't start at boot: systemctl enable $u"
done
pass "PostgreSQL, Caddy, web, worker and scheduler running, and enabled at boot"

gate "4/9 billing reconciliation"
systemctl disable -q --now sniper-reconcile.timer 2>/dev/null || true
systemctl is-enabled -q sniper-reconcile.timer 2>/dev/null && fail "could not disable sniper-reconcile.timer"
pass "off (no Stripe; turn it back on only with billing: FIRST_DEPLOY.md, Billing later)"

gate "5/9 HTTPS at $origin"
ok=""
for _ in $(seq 1 30); do
    curl -fsS -m 10 "$origin/health" >/dev/null 2>&1 && { ok=1; break; }; sleep 2
done
[[ -n $ok ]] || fail "$origin/health doesn't answer with a valid certificate. Check that DNS for $host points at this server (dig +short $host), then journalctl -u caddy -n 50"
curl -fsSI -m 10 "$origin/login" | grep -qi '^strict-transport-security:' \
    || fail "$origin/login has no Strict-Transport-Security header (see deploy/Caddyfile.staging)"
code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://$host/login")
[[ $code == 308 || $code == 301 ]] || fail "http://$host/login answered $code; expected a redirect to HTTPS"
# An invalid email: if sign-up were open this gets a 400 and creates nothing.
body=$(curl -s -m 10 -H 'Content-Type: application/json' \
    -d '{"email":"golive-probe","password":"golive-probe"}' "$origin/api/auth/register")
grep -q "invitation only" <<<"$body" || fail "sign-up without an invitation isn't refused: check ALLOW_REGISTRATION=0"
pass "valid certificate, /health 200, HSTS, HTTP redirects to HTTPS, sign-up needs an invitation"

gate "6/9 encrypted off-server backup"
systemctl start sniper-backup.service \
    || fail "the backup failed: journalctl -u sniper-backup -n 30 (B2_* keys and bucket, AGE_RECIPIENT)"
systemctl enable -q --now sniper-backup.timer || fail "could not enable sniper-backup.timer"
pass "one backup uploaded; nightly from now on (04:23 UTC); the restore drill is next, on your computer"

gate "7/9 monitoring"
systemctl enable -q --now sniper-healthcheck.timer || fail "could not enable sniper-healthcheck.timer"
ok=""
for _ in 1 2 3 4; do      # the pipeline probe needs one scheduler tick after the restart
    systemctl start sniper-healthcheck.service 2>/dev/null && { ok=1; break; }; sleep 20
done
[[ -n $ok ]] || fail "the health probe failed: journalctl -u sniper-healthcheck -n 20"
for k in HEALTHCHECK_WEB_URL HEALTHCHECK_PIPELINE_URL HEALTHCHECK_BACKUP_URL; do
    ping_ok "$k" || fail "$k doesn't accept a ping: copy it again from Healthchecks.io"
done
pass "web and pipeline probes passed; all three Healthchecks URLs accept pings; probes every 5 minutes from now"

gate "8/9 firewall and admin access"
ufw status | grep -q '^Status: active' || fail "ufw is not active (deploy/install.sh step 9)"
extra=$(ufw status | extra_rules)
[[ -z $extra ]] || fail "ufw allows more than 80 and 443: $extra(SSH goes over Tailscale; remove with ufw delete)"
tailscale status >/dev/null 2>&1 || fail "Tailscale isn't connected: tailscale up --ssh --hostname=sniper-staging"
pass "only 80 and 443 open to the internet; Tailscale connected"

gate "9/9 readiness"
out=$(app -m preflight --target internal 2>&1) || { sed -n '/^READY FOR/,$p' <<<"$out"; fail "preflight found a problem (above)"; }
pass "$(grep -m1 '^READY FOR' <<<"$out")"
admins=$(app -c "import db
with db.session() as c:
    print(c.execute(\"SELECT COUNT(*) FROM users WHERE role = 'admin'\").fetchone()[0])" 2>&1)
[[ $admins =~ ^[0-9]+$ ]] || fail "could not count admin accounts: $(tail -1 <<<"$admins")"
(( admins >= 1 )) || fail "no admin account: create yours with STAGING_RUNBOOK.md §7, then run golive.sh again"
pass "an admin account exists"
app -c "import mailer, sys; mailer.send(sys.argv[1], 'FlipFirst test email', 'Sent by golive.sh. If you can read this, email works.')" "$TEST_EMAIL" \
    || fail "the test email wasn't accepted: check SMTP_* and that Resend shows the domain verified"
pass "test email accepted for $TEST_EMAIL: check that inbox, and spam"

echo
echo "=========================================================="
echo "  ✓ golive.sh: all gates passed"
echo "=========================================================="
echo "  Still yours to do:"
echo "    1. Restore drill on your computer: bash deploy/restore.sh (FIRST_DEPLOY.md)"
echo "    2. Then the first real test: FIRST_REAL_TEST.md"
echo "=========================================================="
