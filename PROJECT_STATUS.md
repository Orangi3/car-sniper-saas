# FlipFirst project status and owner-action package

Checkpoint: 2026-10-31, release merged. Nothing is deployed, and nothing
has been bought or sent.

**Release:** `main` = `f5286f115691cd27e07a8ab10f126941c501d61f` (tree
`7b562ac2224561d6b5feac932049bea2efbdb162`). It holds PRs #11–#15, merged
under your approval of their exact heads, each with a normal merge commit:

| PR | Approved head | Merge commit on `main` |
|---|---|---|
| #11 real-data readiness | `ae5308d22dbd0721d64b5cb879a7d857ea791804` | `9e941cf3542e301b6ef78b16cbb0f38635525d14` |
| #12 first live data, compliance | `5c6b2f28b8c971379406cd10c1b9e28f9330618c` | `af819a1c719079c61ed6f5dda51cf1fd7af736f7` |
| #13 Analyze, deal workspace | `9b169635af20bbdf4debe4626df705bb9f8c3470` | `7e830afc951ffacb66b9cf1c0f81ba39b142c96e` |
| #14 dealer inventory, reports | `b3084a2a0f7538e0a2f9c0bd1474de38785505f6` | `d71c6a4fd12d5085b81d14a6acceb0e33ba43d77` |
| #15 release handoff | `9faac0b222f3d03807ed8e980d6486a12f57e3be` | `f5286f115691cd27e07a8ab10f126941c501d61f` |

Before each merge, VERIFIED:
- the head was exactly the approved SHA;
- the PR was `clean`;
- the diff had the expected size;
- the required checks were green;
- the local merge result was exactly the head's tested tree.

After each merge, VERIFIED:
- `main` merged that head and had exactly its files;
- CI on `main` was green.

Final `main`:
- its tree is the predicted one;
- its first-parent history is exactly the five merge commits;
- `check_migrations` passes, through 0021;
- all three required checks are green;
- protection is unchanged.

**Next:** PR #16 needs your separate approval. It holds:
- the gated go-live script and the B2 region fix;
- the fixes from its security review (below);
- this checkpoint and the pilot docs.

Evidence labels: **VERIFIED** (run here), **SIMULATED** (fixtures /
stand-ins / disposable host), **ESTIMATED**, **NOT TESTED**, **BLOCKED**.

## Release: merge checklist, migrations, rollback

**Merge checklist.**

Rules:
- Merges happen only with your approval: the consolidated approval of the
  five exact heads (A, below), or one PR at a time.
- Normal merge commits only: never squash, rebase or force-push, and never
  bypass branch protection.
- Protection requires a PR and the three CI checks (tests-sqlite,
  tests-postgres, browser-smoke). Admins are included; 0 approving reviews
  are required.

`scripts/release_check.sh` runs every check below and changes nothing: it
never merges, retargets or pushes. It prints `READY` with the exact merge
command, or `STOP` with the reason.

For each PR, in order:
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
5. Report. Under the consolidated approval, go on to the next PR only if
   every check still holds; otherwise stop. Approving one PR at a time
   means stopping after each merge.

| PR | Approve | `pre` arguments |
|---|---|---|
| #11 | `ae5308d22dbd0721d64b5cb879a7d857ea791804` | `pre 11 ae5308d22dbd0721d64b5cb879a7d857ea791804 main 7 35` |
| #12 | `5c6b2f28b8c971379406cd10c1b9e28f9330618c` | `pre 12 5c6b2f28b8c971379406cd10c1b9e28f9330618c main 9 44` |
| #13 | `9b169635af20bbdf4debe4626df705bb9f8c3470` | `pre 13 9b169635af20bbdf4debe4626df705bb9f8c3470 main 3 27` |
| #14 | `b3084a2a0f7538e0a2f9c0bd1474de38785505f6` | `pre 14 b3084a2a0f7538e0a2f9c0bd1474de38785505f6 main 10 19` |
| #15 | `9faac0b222f3d03807ed8e980d6486a12f57e3be` | `pre 15 9faac0b222f3d03807ed8e980d6486a12f57e3be main 4 8` |
| #16 | separate approval, after the five | `pre 16 HEAD main COMMITS FILES`, with the counts the PR shows before #15 merges |

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

