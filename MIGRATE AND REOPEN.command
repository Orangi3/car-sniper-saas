#!/bin/bash
# MIGRATE AND REOPEN.command
# Apply pending migrations against the local listings.db then restart the
# server. Idempotent; safe to run repeatedly. Used after pulling new
# billing-core / phase 2 code.
set -e
cd "$(dirname "$0")" || exit 1
clear
echo "=========================================================="
echo "  Apply migrations + restart server"
echo "=========================================================="
source .venv/bin/activate
python3 -m migrations.runner
echo
echo "▸ migrations done — restarting server"
exec bash "./REOPEN WITH FIXES.command"
