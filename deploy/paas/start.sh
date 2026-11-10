#!/usr/bin/env bash
# deploy/paas/start.sh — run all of FlipFirst in ONE container.
#
# For managed hosts (Render, Railway, Fly.io) that run a single container
# per service. It does in order what the systemd units do on a VPS:
#
#   1. migrations   (python -m migrations.runner — once, before anything)
#   2. worker       (scores listings, delivers alerts)
#   3. scheduler    (polls sources; holds a DB lease so only one runs)
#   4. web          (gunicorn on $PORT)
#
# If the web server or the worker exits, the whole container exits so the
# host restarts it — nothing is left half-running. The scheduler is the
# exception while it waits: exit code 2 means another container still
# holds its lease (normal during a rolling deploy), so it retries.
set -uo pipefail

cd "$(dirname "$0")/../.."

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[start] DATABASE_URL is not set. The cloud deploy needs PostgreSQL;" >&2
  echo "[start] a container's disk is wiped on every restart." >&2
  exit 1
fi

# The public https address: used for secure cookies, invite links and
# Stripe return URLs. Render provides RENDER_EXTERNAL_URL; set
# BILLING_ORIGIN yourself once you add a custom domain.
if [[ -z "${BILLING_ORIGIN:-}" && -n "${RENDER_EXTERNAL_URL:-}" ]]; then
  export BILLING_ORIGIN="$RENDER_EXTERNAL_URL"
fi
echo "[start] public origin: ${BILLING_ORIGIN:-<not set>}"

echo "[start] applying migrations"
python -m migrations.runner || { echo "[start] migrations failed" >&2; exit 1; }

pids=()
stop() {
  echo "[start] stopping"
  kill -TERM "${pids[@]}" 2>/dev/null
  wait
  exit "${1:-0}"
}
trap 'stop 0' TERM INT

python -m jobs.worker --threads "${WORKER_THREADS:-2}" &
pids+=($!)

(
  child=
  trap '[[ -n $child ]] && kill -TERM $child 2>/dev/null; wait; exit 0' TERM
  while true; do
    python -m jobs.scheduler &
    child=$!
    wait $child
    code=$?
    child=
    if [[ $code -eq 2 ]]; then
      echo "[scheduler] another instance holds the lease; retrying in 30s"
      sleep 30 & wait $!
      continue
    fi
    exit $code
  done
) &
pids+=($!)

gunicorn server:app \
  --workers "${WEB_CONCURRENCY:-2}" \
  --bind "0.0.0.0:${PORT:-8765}" \
  --access-logfile - \
  --timeout 30 &
pids+=($!)

wait -n "${pids[@]}"
code=$?
echo "[start] a process exited (code $code); stopping the container so the host restarts it" >&2
stop $(( code == 0 ? 1 : code ))
