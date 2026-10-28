# Car Sniper — Multi-source

A self-hosted, local-first car deal sniper that polls **every auto-listings
source that legally allows automated access**, normalizes them to a common
schema, scores each listing against actual sold-price comps (not KBB), and
alerts you when something appears at least 10% below comp average.

## What's plugged in

Out of the box (no keys needed):

| Source | What it covers | Cadence |
| --- | --- | --- |
| **Craigslist** (by owner) | 13 regions by default: every Alabama region + Meridian, Columbus GA, Chattanooga, Atlanta | every 30s |
| **Marketplace / OfferUp / Nextdoor** | Pushed in by the bundled browser extension from your own logged-in session | every 30s |
| **Email digests** | Saved-search emails from Marketplace, OfferUp, Nextdoor, eBay, Craigslist (needs IMAP creds) | every 30s |
| **Bring a Trailer** | Collector / enthusiast auctions | every 2 min |
| **Hemmings** | Classics | every 2 min |
| **GovDeals** | Government surplus — AL, MS, GA, TN, FL | every 2 min |

With a key:

| Source | What it adds | Setup |
| --- | --- | --- |
| **eBay Motors** | Nationwide listings via the official Browse API | Free dev account → `EBAY_CLIENT_ID` + `EBAY_CLIENT_SECRET` |
| **MarketCheck** | **Cars.com, Autotrader, CarGurus, dealer inventory** — the licensed way | ~$50/mo → `MARKETCHECK_API_KEY` |

Not available (no public access): Cars & Bids, Copart, IAA, GSA Auctions,
PublicSurplus. Their files in `sources/` are intentionally empty.

## What's deliberately NOT included (and why)

- **Facebook Marketplace** — TOS prohibits scraping. Real account-ban risk.
- **OfferUp / 5miles** — same.
- **Cars.com / Autotrader / CarGurus directly** — TOS prohibits scraping.
  Use MarketCheck (above) for the licensed feed of these same sites.
- **Carvana / Vroom / CarMax inventory** — TOS prohibits.

If a site you want isn't here, check whether they offer an RSS feed or a
public API. If they do, drop a new file into `sources/` following the
pattern in `sources/base.py` — the sniper will auto-discover it.

## Setup

Easiest path: open the folder, double-click **`START HERE.command`**.
First time only: right-click → Open (macOS Gatekeeper).

That's it. Dashboard opens at `http://127.0.0.1:8765`.

See `QUICKSTART.txt` for the no-jargon version.

## Daily commands (if you prefer the terminal)

```bash
source .venv/bin/activate

python sniper.py poll              # one-shot poll of every source
python sniper.py daemon            # poll forever
python sniper.py sources           # see which sources are on/off
python sniper.py recent 25         # last 25 alerts
python sniper.py stats             # per-source listing counts

python vin.py 1HGCM82633A123456    # VIN check
python comps.py "2015 Mazda Miata" # sold comps

python server.py                   # dashboard
```

## Enabling the optional API sources

### eBay Motors (free, ~5,000 calls/day)

1. Sign up: https://developer.ebay.com/
2. Application Keys → "Production" → copy Client ID and Client Secret
3. Add to your shell profile (or run before launching):
   ```bash
   export EBAY_CLIENT_ID=your_id
   export EBAY_CLIENT_SECRET=your_secret
   ```
4. Restart the sniper. eBay should show "ON" in the dashboard's Sources panel.

### MarketCheck (paid, covers Cars.com / Autotrader / CarGurus)

1. Sign up: https://www.marketcheck.com/automotive/cars-api
2. Free trial (~250 calls) gets you started; paid plans from ~$50/mo
3. ```bash
   export MARKETCHECK_API_KEY=your_key
   ```
4. Restart. MarketCheck flips to "ON" automatically.

## Adding your own source

Drop `sources/your_site.py` with this shape:

```python
SOURCE_ID = "your_site"
SOURCE_NAME = "Your Site"

def enabled(cfg): return True
def poll(cfg) -> list[NormalizedListing]:
    # fetch + parse; return normalized objects
    return [...]
```

The sniper auto-discovers it on next launch. Use existing source files as
templates — `craigslist.py` is the simplest RSS pattern, `ebay_motors.py`
shows OAuth-based API auth, `copart_live.py` shows raw JSON endpoints.

## Tuning

All knobs live in `config.py`:

| Knob | What it does |
| --- | --- |
| `zip` / `radius_mi` | Used by sources that respect geo (eBay, MarketCheck) |
| `min_price` / `max_price` | Filter listings outside this band |
| `deal_threshold_pct` | How far below comp avg to alert. Default `10`. |
| `poll_interval_sec` | Daemon polling frequency. Default `120`. |
| `sources.<id>.enabled` | Turn any source on/off without deleting it |
| `sources.craigslist.regions` | Add/remove CL subdomains |
| `sources.govdeals.states` | Which states to pull govt surplus from |
| `sources.copart_live.states` | Which states for salvage |

