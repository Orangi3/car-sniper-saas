# FlipFirst project status and owner-action package

Checkpoint: 2026-10-30. `main` = `b0d6ee4`. Open work: PR #11 (`ae5308d`,
approved-ready), PR #12 (stacked on #11; branch
`feat/flipfirst-first-live-data`). Evidence labels: **VERIFIED** (run here),
**SIMULATED** (fixtures / stand-ins / disposable host), **ESTIMATED**,
**NOT TESTED**, **BLOCKED**.

## Critical path

```
owner approves #11 ──► merge #11 ──► retarget #12 to main ──► CI ──► owner approves #12 ──► merge
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
