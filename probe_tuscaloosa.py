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

Corrections applied after the independent review of PR #20 head
4e32251443aac6126d31e1545b5861752ab04708 (review verdict: CHANGES
REQUIRED). The original head had four HIGH defects; this version fixes
them, and the tests in tests/test_probe_tuscaloosa.py prove each fix:

1. Segment classification. A segment is either a *category* segment
   (it lists explicit model families and/or makes, and a listing must
   match them) or a *price/year band* segment (it lists neither, and
   its matches are price/year matches only — never a vehicle-category
   claim). Missing or ambiguous model information never matches a
   category segment. Categories that cannot be distinguished
   responsibly from make/model alone (SUVs, sedans, hatchbacks, vans,
   and similar) were consolidated into price/year bands in
   probe_segments_proposed.json; they are not named as categories.
2. Cross-segment deduplication. Global vehicle identity and
   per-segment membership are separate. A vehicle in two segments
   counts in each segment's unique and match counts, while the global
   unique count stays 1. Segment order does not change any result
   (except which segments are reached before a budget cap stops the
   run, which is reported per segment). Cross-segment overlap is
   neither a relist nor a repeated observation.
3. Freshness integrity. First-seen comes ONLY from the fixture's
   first_seen_at field. There is no fallback to posted_at. Missing or
   malformed dates are unknown, never zero lag. A first-seen date
   before the posted date, or any date after run_date when run_date
   is supplied, is invalid, never a negative lag. Unknown first-seen
   dates never populate first_seen_by_day.
4. Shared-poller concurrency. SharedPoller holds one lock across
   cache lookup, budget reservation, fetching, and cache publication,
   for both single and paged polls. A failed fetch leaves no cache
   entry, so a later poll retries cleanly; the failed attempt's
   budget charge stands, because an attempted request is a cost.

Relist definition (exact, deterministic):
- *Genuine relist*: same dedupe key, same segment, a later
  first_seen_at than the first observation in that segment, AND a
  different source_id. Example: first_seen 2026-10-22, source fx-1,
  then first_seen 2026-10-23, source fx-2.
- *Repeated observation*: same dedupe key, same segment, but NOT a
  genuine relist — same or earlier first_seen_at, or the same
  source_id. This is a duplicate record, not evidence of a new
  listing event.
- *Cross-segment overlap*: same dedupe key first seen in a different
  segment. This is neither a relist nor a repeated observation; it
  is multi-segment membership of one vehicle.

Canonical observation ordering (exact, deterministic):
Fixture input order never decides anything. Before per-segment
deduplication, records in a segment are sorted by _canonical_key:
(1) known, parseable first_seen_at first, unknown/malformed last;
(2) parsed first_seen_at, earliest first; (3) source_id, ascending;
(4) posted_at, known first, earliest first; (5) a deterministic
final tie-breaker over title, price, year, make, model, odometer,
ZIP, and photo hash. The first record for a dedupe key after this
sort is that key's first observation in the segment. "First
observation" in the relist definition means this canonical first,
never the first line in the fixture file. Missing or malformed
dates are never replaced by dates from another record.

Canonical global observation (exact, deterministic):
Global freshness uses ONE canonical observation per unique vehicle:
the per-segment first observation with the earliest known
first_seen_at, tie-broken by source_id, then posted_at, using the
same ordering as above. Freshness is computed ONLY from that
observation's own first_seen_at and posted_at pair. Dates are
never combined across observations to create a synthetic lag. If
the canonical observation's dates are chronologically invalid
(first_seen before posted, or any date after run_date), the vehicle
counts as invalid, even if another observation for the same vehicle
is valid. If a required timestamp is unknown, the vehicle counts as
unknown, even if another observation supplies that field. Conflicting
observations are therefore resolved by selection, not by merging,
and the result is a measurement of the canonical observation, not
provider-verified truth about the vehicle.

Budget notes:
- SimulatedBudget reserves atomically under a lock in one process,
  counts pages, retries, and failures, and stops at the cap. It
  rejects reservations below 1 and caps at or below 0 at construction.
  This is a simulation for offline verification, NOT production-grade
  cost enforcement (see PROBE_TUSCALOOSA.md for what live enforcement
  would still need). There is intentionally NO live-access switch,
  flag, or environment variable in this module.
- Budget is charged per segment polled, even if a segment has zero
  fixtures, because polling a segment is an attempt regardless of
  results. With pagination, a segment costs one call per page, with
  a minimum of one call per segment polled.

