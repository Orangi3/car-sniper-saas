"""
preflight.py — is this deployment ready, and for WHAT?

    python -m preflight                 # human-readable
    python -m preflight --json
    python -m preflight --target paid   # exit 1 unless ready for a paid beta

An HTTP 200 from /health proves the web process runs. It says nothing about
whether the business can operate. This checks, in order:

  1. required configuration            (env vars; secrets never printed)
  2. internal infrastructure           (DB, migrations, scheduler, worker, dead letters)
  3. external providers                (each source's real health, verified or not)
  4. licensing / permission blockers   (commercial mode, signed agreements)
  5. commercial data availability      (licensed source with fresh listings)
  6. payments                          (live vs test Stripe configuration)
  7. readiness verdict                 (internal test / free beta / paid beta)

Readiness levels (each requires the previous):
  private_internal_testing  infrastructure healthy (owner-only use)
  controlled_free_beta      + commercial mode + a licensed source delivering
                              fresh listings + HTTPS origin + alert channel.
                              (Sharing listings with invited users is
                              distribution, so licensing applies even if free.)
  controlled_paid_beta      + Postgres + live Stripe + backups configured +
                              no dead-lettered jobs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

OK, WARN, FAIL = "ok", "warn", "fail"
LEVELS = ("not_ready", "private_internal_testing", "controlled_free_beta",
          "controlled_paid_beta")


def _env(k: str) -> str:
    return (os.environ.get(k) or "").strip()


def _age_s(iso) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except ValueError:
        return None


def run() -> dict:
    checks: list[dict] = []

    def add(cat, name, status, detail, blocks=None):
        checks.append({"category": cat, "check": name, "status": status,
                       "detail": detail, "blocks": blocks})

    # ---- 1. configuration -------------------------------------------------
    import db as _db
    import plans  # noqa: F401  (import errors surface here)
    from config import CONFIG
    add("config", "database", OK if _db.IS_PG else WARN,
        "PostgreSQL" if _db.IS_PG else f"SQLite at {_db.SQLITE_PATH} (fine for local/internal only)",
        None if _db.IS_PG else "controlled_paid_beta")
    origin = _env("BILLING_ORIGIN")
    add("config", "public https origin", OK if origin.startswith("https://") else FAIL,
        origin or "BILLING_ORIGIN not set", None if origin.startswith("https://")
        else "controlled_free_beta")
    reg = _env("ALLOW_REGISTRATION")
    add("config", "registration policy", OK, f"ALLOW_REGISTRATION={reg or '0 (invite-only)'}")
    push = _env("NTFY_SERVER") or "https://ntfy.sh (default public server)"
    add("config", "alert channel", OK, f"ntfy server: {push}"
        + ("; Twilio configured" if _env("TWILIO_ACCOUNT_SID") else ""))

    # ---- 2. internal infrastructure --------------------------------------
    infra_ok = True
    try:
        with _db.session() as conn:
            conn.execute("SELECT 1").fetchone()
            from migrations import runner
            want = [m[0] for m in runner._list_migrations()]
            have = {r[0] for r in conn.execute("SELECT id FROM schema_migrations")}
            missing = [m for m in want if m not in have]
            sched = conn.execute("SELECT last_tick_at FROM scheduler_state WHERE id = 1"
                                 ).fetchone()
            jobs = {r[0]: int(r[1]) for r in conn.execute(
                "SELECT status, COUNT(*) FROM jobs GROUP BY status")}
            oldest = conn.execute("SELECT MIN(run_after) FROM jobs WHERE status = 'pending'"
                                  ).fetchone()[0]
            last_done = conn.execute("SELECT MAX(finished_at) FROM jobs WHERE status = 'done'"
                                     ).fetchone()[0]
            health_rows = {r["source_id"]: dict(r) for r in
                           conn.execute("SELECT * FROM source_health")}
        add("infrastructure", "database connection", OK, _db.describe().get("engine"))
    except Exception as e:
        add("infrastructure", "database connection", FAIL, f"{type(e).__name__}: {e}",
            "private_internal_testing")
        return _verdict(checks)
    if missing:
        infra_ok = False
        add("infrastructure", "migrations", FAIL, f"pending: {', '.join(missing)} — "
            "run python -m migrations.runner", "private_internal_testing")
    else:
        add("infrastructure", "migrations", OK, f"{len(want)} applied")
    tick_age = _age_s(sched[0] if sched else None)
    if tick_age is None or tick_age > 900:
        infra_ok = False
        add("infrastructure", "scheduler heartbeat", FAIL,
            "no tick in the last 15 min" if tick_age else "scheduler has never ticked",
            "private_internal_testing")
    else:
        add("infrastructure", "scheduler heartbeat", OK, f"last tick {tick_age:.0f}s ago")
    pend_age = _age_s(oldest)
    if pend_age is not None and pend_age > 600:
        infra_ok = False
        add("infrastructure", "job worker", FAIL,
            f"oldest runnable job waiting {pend_age/60:.0f} min — is jobs.worker running?",
            "private_internal_testing")
    else:
        add("infrastructure", "job worker", OK,
            f"pending={jobs.get('pending', 0)} running={jobs.get('running', 0)} "
            f"done={jobs.get('done', 0)}; last completion {last_done or 'never'}")
    dead = jobs.get("dead", 0)
    add("infrastructure", "dead-lettered jobs", OK if not dead else WARN,
        f"{dead} dead job(s) — inspect jobqueue.dead_letters()" if dead else "none",
        "controlled_paid_beta" if dead else None)
    backups = all(_env(k) for k in ("B2_BUCKET", "B2_KEY_ID", "B2_APPLICATION_KEY",
                                    "AGE_RECIPIENT"))
    add("infrastructure", "backups configured", OK if backups else WARN,
        "encrypted B2 backups configured" if backups else
        "B2_* / AGE_RECIPIENT not set (deploy/backup.sh)",
        None if backups else "controlled_paid_beta")

    # ---- 3/4/5. providers, licensing, data -------------------------------
    import sources
    commercial = bool(CONFIG.get("commercial_mode"))
    add("licensing", "commercial mode", OK if commercial else FAIL,
        "SNIPER_COMMERCIAL_MODE=1" if commercial else
        "off — unlicensed sources may run; required before sharing data with any user",
        None if commercial else "controlled_free_beta")
    fresh_licensed = []
    for m in sources.all_sources():
        sid = m.SOURCE_ID
        h = health_rows.get(sid, {})
        block = sources.licensing_block(m, {**CONFIG, "commercial_mode": True})
        issue = getattr(m, "config_issue", lambda c: None)(CONFIG)
        genuine_age = _age_s(h.get("last_genuine_success_at"))
        verified = h.get("status") == "healthy" and genuine_age is not None \
            and genuine_age < 48 * 3600
        state = ("verified (listings in last 48h)" if verified else
                 f"unverified — {h.get('status') or 'never polled'}"
                 + (f": {h.get('status_reason')}" if h.get("status_reason") else ""))
        add("providers", sid, OK if verified else WARN,
            state + (f"; config: {issue}" if issue else ""))
        if block:
            add("licensing", sid, WARN, block)
        elif verified:
            fresh_licensed.append(sid)
    if fresh_licensed:
        add("data", "licensed data available", OK,
            f"fresh listings from: {', '.join(fresh_licensed)}")
    else:
        add("data", "licensed data available", FAIL,
            "no source that is licensed for commercial use has delivered listings in "
            "the last 48h", "controlled_free_beta")

    # ---- 6. payments -----------------------------------------------------
    sk = _env("STRIPE_SECRET_KEY")
    if sk.startswith("sk_live_"):
        add("payments", "stripe key", OK, "live secret key present")
    elif sk.startswith("sk_test_"):
        add("payments", "stripe key", WARN, "TEST key — fine for a free beta, not for charging",
            "controlled_paid_beta")
    else:
        add("payments", "stripe key", FAIL, "STRIPE_SECRET_KEY not set", "controlled_paid_beta")
    for k in ("STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_STARTER", "STRIPE_PRICE_PRO"):
        v = _env(k)
        good = v.startswith("whsec_") if k == "STRIPE_WEBHOOK_SECRET" else v.startswith("price_")
        add("payments", k, OK if good else FAIL, "set" if good else "missing or malformed",
            None if good else "controlled_paid_beta")
    return _verdict(checks, infra_ok)


def _verdict(checks: list[dict], infra_ok: bool = False) -> dict:
    blocked = {c["blocks"] for c in checks if c.get("blocks")}
    level = "not_ready"
    for lvl in LEVELS[1:]:
        if lvl in blocked or (lvl == "private_internal_testing" and not infra_ok):
            break
        level = lvl
    blockers = {lvl: [f"{c['category']}/{c['check']}: {c['detail']}" for c in checks
                      if c.get("blocks") == lvl] for lvl in LEVELS[1:]}
    return {"ready_for": level, "checks": checks,
            "blockers": {k: v for k, v in blockers.items() if v},
            "checked_at": datetime.now(timezone.utc).isoformat()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m preflight")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--target", choices=("internal", "free", "paid"))
    a = ap.parse_args(argv)
    r = run()
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        mark = {OK: "✓", WARN: "!", FAIL: "✗"}
        cat = None
        for c in r["checks"]:
            if c["category"] != cat:
                cat = c["category"]
                print(f"\n[{cat}]")
            print(f"  {mark[c['status']]} {c['check']}: {c['detail']}")
        print(f"\nREADY FOR: {r['ready_for'].replace('_', ' ').upper()}")
        for lvl, items in r["blockers"].items():
            print(f"  blocking {lvl.replace('_', ' ')}:")
            for i in items:
                print(f"    - {i}")
    need = {"internal": "private_internal_testing", "free": "controlled_free_beta",
            "paid": "controlled_paid_beta"}.get(a.target or "")
    if need and LEVELS.index(r["ready_for"]) < LEVELS.index(need):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
