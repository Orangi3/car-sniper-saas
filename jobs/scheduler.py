"""
jobs/scheduler.py — explicit source-polling scheduler.

THIS IS THE ONLY PROCESS that drives sniper.poll_once() in production.
Web (gunicorn) workers MUST NOT own the scheduler — see DEPLOY.md for
the systemd unit layout that keeps these responsibilities separate.

Usage:
    python3 -m jobs.scheduler                  # forever loop
    python3 -m jobs.scheduler --once           # one tick, then exit
    python3 -m jobs.scheduler --interval 60    # override tick interval
    python3 -m jobs.scheduler --max-ticks 5    # exit after N ticks

Concurrency safety:
  * Single shared `billing_job_locks(job_name='scheduler')` row.
  * Acquire with a short lease (default 120s) refreshed before every
    tick. A second instance that races us simply doesn't acquire and
    exits 2.
  * A previous run that crashed leaves a stale lease — after
    --steal-after-seconds (default 600), the next process takes over.

Output:
  * One JSON line per tick on stdout (suitable for journalctl).
  * scheduler_state row in the DB is updated with holder, last tick,
    last success, last error — surfaced to admins via the existing
    /api/admin/sources/health endpoint.

Exit codes:
  0  normal termination (--once succeeded, --max-ticks reached, SIGTERM
     received and lock released cleanly).
  1  operational failure (DB unreachable, can't import sniper, etc.).
  2  another scheduler holds the lease — we did not run.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SCHEDULER_JOB_NAME = "scheduler"


def _holder_token() -> str:
    short = uuid.uuid4().hex[:8]
    return f"{socket.gethostname()}:{os.getpid()}:{short}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="jobs.scheduler",
        description=("Source-polling scheduler. Acquires the scheduler "
                     "lease and drives sniper.poll_once on a cadence. "
                     "Never expose this over HTTP."),
    )
    p.add_argument("--interval", type=int, default=60,
                   help="Seconds between ticks (default 60)")
    p.add_argument("--once", action="store_true",
                   help="Run a single tick, then exit")
    p.add_argument("--max-ticks", type=int, default=0,
                   help="Exit after N ticks (0 = forever)")
    p.add_argument("--lease-seconds", type=int, default=120,
                   help="Lock lease seconds, refreshed each tick (default 120)")
    p.add_argument("--steal-after-seconds", type=int, default=600,
                   help="Take over a stale lock older than N seconds (default 600)")
    return p.parse_args(argv)


def _bump_scheduler_state(db_mod, *, holder, last_tick_at, last_tick_secs,
                           enabled_sources, error=None):
    """Write the heartbeat row used by the admin health view."""
    ph = db_mod.placeholder()
    with db_mod.transaction() as conn:
        cur = conn.cursor()
        if db_mod.IS_PG:
            cur.execute(
                "UPDATE scheduler_state SET "
                "  last_tick_at = %s, last_tick_secs = %s, "
                "  tick_counter = COALESCE(tick_counter,0) + 1, "
                "  enabled_sources = %s, holder = %s, "
                "  last_success_at = COALESCE(%s, last_success_at), "
                "  last_error = %s, last_error_at = %s "
                "WHERE id = 1",
                (last_tick_at, last_tick_secs, ",".join(enabled_sources),
                 holder,
                 (last_tick_at if not error else None),
                 (error or None),
                 (last_tick_at if error else None)))
        else:
            cur.execute(
                "UPDATE scheduler_state SET "
                "  last_tick_at = ?, last_tick_secs = ?, "
                "  tick_counter = COALESCE(tick_counter,0) + 1, "
                "  enabled_sources = ?, holder = ?, "
                "  last_success_at = COALESCE(?, last_success_at), "
                "  last_error = ?, last_error_at = ? "
                "WHERE id = 1",
                (last_tick_at, last_tick_secs, ",".join(enabled_sources),
                 holder,
                 (last_tick_at if not error else None),
                 (error or None),
                 (last_tick_at if error else None)))


def run(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    # Imports happen INSIDE run() so importing jobs.scheduler doesn't
    # touch the DB or build a sniper queue at module-import time.
    try:
        import db
        import sniper
        import sources
        import config as _config
        from config import CONFIG
        import joblock
    except Exception as e:
        sys.stderr.write(f"scheduler: import failure: {e}\n")
        return 1

    holder = _holder_token()

    if not joblock.acquire(SCHEDULER_JOB_NAME, holder=holder,
                            lease_seconds=args.lease_seconds,
                            steal_after_seconds=args.steal_after_seconds):
        existing = joblock.current_holder(SCHEDULER_JOB_NAME) or {}
        sys.stdout.write(json.dumps({
            "event": "lock_held",
            "existing_holder":     existing.get("holder"),
            "existing_expires_at": existing.get("expires_at"),
        }) + "\n")
        return 2

    # SIGTERM / SIGINT → finish current tick, release lock, exit 0.
    stop_requested = {"v": False}
    def _stop(signum, frame):
        stop_requested["v"] = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT,  _stop)

    sys.stdout.write(json.dumps({
        "event": "started", "holder": holder,
        "interval_s": args.interval,
        "once": bool(args.once), "max_ticks": args.max_ticks,
    }) + "\n")
    sys.stdout.flush()

    # The scorer thread pool is owned by THIS process — exactly where it
    # should run. Web workers never call this.
    sniper.ensure_scorer_started()

    ticks = 0
    try:
        while True:
            t0 = time.time()
            tick_at = _now_iso()
            err_summary = None
            try:
                _config.reload()
                result = sniper.poll_once(verbose=False, force_all=False)
                elapsed = round(time.time() - t0, 3)
                enabled_ids = [s.SOURCE_ID for s in sources.iter_sources(CONFIG)]
                _bump_scheduler_state(db,
                    holder=holder, last_tick_at=tick_at,
                    last_tick_secs=elapsed, enabled_sources=enabled_ids)
                sys.stdout.write(json.dumps({
                    "event": "tick", "at": tick_at, "elapsed_s": elapsed,
                    "fetched": result.get("fetched"),
                    "new":     result.get("new"),
                    "scoring_pending": result.get("scoring_pending"),
                }) + "\n")
                sys.stdout.flush()
            except Exception as e:
                err_summary = f"{type(e).__name__}: {str(e)[:200]}"
                try:
                    _bump_scheduler_state(db,
                        holder=holder, last_tick_at=tick_at,
                        last_tick_secs=round(time.time() - t0, 3),
                        enabled_sources=[], error=err_summary)
                except Exception:
                    pass
                sys.stdout.write(json.dumps({
                    "event": "tick_error", "at": tick_at,
                    "error": err_summary,
                }) + "\n")
                sys.stdout.flush()

            ticks += 1
            if args.once or (args.max_ticks and ticks >= args.max_ticks):
                break
            if stop_requested["v"]:
                break

            # Refresh the lease and sleep until next tick. If refresh
            # returns False, another process stole the lock — exit safely.
            if not joblock.refresh(SCHEDULER_JOB_NAME, holder=holder,
                                    lease_seconds=args.lease_seconds):
                sys.stdout.write(json.dumps({
                    "event": "lock_lost", "holder": holder,
                }) + "\n")
                return 2

            # Sleep in small slices so SIGTERM is responsive.
            slept = 0.0
            while slept < args.interval and not stop_requested["v"]:
                step = min(0.5, args.interval - slept)
                time.sleep(step)
                slept += step
    finally:
        joblock.release(SCHEDULER_JOB_NAME, holder=holder)
        sys.stdout.write(json.dumps({
            "event": "stopped", "holder": holder, "ticks": ticks,
        }) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
