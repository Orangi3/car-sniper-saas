#!/bin/bash
# Amend the GIT_BILLING_CORE_TAG.command helper into the v0.2.0-billing-core
# commit, then re-create the annotated tag (since amend rewrites the hash).
set -e
cd "$(dirname "$0")" || exit 1
clear
LOG="git_amend_billing_core.log"
exec > >(tee "$LOG") 2>&1

[ -f .git/index.lock ] && rm -f .git/index.lock || true
git config user.name  "Ty Phelps"
git config user.email "ronsmith13131313@gmail.com"

echo "----- status before -----"
git status --short
git log --oneline --decorate -3

git add GIT_BILLING_CORE_TAG.command
git add GIT_AMEND_BILLING_CORE.command

echo
echo "----- staged for amend -----"
git diff --cached --name-only

# Re-run the full suite before amending
source .venv/bin/activate
python3 -m pytest tests/ -q --no-header 2>&1 | tail -3
RC=${PIPESTATUS[0]}
if [ $RC -ne 0 ]; then echo "tests not green — abort"; exit $RC; fi

git commit --amend --no-edit
# Recreate the tag at the new HEAD
git tag -d v0.2.0-billing-core
git tag -a v0.2.0-billing-core -m "Billing core: verified Stripe webhooks, subscription lifecycle, portal readiness, and internal reconciliation."

echo
echo "▸ commit hash : $(git rev-parse HEAD)"
echo "▸ short hash  : $(git rev-parse --short HEAD)"
echo "▸ tag         : $(git describe --exact-match --tags HEAD)"
git show --stat --pretty="" HEAD | tail -5
echo
git log --oneline --decorate -3
echo
echo "▸ git status --short (must be empty):"
git status --short
read -p "Press return to close." _