**Stop immediately, merge nothing more, and report** if any of these
happens:
- a head isn't the approved SHA;
- `main` moved for any other reason;
- a required check fails or stays red;
- a diff's commit or file count differs from the table;
- a merge would conflict;
- a migration number repeats or `check_migrations.py` fails on `main`;
- `main`'s files differ from the merged head's;
- anything changes the risk assessment.

**Merge simulation (VERIFIED, 2026-10-31).** All five merges were run
locally, in order, as real merge commits on a copy of `main`:
- each result is exactly the tree CI tested on that PR's head;
- there are no conflicts;
- the commit and file counts match the table;
- the final tree is `7b562ac2224561d6b5feac932049bea2efbdb162`, #15's head
  tree.

So after the five merges, `git rev-parse origin/main^{tree}` must print
exactly that.

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

## Release manifest

| Item | Value |
|---|---|
| Release (five PRs merged) | `f5286f115691cd27e07a8ab10f126941c501d61f`, tree `7b562ac2224561d6b5feac932049bea2efbdb162` (VERIFIED) |
| Production candidate | `main` after PR #16 merges, so `golive.sh` and the B2 region fix ship. Without #16, deploy the five-PR `main` with #15's 13 manual steps |
| Schema | through `0021_seller_profiles_and_reports` (22 migrations, 0000–0021) |
| Settings, by name only (`/etc/sniper/env`) | `DATABASE_URL`, `BILLING_ORIGIN`, `SNIPER_TRUSTED_PROXIES`, `ALLOW_REGISTRATION=0`, `SNIPER_COMMERCIAL_MODE=1`, `SNIPER_SELLER_SUBMISSIONS=1`, `SMTP_HOST`/`PORT`/`USER`/`PASSWORD`/`FROM`, `B2_BUCKET`/`KEY_ID`/`APPLICATION_KEY`/`ENDPOINT`, `AGE_RECIPIENT`, `HEALTHCHECK_WEB_URL`/`PIPELINE_URL`/`BACKUP_URL`. `STRIPE_*` and data-provider keys stay empty |
| Services | `sniper-web` (gunicorn, 2 workers, 127.0.0.1:8765), `sniper-worker`, `sniper-scheduler`, `sniper-migrate` at boot, PostgreSQL 16, Caddy, fail2ban |
| Timers | `sniper-backup.timer` 04:23 UTC; `sniper-healthcheck.timer` every 5 min; `sniper-reconcile.timer` **off** (no billing) |
| Backups | nightly `pg_dump` → zstd → age (public key on the server) → B2, kept 30 days; the last 3 nights also on the server; a restore drill monthly, off the server |
| Health | `GET /health` through Caddy; health pings for web and pipeline (`preflight --target internal`) and backup; `golive.sh` re-checks everything |
| Rollback | `deploy/sync.sh --rollback` (rehearsed; older code runs on the newer schema). Whole server: final backup, remove the `A` record, destroy the VPS |

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

## Security review of PR #16 (2026-10-31, at `f875ada`)

Scope: `deploy/golive.sh`, the `backup.sh` and `restore.sh` changes, secret
handling, and the infrastructure the script assumes. Fixed in `9cbbab1`,
with regression tests in `tests/test_deploy_config.py`.

