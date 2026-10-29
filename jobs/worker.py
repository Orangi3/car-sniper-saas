"""
jobs/worker.py — executes durable background jobs (scoring, notifications).

Runs alongside jobs/scheduler.py (which only ingests). Any number of worker
processes may run at once — claims are atomic, and a job held by a worker
that dies is reclaimed when its lease expires.

Usage:
    python3 -m jobs.worker                       # forever
    python3 -m jobs.worker --once                # drain ready jobs, exit
    python3 -m jobs.worker --threads 4           # parallel claimers
    python3 -m jobs.worker --kinds notify        # only some job kinds

One JSON line per processed job on stdout (journalctl-friendly).
Exit codes: 0 normal stop, 1 import/DB failure.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def handlers() -> dict:
    import notifications
    import sniper
    return {"score": sniper.score_job, "notify": notifications.deliver_job}


def _parse(argv):
    p = argparse.ArgumentParser(description="Durable job worker")
    p.add_argument("--once", action="store_true",
                   help="process every ready job, then exit")
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--kinds", default="",
                   help="comma-separated job kinds (default: all)")
    p.add_argument("--idle-sleep", type=float, default=1.0)
    p.add_argument("--lease-seconds", type=int, default=120)
    p.add_argument("--max-jobs", type=int, default=0,
                   help="exit after this many jobs (0 = no limit)")
    return p.parse_args(argv)


def run(argv=None) -> int:
    args = _parse(argv if argv is not None else sys.argv[1:])
    try:
        import jobqueue
        all_h = handlers()
    except Exception as e:
        sys.stderr.write(f"worker: import failure: {e}\n")
        return 1
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    h = {k: v for k, v in all_h.items() if not kinds or k in kinds}

    stop = threading.Event()

    def _stop(signum, frame):
        stop.set()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    processed = {"n": 0}
    lock = threading.Lock()

    def loop():
        wid = jobqueue.worker_id()
        while not stop.is_set():
            try:
                r = jobqueue.run_one(h, wid, lease_s=args.lease_seconds)
            except Exception as e:      # DB hiccup — back off, keep going
                sys.stdout.write(json.dumps({"event": "worker_error",
                                             "error": f"{type(e).__name__}: {e}"}) + "\n")
                time.sleep(min(30, args.idle_sleep * 5))
                continue
            if r is None:
                if args.once:
                    return
                stop.wait(args.idle_sleep)
                continue
            with lock:
                processed["n"] += 1
                sys.stdout.write(json.dumps({"event": "job", **r}, default=str) + "\n")
                sys.stdout.flush()
                if args.max_jobs and processed["n"] >= args.max_jobs:
                    stop.set()

    threads = [threading.Thread(target=loop, name=f"jobworker-{i}", daemon=True)
               for i in range(max(1, args.threads))]
    for t in threads:
        t.start()
    for t in threads:
        while t.is_alive():
            t.join(timeout=0.5)
    sys.stdout.write(json.dumps({"event": "stopped", "processed": processed["n"]}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
