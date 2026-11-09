# FINAL RELEASE GATE — FlipFirst

Decision record for the first real users, 2026-11-09.
- **Evidence only.** Every test uses SYNTHETIC fixtures; nothing here is real-world performance.
- **Nothing pushed or deployed.** Nothing was merged or deployed, and this session was not granted push access.

## Candidate

| | |
|---|---|
| Baseline `main` (unchanged since 2026-11-02) | `b2f72af83f6dae74a73639a5f9022002265a0d4f` |
| Verified code candidate | branch `release/rc-20261108`, commit `af76f9d3c12faa884d9a19482728dfbccedd0cec`, tree `2bb12e011ddb127d0d03397ed44a911c436e609a` |
| This document | added in a docs-only commit on top of that candidate |

**Merge order.** Use merge commits, not squash: a squash-merged #19 makes #21 conflict.

| # | Change | Head | Base |
|---|---|---|---|
| 1 | PR #19, rollback start-limit fix (open) | `f17caf7` | `main` |
| 2 | PR #21, release safety (draft; contains #19) plus review fixes `cb01818` (branch `fix/release-safety-review-20261109`) | `aa506e3` → `cb01818` | PR #21 |
| 3 | Backend integrity series, 13 commits (branch `fix/pro-initial-alert-delivery`) | `cc7a3a8` | `main` |
| 4 | Entitlement fix SEC-1/SEC-2 (branch `fix/entitlement-gates-20261109`) | `d2a8109` | `main` |
| 5 | Owner password tool (branch `fix/owner-password-reset-20261109`) | `8e51924` | `main` |
| 6 | Customer-journey test and this document (branch `fix/release-gate-20261108`) | `c5b4124` + docs | #3 |

- Items 3–6 each merge cleanly into `main`, PR #21 and one another (`git merge-tree`).
- PRs #2 `3ea6b9f`, #3 `33ac794` and #4 `1f7f7bc` are fully contained in `main` (0 commits ahead): close them.

## Verdicts

| Stage | Verdict | Evidence-based reason |
|---|---|---|
| **CODE READY** | **PASS** | Full SQLite and PostgreSQL 16 suites, browser smoke, benchmark and migration checks all pass on `af76f9d`. The two reproduced security defects (SEC-1, SEC-2) and the three reproduced release-script defects (D1–D3) are fixed with regression tests. No known P0 or P1 code defect remains. **Condition:** items 2–6 were authored by this session and need an independent Codex/Atlas review plus GitHub CI before merge. |
| **CONTROLLED DEMO READY** | **PASS** | The demo runs locally on fixtures in commercial mode: seeded, healthy, registration works, 2 synthetic deals shown. The full journey passes the browser smoke (9/9) and the end-to-end journey test. **Condition:** listings are synthetic and labeled "craigslist:demo:*", and there is no on-screen sample-data banner, so the presenter must say so. |
| **LIVE FREE BETA READY** | **BLOCKED** | See the blockers listed below this table. |
| **PAID BETA READY** | **BLOCKED** | See the blockers listed below this table. |

Live free beta is blocked by:
- **No licensed data source.** `preflight`: "no source that is licensed for commercial use has delivered listings in the last 48h". The MarketCheck agreement has not been requested; eBay production access and its price-estimate permission have not been applied for.
- **No production infrastructure demonstrated:** host, HTTPS origin, scheduler and backups.
- **No native Linux/systemd rehearsal.** This container has no systemd; it needs a disposable Ubuntu 24.04 VM.
- **Changes unmerged:** items 1–6.
- **No legal review:** the 14 `[LEGAL REVIEW]` markers are open and the seller terms are unpublished.
- **Stage B criteria** (`CURRENT_STATE.md` §5) are unmeasured.

Paid beta is blocked by everything above, plus:
- live Stripe products and keys (no scripted paid go-live: `golive.sh` refuses Stripe keys);
- a decision on prices (docs disagree: `plans.py` vs `OWNER_ACTIONS.md` vs `STRIPE_TESTING.md`);
- the Stage C criteria: 2 weeks of free beta, alert precision ≥ 0.6 with n ≥ 20, a support and refund process.

