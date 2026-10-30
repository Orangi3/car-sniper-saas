"""
valuation.py — market value of a listing, with explicit evidence.

Evidence classes (never mixed or relabeled):

  confirmed_sales        completed sales with a sold price AND a sale date
                         (eBay sold listings, BaT / Cars & Bids / PCarMarket
                         results) within SOLD_WINDOW_DAYS
  asking_comparables     current asking prices of comparable listings we've
                         ingested (what sellers want — not what buyers paid)
  historical_incomplete  sales evidence missing a date, older than the
                         window, or where the "sold" price is really a last
                         asking price (MarketCheck /sales, Hemmings "sold")
  model_estimate         reserved for a licensed pricing model; nothing in
                         this codebase produces it today
  insufficient           fewer than MIN_COMPS usable comparables

Matching rules for asking comparables:
  * same make + model; model year within ±1 (±2 only to reach MIN_COMPS,
    and that lowers confidence). No depreciation curve is applied across
    years — we don't have data to justify a universal one.
  * the target itself, other listings of the same vehicle (VIN) and
    cross-posts (same year/make/model/mileage/price) are excluded or
    collapsed to one
  * salvage / rebuilt / parts / needs-work / payment-teaser listings excluded
  * auctions excluded: a running auction's price is its current bid
  * model names that cover different vehicles are split by the title's
    marker (VARIANT_MARKERS: "Silverado 2500HD" is not a "Silverado 1500",
    a "Wrangler Unlimited" is not a two-door "Wrangler"); peers of the other
    variant are excluded, never just down-weighted
  * same trim preferred when the title states one and enough peers share it;
    when most comparables state a DIFFERENT trim, confidence is capped at low
  * private-party peers preferred; dealer asking prices only as a fallback
  * stale peers (not seen recently) are down-weighted, then dropped
  * mileage: adjusted ONLY when >= MIN_MILEAGE_POINTS peers have odometers,
    using a Theil–Sen slope clamped to a sane range; otherwise not adjusted
    and that is stated. A listing more than MILEAGE_GAP_MI above every
    comparable's odometer is capped at low confidence when not adjusted (its
    value would be that of a lower-mileage car), and loses a point when the
    adjustment is extrapolated that far
  * robust statistics: MAD outlier rejection, weighted median for the point
    value, weighted 25th/75th percentiles for the range

  * geography: when the target has coordinates, peers within
    REGIONAL_RADIUS_MI are used if there are enough; otherwise nationwide
    asking prices are used and confidence drops (regional price differences
    are not modeled)
  * drivetrain / engine: matched when the target states one (structured
    provider fields only) and enough peers share it
  * every peer gets a match-quality score (year, mileage, distance, trim);
    it multiplies the freshness weight and is reported per comparable

Market segments are reported separately and never blended into one number:
private asking, dealer asking, completed sales (of which auction results),
historical/incomplete records, and a model estimate (none connected).

Confidence is a label (high / medium / low / insufficient) derived from
named evidence — sample size, spread, year span, trim match, mileage
handling, evidence class, conflicts — and every point is reported as a
reason. Without completed-sale evidence it is capped at "medium" and the
value range is widened by ASKING_UNCERTAINTY_PCT on each side. It is not a
probability and has not been calibrated against realized sale prices.
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

CONFIRMED_SALES = "confirmed_sales"
ASKING = "asking_comparables"
HISTORICAL = "historical_incomplete"
MODEL = "model_estimate"
INSUFFICIENT = "insufficient"

MIN_COMPS = 3
MIN_MILEAGE_POINTS = 6
SOLD_WINDOW_DAYS = 548            # ~18 months
STALE_FULL_WEIGHT_DAYS = 7
STALE_DROP_DAYS = 45
SLOPE_BOUNDS = (-0.30, 0.0)       # $ per mile; price never rises with miles
CONFIRMED_SOURCES = {"ebay", "bat", "carsandbids", "pcarmarket"}
AUCTION_SOURCES = {"bat", "carsandbids", "pcarmarket"}
REGIONAL_RADIUS_MI = 150
# Asking prices are not transactions: without completed sales the range is
# widened by this fraction of the value on each side (a labeled assumption).
ASKING_UNCERTAINTY_PCT = 0.03
MATCH_ATTRS = ("drivetrain", "engine")
MILEAGE_GAP_MI = 40_000           # same gap match_quality calls "far apart"

# One model name, different vehicles. Sellers of the heavier or longer
# variant almost always say so ("2500HD", "3/4 ton", "Unlimited"), so a title
# without the marker is the base variant. Matched on the title only.
_HEAVY_DUTY = (r"(?<!\$)\b(?:[2-5]500|1500\s*hd)(?:\s*hd)?\b|\bhd\b|\bheavy[\s-]*duty\b|"
               r"(?:\b3/4|\bone|\b1)[\s-]*ton\b|\bdually\b|\bdrw\b")
VARIANT_MARKERS = {
    **{key: ("heavy-duty", _HEAVY_DUTY) for key in (
        ("chevrolet", "silverado"), ("gmc", "sierra"), ("dodge", "ram"),
        ("chevrolet", "suburban"), ("chevrolet", "express"), ("gmc", "savana"),
        ("chevrolet", "avalanche"))},
    ("jeep", "wrangler"): ("Unlimited", r"\bunlimited\b|\b(?:4|four)[\s-]*door\b|\bjku\b|\bjlu\b"),
    ("nissan", "titan"): ("XD", r"\bxd\b"),
}
_VARIANT_RES = {k: (label, re.compile(rx, re.IGNORECASE))
                for k, (label, rx) in VARIANT_MARKERS.items()}

TRIMS = {
    "lx", "ex", "ex-l", "exl", "se", "le", "xle", "xse", "sr", "sr5", "trd",
    "limited", "platinum", "sport", "touring", "base", "s", "sv", "sl", "sel",
    "lt", "ltz", "ls", "rs", "ss", "z71", "xl", "xlt", "lariat", "king",
    "denali", "slt", "sle", "at4", "laredo", "overland", "trailhawk", "rubicon",
    "sahara", "gt", "premium", "hybrid", "si", "type-r", "tlx", "srt", "rt",
    "r/t", "sxt", "big", "longhorn", "tradesman", "raptor", "tremor",
}


@dataclass
class Valuation:
    evidence_class: str
    value_low: Optional[float] = None
    value_mid: Optional[float] = None
    value_high: Optional[float] = None
    n: int = 0
    confidence: str = "insufficient"
    reasons: list[str] = field(default_factory=list)
    comps: list[dict] = field(default_factory=list)
    mileage_adjusted: bool = False
    slope_per_mile: Optional[float] = None
    # share of the comparables that are DEALER retail asking prices; the
    # profit model discounts resale accordingly (dealer asks ≠ private sales)
    dealer_share: float = 0.0
    # separately reported market segments (never blended); see module doc
    segments: dict = field(default_factory=dict)
    comp_quality: Optional[float] = None      # mean match quality 0–100
    geography: str = "unknown"                # regional | national | unknown

    @property
    def basis(self) -> str:
        """Legacy two-value label kept for older API consumers."""
        return "verified" if self.evidence_class == CONFIRMED_SALES else "estimate"

    def to_dict(self) -> dict:
        return {"evidence_class": self.evidence_class, "value_low": self.value_low,
                "value_mid": self.value_mid, "value_high": self.value_high,
                "n": self.n, "confidence": self.confidence, "reasons": self.reasons,
                "comps": self.comps, "mileage_adjusted": self.mileage_adjusted,
                "slope_per_mile": self.slope_per_mile, "basis": self.basis,
                "dealer_share": self.dealer_share, "segments": self.segments,
                "comp_quality": self.comp_quality, "geography": self.geography}


# ---------- small helpers --------------------------------------------------

def parse_trim(title: str, model: Optional[str]) -> Optional[str]:
    """First recognised trim token after the model name, else None. Never
    guesses — an unrecognised word is not a trim."""
    if not title or not model:
        return None
    t = title.lower()
    # "F-150" is also written "F150" / "F 150"; "CR-V" as "CRV"
    m = re.search(re.sub(r"(\\-|\\ )", r"[\\s-]?", re.escape(model.lower())), t)
    if not m:
        return None
    for tok in re.split(r"[\s,/()]+", t[m.end():])[:4]:
        tok = tok.strip(".-")
        if tok in TRIMS:
            return tok.upper()
    return None


def model_variant(title: str, make: Optional[str], model: Optional[str]) -> Optional[str]:
    """Which variant of a multi-vehicle model name the title describes
    (VARIANT_MARKERS): the marker's label, "standard" without it, or None
    for models that have no such split."""
    hit = _VARIANT_RES.get((str(make or "").lower(), str(model or "").lower()))
    if not hit:
        return None
    label, rx = hit
    return label if rx.search(title or "") else "standard"


def _parse_ts(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _staleness_weight(last_seen, now: datetime) -> float:
    d = _parse_ts(last_seen)
    if d is None:
        return 0.5
    age = (now - d).total_seconds() / 86400
    if age <= STALE_FULL_WEIGHT_DAYS:
        return 1.0
    if age <= 21:
        return 0.6
    if age <= STALE_DROP_DAYS:
        return 0.3
    return 0.0


def _attr(row: dict, key: str) -> Optional[str]:
    """Structured attribute from the row or its provider extras (never parsed
    from free text), normalized for comparison."""
    v = row.get(key)
    if v is None:
        ex = row.get("extras")
        if isinstance(ex, str):
            try:
                ex = json.loads(ex)
            except ValueError:
                ex = None
        v = (ex or {}).get(key) if isinstance(ex, dict) else None
    v = re.sub(r"[^a-z0-9.]+", "", str(v).lower()) if v not in (None, "") else ""
    return {"4x4": "4wd", "fourwheeldrive": "4wd", "allwheeldrive": "awd",
            "frontwheeldrive": "fwd", "rearwheeldrive": "rwd"}.get(v, v) or None


def seller_kind(row: dict) -> str:
    """private | dealer | unknown. A provider that cannot say (eBay US has
    no account type) is unknown — never assumed private."""
    if row.get("is_dealer"):
        return "dealer"
    return "unknown" if _attr(row, "seller_type") == "unknown" else "private"


def _coords(row: dict) -> Optional[tuple[float, float]]:
    lat, lon = row.get("latitude"), row.get("longitude")
    try:
        return (float(lat), float(lon)) if lat is not None and lon is not None else None
    except (TypeError, ValueError):
        return None


def match_quality(c: dict, year: int, t_miles, t_trim, has_geo: bool) -> tuple[float, list]:
    """0–1 similarity of a peer to the target and the factors behind it.
    Gentle multipliers: they rank peers, they don't invent adjustments."""
    q, why = 1.0, []
    dy = abs(c["year"] - year)
    if dy:
        q *= 0.9 if dy == 1 else 0.75
        why.append(f"{dy} model year(s) off")
    if t_miles:
        if c.get("odometer"):
            d = abs(c["odometer"] - t_miles)
            if d > 40000:
                q *= 0.75
                why.append("mileage >40k apart")
            elif d > 15000:
                q *= 0.9
                why.append("mileage 15–40k apart")
        else:
            q *= 0.85
            why.append("peer mileage unknown")
    if has_geo:
        dist = c.get("_dist")
        if dist is None:
            q *= 0.9
            why.append("peer location unknown")
        elif dist > REGIONAL_RADIUS_MI:
            q *= 0.8
            why.append(f"{dist:.0f} mi away")
        elif dist > 50:
            q *= 0.95
    if t_trim:
        ct = c.get("trim") or parse_trim(c.get("title") or "", c.get("model"))
        if ct is None:
            q *= 0.9
            why.append("peer trim unknown")
        elif ct != t_trim:
            q *= 0.85
            why.append(f"different trim ({ct})")
    return q, why


