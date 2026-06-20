#!/bin/bash
# TUNNEL STATUS.command
# Read-only diagnostic: what tunnel is supposed to be up right now,
# is it actually reachable from this Mac, and what's running.
# Does NOT change anything — safe to run any time.

cd "$(dirname "$0")" || exit 1
LOG="tunnel_status.log"
exec > >(tee "$LOG") 2>&1
set +e
clear

echo "=========================================================="
echo "  SNIPER — TUNNEL STATUS  ($(date -u))"
echo "=========================================================="

# 1. What URL does ngrok-url.txt claim is active?
echo
echo "----- 1) recorded public URL -----"
if [ ! -s ngrok-url.txt ]; then
    echo "  (none — ngrok-url.txt is empty or missing)"
    URL=""
else
    URL="$(cat ngrok-url.txt)"
    echo "  URL: $URL"
    MTIME="$(stat -f '%Sm' -t '%Y-%m-%d %H:%M:%S %Z' ngrok-url.txt 2>/dev/null \
             || stat -c '%y' ngrok-url.txt 2>/dev/null)"
    echo "  written: $MTIME"
fi

# 2. Local backend health
echo
echo "----- 2) local /health (must respond before any tunnel can work) -----"
LOCAL_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8765/health)
LOCAL_BODY=$(curl -s --max-time 3 http://127.0.0.1:8765/health)
echo "  HTTP $LOCAL_CODE   $LOCAL_BODY"

# 3. Tunnel /health (proves the public URL actually reaches the backend)
echo
echo "----- 3) tunneled /health (public URL -> backend) -----"
if [ -z "$URL" ]; then
    echo "  (skipped — no URL recorded)"
else
    T0=$(python3 -c 'import time; print(time.time())')
    REMOTE_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL/health")
    T1=$(python3 -c 'import time; print(time.time())')
    DT=$(python3 -c "print(f'{(${T1}-${T0})*1000:.0f}')")
    REMOTE_BODY=$(curl -s --max-time 10 "$URL/health" | head -c 120)
    echo "  HTTP $REMOTE_CODE  (${DT}ms)"
    echo "  body: $REMOTE_BODY"
fi

# 4. Tunnel processes
echo
echo "----- 4) running tunnel processes -----"
PS_OUT="$(ps -eo pid,etime,command 2>/dev/null \
    | grep -E "cloudflared.*8765|ssh.*localhost\.run|ngrok http 8765" \
    | grep -v grep)"
if [ -z "$PS_OUT" ]; then
    echo "  (none — no tunnel is actually running on this Mac)"
else
    echo "$PS_OUT" | sed 's/^/  /'
fi

# 5. DNS resolution (only useful for trycloudflare/lhr URLs)
echo
echo "----- 5) DNS for the recorded URL -----"
if [ -z "$URL" ]; then
    echo "  (skipped)"
else
    HOST="${URL#https://}"; HOST="${HOST%%/*}"
    dig +short "$HOST" 2>/dev/null | head -3 | sed 's/^/  /'
fi

# 6. One-line summary
echo
echo "=========================================================="
if [ -z "$URL" ]; then
    echo "  ⟁ STATUS: NO TUNNEL CONFIGURED"
    echo "     - ngrok-url.txt is empty."
    echo "     - To start one: double-click CLOUDFLARE TUNNEL.command"
elif [ "$LOCAL_CODE" != "200" ]; then
    echo "  ⟁ STATUS: APP NOT RUNNING LOCALLY"
    echo "     - The backend at 127.0.0.1:8765 isn't responding."
    echo "     - Start it: double-click START HERE.command"
elif [ -z "$PS_OUT" ] && [ "$REMOTE_CODE" != "200" ]; then
    echo "  ⟁ STATUS: TUNNEL DEAD (no process, no response)"
    echo "     - The recorded URL is stale."
    echo "     - Restart: double-click CLOUDFLARE TUNNEL.command"
elif [ "$REMOTE_CODE" = "200" ]; then
    echo "  ⟁ STATUS: TUNNEL LIVE"
    echo "     - $URL → backend via the tunnel ✓"
else
    echo "  ⟁ STATUS: TUNNEL DEGRADED"
    echo "     - process running but URL returned HTTP $REMOTE_CODE"
    echo "     - Restart: double-click CLOUDFLARE TUNNEL.command"
fi
echo "=========================================================="
echo
echo "Output written to tunnel_status.log"
