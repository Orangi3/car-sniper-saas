#!/usr/bin/env bash
# deploy/install.sh — bootstrap a fresh Ubuntu 24.04 LTS host for
# Car Sniper staging. Idempotent. Run as a sudo-capable user.
#
# Usage:
#   curl ... | bash      # NO. Don't curl-pipe-shell secrets-adjacent stuff.
#
# Instead, on the new VPS:
#   ssh sniper@<vps-ip>           # via Tailscale, see STAGING_RUNBOOK.md
#   git clone <local-repo-bundle> /tmp/sniper-bootstrap
#   sudo bash /tmp/sniper-bootstrap/deploy/install.sh
#
# What it does (idempotent):
#   1. apt update + upgrade + base tools
#   2. Tailscale install (you tailscale-up after)
#   3. Postgres 16 install, create sniper DB + role with random password
#   4. Python 3.12 venv at /opt/sniper/.venv
#   5. Caddy install, Caddyfile with placeholder hostname
#   6. /opt/sniper/{releases,current,shared,logs} tree
#   7. sniper user (system account)
#   8. /etc/sniper/env (root:sniper 0600) seeded from env.staging.example
#   9. systemd units installed; web + migrate + reconcile.timer enabled;
#      scheduler + healthcheck.timer + backup.timer NOT enabled by default
#  10. ufw: allow 80, 443, plus Tailscale SSH; deny everything else
#
# What it does NOT do:
#   * disable root SSH or password auth — you do that AFTER verifying a
#     second SSH session works via Tailscale
#   * fill in any Stripe / B2 / Healthchecks secret — those go directly
#     into /etc/sniper/env via the runbook
#   * provision DNS — you add the A record to your registrar

set -euo pipefail
trap 'echo "▸ install.sh aborted at line $LINENO (rc=$?)"; exit 1' ERR

if [[ $EUID -ne 0 ]]; then
    echo "install.sh must run as root (sudo)."
    exit 1
fi

if ! command -v lsb_release >/dev/null 2>&1 || \
   [[ "$(lsb_release -si)" != "Ubuntu" ]]; then
    echo "install.sh targets Ubuntu LTS only."
    exit 1
fi

# ----------------------------------------------------------------------
# 0. Prompt for the staging hostname (e.g. staging.car-sniper.com).
#    Hostnames are non-secret. We need it for the Caddyfile.
# ----------------------------------------------------------------------
if [[ -z "${STAGING_HOSTNAME:-}" ]]; then
    read -r -p "staging hostname (e.g. staging.example.com): " STAGING_HOSTNAME
fi
if [[ -z "$STAGING_HOSTNAME" ]]; then
    echo "STAGING_HOSTNAME required."
    exit 1
fi
echo "▸ Using staging hostname: $STAGING_HOSTNAME"

# ----------------------------------------------------------------------
# 1. apt base
# ----------------------------------------------------------------------
echo "▸ apt update + base tooling"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get -y upgrade
apt-get -y install \
    curl wget gnupg ca-certificates lsb-release software-properties-common \
    rsync ufw fail2ban age jq sqlite3 git \
    build-essential libpq-dev pkg-config \
    python3.12 python3.12-venv python3.12-dev \
    postgresql-16 postgresql-contrib-16

# ----------------------------------------------------------------------
# 2. Tailscale
# ----------------------------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
    echo "▸ Installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
fi
echo "▸ NOTE: after this script finishes, run:"
echo "    sudo tailscale up --ssh --hostname=sniper-staging"
echo "  to add this host to your tailnet."

# ----------------------------------------------------------------------
# 3. Postgres: sniper DB + sniper role
# ----------------------------------------------------------------------
systemctl enable --now postgresql
PG_PASS=$(openssl rand -hex 24)
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='sniper'" \
     | grep -q 1 || \
    sudo -u postgres psql -c \
        "CREATE ROLE sniper LOGIN PASSWORD '${PG_PASS}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='sniper'" \
     | grep -q 1 || \
    sudo -u postgres psql -c \
        "CREATE DATABASE sniper OWNER sniper TEMPLATE template0 ENCODING 'UTF8';"
sudo -u postgres psql -d sniper -c \
    "GRANT ALL ON SCHEMA public TO sniper;" >/dev/null

# Tighten Postgres: localhost only.
PG_CONF="/etc/postgresql/16/main/postgresql.conf"
if ! grep -q "^listen_addresses = 'localhost'" "$PG_CONF"; then
    sed -i "s/^#\?listen_addresses.*/listen_addresses = 'localhost'/" "$PG_CONF"
    systemctl restart postgresql
fi

# ----------------------------------------------------------------------
# 4. sniper system user + /opt/sniper tree
# ----------------------------------------------------------------------
id -u sniper >/dev/null 2>&1 || \
    useradd --system --create-home --home /opt/sniper --shell /usr/sbin/nologin sniper

install -d -o sniper -g sniper -m 0755 /opt/sniper/releases
install -d -o sniper -g sniper -m 0755 /opt/sniper/shared
install -d -o sniper -g sniper -m 0755 /var/log/sniper
install -d -o sniper -g sniper -m 0700 /var/backups/sniper

# ----------------------------------------------------------------------
# 5. Python venv (only created once; sync.sh `pip install -r` per deploy)
# ----------------------------------------------------------------------
if [[ ! -x /opt/sniper/.venv/bin/python3 ]]; then
    sudo -u sniper python3.12 -m venv /opt/sniper/.venv
