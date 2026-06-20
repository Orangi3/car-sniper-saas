# SNIPER — Tunnel / Launcher Polish Round 1

Operational round focused on external-tunnel ergonomics plus a small
local launcher convenience. **No core scanner, dashboard JS, backend
polling, deal calculation, diagnostics, or verifier logic was touched.**
The locked baseline (INTERNAL_VERIFIED_WORKING.md) plus the UI Polish
R1 snapshot (UI_POLISH_R1.md) remain authoritative for internal
behavior — this round only adds operational tooling.

## 1. What changed

Three new files added. No existing file edited.

| File | Purpose |
|---|---|
| `TUNNEL STATUS.command` | Read-only diagnostic. Prints recorded URL + mtime, hits local `/health`, hits tunneled `/health` with latency, lists running tunnel processes, resolves DNS for the recorded host, ends with a one-line status (NO TUNNEL CONFIGURED · APP NOT RUNNING LOCALLY · TUNNEL DEAD · TUNNEL LIVE · TUNNEL DEGRADED). Output to `tunnel_status.log`. |
| `STOP TUNNEL.command` | Cleanly terminates whatever tunnel is running (`cloudflared`, `ssh localhost.run`, `ngrok http 8765`). Archives the current URL to `tunnel_history.log` with a UTC timestamp. TERM then KILL. Asks before clearing `ngrok-url.txt`. Does NOT touch the backend on port 8765 or the scanner daemon. |
| `OPEN DASHBOARD.command` | Smallest possible local launcher. Curls `/health`, refuses to proceed if backend isn't up (and tells you to run `START HERE.command`). If healthy, runs `open -a "Google Chrome" "http://127.0.0.1:8765/?v=<unix-ts>"`. No server restart, no state mutation, no tunnel interaction. |

All three are pure `.command` shell scripts. Both syntax-checked with
`bash -n` before being made executable.

## 2. Confirmation: backend / core app code NOT touched

| Path | Touched in this round? |
|---|---|
| `server.py` | No |
| `sniper.py` | No |
| `dashboard.html` | No |
| `sources/*.py` | No |
| `comps.py` / `vin.py` / `notifications.py` / `scam_detector.py` / `config.py` | No |
| `overrides.json` | No |
| `FULL VERIFY.command` (the verifier itself) | No |
| Any existing tunnel script (`CLOUDFLARE TUNNEL.command`, `VERIFY TUNNEL.command`, `TEMPORARY LINK.command`, `SHARE EXTERNALLY.command`, `CUSTOM DOMAIN.command`) | No |

Locked code paths confirmed unmodified: cache-bust 302 at
`server.py:268`, boot beacon, `window.addEventListener("error", …)` and
`("unhandledrejection", …)` handlers, every `refresh*` function,
`/api/stats` `deals` field, `/api/poll-all` consolidated `deals_found`,
`_check_auth`, `_json_error`'s HTTPException branch, all scanner
modules.

## 3. Current live dashboard state

Captured directly from the rendered Chrome tab after the launcher fired
(`OPEN DASHBOARD.command` → `http://127.0.0.1:8765/?v=1780134301`):

| Field | Value |
|---|---|
| Status pill | TRACKING |
| ZIP / Radius / Last poll / Threshold | 35401 · R100mi · 8h ago · 5% |
| Live ticker | scrolling — `…$110 · 110k mi · bat ▸ 2021 Ford F150 $17,600 · craigslist ▸ 2019 Chrysler Pacifica $14,500 · craigslist ▸ 2023 Toyota Gr Supra $250 · 250k mi · bat…` |
| PROFIT POOL | $28,284 |
| BEST DISCOUNT | −64.6% (2002 Ford F-350, near-miss) |
| **DEALS FOUND tile** | **15** |
| **LISTINGS SCANNED tile** | **181** |
| BEST DEALS tab badge | **15** (matches the tile — patch from round 9 still holding) |
| CLOSING SOON / SAVED / ALL LISTINGS tabs | 0 · 0 · 3 |
| TOP TARGET | 2002 Ford F-350 · $5,500 · −64.6% · Est. profit $8,531 · vs comp avg $15,531 (n=2) |
| First listing row | $5,500 · −64.6% · EST · RISK 20 · P $8,531 · Craigslist (by owner) · CASH · AL · 2002 Ford F-350 · 15d ago · sc 82 |
| VIN provider card | "Setup Needed" (no key configured) |
| Console UNCAUGHT errors | 0 |
| CORE non-2xx | 0 |

## 4. Difference from locked baseline = scanner data evolution, not regression

| Field | Locked baseline (2026-05-29 20:31 UTC) | Now (2026-05-30 ~5:45 AM local) | Cause |
|---|---|---|---|
| listings | 178 | 181 | +3 from overnight scanner cycles (Craigslist + bat) |
| deals | 14 | 15 | one more near-miss crossed the discount threshold overnight |
| profit_pool | $25,902 | $28,284 | one more positive-profit near-miss; same calculation as baseline |
| /api/stats | HTTP 200 | HTTP 200 | unchanged |
| /api/diagnostics | HTTP 200 | HTTP 200 | unchanged |
| UNCAUGHT JS errors | 0 | 0 | unchanged |
| CORE non-2xx | 0 | 0 | unchanged |
| Verifier classification | INTERNALLY VERIFIED WORKING | INTERNALLY VERIFIED WORKING | unchanged |

The deltas are purely the result of the scanner continuing to ingest
listings overnight as designed. No code path produced different output
than it did at the baseline; the input set just grew. The verifier
re-ran cleanly after the three new files were added (see
`full_verify.log`: `delta requests in window: 101`, `0 UNCAUGHT`,
`0 CORE non-2xx`).

## 5. Final classification

**LAUNCHER / TUNNEL POLISH VERIFIED — INTERNAL BASELINE PRESERVED.**

The three new operational scripts are additive: they observe the system
and bring up a browser tab. They do not call into any locked code path,
do not write to runtime state, and do not restart the server. The
internal verifier confirms the baseline still holds, and the deltas
versus the locked numbers are accounted for by natural scanner activity.

## Files relevant to this checkpoint

- `TUNNEL STATUS.command` — new
- `STOP TUNNEL.command` — new
- `OPEN DASHBOARD.command` — new
- `BACKUP TUNNEL POLISH R1.command` — new (rerunnable backup of this checkpoint)
- `TUNNEL_POLISH_R1.md` — this doc
- `full_verify.log` — last verifier run that proved the baseline still holds
- `INTERNAL_VERIFIED_WORKING.md` — original locked baseline
- `UI_POLISH_R1.md` — previous round (cosmetic copy edits)

## Backup of this checkpoint

Created by `BACKUP TUNNEL POLISH R1.command`:

- **Backup file:** `~/Desktop/sniper_INTERNAL_VERIFIED_WORKING_TUNNEL_POLISH_R1_20260530_055056.zip`
- **Three restore points** now live on the Desktop:
  1. `sniper_INTERNAL_VERIFIED_WORKING_<TS>.zip` — original locked internal baseline
  2. `sniper_INTERNAL_VERIFIED_WORKING_UI_POLISH_R1_<TS>.zip` — after UI Polish R1 cosmetic edits
  3. `sniper_INTERNAL_VERIFIED_WORKING_TUNNEL_POLISH_R1_20260530_055056.zip` — this checkpoint (adds the three operational scripts above)

To restore any of them, double-click `RESTORE BASELINE.command` and
type the matching timestamp when prompted. The script extracts the
chosen backup into a new dated folder on the Desktop and never
overwrites the live `~/Desktop/sniper/` folder.
