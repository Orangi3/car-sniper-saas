# FlipFirst project status and owner-action package

Checkpoint: 2026-10-30. `main` = `b0d6ee4`. Open work is a stack of four
pull requests, merged in this order:
1. PR #11 (`ae5308d`), ready for your approval.
2. PR #12 (`5c6b2f2`).
3. PR #13, Analyze and workspace (`feat/flipfirst-analyze`).
4. PR #14, seller and dealer inventory (`feat/flipfirst-seller-inventory`). Evidence labels: **VERIFIED** (run here),
**SIMULATED** (fixtures / stand-ins / disposable host), **ESTIMATED**,
**NOT TESTED**, **BLOCKED**.

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
- PR #11: reply "approve PR #11 at ae5308d22dbd0721d64b5cb879a7d857ea791804"; it
  will be merged with a standard merge commit, pinned to that SHA, only if
  CI is still green.
- PR #12: after #11 merges it is retargeted to `main`; approve its final
  head the same way.

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
- Then: whichever provider answers first → `FIRST_LIVE_TEST.md`.
