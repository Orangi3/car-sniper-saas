"""
seller_listings.py — vehicle owners list their own car (first-party data).

Why: licensed listing APIs mostly carry dealer inventory, the only licensed
private-party feed found needs a written agreement, and eBay's production
Buy APIs need partner approval. A seller who lists their own car, with
consent, is authentic private-party inventory that Car Sniper is entitled
to use — no third party's permission involved.

OFF by default (SNIPER_SELLER_SUBMISSIONS=1). Lifecycle:

  pending   submitted and validated, waiting for the seller to verify their
            contact email; not visible, not valued, never alerts
  active    published: valued, matched, alertable, contact shown to paid
            buyers. Discoverable only while the contact is still verified
            and the seller re-confirmed it within CONFIRM_DAYS
  (expired) an active listing not re-confirmed in CONFIRM_DAYS: hidden from
            discovery until the seller taps "still available"
  removed   withdrawn by the seller or an admin: the listing and everything
            derived from it is deleted; an audit stub without vehicle or
            contact data remains

Contact verification (mailer.py): a single-use token, stored only as a
SHA-256 hash, expiring after TOKEN_TTL_H, bound to the account AND the
address; changing the address revokes earlier verification and tokens;
sends and failed attempts are rate-limited. Without SMTP configured, nothing
is published. The `issue-link` CLI (hand-delivered link) exists for isolated
development/testing only: it refuses to run in commercial mode or without
SNIPER_ALLOW_MANUAL_VERIFY_LINKS=1, every issue is audited
(usage_events 'seller_manual_verify_link', units = token id) and a
verification through such a link is audited separately
('seller_verified_manual_link'), never passed off as an emailed one.

Verification proves control of the email address only. It does NOT
verify ownership of the vehicle or any detail the seller entered.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

SOURCE = "direct"
MAX_ACTIVE_PER_USER = int(os.environ.get("SELLER_MAX_ACTIVE_PER_USER", "3"))
MAX_NEW_PER_USER_PER_DAY = int(os.environ.get("SELLER_MAX_NEW_PER_USER_PER_DAY", "5"))
MAX_NEW_PER_DAY = int(os.environ.get("SELLER_MAX_NEW_PER_DAY", "50"))
CONFIRM_DAYS = int(os.environ.get("SELLER_CONFIRM_DAYS", "30"))
TOKEN_TTL_H = 24
RESEND_MIN_GAP_S = 60
MAX_SENDS_PER_DAY = 5
MAX_FAILED_VERIFY_PER_HOUR = 10
ATTESTATIONS = ("authorized_to_sell", "details_accurate", "share_contact_with_buyers")
TITLE_STATUSES = ("clean", "rebuilt", "salvage", "unknown")
LIVE = ("pending", "active")
_EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


class SubmissionError(Exception):
    def __init__(self, errors: list[str], status: int = 400):
        super().__init__("; ".join(errors))
        self.errors, self.status = errors, status


def enabled() -> bool:
    return os.environ.get("SNIPER_SELLER_SUBMISSIONS", "").strip().lower() in ("1", "true", "yes")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _int(v):
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _lock_user(conn, user_id: int) -> None:
    """Serialize this account's seller writes (PG row lock / SQLite write
    lock) so concurrent requests can't both pass a limit check."""
    conn.execute("UPDATE users SET id = id WHERE id = ?", (user_id,))


# ---------- validation ----------------------------------------------------

