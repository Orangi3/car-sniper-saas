#!/usr/bin/env bash
# Deploy from an approved committed tree. The host helper owns the release lock.
set -euo pipefail

mode=plan
restart=restart
expected=
target=
host=
while (( $# )); do
    case "$1" in
        --apply|--rollback|--recover|--status|--verify-delete-scope)
            [[ "$mode" == plan ]] || { echo "Choose one operation." >&2; exit 2; }
            mode="$1"
            shift ;;
        --no-restart) restart=defer; shift ;;
        --expect) expected="${2:?Missing expected active revision}"; shift 2 ;;
        --target) target="${2:?Missing rollback target}"; shift 2 ;;
        -h|--help)
            cat <<'HELP'
deploy/sync.sh [--apply [--no-restart] | --status | --recover] root@host
deploy/sync.sh --rollback --expect ACTIVE --target PREVIOUS root@host
deploy/sync.sh --verify-delete-scope root@host
Default mode prints the committed file plan. --recover never switches the pointer.
HELP
            exit 0 ;;
        root@*)
            [[ -z "$host" ]] || { echo "Specify exactly one host." >&2; exit 2; }
            host="$1"
            shift ;;
        *) echo "Unsupported argument: $1" >&2; exit 2 ;;
    esac
done
[[ "$host" =~ ^root@[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || {
    echo "Specify the approved root@host destination." >&2; exit 2;
}
if [[ "$mode" != --apply && "$restart" != restart ]]; then
    echo "--no-restart requires --apply." >&2; exit 2
fi
if [[ "$mode" != --rollback && -n "$expected$target" ]]; then
    echo "Revision pins require --rollback." >&2; exit 2
fi

runner=$(base64 < "$(dirname "$0")/release_transition.py" | tr -d '\r\n')
remote() {
    # Mode, revision and digest arguments are fixed tokens or validated hex.
    # BatchMode + ConnectTimeout: a hung or interactively-prompting ssh
    # (unknown host key, password prompt) must fail fast, never hang
    # the operator run indefinitely. Authentication is key-based.
    ssh -o BatchMode=yes -o ConnectTimeout=30 "$host" "python3 -c 'import base64;exec(base64.b64decode(\"$runner\"))' $*"
}

case "$mode" in
    --status) remote status < /dev/null; exit ;;
    --recover) remote recover < /dev/null; exit ;;
    --rollback)
        if ! [[ "$expected" =~ ^[a-f0-9]{7,40}$ && "$target" =~ ^[a-f0-9]{7,40}$ ]]; then
            echo "Run --status, then supply --expect ACTIVE and --target PREVIOUS." >&2
            exit 2
        fi
        remote rollback "$expected" "$target" < /dev/null
        exit ;;
    --verify-delete-scope)
        echo "Apply stages a committed archive in a new directory. There is no delete or prune operation."
        exit ;;
esac

cd "$(git rev-parse --show-toplevel)"
sha=$(git rev-parse --verify HEAD)
[[ "$sha" =~ ^[a-f0-9]{40}$ ]] || { echo "Unexpected Git revision." >&2; exit 2; }
git diff --quiet HEAD -- || { echo "Refusing deploy: uncommitted changes present. Commit and review before deploying." >&2; exit 4; }
if [[ "$mode" == plan ]]; then
    printf 'Committed file plan for %s at %s\n' "$host" "$sha"
    git ls-tree -r --name-only "$sha"
    exit
fi
archive=$(mktemp)
trap 'rm -f -- "$archive"' EXIT
git archive --format=tar --output="$archive" "$sha"
digest=$(shasum -a 256 < "$archive")
digest=${digest%% *}
[[ "$digest" =~ ^[a-f0-9]{64}$ ]] || { echo "Archive digest failed." >&2; exit 2; }
remote deploy "$sha" "$digest" "$restart" < "$archive"
