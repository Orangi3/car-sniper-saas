"""
tests/test_structured_vin_identity.py — vehicle identity from a provider's
structured VIN field (sniper._insert_listing).

MarketCheck records carry the VIN as a field (extras["vin"]), not in the
title or description, so it never became the listing's vehicle_key. The same
car cross-posted on two marketplaces then produced two alerts, and each copy
was a comparable for the other. A structured VIN counts only when its check
digit verifies — the same bar as a VIN found in the text — and a VIN in the
text still wins.

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json

from tests.test_matching_and_history import (PEER_PRICES, L, q, score_all,  # noqa: F401
                                             world)
from tests.test_send_time_check import _camry_buyer, _deliver, pushes  # noqa: F401

VIN_A = "1HGCM82633A004352"          # check digit verifies
VIN_B = "1M8GDM9AXKP042788"          # check digit verifies
VIN_BAD = "1HGCM82633A004353"        # check digit does not verify


def _comps_used(cid):
    nm = q("SELECT comp_n, evidence FROM near_misses WHERE composite_id = ?", (cid,))[0]
    return nm["comp_n"], {c.get("composite_id")
                          for c in json.loads(nm["evidence"]).get("comps", [])}


def _fsbo(sid, price, vin, **kw):
    return L(sid, price, source="marketcheck_fsbo", desc="clean title, one owner",
             extras={"provider": "marketcheck", "seller_type": "fsbo", "vin": vin}, **kw)


def test_structured_provider_vin_identifies_the_vehicle(world):
    world.ingest([_fsbo("fb-1", 8100, VIN_A), _fsbo("cl-9", 8300, VIN_BAD)])
    rows = {r["composite_id"]: r for r in q(
        "SELECT composite_id, vin, vehicle_key FROM listings "
        "WHERE source = 'marketcheck_fsbo'")}
    assert rows["marketcheck_fsbo:fb-1"]["vehicle_key"] == f"vin:{VIN_A}"
    assert rows["marketcheck_fsbo:cl-9"]["vin"] is None      # check digit fails


def test_vin_in_the_text_still_wins(world):
    world.ingest([L("txt", 8100, desc=f"VIN {VIN_B} clean title",
                    extras={"vin": VIN_A})])
    assert q("SELECT vin FROM listings WHERE composite_id = 'craigslist:txt'") \
        == [{"vin": VIN_B}]


def test_same_car_on_two_marketplaces_notifies_once(client, world, pushes):
    """MarketCheck FSBO aggregates several marketplaces; one car posted on
    two of them is two records with one VIN field."""
    uid = _camry_buyer(client)
    world.ingest([_fsbo("fb-2", 8100, VIN_A)])
    score_all()
    world.ingest([_fsbo("cl-2", 8150, VIN_A, odo=130200)])
    score_all()
    assert len(q("SELECT * FROM user_matches WHERE user_id = ?", (uid,))) == 1
    _deliver()
    assert len(pushes["sent"]) == 1


def test_a_cars_other_posting_is_not_its_own_comparable(world):
    """The seller asks $9,900 on one marketplace and $8,100 on another: the
    $9,900 copy is the same car, not market evidence for the $8,100 one."""
    world.ingest([_fsbo("fb-3", 8100, VIN_A), _fsbo("cl-3", 9900, VIN_A, odo=129000)])
    score_all()
    n, used = _comps_used("marketcheck_fsbo:fb-3")
    assert "marketcheck_fsbo:cl-3" not in used
    assert n == len(PEER_PRICES)
