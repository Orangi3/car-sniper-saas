#!/usr/bin/env bash
# deploy/backup.sh — encrypted, off-host Postgres backup.
# Runs from the sniper-backup.timer on the staging host.
#
# Pipeline:
#   pg_dump → zstd → age (encrypt to PUBLIC recipient AGE_RECIPIENT) → B2
#
# Why age public-key + B2:
#   * The host has the PUBLIC recipient only. It can ENCRYPT backups but
#     CANNOT decrypt them. A breach of the host doesn't compromise
#     historical backups.
#   * The PRIVATE key lives ONLY on the operator's Mac (or a dedicated
#     recovery box). It NEVER touches:
#       - this VPS
#       - any environment file
#       - any logfile
#       - source control
#       - chat / screenshots
#     If you find a *.age.key file on this host, that's an incident —
#     wipe it, generate a new keypair, rotate AGE_RECIPIENT, drop and
#     re-upload backups.
#   * Restores happen off-host via deploy/restore.sh run from your Mac.
#   * B2 is cheap, S3-compatible, and accessed with a write-only key
#     scoped to the staging-sniper bucket.
#
# Required env (loaded by systemd from /etc/sniper/env):
#   DATABASE_URL=postgresql://sniper:…@127.0.0.1:5432/sniper
#   B2_BUCKET, B2_KEY_ID, B2_APPLICATION_KEY, B2_ENDPOINT
#   AGE_RECIPIENT=age1…   (PUBLIC recipient — safe to live in env)

set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL required}"
: "${B2_BUCKET:?B2_BUCKET required}"
: "${B2_KEY_ID:?B2_KEY_ID required}"
: "${B2_APPLICATION_KEY:?B2_APPLICATION_KEY required}"
: "${B2_ENDPOINT:?B2_ENDPOINT required}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT required (PUBLIC age recipient, age1…)}"
# Keys and signed requests never cross the network in clear text: https://
# only, except a loopback stand-in used in tests (as the mailer allows SMTP).
case "$B2_ENDPOINT" in
    https://*|http://127.0.0.1|http://127.0.0.1[:/]*|http://localhost|http://localhost[:/]*|http://\[::1\]|http://\[::1\][:/]*) ;;
    *) echo "FATAL: B2_ENDPOINT must start with https://" >&2; exit 2 ;;
esac
# The signing region is the endpoint's (s3.<region>.backblazeb2.com), so a
# bucket in any B2 region works with only B2_ENDPOINT set.
B2_REGION=$(sed -nE 's#^https?://s3\.([a-z0-9-]+)\.backblazeb2\.com/?$#\1#p' <<<"$B2_ENDPOINT")
B2_REGION=${B2_REGION:-us-east-005}

# Defense in depth — refuse to run if the AGE_RECIPIENT value smells
# like a private key. age private keys start with "AGE-SECRET-KEY-".
# Stops a copy-paste mistake from blowing away encryption.
case "$AGE_RECIPIENT" in
    AGE-SECRET-KEY-*)
        echo "FATAL: AGE_RECIPIENT looks like a PRIVATE age key." >&2
        echo "       Only the PUBLIC recipient (age1…) belongs on this host." >&2
        echo "       Treat this as an incident — see deploy/backup.sh header." >&2
        exit 2 ;;
    age1*) : ;;   # ok
    *)
        echo "FATAL: AGE_RECIPIENT is not a valid age public recipient." >&2
        exit 2 ;;
esac

# Defense in depth #2 — if any private-key-looking file exists in the
# usual mistake locations, fail loudly. We don't try to read or delete
# it (that would be too clever); we just refuse to back up until an
# operator audits.
for f in /etc/sniper/backup_age.key /etc/sniper/age.key \
         /opt/sniper/backup_age.key /opt/sniper/.age.key \
         /root/.config/age.key /home/sniper/.age.key ; do
    if [[ -e "$f" ]]; then
        echo "FATAL: private-key-shaped file present on host: $f" >&2
        echo "       The private key MUST NOT live on this VPS. Remove it," >&2
        echo "       rotate the keypair, update AGE_RECIPIENT, and re-run." >&2
        exit 2
    fi
done

# Every tool in the pipeline must exist BEFORE we start: a missing one
# would otherwise leave a truncated, unencrypted-looking file behind.
for tool in pg_dump zstd age aws sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "FATAL: $tool not found — re-run deploy/install.sh" >&2; exit 3; }
done

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="/var/backups/sniper/sniper-${STAMP}.sql.zst.age"
LOG="/var/log/sniper/backup-${STAMP}.log"

mkdir -p "$(dirname "$OUT")" "$(dirname "$LOG")"

# Strip credentials out of any log output: never print DATABASE_URL.
# pg_dump reads it from the environment directly.
echo "▸ $(date -u +%FT%TZ) starting backup → ${OUT}" | tee -a "$LOG"

# Streaming pipeline: pg_dump → zstd → age. Each stage is bounded RAM.
pg_dump --no-owner --no-privileges --format=custom \
        "$DATABASE_URL" 2>>"$LOG" \
    | zstd -T0 -19 --stdout 2>>"$LOG" \
    | age -r "$AGE_RECIPIENT" -o "$OUT" 2>>"$LOG"

SIZE=$(stat -c%s "$OUT")
SHA=$(sha256sum "$OUT" | awk '{print $1}')
echo "▸ $(date -u +%FT%TZ) wrote ${SIZE} bytes, sha256=${SHA}" | tee -a "$LOG"

# Upload via the AWS CLI (s3 compatible — works with B2 endpoint).
# AWS_* env names are what aws-cli expects; we set them from the B2_* env
# locally so the rest of the host never sees AWS-named credentials.
AWS_ACCESS_KEY_ID="$B2_KEY_ID" \
AWS_SECRET_ACCESS_KEY="$B2_APPLICATION_KEY" \
AWS_DEFAULT_REGION="$B2_REGION" \
aws --endpoint-url "$B2_ENDPOINT" \
    s3 cp "$OUT" "s3://${B2_BUCKET}/$(basename "$OUT")" \
    --no-progress 2>>"$LOG"

echo "▸ $(date -u +%FT%TZ) uploaded to B2 ${B2_BUCKET}/$(basename "$OUT")" | tee -a "$LOG"

# Local retention: keep the last 3 nights on-disk for fast restore drills.
find /var/backups/sniper -type f -name 'sniper-*.sql.zst.age' \
    -mtime +3 -print -delete | tee -a "$LOG"

# Healthchecks.io ping is fired by the systemd unit's ExecStartPost,
# not from this script — that way a failure in this script causes a
# DEAD-MAN (timer expires without a ping) instead of a silent fail.
echo "▸ $(date -u +%FT%TZ) backup OK" | tee -a "$LOG"
