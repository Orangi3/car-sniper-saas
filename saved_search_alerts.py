"""
saved_search_alerts.py — personalized opportunity matching.

Previously subscribers were only routed alerts that had already passed the
OWNER's global deal threshold, so a user whose criteria differed (say 12%
under with a $2,000 profit floor) never heard about matching cars.

Now every valued listing (and every material price drop) is evaluated
against each entitled subscriber's own saved searches:

  * ingestion and valuation run once per listing — shared by everyone;
    matching is a cheap in-memory filter per search
  * each search's own criteria decide (see `criteria_match`)
  * one match per user per event: several matching searches → one
    notification (UNIQUE(user_id, event_key) in user_matches); the same car
    cross-posted on another source is not re-sent within 14 days
  * plan limits are enforced here, server-side: entitled plans only, at
    most N active searches, alerts/day cap (extra matches still land in the
    tray, unnotified), price-drop alerts and aggressive mode for Pro, SMS
    monthly cap
  * jobs store the user id, not the destination (see notifications.py)

Filter keys (all optional):
  make, model, state, source (comma lists) · min/max_price · min/max_year ·
  min/max_miles · max_age_years · search (title substring) · cash_only ·
  hide_dealer · hide_auction · hide_salvage · min_discount_pct (default 15)
  · min_profit · min_confidence (low|medium|high; default medium) ·
  risk_mode (conservative|aggressive) · allow_needs_work · allow_salvage ·
  price_drops (default true)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

CONF_RANK = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}
DEFAULT_MIN_DISCOUNT = 15.0
CROSS_POST_WINDOW_DAYS = 14


def _csv(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip().lower() for x in v if str(x).strip()]
    return [x.strip().lower() for x in str(v).split(",") if x.strip()]


def _num(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _flag(f: dict, k: str) -> bool:
    return str(f.get(k, "")).lower() in ("1", "true", "yes")


def matches(f: dict, deal: dict) -> bool:
    """Listing-attribute filters shared with the dashboard's listing view.
    Unknown keys are ignored."""
    srcs = _csv(f.get("source"))
    if srcs and str(deal.get("source", "")).lower() not in srcs:
        return False
    if _flag(f, "cash_only") and not deal.get("is_cash_only"):
        return False
    if _flag(f, "hide_dealer") and deal.get("is_dealer"):
        return False
    if _flag(f, "hide_auction") and deal.get("is_auction"):
        return False
    if _flag(f, "hide_salvage") and deal.get("is_salvage"):
        return False
    for key, field, op in (("min_price", "price", "ge"), ("max_price", "price", "le"),
                           ("min_miles", "odometer", "ge"), ("max_miles", "odometer", "le"),
                           ("min_year", "year", "ge"), ("max_year", "year", "le"),
                           ("min_discount_pct", "discount_pct", "ge"),
                           ("min_profit", "est_profit", "ge")):
        lim = _num(f.get(key))
        if lim is None:
            continue
        val = deal.get(field)
        if val is None:
            return False
        if op == "ge" and val < lim:
            return False
        if op == "le" and val > lim:
            return False
    age = _num(f.get("max_age_years"))
    if age is not None:
        if not deal.get("year") or deal["year"] < datetime.now().year - age:
            return False
    for key in ("make", "model", "state"):
        wanted = _csv(f.get(key))
        if wanted and str(deal.get(key) or "").lower() not in wanted:
            return False
    q = str(f.get("search") or "").strip().lower()
    if q and q not in str(deal.get("title") or "").lower():
        return False
    return True


def criteria_match(f: dict, opp: dict, lim: dict) -> tuple[bool, str]:
    """Does opportunity `opp` satisfy saved-search `f` under plan limits
    `lim`? Returns (ok, reason-if-not)."""
    if opp.get("event") == "price_drop":
        if not lim.get("price_drop_alerts"):
            return False, "price-drop alerts not in plan"
        if str(f.get("price_drops", "true")).lower() in ("0", "false", "no"):
            return False, "search excludes price drops"
    mode = str(f.get("risk_mode") or "conservative").lower()
    if mode == "aggressive" and not lim.get("aggressive_mode"):
        mode = "conservative"
    if (opp.get("scam_score") or 0) >= 80:
        return False, "scam signals"
    if opp.get("is_salvage") and not _flag(f, "allow_salvage"):
        return False, "salvage/branded title"
    if opp.get("needs_work") and not _flag(f, "allow_needs_work"):
        return False, "needs mechanical work"
    if mode == "conservative" and opp.get("trust") != "trusted":
        return False, "risk-flagged (conservative mode)"
    default_conf = "medium" if mode == "conservative" else "low"
    want = CONF_RANK.get(str(f.get("min_confidence") or default_conf).lower(), 2)
    if CONF_RANK.get(opp.get("confidence") or "insufficient", 0) < want:
        return False, "confidence below search minimum"
    eff = dict(f)
    if _num(eff.get("min_discount_pct")) is None:
        eff["min_discount_pct"] = DEFAULT_MIN_DISCOUNT
    if not matches(eff, opp):
        return False, "filters"
    return True, ""


def _day_start(now: datetime) -> str:
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _month_start(now: datetime) -> str:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def evaluate(conn, opp: dict) -> list[dict]:
    """Match one opportunity against every entitled subscriber's searches,
    inside the caller's transaction. Records user_matches rows and queues
    notification jobs. Returns the created matches."""
    import notifications
    import plans
    import profit as profit_mod

    now = datetime.now(timezone.utc)
    event = opp.get("event") or "new"
    cid = opp["composite_id"]
    event_key = (f"{cid}:drop:{opp.get('price')}" if event == "price_drop"
                 else f"{cid}:new")

    rows = [dict(r) for r in conn.execute(
        "SELECT s.id, s.user_id, s.filter_json, u.plan, u.ntfy_topic, "
        "  u.alert_webhook, u.notify_phone, u.profit_settings "
        "FROM saved_searches s JOIN users u ON u.id = s.user_id "
        "WHERE s.notify_enabled = TRUE ORDER BY s.user_id, s.created_at, s.id"
    ).fetchall()]

    by_user: dict[int, list[dict]] = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)

    created = []
    for uid, searches in by_user.items():
        plan = searches[0].get("plan")
        lim = plans.limits(plan)
        if not plans.can_receive_alerts(plan):
            continue                      # lapsed / free: searches kept, silent
        u_event, u_key = event, event_key
        if event == "price_drop" and not lim["price_drop_alerts"]:
            # Without price-drop alerts, a drop can still make a car newly
            # match — deliver that as an ordinary first match, once.
            seen = conn.execute("SELECT 1 FROM user_matches WHERE user_id = ? "
                                "AND composite_id = ?", (uid, cid)).fetchone()
            if seen:
                continue
            u_event, u_key = "new", f"{cid}:new"
        searches = searches[: lim["saved_searches"]]   # downgrade: oldest N only
        try:
            psettings = json.loads(searches[0].get("profit_settings") or "{}")
        except ValueError:
            psettings = {}
        pr = profit_mod.compute(opp.get("price"), opp.get("value_low"),
                                opp.get("value_mid"), opp.get("value_high"),
                                opp.get("evidence_class"), psettings, scenarios=False)
        opp_u = {**opp, "event": u_event,
                 "est_profit": pr["scenarios"]["base"]["profit"] if pr else None}

        hit = None
        for s in searches:
            try:
                f = json.loads(s.get("filter_json") or "{}")
            except ValueError:
                continue
            if isinstance(f, dict) and criteria_match(f, opp_u, lim)[0]:
                hit = s
                break
        if hit is None:
            continue

        if u_event == "new" and opp.get("vehicle_key"):
            dup = conn.execute(
                "SELECT 1 FROM user_matches m JOIN listings l ON l.composite_id = m.composite_id "
                "WHERE m.user_id = ? AND l.vehicle_key = ? AND m.composite_id <> ? "
                "  AND m.created_at > ?",
                (uid, opp["vehicle_key"], cid,
                 (now - timedelta(days=CROSS_POST_WINDOW_DAYS)).isoformat())).fetchone()
            if dup:
                continue                  # same car already sent from another source

        sent_today = int(conn.execute(
            "SELECT COUNT(*) FROM user_matches WHERE user_id = ? AND notified = TRUE "
            "AND created_at >= ?", (uid, _day_start(now))).fetchone()[0])
        notify = sent_today < lim["alerts_per_day"]

        row = conn.execute(
            "INSERT INTO user_matches(user_id, saved_search_id, composite_id, event_key, "
            "  event, price, previous_price, discount_pct, value_mid, profit_base, "
            "  confidence, trust, notified, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, event_key) DO NOTHING RETURNING id",
            (uid, hit["id"], cid, u_key, u_event, opp.get("price"),
             opp.get("previous_price"), opp.get("discount_pct"), opp.get("value_mid"),
             opp_u.get("est_profit"), opp.get("confidence"), opp.get("trust"),
             False, now.isoformat())).fetchone()
        if row is None:
            continue                      # this event was already handled

        chans = []
        if notify:
            chans = [c for c in notifications.user_channels(
                         hit.get("notify_phone"), hit.get("ntfy_topic"),
                         hit.get("alert_webhook"))
                     if plans.channel_allowed(plan, c)]
            if "twilio" in chans:
                used = int(conn.execute(
                    "SELECT COALESCE(SUM(units), 0) FROM usage_events WHERE user_id = ? "
                    "AND kind = 'sms' AND created_at >= ?",
                    (uid, _month_start(now))).fetchone()[0])
                if used >= lim["sms_per_month"]:
                    chans.remove("twilio")
            queued = notifications.enqueue_user(conn, opp_u, u_key, uid, chans)
            if queued:
                conn.execute("UPDATE user_matches SET notified = TRUE WHERE id = ?",
                             (row[0],))
        created.append({"user_id": uid, "match_id": row[0], "saved_search_id": hit["id"],
                        "event": u_event, "channels": chans, "notified": bool(chans)})
    return created
