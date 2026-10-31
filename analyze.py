"""
analyze.py — FlipFirst Analyze: a deal analysis for a car the user found
anywhere (a lot, a friend, any listing site), with no marketplace ingestion.

    result, errors = analyze(payload, settings=..., home_zip=..., peer_filter=...)

Where the numbers come from (no paid calls):
  * identity: what the user entered, checked against the free NHTSA vPIC VIN
    decode (vin.py, cached) when a VIN is given
  * market value: comparables already in FlipFirst's database — licensed
    sources and FlipFirst sellers, only those currently allowed on screen —
    through the same valuation engine as Discover (valuation.value_listing).
    No sold-price lookups and no third-party listing calls.
  * costs: the user's numbers for this car on top of their saved assumptions
    (profit.py)

It never invents a market value. With fewer than three usable comparables
the valuation says so; profit is then computed only from a resale estimate
the user typed (labeled as theirs), and the break-even resale price and the
maximum offer for a target profit are what the user gets instead.

Decision — conservative on purpose; the thresholds are the constants below:
  high_risk              any blocking risk, or the expected case loses money
  insufficient_evidence  no market value and no resale estimate of the user's
  strong_candidate       market value at medium or high confidence, no
                         blocking risk, conservative profit ≥ STRONG_MIN_PROFIT,
                         expected ROI ≥ STRONG_MIN_ROI_PCT and margin of safety
                         ≥ STRONG_MIN_SAFETY_PCT
  worth_investigating    everything else that makes money in the expected case
A user's own resale estimate can never make a car a strong candidate.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Optional

STRONG_MIN_PROFIT = 1000          # $, conservative scenario
STRONG_MIN_ROI_PCT = 15.0         # expected (base) scenario
STRONG_MIN_SAFETY_PCT = 10.0      # how far resale may fall before a loss
DEFAULT_TARGET_PROFIT = 1000      # for the maximum-offer figure

TITLE_STATUSES = ("clean", "rebuilt", "salvage", "flood", "lemon", "lien", "missing", "unknown")
CONDITIONS = ("runs_drives", "needs_work", "not_running", "unknown")
COST_KEYS = ("mechanical", "recon", "transport", "title_registration", "sales_tax_pct",
             "selling_fees", "holding_per_day", "days_to_sell", "misc")
EVIDENCE_WORDS = {"confirmed_sales": "completed sales", "asking_comparables": "asking prices",
                  "historical_incomplete": "older or partial sales",
                  "model_estimate": "a licensed pricing model"}
MARKET_EVIDENCE = tuple(EVIDENCE_WORDS)

RISK_LABELS = {
    "branded_title": "branded title", "flood_damage": "flood damage",
    "structural_damage": "frame or structural damage", "title_missing": "no title",
    "title_lien": "lien on the title", "not_running": "doesn't run",
    "needs_work": "needs work",
    "repairs_not_estimated": "needs work, but no repair estimate was entered",
    "scam_signals": "scam signals", "too_good_to_be_true": "price far below market",
    "priced_above_market": "priced above market", "mileage_suspect": "implausibly low mileage",
    "mileage_unknown": "mileage unknown", "vin_invalid": "VIN fails its check digit",
    "vin_mismatch": "VIN decodes to a different car", "description_missing": "no seller description",
    "low_confidence": "few or loose comparables", "insufficient_data": "no market value",
}
# Any of these makes the car high risk. "needs_work" joins them when no
# repair estimate was entered (the default allowance can't price a repair).
BLOCKING = {"branded_title", "flood_damage", "structural_damage", "title_missing",
            "not_running", "scam_signals", "too_good_to_be_true", "mileage_suspect",
            "vin_invalid", "vin_mismatch"}
DECISIONS = {"strong_candidate": "Strong candidate", "worth_investigating": "Worth investigating",
             "high_risk": "High risk", "insufficient_evidence": "Insufficient evidence"}

FLOOD_RE = re.compile(r"\b(?:flood(?:ed)?|water[\s-]*damage[d]?|submerged|hurricane\s*car)\b",
                      re.IGNORECASE)
STRUCTURAL_RE = re.compile(
    r"\b(?:frame|unibody|structural)[\s-]*(?:damage[d]?|bent|rot(?:ted)?|rust(?:ed)?|"
    r"repair(?:ed|s)?)\b|\bbent\s*frame\b|\bair\s*bags?\s*(?:deployed|went\s*off)\b|"
    r"\bdeployed\s*air\s*bags?\b", re.IGNORECASE)
_NAME_CHARS = re.compile(r"^[\w .\-/&']*$")


def _num(p: dict, key: str, lo: int, hi: int, label: str, errs: list) -> Optional[int]:
    v = p.get(key)
    if v is None or v == "":
        return None
    try:
        n = int(round(float(str(v).replace(",", "").replace("$", "").strip())))
    except (TypeError, ValueError, OverflowError):     # "abc", "nan", "1e999", "inf"
        errs.append(f"{label} must be a number")
        return None
    if not lo <= n <= hi:
        errs.append(f"{label} must be between {lo:,} and {hi:,}")
        return None
    return n


def _name(p: dict, key: str, n: int, label: str, errs: list) -> Optional[str]:
    s = " ".join(str(p.get(key) or "").split())[:n]
    if s and not _NAME_CHARS.match(s):
        errs.append(f"{label} may only contain letters, numbers and - . / & '")
        return None
    return (s.title() if s.islower() else s) or None


def clean(p) -> tuple[dict, list[str]]:
    """Validated inputs + problems (empty = valid). Unknown keys are ignored."""
    import geo
    import profit
    p = p if isinstance(p, dict) else {}
    errs: list[str] = []
    this_year = datetime.now(timezone.utc).year
    costs_in = p.get("costs") if isinstance(p.get("costs"), dict) else {}
    f = {
        "vin": re.sub(r"[\s-]", "", str(p.get("vin") or "")).upper()[:20] or None,
        "year": _num(p, "year", 1950, this_year + 1, "Year", errs),
        "make": _name(p, "make", 30, "Make", errs),
        "model": _name(p, "model", 40, "Model", errs),
        "trim": _name(p, "trim", 30, "Trim", errs),
        "mileage": _num(p, "mileage", 0, 999_999, "Mileage", errs),
        "asking_price": _num(p, "asking_price", 100, 500_000, "Asking price", errs),
        "offer_price": _num(p, "offer_price", 100, 500_000, "Your purchase price", errs),
        "resale_estimate": _num(p, "resale_estimate", 100, 500_000, "Your resale estimate", errs),
        "target_profit": _num(p, "target_profit", 0, 100_000, "Target profit", errs),
        "zip": str(p.get("zip") or "").strip()[:5] or None,
        "title_status": str(p.get("title_status") or "unknown").strip().lower(),
        "condition": str(p.get("condition") or "unknown").strip().lower(),
        "faults": str(p.get("faults") or "").strip()[:1000],
        "description": str(p.get("description") or "").strip()[:4000],
        "costs": profit.clean_settings({k: costs_in[k] for k in COST_KEYS if k in costs_in}),
        "composite_id": str(p.get("composite_id") or "").strip()[:200] or None,
    }
    if f["asking_price"] is None and not any(e.startswith("Asking price") for e in errs):
        errs.append("Asking price is required")
    if f["zip"] and not geo.valid_zip(f["zip"]):
        errs.append("ZIP code isn't a valid US ZIP")
    if f["title_status"] not in TITLE_STATUSES:
        errs.append("Title status must be one of: " + ", ".join(TITLE_STATUSES))
    if f["condition"] not in CONDITIONS:
        errs.append("Condition must be one of: " + ", ".join(CONDITIONS))
    return f, errs


def input_key(f: dict) -> str:
    """Identifies an analysis request (validated inputs) for usage counting."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(f, sort_keys=True, default=str).encode()).hexdigest()[:32]


