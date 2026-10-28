"""
seller_listings.py — vehicle owners list their own car (first-party data).

Why: licensed listing APIs mostly carry dealer inventory, the only licensed
private-party feed found needs a written agreement, and eBay's production
Buy APIs need partner approval. A seller who lists their own car, with
consent, is authentic private-party inventory that Car Sniper is entitled
to use — no third party's permission involved.

Smallest viable version, OFF by default (SNIPER_SELLER_SUBMISSIONS=1):
  * signed-in accounts only; every submission is tied to the account
  * the seller affirms: they own the car or may sell it, the details are
    accurate, and buyers on Car Sniper may see their contact email
  * strict validation (year, make/model, mileage, price, ZIP with a known
    centroid, optional VIN with check digit), duplicate active VIN refused
  * abuse limits: active listings per account, submissions per account per
    day, and a global daily cap that bounds moderation load
  * the car enters the normal pipeline as source 'direct' (seller_type
    private, marked unverified) so valuation, matching and alerts need no
    new code; the scam detector still runs on its text
  * price updates go through ingest (price history + price-drop events)
  * removal (seller or admin) deletes the listing and everything derived
    from it; only an audit stub without vehicle or contact data remains
  * the seller's email is shown only to signed-in paid users, never in
    feeds, push alerts or to anonymous visitors
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

SOURCE = "direct"
MAX_ACTIVE_PER_USER = int(os.environ.get("SELLER_MAX_ACTIVE_PER_USER", "3"))
MAX_NEW_PER_USER_PER_DAY = int(os.environ.get("SELLER_MAX_NEW_PER_USER_PER_DAY", "5"))
MAX_NEW_PER_DAY = int(os.environ.get("SELLER_MAX_NEW_PER_DAY", "50"))
ATTESTATIONS = ("authorized_to_sell", "details_accurate", "share_contact_with_buyers")
TITLE_STATUSES = ("clean", "rebuilt", "salvage", "unknown")


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


def validate(p: dict) -> tuple[dict, list[str]]:
    """Clean fields + list of problems (empty = valid)."""
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
    if miles is None or not 0 <= miles <= 500_000:
        errs.append("mileage must be 0–500,000")
    if price is None or not 500 <= price <= 250_000:
        errs.append("price must be $500–$250,000")
    zip_code = str(p.get("zip") or "").strip()[:5]
    if not geo.valid_zip(zip_code):
        errs.append("a valid US ZIP code is required")
    title_status = str(p.get("title_status") or "unknown").lower()
    if title_status not in TITLE_STATUSES:
        errs.append(f"title_status must be one of {', '.join(TITLE_STATUSES)}")
    vin = str(p.get("vin") or "").strip().upper() or None
    if vin:
        v = vin_mod.validate_vin(vin)
        if not v["ok"]:
            errs.append("VIN: " + "; ".join(v["errors"]))
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
             "description": description}, errs)


def _to_listing(sid: int, f: dict, created: str, prefix: bool = True):
    from sources.base import NormalizedListing
    title = " ".join(str(x) for x in (f["year"], f["make"], f["model"], f["trim"]) if x)
    desc = f["description"]
    if prefix and f["vin"]:
        desc = f"VIN: {f['vin']}\n{desc}"          # ingestion keys duplicates on VIN
    if prefix and f["title_status"] in ("rebuilt", "salvage"):
        desc = f"{f['title_status']} title\n{desc}"  # → branded-title flag downstream
    return NormalizedListing(
        source=SOURCE, source_id=str(sid), url="", title=title, price=f["price"],
        year=f["year"], make=f["make"], model=f["model"], odometer=f["mileage"],
        zip=f["zip"], description=desc, posted_at=created, is_dealer=False,
        is_salvage=f["title_status"] in ("rebuilt", "salvage"),
        extras={"seller_type": "private", "submitted_by_seller": True,
                "verified": False, "title_status": f["title_status"], "trim": f["trim"],
                "provenance": "seller-submitted on Car Sniper; details not independently verified"})


def create(user, payload: dict) -> dict:
    import db as _db
    import sniper
    if not enabled():
        raise SubmissionError(["seller listings are not open yet"], 403)
    f, errs = validate(payload or {})
    if errs:
        raise SubmissionError(errs)
    now = _now()
    day_ago = (now - timedelta(days=1)).isoformat()
    with _db.session() as conn:
        active = int(conn.execute("SELECT COUNT(*) FROM seller_listings WHERE user_id = ? "
                                  "AND status = 'active'", (user.id,)).fetchone()[0])
        if active >= MAX_ACTIVE_PER_USER:
            raise SubmissionError([f"limit of {MAX_ACTIVE_PER_USER} active listings reached"], 429)
        mine_today = int(conn.execute("SELECT COUNT(*) FROM seller_listings WHERE user_id = ? "
                                      "AND created_at >= ?", (user.id, day_ago)).fetchone()[0])
        all_today = int(conn.execute("SELECT COUNT(*) FROM seller_listings WHERE created_at >= ?",
                                     (day_ago,)).fetchone()[0])
        if mine_today >= MAX_NEW_PER_USER_PER_DAY or all_today >= MAX_NEW_PER_DAY:
            raise SubmissionError(["too many new listings today — try again tomorrow"], 429)
        if f["vin"] and conn.execute("SELECT 1 FROM seller_listings WHERE vin = ? AND "
                                     "status = 'active'", (f["vin"],)).fetchone():
            raise SubmissionError(["this VIN is already listed"], 409)
        att = {a: True for a in ATTESTATIONS}
        sid = conn.execute(
            "INSERT INTO seller_listings(user_id, status, vin, contact_email, attested_at, "
            "  attestation, created_at, updated_at) VALUES (?, 'active', ?, ?, ?, ?, ?, ?) "
            "RETURNING id",
            (user.id, f["vin"], user.email, now.isoformat(), json.dumps(att),
             now.isoformat(), now.isoformat())).fetchone()[0]
    try:
        sniper.ingest([_to_listing(int(sid), f, now.isoformat())])
    except Exception:
        with _db.session() as conn:
            conn.execute("DELETE FROM seller_listings WHERE id = ?", (sid,))
        raise
    return {"id": int(sid), "composite_id": f"{SOURCE}:{sid}"}


def _owned(conn, user, sid: int, admin_ok: bool = False) -> dict:
    r = conn.execute("SELECT * FROM seller_listings WHERE id = ?", (sid,)).fetchone()
    if not r or r["status"] != "active":
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
    cid = f"{SOURCE}:{sid}"
    with _db.session() as conn:
        _owned(conn, user, sid)
        row = dict(conn.execute("SELECT * FROM listings WHERE composite_id = ?",
                                (cid,)).fetchone())
        conn.execute("UPDATE seller_listings SET updated_at = ? WHERE id = ?",
                     (_now().isoformat(), sid))
    extras = json.loads(row.get("extras") or "{}")
    f = {"year": row["year"], "make": row["make"], "model": row["model"],
         "trim": extras.get("trim"), "mileage": row["odometer"], "price": p,
         "zip": row["zip"], "title_status": extras.get("title_status", "unknown"),
         "vin": None, "description": row["description"] or ""}
    # description already carries the VIN/title lines from creation
    res = sniper.ingest([_to_listing(sid, f, row["posted_at"], prefix=False)])
    return {"id": sid, "price": p, "price_changed": bool(res["price_changed"])}


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
                     "removed_by = ?, updated_at = ?, vin = NULL, contact_email = NULL "
                     "WHERE id = ?",
                     (now, "seller" if r["user_id"] == user.id else "admin", now, sid))
    return {"id": sid, "removed": True}


def mine(user) -> list[dict]:
    import db as _db
    with _db.session() as conn:
        rows = conn.execute(
            "SELECT s.id, s.created_at, l.title, l.price, l.odometer, l.zip, "
            "  n.comp_avg AS value_mid, n.confidence, n.evidence_class "
            "FROM seller_listings s JOIN listings l ON l.composite_id = "
            f"  ('{SOURCE}:' || CAST(s.id AS TEXT)) "
            "LEFT JOIN near_misses n ON n.composite_id = l.composite_id "
            "WHERE s.user_id = ? AND s.status = 'active' ORDER BY s.id DESC",
            (user.id,)).fetchall()
    return [dict(r) for r in rows]


def contact_for(conn, composite_id: str, viewer_plan: str) -> dict | None:
    """Seller contact for a 'direct' listing: paid plans only."""
    if not composite_id.startswith(f"{SOURCE}:"):
        return None
    if (viewer_plan or "free") == "free":
        return {"available": False, "why": "upgrade to see the seller's contact"}
    r = conn.execute("SELECT contact_email FROM seller_listings WHERE id = ? AND "
                     "status = 'active'", (int(composite_id.split(":", 1)[1]),)).fetchone()
    return {"available": True, "email": r[0]} if r and r[0] else None
