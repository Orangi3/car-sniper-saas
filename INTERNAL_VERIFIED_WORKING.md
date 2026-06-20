# SNIPER — Internal Baseline: VERIFIED WORKING

This file marks the first state of the project in which every dashboard
function the user relies on was empirically verified end-to-end against
the **local** backend at `http://127.0.0.1:8765/`. Treat the project at
this checkpoint as the **stable internal baseline**. Do not touch
tunnels, auth hardening, UI polish, or production deployment without
first re-running `FULL VERIFY.command` to confirm this baseline still
holds.

## Verification timestamp

- Run completed: **2026-05-29 20:31:02 UTC**  (Fri May 29 16:31 EDT)
- Verifier: `~/Desktop/sniper/FULL VERIFY.command`
- Verifier output: `~/Desktop/sniper/full_verify.log`
- Host: `Tylers-MacBook-Air` (darwin/arm64)
- Server PID at verify time: `15663`
- Build marker in `dashboard.html`: **`v7.r5`**

## Backend / database

| Field | Value |
|---|---|
| listings (DB count, /api/stats) | **178** |
| deals (DB count, /api/stats) | **14** |
| `/health` | HTTP 200 — `{"port":8765,"service":"sniper","status":"ok"}` |
| `/api/stats` final status | HTTP 200, object[12 keys] |
| `/api/diagnostics` final status | HTTP 200, array[8] |
| `/api/poll-all` (one round) | `ok=true`, `listings_scanned=117`, `deals_found=14`, `profit_pool=$25,902`, `duration=1.18s`, `8 sources processed` |

### Per-source state (last verified poll)

| Source | Enabled | Success | Fetched | Elapsed | Error |
|---|---|---|---|---|---|
| bat | ✓ | ✓ | 20 | 1.17s | — |
| craigslist | ✓ | ✓ | 97 | 0.24s | — |
| ebay | — | — | 0 | — | source disabled |
| email_imap | — | — | 0 | — | source disabled |
| govdeals | ✓ | ✓ | 0 | 0.76s | — |
| hemmings | ✓ | ✓ | 0 | 0.16s | — |
| marketcheck | — | — | 0 | — | source disabled |
| marketplace | ✓ | ✓ | 0 | 0.00s | — |

## Frontend (Chrome at `127.0.0.1:8765`)

Empirically confirmed via 18-second observation window of `sniper-requests.log`
(the open Chrome tab's actual fetches):

- **105 requests** fired by the open dashboard tab in the 18s window
- **0 UNCAUGHT** JavaScript errors
- Auto-refresh intervals active (matches the 5s / 15s `setInterval` schedule)
- Every refresher confirmed by URL pattern:
  - `refreshTicker` — `/api/listings?limit=30&sort=newest`
  - `refreshHero` — `/api/alerts` + `/api/near_misses` + `/api/saved`
  - `refreshAlerts` — `/api/alerts` + `/api/near_misses` + `/api/fresh_listings`
  - `refreshListings` — `/api/listings?limit=300&...&max_age_min=1440`
  - `refreshSaved` — `/api/saved`
  - `refreshStats` — `/api/stats`
  - `refreshDiagnostics` — `/api/diagnostics` (fired 3× in 18s → 15s interval correct)
  - `refreshSources` / `refreshFacets` / `loadSettings` / `loadShareSettings` / `loadNotifySettings` / `checkVinProviderState` / `refreshMpCount` — all fired once on boot
- Poll button handler at `dashboard.html` line 1514 (`#poll-now` → `POST /api/poll-all`)

## Network classification

| Class | Count | Detail |
|---|---|---|
| CORE non-2xx | **0** | every dashboard-critical API returned 2xx |
| NON-CORE non-2xx | 1 | `GET / 302` — intentional cache-bust redirect to `/?v=<mtime>` (server.py:268) |
| UNCAUGHT JS errors | 0 | window error/unhandledrejection handlers triggered 0 times |

## Console status

- Build marker logged: `⟁ SNIPER dashboard build v7.r5 (...) loaded`
- No UNCAUGHT JS errors during the verify window
- Boot beacon transitioned `#hero-ticker` past `Initializing scanner...` to live data

## Diagnostics block

Real per-source status visible in the dashboard's Diagnostics panel:

```
bat            ✓ 20  0.16s
craigslist     ✓ 97  0.21s
ebay           off
email_imap     off
govdeals       0 found — not polled this tick  0.35s
hemmings       0 found — not polled this tick  0.24s
marketcheck    off
marketplace    0 found — not polled this tick  0s
```

## Files in this baseline

Patched in this round (compared to the user's r5 starting state):

- `dashboard.html` — boot beacon, global error/unhandledrejection handlers,
  visible failure surfaces in `refreshStats` / `refreshHero` / `loadShareSettings` /
  poll-button handler, eager Current Public URL, `refreshStats` uses `s.deals`
  for the DEALS FOUND tile so it matches the BEST DEALS tab badge.
- `server.py` — `/api/stats` now returns a `deals` field, `/api/poll-all`
  `deals_found` uses the same threshold as the BEST DEALS tab, routine static
  404s (favicon, apple-touch-icon, robots.txt) no longer pollute the UNCAUGHT
  error log.
- New helper scripts: `CLOUDFLARE TUNNEL.command`, `VERIFY TUNNEL.command`,
  `LOCAL VERIFY.command`, `REOPEN WITH FIXES.command`, `FULL VERIFY.command`.

## How to re-verify this baseline

```
# 1. Make sure backend is running:
~/Desktop/sniper/START\ HERE.command

# 2. Run the full local verifier:
~/Desktop/sniper/FULL\ VERIFY.command

# 3. Tail the log:
tail -80 ~/Desktop/sniper/full_verify.log

# Expected:
#   ⟁ FINAL: INTERNALLY VERIFIED WORKING
#   CORE non-2xx: 0, UNCAUGHT: 0, /api/stats=200, /api/diagnostics=200
```

## What NOT to change without re-verification

- The `/` → `/?v=<mtime>` 302 redirect (server.py:268). This is the
  cache-bust mechanism that keeps Safari/Chrome from serving stale
  dashboard JS. Removing it re-introduces the original "Initializing
  scanner…" stuck-state bug.
- The boot beacon + window error handlers at the top of the
  `dashboard.html` `<script>` block. They are the live diagnostic that
  proves JS executed.
- The `share_public: true` config in `overrides.json` (only relevant
  if external sharing is needed; leave as-is for local-only work).

## Baseline classification

**INTERNALLY VERIFIED WORKING** — locked 2026-05-29 20:31 UTC.