def _median(vals: list) -> Optional[float]:
    return round(statistics.median(vals), 2) if vals else None


def _segments(usable: list[dict], sold: dict) -> dict:
    priv = [c["price"] for c in usable if seller_kind(c) == "private"]
    deal = [c["price"] for c in usable if seller_kind(c) == "dealer"]
    unk = [c["price"] for c in usable if seller_kind(c) == "unknown"]
    conf = sold.get(CONFIRMED_SALES) or []
    auc = [s["price"] for s in conf if s.get("source") in AUCTION_SOURCES]
    hist = [s["price"] for s in sold.get(HISTORICAL) or []]
    return {
        "private_asking": {"n": len(priv), "median": _median(priv)},
        "dealer_asking": {"n": len(deal), "median": _median(deal)},
        "unknown_seller_asking": {"n": len(unk), "median": _median(unk)},
        "confirmed_sales": {"n": len(conf), "median": _median([s["price"] for s in conf])},
        "auction_results": {"n": len(auc), "median": _median(auc)},
        "historical_incomplete": {"n": len(hist), "median": _median(hist)},
        "model_estimate": {"n": 0, "median": None,
                           "note": "no licensed pricing model connected"},
    }


def _wquantile(pairs: list[tuple[float, float]], q: float) -> float:
    """Weighted quantile of (value, weight) pairs."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * total:
            return v
    return pairs[-1][0]


def _mad_filter(pairs: list[tuple[float, float, dict]]) -> tuple[list, int]:
    vals = [p[0] for p in pairs]
    if len(vals) < 4:
        return pairs, 0
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals) * 1.4826
    if mad == 0:
        return pairs, 0
    kept = [p for p in pairs if abs(p[0] - med) <= 3 * mad]
    return kept, len(pairs) - len(kept)


def theil_sen_slope(points: list[tuple[float, float]]) -> Optional[float]:
    slopes = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            dx = points[j][0] - points[i][0]
            if abs(dx) >= 1000:
                slopes.append((points[j][1] - points[i][1]) / dx)
    return statistics.median(slopes) if slopes else None


def cross_post_key(c: dict) -> tuple:
    """Same physical car posted twice: identical VIN, or identical
    year/make/model/price and (when known) mileage within 500 mi. Never
    year/make/model/price alone when mileage is missing AND titles differ."""
    if c.get("vehicle_key"):
        return ("vk", c["vehicle_key"])
    odo = c.get("odometer")
    base = (c.get("year"), str(c.get("make") or "").lower(),
            str(c.get("model") or "").lower(), c.get("price"))
    if odo:
        return ("fp",) + base + (int(odo) // 500,)
    return ("fp-title",) + base + (re.sub(r"\W+", " ", str(c.get("title") or "")
                                          .lower()).strip(),)


# ---------- sold-comp classification -------------------------------------

def classify_sold_items(raw: dict, year: int, make: str, model: str,
                        now: Optional[datetime] = None) -> dict[str, list[dict]]:
    """Split comps.py's raw per-source items into evidence classes."""
    from comps import _comp_match_pct, _parse_sale_date
    now = now or datetime.now(timezone.utc)
    out = {CONFIRMED_SALES: [], HISTORICAL: []}
    for src, items in (raw or {}).items():
        if src == "copart":
            continue                       # salvage auctions: separate floor
        for it in items or []:
            price = it.get("price")
            if not price or not (1000 <= price <= 250_000):
                continue
            if _comp_match_pct(it, year, make, model) < 70:
                continue
            dt = _parse_sale_date(it.get("end_date"))
            rec = {"price": price, "title": it.get("title"), "url": it.get("url"),
                   "source": src, "sale_date": dt.date().isoformat() if dt else None}
            recent = dt is not None and (now - dt).days <= SOLD_WINDOW_DAYS
            if src in CONFIRMED_SOURCES and recent:
                out[CONFIRMED_SALES].append(rec)
            else:
                rec["why_incomplete"] = ("no sale date" if dt is None else
                                         "older than window" if not recent else
                                         "last asking price, not a verified sale")
                out[HISTORICAL].append(rec)
    return out


