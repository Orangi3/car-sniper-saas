# eBay Motors: the path to real listings

Researched 2026-10-30 from search excerpts of eBay's developer pages
(developer.ebay.com itself is blocked from the build environment, so no page
was read in full and no live call was made). Engineering research, not legal
advice. Never paste a key or secret into chat, an issue or Git: secrets go in
`/etc/sniper/env` on the server (`root:sniper 0600`) or in a private env file
on your own machine.

## Seven questions, answered

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | Is the integration complete? | **For search-based ingestion, yes.** OAuth client credentials with token refresh, `item_summary/search` in Cars & Trucks (6001) with local-pickup ZIP + radius, price band, newest first, bounded paging, schema-drift check, affiliate links, 6 h display rule, Buy-API permission error explained, auctions flagged. **Missing:** item-detail calls (`getItem`: mileage, VIN, title brand, description) — summaries are title-only; a marketplace-account-deletion endpoint (or an exemption); a retention purge of stored eBay data | VERIFIED in code and fixture tests; NOT TESTED against the real API |
| 2 | Which credentials? | `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (a production keyset); `EBAY_EPN_CAMPAIGN_ID` (eBay Partner Network campaign); `EBAY_COMMERCIAL_AGREEMENT` (your reference for eBay's production approval). Sandbox testing: a sandbox keyset + `EBAY_ENVIRONMENT=sandbox` | VERIFIED (code) |
| 3 | Is access approval needed? | **Yes for production.** eBay says Buy API production use is for eBay partners: apply through the eBay Partner Network, get approvals and sign contracts; approval is not guaranteed. A production keyset is also disabled until you subscribe to marketplace-account-deletion notifications or are granted the exemption. Sandbox needs no approval but returns test data only | eBay docs via search (sources below); NOT confirmed with eBay |
| 4 | Is commercial use authorized? | **Not yet.** Only eBay's approval of an application that describes FlipFirst accurately (paid subscribers, valuation from asking prices, alerts, stored price history) authorizes it. Whether eBay permits using listing data to estimate values is **unconfirmed** — ask explicitly. The code keeps eBay off in commercial mode until `EBAY_COMMERCIAL_AGREEMENT` is set | BLOCKED on owner action |
| 5 | Do API limits support the polling? | **Yes.** Default 5,000 Browse calls/day. One territory every 5 min with ≤ 2 pages = ≤ 576 search calls/day plus ~12 token calls; three territories still fit. More via the free Application Growth Check | eBay docs via search; ESTIMATED usage from code |
| 6 | Are the fields sufficient for scoring? | **Partly.** Price, year, make/model and location: yes. Mileage only when the title states it (otherwise `mileage_unknown`, lower confidence). Trim, cab and drivetrain from the title only. No description → damage is not screened (`description_unchecked`). No title brand, no VIN. Seller type is absent on EBAY_US (`seller_type_unknown`). Adding `getItem` for candidates only would close most gaps within the call limit | VERIFIED (code + fixtures); real field coverage NOT TESTED |
| 7 | Are display restrictions handled? | **Partly.** Listings not re-confirmed by a poll within 6 h are hidden and their detail page returns 410; ended auctions are hidden; links go to eBay (affiliate links when a campaign id is set). **Not handled:** retention/deletion of stored eBay data, attribution/branding rules, and the current agreement's exact refresh figure (the 6 h number was not re-confirmed in today's search) | VERIFIED (code + tests) for what exists; agreement terms NOT TESTED |

## Setup checklist (owner)

Each step says what it proves. Steps 1–3 cost $0 and need no approval.

1. **Developer account.** Sign up at developer.ebay.com and read the API
   License Agreement before accepting it — especially data storage,
   deletion, refresh and display rules. *Proves nothing yet.*
2. **Sandbox keyset → integration check (fake data).** Create a Sandbox
   keyset. On your own machine, keep the values in a private file outside
   the repository, for example `~/.config/flipfirst/ebay-sandbox.env`
   (`chmod 600`), containing `EBAY_CLIENT_ID=…`, `EBAY_CLIENT_SECRET=…`,
   `EBAY_ENVIRONMENT=sandbox`. Then, in the repository:
   ```bash
   set -a; . ~/.config/flipfirst/ebay-sandbox.env; set +a
   ```
   ```bash
   python -m validate_deals run --sources ebay --zip 35401 --radius 150
   ```
   Expect a `NOT REAL MARKET DATA` headline: exit code 3 if the Sandbox
   returned listings, 1 if it returned none (its test inventory is sparse).
   Either way `report.json` → `sources` → request outcomes show whether
   authentication and the search call succeeded. *Proves: credentials,
   OAuth and request shape work. Says nothing about real listings.*
3. **eBay Partner Network.** Join EPN and create a campaign; its numeric id
   becomes `EBAY_EPN_CAMPAIGN_ID`.
4. **Apply for Buy API production access** through EPN. Describe the
   business exactly; a vague description risks a later revocation:
   > FlipFirst is a subscription web app for people who buy used cars to
   > resell, starting with one territory (Tuscaloosa, AL, 150-mile radius,
   > $2,000–$15,000). It searches eBay Motors Cars & Trucks local-pickup
   > listings with the Browse API every 5 minutes, estimates each car's
   > value from the asking prices of comparable current listings, and shows
   > matching listings to subscribers with a link to the eBay listing
   > (affiliate links via our EPN campaign) and optional alerts. We store
   > listing title, price, location and URL to detect price changes, hide
   > any listing not re-confirmed within your refresh window, and delete
   > stored data within the period your agreement requires.

   Ask in writing: (a) is storing listing data and price history for N days
   permitted, and for how long; (b) is estimating values from asking prices
   permitted; (c) the refresh/display window; (d) attribution and branding
   rules; (e) whether showing listings to paying subscribers is acceptable.
5. **Production keyset activation.** Either subscribe to
   marketplace-account-deletion notifications (needs a public HTTPS
   endpoint and a verification token — **FlipFirst does not have this
   endpoint yet**) or request the exemption, which eBay offers to apps that
   keep no eBay user data. FlipFirst stores listing data but no eBay user
   accounts; confirm with eBay which applies.
6. **After approval:** follow eBay's instructions. Community posts (not
   official) describe a Developer Support ticket titled
   "Buy API Production Access (eBay user ID)" after EPN approval. Then sign
   the contracts eBay sends.
7. **Configure the server** (as root): `nano /etc/sniper/env` and set
   `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (production), `EBAY_EPN_CAMPAIGN_ID`,
   and `EBAY_COMMERCIAL_AGREEMENT` = a reference to eBay's approval (date and
   ticket or contract number — not a secret). Leave `EBAY_ENVIRONMENT`
   empty. Do not set `EBAY_COMMERCIAL_AGREEMENT` before approval: it is what
   switches eBay on in commercial mode.
