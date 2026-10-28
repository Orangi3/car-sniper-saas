"""
tests/test_jobqueue.py — durable job processing (P0.2).

Runs on SQLite by default and on real Postgres when SNIPER_TEST_PG_URL is
set (see conftest.py). The restart test uses a real worker subprocess that
is SIGKILLed mid-delivery.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _job(jid):
    import db
    with db.session() as c:
        r = c.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        return dict(r) if r else None


def test_enqueue_is_idempotent(app):
    import db, jobqueue
    with db.session() as c:
        a = jobqueue.enqueue(c, "notify", "k1", {"x": 1})
        b = jobqueue.enqueue(c, "notify", "k1", {"x": 2})
    assert a is not None and b is None
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_retry_with_backoff_then_dead_letter(app):
    import db, jobqueue
    with db.session() as c:
        jid = jobqueue.enqueue(c, "notify", "flaky", {}, max_attempts=3)

    def boom(job):
        raise RuntimeError("provider 503")

    w = "w1"
    r = jobqueue.run_one({"notify": boom}, w)
    assert r["status"] == "pending"
    j = _job(jid)
    assert j["attempts"] == 1 and "provider 503" in j["last_error"]
    assert j["run_after"] > jobqueue.ts()          # backed off into the future
    assert jobqueue.claim(w) is None                # not runnable yet

    for _ in range(2):                              # force-run remaining attempts
        with db.session() as c:
            c.execute("UPDATE jobs SET run_after = ? WHERE id = ?", (jobqueue.ts(), jid))
        jobqueue.run_one({"notify": boom}, w)
    j = _job(jid)
    assert j["status"] == "dead" and j["attempts"] == 3
    assert jobqueue.stats()["totals"]["dead"] == 1
    assert jobqueue.dead_letters()[0]["id"] == jid
    assert jobqueue.requeue_dead(jid) and _job(jid)["status"] == "pending"


def test_permanent_error_dead_letters_immediately(app):
    import db, jobqueue
    with db.session() as c:
        jid = jobqueue.enqueue(c, "notify", "perm", {})

    def bad(job):
        raise jobqueue.PermanentError("HTTP 404")

    jobqueue.run_one({"notify": bad}, "w")
    j = _job(jid)
    assert j["status"] == "dead" and j["attempts"] == 1


def test_backoff_has_jitter_and_cap():
    import random
    import jobqueue
    rng = random.Random(7)
    vals = [jobqueue.backoff_seconds(3, rng) for _ in range(50)]
    assert len(set(round(v, 3) for v in vals)) > 40           # jittered
    assert all(1 <= v <= jobqueue.BACKOFF_BASE_S * 4 for v in vals)
    assert jobqueue.backoff_seconds(40, rng) <= jobqueue.BACKOFF_CAP_S


def test_expired_lease_is_reclaimed_and_stale_worker_is_fenced(app):
    import db, jobqueue
    with db.session() as c:
        jid = jobqueue.enqueue(c, "notify", "lease", {})
    first = jobqueue.claim("dead-worker", lease_s=60)
    assert first["id"] == jid
    assert jobqueue.claim("other") is None               # leased
    with db.session() as c:                              # simulate lease expiry
        c.execute("UPDATE jobs SET locked_until = ? WHERE id = ?",
                  (jobqueue.ts(datetime.now(timezone.utc) - timedelta(seconds=1)), jid))
    second = jobqueue.claim("new-worker")
    assert second["id"] == jid and second["attempts"] == 2
    assert jobqueue.complete(jid, "dead-worker") is False  # fenced
    assert jobqueue.complete(jid, "new-worker") is True
    assert _job(jid)["status"] == "done"


def test_concurrent_workers_never_double_process(app):
    import db, jobqueue
    with db.session() as c:
        for i in range(60):
            jobqueue.enqueue(c, "score", f"c{i}", {"i": i})
    seen, lock = [], threading.Lock()

    def handler(job):
        with lock:
            seen.append(job["payload"]["i"])
        time.sleep(0.002)
        return None

    errors = []

    def run():
        try:
            jobqueue.drain({"score": handler}, max_jobs=200)
        except Exception as e:     # pragma: no cover — surfaced below
            errors.append(e)

    ts = [threading.Thread(target=run) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    # SQLite can return "database is locked" under contention; any job not
    # drained by one thread is still pending, never double-run.
    jobqueue.drain({"score": handler}, max_jobs=200)
    assert sorted(seen) == list(range(60)), errors
    assert jobqueue.stats()["totals"]["done"] == 60


def test_alert_and_delivery_jobs_commit_atomically(app, monkeypatch):
    import db, jobqueue, notifications
    monkeypatch.setenv("NTFY_TOPIC", "sniper-owner-topic-123")
    with pytest.raises(RuntimeError):
        with db.session() as c:
            notifications.enqueue_owner(c, {"composite_id": "x"}, "alert99")
            raise RuntimeError("crash after enqueue, before commit")
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_quiet_hours_defer_owner_alert_instead_of_dropping(app, monkeypatch):
    import db, notifications
    from config import CONFIG
    h = datetime.now().astimezone().hour
    monkeypatch.setitem(CONFIG, "quiet_start_hour", h)
    monkeypatch.setitem(CONFIG, "quiet_end_hour", (h + 2) % 24)
    monkeypatch.setenv("NTFY_TOPIC", "sniper-owner-topic-123")
    with db.session() as c:
        notifications.enqueue_owner(c, {"composite_id": "x"}, "alert1")
        j = dict(c.execute("SELECT run_after FROM jobs").fetchone())
    assert j["run_after"] > datetime.now(timezone.utc).isoformat()


def test_score_backpressure_then_backfill(app, monkeypatch):
    import jobqueue, sniper
    from sources.base import NormalizedListing
    monkeypatch.setitem(jobqueue.MAX_PENDING, "score", 2)
    ls = [NormalizedListing(source="t", source_id=str(i), url=f"https://x/{i}",
                            title=f"2015 Toyota Camry ${9000 + i}", price=9000 + i,
                            year=2015, make="Toyota", model="Camry") for i in range(5)]
    sniper.store_new(ls[:2])
    sniper.store_new(ls[2:])             # queue full: stored, not queued
    assert sniper.get_scorer_stats()["pending"] == 2
    monkeypatch.setitem(jobqueue.MAX_PENDING, "score", 100)
    assert sniper.backfill_unscored() == 3
    assert sniper.get_scorer_stats()["pending"] == 5


# ---------- the required restart test ------------------------------------

class _Ntfy(BaseHTTPRequestHandler):
    hang = threading.Event()
    received: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if _Ntfy.hang.is_set():
            time.sleep(30)          # never answers in time: worker is mid-send
            return
        _Ntfy.received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def test_pending_notification_survives_worker_kill_and_restart(app, monkeypatch):
    import db, jobqueue, notifications
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ntfy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        server_url = f"http://127.0.0.1:{srv.server_address[1]}"
        monkeypatch.setenv("NTFY_TOPIC", "sniper-restart-topic-1")
        monkeypatch.setenv("NTFY_SERVER", server_url)
        for k in ("PUSHOVER_TOKEN", "TWILIO_ACCOUNT_SID", "DISCORD_WEBHOOK_URL",
                  "ALERT_WEBHOOK_URL"):
            monkeypatch.delenv(k, raising=False)
        with db.session() as c:
            notifications.enqueue_owner(c, {"composite_id": "craigslist:1",
                                            "title": "2015 Tacoma", "price": 14000,
                                            "url": "https://example.com/1"}, "alert1")
        jid = 1

        # 1) A real worker process claims the job, starts sending, and is
        #    SIGKILLed while the provider hasn't answered.
        _Ntfy.hang.set()
        _Ntfy.received.clear()
        env = {**os.environ}
        proc = subprocess.Popen(
            [sys.executable, "-m", "jobs.worker", "--threads", "1",
             "--lease-seconds", "2", "--idle-sleep", "0.2"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.time() + 20
        while time.time() < deadline and (_job(jid) or {}).get("status") != "running":
            time.sleep(0.1)
        assert _job(jid)["status"] == "running"
        proc.send_signal(signal.SIGKILL)
        proc.wait(10)
        assert _job(jid)["status"] == "running"       # orphaned, not lost
        assert _Ntfy.received == []

        # 2) Restart: once the dead worker's lease expires, a new worker
        #    (another real process) reclaims and delivers it.
        _Ntfy.hang.clear()
        time.sleep(2.2)
        out = subprocess.run(
            [sys.executable, "-m", "jobs.worker", "--once", "--threads", "1"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        j = _job(jid)
        assert j["status"] == "done", (j, out.stdout, out.stderr)
        assert j["attempts"] == 2
        assert len(_Ntfy.received) == 1
        assert _Ntfy.received[0]["click"] == "https://example.com/1"
    finally:
        srv.shutdown()
