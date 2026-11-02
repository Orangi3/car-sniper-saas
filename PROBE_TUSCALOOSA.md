# Tuscaloosa fixture probe — scope, limits, and proposed segments

FIXTURE — NOT A REAL LISTING. Every count in a probe report is a count
of synthetic fixtures, not observed market inventory.

## Scope — do not conflate with the pilot

- **This probe:** 100-mile radius around Tuscaloosa, AL (ZIP 35401),
  fixture listings only, no users, no testers, no live providers.
- **The pilot in PILOT_TUSCALOOSA.md:** 150-mile radius, 10 testers,
  $2,000–$15,000 price band, real (licensed or first-party) data.
  This probe does NOT rewrite, replace, or approve that plan. The
  100-mile radius is a separate preliminary measurement exercise,
  chosen because it matches the MarketCheck Free/Basic radius
  documented in the pricing research, not because it is the pilot
  radius.

## What the probe does and does not prove

Does prove (on fixtures only): shared polling charges once per segment
(sequentially and, after the review repair, concurrently); a simulated
budget stops at its cap and rejects invalid reservations; pagination
and retries are counted by a real paged poll; blocked sources are
refused before any poll; per-segment and global dedupe are
order-independent for both segment order and fixture record order
(after the canonical ordering below; the budget-cap exception is
retained); freshness is computed only from a canonical observation's
own dates, never by combining dates across observations; reports are
reproducible.

Does NOT prove: real vehicle inventory in Tuscaloosa or Birmingham;
listing density, freshness, or match rates in any real market;
valuation accuracy or profit; customer demand; willingness to pay;
licensing permission for MarketCheck, eBay, or any other source; or
that any segment will produce deals. A "qualifying match" in a probe
report means only: in radius, inside the segment price/year bounds,
and, for a category segment, inside its model/make bounds. It is not
a deal.

## Segment classification — corrected after independent review

The original PR #20 head classified segments by price and year only,
so a sedan could match a truck segment and trucks-fullsize,
trucks-midsize, and work-vans-trucks were indistinguishable. That
defect is fixed, not documented away:

- A segment is a *category* segment only when it lists explicit
  model families and/or makes. A listing must match them; a missing
  or ambiguous model never matches a category segment.
  - trucks-fullsize: model families f150, f250, f350, f450,
    silverado, sierra, tundra, titan, ram1500, ram2500, ram3500.
    A model matches by normalized prefix, so "Silverado 1500"
    matches "silverado", and "F-150 XLT" matches "f150". Bare "ram"
    does not match, because it is ambiguous.
  - trucks-midsize: ranger, colorado, canyon, tacoma, frontier,
    gladiator, ridgeline.
  - jeeps-offroad: make "jeep" only. Jeep is a make, and every Jeep
    vehicle in the available data is an SUV/off-road vehicle, so a
    make filter is a responsible category mechanism here.
- Every other proposed category was consolidated, because make/model
  alone cannot distinguish SUVs, sedans, hatchbacks, vans, and
  work-vans responsibly without exhaustive model lists that would
  false-negative legitimate vehicles. Those segments are now
  price/year bands, honestly labelled as such:
  - band-2008-2020: $2,000–$15,000, 2008–2020, no category claim.
  - older-value: $2,000–$8,000, 2000–2012.
  - newer-value: $8,000–$15,000, 2015–2020.
  A band match must never be quoted as a category match. If a future
  probe needs SUV/sedan/van categories, it needs a body-type field
  from a provider or a curated, tested model list — not a rename.

## Proposed real segment list — NOT owner-approved

`probe_segments_proposed.json` now contains 6 segments (3 category,
3 price/year bands) for separate owner approval. No segment in that
file is approved, and no fixture in this repository was chosen to
imply that a segment has supply. The owner must approve the real
segment list, in writing, before any live probe is designed around
it. Synthetic test configurations in
`tests/test_probe_tuscaloosa.py` are explicitly synthetic and are not
that approval.

## Deduplication, relists, and repeated observations — exact definitions

- Global vehicle identity and per-segment membership are separate.
  A vehicle in two segments counts in each segment's unique and
  match counts, while the global unique count stays 1. Segment order
  does not change per-segment or global results, except that a
  budget cap stops later segments first, which is reported per
  segment as `stopped_at_cap`. That budget exception is retained
  and documented; it is not a claim that a capped run is
  order-independent.
- Fixture record order does not change any result. Before
  per-segment deduplication, records in a segment are sorted by the
  canonical ordering: (1) known, parseable first_seen_at first,
  unknown/malformed last; (2) parsed first_seen_at, earliest first;
  (3) source_id, ascending; (4) posted_at, known first, earliest
  first; (5) a deterministic final tie-breaker over title, price,
  year, make, model, odometer, ZIP, and photo hash. Pagination and
  budget accounting are computed before this sort and are
  unaffected by it.
- Per-segment match totals must NOT be summed. Use `global_matches`
  for unique qualifying vehicles.
- Genuine relist: same dedupe key, same segment, later first_seen_at
  than the first observation in that segment, AND a different
  source_id. "First observation" means the canonical first after the
  sort above, never the first line in the fixture file.
