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
high. When the valuation comes from asking prices (not completed sales),
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
    "recon": 750.0,               # reconditioning / repair allowance
    "selling_fees": 100.0,        # listing / platform fees when you resell
    "misc": 0.0,
}

_BOUNDS = {
    "negotiation_pct": (0, 60), "asking_to_sale_pct": (0, 40),
    "sales_tax_pct": (0, 15), "title_registration": (0, 5000),
    "transport": (0, 20000), "recon": (0, 100000), "selling_fees": (0, 20000),
    "misc": (0, 100000), "transport_per_mile": (0, 5), "dealer_to_private_pct": (0, 40),
}

LABELS = {
    "negotiation_pct": "Expected discount off asking",
    "asking_to_sale_pct": "Asking-to-sale haircut on resale value",
    "sales_tax_pct": "Sales tax",
    "title_registration": "Title & registration",
    "transport": "Transport",
    "transport_per_mile": "Transport per mile (round trip)",
    "dealer_to_private_pct": "Dealer-retail to private-sale haircut",
    "recon": "Repairs / reconditioning allowance",
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
    a.update(clean_settings(settings or {}))
    for k in ("negotiation_pct", "asking_to_sale_pct", "transport", "recon",
              "selling_fees", "misc", "transport_per_mile", "dealer_to_private_pct"):
        if a.get(k) is None:
            a[k] = 0.0
    return a


def load_user_settings(conn, user_id: Optional[int]) -> dict:
    if not user_id:
        return {}
    r = conn.execute("SELECT profit_settings FROM users WHERE id = ?", (user_id,)).fetchone()
    try:
        return json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        return {}


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
    acquisition = price * (1 - a["negotiation_pct"] / 100.0)
    costs: dict[str, float] = {}
    not_included: list[str] = []
    if a["sales_tax_pct"] is None:
        not_included.append("sales tax — set your state's rate")
    else:
        costs["sales_tax_pct"] = acquisition * a["sales_tax_pct"] / 100.0
    if a["title_registration"] is None:
        not_included.append("title & registration — set your local fees")
    else:
        costs["title_registration"] = a["title_registration"]
    for k in ("transport", "recon", "selling_fees", "misc"):
        if a[k]:
            costs[k] = a[k]
    if a["transport_per_mile"] and distance_mi:
        costs["transport_per_mile"] = 2 * distance_mi * a["transport_per_mile"]
    total_costs = sum(costs.values())
    invested = acquisition + total_costs

    haircut = (a["asking_to_sale_pct"] / 100.0
               if evidence_class and evidence_class != "confirmed_sales" else 0.0)
    dealer_cut = (max(0.0, min(1.0, dealer_share or 0.0)) * a["dealer_to_private_pct"] / 100.0
                  if evidence_class == "asking_comparables" else 0.0)
    haircut = min(0.9, haircut + dealer_cut)
    resale_points = {"conservative": value_low or value_mid, "base": value_mid,
                     "optimistic": value_high or value_mid}
    if not scenarios:
        resale_points = {"base": value_mid}
    out_scen = {}
    for name, v in resale_points.items():
        resale = v * (1 - haircut)
        profit = resale - invested
        out_scen[name] = {"resale": round(resale), "profit": round(profit),
                          "roi_pct": round(100 * profit / invested, 1) if invested else None}

    assumptions = []
    for k in ("negotiation_pct", "asking_to_sale_pct", "dealer_to_private_pct",
              "sales_tax_pct", "title_registration", "transport", "transport_per_mile",
              "recon", "selling_fees", "misc"):
        if k == "asking_to_sale_pct" and evidence_class == "confirmed_sales":
            continue
        if k == "dealer_to_private_pct" and not dealer_cut:
            continue
        if k == "transport_per_mile" and "transport_per_mile" not in costs:
            continue
        v = a.get(k)
        if v is None:
            continue
        assumptions.append({"key": k, "label": LABELS[k], "value": v,
                            "unit": "%" if k.endswith("_pct") else "$",
                            "is_default": settings is None or k not in (settings or {})})
    explanation = [
        f"Buy at ${acquisition:,.0f}" + (f" ({a['negotiation_pct']:g}% under asking)"
                                         if a["negotiation_pct"] else " (asking price)"),
        f"Plus costs ${total_costs:,.0f}: " + (", ".join(
            f"{LABELS[k].lower()} ${v:,.0f}" for k, v in costs.items()) or "none entered"),
        (f"Resale = market value × (1 − {haircut * 100:.1f}%): "
         f"{a['asking_to_sale_pct']:g}% asking-to-sale"
         + (f" + {dealer_cut * 100:.1f}% because {dealer_share:.0%} of comparables were "
            "dealer retail asks" if dealer_cut else "")
         + " — the value comes from asking prices, not completed sales")
        if haircut else "Resale = market value from completed sales",
        "Profit = resale − purchase − costs. Estimate only; not guaranteed.",
    ]
    return {
        "known": {"asking_price": price, "value_low": value_low, "value_mid": value_mid,
                  "value_high": value_high, "evidence_class": evidence_class},
        "acquisition": round(acquisition), "costs": {k: round(v) for k, v in costs.items()},
        "total_costs": round(total_costs), "invested": round(invested),
        "scenarios": out_scen, "assumptions": assumptions,
        "not_included": not_included, "explanation": explanation,
        "disclaimer": "Estimated, not guaranteed or independently verified.",
    }
