# First live-data test

The first time FlipFirst sees real vehicles, it runs in the validation
harness (`validate_deals.py`): its own private database, no users, text
notifications forced off, notification jobs never run. Nothing reaches a
customer. Commands below use variable **names** only — put values in a
private file, never in chat, an issue or Git.

## 1. What the test proves

- Exit code **0** and the headline `REAL listings from production provider
  endpoints`: real listings were retrieved from a licensed source and went
  through the production pipeline. That is the first verified live ingestion.
- Exit **3** (`NOT REAL MARKET DATA`): Sandbox or a stand-in answered —
  plumbing only. Exit **2**: no credentials. Exit **1**: nothing retrieved.
- It does **not** prove deals are good. That takes your review (§ 5) and
  the gates in `EVALUATION.md`.

## 2. Rehearsal today: eBay Sandbox (no approval needed)

On your own computer (no deployment needed; CI tests on Python 3.13):

```bash
git clone https://github.com/Orangi3/car-sniper-saas && cd car-sniper-saas
```
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Create `~/.config/flipfirst/ebay-sandbox.env` (`chmod 600`) with three
lines: `EBAY_CLIENT_ID=` and `EBAY_CLIENT_SECRET=` followed by your
**Sandbox** keyset values, and `EBAY_ENVIRONMENT=sandbox`. Then:

```bash
set -a; . ~/.config/flipfirst/ebay-sandbox.env; set +a
```
```bash
.venv/bin/python -m validate_deals run --sources ebay --zip 35401 --radius 150 --out validation-runs/sandbox-1
```

Expected: `NOT REAL MARKET DATA — eBay Sandbox test data`, exit 3 (or 1 if
the Sandbox returned nothing). `report.json` → `sources` → `requests` shows
whether the token and search calls succeeded.

## 3. eBay, after written approval

Only after eBay's written approval covers production access **and** price
estimates (`EBAY_SETUP.md`). Create `~/.config/flipfirst/ebay-production.env`
(`chmod 600`) with `EBAY_CLIENT_ID=`, `EBAY_CLIENT_SECRET=` (production),
`EBAY_EPN_CAMPAIGN_ID=`, and `EBAY_COMMERCIAL_AGREEMENT=` (your reference to
eBay's approval). Leave `EBAY_ENVIRONMENT` unset. One poll first:

```bash
set -a; . ~/.config/flipfirst/ebay-production.env; set +a
```
```bash
.venv/bin/python -m validate_deals run --sources ebay --zip 35401 --radius 150 --min-price 2000 --max-price 15000 --out validation-runs/ebay-1
```

Then grow the comparable pool by continuing the same run (the harness
enforces eBay's 5-minute minimum interval; 8 polls 30 minutes apart ≈ 4 h):

```bash
.venv/bin/python -m validate_deals run --sources ebay --zip 35401 --radius 150 --polls 8 --interval 1800 --out validation-runs/ebay-1
```

On the server instead (secrets already in `/etc/sniper/env`):

```bash
systemd-run --pipe --wait --quiet --collect --uid=sniper -p EnvironmentFile=/etc/sniper/env -p WorkingDirectory=/opt/sniper/current /opt/sniper/.venv/bin/python3 -m validate_deals run --sources ebay --zip 35401 --radius 150 --out /opt/sniper/shared/validation/ebay-1
```

## 4. MarketCheck private sellers, after a written agreement

Only after MarketCheck confirms in writing that storing listings for review
is allowed (`OWNER_ACTIONS.md` § 1). `~/.config/flipfirst/marketcheck.env`
(`chmod 600`): `MARKETCHECK_API_KEY=`, `MARKETCHECK_COMMERCIAL_AGREEMENT=`
(your agreement reference). Each poll is ≤ 2 paid calls
(`MARKETCHECK_FSBO_MAX_PAGES`, default 2), so a run is at most 2 × `--polls`
calls (96 at the 48-poll maximum). Harness runs use their own database, so
the server's monthly call budget (`provider_budget.py`) does not see them —
add them up yourself against your plan. Use the radius your plan allows
(a 100-mile cap is reported on the free tier):

```bash
set -a; . ~/.config/flipfirst/marketcheck.env; set +a
```
```bash
.venv/bin/python -m validate_deals run --sources marketcheck_fsbo --zip 35401 --radius 100 --out validation-runs/mc-1
```

## 5. Review, measure, purge

1. Open `<run dir>/review.csv` in a spreadsheet and `report.json` in a text
   viewer **yourself**. Do not paste real provider data or these files into
   Claude or any AI tool: eBay's 2025 agreement restricts putting eBay data
   into generative-AI tools, and MarketCheck's terms restrict reuse. Sharing
   the counts printed by `report` is fine.
2. For each candidate, open the listing and set `outcome_label` (the label
   guide is in `EVALUATION.md` § Real-deal validation).
3. Measure:
   ```bash
   .venv/bin/python -m validate_deals report validation-runs/ebay-1
   ```
4. Optional — keep only ids and labels in the evaluation store of the
   database you point it at:
   ```bash
   .venv/bin/python -m evaluation import validation-runs/ebay-1/review.csv
   ```
5. Delete the provider data once reviewed:
   ```bash
   .venv/bin/python -m validate_deals purge validation-runs/ebay-1
   ```

## 6. Stop conditions

- `auth_config` with "production Buy APIs need eBay Partner Network
  approval": production access isn't active — stop, don't retry.
- A `rate_limited` request: stop for a day.
- `schema` → `drifted: true`: stop; the provider changed its format.
- Exit 3 on a run you expected to be real: a Sandbox or stand-in setting is
  still in your environment (`EBAY_ENVIRONMENT`, `EBAY_API_BASE`).

## 7. What still blocks a legitimate first live test

| Requirement | Who | Status |
|---|---|---|
| eBay Buy API production approval **and** written permission for price estimates | eBay | BLOCKED — apply (`EBAY_SETUP.md`) |
| Production keyset activated (account-deletion notifications or exemption) | you + eBay | not started |
| `EBAY_EPN_CAMPAIGN_ID` (EPN campaign) | you | not started |
| or: MarketCheck written agreement + plan | MarketCheck + you | not started (`OWNER_ACTIONS.md` § 1) |
| A computer with outbound HTTPS to the provider | you | your laptop works; no deployment needed |
| The harness itself | repository | ready — VERIFIED end to end against a stand-in only |

Customer notifications stay off throughout: the harness forces them off,
and on the server invited testers are on Free, which cannot create alert
channels or saved searches (`tests/test_pilot_access.py`).
