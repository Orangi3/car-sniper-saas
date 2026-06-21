"""
joblock.py — small DB-backed advisory lock used by every periodic job.

Backed by the `billing_job_locks` table (migrations 0005 + 0006). Same
single-row conditional UPDATE primitive that already powers
`billing.acquire_job_lock`; reused here so the polling scheduler and the
reconciliation job share one mechanism. Works on SQLite and Postgres.

Public API:
    acquire(job_name, holder, lease_seconds, steal_after_seconds) -> bool
    refresh(job_name, holder, lease_seconds) -> bool        # extends lease
    release(job_name, holder) -> None
    current_holder(job_name) -> dict | None

Holder tokens are short opaque strings (host:pid:rand). Never include
secrets or user identifiers — these tokens land in logs and the admin
health JSON.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import db as _db


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def acquire(job_name: str, *, holder: str,
            lease_seconds: int = 600,
            steal_after_seconds: int = 3600) -> bool:
    """Take the named lock. Returns True on success, False if someone else
    holds it within the steal-after window."""
    now = _now_utc()
    expires = now + timedelta(seconds=lease_seconds)
    steal_cutoff = now - timedelta(seconds=steal_after_seconds)
    ph = _db.placeholder()
    with _db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE billing_job_locks "
            f"SET holder = {ph}, acquired_at = {ph}, expires_at = {ph}, "
            f"    updated_at = {ph} "
            f"WHERE job_name = {ph} "
            f"  AND (holder IS NULL OR holder = '' OR expires_at < {ph})",
            (holder, now.isoformat(), expires.isoformat(), now.isoformat(),
             job_name, steal_cutoff.isoformat()))
        return cur.rowcount > 0


def refresh(job_name: str, *, holder: str, lease_seconds: int) -> bool:
    """Extend the lease for the current holder. Returns False if the lock
    was stolen out from under us (lease expired and another worker took
    over); the caller should treat that as a fatal signal and exit."""
    now = _now_utc()
    expires = now + timedelta(seconds=lease_seconds)
    ph = _db.placeholder()
    with _db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE billing_job_locks "
            f"SET expires_at = {ph}, updated_at = {ph} "
            f"WHERE job_name = {ph} AND holder = {ph}",
            (expires.isoformat(), now.isoformat(), job_name, holder))
        return cur.rowcount > 0


def release(job_name: str, *, holder: str) -> None:
    """Drop the lock — only if we still hold it (avoid releasing a lock
    another instance has already stolen after our lease expired)."""
    ph = _db.placeholder()
    with _db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE billing_job_locks "
            f"SET holder = NULL, acquired_at = NULL, expires_at = NULL, "
            f"    updated_at = {ph} "
            f"WHERE job_name = {ph} AND holder = {ph}",
            (_now_utc().isoformat(), job_name, holder))


def current_holder(job_name: str) -> Optional[dict]:
    ph = _db.placeholder()
    with _db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT holder, acquired_at, expires_at, updated_at "
            f"FROM billing_job_locks WHERE job_name = {ph}",
            (job_name,))
        row = cur.fetchone()
        if not row:
            return None
        return {k: row[k] for k in ("holder", "acquired_at",
                                     "expires_at", "updated_at")}
