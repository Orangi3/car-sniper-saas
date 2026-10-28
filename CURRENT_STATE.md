# Current state, blockers and roadmap

Branch `feat/market-intelligence-v2`, 2026-10-28. "Verified" = observed in
this session (test run, command output, file contents). "Assumed" = not
observed; stated with the reason.

## 1. Assessment

### A. What works (verified)

- Ingestion → scoring → alerting on durable jobs, one database layer, both
  SQLite and PostgreSQL 16: **SQLite 311 passed / 2 skipped, Postgres 313
  passed** on this branch before the docs commit.
- Honest per-source health (blocked / rate-limited / auth / malformed /
  unverified), licensing gate in commercial mode, provider call budgets.
- Valuation with separate evidence classes, regional comparables, market
  segments, per-comparable match quality, confidence that cannot reach
  "high" without completed sales; profit with repair, holding, transport,
  negotiation scenarios and annualized ROI.
- Per-subscriber matching in ZIP + radius territories; risk-adjusted
  ranking with named factors; mobile deal card that shows all of the above.
- Evaluation harness (precision, recall with a reference set, latency,
  valuation error vs sale price, coverage); preflight readiness levels.
- Schema-drift detection for the eBay and MarketCheck FSBO adapters, and a
  bounded live probe (`python -m verify_sources`) + manual workflow.
- `python -m preflight` (scratch DB, commercial mode, after a real scheduler
  tick): **READY FOR: PRIVATE INTERNAL TESTING**.

### B. What does not work yet

- **No licensed source has ever returned a live listing.** eBay and
  MarketCheck adapters are fixture-tested only. From this sandbox,
  listing-site and vendor-doc requests are blocked by network policy, and no
  provider keys exist here.
- No real-world accuracy measurement: zero real evaluation observations.
- Desktop `dashboard.html` predates evidence/profit fields (mobile `/m` is
  current).

### C. Blocked by people, money or agreements

MarketCheck written agreement + key; eBay production keys; legal review;
production host, HTTPS origin, backups; live Stripe products; branch
protection; Anthropic key for agent runs. See `OWNER_ACTIONS.md`.

### D. Risks

| Risk | Likelihood | Impact | Mitigation in place |
|---|---|---|---|
| MarketCheck declines or prices the stored-alert use out of reach | Medium (assumed) | Critical — no private-party supply | Fail-closed gate; eBay secondary; seller-submitted listings later |
| Too few FSBO listings in one territory | Unknown | High | Probe reports counts per territory before any spend beyond one call |
| Asking-price valuations overstate resale | High | Medium | Haircuts, confidence cap, widened range, evaluation of error vs sale price |
| Provider payload changes silently | Medium | Medium | Schema drift → `malformed`/degraded health |
| Agent automation merges or deploys | Low once protected | High | PR-only workflow; **branch protection still off** |

### E. Assumptions not yet verified

eBay pickup filters return vehicles in category 6001; MarketCheck FSBO field
names match its general listing schema; FSBO sort `dom asc` is newest first;
days-to-sell 21 and holding $5/day; competitor prices; all unit-economics
inputs marked ASSUMPTION.

### Ten highest-leverage tasks

| # | Task | Who | Unblocks |
|---|---|---|---|
| 1 | Add eBay keys as repo secrets; run `live-source-check` for the launch ZIP | Owner (10 min) | First real listings; turns eBay's estimates into facts |
| 2 | Send the MarketCheck inquiry (`OWNER_ACTIONS.md` §1) | Owner | Private-party supply decision |
| 3 | Enable branch protection on `main` | Owner (5 min) | Safe automation |
| 4 | Fix whatever the first live probe reveals (field mapping, geography) | Agent | Healthy licensed source |
| 5 | Production host + HTTPS + Postgres + backups | Owner + agent | Free beta preflight |
| 6 | Evaluation reference set: one week, one territory | Owner + agent | First real precision/latency numbers |
| 7 | Calibrate valuation haircuts from confirmed sale prices | Valuation agent | Trustworthy profit |
| 8 | Desktop dashboard parity with mobile deal card | Frontend agent | Consistent UI |
| 9 | Live Stripe products (annual Pro + Lite) | Owner | Paid beta |
| 10 | Seller-submitted listing intake (design only) | Product + Architect | Owned data moat |

## 2. Blocker matrix

