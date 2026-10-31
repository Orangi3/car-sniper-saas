# FlipFirst project status and owner-action package

Checkpoint: 2026-10-31. `main` = `b0d6ee4` (unchanged). Open work is a stack
of four pull requests, merged strictly in this order (final heads under
"Release" below):
1. PR #11 — real-data readiness (`ae5308d`)
2. PR #12 — first live data, compliance, launch prep (`5c6b2f2`)
3. PR #13 — FlipFirst Analyze and the private deal workspace
   (`feat/flipfirst-analyze`)
4. PR #14 — dealer inventory, CSV import, buyer reports, cost model
   (`feat/flipfirst-seller-inventory`)

Evidence labels: **VERIFIED** (run here), **SIMULATED** (fixtures /
stand-ins / disposable host), **ESTIMATED**, **NOT TESTED**, **BLOCKED**.

## Release: merge procedure, migrations, rollback

**Merge procedure.** Each step happens only after you approve that PR's
exact head SHA. Never squash or force-push, and never bypass branch
protection: protection requires PRs, the three CI checks and admin
enforcement, with 0 approving reviews required.
1. Re-check the PR: its head equals the approved SHA, the three required
   checks are green on that SHA, and `main` is unchanged.
2. Merge with a merge commit pinned to the SHA:
   `gh api -X PUT repos/Orangi3/car-sniper-saas/pulls/N/merge -f merge_method=merge -f sha=SHA`.
3. CI runs on `main` by itself; wait for it to go green.
4. Retarget the next PR: `gh api -X PATCH repos/Orangi3/car-sniper-saas/pulls/N+1 -f base=main`.
5. Check that its diff is only its own commits (commit and file counts match
   what it showed before).
6. Check that `main`'s tree equals the merged PR's head:
   `git diff --quiet <merged head> origin/main`.
7. If the trees are equal, the next PR's merge result is exactly its own
   head's tree. The push-triggered CI run on that head already tested that
   tree, so no new run and no close/reopen are needed.
8. If `main` moved for any other reason, bring the branch up to date with
   GitHub's update-branch endpoint
   (`gh api -X PUT repos/Orangi3/car-sniper-saas/pulls/N+1/update-branch -f expected_head_sha=SHA`).
   That creates a new head, starts CI normally, and the new head is what you
   approve.
9. To repeat a flaky check on the same head, re-run that SHA's push workflow
   run (`gh api -X POST repos/Orangi3/car-sniper-saas/actions/runs/RUN_ID/rerun`).

**Migrations.**
- #11 and #12 add none.
- #13 adds `0019_opportunities` (new table) and
  `0020_listings_lower_ymm_index` (new index on `listings`).
- #14 adds `0021_seller_profiles_and_reports` (two new tables).
- All three are `CREATE … IF NOT EXISTS`, paired for SQLite and PostgreSQL
  (`scripts/check_migrations.py` ok), and none alters or deletes existing
  data.
- The 0020 index build briefly blocks writes to `listings` on PostgreSQL.
  At pilot size that takes well under a second; at hundreds of thousands of
  rows, run it during a quiet period.

**Rollback** (VERIFIED on SQLite).
- `deploy/sync.sh --rollback` returns to the previous release's code.
  Startup only checks that the migrations *that code* knows about are
  applied, so newer tables don't stop it. PR #12's head (`5c6b2f2`) started
  against a database migrated through 0021: `/health` 200, `/login` 200.
- Older code ignores the new tables. Dealer listings stay marked as dealer
  listings, without the business name.
- Dropping the new tables is not needed for a rollback, and it would delete
  users' saved deals, dealer approvals and reports. Do it only on your
  explicit decision, after a backup.

## Security review of PRs #13–#14 (independent reviewer, 2026-10-31)

8 confirmed defects, all fixed, each with a regression test. 0 open.

| # | Finding | PR | Fix |
|---|---|---|---|
| 1 | A cosmetic-only cost dropped the $500 repair allowance and counted as a repair estimate (a car needing work could rate "strong") | #13 | costs layered on the effective assumptions; only mechanical counts |
| 2 | `remove_missing` withdrew a car whose row was rejected | #14 | every VIN the file mentions counts as present |
| 3 | Dealer imports used up the private sellers' daily cap | #14 | dealers are outside that count |
| 4 | Withdrawals ran after creates, so a dealer at its limit couldn't refresh | #14 | withdrawals first; dealer daily cap = 2 × live limit |
| 5 | A revoked dealer kept the "approved by FlipFirst" label | #14 | the label comes from the current approval |
| 6 | A refused `approve-dealer` deleted the existing approval | #14 | validate before any change |
| 7 | Saving re-ran and re-counted the analysis, so the last analysis of the day couldn't be saved | #13 | `usage_events.ref`; same inputs within 30 min are free |
| 8 | `"1e999"` / `"inf"` and a non-object `attest` returned 500 | #13, #14 | 400 |

