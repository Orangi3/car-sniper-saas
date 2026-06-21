#!/bin/bash
# GIT_PHASE2C_COMMIT.command
# Single commit for the Phase 2C subscription-management UI work.
# No tag, no push.
set -e
cd "$(dirname "$0")" || exit 1
clear
LOG="git_phase2c.log"
exec > >(tee "$LOG") 2>&1

echo "=========================================================="
echo "  Phase 2C — subscription management dashboard UI"
echo "=========================================================="

[ -f .git/index.lock ] && rm -f .git/index.lock || true
git config user.name  "Ty Phelps"
git config user.email "ronsmith13131313@gmail.com"

echo
echo "----- status -----"
git status --short

echo
echo "----- stage Phase 2C files -----"
git add dashboard.html               # billing-card placeholder + JS module
git add server.py                    # /billing/{success,cancel} + index passthrough
git add tests/test_billing_ui.py
git add "MIGRATE AND REOPEN.command" # helper, no secrets
git add GIT_PHASE2C_COMMIT.command

echo
echo "----- staged -----"
git diff --cached --name-only | sort

echo
echo "----- unstaged remainder -----"
git status --short | grep -v '^A ' | grep -v '^M ' || echo "(none)"

echo
echo "----- whitespace + conflict check -----"
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
HITS=$(
{
    echo "$ADDED" | grep -E 'sk_live_[A-Za-z0-9]{20,}|sk_test_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|pk_live_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}' || true
    echo "$ADDED" | grep -E -- '-----BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----' || true
} | grep -vE '\.example|\.gitignore|GIT_.*\.command|STRIPE_TESTING\.md|DEPLOY\.md|PHASE2_PLAN\.md' || true
)
if [ -n "$HITS" ]; then echo "⚠ possible secret:"; echo "$HITS" | head -20; exit 3; fi
echo "▸ no secret values in staged content"

echo
echo "----- full test suite must be green -----"
source .venv/bin/activate
python3 -m pytest tests/ -q --no-header 2>&1 | tee /tmp/_phase2c_pytest.log
TEST_RC=${PIPESTATUS[0]}
RESULT=$(grep -E '^[0-9]+ passed' /tmp/_phase2c_pytest.log | tail -1)
echo "▸ $RESULT (exit $TEST_RC)"
if [ $TEST_RC -ne 0 ]; then echo "▸ Aborting — tests not green."; exit $TEST_RC; fi
if ! echo "$RESULT" | grep -qE '^109 passed'; then
    echo "▸ Aborting — expected '109 passed' but got '$RESULT'."; exit 4
fi

echo
echo "----- commit -----"
git commit -m "feat: add subscription management dashboard UI"

echo
echo "▸ commit hash : $(git rev-parse HEAD)"
echo "▸ short hash  : $(git rev-parse --short HEAD)"
git show --stat --pretty="" HEAD | tail -15
echo
git log --oneline --decorate -5
echo
echo "▸ git status --short (should be empty):"
git status --short
read -p "Press return to close." _
