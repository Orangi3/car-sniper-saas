#!/usr/bin/env bash
# deploy/restore.sh — OFF-HOST restore drill for the encrypted backups
# produced by deploy/backup.sh on the staging VPS.
#
# THIS SCRIPT IS NEVER RUN ON THE STAGING VPS. Run it on your Mac (or
# a dedicated recovery host) that holds the age PRIVATE key. The
# staging VPS only has the PUBLIC recipient and therefore cannot decrypt
# anything it produces.
#
# Hard rule: the age private key file
#   * lives ONLY in your password manager / a dedicated encrypted volume
#     on this Mac (suggested path: ~/.config/sniper-staging.age.key,
#     macOS file permission 0600, never synced to iCloud Drive)
#   * is NEVER committed, copied to the VPS, included in /etc/sniper/env,
#     written to a log, screenshotted, or pasted into chat
#   * if it leaks: revoke it (it can't be revoked — instead generate a
#     new keypair, update AGE_RECIPIENT on the VPS, drop old backup
#     objects from B2 since they're now bound to the leaked key)
#
# Usage (from your Mac, sniper repo root):
#   bash deploy/restore.sh                 # newest object, default temp DB
#   bash deploy/restore.sh sniper-….age    # specific object name
#   bash deploy/restore.sh --target-db NAME # name the verification DB explicitly
#   bash deploy/restore.sh --keep          # don't drop the verification DB at end
#
# Required local env (export in your shell or write to a *separate*
# off-host file you source — NOT the VPS env file):
#   B2_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET, B2_ENDPOINT
#       (B2 keys with READ scope; the VPS itself uses a write-only key.
#        Create a second B2 application key in the B2 UI with read-only
#        permission on the staging-sniper bucket and put those values
#        into your local shell.)
#   AGE_KEY_FILE=~/.config/sniper-staging.age.key   (DEFAULT)
#       — the private-key file. Anyone with read access to this file
#       can decrypt every staging backup. Treat it like an SSH key.
#   VERIFICATION_DATABASE_URL=postgresql://localhost/sniper_restore   (DEFAULT)
#       — a local Postgres URL. The verification DB is created fresh
#       and dropped at the end (unless --keep). NEVER point this at the
#       staging or live DB.

set -euo pipefail

# ---------- Refuse to run on the VPS itself ----------
# Quick guard: the staging VPS is a Linux system user 'sniper'. If we're
# running as that user on a Linux box that has /opt/sniper, we are very
# likely on the VPS — bail. Restore drills happen off-host. Period.
if [[ "$(uname -s 2>/dev/null)" == "Linux" && -d /opt/sniper && "$(id -un)" == "sniper" ]]; then
    echo "FATAL: deploy/restore.sh must NOT run on the staging VPS." >&2
    echo "       Run this on your Mac (or a dedicated recovery host)" >&2
    echo "       that holds the age private key. The VPS doesn't have" >&2
    echo "       the private key by design." >&2
    exit 2
fi

# ---------- Args ----------
NAMED=""
TARGET_DB_DEFAULT="sniper_restore_$(date -u +%Y%m%d_%H%M%S)"
TARGET_DB="$TARGET_DB_DEFAULT"
KEEP=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target-db) TARGET_DB="$2"; shift 2 ;;
        --keep)      KEEP=true; shift ;;
        -h|--help)
            sed -n '2,40p' "$0"
            exit 0 ;;
        *) NAMED="$1"; shift ;;
    esac
done

: "${B2_KEY_ID:?B2_KEY_ID required (read-only B2 key, off-host only)}"
: "${B2_APPLICATION_KEY:?B2_APPLICATION_KEY required}"
: "${B2_BUCKET:?B2_BUCKET required}"
: "${B2_ENDPOINT:?B2_ENDPOINT required}"
AGE_KEY_FILE="${AGE_KEY_FILE:-$HOME/.config/sniper-staging.age.key}"
VERIFICATION_DATABASE_URL="${VERIFICATION_DATABASE_URL:-postgresql://localhost/${TARGET_DB}}"

if [[ ! -r "$AGE_KEY_FILE" ]]; then
    echo "FATAL: cannot read AGE_KEY_FILE='$AGE_KEY_FILE'" >&2
    echo "       Generate one with: age-keygen -o '$AGE_KEY_FILE' && chmod 600 '$AGE_KEY_FILE'" >&2
    echo "       Then put its PUBLIC line (age1…) into the VPS env as AGE_RECIPIENT." >&2
    exit 1
fi
# Refuse a private key that's readable by group or other. The mode octet
# we care about is the last 3 chars of `stat`'s output: owner-group-other.
# We accept ONLY when group AND other bits are both 0 (i.e. 600 / 400 /
# 500 / 700). Anything else means leakage risk → bail.
PERM_OCT=$(stat -f %A "$AGE_KEY_FILE" 2>/dev/null || stat -c %a "$AGE_KEY_FILE")
PERM_OCT="${PERM_OCT: -3}"     # last 3 chars in case stat returned a file-type prefix
GROUP_BITS="${PERM_OCT:1:1}"
OTHER_BITS="${PERM_OCT:2:1}"
if [[ "$GROUP_BITS" != "0" || "$OTHER_BITS" != "0" ]]; then
    echo "FATAL: AGE_KEY_FILE permissions are too open (mode=$PERM_OCT)." >&2
    echo "       chmod 600 '$AGE_KEY_FILE' and try again." >&2
    exit 1
