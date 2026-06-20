#!/bin/bash
# RESTORE BASELINE.command
# Restores a previously-saved sniper backup zip into a NEW folder.
# Never overwrites the current ~/Desktop/sniper folder automatically.
#
# Safety model:
#   1. Lists backups so you can pick one.
#   2. Requires you to type the exact timestamp to choose.
#   3. Requires you to type "yes" to confirm.
#   4. Stops the running server first (only if you confirm again).
#   5. Snapshots the CURRENT folder as PRE_RESTORE_<TS>.zip first.
#   6. Extracts to ~/Desktop/sniper_restored_<TS>/ (a NEW folder).
#   7. Prints the manual swap instructions. Doesn't touch the live folder.

cd "$HOME/Desktop" || exit 1

cat <<'BANNER'
==========================================================
   SNIPER — RESTORE FROM LOCKED BASELINE BACKUP
==========================================================
This script will:
  - list your baseline backup zips
  - extract the one you choose into a NEW folder on the Desktop
  - NEVER overwrite your current ~/Desktop/sniper folder
You will still swap the folders yourself once you've verified
the restored copy looks right. That is intentional.
==========================================================

BANNER

# 1. Find candidate backups
mapfile -t ZIPS < <(ls -t sniper_INTERNAL_VERIFIED_WORKING_*.zip 2>/dev/null)
if [ "${#ZIPS[@]}" -eq 0 ]; then
    echo "⚠ No backups found on the Desktop."
    echo "   Run BACKUP BASELINE.command first."
    read -p "Press return to close." _
    exit 1
fi

echo "Available backups (newest first):"
echo
i=0
for z in "${ZIPS[@]}"; do
    SIZE="$(du -h "$z" | awk '{print $1}')"
    # Extract the timestamp suffix between the prefix and the .zip extension
    TS="${z#sniper_INTERNAL_VERIFIED_WORKING_}"
    TS="${TS%.zip}"
    printf "  [%2d]  %-40s  %s\n" "$i" "$TS" "$SIZE"
    i=$((i+1))
done
echo

# 2. Choose by exact timestamp typed back
read -p "Type the EXACT timestamp from the list (e.g. 20260529_163357), or anything else to abort: " CHOICE
if [ -z "$CHOICE" ]; then
    echo "Aborted."; exit 1
fi
PICK="sniper_INTERNAL_VERIFIED_WORKING_${CHOICE}.zip"
if [ ! -f "$PICK" ]; then
    echo "⚠ No backup matches that timestamp. Aborted."; exit 1
fi
echo "Selected: $PICK"
echo

# 3. Final confirmation
read -p "Type 'yes' (lowercase, no quotes) to proceed: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."; exit 1
fi
echo

# 4. Stop the running server only after a second confirmation
read -p "Stop the running server/sniper daemon now? [yes/N] " STOP_OK
if [ "$STOP_OK" = "yes" ]; then
    cd sniper 2>/dev/null && {
        for pidf in server.pid sniper.pid; do
            [ -f "$pidf" ] && kill "$(cat "$pidf")" 2>/dev/null && echo "  killed $pidf"
        done
        cd "$HOME/Desktop"
    }
else
    echo "  leaving the running server alone."
fi

# 5. Pre-restore snapshot of the CURRENT folder
TS_NOW="$(date +%Y%m%d_%H%M%S)"
PRE_ZIP="$HOME/Desktop/PRE_RESTORE_${TS_NOW}.zip"
echo "Making safety snapshot of current sniper folder before extracting..."
( cd sniper && zip -qr "$PRE_ZIP" . \
    -x ".git/*" -x ".venv/*" -x "venv/*" -x "__pycache__/*" -x "*.pyc" \
    -x ".DS_Store" -x "cloudflared" -x "cloudflared.log" \
    -x "cloudflared.tgz" -x "tunnel.log" -x "server.log" -x "sniper.log" \
    -x "sniper-requests.log" -x "verify.log" -x "local_verify.log" \
    -x "full_verify.log" -x "reopen.log" -x "*.pid" \
    -x "listings.db-shm" -x "listings.db-wal" \
    -x "comps_cache.sqlite-shm" -x "comps_cache.sqlite-wal" ) \
    && echo "  ✓ $PRE_ZIP ($(du -h "$PRE_ZIP" | awk '{print $1}'))" \
    || echo "  ⚠ snapshot failed — continuing anyway"
echo

# 6. Extract into a new dated folder
DEST="$HOME/Desktop/sniper_restored_${TS_NOW}"
echo "Extracting backup into NEW folder:"
echo "  $DEST"
mkdir -p "$DEST"
unzip -q "$PICK" -d "$DEST"
RESULT=$?
if [ "$RESULT" -ne 0 ]; then
    echo "⚠ unzip failed ($RESULT). The current ~/Desktop/sniper folder was NOT touched."
    read -p "Press return to close." _; exit 1
fi
echo "  ✓ Extracted."
echo

# 7. Final instructions
cat <<NEXT
==========================================================
  ⟁ RESTORE STAGED — no live folder was overwritten.
==========================================================

  Restored copy:   $DEST
  Safety snapshot: $PRE_ZIP

  To swap the restored copy in as your live project:

    cd ~/Desktop
    mv sniper sniper_OLD_${TS_NOW}
    mv "$(basename "$DEST")" sniper
    cd sniper
    open "START HERE.command"
    open "FULL VERIFY.command"

  If anything looks wrong before you swap:
    - inspect:    open "$DEST"
    - diff with current:   diff -r sniper "$DEST" | head -40

  If you swapped and want to revert to the pre-restore state:
    cd ~/Desktop
    rm -rf sniper_BAD
    mv sniper sniper_BAD
    mkdir sniper
    unzip "$PRE_ZIP" -d sniper

==========================================================
NEXT

open -R "$DEST" 2>/dev/null