fi
sudo -u sniper /opt/sniper/.venv/bin/python3 -m pip install --upgrade pip wheel
# psycopg2-binary is required for the Postgres deploy; the rest are
# (re)installed per release via sync.sh.
sudo -u sniper /opt/sniper/.venv/bin/python3 -m pip install \
    "psycopg2-binary>=2.9"

# ----------------------------------------------------------------------
# 6. /etc/sniper/env (root:sniper 0600). Seeded from template only if
#    missing. Operator fills in real values via the runbook.
# ----------------------------------------------------------------------
install -d -o root -g sniper -m 0750 /etc/sniper
if [[ ! -f /etc/sniper/env ]]; then
    HERE="$(cd "$(dirname "$0")" && pwd)"
    install -o root -g sniper -m 0600 "$HERE/env.staging.example" /etc/sniper/env
    # Pre-fill the DATABASE_URL with the freshly-generated PG password.
    sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://sniper:${PG_PASS}@127.0.0.1:5432/sniper|" /etc/sniper/env
    # Pre-fill BILLING_ORIGIN with the hostname.
    sed -i "s|^BILLING_ORIGIN=.*|BILLING_ORIGIN=https://${STAGING_HOSTNAME}|" /etc/sniper/env
    echo "▸ /etc/sniper/env created. Edit it (vi /etc/sniper/env) to add"
    echo "  STRIPE_*, B2_*, HEALTHCHECK_* — never via chat, never via git."
else
    echo "▸ /etc/sniper/env already exists, leaving as-is."
fi

# ----------------------------------------------------------------------
# 7. Caddy
# ----------------------------------------------------------------------
if ! command -v caddy >/dev/null 2>&1; then
    echo "▸ Installing Caddy"
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/caddy.gpg
    echo "deb [signed-by=/usr/share/keyrings/caddy.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
        > /etc/apt/sources.list.d/caddy.list
    apt-get update -q && apt-get -y install caddy
fi
install -d -o caddy -g caddy -m 0755 /var/log/caddy
HERE="$(cd "$(dirname "$0")" && pwd)"
sed "s|__STAGING_HOSTNAME__|${STAGING_HOSTNAME}|g" \
    "$HERE/Caddyfile.staging" > /etc/caddy/Caddyfile
chown root:caddy /etc/caddy/Caddyfile
chmod 0640 /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy || true

# ----------------------------------------------------------------------
# 8. systemd units
# ----------------------------------------------------------------------
echo "▸ Installing systemd units"
install -m 0644 "$HERE/systemd/sniper-migrate.service"     /etc/systemd/system/
install -m 0644 "$HERE/systemd/sniper-web.service"         /etc/systemd/system/
install -m 0644 "$HERE/systemd/sniper-scheduler.service"   /etc/systemd/system/
install -m 0644 "$HERE/systemd/sniper-reconcile.service"   /etc/systemd/system/
install -m 0644 "$HERE/systemd/sniper-reconcile.timer"     /etc/systemd/system/
install -m 0644 "$HERE/systemd/sniper-backup.service"      /etc/systemd/system/
install -m 0644 "$HERE/systemd/sniper-backup.timer"        /etc/systemd/system/
install -m 0644 "$HERE/systemd/sniper-healthcheck.service" /etc/systemd/system/
install -m 0644 "$HERE/systemd/sniper-healthcheck.timer"   /etc/systemd/system/
systemctl daemon-reload

# Enabled by default: migrate, web, reconcile.timer
# NOT enabled by default: scheduler (Phase 2C.1 rule), backup.timer
# (waits until B2 creds are in /etc/sniper/env), healthcheck.timer
# (waits until HEALTHCHECK_WEB_URL is set).
systemctl enable sniper-migrate.service
systemctl enable sniper-web.service
systemctl enable sniper-reconcile.timer

# ----------------------------------------------------------------------
# 9. ufw firewall
#    Allow only 80/443 publicly. SSH gated by Tailscale (we don't open
#    port 22 to the public internet).
# ----------------------------------------------------------------------
echo "▸ Configuring ufw"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 80/tcp
ufw allow 443/tcp
# Tailscale SSH listens on its own tailnet IP (100.64.0.0/10) — not on
# public 0.0.0.0. The `tailscale up --ssh` command activates that path.
# ufw doesn't need a rule for tailscale0; it sees only the public iface.
ufw --force enable

# ----------------------------------------------------------------------
# 10. fail2ban (defense-in-depth even though SSH is tailnet-only)
# ----------------------------------------------------------------------
systemctl enable --now fail2ban

echo
echo "=========================================================="
echo "  ✓ install.sh finished"
echo "=========================================================="
echo "  Next:"
echo "    1. sudo tailscale up --ssh --hostname=sniper-staging"
echo "       (the URL it prints goes to your Tailscale admin console)"
echo "    2. Verify a SECOND SSH session works:"
echo "       ssh sniper@sniper-staging.<your-tailnet>.ts.net"
echo "    3. On your Mac, run:"
echo "       bash deploy/sync.sh --apply sniper@sniper-staging"
echo "  At that point web/migrate will start (no Stripe yet — that's fine)."
echo "=========================================================="
