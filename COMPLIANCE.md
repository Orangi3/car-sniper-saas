# Compliance register (pilot)

What a legitimate pilot must satisfy, what is built, and what is an open
question. Provider terms come from search excerpts (2026-10-30; the
providers' sites are blocked from the build environment) — check each
against the live text. Engineering research, not legal advice. Nothing here
invents a rule: where the terms are uncertain, the item is a question and
nothing was built.

| # | Requirement | Source | Status | Next step |
|---|---|---|---|---|
| C1 | Price estimates for eBay items need eBay's written permission ("may not … suggest or model prices for items listed on eBay Sites") | eBay API License Agreement (excerpt) | **BLOCKER** — eBay off in commercial mode until `EBAY_COMMERCIAL_AGREEMENT` is set (code gate, tested) | ask in the application (`EBAY_SETUP.md`) |
| C2 | Delete eBay content when it is no longer publicly available; delete intermediate copies when no longer needed | eBay agreement (excerpts; 2018 text for copies) | **Not built** for the server. Harness: `validate_deals purge`. Display: stale listings hidden after 6 h | Build before production eBay data reaches the server, to the window eBay states. Spec: on each scheduler tick, delete eBay listings not re-confirmed within that window, and ended auctions, with the rows derived from them — the same tables `seller_listings.remove` clears. Deleting sooner than required is always compliant. Ask whether encrypted backups (30-day lifecycle) count |
| C3 | Displayed item data ≤ 6 h older than eBay; other eBay content ≤ 24 h | eBay agreement (excerpt) | **Built** — hidden after 6 h; detail returns 410; ended auctions hidden (tests) | re-check the figure in the live text |
| C4 | Subscribe to marketplace-account-deletion notifications or hold an exemption before the first production call | eBay developer page (excerpt) | **Not built** — not needed until eBay grants access | Ask eBay whether an app that stores listing data but no eBay user accounts qualifies for the exemption. If it doesn't: a public HTTPS endpoint that answers eBay's `challenge_code` with SHA-256(challenge code + verification token + endpoint URL) as `{"challengeResponse": …}` and acknowledges notifications with a 2xx (third-party guide; confirm on eBay's page). FlipFirst stores no eBay user identifiers, so a notification deletes nothing |
| C5 | No AI training on eBay content; eBay data in third-party generative-AI tools needs consent (scope reported as Restricted-API data) | eBay agreement update, 2025 (press report + excerpt) | **Operational rule** — FlipFirst runs no AI on listing data | never paste real eBay data, run reports or review sheets into Claude or another AI tool; share only counts |
| C6 | eBay attribution, logo and wording | unknown | source chip "eBay", link to the eBay listing, affiliate link when `EBAY_EPN_CAMPAIGN_ID` is set | ask eBay (application question 4) |
| C7 | MarketCheck data is "licensed, not sold", may not form "independent or persistent datasets outside of the Service", and use must be "tied to a live query or user-driven interaction" | MarketCheck terms (excerpt) | **BLOCKER** for central polling + storage — off until `MARKETCHECK_COMMERCIAL_AGREEMENT` is set (code gate) | written agreement (`OWNER_ACTIONS.md` § 1) |
| C8 | Seller submissions: consent, limited contact exposure, removal | own policy | **Built** — three recorded consents with a timestamp (authorized to sell, details accurate, contact may be shown); verified contact email shown only to signed-in paid (Lite or Pro) buyers — so testers set to Lite at Gate 2 see it; email confirmation before publishing; Remove deletes the listing and everything derived from it; hidden after 30 days without "Still for sale" (`SELLER_CONFIRM_DAYS`) | **you**: write and publish seller terms + privacy notice (`SELLER_PILOT.md`); decide how long hidden stale listings are kept (today: until removed) |
| C9 | FlipFirst account deletion on request | your privacy notice; law where testers live | **Not built** — sessions can be revoked (`auth.revoke_sessions`), there is no delete-account tool | decide what your privacy notice promises before inviting testers; build an owner deletion tool if it promises deletion |
| C10 | Customer-facing disclaimers | product honesty | **Built** — values labeled as estimates from asking prices, never sales; profit "Estimated, not guaranteed or independently verified"; "FlipFirst doesn't inspect cars or verify titles…"; risk labels; auction prices called current bids | **you**: tester terms of use + privacy policy (no page exists in the app; publish them anywhere and link them in the invitation) |
| C11 | Pilot tester access | product rule | **Built** — invite-only single-use links, Free plan (feeds only; no saved searches or alert channels), sessions revocable. Gate 2: set a tester to Lite by hand; it survives billing reconciliation (test) | remove a tester: revoke sessions, set plan to `free` |
| C12 | Logs and backups hold provider data | provider terms (C2, C7) | backups: 30-day B2 lifecycle; access logs record request paths (listing ids) | include in the C2/C7 questions |

## Commands for C11 (as root on the server)

Grant a tester Lite at Gate 2, or take it back (`starter` is Lite's
internal name; `free` reverses it):

```bash
systemd-run --pipe --wait --quiet --collect --uid=sniper -p EnvironmentFile=/etc/sniper/env -p WorkingDirectory=/opt/sniper/current /opt/sniper/.venv/bin/python3 -c "import auth; auth.set_plan(auth.get_user_by_email('tester@example.com').id, 'starter')"
```

Sign a tester out everywhere:

```bash
systemd-run --pipe --wait --quiet --collect --uid=sniper -p EnvironmentFile=/etc/sniper/env -p WorkingDirectory=/opt/sniper/current /opt/sniper/.venv/bin/python3 -c "import auth; print(auth.revoke_sessions(auth.get_user_by_email('tester@example.com').id))"
```
