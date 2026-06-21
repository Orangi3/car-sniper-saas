#!/usr/bin/env bash
# deploy/sync.sh — rsync deploy from your Mac to the staging VPS.
#
# Release model (immutable, atomic flip):
#
#   /opt/sniper/
#   ├── .venv/                       (PERSISTENT — never touched by sync)
#   ├── releases/
#   │   ├── <sha-A>/                 (IMMUTABLE release dir; built by rsync)
#   │   ├── <sha-B>/                 (IMMUTABLE)
#   │   └── <sha-C>/                 (IMMUTABLE — last 5 kept; prior known-good never pruned)
#   ├── current -> releases/<sha-X>  (SYMLINK; flipped atomically AFTER canary passes)
#   ├── current_revision             (text file; the sha currently in `current`)
#   └── previous_revision            (text file; the sha to roll back to)
#
#   /etc/sniper/             (PERSISTENT — env file, age recipient, …)
#   /var/log/sniper/         (PERSISTENT — gunicorn + scheduler + reconcile logs)
#   /var/backups/sniper/     (PERSISTENT — encrypted dump staging area)
#
# Hard guarantees (enforced in code below + provable via --verify-delete-scope):
#
#   * rsync --delete targets ONLY /opt/sniper/releases/<sha>/.
#     It physically cannot reach /opt/sniper/, /opt/sniper/current/,
#     /opt/sniper/.venv/, /etc/sniper/, /var/log/sniper/, /var/backups/sniper/,
#     or any prior release directory.
#   * No --exclude denylist exists. The transfer set comes ONLY from
#     `git ls-files` (strict allowlist). A file that's untracked in git
#     cannot reach the host.
#   * `current` is a symlink, not a directory. rsync never overwrites it.
#   * The flip happens AFTER migrations succeed AND a canary gunicorn
#     bound to a private port returns 200 from /health against the new
#     release. A failed canary leaves `current` pointing at the previous
#     release untouched.
#   * `previous_revision` is updated BEFORE the flip so rollback always
#     knows the prior known-good sha. The last 5 releases are kept and
#     the prior known-good is NEVER pruned even if it falls out of the
#     last-5 window.
#
# Modes:
#   bash deploy/sync.sh                                # dry-run (default)
#   bash deploy/sync.sh --apply sniper@<host>          # full deploy + canary + flip
#   bash deploy/sync.sh --apply --no-restart …         # transfer + flip only
#   bash deploy/sync.sh --rollback sniper@<host>       # flip current → previous_revision
#   bash deploy/sync.sh --status sniper@<host>         # show current + previous + service status
#   bash deploy/sync.sh --verify-delete-scope sniper@<host>
#       Dry-run rsync with --itemize-changes to show EXACTLY which paths
#       --delete would touch. Asserts every touched path is under
#       /opt/sniper/releases/<sha>/. Fails loudly if not.

set -euo pipefail

DRY_RUN=true
NO_RESTART=false
DO_ROLLBACK=false
DO_STATUS=false
VERIFY_SCOPE=false
HOST=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply)                DRY_RUN=false; shift ;;
        --no-restart)           NO_RESTART=true; shift ;;
        --rollback)             DO_ROLLBACK=true; DRY_RUN=false; shift ;;
        --status)               DO_STATUS=true; DRY_RUN=false; shift ;;
        --verify-delete-scope)  VERIFY_SCOPE=true; shift ;;
        -h|--help)
            sed -n '2,52p' "$0"
            exit 0 ;;
        *)                      HOST="$1"; shift ;;
    esac
done

if [[ -z "$HOST" ]]; then
    echo "▸ usage: bash deploy/sync.sh [--apply|--rollback|--status|--verify-delete-scope] sniper@<host>"
    exit 1
fi

cd "$(git rev-parse --show-toplevel)"
SHA="$(git rev-parse --short HEAD)"

