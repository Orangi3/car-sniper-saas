# SNIPER — Operational Hardening Round

This round added day-to-day operational ergonomics on top of the locked
**INTERNALLY VERIFIED WORKING** baseline (2026-05-29 20:31 UTC). **No
core scanner or dashboard code was modified.** The intent is to make
running, verifying, backing up, restoring, and rotating logs easier and
safer, while leaving everything that the verifier proved correct
exactly as it was.

## Files added

| File | Purpose |
|---|---|
| `RUNBOOK.md` | Operational playbook — start/stop, tunnel, verify, backup, restore, log rotation, secrets, what-not-to-touch, command-map index. |
| `RESTORE BASELINE.command` | Safe restore. Lists backups, requires typed timestamp + `yes` confirmation, makes a `PRE_RESTORE_<TS>.zip` of the current folder first, extracts to a **new** `sniper_restored_<TS>/` folder (never overwrites the live project), prints manual-swap instructions. |
| `ROTATE LOGS.command` | Copy-truncate rotation for `sniper-requests.log`, `server.log`, `sniper.log`, `tunnel.log`, `cloudflared.log`, `verify.log`, `local_verify.log`, `full_verify.log`, `reopen.log`. No server restart needed — Python's `FileHandler` keeps its inode-bound fd alive after truncate. Archives gzipped into `log_archive/`, keeps newest 12 per base name. |
| `.env.example` | Documents the env vars `config.py` already supports (`BUMPER_API_KEY`, `CLEARVIN_API_KEY`, `EMAIL_IMAP_*`, `VIN_PROVIDER`) and the one-line `config.py` patch needed to optionally migrate `SHARE_USERNAME`, `SHARE_PASSWORD`, `NOTIFY_PHONE`. |
| `OPERATIONAL_HARDENING.md` | This file. |

## Files changed

**None.** Specifically not changed:

- `dashboard.html` — every refresher, the boot beacon, the window error/unhandledrejection handlers, the poll button binding.
- `server.py` — the `/` → `/?v=<mtime>` cache-bust 302, `/api/stats` (with the patched `deals` field), `/api/poll-all` (with the consolidated `deals_found`), `_check_auth`, `_json_error`.
- `sources/*.py` — every scanner module.
- `comps.py`, `vin.py`, `sniper.py`, `scam_detector.py`, `notifications.py`, `config.py`.
- `overrides.json` — secrets stay where they are; `.env.example` only *documents* the future migration path.

## What was intentionally not changed

- **The `/` 302 cache-bust** at `server.py:268` — proven necessary in the earlier rounds to keep browsers from running stale dashboard JS.
- **`config.py` secret-loading logic** — touching it requires a re-verify; documenting the future change in `.env.example` is the safest first step.
- **`overrides.json`** — moving values out of it without first wiring env-fallback into `config.py` would break local behavior.
- **All scanner internals** (`sources/*.py`) and ranking/comp logic (`comps.py`, `sniper.py`) — out of scope for an operational round.

## How log rotation preserves running behavior

The Flask app uses `logging.FileHandler` which holds an open file
descriptor on `sniper-requests.log`. The file is identified by inode,
not by name. `ROTATE LOGS.command` uses **copy-truncate**:

1. `cp sniper-requests.log /tmp/.rotate_tmp_$$`
2. `gzip -c /tmp/.rotate_tmp_$$ > log_archive/sniper-requests.<TS>.log.gz`
3. `: > sniper-requests.log` (truncate to 0 bytes; inode preserved)

The server's open fd survives step 3 and the next log line lands in the
now-empty file. The race window is microseconds — at most a single line
could land in either side of the boundary, which is acceptable for a
request log.

`server.log` and `sniper.log` are written by `nohup`'s shell stdout
redirection (not by Python logging), but `>` truncation behaves the
same way for those.

## Restore safety guarantees

`RESTORE BASELINE.command` will **never** overwrite the live
`~/Desktop/sniper/` folder. The flow is:

1. Pick a backup by typing its exact timestamp.
2. Confirm with `yes`.
3. Optional: stop the running server (only if you confirm again).
4. **Safety snapshot of the current folder** → `PRE_RESTORE_<TS>.zip`.
5. Extract chosen backup into a **new** `sniper_restored_<TS>/` folder.
6. Print the manual-swap commands.

If anything looks wrong after the restore stages, the live folder is
still untouched, and the `PRE_RESTORE_<TS>.zip` lets you get back to
the moment-before state even if you've already swapped.

## Exact verification command used

```
~/Desktop/sniper/FULL\ VERIFY.command
```

Output is in `~/Desktop/sniper/full_verify.log` (see below for the
final block produced for this hardening round).

## Final verification result

Captured from the most recent `FULL VERIFY.command` run after the
hardening files were added (none of the verifier-watched code paths
were touched):

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

The hardening files (`.md` and `.command`) are not on any code path the
dashboard touches at runtime, so no behavioral change was expected and
none was observed. Baseline preserved.