8. **First real run — isolated, nothing reaches users:**
   ```bash
   systemd-run --pipe --wait --quiet --collect --uid=sniper -p EnvironmentFile=/etc/sniper/env -p WorkingDirectory=/opt/sniper/current /opt/sniper/.venv/bin/python3 -m validate_deals run --sources ebay --zip 35401 --radius 150 --out /opt/sniper/shared/validation/run-1
   ```
   Exit code 0 and `REAL listings from production provider endpoints` is
   the first verified live ingestion. Anything else is not.
9. **Review**, as described in `EVALUATION.md` § Real-deal validation, then
   `python -m validate_deals purge /opt/sniper/shared/validation/run-1`.
10. **Before customers see eBay data:** a retention purge matching the
    agreement (engineering, after step 4's answers), the account-deletion
    decision (step 5), and the pilot acceptance criteria in `EVALUATION.md`.

`python -m verify_sources --sources ebay` (one search request, stores
nothing, prints aggregates) is the cheapest health check once production
access is granted; `live-source-check` runs it from GitHub Actions.

## Sources (search excerpts, 2026-10-30)

- [Buy APIs requirements](https://www.developer.ebay.com/api-docs/buy/buy-requirements.html) — limited release; production for eBay partners; apply through EPN; no guarantee
- [Browse API overview](https://developer.ebay.com/api-docs/buy/browse/overview.html) — Sandbox open to all; production needs eligibility, approvals and contracts
- [Buy API call limits](https://developer.ebay.com/develop/api/buy/api_call_limits) · [Application Growth Check](https://developer.ebay.com/api-docs/static/gs_request-an-application-growth.html)
- [Marketplace account deletion](https://www.developer.ebay.com/marketplace-account-deletion) — subscribe or opt out before the first production call
- [API License Agreement](https://developer.ebay.com/join/api-license-agreement) — read in full before accepting
- Community, not authoritative: [Buy API production access ticket](https://community.ebay.com/t5/eBay-APIs-Talk-to-your-fellow/Trying-to-access-some-of-the-end-points-and-getting-permission/m-p/34345698) · [keyset disabled as non-compliant](https://community.ebay.com/forum/ebay-developers-program-57950/topic/new-keyset-disabled-as-non-compliant-requesting-exemption-474102/)