def validate(p: dict) -> tuple[dict, list[str]]:
    """Clean fields + list of problems (empty = valid). Only the fields
    below are read; anything else in the request is ignored."""
    import geo
    import vin as vin_mod
    errs: list[str] = []
    year, price, miles = _int(p.get("year")), _int(p.get("price")), _int(p.get("mileage"))
    this_year = _now().year
    if year is None or not 1981 <= year <= this_year + 1:
        errs.append(f"year must be between 1981 and {this_year + 1}")
    make = " ".join(str(p.get("make") or "").split())[:30]
    model = " ".join(str(p.get("model") or "").split())[:40]
    trim = " ".join(str(p.get("trim") or "").split())[:30] or None
    if not make.replace("-", "").replace(" ", "").isalnum():
        errs.append("make is required (letters and numbers)")
    if not model or not all(c.isalnum() or c in " -./" for c in model):
        errs.append("model is required (letters, numbers, - . /)")
    if trim and not all(c.isalnum() or c in " -./" for c in trim):
        errs.append("trim may only contain letters, numbers, - . /")
    if miles is None or not 0 <= miles <= 500_000:
        errs.append("mileage must be 0–500,000")
    if price is None or not 500 <= price <= 250_000:
        errs.append("price must be $500–$250,000")
    zip_code = str(p.get("zip") or "").strip()[:5]
    if not geo.valid_zip(zip_code):
        errs.append("a valid US ZIP code is required")
    title_status = str(p.get("title_status") or "").lower()
    if title_status not in TITLE_STATUSES:
        errs.append("title status is required (clean, rebuilt, salvage, or unknown)")
    vin = str(p.get("vin") or "").strip().upper() or None
    if vin:
        v = vin_mod.validate_vin(vin)
        if not v["ok"]:
            errs.append("VIN: " + "; ".join(v["errors"]))
    mech = str(p.get("mechanical_issues") or "").strip()[:1000]
    cosm = str(p.get("cosmetic_issues") or "").strip()[:1000]
    description = str(p.get("description") or "").strip()[:2000]
    att = p.get("attest") or {}
    missing = [a for a in ATTESTATIONS if att.get(a) is not True]
    if missing:
        words = {"authorized_to_sell": "you may sell this car",
                 "details_accurate": "the details are accurate",
                 "share_contact_with_buyers": "buyers may see your contact email"}
        errs.append("please confirm that " + "; ".join(words[m] for m in missing))
    return ({"year": year, "make": make.title() if make.islower() else make,
             "model": model, "trim": trim, "mileage": miles, "price": price,
             "zip": zip_code, "title_status": title_status, "vin": vin,
             "mechanical_issues": mech, "cosmetic_issues": cosm,
             "description": description}, errs)


def _describe(f: dict) -> str:
    """Seller-reported text, labeled as such. VIN and branded title go on
    their own lines because ingestion keys duplicates and the branded-title
    flag on them."""
    lines = []
    if f.get("vin"):
        lines.append(f"VIN: {f['vin']}")
    if f["title_status"] in ("rebuilt", "salvage"):
        lines.append(f"{f['title_status']} title")
    lines.append(f"Title (seller-reported): {f['title_status']}")
    lines.append("Known mechanical issues (seller-reported): " + (f.get("mechanical_issues") or "none stated"))
    lines.append("Known cosmetic issues (seller-reported): " + (f.get("cosmetic_issues") or "none stated"))
    if f.get("description"):
        lines.append(f["description"])
    return "\n".join(lines)


def _to_listing(sid: int, f: dict, created: str):
    from sources.base import NormalizedListing
    title = " ".join(str(x) for x in (f["year"], f["make"], f["model"], f["trim"]) if x)
    return NormalizedListing(
        source=SOURCE, source_id=str(sid), url="", title=title, price=f["price"],
        year=f["year"], make=f["make"], model=f["model"], odometer=f["mileage"],
        zip=f["zip"], description=_describe(f), posted_at=created, is_dealer=False,
        is_salvage=f["title_status"] in ("rebuilt", "salvage"),
        extras={"seller_type": "private", "submitted_by_seller": True,
                "verified": False, "contact_verified": True,
                "title_status": f["title_status"], "trim": f["trim"],
                "provenance": "seller-submitted on FlipFirst; seller's email verified; "
                              "vehicle details are seller-reported, not independently verified"})


# ---------- contact verification -----------------------------------------

def contact(conn, user) -> dict:
    r = conn.execute("SELECT email, verified_at FROM seller_contacts WHERE user_id = ?",
                     (user.id,)).fetchone()
    if not r:
        return {"email": user.email, "verified": False}
    return {"email": r[0], "verified": bool(r[1])}


