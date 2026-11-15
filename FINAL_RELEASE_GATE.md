# FINAL RELEASE GATE — FlipFirst

Decision record for the first real users, 2026-11-09, revision 2. It replaces the record made on `af76f9d`.
- **Evidence only.** Every test uses SYNTHETIC fixtures; nothing here is real-world performance.
- **Nothing pushed or deployed.** Nothing was merged or deployed, and this session has no GitHub write access. The release package's `transfer/push_and_open_prs.sh` lets the owner push the branches and open the draft PRs.

## Candidate

| | |
|---|---|
| Baseline `main` (unchanged since 2026-11-02; GitHub re-checked 2026-11-09) | `b2f72af83f6dae74a73639a5f9022002265a0d4f` |
| Verified code candidate | branch `release/rc-20261108`, commit `c559d96bf1d9f1a623ca29c5b2f8fee136d7e419`, tree `dfdbb17cb0d27d0582aa696bdcfa1471c9d73a10` |
| This document | added in a docs-only commit on top of that candidate |

**Merge order.** Use merge commits, not squash: a squash-merged #19 makes #21 conflict.

| # | Change | Head | Base |
|---|---|---|---|
| 1 | PR #19, rollback start-limit fix (open) | `f17caf7` | `main` |
| 2 | PR #21, release safety (draft; stacked on #19), after its review-fix PR merges into it (branch `fix/release-safety-review-20261109`) | `aa506e3` → `cb01818` | `fix/rollback-start-limit` → `main` |
| 3 | Backend integrity series, 13 commits (`fix/pro-initial-alert-delivery`) | `cc7a3a8` | `main` |
| 4 | Entitlement fix SEC-1/SEC-2 (`fix/entitlement-gates-20261109`) | `d2a8109` | `main` |
| 5 | Owner password tool, 3 commits (`fix/owner-password-reset-20261109`) | `eb17d41` | `main` |
| 6 | Demo-data disclosure (`fix/demo-data-disclosure-20261109`) | `69419b4` | `main` |
| 7 | Customer-journey test and this document (`fix/release-gate-20261108`) | this branch | #3 |

- **PR #21 targets #19's branch.** GitHub's test merge for #21 is built on `f17caf7`, the tip of `fix/rollback-start-limit`. After #19 merges, change #21's base to `main` before merging it; GitHub does this by itself only if the merged branch is deleted.
- **Merge order simulated** with `git merge-tree`, merge commits in this order: every step is clean, and the result is exactly the release candidate's tree.
- **Each of items 3–6 also merges cleanly alone**, and passes the full SQLite suite alone.
- **PR #22** (one-container cloud deploy, `a4879d4`) is not part of this candidate. It merges cleanly with it whichever goes first, and the accounts and cloud-deploy tests pass together (20 passed). Whether to deploy that way is an owner/Atlas decision.
- **PRs #2 `3ea6b9f`, #3 `33ac794` and #4 `1f7f7bc`** are fully contained in `main` (0 commits ahead): close them.

## Verdicts

| Stage | Verdict | Evidence-based reason |
|---|---|---|
| **CODE READY** | **PASS** | Full SQLite and PostgreSQL 16 suites pass on `c559d96`. The browser smoke, benchmark and migration checks pass on `3f02011`; `c559d96` differs from it only in `accounts.py` set-password input and its tests. All reproduced P0/P1 defects are fixed with regression tests. An adversarial check of the five newest commits found no P0 or P1. **Condition:** items 2–7 were authored by this session and need an independent Codex review plus GitHub CI before merge, and the owner approves each exact head SHA. |
| **CONTROLLED DEMO READY** | **PASS** | Demo on `c559d96` in commercial mode: seeded, healthy, registration 200, `demo_data` reported to visitors and signed-in users, 2 synthetic deals in the trusted feed (all from source `demo`, none with a link), 0 server errors. The browser smoke requires the banner (`3f02011`, and the demo branch alone). Every screen shows the banner "DEMO DATA — NOT LIVE MARKETPLACE LISTINGS"; demo cards carry no external link; `preflight` refuses any beta on a demo database. **Condition:** seed only with `tests/browser/seed_demo.py`, into a throwaway database. |
| **LIVE FREE BETA READY** | **BLOCKED** | See the blockers listed below this table. |
| **PAID BETA READY** | **BLOCKED** | See the blockers listed below this table. |

Live free beta is blocked by:
- **No licensed data source.** `preflight`: "no source that is licensed for commercial use has delivered listings in the last 48h". The MarketCheck agreement has not been requested; eBay production access and its price-estimate permission have not been applied for.
- **No production infrastructure demonstrated:** host, HTTPS origin, scheduler and backups. The host choice is Atlas's; it is not made here.
- **No native Linux/systemd rehearsal.** This container has no systemd; it needs a disposable Ubuntu 24.04 VM.
- **Changes unmerged and not on GitHub:** items 1–7. The owner runs the transfer script.
- **No legal review:** the 14 `[LEGAL REVIEW]` markers are open and the seller terms are unpublished.
- **Stage B criteria** (`CURRENT_STATE.md` §5) are unmeasured.

