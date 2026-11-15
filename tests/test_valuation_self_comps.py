"""
tests/test_valuation_self_comps.py — one car posted on several marketplaces
is not market evidence for itself (valuation.value_listing).

Only same-VIN postings were excluded from a car's comparables. The same car
posted without a VIN on another marketplace counted as an independent
comparable: it filled the MIN_COMPS = 3 floor, the n >= 5 / n >= 10
confidence points, the same-trim, regional and private-party thresholds,
and moved the median toward whatever the seller asks elsewhere. With two
real comparables, a car's own second posting produced a TRUSTED "23% below
market" alert. The exclusion reuses the tested conservative identity of
vehicle_identity.same_vehicle; lookalikes stay evidence.

The core oracle: valuing a car with its other postings present must equal
valuing it as if they did not exist (zero influence).

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json

from sources.base import NormalizedListing
from tests.test_matching_and_history import make_user, q, score_all, world  # noqa: F401
from tests.test_send_time_check import _deliver, pushes  # noqa: F401

VIN_A = "1HGCM82633A004352"
VIN_B = "1M8GDM9AXKP042788"
EXACT = 101337                     # an exact odometer reading identifies a car
ROUND = 100000                     # "100k" does not
T = "craigslist:t"


def A(sid, price, source="craigslist", odo=EXACT, zip_code="35401",
      title="2014 Honda Accord EX", desc="clean title, one owner", **kw):
    return NormalizedListing(
        source=source, source_id=sid, url=f"https://example.com/{source}/{sid}",
        title=f"{title} - ${price:,}", price=price, year=2014, make="Honda", model="Accord",
        odometer=odo, description=desc, is_cash_only=True, state="AL", zip=zip_code, **kw)


def twin(sid="tw", price=10900, source="marketcheck_fsbo", zip_code="35404", **kw):
    return A(sid, price, source=source, zip_code=zip_code, **kw)


PEERS2 = [A("p1", 10000, odo=95000), A("p2", 10400, odo=99000)]
PEERS3 = PEERS2 + [A("p3", 9800, odo=104000)]
PEERS4 = PEERS3 + [A("p4", 10200, odo=108000)]


def _nm(cid=T):
    r = q("SELECT comp_n, comp_avg, value_low, value_high, discount_pct, confidence, trust, "
          "evidence_class, evidence, confidence_reasons FROM near_misses "
          "WHERE composite_id = ?", (cid,))[0]
    return {"n": r["comp_n"], "value": None if r["comp_avg"] is None else round(r["comp_avg"]),
            "discount": r["discount_pct"], "confidence": r["confidence"], "trust": r["trust"],
            "evidence": r["evidence_class"],
            "comps": {c.get("composite_id")
                      for c in json.loads(r["evidence"] or "{}").get("comps", [])},
            "reasons": " | ".join(json.loads(r["confidence_reasons"] or "[]"))}


def _scored(world, market, target):
    world.ingest(market)
    world.ingest([target])
    score_all()
    return _nm(f"{target.source}:{target.source_id}")


def _zero_influence(cid, others):
    """Value `cid` against every stored peer, and against the peers minus
    `others` (its other postings): the two valuations must be identical."""
    import db
    import valuation
    with db.session() as c:
        target = dict(c.execute("SELECT * FROM listings WHERE composite_id = ?",
                                (cid,)).fetchone())
        peers = valuation.load_peers(c, target)
    with_twins = valuation.value_listing(target, peers)
    without = valuation.value_listing(target, [p for p in peers
                                               if p["composite_id"] not in others])
    key = ("evidence_class", "n", "value_low", "value_mid", "value_high", "confidence")
    assert [getattr(with_twins, k) for k in key] == [getattr(without, k) for k in key]
    return with_twins


# ---------- positive identity: excluded ----------------------------------------

def test_same_vin_posting_has_no_influence(world):
    nm = _scored(world, PEERS3 + [twin(desc=f"VIN {VIN_A}")], A("t", 8000, desc=f"VIN {VIN_A}"))
    assert (nm["n"], nm["value"], nm["discount"]) == (3, 10000, 20.0)
    assert "marketcheck_fsbo:tw" not in nm["comps"]
    _zero_influence(T, {"marketcheck_fsbo:tw"})


def test_no_vin_posting_of_the_same_car_has_no_influence(world):
    """Before: n 5, value $10,200, 21.57% below (the $10,900 copy counted).
    After: exactly the valuation of the same market without the copy (n 4,
    $10,000, 20.0% — measured on the unmodified code with the copy absent)."""
    nm = _scored(world, PEERS4 + [twin()], A("t", 8000))
    assert (nm["n"], nm["value"], nm["discount"], nm["confidence"]) == (4, 10000, 20.0, "medium")
    assert "marketcheck_fsbo:tw" not in nm["comps"]
    assert "1 posting(s) of this same car on other marketplaces excluded" in nm["reasons"]
    _zero_influence(T, {"marketcheck_fsbo:tw"})


def test_three_postings_of_one_car(world):
    """Before: n 5, value $10,400, 23.08% below (both copies counted)."""
    market = PEERS3 + [twin("tw1"), twin("tw2", price=10700, source="ebay", zip_code="35473")]
    nm = _scored(world, market, A("t", 8000))
    assert (nm["n"], nm["value"], nm["discount"]) == (3, 10000, 20.0)
    assert not nm["comps"] & {"marketcheck_fsbo:tw1", "ebay:tw2"}
    assert "2 posting(s) of this same car" in nm["reasons"]
    _zero_influence(T, {"marketcheck_fsbo:tw1", "ebay:tw2"})


def test_vin_on_one_posting_only(world):
    """The copy states a VIN, this posting does not: no conflict, so the
    attribute identity applies. Before: n 4 (the copy counted)."""
    nm = _scored(world, PEERS3 + [twin(desc=f"VIN {VIN_A} clean title")], A("t", 8000))
    assert (nm["n"], nm["value"], nm["discount"]) == (3, 10000, 20.0)
    assert "marketcheck_fsbo:tw" not in nm["comps"]
    _zero_influence(T, {"marketcheck_fsbo:tw"})


def test_own_posting_cannot_choose_the_comparable_set(world):
    """Two EX and two LX comparables: the EX copy made the third EX peer, so
    the valuation switched to a trim-matched set. Before: n 3, $10,400,
    23.08% below. After: mixed trims, n 4, $10,000, 20.0%."""
    lx = "2014 Honda Accord LX"
    market = [A("e1", 10000, odo=95000), A("e2", 10400, odo=99000),
              A("l1", 9600, odo=97000, title=lx), A("l2", 9500, odo=103000, title=lx), twin()]
    nm = _scored(world, market, A("t", 8000))
    assert (nm["n"], nm["value"], nm["discount"], nm["confidence"]) == (4, 10000, 20.0, "medium")
    assert "not enough EX-trim peers" in nm["reasons"]
    _zero_influence(T, {"marketcheck_fsbo:tw"})


def test_a_cars_own_posting_cannot_make_a_valuation(client, world, pushes):
    """Two real comparables plus the car's own second posting. Before: n 3,
    value $10,400, 23.08% below, medium, TRUSTED, one alert pushed."""
    uid = make_user(client, "b@example.com", searches=[("accord", {"model": "accord"})])
    nm = _scored(world, PEERS2 + [twin()], A("t", 8000))
    _deliver()
    assert (nm["evidence"], nm["value"], nm["trust"]) == ("insufficient", None, "risk")
    assert nm["n"] == 2
    assert len(q("SELECT 1 FROM user_matches WHERE user_id = ?", (uid,))) == 0
    assert pushes["sent"] == []


# ---------- negative identity: lookalikes stay evidence ---------------------------

def _stays_evidence(world, market, target, lookalike_cid, n):
    nm = _scored(world, market, target)
    assert nm["n"] == n and lookalike_cid in nm["comps"], nm
    assert "same car on other marketplaces" not in nm["reasons"]


def test_different_trim_stays_evidence(world):
    # two EX peers only, so mixed trims are used and the LX car must count
    _stays_evidence(world, PEERS2 + [twin(title="2014 Honda Accord LX")], A("t", 8000),
                    "marketcheck_fsbo:tw", 3)


def test_different_mileage_stays_evidence(world):
    _stays_evidence(world, PEERS3 + [twin(odo=EXACT + 443)], A("t", 8000),
                    "marketcheck_fsbo:tw", 4)


def test_missing_odometer_stays_evidence(world):
    _stays_evidence(world, PEERS3 + [twin(odo=None)], A("t", 8000), "marketcheck_fsbo:tw", 4)


def test_different_first_photo_stays_evidence(world):
    _stays_evidence(world, PEERS3 + [twin(extras={"photo_hash": "ph-2"})],
                    A("t", 8000, extras={"photo_hash": "ph-1"}), "marketcheck_fsbo:tw", 4)


def test_conflicting_vins_stay_evidence(world):
    _stays_evidence(world, PEERS3 + [twin(desc=f"VIN {VIN_B}")],
                    A("t", 8000, desc=f"VIN {VIN_A}"), "marketcheck_fsbo:tw", 4)


def test_geographically_incompatible_stays_evidence(world):
    _stays_evidence(world, PEERS3 + [twin(zip_code="35203")], A("t", 8000),   # Birmingham
                    "marketcheck_fsbo:tw", 4)


def test_separate_car_at_a_similar_price_stays_evidence(world):
    # both near the market, so only identity (not the outlier filter) could drop it
    _stays_evidence(world, PEERS3 + [twin(price=9650, odo=117204)], A("t", 9600),
                    "marketcheck_fsbo:tw", 4)


def test_same_source_lookalike_stays_evidence(world):
    _stays_evidence(world, PEERS3 + [A("lk", 10900, zip_code="35404")], A("t", 8000),
                    "craigslist:lk", 4)


def test_common_rounded_odometer_stays_evidence(world):
    _stays_evidence(world, PEERS3 + [twin(odo=ROUND)], A("t", 8000, odo=ROUND),
                    "marketcheck_fsbo:tw", 4)


# ---------- outcomes around price changes and alerts --------------------------------

def test_copy_repriced_still_excluded_both_ways(world):
    import sniper
    _scored(world, PEERS4 + [twin()], A("t", 8000))
    world.ingest([twin(price=9950)])                      # the copy is repriced
    score_all()
    tw = _nm("marketcheck_fsbo:tw")
    assert T not in tw["comps"] and tw["n"] == 4          # the copy is not valued from T
    sniper._score_one_listing(T)                          # nor T from the copy
    nm = _nm()
    assert (nm["n"], nm["value"]) == (4, 10000) and "marketcheck_fsbo:tw" not in nm["comps"]


def test_alert_counts_with_two_cheap_postings_of_one_car(client, world, pushes):
    """Both postings look like deals: each is valued on the four real
    comparables and the buyer is alerted once — Phase II's suppression is
    unaffected by the valuation change."""
    uid = make_user(client, "b@example.com", searches=[("accord", {"model": "accord"})])
    world.ingest(PEERS4)
    world.ingest([A("t", 8000)])
    score_all()
    world.ingest([twin(price=8100)])
    score_all()
    _deliver()
    assert _nm()["n"] == 4 and _nm("marketcheck_fsbo:tw")["n"] == 4
    assert len(q("SELECT 1 FROM user_matches WHERE user_id = ?", (uid,))) == 1
    assert len(pushes["sent"]) == 1
    assert [r["reason"] for r in q("SELECT reason FROM alert_suppressions")] \
        == ["same_vehicle_no_vin"]
