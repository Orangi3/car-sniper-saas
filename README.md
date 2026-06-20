# Car Sniper — Multi-source

A self-hosted, local-first car deal sniper that polls **every auto-listings
source that legally allows automated access**, normalizes them to a common
schema, scores each listing against actual sold-price comps (not KBB), and
alerts you when something appears at least 10% below comp average.

## What's plugged in

Out of the box, polling every 2 minutes:

| Source | What it covers | Method |
| --- | --- | --- |
| **Craigslist** | Tuscaloosa + Birmingham + Montgomery, by owner | RSS |
| **Bring a Trailer** | Collector / enthusiast cars, nationwide | RSS |
| **Cars & Bids** | Modern enthusiast auctions | RSS |
| **Hemmings** | Classics, vintage | RSS |
| **GovDeals** | State/local government surplus (fleet trucks, cruisers, vans) — AL, MS, GA, TN, FL | RSS |
| **GSA Auctions** | Federal government surplus vehicles | RSS |
| **PublicSurplus** | More state/local surplus | RSS |
| **Copart (live)** | Active salvage lots near AL — projects, parts, rebuildables | JSON endpoint |
| **IAA (live)** | The other big salvage marketplace | JSON endpoint |

Optional, with a free/paid API key:

| Source | What it adds | Setup |
| --- | --- | --- |
| **eBay Motors** | Nationwide active listings + auctions, dealers + private | Free dev account at developer.ebay.com → set `EBAY_CLIENT_ID` + `EBAY_CLIENT_SECRET` |
| **MarketCheck** | **Cars.com, Autotrader, CarGurus, dealer.com inventory** — the legal way to cover the big aggregators | Sign up at marketcheck.com (~$50/mo) → set `MARKETCHECK_API_KEY` |

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
1. Filter dealers (unless source is dealer-by-design like MarketCheck).
2. Filter outside `min_price`/`max_price`.
3. Skip if can't extract year/make/model.
4. Skip if salvage (surfaced in the listings table, not as a "deal").
5. Pull sold comps from eBay + BaT + Cars & Bids (cached 24h).
6. If listing is ≥ `deal_threshold_pct` below comp avg → write an alert.

Alerts surface in the dashboard's "Live deals" panel and via
`python sniper.py recent`.

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
- Mileage adjustment is a flat `-$0.08/mi above 100k`. Replace in
  `comps.py` if you want a model-specific curve.
- Personal, non-commercial use only. Be respectful with poll intervals;
  the defaults are conservative.
