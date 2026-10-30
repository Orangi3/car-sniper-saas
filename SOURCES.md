# Data sources: coverage, verification and licensing

> **Live verification:** `python -m verify_sources` (or the manual
> `live-source-check` workflow) makes at most one search request per
> licensed API source, stores nothing and reports aggregates only — status,
> listing count, field coverage, schema problems. Exit 0 = verified.

Last reviewed: 2026-10-30. **This is an engineering audit, not legal advice.**
Before charging customers, have counsel confirm the licensing column and get
written permission or a commercial license wherever it says so.

A source file existing in `sources/` is **not** proof of working coverage.
"Verified live" means a real request returned real listings. **No source has
ever been verified live**: the build environment's network policy blocks the
providers, and no provider credentials exist yet. Tests that pass against
fixtures prove parsing and pipeline behavior only.

## Readiness matrix

Classes: **VERIFIED LIVE** · **IMPLEMENTED — NOT LIVE-VERIFIED** ·
**REQUIRES CREDENTIALS** · **REQUIRES COMMERCIAL AUTHORIZATION** ·
**UNAVAILABLE** (not usable for a paid product, or not built). Where several
apply, all are listed; the first is what blocks it today.

| Source | Adapter | Auth / credentials | Commercial licensing (code gate) | Tests | Live ingestion shown? | Classification |
|---|---|---|---|---|---|---|
| eBay Motors — Browse API `item_summary/search` | `ebay_motors.py` — complete: OAuth client credentials + token refresh, pickup ZIP/radius filters, bounded paging, schema-drift check, affiliate links, 6 h display rule, Buy-API permission error explained | `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` (none exist yet) | `requires_agreement` → off in commercial mode until `EBAY_COMMERCIAL_AGREEMENT` records eBay's written approval, which must cover production access (EPN approval + contracts) **and** price estimates — the licence bars modeling prices of eBay items without permission (`EBAY_SETUP.md`) | fixture tests: `test_ebay_api`, `test_e2e_pipeline`, `test_source_schema`, `test_false_positives`, `test_validate_deals` | **No** | REQUIRES COMMERCIAL AUTHORIZATION · REQUIRES CREDENTIALS · IMPLEMENTED — NOT LIVE-VERIFIED |
| MarketCheck private sellers (FSBO) `/v2/search/car/fsbo/active` | `marketcheck_fsbo.py` — complete per published docs: ZIP/radius, newest first, bounded paging, schema check, paid-call budget (`provider_budget.py`) | `MARKETCHECK_API_KEY` (paid plan) | `requires_agreement` → off until `MARKETCHECK_COMMERCIAL_AGREEMENT` | `test_marketcheck_fsbo`, `test_source_schema` | **No** | REQUIRES COMMERCIAL AUTHORIZATION · REQUIRES CREDENTIALS · IMPLEMENTED — NOT LIVE-VERIFIED |
| MarketCheck dealer inventory | `marketcheck.py` — dealer asking prices (comparables, not deals) | `MARKETCHECK_API_KEY` | `requires_agreement` | licensing gate only | **No** | REQUIRES COMMERCIAL AUTHORIZATION · REQUIRES CREDENTIALS |
| FlipFirst seller listings (`direct`) | `seller_listings.py` + `/m` Sell flow — complete: form, email confirmation, lifecycle, removal, privacy | SMTP for confirmation email | own seller terms with consent; off until `SNIPER_SELLER_SUBMISSIONS=1` | `test_seller_listings` + browser flows | **No** (no real seller yet) | IMPLEMENTED — NOT LIVE-VERIFIED |
| Craigslist (by-owner RSS + search) | `craigslist.py` | none | `terms_prohibit_automation` | `test_source_health` | historically, personal use | UNAVAILABLE (terms) |
| Bring a Trailer RSS | `bring_a_trailer.py` | none | `unverified` | none | No | UNAVAILABLE (permission not sought; collector auctions, off-niche) |
| Hemmings RSS | `hemmings.py` | none | `unverified` | none | No | UNAVAILABLE (permission not sought; off-niche) |
| GovDeals RSS | `govdeals.py` (code notes feed URLs may be retired) | none | `unverified` | none | No | UNAVAILABLE (permission not sought; auctions = current bids) |
| Browser-extension imports (FB Marketplace, OfferUp, …) | `marketplace_import.py` | owner's own browser | `personal_use_only` | none | owner's local use only | UNAVAILABLE (personal data, platform terms) |
| Email digests (IMAP) | `email_imap.py` | owner's mailbox | `personal_use_only` | none | No | UNAVAILABLE (personal data) |
| Cars & Bids, Copart, IAA, GSA Auctions, PublicSurplus | empty stubs | — | — | — | — | UNAVAILABLE (not built) |

