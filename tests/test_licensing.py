"""
tests/test_licensing.py — commercial mode only uses licensed data (P7).
"""
from __future__ import annotations

import sources


def test_every_source_declares_licensing():
    for m in sources.all_sources():
        assert getattr(m, "LICENSING", None) in {
            "licensed_api", "requires_agreement", "terms_prohibit_automation",
            "unverified", "personal_use_only"}, m.SOURCE_ID


def test_commercial_mode_keeps_only_licensed_sources(monkeypatch):
    monkeypatch.delenv("MARKETCHECK_COMMERCIAL_AGREEMENT", raising=False)
    monkeypatch.delenv("EBAY_COMMERCIAL_AGREEMENT", raising=False)
    cfg = {"commercial_mode": True, "sources": {}}
    ids = {m.SOURCE_ID for m in sources.iter_sources(cfg)}
    assert ids == set()                    # every provider fail-closed without approval
    monkeypatch.setenv("MARKETCHECK_COMMERCIAL_AGREEMENT", "MC-2026-001")
    ids = {m.SOURCE_ID for m in sources.iter_sources(cfg)}
    assert ids == {"marketcheck", "marketcheck_fsbo"}
    monkeypatch.setenv("EBAY_COMMERCIAL_AGREEMENT", "EPN approval 2026-11-02")
    ids = {m.SOURCE_ID for m in sources.iter_sources(cfg)}
    assert ids == {"ebay", "marketcheck", "marketcheck_fsbo"}


def test_ebay_needs_recorded_buy_api_approval_in_commercial_mode(monkeypatch):
    """Production Buy API use needs eBay's approval and contracts (EPN);
    credentials alone must not switch it on for a paid product."""
    import sources.ebay_motors as eb
    monkeypatch.delenv("EBAY_COMMERCIAL_AGREEMENT", raising=False)
    why = sources.licensing_block(eb, {"commercial_mode": True})
    assert "EBAY_COMMERCIAL_AGREEMENT" in why
    assert sources.licensing_block(eb, {"commercial_mode": False}) is None
    off = {m.SOURCE_ID for m in sources.iter_sources({"sources": {}})}
    assert {"craigslist", "marketplace"} <= off            # personal mode unchanged


def test_commercial_mode_reports_why_sources_are_off(app, monkeypatch):
    import sniper
    monkeypatch.setitem(sniper.CONFIG, "commercial_mode", True)
    monkeypatch.setattr(sniper._config_mod, "reload", lambda: None)
    out = sniper.poll_once(verbose=False, only_sources=["craigslist", "marketplace"],
                           force_all=True)
    st = out["source_status"]
    assert st["craigslist"]["status"] == "disabled"
    assert "terms_prohibit_automation" in st["craigslist"]["reason"]
    assert "personal_use_only" in st["marketplace"]["reason"]


def test_commercial_mode_skips_unlicensed_comp_scrapers(monkeypatch):
    import comps
    from config import CONFIG
    called = []
    for name in ("ebay_sold", "bat_sold", "carsbids_sold", "pcarmarket_sold",
                 "hemmings_sold", "copart_sold"):
        monkeypatch.setattr(comps, name, lambda *a, _n=name, **k: called.append(_n) or [])
    monkeypatch.setattr(comps, "marketcheck_sold",
                        lambda *a, **k: called.append("marketcheck") or [])
    monkeypatch.setitem(CONFIG, "commercial_mode", True)
    monkeypatch.delenv("MARKETCHECK_COMMERCIAL_AGREEMENT", raising=False)
    out = comps._fetch_all("2015 Toyota Camry", 2015, "Toyota", "Camry")
    assert called == []                     # nothing licensed without agreement
    monkeypatch.setenv("MARKETCHECK_COMMERCIAL_AGREEMENT", "MC-2026-001")
    out = comps._fetch_all("2015 Toyota Camry", 2015, "Toyota", "Camry")
    assert called == ["marketcheck"]
    assert set(out) >= {"ebay", "marketcheck"}
