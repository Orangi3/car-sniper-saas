"""
jobqueue.py — durable, database-backed background jobs.

Replaces the in-memory Queue + daemon threads that used to hold pending
scoring and notification work (and lost all of it on every restart).

Guarantees
  * Enqueue is idempotent: `dedupe_key` is UNIQUE, a second enqueue of the
    same work is a no-op. Pass the caller's connection to make the enqueue
    atomic with the write that caused it (e.g. alert row + its deliveries).
  * Claim is atomic across processes: Postgres uses FOR UPDATE SKIP LOCKED;
    SQLite uses BEGIN IMMEDIATE (single writer).
  * A claimed job carries a lease. If the worker dies, the lease expires and
    another worker reclaims the job. `attempts` is counted at claim time, so
    a job that keeps crashing its worker still ends up dead-lettered.
  * Completion/failure is fenced on (id, locked_by): a worker whose lease
    was taken over cannot overwrite the new owner's outcome.
  * Failures retry with exponential backoff + full jitter, up to
    max_attempts, then move to status 'dead' with the last error kept.

This is at-least-once execution. Handlers must be idempotent; for outbound
notifications that means a crash between "provider accepted" and "job
marked done" can produce one duplicate message (see notifications.py).
"""
from __future__ import annotations

import json
import os
import random
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

import db as _db

PENDING, RUNNING, DONE, DEAD = "pending", "running", "done", "dead"

DEFAULT_LEASE_S = 120
BACKOFF_BASE_S = 5.0
BACKOFF_CAP_S = 1800.0

# Bounded queue depth per kind. Enqueue beyond this is refused (returns
# None) so a source flood can't grow the table without limit; refused
# scoring work is picked up later by sniper.backfill_unscored().
MAX_PENDING = {"score": 20000, "notify": 50000}


class PermanentError(Exception):
    """Raise from a handler for failures that retrying cannot fix."""


def ts(dt: Optional[datetime] = None) -> str:
    """Fixed-width UTC ISO timestamp (always with microseconds) so SQLite's
    text comparison orders correctly."""
    dt = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def backoff_seconds(attempts: int, rng: random.Random = random) -> float:
    """Full-jitter exponential backoff: uniform(0, min(cap, base*2^n))."""
    ceiling = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** max(0, attempts - 1)))
    return max(1.0, rng.uniform(0, ceiling))


# ---------- enqueue -------------------------------------------------------

def enqueue(conn, kind: str, dedupe_key: str, payload: dict | None = None, *,
            priority: int = 100, max_attempts: int = 6,
            run_after: Optional[datetime] = None,
            user_id: Optional[int] = None) -> Optional[int]:
    """Insert a job using the caller's open connection (db.session()).
    Returns the new job id, or None if the dedupe_key already exists."""
    now = ts()
    row = conn.execute(
        "INSERT INTO jobs(kind, dedupe_key, payload, status, priority, "
        "  max_attempts, run_after, user_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(dedupe_key) DO NOTHING RETURNING id",
        (kind, dedupe_key, json.dumps(payload or {}, default=str), PENDING,
         int(priority), int(max_attempts), ts(run_after) if run_after else now,
         user_id, now, now)).fetchone()
    return int(row[0]) if row else None


def pending_count(conn, kind: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE kind = ? AND status IN (?, ?)",
        (kind, PENDING, RUNNING)).fetchone()[0])


def has_capacity(conn, kind: str) -> bool:
    limit = MAX_PENDING.get(kind)
    return limit is None or pending_count(conn, kind) < limit


# ---------- claim / finish ------------------------------------------------

def _claim_sql(kinds: Optional[Iterable[str]]) -> tuple[str, list]:
    kinds = list(kinds or [])
    kind_sql = f" AND kind IN ({','.join('?' for _ in kinds)})" if kinds else ""
    pick = ("SELECT id FROM jobs WHERE "
            "((status = ? AND run_after <= ?) OR (status = ? AND locked_until < ?))"
            f"{kind_sql} ORDER BY priority, run_after, id LIMIT 1")
    if _db.IS_PG:
        pick += " FOR UPDATE SKIP LOCKED"
    sql = ("UPDATE jobs SET status = ?, locked_by = ?, locked_until = ?, "
           "  attempts = attempts + 1, updated_at = ? "
           f"WHERE id = ({pick}) RETURNING *")
    return sql, kinds


def claim(worker: str, kinds: Optional[Iterable[str]] = None,
          lease_s: int = DEFAULT_LEASE_S) -> Optional[dict]:
    """Atomically take the next runnable job (or a job whose lease has
    expired). Returns the job as a dict with `payload` decoded, or None."""
    now_dt = datetime.now(timezone.utc)
    now = ts(now_dt)
    sql, kinds = _claim_sql(kinds)
    params = [RUNNING, worker, ts(now_dt + timedelta(seconds=lease_s)), now,
              PENDING, now, RUNNING, now, *kinds]
    conn = _db.session()
    try:
        if not _db.IS_PG:
            conn.raw.isolation_level = None          # manual txn control
            conn.raw.execute("BEGIN IMMEDIATE")
        row = conn.execute(sql, params).fetchone()
        if _db.IS_PG:
            conn.commit()
        else:
            conn.raw.execute("COMMIT")
    except Exception:
        if not _db.IS_PG:
            try:
                conn.raw.execute("ROLLBACK")
            except Exception:
                pass
        else:
            conn.rollback()
        raise
    finally:
        conn.close()
    if row is None:
        return None
    job = dict(row)
    try:
        job["payload"] = json.loads(job.get("payload") or "{}")
    except ValueError:
        job["payload"] = {}
    return job


