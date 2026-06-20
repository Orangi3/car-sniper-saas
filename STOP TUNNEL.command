#!/bin/bash
# STOP TUNNEL.command
# Cleanly kills every tunnel process this project might have started:
#   - cloudflared quick tunnel  (CLOUDFLARE TUNNEL.command)
#   - ssh -R to localhost.run  (TEMPORARY LINK.command)
#   - ngrok http 8765           (SHARE EXTERNALLY.command)
# Archives the recorded URL to tunnel_history.log so you can see when each
# URL stopped being valid. Does NOT touch the backend on port 8765, the
# scanner daemon, or any data file.

cd "$(dirname "$0")" || exit 1
clear

echo "=========================================================="
echo "  SNIPER — STOP TUNNEL"
echo "=========================================================="
echo

# 1. Snapshot what's currently running
RUNNING="$(ps -eo pid,etime,command 2>/dev/null \
    | grep -E "cloudflared.*8765|ssh.*localhost\.run|ngrok http 8765" \
    | grep -v grep)"

if [ -z "$RUNNING" ]; then
    echo "  (no tunnel processes are running.)"
else
    echo "Tunnel processes about to be terminated:"
    echo "$RUNNING" | sed 's/^/  /'
fi
echo

# 2. Archive the current URL (so we have a paper trail of which URL died when)
if [ -s ngrok-url.txt ]; then
    URL="$(cat ngrok-url.txt)"
    echo "$(date -u +%FT%TZ)  STOPPED  $URL" >> tunnel_history.log
    echo "Archived current URL to tunnel_history.log:"
    echo "  $URL"
fi
echo

# 3. Kill the processes
pkill -f "cloudflared.*8765"       2>/dev/null && echo "  killed cloudflared"
pkill -f "cloudflared.*localhost:8765"  2>/dev/null
pkill -f "cloudflared.*127.0.0.1:8765"  2>/dev/null
pkill -f "ssh.*localhost\.run"     2>/dev/null && echo "  killed ssh tunnel"
pkill -f "ngrok http 8765"         2>/dev/null && echo "  killed ngrok"
sleep 1
# Be polite first, then escalate if anything survived
pkill -9 -f "cloudflared.*8765"    2>/dev/null
pkill -9 -f "ssh.*localhost\.run"  2>/dev/null
pkill -9 -f "ngrok http 8765"      2>/dev/null

# 4. Confirm
STILL="$(ps -eo pid,etime,command 2>/dev/null \
    | grep -E "cloudflared.*8765|ssh.*localhost\.run|ngrok http 8765" \
    | grep -v grep)"
echo
if [ -z "$STILL" ]; then
    echo "  ✓ all tunnel processes are stopped."
else
    echo "  ⚠ these tunnel processes still appear running:"
    echo "$STILL" | sed 's/^/    /'
fi

# 5. Optionally clear ngrok-url.txt (ask first — it's just a one-key reminder)
echo
read -p "Clear ngrok-url.txt now? (the URL is already archived) [y/N] " CLR
if [ "$CLR" = "y" ] || [ "$CLR" = "Y" ]; then
    : > ngrok-url.txt
    echo "  ngrok-url.txt cleared."
else
    echo "  ngrok-url.txt left as-is."
fi

echo
echo "=========================================================="
echo "  Done. The local backend on port 8765 was NOT touched."
echo "  To start a new tunnel: CLOUDFLARE TUNNEL.command"
echo "=========================================================="
