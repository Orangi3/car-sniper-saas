"""
notifications.py — outbound text alerts via macOS Messages.app.

Uses osascript to drive Messages.app — sends iMessage if the recipient has it,
otherwise SMS via the user's paired iPhone (Continuity / Messages-in-iCloud).

REQUIREMENTS:
  - User is on macOS (this is local-only by design)
  - Messages.app is signed in to the user's Apple ID
  - First call triggers a one-time Automation permission prompt:
      System Settings → Privacy & Security → Automation → Python → Messages

No Twilio, no API keys, no third-party services. Free.
"""
from __future__ import annotations

import re
import subprocess
import time
from typing import Optional


def normalize_phone(raw: str) -> str:
    """Convert any common US phone format to +1XXXXXXXXXX."""
    digits = re.sub(r"\D+", "", raw or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw.startswith("+"):
        return raw
    return f"+{digits}"


def _escape_for_applescript(s: str) -> str:
    """Escape backslashes and quotes for AppleScript string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send_imessage(phone: str, body: str, timeout_s: float = 8.0) -> tuple[bool, str]:
    """Send a Messages text. Returns (ok, detail).
    detail is empty on success or contains the error reason.
    """
    if not phone:
        return False, "no phone configured"
    e164 = normalize_phone(phone)
    body = (body or "").strip()
    if not body:
        return False, "empty body"
    if len(body) > 1500:
        body = body[:1497] + "..."

    script = f'''
on run
    set targetPhone to "{_escape_for_applescript(e164)}"
    set targetBody to "{_escape_for_applescript(body)}"
    tell application "Messages"
        try
            set svc to 1st service whose service type = iMessage
            set buddy to participant targetPhone of svc
            send targetBody to buddy
            return "ok"
        on error errMsg number errNum
            -- iMessage failed, try SMS via paired iPhone
            try
                set svcSMS to 1st service whose service type = SMS
                set buddySMS to participant targetPhone of svcSMS
                send targetBody to buddySMS
                return "ok-sms"
            on error errMsg2
                return "fail: " & errMsg & " | sms: " & errMsg2
            end try
        end try
    end tell
end run
'''
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout_s,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode == 0 and out.startswith("ok"):
            return True, out
        return False, err or out or "unknown osascript error"
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout_s}s"
    except FileNotFoundError:
        return False, "osascript not found (macOS only)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# Throttle — never send more than 1 text per N seconds per phone
_LAST_SENT: dict[str, float] = {}


def send_alert_text(phone: str, year, make, model, price, discount_pct, url,
                     min_seconds_between: int = 60) -> tuple[bool, str]:
    """Format a deal as an iMessage. Built-in throttling prevents spam."""
    now = time.time()
    last = _LAST_SENT.get(phone, 0)
    if now - last < min_seconds_between:
        return False, f"throttled (last text {int(now-last)}s ago)"
    car = f"{year or ''} {make or ''} {model or ''}".strip()
    body = (f"🎯 SNIPER — {car}\n"
            f"${int(price):,} ({discount_pct:.0f}% below comp)\n"
            f"{url}")
    ok, detail = send_imessage(phone, body)
    if ok:
        _LAST_SENT[phone] = now
    return ok, detail
