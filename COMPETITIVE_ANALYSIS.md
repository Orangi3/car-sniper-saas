# Competitive analysis

Researched 2026-10-28 by web search. Competitor facts come mostly from their
own app-store pages and marketing, or from comparison pages written by
competitors; treat claims about speed and "AI" as **unverified**. Prices
conflict between sources and change often — check the store listing before
quoting them. No competitor product was used hands-on.

## Who else sells car-deal alerts

| Product | What it does (claimed) | Sources watched (claimed) | Price (reported) | Valuation / profit? | Notes |
|---|---|---|---|---|---|
| **Swoopa** | Instant alerts on new marketplace listings; vehicle searches by make/model/year/price; spam filtering; "AI" filtering; dealer workflow app | Facebook Marketplace, Craigslist, OfferUp, eBay, Nextdoor, others | Go plan ~$47/mo monthly or ~$28/mo annual; Turbo/Nitro higher; price-drop alerts on Nitro (third-party comparison, Sep 2026); 48 h or 7-day trial (sources conflict) | No transparent valuation found | Market leader in speed-of-alert positioning; a separate **Swoopa Dealers** app targets dealerships |
| **Flipify** | Marketplace alerts with a car-flipper section | Facebook Marketplace, Craigslist | Free with in-app purchases (amounts not found) | Not found | Consumer/reseller focus |
| **CarFlipperAI** | Telegram bot: "real-time alerts for undervalued cars" | Facebook Marketplace, Craigslist, "more" | Listed as free (TrustMRR, Apr 2026) | Claims "undervalued", method not published | Launched Mar 2026 on Product Hunt |
| **Flipsentry** | Deal finder for resellers and car flippers; multiple alerts, custom alert speeds | Not stated | Not found | Not found | — |
| **Facebook saved searches** | Native notifications | Facebook Marketplace | Free | No | Described (by competitors) as sporadic |
| Dealer acquisition tools (e.g. vAuto/Provision, ACV, Vehicle Acquisition Network) | Inventory sourcing and appraisal for dealers | Dealer, auction and aggregated FSBO feeds | Enterprise contracts | Yes (appraisal) | Different buyer: dealerships, $100s–$1,000s/mo. Not researched in depth. |

"Be Sure" (named in the original request) was not found by search; if it is
a specific app, its exact name or store link is needed to include it.

## What the competition implies

1. **Speed on Facebook Marketplace is the category's main promise — and
   Car Sniper cannot match it lawfully.** Most competitors advertise
   Facebook Marketplace and Craigslist monitoring. Car Sniper treats both as
   not licensed for a paid multi-user product (`SOURCES.md`). Competing on
   "fastest Marketplace alert" means competing on the riskiest ground.
2. **Nobody found publishes how they value a car.** "Undervalued" and "AI"
   are asserted, not explained. Car Sniper already shows evidence class,
   comparables, confidence reasons and profit assumptions per deal.
3. **Price points are $0–$50/month.** Swoopa's ~$28–47/mo anchors the
   serious-flipper segment; free bots anchor the low end. Car Sniper's
   planned $4.99/mo is far below Swoopa — a price advantage only if it
   delivers enough deals in a territory.

## Customer segments

| Segment | Need | Willingness to pay | Fit today |
|---|---|---|---|
| Side-hustle flippers (1–4 cars/month) | Find underpriced private-party cars near them, know the profit | $5–50/mo | **Primary.** Needs private-party data (MarketCheck) |
| Budget car buyers (one purchase) | Not overpay; avoid scams | $0–10 one-off | Good fit for valuation + risk flags; low LTV |
| Small independent dealers | Weekly acquisition volume | $100s/mo | Weak: they need volume and wholesale data; competitors exist |
| Enthusiast buyers (BaT etc.) | Specific models, auction results | Varies | Out of the $2k–$15k niche |

## Differentiation, ranked (implement the top items first)

Ranked by (customer value × defensibility) ÷ effort, considering what is
lawful and already half-built.

| Rank | Differentiator | Why it matters | State | Effort |
|---|---|---|---|---|
| 1 | **Licensed, compliant data** (MarketCheck FSBO, eBay API) | The only sustainable basis for a paid product; competitors scraping Facebook carry platform risk | Adapters + probe built; agreement + keys pending | Owner action |
| 2 | **Transparent valuation with evidence** (segments, comps, confidence, capped when asking-only) | No competitor found explains its numbers; trust is the product | Built (v2 this branch) | Done → measure |
| 3 | **Profit after real costs** (repairs, holding, transport by distance, scenarios, annualized ROI) | Flippers decide on profit, not discount | Built | Done → calibrate |
| 4 | **Measured accuracy** (evaluation harness: precision, valuation error vs sale price, latency) | Lets us publish real numbers no competitor publishes | Built; no real data yet | Needs beta |
| 5 | **Risk flags** (salvage, needs-work, scams, VIN mismatch, too-good-to-be-true) | Avoids the costly bad flip | Built | Low |
| 6 | **Territory-first pricing** ($4.99 in served metros) | Undercuts Swoopa 6–9× where we have density | Modeled | Owner decision |
| 7 | Seller-submitted listings / partnerships | Data we own; long-term moat | Not built | High |
| 8 | Title-history checks per deal (NMVTIS provider) | Pro add-on | Not built | Medium + contract |
| 9 | Dealer workflow features | Different segment; crowded | Not built | High — **not recommended now** |

**Not recommended:** chasing Facebook Marketplace / Craigslist speed
through unlicensed collection, or "AI" claims we cannot measure.

## Sources

- [Swoopa car search tutorial](https://getswoopa.com/car-searches) · [Swoopa: tools to automate Marketplace alerts](https://getswoopa.com/tools-to-automate-facebook-marketplace-alerts) · [Swoopa on the App Store](https://apps.apple.com/gb/app/id6475300269) · [Swoopa Dealers](https://apps.apple.com/app/id6737966575)
- [Swoopa alternatives / pricing comparison (competitor-written)](https://spottable-lp.pages.dev/blog/swoopa-alternatives) · [Best Facebook Marketplace alerts app (competitor-written)](https://spottable-lp.pages.dev/blog/best-facebook-marketplace-alerts-app)
- [Flipify on the App Store](https://apps.apple.com/app/id6504143452) · [Flipify: Facebook Marketplace cars](https://www.flipifyapp.com/facebook-marketplace-cars)
- [CarFlipperAI on TrustMRR](https://trustmrr.com/startup/carflipperai) · [CarFlipper on Product Hunt](https://www.producthunt.com/products/carflipper/launches)
- [Flipsentry on the App Store](https://apps.apple.com/pe/app/flipsentry/id6748539654)
- [Marketplace Monitor (Google Play pricing page)](https://www.apppricinglab.com/app/google_play/com.marketplace.monitormobile)
