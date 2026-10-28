# Data provider decision

Researched 2026-10-28 by web search. **Direct page fetches to vendor sites
(developers.marketcheck.com, docs.marketcheck.com) were blocked by this
build environment's network policy**, so everything below comes from search
results quoting vendor pages. Nothing here was confirmed with a live API
call or with the vendor. Engineering research, not legal advice.

## Decision

**Primary: MarketCheck Private Party (FSBO) API — conditional on a written
agreement that permits Car Sniper's storage-and-alert model.**
**Secondary: eBay Browse API (free, already integrated; geography fixed in
this branch) for auction/BIN vehicles.**

MarketCheck FSBO is the only candidate found that offers documented,
API-delivered *private-party* car listings with price, vehicle and source
URL. Its public Terms, however, appear to restrict exactly what an alert
product does (persisting data, caching outside "normal application use",
use "tied to a live query or user-driven interaction"). Until MarketCheck
confirms in writing that central polling, storing listings + price history,
and pushing alerts to paying subscribers are permitted, the adapter is
**fail-closed** in commercial mode.

## Comparison

| Provider | Private-party coverage | Geography | Freshness (claimed) | Useful fields | Price | Quota | Commercial redistribution | Contract | Verification | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|
| **MarketCheck FSBO** `GET /v2/search/car/fsbo/active`, detail `GET /v2/listing/car/fsbo/{id}` | Yes — private sellers only (`seller_type` fsbo); "over 80,000 active listings nationwide"; source marketplaces not named; vendor pages conflict on whether classifieds are crawled | US nationwide; zip/radius filters (same as Inventory Search) | "near real-time" (docs) vs "refreshed every 24h" (older vendor article) — **unresolved** | price, year/make/model, miles, location, source marketplace + listing URL; **no seller contact** | Basic **$299/mo** + data fees; Private Party Search listed at **$0.01/call** | Basic: 5,000 calls/mo, 5 calls/s, 500-row pagination limit; free tier 500 calls (third-party APIs need Basic+) | Public ToS: licensed not sold; no persistent datasets outside the Service; no caching "substantial portions … outside normal application use"; display within your app tied to live query/user interaction. **Our use needs explicit written permission.** | Order form / sales agreement; terms may differ by plan | Docs found via search; not live-tested; ToS read via search excerpts | **Primary candidate — pursue written agreement + pilot pricing.** Adapter built, fail-closed. |
| MarketCheck dealer inventory `/v2/search/car/active` | No (dealers) | US | as above | full dealer listing | $0.002/call + plan | as above | same ToS | same | as above | Useful only for dealer *asking-price comparables*, labeled retail. Not a deal source for our niche. |
| **eBay Browse API** `item_summary/search` | Some (eBay sellers incl. private); auctions + BIN; not classifieds | US; pickup filters `pickupPostalCode`/`pickupRadius`/`pickupCountry`/`pickupRadiusUnit` | Displayed listings must be ≤ 6 h behind eBay (API License Agreement) | title, price, item location, image, item URL; year/make/model parsed or from aspects | Free | Default app call limits (not verified for our volume) | API License Agreement: display freshness, delete data ≤ 30 days after no longer needed, security controls, no eBay data in non-prod | Developer program agreement (click-through) | Category 6001 = Cars & Trucks confirmed; **vehicle search via Browse with 6001 not live-tested**; marketplace header for Motors unconfirmed | **Keep as secondary.** Low cost; fix geo params (done); verify with real keys. |
| Craigslist | Large private-party supply | Local | Real-time | Rich | — | — | Terms prohibit automated collection; enforced (Instamotor injunction, reported $31M) | Written license required | — | **Do not use** without a license. |
| BaT / Hemmings / GovDeals RSS | Few private-party daily drivers (enthusiast / gov fleet) | National / state | RSS | Partial | — | — | Permission unverified | Ask each | — | Low fit for $2k–$15k niche; skip for paid launch. |
| Vehicle Acquisition Network (VAN) | Aggregates FSBO from "25+ marketplaces" (vendor listing) | US (unverified) | Unknown | Unknown | Not public | Unknown | Unknown — dealer-acquisition product | Sales contract | Only a directory listing found | **Ask for quote** as a MarketCheck alternative; provenance of their FSBO data must be checked. |
| VinAudit / NMVTIS providers | No listings | US | — | title brand, salvage, theft history | Per report | — | Per contract | Contract | Directory listing | Later: per-deal title checks (Pro add-on), not a listing feed. |
| Seller-submitted listings / partnerships | Whatever we attract | Local | Instant | Whatever we collect | Our cost | — | We own it (with seller consent) | Our own seller terms | — | Long-term moat; not a launch source (cold start). |

## What remains unverified (ask MarketCheck)

1. Whether storing FSBO listings and price history, polling centrally, and
   sending alerts to paying subscribers is permitted — and for how long data
   may be retained.
2. Which marketplaces feed FSBO (classifieds vs. dealer-adjacent), and how
   many active listings exist within 100 mi of our launch ZIPs.
3. Actual refresh latency (near-real-time vs 24 h) and whether price changes
   produce updated records.
4. Whether `first_seen_at`/`last_seen_at`-style fields exist on FSBO results
   (needed to sort by newest and detect drops cheaply).
5. Attribution/display rules for the listing URL and photos (cached images
   documented at ≤ 4 h private caching).
6. Startup / pilot pricing below $299/mo, or a free-tier evaluation that
   includes the FSBO endpoint (free tier reportedly excludes third-party APIs).

Draft inquiry: `OWNER_ACTIONS.md` § 1.

## Polling budget (central, not per subscriber)

One territory = one `zip` + `radius` query sorted newest first, 50 rows/call.

| Poll interval | Calls / territory / month | Data fee @ $0.01 | Fits Basic 5,000 calls? |
|---|---|---|---|
| 60 min | 720 | $7.20 | yes (6 territories) |
| 30 min | 1,440 | $14.40 | yes (3 territories) |
| 15 min | 2,880 | $28.80 | 1 territory |

Subscribers never trigger provider calls: matching runs against the shared
ingested data. Cost scales with **territories × poll frequency**, not users.
(Pagination beyond the first page only when the newest page is all new.)

## Sources

- [MarketCheck Private Party Search docs](https://docs.marketcheck.com/docs/api/cars/inventory/private-party-search)
- [MarketCheck Private Party Listing Details](https://docs.marketcheck.com/docs/api/cars/vehicle-listing/private-party-listing)
- [MarketCheck pricing](https://www.marketcheck.com/pricing) · [API pricing](https://www.marketcheck.com/apis/pricing/)
- [MarketCheck quota and rate limits](https://docs.marketcheck.com/docs/get-started/api/quota-and-rate-limits) · [rate-limit headers](https://docs.marketcheck.com/docs/get-started/api/rate-limit-headers)
- [MarketCheck Terms of Service](https://www.marketcheck.com/terms_of_service/)
- [MarketCheck car inventory data overview](https://docs.marketcheck.com/docs/guides/data/cars/inventory/overview) · [Private party data feed](https://www.marketcheck.com/data_feed/private-party/)
- [eBay Browse API search](https://developers.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search) · [eBay Motors categories](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/ebay-motors-categories.html)
- [eBay API License Agreement](https://developer.ebay.com/join/api-license-agreement)
- [MarketCheck alternatives directory (VAN, VinAudit)](https://sourceforge.net/software/product/MarketCheck/alternatives)
