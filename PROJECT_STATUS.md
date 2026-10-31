# FlipFirst project status and owner-action package

Checkpoint: 2026-10-31, release handoff. `main` = `b0d6ee4` (unchanged).
Open work is a stack of pull requests, merged strictly in this order:
1. PR #11 — real-data readiness:
   `ae5308d22dbd0721d64b5cb879a7d857ea791804`
2. PR #12 — first live data, compliance, launch prep:
   `5c6b2f28b8c971379406cd10c1b9e28f9330618c`
3. PR #13 — FlipFirst Analyze and the private deal workspace:
   `9b169635af20bbdf4debe4626df705bb9f8c3470`
4. PR #14 — dealer inventory, CSV import, buyer reports, cost model:
   `b3084a2a0f7538e0a2f9c0bd1474de38785505f6`
5. PR #15 — release handoff. It holds this checkpoint, the release check
   script, the first real test runbook, the rehearsal results and the
   checked costs: documentation plus one read-only script. Its head is
   shown on the PR.

Evidence labels: **VERIFIED** (run here), **SIMULATED** (fixtures /
stand-ins / disposable host), **ESTIMATED**, **NOT TESTED**, **BLOCKED**.

## Release: merge checklist, migrations, rollback

**Merge checklist.**

Rules:
- Each merge happens only after you approve that PR's exact head SHA, one
  PR at a time.
- Normal merge commits only: never squash, rebase or force-push, and never
  bypass branch protection.
- Protection requires a PR and the three CI checks (tests-sqlite,
  tests-postgres, browser-smoke). Admins are included; 0 approving reviews
  are required.

`scripts/release_check.sh` runs every check below and changes nothing: it
never merges, retargets or pushes. It prints `READY` with the exact merge
command, or `STOP` with the reason.

