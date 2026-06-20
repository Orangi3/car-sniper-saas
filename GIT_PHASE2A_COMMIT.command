#!/bin/bash
# GIT_PHASE2A_COMMIT.command
# Phase-2A checkpoint: stage ONLY the Stripe billing implementation,
# migration, tests, docs, and .env.example. Verify no secret / db / log
# is staged. Run the full suite. Commit. No tag. No push.
set -e
cd "$(dirname "$0")" || exit 1
clear

LOG="git_phase2a.log"
exec > >(tee "$LOG") 2>&1

echo "=========================================================="
echo "  Phase 2A Git checkpoint  (Stripe TEST MODE foundation)"
echo "=========================================================="

# 0. Clear any stale index.lock left by an aborted run
[ -f .git/index.lock ] && { echo "▸ removing stale .git/index.lock"; rm -f .git/index.lock; }

# 1. Identity (idempotent)
git config user.name  "Ty Phelps"
git config user.email "ronsmith13131313@gmail.com"

echo
echo "----- 1) git status --short (before staging) -----"
git status --short

echo
echo "----- 2) stage ONLY Phase 2A files (explicit add list) -----"
# Brand-new files
git add billing.py
git add migrations/0003_billing.sql
git add migrations/0003_billing.postgres.sql
git add tests/test_billing.py
git add STRIPE_TESTING.md
# Modified Phase-1 files
git add .env.example           # adds STRIPE_* placeholders, no real values
git add requirements.txt       # pins stripe>=8.0
git add server.py              # /api/billing/{checkout,webhook,me}
# Dev tooling — pytest dep check now also imports stripe
git add "RUN PHASE1 TESTS.command"
# This script itself (so the next round can re-run / inspect what was done)
git add GIT_PHASE2A_COMMIT.command

echo
echo "----- 3) what's staged -----"
git diff --cached --name-only | sort

echo
echo "----- 4) what's still UNSTAGED (must be empty if clean) -----"
git status --short | grep -v '^A ' | grep -v '^M ' || echo "(none)"

echo
echo "----- 5) whitespace / conflict check -----"
git diff --cached --check && echo "▸ clean"

echo
echo "----- 6) verify NOTHING sensitive is staged -----"
# Hard fail if any of these somehow ended up in the staged set.
FORBIDDEN_HITS=""
for pat in '\.env$' '\.env\.local$' '^overrides\.json$' \
           '\.db$' '\.db-shm$' '\.db-wal$' '\.sqlite' \
           '\.log$' '^\.venv/' '__pycache__' \
           '^cloudflared$' 'phase1_pytest\.log' 'git_phase[12].*\.log' ; do
    HIT=$(git diff --cached --name-only | grep -E "$pat" || true)
    if [ -n "$HIT" ]; then
        FORBIDDEN_HITS="$FORBIDDEN_HITS$HIT"$'\n'
    fi
done
if [ -n "$FORBIDDEN_HITS" ]; then
    echo "⚠ Forbidden files staged:"
    echo "$FORBIDDEN_HITS"
    echo "Aborting."
    exit 2
fi
echo "▸ no .env, secrets, DBs, logs, or local artifacts staged"

echo
echo "----- 7) scan staged content for actual secret VALUES -----"
# Catch sk_live_, sk_test_, whsec_, AWS keys, PEM private keys, or
# assignment-of-long-random-strings. Variable/function NAMES are fine.
ADDED=$(git diff --cached -U0 | grep '^+' | grep -v '^+++')
SECRET_HITS=$(
{
    echo "$ADDED" | grep -E 'sk_live_[A-Za-z0-9]{20,}|sk_test_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|rk_live_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}' || true
    echo "$ADDED" | grep -E -- '-----BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----' || true
    # high-entropy assignment to a secret-named variable, excluding code patterns
    echo "$ADDED" | grep -iE '(secret|password|api[_-]?key|token|access[_-]?key|private[_-]?key)[[:space:]]*[:=][[:space:]]*["'\'']?[A-Za-z0-9_/+=-]{24,}["'\'']?' \
        | grep -vE '(\bget\b|\bos\.environ\.get|getattr|hasattr|setattr|\bdef [a-z_]+\(|placeholder|TODO|FIXME|REPLACE|YOUR_|example|<your|\${|<input|value=""|password>|passwordType|\.password\b|password,|password =\s*body|password =\s*\(?body|fake|test|dummy|monkeypatch)' || true
} | grep -vE '\.example|\.gitignore|GIT_PHASE.*\.command|STRIPE_TESTING\.md' || true
)
if [ -n "$SECRET_HITS" ]; then
    echo "⚠ Possible secret VALUE in staged content:"
    echo "$SECRET_HITS" | head -30
    exit 2
fi
echo "▸ no high-entropy secret values in staged content"

echo
echo "----- 8) full test suite must be green -----"
source .venv/bin/activate
python3 -m pytest tests/ -q --no-header 2>&1 | tee /tmp/_phase2a_pytest.log
TEST_RC=${PIPESTATUS[0]}
RESULT=$(grep -E '^[0-9]+ passed' /tmp/_phase2a_pytest.log | tail -1)
echo "▸ $RESULT (exit $TEST_RC)"
if [ $TEST_RC -ne 0 ]; then
    echo "▸ Aborting commit — tests not green."
    exit $TEST_RC
fi
if ! echo "$RESULT" | grep -qE '^65 passed'; then
    echo "▸ Aborting commit — expected '65 passed' but got '$RESULT'."
    exit 3
fi

echo
echo "----- 9) commit -----"
git commit -m "feat: add Stripe test-mode billing foundation"

echo
echo "----- 10) result -----"
echo
echo "▸ commit hash : $(git rev-parse HEAD)"
echo "▸ short hash  : $(git rev-parse --short HEAD)"
echo
echo "▸ committed files:"
git show --stat --pretty="" HEAD
echo
echo "▸ test result : $RESULT"
echo
echo "▸ recent log:"
git log --oneline -5
echo
echo "▸ git status --short (should be empty):"
git status --short
echo "▸ done."
read -p "Press return to close." _
