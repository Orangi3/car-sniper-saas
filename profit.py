"""
profit.py — transparent flip-profit estimates.

Inputs are split into:
  known        facts from the listing (asking price) and the valuation
               (value range + evidence class)
  assumptions  user-adjustable numbers with defaults; every one is
               reported back so the UI can show and edit it

Deliberately NOT assumed:
  * sales tax and title/registration — they depend on the buyer's
    jurisdiction and dealer/private status. They stay at "not included"
    until the user enters their own state's figures.
  * a negotiated purchase price — defaults to paying the asking price.

Scenarios: conservative / base / optimistic resale = valuation low / mid /
high. They also differ in purchase price (expected discount ∓
negotiation_spread_pct) and time to sell (days_to_sell × 1.5 / 1 / 0.5),
which drives holding cost and annualized ROI. Days to sell is a labeled
assumption, not a measured liquidity figure. When the valuation comes from asking prices (not completed sales),
`asking_to_sale_pct` haircuts all three, because asking prices overstate
what cars actually sell for; that haircut is itself a labeled assumption.

Nothing here is a guarantee; outputs are labeled as estimates.
"""
from __future__ import annotations

import json
from typing import Optional

DEFAULTS: dict = {
    "negotiation_pct": 0.0,       # % below asking you expect to pay
    "asking_to_sale_pct": 5.0,    # applied only when value is asking-based
    "sales_tax_pct": None,        # None = not included (set your state's rate)
    "title_registration": None,   # None = not included
    "transport": 0.0,
    "transport_per_mile": 0.0,    # round trip cost per mile of distance (optional)
    "dealer_to_private_pct": 10.0,  # dealer retail ask -> private resale, scaled by dealer share
    "negotiation_spread_pct": 5.0,  # ± around the expected discount (scenarios)
    "mechanical": 500.0,          # mechanical repair allowance
    "recon": 250.0,               # cosmetic reconditioning / detailing
    "holding_per_day": 5.0,       # insurance, parking, capital cost per day held
    "days_to_sell": 21.0,         # expected days from purchase to sale (base case)
    "selling_fees": 100.0,        # listing / platform fees when you resell
    "misc": 0.0,
}

# A resale estimate the user typed is their expected SALE price, so like
# completed sales it gets no asking-to-sale haircut (Analyze labels it).
USER_ESTIMATE = "user_estimate"
SALE_PRICE_EVIDENCE = ("confirmed_sales", USER_ESTIMATE)

_BOUNDS = {
    "negotiation_pct": (0, 60), "asking_to_sale_pct": (0, 40),
    "sales_tax_pct": (0, 15), "title_registration": (0, 5000),
    "transport": (0, 20000), "recon": (0, 100000), "selling_fees": (0, 20000),
    "misc": (0, 100000), "transport_per_mile": (0, 5), "dealer_to_private_pct": (0, 40),
    "negotiation_spread_pct": (0, 30), "mechanical": (0, 100000),
    "holding_per_day": (0, 500), "days_to_sell": (1, 365),
}

LABELS = {
    "negotiation_pct": "Expected discount off asking",
    "asking_to_sale_pct": "Asking-to-sale haircut on resale value",
    "sales_tax_pct": "Sales tax",
    "title_registration": "Title & registration",
    "transport": "Transport",
    "transport_per_mile": "Transport per mile (round trip)",
    "dealer_to_private_pct": "Dealer-retail to private-sale haircut",
    "negotiation_spread_pct": "Negotiation uncertainty (± scenarios)",
    "mechanical": "Mechanical repair allowance",
    "recon": "Cosmetic reconditioning",
    "holding_per_day": "Holding cost per day",
    "days_to_sell": "Expected days to sell",
    "selling_fees": "Selling fees",
    "misc": "Other costs",
}


def clean_settings(raw: Optional[dict]) -> dict:
    """Validate user-supplied assumptions; unknown keys dropped, values
    clamped, empty string / None means 'not set'."""
    out = {}
    for k, (lo, hi) in _BOUNDS.items():
        if not raw or k not in raw:
            continue
        v = raw[k]
        if v is None or v == "":
            out[k] = None
            continue
        try:
            out[k] = max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            continue
    return out


