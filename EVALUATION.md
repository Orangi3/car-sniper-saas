# Real-world evaluation

The unit tests and `benchmarks/` (38 synthetic, engineer-labeled cases)
check behavior on known edge cases. **They are not evidence that customers
find worthwhile cars.** This workflow measures that, using real listings and
real outcomes recorded by people.

## What is tracked (`eval_observations`, `evaluation.py`)

| Recorded by the system (`snapshot`) | Recorded by a person (`label` / CSV) |
|---|---|
| source + listing id, first seen / detection time | buyer action: none, viewed, contacted, negotiated, purchased, passed |
| posted time **and whether it is the seller's own post time** (`posted_at_kind`) | seller contacted |
| notification queued time, provider-accepted time (from the jobs table) | sold (independently confirmed), sale amount where lawfully available |
| asking price, value range, evidence class, confidence, estimated base profit | acquisition costs, realized profit |
| whether the system alerted (owner alert or any subscriber match) | outcome label (below), labeler, notes |

Outcome labels: `genuine_opportunity`, `weak_opportunity`, `bad_comparison`,
`mechanical_condition_problem`, `suspicious_listing`, `duplicate`,
`listing_unavailable`, `insufficient_evidence`, `purchased_profit_verified`
(requires a realized profit). Nothing infers an outcome automatically.

## Metrics (`python -m evaluation report`)

- **Precision of alerts**: genuine / (genuine + bad) among alerted, labeled
  listings. `listing_unavailable` and `insufficient_evidence` are excluded
  (outcome unknown).
- **Recall and false-positive rate**: computed **only** from a reference set
  (`in_reference_set`): a complete, reviewed sample of listings in a territory
  and time window, labeled whether or not we alerted. Without one, the report
  says recall is not computable.
- **Detection latency**: seller-posted time → first seen, only where the
  source gives the seller's own post time (provider first-seen times are
  excluded).
- **Alert latency**: first seen → notification queued, and → provider
  accepted. (Delivery to the handset is not observable.)
- **Opportunities per active user** over 30 days.
- **Estimated vs realized profit** for verified purchases.
- **Valuation error**: estimated value vs independently confirmed sale
  amount — median absolute % error, mean bias, and interval coverage (share
  of sales inside the reported value range), overall and per evidence class.
- **Coverage** by source and by territory (listing state): observations,
  share valued, alerted, labeled.

Every metric reports its sample size; below 20 it is flagged
`enough_samples: false`.

## First real listings (cohort)

When a source first delivers real listings (eBay, seller submissions, …):

1. `python -m evaluation cohort --source ebay --n 100 --snapshot --sheet review.csv`
   prints validity, field completeness, duplicate rate, comparable coverage,
   confidence mix, potential-deal rate and the conservative profit-estimate
   distribution, records each listing for review, and writes `review.csv`.
2. A person opens each listing, fills `outcome_label` (see the list above)
   and `labeled_by`, then `python -m evaluation import review.csv`.
3. Re-run `cohort` for precision of potential deals among reviewed ones,
   and `report` for latency and valuation error.

Profit figures are estimates from asking-price comparables, not outcomes.

## Real-deal validation (`validate_deals`)

The harness answers "does the pipeline find real deals in real data?"
before any customer sees a result. It never touches the production database
or notifications (details in the module docstring; `EBAY_SETUP.md` step 8
for the server command).

1. **Run** — `python -m validate_deals run --sources ebay --zip 35401
   --radius 150 --polls 4 --interval 1800 --out <dir>`: bounded polls of
   licensed sources, the production pipeline into a private SQLite file,
   a final valuation pass over everything collected. Re-running with the
   same `--out` continues the run (more polls, a bigger comparable pool).
2. **Check provenance** — `report.json` → `headline` / `data_kind`. Only
   `real` (exit code 0) is a live-data result. `sandbox` and `stand_in` runs
   prove plumbing only.
