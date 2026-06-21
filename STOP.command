#!/bin/bash
# Stop the Car Sniper. Double-click this file when you're done.
cd "$(dirname "$0")" || exit 1

# Kill by saved PIDs
[ -f sniper.pid ] && kill "$(cat sniper.pid)" 2>/dev/null && rm sniper.pid
[ -f server.pid ] && kill "$(cat server.pid)" 2>/dev/null && rm server.pid

# Belt-and-suspenders: kill anything still matching
pkill -f "sniper.py daemon" 2>/dev/null   # legacy entry, retired
pkill -f "jobs.scheduler"   2>/dev/null
pkill -f "server.py"        2>/dev/null

osascript -e 'display notification "Stopped." with title "Car Sniper" sound name "Pop"'

clear
echo ""
echo "===================================="
echo "  Car Sniper stopped."
echo "===================================="
echo ""
echo "  You can close this window."
echo ""
sleep 2