Unverified concerns, fixed anyway because cheap:
- the ZIP, VIN and title are carried into "Analyze with my numbers";
- stale eBay comparables are hidden in saved deals (6-hour rule);
- the mileage slope is capped at 200 points;
- 2 MB request limit;
- NUL characters stripped;
- the privacy draft names the pilot's counts.

Accepted: limits are checked without a lock, so concurrent requests can
exceed them by one or two (bounded by rate limits).

Clean per the reviewer: owner scoping (404 for other users), no HTTP route
to approve dealers or resolve reports, whitelisted SQL column names,
escaped rendering, account deletion covering the new tables.

## Beta readiness: what works with no paid data (VERIFIED in tests, SIMULATED cars)

| # | Capability | Without paid data | Evidence |
|---|---|---|---|
| 1 | Enter a vehicle by hand or by VIN | yes (VIN decode: NHTSA, free) | `test_analyze`, browser journey |
| 2 | Evaluate costs and risks | yes: costs, break-even, maximum offer, risks, missing information. With fewer than three comparables a market value is **"no defensible value"**; profit then uses the user's own resale estimate, labeled as theirs | `test_analyze`, `test_beta_journey` |
| 3 | Save an opportunity | yes, private to the user | same |
| 4 | Compare deals | yes, 2–3 side by side | browser journey |
| 5 | Record an actual purchase and sale | yes, labeled "recorded by you, not verified" | same |
| 6 | Submit a seller-owned listing | yes; needs working email for contact confirmation and `SNIPER_SELLER_SUBMISSIONS=1`; findable under **Discover → FlipFirst sellers** | `test_seller_listings`, `test_beta_journey`, UI flows |
| 7 | Import authorized dealer inventory | yes, once you approve the dealer | `test_seller_inventory`, local browser check |
| 8 | Report suspicious inventory | yes; you resolve reports from the command line | same |

Not available without licensed data:
- market values for most cars at launch;
- Discover's external listings;
- alerts on external listings.

**Shortest path to a real test (one flipper, one seller):**
1. You approve the four merges, in order.
2. You approve the infrastructure request (below). The server is set up
   with `FIRST_DEPLOY.md`.
3. On the server: Resend `SMTP_*`, `SNIPER_SELLER_SUBMISSIONS=1`,
   `ALLOW_REGISTRATION=0`, `BILLING_ORIGIN=https://app.tryflipfirst.com`.
4. Issue two invitations: `python -m invites issue --note "flipper: NAME"`
   and `--note "seller: NAME"`.
5. The seller signs up and confirms their email. A dealer is approved only
   after you check the business (`approve-dealer`). They list one or more
   cars.
6. The flipper:
   - analyzes a car they're really considering, using their own resale
     estimate, and saves it;
   - opens the seller's car from **Discover → FlipFirst sellers**, then
     uses **Analyze with my numbers**;
   - emails the seller and records what happens.
7. After a week: `python -m pilot_report` (counts only) and a short call
   with each person.

Your decision: have a lawyer review the privacy, tester and seller terms
before inviting anyone outside your own circle.

## Critical path

```
owner approves #11 ──► merge #11 ──► retarget #12 to main ──► CI ──► owner approves #12 ──► merge
      └─► then the same for #13 (Analyze, workspace) and #14 (seller and dealer inventory), in that order
MarketCheck written agreement ─┐
   or eBay written permission ─┴─► first live test (FIRST_LIVE_TEST.md) ──► Gate 0 ──► Gate 1 ──► 10 testers
VPS + domain + email + backups (owner) ──► deploy (FIRST_DEPLOY.md) ───────┘            └──► Gate 2 alerts
legal review of legal/ drafts ──► publish links ──► invitations
```

## Checklist