Paid beta is blocked by everything above, plus:
- live Stripe products and keys (no scripted paid go-live: `golive.sh` refuses Stripe keys);
- a decision on prices (docs disagree: `plans.py` vs `OWNER_ACTIONS.md` vs `STRIPE_TESTING.md`);
- the Stage C criteria: 2 weeks of free beta, alert precision ≥ 0.6 with n ≥ 20, a support and refund process;
- if SMS is sold: F4 and F5 below fixed before Twilio is enabled.

## Evidence on the code candidate

| Check | Result | Commit |
|---|---|---|
| Full suite, SQLite (Python 3.13.18) | 917 passed, 2 skipped (PostgreSQL-only), 10 subtests; exit 0 | `c559d96` |
| Full suite, PostgreSQL 16.15 | 919 passed, 10 subtests; exit 0 | `c559d96` |
| Migration consistency (0022 is the only new migration; additive) | `migrations ok` | `c559d96` |
| Browser smoke (CI mirror: mobile flow + UI flows, Chromium) | SMOKE OK; UI FLOWS OK; demo banner required | `3f02011` |
| Valuation benchmark (39 synthetic cases) + drift | TP 11 FP 1 TN 27 FN 0 (unchanged); no drift | `3f02011` |
| Each PR branch alone, full SQLite suite | entitlement `d2a8109` 705 passed, 2 skipped; password `eb17d41` 701 passed, 2 skipped; demo `69419b4` 699 passed, 2 skipped, browser smoke OK; backend `cc7a3a8` 843 passed, 2 skipped (PostgreSQL 845 passed) | per branch |
| Owner merge order (items 1–7, merge commits) | every step clean; final tree equals the candidate's | `git merge-tree` |
| Transfer script | Dry run on a fresh GitHub clone: every check passes. Simulated `--execute` against a local remote with a stub `gh`: creates 7 branches in one atomic push and opens 6 draft PRs. A second run changes nothing, and an interrupted run resumes without duplicates. It refuses an existing branch, a rewritten base, a conflict or a modified package, and nothing is pushed. | release package |
| End-to-end customer journey test | passes on SQLite and PostgreSQL 16 | in the full suites above |
| Adversarial check of `cb01818`, `d2a8109`, `8e51924`, `c5b4124`, `357fb01` (agent; not an independent review) | No P0/P1. Rollback takeover never switches to a wrong release, and a refusal leaves the directory byte-identical. 5 P2s: F1 and F2 fixed in `eb17d41`; F3–F5 open (below). | `af76f9d` |
| Dependency audit (`pip-audit` 2.10.1) | no known vulnerabilities in the declared or installed versions | tree `f9bb8fe` (dependencies unchanged since) |
| Security verification (independent agent, 62 reproduction tests) | 2 issues reproduced and fixed (below); no auth bypass, cross-account access, webhook forgery or committed secret found | `ea77b18` |
| Release-safety review of PR #19/#21 (independent agent; fake ssh/systemctl harness) | 3 P1 defects reproduced and fixed (below); shellcheck: nothing new | `aa506e3` / `ea77b18` |
| Fixed defects, before → after | SEC-1/SEC-2: 8 failed → 10 passed. D1–D3: 4 failed → 74 passed (PR #21's whole test set). F1/F2: 3 failed → 11 passed (`tests/test_accounts.py`, SQLite and PostgreSQL). | `b2f72af` → `d2a8109`; `aa506e3` → `cb01818`; `6681c17` → `eb17d41` |
| `preflight` (sandbox, no secrets) | NOT READY: scheduler heartbeat, `BILLING_ORIGIN`, commercial mode, licensed data, Postgres, backups, Stripe | `d8ed9f5` |
| 30-minute Pro alert suppression detection query | finds exactly the suppressed alert on both engines | `d8ed9f5` |
| Native Linux/systemd release rehearsal | **BLOCKED**: PID 1 is not systemd here | — |
| GitHub CI on the exact candidate | **BLOCKED** until the owner pushes: the transfer script pushes the candidate as `fix/rc-integration-20261109` (CI only; no PR) | — |

## Defects fixed in this release pass

- **SEC-1 (P1): aggressive mode on Lite through a variant spelling.** A Lite subscriber could save `"aggressive "` and get risk-flagged deals in the preview and in alerts. Now the matcher fails closed, and saving accepts only the two valid values. `d2a8109`
- **SEC-2 (P0 once Twilio is configured): the Lite test button sent real, unmetered SMS to any number.** The test send now follows real-alert rules: plan channels, monthly SMS cap, metering. `d2a8109`
- **D1 (P1): deploy scripts lost their executable bit.** The health-check unit failed every 5 minutes and `golive.sh` gate 7 failed. Bits restored, plus a mode test. `cb01818`
- **D2 (P1): no tool path back after a failed switch.** Rollback now takes over exactly a journal that records a completed switch; every other journal is still refused. `cb01818`
- **D3 (P1): existing hosts refused with no fix given.** The refusal now gives the ownership commands, and the upgrade doc makes them a precondition. `cb01818`
- **Demo trust gap: synthetic cars shown as Craigslist listings with fake links.** Demo rows now have their own source and no link, a banner shows on every screen, and `preflight` refuses a demo database. `69419b4`
- **Functional gap: no password recovery at all.** New owner tool `python -m accounts set-password` (random password, or one the person chose; ends all sessions). `8e51924`, `6681c17`
  - F1 and F2 fixed in `eb17d41`: a typed password is now hidden and asked twice, and a CRLF ending is dropped.
- **Coverage gap: no single test of the whole customer journey.** Added `tests/test_customer_journey.py`. `c5b4124`

## Open defects and risks (none block the demo; see stage verdicts)

**P2, code:**
- **F4 (pre-existing; fix before Twilio is enabled).** The real-alert SMS cap counts only delivered SMS, so matches queued before delivery can exceed the monthly cap. Reproduced: cap 1 → 3 SMS.
- **F5 (pre-existing; fix before Twilio is enabled).** An SMS queued while a user was Pro is still sent after a downgrade. The window is up to 30 minutes with the Pro re-score wait.
- **F3.** At the monthly SMS cap, the test button tells a Pro user with only a phone that the plan includes no channel.
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
- self-serve password reset;
- eBay compliance items C2 (deleting eBay data) and C4 (account-deletion notifications), needed only if eBay is used.

**Unverifiable without a real systemd host:**
- a worker or scheduler crash loop after a switch is not caught by the deploy health check (web only);
- rolling back to a pre-#19 release reinstalls the old units;
- canary diagnostics;
- disk growth (no pruning; one venv per release).

## Demo (fixtures only; the banner says the data is synthetic)
```bash
SNIPER_DB_PATH=/tmp/demo.db SNIPER_COMMERCIAL_MODE=1 ALLOW_REGISTRATION=1 python tests/browser/seed_demo.py
```
```bash
SNIPER_DB_PATH=/tmp/demo.db SNIPER_COMMERCIAL_MODE=1 ALLOW_REGISTRATION=1 python server.py
```
Open http://127.0.0.1:8765/m and register. Never point a demo at a database meant for real users; `preflight` refuses one that has demo rows.

## Owner decisions to go live (dependency order; costs as written in `OWNER_ACTIONS.md`)

1. **Put the branches on GitHub:** run `transfer/push_and_open_prs.sh` from the release package in your clone, first as a dry run, then with `--execute` ($0; CI runs on GitHub Actions).
2. **Merge, in the order above, each after independent review and green CI:** #19 → #21 with `cb01818` → items 3–6 → item 7. Close #2–#4. Enable branch protection on `main` ($0).
3. **Send the MarketCheck inquiry** ($0 to ask; public Basic $299/mo + $0.01 per FSBO call). Optionally apply for eBay production access with price-estimate permission ($0 API fee; approval not guaranteed).
4. **Production host, domain and backups** (Atlas's decision; `FIRST_DEPLOY.md` estimates ~$19–21/mo, `OWNER_ACTIONS.md` ~$40–70/mo with managed Postgres). Run the native Linux release rehearsal on a disposable VM, then deploy the merged `main` with `golive.sh`.
5. **Legal review** of the sources, terms and the 14 `[LEGAL REVIEW]` markers (cost varies). Choose the territory; accept the Stage B/C thresholds.
6. **If a data agreement is signed:** set the agreement variable only after signing, approve the data budget (~$313/mo for one territory), and pass `preflight --target free`. Then authorize the free beta.
7. **Paid beta:** after 2 weeks meeting Stage C, fix F4/F5 if SMS is sold, decide prices, create live Stripe products (Stripe fees only), pass `preflight --target paid`, and sign off.

## Definition of done for the first real users (free beta)

All of the following, measured on the production host:
1. Merged `main` deployed by `golive.sh`; the native Linux rehearsal and a restore drill pass.
2. `python -m preflight --target free` passes: commercial mode, HTTPS, a licensed source delivering listings within 48 h, no demo data.
3. Counsel has reviewed the sourcing and terms; the seller terms are published.
4. CI (SQLite, PostgreSQL, benchmark, browser smoke) is green on the deployed SHA, and branch protection is on.
5. Stage B measured: ≥ 30 in-band listings a week in the territory, and median first-seen → alert queued ≤ 5 minutes.
6. The owner can recover a locked-out user (`accounts set-password`) and delete an account on request (`accounts delete`).
