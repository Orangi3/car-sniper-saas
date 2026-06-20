#!/bin/bash
# ROTATE LOGS.command
# Rotates the noisy log files in-place without restarting the running
# server/sniper daemon, using the copy-truncate pattern.
#
# Why copy-truncate instead of rename?
# Python's logging.FileHandler holds the file descriptor open by inode.
# If we rename sniper-requests.log to sniper-requests.log.old, the server
# keeps writing to the OLD inode and the renamed file keeps growing. With
# copy-truncate, we copy the bytes off, then truncate the live file to 0
# while preserving its inode. The server's fd survives, future writes
# land in the now-empty file. Tiny race window (microseconds) — acceptable.
#
# Output: each rotated log becomes <name>.<TS>.gz next to the live file.
# A subdirectory `log_archive/` is created and the gzipped copies move
# there so the project root stays clean.

cd "$(dirname "$0")" || exit 1
TS="$(date +%Y%m%d_%H%M%S)"
ARCHIVE="log_archive"
mkdir -p "$ARCHIVE"

LOGS=(
    "sniper-requests.log"
    "server.log"
    "sniper.log"
    "tunnel.log"
    "cloudflared.log"
    "verify.log"
    "local_verify.log"
    "full_verify.log"
    "reopen.log"
)

echo "=========================================================="
echo "  SNIPER — LOG ROTATION (copy-truncate, no server restart)"
echo "  archive: $ARCHIVE/"
echo "  ts:      $TS"
echo "=========================================================="
echo

TOTAL_SAVED=0
for L in "${LOGS[@]}"; do
    if [ ! -s "$L" ]; then
        printf "  %-26s  (empty, skipped)\n" "$L"
        continue
    fi
    SIZE_BEFORE=$(wc -c < "$L")
    OUT="$ARCHIVE/${L%.log}.${TS}.log.gz"
    if cp "$L" /tmp/.rotate_tmp_$$ 2>/dev/null && \
       gzip -c /tmp/.rotate_tmp_$$ > "$OUT" 2>/dev/null && \
       : > "$L"; then
        rm -f /tmp/.rotate_tmp_$$
        SIZE_AFTER=$(wc -c < "$OUT")
        TOTAL_SAVED=$((TOTAL_SAVED + SIZE_BEFORE))
        printf "  %-26s  %8s -> %8s gz\n" "$L" "$(numfmt --to=iec $SIZE_BEFORE 2>/dev/null || echo $SIZE_BEFORE)" \
               "$(numfmt --to=iec $SIZE_AFTER 2>/dev/null || echo $SIZE_AFTER)"
    else
        rm -f /tmp/.rotate_tmp_$$
        printf "  %-26s  FAILED (left untouched)\n" "$L"
    fi
done

# Trim archive: keep the most recent 12 archived copies of each base name.
shopt -s nullglob
declare -A SEEN
for f in $(ls -t "$ARCHIVE"/*.log.gz 2>/dev/null); do
    base="${f##*/}"
    base="${base%%.[0-9]*}"
    SEEN[$base]=$(( ${SEEN[$base]:-0} + 1 ))
    if [ "${SEEN[$base]}" -gt 12 ]; then
        rm -f "$f" && echo "  trimmed old archive: $f"
    fi
done

echo
echo "=========================================================="
echo "  ✓ Rotation complete."
echo "  Saved off about $(numfmt --to=iec $TOTAL_SAVED 2>/dev/null || echo $TOTAL_SAVED) bytes of log data."
echo "  Server / sniper daemon were NOT restarted."
echo "=========================================================="
