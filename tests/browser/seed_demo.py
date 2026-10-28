"""
tests/browser/seed_demo.py — seed a THROWAWAY database with synthetic
listings for the browser smoke test. Never point this at a real database:
it refuses unless SNIPER_DB_PATH is set and DATABASE_URL is not.

    SNIPER_DB_PATH=/tmp/smoke.db python tests/browser/seed_demo.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

if not os.environ.get("SNIPER_DB_PATH") or os.environ.get("DATABASE_URL"):
    sys.exit("refusing: set SNIPER_DB_PATH to a scratch file and unset DATABASE_URL")

from migrations import runner  # noqa: E402

runner.run_pending(verbose=False)

import comps  # noqa: E402
import jobqueue  # noqa: E402
import sniper  # noqa: E402
from sources.base import NormalizedListing  # noqa: E402

comps.raw_sold = lambda y, mk, md: ({}, 0)     # no network in the smoke test


def L(sid, price, odo, title="2015 Toyota Camry SE", desc="clean title"):
    return NormalizedListing(
        source="craigslist", source_id=f"demo:{sid}",
        url=f"https://example.com/demo/{sid}", title=f"{title} - ${price:,}",
        price=price, year=2015, make="Toyota", model="Camry", odometer=odo,
        description=desc, state="AL", location="Tuscaloosa", is_cash_only=True)


peers = [L(f"p{i}", p, 100000 + i * 7000) for i, p in
         enumerate([10000, 10400, 9800, 10200, 10600, 9900, 10100, 10300])]
sniper.ingest(peers)
sniper.ingest([L("deal", 8100, 128000), L("risky", 5200, 131000, desc="rebuilt title")])
jobqueue.drain({"score": sniper.score_job})
sniper.ingest([L("deal", 7600, 128000)])          # price drop
jobqueue.drain({"score": sniper.score_job})
print("seeded")