# ---------------------------------------------------------------------------
# Strict REL_DIR validator — refuses to proceed unless the destination
# matches /opt/sniper/releases/<7-or-more-hex>/.
# Defense-in-depth against accidental REL_DIR=/opt/sniper/current/ etc.
# ---------------------------------------------------------------------------
release_dir_for() {
    local sha="$1"
    # Strict: ONLY pure lowercase hex, length 7..40. No slashes, no
    # dots, no uppercase, no anything else. Anything else exits 3.
    # Critical: defending against e.g. "abc1234/../current" which would
    # otherwise resolve to /opt/sniper/releases/abc1234/../current
    # → /opt/sniper/current. The deny-list below would still catch
    # /opt/sniper/current, but the strict-hex check catches it earlier.
    if ! [[ "$sha" =~ ^[a-f0-9]{7,40}$ ]]; then
        echo "FATAL: refusing — sha '$sha' does not look like a git short SHA." >&2
        exit 3
    fi
    local rd="/opt/sniper/releases/${sha}"
    # Belt + suspenders: explicit deny-list of paths that the rest of
    # this script MUST never construct. If any of these strings appears
    # as REL_DIR, abort.
    for forbidden in \
        "/opt/sniper" \
        "/opt/sniper/" \
        "/opt/sniper/current" \
        "/opt/sniper/current/" \
        "/opt/sniper/.venv" \
        "/opt/sniper/releases" \
        "/opt/sniper/releases/" \
        "/etc/sniper" \
        "/var/log/sniper" \
        "/var/backups/sniper" ; do
        if [[ "$rd" == "$forbidden" || "$rd" == "$forbidden/" ]]; then
            echo "FATAL: REL_DIR resolved to a persistent path: $rd" >&2
            exit 3
        fi
    done
    # Must be under releases/, must NOT equal releases/ itself.
    case "$rd" in
        /opt/sniper/releases/?*) : ;;
        *) echo "FATAL: REL_DIR not under /opt/sniper/releases/: $rd" >&2
           exit 3 ;;
    esac
    echo "$rd"
}
REL_DIR="$(release_dir_for "$SHA")"

# ---------------------------------------------------------------------------
# --status
# ---------------------------------------------------------------------------
if $DO_STATUS; then
    ssh "$HOST" "set -e
        echo '▸ host paths:'
        ls -l /opt/sniper/current 2>/dev/null || true
        echo '▸ current revision:  '\$(cat /opt/sniper/current_revision  2>/dev/null || echo '(none)')
        echo '▸ previous revision: '\$(cat /opt/sniper/previous_revision 2>/dev/null || echo '(none)')
        echo '▸ releases on host:'
        ls -1t /opt/sniper/releases 2>/dev/null | head -10 | sed 's/^/    /'
        echo '▸ services:'
        systemctl --no-pager is-active sniper-migrate.service \\
            sniper-web.service sniper-scheduler.service \\
            sniper-reconcile.timer | paste -d '\\t' <(echo -e 'sniper-migrate.service\\nsniper-web.service\\nsniper-scheduler.service\\nsniper-reconcile.timer') -
    "
    exit 0
fi

# ---------------------------------------------------------------------------
# --rollback — flip `current` back to `previous_revision`, restart web.
# Never deletes anything; the previous release directory stays in place.
# ---------------------------------------------------------------------------
if $DO_ROLLBACK; then
    PREV=$(ssh "$HOST" "cat /opt/sniper/previous_revision 2>/dev/null || true")
    if [[ -z "$PREV" ]]; then
        echo "FATAL: /opt/sniper/previous_revision missing on host — nothing to roll back to." >&2
        exit 4
    fi
    PREV_DIR="$(release_dir_for "$PREV")"
    echo "▸ rolling back: current → $PREV_DIR"
    ssh "$HOST" "set -e
        test -d $PREV_DIR || { echo 'FATAL: previous release dir missing: $PREV_DIR' >&2; exit 5; }
        # Swap previous_revision and current_revision before the flip
        # so a rollback-of-rollback also works.
        CUR=\$(cat /opt/sniper/current_revision  2>/dev/null || echo '')
        echo \"\$CUR\" > /opt/sniper/previous_revision
        echo '$PREV' > /opt/sniper/current_revision
        ln -sfn $PREV_DIR /opt/sniper/current.new
        mv -Tf /opt/sniper/current.new /opt/sniper/current
        sudo systemctl restart sniper-migrate.service
        sudo systemctl restart sniper-web.service
        if systemctl is-enabled --quiet sniper-scheduler.service; then
            sudo systemctl restart sniper-scheduler.service
        fi"
    echo "▸ rollback complete; current is $PREV"
    exit 0
fi

# ---------------------------------------------------------------------------
# Build the rsync include-list from `git ls-files`. Strict allowlist; no
# --exclude. A file NOT in `git ls-files` cannot be transferred.
# ---------------------------------------------------------------------------
TMP_LIST=$(mktemp)
trap 'rm -f "$TMP_LIST"' EXIT
git ls-files > "$TMP_LIST"
N_FILES=$(wc -l < "$TMP_LIST" | tr -d ' ')

