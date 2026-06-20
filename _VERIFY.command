#!/bin/bash
# One-shot hybrid-auth smoke test. Confirms the live sniper distinguishes
# owner (direct local) from external (tunneled) callers correctly.
cd "$(dirname "$0")" || exit 1
{
  echo "================================================="
  echo " SNIPER hybrid-auth verification — $(date)"
  echo "================================================="
  echo
  echo "[1] /health  (public, no auth, no DB)"
  curl -s http://127.0.0.1:8765/health
  echo
  echo
  echo "[2] /api/share_settings  (OWNER — direct local)"
  curl -s http://127.0.0.1:8765/api/share_settings
  echo
  echo
  echo "[3] /api/share_settings  (EXTERNAL — X-Forwarded-For: 1.2.3.4)"
  curl -s -H "X-Forwarded-For: 1.2.3.4" http://127.0.0.1:8765/api/share_settings
  echo
  echo
  echo "[4] /api/notify_settings (OWNER — full phone)"
  curl -s http://127.0.0.1:8765/api/notify_settings
  echo
  echo
  echo "[5] /api/notify_settings (EXTERNAL — phone should be masked)"
  curl -s -H "X-Forwarded-For: 1.2.3.4" http://127.0.0.1:8765/api/notify_settings
  echo
  echo
  echo "[6] POST /api/poll       (EXTERNAL — expect 401/403)"
  curl -s -o /dev/null -w "    -> HTTP %{http_code}\n" \
    -H "X-Forwarded-For: 1.2.3.4" -X POST http://127.0.0.1:8765/api/poll
  echo
  echo "[7] GET  /               (EXTERNAL public read — expect 200)"
  curl -s -o /dev/null -w "    -> HTTP %{http_code}\n" \
    -H "X-Forwarded-For: 1.2.3.4" http://127.0.0.1:8765/
  echo
  echo "[8] response headers     (CSP + frame + nosniff present?)"
  curl -sI http://127.0.0.1:8765/ | grep -iE "content-security|x-frame|x-content|referrer|permissions"
  echo
  echo "================================================="
  echo " DONE — output also saved to _verify_out.txt"
  echo "================================================="
} | tee _verify_out.txt
sleep 1
osascript -e 'tell application "Terminal" to close (every window whose name contains "_VERIFY")' &
exit 0
