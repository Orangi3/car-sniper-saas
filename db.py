"""
db.py — single connection factory shared by every module that needs the
listings database.

Default: SQLite at sniper/listings.db (the legacy local dev path).
Production: set DATABASE_URL=postgresql://user:pass@host/db (the SaaS path).

The SQL emitted by callers is kept portable — no SQLite-specific JSON1 or
RETURNING tricks, no Postgres-only enums. Where dialect divergence is
unavoidable (e.g. ON CONFLICT vs ON DUPLICATE) callers branch on db.IS_PG.

This module never imports the rest of the app, so it's safe to import from
auth.py, migrations/runner.py, sniper.py, and server.py without cycles.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

# ---------- Connection target ------------------------------------------

ROOT = Path(__file__).parent

# DATABASE_URL is the single switch. Empty/unset = SQLite for local dev.
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
SQLITE_PATH = Path(os.environ.get("SNIPER_DB_PATH") or (ROOT / "listings.db"))

# psycopg2 is imported lazily so SQLite-only installs don't need it.
_pg = None


def _pg_module():
    global _pg
    if _pg is None:
        try:
            import psycopg2  # noqa: F401
            import psycopg2.extras  # noqa: F401
            _pg = __import__("psycopg2")
        except ImportError as e:
            raise RuntimeError(
                "DATABASE_URL is set to Postgres but psycopg2 isn't installed. "
                "pip install 'psycopg2-binary>=2.9' (or unset DATABASE_URL "
                "to use SQLite for local dev)."
            ) from e
    return _pg


# ---------- Connection -------------------------------------------------

def connect():
    """Return a fresh DB connection.

    SQLite: row_factory set to sqlite3.Row, WAL + busy_timeout enabled, schema
    bootstrap deferred to migrations.runner.
    Postgres: returns a real psycopg2 connection with DictRow row factory.

    Callers must close (or use as a context manager via `transaction()`).
    """
    if IS_PG:
        psycopg2 = _pg_module()
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn

    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.OperationalError:
        pass
    return conn


@contextmanager
def transaction() -> Iterator:
    """Auto-commits on success, rolls back on exception. Always closes."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------- Portable SQL helpers --------------------------------------

def placeholder() -> str:
    """? for SQLite, %s for Postgres. Used by handcrafted parameterized SQL."""
    return "%s" if IS_PG else "?"


def now_utc_iso() -> str:
    """A single ISO-8601 UTC timestamp string used everywhere — keeps SQLite's
    text-date storage and Postgres's TIMESTAMPTZ interchangeable."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def describe() -> dict:
    """Diagnostic: where we're pointed. Never logs the full URL (password)."""
    if IS_PG:
        u = urlparse(DATABASE_URL)
        return {"engine": "postgres",
                "host": u.hostname, "port": u.port, "db": (u.path or "/").lstrip("/")}
    return {"engine": "sqlite", "path": str(SQLITE_PATH)}