fi

# Verify the verification DB URL isn't pointing at production / staging.
case "$VERIFICATION_DATABASE_URL" in
    *@127.0.0.1*|*@localhost*|postgresql:///*|postgresql://localhost*)
        : ;;
    *)
        echo "FATAL: VERIFICATION_DATABASE_URL must point at a LOCAL Postgres." >&2
        echo "       Refusing to restore into a remote DB. Got: $VERIFICATION_DATABASE_URL" >&2
        exit 1 ;;
esac

# ---------- Pick the backup object ----------
echo "▸ Listing B2 bucket s3://${B2_BUCKET}/ for newest sniper-*.age"
if [[ -z "$NAMED" ]]; then
    NAMED=$(AWS_ACCESS_KEY_ID="$B2_KEY_ID" \
            AWS_SECRET_ACCESS_KEY="$B2_APPLICATION_KEY" \
            aws --endpoint-url "$B2_ENDPOINT" \
                s3 ls "s3://${B2_BUCKET}/" \
            | awk '/sniper-.*\.sql\.zst\.age$/ {print $4}' \
            | sort | tail -n 1)
fi
[[ -n "$NAMED" ]] || { echo "no backups found in s3://${B2_BUCKET}"; exit 1; }
echo "▸ Selected backup object: $NAMED"
echo "▸ Verification DB:        $VERIFICATION_DATABASE_URL"

WORK=$(mktemp -d -t sniper_restore.XXXXXX)
trap 'rm -rf "$WORK"' EXIT

# 1. Download encrypted blob
echo "▸ Downloading encrypted blob…"
AWS_ACCESS_KEY_ID="$B2_KEY_ID" \
AWS_SECRET_ACCESS_KEY="$B2_APPLICATION_KEY" \
aws --endpoint-url "$B2_ENDPOINT" \
    s3 cp "s3://${B2_BUCKET}/${NAMED}" "$WORK/${NAMED}" --no-progress

# 2. Decrypt LOCALLY with the off-host private key, then decompress.
echo "▸ Decrypting (off-host) + decompressing…"
age -d -i "$AGE_KEY_FILE" "$WORK/${NAMED}" \
    | zstd -d --stdout > "$WORK/dump.bin"
DUMP_SIZE=$(wc -c < "$WORK/dump.bin")
DUMP_SHA=$(shasum -a 256 "$WORK/dump.bin" | awk '{print $1}')
echo "▸ Decrypted dump: ${DUMP_SIZE} bytes  sha256=${DUMP_SHA:0:16}…"

# 3. Create the verification DB fresh.
echo "▸ Creating verification DB ${TARGET_DB}"
DB_HOST=$(python3 -c "
import os, urllib.parse as up
u = up.urlparse('${VERIFICATION_DATABASE_URL}')
print(u.hostname or 'localhost')
")
psql -h "$DB_HOST" -d postgres -c "DROP DATABASE IF EXISTS ${TARGET_DB};" >/dev/null
psql -h "$DB_HOST" -d postgres -c \
    "CREATE DATABASE ${TARGET_DB} TEMPLATE template0 ENCODING 'UTF8';" >/dev/null

# 4. Restore.
echo "▸ Restoring into ${TARGET_DB}…"
pg_restore --no-owner --no-privileges --dbname "$VERIFICATION_DATABASE_URL" \
    "$WORK/dump.bin"

# 5. Validate: run migrations against the restored DB. Expect: 0 new.
echo "▸ Running migrations against restored DB (expect 0 new)…"
DATABASE_URL="$VERIFICATION_DATABASE_URL" \
python3 -m migrations.runner

# 6. Row-count sanity for the tables the app cares about.
echo "▸ Row counts in restored DB:"
for tbl in users sessions subscriptions stripe_webhook_events \
          billing_anomalies billing_reconciliation_runs listings; do
    n=$(psql -h "$DB_HOST" -d "$TARGET_DB" -tAc "SELECT COUNT(*) FROM $tbl" 2>/dev/null || echo "n/a")
    printf "    %-32s %s\n" "$tbl" "$n"
done

echo
echo "=========================================================="
echo "  ✓ Restore drill completed off-host."
echo "  Object  : ${NAMED}"
echo "  Size    : ${DUMP_SIZE} bytes"
echo "  SHA256  : ${DUMP_SHA:0:16}…"
echo "  DB      : ${TARGET_DB}"
if $KEEP; then
    echo "  --keep set — verification DB left in place."
    echo "  Drop when done:  psql -h $DB_HOST -d postgres -c 'DROP DATABASE ${TARGET_DB};'"
else
    echo "▸ Dropping verification DB…"
    psql -h "$DB_HOST" -d postgres -c "DROP DATABASE ${TARGET_DB};" >/dev/null
    echo "  Dropped."
fi
echo "=========================================================="
echo "  Record this drill in STAGING_RUNBOOK.md → Restore drill log:"
echo "    date     : $(date -u +%FT%TZ)"
echo "    object   : ${NAMED}"
echo "    size     : ${DUMP_SIZE}"
echo "    sha256   : ${DUMP_SHA:0:16}…"
echo "=========================================================="