## Evidence on the code candidate

| Check | Result | Commit |
|---|---|---|
| Full suite, SQLite (Python 3.13.18) | 910 passed, 2 skipped (PostgreSQL-only), 10 subtests; exit 0 | `af76f9d` |
| Full suite, PostgreSQL 16.15 | 912 passed, 10 subtests; exit 0 | `af76f9d` |
| Browser smoke (mobile flow + 9 UI flows, Chromium) | SMOKE OK; UI FLOWS OK 9/9 | `af76f9d` |
| Valuation benchmark (39 synthetic cases) + drift | TP 11 FP 1 TN 27 FN 0 (unchanged); no drift | `af76f9d` |
| Migration consistency (0022 is the only new migration; additive) | `migrations ok` | `af76f9d` |
| End-to-end customer journey test | passes on SQLite and PostgreSQL 16 | `d8ed9f5` and in the full suites above |
| Dependency audit (`pip-audit` 2.10.1) | no known vulnerabilities in the declared or installed versions | tree `f9bb8fe` (dependencies unchanged since) |
| Security verification (independent agent, 62 reproduction tests) | 2 issues reproduced and fixed (below); no auth bypass, cross-account access, webhook forgery or committed secret found | `ea77b18` |
| Release-safety review of PR #19/#21 (independent agent; fake ssh/systemctl harness) | 3 P1 defects reproduced and fixed (below); shellcheck: nothing new | `aa506e3` / `ea77b18` |
| Fixed defects, before → after | SEC-1/SEC-2: 8 failed → 10 passed. D1–D3: 4 failed → 74 passed (PR #21's whole test set) | `b2f72af` → `d2a8109`; `aa506e3` → `cb01818` |
| Demo in commercial mode | seeded, healthy, registration 200, trusted feed 2 synthetic deals, 0 server errors | `af76f9d` |
| `preflight` (sandbox, no secrets) | NOT READY: scheduler heartbeat, `BILLING_ORIGIN`, commercial mode, licensed data, Postgres, backups, Stripe | `d8ed9f5` |
| 30-minute Pro alert suppression detection query | finds exactly the suppressed alert on both engines | `d8ed9f5` |
| Native Linux/systemd release rehearsal | **BLOCKED**: PID 1 is not systemd here | — |
| GitHub CI on the exact candidate | **BLOCKED**: push access was not granted to this session | — |

## Defects fixed in this release pass

- **SEC-1 (P1): aggressive mode on Lite through a variant spelling.** A Lite subscriber could save `"aggressive "` and get risk-flagged deals in the preview and in alerts. Now the matcher fails closed, and saving accepts only the two valid values. `d2a8109`
- **SEC-2 (P0 once Twilio is configured): the Lite test button sent real, unmetered SMS to any number.** The test send now follows real-alert rules: plan channels, monthly SMS cap, metering. `d2a8109`
- **D1 (P1): deploy scripts lost their executable bit.** The health-check unit failed every 5 minutes and `golive.sh` gate 7 failed. Bits restored, plus a mode test. `cb01818`
- **D2 (P1): no tool path back after a failed switch.** Rollback now takes over exactly a journal that records a completed switch; every other journal is still refused. `cb01818`
- **D3 (P1): existing hosts refused with no fix given.** The refusal now gives the ownership commands, and the upgrade doc makes them a precondition. `cb01818`
- **Functional gap: no password recovery at all.** New owner tool `python -m accounts set-password` (random password, or one read from stdin; ends all sessions). `8e51924`
- **Coverage gap: no single test of the whole customer journey.** Added `tests/test_customer_journey.py`. `c5b4124`

## Open defects and risks (none block the demo; see stage verdicts)

**P2, code:**
- `release_transition` treats masked units as fatal after the switch (D4).
- Release venvs are built as root from unpinned `requirements.txt` (`>=`, 9 of 9 lines) (D5).
- No per-IP registration throttle, and a duplicate email says "already registered" (SEC-3; only matters with open registration).
- The seller verification email can be sent to any address, 5 per account (SEC-4; gate the route on `SNIPER_SELLER_SUBMISSIONS`, add per-recipient caps).
- Admin console has unescaped attribute sinks (SEC-5; not shown to execute).
- Stripe events' `livemode` is not checked against the key mode.
- API 500 responses echo the exception text.
- Display does not filter by licence. Defence in depth only: `golive.sh` enforces commercial mode, so a fresh production database never ingests unlicensed rows.
- A Pro first alert waiting on a re-score is skipped after a worker outage of 30+ minutes. It is fail-closed and detectable:
  `SELECT id, result FROM jobs WHERE kind='notify' AND result LIKE '%still unfinished after 30 min%'`

**P2, docs:**
- Stale rollback and venv commands in runbooks (D6).
- Contradictory prices, pilot radius (150/100/75 mi), hosting cost and branch-protection status across the docs.

**Not built (needed before open registration):**
- self-serve password reset and account deletion;
- eBay compliance items C2 (deleting eBay data) and C4 (account-deletion notifications), needed only if eBay is used.

**Unverifiable without a real systemd host:**
- a worker or scheduler crash loop after a switch is not caught by the deploy health check (web only);
- rolling back to a pre-#19 release reinstalls the old units;
- canary diagnostics;
- disk growth (no pruning; one venv per release).

## Demo (fixtures only; say that the data is synthetic)
```bash
SNIPER_DB_PATH=/tmp/demo.db SNIPER_COMMERCIAL_MODE=1 ALLOW_REGISTRATION=1 python tests/browser/seed_demo.py
```
```bash
SNIPER_DB_PATH=/tmp/demo.db SNIPER_COMMERCIAL_MODE=1 ALLOW_REGISTRATION=1 python server.py
```
Open http://127.0.0.1:8765/m and register.

## Owner decisions to go live (dependency order; costs as written in `OWNER_ACTIONS.md`)

1. **Merge, in this order, each after independent review and green CI:** #19 → #21 with `cb01818` → item 3 → items 4 and 5 → item 6. Close #2–#4. Enable branch protection on `main` ($0).
2. **Send the MarketCheck inquiry** ($0 to ask; public Basic $299/mo + $0.01 per FSBO call). Optionally apply for eBay production access with price-estimate permission ($0 API fee; approval not guaranteed).
3. **Production host, domain and backups** (`FIRST_DEPLOY.md` estimates ~$19–21/mo; `OWNER_ACTIONS.md` estimates ~$40–70/mo with managed Postgres). Run the native Linux release rehearsal on a disposable VM, then deploy the merged `main` with `golive.sh`.
4. **Legal review** of the sources, terms and the 14 `[LEGAL REVIEW]` markers (cost varies). Choose the territory; accept the Stage B/C thresholds.
5. **If a data agreement is signed:** set the agreement variable only after signing, approve the data budget (~$313/mo for one territory), and pass `preflight --target free`. Then authorize the free beta.
6. **Paid beta:** after 2 weeks meeting Stage C, decide prices, create live Stripe products (Stripe fees only), pass `preflight --target paid`, and sign off.

## Definition of done for the first real users (free beta)

All of the following, measured on the production host:
1. Merged `main` deployed by `golive.sh`; the native Linux rehearsal and a restore drill pass.
2. `python -m preflight --target free` passes: commercial mode, HTTPS, a licensed source delivering listings within 48 h.
3. Counsel has reviewed the sourcing and terms; the seller terms are published.
4. CI (SQLite, PostgreSQL, benchmark, browser smoke) is green on the deployed SHA, and branch protection is on.
5. Stage B measured: ≥ 30 in-band listings a week in the territory, and median first-seen → alert queued ≤ 5 minutes.
6. The owner can recover a locked-out user (`accounts set-password`) and delete an account on request (`accounts delete`).
