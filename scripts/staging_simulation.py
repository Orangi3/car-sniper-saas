"""
scripts/staging_simulation.py — run the REAL services the way production
does and check the whole system, including failure and recovery.

    python scripts/staging_simulation.py --pg-admin-url postgresql://postgres@127.0.0.1:55432/postgres \
        [--pg-ctl "su postgres -c '/usr/lib/postgresql/16/bin/pg_ctl -D DATA ...'"]

What is real: PostgreSQL, migrations, gunicorn web, jobs.scheduler,
jobs.worker (separate OS processes, no Claude session involved), HTTP API,
ingestion, valuation, matching, the durable job queue, notification
delivery over HTTP, crash/restart behaviour.

What is SIMULATED (and labeled so in the output): the provider. A local
HTTP server answers like eBay's OAuth + Browse endpoints with synthetic
listings (EBAY_API_BASE), and stands in for the ntfy push server
(NTFY_SERVER). No real listing data is used or produced.

The code under test is a `git archive` of HEAD in a temp directory, so the
working tree is never modified. Exit 0 only if every step passes.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


# ---------------------------------------------------------------- simulated provider
class Provider:
    """eBay-shaped OAuth + Browse search, and an ntfy-shaped push sink."""

    def __init__(self):
        self.items: list[dict] = []
        self.pushes: list[dict] = []
        self.searches = 0
        self.lock = threading.Lock()

    def item(self, iid, price, title, miles):
        # US marketplace: no sellerAccountType (eBay returns it only in EU)
        return {"itemId": f"v1|{iid}|0", "title": f"{title} {miles // 1000}k miles",
                "itemWebUrl": f"https://www.ebay.test/itm/{iid}",
                "price": {"value": f"{price}.00", "currency": "USD"},
                "buyingOptions": ["FIXED_PRICE"],
                "itemLocation": {"city": "Northport", "stateOrProvince": "AL",
                                 "postalCode": "35473", "country": "US"},
                "itemCreationDate": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                "seller": {"username": f"s{iid}", "feedbackPercentage": "99.0"},
                "distanceFromPickupLocation": {"value": "6", "unitOfMeasure": "MILE"}}

    def handler(self):
        prov = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                if self.path.startswith("/identity/v1/oauth2/token"):
                    return self._send(200, {"access_token": "sim-token", "expires_in": 7200})
                try:
                    payload = json.loads(body or b"{}")
                except ValueError:
                    payload = {"raw": body.decode(errors="replace")}
                with prov.lock:
                    prov.pushes.append(payload)
                return self._send(200, {"id": len(prov.pushes)})

            def do_GET(self):
                if self.path.startswith("/buy/browse/v1/item_summary/search"):
                    with prov.lock:
                        prov.searches += 1
                        items = list(prov.items)
                    return self._send(200, {"total": len(items), "itemSummaries": items})
                return self._send(404, {})
        return H


# ---------------------------------------------------------------- helpers
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_for(fn, timeout=60, every=0.5, what="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:          # services restarting, DB down, ...
            last = e
        time.sleep(every)
    raise TimeoutError(f"timed out waiting for {what} (last: {last!r})")


class Sim:
    def __init__(self, args):
        self.args = args
        self.results: list[dict] = []
        self.procs: dict[str, subprocess.Popen] = {}
        self.tmp = Path(tempfile.mkdtemp(prefix="sniper-sim-"))
        self.app = self.tmp / "app"
        self.logs = self.tmp / "logs"
        self.logs.mkdir()
        self.prov = Provider()
        self.dbname = "sniper_sim_" + uuid.uuid4().hex[:8]
        u = urlparse(args.pg_admin_url)
        self.db_url = urlunparse(u._replace(path="/" + self.dbname))
        self.web_port = free_port()
        self.base = f"http://127.0.0.1:{self.web_port}"
        # systemd stand-in: services we want up, restarted like the units do
        # (web/scheduler Restart=on-failure, worker Restart=always)
        self.wanted: set[str] = set()
        self.restarts: dict[str, int] = {}
        self._sup_stop = threading.Event()

    # -- bookkeeping
    def step(self, name, fn):
        t0 = time.time()
        try:
            detail = fn()
            self.results.append({"step": name, "ok": True, "detail": detail,
                                 "seconds": round(time.time() - t0, 1)})
            print(f"  PASS  {name}: {detail}")
            return True
        except Exception as e:
            self.results.append({"step": name, "ok": False,
                                 "detail": f"{type(e).__name__}: {e}"[:400],
                                 "seconds": round(time.time() - t0, 1)})
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            return False

    def env(self):
        e = {k: v for k, v in os.environ.items()
             if not k.startswith(("SNIPER_TEST", "EBAY_", "MARKETCHECK_", "STRIPE_"))}
        e.update({"DATABASE_URL": self.db_url, "SNIPER_COMMERCIAL_MODE": "1",
                  "ALLOW_REGISTRATION": "1", "EBAY_CLIENT_ID": "sim", "EBAY_CLIENT_SECRET": "sim",
                  "EBAY_API_BASE": f"http://127.0.0.1:{self.prov_port}",
                  "NTFY_SERVER": f"http://127.0.0.1:{self.prov_port}",
                  "PYTHONUNBUFFERED": "1", "SNIPER_SECRET_KEY": "sim-secret"})
        return e

    def sql(self, q, params=()):
        import psycopg2
        import psycopg2.extras
        c = psycopg2.connect(self.db_url)
        try:
            cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(q, params)
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            c.commit()
            return rows
        finally:
            c.close()

    def start(self, name, argv):
        log = open(self.logs / f"{name}.log", "a")
        self.procs[name] = subprocess.Popen(argv, cwd=self.app, env=self.env(), stdout=log,
                                            stderr=subprocess.STDOUT, start_new_session=True)
        return self.procs[name].pid

    def supervise(self):
        policy = {"web": "on-failure", "scheduler": "on-failure", "worker": "always"}
        starters = {"web": self.start_web, "scheduler": self.start_scheduler,
                    "worker": self.start_worker}
        while not self._sup_stop.wait(2):        # RestartSec (scaled down)
            for n in list(self.wanted):
                p = self.procs.get(n)
                if p is None or p.poll() is None:
                    continue
                if policy[n] == "always" or p.returncode != 0:
                    self.restarts[n] = self.restarts.get(n, 0) + 1
                    starters[n]()

    def start_web(self):
        return self.start("web", [str(Path(PY).parent / "gunicorn"), "server:app", "--workers", "2",
                                  "--bind", f"127.0.0.1:{self.web_port}", "--timeout", "30",
                                  "--graceful-timeout", "10"])

    def start_scheduler(self, lease=15, steal=0):
        # production semantics (sniper-scheduler.service), shorter lease
        return self.start("scheduler", [PY, "-m", "jobs.scheduler", "--interval", "3",
                                        "--lease-seconds", str(lease),
                                        "--steal-after-seconds", str(steal)])

    def start_worker(self):
        return self.start("worker", [PY, "-m", "jobs.worker", "--threads", "2",
                                     "--idle-sleep", "0.5", "--lease-seconds", "30"])

    def stop(self, name, sig=signal.SIGTERM, timeout=30, wanted=False):
        if not wanted:
            self.wanted.discard(name)
        p = self.procs.get(name)
        if not p or p.poll() is not None:
            return p.returncode if p else None
        os.killpg(p.pid, sig)
        try:
            return p.wait(timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            return p.wait(10)

    def ticks(self):
        r = self.sql("SELECT tick_counter FROM scheduler_state WHERE id = 1")
        return int(r[0]["tick_counter"] or 0) if r else 0

    def pushes_for(self, iid):
        with self.prov.lock:
            return [p for p in self.prov.pushes if iid in json.dumps(p)]

    # -- the run
    def run(self) -> int:
        a = self.args
        srv = ThreadingHTTPServer(("127.0.0.1", 0), self.prov.handler())
        self.prov_port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        threading.Thread(target=self.supervise, daemon=True).start()
        peers = [(1000 + i, 9600 + (i % 6) * 150, "2015 Toyota Camry SE", 118000 + i * 2500)
                 for i in range(12)]
        self.prov.items = [self.prov.item(*p) for p in peers]
        print(f"[sim] temp dir {self.tmp}; database {self.dbname}; provider/ntfy stand-in on "
              f":{self.prov_port} (SIMULATED)")
        ok = True
        S = self.step

        def export():
            arch = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, capture_output=True,
                                  check=True).stdout
            self.app.mkdir()
            subprocess.run(["tar", "-x", "-C", str(self.app)], input=arch, check=True)
            (self.app / "overrides.json").write_text(json.dumps(
                {"source_min_interval_s": {"ebay": 2}, "zip": "35401", "radius_mi": 150}))
            head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                  capture_output=True, text=True).stdout.strip()
            return f"release {head} exported"
        ok &= S("01 release export", export)

        def createdb():
            import psycopg2
            c = psycopg2.connect(a.pg_admin_url)
            c.autocommit = True
            c.cursor().execute(f'CREATE DATABASE "{self.dbname}"')
            c.close()
            return "fresh PostgreSQL database"
        ok &= S("02 fresh database", createdb)

        def migrate():
            outs = [subprocess.run([PY, "-m", "migrations.runner"], cwd=self.app, env=self.env(),
                                   capture_output=True, text=True, check=True).stdout
                    for _ in range(2)]
            n = len(self.sql("SELECT id FROM schema_migrations"))
            assert "0 new migration(s)" in outs[1], outs[1]
            return f"{n} migrations applied; re-run is a no-op"
        ok &= S("03 migrations", migrate)
        if not ok:
            return self.finish()

        def web():
            self.start_web(); self.wanted.add("web")
            wait_for(lambda: requests.get(self.base + "/health", timeout=2).ok, 30, what="web")
            return f"gunicorn (2 workers) healthy on :{self.web_port}"
        ok &= S("04 web service (gunicorn)", web)

        def sched():
            self.start_scheduler(); self.wanted.add("scheduler")
            n = wait_for(lambda: self.ticks() >= 1 and self.ticks(), 40, what="first tick")
            return f"ticking (tick_counter={n})"
        ok &= S("05 scheduler", sched)

        def worker():
            self.start_worker(); self.wanted.add("worker")
            time.sleep(1)
            assert self.procs["worker"].poll() is None, "worker exited"
            return "running"
        ok &= S("06 worker", worker)

        def ingest():
            n = wait_for(lambda: len(self.sql("SELECT 1 FROM listings WHERE source='ebay'")) >= 12
                         and len(self.sql("SELECT 1 FROM listings WHERE source='ebay'")), 60,
                         what="ingestion")
            h = self.sql("SELECT status FROM source_health WHERE source_id='ebay'")[0]
            loc = self.sql("SELECT DISTINCT location_source FROM listings WHERE source='ebay'")
            return (f"{n} SIMULATED provider listings ingested; ebay health={h['status']}; "
                    f"location from {[r['location_source'] for r in loc]}")
        ok &= S("07 provider ingestion + normalization (simulated provider)", ingest)

        s = requests.Session()

        def user():
            email = "sim-buyer@example.com"
            r = s.post(self.base + "/api/auth/register",
                       json={"email": email, "password": "sim-pass-123"})
            assert r.ok, r.text
            subprocess.run([PY, "-c", f"import auth; auth.set_plan(auth.get_user_by_email('{email}').id, 'pro')"],
                           cwd=self.app, env=self.env(), check=True)
            s.post(self.base + "/api/auth/login", json={"email": email, "password": "sim-pass-123"})
            assert s.post(self.base + "/api/me/territory",
                          json={"home_zip": "35401", "radius_mi": 150}).ok
            assert s.post(self.base + "/api/me/alert_channels",
                          json={"ntfy_topic": "generate"}).ok
            r = s.post(self.base + "/api/saved_searches", json={
                "name": "cheap camry", "notify_enabled": True,
                "filter": {"make": "Toyota", "max_price": 9000}})
            assert r.ok, r.text
            return "registered, Pro plan, territory 35401+150mi, ntfy channel, saved search"
        ok &= S("08 subscriber setup via HTTP API", user)

        def deal():
            self.prov.items.append(self.prov.item(2001, 6400, "2015 Toyota Camry SE", 124000))
            pushes = wait_for(lambda: self.pushes_for("2001"), 90, what="alert push")
            nm = self.sql("SELECT evidence_class, confidence, trust, risk_flags, evidence "
                          "FROM near_misses WHERE composite_id = 'ebay:v1|2001|0'")[0]
            ev = json.loads(nm["evidence"] or "{}")
            m = self.sql("SELECT event, profit_base FROM user_matches WHERE composite_id = "
                         "'ebay:v1|2001|0'")
            return (f"valued {nm['evidence_class']}/{nm['confidence']}/{nm['trust']}, flags "
                    f"{nm['risk_flags']}, dealer_share={ev.get('dealer_share')}, "
                    f"segments unknown_seller n={ev.get('segments', {}).get('unknown_seller_asking', {}).get('n')}; "
                    f"match {m}; {len(pushes)} push(es) delivered to the ntfy stand-in")
        ok &= S("09-14 dedup → comps → valuation → profit → match → alert → delivery", deal)

        def dashboard():
            d = s.get(self.base + "/api/deals?view=all").json()
            hit = [x for x in d if x["composite_id"] == "ebay:v1|2001|0"]
            assert hit, "deal missing from /api/deals"
            page = s.get(self.base + "/m")
            assert page.ok and "<html" in page.text.lower()
            x = hit[0]
            return (f"/api/deals shows it (profit {x['profit']}, risk-adjusted "
                    f"{x['opportunity']['risk_adjusted_profit']}); /m 200")
        ok &= S("15 customer dashboard", dashboard)

        def ops():
            subprocess.run([PY, "-c", "import auth; auth.set_role(auth.get_user_by_email('sim-buyer@example.com').id, 'admin')"],
                           cwd=self.app, env=self.env(), check=True)
            o = s.get(self.base + "/api/admin/ops").json()
            assert "ebay" in o["data_health"]["working"], o["data_health"]["working"]
            page = s.get(self.base + "/admin/ops")
            assert page.ok
            di = o["deal_intelligence"]
            return (f"ebay working; deals funnel {di.get('funnel', di.get('qualified_deals'))}; "
                    f"notifications {di['notifications']['delivered_to_provider']} delivered; "
                    f"system ready_for={o['system_health']['ready_for']}")
        ok &= S("16 owner operations view", ops)

        def worker_crash():
            before = len(self.pushes_for("2001"))
            self.stop("worker", signal.SIGKILL)       # not restarted: test the queue alone
            # material price drop while no worker is running
            for it in self.prov.items:
                if it["itemId"] == "v1|2001|0":
                    it["price"] = {"value": "5600.00", "currency": "USD"}
            wait_for(lambda: self.sql("SELECT 1 FROM jobs WHERE kind='score' AND status='pending' "
                                      "AND dedupe_key = %s", ("score:ebay:v1|2001|0:5600",)),
                     60, what="queued re-score with no worker")
            self.start_worker(); self.wanted.add("worker")
            wait_for(lambda: len(self.pushes_for("2001")) > before, 90, what="price-drop push")
            time.sleep(4)
            n = len(self.pushes_for("2001")) - before
            assert n == 1, f"{n} price-drop pushes (expected exactly 1)"
            return "worker SIGKILLed; price drop queued durably; restarted worker delivered it once"
        ok &= S("17 worker crash recovery", worker_crash)

        def sched_crash():
            t, t0 = self.ticks(), time.time()
            self.stop("scheduler", signal.SIGKILL, wanted=True)   # lease NOT released
            wait_for(lambda: self.ticks() > t, 90, what="new scheduler tick after lease expiry")
            return (f"scheduler SIGKILLed with its lease held; supervisor restarts were refused "
                    f"until the 15s lease expired, then the replacement ticked after "
                    f"{time.time() - t0:.0f}s (restarts: {self.restarts.get('scheduler', 0)})")
        ok &= S("18 scheduler crash recovery", sched_crash)

        def db_outage():
            if not a.pg_ctl:
                raise RuntimeError("skipped: pass --pg-ctl to stop/start PostgreSQL")
            self.prov.items.append(self.prov.item(2002, 6500, "2015 Toyota Camry SE", 121000))
            before = dict(self.restarts)
            pids = {n: p.pid for n, p in self.procs.items()}
            subprocess.run(a.pg_ctl.replace("ACTION", "stop -m fast"), shell=True, check=True,
                           capture_output=True)
            time.sleep(10)
            subprocess.run(a.pg_ctl.replace("ACTION", "start -w"), shell=True, check=True,
                           capture_output=True)
            t0 = time.time()
            wait_for(lambda: requests.get(self.base + "/health", timeout=2).ok, 60, what="web")
            wait_for(lambda: self.pushes_for("2002"), 120, what="post-outage alert")
            crashed = {n: self.restarts.get(n, 0) - before.get(n, 0) for n in self.wanted}
            same = [n for n, p in self.procs.items() if p.pid == pids.get(n)]
            return (f"PostgreSQL stopped 10s; restarts needed: {crashed}; processes that rode "
                    f"it out: {same}; next listing ingested + alerted "
                    f"{time.time() - t0:.0f}s after the database returned")
        ok &= S("19 database outage and recovery", db_outage)

        def shutdown():
            codes = {n: self.stop(n, signal.SIGTERM, 60) for n in ("scheduler", "worker", "web")}
            lock = self.sql("SELECT holder, expires_at FROM billing_job_locks "
                            "WHERE job_name = 'scheduler'")
            released = not lock or not lock[0]["holder"]
            bad = {n: c for n, c in codes.items() if c not in (0, -15)}
            assert not bad, f"non-clean exit codes {bad}"
            assert released, f"scheduler lease still held: {lock}"
            pushes = len(self.prov.pushes)
            self.start_web(); self.start_scheduler(); self.start_worker()
            self.wanted.update({"web", "scheduler", "worker"})
            wait_for(lambda: requests.get(self.base + "/health", timeout=2).ok, 30, what="web")
            t = self.ticks()
            wait_for(lambda: self.ticks() >= t + 3, 60, what="ticks after restart")
            time.sleep(3)
            assert len(self.prov.pushes) == pushes, "restart re-sent notifications"
            return (f"SIGTERM exit codes {codes}; scheduler lease released; restart resumed "
                    f"ticking with no duplicate notifications")
        ok &= S("20 graceful shutdown and restart", shutdown)
        return self.finish()

    def finish(self) -> int:
        self._sup_stop.set()
        self.wanted.clear()
        for n in list(self.procs):
            self.stop(n, signal.SIGTERM, 20)
        try:
            import psycopg2
            c = psycopg2.connect(self.args.pg_admin_url)
            c.autocommit = True
            c.cursor().execute(f'DROP DATABASE IF EXISTS "{self.dbname}" WITH (FORCE)')
            c.close()
        except Exception:
            pass
        passed = sum(r["ok"] for r in self.results)
        report = {"passed": passed, "total": len(self.results), "provider": "SIMULATED",
                  "restarts": self.restarts,
                  "logs": str(self.logs), "steps": self.results}
        (self.tmp / "report.json").write_text(json.dumps(report, indent=2))
        print(f"\n[sim] {passed}/{len(self.results)} steps passed — report {self.tmp / 'report.json'}")
        return 0 if passed == len(self.results) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pg-admin-url", required=True,
                    help="PostgreSQL URL of a role that may CREATE DATABASE")
    ap.add_argument("--pg-ctl", default="",
                    help="shell command with ACTION placeholder to stop/start PostgreSQL, "
                         "for the outage step (e.g. \"pg_ctl -D DATA ACTION\")")
    return Sim(ap.parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
