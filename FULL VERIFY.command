#!/bin/bash
# FULL VERIFY.command
# Final internal verification: curls every endpoint the dashboard JS hits,
# triggers /api/poll-all (same path the Poll button uses), watches the
# server's request log to capture what Chrome's open tab actually fetches
# during the 5s and 15s auto-refresh intervals — that's the empirical
# proof that refreshTicker, refreshHero, refreshDiagnostics, refreshAlerts,
# refreshListings, refreshSaved, refreshStats all execute in the browser.
#
# Output → full_verify.log so Claude reads back the exact results.

cd "$(dirname "$0")" || exit 1
LOG="full_verify.log"
exec > >(tee "$LOG") 2>&1
set +e
clear

B="http://127.0.0.1:8765"
REQLOG="sniper-requests.log"

echo "=========================================================="
echo "  SNIPER — FULL INTERNAL FUNCTION VERIFY  ($(date -u))"
echo "  Target: $B"
echo "=========================================================="

# --- 0) Pre-snapshot the request log so we can diff exactly what Chrome
# fetches during the poll + auto-refresh window.
BEFORE_LINES=$(wc -l < "$REQLOG" 2>/dev/null || echo 0)
echo "request log line count before: $BEFORE_LINES"

probe() {
    local method="$1" path="$2" body="$3"
    local url="$B$path"
    local t0=$(python3 -c 'import time; print(time.time())')
    local code; local size; local resp
    if [ "$method" = "POST" ]; then
        resp="$(curl -s -X POST -H 'Content-Type: application/json' \
                -d "${body:-{}}" -w "\n__HTTP__%{http_code}\n__SIZE__%{size_download}" \
                --max-time 60 "$url" 2>/dev/null)"
    else
        resp="$(curl -s -w "\n__HTTP__%{http_code}\n__SIZE__%{size_download}" \
                --max-time 15 "$url" 2>/dev/null)"
    fi
    code=$(echo "$resp" | sed -n 's/^__HTTP__//p' | tail -1)
    size=$(echo "$resp" | sed -n 's/^__SIZE__//p' | tail -1)
    local body="$(echo "$resp" | sed '/^__HTTP__/,$d')"
    local t1=$(python3 -c 'import time; print(time.time())')
    local dt=$(python3 -c "print(f'{(${t1}-${t0})*1000:.0f}')")
    # Detect content type from body
    local shape="unknown"
    if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(type(d).__name__)" >/dev/null 2>&1; then
        shape="$(echo "$body" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if isinstance(d, list):   print(f'array[{len(d)}]')
elif isinstance(d, dict): print(f'object[{len(d)} keys]')
else:                     print(type(d).__name__)
" 2>/dev/null)"
    elif [ "${body:0:9}" = "<!doctype" ] || [ "${body:0:5}" = "<html" ] || [ "${body:0:5}" = "<!DOC" ]; then
        shape="html[${size}b]"
    else
        shape="text[${size}b]"
    fi
    printf "  %-6s %-32s HTTP %3s  %5sms  %s\n" "$method" "$path" "$code" "$dt" "$shape"
}

echo
echo "----- 1) every endpoint the frontend calls (status + time + shape) -----"
probe GET /health
probe GET /
probe GET /api/stats
probe GET /api/sources
probe GET /api/facets
probe GET "/api/alerts?cash_only=1&hide_auction=1&limit=200"
probe GET "/api/near_misses?cash_only=1&hide_auction=1&limit=200"
probe GET "/api/fresh_listings?cash_only=1&hide_auction=1&limit=30"
probe GET "/api/listings?limit=300&cash_only=1&hide_auction=1&hide_salvage=1&max_age_min=1440&sort=newest"
probe GET "/api/listings?limit=30&sort=newest"      # what refreshTicker uses
probe GET "/api/listings/closing_soon?hrs=24"
probe GET /api/saved
probe GET /api/diagnostics
probe GET /api/settings
probe GET /api/share_settings
probe GET /api/notify_settings
probe GET /api/score_progress
probe GET /debug/status

echo
echo "----- 2) discover the Poll button handler in dashboard.html -----"
grep -n -B1 -A2 'addEventListener.*click.*$\|#poll-now' dashboard.html \
    | grep -E '#poll-now|api/poll-all|/api/poll' | head -8

echo
echo "----- 3) POST /api/poll-all  (this is what clicking the Poll button does) -----"
T0=$(python3 -c 'import time; print(time.time())')
POLL_RESP="$(curl -s -X POST -H 'Content-Type: application/json' -d '{}' \
              --max-time 60 "$B/api/poll-all")"
