"""
accounts.py — owner tools for FlipFirst accounts.

    python -m accounts delete pat@example.com          # dry run: shows what would go
    python -m accounts delete pat@example.com --yes    # deletes
    python -m accounts set-password pat@example.com    # locked out: new random password
    python -m accounts set-password pat@example.com --stdin   # one the person chose

set-password exists because there is no self-serve password reset yet. It
replaces the password and signs the account out everywhere. Give the new
password to the person over a channel you already trust, never by email to
an address the account has not verified.

What a deletion removes:
  * the user's seller listings, through seller_listings.remove() (listing,
    valuation, price history, other buyers' saved copies and matches)
  * then, in one transaction: their jobs, usage events, sessions, saved
    searches and deals, analyzed deals (opportunities), matches, alert
    history, seller contacts and verifications, subscription mirrors and
    listing tombstones (deleted explicitly — never relying on foreign-key
    cascades), the name on the invitation they used, Stripe webhook
    payloads that mention their email or customer id (redacted; the event
    id stays for idempotency), and the users row

Refused: an account with a subscription that may still bill (cancel it in
Stripe first — Stripe keeps its own billing records), and the last admin.
Not reached: server logs and encrypted backups age out on their own
schedules (see legal/DATA_HANDLING.md).
"""
from __future__ import annotations

import argparse
import sys

LIVE_SUBSCRIPTION = ("active", "trialing", "past_due", "unpaid", "incomplete")
_USER_TABLES = ("jobs", "usage_events", "sessions", "saved_searches", "saved_v2",
                "user_matches", "user_alerts", "seller_contacts", "contact_verifications",
                "subscriptions", "opportunities", "seller_profiles", "listing_reports",
                "alert_suppressions")


class Refused(Exception):
    pass


def plan(conn, user_id: int) -> dict:
    """Rows that belong to the user, by table."""
    out = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?",
                           (user_id,)).fetchone()[0] for t in _USER_TABLES}
    out["seller_listings"] = conn.execute(
        "SELECT COUNT(*) FROM seller_listings WHERE user_id = ? AND status <> 'removed'",
        (user_id,)).fetchone()[0]
    return out


def delete(email: str, *, confirm: bool = False) -> dict:
    """Delete the account for `email` (dry run unless confirm=True)."""
    import auth
    import db as _db
    import seller_listings
    user = auth.get_user_by_email(email)
    if not user:
        raise Refused(f"no account for {email}")
    with _db.session() as conn:
        live = conn.execute(
            f"SELECT COUNT(*) FROM subscriptions WHERE user_id = ? AND status IN "
            f"({','.join('?' for _ in LIVE_SUBSCRIPTION)})",
            (user.id, *LIVE_SUBSCRIPTION)).fetchone()[0]
        admins = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        rows = plan(conn, user.id)
        listing_ids = [r[0] for r in conn.execute(
            "SELECT id FROM seller_listings WHERE user_id = ? AND status <> 'removed'",
            (user.id,)).fetchall()]
    if live:
        raise Refused("subscription may still bill: cancel it in Stripe first")
    if user.is_admin and admins <= 1:
        raise Refused("this is the only admin account")
    if not confirm:
        return {"email": email, "deleted": False, "would_delete": rows}
    for sid in listing_ids:
        seller_listings.remove(user, sid)
    with _db.session() as conn:                  # one transaction; rolled back on Refused
        if _db.IS_PG:                            # two deletions at once can't both pass the admin check
            conn.execute("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")
        if conn.execute("SELECT COUNT(*) FROM seller_listings WHERE user_id = ? AND "
                        "status <> 'removed'", (user.id,)).fetchone()[0]:
            raise Refused("a new listing appeared while deleting; run the command again")
        needles = [email] + [r[0] for r in conn.execute(
            "SELECT DISTINCT stripe_customer_id FROM subscriptions WHERE user_id = ?",
            (user.id,)).fetchall() if r[0]]
        for needle in needles:
            like = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conn.execute("UPDATE stripe_webhook_events SET payload = '{\"redacted\": true}' "
                         "WHERE payload LIKE ? ESCAPE '\\'", (like,))
        conn.execute("UPDATE invitations SET note = NULL WHERE used_by_user_id = ?", (user.id,))
        for table in _USER_TABLES + ("seller_listings",):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user.id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user.id,))
        if user.is_admin and not conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]:
            raise Refused("this is the only admin account")
    return {"email": email, "deleted": True, "removed": rows}


def set_password(email: str, password: str | None = None) -> dict:
    """Owner tool for a locked-out user: set a new password (a random one
    unless `password` is given; at least 8 characters either way) and end
    every session of the account."""
    import secrets
    import auth
    import db as _db
    user = auth.get_user_by_email(email)
    if user is None:
        raise Refused(f"no account for {email}")
    new = password if password is not None else secrets.token_urlsafe(12)
    pw_hash = auth.hash_password(new)          # refuses < 8 or > 200 characters
    with _db.session() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user.id))
    return {"email": user.email, "password": new, "generated": password is None,
            "sessions_revoked": auth.revoke_sessions(user.id)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m accounts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("delete", help="delete an account on the owner's request")
    d.add_argument("email")
    d.add_argument("--yes", action="store_true", help="really delete (default: dry run)")
    p = sub.add_parser("set-password", help="give a locked-out user a new password")
    p.add_argument("email")
    p.add_argument("--stdin", action="store_true",
                   help="read the new password from stdin (never from the command line)")
    a = ap.parse_args(argv)
    if a.cmd == "set-password":
        chosen = sys.stdin.readline().rstrip("\n") if a.stdin else None
        try:
            r = set_password(a.email.strip().lower(), chosen)
        except (Refused, ValueError) as e:
            print(f"refused: {e}", file=sys.stderr)
            return 1
        if r["generated"]:
            print(f"new password for {r['email']} (shown once; give it to them privately): "
                  f"{r['password']}")
        else:
            print(f"password set for {r['email']}")
        print(f"signed out of {r['sessions_revoked']} session(s)")
        return 0
    try:
        r = delete(a.email.strip().lower(), confirm=a.yes)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    counts = r.get("removed") or r.get("would_delete")
    print(("deleted " if r["deleted"] else "dry run — would delete ") + r["email"])
    for table, n in counts.items():
        if n:
            print(f"  {table}: {n}")
    if not r["deleted"]:
        print("re-run with --yes to delete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
