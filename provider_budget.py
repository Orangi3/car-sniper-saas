"""
provider_budget.py — hard monthly caps on paid provider calls.

Paid listing APIs bill per call. Every call an adapter makes is recorded in
usage_events (user_id NULL, kind 'provider:<source>'), and an adapter must
check `remaining()` before calling. When the month's budget is spent the
source reports itself rate-limited until the 1st of next month instead of
running up an unbounded bill.

Budgets: env <SOURCE>_MONTHLY_CALL_BUDGET (e.g. MARKETCHECK_FSBO_MONTHLY_CALL_BUDGET),
cost per call: env <SOURCE>_COST_PER_CALL_CENTS. Defaults are conservative.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

DEFAULT_BUDGET = {"marketcheck_fsbo": 4000, "marketcheck": 500}
DEFAULT_COST_CENTS = {"marketcheck_fsbo": 1.0, "marketcheck": 0.2}


def _env(source: str, suffix: str) -> str | None:
    return os.environ.get(f"{source.upper()}_{suffix}")


def budget(source: str) -> int:
    try:
        return int(_env(source, "MONTHLY_CALL_BUDGET") or DEFAULT_BUDGET.get(source, 1000))
    except ValueError:
        return DEFAULT_BUDGET.get(source, 1000)


def cost_cents(source: str) -> float:
    try:
        return float(_env(source, "COST_PER_CALL_CENTS") or DEFAULT_COST_CENTS.get(source, 0))
    except ValueError:
        return DEFAULT_COST_CENTS.get(source, 0.0)


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def seconds_to_next_month(now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    s = month_start(now)
    nxt = s.replace(year=s.year + 1, month=1) if s.month == 12 else s.replace(month=s.month + 1)
    return (nxt - now).total_seconds()


def used(source: str) -> int:
    import db as _db
    with _db.session() as conn:
        return int(conn.execute(
            "SELECT COALESCE(SUM(units), 0) FROM usage_events "
            "WHERE user_id IS NULL AND kind = ? AND created_at >= ?",
            (f"provider:{source}", month_start().isoformat())).fetchone()[0])


def remaining(source: str) -> int:
    return max(0, budget(source) - used(source))


def record(source: str, calls: int = 1) -> None:
    import db as _db
    with _db.session() as conn:
        conn.execute(
            "INSERT INTO usage_events(user_id, kind, units, cost_cents, created_at) "
            "VALUES (NULL, ?, ?, ?, ?)",
            (f"provider:{source}", calls, calls * cost_cents(source),
             datetime.now(timezone.utc).isoformat()))