For each PR, in order, after "approve PR #N at SHA":
1. Retarget it to `main` (from #12 on):
   `gh api -X PATCH repos/Orangi3/car-sniper-saas/pulls/N -f base=main`.
2. `scripts/release_check.sh pre N SHA main COMMITS FILES` (table below)
   confirms:
   - the PR is open and its head is exactly the approved SHA;
   - it targets `main`, and GitHub reports it mergeable (`clean`);
   - its diff still has the expected commits and files, so the retarget
     pulled in nothing else;
   - every required check is green on that SHA.
3. Merge with the command it prints. It makes a merge commit pinned to the
   SHA, and GitHub refuses it if the head moved:
   `gh api -X PUT repos/Orangi3/car-sniper-saas/pulls/N/merge -f merge_method=merge -f sha=SHA`.
4. `scripts/release_check.sh post SHA` confirms:
   - `main`'s newest commit merges SHA;
   - `main`'s files are exactly SHA's files;
   - the required checks are green on `main`.

   Re-run it until CI on `main` finishes; it stops while checks are
   pending.
5. Stop and report. The next PR waits for its own approval.

| PR | Approve | `pre` arguments |
|---|---|---|
| #11 | `ae5308d22dbd0721d64b5cb879a7d857ea791804` | `pre 11 ae5308d22dbd0721d64b5cb879a7d857ea791804 main 7 35` |
| #12 | `5c6b2f28b8c971379406cd10c1b9e28f9330618c` | `pre 12 5c6b2f28b8c971379406cd10c1b9e28f9330618c main 9 44` |
| #13 | `9b169635af20bbdf4debe4626df705bb9f8c3470` | `pre 13 9b169635af20bbdf4debe4626df705bb9f8c3470 main 3 27` |
| #14 | `b3084a2a0f7538e0a2f9c0bd1474de38785505f6` | `pre 14 b3084a2a0f7538e0a2f9c0bd1474de38785505f6 main 10 19` |
| #15 | its head, shown on the PR | `pre 15 HEAD main COMMITS FILES`, with the counts the PR shows before #14 merges |

Why CI doesn't need to run again after a retarget:
- after each merge, `main`'s files equal the merged head's (`post` checks
  this);
- so merging the next PR produces exactly its own head's files;
- the push-triggered CI run on that head already tested those files, and
  its results are attached to the SHA.

If anything else moved `main`, `pre` stops, because the PR isn't `clean` or
its counts are off. Then:
- bring the branch up to date with GitHub's update-branch endpoint:
  `gh api -X PUT repos/Orangi3/car-sniper-saas/pulls/N/update-branch -f expected_head_sha=SHA`;
- that creates a new head, and CI runs on it;
- the new head is what you approve.

To repeat a flaky check on the same head, re-run that SHA's push workflow
run: `gh api -X POST repos/Orangi3/car-sniper-saas/actions/runs/RUN_ID/rerun`.
Never close and reopen a PR to trigger CI.

`release_check.sh` was VERIFIED against the live PRs, read-only:
- `pre 11 … main 7 35` → READY;
- a wrong SHA, a wrong base, and `post` before any merge → STOP with the
  reason.

**Migrations.**
- #11 and #12 add none.
- #13 adds `0019_opportunities` (new table) and
  `0020_listings_lower_ymm_index` (new index on `listings`).
- #14 adds `0021_seller_profiles_and_reports` (two new tables).
- All three are `CREATE … IF NOT EXISTS`, paired for SQLite and PostgreSQL
  (`scripts/check_migrations.py` ok), and none alters or deletes existing
  data. On the disposable host they applied on PostgreSQL 16 during the
  upgrade from `5c6b2f2` to `b3084a2`.
- The 0020 index build briefly blocks writes to `listings` on PostgreSQL.
  At pilot size that takes well under a second; at hundreds of thousands of
  rows, run it during a quiet period.

**Rollback** (VERIFIED on SQLite; SIMULATED on PostgreSQL on the disposable
host, 2026-10-31).
- `deploy/sync.sh --rollback` returns to the previous release's code.
  Startup only checks that the migrations *that code* knows about are
  applied, so newer tables don't stop it.
- PR #12's head (`5c6b2f2`) started against a database migrated through
  0021: `/health` 200, `/login` 200 on SQLite. On the disposable host,
  `sync.sh --rollback` from `b3084a2` to `5c6b2f2` on PostgreSQL gave
  `/health` 200, and rolling forward again worked.
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
`FIRST_REAL_TEST.md` has the steps and pass checks. The whole flow was
rehearsed on the disposable host. In short:
1. You approve the merges, in order.
2. You approve the infrastructure request (below) and set the server up
   with `FIRST_DEPLOY.md`.
3. On the server: Resend `SMTP_*`, `SNIPER_SELLER_SUBMISSIONS=1`,
   `ALLOW_REGISTRATION=0`, `BILLING_ORIGIN=https://app.tryflipfirst.com`.
4. Issue two invitations. The seller confirms their email and lists one car
   they may sell.
5. You set the buyer to Lite by hand, because only paid plans show a
   seller's contact email.
6. The buyer:
   - finds the car under **Discover → FlipFirst sellers**, where it plainly
     says there is no market value;
   - analyzes it with their own resale estimate;
   - saves and compares deals;
   - emails the seller.
7. You check `pilot_report`, listing reports, the logs and Healthchecks.
   Then have a short call with each person.

Your decision: have a lawyer review the privacy, tester and seller terms
before inviting anyone outside your own circle.

## Critical path

```
owner approves #11 ──► merge #11 ──► retarget #12 to main ──► CI ──► owner approves #12 ──► merge
      └─► then the same for #13 (Analyze, workspace), #14 (seller and dealer inventory) and #15 (release handoff), in that order
MarketCheck written agreement ─┐
   or eBay written permission ─┴─► first live test (FIRST_LIVE_TEST.md) ──► Gate 0 ──► Gate 1 ──► 10 testers
VPS + domain + email + backups (owner) ──► deploy (FIRST_DEPLOY.md) ───────┘            └──► Gate 2 alerts
      └─► first real buyer-and-seller test (FIRST_REAL_TEST.md; no paid data needed)
legal review of legal/ drafts ──► publish links ──► invitations
```

## Checklist

| # | Task | Priority | Status | Depends on | Evidence | Next action |
|---|---|---|---|---|---|---|
| 1 | Merge PR #11 | CRITICAL | ready | your approval | CI 6/6 at `ae5308d`; `clean`; protection on | you approve; merge checklist above |
| 2 | Merge PR #12 | CRITICAL | ready after #1 | #1 | CI 6/6 at `5c6b2f2`; full suites, browser, simulation (below) | retarget, `pre`, you approve, merge |
| 2a | Merge PR #13 (Analyze + private workspace) | HIGH | ready after #2 | #2 | CI 6/6 at `9b16963`; `tests/test_analyze.py`; full suites; browser journey | same checklist |
| 2b | Merge PR #14 (dealer inventory, CSV import, reports) | HIGH | ready after #2a | #2a | CI 6/6 at `b3084a2`; `tests/test_seller_inventory.py`; full suites; UI flows | same checklist |
| 2d | Merge PR #15 (release handoff: docs + read-only script) | HIGH | open | #2b | CI on its head | same checklist |
| 2e | First real buyer-and-seller test | HIGH | prepared | #2d merged; server; your legal decision | whole flow rehearsed on the disposable host (`DEPLOY_TEST_REPORT.md`) | `FIRST_REAL_TEST.md` |
| 2c | Recruit the first sellers | HIGH | drafted | legal review of seller terms; server live | `outreach/SELLER_RECRUITING.md` (not sent) | approve the messages; visit 10 lots |
| 3 | Licensed listing source | CRITICAL BLOCKER | BLOCKED | provider | `outreach/MARKETCHECK_REQUEST.md`, `EBAY_SETUP.md` ready | you send both requests |
| 4 | Licensed market value | CRITICAL BLOCKER | BLOCKED | #3 (MarketCheck comparables / Price API, or eBay permission), or VinAudit values alone | `DATA_PROVIDER_DECISION.md`; `outreach/VINAUDIT_REQUEST.md` (not sent) | in the same requests; VinAudit optional |
| 5 | First live test | CRITICAL | ready to run | #3 | harness VERIFIED against a stand-in only | `FIRST_LIVE_TEST.md` |
| 6 | Production server | HIGH | prepared | your approval + purchase | the final release rehearsed on a disposable host, 2026-10-31: install, upgrade, HTTPS, rollback, first-test flow (`DEPLOY_TEST_REPORT.md`) | infrastructure request below |
| 7 | Email, backups, monitoring | HIGH | prepared | #6, accounts | `FIRST_DEPLOY.md` steps 10, 11, 13; SMTP and health pings proven against local stand-ins only | create accounts |
| 8 | Legal drafts | HIGH | drafted | lawyer | `legal/` (not published) | review, fill `[…]`, publish links |
| 9 | eBay data deletion rule (C2) | HIGH if eBay | not built | eBay's written terms | `COMPLIANCE.md` | build after eBay answers |
| 10 | eBay account-deletion notifications (C4) | HIGH if eBay | not built | eBay's answer on exemption | `COMPLIANCE.md` | build or claim exemption |
| 11 | MarketCheck Price API connection | MEDIUM | not built | MarketCheck agreement | — | small adapter once permitted |
| 12 | Pilot onboarding | HIGH | prepared | Gate 1 | `PILOT_TUSCALOOSA.md` operations; `pilot_report`; invites VERIFIED on the disposable host | invite after Gate 1 |
| 13 | Gate 2 alerts for testers | MEDIUM | prepared | Gate 2 | hand-set Lite survives reconciliation (test) | `COMPLIANCE.md` C11 command |
| 14 | Close stale PRs #2, #3, #4 | OPTIONAL | — | — | each 0 commits ahead of `main` | close on GitHub |
| 15 | Self-serve account deletion | OPTIONAL | not built | after pilot | in-app listing reports are built (PR #14); you delete accounts on request (`accounts delete`) | post-pilot |

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
- PR #14: `b3084a2a0f7538e0a2f9c0bd1474de38785505f6`

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
| Release rehearsal on a disposable Ubuntu 24.04 host (PostgreSQL 16, Caddy, systemd), 2026-10-31 | SIMULATED — install at `5c6b2f2`, upgrade to `b3084a2` (0019–0021 on PostgreSQL), HTTPS and headers, rollback and roll forward, and the first buyer-and-seller flow: all pass (`DEPLOY_TEST_REPORT.md`) |
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

**Merge authorization.** Approve one PR at a time, in order, each with its
exact SHA:
1. "approve PR #11 at ae5308d22dbd0721d64b5cb879a7d857ea791804"
2. "approve PR #12 at 5c6b2f28b8c971379406cd10c1b9e28f9330618c"
3. "approve PR #13 at 9b169635af20bbdf4debe4626df705bb9f8c3470"
4. "approve PR #14 at b3084a2a0f7538e0a2f9c0bd1474de38785505f6"
5. "approve PR #15 at <its head>"

Each merge follows the merge checklist at the top of this file. After each
one I stop and report, and the next waits for your approval. If a PR's head
or diff isn't what you approved, nothing is merged and I report the
difference.

**Written provider approval** (drafts only; nothing has been sent):
- MarketCheck: send `outreach/MARKETCHECK_REQUEST.md` (contact form on
  marketcheck.com or info@marketcheck.com). Save the written answer.
- eBay: `EBAY_SETUP.md` → steps 1–6, including the price-estimate permission
  request. Save the written answer.
- Optional, market values only: `outreach/VINAUDIT_REQUEST.md`. It's an
  alternative to MarketCheck's Price API.

No adapter is built for any of them until its written answer allows
display to subscribers and storage in saved deals.

**Account creation** (all free to open):
- eBay developer account and Sandbox keys; eBay Partner Network;
- Resend; Backblaze B2; Healthchecks.io; Tailscale;
- a VPS provider account;
- the domain registrar for `tryflipfirst.com`, if not yet registered.

**Payment** (only after you approve each):
- VPS $10–12/mo;
- domain ~$11–20/yr;
- a MarketCheck plan only after its written answer and quote
  (`economics/scale.md`; ESTIMATES).

**Credentials.** Never in chat or Git. Keep them only in `/etc/sniper/env`
on the server, or in a `chmod 600` file on your computer:
- eBay: `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_EPN_CAMPAIGN_ID`,
  `EBAY_COMMERCIAL_AGREEMENT`;
- MarketCheck: `MARKETCHECK_API_KEY`, `MARKETCHECK_COMMERCIAL_AGREEMENT`;
- `SMTP_*`; `B2_*`, `AGE_RECIPIENT`; `HEALTHCHECK_*`.

**Legal review**: `legal/` — every `[LEGAL REVIEW]` and `[…]`.

### Infrastructure request — one approval: "approve infrastructure request"

You carry it out: I have no access to your accounts or to a server. Every
step is in `FIRST_DEPLOY.md`. The steps were rehearsed on a disposable host
except where marked under Risks. Approving it authorizes exactly:

| # | Action | Cost | Undo |
|---|---|---|---|
| 1 | Open free accounts: a VPS provider, Resend, Backblaze B2, Healthchecks.io, Tailscale | $0 | close them |
| 2 | Register `tryflipfirst.com` at an at-cost registrar, if you don't own it | ~$11–20 a year | let it lapse |
| 3 | Buy one VPS: Ubuntu 24.04, 2 GB RAM, 1 vCPU, ≥ 25 GB disk, US region, your SSH key | $10–12 a month | destroy it; billing stops |
| 4 | DNS records: `A` `app.tryflipfirst.com` → the VPS; Resend's SPF and DKIM `TXT` and bounce `MX` for `app.tryflipfirst.com`; `_dmarc.app` `TXT` | $0 | delete the records |
| 5 | On the VPS: `install.sh`, then `tailscale up --ssh`. It sets up PostgreSQL 16, Caddy with Let's Encrypt, ufw (80 and 443 only), fail2ban, log rotation and the systemd units | $0 | rebuild or destroy the VPS |
| 6 | Secrets, only in `/etc/sniper/env`: `SMTP_*` (Resend), `B2_*`, `AGE_RECIPIENT`, `HEALTHCHECK_*` | $0 | delete the lines; revoke the keys |
| 7 | Deploy merged `main` (`sync.sh --apply`); start the worker and scheduler; disable the billing reconcile timer | $0 | `sync.sh --rollback` |
| 8 | Backups: a private B2 bucket with the lifecycle rule; an age keypair, private key off the server; the nightly timer; one restore drill | $0 under 10 GB | delete the bucket |
| 9 | Monitoring: 3 Healthchecks.io checks and the health timer | $0 | delete the checks |
| 10 | For the first real test: `SNIPER_SELLER_SUBMISSIONS=1` | $0 | set `0`, restart `sniper-web` |

**Expected cost:** ~$11–14 a month, plus the domain if you don't own it.
With a paid Tailscale seat it is ~$19–22. The costs table is in
`FIRST_DEPLOY.md`: list prices as of 2026-10-31, not quotes.

**Not authorized by it:**
- invitations;
- publishing the legal documents;
- any paid plan: Resend Pro, a Tailscale seat, provider backups;
- Stripe or data-provider credentials;
- other DNS records or firewall changes.

**Risks, and what limits each:**
- **SSH lockout** when ufw closes public SSH. The Tailscale steps are not
  proven here. Keep the first session open until `ssh root@sniper-staging`
  works over Tailscale. The provider's web console is the fallback.
- **Certificate.** Let's Encrypt issuance is untested, because it needs
  real DNS.
  - Create the `A` record first and wait for `dig` (step 2).
  - Caddy retries a failure without burning the production rate limit.
  - Don't rebuild the server in a loop: the limit is 5 identical
    certificates a week.
- **Email.**
  - Many VPS providers block outbound ports 25, 465 and 587 on new
    accounts. If yours does, use `SMTP_PORT=2587`.
  - Until Resend shows the domain as verified, seller confirmations fail.
    Step 2 of the first test catches this.
  - The free plan refuses mail beyond 100 a day.
- **Backups.** Uploading to B2 and restoring from B2 have not been run. The
  restore drill (step 11) must pass before anyone is invited.
- **Server size.** A real 1–2 vCPU VPS hasn't been measured. In the
  container the stack used ~450 MB idle and ~670 MB under a 20-client load.
  Add the 1 GB swap file.
- **Newer Caddy.** The VPS gets Caddy from Caddy's repository, newer than
  the tested 2.6.2. The Caddyfile uses only basic directives.
- **Cost creep.** Each paid add-on needs its own approval.

**Roll back:**
- **Application:** `bash deploy/sync.sh --rollback root@sniper-staging`
  (rehearsed). Older code runs on the newer schema.
- **Whole server:** take a final backup, remove the `A` record, destroy the
  VPS. Billing stops.
- **Data:** the B2 bucket holds the encrypted dumps. Delete it only when you
  decide to delete the pilot data under the privacy policy.
- **Email:** remove the domain in Resend and its DNS records.

**Minimum budget, same security and backups (~$11–13 a month):**
- VPS: the cheapest 2 GB plan of the listed providers ($10–12). Not 1 GB:
  a deploy briefly runs two web instances.
- Domain: an at-cost registrar, ~$11 a year.
- Free plans: Resend, B2 (under 10 GB), Healthchecks.io, Let's Encrypt.
- No provider backup add-on. The encrypted B2 dumps and the monthly restore
  drill cover the database. The server rebuilds from Git plus
  `/etc/sniper/env`, so keep a copy of that file in your password manager.
- Tailscale's Personal plan ($0), if its terms fit your use. If they don't,
  one paid seat (~$8) keeps the same security.

## Resume instructions

- Stack, in merge order:

  | PR | Branch | Stacked on |
  |---|---|---|
  | #11 | `feat/flipfirst-real-data-readiness` | `main` |
  | #12 | `feat/flipfirst-first-live-data` | #11 |
  | #13 | `feat/flipfirst-analyze` | #12 |
  | #14 | `feat/flipfirst-seller-inventory` | #13 |
  | #15 | `feat/flipfirst-release-handoff` | #14 |

- Re-check any PR with `gh api repos/Orangi3/car-sniper-saas/pulls/N`, or
  run `scripts/release_check.sh pre …`.
- Merging: follow the merge checklist at the top, one approval at a time.
- Tests:
  - SQLite: `python -m pytest -q`;
  - PostgreSQL: `SNIPER_TEST_PG_URL=… python -m pytest -q`;
  - browser: the browser-smoke steps in `.github/workflows/ci.yml`.
- Browser tests locally: use Playwright **1.47.2**, the version CI pins.
  Newer versions hide CSP problems; 1.63 passed a test that 1.47.2 failed.
- Next, in order:
  - after the merges and the server: `FIRST_REAL_TEST.md`;
  - whichever provider answers first: `FIRST_LIVE_TEST.md`.