def summarize(raw: Optional[dict]) -> Optional[dict]:
    """The decoded fields Analyze shows, from NHTSA's DecodeVinValuesExtended."""
    if not raw:
        return None
    make = raw.get("Make")
    if make and make.isupper() and len(make) > 3:
        make = make.title()
    year = raw.get("ModelYear")
    engine = " ".join(x for x in (f"{raw['DisplacementL']}L" if raw.get("DisplacementL") else "",
                                  f"{raw['EngineCylinders']} cyl" if raw.get("EngineCylinders") else "")
                      if x)
    return {"year": int(year) if str(year or "").isdigit() else None, "make": make,
            "model": raw.get("Model"), "trim": raw.get("Trim") or raw.get("Series"),
            "body": raw.get("BodyClass"), "drive": raw.get("DriveType"),
            "engine": engine or None, "fuel": raw.get("FuelTypePrimary"),
            "transmission": raw.get("TransmissionStyle")}


def decode(vin_value: Optional[str]) -> tuple[dict, Optional[dict]]:
    """(what we know about the VIN, NHTSA's decoded fields or None). Never
    raises: an unreachable decoder just means the analysis runs on what the
    user entered."""
    import vin as vin_mod
    if not vin_value:
        return {"value": None, "valid": False, "status": "not_entered", "errors": []}, None
    val = vin_mod.validate_vin(vin_value)
    info = {"value": val.get("normalized") or vin_value, "valid": bool(val["ok"]),
            "status": "invalid", "errors": list(val.get("errors") or []),
            "source": "NHTSA vPIC (U.S. Department of Transportation)"}
    if not val["ok"]:
        return info, None
    try:
        raw = vin_mod.decode_vin(val["normalized"])
    except Exception:
        info.update(status="unavailable", errors=["the VIN decoder couldn't be reached; "
                                                  "the analysis uses what you entered"])
        return info, None
    if not raw.get("Make"):
        info.update(status="not_found",
                    errors=[raw.get("ErrorText") or "NHTSA has no vehicle for this VIN"])
        return info, None
    info.update(status="decoded", errors=[])
    return info, raw


