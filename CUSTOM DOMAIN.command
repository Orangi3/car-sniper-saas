#!/bin/bash
# One-click setup for sniper.elitecarflipping.com via Cloudflare Tunnel.
# First run: installs cloudflared, walks through Cloudflare login + tunnel + DNS.
# Subsequent runs: just starts the tunnel.
#
# PREREQUISITES (one-time, only you can do these):
#   1. elitecarflipping.com must be in your Cloudflare account
#      (sign up at cloudflare.com → Add Site → follow steps to point your
#      registrar's nameservers at Cloudflare's. Free plan is fine.)
#   2. You must have a Cloudflare account with that domain visible.
# That's it — this script handles everything else.

set -e
cd "$(dirname "$0")" || exit 1
clear

cat <<'BANNER'
==========================================================
   SNIPER — CUSTOM DOMAIN via CLOUDFLARE TUNNEL
   Target: https://sniper.elitecarflipping.com
==========================================================

BANNER

# 1. Confirm the dashboard is shareable — either a password is set, OR the
#    owner has turned on public (no-login) mode.
ACCESS_OK=$(python3 -c "
import json, os
o = json.load(open('overrides.json')) if os.path.exists('overrides.json') else {}
pw  = (o.get('share_password') or '').strip()
pub = bool(o.get('share_public'))
print('yes' if (pw or pub) else 'no')
" 2>/dev/null || echo "no")
if [ "$ACCESS_OK" != "yes" ]; then
    echo "⚠ Set a share password OR enable public mode in the dashboard first"
    echo "  (External Access card), then run me again."
    read -p "Enter to close." _; exit 1
fi

# 2. Local sniper running?
if ! curl -fs http://127.0.0.1:8765/api/stats >/dev/null 2>&1; then
    echo "⚠ Sniper isn't running. Double-click START HERE.command first."
    read -p "Enter to close." _; exit 1
fi

# 3. cloudflared installed?
if ! command -v cloudflared >/dev/null 2>&1; then
    echo "Installing cloudflared via Homebrew (one-time, ~30 sec)..."
    if ! command -v brew >/dev/null 2>&1; then
        echo "Homebrew is required. Install from https://brew.sh"
        echo "(paste their one-line installer into Terminal, then run me again)"
        read -p "Enter to close." _; exit 1
    fi
    brew install cloudflared
fi

# 4. Logged into Cloudflare?
CF_DIR="$HOME/.cloudflared"
if [ ! -f "$CF_DIR/cert.pem" ]; then
    echo ""
    echo "Logging you into Cloudflare — this opens a browser tab."
    echo "Select 'elitecarflipping.com' when asked."
    echo ""
    cloudflared tunnel login
    if [ ! -f "$CF_DIR/cert.pem" ]; then
        echo "⚠ Cloudflare login didn't complete. Try again."
        read -p "Enter to close." _; exit 1
    fi
fi

# 5. Tunnel exists?
TUNNEL_NAME="sniper"
TUNNEL_ID=$(cloudflared tunnel list --output json 2>/dev/null \
            | python3 -c "import sys,json;print(next((t['id'] for t in json.load(sys.stdin) if t['name']=='$TUNNEL_NAME'),''))" \
            2>/dev/null || true)

if [ -z "$TUNNEL_ID" ]; then
    echo ""
    echo "Creating tunnel '$TUNNEL_NAME'..."
    cloudflared tunnel create "$TUNNEL_NAME"
    TUNNEL_ID=$(cloudflared tunnel list --output json \
                | python3 -c "import sys,json;print(next(t['id'] for t in json.load(sys.stdin) if t['name']=='$TUNNEL_NAME')))")
fi
echo "Tunnel ID: $TUNNEL_ID"

# 6. DNS route — sniper.elitecarflipping.com → this tunnel
echo ""
echo "Pointing sniper.elitecarflipping.com at the tunnel..."
cloudflared tunnel route dns "$TUNNEL_NAME" sniper.elitecarflipping.com 2>&1 \
    | grep -v "already exists" || true

# 7. Write config
mkdir -p "$CF_DIR"
cat > "$CF_DIR/sniper-config.yml" <<EOF
tunnel: $TUNNEL_ID
credentials-file: $CF_DIR/$TUNNEL_ID.json
ingress:
  - hostname: sniper.elitecarflipping.com
    service: http://localhost:8765
  - service: http_status:404
EOF
echo "Wrote $CF_DIR/sniper-config.yml"

# 8. Persist URL for the dashboard's External Access card
echo "https://sniper.elitecarflipping.com" > ngrok-url.txt

ACCESS_NOTE=$(python3 -c "
import json, os
o = json.load(open('overrides.json')) if os.path.exists('overrides.json') else {}
if o.get('share_public'):
    print('  NO login required — anyone with the link can open it.')
else:
    print('  Login:  username '+(o.get('share_username','sniper'))+'  /  your External Access password')
")

cat <<DONE

==========================================================
  ⟁ LIVE URL:  https://sniper.elitecarflipping.com
==========================================================

$ACCESS_NOTE

  Tunnel is starting now — DNS propagation can take 1-5 min the first time.
  After that, opening sniper.elitecarflipping.com loads instantly.

  Keep this Terminal window OPEN — closing it kills the tunnel.

DONE

# 9. Run tunnel — blocks until terminal closes
exec cloudflared tunnel --config "$CF_DIR/sniper-config.yml" run "$TUNNEL_NAME"
