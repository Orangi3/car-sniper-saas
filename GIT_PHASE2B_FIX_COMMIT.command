#!/bin/bash
# GIT_PHASE2B_FIX_COMMIT.command
# Production-hardening correction: move billing reconciliation from a
# public HTTP route to an internal CLI job. Separate commit from the
# Phase 2B feature itself.
set -e
cd "$(dirname "$0")" || exit 1
clear
LOG="git_phase2b_fix.log"
exec > >(tee "$LOG") 2>&1

echo "=========================================================="
echo "  Phase 2B hardening — internal reconciliation job"
echo "=========================================================="

[ -f .git/index.lock ] && { echo "▸ removing stale .git/index.lock"; rm -f .git/index.lock; }

git config user.name  "Ty Phelps"
git config user.email "ronsmith13131313@gmail.com"

echo
echo "----- status -----"
git status --short

echo
echo "----- stage hardening files only -----"
git add migrations/0005_billing_job_locks.sql
git add migrations/0005_billing_job_locks.postgres.sql
git add jobs/__init__.py
git add jobs/reconcile_billing.py
git add tests/test_reconcile_job.py
git add billing.py                       # lock + dry-run + with-lock wrapper
git add server.py                        # remove POST /api/admin/billing/reconcile
git add DEPLOY.md                        # systemd timer + tax checklist
git add GIT_PHASE2B_FIX_COMMIT.command

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
    if [ -n "$HIT" ]; then echo "⚠ forbidden file staged: $HIT"; exit 2; fi
done
echo "▸ clean"

echo
echo "----- secret-value scan -----"
ADDED=$(git diff --cached -U0 | grep '^+' | grep -v '^+++')
SECRET_HITS=$(
{
    echo "$ADDED" | grep -E 'sk_live_[A-Za-z0-9]{20,}|sk_test_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}' || true
    echo "$ADDED" | grep -E -- '-----BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----' || true
} | grep -vE '\.example|GIT_PHASE.*\.command|STRIPE_TESTING\.md|DEPLOY\.md' || true
)
if [ -n "$SECRET_HITS" ]; then echo "⚠ possible secret:"; echo "$SECRET_HITS" | head -20; exit 2; fi
echo "▸ clean"

echo
echo "----- full test suite -----"
source .venv/bin/activate
python3 -m pytest tests/ -q --no-header 2>&1 | tee /tmp/_phase2b_fix_pytest.log
TEST_RC=${PIPESTATUS[0]}
RESULT=$(grep -E '^[0-9]+ passed' /tmp/_phase2b_fix_pytest.log | tail -1)
echo "▸ $RESULT (exit $TEST_RC)"
if [ $TEST_RC -ne 0 ]; then echo "▸ Aborting — tests not green."; exit $TEST_RC; fi
if ! echo "$RESULT" | grep -qE '^98 passed'; then
    echo "▸ Aborting — expected '98 passed' but got '$RESULT'."; exit 3
fi

echo
echo "----- commit -----"
git commit -m "fix: run billing reconciliation as an internal job"

echo
echo "▸ commit hash : $(git rev-parse HEAD)"
echo "▸ short hash  : $(git rev-parse --short HEAD)"
git show --stat --pretty="" HEAD | tail -25
echo
echo "▸ git log:"
git log --oneline -6
echo
echo "▸ status:"
git status --short
read -p "Press return to close." _