def identity(f: dict, vin_info: dict, raw: Optional[dict]) -> dict:
    import vin as vin_mod
    dec = summarize(raw)
    entered = {k: f[k] for k in ("year", "make", "model", "trim")}
    used = {k: entered[k] or (dec or {}).get(k) for k in entered}
    cmp = vin_mod.compare_listing(raw, entered) if raw else {"mismatches": [], "message": None}
    return {"entered": entered, "decoded": dec, "used": used, "vin": vin_info,
            "filled_from_vin": [k for k in entered if not entered[k] and used[k]],
            "mismatches": cmp["mismatches"], "mismatch_message": cmp.get("message")}


def _money(x) -> str:
    return ("−$" if x < 0 else "$") + f"{abs(round(x)):,}"


def run(f: dict, ident: dict, *, settings: Optional[dict], home_zip: Optional[str],
        peers: list[dict], now: Optional[datetime] = None) -> dict:
    """The analysis, given validated inputs, identity and candidate peers."""
    import geo
    import profit
    import sniper
    import valuation
    from scam_detector import score_listing
    from sources.base import _unnegated, looks_needs_work, looks_salvage

    now = now or datetime.now(timezone.utc)
    used = ident["used"]
    car_zip = f["zip"] or home_zip
    at = geo.zip_centroid(car_zip)
    title = " ".join(str(x) for x in (used["year"], used["make"], used["model"], used["trim"]) if x)
    vin_ok = ident["vin"]["valid"]
    target = {"composite_id": f["composite_id"],
              "vehicle_key": f"vin:{ident['vin']['value']}" if vin_ok else None,
              "title": title, "year": used["year"], "make": used["make"], "model": used["model"],
              "trim": used["trim"], "odometer": f["mileage"], "price": f["asking_price"],
              "latitude": at[0] if at else None, "longitude": at[1] if at else None}
    v = valuation.value_listing(target, peers, None, now=now)
    market = v.evidence_class in MARKET_EVIDENCE and bool(v.value_mid)
    seen = {p.get("composite_id"): p.get("last_seen_at") or p.get("first_seen_at") for p in peers}
    comps = [{**c, "last_seen_at": seen.get(c.get("composite_id"))} for c in v.comps] if market else []
    stamps = sorted(str(c["last_seen_at"]) for c in comps if c["last_seen_at"])

    # ---- profit: the price you expect to pay, your costs, a resale basis ----
    pay = f["offer_price"] or f["asking_price"]
    # this car's costs over the user's full, effective assumptions: overlaying
    # raw settings would let a lone cosmetic cost trip profit.merged's
    # pre-split rule and drop the mechanical allowance
    s = {**profit.merged(settings), **f["costs"], "negotiation_pct": 0}
    dist = geo.distance_mi(home_zip, *at) if (at and home_zip and car_zip != home_zip) else None
    if market:
        basis = "market"
        pr = profit.compute(pay, v.value_low, v.value_mid, v.value_high, v.evidence_class, s,
                            scenarios=True, distance_mi=dist, dealer_share=v.dealer_share)
    elif f["resale_estimate"]:
        basis, e = "user_estimate", f["resale_estimate"]
        pr = profit.compute(pay, e, e, e, profit.USER_ESTIMATE, s, scenarios=True, distance_mi=dist)
    else:
        basis, pr = "none", None
    cb = profit.cost_basis(pay, s, dist)
    target_profit = (f["target_profit"] if f["target_profit"] is not None
                     else DEFAULT_TARGET_PROFIT)

    def max_offer(sc: dict) -> int:
        """The price that leaves target_profit in scenario `sc`: solves
        resale − target = price × (1 + tax) + fixed costs + holding."""
        room = sc["resale"] - target_profit - cb["fixed_costs"] - sc["holding"]
        return max(0, round(room / (1 + cb["tax_rate"])))

    offers, safety = {}, None
    if pr:
        base, cons = pr["scenarios"]["base"], pr["scenarios"]["conservative"]
        offers["expected"] = max_offer(base)
        if basis == "market":
            offers["conservative"] = max_offer(cons)
        if base["resale"]:
            safety = round(100 * (base["resale"] - cb["invested"]) / base["resale"], 1)

    # ---- risks and missing information ----
    flags: list[str] = []

    def flag(k: str) -> None:
        if k not in flags:
            flags.append(k)

    ts, cond = f["title_status"], f["condition"]
    if ts in ("rebuilt", "salvage", "flood", "lemon"):
        flag("branded_title")
    if ts == "flood":
        flag("flood_damage")
    if ts == "missing":
        flag("title_missing")
    if ts == "lien":
        flag("title_lien")
    text = f"{f['faults']}\n{f['description']}"
    if looks_salvage("", text):
        flag("branded_title")
    if _unnegated(FLOOD_RE, text):
        flag("flood_damage")
    if _unnegated(STRUCTURAL_RE, text):
        flag("structural_damage")
    if cond == "not_running":
        flag("not_running")
    repairs_entered = bool(f["costs"].get("mechanical"))      # cosmetic work isn't a repair estimate
    if cond == "needs_work" or looks_needs_work("", text):
        flag("needs_work")
        if not repairs_entered:
            flag("repairs_not_estimated")
    if not f["mileage"]:
        flag("mileage_unknown")
    elif sniper.mileage_suspect(used["year"], f["mileage"]):
        flag("mileage_suspect")
    if f["description"]:
        if score_listing(title, f["description"], f["asking_price"],
                         v.value_mid if market else None)[0] >= 50:
            flag("scam_signals")
    else:
        flag("description_missing")
    discount = None
    if market:
        discount = (v.value_mid - f["asking_price"]) / v.value_mid
        if discount > int(sniper.CONFIG.get("max_believable_discount_pct", 50)) / 100.0:
            flag("too_good_to_be_true")
        if f["asking_price"] > (v.value_high or v.value_mid):
            flag("priced_above_market")
        if v.confidence in ("low", "insufficient"):
            flag("low_confidence")
    else:
        flag("insufficient_data")
    if ident["vin"]["status"] == "invalid":
        flag("vin_invalid")
    if any(m["field"] in ("year", "make", "model") for m in ident["mismatches"]):
        flag("vin_mismatch")

    missing = [{"field": k, "why": why} for k, why, gap in (
        ("vin", "VIN — confirms the exact car and lets you run a history report", not f["vin"]),
        ("mileage", "Mileage — needed to compare against similar cars", not f["mileage"]),
        ("title_status", "Title status — a branded title cuts resale value", ts == "unknown"),
        ("condition", "Condition — does it run and drive?", cond == "unknown"),
        ("description", "The seller's description — title and damage wording get checked",
         not f["description"]),
        ("zip", "Where the car is — nearby comparables count more", not car_zip),
        ("repairs", "A repair estimate for the work it needs",
         "repairs_not_estimated" in flags),
        ("resale_estimate", "A market value or your own resale estimate — without one, "
                            "profit can't be estimated", basis == "none"),
    ) if gap]

    # ---- decision ----
    blocking = [k for k in flags if k in BLOCKING or k == "repairs_not_estimated"]
    reasons: list[str] = []
    if basis == "market":
        reasons.append(f"Market value from {v.n} comparable "
                       f"{'listing' if v.n == 1 else 'listings'} "
                       f"({EVIDENCE_WORDS[v.evidence_class]}), {v.confidence} confidence.")
    elif basis == "user_estimate":
        reasons.append("Resale is your own estimate, not a market value.")
    if blocking:
        cls = "high_risk"
        reasons += [f"Risk: {RISK_LABELS[k]}." for k in blocking]
    elif not pr:
        cls = "insufficient_evidence"
        reasons.append("No market value from comparables and no resale estimate of yours, "
                       "so profit can't be judged.")
    else:
        base, cons = pr["scenarios"]["base"], pr["scenarios"]["conservative"]
        checks = [
            (cons["profit"] >= STRONG_MIN_PROFIT,
             f"low-case profit {_money(cons['profit'])} "
             f"{'≥' if cons['profit'] >= STRONG_MIN_PROFIT else '<'} {_money(STRONG_MIN_PROFIT)}"),
            ((base["roi_pct"] or 0) >= STRONG_MIN_ROI_PCT,
             f"expected ROI {base['roi_pct']}% "
             f"{'≥' if (base['roi_pct'] or 0) >= STRONG_MIN_ROI_PCT else '<'} {STRONG_MIN_ROI_PCT:g}%"),
            ((safety or 0) >= STRONG_MIN_SAFETY_PCT,
             f"margin of safety {safety}% "
             f"{'≥' if (safety or 0) >= STRONG_MIN_SAFETY_PCT else '<'} {STRONG_MIN_SAFETY_PCT:g}%"),
        ]
        if base["profit"] <= 0:
            cls = "high_risk"
            reasons.append(f"The expected case loses money ({_money(base['profit'])}).")
        elif (basis == "market" and v.confidence in ("medium", "high")
              and all(ok for ok, _ in checks)):
            cls = "strong_candidate"
            reasons += [why[0].upper() + why[1:] + "." for _, why in checks]
        else:
            cls = "worth_investigating"
            reasons.append(f"Expected profit {_money(base['profit'])}.")
            if basis != "market" or v.confidence not in ("medium", "high"):
                reasons.append("Not rated strong: the value isn't backed by enough matching "
                               "comparables.")
            reasons += [why[0].upper() + why[1:] + "." for ok, why in checks if not ok]

    data_used = []
    if ident["vin"]["status"] == "decoded":
        data_used.append("VIN decoded by NHTSA vPIC (U.S. Department of Transportation)")
    if basis == "market":
        data_used.append(f"{v.n} comparable listings on FlipFirst from licensed sources and "
                         "FlipFirst sellers" + (f", last confirmed {stamps[0][:10]} to "
                                                f"{stamps[-1][:10]}" if stamps else ""))
    data_used.append("Your inputs and cost assumptions")
    return {
        "title": title, "year": used["year"], "make": used["make"], "model": used["model"],
        "trim": used["trim"], "odometer": f["mileage"], "price": f["asking_price"],
        "offer_price": f["offer_price"], "zip": car_zip, "distance_mi": dist,
        "title_status": ts, "condition": cond, "identity": ident,
        "valuation_basis": basis, "user_estimate": f["resale_estimate"],
        "evidence_class": v.evidence_class if market else "insufficient",
        "value_low": v.value_low if market else None,
        "value_mid": v.value_mid if market else None,
        "value_high": v.value_high if market else None,
        "confidence": v.confidence if market else "insufficient",
        "comp_n": v.n if market else 0,
        "discount_pct": round(discount * 100, 1) if discount is not None else None,
        "confidence_reasons": v.reasons,
        "evidence": {"comps": comps, "geography": v.geography, "comp_quality": v.comp_quality},
        "freshness": {"oldest": stamps[0], "newest": stamps[-1]} if stamps else None,
        "profit_detail": pr,
        "profit": {k: sc["profit"] for k, sc in pr["scenarios"].items()} if pr else None,
        "investment": {"purchase": round(cb["acquisition"]),
                       "costs": {k: round(x) for k, x in cb["costs"].items()},
                       "holding": round(cb["holding"]), "total": round(cb["invested"])},
        "break_even_resale": round(cb["invested"]),
        "target_profit": target_profit, "max_offer": offers, "margin_of_safety_pct": safety,
        "risk_flags": flags, "missing": missing,
        "decision": {"class": cls, "label": DECISIONS[cls], "reasons": reasons,
                     "thresholds": {"strong_min_conservative_profit": STRONG_MIN_PROFIT,
                                    "strong_min_expected_roi_pct": STRONG_MIN_ROI_PCT,
                                    "strong_min_margin_of_safety_pct": STRONG_MIN_SAFETY_PCT}},
        "data_used": data_used,
        "disclaimer": ("Estimates from the data shown, not guarantees. FlipFirst doesn't "
                       "inspect cars or verify titles, ownership or history."),
        "analyzed_at": now.isoformat(),
    }


def analyze(payload, *, settings: Optional[dict] = None, home_zip: Optional[str] = None,
            peer_filter: Optional[Callable[[dict], bool]] = None
            ) -> tuple[Optional[dict], list[str]]:
    """Validate, decode the VIN, load comparables and run the analysis."""
    import db as _db
    import valuation
    f, errs = clean(payload)
    if errs:
        return None, errs
    vin_info, raw = decode(f["vin"])
    ident = identity(f, vin_info, raw)
    used = ident["used"]
    if not (used["year"] and used["make"] and used["model"]):
        return None, ["Enter the year, make and model, or a VIN that decodes them"]
    with _db.session() as conn:
        peers = valuation.load_peers(conn, used)
    if peer_filter:
        peers = [p for p in peers if peer_filter(p)]
    return run(f, ident, settings=settings, home_zip=home_zip, peers=peers), []
