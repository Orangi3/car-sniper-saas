"""
migrations/runner.py — tiny forward-only migration runner.

Every .sql file in this directory is applied in lexicographic order. A
schema_migrations table tracks which IDs have already run. A migration is
applied in a single transaction; if it raises, nothing from that file
commits and the runner stops (no partial state).

Files are SQL — keep them dialect-portable. If a migration absolutely
needs dialect-specific code, name it `0042_thing.sqlite.sql` or
`0042_thing.postgres.sql` — the runner picks the variant that matches
the active engine (and falls back to the plain name if no variant matches).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

import db

HERE = Path(__file__).parent

_FNAME_RE = re.compile(r"^(\d+)_([a-z0-9_]+?)(?:\.(sqlite|postgres))?\.sql$")


def _list_migrations() -> List[Tuple[str, Path]]:
    """[(migration_id, path), ...] in apply order, with dialect variants
    collapsed to the one matching the active engine."""
    by_id: dict[str, dict[str, Path]] = {}
    for p in sorted(HERE.iterdir()):
        m = _FNAME_RE.match(p.name)
        if not m:
            continue
        mid = f"{m.group(1)}_{m.group(2)}"
        dialect = m.group(3) or "any"
        by_id.setdefault(mid, {})[dialect] = p
    pref = "postgres" if db.IS_PG else "sqlite"
    out: List[Tuple[str, Path]] = []
    for mid in sorted(by_id):
        variants = by_id[mid]
        chosen = variants.get(pref) or variants.get("any")
        if chosen:
            out.append((mid, chosen))
    return out


def _ensure_table(conn) -> None:
    """schema_migrations bootstrap — runs every time, idempotent."""
    if db.IS_PG:
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id          TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id          TEXT PRIMARY KEY,
                applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


def _applied_ids(conn) -> set[str]:
    if db.IS_PG:
        cur = conn.cursor()
        cur.execute("SELECT id FROM schema_migrations")
        rows = cur.fetchall()
        # RealDictCursor returns dict rows
        return {r["id"] for r in rows}
    return {r[0] for r in conn.execute("SELECT id FROM schema_migrations").fetchall()}


def _exec_script(conn, sql: str) -> None:
    """Run a (potentially multi-statement) script. Both engines support it,
    but the call shape differs."""
    if db.IS_PG:
        cur = conn.cursor()
        cur.execute(sql)
    else:
        conn.executescript(sql)


def run_pending(verbose: bool = True) -> list[str]:
    """Apply every migration that hasn't run yet. Returns the IDs applied
    in this call. Idempotent and safe at every startup."""
    conn = db.connect()
    try:
        _ensure_table(conn)
        try:
            conn.commit()
        except Exception:
            pass
        done = _applied_ids(conn)
        applied: list[str] = []
        for mid, path in _list_migrations():
            if mid in done:
                continue
            sql = path.read_text()
            try:
                _exec_script(conn, sql)
                if db.IS_PG:
                    cur = conn.cursor()
                    cur.execute("INSERT INTO schema_migrations(id) VALUES (%s)", (mid,))
                else:
                    conn.execute("INSERT INTO schema_migrations(id) VALUES (?)", (mid,))
                conn.commit()
                applied.append(mid)
                if verbose:
                    print(f"[migrate] applied {mid}  ({path.name})")
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise RuntimeError(f"migration {mid} failed: {e}") from e
        return applied
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    print(f"[migrate] target = {db.describe()}")
    applied = run_pending()
    print(f"[migrate] {len(applied)} new migration(s) applied")
