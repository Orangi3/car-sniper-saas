# Valuation audit — how FlipFirst decides a car is a deal

Audit of the code on this branch (2026-10-30). Every claim points to code or
a test. Benchmark results are on **SIMULATED** engineer-labeled cases; **no
real-world accuracy has been measured** — that starts with the first
licensed data (`FIRST_LIVE_TEST.md`).

## Pipeline

1. **Normalize** (`sources/base.py`, `sniper._normalize_listing`): year,
   make, model (canonical spellings — "F150"/"F 150" → F-150), mileage and
   price from structured fields or the title; branded-title, damage and
   mechanical wording; dealer and payment-teaser prices.
2. **Deduplicate** (`sniper.ingest`, `valuation.cross_post_key`): one row
   per source listing; same VIN or same year/make/model/price/mileage across
   sources collapse to one comparable; relists detected.
3. **Value** (`valuation.value_listing`): comparable asking prices — same
   make and model, ±1 model year (±2 only to reach 3), same duty class /
   body variant, same trim preferred, structured drivetrain/engine matched
   when stated, private sellers preferred, stale peers down-weighted then
   dropped, regional (150 mi) before national, Theil–Sen mileage adjustment
   only with ≥ 6 odometers, MAD outlier rejection, weighted median and
   25th–75th percentile range.
4. **Assess risk** (`sniper.assess_risk`): flags; any blocking flag or low
   confidence keeps a car out of the trusted feed and conservative alerts.
5. **Profit** (`profit.py`): resale value minus assumed costs, as a range.

## Factors

| Factor | Used | How / limit |
|---|---|---|
| Make, model, year | yes | title parsing for title-only sources; ±1 year |
| Trim | partly | from the title (`parse_trim`); same-trim preferred; capped at low confidence when most comparables state a different trim |
| Duty class / body variant | yes | Silverado/Sierra/Ram HD, Wrangler Unlimited, Titan XD never compared with the other variant |
| Engine, drivetrain | partly | matched only from structured provider fields, never guessed from text |
| Mileage | yes | adjustment with enough data; low confidence when far above every comparable; implausibly low odometer for the age is a blocking flag |
| Title status | partly | branded-title wording blocks; a "rebuilt engine" is not a branded title; a clean title is never assumed verified |
| Condition | partly | damage and mechanical wording blocks ("needs_work"); title-only listings say "description not checked" |
| Geography | yes | regional comparables within 150 mi; otherwise national with lower confidence |
| Asking vs completed sales | yes | separate evidence classes; asking-based values are labeled, range widened ±3%, confidence capped at medium |
| Completed sales | only licensed feeds | none licensed today, so values are asking-based |
| Repair costs | assumption | defaults $500 mechanical + $250 cosmetic, user-editable |
| Transaction costs | assumption | selling fees $100, holding $5/day × 21 days, optional transport per mile, tax and title if set |
| Resale range | yes | value range × (1 − 5% asking-to-sale haircut) when asking-based |
| Gross profit | estimate | base / conservative / optimistic scenarios; labeled "Estimated, not guaranteed" |
| Confidence | yes | high/medium/low from named evidence points; not a probability; not calibrated against sales |

## Misleading listings and the defense against each

| Pattern | Defense | Evidence (tests / benchmark case) |
|---|---|---|
| Salvage / rebuilt title | `branded_title` blocks; never a comparable | `test_salvage_never_alerts`, `test_branded_title_never_trusted`; benchmark `rebuilt-title`, `salvage-in-desc` |
| Rebuilt *part* mistaken for a branded title | part phrases ignored | `test_a_rebuilt_part_is_not_a_branded_title` |
| Non-running, engine or transmission problems, flood, collision, airbags | `needs_work` blocks | `test_damage_wording_marks_needs_work`, `test_needs_work_never_alerts`; benchmark `needs-motor`, `wont-start`, `front-end-damage`, `transmission-slipping` |
| Denials read as damage ("never wrecked", "damage free") | negation rules | `test_denials_of_damage_are_not_flagged` |
| Misidentified trim / model variant | variant split; trim cap | `test_half_ton_is_not_valued_from_heavy_duty_asks`, `test_stated_trim_that_no_comparable_shares_is_low_confidence`; benchmark `duty-class-hd-peers`, `trim-xl-vs-lariat` |
| Mileage far above comparables | low confidence | `test_mileage_far_beyond_every_comparable_caps_confidence`; benchmark `high-miles-beyond-comps` |
| Incorrect (implausibly low) mileage | `mileage_suspect` blocks | `test_implausibly_low_mileage_is_not_a_trusted_deal`; benchmark `mileage-typo-18k` |
| Auction current bid shown as a price | `auction_bid` blocks; bids never comparables; UI says "Current bid" | `test_running_auction_bid_is_never_a_trusted_deal`, `test_an_auction_price_is_called_a_current_bid`; benchmark `auction-current-bid` |
| Fake or suspicious price | scam wording, > 50% under market, payment teasers | `test_more_than_half_off_goes_to_the_risk_feed`, `test_too_good_to_be_true_does_not_alert`, `test_suspicious_price_is_flagged_not_trusted`; benchmark `too-good-55`, `scam-*`, `payment-teaser`, `dollar-one` |
| Expired listing | eBay hidden after 6 h unconfirmed; ended auctions hidden; seller listings hidden after 30 days | `test_ebay_listings_hidden_once_not_reconfirmed_for_6_hours`, `test_ended_auction_is_hidden_and_a_live_one_is_labeled` |
| Duplicate listings | VIN / cross-post collapse; one notification per car | `test_same_vin_and_cross_posts_collapse_to_one`, `test_same_vin_on_two_sources_notifies_once`; benchmark `dup-heavy-market` |
| Asking price presented as a sale | separate evidence class and labels end to end | `test_asking_comps_never_labelled_as_sales`, `test_asking_prices_are_labeled_as_asking_end_to_end` |

