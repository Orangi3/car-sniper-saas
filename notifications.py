"""
notifications.py — outbound deal alerts, delivered by durable jobs.

Channels (each active when its settings exist):

  ntfy        NTFY_TOPIC [, NTFY_SERVER, NTFY_TOKEN]   instant phone push, free
  Pushover    PUSHOVER_TOKEN + PUSHOVER_USER            phone push
  Twilio SMS  TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + TWILIO_FROM  (+ phone)
  Discord     DISCORD_WEBHOOK_URL                       crew channel
  Webhook     ALERT_WEBHOOK_URL / per-user webhook      JSON POST
  iMessage    macOS only, owner phone                   original channel

Flow
  enqueue_owner()/enqueue_user() insert one `notify` job per channel in the
  SAME transaction as the alert / match that caused it. jobs/worker.py runs
  deliver_job(). Jobs hold the channel and the audience (owner or user id),
  never the destination: the user's current topic / webhook / phone is read
  at send time, so a removed channel or downgraded plan stops delivery and
  one user's job can never be sent to another user's destination.

Delivery semantics
  Enqueue is effectively-once (UNIQUE dedupe key per event × audience ×
  channel). Delivery is at-least-once: if a worker dies after the provider
  accepted a message but before the job is marked done, the retry sends it
  again. None of these providers offers an idempotency key we can use, so a
  rare duplicate push is possible; a lost alert is not.

User webhooks go through safe_http (connect-time SSRF checks, no redirects,
size limits). Payloads carry only public listing facts — never a user's
email, phone, id or other channels.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

import jobqueue
import safe_http

NOTIFY_MAX_ATTEMPTS = 6
# Per-process minimum spacing between sends on rate-sensitive channels.
_MIN_GAP_S = {"imessage": 5.0, "twilio": 1.0, "discord": 1.0}
_LAST_SEND_AT: dict[str, float] = {}

# Only these deal fields ever leave the server.
PUBLIC_DEAL_FIELDS = (
    "composite_id", "title", "year", "make", "model", "price", "previous_price",
    "odometer", "location", "state", "source", "url", "discount_pct",
    "comp_avg", "comp_n", "basis", "evidence_class", "confidence",
    "value_low", "value_high", "est_profit", "event",
)


def public_deal(deal: dict) -> dict:
    return {k: deal.get(k) for k in PUBLIC_DEAL_FIELDS if deal.get(k) is not None}


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



# ---------- formatting ----------------------------------------------------

def format_deal(deal: dict) -> tuple[str, str]:
    """(title, body) for one deal — short enough for a lock screen."""
    car = " ".join(str(x) for x in (deal.get("year"), deal.get("make"),
                                    deal.get("model")) if x)
    price = int(deal.get("price") or 0)
    disc = float(deal.get("discount_pct") or 0)
    if deal.get("event") == "price_drop" and deal.get("previous_price"):
        title = (f"📉 {car} dropped ${int(deal['previous_price']):,} → ${price:,} "
                 f"({disc:.0f}% under)")
    else:
        title = f"🎯 {car} — ${price:,} ({disc:.0f}% under)"
    ev = deal.get("evidence_class") or (
        "confirmed_sales" if deal.get("basis") == "verified" else "asking_comparables")
    label = {"confirmed_sales": "sold comps", "asking_comparables": "local asking prices",
             "historical_incomplete": "older/partial sales",
             "model_estimate": "model estimate"}.get(ev, ev)
    lo, hi = deal.get("value_low"), deal.get("value_high")
    if lo and hi:
        value = f"Value ${float(lo):,.0f}–${float(hi):,.0f}"
    else:
        value = f"Value ${float(deal.get('comp_avg') or 0):,.0f}"
    conf = f", {deal['confidence']} confidence" if deal.get("confidence") else ""
    lines = [f"{value} ({label}, n={deal.get('comp_n') or 0}{conf})"]
    if deal.get("est_profit") is not None:
        lines.append(f"Est. profit ~${int(deal['est_profit']):,} (estimate, not guaranteed)")
    if deal.get("odometer"):
        lines.append(f"{int(deal['odometer']):,} mi")
    where = deal.get("location") or deal.get("state")
    lines.append(f"{deal.get('source', '')}{' · ' + where if where else ''}")
    lines.append(deal.get("url") or "")
    return title, "\n".join(lines)


# ---------- provider senders (raise on failure) ---------------------------

def _check_response(r: requests.Response, name: str) -> str:
    if 200 <= r.status_code < 300:
        return f"{name} {r.status_code}"
    retryable = r.status_code == 429 or r.status_code >= 500
    raise safe_http.DeliveryError(f"{name} HTTP {r.status_code}",
                                  retryable=retryable, status=r.status_code)


def _post(url: str, **kw) -> requests.Response:
    try:
        return requests.post(url, timeout=8, allow_redirects=False, **kw)
    except requests.RequestException as e:
        raise safe_http.DeliveryError(f"network error: {type(e).__name__}",
                                      retryable=True) from None


def _send_ntfy(topic: str, title: str, body: str, url: str) -> str:
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    payload = {"topic": topic, "title": title, "message": body,
               "priority": 4, "tags": ["car", "moneybag"]}
    if url:
        payload["click"] = url
    headers = {}
    tok = os.environ.get("NTFY_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return _check_response(_post(server, json=payload, headers=headers), "ntfy")


def _send_pushover(title: str, body: str, url: str) -> str:
    return _check_response(_post(
        "https://api.pushover.net/1/messages.json", data={
            "token": os.environ["PUSHOVER_TOKEN"], "user": os.environ["PUSHOVER_USER"],
            "title": title[:250], "message": body[:1024], "url": url or "",
            "priority": 1}), "pushover")


def _send_twilio(phone: str, title: str, body: str) -> str:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    return _check_response(_post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, os.environ["TWILIO_AUTH_TOKEN"]),
        data={"From": os.environ["TWILIO_FROM"], "To": normalize_phone(phone),
              "Body": f"{title}\n{body}"[:1500]}), "twilio")


def _send_discord(webhook: str, title: str, body: str) -> str:
    safe_http.post_json(webhook, {"content": f"**{title}**\n{body}"[:1900]})
    return "discord ok"


def _send_webhook(webhook: str, deal: dict, title: str, body: str) -> str:
    safe_http.post_json(webhook, {"title": title, "body": body,
                                  "deal": public_deal(deal)})
    return "webhook ok"


def _twilio_ready() -> bool:
    return all(os.environ.get(k) for k in
               ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"))


# ---------- channel selection ---------------------------------------------

def channels_for(phone: Optional[str] = None) -> list[str]:
    """Owner channels currently configured on this server."""
    out = []
    if os.environ.get("NTFY_TOPIC"):
        out.append("ntfy")
    if os.environ.get("PUSHOVER_TOKEN") and os.environ.get("PUSHOVER_USER"):
        out.append("pushover")
    if phone and _twilio_ready():
        out.append("twilio")
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        out.append("discord")
    if os.environ.get("ALERT_WEBHOOK_URL"):
        out.append("webhook")
    if phone and sys.platform == "darwin":
        out.append("imessage")
    return out


def user_channels(phone: Optional[str] = None, ntfy_topic: Optional[str] = None,
                  webhook: Optional[str] = None) -> list[str]:
    """A subscriber's own channels — never the owner's global channels and
    never iMessage (that sends from the owner's Apple ID)."""
    out = []
    if ntfy_topic:
        out.append("ntfy")
    if webhook:
        out.append("webhook")
    if phone and _twilio_ready():
        out.append("twilio")
    return out


def _deliver(channel: str, deal: dict, *, phone=None, ntfy_topic=None,
             webhook=None) -> str:
    """Send one message now. Returns a detail string; raises
    safe_http.DeliveryError / UnsafeDestination on failure."""
    title, body = format_deal(deal)
    url = deal.get("url") or ""
    gap = _MIN_GAP_S.get(channel, 0.0)
    wait = _LAST_SEND_AT.get(channel, 0.0) + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        if channel == "ntfy":
            return _send_ntfy(ntfy_topic, title, body, url)
        if channel == "pushover":
            return _send_pushover(title, body, url)
        if channel == "twilio":
            return _send_twilio(phone, title, body)
        if channel == "discord":
            return _send_discord(webhook, title, body)
        if channel == "webhook":
            return _send_webhook(webhook, deal, title, body)
        if channel == "imessage":
            ok, detail = send_imessage(phone, f"{title}\n{body}")
            if not ok:
                raise safe_http.DeliveryError(f"imessage: {detail}", retryable=True)
            return detail
        raise safe_http.DeliveryError(f"unknown channel {channel}", retryable=False)
    finally:
        _LAST_SEND_AT[channel] = time.monotonic()


# ---------- enqueue (inside the caller's transaction) ---------------------

def _quiet_until(now: Optional[datetime] = None) -> Optional[datetime]:
    """If owner quiet hours are active, when they end (UTC); else None."""
    try:
        from config import CONFIG
    except Exception:
        return None
    qs, qe = CONFIG.get("quiet_start_hour"), CONFIG.get("quiet_end_hour")
    if qs is None or qe is None or qs == qe:
        return None
    local = (now or datetime.now(timezone.utc)).astimezone()
    h = local.hour
    active = (qs <= h < qe) if qs < qe else (h >= qs or h < qe)
    if not active:
        return None
    end = local.replace(hour=int(qe), minute=0, second=0, microsecond=0)
    if end <= local:
        end += timedelta(days=1)
    return end.astimezone(timezone.utc)


def enqueue_owner(conn, deal: dict, event_key: str,
                  phone: Optional[str] = None) -> list[str]:
    """Queue the owner's alert on every configured channel. During quiet
    hours the jobs are scheduled for when quiet hours end (not dropped)."""
    chans = channels_for(phone)
    run_after = _quiet_until()
    for ch in chans:
        jobqueue.enqueue(conn, "notify", f"notify:{event_key}:owner:{ch}",
                         {"audience": "owner", "channel": ch, "deal": public_deal(deal)},
                         priority=10, max_attempts=NOTIFY_MAX_ATTEMPTS,
                         run_after=run_after)
    return chans


def enqueue_user(conn, deal: dict, event_key: str, user_id: int,
                 channels: list[str]) -> list[str]:
    queued = []
    for ch in channels:
        jid = jobqueue.enqueue(conn, "notify", f"notify:{event_key}:u{user_id}:{ch}",
                               {"audience": "user", "user_id": user_id,
                                "channel": ch, "deal": public_deal(deal)},
                               priority=20, max_attempts=NOTIFY_MAX_ATTEMPTS,
                               user_id=user_id)
        if jid:
            queued.append(ch)
    return queued


# ---------- job handler ---------------------------------------------------

def _user_destination(user_id: int) -> Optional[dict]:
    import db as _db
    with _db.session() as conn:
        r = conn.execute(
            "SELECT plan, ntfy_topic, alert_webhook, notify_phone FROM users "
            "WHERE id = ?", (user_id,)).fetchone()
    return dict(r) if r else None


_USAGE_KIND = {"twilio": "sms", "ntfy": "push", "pushover": "push", "imessage": "push",
               "webhook": "webhook", "discord": "webhook"}


def _record_usage(user_id: Optional[int], channel: str) -> None:
    """Meter a successful send (feeds SMS caps and /api/admin/usage).
    Never fails the delivery."""
    try:
        import db as _db
        import plans
        kind = _USAGE_KIND.get(channel, channel)
        with _db.session() as conn:
            conn.execute(
                "INSERT INTO usage_events(user_id, kind, units, cost_cents, created_at) "
                "VALUES (?,?,?,?,?)",
                (user_id, kind, 1, plans.UNIT_COST_CENTS.get(kind, 0.0),
                 datetime.now(timezone.utc).isoformat()))
    except Exception as e:
        print(f"[usage] {e}", file=sys.stderr)


def deliver_job(job: dict) -> dict:
    """jobs/worker.py handler for kind='notify'."""
    p = job["payload"]
    ch, deal = p.get("channel"), p.get("deal") or {}
    kw: dict = {}
    if p.get("audience") == "user":
        dest = _user_destination(int(p["user_id"]))
        if dest is None:
            return {"skipped": "user deleted"}
        try:
            import plans
            if not plans.can_receive_alerts(dest.get("plan")):
                return {"skipped": f"plan '{dest.get('plan')}' not entitled"}
        except ImportError:
            pass
        kw = {"ntfy_topic": dest.get("ntfy_topic"), "webhook": dest.get("alert_webhook"),
              "phone": dest.get("notify_phone")}
        if ch not in user_channels(kw["phone"], kw["ntfy_topic"], kw["webhook"]):
            return {"skipped": f"channel {ch} no longer configured"}
    else:
        from config import CONFIG
        phone = CONFIG.get("notify_phone")
        if ch not in channels_for(phone):
            return {"skipped": f"owner channel {ch} no longer configured"}
        kw = {"phone": phone, "ntfy_topic": os.environ.get("NTFY_TOPIC"),
              "webhook": (os.environ.get("DISCORD_WEBHOOK_URL") if ch == "discord"
                          else os.environ.get("ALERT_WEBHOOK_URL"))}
    try:
        detail = _deliver(ch, deal, **kw)
        _record_usage(p.get("user_id") if p.get("audience") == "user" else None, ch)
    except safe_http.UnsafeDestination as e:
        raise jobqueue.PermanentError(f"unsafe destination: {e}") from None
    except safe_http.DeliveryError as e:
        if not e.retryable:
            raise jobqueue.PermanentError(str(e)) from None
        raise
    return {"sent": detail}


# ---------- synchronous test sends (dashboard buttons) --------------------

_SAMPLE = {"year": 2015, "make": "Toyota", "model": "Tacoma", "price": 17500,
           "discount_pct": 31, "comp_avg": 25400, "comp_n": 12,
           "evidence_class": "confirmed_sales", "est_profit": 6400,
           "source": "test", "url": "https://example.com/sniper-test",
           "composite_id": "test"}


def _try(ch, **kw) -> tuple[bool, str]:
    try:
        return True, _deliver(ch, _SAMPLE, **kw)
    except (safe_http.DeliveryError, safe_http.UnsafeDestination) as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def send_test(phone: Optional[str] = None) -> dict[str, tuple[bool, str]]:
    kw = {"phone": phone, "ntfy_topic": os.environ.get("NTFY_TOPIC")}
    out = {}
    for ch in channels_for(phone):
        hook = (os.environ.get("DISCORD_WEBHOOK_URL") if ch == "discord"
                else os.environ.get("ALERT_WEBHOOK_URL"))
        out[ch] = _try(ch, webhook=hook, **kw)
    return out


def send_user_test(phone=None, ntfy_topic=None, webhook=None) -> dict[str, tuple[bool, str]]:
    return {ch: _try(ch, phone=phone, ntfy_topic=ntfy_topic, webhook=webhook)
            for ch in user_channels(phone, ntfy_topic, webhook)}