| Blocker | Impact | Root cause | Solution | Effort | Depends on | Owner / agent |
|---|---|---|---|---|---|---|
| No licensed private-party data | Product has nothing to sell | Only MarketCheck FSBO offers it; terms need written permission | Inquiry → agreement → key → probe | Days–weeks | Vendor | **Owner** |
| No live verification of any adapter | Unknown field mapping | No keys in any environment | eBay keys → `live-source-check` | 10 min + fixes | eBay keys | Owner, then agent |
| No real-world accuracy data | Can't claim performance | No users / no data yet | Free beta + evaluation reference set | 2–4 weeks | Data, host | Owner + agent |
| No production host / HTTPS / backups | Free beta preflight fails | Not provisioned | `DEPLOY.md`, `LAUNCH_CHECKLIST.md` | 1 day | Budget approval | Owner + agent |
| Payments test-only | Can't charge | Live products are owner-only | Create Stripe prices | 1 h | Pricing decision | **Owner** |
| `main` unprotected | Agents/tokens could push to main | Repo setting | Branch protection | 5 min | — | **Owner** |
| Asking-only valuations | Profit may be overstated | No licensed sold-price feed | Calibrate from confirmed sales in evaluation; consider a licensed pricing API later | Ongoing | Beta data | Valuation agent |
| Legal review | Licensing risk | Not done | Counsel reviews `SOURCES.md`, agreements | Varies | Agreements | **Owner** |

## 3. Roadmap

| Phase | Goal | Exit criteria (measurable) |
|---|---|---|
| **Now (this PR)** | Verification path, valuation v2, automation foundation | CI green on both engines; drift guard clean |
| **Week 1** | First real data | `verify_sources` exits 0 for eBay in the launch territory; listing count + field coverage recorded |
| **Weeks 1–3** | Private-party decision | MarketCheck answer in writing; if yes, FSBO probe exits 0 and `num_found` for territory recorded |
| **Stage A** (below) | Internal testing on real data | Stage A criteria met |
| **Stage B** | Free controlled beta | Stage B criteria met for 2 consecutive weeks |
| **Stage C** | Paid beta | Stage C criteria met |
| Later | Moat | Seller-submitted listings; title checks; licensed pricing model |

## 4. Valuation improvement plan

Done in this branch (`valuation.py`, `profit.py`, `evaluation.py`):
regional comparables with stated fallback; drivetrain/engine matching from
structured fields; per-comparable match quality; separate segments (private
asking, dealer asking, confirmed sales, auction results, historical, model
estimate); asking-only confidence capped at medium and range widened ±3%;
mechanical vs cosmetic repairs, holding cost × days to sell, negotiation
spread across scenarios, annualized ROI; valuation error and interval
coverage against confirmed sale amounts.

Next, in order, each gated on real data:

1. **Calibrate** `asking_to_sale_pct`, `ASKING_UNCERTAINTY_PCT` and the
   dealer haircut from `valuation_error.by_evidence_class` once n ≥ 20
   confirmed sales per class. Target: median absolute error ≤ 10%,
   interval coverage ≥ 50% for the IQR-based range.
2. **Days to sell from data**: replace the 21-day assumption with observed
   listing lifetimes (`first_seen_at` → disappearance) per make/model/price
   band once a territory has 60+ days of history.
3. **Regional price index** only if error by territory shows a bias > 5%.
4. **Licensed model estimate** (e.g. a pricing API) as the
   `model_estimate` segment — evaluated against sales before it may affect
   confidence.
5. Condition from structured fields only (provider condition codes); no
   free-text guessing beyond the existing needs-work flags.

Validation metrics: median absolute % error vs confirmed sale; bias; IQR
interval coverage; alert precision; per-class sample sizes (≥ 20 before any
claim).

## 5. Launch stages

Gates are cumulative and none may be weakened to pass (`preflight.py`).

| Stage | Criteria (all required) |
|---|---|
| **A — Private internal testing on real data** | `preflight --target internal` passes on the production host; ≥ 1 licensed source `healthy` with listings in the last 48 h; `verify_sources` exit 0 for the launch territory; schema failing items < 10%; CI + nightly maintenance green 7 days |
| **B — Controlled free beta (25–50 invited, one territory)** | A + `preflight --target free` passes (commercial mode, HTTPS, licensed fresh data); counsel has reviewed sourcing; ≥ 30 in-band ($2k–$15k) listings/week in territory (measured); median first-seen → notification queued ≤ 5 min (measured, `alert_enqueue_latency`); evaluation reference set started |
| **C — Controlled paid beta** | B for ≥ 2 weeks + `preflight --target paid` passes (Postgres, backups, live Stripe, no dead jobs); alert precision ≥ 0.6 with n ≥ 20 labeled alerts; valuation median abs error ≤ 15% with n ≥ 20 confirmed sales **or** product copy says values are asking-based estimates; ≥ 1 opportunity per active user per week; refund/support process written; owner sign-off |

Thresholds are proposals for the owner to accept or change *before* the
data arrives, so the bar is not set after seeing the result.

## 6. Readiness verdict

**GO for private internal testing. NO-GO for any beta** until a licensed
source returns real listings (`verify_sources` exit 0) and the Stage B
criteria are met. Nothing here has measured real-world performance.
