"""
tests/test_valuation.py — evidence classes, comparable matching and
confidence (P1). Pure functions; no network, no DB.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import valuation as V

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(days=1)).isoformat()


def peer(i, price, year=2015, odo=None, **kw):
    return {"composite_id": f"cl:p{i}", "source": "craigslist", "title":
            kw.pop("title", f"{year} Toyota Camry"), "price": price, "year": year,
            "make": "Toyota", "model": "Camry", "odometer": odo,
            "last_seen_at": kw.pop("seen", FRESH), **kw}


TARGET = {"composite_id": "cl:target", "title": "2015 Toyota Camry", "price": 7000,
          "year": 2015, "make": "Toyota", "model": "Camry", "odometer": None}


def test_target_is_never_its_own_comparable():
    peers = [peer(i, 10000 + i * 100) for i in range(5)]
    peers.append({**TARGET, "last_seen_at": FRESH})
    v = V.value_listing(TARGET, peers, now=NOW)
    assert v.n == 5
    assert all(c["composite_id"] != "cl:target" for c in v.comps)


def test_identical_untitled_reposts_collapse():
    """Same title + same price + no mileage is treated as one car reposted
    (it can't be told apart), so it counts once."""
    v = V.value_listing(TARGET, [peer(i, 10000) for i in range(5)], now=NOW)
    assert v.evidence_class == V.INSUFFICIENT


def test_same_vin_and_cross_posts_collapse_to_one():
    t = {**TARGET, "vehicle_key": "vin:X"}
    peers = [peer(i, 10000 + i * 300, odo=90000 + i * 5000) for i in range(4)]
    peers.append(peer(9, 7100, vehicle_key="vin:X"))            # the target, other site
    peers.append({**peers[0], "composite_id": "ebay:dup", "source": "ebay"})  # cross-post
    v = V.value_listing(t, peers, now=NOW)
    assert v.n == 4
    assert any("collapsed" in r for r in v.reasons)


def test_salvage_needs_work_and_stale_peers_excluded():
    peers = [peer(i, 10000 + i * 10) for i in range(3)]
    peers += [peer(10, 3000, is_salvage=1), peer(11, 2500, needs_work=1),
              peer(12, 4000, seen=(NOW - timedelta(days=60)).isoformat())]
    v = V.value_listing(TARGET, peers, now=NOW)
    assert v.n == 3 and v.value_mid == 10010


def test_insufficient_when_fewer_than_three():
    v = V.value_listing(TARGET, [peer(1, 9000), peer(2, 9100)], now=NOW)
    assert v.evidence_class == V.INSUFFICIENT and v.value_mid is None
    assert v.confidence == "insufficient"


def test_trim_matched_when_enough_same_trim_peers():
    t = {**TARGET, "title": "2015 Toyota Camry XLE"}
    peers = [peer(i, 12000 + i * 100, title="2015 Toyota Camry XLE") for i in range(3)]
    peers += [peer(10 + i, 8000 + i * 100, title="2015 Toyota Camry LE") for i in range(5)]
    v = V.value_listing(t, peers, now=NOW)
    assert 12000 <= v.value_mid <= 12200
    assert any("trim-matched (XLE)" in r for r in v.reasons)


def test_mileage_adjustment_only_with_enough_data():
    # price falls ~$0.05/mile
    peers = [peer(i, int(15000 - 0.05 * (40000 + i * 20000)), odo=40000 + i * 20000)
             for i in range(8)]
    low = V.value_listing({**TARGET, "odometer": 40000}, peers, now=NOW)
    high = V.value_listing({**TARGET, "odometer": 180000}, peers, now=NOW)
    assert low.mileage_adjusted and high.mileage_adjusted
    assert low.value_mid > high.value_mid + 5000
    assert -0.06 < low.slope_per_mile < -0.04
    sparse = V.value_listing({**TARGET, "odometer": 40000}, peers[:4], now=NOW)
    assert not sparse.mileage_adjusted
    assert any("not mileage-adjusted" in r for r in sparse.reasons)


def test_outliers_rejected():
    peers = [peer(i, 10000 + i * 50) for i in range(8)] + [peer(99, 45000)]
    v = V.value_listing(TARGET, peers, now=NOW)
    assert v.value_high < 11000
    assert any("outlier" in r for r in v.reasons)


def test_dealer_fallback_lowers_confidence_and_is_stated():
    peers = [peer(i, 13000 + i * 50, is_dealer=1) for i in range(4)] + [peer(9, 9000)]
    v = V.value_listing(TARGET, peers, now=NOW)
    assert any("dealer asking prices" in r for r in v.reasons)


def test_years_beyond_one_flagged_not_depreciated():
    peers = [peer(i, 10000 + i * 10, year=2013) for i in range(3)]
    v = V.value_listing(TARGET, peers, now=NOW)
    assert v.value_mid == 10010                   # no invented depreciation
    assert any("±2" in r for r in v.reasons)


def test_asking_only_confidence_capped_at_medium_and_range_widened():
    many = [peer(i, 10000 + (i % 5) * 100, odo=80000 + i * 3000) for i in range(12)]
    v = V.value_listing({**TARGET, "odometer": 100000}, many, now=NOW)
    assert v.confidence == "medium"               # would be "high" on points
    assert any("capped at medium" in r for r in v.reasons)
    w = v.value_mid * V.ASKING_UNCERTAINTY_PCT
    assert v.value_high - v.value_low >= 2 * w
    assert any("range widened" in r for r in v.reasons)


def test_confirmed_sales_can_reach_high_and_are_not_widened():
    d = (NOW - timedelta(days=20)).date().isoformat()
    sold = {V.CONFIRMED_SALES: [{"price": 10000 + (i % 3) * 100, "source": "ebay",
                                 "sale_date": d} for i in range(12)]}
    v = V.value_listing(TARGET, [], sold, now=NOW)
    assert v.evidence_class == V.CONFIRMED_SALES and v.confidence == "high"
    assert not any("range widened" in r for r in v.reasons)
    few = V.value_listing(TARGET, [peer(i, 7000 + i * 3000) for i in range(3)], now=NOW)
    assert few.confidence == "low"
    assert any("listing mileage unknown" in r for r in few.reasons)


# ---------- sold evidence classification ---------------------------------

def _raw():
    d = "10/01/2026"
    return {
        "ebay": [{"price": 11000, "title": "2015 Toyota Camry SE", "end_date": "Sep 20, 2026"},
                 {"price": 11500, "title": "2015 Toyota Camry LE", "end_date": "Oct 2, 2026"},
                 {"price": 10800, "title": "2015 Toyota Camry", "end_date": None}],
        "bat": [{"price": 12000, "title": "2015 Toyota Camry XSE", "end_date": d}],
        "marketcheck": [{"price": 9900, "title": "2015 Toyota Camry",
                         "end_date": "2026-10-01"}],
        "hemmings": [{"price": 9800, "title": "2015 Toyota Camry", "end_date": d}],
        "carsandbids": [{"price": 9000, "title": "2015 Toyota Camry", "end_date": "01/05/2023"}],
        "copart": [{"price": 2000, "title": "2015 TOYOTA CAMRY", "end_date": d}],
    }


def test_only_dated_completed_sales_are_confirmed():
    c = V.classify_sold_items(_raw(), 2015, "Toyota", "Camry", now=NOW)
    conf = sorted(x["price"] for x in c[V.CONFIRMED_SALES])
    assert conf == [11000, 11500, 12000]
    hist = {(x["source"], x["why_incomplete"]) for x in c[V.HISTORICAL]}
    assert ("ebay", "no sale date") in hist
    assert ("carsandbids", "older than window") in hist
    assert ("marketcheck", "last asking price, not a verified sale") in hist
    assert ("hemmings", "last asking price, not a verified sale") in hist
    assert all(x["source"] != "copart" for x in c[V.CONFIRMED_SALES] + c[V.HISTORICAL])


def test_confirmed_sales_win_and_conflicts_are_reported():
    sold = V.classify_sold_items(_raw(), 2015, "Toyota", "Camry", now=NOW)
    asking = [peer(i, 16000 + i * 100) for i in range(6)]
    v = V.value_listing(TARGET, asking, sold, now=NOW)
    assert v.evidence_class == V.CONFIRMED_SALES and v.basis == "verified"
    assert 11000 <= v.value_mid <= 12000
    assert any(r.startswith("conflict") for r in v.reasons)
    assert all(c["kind"] == "sold" and c["sale_date"] for c in v.comps)


def test_historical_only_is_labelled_and_low_confidence():
    sold = {V.CONFIRMED_SALES: [], V.HISTORICAL: [
        {"price": 9000 + i * 100, "source": "marketcheck"} for i in range(4)]}
    v = V.value_listing(TARGET, [], sold, now=NOW)
    assert v.evidence_class == V.HISTORICAL and v.basis == "estimate"
    assert v.confidence == "low"


def test_asking_comps_never_labelled_as_sales():
    v = V.value_listing(TARGET, [peer(i, 10000 + i * 25) for i in range(5)], now=NOW)
    assert v.evidence_class == V.ASKING and v.basis == "estimate"
    assert "not completed sales" in v.reasons[0]


def test_comps_api_excludes_undated_from_sales(monkeypatch):
    import comps
    monkeypatch.setattr(comps, "raw_sold", lambda y, mk, md: (_raw(), 0))
    out = comps.comps_for_listing(2015, "Toyota", "Camry")
    assert out["incomplete_records"] >= 3
    assert set(out["sources"]) <= set(comps.CONFIRMED_SALE_SOURCES)


# ---------- v2: geography, structured attributes, segments, quality -------

def _geo(i, price, lat, lon, **kw):
    return peer(i, price, latitude=lat, longitude=lon, **kw)


TUSC = (33.2098, -87.5692)          # target location
BHAM = (33.5186, -86.8104)          # ~50 mi
ATL = (33.7490, -84.3880)           # ~190 mi


def test_regional_peers_preferred_when_enough():
    peers = ([_geo(i, 9000 + i * 100, *BHAM) for i in range(4)]
             + [_geo(10 + i, 14000 + i * 100, *ATL) for i in range(4)])
    v = V.value_listing({**TARGET, "latitude": TUSC[0], "longitude": TUSC[1]}, peers, now=NOW)
    assert v.geography == "regional" and v.n == 4
    assert v.value_mid < 10000
    assert all(c["distance_mi"] <= V.REGIONAL_RADIUS_MI for c in v.comps)


def test_national_fallback_is_stated_and_lowers_confidence():
    peers = [_geo(i, 9000 + i * 100, *ATL) for i in range(5)]
    tgt = {**TARGET, "latitude": TUSC[0], "longitude": TUSC[1]}
    v = V.value_listing(tgt, peers, now=NOW)
    base = V.value_listing(TARGET, peers, now=NOW)            # location unknown
    assert v.geography == "national" and base.geography == "unknown"
    assert any("within 150 mi" in r for r in v.reasons)
    order = ["insufficient", "low", "medium", "high"]
    assert order.index(v.confidence) <= order.index(base.confidence)


def test_drivetrain_matched_from_structured_fields_only():
    awd = [peer(i, 12000 + i * 100, extras='{"drivetrain": "AWD"}') for i in range(3)]
    fwd = [peer(10 + i, 9000 + i * 100, drivetrain="FWD") for i in range(3)]
    v = V.value_listing({**TARGET, "extras": {"drivetrain": "All Wheel Drive"}},
                        awd + fwd, now=NOW)
    assert v.value_mid >= 12000 and any("drivetrain-matched (awd)" in r for r in v.reasons)
    # title text never sets drivetrain
    t2 = V.value_listing({**TARGET, "title": "2015 Toyota Camry AWD"}, awd + fwd, now=NOW)
    assert not any("drivetrain" in r for r in t2.reasons)


def test_segments_reported_separately_never_blended():
    d = (NOW - timedelta(days=20)).date().isoformat()
    peers = ([peer(i, 9000 + i * 100) for i in range(3)]
             + [peer(10 + i, 13000 + i * 100, is_dealer=1) for i in range(2)])
    sold = {V.CONFIRMED_SALES: [{"price": 9500, "source": "bat", "sale_date": d},
                                {"price": 9700, "source": "ebay", "sale_date": d}],
            V.HISTORICAL: [{"price": 8000}]}
    v = V.value_listing(TARGET, peers, sold, now=NOW)
    seg = v.segments
    assert seg["private_asking"] == {"n": 3, "median": 9100}
    assert seg["dealer_asking"] == {"n": 2, "median": 13050}
    assert seg["confirmed_sales"]["n"] == 2 and seg["auction_results"]["n"] == 1
    assert seg["historical_incomplete"]["n"] == 1
    assert seg["model_estimate"]["median"] is None
    assert v.evidence_class == V.ASKING            # 2 sales < MIN_COMPS: not used


def test_match_quality_reported_and_ranks_closer_peers_higher():
    peers = [peer(i, 10000 + i * 50, odo=100000 + i * 1000) for i in range(3)] + \
            [peer(9, 10100, odo=190000)]
    v = V.value_listing({**TARGET, "odometer": 101000}, peers, now=NOW)
    q = {c["composite_id"]: c["match_quality"] for c in v.comps}
    assert q["cl:p0"] == 100 and q["cl:p9"] < q["cl:p0"]
    assert any("mileage >40k apart" in n for c in v.comps for n in c["match_notes"])
    assert v.comp_quality is not None and 0 < v.comp_quality <= 100