### What each usable candidate delivers

| | eBay Browse API | MarketCheck FSBO | Seller listings |
|---|---|---|---|
| Fields | item id, title, price (USD), fixed price or current auction bid + Buy It Now, item city/state/ZIP, pickup distance, created/end dates, condition + id, seller account type (absent on EBAY_US → "unknown"), feedback %, image, URL. **Year/make/model/mileage parsed from the title**; no description in summaries (labeled `description_unchecked`), no VIN, no title brand | price, year/make/model, miles, location, source marketplace + listing URL (per docs); no seller contact | everything the form asks: VIN, miles, title status, photos, description, ZIP |
| Enough for scoring? | Partly: price, year, model and location yes; mileage only when the title states it; trim/cab/drivetrain from the title only; damage unscreened | Yes on paper (structured fields); unverified | Yes |
| Rate limit | 5,000 Browse calls/day default; Application Growth Check for more (eBay docs). Our use: 1 territory every 5 min, ≤ 2 pages → ≤ 576 calls/day | plan-based; vendor pages conflict (500 vs 5,000 calls/month; 5 calls/s) | none |
| Freshness | newest-first search each poll; listings not re-confirmed within 6 h are hidden (rule in code; the 6 h figure was **not re-confirmed** in eBay's current agreement text) | "near real-time" vs "every 24 h" (vendor pages conflict) | instant |
| Cost | no API fee published; affiliate (EPN) program | $299/mo base + per-call fee ($0.01 or $0.006 — vendor pages conflict); a lower tier may cap the radius at 100 mi | $0 (email on a free tier) |
| Main risks | approval may be refused or slow; business model must be disclosed (valuation/deal scoring); keyset needs marketplace-account-deletion notifications or an exemption; title-only data → misparses; auctions | Terms restrict storage/caching; FSBO provenance (which marketplaces) unknown; cost before any revenue | cold start: no supply until sellers come |

## Sold-price (comparable) feeds — `comps.py`

| Feed | Method | Evidence class it can produce | Licensing class | Commercial mode |
|---|---|---|---|---|
| eBay sold listings | HTML scrape of search pages | confirmed_sales (dated) | `terms_prohibit_automation` (eBay user agreement bans scrapers) | **Off** |
| BaT / Cars & Bids / PCarMarket results | HTML scrape | confirmed_sales (dated) | `unverified` | **Off** |
| Hemmings "sold" | HTML scrape | historical_incomplete (last asking price) | `unverified` | **Off** |
| Copart | JSON endpoint | salvage floor only (not used for value) | `unverified` | **Off** |
| MarketCheck `/sales` | Licensed API | historical_incomplete (last asking price of delisted cars) | `requires_agreement` | Off until agreement recorded |

See `DATA_PROVIDER_DECISION.md` for the provider comparison.

Implication: in commercial mode there is **no confirmed-sales feed** today.
Valuations fall back to asking-price comparables (clearly labeled as such)
until a licensed sold-price feed is contracted.

## Why the classifications

- **Craigslist** terms prohibit collecting content with automated tools and
  reusing it commercially without a license; Craigslist has enforced this
  against a commercial car-listing service (Instamotor: permanent injunction
  and a reported $31M settlement, 2017).
- **eBay** user agreement prohibits robots, scrapers and data-mining tools
  without permission; the official Browse API is the sanctioned route. Its
  production use is not click-through: eBay's Buy API requirements say
  production access is for eBay partners — apply through the eBay Partner
  Network, get eBay's approval and sign its contracts, with no guarantee of
  approval. Sandbox keys return test data only. The eBay API License
  Agreement's display, storage and deletion rules apply (review before
  launch; not re-read in full here because developer.ebay.com is blocked
  from this environment).
