#!/bin/bash
# BACKUP TUNNEL POLISH R1.command
# Writes a checkpoint of the project right after Tunnel/Launcher Polish R1
# was verified, with the same exclusions as the prior baseline backups.
#
# Output: ~/Desktop/sniper_INTERNAL_VERIFIED_WORKING_TUNNEL_POLISH_R1_<TS>.zip

set -u
cd "$(dirname "$0")" || exit 1
TS="$(date +%Y%m%d_%H%M%S)"
OUT="$HOME/Desktop/sniper_INTERNAL_VERIFIED_WORKING_TUNNEL_POLISH_R1_$TS.zip"

echo "=========================================================="
echo "  SNIPER — TUNNEL / LAUNCHER POLISH R1 BACKUP"
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
  -x "tunnel_status.log" \
  -x "log_archive/*" \
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
open -R "$OUT" 2>/dev/null
