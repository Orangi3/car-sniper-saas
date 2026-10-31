"""
pilot_report.py — weekly numbers for the private pilot, from data the app
already keeps. Read-only; prints counts, never names, emails or listings.

    python -m pilot_report               # last 7 days
    python -m pilot_report --days 14 --json

Sections: testers (invitations, accounts, sign-ins), engagement (deal
actions by stage), analyze (analyses run, deals saved to the workspace, by
stage, outcomes recorded), alerts (matches, notifications delivered / failed),
supply (listings by source, valued, trusted candidates), seller listings,
and reviewed outcomes (evaluation labels). "Signed in" counts testers whose
last sign-in falls in the window — the app records sign-ins, not page
views. The window starts at midnight UTC `days` days ago.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone


def _count(conn, sql: str, args: tuple = ()) -> int:
    return conn.execute(sql, args).fetchone()[0] or 0


def _groups(conn, sql: str, args: tuple = ()) -> dict:
    return {str(k): int(n) for k, n in conn.execute(sql, args).fetchall()}


def report(days: int = 7) -> dict:
    import db as _db
    # a date compares correctly with every timestamp format the tables hold
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    with _db.session() as conn:
        delivered = _count(conn, "SELECT COUNT(*) FROM jobs WHERE kind = 'notify' AND status = 'done' "
                                 "AND finished_at >= ?", (since,))
        failed = _count(conn, "SELECT COUNT(*) FROM jobs WHERE kind = 'notify' AND status = 'dead' "
                              "AND updated_at >= ?", (since,))
        out = {
            "window_days": days, "since": since,
            "testers": {
                "accounts": _count(conn, "SELECT COUNT(*) FROM users WHERE role <> 'admin'"),
                "invitations_issued": _count(conn, "SELECT COUNT(*) FROM invitations WHERE created_at >= ?",
                                             (since,)),
                "invitations_claimed": _count(conn, "SELECT COUNT(*) FROM invitations WHERE used_at >= ?",
                                              (since,)),
                "signed_in": _count(conn, "SELECT COUNT(*) FROM users WHERE role <> 'admin' "
                                          "AND last_login_at >= ?", (since,)),
                "by_plan": _groups(conn, "SELECT plan, COUNT(*) FROM users WHERE role <> 'admin' "
                                         "GROUP BY plan"),
            },
            "engagement": {
                "deal_actions_by_stage": _groups(conn, "SELECT status, COUNT(*) FROM saved_v2 "
                                                       "WHERE updated_at >= ? GROUP BY status", (since,)),
                "testers_acting": _count(conn, "SELECT COUNT(DISTINCT user_id) FROM saved_v2 "
                                               "WHERE updated_at >= ?", (since,)),
            },
            "alerts": {
                "matches": _count(conn, "SELECT COUNT(*) FROM user_matches WHERE created_at >= ?", (since,)),
                "notifications_delivered": delivered,
                "notifications_failed": failed,
                "delivery_rate": round(delivered / (delivered + failed), 3) if delivered + failed else None,
                "notifications_waiting": _count(conn, "SELECT COUNT(*) FROM jobs WHERE kind = 'notify' "
                                                      "AND status IN ('pending', 'running')"),
            },
            "supply": {
                "listings_by_source": _groups(conn, "SELECT source, COUNT(*) FROM listings "
                                                    "WHERE first_seen_at >= ? GROUP BY source", (since,)),
                "valued": _count(conn, "SELECT COUNT(*) FROM near_misses WHERE refreshed_at >= ? "
                                       "AND evidence_class <> 'insufficient'", (since,)),
                "trusted_candidates": _count(conn, "SELECT COUNT(*) FROM near_misses WHERE refreshed_at >= ? "
                                                   "AND trust = 'trusted' AND discount_pct >= 10", (since,)),
            },
            "analyze": {
                "analyses": _count(conn, "SELECT COUNT(*) FROM usage_events WHERE kind = 'analysis' "
                                         "AND created_at >= ?", (since,)),
                "testers_analyzing": _count(conn, "SELECT COUNT(DISTINCT user_id) FROM usage_events "
                                                  "WHERE kind = 'analysis' AND created_at >= ?",
                                            (since,)),
                "deals_saved": _count(conn, "SELECT COUNT(*) FROM opportunities WHERE created_at >= ?",
                                      (since,)),
                "deals_by_stage": _groups(conn, "SELECT status, COUNT(*) FROM opportunities "
                                                "GROUP BY status"),
                "outcomes_recorded": _count(conn, "SELECT COUNT(*) FROM opportunities "
                                                  "WHERE sale_price IS NOT NULL"),
            },
            "seller_listings_by_status": _groups(conn, "SELECT status, COUNT(*) FROM seller_listings "
                                                       "GROUP BY status"),
            "reviewed_outcomes": _groups(conn, "SELECT outcome_label, COUNT(*) FROM eval_observations "
                                               "WHERE is_fixture = FALSE AND outcome_label IS NOT NULL "
                                               "AND updated_at >= ? GROUP BY outcome_label", (since,)),
        }
    return out


def _print(r: dict) -> None:
    print(f"Pilot report — last {r['window_days']} days (since {r['since']} UTC)")
    for section in ("testers", "engagement", "analyze", "alerts", "supply"):
        print(f"\n{section}")
        for k, v in r[section].items():
            print(f"  {k}: {v}")
    print(f"\nseller listings: {r['seller_listings_by_status']}")
    print(f"reviewed outcomes: {r['reviewed_outcomes']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pilot_report")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = report(max(1, a.days))
    print(json.dumps(r, indent=2)) if a.json else _print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
