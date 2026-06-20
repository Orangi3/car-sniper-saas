#!/bin/bash
# BACKUP BASELINE.command
# Saves a complete, restorable copy of the sniper project at the
# INTERNALLY VERIFIED WORKING checkpoint.
#
# Output: ~/Desktop/sniper_INTERNAL_VERIFIED_WORKING_<timestamp>.zip
#
# Excludes runtime-only files (logs, PID files, sqlite -shm/-wal sidecars,
# the cloudflared binary which can be re-downloaded, __pycache__, venv).
# Includes: all .py / .html / .json / .command / .md / requirements.txt
# and the listings.db file (so the deal/comp state at the verified moment
# can be restored exactly).

set -u
cd "$(dirname "$0")" || exit 1
TS="$(date +%Y%m%d_%H%M%S)"
OUT="$HOME/Desktop/sniper_INTERNAL_VERIFIED_WORKING_$TS.zip"

echo "=========================================================="
echo "  SNIPER — BASELINE BACKUP"
echo "  out: $OUT"
echo "=========================================================="

zip -r "$OUT" . \
  -x ".git/*" \
  -x ".venv/*" \
  -x "venv/*" \
  -x "__pycache__/*" \
  -x "*.pyc" \
  -x ".DS_Store" \
  -x "cloudflared" \
  -x "cloudflared.log" \
  -x "cloudflared.tgz" \
  -x "tunnel.log" \
  -x "server.log" \
  -x "sniper.log" \
  -x "sniper-requests.log" \
  -x "verify.log" \
  -x "local_verify.log" \
  -x "full_verify.log" \
  -x "reopen.log" \
  -x "*.pid" \
  -x "listings.db-shm" \
  -x "listings.db-wal" \
  -x "comps_cache.sqlite-shm" \
  -x "comps_cache.sqlite-wal"

echo
echo "=========================================================="
echo "  ✓ Backup written: $OUT"
echo "  size: $(du -h "$OUT" | awk '{print $1}')"
echo "=========================================================="
echo
echo "To restore:"
echo "  1. Unzip into a new folder (e.g. ~/Desktop/sniper_restore)"
echo "  2. cd into it and run ./START\\ HERE.command"
echo "  3. ./FULL\\ VERIFY.command should print INTERNALLY VERIFIED WORKING"
echo
open -R "$OUT" 2>/dev/null
