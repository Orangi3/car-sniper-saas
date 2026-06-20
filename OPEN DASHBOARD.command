#!/bin/bash
# OPEN DASHBOARD.command
# Just opens the local dashboard in Chrome with a cache-bust query.
# Does NOT touch the running server. If the backend isn't up, prints
# how to start it and exits cleanly.

cd "$(dirname "$0")" || exit 1
clear

CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8765/health 2>/dev/null)
if [ "$CODE" != "200" ]; then
    echo "⚠ Backend is not responding at http://127.0.0.1:8765/health (HTTP $CODE)."
    echo "   Start it first: double-click START HERE.command"
    echo ""
    read -p "Press return to close." _
    exit 1
fi

TS=$(date +%s)
URL="http://127.0.0.1:8765/?v=$TS"
echo "Opening Chrome at: $URL"
open -a "Google Chrome" "$URL" 2>/dev/null || open "$URL"
echo ""
echo "✓ Done. This Terminal can be closed."