def _verified(conn, user_id: int) -> bool:
    r = conn.execute("SELECT verified_at FROM seller_contacts WHERE user_id = ?",
                     (user_id,)).fetchone()
    return bool(r and r[0])


def set_contact(user, email: str | None = None) -> dict:
    """Choose the contact address (default: account email). A different
    address revokes verification and all outstanding tokens."""
    import db as _db
    email = (email or user.email or "").strip()
    if not _EMAIL_RE.match(email):
        raise SubmissionError(["enter a valid email address"])
    now = _now().isoformat()
    with _db.session() as conn:
        _lock_user(conn, user.id)
        cur = contact(conn, user) if conn.execute(
            "SELECT 1 FROM seller_contacts WHERE user_id = ?", (user.id,)).fetchone() else None
        if cur and cur["email"].lower() == email.lower():
            return cur
        if cur:
            conn.execute("UPDATE seller_contacts SET email = ?, verified_at = NULL, "
                         "updated_at = ? WHERE user_id = ?", (email, now, user.id))
        else:
            conn.execute("INSERT INTO seller_contacts(user_id, email, verified_at, updated_at) "
                         "VALUES (?, ?, NULL, ?)", (user.id, email, now))
        conn.execute("UPDATE contact_verifications SET used_at = ? WHERE user_id = ? "
                     "AND used_at IS NULL", (now, user.id))
        conn.execute("UPDATE seller_listings SET contact_email = ? WHERE user_id = ? AND "
                     "status IN ('pending', 'active')", (email, user.id))
    return {"email": email, "verified": False}


def _issue_token(conn, user_id: int, email: str) -> str:
    now = _now()
    sent = conn.execute("SELECT created_at FROM contact_verifications WHERE user_id = ? "
                        "AND created_at >= ? ORDER BY created_at DESC",
                        (user_id, (now - timedelta(days=1)).isoformat())).fetchall()
    if len(sent) >= MAX_SENDS_PER_DAY:
        raise SubmissionError(["too many verification emails today — try tomorrow"], 429)
    if sent:
        last = datetime.fromisoformat(str(sent[0][0]).replace("Z", "+00:00"))
        last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        if (now - last).total_seconds() < RESEND_MIN_GAP_S:
            raise SubmissionError(["please wait a minute before requesting another email"], 429)
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO contact_verifications(user_id, email, token_hash, created_at, "
                 "expires_at) VALUES (?, ?, ?, ?, ?)",
                 (user_id, email, _hash(token), now.isoformat(),
                  (now + timedelta(hours=TOKEN_TTL_H)).isoformat()))
    return token


def link_for(origin: str, token: str) -> str:
    # fragment, not query: the token never reaches server or proxy logs
    return f"{origin.rstrip('/')}/m#verify={token}"


def send_verification(user, origin: str) -> dict:
    import db as _db
    import mailer
    if not mailer.configured():
        raise SubmissionError(["email delivery isn't set up yet, so listings can't be "
                               "published — the site owner has been notified"], 503)
    with _db.session() as conn:
        _lock_user(conn, user.id)
        c = contact(conn, user)
        if not conn.execute("SELECT 1 FROM seller_contacts WHERE user_id = ?",
                            (user.id,)).fetchone():
            conn.execute("INSERT INTO seller_contacts(user_id, email, verified_at, updated_at) "
                         "VALUES (?, ?, NULL, ?)", (user.id, c["email"], _now().isoformat()))
        if c["verified"]:
            return {"email": c["email"], "verified": True, "sent": False}
        token = _issue_token(conn, user.id, c["email"])
    try:
        mailer.send(c["email"], "Confirm your email to publish your car on FlipFirst",
                    "Hi,\n\nConfirm this email address to publish your listing on FlipFirst. "
                    "Open this link while signed in:\n\n"
                    f"{link_for(origin, token)}\n\n"
                    f"The link works once and expires in {TOKEN_TTL_H} hours. Confirming shows "
                    "you control this address; it doesn't verify the vehicle.\n\n"
                    "If you didn't ask for this, you can ignore this email.\n\n"
                    "— FlipFirst · Find it first. Flip it better.")
    except mailer.MailerError as e:
        # not delivered: withdraw the token so it neither works nor counts
        # against the seller's send limits, and say so plainly
        with _db.session() as conn:
            conn.execute("DELETE FROM contact_verifications WHERE token_hash = ?", (_hash(token),))
        import logging
        logging.getLogger("sniper").warning("seller verification email not sent: %s", e)
        raise SubmissionError(["we couldn't send the email right now — please try again "
                               "in a few minutes"], 503) from None
    return {"email": c["email"], "verified": False, "sent": True}


