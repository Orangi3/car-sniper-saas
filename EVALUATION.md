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
