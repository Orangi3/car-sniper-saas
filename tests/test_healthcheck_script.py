"""
tests/test_healthcheck_script.py — runs deploy/healthcheck.sh for real
(stub curl on PATH, real `python -m preflight` against a scratch DB): the
pipeline dead-man ping must go out only while scheduler and worker are
actually working.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")


def _run(tmp_path, web_ok=True, **extra_env):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "curl.log"
    (bindir / "curl").write_text(
        "#!/bin/sh\n"
        f"for a; do last=$a; done; echo \"$last\" >> {log}\n"
        f"case \"$last\" in *'/health') exit {0 if web_ok else 7} ;; esac\nexit 0\n")
    (bindir / "curl").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
           "SNIPER_APP_DIR": str(ROOT), "SNIPER_PYTHON": sys.executable,
           "HEALTHCHECK_WEB_URL": "https://hc.test/web",
           "HEALTHCHECK_PIPELINE_URL": "https://hc.test/pipeline", **extra_env}
    r = subprocess.run(["sh", str(ROOT / "deploy" / "healthcheck.sh")], env=env,
                       capture_output=True, text=True, timeout=120)
    pinged = log.read_text().split() if log.exists() else []
    return r.returncode, r.stdout, pinged


def _tick(minutes_ago):
    import db
    t = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with db.session() as c:
        c.execute("UPDATE scheduler_state SET last_tick_at = ? WHERE id = 1", (t,))


def test_healthy_pipeline_pings_both(app, tmp_path):
    _tick(1)
    rc, out, pinged = _run(tmp_path)
    assert rc == 0, out
    assert "https://hc.test/web" in pinged and "https://hc.test/pipeline" in pinged


def test_dead_scheduler_withholds_pipeline_ping_only(app, tmp_path):
    _tick(30)                                    # last tick 30 min ago
    rc, out, pinged = _run(tmp_path)
    assert rc == 1 and "pipeline FAILED" in out and "scheduler" in out
    assert "https://hc.test/web" in pinged and "https://hc.test/pipeline" not in pinged


def test_stuck_worker_withholds_pipeline_ping(app, tmp_path):
    import db
    _tick(1)
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    with db.session() as c:
        c.execute("INSERT INTO jobs(kind, dedupe_key, payload, status, run_after, created_at, "
                  "updated_at) VALUES ('score', 'k1', '{}', 'pending', ?, ?, ?)", (old, old, old))
    rc, out, pinged = _run(tmp_path)
    assert rc == 1 and "https://hc.test/pipeline" not in pinged


def test_web_down_withholds_web_ping(app, tmp_path):
    _tick(1)
    rc, out, pinged = _run(tmp_path, web_ok=False)
    assert rc == 1 and "web /health FAILED" in out
    assert "https://hc.test/web" not in pinged and "https://hc.test/pipeline" in pinged


def test_web_probe_uses_the_public_origin_from_the_env_file(app, tmp_path):
    """/etc/sniper/env has BILLING_ORIGIN (written by install.sh) but no
    STAGING_HOSTNAME; probing https://127.0.0.1 fails TLS on every host."""
    _tick(1)
    rc, out, pinged = _run(tmp_path, BILLING_ORIGIN="https://staging.example.test/")
    assert rc == 0, out
    assert "https://staging.example.test/health" in pinged
