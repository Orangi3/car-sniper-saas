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
        # Session timezone pinned to UTC, and timestamps returned as ISO-8601
        # strings — the exact format SQLite stores — so code that compares or
        # serializes dates behaves identically on both engines.
        conn = psycopg2.connect(DATABASE_URL, options="-c timezone=UTC")
        _register_iso_timestamps(conn)
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


def _register_iso_timestamps(conn) -> None:
    import psycopg2.extensions as ext
    from datetime import datetime as _dt
    base_ts = ext.string_types.get(1184)   # timestamptz
    base_t = ext.string_types.get(1114)    # timestamp
    base_d = ext.string_types.get(1082)    # date

    def _mk(base):
        def cast(value, cur):
            if value is None:
                return None
            v = base(value, cur)
            return v.isoformat() if hasattr(v, "isoformat") else v
        return cast

    for oid, base, name in ((1184, base_ts, "TSTZ_ISO"), (1114, base_t, "TS_ISO"),
                            (1082, base_d, "DATE_ISO")):
        if base is not None:
            ext.register_type(ext.new_type((oid,), name, _mk(base)), conn)


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


# ---------- Portable connection wrapper -------------------------------
#
# Application code (sniper.py, server.py read paths, workers) talks to the
# database through `session()`. It accepts SQLite-style `?` placeholders on
# both engines and returns rows that behave identically everywhere:
#   * row["col"] and row[0] both work; dict(row) gives a plain dict
#   * datetimes come back as ISO-8601 strings (SQLite stores text; Postgres
#     TIMESTAMPTZ would otherwise serialize differently in JSON)
#   * booleans come back as 0/1 and NUMERIC as float, matching SQLite
#
# Portable-SQL rules for callers: compare boolean columns with TRUE/FALSE
# (SQLite treats them as 1/0), pass Python bools for boolean values, use
# `INSERT … RETURNING id` instead of cursor.lastrowid, and use
# `ON CONFLICT … DO NOTHING/UPDATE` instead of catching IntegrityError
# (a failed statement aborts the whole transaction on Postgres).

import decimal as _decimal
from datetime import date as _date, datetime as _datetime


def _pg_errors():
    if not IS_PG:
        return ()
    psycopg2 = _pg_module()
    return (psycopg2.IntegrityError,)


IntegrityError = (sqlite3.IntegrityError,)   # extended lazily in session()


def _norm_value(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, _datetime):
        return v.isoformat()
    if isinstance(v, _date):
        return v.isoformat()
    if isinstance(v, _decimal.Decimal):
        return float(v)
    return v


class Row:
    """sqlite3.Row-compatible row: index by name or position, iterate over
    values, dict(row) works via keys()."""
    __slots__ = ("_keys", "_vals")

    def __init__(self, keys, vals):
        self._keys = list(keys)
        self._vals = [_norm_value(v) for v in vals]

    def keys(self):
        return list(self._keys)

    def __getitem__(self, k):
        if isinstance(k, (int, slice)):
            return self._vals[k]
        try:
            return self._vals[self._keys.index(k)]
        except ValueError:
            raise KeyError(k) from None

    def get(self, k, default=None):
        try:
            return self[k]
        except (KeyError, IndexError):
            return default

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __contains__(self, k):
        return k in self._keys

    def __repr__(self):
        return f"Row({dict(zip(self._keys, self._vals))!r})"


def translate(sql: str) -> str:
    """`?` → `%s` for Postgres (outside quoted literals), with literal `%`
    escaped. SQLite SQL is returned unchanged."""
    if not IS_PG:
        return sql
    out, in_str = [], False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "%":
            out.append("%%")
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


class Cursor:
    def __init__(self, raw):
        self._raw = raw

    def _cols(self):
        d = self._raw.description
        return [c[0] for c in d] if d else []

    def _wrap(self, r):
        if r is None:
            return None
        if isinstance(r, dict):
            return Row(r.keys(), r.values())
        return Row(self._cols(), r)

    def fetchone(self):
        return self._wrap(self._raw.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._raw.rowcount


class Conn:
    """Thin portable connection. Use as a context manager: commits on
    success, rolls back on error, always closes."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql: str, params=()) -> Cursor:
        cur = self.raw.cursor()
        cur.execute(translate(sql), tuple(params))
        return Cursor(cur)

    def executemany(self, sql: str, seq) -> None:
        cur = self.raw.cursor()
        cur.executemany(translate(sql), [tuple(p) for p in seq])

    def commit(self):
        self.raw.commit()

    def rollback(self):
        try:
            self.raw.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def session() -> Conn:
    """Portable connection to the configured database (see module notes)."""
    global IntegrityError
    if IS_PG and len(IntegrityError) == 1:
        IntegrityError = (sqlite3.IntegrityError, *_pg_errors())
    raw = connect()
    if IS_PG:
        # Plain tuple cursor; Cursor wraps rows itself.
        raw.cursor_factory = None
        import psycopg2.extensions as _ext
        raw.cursor_factory = _ext.cursor
    return Conn(raw)


def bool_sql(v: bool) -> str:
    """Literal for a boolean comparison that works on both engines."""
    return "TRUE" if v else "FALSE"
