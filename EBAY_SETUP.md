# eBay Motors: application package and setup

Researched 2026-10-30 from **search-engine excerpts** of eBay's developer
pages. developer.ebay.com is blocked from the build environment, so no page
was read in full, and the excerpts may be from an older cached copy of the
API License Agreement (eBay revised it in June and September 2025).
Everything marked *excerpt* must be checked against the live text before you
rely on it. Engineering research, not legal advice. Never paste a key,
secret or token into chat, an issue or Git.

## Read this first: price estimates need eBay's written permission

The API License Agreement, as excerpted from developer.ebay.com, lists among
the things developers may not do:

> "Use eBay Content, either alone or in combination with third-party
> information, to suggest or model prices for items listed on eBay Sites."

and requires "eBay's express prior written permission to use or display eBay
Content in any way that enables derivation of" site-wide statistics or
average selling prices (*excerpt*). Pricing and market-trend data are
"Restricted APIs", granted only to select developers (*excerpt*).

FlipFirst's core feature — an estimated market value and "X% below market"
for each listing, computed from comparable asking prices — is exactly a
price model for items listed on eBay. **Buy API production access alone does
not authorize it.** Ask eBay for written permission for this use, in the
application and again in the support ticket. If eBay says no, eBay can at
most supply listings shown *without* a FlipFirst value, which is not the
product; use another path (`DATA_PROVIDER_DECISION.md`).

`EBAY_COMMERCIAL_AGREEMENT` must only ever hold a reference to a written
approval that covers production access **and** this price-estimate use.

