# FIXTURE — NOT A REAL LISTING

Separate preliminary fixture measurement only. NOT the PILOT_TUSCALOOSA.md pilot (150 miles, 10 testers). Fixture counts are NOT observed market inventory, customer demand, valuation accuracy, or licensing permission.

Home ZIP: 35401 | Radius: 100 mi | Price band: $2000-$15000
Live access: False | Network calls: 0

## Counts
- Listings seen (observations): 4
- Unique listings: 3
- Global qualifying matches (unique vehicles): 3
- Genuine relist records: 1
- Repeated observations: 0
- Out-of-radius records: 0
- First seen by day: {'2026-10-21': 1, '2026-10-22': 2}
- Freshness: {'median_days': 2.0, 'min_days': 2, 'max_days': 2, 'count_with_dates': 2, 'unknown_date_listings': 1, 'invalid_date_listings': 0}
- Budget: 6 / 450 (poll calls: 6, stopped at cap: False)
- Budget note: Budget is charged per segment polled, even if a segment has zero fixtures, because polling a segment is an attempt regardless of results. With pagination, a segment costs one call per page, with a minimum of one call per segment polled. This ledger is a fixture simulation, not production-grade cost enforcement.

## Per search
| Segment | Kind | Seen | Unique | Matches | Relists | Repeated | Stopped at cap |
|---|---|---|---|---|---|---|---|
| band-2008-2020 | price_year_band | 1 | 1 | 1 | 0 | 0 | False |
| jeeps-offroad | category | 0 | 0 | 0 | 0 | 0 | False |
| newer-value | price_year_band | 0 | 0 | 0 | 0 | 0 | False |
| older-value | price_year_band | 0 | 0 | 0 | 0 | 0 | False |
| trucks-fullsize | category | 3 | 2 | 2 | 1 | 0 | False |
| trucks-midsize | category | 0 | 0 | 0 | 0 | 0 | False |

## Coverage and exclusions
Any segment not in the owner-approved config is excluded. No segment in a report is owner-approved unless the config file itself was owner-approved. Category segments require a model/make match; price/year band segments claim no vehicle category.

## Relist definition
Genuine relist: same dedupe key, same segment, later first_seen_at than the first observation in that segment, and a different source_id. The first observation is the canonical first: records are sorted before deduplication by known first_seen_at first, earliest first_seen_at, source_id, posted_at, then a deterministic tie-breaker over title, price, year, make, model, odometer, ZIP, and photo hash; fixture input order never decides it. Repeated observation: same dedupe key, same segment, but not a genuine relist (same or earlier first_seen_at, or the same source_id). Cross-segment overlap is neither a relist nor a repeated observation. Global freshness uses one canonical observation per vehicle (earliest known first_seen_at, tie-broken by source_id then posted_at) and only that observation's own first_seen_at and posted_at pair; dates are never combined across observations.

## Qualifying definition
A qualifying match here means only: in radius, and inside the segment's price/year bounds, plus its model/make bounds when the segment is a category segment. It is NOT a deal, a valuation, or a profit estimate. Per-segment match totals must not be summed: use global_matches for unique qualifying vehicles.
