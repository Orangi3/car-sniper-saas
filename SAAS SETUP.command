#!/bin/bash
# SAAS SETUP.command
# One-shot setup for the Phase-1 SaaS rewrite:
#   1. Activate the venv
#   2. Install bcrypt + pytest
#   3. Run migrations (creates users/sessions/saved_v2/saved_searches/source_health)
#   4. Run the Phase-1 test suite
#   5. Prompt for an admin email + password and create the bootstrap admin user
#
# Idempotent — safe to re-run. Migrations skip already-applied IDs.

set -u
cd "$(dirname "$0")" || exit 1
clear

echo "=========================================================="
echo "  SNIPER — Phase 1 SaaS SETUP"
echo "=========================================================="

# 1. Activate venv (created if missing)
if [ ! -d ".venv" ]; then
    echo "▸ Creating .venv (first run)"
    python3 -m venv .venv || { echo "venv create failed"; read -p "Press return to close." _; exit 1; }
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "▸ Python: $(which python3) — $(python3 --version)"

# 2. Install deps
echo
echo "----- 2) Installing deps -----"
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "▸ Done."

# 3. Run migrations
echo
echo "----- 3) Running migrations -----"
python3 -m migrations.runner

# 4. Tests
echo
echo "----- 4) Running Phase-1 tests -----"
python3 -m pytest tests/test_phase1.py -v --tb=short
TEST_RC=$?
if [ $TEST_RC -ne 0 ]; then
    echo
    echo "⚠ Tests failed — Stop here, inspect, do NOT promote a user to admin"
    echo "  until the suite is green. Re-run this script after fixing."
    read -p "Press return to close." _
    exit $TEST_RC
fi
echo "▸ All tests passed."

# 5. Bootstrap admin
echo
echo "----- 5) Creating your admin account -----"
echo "(Skip with Ctrl-C if you've already created one.)"
read -p "  admin email: " ADMIN_EMAIL
read -s -p "  admin password (min 8 chars): " ADMIN_PW
echo
if [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_PW" ]; then
    python3 - "$ADMIN_EMAIL" "$ADMIN_PW" <<'PY'
import sys, types
# auth.py imports flask at top — install a stub before import so this
# bootstrap script never depends on a request context.
fake = types.ModuleType("flask")
class _G: pass
fake.g = _G(); fake.jsonify = lambda *a, **k: None
class _Req:
    headers = {}; remote_addr = ""; cookies = {}; is_secure = False
fake.request = _Req()
sys.modules["flask"] = fake

import auth
email, pw = sys.argv[1], sys.argv[2]
existing = auth.get_user_by_email(email)
if existing:
    auth.set_role(existing.id, "admin")
    auth.set_plan(existing.id, "pro")
    print(f"▸ Promoted existing user {email} to admin/pro")
else:
    u = auth.create_user(email, pw, role="admin", plan="pro")
    print(f"▸ Created admin user id={u.id}  email={u.email}  role={u.role}  plan={u.plan}")
PY
fi

echo
echo "=========================================================="
echo "  Setup complete."
echo "  Next: double-click REOPEN WITH FIXES.command to restart"
echo "  the server with the new code."
echo "=========================================================="
read -p "Press return to close." _