def merged(settings: Optional[dict]) -> dict:
    a = dict(DEFAULTS)
    mine = clean_settings(settings or {})
    if "recon" in mine and "mechanical" not in mine:
        # saved before the split: "recon" was the whole repair allowance
        a["mechanical"] = 0.0
    a.update(mine)
    for k in ("negotiation_pct", "asking_to_sale_pct", "transport", "recon",
              "selling_fees", "misc", "transport_per_mile", "dealer_to_private_pct",
              "negotiation_spread_pct", "mechanical", "holding_per_day"):
        if a.get(k) is None:
            a[k] = 0.0
    if not a.get("days_to_sell"):
        a["days_to_sell"] = DEFAULTS["days_to_sell"]
    return a


def load_user_settings(conn, user_id: Optional[int]) -> dict:
    if not user_id:
        return {}
    r = conn.execute("SELECT profit_settings FROM users WHERE id = ?", (user_id,)).fetchone()
    try:
        return json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        return {}


def _buy_costs(price: float, a: dict, discount_pct: float,
               distance_mi: Optional[float]) -> tuple[float, dict]:
    """Purchase price after `discount_pct` and every cost of buying, fixing
    and reselling the car (holding excluded: it depends on days held)."""
    acq = price * (1 - discount_pct / 100.0)
    c: dict[str, float] = {}
    if a["sales_tax_pct"] is not None:
        c["sales_tax_pct"] = acq * a["sales_tax_pct"] / 100.0
    if a["title_registration"] is not None:
        c["title_registration"] = a["title_registration"]
    for k in ("transport", "mechanical", "recon", "selling_fees", "misc"):
        if a[k]:
            c[k] = a[k]
    if a["transport_per_mile"] and distance_mi:
        c["transport_per_mile"] = 2 * distance_mi * a["transport_per_mile"]
    return acq, c


def cost_basis(price: float, settings: Optional[dict] = None,
               distance_mi: Optional[float] = None) -> dict:
    """Base-case money in, without any resale value: what break-even and
    maximum-offer figures are solved from. Same arithmetic as compute()'s
    base scenario."""
    a = merged(settings)
    acq, costs = _buy_costs(price, a, a["negotiation_pct"], distance_mi)
    holding = a["holding_per_day"] * a["days_to_sell"]
    return {"acquisition": acq, "costs": costs, "holding": holding,
            "invested": acq + sum(costs.values()) + holding,
            "fixed_costs": sum(v for k, v in costs.items() if k != "sales_tax_pct"),
            "tax_rate": (a["sales_tax_pct"] or 0.0) / 100.0,
            "days_to_sell": a["days_to_sell"]}


