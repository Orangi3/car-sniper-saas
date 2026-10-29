"""
invites.py — single-use invitations for the invite-only pilot.

With ALLOW_REGISTRATION=0, an account can be created only with an
invitation the owner issued:

    python -m invites issue --note "Pat (seller)" [--count 10] [--days 14]
    python -m invites list
    python -m invites revoke ID

Each link (https://<host>/login#invite=<token>) is printed once and works
once. Only the token's SHA-256 is stored; the token travels in the URL
fragment, which browsers never send to the server, so it never reaches
server, proxy or application logs.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

DEFAULT_DAYS = 14


class InviteError(Exception):
    """Invalid, expired, revoked or already used (deliberately one message)."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _db():
    import db          # imported late, like seller_listings: db is re-bound per test DB
    return db


def _ph() -> str:
    return "%s" if _db().IS_PG else "?"


def _exec(conn, sql: str, params: tuple = ()):
    if _db().IS_PG:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur
    return conn.execute(sql, params)


def issue(count: int = 1, note: str | None = None, days: int = DEFAULT_DAYS) -> list[tuple[int, str]]:
    """Create `count` invitations; returns [(id, token)]. Tokens are not
    stored and cannot be shown again."""
    now = datetime.now(timezone.utc)
    out = []
    with _db().transaction() as conn:
        for _ in range(count):
            token = secrets.token_urlsafe(32)
            _exec(conn, f"INSERT INTO invitations(token_hash, note, created_at, expires_at) "
                        f"VALUES ({_ph()},{_ph()},{_ph()},{_ph()})",
                  (_hash(token), note, now.isoformat(), (now + timedelta(days=days)).isoformat()))
            row = _exec(conn, f"SELECT id FROM invitations WHERE token_hash = {_ph()}",
                        (_hash(token),)).fetchone()
            out.append((int(row["id"] if _db().IS_PG else row[0]), token))
    return out


def claim(conn, token, user_id: int) -> None:
    """Mark the invitation used by `user_id`, inside the caller's
    transaction. One conditional UPDATE: of two concurrent registrations
    with the same token exactly one matches; if the caller's transaction
    rolls back (e.g. email already registered) the invitation stays unused."""
    if not isinstance(token, str) or not token.strip():
        raise InviteError()
    now = datetime.now(timezone.utc).isoformat()
    cur = _exec(conn, f"UPDATE invitations SET used_at = {_ph()}, used_by_user_id = {_ph()} "
                      f"WHERE token_hash = {_ph()} AND used_at IS NULL AND revoked_at IS NULL "
                      f"AND expires_at > {_ph()}",
                (now, user_id, _hash(token.strip()), now))
    if cur.rowcount != 1:
        raise InviteError()


def revoke(invite_id: int) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with _db().transaction() as conn:
        cur = _exec(conn, f"UPDATE invitations SET revoked_at = {_ph()} WHERE id = {_ph()} "
                          f"AND used_at IS NULL AND revoked_at IS NULL", (now, int(invite_id)))
        return cur.rowcount == 1


def list_all() -> list[dict]:
    with _db().transaction() as conn:
        rows = _exec(conn, "SELECT i.id, i.note, i.created_at, i.expires_at, i.used_at, "
                           "i.revoked_at, u.email AS used_by FROM invitations i "
                           "LEFT JOIN users u ON u.id = i.used_by_user_id ORDER BY i.id").fetchall()
    return [dict(r) for r in rows]


def _status(r: dict, now: str) -> str:
    if r["used_at"]:
        return f"used by {r['used_by'] or '(deleted account)'}"
    if r["revoked_at"]:
        return "revoked"
    return "expired" if r["expires_at"] <= now else "open"


def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m invites")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("issue", help="create invitation links (each printed once)")
    i.add_argument("--count", type=int, default=1)
    i.add_argument("--note", help="who it is for, e.g. 'Pat (seller)' — not secret")
    i.add_argument("--days", type=int, default=DEFAULT_DAYS)
    i.add_argument("--origin", default=os.environ.get("BILLING_ORIGIN", ""))
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("id", type=int)
    a = ap.parse_args(argv)
    if a.cmd == "issue":
        if not a.origin or not (1 <= a.count <= 50) or not (1 <= a.days <= 90):
            print("need --origin/BILLING_ORIGIN, --count 1..50, --days 1..90")
            return 1
        print(f"Send each link to one person yourself. Single use, expires in {a.days} days.")
        print("These links are shown only now; they are not stored.")
        for iid, token in issue(a.count, a.note, a.days):
            print(f"  #{iid}  {a.origin.rstrip('/')}/login#invite={token}")
        return 0
    if a.cmd == "list":
        now = datetime.now(timezone.utc).isoformat()
        for r_ in list_all():
            print(f"#{r_['id']:<4} {_status(r_, now):<36} expires {r_['expires_at'][:10]}  "
                  f"{r_['note'] or ''}")
        return 0
    ok = revoke(a.id)
    print("revoked" if ok else "not revoked (unknown, already used, or already revoked)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
