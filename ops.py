"""
ops.py — one owner-facing snapshot of whether Car Sniper is actually working.

    python -m ops            # JSON
    GET /api/admin/ops       # same, admin only;  /admin/ops renders it

Every number comes from the database or the process environment at the time
of the call. Anything the application cannot measure is returned as
{"value": None, "unavailable": "<why>"} — never estimated, never filled in.
Configured figures (e.g. hosting cost the owner typed in) are labeled
"configured", not "measured".
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone


def _na(why: str) -> dict:
    return {"value": None, "unavailable": why}


def _since(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def data_health(conn, window_h: int = 24) -> dict:
    import sources
    from config import CONFIG
    health = {r["source_id"]: dict(r) for r in conn.execute("SELECT * FROM source_health")}
    since = _since(window_h)
    new_by_src = {r[0]: int(r[1]) for r in conn.execute(
        "SELECT source, COUNT(*) FROM listings WHERE first_seen_at >= ? GROUP BY source",
        (since,))}
    probes = {}
    for r in conn.execute("SELECT source, checked_at, status, verified, listings, origin "
                          "FROM source_probe_runs ORDER BY checked_at"):
        probes[r["source"]] = dict(r)            # last one wins
    rows = []
    for m in sources.all_sources():
        sid = m.SOURCE_ID
        h = health.get(sid, {})
        rows.append({
            "source": sid, "licensing": getattr(m, "LICENSING", "unverified"),
            "commercially_blocked": sources.licensing_block(m, {**CONFIG, "commercial_mode": True}),
            "status": h.get("status") or "never polled",
            "reason": h.get("status_reason"),
            "last_attempt_at": h.get("last_attempt_at"),
            "last_genuine_success_at": h.get("last_genuine_success_at"),
            "listings_new_last_poll": h.get("listings_new"),
            "listings_first_seen_in_window": new_by_src.get(sid, 0),
            "auth_failure": h.get("status") == "auth_config",
            "last_live_probe": probes.get(sid) or _na("no verify_sources result imported"),
        })
    working = [r["source"] for r in rows if r["status"] == "healthy"]
    xpost = conn.execute(
        "SELECT COUNT(*) FROM (SELECT vehicle_key FROM listings WHERE vehicle_key IS NOT NULL "
        "GROUP BY vehicle_key HAVING COUNT(*) > 1) t").fetchone()[0]
    return {"sources": rows, "working": working,
            "failing": [r["source"] for r in rows
                        if r["status"] not in ("healthy", "never polled", "disabled")],
            "vehicles_seen_on_multiple_listings": int(xpost),
            "duplicates_rejected_per_poll": _na("re-poll duplicates are counted per poll "
                                                "(seen) but not persisted")}


def deal_intelligence(conn, window_h: int = 24, top: int = 5) -> dict:
    import profit as profit_mod
    since = _since(window_h)
    rows = [dict(r) for r in conn.execute(
        "SELECT n.composite_id, n.trust, n.confidence, n.evidence_class, n.comp_n, "
        "  n.value_low, n.comp_avg AS value_mid, n.value_high, n.evidence, l.price, "
        "  l.year, l.make, l.model, l.source "
        "FROM near_misses n JOIN listings l ON l.composite_id = n.composite_id "
        "WHERE n.refreshed_at >= ?", (since,))]
    by_conf: dict[str, int] = {}
    by_ev: dict[str, int] = {}
    quality = []
    ranked = []
    for r in rows:
        by_conf[r["confidence"] or "none"] = by_conf.get(r["confidence"] or "none", 0) + 1
        by_ev[r["evidence_class"] or "none"] = by_ev.get(r["evidence_class"] or "none", 0) + 1
        try:
            ev = json.loads(r["evidence"] or "{}")
        except ValueError:
            ev = {}
        if ev.get("comp_quality") is not None:
            quality.append(ev["comp_quality"])
        if r["trust"] == "trusted" and r["value_mid"]:
            pr = profit_mod.compute(r["price"], r["value_low"], r["value_mid"],
                                    r["value_high"], r["evidence_class"], None,
                                    dealer_share=ev.get("dealer_share") or 0)
            if pr:
                ranked.append({"composite_id": r["composite_id"], "source": r["source"],
                               "vehicle": f"{r['year']} {r['make']} {r['model']}",
                               "price": r["price"],
                               "conservative_profit": pr["scenarios"]["conservative"]["profit"],
                               "base_profit": pr["scenarios"]["base"]["profit"],
                               "confidence": r["confidence"], "comps": r["comp_n"]})
    ranked.sort(key=lambda d: -d["conservative_profit"])
    potential = [d for d in ranked if d["conservative_profit"] > 0]
    discovered = int(conn.execute("SELECT COUNT(*) FROM listings WHERE first_seen_at >= ?",
                                  (since,)).fetchone()[0])
    verified = int(conn.execute(
        "SELECT COUNT(*) FROM eval_observations WHERE is_fixture = FALSE AND updated_at >= ? "
        "AND outcome_label IN ('genuine_opportunity', 'purchased_profit_verified')",
        (since,)).fetchone()[0])
    funnel = {
        "discovered": discovered,
        "scored": len(rows),
        "valued": sum(1 for r in rows if r["value_mid"]),
        # trusted valuation AND profitable in the conservative scenario — an
        # ESTIMATE from the evidence we have, not a verified deal
        "potential_opportunities": len(potential),
        # needs completed-sale evidence (confidence 'high' is impossible on
        # asking prices alone, by design)
        "high_confidence_opportunities": sum(1 for d in potential if d["confidence"] == "high"),
        # a person reviewed the listing and labeled it a genuine opportunity
        "manually_verified": verified,
    }
    jobs = {(r[0]): int(r[1]) for r in conn.execute(
        "SELECT status, COUNT(*) FROM jobs WHERE kind = 'notify' AND created_at >= ? "
        "GROUP BY status", (since,))}
    return {
        "window_hours": window_h, "valued_listings": len(rows),
        "funnel": funnel,
        "trusted_valuations": sum(1 for r in rows if r["trust"] == "trusted"),
        "by_confidence": by_conf, "by_evidence_class": by_ev,
        "mean_comparable_quality": (round(sum(quality) / len(quality), 1) if quality
                                    else _na("no valuation with comparable quality yet")),
        "top_by_conservative_profit": potential[:top],
        "profit_basis": "default assumptions (profit.DEFAULTS); estimates, not outcomes",
        "owner_alerts": int(conn.execute("SELECT COUNT(*) FROM alerts WHERE created_at >= ?",
                                         (since,)).fetchone()[0]),
        "subscriber_matches": int(conn.execute(
            "SELECT COUNT(*) FROM user_matches WHERE created_at >= ?", (since,)).fetchone()[0]),
        "notifications": {"queued": jobs.get("pending", 0) + jobs.get("running", 0),
                          "delivered_to_provider": jobs.get("done", 0),
                          "failed_permanently": jobs.get("dead", 0),
                          "note": "delivered = accepted by the push/SMS provider; "
                                  "arrival on the phone is not observable"},
    }


def system_health(conn) -> dict:
    import jobqueue
    import preflight
    pf = preflight.run()
    infra = [c for c in pf["checks"] if c["category"] == "infrastructure"]
    errors = [dict(r) for r in conn.execute(
        "SELECT kind, attempts, last_error, updated_at FROM jobs "
        "WHERE last_error IS NOT NULL ORDER BY updated_at DESC LIMIT 5")]
    sched = conn.execute("SELECT last_tick_at, last_error, last_error_at FROM scheduler_state "
                         "WHERE id = 1").fetchone()
    return {"ready_for": pf["ready_for"], "checks": infra, "queue": jobqueue.stats(),
            "scheduler": dict(sched) if sched else _na("scheduler has never run"),
            "recent_job_errors": errors}


def costs(conn) -> dict:
    import provider_budget
    import sources
    month = provider_budget.month_start().isoformat()
    usage = {r[0]: (int(r[1]), float(r[2] or 0)) for r in conn.execute(
        "SELECT kind, SUM(units), SUM(cost_cents) FROM usage_events "
        "WHERE created_at >= ? GROUP BY kind", (month,))}
    providers = []
    for m in sources.all_sources():
        sid = m.SOURCE_ID
        if sid not in provider_budget.DEFAULT_BUDGET and not os.environ.get(
                f"{sid.upper()}_MONTHLY_CALL_BUDGET"):
            continue
        calls, cents = usage.get(f"provider:{sid}", (0, 0.0))
        providers.append({"source": sid, "calls_this_month": calls,
                          "budget": provider_budget.budget(sid),
                          "remaining": max(0, provider_budget.budget(sid) - calls),
                          "estimated_call_fees_usd": round(cents / 100, 2),
                          "basis": "call count measured; per-call price configured"})
    notif = {k: {"sent": v[0], "estimated_usd": round(v[1] / 100, 2)}
             for k, v in usage.items() if k in ("sms", "push", "webhook")}
    hosting = os.environ.get("OPS_HOSTING_MONTHLY_USD")
    platform = os.environ.get("OPS_DATA_PLATFORM_MONTHLY_USD")
    return {
        "since": month, "providers": providers,
        "notifications_this_month": notif or _na("no metered notifications recorded"),
        "data_platform_fee": ({"value": float(platform), "basis": "configured"} if platform
                              else _na("set OPS_DATA_PLATFORM_MONTHLY_USD (e.g. plan fee)")),
        "hosting": ({"value": float(hosting), "basis": "configured"} if hosting
                    else _na("set OPS_HOSTING_MONTHLY_USD from your invoice")),
        "ai_agent_spend": _na("not visible to the app — see the Anthropic console "
                              "spend page; runs are capped by AGENT_MAX_RUNS_PER_DAY"),
    }


def snapshot(window_h: int = 24) -> dict:
    import db as _db
    with _db.session() as conn:
        out = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "data_health": data_health(conn, window_h),
               "deal_intelligence": deal_intelligence(conn, window_h),
               "costs": costs(conn)}
        out["system_health"] = system_health(conn)
    return out


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2, default=str))
