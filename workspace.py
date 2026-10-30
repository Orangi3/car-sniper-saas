"""
workspace.py — the private deal workspace behind Analyze: cars a user
analyzed and saved, their inspection notes, the stage, and what actually
happened (purchase price, repair spending, fees, sale price and dates).

Private by construction: every function takes the user's id and every query
is scoped to it, so another user's deal is simply "not found". Outcomes are
recorded by the user and labeled that way: FlipFirst doesn't verify them,
show them to anyone else, or use them in aggregate.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Optional

STATUSES = ("analyzing", "inspecting", "negotiating", "purchased", "sold", "passed")
MONEY_FIELDS = ("purchase_price", "repair_spent", "fees_spent", "sale_price")
DATE_FIELDS = ("purchased_on", "sold_on")
NOTES_MAX = 4000
_COLS = ("id, status, title, input_json, analysis_json, notes, composite_id, purchase_price, "
         "purchased_on, repair_spent, fees_spent, sale_price, sold_on, created_at, updated_at")


class NotFound(Exception):
    pass


class LimitReached(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_changes(p) -> tuple[dict, list[str]]:
    """Notes, stage and recorded outcome fields from a request body."""
    p = p if isinstance(p, dict) else {}
    out: dict = {}
    errs: list[str] = []
    if "notes" in p:
        out["notes"] = str(p.get("notes") or "").strip()[:NOTES_MAX] or None
    if "status" in p:
        st = str(p.get("status") or "").lower()
        if st not in STATUSES:
            errs.append("status must be one of: " + ", ".join(STATUSES))
        else:
            out["status"] = st
    for k in MONEY_FIELDS:
        if k not in p:
            continue
        v = p.get(k)
        if v is None or v == "":
            out[k] = None
            continue
        try:
            n = int(round(float(str(v).replace(",", "").replace("$", ""))))
        except (TypeError, ValueError):
            errs.append(f"{k.replace('_', ' ')} must be a number")
            continue
        if not 0 <= n <= 1_000_000:
            errs.append(f"{k.replace('_', ' ')} must be between 0 and 1,000,000")
            continue
        out[k] = n
    for k in DATE_FIELDS:
        if k not in p:
            continue
        v = str(p.get(k) or "").strip()
        if not v:
            out[k] = None
            continue
        try:
            d = date.fromisoformat(v[:10])
        except ValueError:
            errs.append(f"{k.replace('_', ' ')} must be a date (YYYY-MM-DD)")
            continue
        if d > datetime.now(timezone.utc).date():
            errs.append(f"{k.replace('_', ' ')} can't be in the future")
            continue
        out[k] = d.isoformat()
    return out, errs


def realized(r: dict) -> Optional[dict]:
    """What the user recorded actually happened, once bought and sold."""
    if r.get("purchase_price") is None or r.get("sale_price") is None:
        return None
    spent = r["purchase_price"] + (r.get("repair_spent") or 0) + (r.get("fees_spent") or 0)
    profit = r["sale_price"] - spent
    days = None
    if r.get("purchased_on") and r.get("sold_on"):
        days = (date.fromisoformat(r["sold_on"]) - date.fromisoformat(r["purchased_on"])).days
    return {"invested": spent, "profit": profit,
            "roi_pct": round(100 * profit / spent, 1) if spent else None,
            "holding_days": days, "recorded_by": "you (not verified by FlipFirst)"}


def _item(row) -> dict:
    """A workspace row for lists and comparison: the key figures only."""
    r = dict(row)
    a = json.loads(r.pop("analysis_json") or "null") or {}
    inp = json.loads(r.pop("input_json") or "{}")
    pr = a.get("profit") or {}
    r.update({
        "asking_price": inp.get("asking_price"), "offer_price": inp.get("offer_price"),
        "mileage": inp.get("mileage"), "decision": (a.get("decision") or {}).get("class"),
        "decision_label": (a.get("decision") or {}).get("label"),
        "valuation_basis": a.get("valuation_basis"), "value_mid": a.get("value_mid"),
        "confidence": a.get("confidence"), "est_profit": pr.get("base"),
        "est_profit_conservative": pr.get("conservative"),
        "est_roi_pct": ((a.get("profit_detail") or {}).get("scenarios") or {})
                       .get("base", {}).get("roi_pct"),
        "break_even_resale": a.get("break_even_resale"),
        "margin_of_safety_pct": a.get("margin_of_safety_pct"),
        "risk_flags": a.get("risk_flags") or [], "analyzed_at": a.get("analyzed_at"),
        "realized": realized(r)})
    return r


def history(items: list[dict]) -> dict:
    """The user's own track record from the outcomes they recorded."""
    by_status = {s: 0 for s in STATUSES}
    for it in items:
        by_status[it["status"]] = by_status.get(it["status"], 0) + 1
    done = [it for it in items if it["realized"]]
    with_est = [it for it in done if it.get("est_profit") is not None]
    held = [it["realized"]["holding_days"] for it in done
            if it["realized"]["holding_days"] is not None]
    rois = [it["realized"]["roi_pct"] for it in done if it["realized"]["roi_pct"] is not None]
    return {
        "by_status": by_status, "completed_flips": len(done),
        "realized_profit": sum(it["realized"]["profit"] for it in done) if done else None,
        "avg_roi_pct": round(sum(rois) / len(rois), 1) if rois else None,
        "avg_holding_days": round(sum(held) / len(held), 1) if held else None,
        # realized minus what Analyze expected when the deal was saved or last re-run
        "avg_estimate_error": (round(sum(it["realized"]["profit"] - it["est_profit"]
                                         for it in with_est) / len(with_est))
                               if with_est else None),
        "note": "From outcomes you recorded; not verified by FlipFirst.",
    }


