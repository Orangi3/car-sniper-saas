"""
probe_tuscaloosa.py — fixture-only Tuscaloosa measurement harness.

FIXTURE — NOT A REAL LISTING. Nothing in this module makes a network
request, reads a production credential, touches a production database,
sends a notification, or enables a live provider.

Scope note (read this before quoting any number from a report):
This is a SEPARATE preliminary measurement exercise at a 100-mile radius
around Tuscaloosa, AL (ZIP 35401). It is NOT the pilot in
PILOT_TUSCALOOSA.md, which proposes 150 miles and 10 testers. Do not
rewrite that pilot plan, and do not present fixture counts as observed
market inventory, customer demand, valuation accuracy, or licensing
permission.

What this module does, in one line: given synthetic fixture listings and
a list of test-search segments, it counts, deduplicates, and reports
those fixtures deterministically, while simulating (not enforcing, for
money) an API call budget.

Reused, not duplicated:
- geo.zip_centroid / geo.distance_mi for territory filtering (local
  ZIP-centroid data only, no network).
- sources.base.NormalizedListing as the listing type.
- The price band (2000, 15000) and the 10% candidate floor concept from
  validate_deals.py are referenced as constants only; valuation and
  profit are NOT computed here, because valuation needs provider
  comparables and a database, and computing it from synthetic fixtures
  would imply accuracy this exercise cannot prove.
- provider_budget.py is NOT reused for the simulation, deliberately:
  it is DB-backed, checks remaining() and then records() in two steps
  (a race under parallel requests), and only records successful calls.
  The simulated ledger below is in-memory plus an optional JSON file,
  reserves atomically under a lock, and counts every attempted request
  including pages, retries, and failures. See PROBE_TUSCALOOSA.md for the
  documented limitations and what a future live enforcement layer would
  still need (a transactional store, a distributed lock, and a live
  gate outside this module). There is intentionally NO live-access
  switch, flag, or environment variable in this module.

Usage:
    python -m probe_tuscaloosa --fixtures probe_sample_fixtures.json \
        --segments probe_segments_proposed.json --out probe_sample_report
    # writes probe_sample_report.json and probe_sample_report.md
    # (both labelled FIXTURE — SYNTHETIC ONLY)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from geo import distance_mi, zip_centroid
from sources.base import NormalizedListing

HOME_ZIP = "35401"
RADIUS_MI = 100
PRICE_BAND = (2000, 15000)
FIXTURE_LABEL = "FIXTURE — NOT A REAL LISTING"
# Only this source id may ever appear in a probe run. Every real source
# id is blocked, including the licensed adapters, because this module
# must never call a provider, licensed or not.
ALLOWED_FIXTURE_SOURCES = {"fixture"}
BLOCKED_SOURCES = {
    "ebay", "marketcheck", "marketcheck_fsbo", "craigslist",
    "facebook", "marketplace", "offerup", "bat", "hemmings",
    "cars_and_bids", "copart", "iaa", "govdeals", "gsa", "publicsurplus",
}


class BlockedSourceError(ValueError):
    """Raised before any poll when a non-fixture source is requested."""


class BudgetExhausted(Exception):
    """Raised by reserve() when a simulated attempt would exceed the cap."""


def _norm_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@dataclass
class Segment:
    """One test-search segment. Configurable; never a claim about supply."""
    id: str
    name: str
    min_price: int = PRICE_BAND[0]
    max_price: int = PRICE_BAND[1]
    min_year: int | None = None
    max_year: int | None = None
    makes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            min_price=int(d.get("min_price", PRICE_BAND[0])),
            max_price=int(d.get("max_price", PRICE_BAND[1])),
            min_year=d.get("min_year"),
            max_year=d.get("max_year"),
            makes=[str(m).lower() for m in d.get("makes", [])],
        )

    def matches(self, listing: NormalizedListing) -> bool:
        if listing.price is None or not (self.min_price <= listing.price <= self.max_price):
            return False
        if self.min_year is not None and (listing.year is None or listing.year < self.min_year):
            return False
        if self.max_year is not None and (listing.year is None or listing.year > self.max_year):
            return False
        if self.makes and (listing.make or "").lower() not in self.makes:
            return False
        return True


@dataclass
class SimulatedBudget:
    """Simulated, atomic-under-one-process call ledger.

    reserve() checks and increments under a single lock, so parallel
    threads in one process cannot both pass at the cap. It counts
    pages, retries, and failed attempts exactly like successful calls:
    an attempted request is a cost, whether or not it returned data.
    Persistence is best-effort (temp file + replace); a crash mid-write
    leaves the last committed ledger, which is acceptable for a
    simulation and NOT acceptable as live billing enforcement.
    """
    cap: int
    used: int = 0
    ledger: list[dict] = field(default_factory=list)
    stopped_at_cap: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reserve(self, n: int = 1, kind: str = "poll", segment: str = "") -> bool:
        with self._lock:
            if self.used + n > self.cap:
                self.stopped_at_cap = True
                self.ledger.append({"kind": kind, "segment": segment, "calls": n,
                                    "accepted": False, "used_after": self.used})
                return False
            self.used += n
            self.ledger.append({"kind": kind, "segment": segment, "calls": n,
                                "accepted": True, "used_after": self.used})
            return True

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"cap": self.cap, "used": self.used,
                                   "stopped_at_cap": self.stopped_at_cap,
                                   "ledger": self.ledger}, indent=2, sort_keys=True))
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, cap: int) -> "SimulatedBudget":
        if not path.exists():
            return cls(cap=cap)
        data = json.loads(path.read_text())
        return cls(cap=int(data.get("cap", cap)), used=int(data.get("used", 0)),
                   ledger=list(data.get("ledger", [])),
                   stopped_at_cap=bool(data.get("stopped_at_cap", False)))


def dedupe_key(listing: NormalizedListing) -> str:
    """Deterministic identity: VIN first, else source+title+odometer+photo.

    A relist (same key, new source_id / later observation) is NOT a new
    unique listing. Photo hash, when present, lives in extras["photo_hash"].
    """
    vin = str((listing.extras or {}).get("vin", "")).strip().upper()
    if vin:
        return f"vin:{vin}"
    photo = str((listing.extras or {}).get("photo_hash", "")).strip()
    return f"listing:{listing.source}:{_norm_title(listing.title)}:{listing.odometer}:{photo}"


class SharedPoller:
    """One poll per segment, shared by every test search mapped to it.

    poll(segment_id, fetch) calls fetch() at most once per segment_id
    and charges the budget once. A second search over the same segment
    reuses the cached listings and charges nothing. fetch() receives no
    network access; in this module it is always a fixture lookup.
    """
    def __init__(self, budget: SimulatedBudget):
        self.budget = budget
        self._cache: dict[str, list[NormalizedListing]] = {}
        self.poll_calls = 0

    def poll(self, segment_id: str, fetch) -> list[NormalizedListing]:
        if segment_id in self._cache:
            return self._cache[segment_id]
        if not self.budget.reserve(1, kind="poll", segment=segment_id):
            raise BudgetExhausted(f"simulated budget cap reached before segment {segment_id}")
        self.poll_calls += 1
        listings = list(fetch())
        self._cache[segment_id] = listings
        return listings


def fixture_listing(d: dict) -> NormalizedListing:
    """Build a NormalizedListing from synthetic fixture data only.

    The caller must label the data as a fixture. Real provider fields
    are never read here; source is forced to "fixture" so a fixture can
    never be mistaken for a provider listing downstream.
    """
    return NormalizedListing(
        source="fixture",
        source_id=str(d["source_id"]),
        url=str(d.get("url", "fixture://not-a-real-listing")),
        title=str(d.get("title", "")),
        price=d.get("price"),
        year=d.get("year"),
        make=d.get("make"),
        model=d.get("model"),
        odometer=d.get("odometer"),
        zip=d.get("zip"),
        latitude=d.get("latitude"),
        longitude=d.get("longitude"),
        posted_at=d.get("posted_at"),
        extras=dict(d.get("extras", {})),
    )


def run_probe(*, segments: list[Segment], fixtures: list[dict],
              budget_cap: int = 450, source_ids: Iterable[str] = ("fixture",),
              run_date: str | None = None) -> dict:
    """Run a fixture-only probe. Pure except for the budget ledger object.

    Raises BlockedSourceError before any poll if a non-fixture source
    is requested. Never imports a provider adapter, never opens a
    socket, never reads an environment credential, never notifies.
    """
    for sid in source_ids:
        if sid not in ALLOWED_FIXTURE_SOURCES:
            raise BlockedSourceError(f"source {sid!r} is blocked in fixture mode")
    if zip_centroid(HOME_ZIP) is None:
        raise ValueError(f"home ZIP {HOME_ZIP} has no centroid in local data")

    budget = SimulatedBudget(cap=int(budget_cap))
    poller = SharedPoller(budget)
    # Fixtures are grouped by segment id in the input; a fixture record
    # may appear in several segments' input lists, which is exactly the
    # shared-polling case: the poller charges once per segment.
    by_segment: dict[str, list[dict]] = {}
    for rec in fixtures:
        by_segment.setdefault(str(rec.get("segment", "unsegmented")), []).append(rec)

    seen = 0
    unique: dict[str, dict] = {}
    relists = 0
    first_seen_by_day: dict[str, int] = {}
    lags: list[int] = []
    unknown_dates = 0
    out_of_radius = 0
    per_search: dict[str, dict] = {}

    for seg in segments:
        records = by_segment.get(seg.id, [])
        # The poll is per segment, not per search: fetch is a local
        # lookup over the fixture records for this segment.
        try:
            polled = poller.poll(seg.id, lambda r=records: [fixture_listing(x) for x in r])
        except BudgetExhausted:
            per_search[seg.id] = {"segment": seg.id, "name": seg.name,
                                  "listings_seen": 0, "unique": 0, "matches": 0,
                                  "stopped_at_cap": True}
            continue
        seg_seen = 0
        seg_unique: set[str] = set()
        seg_matches = 0
        for listing in polled:
            seg_seen += 1
            seen += 1
            # Territory: structured coordinates first, else the listing
            # ZIP centroid, via geo.distance_mi. Unknown distance is
            # out of coverage, not assumed in-radius.
            dist = distance_mi(HOME_ZIP, listing.latitude, listing.longitude)
            if dist is None and listing.zip:
                home = zip_centroid(HOME_ZIP)
                other = zip_centroid(listing.zip)
                if home and other:
                    from geo import haversine_mi
                    dist = round(haversine_mi(home, other), 1)
            if dist is None or dist > RADIUS_MI:
                out_of_radius += 1
                continue
            key = dedupe_key(listing)
            if key in unique:
                relists += 1
                continue
            first_seen = _parse_date((listing.extras or {}).get("first_seen_at")
                                     or listing.posted_at)
            # Freshness is posted-date vs first-seen date. A missing or
            # unparseable posted date is unknown, never zero.
            posted = _parse_date(listing.posted_at)
            if posted is None or first_seen is None:
                unknown_dates += 1
            else:
                lags.append((first_seen - posted).days)
            if first_seen is not None:
                day = first_seen.isoformat()
                first_seen_by_day[day] = first_seen_by_day.get(day, 0) + 1
            unique[key] = {"key": key, "title": listing.title, "price": listing.price,
                           "year": listing.year, "distance_mi": dist}
            seg_unique.add(key)
            if seg.matches(listing):
                seg_matches += 1
        per_search[seg.id] = {"segment": seg.id, "name": seg.name,
                              "listings_seen": seg_seen, "unique": len(seg_unique),
                              "matches": seg_matches, "stopped_at_cap": False}

    lag_stats = {}
    if lags:
        lags_sorted = sorted(lags)
        lag_stats = {"median_days": statistics.median(lags_sorted),
                     "min_days": lags_sorted[0], "max_days": lags_sorted[-1],
                     "count_with_dates": len(lags_sorted)}
    report = {
        "label": FIXTURE_LABEL,
        "scope": "Separate preliminary fixture measurement only. NOT the "
                 "PILOT_TUSCALOOSA.md pilot (150 miles, 10 testers). "
                 "Fixture counts are NOT observed market inventory, "
                 "customer demand, valuation accuracy, or licensing permission.",
        "home_zip": HOME_ZIP,
        "radius_mi": RADIUS_MI,
        "price_band": list(PRICE_BAND),
        "run_date": run_date,
        "sources_allowed": sorted(ALLOWED_FIXTURE_SOURCES),
        "sources_blocked": sorted(BLOCKED_SOURCES),
        "live_access": False,
        "network_calls": 0,
        "listings_seen": seen,
        "unique_listings": len(unique),
        "relist_records": relists,
        "first_seen_by_day": dict(sorted(first_seen_by_day.items())),
        "out_of_radius_records": out_of_radius,
        "freshness": {**lag_stats, "unknown_date_listings": unknown_dates},
        "per_search": per_search,
        "budget": {"cap": budget.cap, "used": budget.used,
                   "stopped_at_cap": budget.stopped_at_cap,
                   "poll_calls": poller.poll_calls,
                   "ledger_entries": len(budget.ledger)},
        "segments_included": [s.id for s in segments],
        "segments_excluded": "Any segment not in the owner-approved config is excluded. "
                            "No segment in a report is owner-approved unless the "
                            "config file itself was owner-approved.",
        "qualifying_definition": "A qualifying match here means only: in radius, in the "
                                "segment price/year/make bounds. It is NOT a deal, "
                                "a valuation, or a profit estimate.",
    }
    return report


def render_markdown(report: dict) -> str:
    lines = [f"# {report['label']}", "", report["scope"], "",
             f"Home ZIP: {report['home_zip']} | Radius: {report['radius_mi']} mi | "
             f"Price band: ${report['price_band'][0]}-${report['price_band'][1]}",
             f"Live access: {report['live_access']} | Network calls: {report['network_calls']}",
             "", "## Counts",
             f"- Listings seen: {report['listings_seen']}",
             f"- Unique listings: {report['unique_listings']}",
             f"- Relist records (not new uniques): {report['relist_records']}",
             f"- Out-of-radius records: {report['out_of_radius_records']}",
             f"- First seen by day: {report['first_seen_by_day'] or 'none'}",
             f"- Freshness: {report['freshness']}",
             f"- Budget: {report['budget']['used']} / {report['budget']['cap']} "
             f"(poll calls: {report['budget']['poll_calls']}, "
             f"stopped at cap: {report['budget']['stopped_at_cap']})",
             "", "## Per search",
             "| Segment | Seen | Unique | Matches | Stopped at cap |",
             "|---|---|---|---|---|"]
    for seg_id in sorted(report["per_search"]):
        s = report["per_search"][seg_id]
        lines.append(f"| {seg_id} | {s['listings_seen']} | {s['unique']} | "
                     f"{s['matches']} | {s['stopped_at_cap']} |")
    lines += ["", "## Coverage and exclusions", report["segments_excluded"],
              "", "## Qualifying definition", report["qualifying_definition"], ""]
    return "\n".join(lines)


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m probe_tuscaloosa")
    ap.add_argument("--fixtures", required=True, help="JSON list of synthetic fixture records")
    ap.add_argument("--segments", required=True, help="JSON list of segment configs")
    ap.add_argument("--out", required=True, help="Output stem; writes .json and .md")
    ap.add_argument("--budget-cap", type=int, default=450)
    ap.add_argument("--run-date", default=None)
    a = ap.parse_args(argv)
    segments = [Segment.from_dict(d) for d in _load_json(a.segments)]
    fixtures = _load_json(a.fixtures)
    report = run_probe(segments=segments, fixtures=fixtures,
                       budget_cap=a.budget_cap, run_date=a.run_date)
    out = Path(a.out)
    out.with_suffix(".json").write_text(json.dumps(report, indent=2, sort_keys=True))
    out.with_suffix(".md").write_text(render_markdown(report))
    print(f"fixture probe: {report['unique_listings']} unique of "
          f"{report['listings_seen']} seen; budget {report['budget']['used']}/"
          f"{report['budget']['cap']}; live_access=False")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