def verify(user, token: str) -> dict:
    """Consume a token. Publishes the seller's pending listings."""
    import db as _db
    now = _now()
    with _db.session() as conn:
        _lock_user(conn, user.id)
        fails = int(conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE user_id = ? AND kind = 'seller_verify_fail' "
            "AND created_at >= ?", (user.id, (now - timedelta(hours=1)).isoformat())).fetchone()[0])
        if fails >= MAX_FAILED_VERIFY_PER_HOUR:
            raise SubmissionError(["too many attempts — try again in an hour"], 429)
        row = conn.execute(
            "SELECT id, user_id, email, expires_at, used_at FROM contact_verifications "
            "WHERE token_hash = ?", (_hash(str(token or "")),)).fetchone()
        c = contact(conn, user)
        exp = None
        if row:
            exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
            exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        ok = (row is not None and row["user_id"] == user.id and row["used_at"] is None
              and exp > now and row["email"].lower() == c["email"].lower())
        if not ok:
            conn.execute("INSERT INTO usage_events(user_id, kind, units, cost_cents, created_at) "
                         "VALUES (?, 'seller_verify_fail', 1, 0, ?)", (user.id, now.isoformat()))
    if not ok:
        reason = ("this link has expired — request a new one" if row and exp and exp <= now
                  else "this link is invalid or was already used")
        raise SubmissionError([reason], 400)
    with _db.session() as conn:
        conn.execute("UPDATE contact_verifications SET used_at = ? WHERE id = ? AND used_at IS NULL",
                     (now.isoformat(), row["id"]))
        conn.execute("UPDATE seller_contacts SET verified_at = ?, updated_at = ? WHERE user_id = ?",
                     (now.isoformat(), now.isoformat(), user.id))
        if conn.execute("SELECT 1 FROM usage_events WHERE user_id = ? AND kind = "
                        "'seller_manual_verify_link' AND units = ?",
                        (user.id, row["id"])).fetchone():
            conn.execute("INSERT INTO usage_events(user_id, kind, units, cost_cents, created_at) "
                         "VALUES (?, 'seller_verified_manual_link', ?, 0, ?)",
                         (user.id, row["id"], now.isoformat()))
    published = _publish_pending(user.id)
    return {"email": c["email"], "verified": True, "published": published}


def _publish_pending(user_id: int) -> int:
    import db as _db
    import sniper
    with _db.session() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, payload, created_at FROM seller_listings WHERE user_id = ? AND "
            "status = 'pending' ORDER BY id", (user_id,))]
    n = 0
    for r in rows:
        f = json.loads(r["payload"])
        now = _now().isoformat()
        with _db.session() as conn:      # flip first: a concurrent verify publishes once
            cur = conn.execute("UPDATE seller_listings SET status = 'active', payload = NULL, "
                               "last_confirmed_at = ?, updated_at = ? WHERE id = ? AND "
                               "status = 'pending'", (now, now, r["id"]))
            if not cur.rowcount:
                continue
        sniper.ingest([_to_listing(r["id"], f, now)])
        n += 1
    return n


# ---------- listings -------------------------------------------------------

