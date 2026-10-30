"""
benchmarks/run_benchmark.py — repeatable opportunity-detection benchmark.

Runs every labeled case in benchmarks/cases.json through the REAL pipeline
pieces (title/price parsing, ingestion normalization, valuation, risk
assessment) with no network and no database, then reports precision,
recall, false-positive rate and false-negative rate.

    python benchmarks/run_benchmark.py           # table + metrics
    python benchmarks/run_benchmark.py --json    # machine-readable

The cases are synthetic and were labeled by the engineer who wrote the
pipeline. The numbers show behavior on known edge cases; they are NOT an
accuracy claim for real listings. Measuring real-world accuracy needs
independently labeled listings with observed sale outcomes.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sniper                                      # noqa: E402
import valuation as V                              # noqa: E402
from sources.base import (NormalizedListing, extract_make_model,  # noqa: E402
                          extract_miles, extract_price, extract_year)

NOW = datetime.now(timezone.utc)


def _peers(m: dict) -> list[dict]:
    out = []

    # Peers go through the same title parser real ingestion uses, so the
    # target and its market are identified consistently.
    def add(title, prices, odos, tag):
        mk, md = extract_make_model(title)
        for i, p in enumerate(prices):
            odo = odos[i] if odos else None
            out.append({"composite_id": f"cl:{tag}{i}", "source": "craigslist",
                        "title": f"{title} - ${p:,}", "price": p, "year": m["year"],
                        "make": mk, "model": md, "odometer": odo,
                        "last_seen_at": (NOW - timedelta(days=1)).isoformat()})
    add(m["title"], m["prices"], m.get("odometers"), "p")
    if m.get("extra"):
        e = m["extra"]
        add(e["title"], e["prices"], e.get("odometers"), "x")
    for j, cp in enumerate(m.get("cross_posts") or []):
        for k in range(cp["copies"]):
            mk, md = extract_make_model(m["title"])
            out.append({"composite_id": f"src{k}:cp{j}", "source": f"src{k}",
                        "title": f"{m['title']} - ${cp['price']:,}", "price": cp["price"],
                        "year": m["year"], "make": mk, "model": md,
                        "odometer": cp["odometer"],
                        "last_seen_at": (NOW - timedelta(days=1)).isoformat()})
    return out


def _sold(m: dict):
    if not m.get("sold"):
        return None
    d = (NOW - timedelta(days=30)).date().isoformat()
    return {V.CONFIRMED_SALES: [{"price": p, "source": "ebay", "sale_date": d,
                                 "title": m["title"]} for p in m["sold"]],
            V.HISTORICAL: []}


def evaluate_case(c: dict, threshold: float) -> dict:
    t = c["target"]
    title, desc = t["title"], t.get("description", "")
    price = extract_price(title) or extract_price(desc)
    make, model = extract_make_model(title)
    year = extract_year(title)
    res = {"id": c["id"], "category": c["category"], "label": c["label"],
           "parsed": {"price": price, "year": year, "make": make, "model": model}}
    if not (price and year and make and model):
        return {**res, "predicted": False, "why": "unparseable price/vehicle"}
    nl = NormalizedListing(source="craigslist", source_id=c["id"], url="",
                           title=title, price=price, year=year, make=make, model=model,
                           odometer=extract_miles(title) or extract_miles(desc),
                           description=desc)
    sniper._normalize_listing(nl)
    target = {"composite_id": "cl:target", "title": title, "description": desc,
              "price": price, "year": year, "make": make, "model": model,
              "odometer": nl.odometer, "is_salvage": nl.is_salvage,
              "needs_work": bool((nl.extras or {}).get("needs_work")),
              "is_dealer": nl.is_dealer, "previous_price": t.get("previous_price"),
              "is_auction": bool(t.get("is_auction")),
              "trim": V.parse_trim(title, model)}
    m = c["market"]
    peers = [p for p in _peers(m) if p["make"] and p["model"]
             and p["make"].lower() == make.lower() and p["model"].lower() == model.lower()]
    v = V.value_listing(target, peers, _sold(m), now=NOW)
    if not v.value_mid:
        return {**res, "predicted": False, "why": v.reasons[0] if v.reasons else "no value",
                "evidence": v.evidence_class, "confidence": v.confidence}
    disc = (v.value_mid - price) / v.value_mid
    trust, flags = sniper.assess_risk(target, v, disc)
    pred = trust == "trusted" and disc >= threshold
    return {**res, "predicted": pred, "discount_pct": round(disc * 100, 1),
            "value_mid": v.value_mid, "evidence": v.evidence_class,
            "confidence": v.confidence, "trust": trust, "flags": flags}


def run(path: Path = ROOT / "benchmarks" / "cases.json") -> dict:
    data = json.loads(path.read_text())
    thr = data.get("threshold_pct", 20) / 100.0
    rows = [evaluate_case(c, thr) for c in data["cases"]]
    tp = sum(r["label"] and r["predicted"] for r in rows)
    fp = sum((not r["label"]) and r["predicted"] for r in rows)
    tn = sum((not r["label"]) and (not r["predicted"]) for r in rows)
    fn = sum(r["label"] and (not r["predicted"]) for r in rows)
    for r in rows:
        r["outcome"] = ("TP" if r["label"] and r["predicted"] else
                        "FP" if r["predicted"] else
                        "FN" if r["label"] else "TN")

    def ratio(a, b):
        return round(a / b, 3) if b else None
    metrics = {"cases": len(rows), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
               "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
               "false_positive_rate": ratio(fp, fp + tn),
               "false_negative_rate": ratio(fn, fn + tp),
               "threshold_pct": thr * 100,
               "note": data.get("_about")}
    return {"metrics": metrics, "cases": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    out = run()
    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    print(f"{'case':28s} {'category':20s} {'label':5s} {'pred':5s} {'out':3s} "
          f"{'disc%':>6s} {'evidence':22s} {'conf':7s} flags / why")
    for r in out["cases"]:
        print(f"{r['id']:28s} {r['category']:20s} {str(r['label'])[0]:5s} "
              f"{str(r['predicted'])[0]:5s} {r['outcome']:3s} "
              f"{str(r.get('discount_pct', '')):>6s} {str(r.get('evidence', '')):22s} "
              f"{str(r.get('confidence', '')):7s} {r.get('flags') or r.get('why', '')}")
    m = out["metrics"]
    print(f"\n{m['cases']} cases  TP={m['tp']} FP={m['fp']} TN={m['tn']} FN={m['fn']}")
    print(f"precision={m['precision']}  recall={m['recall']}  "
          f"FPR={m['false_positive_rate']}  FNR={m['false_negative_rate']}")
    print(f"\nNOTE: {m['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
