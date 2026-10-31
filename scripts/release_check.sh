#!/usr/bin/env bash
# scripts/release_check.sh — read-only checks for the stacked release
# (#11 -> #12 -> #13 -> #14). It never merges, retargets or pushes; it only
# reports whether the next step is safe, and stops with the reason if not.
#
#   scripts/release_check.sh pre PR APPROVED_SHA EXPECTED_BASE [COMMITS FILES]
#       before merging PR: open; head is exactly the approved SHA; base is as
#       expected; GitHub reports it mergeable ("clean"); every check that
#       branch protection requires is green on that SHA; and (after a
#       retarget) its diff still has the expected commit and file counts
#
#   scripts/release_check.sh post MERGED_HEAD_SHA
#       after a merge: main's newest commit merges MERGED_HEAD_SHA, main's
#       files are exactly the merged head's files (nothing else changed), and
#       the required checks are green on main's head (re-run until CI ends)
#
# Needs gh (authenticated) and, for post, a clone with origin fetched.
set -euo pipefail
REPO=${REPO:-Orangi3/car-sniper-saas}
stop() { echo "STOP: $*" >&2; exit 1; }
ok() { echo "ok    $*"; }

green_on() {    # every required check has a successful run on $1
    local sha=$1 c n
    for c in $(gh api "repos/$REPO/branches/main/protection" --jq '.required_status_checks.contexts[]'); do
        n=$(gh api "repos/$REPO/commits/$sha/check-runs?check_name=$c" \
              --jq '[.check_runs[] | select(.conclusion == "success")] | length')
        [ "$n" -ge 1 ] || stop "required check '$c' is not green on ${sha:0:7} (pending or failed)"
        ok "required check $c green on ${sha:0:7}"
    done
}

case "${1:-}" in
pre)
    [ $# -ge 4 ] || stop "usage: $0 pre PR APPROVED_SHA EXPECTED_BASE [COMMITS FILES]"
    pr=$2 sha=$3 base=$4
    IFS=$'\t' read -r state head b ms commits files < <(gh api "repos/$REPO/pulls/$pr" \
        --jq '[.state, .head.sha, .base.ref, .mergeable_state, .commits, .changed_files] | @tsv')
    [ "$state" = open ] || stop "PR #$pr is $state"
    [ "$head" = "$sha" ] || stop "PR #$pr head is $head, not the approved $sha"
    ok "PR #$pr head is the approved $sha"
    [ "$b" = "$base" ] || stop "PR #$pr targets $b, expected $base"
    ok "PR #$pr targets $base"
    [ "$ms" = clean ] || stop "PR #$pr mergeable_state is '$ms' (needs 'clean'; 'unknown' = ask again in a minute)"
    ok "PR #$pr is mergeable (clean)"
    if [ $# -ge 6 ]; then
        [ "$commits" = "$5" ] && [ "$files" = "$6" ] \
            || stop "PR #$pr diff is $commits commits / $files files, expected $5 / $6"
        ok "PR #$pr diff is $commits commits / $files files, as expected"
    fi
    green_on "$sha"
    echo "READY: gh api -X PUT repos/$REPO/pulls/$pr/merge -f merge_method=merge -f sha=$sha"
    ;;
post)
    [ $# -eq 2 ] || stop "usage: $0 post MERGED_HEAD_SHA"
    merged=$2
    git fetch -q origin main
    main=$(git rev-parse origin/main)
    git rev-parse "$main^2" >/dev/null 2>&1 && [ "$(git rev-parse "$main^2")" = "$merged" ] \
        || stop "main's head ${main:0:7} is not a merge of ${merged:0:7}"
    ok "main ${main:0:7} merges ${merged:0:7}"
    git diff --quiet "$merged" "$main" || stop "main's files differ from ${merged:0:7}: something else changed main"
    ok "main's files are exactly ${merged:0:7}'s"
    green_on "$main"
    echo "DONE: main verified; retarget the next PR to main, then run 'pre' for it"
    ;;
*)
    stop "usage: $0 pre PR APPROVED_SHA EXPECTED_BASE [COMMITS FILES] | $0 post MERGED_HEAD_SHA"
    ;;
esac
