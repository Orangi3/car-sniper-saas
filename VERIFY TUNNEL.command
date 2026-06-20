#!/bin/bash
# VERIFY TUNNEL.command
# Runs all the user-requested verification steps from the Mac itself
# (Claude's sandbox can't reach trycloudflare.com — its egress proxy
# allowlists out *.trycloudflare.com, same as it did for *.lhr.life).
#
# Output is teed to verify.log so Claude can read it back.

cd "$(dirname "$0")" || exit 1
LOG="verify.log"
exec > >(tee "$LOG") 2>&1
set +e
clear

echo "=========================================================="
echo "  SNIPER — TUNNEL VERIFICATION ($(date -u))"
echo "=========================================================="

echo
echo "----- 1) is cloudflared still running? -----"
ps -eo pid,etime,command | grep -E "cloudflared.*8765" | grep -v grep || echo "(no cloudflared --url …8765 process)"

echo
echo "----- 2) URL recorded in ngrok-url.txt -----"
URL="$(cat ngrok-url.txt 2>/dev/null || echo "")"
echo "URL = $URL"

echo
echo "----- 3) URL printed in the most recent cloudflared.log -----"
LOG_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' cloudflared.log 2>/dev/null | tail -1)
echo "URL from log = $LOG_URL"

if [ -n "$LOG_URL" ] && [ "$LOG_URL" != "$URL" ]; then
    echo "(log URL differs from ngrok-url.txt — using log URL)"
    URL="$LOG_URL"
    echo "$URL" > ngrok-url.txt
fi

if [ -z "$URL" ]; then
    echo "No URL anywhere — will restart fresh in step 6."
fi

verify_url() {
    local u="$1"
    local host="${u#https://}"
    host="${host%%/*}"
    echo
    echo "----- DNS: nslookup $host -----"
    nslookup "$host" | head -10
    echo
    echo "----- DNS: dig +short $host -----"
    dig +short "$host" | head -5
    echo
    echo "----- curl -I $u -----"
    curl -I --max-time 12 "$u" | head -10
    echo
    echo "----- curl -s $u/health -----"
    curl -s --max-time 12 "$u/health"
    echo
    echo
    echo "----- curl -s $u/api/stats | head -c 400 -----"
    curl -s --max-time 12 "$u/api/stats" | head -c 400
    echo
    echo
    echo "----- curl -s $u/api/diagnostics (parsed) -----"
    curl -s --max-time 12 "$u/api/diagnostics" \
      | python3 -c "import sys, json
try:
    d = json.load(sys.stdin)
    for x in d:
        print(f\"  {x['id']:13} enabled={x['enabled']} count={x.get('count')} elapsed_s={x.get('elapsed_s')} err={x.get('error')}\")
except Exception as e:
    print('  (could not parse:', e, ')')"
}

PASS=0
if [ -n "$URL" ]; then
    echo
    echo "----- 4) verifying CURRENT url: $URL -----"
    verify_url "$URL"
    # Pass if we got a 200 on /health AND the body contains "status":"ok"
    BODY="$(curl -s --max-time 10 "$URL/health" 2>/dev/null)"
    CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL/health" 2>/dev/null)"
    echo
    echo "  /health HTTP code: $CODE"
    echo "  /health body:      $BODY"
    if [ "$CODE" = "200" ] && echo "$BODY" | grep -q '"status":"ok"'; then
        PASS=1
    fi
fi

if [ "$PASS" != "1" ]; then
    echo
    echo "----- 5) URL not verified — restarting cloudflared fresh -----"
    # Kill any old cloudflared and wait briefly
    pkill -f "cloudflared.*8765" 2>/dev/null
    sleep 2

    CFD=""
    if command -v cloudflared >/dev/null 2>&1; then
        CFD="$(command -v cloudflared)"
    elif [ -x "./cloudflared" ]; then
        CFD="$(pwd)/cloudflared"
    else
        echo "cloudflared not found — re-run CLOUDFLARE TUNNEL.command first."
        exit 1
    fi
    echo "  using: $CFD ($($CFD --version 2>&1 | head -1))"

    rm -f cloudflared.log
    : > cloudflared.log
    # Start cloudflared in the background, log to file
    nohup "$CFD" tunnel --no-autoupdate --url http://127.0.0.1:8765 \
         >> cloudflared.log 2>&1 &
    NPID=$!
    echo "  started cloudflared pid=$NPID"

    # Poll the log for the new URL (up to ~30s)
    NEW_URL=""
    for i in $(seq 1 30); do
        sleep 1
        NEW_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' cloudflared.log 2>/dev/null | tail -1)
        if [ -n "$NEW_URL" ]; then break; fi
    done

    if [ -z "$NEW_URL" ]; then
        echo "  no URL appeared in cloudflared.log within 30s. Last log lines:"
        tail -10 cloudflared.log | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g'
        exit 1
    fi
    echo "  NEW URL: $NEW_URL"
    echo "$NEW_URL" > ngrok-url.txt

    # Give Cloudflare a beat to propagate, then verify
    sleep 10
    verify_url "$NEW_URL"
    BODY="$(curl -s --max-time 10 "$NEW_URL/health" 2>/dev/null)"
    CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$NEW_URL/health" 2>/dev/null)"
    echo
    echo "  /health HTTP code: $CODE"
    echo "  /health body:      $BODY"
    if [ "$CODE" = "200" ] && echo "$BODY" | grep -q '"status":"ok"'; then
        PASS=1
        URL="$NEW_URL"
    fi
fi

echo
echo "=========================================================="
if [ "$PASS" = "1" ]; then
    echo "  ⟁ FINAL URL: $URL"
    echo "  ⟁ STATUS:   VERIFIED WORKING through Cloudflare tunnel"
    echo "=========================================================="
    # open in Chrome so the user (and Claude via screenshot) can see render
    open -a "Google Chrome" "$URL"  2>/dev/null || open "$URL"
else
    echo "  ⟁ STATUS:   NOT WORKING (see above for codes/bodies)"
    echo "=========================================================="
fi

echo
echo "Done. Output written to verify.log"
echo "(This Terminal can be closed; cloudflared keeps running in the background.)"