def create(user, payload: dict) -> dict:
    import db as _db
    if not enabled():
        raise SubmissionError(["seller listings are not open yet"], 403)
    f, errs = validate(payload or {})
    if errs:
        raise SubmissionError(errs)
    now = _now()
    day_ago = (now - timedelta(days=1)).isoformat()
    with _db.session() as conn:
        _lock_user(conn, user.id)
        live = int(conn.execute("SELECT COUNT(*) FROM seller_listings WHERE user_id = ? "
                                "AND status IN ('pending', 'active')", (user.id,)).fetchone()[0])
        if live >= MAX_ACTIVE_PER_USER:
            raise SubmissionError([f"limit of {MAX_ACTIVE_PER_USER} listings reached"], 429)
        mine_today = int(conn.execute("SELECT COUNT(*) FROM seller_listings WHERE user_id = ? "
                                      "AND created_at >= ?", (user.id, day_ago)).fetchone()[0])
        all_today = int(conn.execute("SELECT COUNT(*) FROM seller_listings WHERE created_at >= ?",
                                     (day_ago,)).fetchone()[0])
        if mine_today >= MAX_NEW_PER_USER_PER_DAY or all_today >= MAX_NEW_PER_DAY:
            raise SubmissionError(["too many new listings today — try again tomorrow"], 429)
        if f["vin"] and conn.execute("SELECT 1 FROM seller_listings WHERE vin = ? AND "
                                     "status IN ('pending', 'active')", (f["vin"],)).fetchone():
            raise SubmissionError(["this VIN is already listed"], 409)
        c = contact(conn, user)
        try:
            sid = conn.execute(
                "INSERT INTO seller_listings(user_id, status, vin, contact_email, attested_at, "
                "  attestation, created_at, updated_at, payload) "
                "VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (user.id, f["vin"], c["email"], now.isoformat(),
                 json.dumps({a: True for a in ATTESTATIONS}), now.isoformat(),
                 now.isoformat(), json.dumps(f))).fetchone()[0]
        except Exception as e:          # unique live-VIN index: a concurrent duplicate
            if "uq_seller_listings_live_vin" in str(e) or "UNIQUE" in str(e).upper():
                raise SubmissionError(["this VIN is already listed"], 409) from None
            raise
        verified = _verified(conn, user.id)
    published = _publish_pending(user.id) if verified else 0
    return {"id": int(sid), "composite_id": f"{SOURCE}:{sid}",
            "status": "active" if published else "pending",
            "needs_contact_verification": not verified}


def _owned(conn, user, sid: int, admin_ok: bool = False, states=LIVE) -> dict:
    r = conn.execute("SELECT * FROM seller_listings WHERE id = ?", (sid,)).fetchone()
    if not r or r["status"] not in states:
        raise SubmissionError(["listing not found"], 404)
    r = dict(r)
    if r["user_id"] != user.id and not (admin_ok and user.is_admin):
        raise SubmissionError(["listing not found"], 404)   # don't reveal others' ids
    return r


