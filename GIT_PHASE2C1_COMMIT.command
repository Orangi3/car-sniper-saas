#!/bin/bash
# GIT_PHASE2C1_COMMIT.command — single commit for the Phase 2C.1 hardening.
set -e
cd "$(dirname "$0")" || exit 1
clear
LOG="git_phase2c1.log"
exec > >(tee "$LOG") 2>&1

echo "=========================================================="
echo "  Phase 2C.1 — isolate scheduler from web workers"
echo "=========================================================="

[ -f .git/index.lock ] && rm -f .git/index.lock || true
git config user.name  "Ty Phelps"
git config user.email "ronsmith13131313@gmail.com"

echo
echo "----- status -----"
git status --short

echo
echo "----- stage Phase 2C.1 files -----"
git add migrations/0006_scheduler_lock.sql
git add migrations/0006_scheduler_lock.postgres.sql
git add joblock.py
git add jobs/scheduler.py
git add billing.py
git add server.py
git add dashboard.html
git add DEPLOY.md
git add "START HERE.command"
git add STOP.command
git add tests/conftest.py
git add tests/test_phase1.py
git add tests/test_scheduler_isolation.py
git add GIT_PHASE2C1_COMMIT.command

echo
echo "----- staged -----"
git diff --cached --name-only | sort

echo
echo "----- forbidden-path scan -----"
for pat in '\.env$' '\.env\.local$' '^overrides\.json$' \
           '\.db$' '\.db-shm$' '\.db-wal$' '\.sqlite' \
           '\.log$' '^\.venv/' '__pycache__' '^cloudflared$' ; do
    HIT=$(git diff --cached --name-only | grep -E "$pat" || true)
    if [ -n "$HIT" ]; then echo "⚠ forbidden file staged: $HIT"; exit 2; fi
done
echo "▸ clean"

echo
echo "----- secret-value scan -----"
ADDED=$(git diff --cached -U0 | grep '^+' | grep -v '^+++')
HITS=$(
{
    echo "$ADDED" | grep -E 'sk_live_[A-Za-z0-9]{20,}|sk_test_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|pk_live_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}' || true
    echo "$ADDED" | grep -E -- '-----BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----' || true
} | grep -vE '\.example|\.gitignore|GIT_.*\.command|STRIPE_TESTING\.md|DEPLOY\.md|PHASE2_PLAN\.md' || true
)
if [ -n "$HITS" ]; then echo "⚠ secret:"; echo "$HITS" | head -20; exit 3; fi
echo "▸ no secret values in staged content"

echo
echo "----- diff --check -----"
git diff --cached --check && echo "▸ clean"

echo
echo "----- full test suite must be green -----"
source .venv/bin/activate
python3 -m pytest tests/ -q --no-header 2>&1 | tee /tmp/_phase2c1_pytest.log
TEST_RC=${PIPESTATUS[0]}
RESULT=$(grep -E '^[0-9]+ passed' /tmp/_phase2c1_pytest.log | tail -1)
echo "▸ $RESULT (exit $TEST_RC)"
if [ $TEST_RC -ne 0 ]; then echo "▸ Aborting — tests not green."; exit $TEST_RC; fi
if ! echo "$RESULT" | grep -qE '^121 passed'; then
    echo "▸ Aborting — expected '121 passed' but got '$RESULT'."; exit 4
fi

echo
echo "----- commit -----"
git commit -m "fix: isolate scheduler from web workers"

echo
echo "▸ commit hash : $(git rev-parse HEAD)"
echo "▸ short hash  : $(git rev-parse --short HEAD)"
git show --stat --pretty="" HEAD | tail -18
echo
git log --oneline --decorate -5
echo
git status --short
read -p "Press return to close." _
