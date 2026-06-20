# SNIPER — UI Polish Round 1

Post-polish snapshot of the locked baseline. **`dashboard.html` only**
was edited. Two static copy changes, no script/backend/scanner logic
touched. Both changes verified by `FULL VERIFY.command`.

## Verification timestamp

- Verifier run: 2026-05-29 20:50:04 UTC
- Verifier: `~/Desktop/sniper/FULL VERIFY.command`
- Verifier output: `~/Desktop/sniper/full_verify.log`

## Files changed

| File | Change |
|---|---|
| `dashboard.html` | Two static HTML strings replaced (lines 773 and 807–811). |

## Files NOT changed

`server.py`, `sniper.py`, every file in `sources/`, `comps.py`, `vin.py`,
`config.py`, `notifications.py`, `scam_detector.py`, `overrides.json`,
`FULL VERIFY.command`, every other `.command` script, the new
hardening docs.

## Exact copy edits

**1) FB / OfferUp / Nextdoor card — `dashboard.html` line 773**

- Removed: "It scrapes only what's visible in YOUR session — legal, zero TOS exposure."
- Added: "Browser-session capture tool. Use only on accounts and sites where you have permission and review each platform's terms."

**2) External Access card footer — `dashboard.html` lines 807–811**

- Removed: ngrok-only walkthrough that pointed at `SHARE EXTERNALLY.command`.
- Added: Cloudflare-first walkthrough pointing at `CLOUDFLARE TUNNEL.command`, with `TEMPORARY LINK.command` and `SHARE EXTERNALLY.command` listed as fallbacks.

## What guarantees these are cosmetic-only

- Both edits land **inside** `<aside>…</aside>` static markup; both are inside `<small class="muted">…</small>` text nodes; neither touches any `id="…"` attribute or any element a refresher updates by ID.
- Neither edit is inside the `<script>…</script>` block, and the `<script>` body length stayed at **54295 bytes — bit-for-bit identical to the locked baseline**.
- No CSS rules, color tokens, or layout selectors were modified.
- No JS function, event listener, or DOM ID was added, removed, or renamed.
- No backend route, response shape, or status code changed.
- No database query, scanner, comp calculation, or poll behavior changed.

## Locked code paths still intact

The whole "do not touch without re-verification" set from
`INTERNAL_VERIFIED_WORKING.md` §9 remains untouched:

- `dashboard.html` boot beacon, window error/unhandledrejection handlers,
  `refreshTicker` / `refreshHero` / `refreshAlerts` / `refreshDiagnostics` /
  `refreshStats` / `refreshSources` / `refreshFacets`, poll-button handler.
- `server.py` cache-bust 302 at line 268, `/api/stats` (with `deals`
  field), `/api/poll-all` (consolidated `deals_found`), `_check_auth`,
  `_json_error` HTTPException branch.
- All scanners (`sources/*.py`), ranking/comp logic, `overrides.json`.
- `FULL VERIFY.command` verifier logic.

## Verifier result

```
/api/stats final status:        HTTP 200
/api/diagnostics final status:  HTTP 200
listings (DB count):            178
deals    (DB count):            14
CORE non-2xx:                   0
NON-CORE non-2xx:               1   (intentional GET / 302 cache-bust)
UNCAUGHT JS errors:             0
delta requests in window:       105

⟁ FINAL: INTERNALLY VERIFIED WORKING
```

The DB counts (178 listings, 14 deals) match the locked baseline,
proving the copy edits did not perturb behavior.

## Backup of this checkpoint

`~/Desktop/sniper_INTERNAL_VERIFIED_WORKING_UI_POLISH_R1_<TS>.zip` —
produced by `BACKUP UI POLISH R1.command`. Same exclusions as the
original baseline backup (no `.venv`, `__pycache__`, logs, PIDs,
sqlite-shm/-wal, cloudflared binary, .git, .DS_Store).

To roll back this round only (revert to the pre-polish baseline):
restore from the *previous* baseline zip
`sniper_INTERNAL_VERIFIED_WORKING_<TS>.zip` using
`RESTORE BASELINE.command`. The pre-polish baseline differs only in
the two `<small class="muted">` text blocks above.

## Classification

**INTERNALLY VERIFIED WORKING** (locked baseline preserved + UI polish R1 applied).