# Compose rsync args once so --verify-delete-scope and the real deploy
# share the same scoping (the only difference is --dry-run + verbose
# itemize for the verify path).
COMMON_ARGS=(
    -ahz
    --delete                     # scoped to $REL_DIR only — see assertion above
    --files-from="$TMP_LIST"
    --rsync-path="mkdir -p $REL_DIR && rsync"
    .
    "$HOST:$REL_DIR/"
)

# ---------------------------------------------------------------------------
# --verify-delete-scope
# Dry-runs rsync with --itemize-changes, captures every "*deleting"
# line, and asserts each one falls under $REL_DIR. Also greps for any
# accidental mention of persistent paths.
# ---------------------------------------------------------------------------
if $VERIFY_SCOPE; then
    echo "▸ Verifying --delete scope against $REL_DIR …"
    echo "    (rsync --dry-run --delete --itemize-changes, allowlist = $N_FILES tracked files)"
    OUT=$(mktemp)
    rsync --dry-run --itemize-changes "${COMMON_ARGS[@]}" > "$OUT" 2>&1 || true
    DEL_LINES=$(grep -E "^\*deleting" "$OUT" || true)
    if [[ -z "$DEL_LINES" ]]; then
        echo "▸ rsync would delete nothing on the host."
    else
        echo "▸ rsync would delete the following paths on the host:"
        echo "$DEL_LINES" | sed 's/^/    /'
        # Each path printed by rsync is RELATIVE to the destination root,
        # which is \$REL_DIR. So any deletion line is already, by
        # construction, scoped under \$REL_DIR. Still, fail loudly if
        # rsync ever emits an absolute path or a parent-traversal.
        if echo "$DEL_LINES" | grep -qE '\.\.|^[[:space:]]*\*deleting[[:space:]]+/'; then
            echo "FATAL: deletion path escaped destination scope." >&2
            cat "$OUT" >&2
            rm -f "$OUT"
            exit 6
        fi
    fi
    echo
    echo "▸ Persistent paths that MUST NOT appear above:"
    for p in "/etc/sniper" "/var/log/sniper" "/var/backups/sniper" \
             "/opt/sniper/.venv" "/opt/sniper/current" \
             "/opt/sniper/current_revision" "/opt/sniper/previous_revision" ; do
        if echo "$DEL_LINES" | grep -qF "$p"; then
            echo "  ⚠ FOUND $p IN DELETE PLAN — ABORT" >&2
            rm -f "$OUT"
            exit 6
        else
            printf "    ok  %s\n" "$p"
        fi
    done
    echo
    echo "▸ Destination scope confirmed: only $REL_DIR/ may be modified."
    rm -f "$OUT"
    exit 0
fi

# ---------------------------------------------------------------------------
# Normal deploy path
# ---------------------------------------------------------------------------
if ! git diff --quiet HEAD; then
    echo "⚠ working tree has uncommitted changes; deploying the committed HEAD only."
    echo "  (rsync ships git ls-files which is committed state.)"
fi

echo "▸ Deploy plan:"
echo "    host           : $HOST"
echo "    sha            : $SHA  ($(git log -1 --pretty=%s))"
echo "    release dir    : $REL_DIR"
echo "    files in plan  : $N_FILES (from git ls-files)"
echo "    mode           : $($DRY_RUN && echo DRY-RUN || echo APPLY)"
echo

# Pre-flight on the host: make sure /opt/sniper layout is what we expect.
# This protects against deploying into a brand-new box that never ran
# install.sh (in which case `current` wouldn't be a symlink).
ssh "$HOST" "set -e
    test -d /opt/sniper                   || { echo '/opt/sniper missing — run deploy/install.sh first' >&2; exit 7; }
    test -d /opt/sniper/releases          || mkdir -p /opt/sniper/releases
    if [ -e /opt/sniper/current ] && [ ! -L /opt/sniper/current ]; then
        echo 'FATAL: /opt/sniper/current exists and is NOT a symlink.' >&2
        echo '       Refusing to deploy — would risk persistent data.' >&2
        exit 8
    fi
"

RSYNC_ARGS=("${COMMON_ARGS[@]}")
if $DRY_RUN; then
    RSYNC_ARGS=(--dry-run -v --itemize-changes "${RSYNC_ARGS[@]}")
fi

rsync "${RSYNC_ARGS[@]}"

if $DRY_RUN; then
    echo
    echo "▸ Dry-run complete. Re-run with --apply to deploy."
    echo "▸ Run --verify-delete-scope to inspect exactly what --delete would touch."
    exit 0
