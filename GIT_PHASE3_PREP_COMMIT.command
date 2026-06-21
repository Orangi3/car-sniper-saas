#!/usr/bin/env bash
# GIT_PHASE3_PREP_COMMIT.command — staging deployment artifacts only.
# No tag, no push, no remote. Adds 17 files under deploy/ + STAGING_RUNBOOK.md.
set -e
cd "$(dirname "$0")" || exit 1
clear
LOG="git_phase3_prep.log"
exec > >(tee "$LOG") 2>&1

echo "=========================================================="
echo "  Phase 3 prep — staging deployment artifacts"
echo "=========================================================="

[ -f .git/index.lock ] && rm -f .git/index.lock || true
git config user.name  "Ty Phelps"
git config user.email "ronsmith13131313@gmail.com"

echo "----- status -----"
git status --short

echo
echo "----- stage Phase 3 prep files -----"
git add STAGING_RUNBOOK.md
git add deploy/README.md
git add deploy/Caddyfile.staging
git add deploy/env.staging.example
git add deploy/install.sh
git add deploy/sync.sh
git add deploy/backup.sh
git add deploy/restore.sh
git add deploy/systemd/sniper-migrate.service
git add deploy/systemd/sniper-web.service
git add deploy/systemd/sniper-scheduler.service
git add deploy/systemd/sniper-reconcile.service
git add deploy/systemd/sniper-reconcile.timer
git add deploy/systemd/sniper-backup.service
git add deploy/systemd/sniper-backup.timer
git add deploy/systemd/sniper-healthcheck.service
git add deploy/systemd/sniper-healthcheck.timer
git add GIT_PHASE3_PREP_COMMIT.command

echo
echo "----- staged -----"
git diff --cached --name-only | sort

echo
echo "----- diff --check -----"
git diff --cached --check && echo "▸ clean"

echo
echo "----- forbidden-path scan -----"
for pat in '\.env$' '\.env\.local$' '^overrides\.json$' \
           '\.db$' '\.db-shm$' '\.db-wal$' '\.sqlite' \
           '\.log$' '^\.venv/' '__pycache__' '^cloudflared$' ; do
    HIT=$(git diff --cached --name-only | grep -E "$pat" || true)
    if [ -n "$HIT" ]; then echo "⚠ forbidden: $HIT"; exit 2; fi
done
echo "▸ clean"

echo
echo "----- secret-value scan -----"
ADDED=$(git diff --cached -U0 | grep '^+' | grep -v '^+++')
HITS=$(
{
    echo "$ADDED" | grep -E 'sk_live_[A-Za-z0-9]{20,}|sk_test_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|pk_live_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}' || true
    echo "$ADDED" | grep -E -- '-----BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----' || true
} | grep -vE '\.example|GIT_.*\.command|STRIPE_TESTING\.md|DEPLOY\.md|PHASE2_PLAN\.md|STAGING_RUNBOOK\.md|deploy/README\.md|deploy/backup\.sh|deploy/restore\.sh' || true
)
if [ -n "$HITS" ]; then echo "⚠ possible secret:"; echo "$HITS" | head -20; exit 3; fi
echo "▸ no secret values in staged content"

echo
echo "----- full test suite -----"
source .venv/bin/activate
python3 -m pytest tests/ -q --no-header 2>&1 | tee /tmp/_p3_pytest.log
TEST_RC=${PIPESTATUS[0]}
RESULT=$(grep -E '^[0-9]+ passed' /tmp/_p3_pytest.log | tail -1)
echo "▸ $RESULT (exit $TEST_RC)"
if [ $TEST_RC -ne 0 ]; then echo "▸ aborting — tests not green"; exit $TEST_RC; fi
if ! echo "$RESULT" | grep -qE '^121 passed'; then
    echo "▸ aborting — expected '121 passed' but got '$RESULT'"; exit 4
fi

echo
echo "----- no git remote (must be empty) -----"
git remote -v
[ -z "$(git remote)" ] && echo "▸ no remote configured (correct)"

echo
echo "----- commit -----"
git commit -m "chore: add staging deployment runbook"

echo
echo "▸ commit hash : $(git rev-parse HEAD)"
echo "▸ short hash  : $(git rev-parse --short HEAD)"
git show --stat --pretty="" HEAD | tail -22
echo
git log --oneline --decorate -5
echo
echo "▸ git status --short (must be empty):"
git status --short
read -p "Press return to close." _
