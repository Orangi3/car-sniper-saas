"""
tests/test_pilot_access.py — the free Tuscaloosa pilot's access rules.

Invited testers start on Free: they browse the deal feeds, but saved
searches and alert channels need Lite, so customer alerts are off by
construction until Gate 2 (PILOT_TUSCALOOSA.md). At Gate 2 the owner sets a
tester's plan to Lite by hand; with no Stripe subscription behind it,
billing reconciliation leaves that plan alone. SIMULATED listings only.
"""
from __future__ import annotations

from tests.conftest import login
from tests.test_matching_and_history import L, make_user, q, score_all, user_jobs, world  # noqa: F401


def test_free_testers_browse_but_cannot_set_up_alerts(client, world):
    uid = make_user(client, "tester@example.com", plan="free", topic=False)
    login(client, "tester@example.com")
    assert client.post("/api/saved_searches", json={
        "name": "c", "notify_enabled": True, "filter": {"model": "camry"}}).status_code == 402
    assert client.post("/api/me/alert_channels",
                       json={"ntfy_topic": "generate"}).status_code == 402
    world.ingest([L("deal", 8100, odo=120000)])
    score_all()
    feed = client.get("/api/deals?feed=trusted").get_json()
    assert any(d["composite_id"] == "craigslist:deal" for d in feed)
    assert q("SELECT 1 FROM user_matches WHERE user_id = ?", (uid,)) == []


def test_gate_2_hand_set_lite_plan_gets_alerts_and_survives_reconciliation(client, world):
    import auth
    import billing
    uid = make_user(client, "tester@example.com", plan="starter",      # the owner's set_plan
                    searches=[("c", {"model": "camry"})])
    billing.reconcile_all(triggered_by="test")        # no subscription row: nothing to undo
    assert auth.get_user_by_email("tester@example.com").plan == "starter"
    world.ingest([L("deal", 8100, odo=120000)])
    score_all()
    assert [m["composite_id"] for m in q("SELECT composite_id FROM user_matches "
                                         "WHERE user_id = ?", (uid,))] == ["craigslist:deal"]
    assert len(user_jobs(uid)) == 1


def test_pilot_report_counts_without_personal_data(client, world, capsys):
    import pilot_report
    uid = make_user(client, "tester@example.com", plan="starter",
                    searches=[("c", {"model": "camry"})])
    world.ingest([L("deal", 8100, odo=120000)])
    score_all()
    login(client, "tester@example.com")
    client.post("/api/deals/craigslist:deal/action", json={"action": "contacted"})
    assert client.post("/api/opportunities", json={"input": {
        "year": 2015, "make": "Toyota", "model": "Camry", "asking_price": 7000}}).status_code == 201
    r = pilot_report.report(7)
    assert r["analyze"] == {"analyses": 1, "testers_analyzing": 1, "deals_saved": 1,
                            "deals_by_stage": {"analyzing": 1}, "outcomes_recorded": 0}
    assert r["testers"]["accounts"] == 1 and r["testers"]["signed_in"] == 1
    from tests.conftest import logout
    logout(client)                                             # sign-out keeps the count
    assert pilot_report.report(7)["testers"]["signed_in"] == 1
    assert r["engagement"]["deal_actions_by_stage"] == {"contacted": 1}
    assert r["alerts"]["matches"] == 1 and r["alerts"]["notifications_waiting"] == len(user_jobs(uid))
    assert r["supply"]["listings_by_source"]["craigslist"] >= 1
    assert r["supply"]["trusted_candidates"] >= 1
    assert pilot_report.main([]) == 0
    out = capsys.readouterr().out
    assert "tester@example.com" not in out and "Camry" not in out
