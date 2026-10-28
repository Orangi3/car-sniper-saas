"""
saved_search_alerts.py — route each new deal alert to the users whose saved
searches match it.

Called by sniper._dispatch_alert() right after an alert row is written. For
every saved search with notify_enabled:
  1. test the deal against the search's filter (same keys the dashboard's
     /api/listings filters use, plus min_discount_pct / min_profit),
  2. record it in that user's alert tray (user_alerts),
  3. queue a push on that user's own channels (ntfy topic / webhook / SMS).

A user with several matching searches gets ONE notification per deal.
"""
from __future__ import annotations

import json
from typing import Optional


def _csv(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip().lower() for x in v if str(x).strip()]
    return [x.strip().lower() for x in str(v).split(",") if x.strip()]


def _int(v) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def matches(f: dict, deal: dict) -> bool:
    """True if `deal` passes saved-search filter `f`. Unknown keys are
    ignored so older/newer dashboard filters never break matching."""
    def flag(k):
        return str(f.get(k, "")).lower() in ("1", "true", "yes")

    srcs = _csv(f.get("source"))
    if srcs and str(deal.get("source", "")).lower() not in srcs:
        return False
    if flag("cash_only") and not deal.get("is_cash_only"):
        return False
    if flag("hide_dealer") and deal.get("is_dealer"):
        return False
    if flag("hide_auction") and deal.get("is_auction"):
        return False
    if flag("hide_salvage") and deal.get("is_salvage"):
        return False
    for key, field, op in (("min_price", "price", "ge"), ("max_price", "price", "le"),
                           ("min_miles", "odometer", "ge"), ("max_miles", "odometer", "le"),
                           ("min_year", "year", "ge"), ("max_year", "year", "le"),
                           ("min_discount_pct", "discount_pct", "ge"),
                           ("min_profit", "est_profit", "ge")):
        lim = _int(f.get(key))
        if lim is None:
            continue
        val = deal.get(field)
        if val is None:
            return False
        if op == "ge" and val < lim:
            return False
        if op == "le" and val > lim:
            return False
    for key in ("make", "model", "state"):
        wanted = _csv(f.get(key))
        if wanted and str(deal.get(key) or "").lower() not in wanted:
            return False
    q = str(f.get("search") or "").strip().lower()
    if q and q not in str(deal.get("title") or "").lower():
        return False
    return True


def fan_out(conn, deal: dict, event_key: str) -> int:
    """Match `deal` (an alert just written; must carry alert_id) against
    every notifying saved search, using the caller's open transaction so
    the tray rows and delivery jobs commit atomically with the alert.
    Returns the number of users matched."""
    import notifications
    import plans
    alert_id = deal.get("alert_id")
    if not alert_id:
        return 0
    rows = [dict(r) for r in conn.execute(
        "SELECT s.id, s.user_id, s.filter_json, u.plan, u.ntfy_topic, "
        "       u.alert_webhook, u.notify_phone "
        "FROM saved_searches s JOIN users u ON u.id = s.user_id "
        "WHERE s.notify_enabled = TRUE").fetchall()]

    per_user: dict[int, dict] = {}
    for r in rows:
        if not plans.can_receive_alerts(r.get("plan")):
            continue   # expired / free accounts keep searches but get no alerts
        try:
            f = json.loads(r.get("filter_json") or "{}")
        except ValueError:
            continue
        if not isinstance(f, dict) or not matches(f, deal):
            continue
        per_user.setdefault(r["user_id"], r)   # first matching search wins

    for uid, r in per_user.items():
        conn.execute(
            "INSERT INTO user_alerts(user_id, alert_id, saved_search_id) "
            "VALUES (?,?,?) ON CONFLICT(user_id, alert_id) DO NOTHING",
            (uid, alert_id, r["id"]))
        chans = [c for c in notifications.user_channels(
                     r.get("notify_phone"), r.get("ntfy_topic"), r.get("alert_webhook"))
                 if plans.channel_allowed(r.get("plan"), c)]
        notifications.enqueue_user(conn, deal, event_key, uid, chans)
    return len(per_user)
