"""
listing_display.py — may this listing be shown to a customer right now?

One rule set for every customer-facing surface: the deal feeds and the
detail page (server.py) and outbound deal alerts (notifications.py) — an
alert is a display too. A listing is hidden when:

  * it is a FlipFirst seller listing that is not discoverable now
    (seller_listings.discoverable_ids: active, contact still verified,
    re-confirmed within CONFIRM_DAYS);
  * it is an auction whose end time has passed; or
  * its source's terms limit how stale displayed data may be
    (DISPLAY_MAX_AGE_HOURS on the source module; eBay: 6 h) and no poll has
    re-confirmed it within that window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

DIRECT_KEY = "__direct__"


def limits(conn, include_direct: bool = True) -> dict:
    """source -> max hours since last confirmed by a poll, for sources whose
    terms limit it; plus (include_direct) the set of seller listings that are
    discoverable now."""
    import seller_listings as sl
    import sources
    out: dict = {m.SOURCE_ID: m.DISPLAY_MAX_AGE_HOURS for m in sources.all_sources()
                 if getattr(m, "DISPLAY_MAX_AGE_HOURS", None)}
    if include_direct:
        out[DIRECT_KEY] = sl.discoverable_ids(conn)
    return out


def _utc(value) -> datetime:
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def hidden_reason(d: dict, limits: dict, now: Optional[datetime] = None) -> Optional[str]:
    """Why listing row `d` must not be shown now, or None if it may be."""
    now = now or datetime.now(timezone.utc)
    if d.get("source") == "direct":
        if d.get("composite_id") in limits.get(DIRECT_KEY, set()):
            return None
        return "seller listing is not live (withdrawn, expired or contact unverified)"
    if d.get("is_auction") and d.get("auction_end_at"):
        try:                                  # an ended auction is not for sale
            if _utc(d["auction_end_at"]) <= now:
                return "auction has ended"
        except ValueError:
            pass
    hrs = limits.get(d.get("source"))
    if not hrs:
        return None
    try:
        seen = _utc(d.get("last_seen_at") or d.get("first_seen_at"))
    except ValueError:
        return f"no valid last-confirmed time; {d.get('source')} allows at most {hrs} h"
    if now - seen > timedelta(hours=hrs):
        return f"not re-confirmed by {d.get('source')} in the last {hrs} h"
    return None


def displayable(d: dict, limits: dict, now: Optional[datetime] = None) -> bool:
    return hidden_reason(d, limits, now) is None