3. **Review every candidate** in `review.csv` (trusted first). Open the
   listing, compare it with the comparables in `report.json`, contact the
   seller where useful, and set `outcome_label`:

   | What you found | Label |
   |---|---|
   | Worth pursuing at this price after checking it | `genuine_opportunity` |
   | Cheap, but not enough margin after costs | `weak_opportunity` |
   | Compared with the wrong cars (trim, cab, drivetrain, duty class, model years, mileage) | `bad_comparison` |
   | Damage, mechanical or title problem the system did not flag | `mechanical_condition_problem` |
   | Scam signs, fake price, bait listing, current auction bid read as a price | `suspicious_listing` |
   | Same car as another candidate | `duplicate` |
   | Gone before review | `listing_unavailable` |
   | Can't judge from the information available | `insufficient_evidence` |

   Note the specific reason in `notes` (e.g. "XL work truck vs Lariat
   comps"): it is how new false-positive patterns become regression tests.
4. **Measure** — `python -m validate_deals report <dir>` → precision of
   trusted candidates, its 95% lower bound (Wilson), false positives by
   label, unavailable rate, and genuine cars that landed in the risk feed.
   `python -m evaluation import <dir>/review.csv` keeps the labels in the
   evaluation store (ids and labels only).
5. **Purge** — `python -m validate_deals purge <dir>` once the review is
   done (the run directory holds provider data).

Never quote a harness profit as a result: it is an estimate from asking
prices and default costs. Only `purchased_profit_verified` rows carry a
realized profit.

## Pilot metrics and acceptance criteria

No real measurement exists yet; every number below is a **proposed
threshold for the owner to confirm**, not a result.

| Area | Metric | How it is measured | Proposed pilot target |
|---|---|---|---|
| Coverage | In-band listings per week in the territory, per source | `validate_deals` funnel (`unique_listings`) per weekly run; `python -m evaluation cohort --source ebay` in production | measure in week 0; no target of its own — the trusted-candidate rate below is what testers feel |
| Coverage | Share valued (≥ 3 comparables) | funnel `valued / unique_listings` | ≥ 60% |
| Coverage | Field completeness: mileage, location, trim | `cohort` → `field_completeness_pct` | report; mileage drives confidence |
| Coverage | Trusted candidates per week | funnel `trusted_candidates` across runs | ≥ 5/week for 10 testers |
| Speed | Detection latency: seller post → first seen | `evaluation report` (seller-posted sources only); harness `freshness` | p90 ≤ 15 min at 5-min polling |
| Speed | Valued latency: first seen → valuation stored | `near_misses.refreshed_at − listings.first_seen_at` | p90 ≤ 2 min |
| Speed | Alert latency: first seen → notification accepted | `evaluation report` (once alerts are on) | p90 ≤ 5 min |
| Speed | Time a candidate stays listed | harness `freshness` across polls (`seen_in_polls`, last confirmed) | report: how fast testers must act |
| Accuracy | Precision of trusted candidates | human labels → `validate_deals report` (`precision`, `precision_lower_95`) | Gate 1: ≥ 20 judged, lower bound ≥ 40%; Gate 2: ≥ 40 judged, lower bound ≥ 50% |
| Accuracy | False positives by reason | `false_positive_reasons` | no single reason ≥ 25% of judged without a fix + regression test |
| Accuracy | Unavailable at review | `unavailable / labeled` | ≤ 30% (else poll more often or review faster) |
| Accuracy | Genuine cars in the risk feed | `review.risk.genuine` | report; tune flags only with evidence |
| Accuracy | Recall and false-positive rate | weekly reference set (below) | report with sample size; no target until n ≥ 20 |
| Accuracy | Valuation error | `evaluation report` vs confirmed sale amounts | report only; too few sales expected in a pilot |
| Reliability | Successful polls | `polls.jsonl` statuses; `source_health`; Healthchecks pings | ≥ 95% of polls healthy over 7 days |
| Reliability | Schema drift, dead jobs | probe/harness `schema`; `jobqueue` stats | 0 unresolved |
| Reliability | Display-rule violations | eBay listings shown with last confirmation > 6 h | 0 (enforced in code) |
| Economics | Provider calls per day vs quota | `requests` per poll × polls per day | ≤ 20% of eBay's 5,000/day |
| Economics | Cost per trusted candidate | monthly cost ÷ trusted candidates per month | report (ESTIMATE) |
| Economics | Tester outcomes | deal stages (saved → contacted → bought), `purchased_profit_verified` | report; realized profit only from verified purchases |

Gates:

- **Gate 0 — first real data:** a `validate_deals` run on the production
  host exits 0 (real listings), with eBay's production approval recorded.
- **Gate 1 — invite the 10 testers** (free; deal feed visible, alerts off):
  7 consecutive days of polling meeting the reliability targets; coverage
  targets met; ≥ 20 judged trusted candidates with a precision lower bound
  ≥ 40%; every false-positive pattern seen twice has a fix or is labeled in
  the UI.
- **Gate 2 — turn on customer alerts** (still free): two more weeks with
  testers; ≥ 40 judged trusted candidates with a lower bound ≥ 50%;
  unavailable rate ≤ 30%; at least half the testers say a candidate was
  worth contacting.
- Paid plans are out of scope for this pilot.

## Running a beta evaluation

1. Territory and window: e.g. 35401 + 75 mi, 4 weeks.
2. For a reference set, once a week review every in-territory listing in the
   $2k–$15k band that the system ingested in a 24-hour window:
   `python -m evaluation snapshot <ids…> --reference`, then label each.
3. For every alert a beta user acts on, record the action and outcome:
   `python -m evaluation label craigslist 12345 genuine_opportunity --action contacted --contacted --by owner`
4. Bulk entry: `python -m evaluation import samples.csv` (columns as in
   `tests/fixtures/eval_samples_FIXTURE.csv`).
5. `python -m evaluation report` or `GET /api/admin/eval/report`.

Only use listings you are authorized to collect and keep (see `SOURCES.md`).

## Demonstration on fixture data (NOT real-world)

`tests/fixtures/eval_samples_FIXTURE.csv` has 6 synthetic rows. With
`--include-fixtures` the report is stamped
`"INCLUDES FIXTURE DATA — NOT REAL-WORLD"` and shows, for example:
precision 2/3, recall 2/3, FPR 1/2, median detection latency 600 s,
median delivery latency 31 s, estimate-vs-realized error $250 (n=1) — all
flagged `enough_samples: false`. These numbers only show that the harness
computes what it should. **No real-world performance has been measured yet.**
