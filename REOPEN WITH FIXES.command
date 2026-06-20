#!/bin/bash
# REOPEN WITH FIXES.command
# Restarts the local Flask backend (so the server.py patches take effect)
# and opens the local dashboard in Chrome with a cache-bust query.
# dashboard.html is served from disk every request so no rebuild is needed.

cd "$(dirname "$0")" || exit 1
LOG="reopen.log"
exec > >(tee "$LOG") 2>&1
set +e
clear

echo "=========================================================="
echo "  SNIPER — RESTART + REOPEN  ($(date -u))"
echo "=========================================================="

# 1. Kill the old server (the patches don't take effect without restart)
echo
echo "----- 1) stopping current server.py -----"
if [ -f server.pid ]; then
    OLD=$(cat server.pid)
    if kill -0 "$OLD" 2>/dev/null; then
        kill "$OLD" 2>/dev/null
        echo "  sent TERM to pid $OLD"
    fi
fi
pkill -f "python.*server.py" 2>/dev/null
sleep 1
pkill -9 -f "python.*server.py" 2>/dev/null
sleep 1

# 2. Start the new one
echo
echo "----- 2) starting patched server.py -----"
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="$(command -v python3 || command -v python)"
fi
echo "  python: $PY"

nohup "$PY" server.py > server.log 2>&1 &
NEW=$!
echo $NEW > server.pid
echo "  new server pid: $NEW"

# 3. Wait for /health to respond
echo
echo "----- 3) waiting for /health -----"
for i in $(seq 1 20); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8765/health)
    if [ "$code" = "200" ]; then
        echo "  /health OK after ${i}s"
        break
    fi
    sleep 1
done
if [ "$code" != "200" ]; then
    echo "  ⚠ /health did not respond. Last server.log lines:"
    tail -20 server.log
    exit 1
fi

# 4. Probe the patched endpoints
echo
echo "----- 4) /api/stats (looking for new 'deals' field) -----"
curl -s --max-time 5 http://127.0.0.1:8765/api/stats \
  | python3 -m json.tool | head -20

echo
echo "----- 5) POST /api/poll-all (deals_found should match deals above) -----"
curl -s -X POST --max-time 30 http://127.0.0.1:8765/api/poll-all \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(f\"  deals_found={r['deals_found']}  profit_pool=\${r['profit_pool']:,}  listings_scanned={r['listings_scanned']}\")"

echo
echo "----- 6) /api/stats AGAIN (post-poll, deals should be stable) -----"
curl -s --max-time 5 http://127.0.0.1:8765/api/stats \
  | python3 -c "import sys,json; s=json.load(sys.stdin); print(f\"  listings={s['listings']}  alerts={s['alerts']}  deals={s.get('deals')}\")"

# 7. Open the local dashboard in Chrome (cache-bust)
TS=$(date +%s)
URL="http://127.0.0.1:8765/?v=$TS"
echo
echo "----- 7) opening Chrome -----"
echo "  URL: $URL"
open -a "Google Chrome" "$URL" 2>/dev/null || open "$URL"

echo
echo "=========================================================="
echo "  RESTART COMPLETE"
echo "  - server pid: $NEW"
echo "  - opened:     $URL"
echo "  (this Terminal can be closed; the server keeps running.)"
echo "=========================================================="