| # | Finding | Severity | Fix |
|---|---|---|---|
| 1 | Gate 7 said the probes "pinged Healthchecks", but `healthcheck.sh` ignores a failed ping, so a mistyped ping URL passed. Monitoring still fails safe: Healthchecks alerts when pings stop | medium | each of the three URLs must accept a ping. The URL goes to curl on stdin, never on a command line |
| 2 | `--test-email` with no address looped forever | low | exits 2 with a message |
| 3 | Gate 8 rejected `ufw allow in on tailscale0`, which opens nothing to the internet | low | rules bound to `tailscale0` don't count |
| 4 | Settings in single quotes failed the checks, although systemd accepts them (fails closed) | low | both quote styles are stripped |
| 5 | `restore.sh` printed the verification database URL with any password in it | low (existing; your terminal only) | password masked |
| 6 | `B2_ENDPOINT` wasn't required to be HTTPS. Re-assessed in the next round as a genuine issue (below) | **medium** | fixed in `f344f26` |
| 7 | `install.sh` installs Tailscale with its official `curl … \| sh` script | info (existing, outside this PR) | none. Tailscale's signed apt repository would be stricter |

No secret values are printed:
- settings are read only into comparisons;
- the only value printed is the public origin;
- failure messages name settings, never their values;
- PostgreSQL errors name the host and user, never the password;
- only the test email address appears on a command line.

The script edits nothing in `/etc/sniper/env` and touches nothing off the
server. Re-running it restarts the app, a few seconds of downtime.

Assumptions:
- the server can reach its own public address (gate 5). True at
  DigitalOcean, Akamai/Linode and Vultr;
- ufw and the `tailscale` command come from `install.sh`;
- Caddy redirects HTTP with a 308.

### Second round (2026-10-31, at `682f220`): transport and gates

- **The HTTPS issue is genuine.**
  - Over `http://`, every backup request would carry the B2 key ID and a
    signature that can be replayed for about 15 minutes. Bucket and file
    names would be visible. The secret key itself is never sent.
  - Worse, the restore drill could be served a forged "backup". Anyone can
    encrypt one to the public `AGE_RECIPIENT`, and pg_restore would load
    attacker-written SQL on your computer.
  - With HTTPS, the AWS CLI verifies B2's certificate, which closes this.
- **Fixed in `f344f26`, with tests:**
  - `backup.sh` and `restore.sh` refuse a non-`https://` endpoint. Only a
    loopback stand-in for tests is allowed, the same rule the mailer applies
    to SMTP.
  - `golive.sh` gate 1 enforces the same rule for `B2_ENDPOINT` and the
    three Healthchecks URLs (a ping URL over HTTP would leak too).
- **Gates that only warned now fail:**
  - a missing admin account fails gate 9;
  - `--test-email` is required;
  - `install.sh` stops on anything but Ubuntu 24.04, before installing
    anything.
- **Copy:** the seller's price-change toast no longer claims buyers are
  notified. No pilot plan sends price-drop alerts.

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
1. You approve the five-PR release (A), then PR #16.
2. You approve the infrastructure request (B) and run `FIRST_DEPLOY.md`.
   Its step 7, `golive.sh`, checks the settings, HTTPS, backup, monitoring
   and firewall.
3. You approve the test (E). The seller and buyer accept the reviewed
   terms in writing. You issue two invitations and send the drafted
   messages.
4. The seller confirms their email and lists one car they may sell.
5. You set the buyer to Lite by hand, because only paid plans show a
   seller's contact email.
6. The buyer:
   - finds the car under **Discover → FlipFirst sellers**, where it plainly
     says there is no market value;
   - analyzes it with their own resale estimate;
   - saves and compares deals;
   - marks the car Contacted and emails the seller.
7. You check `pilot_report`, listing reports, the logs and Healthchecks.
   Then have a short call with each person.

Your decision: have a lawyer review the privacy, tester and seller terms
before inviting anyone outside your own circle.

## Critical path