| # | Task | Priority | Status | Depends on | Evidence | Next action |
|---|---|---|---|---|---|---|
| 1 | Merge PR #11 | CRITICAL | ready | your approval | CI 6/6 green at `ae5308d`; mergeable; protection on | you approve; merge with the SHA pinned |
| 2 | Merge PR #12 | CRITICAL | open | #1 | full SQLite + PostgreSQL suites, browser, simulation (below) | after #1: retarget to `main`, re-run CI, you approve its final head |
| 2a | Merge PR #13 (Analyze + private workspace) | HIGH | open | #2 | `tests/test_analyze.py`; full suites; browser journey | after #2, same procedure |
| 2b | Merge PR #14 (dealer inventory, CSV import, reports) | HIGH | open | #2a | `tests/test_seller_inventory.py`; full suites; UI flows | after #2a, same procedure |
| 2c | Recruit the first sellers | HIGH | drafted | legal review of seller terms; server live | `outreach/SELLER_RECRUITING.md` (not sent) | approve the messages; visit 10 lots |
| 3 | Licensed listing source | CRITICAL BLOCKER | BLOCKED | provider | `outreach/MARKETCHECK_REQUEST.md`, `EBAY_SETUP.md` ready | you send both requests |
| 4 | Licensed market value | CRITICAL BLOCKER | BLOCKED | #3 (MarketCheck comparables / Price API, or eBay permission) | `DATA_PROVIDER_DECISION.md` | in the same requests |
| 5 | First live test | CRITICAL | ready to run | #3 | harness VERIFIED against a stand-in only | `FIRST_LIVE_TEST.md` |
| 6 | Production server | HIGH | prepared | your approval + purchase | install, deploy, upgrade, rollback, HTTPS, isolation SIMULATED on a disposable host (`DEPLOY_TEST_REPORT.md`) | approval request below |
| 7 | Email, backups, monitoring | HIGH | prepared | #6, accounts | `FIRST_DEPLOY.md` steps 10, 11, 13 | create accounts |
| 8 | Legal drafts | HIGH | drafted | lawyer | `legal/` (not published) | review, fill `[…]`, publish links |
| 9 | eBay data deletion rule (C2) | HIGH if eBay | not built | eBay's written terms | `COMPLIANCE.md` | build after eBay answers |
| 10 | eBay account-deletion notifications (C4) | HIGH if eBay | not built | eBay's answer on exemption | `COMPLIANCE.md` | build or claim exemption |
| 11 | MarketCheck Price API connection | MEDIUM | not built | MarketCheck agreement | — | small adapter once permitted |
| 12 | Pilot onboarding | HIGH | prepared | Gate 1 | `PILOT_TUSCALOOSA.md` operations; `pilot_report`; invites VERIFIED on the disposable host | invite after Gate 1 |
| 13 | Gate 2 alerts for testers | MEDIUM | prepared | Gate 2 | hand-set Lite survives reconciliation (test) | `COMPLIANCE.md` C11 command |
| 14 | Close stale PRs #2, #3, #4 | OPTIONAL | — | — | each 0 commits ahead of `main` | close on GitHub |
| 15 | In-app abuse report, self-serve deletion | OPTIONAL | not built | after pilot | owner handles by email (`SELLER_PILOT.md`) | post-pilot |

## Verified this round (PR #12 branch before push)

| Check | Result |
|---|---|
| SQLite suite | VERIFIED — 552 passed, 2 skipped |
| PostgreSQL suite | VERIFIED — 550 passed (before the review-fix commit; targeted PostgreSQL tests after it: 28 passed; CI runs the full suite on the head) |
| Browser smoke + UI flows (Playwright 1.47.2) | VERIFIED — pass; new "Check the mileage" label rendered on a SIMULATED listing |
| Production simulation (real PostgreSQL and processes, stand-in provider) | SIMULATED — 19/19 |
| Benchmark | SIMULATED — 39 cases: 11 TP, 27 TN, 1 FP (known trim mislabel), 0 FN; no drift |
| Migrations, `pip-audit` | VERIFIED — ok; no known vulnerabilities |
| Disposable-host deployment | SIMULATED — see `DEPLOY_TEST_REPORT.md` |
| Real listings, real deals, real users | NOT TESTED — BLOCKED on #3 |

## Verified — final release candidate (2026-10-31)

Final heads:
- PR #11: `ae5308d22dbd0721d64b5cb879a7d857ea791804`
- PR #12: `5c6b2f28b8c971379406cd10c1b9e28f9330618c`
- PR #13: `9b169635af20bbdf4debe4626df705bb9f8c3470`
- PR #14: the commit that adds this checkpoint (its SHA is shown on the PR)