# ---------- core ----------------------------------------------------------

def _value_from(pairs: list[tuple[float, float, dict]]) -> tuple[float, float, float]:
    vw = [(v, w) for v, w, _ in pairs]
    return (_wquantile(vw, 0.25), _wquantile(vw, 0.5), _wquantile(vw, 0.75))


def _confidence(points: int) -> str:
    return "high" if points >= 4 else "medium" if points >= 2 else "low"


def value_listing(target: dict, peers: list[dict], sold: Optional[dict] = None,
                  now: Optional[datetime] = None) -> Valuation:
    """Value `target` (a listings row as dict) from asking-price `peers`
    (listings rows, same make/model, any year) and optional classified
    `sold` evidence ({confirmed_sales: [...], historical_incomplete: [...]})."""
    now = now or datetime.now(timezone.utc)
    sold = sold or {}
    year = target.get("year")
    tid = target.get("composite_id")
    tkey = target.get("vehicle_key")
    t_trim = target.get("trim") or parse_trim(target.get("title") or "", target.get("model"))
    t_miles = target.get("odometer")
    t_var = model_variant(target.get("title") or "", target.get("make"), target.get("model"))

    # ---- asking comparables ----
    usable = []
    other_variant = 0
    for c in peers:
        if c.get("composite_id") == tid:
            continue
        if tkey and c.get("vehicle_key") == tkey:
            continue
        if c.get("is_salvage") or c.get("needs_work") or not c.get("price"):
            continue
        if c.get("is_auction"):
            continue                       # a current bid is not an asking price
        if t_var and model_variant(c.get("title") or "", c.get("make") or target.get("make"),
                                   c.get("model") or target.get("model")) != t_var:
            other_variant += 1
            continue
        if not c.get("year") or abs(c["year"] - year) > 2:
            continue
        if not (1000 <= c["price"] <= 250_000):
            continue
        w = _staleness_weight(c.get("last_seen_at") or c.get("first_seen_at"), now)
        if w <= 0:
            continue
        usable.append({**c, "_w": w})

    # collapse cross-posts: keep the most recently seen copy
    by_key: dict = {}
    for c in usable:
        k = cross_post_key(c)
        cur = by_key.get(k)
        if cur is None or str(c.get("last_seen_at") or "") > str(cur.get("last_seen_at") or ""):
            by_key[k] = c
    collapsed = len(usable) - len(by_key)
    usable = list(by_key.values())

    reasons: list[str] = []
    points = 0
    cap_low: list[str] = []          # reasons that cap confidence at "low"
    if other_variant:
        reasons.append(f"{other_variant} peer(s) excluded: a different vehicle than this "
                       f"{t_var} {target.get('model')} (model name shared)")

    # geography: regional peers when the target's location is known
    t_geo = _coords(target)
    geography = "unknown"
    if t_geo:
        from geo import haversine_mi
        for c in usable:
            cg = _coords(c)
            c["_dist"] = haversine_mi(t_geo, cg) if cg else None
        local = [c for c in usable
                 if c["_dist"] is not None and c["_dist"] <= REGIONAL_RADIUS_MI]
        if len(local) >= MIN_COMPS:
            usable, geography = local, "regional"
            reasons.append(f"regional comparables within {REGIONAL_RADIUS_MI} mi")
        elif usable:
            geography = "national"
            points -= 1
            reasons.append(f"only {len(local)} peer(s) within {REGIONAL_RADIUS_MI} mi; "
                           "wider-area asking prices used (regional differences not adjusted)")
    segments = _segments(usable, sold)

    # private-party first
    private = [c for c in usable if seller_kind(c) == "private"]
    dealer_fallback = False
    if len(private) >= MIN_COMPS:
        usable = private
    elif usable and len(private) < len(usable):
        dealer_fallback = True

    # model-year window: ±1 preferred
    tight = [c for c in usable if abs(c["year"] - year) <= 1]
    wide_years = False
    if len(tight) >= MIN_COMPS:
        usable = tight
    else:
        wide_years = True

    # trim
    trim_matched = False
    if t_trim:
        same = [c for c in usable
                if (c.get("trim") or parse_trim(c.get("title") or "", c.get("model"))) == t_trim]
        if len(same) >= MIN_COMPS:
            usable, trim_matched = same, True

    # structured drivetrain / engine (provider fields only)
    for key in MATCH_ATTRS:
        tv = _attr(target, key)
        if not tv:
            continue
        same = [c for c in usable if _attr(c, key) == tv]
        if len(same) >= MIN_COMPS:
            usable = same
            reasons.append(f"{key}-matched ({tv})")
        else:
            reasons.append(f"{key} not matched (too few {tv} peers)")

    # mileage
    slope, adjusted = None, False
    with_miles = [c for c in usable if c.get("odometer")]
    if t_miles and len(with_miles) >= MIN_MILEAGE_POINTS:
        slope = theil_sen_slope([(c["odometer"], c["price"]) for c in with_miles])
        if slope is not None:
            slope = max(SLOPE_BOUNDS[0], min(SLOPE_BOUNDS[1], slope))
            adjusted = slope < 0          # 0 = no detectable mileage effect
        if not adjusted:
            slope = None

    pairs = []
    for c in usable:
        v = float(c["price"])
        if adjusted and c.get("odometer"):
            v += slope * (t_miles - c["odometer"])
        elif adjusted:
            continue                       # can't adjust a peer w/o mileage
        q, why = match_quality(c, year, t_miles, t_trim, bool(t_geo))
        c["_q"], c["_qwhy"] = q, why
        pairs.append((v, c["_w"] * q, c))
    pairs, outliers = _mad_filter(pairs)

    asking_val = None
    if len(pairs) >= MIN_COMPS:
        asking_val = _value_from(pairs)

    confirmed = sold.get(CONFIRMED_SALES) or []
    historical = sold.get(HISTORICAL) or []

    # ---- choose the evidence class ----
    if len(confirmed) >= MIN_COMPS:
        cp = [(float(s["price"]), 1.0, s) for s in confirmed]
        cp, c_out = _mad_filter(cp)
        lo, mid, hi = _value_from(cp)
        v = Valuation(CONFIRMED_SALES, lo, mid, hi, n=len(cp))
        points += 1
        reasons.append(f"{len(cp)} completed sales with sold prices and dates")
        if c_out:
            reasons.append(f"{c_out} outlier sale(s) excluded")
        if len(cp) >= 10:
            points += 2
        elif len(cp) >= 5:
            points += 1
        if t_miles:
            reasons.append("sold comps not mileage-adjusted (sale mileage rarely published)")
        if asking_val and abs(asking_val[1] - mid) / mid > 0.25:
            points -= 1
            reasons.append(f"conflict: local asking prices center on ${asking_val[1]:,.0f} "
                           f"vs ${mid:,.0f} in completed sales")
        v.comps = [{"price": s["price"], "title": s.get("title"), "url": s.get("url"),
                    "source": s.get("source"), "sale_date": s.get("sale_date"),
                    "kind": "sold"} for _, _, s in cp][:15]
    elif asking_val:
        lo, mid, hi = asking_val
        v = Valuation(ASKING, lo, mid, hi, n=len(pairs))
        reasons.append(f"{len(pairs)} comparable asking prices (what sellers ask, "
                       "not completed sales)")
        v.comps = [{"composite_id": c.get("composite_id"), "price": c["price"],
                    "adjusted_value": round(val), "title": c.get("title"),
                    "url": c.get("url"), "year": c.get("year"),
                    "odometer": c.get("odometer"), "source": c.get("source"),
                    "weight": round(w, 2), "kind": "asking",
                    "seller_type": seller_kind(c),
                    "distance_mi": (round(c["_dist"]) if c.get("_dist") is not None
                                    else None),
                    "match_quality": round(100 * c["_q"]),
                    "match_notes": c["_qwhy"]}
                   for val, w, c in sorted(pairs, key=lambda p: -p[1])][:15]
    elif len(historical) >= MIN_COMPS:
        hp = [(float(s["price"]), 1.0, s) for s in historical]
        hp, _ = _mad_filter(hp)
        lo, mid, hi = _value_from(hp)
        v = Valuation(HISTORICAL, lo, mid, hi, n=len(hp))
        points -= 1
        reasons.append(f"only {len(hp)} undated, old or unverified sale records")
        v.comps = [{**s, "kind": "historical"} for _, _, s in hp][:15]
        v.segments, v.geography = segments, geography
        return _finish(v, points, reasons)
    else:
        n = len(pairs)
        return Valuation(INSUFFICIENT, n=n, confidence="insufficient",
                         reasons=reasons + [f"only {n} usable comparable(s); need {MIN_COMPS}"],
                         segments=segments, geography=geography)
    v.segments, v.geography = segments, geography

    if v.evidence_class == ASKING:
        if v.n >= 10:
            points += 2
        elif v.n >= 5:
            points += 1
    spread = (v.value_high - v.value_low) / v.value_mid if v.value_mid else 1
    if spread <= 0.15:
        points += 1
        reasons.append(f"tight price spread ({spread:.0%} of value)")
    elif spread > 0.35:
        points -= 1
        reasons.append(f"wide price spread ({spread:.0%} of value)")
    if v.evidence_class == ASKING:
        if wide_years:
            reasons.append("includes model years up to ±2 (no cross-year adjustment)")
        else:
            points += 1
            reasons.append("model years within ±1")
        if trim_matched:
            points += 1
            reasons.append(f"trim-matched ({t_trim})")
        elif t_trim:
            reasons.append(f"not enough {t_trim}-trim peers; mixed trims used")
            other = [ct for _, _, c in pairs
                     if (ct := c.get("trim") or parse_trim(c.get("title") or "", c.get("model")))
                     and ct != t_trim]
            if 2 * len(other) > len(pairs):
                cap_low.append(f"most comparables are a different trim "
                               f"({', '.join(sorted(set(other)))}) than this {t_trim}")
        else:
            reasons.append("trim not stated in title; mixed trims")
        if adjusted:
            reasons.append(f"mileage-adjusted at ${-slope:.3f}/mile "
                           f"(from {len(with_miles)} peers)")
        elif not t_miles:
            points -= 1
            reasons.append("listing mileage unknown")
        elif len(with_miles) >= MIN_MILEAGE_POINTS:
            reasons.append("no mileage effect detectable among peers; not adjusted")
        else:
            reasons.append("not mileage-adjusted (too few peers with mileage)")
        odos = [c["odometer"] for _, _, c in pairs if c.get("odometer")]
        if t_miles and len(odos) >= MIN_COMPS:
            above = t_miles - max(odos) > MILEAGE_GAP_MI
            if above and not adjusted:
                cap_low.append(f"listing has {t_miles:,} mi, over {MILEAGE_GAP_MI // 1000}k more "
                               f"than any comparable (max {max(odos):,}); value not adjusted")
            elif adjusted and (above or min(odos) - t_miles > MILEAGE_GAP_MI):
                points -= 1
                reasons.append(f"mileage adjustment extrapolated: listing has {t_miles:,} mi, "
                               f"comparables {min(odos):,}–{max(odos):,}")
        if dealer_fallback:
            points -= 1
            reasons.append("includes dealer or unknown-seller asking prices "
                           "(often retail, usually higher)")
        if collapsed:
            reasons.append(f"{collapsed} duplicate/cross-posted peer(s) collapsed")
        if outliers:
            reasons.append(f"{outliers} outlier peer(s) excluded")
    v.mileage_adjusted, v.slope_per_mile = adjusted, slope
    if v.evidence_class == ASKING and pairs:
        # unknown sellers are discounted like dealers: we can't show they're private
        v.dealer_share = round(sum(1 for _, _, c in pairs if seller_kind(c) != "private")
                               / len(pairs), 2)
        v.comp_quality = round(100 * sum(c["_q"] for _, _, c in pairs) / len(pairs), 1)
        reasons.append(f"average comparable match quality {v.comp_quality:.0f}/100")
    if cap_low:
        points = min(points, 1)
        reasons += [f"confidence capped at low: {why}" for why in cap_low]
    return _finish(v, points, reasons)