def extend_lease(job_id: int, worker: str, lease_s: int = DEFAULT_LEASE_S) -> bool:
    with _db.session() as conn:
        cur = conn.execute(
            "UPDATE jobs SET locked_until = ?, updated_at = ? "
            "WHERE id = ? AND locked_by = ? AND status = ?",
            (ts(datetime.now(timezone.utc) + timedelta(seconds=lease_s)), ts(),
             job_id, worker, RUNNING))
        return cur.rowcount == 1


def complete(job_id: int, worker: str, result: Optional[dict] = None) -> bool:
    """Mark done. Returns False if this worker no longer owns the job."""
    now = ts()
    with _db.session() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = ?, result = ?, locked_by = NULL, "
            "  locked_until = NULL, updated_at = ?, finished_at = ? "
            "WHERE id = ? AND locked_by = ? AND status = ?",
            (DONE, json.dumps(result, default=str) if result is not None else None,
             now, now, job_id, worker, RUNNING))
        return cur.rowcount == 1


def fail(job_id: int, worker: str, error: str, *, permanent: bool = False,
         rng: random.Random = random) -> str:
    """Record a failure: reschedule with backoff, or dead-letter when
    permanent / out of attempts. Returns the resulting status ('' if the
    job is no longer ours)."""
    with _db.session() as conn:
        row = conn.execute(
            "SELECT attempts, max_attempts FROM jobs "
            "WHERE id = ? AND locked_by = ? AND status = ?",
            (job_id, worker, RUNNING)).fetchone()
        if row is None:
            return ""
        attempts, max_attempts = int(row[0]), int(row[1])
        now_dt = datetime.now(timezone.utc)
        if permanent or attempts >= max_attempts:
            conn.execute(
                "UPDATE jobs SET status = ?, last_error = ?, locked_by = NULL, "
                "  locked_until = NULL, updated_at = ?, finished_at = ? WHERE id = ?",
                (DEAD, error[:2000], ts(now_dt), ts(now_dt), job_id))
            return DEAD
        delay = backoff_seconds(attempts, rng)
        conn.execute(
            "UPDATE jobs SET status = ?, last_error = ?, locked_by = NULL, "
            "  locked_until = NULL, run_after = ?, updated_at = ? WHERE id = ?",
            (PENDING, error[:2000], ts(now_dt + timedelta(seconds=delay)),
             ts(now_dt), job_id))
        return PENDING


# ---------- worker loop ---------------------------------------------------

Handler = Callable[[dict], Optional[dict]]


def run_one(handlers: dict[str, Handler], worker: str,
            lease_s: int = DEFAULT_LEASE_S) -> Optional[dict]:
    """Claim and execute a single job. Returns a summary or None if idle."""
    job = claim(worker, kinds=handlers.keys(), lease_s=lease_s)
    if job is None:
        return None
    fn = handlers[job["kind"]]
    try:
        result = fn(job)
    except PermanentError as e:
        status = fail(job["id"], worker, f"permanent: {e}", permanent=True)
        return {"id": job["id"], "kind": job["kind"], "status": status, "error": str(e)}
    except Exception as e:  # retryable
        status = fail(job["id"], worker, f"{type(e).__name__}: {e}")
        return {"id": job["id"], "kind": job["kind"], "status": status,
                "error": f"{type(e).__name__}: {e}"}
    complete(job["id"], worker, result)
    return {"id": job["id"], "kind": job["kind"], "status": DONE}


def drain(handlers: dict[str, Handler], max_jobs: int = 1000,
          worker: Optional[str] = None) -> list[dict]:
    """Run jobs until none are ready (or max_jobs). Used by tests and the
    CLI's --once mode."""
    worker = worker or worker_id()
    out = []
    for _ in range(max_jobs):
        r = run_one(handlers, worker)
        if r is None:
            break
        out.append(r)
    return out


# ---------- metrics -------------------------------------------------------

def stats() -> dict:
    """Counts by kind and status, plus retried and oldest-pending age."""
    with _db.session() as conn:
        rows = conn.execute(
            "SELECT kind, status, COUNT(*) AS n, "
            "  SUM(CASE WHEN attempts > 1 THEN 1 ELSE 0 END) AS retried "
            "FROM jobs GROUP BY kind, status").fetchall()
        oldest = conn.execute(
            "SELECT MIN(run_after) FROM jobs WHERE status = ?", (PENDING,)).fetchone()[0]
    out: dict = {"by_kind": {}, "totals": {PENDING: 0, RUNNING: 0, DONE: 0, DEAD: 0},
                 "retried": 0, "oldest_pending_run_after": oldest}
    for r in rows:
        k = out["by_kind"].setdefault(r["kind"], {})
        k[r["status"]] = int(r["n"])
        out["totals"][r["status"]] = out["totals"].get(r["status"], 0) + int(r["n"])
        out["retried"] += int(r["retried"] or 0)
    return out


def dead_letters(limit: int = 50) -> list[dict]:
    with _db.session() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, kind, dedupe_key, attempts, last_error, finished_at "
            "FROM jobs WHERE status = ? ORDER BY finished_at DESC LIMIT ?",
            (DEAD, limit)).fetchall()]


def requeue_dead(job_id: int) -> bool:
    """Operator action: give a dead job a fresh set of attempts."""
    with _db.session() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = ?, attempts = 0, run_after = ?, "
            "  updated_at = ?, finished_at = NULL WHERE id = ? AND status = ?",
            (PENDING, ts(), ts(), job_id, DEAD))
        return cur.rowcount == 1
