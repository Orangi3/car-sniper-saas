"""
opportunity.py — explainable ranking of buying opportunities.

The rank is a dollar figure: the CONSERVATIVE-scenario profit (value_low
resale, after the buyer's own cost assumptions incl. distance-based
transport) multiplied by named risk factors. Every factor and its reason is
returned, so the UI can show exactly why one car ranks above another. There
are no learned weights and no invented probabilities — the factors are
editorial discounts, listed here and tunable in one place.

    risk_adjusted_profit = conservative_profit
                           × evidence × confidence × condition
                           × freshness × distance × liquidity

A negative conservative profit is never inflated by factors (it is kept as
is), so losers sort last.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

EVIDENCE = {"confirmed_sales": 1.0, "asking_comparables": 0.85,
            "historical_incomplete": 0.6, "model_estimate": 0.6}
CONFIDENCE = {"high": 1.0, "medium": 0.85, "low": 0.6}
CONDITION = {  # multiplied together for each flag present
    "scam_signals": 0.0, "branded_title": 0.4, "needs_work": 0.5,
    "too_good_to_be_true": 0.3, "mileage_unknown": 0.85, "relisted": 0.9,
    "dealer": 0.9,
    "seller_type_unknown": 0.95,
    "auction_bid": 0.3, "description_unchecked": 0.9,
}
FRESHNESS = ((2, 1.0, "posted in the last 2 hours"), (24, 0.9, "posted today"),
             (72, 0.75, "1–3 days old"), (None, 0.6, "older than 3 days (likely contacted)"))
DISTANCE = ((25, 1.0), (50, 0.95), (100, 0.9), (200, 0.8), (None, 0.7))
PRICE_BAND = (2000, 15000)   # the niche: most liquid private-party resale band


def _age_hours(iso: Optional[str], now: datetime) -> Optional[float]:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return max(0.0, (now - t).total_seconds() / 3600)
    except ValueError:
        return None


def score(deal: dict, conservative_profit: Optional[float],
          distance_mi: Optional[float] = None,
          now: Optional[datetime] = None) -> dict:
    """Returns {'risk_adjusted_profit', 'factors': [{name, value, why}],
    'conservative_profit'}."""
    now = now or datetime.now(timezone.utc)
    factors = []

    def f(name, value, why):
        factors.append({"name": name, "value": round(value, 3), "why": why})
        return value

    if conservative_profit is None:
        return {"risk_adjusted_profit": None, "conservative_profit": None,
                "factors": [{"name": "valuation", "value": 0,
                             "why": "no usable valuation"}]}
    m = 1.0
    ev = deal.get("evidence_class")
    m *= f("evidence", EVIDENCE.get(ev, 0.6),
           {"confirmed_sales": "value from completed sales",
            "asking_comparables": "value from asking prices (sellers' asks)"}.get(
               ev, "weak or incomplete sales evidence"))
    conf = deal.get("confidence") or "low"
    m *= f("confidence", CONFIDENCE.get(conf, 0.6), f"{conf} valuation confidence")
    cond = 1.0
    hit = [fl for fl in (deal.get("risk_flags") or []) if fl in CONDITION]
    for fl in hit:
        cond *= CONDITION[fl]
    m *= f("condition", cond, ", ".join(fl.replace("_", " ") for fl in hit)
           or "no condition or risk flags")
    age = _age_hours(deal.get("posted_at") or deal.get("first_seen_at"), now)
    if age is None:
        m *= f("freshness", 0.85, "listing age unknown")
    else:
        for limit, val, why in FRESHNESS:
            if limit is None or age <= limit:
                m *= f("freshness", val, why)
                break
    if distance_mi is None:
        m *= f("distance", 0.85, "distance unknown")
    else:
        for limit, val in DISTANCE:
            if limit is None or distance_mi <= limit:
                m *= f("distance", val, f"{distance_mi:.0f} mi away")
                break
    n = int(deal.get("comp_n") or 0)
    liq = 1.0 if n >= 10 else 0.95 if n >= 5 else 0.9
    price = deal.get("price") or 0
    in_band = PRICE_BAND[0] <= price <= PRICE_BAND[1]
    if not in_band:
        liq *= 0.9
    m *= f("liquidity", liq, f"{n} comparable listings"
           + ("" if in_band else "; outside the $2k–$15k resale sweet spot"))
    rap = conservative_profit if conservative_profit <= 0 else conservative_profit * m
    return {"risk_adjusted_profit": round(rap), "conservative_profit": round(conservative_profit),
            "multiplier": round(m, 3), "factors": factors}
