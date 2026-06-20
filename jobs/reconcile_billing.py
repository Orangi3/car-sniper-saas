"""
jobs/reconcile_billing.py — internal CLI for Stripe-backed reconciliation.

Run by a systemd timer on the application host. Never exposed over HTTP.

    python3 -m jobs.reconcile_billing                  # normal run
    python3 -m jobs.reconcile_billing --dry-run        # report only
    python3 -m jobs.reconcile_billing --max-count 50 --time-budget 30

Exit codes:
    0   success (run completed, regardless of mismatches found)
    1   operational failure (DB unreachable, missing Stripe key, etc.)
    2   lock not acquired (another reconcile is already running)
    3   reconcile completed but reported errors talking to Stripe
        (mismatches/corrections still applied where possible)

What this command guarantees:
  * The same verified Stripe integration the webhook handlers use.
  * Existing reconciliation rules (out-of-order protection, anomaly
    audit trail, transactional corrections) preserved.
  * DB-backed advisory lock prevents overlapping runs across hosts.
  * Never prints raw Stripe secrets, raw event payloads, customer
    payment data, or authorization headers. Output is a single
    machine-readable JSON line summarising the run.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import uuid
from pathlib import Path

# Make the project importable when invoked as `python -m jobs.reconcile_billing`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _safe_env_present(name: str) -> bool:
    """Existence check that NEVER reads the value. Avoids any risk of an
    env var being interpolated into a log line by accident."""
    return bool(os.environ.get(name))


def _build_holder_token() -> str:
    """Short opaque identifier for the lock holder: host:pid:randomshort.
    No secrets; safe to log."""
    short = uuid.uuid4().hex[:8]
    return f"{socket.gethostname()}:{os.getpid()}:{short}"


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="jobs.reconcile_billing",
        description=(
            "Run Stripe-backed billing reconciliation. Internal command — "
            "never expose this over HTTP."),
    )
    p.add_argument("--max-count", type=int, default=200,
                   help="Maximum subscription rows to check (default 200)")
    p.add_argument("--time-budget", type=int, default=60,
                   help="Wall-clock seconds before stopping (default 60)")
    p.add_argument("--sleep-between", type=float, default=0.05,
                   help="Seconds to sleep between Stripe calls (default 0.05)")
    p.add_argument("--lease-seconds", type=int, default=600,
                   help="DB lock lease in seconds (default 600)")
    p.add_argument("--steal-after", type=int, default=3600,
                   help="Steal a stale lock older than N seconds (default 3600)")
    p.add_argument("--dry-run", action="store_true",
                   help="Report mismatches but never write corrections")
    p.add_argument("--triggered-by", default="cron",
                   help="Free-form tag stored on the run row (default 'cron')")
    return p.parse_args(argv)


def main(argv=None) -> int:
    # `argv if argv is not None` is intentional — an empty list is a
    # legitimate "no flags" invocation; we must NOT fall through to
    # sys.argv (which during pytest contains the pytest CLI flags).
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # Pre-flight: required env present. We only check NAMES are set; never
    # read or echo the values themselves.
    missing = [name for name in
               ("STRIPE_SECRET_KEY",)  # webhook secret + price IDs aren't
                                       # needed for reconciliation reads
               if not _safe_env_present(name)]
    if missing:
        sys.stderr.write(
            "reconcile_billing: missing required env var(s): "
            + ",".join(missing) + "\n")
        return 1

    # Load app modules. Migrations are NOT auto-applied — that's the deploy
    # script's job (`python3 -m migrations.runner`). If the schema is behind
    # we fail loudly.
    try:
        import db                                 # noqa: F401
        import billing                            # noqa: F401
    except Exception as e:
        sys.stderr.write(f"reconcile_billing: import failure: {e}\n")
        return 1

    holder = _build_holder_token()

    try:
        summary = billing.reconcile_billing_with_lock(
            holder=holder,
            lease_seconds=args.lease_seconds,
            steal_after_seconds=args.steal_after,
            dry_run=args.dry_run,
            max_count=args.max_count,
            time_budget_s=args.time_budget,
            sleep_between_s=args.sleep_between,
            triggered_by=args.triggered_by,
        )
    except Exception as e:
        # Catch only at the outermost layer so anything that survived
        # billing's per-row try/except is logged with type but no payload.
        sys.stderr.write(
            f"reconcile_billing: unhandled error: {type(e).__name__}\n")
        return 1

    # One JSON line on stdout — easy for journalctl + grep + monitoring.
    # Strip any field that might contain Stripe-side detail beyond IDs.
    redacted = dict(summary)
    # last_error stays — it's a Python type name + truncated str, never a
    # secret or payment payload — but enforce length again defensively.
    if isinstance(redacted.get("last_error"), str):
        redacted["last_error"] = redacted["last_error"][:240]
    sys.stdout.write(json.dumps(redacted, sort_keys=True) + "\n")

    if not summary.get("lock_acquired", True):
        return 2  # another instance is already running

    if int(summary.get("errors") or 0) > 0:
        return 3  # ran, but had Stripe API errors mid-loop

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