| Check | Result |
|---|---|
| Full SQLite suite, #14 code (contains #13) | VERIFIED — 611 passed, 2 skipped (the two need PostgreSQL), 0 failed |
| Full PostgreSQL 16 suite, #14 code | VERIFIED — 613 passed, 0 skipped, 0 failed |
| Full SQLite suite, #13 code alone | VERIFIED — 592 passed, 2 skipped |
| GitHub CI on #13 `9b16963` and on #14 `ffdd7e5` | VERIFIED — 6/6 each (tests-sqlite, tests-postgres, browser-smoke; push and pull_request runs). The checkpoint commit on #14 changes only this file |
| Browser, Playwright 1.47.2 (CI's version) | VERIFIED — `smoke_mobile.js`: Analyze journey, workspace, compare, sellers tab, no sideways scroll at 320 px. `ui_flows.js`: invites, seller wizard, account, ops, no overflow at 375–1440 px. Local dealer CSV import check |
| Migrations | VERIFIED — `check_migrations.py` ok. Rollback to the previous release against a 0021 schema: starts, `/health` 200 |
| Valuation benchmark | VERIFIED — no drift (39 SIMULATED cases: 11 TP, 1 FP, 27 TN, 0 FN) |
| Analyze speed on 50,000 SIMULATED listings | MEASURED — 41 ms per analysis on SQLite, 59 ms on PostgreSQL |
| Real vehicles, real sellers, real users | NOT TESTED — no licensed data, sellers or users yet |

## Task register — Analyze, workspace, original inventory (branch `feat/flipfirst-analyze`)

Scored before building: priority = value × business importance ÷ (effort +
risk), each 1–5; security and legal items are overridden by judgment.
Operating cost is per month at pilot scale.

| # | Task | Value | Biz | Effort | Risk | Op cost | Depends on | Verified by | Score | Decision |
|---|---|---|---|---|---|---|---|---|---|---|
| T1 | Analyze a car the user found: identity (VIN decode vs entered), market value from licensed comparables or "no defensible value", profit scenarios, risks, missing info, decision | 5 | 5 | 3 | 2 | $0 (NHTSA free, local compute) | — | unit + API tests, SQLite + PostgreSQL | 5.0 | **built** (PR #13) |
| T2 | Break-even resale, maximum offer, margin of safety — useful with no market data | 5 | 4 | 1 | 1 | $0 | T1 | unit tests | 10 | **built** (PR #13) |
| T3 | Private deal workspace: save, inspection notes, stages, actual costs and sale, history, compare | 5 | 4 | 3 | 2 | $0 | T1, migration 0019 | API + access-control tests | 4.0 | **built** (PR #13) |
| T4 | Analyze and workspace screens in `/m` | 5 | 5 | 3 | 2 | $0 | T1, T3 | browser flow, phone and desktop widths | 5.0 | **built** (PR #13) |
| T5 | Seller form: VIN decode prefill; one live listing per VIN | 3 | 4 | 2 | 1 | $0 | T1's VIN endpoint | tests | 4.0 | **built** (PR #14; one live listing per VIN already existed) |
| T6 | Dealer / wholesaler inventory: owner-granted seller limits, CSV import with validation | 3 | 5 | 3 | 3 | $0 | seller system | upload validation tests | 2.5 | **built** (PR #14) |
| T7 | In-app "report this listing" | 3 | 3 | 2 | 1 | $0 | — | tests | 3.0 | **built** (PR #14) |
| T8 | Seller photo uploads | 4 | 4 | 4 | 4 | storage + moderation | — | — | 2.0 | defer: malicious-upload and privacy (EXIF location) work outweighs pilot value; buyers ask sellers for photos |
| T9 | Seller recruiting playbook and outreach drafts (not sent) | 4 | 5 | 1 | 1 | $0 | — | review | 10 | **written** (`outreach/SELLER_RECRUITING.md`, not sent) |
| T10 | Provider rights and cost-per-valuation comparison | 3 | 5 | 1 | 1 | $0 | research | official sources cited | 7.5 | **written** (`DATA_PROVIDER_DECISION.md`) |
| T11 | Cost model to 10,000 users; cost per analysis | 2 | 4 | 1 | 1 | $0 | — | tests | 4.0 | **built** (`economics/scale.md`) |
| T12 | AI-written explanations | 2 | 2 | 3 | 3 | paid per call | — | — | 0.7 | no: deterministic explanations already cover it |
| T13 | Phone verification by text | 2 | 2 | 3 | 2 | per message + number | SMS provider | — | 0.8 | defer |
| T14 | Aggregate analytics from users' recorded outcomes | 3 | 4 | 3 | 4 | $0 | consent, volume | — | 1.7 | defer: outcomes stay private to each user |

## Owner-action package

**Merge authorization**
- Reply with the exact SHA, one PR at a time, in order: "approve PR #11 at
  ae5308d22dbd0721d64b5cb879a7d857ea791804", then (after it merges and #12
  is re-checked) "approve PR #12 at 5c6b2f28b8c971379406cd10c1b9e28f9330618c",
  then "approve PR #13 at 9b169635af20bbdf4debe4626df705bb9f8c3470", then #14
  at its final head.
- PR #11: reply "approve PR #11 at ae5308d22dbd0721d64b5cb879a7d857ea791804"; it
  will be merged with a standard merge commit, pinned to that SHA, only if
  CI is still green.
- PR #12: after #11 merges it is retargeted to `main`; approve its final
  head the same way.
- PR #13 (Analyze, workspace), then PR #14 (dealer inventory, reports,
  cost model): the same each time. I merge the one below, retarget the next
  to `main`, check that its diff is only its own commits, close and reopen it
  so CI runs against `main`, then ask you to approve its final head. Never
  squash, so the stacked branches keep applying cleanly.

**Written provider approval**
- MarketCheck: send `outreach/MARKETCHECK_REQUEST.md` (contact form on
  marketcheck.com or info@marketcheck.com). Save the written answer.
- eBay: `EBAY_SETUP.md` → steps 1–6, including the price-estimate permission
  request. Save the written answer.

**Account creation** (all free to open): eBay developer account and Sandbox
keys; eBay Partner Network; Resend; Backblaze B2; Healthchecks.io;
Tailscale; a VPS provider account; the domain registrar for
`tryflipfirst.com` if not yet registered.

**Payment** (only after you approve each): VPS ~$5–12/mo, domain ~$10–20/yr,
MarketCheck plan only after its written answer and quote
(`economics/scale.md`; ESTIMATES).

**Credentials** — never in chat or Git; only in `/etc/sniper/env` on the
server or a `chmod 600` file on your computer: `EBAY_CLIENT_ID`,
`EBAY_CLIENT_SECRET`, `EBAY_EPN_CAMPAIGN_ID`, `EBAY_COMMERCIAL_AGREEMENT`;
`MARKETCHECK_API_KEY`, `MARKETCHECK_COMMERCIAL_AGREEMENT`; `SMTP_*`; `B2_*`,
`AGE_RECIPIENT`; `HEALTHCHECK_*`.

**Legal review**: `legal/` — every `[LEGAL REVIEW]` and `[…]`.

**Infrastructure and deployment authorization** — one consolidated request.
Approving it authorizes exactly:
1. Buy one VPS: Ubuntu 24.04, 1–2 vCPU, 2 GB RAM, ≥ 25 GB disk, US region.
2. DNS: one `A` record `app.tryflipfirst.com` → the VPS address, plus
   Resend's SPF/DKIM/MX records and `_dmarc.app` (`FIRST_DEPLOY.md`).
3. Run `deploy/install.sh` on that VPS: PostgreSQL 16, Caddy (HTTPS), ufw
   allowing only 80/443 and Tailscale, fail2ban, log rotation, systemd units.
4. Deploy merged `main` with `deploy/sync.sh --apply`; start the worker and
   scheduler; registration stays invitation-only; commercial mode on; no
   provider credentials until approvals exist.
Nothing else (no other DNS records, no firewall changes beyond install.sh,
no paid plans).

## Resume instructions

- Branches: `feat/flipfirst-real-data-readiness` (PR #11, `ae5308d`) and
  `feat/flipfirst-first-live-data` (PR #12, stacked). Re-check with
  `gh api repos/Orangi3/car-sniper-saas/pulls/11` and `…/12`.
- Tests: `python -m pytest -q`; PostgreSQL: `SNIPER_TEST_PG_URL=… python -m
  pytest -q`; browser: `.github/workflows/ci.yml` → browser-smoke steps.
- After #11 merges: `gh api repos/Orangi3/car-sniper-saas/pulls/12 -X PATCH
  -f base=main`, confirm the diff is only #12's commits, wait for CI, ask for
  approval of the final head.
- Branches for the stack: `feat/flipfirst-analyze` (PR #13) and
  `feat/flipfirst-seller-inventory` (PR #14, stacked on #13).
- Browser tests locally: use Playwright **1.47.2**, the version CI pins.
  Newer versions hide CSP problems; 1.63 passed a test that 1.47.2 failed.
- Then: whichever provider answers first → `FIRST_LIVE_TEST.md`.
