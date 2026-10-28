"""
tests/test_ops.py — owner operations snapshot. Values must come from the
database; anything the app can't measure must say so instead of a number.
"""
from __future__ import annotations

from tests.conftest import login, make_admin, register
from tests.test_e2e_pipeline import _deliver, _ebay_item, _poll, ebay_feed  # noqa: F401
from tests.test_matching_and_history import make_user, score_all, world  # noqa: F401


def _admin(client):
    register(client, "owner@example.com")
    make_admin("owner@example.com")
    login(client, "owner@example.com")


def test_ops_requires_admin(client, app):
    register(client, "user@example.com")
    login(client, "user@example.com")
    assert client.get("/api/admin/ops").status_code in (401, 403)
    assert client.get("/admin/ops").status_code in (401, 403)


def test_empty_system_reports_unavailable_not_zeros(client, app, monkeypatch):
    for k in ("OPS_HOSTING_MONTHLY_USD", "OPS_DATA_PLATFORM_MONTHLY_USD"):
        monkeypatch.delenv(k, raising=False)
    _admin(client)
    d = client.get("/api/admin/ops").get_json()
    c = d["costs"]
    assert c["hosting"]["value"] is None and "OPS_HOSTING_MONTHLY_USD" in c["hosting"]["unavailable"]
    assert c["ai_agent_spend"]["value"] is None
    assert d["deal_intelligence"]["mean_comparable_quality"]["value"] is None
    ebay = next(s for s in d["data_health"]["sources"] if s["source"] == "ebay")
    assert ebay["status"] == "never polled"
    assert ebay["last_live_probe"]["value"] is None
    assert d["system_health"]["ready_for"] in ("not_ready", "private_internal_testing")
    assert client.get("/admin/ops").status_code == 200


def test_ops_reflects_real_pipeline_activity(client, ebay_feed, monkeypatch):
    import verify_sources as vs
    monkeypatch.setenv("OPS_HOSTING_MONTHLY_USD", "44")
    make_user(client, "buyer@example.com", searches=[("camry", {"max_price": 9000})])
    ebay_feed["items"] = [_ebay_item("v1|600|0", 6900)]
    _poll(); score_all(); _deliver()
    vs.record({"checked_at": "2026-10-28T10:00:00+00:00",
               "territory": {"zip": "35401", "radius_mi": 150},
               "results": [{"source": "ebay", "status": "healthy", "verified": True,
                            "listings": 1}]}, origin="test")
    _admin(client)
    d = client.get("/api/admin/ops").get_json()
    assert "ebay" in d["data_health"]["working"]
    ebay = next(s for s in d["data_health"]["sources"] if s["source"] == "ebay")
    assert ebay["listings_first_seen_in_window"] == 1
    assert ebay["last_live_probe"]["origin"] == "test"
    di = d["deal_intelligence"]
    assert di["qualified_deals"] >= 1 and di["subscriber_matches"] == 1
    assert di["qualified_deals"] <= di["trusted_valuations"]
    assert di["notifications"]["delivered_to_provider"] >= 1
    top = di["top_by_conservative_profit"][0]
    assert top["composite_id"] == "ebay:v1|600|0" and top["conservative_profit"] is not None
    assert isinstance(di["mean_comparable_quality"], float)
    assert d["costs"]["hosting"] == {"value": 44.0, "basis": "configured"}
    assert d["costs"]["notifications_this_month"]["push"]["sent"] >= 1
