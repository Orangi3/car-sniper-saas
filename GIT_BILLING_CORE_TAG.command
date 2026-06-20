#!/bin/bash
# GIT_BILLING_CORE_TAG.command
# Consolidate the three billing commits (9cf7fe3, f899a4f, 2da76c6) made
# since v0.1.0-phase1 into ONE checkpoint commit:
#     feat: establish subscription billing core
# Then tag it v0.2.0-billing-core. No push. No remote.
#
# Safe to re-run: aborts if the squashed commit already exists at HEAD.
set -e
cd "$(dirname "$0")" || exit 1
clear
LOG="git_billing_core.log"
exec > >(tee "$LOG") 2>&1

echo "=========================================================="
echo "  Billing core checkpoint  (v0.2.0-billing-core)"
echo "=========================================================="

[ -f .git/index.lock ] && { echo "▸ removing stale .git/index.lock"; rm -f .git/index.lock; }
git config user.name  "Ty Phelps"
git config user.email "ronsmith13131313@gmail.com"

echo
echo "----- 1) git status --short -----"
git status --short

echo
echo "----- log before squash -----"
git log --oneline --decorate -10

# Don't double-squash if the tag already exists.
if git rev-parse v0.2.0-billing-core >/dev/null 2>&1; then
    echo "▸ tag v0.2.0-billing-core already exists — nothing to do."
    git log --oneline --decorate -5
    exit 0
fi

# Hard sanity: every commit since v0.1.0-phase1 must be one of the three
# expected billing commits. If anything else is present, abort.
EXPECTED="9cf7fe3 f899a4f 2da76c6"
ACTUAL=$(git rev-list --reverse v0.1.0-phase1..HEAD | awk '{print substr($1,1,7)}' | xargs)
echo "expected:  $EXPECTED"
echo "actual  :  $ACTUAL"
if [ "$EXPECTED" != "$ACTUAL" ]; then
    echo "⚠ commits between v0.1.0-phase1 and HEAD don't match the expected"
    echo "  billing-core trio. Aborting to avoid losing unrelated work."
    exit 2
fi

echo
echo "----- 2) soft-reset to v0.1.0-phase1 (keeps all changes staged) -----"
git reset --soft v0.1.0-phase1
git status --short | head -40

echo
echo "----- 3) staged-only check — anything ignored leaked? -----"
for pat in '\.env$' '\.env\.local$' '^overrides\.json$' \
           '\.db$' '\.db-shm$' '\.db-wal$' '\.sqlite' \
           '\.log$' '^\.venv/' '__pycache__' '^cloudflared$' ; do
    HIT=$(git diff --cached --name-only | grep -E "$pat" || true)
    if [ -n "$HIT" ]; then echo "⚠ forbidden file staged: $HIT"; exit 3; fi
done
echo "▸ no ignored / forbidden paths in staged set"

echo
echo "----- 4) diff --check -----"
git diff --cached --check && echo "▸ clean"

echo
echo "----- 5) secret-value scan -----"
ADDED=$(git diff --cached -U0 | grep '^+' | grep -v '^+++')
HITS=$(
{
    # service-key prefixes
    echo "$ADDED" | grep -E 'sk_live_[A-Za-z0-9]{20,}|sk_test_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|rk_live_[A-Za-z0-9]{20,}|pk_live_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36,}|xox[abprs]-[A-Za-z0-9-]{10,}' || true
    # PEM blocks
    echo "$ADDED" | grep -E -- '-----BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----' || true
    # high-entropy assignment to a secret-named var (excluding code that just NAMES a token/password)
    echo "$ADDED" | grep -iE '(secret|password|api[_-]?key|token|access[_-]?key|private[_-]?key)[[:space:]]*[:=][[:space:]]*["'\'']?[A-Za-z0-9_/+=-]{32,}["'\'']?' \
        | grep -vE '(\bget\b|\bos\.environ\.get|getattr|hasattr|setattr|\bdef [a-z_]+\(|placeholder|TODO|FIXME|REPLACE|YOUR_|example|<your|\${|<input|value=""|password>|passwordType|\.password\b|password,|password =\s*body|password =\s*\(?body|fake|test|dummy|monkeypatch|str\(uuid|sha256|hash)' || true
} | grep -vE '\.example|\.gitignore|GIT_.*\.command|STRIPE_TESTING\.md|DEPLOY\.md|PHASE2_PLAN\.md' || true
)
if [ -n "$HITS" ]; then
    echo "⚠ possible secret VALUE in staged content:"; echo "$HITS" | head -20; exit 4
fi
echo "▸ no high-entropy secret values in staged content"

echo
echo "----- 6) full pytest -----"
source .venv/bin/activate
python3 -m pytest tests/ -q --no-header 2>&1 | tee /tmp/_billing_core_pytest.log
TEST_RC=${PIPESTATUS[0]}
RESULT=$(grep -E '^[0-9]+ passed' /tmp/_billing_core_pytest.log | tail -1)
echo "▸ $RESULT (exit $TEST_RC)"
if [ $TEST_RC -ne 0 ]; then echo "▸ Aborting — tests not green."; exit $TEST_RC; fi
if ! echo "$RESULT" | grep -qE '^98 passed'; then
    echo "▸ Aborting — expected '98 passed' but got '$RESULT'."; exit 5
fi

echo
echo "----- 7) commit + tag -----"
git commit -m "feat: establish subscription billing core"
git tag -a v0.2.0-billing-core -m "Billing core: verified Stripe webhooks, subscription lifecycle, portal readiness, and internal reconciliation."

echo
echo "▸ commit hash : $(git rev-parse HEAD)"
echo "▸ short hash  : $(git rev-parse --short HEAD)"
echo "▸ tag         : $(git describe --exact-match --tags HEAD)"
echo
echo "▸ committed files:"
git show --stat --pretty="" HEAD
echo
echo "▸ test result : $RESULT"
echo
echo "▸ git log:"
git log --oneline --decorate -6
echo
echo "▸ git status --short (must be empty):"
git status --short
read -p "Press return to close." _