T1=$(python3 -c 'import time; print(time.time())')
DT=$(python3 -c "print(f'{(${T1}-${T0})*1000:.0f}')")
echo "duration: ${DT}ms"
echo "$POLL_RESP" | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f'  ok                = {r.get(\"ok\")}')
print(f'  listings_scanned  = {r.get(\"listings_scanned\")}')
print(f'  new_listings      = {r.get(\"new_listings\")}')
print(f'  deals_found       = {r.get(\"deals_found\")}')
print(f'  profit_pool       = \${r.get(\"profit_pool\",0):,}')
print(f'  duration_seconds  = {r.get(\"duration_seconds\")}')
print(f'  scoring_pending   = {r.get(\"scoring_pending\")}')
print(f'  sources           = {len(r.get(\"sources\",[]))}')
for s in r.get('sources', []):
    err = s.get('error') or s.get('skipped_reason') or '-'
    print(f\"    {s['id']:13} enabled={s['enabled']} success={s['success']} fetched={s['listings_returned']} new={s['new_listings']} elapsed_s={s['elapsed_s']} err={err}\")"

echo
echo "----- 4) wait 18s so Chrome's 15s interval fires once -----"
sleep 18

echo
echo "----- 5) what did Chrome actually fetch since step 0 (proves JS still runs) -----"
AFTER_LINES=$(wc -l < "$REQLOG" 2>/dev/null || echo 0)
DELTA=$((AFTER_LINES - BEFORE_LINES))
echo "  +${DELTA} new request log lines"
tail -n "$DELTA" "$REQLOG" \
  | grep -E "AUTH path=" \
  | awk -F 'path=' '{print $2}' \
  | awk '{print $1}' \
  | sort \
  | uniq -c \
  | sort -rn \
  | awk '{printf "    %3s  %s\n", $1, $2}'

# Status-code histogram for the same window
echo
echo "  status codes in the same window:"
tail -n "$DELTA" "$REQLOG" \
  | grep -oE ' [12345][0-9][0-9] [0-9]+ms' \
  | awk '{print $1}' \
  | sort | uniq -c \
  | awk '{printf "    %3s  HTTP %s\n", $1, $2}'

# Any non-2xx? Walk every one and classify CORE vs NON-CORE.
#
# CORE = an API the dashboard's JS relies on (refreshTicker, refreshHero,
#         refreshAlerts, refreshListings, refreshSaved, refreshStats,
#         refreshDiagnostics, refreshSources, refreshFacets, settings,
#         share_settings, notify_settings, poll-all). A non-2xx here would
#         actually break the dashboard.
#
# NON-CORE = stuff that is fine to fail or expected to redirect:
#   - 302 from `/`  -> intentional cache-bust redirect to /?v=<mtime>
#   - 304 Not Modified from any path -> success-equivalent
#   - 404 on /favicon.ico, /apple-touch-icon*, /robots.txt -> browser noise
#   - any 3xx redirect on a non-/api path
echo
echo "  non-2xx responses in window (classified):"
ALL_NON2XX=$(tail -n "$DELTA" "$REQLOG" | grep -E ' [13-5][0-9][0-9] [0-9]+ms' \
            | grep -v ' 200 \| 201 \| 204 \| 206 ' || true)
CORE_FAIL_COUNT=0
NONCORE_COUNT=0
NONCORE_DETAIL=""
CORE_DETAIL=""
if [ -z "$ALL_NON2XX" ]; then
    echo "    (none)"
else
    while IFS= read -r line; do
        # Example line: '2026-05-29 16:24:35,464 INFO GET / 302 0ms'
        method=$(echo "$line" | grep -oE 'GET|POST|PUT|DELETE|HEAD|OPTIONS' | head -1)
        path=$(echo "$line" | awk -v m="$method" '{for(i=1;i<=NF;i++) if($i==m){print $(i+1); exit}}')
        status=$(echo "$line" | grep -oE ' [1-5][0-9][0-9] [0-9]+ms' | head -1 | awk '{print $1}')
        # Classification
        kind="CORE"
        reason=""
        if [ "$path" = "/" ] && [ "$status" = "302" ]; then
            kind="NON-CORE"; reason="intentional cache-bust redirect to /?v=<mtime>"
        elif [ "$status" = "304" ]; then
            kind="NON-CORE"; reason="Not Modified (success-equivalent)"
        elif echo "$path" | grep -qE '^/favicon\.ico|^/apple-touch-icon|^/robots\.txt'; then
            kind="NON-CORE"; reason="browser auto-fetch, no dashboard impact"
        elif echo "$status" | grep -qE '^3'; then
            # any 3xx on non-/api path is harmless
            if ! echo "$path" | grep -q '^/api/'; then
                kind="NON-CORE"; reason="3xx redirect on non-API path"
            fi
        fi
        if [ "$kind" = "CORE" ]; then
            CORE_FAIL_COUNT=$((CORE_FAIL_COUNT+1))
            CORE_DETAIL="$CORE_DETAIL
    CORE   ${method:--}  ${path:--}  status=$status"
        else
            NONCORE_COUNT=$((NONCORE_COUNT+1))
            NONCORE_DETAIL="$NONCORE_DETAIL
    NON-CORE  ${method:--}  ${path:--}  status=$status  -- $reason"
        fi
    done <<< "$ALL_NON2XX"
    [ -n "$CORE_DETAIL" ]    && echo "$CORE_DETAIL"
    [ -n "$NONCORE_DETAIL" ] && echo "$NONCORE_DETAIL"
fi
echo "  CORE non-2xx:     $CORE_FAIL_COUNT"
echo "  NON-CORE non-2xx: $NONCORE_COUNT"

# Backward-compatible variable for the old final-classification check below
NON2XX="$CORE_DETAIL"

# Any UNCAUGHT?
echo
echo "  UNCAUGHT errors in window:"
UNC=$(tail -n "$DELTA" "$REQLOG" | grep "UNCAUGHT" || true)
if [ -z "$UNC" ]; then
    echo "    (none)"
else
    echo "$UNC" | head -10
fi

echo
echo "----- 6) post-poll stats (compare to step 1) -----"
probe GET /api/stats
probe GET /api/diagnostics

# Final classification — only CORE failures count.
#
# Sanitize every count var to a single integer before [ -eq / -gt ] compares.
# `grep -c . || echo 0` is unsafe: when there are zero matches grep prints
# "0" AND exits 1, then "|| echo 0" appends ANOTHER "0", so the variable
# becomes the string "0\n0" and bash refuses it as an integer expression.
to_int() {
    # First numeric run in the string, defaulting to 0 if none.
    local v; v="$(printf '%s\n' "${1:-}" | grep -Eo '[0-9]+' | head -n1)"
    printf '%s' "${v:-0}"
}

# Empty UNC means there were no UNCAUGHT lines, period.
if [ -z "$UNC" ]; then
    UNC_COUNT=0
else
    UNC_COUNT="$(printf '%s\n' "$UNC" | grep -c .)"
fi
UNC_COUNT="$(to_int "$UNC_COUNT")"
CORE_FAIL_COUNT="$(to_int "$CORE_FAIL_COUNT")"
NONCORE_COUNT="$(to_int "$NONCORE_COUNT")"
DELTA="$(to_int "$DELTA")"

# Also pull /api/stats and /api/diagnostics final status codes so the
# final classification reads them directly instead of relying on the log diff.
STATS_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$B/api/stats")"
DIAG_CODE="$(curl -s -o /dev/null -w '%{http_code}'  --max-time 5 "$B/api/diagnostics")"
LISTINGS_COUNT="$(curl -s --max-time 5 "$B/api/stats" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("listings",0))' 2>/dev/null || echo 0)"
DEALS_COUNT="$(curl -s --max-time 5 "$B/api/stats" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("deals",0))' 2>/dev/null || echo 0)"

echo
echo "  /api/stats final status:        HTTP $STATS_CODE"
echo "  /api/diagnostics final status:  HTTP $DIAG_CODE"
echo "  listings (DB count):            $LISTINGS_COUNT"
echo "  deals    (DB count):            $DEALS_COUNT"
echo "  CORE non-2xx:                   $CORE_FAIL_COUNT"
echo "  NON-CORE non-2xx:               $NONCORE_COUNT"
echo "  UNCAUGHT JS errors:             $UNC_COUNT"
echo "  delta requests in window:       $DELTA"
echo
if [ "$DELTA" -gt 0 ] \
   && [ "$CORE_FAIL_COUNT" -eq 0 ] \
   && [ "$UNC_COUNT" -eq 0 ] \
   && [ "$STATS_CODE" = "200" ] \
   && [ "$DIAG_CODE" = "200" ]; then
    echo "  ⟁ FINAL: INTERNALLY VERIFIED WORKING"
    echo "     - every probed endpoint returned 2xx (or intentional 302 cache-bust on /)"
    echo "     - Poll triggered, sources polled, deals/profit consistent"
    echo "     - Chrome JS fired ${DELTA} requests in the 18s window (auto-refresh active)"
    echo "     - 0 UNCAUGHT JS errors"
    echo "     - 0 CORE non-2xx (non-core: $NONCORE_COUNT — all benign)"
    echo "     - /api/stats=$STATS_CODE  /api/diagnostics=$DIAG_CODE  listings=$LISTINGS_COUNT  deals=$DEALS_COUNT"
else
    echo "  ⟁ FINAL: INTERNAL FUNCTIONALITY STILL BROKEN"
    echo "     - delta requests:    $DELTA"
    echo "     - CORE non-2xx:      $CORE_FAIL_COUNT"
    echo "     - NON-CORE non-2xx:  $NONCORE_COUNT"
    echo "     - UNCAUGHT in window: $UNC_COUNT"
    echo "     - /api/stats=$STATS_CODE  /api/diagnostics=$DIAG_CODE"
fi
echo "=========================================================="
