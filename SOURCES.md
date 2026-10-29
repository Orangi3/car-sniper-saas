# Data sources: coverage, verification and licensing

> **Live verification:** `python -m verify_sources` (or the manual
> `live-source-check` workflow) makes at most one search request per
> licensed API source, stores nothing and reports aggregates only — status,
> listing count, field coverage, schema problems. Exit 0 = verified.

Last reviewed: 2026-10-28. **This is an engineering audit, not legal advice.**
Before charging customers, have counsel confirm the licensing column and get
written permission or a commercial license wherever it says so.

A source file existing in `sources/` is **not** proof of working coverage.
"Verified" below means a real request returned real listings during this
review. This review ran in a sandbox whose network policy blocks every
listing site, so **no source could be live-verified here** — live checks must
be repeated from the production host (`python sniper.py poll`, then
`/api/diagnostics`).

## Listing sources

| Source | Module | Implementation status | Live-verified here | Licensing class | Commercial mode |
|---|---|---|---|---|---|
| Craigslist (by-owner RSS + search page) | `craigslist.py` | Implemented | No — sandbox network blocked (reported `blocked`) | `terms_prohibit_automation` | **Off** |
| Bring a Trailer RSS | `bring_a_trailer.py` | Implemented | No — blocked | `unverified` | **Off** |
| Hemmings RSS | `hemmings.py` | Implemented | No — blocked | `unverified` | **Off** |
| GovDeals RSS | `govdeals.py` | Implemented; code notes feed URLs may be deprecated | No — blocked | `unverified` | **Off** |
| eBay Motors (Browse API) | `ebay_motors.py` | Implemented; geography now uses the Browse pickup filters (it previously sent the Finding-API `buyerPostalCode`, which Browse ignores) | No — no credentials | `licensed_api` (click-through API License Agreement: ≤6 h display freshness, delete within 30 days) | On (needs `EBAY_CLIENT_ID`/`SECRET`) |
| **MarketCheck private sellers (FSBO)** | `marketcheck_fsbo.py` | Implemented against documented `/v2/search/car/fsbo/active`; fixture-tested only | No — no credentials | `requires_agreement` | **Off until** `MARKETCHECK_COMMERCIAL_AGREEMENT` set |
| MarketCheck dealer inventory (retail asking prices) | `marketcheck.py` | Implemented | No — no credentials | `requires_agreement` | **Off until** `MARKETCHECK_COMMERCIAL_AGREEMENT` set |
| Browser-extension imports (FB Marketplace, OfferUp, Nextdoor, Cars.com, Autotrader, CarGurus, C&B) | `marketplace_import.py` + `marketplace_extension/` | Implemented | Not exercised | `personal_use_only` | **Off** |
| Email digests (owner's inbox via IMAP) | `email_imap.py` | Implemented | No — no credentials | `personal_use_only` | **Off** |
| Cars & Bids, Copart, IAA, GSA Auctions, PublicSurplus | `cars_and_bids.py` etc. | **Stub only** (empty files) | — | — | — |

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
  without permission; the official Browse API is the sanctioned route and is
  governed by the eBay API License Agreement (display, storage and refresh
  rules apply — review before launch).
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
`requires_agreement` sources (MarketCheck) stay off until the owner sets
`MARKETCHECK_COMMERCIAL_AGREEMENT` to the reference of a **signed** agreement
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
- [eBay.ca User Agreement (scraper clause)](https://www.ebay.ca/help/policies/member-behaviour-policies/ebayca-user-agreement?id=4259)
