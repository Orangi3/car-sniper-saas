#!/bin/bash
# GIT_PHASE2B_COMMIT.command
# Phase-2B checkpoint: commit the subscription lifecycle hardening work
# AS-IS, before any further correction. This preserves the completed
# Phase 2B work in its own commit.
set -e
cd "$(dirname "$0")" || exit 1
clear
LOG="git_phase2b.log"
exec > >(tee "$LOG") 2>&1

echo "=========================================================="
echo "  Phase 2B checkpoint"
echo "=========================================================="

[ -f .git/index.lock ] && { echo "▸ removing stale .git/index.lock"; rm -f .git/index.lock; }

git config user.name  "Ty Phelps"
git config user.email "ronsmith13131313@gmail.com"

echo
echo "----- status -----"
git status --short

echo
echo "----- staging Phase 2B files -----"
git add migrations/0004_billing_hardening.sql
git add migrations/0004_billing_hardening.postgres.sql
git add billing.py
git add server.py
git add tests/conftest.py
git add tests/test_billing_lifecycle.py
git add GIT_PHASE2B_COMMIT.command

echo
echo "----- staged files -----"
git diff --cached --name-only | sort

echo
echo "----- unstaged remainder (should be empty) -----"
git status --short | grep -v '^A ' | grep -v '^M ' || echo "(none)"

echo
echo "----- diff --check -----"
git diff --cached --check && echo "▸ clean"

echo
echo "----- forbidden-path scan -----"
for pat in '\.env$' '\.env\.local$' '^overrides\.json$' \
           '\.db$' '\.db-shm$' '\.db-wal$' '\.sqlite' \
           '\.log$' '^\.venv/' '__pycache__' '^cloudflared$' ; do
    HIT=$(git diff --cached --name-only | grep -E "$pat" || true)
    if [ -n "$HIT" ]; then
        echo "⚠ forbidden file staged: $HIT"; exit 2
    fi
done
echo "▸ clean"

echo
echo "----- secret-value scan -----"
ADDED=$(git diff --cached -U0 | grep '^+' | grep -v '^+++')
SECRET_HITS=$(
{
    echo "$ADDED" | grep -E 'sk_live_[A-Za-z0-9]{20,}|sk_test_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}' || true
    echo "$ADDED" | grep -E -- '-----BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----' || true
} | grep -vE '\.example|\.gitignore|GIT_PHASE.*\.command|STRIPE_TESTING\.md' || true
)
if [ -n "$SECRET_HITS" ]; then
    echo "⚠ possible secret value in staged content"; echo "$SECRET_HITS" | head -20; exit 2
fi
echo "▸ clean"

echo
echo "----- run the full suite -----"
source .venv/bin/activate
python3 -m pytest tests/ -q --no-header 2>&1 | tee /tmp/_phase2b_pytest.log
TEST_RC=${PIPESTATUS[0]}
RESULT=$(grep -E '^[0-9]+ passed' /tmp/_phase2b_pytest.log | tail -1)
echo "▸ $RESULT (exit $TEST_RC)"
if [ $TEST_RC -ne 0 ]; then echo "▸ Aborting — tests not green."; exit $TEST_RC; fi
if ! echo "$RESULT" | grep -qE '^83 passed'; then
    echo "▸ Aborting — expected '83 passed' but got '$RESULT'."; exit 3
fi

echo
echo "----- commit -----"
git commit -m "feat: harden subscription lifecycle (portal, ordering, refunds, disputes, reconciliation)"

echo
echo "▸ commit hash : $(git rev-parse HEAD)"
echo "▸ short hash  : $(git rev-parse --short HEAD)"
echo
git show --stat --pretty="" HEAD | tail -25
echo
echo "▸ git log:"
git log --oneline -5
echo
echo "▸ git status --short (should be empty):"
git status --short
read -p "Press return to close." _
