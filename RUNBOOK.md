# SNIPER — RUNBOOK

Operational playbook for the locked **INTERNALLY VERIFIED WORKING** baseline
(2026-05-29 20:31 UTC). Use this for day-to-day operation, verification,
backup, and recovery. The intent is that you can always get back to the
verified state in one step.

Every script lives in `~/Desktop/sniper/`. Double-click in Finder or run
from Terminal — they're plain bash with a `.command` extension.

---

## 1. Start the app

**Method A — recommended:** double-click **`START HERE.command`**.

It launches `sniper.py daemon` (poller) and `server.py` (Flask backend on
port 8765), writes `sniper.pid` / `server.pid`, and waits for `/health`.

**Method B — manual:** open Terminal, then:

```
cd ~/Desktop/sniper
.venv/bin/python sniper.py daemon &  echo $! > sniper.pid
.venv/bin/python server.py        &  echo $! > server.pid
curl -s http://127.0.0.1:8765/health    # should print {"status":"ok",...}
```

Then open the dashboard: `http://127.0.0.1:8765/`. The server's 302
cache-bust redirect at `server.py:268` automatically appends `?v=<mtime>`
so the browser never serves stale JS.

To stop everything: double-click **`STOP.command`**.

---

## 2. Start the tunnel (only if you need external access)

You usually do **not** need a tunnel for normal local use.

If you do, two options — both write the public URL to `ngrok-url.txt`,
both die the moment their Terminal window is closed:

| Tunnel | Script | Reliability | URL pattern |
|---|---|---|---|
| Cloudflare Quick Tunnel | **`CLOUDFLARE TUNNEL.command`** | best (used by current baseline) | `*.trycloudflare.com` |
| localhost.run (SSH) | `TEMPORARY LINK.command` | works, occasional DNS lag | `*.lhr.life` |
| ngrok | `SHARE EXTERNALLY.command` | requires free ngrok account/authtoken | `*.ngrok-free.app` |

**If Cloudflare gives you DNS_PROBE_FINISHED_NXDOMAIN on first load**
(the URL was printed but Cloudflare's edge hasn't published it yet), run
**`VERIFY TUNNEL.command`** — it detects the stale URL and restarts
cloudflared automatically to get a fresh one.

---

## 3. Verify the dashboard

Double-click **`FULL VERIFY.command`** (this is the canonical verifier).
Output is written to `full_verify.log`.

The script:

1. Curls every endpoint the dashboard JS calls (status code, latency, response shape).
2. Triggers the same `POST /api/poll-all` the Poll button calls.
3. Waits 18 seconds and reads `sniper-requests.log` to see what the *currently open Chrome tab* fetched — proving the JS is alive (refreshTicker, refreshHero, refreshDiagnostics, etc. firing on their intervals).
4. Classifies non-2xx as **CORE** (real failure) or **NON-CORE** (intentional 302 cache-bust on `/`, browser favicon 404s, 304 Not Modified).

**Pass criteria — all six must hold:**

- listings load (`/api/stats listings > 0` or explicit empty-DB state)
- deals calculate (`/api/stats deals` matches BEST DEALS tab count)
- `/api/stats` returns HTTP 200
- `/api/diagnostics` returns HTTP 200
- 0 UNCAUGHT JS errors in the window
- 0 CORE non-2xx

If those hold, the script prints **`⟁ FINAL: INTERNALLY VERIFIED WORKING`**.

If not, the failure mode is reported with the exact failing URL and status.

`LOCAL VERIFY.command` is a lighter version (no Chrome-window snapshot)
useful for quick spot checks.

---

## 4. Make a backup

Double-click **`BACKUP BASELINE.command`**.

It writes a timestamped zip to `~/Desktop/sniper_INTERNAL_VERIFIED_WORKING_<TS>.zip`
containing everything needed to restore (code, dashboard, `.command`
scripts, `overrides.json`, `listings.db`) and excluding runtime junk
(`.venv`, `__pycache__`, logs, PID files, sqlite -shm/-wal sidecars,
the downloaded `cloudflared` binary, `.git`, `.DS_Store`).

Make a fresh backup before any change you're not 100% sure about.

---

## 5. Restore from a backup

Double-click **`RESTORE BASELINE.command`**.

It is intentionally conservative:

1. Lists every `sniper_INTERNAL_VERIFIED_WORKING_*.zip` on the Desktop
   with date and size, newest first.
2. Asks you to type a specific timestamp to choose one. Anything else aborts.
3. Asks you to type **`yes`** to confirm. Anything else aborts.
4. Stops the running server / sniper daemon.
5. Makes a `PRE_RESTORE_<TS>.zip` of the *current* sniper folder before
   touching it (so the "before" state is also recoverable).
