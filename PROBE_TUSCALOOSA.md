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

Does prove (on fixtures only): shared polling charges once per segment;
a simulated budget stops at its cap; pagination and retries can be
counted; blocked sources are refused before any poll; dedupe and
freshness logic are deterministic; reports are reproducible.

Does NOT prove: real vehicle inventory in Tuscaloosa or Birmingham;
listing density, freshness, or match rates in any real market;
valuation accuracy or profit; customer demand; willingness to pay;
licensing permission for MarketCheck, eBay, or any other source; or
that any segment will produce deals. A "qualifying match" in a probe
report means only: in radius, in the segment price/year/make bounds.
It is not a deal.

## Proposed real segment list — NOT owner-approved

`probe_segments_proposed.json` contains 10 proposed segments for
separate owner approval. No segment in that file is approved, and no
fixture in this repository was chosen to imply that a segment has
supply. The owner must approve the real segment list, in writing,
before any live probe is designed around it. Synthetic test
configurations in `tests/test_probe_tuscaloosa.py` are explicitly
synthetic and are not that approval.

Proposed segments (price/year bounds only; no counts, no demand):
trucks-fullsize, trucks-midsize, suvs, sedans, hatchbacks-compact,
vans-minivans, work-vans-trucks, jeeps-offroad, older-value
(2000–2012, $2,000–$8,000), newer-value (2015–2020, $8,000–$15,000).

## Budget safeguards — what is simulated, what is not

The probe's `SimulatedBudget` reserves atomically under a lock in one
process, counts pages, retries, and failures, and stops at the cap.
That is a simulation for offline verification.

Existing `provider_budget.py` (reused as a concept, not as code here)
is DB-backed, checks `remaining()` and then `record()`s in two steps,
and records successful calls. Under parallel requests that check-then-
record window can overspend, and a failed request that was attempted
is not necessarily recorded as a cost.

A future live enforcement layer would therefore still need, at
minimum: a single transactional reserve-and-record operation in the
database (or a dedicated ledger service), a distributed lock or
single-writer for the ledger across worker processes, crash-safe
persistence (a committed ledger row, not a temp-file replace), and a
live gate that is a separate, owner-approved change — never a flag
added silently to this fixture module. Those changes are documented
here and are NOT in this PR, by design, to keep this PR narrow.

## Security

- No network, no credentials, no production database, no
  notifications, no live adapter imports in `probe_tuscaloosa.py`.
- Source policy: only source id "fixture" is allowed. Every real
  source, including MarketCheck and eBay, raises BlockedSourceError
  before any poll.
- Licensing controls in `sources/__init__.py` (`licensing_block`)
  are untouched and are not bypassed by this module; the probe does
  not call `iter_sources` or `poll_all` at all.
- Synthetic fixtures are labelled "FIXTURE — NOT A REAL LISTING" in
  code, in reports, and in sample files.

## Running it

    python -m probe_tuscaloosa --fixtures probe_sample_fixtures.json \
        --segments probe_segments_proposed.json --out probe_sample_report

This writes `probe_sample_report.json` and `probe_sample_report.md`.
Both are synthetic-only samples. Do not quote their counts as market
data.