def compute(price: float, value_low: Optional[float], value_mid: Optional[float],
            value_high: Optional[float], evidence_class: Optional[str],
            settings: Optional[dict] = None, scenarios: bool = True,
            distance_mi: Optional[float] = None, dealer_share: float = 0.0
            ) -> Optional[dict]:
    """Profit breakdown, or None when there is no usable valuation.
    `distance_mi` adds round-trip transport at the user's per-mile rate;
    `dealer_share` (fraction of comps that were dealer retail asks) applies
    the labeled dealer-to-private haircut."""
    if not price or not value_mid:
        return None
    a = merged(settings)
    neg, spread, days = a["negotiation_pct"], a["negotiation_spread_pct"], a["days_to_sell"]
    shape = {"conservative": (max(0.0, neg - spread), 1.5),
             "base": (neg, 1.0), "optimistic": (min(60.0, neg + spread), 0.5)}

    def buy_costs(discount_pct: float) -> tuple[float, dict]:
        return _buy_costs(price, a, discount_pct, distance_mi)

    not_included: list[str] = []
    if a["sales_tax_pct"] is None:
        not_included.append("sales tax — set your state's rate")
    if a["title_registration"] is None:
        not_included.append("title & registration — set your local fees")
    acquisition, costs = buy_costs(neg)
    if a["holding_per_day"]:
        costs["holding"] = a["holding_per_day"] * days
    total_costs = sum(costs.values())
    invested = acquisition + total_costs

    haircut = (a["asking_to_sale_pct"] / 100.0
               if evidence_class and evidence_class not in SALE_PRICE_EVIDENCE else 0.0)
    dealer_cut = (max(0.0, min(1.0, dealer_share or 0.0)) * a["dealer_to_private_pct"] / 100.0
                  if evidence_class == "asking_comparables" else 0.0)
    haircut = min(0.9, haircut + dealer_cut)
    resale_points = {"conservative": value_low or value_mid, "base": value_mid,
                     "optimistic": value_high or value_mid}
    if not scenarios:
        resale_points = {"base": value_mid}
    out_scen = {}
    for name, v in resale_points.items():
        disc, dmult = shape[name]
        acq, c = buy_costs(disc)
        hold_days = days * dmult
        holding = a["holding_per_day"] * hold_days
        inv = acq + sum(c.values()) + holding
        resale = v * (1 - haircut)
        profit = resale - inv
        roi = profit / inv if inv else None
        out_scen[name] = {
            "resale": round(resale), "purchase": round(acq), "holding": round(holding),
            "days_to_sell": round(hold_days, 1), "invested": round(inv),
            "profit": round(profit),
            "roi_pct": round(100 * roi, 1) if roi is not None else None,
            # simple (non-compounded) annualization; large for short holds
            "annualized_roi_pct": (round(100 * roi * 365 / hold_days, 1)
                                   if roi is not None and hold_days else None)}

    LAB = dict(LABELS, holding="Holding cost")
    assumptions = []
    for k in ("negotiation_pct", "negotiation_spread_pct", "asking_to_sale_pct",
              "dealer_to_private_pct", "sales_tax_pct", "title_registration", "transport",
              "transport_per_mile", "mechanical", "recon", "holding_per_day", "days_to_sell",
              "selling_fees", "misc"):
        if k == "asking_to_sale_pct" and evidence_class in SALE_PRICE_EVIDENCE:
            continue
        if k == "dealer_to_private_pct" and not dealer_cut:
            continue
        if k == "transport_per_mile" and "transport_per_mile" not in costs:
            continue
        if k == "negotiation_spread_pct" and not scenarios:
            continue
        v = a.get(k)
        if v is None:
            continue
        assumptions.append({"key": k, "label": LABELS[k], "value": v,
                            "unit": ("%" if k.endswith("_pct") else
                                     "days" if k == "days_to_sell" else "$"),
                            "is_default": settings is None or k not in (settings or {})})
    explanation = [
        f"Buy at ${acquisition:,.0f}" + (f" ({a['negotiation_pct']:g}% under asking)"
                                         if a["negotiation_pct"] else " (asking price)"),
        f"Plus costs ${total_costs:,.0f}: " + (", ".join(
            f"{LAB[k].lower()} ${v:,.0f}" for k, v in costs.items()) or "none entered")
        + (f" (holding = ${a['holding_per_day']:g}/day × {days:g} days)"
           if a["holding_per_day"] else ""),
        (f"Resale = market value × (1 − {haircut * 100:.1f}%): "
         f"{a['asking_to_sale_pct']:g}% asking-to-sale"
         + (f" + {dealer_cut * 100:.1f}% because {dealer_share:.0%} of comparables were "
            "dealer retail asks" if dealer_cut else "")
         + " — the value comes from asking prices, not completed sales")
        if haircut else ("Resale = your own resale estimate, not a market value"
                         if evidence_class == USER_ESTIMATE
                         else "Resale = market value from completed sales"),
        "Profit = resale − purchase − costs. Estimate only; not guaranteed.",
    ] + ([
        f"Scenarios: conservative pays {shape['conservative'][0]:g}% under asking and "
        f"sells in {days * 1.5:g} days at the low value; optimistic pays "
        f"{shape['optimistic'][0]:g}% under and sells in {days * 0.5:g} days at the high value",
    ] if scenarios else [])
    return {
        "known": {"asking_price": price, "value_low": value_low, "value_mid": value_mid,
                  "value_high": value_high, "evidence_class": evidence_class},
        "acquisition": round(acquisition), "costs": {k: round(v) for k, v in costs.items()},
        "total_costs": round(total_costs), "invested": round(invested),
        "scenarios": out_scen, "assumptions": assumptions,
        "not_included": not_included, "explanation": explanation,
        "disclaimer": "Estimated, not guaranteed or independently verified.",
    }