6. Extracts the chosen backup into `~/Desktop/sniper_restored_<TS>/`
   (a new folder, never overwriting the current one).
7. Prints the next steps: how to swap the folders manually, or how to
   inspect/diff before swapping.

**The script never auto-replaces your project folder.** Swap manually
once you've verified the restored folder looks right.

---

## 6. Rotate logs (no app restart needed)

Logs can grow unbounded. Double-click **`ROTATE LOGS.command`** to
rotate them safely while the app keeps running:

- Files rotated: `sniper-requests.log`, `server.log`, `sniper.log`,
  `tunnel.log`, `cloudflared.log`, `verify.log`, `local_verify.log`,
  `full_verify.log`, `reopen.log`.
- Uses **copy-truncate** — copies the current log to `<name>.<TS>.gz`
  (gzipped), then truncates the live log to zero bytes. This works
  because Python's `FileHandler` holds the file descriptor open by
  *inode*, not by name — truncation preserves the inode.
- No server restart required. Tiny logging window (microseconds) where
  a line could land in either the rotated copy or the truncated live
  file. Acceptable for this app.

---

## 7. Secrets — what's in env, what isn't

`config.py` already reads several values from environment variables and
falls back to `overrides.json` if env is unset. See `.env.example` for
the list. Currently:

| Setting | Source today | In env? |
|---|---|---|
| `BUMPER_API_KEY` | env → `overrides.json` | yes |
| `CLEARVIN_API_KEY` | env → `overrides.json` | yes |
| `EMAIL_IMAP_*` | env → `overrides.json` | yes |
| `VIN_PROVIDER` | env (default `auto`) | yes |
| `share_username` / `share_password` | `overrides.json` only | **no** |
| `notify_phone` | `overrides.json` only | **no** |

If you want to migrate the three "no" entries to env, see `.env.example`
for the proposed names and the one-line `config.py` change. Doing this
is a behavior-touching change that requires re-running
`FULL VERIFY.command` afterward.

---

## 8. What counts as INTERNALLY VERIFIED WORKING

Running `FULL VERIFY.command` and seeing this final line in the output:

```
⟁ FINAL: INTERNALLY VERIFIED WORKING
    - every probed endpoint returned 2xx (or intentional 302 cache-bust on /)
    - Poll triggered, sources polled, deals/profit consistent
    - Chrome JS fired N requests in the 18s window (auto-refresh active)
    - 0 UNCAUGHT JS errors
    - 0 CORE non-2xx (non-core: K — all benign)
    - /api/stats=200  /api/diagnostics=200  listings=<n>  deals=<m>
```

The canonical baseline reference is `INTERNAL_VERIFIED_WORKING.md`.

---

## 9. What NOT to touch without re-verification

These code paths were proved correct in the locked baseline. Modifying
any of them requires re-running `FULL VERIFY.command` immediately after
the change, and restoring from backup if the verifier no longer prints
`INTERNALLY VERIFIED WORKING`:

- `dashboard.html` — boot beacon, window error handlers,
  `refreshTicker` / `refreshHero` / `refreshAlerts` / `refreshDiagnostics` /
  `refreshStats` / `refreshSources` / `refreshFacets`, poll-button handler.
- `server.py` — the `/` → `/?v=<mtime>` 302 redirect (line 268),
  `/api/stats` (the `deals` field), `/api/poll-all` (the consolidated
  `deals_found`), `_check_auth`, `_json_error`'s HTTPException branch.
- `sources/*.py` — every individual scanner.
- `comps.py`, `vin.py`, `sniper.py`, `scam_detector.py`,
  `notifications.py`, `config.py`.
- `overrides.json` — the deal threshold, regions, phone, share config.

If you have to touch any of these, follow the loop in §3 and §5.

---

## 10. Quick-reference command map

| You want to… | Double-click |
|---|---|
| Start backend | `START HERE.command` |
| Stop backend | `STOP.command` |
| Get a public URL via Cloudflare | `CLOUDFLARE TUNNEL.command` |
| Verify a public tunnel works | `VERIFY TUNNEL.command` |
| Get a public URL via lhr.life | `TEMPORARY LINK.command` |
| Get a public URL via ngrok | `SHARE EXTERNALLY.command` |
| Lightweight local API audit | `LOCAL VERIFY.command` |
| Full internal verifier | `FULL VERIFY.command` |
| Reopen Chrome on local dashboard | `REOPEN WITH FIXES.command` |
| Make a backup | `BACKUP BASELINE.command` |
| Restore from a backup | `RESTORE BASELINE.command` |
| Rotate logs without restart | `ROTATE LOGS.command` |
| Read the baseline state | `INTERNAL_VERIFIED_WORKING.md` |
| Read the hardening summary | `OPERATIONAL_HARDENING.md` |