Reused, not duplicated:
- geo.zip_centroid / geo.distance_mi for territory filtering (local
  ZIP-centroid data only, no network).
- sources.base.NormalizedListing as the listing type.
- The price band (2000, 15000) from validate_deals.py is referenced
  as a constant only; valuation and profit are NOT computed here.
- provider_budget.py is NOT reused for the simulation: it is
  DB-backed, checks remaining() and then records() in two steps,
  and only records successful calls.

Usage:
    python -m probe_tuscaloosa --fixtures probe_sample_fixtures.json \
        --segments probe_segments_proposed.json --out probe_sample_report
    # writes probe_sample_report.json and probe_sample_report.md
    # (both labelled FIXTURE — SYNTHETIC ONLY)
"""
from __future__ import annotations

import argparse
import json
import re
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
# must never call a provider, licensed or not. The allowlist, not the
# example blocklist below, is the enforcement mechanism.
ALLOWED_FIXTURE_SOURCES = {"fixture"}
BLOCKED_SOURCES_EXAMPLES = {
    "ebay", "marketcheck", "marketcheck_fsbo", "craigslist",
    "facebook", "marketplace", "offerup", "bat", "hemmings",
    "cars_and_bids", "copart", "iaa", "govdeals", "gsa", "publicsurplus",
}
RELIST_DEFINITION = (
    "Genuine relist: same dedupe key, same segment, later first_seen_at "
    "than the first observation in that segment, and a different "
    "source_id. The first observation is the canonical first: records "
    "are sorted before deduplication by known first_seen_at first, "
    "earliest first_seen_at, source_id, posted_at, then a deterministic "
    "tie-breaker over title, price, year, make, model, odometer, ZIP, "
    "and photo hash; fixture input order never decides it. Repeated "
    "observation: same dedupe key, same segment, but not a genuine "
    "relist (same or earlier first_seen_at, or the same source_id). "
    "Cross-segment overlap is neither a relist nor a repeated "
    "observation. Global freshness uses one canonical observation per "
    "vehicle (earliest known first_seen_at, tie-broken by source_id "
    "then posted_at) and only that observation's own first_seen_at "
    "and posted_at pair; dates are never combined across observations."
)
BUDGET_NOTE = (
    "Budget is charged per segment polled, even if a segment has zero "
    "fixtures, because polling a segment is an attempt regardless of "
    "results. With pagination, a segment costs one call per page, "
    "with a minimum of one call per segment polled. This ledger is a "
    "fixture simulation, not production-grade cost enforcement."
)


class BlockedSourceError(ValueError):
    """Raised before any poll when a non-fixture source is requested."""


class BudgetExhausted(Exception):
    """Raised by reserve() when a simulated attempt would exceed the cap."""


def _norm_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def _norm_model(model: str | None) -> str:
    """Normalize a model for family matching: lower, alphanumeric only.

    "F-150 XLT" -> "f150xlt", "Silverado 1500" -> "silverado1500",
    "Ranger" -> "ranger". A model family matches by prefix, so
    "silverado" matches "silverado" and "silverado1500", but "ram"
    alone does not match "ram1500" families (see segment configs).
    """
    if not model:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(model).lower())


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@dataclass
class Segment:
    """One test-search segment.

    kind is "category" when models or makes are given, and
    "price_year_band" otherwise. A price/year band match is never a
    vehicle-category claim.
    """
    id: str
    name: str
    min_price: int = PRICE_BAND[0]
    max_price: int = PRICE_BAND[1]
    min_year: int | None = None
    max_year: int | None = None
    makes: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "category" if (self.models or self.makes) else "price_year_band"

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
            models=[_norm_model(m) for m in d.get("models", []) if _norm_model(m)],
        )

    def matches(self, listing: NormalizedListing) -> bool:
        if listing.price is None or not (self.min_price <= listing.price <= self.max_price):
            return False
        if self.min_year is not None and (listing.year is None or listing.year < self.min_year):
            return False
        if self.max_year is not None and (listing.year is None or listing.year > self.max_year):
            return False
        if self.models:
            # Missing or ambiguous model information must not produce
            # unsupported certainty: no model, no category match.
            norm = _norm_model(listing.model)
            if not norm or not any(norm.startswith(fam) for fam in self.models):
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
    Reservations below 1 and caps at or below 0 are rejected with
    ValueError, so an invalid input can never reduce recorded usage
    or create additional capacity.
    Persistence is best-effort (temp file + replace); a crash mid-write
    leaves the last committed ledger, which is acceptable for a
    simulation and NOT acceptable as live billing enforcement.
    """
    cap: int
    used: int = 0
    ledger: list[dict] = field(default_factory=list)
    stopped_at_cap: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.cap, int) or isinstance(self.cap, bool) or self.cap <= 0:
            raise ValueError(f"budget cap must be a positive integer, got {self.cap!r}")
        if self.used < 0:
            raise ValueError("budget used must not be negative")

    def reserve(self, n: int = 1, kind: str = "poll", segment: str = "") -> bool:
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ValueError(f"reservation must be a positive integer, got {n!r}")
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
        """Load a saved ledger. If the file exists, its cap wins and the
        caller's cap is only a fallback for a missing file. This is
        documented here because silently overriding a saved cap would
        change the budget a ledger was recorded against."""
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
    Synthetic fixtures use unmistakably synthetic identifiers such as
    FIXTURE-VIN-001, never realistic VINs.
    """
    vin = str((listing.extras or {}).get("vin", "")).strip().upper()
    if vin:
        return f"vin:{vin}"
    photo = str((listing.extras or {}).get("photo_hash", "")).strip()
    return f"listing:{listing.source}:{_norm_title(listing.title)}:{listing.odometer}:{photo}"


def _observation_dates(listing: NormalizedListing) -> tuple[date | None, date | None]:
    """(first_seen, posted) from the fixture only.

    First-seen comes ONLY from extras["first_seen_at"]. There is no
    fallback to posted_at: inventing an observation timestamp would
    create false zero-day freshness. Malformed dates parse to None.
    """
    first_seen = _parse_date((listing.extras or {}).get("first_seen_at"))
    posted = _parse_date(listing.posted_at)
    return first_seen, posted


def _canonical_key(listing: NormalizedListing) -> tuple:
    """Deterministic ordering for observation selection.

    (1) known first_seen first, unknown/malformed last; (2) earliest
    first_seen first; (3) source_id ascending; (4) known posted
    first, earliest posted first; (5) deterministic final tie-breaker
    over title, price, year, make, model, odometer, ZIP, and photo
    hash. Two records that tie on every element are indistinguishable
    for relist, freshness, and match purposes, so input order cannot
    change any reported metric. Missing or malformed dates are never
    manufactured: they sort last, they are not replaced.
    """
    first_seen, posted = _observation_dates(listing)
    return (
        0 if first_seen is not None else 1,
        first_seen if first_seen is not None else date.min,
        str(listing.source_id or ""),
        0 if posted is not None else 1,
        posted if posted is not None else date.min,
        _norm_title(listing.title),
        listing.price if listing.price is not None else -1,
        listing.year if listing.year is not None else -1,
        (listing.make or "").lower(),
        _norm_model(listing.model),
        listing.odometer if listing.odometer is not None else -1,
        listing.zip or "",
        str((listing.extras or {}).get("photo_hash", "")),
    )


def _canonical_observation_key(first_seen: date | None, posted: date | None,
                               source_id: str) -> tuple:
    """Ordering for choosing one canonical observation per vehicle.

    Same rule as _canonical_key, restricted to the fields a
    per-segment first observation carries: known first_seen first,
    earliest first_seen, source_id, known posted first, earliest
    posted. The chosen observation's own date pair is then used whole.
    """
    return (
        0 if first_seen is not None else 1,
        first_seen if first_seen is not None else date.min,
        str(source_id or ""),
        0 if posted is not None else 1,
        posted if posted is not None else date.min,
    )


class SharedPoller:
    """One poll per segment, shared by every test search mapped to it.

    poll(segment_id, fetch) calls fetch() at most once per segment_id
    and charges the budget once. A second search over the same segment
    reuses the cached listings and charges nothing. fetch() receives no
    network access; in this module it is always a fixture lookup.

    Concurrency: one lock covers cache lookup, budget reservation,
    fetching, and cache publication, for both poll() and poll_paged().
    A failed fetch leaves no cache entry, so a later poll retries
    cleanly; the failed attempt's budget charge stands. poll_calls
    counts successful segment polls only, not attempts or pages.
    """
    def __init__(self, budget: SimulatedBudget):
        self.budget = budget
        self._cache: dict[str, list[NormalizedListing]] = {}
        self.poll_calls = 0
        self._lock = threading.Lock()

    def poll(self, segment_id: str, fetch) -> list[NormalizedListing]:
        with self._lock:
            if segment_id in self._cache:
                return self._cache[segment_id]
            if not self.budget.reserve(1, kind="poll", segment=segment_id):
                raise BudgetExhausted(f"simulated budget cap reached before segment {segment_id}")
            listings = list(fetch())  # exception: no cache, no poll_calls
            self._cache[segment_id] = listings
            self.poll_calls += 1
            return listings

    def poll_paged(self, segment_id: str, fetch_page, page_count: int,
                   retries: int = 1) -> list[NormalizedListing]:
        """Poll a segment page by page, with retries per page.

        fetch_page(page_index) returns that page's listings. Each page
        attempt, including a failed attempt and its retry, costs one
        budget call. If any page exhausts its retries or the budget,
        BudgetExhausted (or the fetch exception) propagates, no partial
        result is cached, and a later poll retries from the first page.
        page_count is at least 1: polling a segment costs at least one
        call, even when it returns zero fixtures.
        """
        if not isinstance(page_count, int) or page_count < 1:
            raise ValueError(f"page_count must be a positive integer, got {page_count!r}")
        if retries < 0:
            raise ValueError(f"retries must not be negative, got {retries!r}")
        with self._lock:
            if segment_id in self._cache:
                return self._cache[segment_id]
            pages: list[NormalizedListing] = []
            for page_index in range(page_count):
                page: list[NormalizedListing] | None = None
                for attempt in range(retries + 1):
                    if not self.budget.reserve(1, kind="page", segment=segment_id):
                        raise BudgetExhausted(
                            f"simulated budget cap reached on page {page_index} "
                            f"of segment {segment_id}")
                    try:
                        page = list(fetch_page(page_index))
                        break
                    except Exception:
                        if attempt >= retries:
                            raise
                        # Retry: the loop reserves again on the next attempt.
                        continue
                assert page is not None
                pages.extend(page)
            self._cache[segment_id] = pages
            self.poll_calls += 1
            return pages


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
              run_date: str | None = None, page_size: int = 100) -> dict:
    """Run a fixture-only probe. Pure except for the budget ledger object.

    Raises BlockedSourceError before any poll if a non-fixture source
    is requested, and ValueError for a non-positive budget cap or page
    size. Never imports a provider adapter, never opens a socket, never
    reads an environment credential, never notifies.
    """
    for sid in source_ids:
        if sid not in ALLOWED_FIXTURE_SOURCES:
            raise BlockedSourceError(f"source {sid!r} is blocked in fixture mode")
    if zip_centroid(HOME_ZIP) is None:
        raise ValueError(f"home ZIP {HOME_ZIP} has no centroid in local data")
    if not isinstance(page_size, int) or page_size <= 0:
        raise ValueError(f"page_size must be a positive integer, got {page_size!r}")
    run_day = _parse_date(run_date) if run_date else None
    if run_date and run_day is None:
        raise ValueError(f"run_date is not a valid date, got {run_date!r}")

    budget = SimulatedBudget(cap=int(budget_cap))
    poller = SharedPoller(budget)
    by_segment: dict[str, list[dict]] = {}
    for rec in fixtures:
        by_segment.setdefault(str(rec.get("segment", "unsegmented")), []).append(rec)

    seen = 0
    out_of_radius = 0
    # Per-segment state is independent: order must not change it.
    per_segment: dict[str, dict] = {}
    # Global state: one entry per unique vehicle, across segments.
    # Each entry holds the per-segment first observations as
    # (first_seen, posted, source_id); global freshness later picks
    # exactly one canonical observation from them, never a mix.
    global_unique: set[str] = set()
    global_matches: set[str] = set()
    global_observations: dict[str, list[tuple[date | None, date | None, str]]] = {}
    global_relists = 0
    global_repeated = 0

    for seg in segments:
        records = by_segment.get(seg.id, [])
        page_count = max(1, (len(records) + page_size - 1) // page_size)

        def fetch_page(page_index: int, recs=records) -> list[NormalizedListing]:
            start = page_index * page_size
            return [fixture_listing(x) for x in recs[start:start + page_size]]

        try:
            polled = poller.poll_paged(seg.id, fetch_page, page_count)
        except BudgetExhausted:
            per_segment[seg.id] = {"segment": seg.id, "name": seg.name, "kind": seg.kind,
                                   "listings_seen": 0, "unique": 0, "matches": 0,
                                   "relists": 0, "repeated_observations": 0,
                                   "out_of_radius": 0, "stopped_at_cap": True}
            continue
        seg_seen = 0
        seg_unique: set[str] = set()
        seg_matches: set[str] = set()
        seg_relists = 0
        seg_repeated = 0
        seg_out = 0
        # Canonical order first: fixture input order must not decide
        # which record is a key's first observation. Pagination and
        # budget accounting above are unaffected by this sort.
        polled = sorted(polled, key=_canonical_key)
        # First observation per key *within this segment*, after the
        # canonical sort: the earliest known first_seen for that key.
        seg_first: dict[str, tuple[date | None, str]] = {}
        for listing in polled:
            seg_seen += 1
            seen += 1
            dist = distance_mi(HOME_ZIP, listing.latitude, listing.longitude)
            if dist is None and listing.zip:
                home = zip_centroid(HOME_ZIP)
                other = zip_centroid(listing.zip)
                if home and other:
                    from geo import haversine_mi
                    dist = round(haversine_mi(home, other), 1)
            if dist is None or dist > RADIUS_MI:
                seg_out += 1
                out_of_radius += 1
                continue
            key = dedupe_key(listing)
            first_seen, posted = _observation_dates(listing)
            if key in seg_first:
                prev_first_seen, prev_source_id = seg_first[key]
                if (first_seen is not None and prev_first_seen is not None
                        and first_seen > prev_first_seen
                        and listing.source_id != prev_source_id):
                    seg_relists += 1
                    global_relists += 1
                else:
                    seg_repeated += 1
                    global_repeated += 1
                continue
            seg_first[key] = (first_seen, listing.source_id)
            seg_unique.add(key)
            global_unique.add(key)
            global_observations.setdefault(key, []).append(
                (first_seen, posted, listing.source_id))
            if seg.matches(listing):
                seg_matches.add(key)
                global_matches.add(key)
        per_segment[seg.id] = {"segment": seg.id, "name": seg.name, "kind": seg.kind,
                               "listings_seen": seg_seen, "unique": len(seg_unique),
                               "matches": len(seg_matches), "relists": seg_relists,
                               "repeated_observations": seg_repeated,
                               "out_of_radius": seg_out, "stopped_at_cap": False}

    # Global freshness, canonical-observation only: for each unique
    # vehicle, pick exactly one per-segment first observation by the
    # canonical rule (earliest known first_seen, then source_id, then
    # posted) and use ONLY that observation's own first_seen/posted
    # pair. Dates are never combined across observations. If the
    # canonical observation is invalid, the vehicle is invalid even
    # if another observation is valid; if a timestamp is unknown,
    # the vehicle is unknown even if another observation supplies it.
    # first_seen_by_day follows the same policy: it counts the
    # canonical first_seen date for valid and posted-unknown vehicles
    # only, never for invalid or first_seen-unknown vehicles.
    lags: list[int] = []
    unknown_dates = 0
    invalid_dates = 0
    first_seen_by_day: dict[str, int] = {}
    for key, observations in global_observations.items():
        first_seen, posted, _source_id = min(
            observations,
            key=lambda obs: _canonical_observation_key(obs[0], obs[1], obs[2]))
        if first_seen is None:
            unknown_dates += 1
            continue
        if run_day is not None and first_seen > run_day:
            invalid_dates += 1
            continue
        if posted is None:
            unknown_dates += 1
            first_seen_by_day[first_seen.isoformat()] = first_seen_by_day.get(first_seen.isoformat(), 0) + 1
            continue
        if run_day is not None and posted > run_day:
            invalid_dates += 1
            continue
        if first_seen < posted:
            invalid_dates += 1
            continue
        lags.append((first_seen - posted).days)
        first_seen_by_day[first_seen.isoformat()] = first_seen_by_day.get(first_seen.isoformat(), 0) + 1

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
        "sources_blocked_examples": sorted(BLOCKED_SOURCES_EXAMPLES),
        "all_non_fixture_sources_blocked": True,
        "sources_blocked_note": "Allowlist only: only 'fixture' is allowed. Any other "
                               "source id raises BlockedSourceError. The "
                               "examples list above is not exhaustive.",
        "live_access": False,
        "network_calls": 0,
        "listings_seen": seen,
        "unique_listings": len(global_unique),
        "global_matches": len(global_matches),
        "relist_records": global_relists,
        "repeated_observations": global_repeated,
        "first_seen_by_day": dict(sorted(first_seen_by_day.items())),
        "out_of_radius_records": out_of_radius,
        "freshness": {**lag_stats, "unknown_date_listings": unknown_dates,
                      "invalid_date_listings": invalid_dates},
        "per_search": per_segment,
        "budget": {"cap": budget.cap, "used": budget.used,
                   "stopped_at_cap": budget.stopped_at_cap,
                   "poll_calls": poller.poll_calls,
                   "ledger_entries": len(budget.ledger)},
        "budget_note": BUDGET_NOTE,
        "segments_included": [s.id for s in segments],
        "segments_detail": [{"id": s.id, "name": s.name, "kind": s.kind,
                             "min_price": s.min_price, "max_price": s.max_price,
                             "min_year": s.min_year, "max_year": s.max_year,
                             "makes": s.makes, "models": s.models} for s in segments],
        "segments_excluded": "Any segment not in the owner-approved config is excluded. "
                            "No segment in a report is owner-approved unless the "
                            "config file itself was owner-approved. Category "
                            "segments require a model/make match; price/year "
                            "band segments claim no vehicle category.",
        "relist_definition": RELIST_DEFINITION,
        "qualifying_definition": "A qualifying match here means only: in radius, and "
                                "inside the segment's price/year bounds, plus "
                                "its model/make bounds when the segment is a "
                                "category segment. It is NOT a deal, a "
                                "valuation, or a profit estimate. Per-segment "
                                "match totals must not be summed: use "
                                "global_matches for unique qualifying vehicles.",
    }
    return report


def render_markdown(report: dict) -> str:
    lines = [f"# {report['label']}", "", report["scope"], "",
             f"Home ZIP: {report['home_zip']} | Radius: {report['radius_mi']} mi | "
             f"Price band: ${report['price_band'][0]}-${report['price_band'][1]}",
             f"Live access: {report['live_access']} | Network calls: {report['network_calls']}",
             "", "## Counts",
             f"- Listings seen (observations): {report['listings_seen']}",
             f"- Unique listings: {report['unique_listings']}",
             f"- Global qualifying matches (unique vehicles): {report['global_matches']}",
             f"- Genuine relist records: {report['relist_records']}",
             f"- Repeated observations: {report['repeated_observations']}",
             f"- Out-of-radius records: {report['out_of_radius_records']}",
             f"- First seen by day: {report['first_seen_by_day'] or 'none'}",
             f"- Freshness: {report['freshness']}",
             f"- Budget: {report['budget']['used']} / {report['budget']['cap']} "
             f"(poll calls: {report['budget']['poll_calls']}, "
             f"stopped at cap: {report['budget']['stopped_at_cap']})",
             f"- Budget note: {report['budget_note']}",
             "", "## Per search",
             "| Segment | Kind | Seen | Unique | Matches | Relists | Repeated | Stopped at cap |",
             "|---|---|---|---|---|---|---|---|"]
    for seg_id in sorted(report["per_search"]):
        s = report["per_search"][seg_id]
        lines.append(f"| {seg_id} | {s.get('kind', '')} | {s['listings_seen']} | {s['unique']} | "
                     f"{s['matches']} | {s.get('relists', 0)} | {s.get('repeated_observations', 0)} | "
                     f"{s['stopped_at_cap']} |")
    lines += ["", "## Coverage and exclusions", report["segments_excluded"],
              "", "## Relist definition", report["relist_definition"],
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
    ap.add_argument("--page-size", type=int, default=100,
                    help="Fixture records per simulated page (default 100)")
    a = ap.parse_args(argv)
    segments = [Segment.from_dict(d) for d in _load_json(a.segments)]
    fixtures = _load_json(a.fixtures)
    report = run_probe(segments=segments, fixtures=fixtures,
                       budget_cap=a.budget_cap, run_date=a.run_date,
                       page_size=a.page_size)
    out = Path(a.out)
    out.with_suffix(".json").write_text(json.dumps(report, indent=2, sort_keys=True))
    out.with_suffix(".md").write_text(render_markdown(report))
    print(f"fixture probe: {report['unique_listings']} unique of "
          f"{report['listings_seen']} seen; {report['global_matches']} global matches; "
          f"budget {report['budget']['used']}/{report['budget']['cap']}; live_access=False")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