## Analyze: cars users find themselves (`analyze.py`)

The same engine values a car a user types in (any source, no ingestion):

1. **Identity.** What the user entered is checked against the free NHTSA vPIC
   VIN decode. A failed check digit (`vin_invalid`) or a different year,
   make or model (`vin_mismatch`) blocks the car. A VIN can fill in the
   year, make and model when the user left them blank.
2. **Value.** `valuation.value_listing` runs on comparables already in
   FlipFirst that `/api/deals` may show. Those are licensed sources and
   FlipFirst sellers, within each source's display window (eBay: 6 h).
   Analyze makes no sold-price lookups and no third-party listing calls.
   Fewer than three usable comparables means **no value**; nothing is
   modeled or invented.
3. **Profit.** `profit.compute` runs from the price the user expects to pay,
   their costs for this car on top of their saved assumptions, and either:
   - the market value, with the usual asking-to-sale haircut; or
   - the user's own resale estimate, labeled as theirs, with no haircut,
     since it is already a sale price.

   With neither, profit isn't computed. The user still gets:
   - **break-even resale**: total money in;
   - **maximum offer**: the price that leaves the target profit;
   - **margin of safety**: how far resale can fall before a loss.
4. **Risks.** The title and condition the user chose, plus their fault notes
   and pasted description, are checked with the same negation-aware patterns
   as listings, and two more: flood and structural/airbag. Other checks:
   - implausible mileage (shared with Discover);
   - a price far below or above the comparables;
   - scam wording;
   - "needs work" with no repair estimate.
5. **Decision.** Thresholds are constants in `analyze.py`:

| Class | Rule |
|---|---|
| High risk | any blocking risk, or the expected case loses money |
| Insufficient evidence | no market value and no resale estimate of the user's |
| Strong candidate | market value at medium/high confidence, no blocking risk, low-case profit ≥ $1,000, expected ROI ≥ 15 %, margin of safety ≥ 10 % |
| Worth investigating | everything else that makes money in the expected case |

A user's own estimate never yields "strong candidate". Every analysis lists:
- the data it used;
- the information still missing;
- the reasons for its class.

Tests: `tests/test_analyze.py`, run on SQLite and PostgreSQL, with SIMULATED comparables.

## Benchmark (SIMULATED)

`python benchmarks/run_benchmark.py`: 39 synthetic cases labeled by the
engineer — 11 true positives, 27 true negatives, 1 false positive, 0 false
negatives. The remaining false positive is a seller who states the wrong
trim; text alone cannot catch it, so it stays in the set as a measured
limit. These numbers show behavior on known traps, not market accuracy.

## Known limits

- Keyword condition screening: "no rust, minor front end damage" still reads
  as a denial; a person reads every listing before acting.
- Title-only sources (eBay summaries) give no description, VIN or title
  brand.
- Cab, bed and drivetrain from titles are not matched.
- No licensed completed-sale evidence: every value today is asking-based.
- Confidence is not calibrated; calibration needs labeled real outcomes
  (`EVALUATION.md`).

## How real accuracy gets measured

`validate_deals` on licensed data → you label each candidate → precision
with a 95% lower bound and false positives by reason (`EVALUATION.md`
§ Real-deal validation and gates). Each new false-positive pattern becomes a
test like the ones above.
