"""
tests/conftest.py — pytest fixtures.

Each test gets a FRESH SQLite DB (function-scoped) so users / sessions /
saved_v2 rows from one test never leak into the next. That fixes the
cascading-401 you'd otherwise see — when test N tries to register an email
that test N-1 already claimed, register returns 400, the test client never
gets a session cookie, and every subsequent assertion sees 401.

We pop the four mutable modules (server, db, auth, sniper, migrations*) from
sys.modules so they re-read SNIPER_DB_PATH and re-register routes against
the fresh app. Migrations run at server import time and are idempotent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def temp_db_path(tmp_path):
    """Function-scoped temp DB. Each test starts with a clean schema; no
    state leaks across tests."""
    p = tmp_path / "listings_test.db"
    os.environ["SNIPER_DB_PATH"] = str(p)
    # Defensive: never let a stray DATABASE_URL from the shell point us at
    # Postgres in the middle of a SQLite-only test run.
    os.environ.pop("DATABASE_URL", None)
    # Open registration ON so tests can create users without an admin.
    os.environ["ALLOW_REGISTRATION"] = "1"
    return p


@pytest.fixture()
def app(temp_db_path):
    """Fresh Flask app instance against the temp DB.

    Production no longer auto-migrates at server import — that was unsafe
    under multi-worker Gunicorn — so tests now do what prod ops will do:
    run `migrations.runner.run_pending()` explicitly, THEN import server.
    server.py verifies on startup that every migration is applied; doing
    them before import keeps it happy without weakening the check."""
    # Pop every module that caches per-DB state. `billing` and `joblock`
    # are in here specifically because they call _db.transaction()
    # directly; if we don't re-import them, those calls use the previous
    # test's db module reference and see an empty/stale file.
    # `jobs.scheduler` is popped so the test runner's --once invocations
    # re-bind the module-level imports against the fresh test DB.
    for mod in ("server", "db", "auth", "sniper", "billing", "joblock",
                "jobs.scheduler", "jobs",
                "migrations.runner", "migrations"):
        sys.modules.pop(mod, None)
    import db                       # noqa: F401  (re-import w/ new SNIPER_DB_PATH)
    from migrations import runner as _runner
    _runner.run_pending(verbose=False)
    import server                   # noqa: E402  registers routes
    server.app.config["TESTING"] = True
    yield server.app


@pytest.fixture()
def client(app):
    """Werkzeug test client — preserves cookies across requests within a
    single test, so login/register flows work transparently."""
    return app.test_client()


# ---------- Helpers ---------------------------------------------------

def register(client, email, password="testpass123"):
    return client.post("/api/auth/register",
                       json={"email": email, "password": password})


def login(client, email, password="testpass123"):
    return client.post("/api/auth/login",
                       json={"email": email, "password": password})


def logout(client):
    return client.post("/api/auth/logout")


def make_admin(email: str) -> None:
    """Promote a user — direct DB write, no API surface. We're inside the
    test harness; this is the equivalent of a CLI bootstrap. NEVER expose
    a code path like this through HTTP."""
    import auth as _auth
    u = _auth.get_user_by_email(email)
    if u:
        _auth.set_role(u.id, "admin")


def set_plan(email: str, plan: str) -> None:
    import auth as _auth
    u = _auth.get_user_by_email(email)
    if u:
        _auth.set_plan(u.id, plan)


def insert_listing(composite_id: str = "test:abc",
                   title: str = "2018 Honda Civic LX",
                   price: int = 9500) -> str:
    """Insert a row into `listings` so save/unsave/browse have something
    real to reference. Uses db.transaction() (the same connection factory
    the real API uses) so it always writes to the same file the server
    reads from."""
    import db
    now = db.now_utc_iso()
    with db.transaction() as conn:
        cur = conn.cursor()
        ph = db.placeholder()
        cur.execute(
            f"INSERT INTO listings(composite_id, source, source_id, url, title, "
            f"  price, year, make, model, first_seen_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (composite_id, "test", "abc", "https://example.com/x",
             title, price, 2018, "Honda", "Civic", now))
    return composite_id