def _finish(v: Valuation, points: int, reasons: list[str]) -> Valuation:
    v.confidence = _confidence(points)
    if v.evidence_class in (ASKING, HISTORICAL) and v.value_mid:
        if v.confidence == "high":
            v.confidence = "medium"
            reasons.append("confidence capped at medium: no completed-sale evidence")
        w = v.value_mid * ASKING_UNCERTAINTY_PCT
        v.value_low, v.value_high = v.value_low - w, v.value_high + w
        reasons.append(f"range widened ±{ASKING_UNCERTAINTY_PCT:.0%}: asking prices "
                       "are not transaction prices")
    v.reasons = reasons
    for attr in ("value_low", "value_mid", "value_high"):
        val = getattr(v, attr)
        if val is not None:
            setattr(v, attr, round(val, 2))
    return v


# ---------- DB access -----------------------------------------------------

def load_peers(conn, target: dict) -> list[dict]:
    rows = conn.execute(
        "SELECT composite_id, source, title, url, price, year, make, model, "
        "  odometer, is_dealer, is_salvage, needs_work, is_auction, vehicle_key, trim, "
        "  first_seen_at, last_seen_at, latitude, longitude, extras "
        "FROM listings WHERE LOWER(make) = LOWER(?) AND LOWER(model) = LOWER(?) "
        "  AND year BETWEEN ? AND ? AND price IS NOT NULL",
        (target["make"], target["model"], target["year"] - 2, target["year"] + 2)
    ).fetchall()
    return [dict(r) for r in rows]


def evidence_json(v: Valuation) -> str:
    return json.dumps({"comps": v.comps, "mileage_adjusted": v.mileage_adjusted,
                       "slope_per_mile": v.slope_per_mile,
                       "dealer_share": v.dealer_share, "segments": v.segments,
                       "comp_quality": v.comp_quality, "geography": v.geography},
                      default=str)