## How alerts work

Every new listing from every source goes through:

1. **Normalize** — RSS dates → ISO (so the freshness bonus works), branded
   titles (salvage / rebuilt / flood / no title / parts only) → `is_salvage`,
   non-runners → `needs_work`. Negations like "clean title, not rebuilt" are
   respected. Payment teasers ("$2,500 down", "$299/mo") are never read as
   the price.
2. **Instant estimate** — priced against local peer asking prices already in
   the DB, so it shows a number immediately.
3. **Sold comps** — eBay / BaT / Cars & Bids / PCarMarket / Hemmings sold
   results (+ MarketCheck if keyed), mileage-adjusted, cached 24h.
4. **Alert gate** — a listing alerts when ALL of these hold:
   - inside your `min_price`/`max_price` band, not salvage, not needs-work,
     not a dealer, scam score < 50;
   - discount ≥ `deal_threshold_pct` against **verified sold comps**, OR — if
     sold comps came back empty — ≥ `estimate_alert_threshold_pct` (30%)
     against ≥ `estimate_alert_min_peers` (8) local peers;
   - discount ≤ `max_believable_discount_pct` (65%) — deeper than that is
     almost always a mis-parse or a scam.
5. **Deliver** — to the owner's channels and to every user whose saved
   search matches (see below).

### Alert channels

Set any of these in the environment; every alert goes to all that are set.
Sends are queued and retried — nothing is dropped by rate limiting.

| Channel | Env vars | Notes |
| --- | --- | --- |
| ntfy push | `NTFY_TOPIC` (`NTFY_SERVER`, `NTFY_TOKEN` optional) | Free, instant. Install the ntfy app and subscribe to the topic. Use a long random topic. |
| Pushover | `PUSHOVER_TOKEN`, `PUSHOVER_USER` | |
| Twilio SMS | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` + `NOTIFY_PHONE` | Works on a Linux server |
| Discord | `DISCORD_WEBHOOK_URL` | Good for a flipping crew |
| Webhook | `ALERT_WEBHOOK_URL` | JSON POST — Zapier, n8n, your own bot |
| iMessage | `NOTIFY_PHONE`, macOS only | The original channel |

### Per-user saved-search alerts (SaaS)

Starter+ users open **🔔 My Deal Alerts** on the dashboard, tap **New push
topic** (or add a Discord/Slack webhook or, if Twilio is configured, an SMS
number), set their filters, and click **Alert me on my current filters**.
Matching deals land in their tray (`GET /api/me/alerts`) and are pushed to
their own channels — one push per deal even if several searches match.
Filters support everything the listings view does plus `min_discount_pct`
and `min_profit`.

## File map

```
sniper/
├── START HERE.command       # ← double-click this
├── STOP.command
├── QUICKSTART.txt
├── config.py                # all knobs
├── sniper.py                # orchestrator + CLI + DB
├── server.py                # Flask backend
├── dashboard.html           # local web UI
├── vin.py                   # VIN decoder + recalls + complaints
├── comps.py                 # sold-comp aggregator (eBay + BaT + CB + Copart)
├── sources/                 # plugin sources, auto-discovered
│   ├── base.py
│   ├── craigslist.py
│   ├── ebay_motors.py
│   ├── bring_a_trailer.py
│   ├── cars_and_bids.py
│   ├── hemmings.py
│   ├── govdeals.py
│   ├── gsa_auctions.py
│   ├── publicsurplus.py
│   ├── copart_live.py
│   ├── iaa_live.py
│   └── marketcheck.py
├── requirements.txt
└── setup.sh                 # alternative manual installer
```

## Notes & limits

- All sources are polled **in parallel-but-not-async** — total tick takes
  ~5–15 sec depending on which APIs respond fast that minute. One slow
  source doesn't block the others.
- All scrapers are best-effort and use polite User-Agents. Some endpoints
  (Copart, IAA, Cars & Bids) occasionally change their JSON shape — if you
  see a source returning 0 over many cycles, file an issue and I'll update
  the parser.
- Title parsing is regex-based and won't catch every car. Cars without a
  parseable year/make/model still appear in the listings table; they just
  don't get scored as deals.
- Mileage adjustment is a flat `-$0.08/mi above 100k` (applied to scoring).
  Replace in `comps.py` if you want a model-specific curve.
- Sold-comp scrapers break when those sites change layout. Check with
  `python comps.py "2015 Toyota Camry"`; if it returns nothing, alerts fall
  back to the stricter estimate rule above instead of stopping.
- Craigslist regions that return 403/429 are backed off for 5 minutes. If
  you add many regions, keep an eye on the Sources diagnostics panel.
- Personal, non-commercial use only. Be respectful with poll intervals;
  the defaults are conservative.