def list_for(conn, user_id: int) -> list[dict]:
    rows = conn.execute(f"SELECT {_COLS} FROM opportunities WHERE user_id = ? "
                        "ORDER BY updated_at DESC, id DESC", (user_id,)).fetchall()
    return [_item(r) for r in rows]


def get(conn, user_id: int, oid: int) -> dict:
    row = conn.execute(f"SELECT {_COLS} FROM opportunities WHERE id = ? AND user_id = ?",
                       (oid, user_id)).fetchone()
    if not row:
        raise NotFound()
    r = dict(row)
    out = _item(row)
    out["input"] = json.loads(r["input_json"] or "{}")
    out["analysis"] = json.loads(r["analysis_json"] or "null")
    return out


def create(conn, user_id: int, inputs: dict, analysis: dict, *, limit: int,
           notes: Optional[str] = None) -> dict:
    n = conn.execute("SELECT COUNT(*) FROM opportunities WHERE user_id = ?",
                     (user_id,)).fetchone()[0]
    if n >= limit:
        raise LimitReached(limit)
    now = _now()
    oid = conn.execute(
        "INSERT INTO opportunities(user_id, status, title, input_json, analysis_json, notes, "
        "  composite_id, created_at, updated_at) VALUES (?, 'analyzing', ?, ?, ?, ?, ?, ?, ?) "
        "RETURNING id",
        (user_id, analysis["title"][:120], json.dumps(inputs), json.dumps(analysis, default=str),
         notes, inputs.get("composite_id"), now, now)).fetchone()[0]
    return get(conn, user_id, oid)


def update(conn, user_id: int, oid: int, changes: dict, *,
           inputs: Optional[dict] = None, analysis: Optional[dict] = None) -> dict:
    cur = get(conn, user_id, oid)
    merged = {**{k: cur.get(k) for k in MONEY_FIELDS + DATE_FIELDS}, **changes}
    if (merged.get("purchased_on") and merged.get("sold_on")
            and merged["sold_on"] < merged["purchased_on"]):
        raise ValueError("the sale date is before the purchase date")
    sets = dict(changes)
    if analysis is not None:
        sets.update(input_json=json.dumps(inputs), analysis_json=json.dumps(analysis, default=str),
                    title=analysis["title"][:120])
    sets["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE opportunities SET {cols} WHERE id = ? AND user_id = ?",
                 (*sets.values(), oid, user_id))
    return get(conn, user_id, oid)


def delete(conn, user_id: int, oid: int) -> None:
    if not conn.execute("DELETE FROM opportunities WHERE id = ? AND user_id = ? RETURNING id",
                        (oid, user_id)).fetchone():
        raise NotFound()
