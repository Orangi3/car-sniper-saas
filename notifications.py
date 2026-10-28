"""
notifications.py — outbound deal alerts.

Channels (each turns on when its settings are present — use as many as you
like; every alert goes to all of them):

  ntfy        NTFY_TOPIC [, NTFY_SERVER, NTFY_TOKEN]   instant phone push, free
  Pushover    PUSHOVER_TOKEN + PUSHOVER_USER            phone push, one-time fee
  Twilio SMS  TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + TWILIO_FROM  (+ phone)
  Discord     DISCORD_WEBHOOK_URL                       team / crew channel
  Webhook     ALERT_WEBHOOK_URL                         JSON POST (Zapier, n8n…)
  iMessage    macOS only, needs a phone number          (original channel)

Alerts go through a background outbox: a send that would exceed a channel's
rate limit WAITS instead of being dropped (the old 60s throttle silently
threw deals away).

iMessage uses osascript to drive Messages.app — sends iMessage if the recipient has it,
otherwise SMS via the user's paired iPhone (Continuity / Messages-in-iCloud).

REQUIREMENTS:
  - User is on macOS (this is local-only by design)
  - Messages.app is signed in to the user's Apple ID
  - First call triggers a one-time Automation permission prompt:
      System Settings → Privacy & Security → Automation → Python → Messages

No Twilio, no API keys, no third-party services. Free.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from queue import Queue
from typing import Optional

import requests


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



# ---------- Multi-channel deal alerts -------------------------------------

def format_deal(deal: dict) -> tuple[str, str]:
    """(title, body) for one deal. Kept short — it has to read on a lock
    screen."""
    car = " ".join(str(x) for x in (deal.get("year"), deal.get("make"),
                                    deal.get("model")) if x)
    price = int(deal.get("price") or 0)
    disc = float(deal.get("discount_pct") or 0)
    basis = "sold comps" if deal.get("basis") == "verified" else "local est."
    title = f"🎯 {car} — ${price:,} ({disc:.0f}% under)"
    lines = [f"Comp ${float(deal.get('comp_avg') or 0):,.0f} ({basis}, n={deal.get('comp_n') or 0})"]
    if deal.get("est_profit") is not None:
        lines.append(f"Est. profit ${int(deal['est_profit']):,} after fees/recon")
    if deal.get("odometer"):
        lines.append(f"{int(deal['odometer']):,} mi")
    where = deal.get("location") or deal.get("state")
    lines.append(f"{deal.get('source', '')}{' · ' + where if where else ''}")
    lines.append(deal.get("url") or "")
    return title, "\n".join(lines)


def _send_ntfy(topic: str, title: str, body: str, url: str) -> tuple[bool, str]:
    # JSON publish (not headers) so emoji / non-ASCII titles survive
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    payload = {"topic": topic, "title": title, "message": body,
               "priority": 4, "tags": ["car", "moneybag"]}
    if url:
        payload["click"] = url
    headers = {}
    tok = os.environ.get("NTFY_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    r = requests.post(server, json=payload, headers=headers, timeout=8)
    return r.ok, f"ntfy {r.status_code}"


def _send_pushover(title: str, body: str, url: str) -> tuple[bool, str]:
    r = requests.post("https://api.pushover.net/1/messages.json", timeout=8, data={
        "token": os.environ["PUSHOVER_TOKEN"], "user": os.environ["PUSHOVER_USER"],
        "title": title[:250], "message": body[:1024], "url": url or "",
        "priority": 1})
    return r.ok, f"pushover {r.status_code}"


def _send_twilio(phone: str, title: str, body: str) -> tuple[bool, str]:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, os.environ["TWILIO_AUTH_TOKEN"]), timeout=8,
        data={"From": os.environ["TWILIO_FROM"], "To": normalize_phone(phone),
              "Body": f"{title}\n{body}"[:1500]})
    return r.ok, f"twilio {r.status_code}"


def _send_discord(webhook: str, title: str, body: str) -> tuple[bool, str]:
    r = requests.post(webhook, json={"content": f"**{title}**\n{body}"[:1900]},
                      timeout=8)
    return r.ok, f"discord {r.status_code}"


def _send_webhook(webhook: str, deal: dict, title: str, body: str) -> tuple[bool, str]:
    payload = {"title": title, "body": body,
               "deal": {k: v for k, v in deal.items()
                        if isinstance(v, (str, int, float, bool, type(None)))}}
    r = requests.post(webhook, json=payload, timeout=8)
    return r.ok, f"webhook {r.status_code}"


def channels_for(phone: Optional[str] = None, ntfy_topic: Optional[str] = None,
                 webhook: Optional[str] = None) -> list[str]:
    """Which channels a notify_deal() call with these args would use."""
    out = []
    if ntfy_topic or os.environ.get("NTFY_TOPIC"):
        out.append("ntfy")
    if os.environ.get("PUSHOVER_TOKEN") and os.environ.get("PUSHOVER_USER"):
        out.append("pushover")
    if phone and all(os.environ.get(k) for k in
                     ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")):
        out.append("twilio")
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        out.append("discord")
    if webhook or os.environ.get("ALERT_WEBHOOK_URL"):
        out.append("webhook")
    if phone and sys.platform == "darwin":
        out.append("imessage")
    return out


# Minimum spacing between sends, per channel. Waits, never drops.
_MIN_GAP_S = {"imessage": 5.0, "twilio": 1.0, "discord": 1.0}
_LAST_SEND_AT: dict[str, float] = {}
_OUTBOX: "Queue[tuple]" = Queue()
_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False
SENT_LOG: list[dict] = []          # last few results, for diagnostics


def _deliver(channel: str, deal: dict, phone, ntfy_topic, webhook) -> tuple[bool, str]:
    title, body = format_deal(deal)
    url = deal.get("url") or ""
    if channel == "ntfy":
        return _send_ntfy(ntfy_topic or os.environ["NTFY_TOPIC"], title, body, url)
    if channel == "pushover":
        return _send_pushover(title, body, url)
    if channel == "twilio":
        return _send_twilio(phone, title, body)
    if channel == "discord":
        return _send_discord(os.environ["DISCORD_WEBHOOK_URL"], title, body)
    if channel == "webhook":
        return _send_webhook(webhook or os.environ["ALERT_WEBHOOK_URL"], deal, title, body)
    if channel == "imessage":
        return send_imessage(phone, f"{title}\n{body}")
    return False, f"unknown channel {channel}"


def _outbox_loop() -> None:
    while True:
        channel, deal, phone, ntfy_topic, webhook = _OUTBOX.get()
        try:
            gap = _MIN_GAP_S.get(channel, 0.0)
            wait = _LAST_SEND_AT.get(channel, 0.0) + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            ok, detail = False, ""
            for attempt in range(3):
                try:
                    ok, detail = _deliver(channel, deal, phone, ntfy_topic, webhook)
                except Exception as e:
                    ok, detail = False, f"{type(e).__name__}: {e}"
                if ok:
                    break
                time.sleep(2 ** attempt)
            _LAST_SEND_AT[channel] = time.monotonic()
            SENT_LOG.append({"channel": channel, "ok": ok, "detail": detail,
                             "composite_id": deal.get("composite_id"),
                             "at": time.time()})
            del SENT_LOG[:-50]
            if not ok:
                print(f"[notify] {channel} {deal.get('composite_id')}: {detail}",
                      file=sys.stderr)
        finally:
            _OUTBOX.task_done()


def _ensure_worker() -> None:
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if not _WORKER_STARTED:
            threading.Thread(target=_outbox_loop, daemon=True,
                             name="notify-outbox").start()
            _WORKER_STARTED = True


def notify_deal(deal: dict, phone: Optional[str] = None,
                ntfy_topic: Optional[str] = None,
                webhook: Optional[str] = None) -> list[str]:
    """Queue a deal alert on every configured channel. Returns the channel
    names it was queued on (empty = nothing configured)."""
    chans = channels_for(phone, ntfy_topic, webhook)
    if not chans:
        return []
    _ensure_worker()
    for ch in chans:
        _OUTBOX.put((ch, dict(deal), phone, ntfy_topic, webhook))
    return chans


def send_test(phone: Optional[str] = None, ntfy_topic: Optional[str] = None,
              webhook: Optional[str] = None) -> dict[str, tuple[bool, str]]:
    """Synchronously send a sample alert on every configured channel and
    report each result — powers the dashboard's test button."""
    sample = {"year": 2015, "make": "Toyota", "model": "Tacoma", "price": 17500,
              "discount_pct": 31, "comp_avg": 25400, "comp_n": 12,
              "basis": "verified", "est_profit": 6400, "source": "test",
              "url": "https://example.com/sniper-test", "composite_id": "test"}
    out = {}
    for ch in channels_for(phone, ntfy_topic, webhook):
        try:
            out[ch] = _deliver(ch, sample, phone, ntfy_topic, webhook)
        except Exception as e:
            out[ch] = (False, f"{type(e).__name__}: {e}")
    return out


def user_channels(phone: Optional[str] = None, ntfy_topic: Optional[str] = None,
                  webhook: Optional[str] = None) -> list[str]:
    """Channels for ONE subscriber — only their own targets, never the
    owner's global ntfy/Pushover/Discord, and never iMessage (that sends
    from the owner's Apple ID)."""
    out = []
    if ntfy_topic:
        out.append("ntfy")
    if webhook:
        out.append("webhook")
    if phone and all(os.environ.get(k) for k in
                     ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")):
        out.append("twilio")
    return out


def notify_user_deal(deal: dict, phone: Optional[str] = None,
                     ntfy_topic: Optional[str] = None,
                     webhook: Optional[str] = None) -> list[str]:
    """Queue a saved-search match on a subscriber's own channels."""
    chans = user_channels(phone, ntfy_topic, webhook)
    if chans:
        _ensure_worker()
        for ch in chans:
            _OUTBOX.put((ch, dict(deal), phone, ntfy_topic, webhook))
    return chans