def update_price(user, sid: int, price) -> dict:
    import db as _db
    import sniper
    p = _int(price)
    if p is None or not 500 <= p <= 250_000:
        raise SubmissionError(["price must be $500–$250,000"])
    with _db.session() as conn:
        _lock_user(conn, user.id)
        r = _owned(conn, user, sid)
        now = _now().isoformat()
        if r["status"] == "pending":
            f = json.loads(r["payload"])
            f["price"] = p
            conn.execute("UPDATE seller_listings SET payload = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(f), now, sid))
            return {"id": sid, "price": p, "price_changed": True, "status": "pending"}
        if not _verified(conn, user.id):
            raise SubmissionError(["confirm your contact email before changing a live listing"], 409)
        row = dict(conn.execute("SELECT * FROM listings WHERE composite_id = ?",
                                (f"{SOURCE}:{sid}",)).fetchone())
        conn.execute("UPDATE seller_listings SET updated_at = ?, last_confirmed_at = ? "
                     "WHERE id = ?", (now, now, sid))
    from sources.base import NormalizedListing
    l = NormalizedListing(source=SOURCE, source_id=str(sid), url="", title=row["title"],
                          price=p, year=row["year"], make=row["make"], model=row["model"],
                          odometer=row["odometer"], zip=row["zip"],
                          description=row["description"] or "", posted_at=row["posted_at"],
                          is_salvage=bool(row["is_salvage"]),
                          extras=json.loads(row.get("extras") or "{}"))
    res = sniper.ingest([l])                                    # price history + re-score
    return {"id": sid, "price": p, "price_changed": bool(res["price_changed"]),
            "status": "active"}


def renew(user, sid: int) -> dict:
    """'Still available': keeps an active listing discoverable."""
    import db as _db
    now = _now().isoformat()
    with _db.session() as conn:
        r = _owned(conn, user, sid, states=("active",))
        conn.execute("UPDATE seller_listings SET last_confirmed_at = ?, updated_at = ? "
                     "WHERE id = ?", (now, now, r["id"]))
        conn.execute("UPDATE listings SET last_seen_at = ? WHERE composite_id = ?",
                     (now, f"{SOURCE}:{sid}"))
    return {"id": sid, "confirmed_at": now}


def remove(user, sid: int) -> dict:
    """Seller (own listing) or admin. Deletes the listing and all derived
    rows; queued scoring/alerts for it are dropped."""
    import db as _db
    cid = f"{SOURCE}:{sid}"
    now = _now().isoformat()
    with _db.session() as conn:
        r = _owned(conn, user, sid, admin_ok=True)
        for table in ("user_matches", "alerts", "near_misses", "saved_v2", "saved",
                      "listing_prices"):
            conn.execute(f"DELETE FROM {table} WHERE composite_id = ?", (cid,))
        conn.execute("DELETE FROM jobs WHERE status IN ('pending', 'running') AND "
                     "(dedupe_key LIKE ? OR payload LIKE ?)",
                     (f"score:{cid}:%", f'%"composite_id": "{cid}"%'))
        conn.execute("DELETE FROM listings WHERE composite_id = ?", (cid,))
        conn.execute("UPDATE seller_listings SET status = 'removed', removed_at = ?, "
                     "removed_by = ?, updated_at = ?, vin = NULL, contact_email = NULL, "
                     "payload = NULL WHERE id = ?",
                     (now, "seller" if r["user_id"] == user.id else "admin", now, sid))
    return {"id": sid, "removed": True}


def mine(user) -> list[dict]:
    import db as _db
    cutoff = (_now() - timedelta(days=CONFIRM_DAYS)).isoformat()
    out = []
    with _db.session() as conn:
        for r in conn.execute(
                "SELECT s.id, s.status, s.created_at, s.last_confirmed_at, s.payload, "
                "  l.title, l.price, l.odometer, l.zip, n.comp_avg AS value_mid, n.confidence "
                "FROM seller_listings s LEFT JOIN listings l ON l.composite_id = "
                f"  ('{SOURCE}:' || CAST(s.id AS TEXT)) "
                "LEFT JOIN near_misses n ON n.composite_id = l.composite_id "
                "WHERE s.user_id = ? AND s.status IN ('pending', 'active') ORDER BY s.id DESC",
                (user.id,)):
            d = dict(r)
            if d["status"] == "pending":
                f = json.loads(d.pop("payload") or "{}")
                d.update(title=" ".join(str(x) for x in (f.get("year"), f.get("make"),
                                                          f.get("model"), f.get("trim")) if x),
                         price=f.get("price"), odometer=f.get("mileage"), zip=f.get("zip"))
            else:
                d.pop("payload", None)
                if str(d["last_confirmed_at"] or "") < cutoff:
                    d["status"] = "expired"
            out.append(d)
    return out


def discoverable_ids(conn) -> set[str]:
    """composite_ids of seller listings buyers may see right now: active,
    contact still verified, re-confirmed within CONFIRM_DAYS."""
    cutoff = (_now() - timedelta(days=CONFIRM_DAYS)).isoformat()
    return {f"{SOURCE}:{r[0]}" for r in conn.execute(
        "SELECT s.id FROM seller_listings s JOIN seller_contacts c ON c.user_id = s.user_id "
        "WHERE s.status = 'active' AND c.verified_at IS NOT NULL "
        "AND LOWER(c.email) = LOWER(s.contact_email) AND s.last_confirmed_at >= ?", (cutoff,))}


def contact_for(conn, composite_id: str, viewer_plan: str) -> dict | None:
    """Seller contact for a discoverable 'direct' listing: paid plans only,
    and only a verified address."""
    if not composite_id.startswith(f"{SOURCE}:"):
        return None
    if composite_id not in discoverable_ids(conn):
        return None
    if (viewer_plan or "free") == "free":
        return {"available": False, "why": "upgrade to see the seller's contact"}
    r = conn.execute("SELECT contact_email FROM seller_listings WHERE id = ?",
                     (int(composite_id.split(":", 1)[1]),)).fetchone()
    return {"available": True, "email": r[0], "verified_email": True} if r and r[0] else None


def manual_links_allowed() -> tuple[bool, str]:
    """Hand-delivered links bypass email delivery, so they are a dev/test
    tool only: never in commercial mode, and only when explicitly enabled."""
    from config import CONFIG
    if CONFIG.get("commercial_mode"):
        return False, "refused: manual verification links are disabled in commercial mode"
    if os.environ.get("SNIPER_ALLOW_MANUAL_VERIFY_LINKS", "").lower() not in ("1", "true", "yes"):
        return False, ("refused: set SNIPER_ALLOW_MANUAL_VERIFY_LINKS=1 (isolated "
                       "development/testing only)")
    return True, ""


def _main(argv=None) -> int:
    """Dev/test CLI: issue a verification link by hand when SMTP isn't set up.
    Audited; refused in commercial mode (see manual_links_allowed)."""
    import argparse
    import auth
    import db as _db
    ap = argparse.ArgumentParser(prog="python -m seller_listings")
    sub = ap.add_subparsers(dest="cmd", required=True)
    il = sub.add_parser("issue-link")
    il.add_argument("--account", required=True, help="the seller's account email")
    il.add_argument("--operator", required=True, help="who is issuing this link (audit)")
    il.add_argument("--origin", default=os.environ.get("BILLING_ORIGIN", ""))
    a = ap.parse_args(argv)
    ok, why = manual_links_allowed()
    if not ok:
        print(why)
        return 2
    u = auth.get_user_by_email(a.account)
    if not u or not a.origin or not a.operator.strip():
        print("unknown account, empty --operator, or no --origin/BILLING_ORIGIN")
        return 1
    with _db.session() as conn:
        c = contact(conn, u)
        if not conn.execute("SELECT 1 FROM seller_contacts WHERE user_id = ?", (u.id,)).fetchone():
            conn.execute("INSERT INTO seller_contacts(user_id, email, verified_at, updated_at) "
                         "VALUES (?, ?, NULL, ?)", (u.id, c["email"], _now().isoformat()))
        token = _issue_token(conn, u.id, c["email"])
        tid = conn.execute("SELECT id FROM contact_verifications WHERE token_hash = ?",
                           (_hash(token),)).fetchone()[0]
        conn.execute("INSERT INTO usage_events(user_id, kind, units, cost_cents, created_at) "
                     "VALUES (?, 'seller_manual_verify_link', ?, 0, ?)",
                     (u.id, tid, _now().isoformat()))
    import logging
    logging.getLogger("sniper").warning("manual seller verification link issued for user %s "
                                        "by %s", u.id, a.operator.strip())
    print(f"manual link (dev/test; audited as issued by {a.operator.strip()}). It proves "
          f"control of {c['email']} only if you deliver it to that address. "
          f"Single use, {TOKEN_TTL_H} h:")
    print(link_for(a.origin, token))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
