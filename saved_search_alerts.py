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
import sys
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


def fan_out(deal: dict) -> int:
    """Match `deal` (an alert just written; must carry alert_id) against
    every notifying saved search. Returns the number of users notified."""
    import db as _db  # lazy: picks up the active DB (tests swap it per case)
    alert_id = deal.get("alert_id")
    if not alert_id:
        return 0
    ph = _db.placeholder()
    true_val = "TRUE" if _db.IS_PG else "1"
    try:
        with _db.transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT s.id, s.user_id, s.filter_json, u.ntfy_topic, "
                "       u.alert_webhook, u.notify_phone "
                "FROM saved_searches s JOIN users u ON u.id = s.user_id "
                f"WHERE s.notify_enabled = {true_val}")
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        # Pre-0007 schema or a DB without users (single-user local mode)
        print(f"[saved-search] lookup skipped: {e}", file=sys.stderr)
        return 0

    per_user: dict[int, dict] = {}
    for r in rows:
        try:
            f = json.loads(r.get("filter_json") or "{}")
        except ValueError:
            continue
        if not isinstance(f, dict) or not matches(f, deal):
            continue
        per_user.setdefault(r["user_id"], r)   # first matching search wins

    if not per_user:
        return 0

    with _db.transaction() as conn:
        cur = conn.cursor()
        for uid, r in per_user.items():
            sql = ("INSERT INTO user_alerts(user_id, alert_id, saved_search_id) "
                   f"VALUES ({ph},{ph},{ph}) ON CONFLICT(user_id, alert_id) DO NOTHING")
            cur.execute(sql, (uid, alert_id, r["id"]))

    import notifications
    for uid, r in per_user.items():
        if r.get("ntfy_topic") or r.get("alert_webhook") or r.get("notify_phone"):
            notifications.notify_user_deal(
                deal, phone=r.get("notify_phone"), ntfy_topic=r.get("ntfy_topic"),
                webhook=r.get("alert_webhook"))
    return len(per_user)
