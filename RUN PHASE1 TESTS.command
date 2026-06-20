#!/bin/bash
# RUN PHASE1 TESTS.command
# Non-interactive: install deps if missing, then run only the Phase-1 suite.
# Does NOT prompt for admin creation; does NOT modify production data.

set -u
cd "$(dirname "$0")" || exit 1
clear

echo "=========================================================="
echo "  SNIPER — Phase 1 test suite"
echo "=========================================================="

if [ ! -d ".venv" ]; then
    python3 -m venv .venv || { echo "venv create failed"; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Install deps quietly if any required module is missing
python3 - <<'PY' >/dev/null 2>&1 || NEED_DEPS=1
import flask, bcrypt, pytest  # noqa
PY
if [ "${NEED_DEPS:-0}" = "1" ]; then
    echo "▸ Installing test deps..."
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
fi

# Run pytest and capture exit code. -q for compact summary.
echo
python3 -m pytest tests/ -v --tb=short --no-header 2>&1 | tee phase1_pytest.log
RC=${PIPESTATUS[0]}

echo
echo "=========================================================="
if [ $RC -eq 0 ]; then
    echo "  RESULT: all tests passed."
else
    echo "  RESULT: pytest exited with code $RC — see output above."
fi
echo "  log saved to $(pwd)/phase1_pytest.log"
echo "=========================================================="
read -p "Press return to close." _
exit $RC
