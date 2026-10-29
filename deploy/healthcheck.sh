#!/bin/sh
# deploy/healthcheck.sh — run by sniper-healthcheck.service every 5 minutes.
#
# Two dead-man pings (e.g. Healthchecks.io); each is sent ONLY when its
# check passes, so a silent ping is what alerts you:
#   HEALTHCHECK_WEB_URL       web answers /health through Caddy/TLS
#   HEALTHCHECK_PIPELINE_URL  the pipeline works: database reachable,
#                             migrations applied, scheduler ticked in the
#                             last 15 min, no runnable job waiting > 10 min
#                             (python -m preflight --target internal)
# A web server that is up says nothing about a dead scheduler or worker.
set -u
rc=0
APP_DIR=${SNIPER_APP_DIR:-/opt/sniper/current}
PY=${SNIPER_PYTHON:-/opt/sniper/.venv/bin/python3}

# The public origin (install.sh writes BILLING_ORIGIN=https://<hostname>
# into /etc/sniper/env); Caddy has no certificate for a bare IP.
ORIGIN=${BILLING_ORIGIN:-https://${STAGING_HOSTNAME:-127.0.0.1}}
if curl -fsS -m 8 "${ORIGIN%/}/health" >/dev/null; then
    [ -n "${HEALTHCHECK_WEB_URL:-}" ] && curl -fsS -m 10 "$HEALTHCHECK_WEB_URL" >/dev/null
else
    echo "healthcheck: web /health FAILED"
    rc=1
fi

if out=$(cd "$APP_DIR" && "$PY" -m preflight --target internal 2>&1); then
    [ -n "${HEALTHCHECK_PIPELINE_URL:-}" ] && curl -fsS -m 10 "$HEALTHCHECK_PIPELINE_URL" >/dev/null
else
    # preflight never prints secret values (tests/test_preflight.py)
    echo "healthcheck: pipeline FAILED"
    echo "$out" | sed -n '/^READY FOR/,$p'
    rc=1
fi
exit $rc
