#!/bin/bash
# LOCAL VERIFY.command
# Audits every API endpoint the dashboard calls, against the LOCAL server
# at http://127.0.0.1:8765. Output → local_verify.log so Claude can read it.
# No tunnel, no public URL, no auth — internal correctness only.

cd "$(dirname "$0")" || exit 1
LOG="local_verify.log"
exec > >(tee "$LOG") 2>&1
set +e
clear

B="http://127.0.0.1:8765"

hr() { echo; echo "----- $* -----"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$1"; }
body() { curl -s --max-time 8 "$1"; }
post()  { curl -s -X POST -H 'Content-Type: application/json' \
              -d "${2:-{}}" --max-time 30 "$1"; }

echo "=========================================================="
echo "  SNIPER — LOCAL DASHBOARD AUDIT  ($(date -u))"
echo "  Target: $B"
echo "=========================================================="

# 1. Server health
hr "1) /health"
echo "code = $(code $B/health)"
body "$B/health"; echo

# 2. Dashboard HTML — verify it served and contains the patched markers
hr "2) GET / (HEAD)"
curl -sI --max-time 8 "$B/" | head -10
hr "2b) HTML follow-through — patch markers count"
HTML="$(curl -sL --max-time 8 "$B/")"
echo "bytes:                  ${#HTML}"
echo "SNIPER_BUILD marker:    $(echo "$HTML" | grep -c 'SNIPER_BUILD')"
echo "bootBeacon marker:      $(echo "$HTML" | grep -c 'bootBeacon')"
echo "unhandledrejection:     $(echo "$HTML" | grep -c 'unhandledrejection')"
echo "hero-ticker id:         $(echo "$HTML" | grep -c 'id=\"hero-ticker\"')"
echo "hero-scanned id:        $(echo "$HTML" | grep -c 'id=\"hero-scanned\"')"
echo "hero-deals id:          $(echo "$HTML" | grep -c 'id=\"hero-deals\"')"

# 3. Stats endpoint — what hero tiles get
hr "3) /api/stats"
S="$(body $B/api/stats)"
echo "$S" | python3 -m json.tool 2>/dev/null | head -25

# 4. Sources endpoint
hr "4) /api/sources"
body "$B/api/sources" | python3 -c "import sys,json
d=json.load(sys.stdin)
for x in d:
    print(f\"  {x['id']:13} enabled={x['enabled']} weight={x['weight']}\")"

# 5. Diagnostics endpoint
hr "5) /api/diagnostics"
body "$B/api/diagnostics" | python3 -c "import sys,json
d=json.load(sys.stdin)
for x in d:
    print(f\"  {x['id']:13} enabled={x['enabled']} count={x.get('count')} elapsed_s={x.get('elapsed_s')} err={x.get('error')}\")"

# 6. Alerts endpoint (the 'verified deals' table — empty until comps match exactly)
hr "6) /api/alerts (count)"
body "$B/api/alerts?cash_only=1&hide_auction=1&limit=200" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  rows: {len(d)}')"

# 7. Near-misses endpoint
hr "7) /api/near_misses (count) — feeds the deals tab"
body "$B/api/near_misses?cash_only=1&hide_auction=1&limit=200" \
  | python3 -c "import sys,json
d=json.load(sys.stdin)
print(f'  rows: {len(d)}')
with_pos = [r for r in d if (r.get('discount_pct') or 0) > 0]
print(f'  with positive discount: {len(with_pos)}')
pool = sum(int(r.get('est_profit') or 0) for r in with_pos if (r.get('est_profit') or 0) > 0)
print(f'  profit pool (sum of est_profit > 0): \${pool:,}')"

# 8. Fresh listings (fallback)
hr "8) /api/fresh_listings (count)"
body "$B/api/fresh_listings?limit=30" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  rows: {len(d)}')"

# 9. Listings (the All-Listings tab)
hr "9) /api/listings (count, filtered like dashboard does)"
body "$B/api/listings?limit=300&cash_only=1&hide_auction=1&hide_salvage=1&max_age_min=1440&sort=newest" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  rows: {len(d)}')"
hr "9b) /api/listings unfiltered"
body "$B/api/listings?limit=10000" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  rows: {len(d)}')"

# 10. Closing soon
hr "10) /api/listings/closing_soon"
body "$B/api/listings/closing_soon?hrs=24" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  rows: {len(d)}')"

# 11. Saved
hr "11) /api/saved"
body "$B/api/saved" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  rows: {len(d)}')"

# 12. Settings / share_settings / notify_settings — keys only
hr "12) /api/settings (keys)"
body "$B/api/settings" | python3 -c "import sys,json
d=json.load(sys.stdin)
for k,v in d.items(): print(f'  {k}: {type(v).__name__}')"

hr "12b) /api/share_settings"
body "$B/api/share_settings"; echo

hr "12c) /api/notify_settings"
body "$B/api/notify_settings"; echo

# 13. Facets (filter dropdowns)
hr "13) /api/facets (sizes)"
body "$B/api/facets" | python3 -c "import sys,json
d=json.load(sys.stdin)
for k,v in d.items():
    print(f'  {k}: {len(v) if isinstance(v,list) else v}')"

# 14. /debug/status (owner-only, should pass on loopback)
hr "14) /debug/status"
body "$B/debug/status" | python3 -m json.tool 2>/dev/null | head -40

# 15. POST /api/poll-all — trigger a real poll and capture rich result
hr "15) POST /api/poll-all (this may take 5-30s)"
R="$(post $B/api/poll-all)"
echo "$R" | python3 -m json.tool 2>/dev/null | head -60

# 16. Post-poll re-read of stats + diagnostics
hr "16) post-poll /api/stats"
body "$B/api/stats" | python3 -m json.tool 2>/dev/null | head -20
hr "16b) post-poll /api/diagnostics"
body "$B/api/diagnostics" | python3 -c "import sys,json
d=json.load(sys.stdin)
for x in d:
    print(f\"  {x['id']:13} enabled={x['enabled']} count={x.get('count')} elapsed_s={x.get('elapsed_s')} err={x.get('error')}\")"

# 17. Recent backend errors
hr "17) recent UNCAUGHT errors in sniper-requests.log (last 50)"
grep -E "UNCAUGHT|ERROR" sniper-requests.log 2>/dev/null | tail -50

echo
echo "=========================================================="
echo "  AUDIT COMPLETE — output written to local_verify.log"
echo "=========================================================="