- Repeated observation: same dedupe key, same segment, but not a
  genuine relist (same or earlier first_seen_at, or the same
  source_id). This is a duplicate record, not evidence of a new
  listing event. Under canonical ordering, a record supplied second
  with an earlier first_seen_at is sorted first, so the later record
  is a relist, not a repeated observation.
- Cross-segment overlap: same dedupe key first seen in a different
  segment. This is neither a relist nor a repeated observation; it
  is multi-segment membership of one vehicle.
- Global freshness uses, for each unique vehicle, ONE canonical
  observation: the per-segment first observation with the earliest
  known first_seen_at, tie-broken by source_id, then posted_at.
  Freshness is computed only from that observation's own
  first_seen_at and posted_at pair. Dates are never combined across
  observations, so no synthetic lag can be created from unrelated
  fields.

## Freshness integrity

First-seen comes ONLY from the fixture's `first_seen_at` field.
There is no fallback to posted_at. Missing or malformed dates are
unknown, never zero lag. A first-seen date before the posted date,
or any date after `run_date` when `run_date` is supplied, is
invalid, never a negative lag. Unknown first-seen dates never
populate `first_seen_by_day`; a known first-seen date does populate
it even when the posted date is unknown, because the observation
date itself is known. The report separates valid, unknown, and
invalid counts, with median/min/max computed from valid lags only.

Conflicting observations are resolved by selection, not by
merging, and the result is NOT provider-verified truth. If the
canonical observation's dates are invalid, the vehicle counts as
invalid, even if another observation for the same vehicle is valid.
If a required timestamp is unknown on the canonical observation,
the vehicle counts as unknown, even if another observation supplies
that field. No missing value is ever replaced by a field from a
different observation. `first_seen_by_day`, valid/unknown/invalid
counts, and freshness statistics all follow this same canonical
policy: `first_seen_by_day` counts the canonical first_seen date
for valid and posted-unknown vehicles only, never for invalid or
first_seen-unknown vehicles.

## Budget safeguards — what is simulated, what is not

The probe's `SimulatedBudget` reserves atomically under a lock in one
process, counts pages, retries, and failures, and stops at the cap.
Reservations below 1 and caps at or below 0 are rejected with
`ValueError`, so invalid inputs cannot reduce recorded usage or
create capacity. `SharedPoller` holds one lock across cache lookup,
reservation, fetching, and cache publication, so concurrent polls
of one segment fetch once and charge once; a failed fetch leaves no
cache entry and its budget charge stands.

Budget is charged per segment polled, even if a segment has zero
fixtures, because polling a segment is an attempt regardless of
results. With pagination, a segment costs one call per page, with a
minimum of one call per segment polled. The sample report's budget
therefore reflects segments polled (and pages), not listings seen.

Existing `provider_budget.py` (reused as a concept, not as code here)
is DB-backed, checks `remaining()` and then `record()`s in two steps,
and records successful calls. Under parallel requests that
check-then-record window can overspend, and a failed request that was
attempted is not necessarily recorded as a cost.

A future live enforcement layer would therefore still need, at
minimum: a single transactional reserve-and-record operation in the
database (or a dedicated ledger service), a distributed lock or
single-writer for the ledger across worker processes, crash-safe
persistence (a committed ledger row, not a temp-file replace), and a
live gate that is a separate, owner-approved change — never a flag
added silently to this fixture module. Those changes are documented
here and are NOT in this PR, by design, to keep this PR narrow.
This simulated ledger must never be presented as production-grade
cost enforcement.

## Pagination and retry accounting — implemented, not just claimed

`SharedPoller.poll_paged()` polls a segment page by page. Each page
attempt, including a failed attempt and its retry, costs one budget
call. If a page exhausts its retries or the budget, no partial
result is cached and a later poll retries from the first page.
`run_probe()` uses paged polling for every segment (default page
size 100, so small fixture sets still cost one call per segment).
The paged/retry behaviour is tested directly in
`tests/test_probe_tuscaloosa.py`, not only through direct
`reserve()` calls.

## Security

- No network, no credentials, no production database, no
  notifications, no live adapter imports in `probe_tuscaloosa.py`.
- Source policy: only source id "fixture" is allowed. Every real
  source, including MarketCheck and eBay, raises BlockedSourceError
  before any poll. This is an allowlist: any source id other than
  "fixture" raises, whether or not it appears in the report's
  example blocklist. The example list in reports is not exhaustive,
  and the report now says so explicitly.
- Licensing controls in `sources/__init__.py` (`licensing_block`)
  are untouched and are not bypassed by this module; the probe does
  not call `iter_sources` or `poll_all` at all.
- Synthetic fixtures are labelled "FIXTURE — NOT A REAL LISTING" in
  code, in reports, and in sample files, and use unmistakably
  synthetic identifiers such as FIXTURE-VIN-001, never realistic
  VINs.

## Running it

    python -m probe_tuscaloosa --fixtures probe_sample_fixtures.json \
        --segments probe_segments_proposed.json --out probe_sample_report

This writes `probe_sample_report.json` and `probe_sample_report.md`.
Both are synthetic-only samples. Do not quote their counts as market
data.
