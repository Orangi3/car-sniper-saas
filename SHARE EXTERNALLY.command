#!/bin/bash
# Opens a public tunnel to your sniper so external people can reach it.
# Uses ngrok — free tier gives you a random https URL like abc123.ngrok-free.app.
#
# Before running:
#   1. Sign up at https://dashboard.ngrok.com/signup (free)
#   2. Grab your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken
#   3. First run will prompt you to paste it.
#   4. Set a share_password in the dashboard's External Access card FIRST,
#      otherwise non-loopback requests are refused.

set -e
cd "$(dirname "$0")" || exit 1
clear

cat <<'BANNER'
==========================================================
   SNIPER — EXTERNAL SHARING via NGROK TUNNEL
==========================================================
This will open a PUBLIC URL that points to your sniper.
Anyone with the URL + your share_password can use it.

BANNER

# 1. Ensure ngrok is installed
if ! command -v ngrok >/dev/null 2>&1; then
    echo "ngrok not installed. Installing via Homebrew…"
    if ! command -v brew >/dev/null 2>&1; then
        echo "Homebrew is required. Install from https://brew.sh first."
        echo "Or install ngrok manually from https://ngrok.com/download"
        read -p "Press return to exit." _
        exit 1
    fi
    brew install ngrok/ngrok/ngrok
fi

# 2. Verify auth setup — never tunnel an unprotected dashboard
PWD_SET=$(python3 -c "
import json
try:
    with open('overrides.json') as f: o = json.load(f)
    print('yes' if (o.get('share_password') or '').strip() else 'no')
except Exception:
    print('no')
" 2>/dev/null || echo "no")

if [ "$PWD_SET" != "yes" ]; then
    cat <<'WARNING'

⚠  BLOCKED — no share_password set.

For your safety, this tunnel WILL NOT open until you set a password.
The sniper has your phone number, location, saved deals, etc — exposing
it without auth is a bad idea.

Set one now:
   1. Open the dashboard
   2. Scroll to the ⟁ External Access card
   3. Type a username + password
   4. Hit Save
   5. Run this script again

WARNING
    read -p "Press return to close." _
    exit 1
fi

# 3. Verify the sniper is running locally first
if ! curl -fs http://127.0.0.1:8765/api/stats >/dev/null 2>&1; then
    echo "⚠ Sniper is not running. Run START HERE.command first, then this."
    read -p "Press return to close." _
    exit 1
fi

# 4. Check authtoken
if ! ngrok config check >/dev/null 2>&1; then
    echo ""
    echo "ngrok needs your authtoken (free, get from"
    echo "https://dashboard.ngrok.com/get-started/your-authtoken )"
    read -p "Paste authtoken: " TOKEN
    ngrok config add-authtoken "$TOKEN"
fi

echo ""
echo "Opening tunnel — your public URL will appear below in a few seconds."
echo "Keep this Terminal window OPEN — closing it kills the tunnel."
echo ""

# 5. Start the tunnel in the background, capture URL
pkill -f "ngrok http 8765" 2>/dev/null || true
sleep 1
ngrok http 8765 --log=stdout --log-format=logfmt > ngrok.log 2>&1 &
NGROK_PID=$!
echo $NGROK_PID > ngrok.pid

# 6. Poll for the URL
for i in $(seq 1 20); do
    URL=$(curl -fs http://127.0.0.1:4040/api/tunnels 2>/dev/null \
          | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'])" 2>/dev/null || true)
    if [ -n "$URL" ]; then break; fi
    sleep 1
done

if [ -z "$URL" ]; then
    echo "⚠ Tunnel didn't come up. Check ngrok.log for details."
    tail -20 ngrok.log
    read -p "Press return to close." _
    exit 1
fi

echo "$URL" > ngrok-url.txt

cat <<DONE

==========================================================
  ⟁ PUBLIC URL:  $URL
==========================================================

  Share this with anyone — they will be asked for the
  username + password you set in the dashboard.

  The tunnel runs as long as this Terminal window stays open.

DONE

# Wait on ngrok so the Terminal stays open
wait $NGROK_PID
