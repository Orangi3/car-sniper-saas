#!/bin/bash
# GIT_PHASE1_COMMIT.command
# One-shot script: clean any stale git lock, inspect, stage Phase-1 work,
# verify tests, commit, tag. Halts on any failure.
set -e
cd "$(dirname "$0")" || exit 1
clear

LOG="git_phase1.log"
exec > >(tee "$LOG") 2>&1

echo "=========================================================="
echo "  Phase 1 Git checkpoint"
echo "=========================================================="

# 0. Drop any stale index.lock left by previous interrupted runs
[ -f .git/index.lock ] && { echo "▸ removing stale .git/index.lock"; rm -f .git/index.lock; }

# 1. Identity (idempotent)
git config user.name  "Ty Phelps"
git config user.email "ronsmith13131313@gmail.com"

# Make sure we're on main
git symbolic-ref HEAD refs/heads/main 2>/dev/null || true
git branch -m main 2>/dev/null || true

echo
echo "----- 1) git status --short (before staging) -----"
git status --short

echo
echo "----- .gitignore -----"
cat .gitignore

echo
echo "----- ignored files (sanity: secrets / DBs / logs / venv) -----"
git status --ignored --short | grep '^!!' | head -40 || true

echo
echo "----- 2) stage everything not ignored -----"
git add -A

echo
echo "----- 3) git diff --cached --check (whitespace / conflict markers) -----"
git diff --cached --check && echo "▸ clean"

echo
echo "----- 4) scan staged content for likely secret VALUES -----"
# Match real-looking secrets only: known prefixes, PEM blocks, or
# assignment-of-long-random-string. Refuses code that merely *names*
# tokens/passwords (variable/function names are fine).
ADDED=$(git diff --cached -U0 | grep '^+' | grep -v '^+++')
SECRET_HITS=$(
{
    # 4a. Known service-key prefixes followed by a real value
    echo "$ADDED" | grep -E 'sk_live_[A-Za-z0-9]{20,}|sk_test_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|rk_live_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36,}|gho_[A-Za-z0-9]{36,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}' || true
    # 4b. PEM private-key headers
    echo "$ADDED" | grep -E -- '-----BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----' || true
    # 4c. Assignments of long quoted random-looking strings (24+ chars, mixed case+digits)
    #     to a secret-named variable. Excludes placeholders and example values.
    echo "$ADDED" | grep -iE '(secret|password|api[_-]?key|token|auth[_-]?key|access[_-]?key|private[_-]?key)[[:space:]]*[:=][[:space:]]*["'\'']?[A-Za-z0-9_/+=-]{24,}["'\'']?' \
        | grep -vE '(\bget\b|\bos\.environ\.get|getattr|hasattr|setattr|\bdef [a-z_]+\(|placeholder|TODO|FIXME|REPLACE|YOUR_|example|<your|\${|<input|value=""|password>|passwordType|\.password\b|password,|password =\s*body|password =\s*\(?body|fake|test|dummy)' || true
} | grep -vE '\.example|\.gitignore|GIT_PHASE1_COMMIT\.command' || true
)
if [ -n "$SECRET_HITS" ]; then
    echo
    echo "⚠ Possible secret-like VALUE in staged diff:"
    echo "$SECRET_HITS" | head -30
    echo
    echo "▸ Aborting commit. Inspect the matches above, scrub if real."
    exit 2
fi
echo "▸ no high-entropy secret values detected in staged content"

echo
echo "----- staged file list -----"
git diff --cached --name-only | sort

echo
echo "----- 5) run the full test suite -----"
source .venv/bin/activate
python3 -m pytest tests/ -q --no-header 2>&1 | tee /tmp/_phase1_pytest_inline.log
TEST_RC=${PIPESTATUS[0]}
RESULT=$(grep -E '^[0-9]+ passed' /tmp/_phase1_pytest_inline.log | tail -1)
echo "▸ pytest summary: $RESULT  (exit $TEST_RC)"
if [ $TEST_RC -ne 0 ]; then
    echo "▸ Aborting commit — tests are not green."
    exit $TEST_RC
fi
EXPECTED="47 passed"
if ! echo "$RESULT" | grep -q "$EXPECTED"; then
    echo "▸ Aborting commit — expected '$EXPECTED' but pytest reported '$RESULT'."
    exit 3
fi

echo
echo "----- 6) commit -----"
git commit -m "feat: establish phase 1 SaaS foundation"

echo
echo "----- 7) annotated tag -----"
git tag -a v0.1.0-phase1 -m "Phase 1: multi-user SaaS foundation, secured polling, migrations, and deployment hardening."

echo
echo "----- 8) final report -----"
echo
echo "▸ commit hash : $(git rev-parse HEAD)"
echo "▸ tag         : $(git describe --exact-match --tags HEAD)"
echo
echo "▸ committed files:"
git show --stat --pretty="" HEAD
echo
echo "▸ test result : $RESULT"
echo
echo "▸ git status --short (should be empty):"
git status --short
echo "▸ done."
read -p "Press return to close." _