## Seven questions, answered

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | Is the integration complete? | For search-based ingestion, yes (OAuth client credentials, `item_summary/search` in Cars & Trucks with local-pickup ZIP + radius, bounded paging, schema-drift check, affiliate links, 6 h display rule, auctions flagged). Missing: deletion of eBay data that is no longer public, a marketplace-account-deletion endpoint, item-detail calls | VERIFIED in code and fixture tests; NOT TESTED against the real API |
| 2 | Which credentials? | `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (production keyset), `EBAY_EPN_CAMPAIGN_ID` (affiliate campaign), `EBAY_COMMERCIAL_AGREEMENT` (your reference to eBay's written approval — not a secret). Sandbox rehearsal: a Sandbox keyset + `EBAY_ENVIRONMENT=sandbox` | VERIFIED (code) |
| 3 | Is access approval needed? | Yes. Buy APIs are limited release; production is for eBay partners: apply through the eBay Partner Network, get approvals and sign contracts; approval is not guaranteed. A production keyset stays disabled until you subscribe to marketplace-account-deletion notifications or are granted an exemption | eBay docs, *excerpts* |
| 4 | Is commercial use authorized? | **No.** It needs (a) Buy API production approval and (b) written permission to show price estimates for eBay items. Neither exists | BLOCKED on eBay |
| 5 | Do the limits support the polling? | Yes: 5,000 Browse calls/day by default; one territory every 5 min with ≤ 2 pages is ≤ 576/day | eBay docs, *excerpt*; usage ESTIMATED from code |
| 6 | Are the fields enough for scoring? | Partly: price, year, make/model and location yes; mileage, trim, cab and drivetrain only when the title states them; no description, title brand or VIN in summaries | VERIFIED (code); real coverage NOT TESTED |
| 7 | Are display restrictions handled? | Displayed listing data may be at most 6 h behind eBay (other eBay content 24 h) — enforced: stale eBay listings are hidden and their detail page returns 410; ended auctions hidden. **Not handled:** deleting eBay content once it is no longer publicly available | agreement *excerpt*; code VERIFIED |

## Obligations found (check each against the live agreement)

| Obligation (as excerpted) | FlipFirst today |
|---|---|
| Delete eBay content from your application when it is no longer publicly available | **Not implemented.** Listings are hidden after 6 h but kept. Must be built before production eBay data reaches the server (`COMPLIANCE.md` C2) |
| Intermediate copies deleted when no longer required (2018 text) | `validate_deals purge` after each review |
| On termination, destroy copies within 10 days; certify on request (older text) | owner process; encrypted backups keep data up to 30 days — ask eBay whether backups count |
| Item listing data displayed ≤ 6 h older than eBay; other eBay content ≤ 24 h | enforced (`DISPLAY_MAX_AGE_HOURS = 6`) |
| No price modeling for eBay items; no derived average prices without written permission | **conflicts with the product** — ask for permission |
| 2025 update: no training AI on eBay content; eBay data in third-party generative-AI tools needs eBay's consent (scope reported as Restricted-API data; unconfirmed) | FlipFirst uses no AI on listing data. **Do not paste real eBay data or run reports into Claude or any AI tool** |
| Subscribe to marketplace-account-deletion notifications or obtain an exemption before the first production call; unacknowledged notifications can disable the keyset | not built; decide after eBay answers (`COMPLIANCE.md` C4) |
| Seller arbitrage prohibited | not applicable (FlipFirst does not reprice eBay listings) |

## Application steps (you do these; nothing is submitted for you)

1. **Developer account** at developer.ebay.com. Read the current API License
   Agreement in full before accepting it — especially the price-modeling,
   storage, deletion, display and AI clauses above.
2. **Sandbox keyset** → rehearsal with test data (`FIRST_LIVE_TEST.md`
   § 2). Proves the keys and request shape only.
3. **eBay Partner Network**: join (short online application; EPN may accept
   or reject at its discretion), create a campaign, note its numeric id.
   EPN usually asks for the website where links will appear — have a URL
   that describes FlipFirst honestly (a simple page is enough; it need not be
   the app).
4. **Request Buy API production access** for the Browse API through EPN,
   using the description below. Include the permission request verbatim.
5. **Follow eBay's instructions.** Community posts (not official) describe a
   Developer Support ticket titled "Buy API Production Access (eBay user
   ID)" after EPN approval. Sign the contracts eBay sends only if they cover
   the price-estimate use; keep a copy.
6. **Keyset activation**: subscribe to account-deletion notifications or
   apply for the exemption, whichever eBay confirms applies to an app that
   stores listing data but no eBay user accounts.
7. **Record the approval**: `EBAY_COMMERCIAL_AGREEMENT` = date + ticket or
   contract number (`/etc/sniper/env`, or your private env file).
8. **First live test**, isolated: `FIRST_LIVE_TEST.md` § 3.
9. **Before customers see eBay data**: the deletion rule (C2) and the
   account-deletion decision (C4) built and reviewed.

## Ready-to-submit business description

> **FlipFirst** (pilot) is a web app for individuals who buy used cars to
> resell. Our first territory is Tuscaloosa, Alabama (150-mile radius,
> $2,000–$15,000 vehicles). During the pilot, up to 10 invited users try it
> for free; later we plan a monthly subscription.
>
> **How we would use the Browse API.** One server-side query of
> `item_summary/search` in Cars & Trucks (category 6001), filtered to local
> pickup near ZIP 35401 within 150 miles and our price band, every 5 minutes
> (at most about 600 calls a day). Users never call eBay through us. We show
> each listing's title, price, location and a link to the eBay listing
> (with our EPN campaign), and we hide any listing not re-confirmed within
> six hours.
>
> **Price estimates — permission requested.** Our product estimates a
> market-value range for each listing from the asking prices of comparable
> current listings and shows how far the asking price is from that range.
> We understand the API License Agreement prohibits using eBay content to
> suggest or model prices for items listed on eBay without eBay's written
> permission. **We request written permission for this use**, or eBay's
> guidance on an approved alternative.
>
> **Storage.** Title, price, location, item URL, first/last-seen times and
> price changes, kept only while needed to detect new listings and price
> drops, deleted when a listing is no longer publicly available or as your
> agreement requires. No eBay user data is stored. We do not resell eBay
> data, train AI models on it, or put it into generative-AI tools.

Questions to ask in writing:
1. Is the price-estimate use above permitted, and on what terms?
2. How long may listing data and price history be stored, and does that
   include encrypted backups?
3. Does our app qualify for the account-deletion exemption, given that it
   stores listing data but no eBay user accounts?
4. Attribution, logo and wording requirements on our pages and alerts.
5. Is showing eBay listings to paying subscribers acceptable?

## Owner checklist

- [ ] Read the live API License Agreement (price-model, storage, deletion,
      display, AI clauses)
- [ ] Developer account + Sandbox keyset; Sandbox rehearsal passed
- [ ] EPN account and campaign (`EBAY_EPN_CAMPAIGN_ID`)
- [ ] Production-access request sent, with the permission request
- [ ] Written answers to the five questions saved
- [ ] Keyset activated (notifications or exemption)
- [ ] `EBAY_COMMERCIAL_AGREEMENT` set only after written approval covering
      price estimates
- [ ] First live test run and purged; reviewed by you, not by an AI tool

## Sources (search excerpts, 2026-10-30)

- [API License Agreement](https://developer.ebay.com/join/api-license-agreement) — price-model prohibition, derived-statistics permission, Restricted APIs, refresh windows, deletion when no longer public
- Older agreement PDFs: [2017](https://developer.ebay.com/cms/files/api_license_2017-05-25_final.pdf) · [2018](https://developer.ebay.com/cms/files/api_license_2018-10-26.pdf) — intermediate copies, destruction on termination
- [EcommerceBytes: eBay restricts developers from using its data to train AI (July 2025)](https://www.ecommercebytes.com/2025/07/18/ebay-restricts-developers-from-using-its-data-to-train-ai/)
- [Buy APIs requirements](https://www.developer.ebay.com/api-docs/buy/buy-requirements.html) · [Browse API overview](https://developer.ebay.com/api-docs/buy/browse/overview.html) · [Buy API call limits](https://developer.ebay.com/develop/api/buy/api_call_limits) · [Application Growth Check](https://developer.ebay.com/api-docs/static/gs_request-an-application-growth.html)
- [Marketplace account deletion](https://www.developer.ebay.com/marketplace-account-deletion) · [Hookdeck guide to eBay notifications (third party)](https://hookdeck.com/webhooks/platforms/guide-to-ebay-webhooks-features-and-best-practices)
- [Joining the eBay Partner Network](https://partnernetwork.ebay.com/solutions/joining-the-ebay-partner-network) · [EPN Network Agreement](https://epnpartnernetwork.ebay.com/page/network-agreement)
- Community, not authoritative: [Buy API production access ticket](https://community.ebay.com/t5/eBay-APIs-Talk-to-your-fellow/Trying-to-access-some-of-the-end-points-and-getting-permission/m-p/34345698) · [keyset disabled as non-compliant](https://community.ebay.com/forum/ebay-developers-program-57950/topic/new-keyset-disabled-as-non-compliant-requesting-exemption-474102/)