- **RSS feeds (BaT, Hemmings, GovDeals)**: being publicly readable is not a
  license to republish in a paid product. Permission not confirmed.
- **Personal data paths (extension, IMAP)**: these read the *owner's* own
  logged-in browsing and private inbox. Redistributing that content to other
  paying subscribers isn't covered by the platforms' terms (Facebook prohibits
  automated collection) and mixes one person's private data into a shared
  feed. They remain available for the owner's personal, local use.

## Commercial mode

`SNIPER_COMMERCIAL_MODE=1` (required for any paid deployment) turns off every
listing source and comp feed whose class isn't `licensed_api`.
`requires_agreement` sources stay off until the owner records the approval:
eBay with `EBAY_COMMERCIAL_AGREEMENT` (eBay's Buy API production approval,
`EBAY_SETUP.md`), MarketCheck with `MARKETCHECK_COMMERCIAL_AGREEMENT` set to
the reference of a **signed** agreement
that covers central polling, storage of listings/price history and pushed
alerts to paying subscribers (MarketCheck's public Terms restrict persistent
datasets and caching outside "normal application use"). Every paid MarketCheck
call is counted against a hard monthly budget (`provider_budget.py`). Disabled
sources show as `disabled` with the licensing reason in `/api/diagnostics`.
Personal local use (`SNIPER_COMMERCIAL_MODE` unset) behaves as before.

## Adding an authorized source (adapter interface)

Drop `sources/<name>.py`; it is auto-discovered.

```python
SOURCE_ID = "acme_feed"            # short slug, unique
SOURCE_NAME = "Acme Dealer Feed"
LICENSING = "licensed_api"         # licensed_api | requires_agreement | unverified |
                                   # terms_prohibit_automation | personal_use_only

def enabled(cfg) -> bool:          # user toggle only
    return cfg.get("sources", {}).get(SOURCE_ID, {}).get("enabled", True)

def config_issue(cfg) -> str | None:   # missing credentials → "auth_config"
    return None if os.environ.get("ACME_KEY") else "ACME_KEY not set"

def poll(cfg) -> list[NormalizedListing]:
    rep = health.report(SOURCE_ID)          # REQUIRED: honest health
    r = rep.fetch("listings", URL, timeout=15, headers=...)
    if r is None:
        return []                           # outcome already recorded
    items = parse(r.json())
    rep.parsed("listings", len(items))      # or rep.malformed(...)
    return [NormalizedListing(...) for ...]
```

Rules: record every request through `health` (never swallow errors), return
the asking price in `price` (never a payment figure), leave unknown fields
`None` (never guess), and set `DEFAULT_MIN_INTERVAL_S[SOURCE_ID]` in
`sniper.py` to respect the provider's rate limits. Ingestion, dedupe,
price history, valuation and alerting need no changes.

## Sources consulted

- [craigslist terms of use](https://www.craigslist.org/about/terms.of.use/en)
- [Proskauer: Craigslist reaches $31M settlement with Instamotor](https://newmedialaw.proskauer.com/2017/08/24/ending-data-scraping-dispute-craigslist-reaches-31m-settlement-with-instamotor/)
- [Justia: Craigslist v. Instamotor stipulated judgment](https://cases.justia.com/federal/district-courts/california/candce/3:2017cv02449/310967/28/0.pdf)
- [eBay User Agreement](https://www.ebay.com/help/policies/member-behaviour-policies/user-agreement?id=5414)
- [eBay Buy APIs requirements](https://www.developer.ebay.com/api-docs/buy/buy-requirements.html) · [Browse API overview](https://developer.ebay.com/api-docs/buy/browse/overview.html) · [API call limits](https://developer.ebay.com/develop/api/buy/api_call_limits) · [Application Growth Check](https://developer.ebay.com/api-docs/static/gs_request-an-application-growth.html) · [Marketplace account deletion](https://www.developer.ebay.com/marketplace-account-deletion) (search excerpts, 2026-10-30)
- [MarketCheck pricing](https://marketcheck.com/pricing) · [Private Party Listing docs](https://docs.marketcheck.com/docs/api/cars/vehicle-listing/private-party-listing)
- [eBay.ca User Agreement (scraper clause)](https://www.ebay.ca/help/policies/member-behaviour-policies/ebayca-user-agreement?id=4259)
