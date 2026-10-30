# Owner actions

Things only you can authorize or do. Engineering cannot buy plans, sign
agreements, create live billing, or give legal sign-off for you. Ordered by
what unblocks the most.

## Do first (under 15 minutes each, $0)

| Action | How | Unlocks |
|---|---|---|
| **A. Protect `main`** | GitHub → Settings → Branches → add rule for `main`: require a pull request, require status checks `tests-sqlite`, `tests-postgres`, `browser-smoke`; block force pushes; do not allow bypass | Makes "agents never merge" enforced, not promised (`AGENTS.md`) |
| **B. Send the MarketCheck inquiry (§ 1)** | Email it from your own address; nothing is bought by asking | One written answer can unlock both licensed private-party listings and licensed market values — the two things a real deal needs (`DATA_PROVIDER_DECISION.md`) |
| **B2. Apply to eBay, asking for price-estimate permission** | `EBAY_SETUP.md`: developer account, Sandbox rehearsal (`FIRST_LIVE_TEST.md` § 2), eBay Partner Network campaign, production-access request with the business description and permission request. Never paste keys into chat or Git | eBay's licence bars modeling prices of eBay items without written permission; only that permission makes eBay usable for FlipFirst |
| **C. Same for MarketCheck** (after #3) | add secret `MARKETCHECK_API_KEY`, run the workflow with `marketcheck_fsbo` | Live FSBO field mapping + listing count for the territory (one call ≈ $0.01). The probe stores nothing; it does not replace the written agreement for production use |
| **D. Accept or change the launch-stage thresholds** | Read `CURRENT_STATE.md` §5 and edit the numbers | Fixes the bar before data arrives |
| **E. Email for seller verification** | Step by step (Resend, ~15 min, $0): `FIRST_DEPLOY.md` → Email setup. In general: use a transactional provider that lets you send from your own domain over SMTP (e.g. Amazon SES, Postmark, Resend, Mailgun — compare their current pricing pages yourself; free-tier terms change and are not assumed here). Verify your domain with it (SPF + DKIM DNS records, plus a DMARC record), create SMTP credentials, then put `SMTP_HOST`, `SMTP_PORT=587` (or 465), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM="FlipFirst <no-reply@yourdomain>"` in `/etc/sniper/env` and restart `sniper-web`. A personal mailbox's SMTP only works if `SMTP_FROM` is that same mailbox address. Confirm: Sell → "Email me a confirmation link" arrives in a Gmail and an Outlook inbox (not spam), and the message headers show `spf=pass dkim=pass`. Certificates are always verified; a 503 "couldn't send the email" means check `journalctl -u sniper-web` for the logged reason (never the password) | Seller listings publish only after the seller confirms their email; without SMTP nothing publishes. See `SELLER_PILOT.md` |

Optional: activate agent runs (`AGENTS.md` § Activation) — your own
spend-limited `ANTHROPIC_API_KEY`, `AGENT_MODEL` variable, `agent-go` label.

## Full list

| # | Action | Why it's required | Plan / permission | Expected cost | Unlocks |
|---|---|---|---|---|---|
| 1 | **Send the MarketCheck inquiry below** | Their public Terms restrict persistent datasets and caching outside "normal application use"; our model stores listings, tracks prices and pushes alerts. Only written confirmation makes it usable for a paid product. | Written commercial agreement covering: central polling, storage of listings + price history for N days, alerts to paying subscribers, display of listing details + source link | Unknown until quoted (public Basic is $299/mo + $0.01/FSBO call) | Path to the primary private-party data source |
| 2 | **Approve a data budget** (if MarketCheck says yes) | No licensed private-party data = nothing to alert on | Basic plan or a pilot tier | ~$313/mo base (1 territory, 30-min polling) — see `UNIT_ECONOMICS.md` | Real listings in one launch territory |
| 3 | **Register MarketCheck account; put the key on the server** | The adapter is built but has never seen a live response | API key in `/etc/sniper/env` as `MARKETCHECK_API_KEY`; set `MARKETCHECK_COMMERCIAL_AGREEMENT=<agreement ref>` **only after signing** | as #2 | `marketcheck_fsbo` source; first live verification of the field mapping |
| 4 | **eBay Buy API production approval** (`EBAY_SETUP.md`) | Production use of eBay's Buy APIs needs an eBay Partner Network application, eBay's approval and signed contracts — not a click-through; approval is not guaranteed | Read the API License Agreement before accepting; describe the valuation + alert model in the application and get answers on storage period, refresh window and attribution in writing | $0 API fee (none published) | `ebay` in commercial mode once `EBAY_COMMERCIAL_AGREEMENT` records the approval |
| 5 | **Legal review** of `SOURCES.md`, `DATA_PROVIDER_DECISION.md`, the eBay API License Agreement and the MarketCheck agreement | Licensing is the main launch risk | A lawyer familiar with data licensing / SaaS terms | Varies | Confidence to share data with beta users |
| 6 | **Choose the launch territory** (ZIP + radius) | Data cost is per territory; users outside it get little value | Your decision (e.g. 35401 + 75 mi) | — | Territory-gated beta signups |
| 7 | **Provide/approve a production host** | Paid beta requires PostgreSQL, the worker and scheduler services, backups | VPS (~$24/mo) + Postgres (~$15–30/mo) per `LAUNCH_CHECKLIST.md`; Backblaze B2 for encrypted backups | ~$40–70/mo | Running preflight on real infrastructure |
| 8 | **Authorize a free controlled beta** (25–50 invited users, one territory) | Measure listing density, opportunity rate and alert speed before charging (`EVALUATION.md`) | Your go-ahead after `python -m preflight --target free` passes | Data + hosting above | Real-world evaluation data |
| 9 | **Create live Stripe products** | Charging requires prices you create in your own Stripe account | Recommended: Pro $4.99/mo + $49.99/yr; Lite $29.99/yr (see `UNIT_ECONOMICS.md`) | Stripe fees only | `STRIPE_PRICE_*`, live keys → `python -m preflight --target paid` |
| 10 | **Twilio (optional)** | Pro SMS alerts; push works without it | Twilio account + A2P 10DLC brand/campaign registration | brand $4.50–$46 one-time; campaign ~$1.50–10/mo; ~$0.012/SMS | Pro SMS (capped 30/user/month) |
| 11 | **Authorize production deployment** | Final human sign-off | After #1–#9 | — | Paid beta |

Never set `MARKETCHECK_COMMERCIAL_AGREEMENT` or `EBAY_COMMERCIAL_AGREEMENT`
(or enable a source in commercial mode) without the signed permission or
written approval it represents.

## 1. Draft inquiry to MarketCheck

> **Subject:** Licensing private-party listings and price data for a small used-car pilot
>
> Hello MarketCheck team,
>
> I'm building FlipFirst, a web app that shows individual used-car buyers
> private-party vehicles listed below market value in their area. We are
> starting with a free, invite-only pilot of 10 users around Tuscaloosa, AL
> (ZIP 35401, 150-mile radius, $2,000–$15,000), then a low-cost monthly
> subscription. Before subscribing we want to be sure our use is permitted.
>
> How we would use your data:
> - One central query per territory every 15–60 minutes to Private Party
>   Search (`/v2/search/car/fsbo/active`); users never query you directly.
> - Store listing records (price, year/make/model, mileage, location,
>   listing URL, first/last seen) and price history for up to [30] days to
>   detect new listings and price drops, and delete them after that.
> - Estimate a market value for each listing — from comparable listings or
>   your MarketCheck Price API — and show it with limited listing details
>   and a link to the original listing; later, push alerts to subscribers.
> - Before the pilot, an internal evaluation: a few polls stored privately,
>   reviewed by me, then deleted.
> - No resale, bulk redistribution, or AI training on your data.
>
> Questions:
> 1. Do your plans permit this use (storage for alerts and price history,
>    values and listing details shown to our users)? If not, which agreement
>    or tier does?
> 2. Is the internal evaluation permitted on the Free or Basic plan?
> 3. Which plan includes Private Party Search and the Price API, what does
>    each call cost, and is the 150-mile radius available on that plan?
> 4. Retention, caching, attribution and display rules for listing records,
>    URLs and photos.
> 5. Which marketplaces feed the private-party data, and roughly how many
>    active listings are within 150 miles of ZIP 35401 in our price range?
> 6. How soon do new private listings and price changes appear?
> 7. Is there pilot or startup pricing below the Basic plan?
>
> Thank you — happy to share more about the product.
>
> [Your name] · FlipFirst · [email] · [phone]

Similar questions apply to any alternative provider (e.g. Vehicle
Acquisition Network): ask where their FSBO data comes from and whether they
hold redistribution rights for it.
