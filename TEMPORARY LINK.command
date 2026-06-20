#!/bin/bash
# Opens a free, no-install, no-account public tunnel to your sniper.
# Uses SSH to localhost.run — Mac has ssh built in, nothing to install.
# Keep this Terminal window open while sharing the URL.

cd "$(dirname "$0")" || exit 1
rm -f ngrok-url.txt tunnel.log

clear
echo "=========================================================="
echo "   SNIPER — TEMPORARY PUBLIC LINK"
echo "=========================================================="
echo ""

# 1. Make sure sniper is running locally
if ! curl -fs http://127.0.0.1:8765/api/stats >/dev/null 2>&1; then
    echo "Sniper is not running. Restarting it now…"
    pkill -f "sniper.py daemon" 2>/dev/null
    pkill -f "server.py" 2>/dev/null
    sleep 1
    if [ -d ".venv" ]; then
        nohup .venv/bin/python sniper.py daemon > sniper.log 2>&1 &
        echo $! > sniper.pid
        nohup .venv/bin/python server.py > server.log 2>&1 &
        echo $! > server.pid
        sleep 3
    else
        echo "No .venv found — run START HERE.command first."
        read -p "Press return to close." _
        exit 1
    fi
fi

# 2. Echo the credentials from overrides.json
python3 - <<'PY' 2>/dev/null
import json
try:
    o = json.load(open("overrides.json"))
    print(f"USERNAME : {o.get('share_username','sniper')}")
    print(f"PASSWORD : {o.get('share_password','(not set)')}")
except Exception as e:
    print(f"(could not read overrides.json: {e})")
PY

echo ""
echo "Opening SSH tunnel to localhost.run…"
echo "Public URL will appear below. Anyone with it sees the login prompt."
echo ""
echo "KEEP THIS WINDOW OPEN — closing it kills the link."
echo ""
echo "----------------------------------------------------------"

# 3. Open the tunnel. Print the URL as soon as localhost.run advertises it.
{
    ssh -o StrictHostKeyChecking=accept-new \
        -o ServerAliveInterval=60 \
        -o ExitOnForwardFailure=yes \
        -R 80:localhost:8765 \
        nokey@localhost.run 2>&1
} | tee tunnel.log | while IFS= read -r line; do
    echo "$line"
    URL=$(echo "$line" | grep -oE 'https://[a-z0-9-]+\.lhr(?:\.life|tunnel\.link)' | head -1)
    if [ -z "$URL" ]; then
        URL=$(echo "$line" | grep -oE 'https://[a-z0-9-]+\.lhr\.life' | head -1)
    fi
    if [ -n "$URL" ]; then
        echo "$URL" > ngrok-url.txt
        osascript -e "display notification \"$URL\" with title \"Sniper Public URL\"" 2>/dev/null
    fi
done