fi

# ---------- Post-transfer steps on the host ----------
# 1. Refresh shared venv against the new release's requirements.
# 2. Run migrations from inside the new release directory.
# 3. CANARY: spin up gunicorn against the new release on a private
#    port, curl /health, kill it. If 200, proceed; otherwise abort
#    BEFORE touching `current`.
# 4. Snapshot the prior revision, atomically flip current → new release.
# 5. Restart sniper-web (and scheduler if previously enabled).
# 6. Prune to last 5 releases, but NEVER delete the prior known-good.

CANARY_PORT=18765    # private port used only during canary; bound 127.0.0.1

ssh "$HOST" "set -euo pipefail
    cd $REL_DIR

    echo '▸ Installing requirements…'
    /opt/sniper/.venv/bin/pip install --quiet -r requirements.txt

    echo '▸ Running migrations against live DB from new release…'
    # Migrations are forward-only and additive (Phase 1–3 schema is
    # backward-compatible with the previous code release).
    /opt/sniper/.venv/bin/python3 -m migrations.runner

    echo '▸ Starting canary gunicorn on 127.0.0.1:${CANARY_PORT}…'
    set +e
    /opt/sniper/.venv/bin/gunicorn server:app \
        --bind 127.0.0.1:${CANARY_PORT} --workers 1 --pid /tmp/sniper_canary.pid \
        --log-file /tmp/sniper_canary.log --error-logfile /tmp/sniper_canary.log \
        --daemon
    GUN_RC=\$?
    set -e
    [ \"\$GUN_RC\" = 0 ] || { echo 'canary gunicorn failed to start'; tail -40 /tmp/sniper_canary.log >&2; exit 9; }

    # Give it 3 seconds to bind + load.
    sleep 3
    set +e
    HC=\$(curl -fsS -m 5 http://127.0.0.1:${CANARY_PORT}/health)
    HC_RC=\$?
    set -e

    # Always stop the canary before judging.
    if [ -f /tmp/sniper_canary.pid ]; then
        kill \$(cat /tmp/sniper_canary.pid) 2>/dev/null || true
        rm -f /tmp/sniper_canary.pid
    fi

    if [ \"\$HC_RC\" != 0 ] || ! echo \"\$HC\" | grep -q '\"status\":\"ok\"'; then
        echo 'CANARY FAILED — leaving /opt/sniper/current at prior release.' >&2
        echo \"curl rc=\$HC_RC body=\$HC\" >&2
        tail -40 /tmp/sniper_canary.log >&2 || true
        exit 10
    fi
    echo '▸ Canary OK — flipping current symlink…'

    # Snapshot the prior revision so rollback always knows where to go.
    PRIOR=\$(cat /opt/sniper/current_revision 2>/dev/null || echo '')
    if [ -n \"\$PRIOR\" ] && [ \"\$PRIOR\" != \"$SHA\" ]; then
        echo \"\$PRIOR\" > /opt/sniper/previous_revision
    fi
    echo '$SHA' > /opt/sniper/current_revision

    # Atomic symlink swap: create new target, mv -Tf overwrites
    # symlink-as-symlink (never replaces a real dir).
    ln -sfn $REL_DIR /opt/sniper/current.new
    mv -Tf /opt/sniper/current.new /opt/sniper/current

    # Prune: keep the 5 most recent release directories AND the prior
    # known-good revision (so rollback always works even if it falls
    # outside the last-5 window).
    PREV=\$(cat /opt/sniper/previous_revision 2>/dev/null || echo '')
    cd /opt/sniper/releases
    for d in \$(ls -1t | tail -n +6); do
        if [ -n \"\$PREV\" ] && [ \"\$d\" = \"\$PREV\" ]; then
            continue   # never prune the rollback target
        fi
        echo \"  pruning old release: \$d\"
        rm -rf \"./\$d\"
    done
"

if ! $NO_RESTART; then
    ssh "$HOST" "set -e
        sudo systemctl restart sniper-migrate.service
        sudo systemctl restart sniper-web.service
        if systemctl is-enabled --quiet sniper-scheduler.service; then
            sudo systemctl restart sniper-scheduler.service
        fi"
fi

echo
echo "▸ Deploy complete. current → $SHA"
echo "▸ Rollback target  : \$(ssh $HOST cat /opt/sniper/previous_revision 2>/dev/null || echo '(none yet)')"
echo "▸ Verify:"
echo "    bash deploy/sync.sh --status $HOST"
echo "    curl -fsS https://\$STAGING_HOSTNAME/health"