```
you approve the five-PR release (A) ──► #11 → #12 → #13 → #14 → #15 merged with checks ──► you approve PR #16 ──► merged
accounts (C) + infrastructure (B) ──► FIRST_DEPLOY.md: install, settings, sync --apply, admin, golive.sh, restore drill
legal decision (D) + pilot approval (E) ──► first real buyer-and-seller test (FIRST_REAL_TEST.md; no paid data)
MarketCheck / eBay / VinAudit written permission (F) ──► FIRST_LIVE_TEST.md ──► Gate 0 ──► Gate 1 ──► 10 testers ──► Gate 2 alerts
```

## Checklist

| # | Task | Priority | Status | Depends on | Evidence | Next action |
|---|---|---|---|---|---|---|
| 1 | Merge the five-PR release (#11–#15) | CRITICAL | **done** 2026-10-31 | — | `main` `f5286f1`, tree `7b562ac`, CI green | — |
| 2 | Merge PR #16 (go-live script, B2 region fix, docs) | HIGH | open | #1 | `golive.sh` rehearsed (`DEPLOY_TEST_REPORT.md`); CI on its head | your separate approval |
| 2e | First real buyer-and-seller test | HIGH | prepared | #2; server; legal decision (D); pilot approval (E) | whole flow rehearsed on the disposable host | `FIRST_REAL_TEST.md` |
| 2c | Recruit the first sellers | HIGH | drafted | legal review of seller terms; server live | `outreach/SELLER_RECRUITING.md` (not sent) | approve the messages; visit 10 lots |
| 3 | Licensed listing source | CRITICAL BLOCKER | BLOCKED | provider | `outreach/MARKETCHECK_REQUEST.md`, `EBAY_SETUP.md` ready | you send both requests |
| 4 | Licensed market value | CRITICAL BLOCKER | BLOCKED | #3 (MarketCheck comparables / Price API, or eBay permission), or VinAudit values alone | `DATA_PROVIDER_DECISION.md`; `outreach/VINAUDIT_REQUEST.md` (not sent) | in the same requests; VinAudit optional |
| 5 | First live test | CRITICAL | ready to run | #3 | harness VERIFIED against a stand-in only | `FIRST_LIVE_TEST.md` |
| 6 | Production server | HIGH | prepared; existence of a VPS, domain or DNS unknown to me | your approval (B) + purchase | install, upgrade, HTTPS, rollback, `golive.sh`, backup → restore drill, reboot: all SIMULATED on a disposable host (`DEPLOY_TEST_REPORT.md`) | B and C below |
| 7 | Email, backups, monitoring | HIGH | prepared | #6, accounts (C) | STARTTLS + AUTH email, encrypted backup and restore, health pings: proven against stand-ins only | `golive.sh` gates 5–7 on the real server |
| 8 | Legal drafts | HIGH | drafted | lawyer | `legal/` (not published) | review, fill `[…]`, publish links |
| 9 | eBay data deletion rule (C2) | HIGH if eBay | not built | eBay's written terms | `COMPLIANCE.md` | build after eBay answers |
| 10 | eBay account-deletion notifications (C4) | HIGH if eBay | not built | eBay's answer on exemption | `COMPLIANCE.md` | build or claim exemption |
| 11 | MarketCheck Price API connection | MEDIUM | not built | MarketCheck agreement | — | small adapter once permitted |
| 12 | Pilot onboarding | HIGH | prepared | Gate 1 | `PILOT_TUSCALOOSA.md` operations; `pilot_report`; invites VERIFIED on the disposable host | invite after Gate 1 |
| 13 | Gate 2 alerts for testers | MEDIUM | prepared | Gate 2 | hand-set Lite survives reconciliation (test) | `COMPLIANCE.md` C11 command |
| 14 | Close stale PRs #2, #3, #4 | OPTIONAL | — | — | each 0 commits ahead of `main` | close on GitHub |
| 15 | Self-serve account deletion | OPTIONAL | not built | after pilot | in-app listing reports are built (PR #14); you delete accounts on request (`accounts delete`) | post-pilot |

## Verified — final release candidate (2026-10-31)

Final heads: as listed at the top (#11–#15).

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
| Go-live rehearsal on a fresh disposable host, 2026-10-31 (PR #16 at `732ab60`) | SIMULATED, with stand-ins for Resend, B2 and Healthchecks.io. `golive.sh` stops at gate 1 when settings are missing and at gate 8 on an extra open port, then passes all 9 gates. Also passing: the test email over STARTTLS + AUTH; backup, pipeline and web pings; the restore drill; the reboot (`DEPLOY_TEST_REPORT.md`) |
| GitHub, 2026-10-31 | VERIFIED — #11–#15 heads as listed, all `clean`, CI 6/6 each, 0 reviews, comments or unresolved threads; `main` `b0d6ee4`; protection requires the 3 checks, includes admins, no force-push |
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

## Owner-action package (A–F)

Nothing below has been done for you. Silence is never taken as consent.

**What already exists is unknown to me** (checked 2026-10-31):
- No computer of yours is connected to this session.
- The repo has no deployment records. Its secret and variable names can't
  be read through this session's GitHub access.
- The network policy blocks lookups of `tryflipfirst.com`,
  `app.tryflipfirst.com`, `rdap.verisign.com` and `dns.google`.
- No file in your Google Drive mentions `tryflipfirst`. I searched names
  only and opened nothing.

So tell me in one reply:
- whether the domain is registered, and where;
- whether a server exists: provider, Ubuntu version, RAM, public IP;
- whether Resend, B2, Healthchecks.io or Tailscale accounts exist.

Or allow those hosts under Network access in the environment settings.

### Smallest path to live (one real seller, one real buyer)

1. Reply "approve PR #16 at <its head>". I merge it with the checks.
2. Tell me whether a VPS or the domain already exist, so nothing is bought
   twice.
3. Accounts (C): VPS (2 GB, Ubuntu 24.04, US, your SSH key), Resend, B2,
   Healthchecks.io (3 checks), one Tailscale seat, and `age-keygen` on your
   Mac. Values go only in your password manager.
4. DNS: `A` `app` → the VPS, plus Resend's records and `_dmarc.app`.
5. Server (`FIRST_DEPLOY.md` steps 3–7):
   - `install.sh` and `tailscale up`;
   - settings in `/etc/sniper/env`;
   - `sync.sh --apply` from your Mac;
   - your admin account;
   - `golive.sh --test-email you@…`, until every gate passes.
6. Restore drill on your Mac (`restore.sh`).
7. Legal (D): the reviewed terms, or your decision to test with two people
   you know. Each replies "I agree".
8. Pilot (E):
   - two invitations, with the drafted messages;
   - set the buyer to Lite;
   - run `FIRST_REAL_TEST.md`;
   - send me the `pilot_report` output (counts only).

### A. Release authorization

- **Done.** #11–#15 were merged on 2026-10-31 under your approval of their
  exact heads, with every check above.
- **PR #16** needs its own reply: "approve PR #16 at <its head>". I merge it
  with the same checks: retarget, pre-check, merge-tree check, pinned merge
  commit, post-check. Until then `main` has no `golive.sh`; deploy only
  after #16 merges.

### B. Infrastructure authorization — "approve infrastructure request"

You carry it out with `FIRST_DEPLOY.md` (8 steps; `golive.sh` checks the
result). Approving it authorizes exactly:

| # | Action | Cost a month | Undo |
|---|---|---|---|
| 1 | Buy one VPS: Ubuntu 24.04, 2 GB RAM, 1 vCPU, ≥ 25 GB, US region, your SSH key. Skip if a suitable one exists | $10–12 | destroy it; billing stops |
| 2 | Register `tryflipfirst.com` at an at-cost registrar, only if you don't own it | ~$1 ($11–20 a year) | let it lapse |
| 3 | DNS: `A` `app.tryflipfirst.com` → the VPS; Resend's SPF and DKIM `TXT` and bounce `MX` for `app.tryflipfirst.com`; `_dmarc.app` `TXT` | $0 | delete the records |
| 4 | One Tailscale Standard seat. The free Personal plan is for non-commercial use only. Standard rose from $6 to $8 per user in April 2026 (price trackers; Tailscale's page couldn't be read from here) | $8 | cancel the seat |
| 5 | On the VPS: `install.sh`, `tailscale up --ssh`, settings in `/etc/sniper/env`, `sync.sh --apply`, your admin account, `golive.sh` | $0 | `sync.sh --rollback`, or rebuild the VPS |
| 6 | Restore drill on your computer | $0 | — |

**Expected total:** ~$19–21 a month (VPS $10–12 + Tailscale $8 + domain ~$1), plus a one-time ~$11–20 if you register the domain.
List prices are from web-search summaries of the providers' pages
(2026-10-31), not quotes.
- Free plans: Resend, B2 under 10 GB, Healthchecks.io, Let's Encrypt.
- **Recommended: the Tailscale seat ($8).** It keeps the rehearsed setup,
  with no public SSH port.
- Cheaper alternatives (~$11–13), neither built nor rehearsed:
  - **WireGuard (free, comparable security).** SSH listens only on the
    tunnel. One UDP port is open, and it ignores unauthenticated packets. It
    needs `install.sh` and `golive.sh` changes and key handling: about a day
    of work, plus a rehearsal.
  - **SSH open only to your IP, keys only (weaker).**
    - Port 22 becomes reachable from that one address. If your IP is
      shared (mobile or carrier networks), that's many people.
    - If your IP changes, you need the provider's web console.
    - `golive.sh` gate 8 refuses the open port.
- One-time costs: the domain, only if not yet registered.

**Not authorized by it:** invitations; publishing the legal documents; any
other paid plan (Resend Pro, provider backups); Stripe or data-provider
credentials; other DNS records or firewall changes.

**Risks, and what limits each:**
- **SSH lockout** when ufw closes public SSH. Keep the first session open
  until `ssh root@sniper-staging` works over Tailscale. The provider's web
  console is the fallback.
- **Certificate.** Let's Encrypt is untested here (it needs real DNS).
  - Create the `A` record first and wait for `dig`.
  - Caddy retries a failure without burning the production rate limit.
  - Don't rebuild in a loop: the limit is 5 identical certificates a week.
- **Email.** Many VPS providers block outbound ports 25, 465 and 587 on new
  accounts; if yours does, use `SMTP_PORT=2587`. Seller confirmations fail
  until Resend shows the domain verified. `golive.sh --test-email` catches
  both.
- **Backups.** Real B2 is untested. The backup and the restore drill ran end
  to end against an S3 stand-in (`DEPLOY_TEST_REPORT.md`). Gate 6 and your
  restore drill must pass on the real bucket before anyone is invited.
- **Server size.** A real 1–2 vCPU VPS hasn't been measured; the stack fits
  2 GB in the container. Add the 1 GB swap file.
- **Cost creep.** Each paid add-on needs its own approval.

**Roll back:**
- **Application:** `bash deploy/sync.sh --rollback root@sniper-staging`
  (rehearsed). Older code runs on the newer schema.
- **Whole server:** take a final backup, remove the `A` record, destroy the
  VPS.
- **Data:** delete the B2 bucket only when you decide to delete the pilot
  data under the privacy policy.

### C. External accounts — you create them; values go only to your password manager and `/etc/sniper/env`

| Account | Gives | Cost |
|---|---|---|
| VPS provider | the server (skip if you have one) | $10–12/mo |
| Domain registrar | `tryflipfirst.com` (skip if owned) | $11–20/yr |
| Resend | `SMTP_PASSWORD` (sending-only key) and the DNS records | free: 3,000/mo, 100/day |
| Backblaze B2 | `B2_KEY_ID`, `B2_APPLICATION_KEY`, `B2_ENDPOINT`; a second, read-only key for restore drills | free under 10 GB |
| Healthchecks.io | the three `HEALTHCHECK_*_URL`s | free: 20 checks |
| Tailscale | admin access over SSH | one Standard seat, $8/mo |
| age (on your Mac) | `AGE_RECIPIENT`; the private key stays on your Mac | free |

Never paste a value into chat or Git. `golive.sh` checks the settings by
name and never prints them.

### D. Legal approval

Minimum before the first two real people. The drafts are in `legal/`, and
none is published.
1. Fill in the operator identity everywhere: legal entity, address,
   privacy/contact email, effective date. Seven placeholders are in the
   privacy policy alone.
2. Name your hosting provider in the privacy policy's list of service
   providers.
3. Confirm the retention choices marked `[owner: confirm]`:
   - listing reports kept with the reporter's account;
   - web-server log retention;
   - the deletion-confirmation period (`[…30…]` days).
4. Confirm the seller-terms wording for a buyer you give Lite for free.
   It's marked `[LEGAL REVIEW]` in `SELLER_TERMS.md`.
5. Then either a lawyer reviews the `[LEGAL REVIEW]` items (14 markers
   across the drafts), or you decide to run the first test with two people you know
   before that review.

**Recording acceptance** (the app shows no terms yet):
- Email each person the documents that apply. Name the version: the date
  and the commit of `legal/` you sent.
- Keep their "I agree" reply.
- Log the date, person, documents and version in your own records, not in
  the app.

This review is not legal advice, and no automated review makes the drafts
compliant.

### E. Pilot authorization

1. Approve the first real test: one seller you know who has a car they may
   sell, and one buyer.
2. Issue the two invitations and send the messages in `FIRST_REAL_TEST.md`
   yourself. They're drafted, not sent.
3. Set the buyer to Lite by hand (step 4). Only paid plans show seller
   contact. Lite can't send alerts while no car has a valuation:
   - alerts need a "trusted" valuation;
   - saved searches stay in conservative mode on Lite;
   - a one-seller test has no comparables.
4. After the test: `pilot_report`, feedback, then decide on more testers.

### F. Data-provider outreach — drafts only, nothing sent

Each asks for written permission for:
- commercial SaaS use;
- listing access (where relevant);
- valuations and figures derived from them;
- customer display;
- storage and retention;
- API usage and limits;
- pilot pricing.

The drafts:
- MarketCheck: `outreach/MARKETCHECK_REQUEST.md`;
- eBay: `EBAY_SETUP.md` steps 1–6, including the price-estimate
  permission;
- VinAudit (values only): `outreach/VINAUDIT_REQUEST.md`.

No integration is built until an answer allows display to subscribers and
storage in saved deals. Until then the pilot runs on first-party seller
listings and the buyer's own numbers.

## Resume instructions

- `main` = `f5286f1` holds #11–#15. Open: PR #16, branch
  `feat/flipfirst-golive`, which needs your approval.
- To merge #16 after approval:
  - `scripts/release_check.sh pre 16 HEAD main COMMITS FILES`;
  - `git merge-tree --write-tree origin/main HEAD` must equal `HEAD^{tree}`;
  - the pinned merge;
  - `scripts/release_check.sh post HEAD`.
- Tests:
  - SQLite: `python -m pytest -q`;
  - PostgreSQL: `SNIPER_TEST_PG_URL=… python -m pytest -q`;
  - browser: the browser-smoke steps in `.github/workflows/ci.yml`, with
    Playwright **1.47.2**, the version CI pins.
- Deployment: `FIRST_DEPLOY.md` (8 steps), then `FIRST_REAL_TEST.md`.
- When a provider answers: `FIRST_LIVE_TEST.md`.
