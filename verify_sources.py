"""
verify_sources.py — bounded live check of the licensed API sources.

    python -m verify_sources                          # every licensed API source with keys
    python -m verify_sources --sources ebay --zip 35401 --radius 75
    python -m verify_sources --json > probe.json

Answers one question: does the adapter, with real credentials, get real
listings back in the shape it expects? It is safe to run from CI or a
laptop:

  * at most ONE request per source (MarketCheck FSBO is forced to 1 page),
    plus eBay's OAuth token request
  * runs against a throwaway SQLite database, never the configured one, and
    deletes it afterwards — no listing is stored
  * prints AGGREGATES ONLY: request outcome, listing count, field coverage,
    schema problems, price/year distribution, seller-type split, states.
    No titles, URLs, VINs, descriptions or seller details leave the process.

Exit codes: 0 = at least one source verified (healthy, >0 listings, schema
ok); 2 = no source had credentials (nothing was checked); 1 = otherwise.

A pass proves the integration works at that moment for that territory. It
says nothing about licensing (see SOURCES.md / OWNER_ACTIONS.md) and is not
an accuracy measurement.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROBE_SOURCES = ("ebay", "marketcheck_fsbo")
BAND = (2000, 15000)


def _prepare_db(tmp: Path) -> None:
    # Must happen before anything imports `db`.
    os.environ["SNIPER_DB_PATH"] = str(tmp / "probe.db")
    os.environ.pop("DATABASE_URL", None)
    from migrations import runner
    runner.run_pending(verbose=False)


def _pct(n: int, d: int) -> float:
    return round(100 * n / d, 1) if d else 0.0


def summarize(listings: list) -> dict:
    """Aggregate, content-free description of normalized listings."""
    n = len(listings)
    prices = sorted(x.price for x in listings if x.price)
    years = sorted(x.year for x in listings if x.year)
    seller = Counter("dealer" if x.is_dealer else
                     ("unknown" if (x.extras or {}).get("seller_type") == "unknown"
                      else "private") for x in listings)
    return {
        "listings": n,
        "coverage_pct": {
            "price": _pct(len(prices), n), "year": _pct(len(years), n),
            "make_model": _pct(sum(1 for x in listings if x.make and x.model), n),
            "mileage": _pct(sum(1 for x in listings if x.odometer), n),
            "zip": _pct(sum(1 for x in listings if x.zip), n),
            "coordinates": _pct(sum(1 for x in listings
                                    if x.latitude is not None and x.longitude is not None), n),
            "posted_at": _pct(sum(1 for x in listings if x.posted_at), n),
        },
        "price": ({"min": prices[0], "median": int(statistics.median(prices)),
                   "max": prices[-1]} if prices else None),
        "year": {"min": years[0], "max": years[-1]} if years else None,
        "in_price_band": sum(1 for p in prices if BAND[0] <= p <= BAND[1]),
        "seller_type": dict(seller),
        "states": dict(Counter(x.state for x in listings if x.state).most_common(10)),
    }


def probe_source(module, cfg: dict) -> dict:
    from sources import health, schema
    sid = module.SOURCE_ID
    issue = getattr(module, "config_issue", lambda c: None)(cfg)
    if issue:
        return {"source": sid, "status": "no_credentials", "detail": issue}
    pages = getattr(module, "MAX_PAGES", None)
    if pages is not None:
        module.MAX_PAGES = 1                      # bounded: one search request
    rep = health.begin(sid)
    error = None
    try:
        listings = module.poll(cfg) or []
    except Exception as e:                        # report, don't crash the probe
        listings, error = [], f"{type(e).__name__}: {e}"[:200]
    finally:
        if pages is not None:
            module.MAX_PAGES = pages
    status, reason = health.classify(rep, len(listings), error=error, ever_succeeded=False)
    sc = getattr(module, "LAST_RESULTS", {}).get("schema")
    out = {"source": sid, "status": status, "detail": reason,
           "requests": [{"label": a["label"], "outcome": a["outcome"],
                         "http_status": a["status"]} for a in rep.attempts],
           **summarize(listings)}
    if sc:
        out["schema"] = {"items": sc["n"], "failing_items": sc["bad_items"],
                         "problems": sc["problems"], "field_coverage_pct": sc["coverage"],
                         "drifted": schema.drifted(sc)}
    out["verified"] = bool(status == health.HEALTHY and listings
                           and not (sc and schema.drifted(sc)))
    return out


def run(source_ids=None, zip_code=None, radius=None) -> dict:
    from config import CONFIG
    import sources
    cfg = json.loads(json.dumps(CONFIG))          # deep copy; never mutate global config
    if zip_code:
        cfg["zip"] = zip_code
    if radius:
        cfg["radius_mi"] = int(radius)
    for sid in PROBE_SOURCES:                     # CLI territory overrides per-source config
        src = cfg.setdefault("sources", {}).setdefault(sid, {})
        if zip_code:
            src.pop("zip", None)
        if radius:
            src.pop("radius_mi", None)
    want = set(source_ids or PROBE_SOURCES)
    mods = [m for m in sources.all_sources() if m.SOURCE_ID in want]
    results = [probe_source(m, cfg) for m in mods]
    return {"checked_at": datetime.now(timezone.utc).isoformat(),
            "territory": {"zip": cfg.get("zip"), "radius_mi": cfg.get("radius_mi")},
            "results": results}


def exit_code(report: dict) -> int:
    rs = report["results"]
    if any(r.get("verified") for r in rs):
        return 0
    if all(r["status"] == "no_credentials" for r in rs):
        return 2
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m verify_sources")
    ap.add_argument("--sources", nargs="*", choices=PROBE_SOURCES)
    ap.add_argument("--zip")
    ap.add_argument("--radius", type=int)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="sniper-probe-") as tmp:
        _prepare_db(Path(tmp))
        rep = run(a.sources, a.zip, a.radius)
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        t = rep["territory"]
        print(f"territory {t['zip']} + {t['radius_mi']} mi  ({rep['checked_at']})")
        for r in rep["results"]:
            mark = "✓" if r.get("verified") else ("–" if r["status"] == "no_credentials" else "✗")
            print(f"\n{mark} {r['source']}: {r['status']} — {r['detail']}")
            if r["status"] == "no_credentials":
                continue
            print(f"  listings {r['listings']} (in ${BAND[0]:,}–${BAND[1]:,}: "
                  f"{r['in_price_band']}); seller types {r['seller_type']}")
            print(f"  price {r['price']}; year {r['year']}; states {r['states']}")
            print(f"  coverage % {r['coverage_pct']}")
            if r.get("schema"):
                s = r["schema"]
                print(f"  schema: {s['failing_items']}/{s['items']} items failing"
                      + (f"; problems {s['problems']}" if s["problems"] else "")
                      + ("  ** DRIFT **" if s["drifted"] else ""))
    return exit_code(rep)


if __name__ == "__main__":
    sys.exit(main())
