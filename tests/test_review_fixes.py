"""
tests/test_review_fixes.py — regression tests for the independent
release-safety review findings (2026-11-04) fixed on top of the
Sprint 04 candidate in the integration branch.

Covered findings:
- M1 redaction: case-insensitive key matching, broader key names,
  credential-bearing URLs, Bearer tokens, JSON key forms.
- M2 silent scheduler/worker skip: skips are printed; a shipped unit
  reporting not-found after verified sync fails closed.
- m4 dropped-unit drift: installed-but-not-shipped sniper-* units
  are printed on every sync.
- m5 failed-prepare cleanup: deploy() prints the manual cleanup
  procedure and re-raises the original error unchanged.
- m6 umask modes: extraction forces directory modes; the venv is
  mode-fixed after pip install.
- m9 unit-directory hardening: a symlinked unit directory is refused.
- m10 ssh robustness: sync.sh passes BatchMode + ConnectTimeout.

Harness mirrors tests/test_release_safety.py (fake command runner, no
production host, no real systemctl).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy import release_transition as host


OLD, PREVIOUS = ("a" * 40, "b" * 40)
MIGRATE = "sniper-migrate.service"
WEB = "sniper-web.service"
SCHEDULER = "sniper-scheduler.service"
WORKER = "sniper-worker.service"
ROOT = Path(__file__).resolve().parent.parent

NEW_UNITS = {
    WEB: "ExecStart=/opt/sniper/current/.venv/bin/python3 -m gunicorn server:app\n",
    MIGRATE: "ExecStart=/opt/sniper/current/.venv/bin/python3 -m migrations.runner\n",
    SCHEDULER: "ExecStart=/opt/sniper/current/.venv/bin/python3 -m scheduler\n",
    WORKER: "ExecStart=/opt/sniper/current/.venv/bin/python3 -m worker\n",
}


# The real production check, saved before any fixture patching: the
# dedicated tests below call it directly so the ownership invariant
# is proven against the production code, not the test seam.
_REAL_CHECK = host.check_system_dir


def _stat_result(uid):
    """A directory stat_result with a controlled owner uid (real
    directory mode, so is_dir() still works under a Path.stat mock)."""
    return os.stat_result((0o40755, 0, 0, 1, uid, 0, 0, 0, 0, 0))


def _test_system_dir_check(path):
    """Test seam for simulated deployments: enforce that the unit
    directory is a real directory and not a symlink, but skip the
    root-ownership check, which a temp directory on an unprivileged
    CI runner cannot satisfy. See _REAL_CHECK tests below for the
    production ownership proof."""
    host.require(path.is_dir() and not path.is_symlink(),
                 f"Systemd unit directory is not a real directory: {path}")


class FakeRun:
    def __init__(self, system_dir):
        self.calls = []
        self.enabled = {SCHEDULER, WORKER}
        self.not_found = set()
        self.pip_fails = False
        self.journal_lines = ""
        self.system_dir = system_dir

    def __call__(self, *cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[:2] == ("systemctl", "daemon-reload"):
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[:2] == ("systemctl", "restart"):
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[:2] == ("systemctl", "show"):
            if "FragmentPath" in cmd:
                service = cmd[2]
                return SimpleNamespace(
                    stdout=f"LoadState=loaded\nFragmentPath={self.system_dir}/{service}\n",
                    returncode=0)
            service = cmd[-1]
            return SimpleNamespace(
                stdout="LoadState=loaded\nActiveState=active\nResult=success\n",
                returncode=0)
        if cmd[:2] == ("systemctl", "is-enabled"):
            service = cmd[2]
            if service in self.not_found:
                return SimpleNamespace(stdout="not-found\n", returncode=0)
            return SimpleNamespace(
                stdout="enabled\n" if service in self.enabled else "disabled\n",
                returncode=0)
        if cmd[:2] == ("systemctl", "reset-failed"):
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[0] == "journalctl":
            return SimpleNamespace(stdout=self.journal_lines, returncode=0)
        if cmd[0] == "curl":
            return SimpleNamespace(stdout='{"status":"ok"}', returncode=0)
        if cmd[:3] == ("python3.12", "-m", "venv"):
            venv_bin = Path(cmd[3]) / "bin"
            venv_bin.mkdir(parents=True, exist_ok=True)
            python = venv_bin / "python"
            python.write_text("")
            python.chmod(0o755)
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[0] == "systemd-run":
            return SimpleNamespace(stdout="", returncode=0)
        if "-m" in cmd and "pip" in cmd:
            if self.pip_fails:
                raise subprocess.CalledProcessError(1, cmd)
            return SimpleNamespace(stdout="", returncode=0)
        raise AssertionError(f"unexpected command {cmd}")


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        # Simulated deployments run with a temp unit directory: keep
        # the directory/symlink checks, skip only the root-ownership
        # check (see _test_system_dir_check and the _REAL_CHECK tests).
        self.stack.enter_context(
            patch.object(host, "check_system_dir", _test_system_dir_check))
        self.system_dir = self.root / "system"
        self.system_dir.mkdir()
        self.fake = FakeRun(self.system_dir)
        self.t = host.Transition(self.root, self.fake, system_dir=self.system_dir)
        for revision in (OLD, PREVIOUS):
            (self.t.releases / revision).mkdir(parents=True)
        host.os.symlink(self.t.target(OLD), self.t.current)
        host.atomic_text(self.root / "current_revision", OLD)
        host.atomic_text(self.root / "previous_revision", PREVIOUS)

    def transition(self):
        return host.Transition(self.root, self.fake, system_dir=self.system_dir)

    def make_release(self, revision, units=None, venv=False):
        dest = self.t.releases / revision
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "requirements.txt").write_text("requests>=2.31\n")
        if units:
            unit_dir = dest / "deploy" / "systemd"
            unit_dir.mkdir(parents=True, exist_ok=True)
            for name, text in units.items():
                (unit_dir / name).write_text(text)
        if venv:
            venv_bin = dest / ".venv" / "bin"
            venv_bin.mkdir(parents=True, exist_ok=True)
            python = venv_bin / "python"
            python.write_text("")
            python.chmod(0o755)
        return dest

    def rollback(self):
        return self.transition().rollback(OLD, PREVIOUS)

    # ---- M1: redaction ----
    def test_redaction_covers_caps_urls_bearer_and_json(self):
        self.fake.journal_lines = (
            "SMTP_PASSWORD=hunter2\n"
            "API_KEY=zzz-api-key\n"
            "B2_APPLICATION_KEY=b2appkey\n"
            "B2_KEY_ID=b2keyid\n"
            "DATABASE_URL=postgres://dbuser:dbpass@dbhost/app\n"
            "Authorization: Bearer tok123secret\n"
            '"api_key": "jsonsekrit"\n'
            "unit started ok, no secrets here\n"
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.t.report_restart_failure(WEB, "restart", 1, "application")
        out = err.getvalue()
        for secret in ("hunter2", "zzz-api-key", "b2appkey", "b2keyid",
                       "dbuser:dbpass", "tok123secret", "jsonsekrit"):
            self.assertNotIn(secret, out)
        self.assertIn("***REDACTED***", out)
        self.assertIn("unit started ok, no secrets here", out)

    # ---- M2: silent skip ----
    def test_disabled_scheduler_skip_is_printed_and_completes(self):
        self.make_release(PREVIOUS, units=NEW_UNITS, venv=True)
        self.fake.enabled = {WORKER}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.rollback()
        self.assertIn("Skipping sniper-scheduler.service: disabled", err.getvalue())
        restarts = [c[2] for c in self.fake.calls if c[:2] == ("systemctl", "restart")]
        self.assertIn(WORKER, restarts)
        self.assertNotIn(SCHEDULER, restarts)
        self.assertIsNone(self.t.load())

    def test_shipped_unit_not_found_fails_closed(self):
        self.make_release(PREVIOUS, units=NEW_UNITS, venv=True)
        self.fake.not_found = {SCHEDULER}
        with self.assertRaises(RuntimeError):
            self.rollback()
        self.assertIsNotNone(self.t.load())

    # ---- m4: drift ----
    def test_dropped_unit_drift_is_printed(self):
        (self.system_dir / "sniper-old.service").write_text("ExecStart=/bin/true\n")
        self.make_release(PREVIOUS, units=NEW_UNITS, venv=True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.rollback()
        self.assertIn("Unit drift", err.getvalue())
        self.assertIn("sniper-old.service", err.getvalue())

    # ---- m5: prepare failure cleanup note ----
    def test_prepare_failure_prints_cleanup_and_reraises(self):
        self.fake.pip_fails = True
        data = self.archive("d" * 40, {"server.py": b"pass\n"})
        err = io.StringIO()
        with contextlib.redirect_stderr(err), patch.object(
                host.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data))):
            with self.assertRaises(subprocess.CalledProcessError):
                self.transition().deploy(
                    "d" * 40, hashlib.sha256(data).hexdigest(), True)
        self.assertIn("Prepare failed", err.getvalue())
        self.assertIn("remove both by hand", err.getvalue())
        self.assertIsNotNone(self.t.load())

    @staticmethod
    def archive(revision, files):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT,
                          pax_headers={"comment": revision}) as bundle:
            for name, data in files.items():
                item = tarfile.TarInfo(name)
                item.size = len(data)
                bundle.addfile(item, io.BytesIO(data))
        return output.getvalue()

    # ---- m6: umask modes ----
    def test_fix_venv_modes_restores_readable_executable_modes(self):
        venv = self.root / "venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "lib").mkdir()
        script = venv / "bin" / "python"
        script.write_text("")
        script.chmod(0o700)
        data = venv / "lib" / "mod.py"
        data.write_text("")
        data.chmod(0o600)
        host.Transition.fix_venv_modes(venv)
        self.assertEqual(oct((venv / "bin").stat().st_mode & 0o777), "0o755")
        self.assertEqual(oct(script.stat().st_mode & 0o777), "0o755")
        self.assertEqual(oct(data.stat().st_mode & 0o777), "0o644")

    def test_extract_forces_directory_modes_under_restrictive_umask(self):
        data = self.archive("e" * 40, {"a/b/c.txt": b"x\n", "a/d.txt": b"y\n"})
        stage = self.root / "stage"
        stage.mkdir()
        old_umask = host.os.umask(0o077)
        try:
            host.Transition.extract(io.BytesIO(data), stage, "e" * 40)
        finally:
            host.os.umask(old_umask)
        self.assertEqual(oct((stage / "a").stat().st_mode & 0o777), "0o755")
        self.assertEqual(oct((stage / "a" / "b").stat().st_mode & 0o777), "0o755")
        self.assertEqual(oct((stage / "a" / "b" / "c.txt").stat().st_mode & 0o777), "0o644")

    # ---- m9: unit directory hardening ----
    def test_symlinked_unit_directory_is_refused(self):
        real = self.root / "real-system"
        real.mkdir()
        link = self.root / "link-system"
        host.os.symlink(real, link)
        t = host.Transition(self.root, self.fake, system_dir=link)
        self.make_release(PREVIOUS, units=NEW_UNITS, venv=True)
        with self.assertRaises(RuntimeError):
            t.rollback(OLD, PREVIOUS)

    # ---- production ownership guard, tested directly ----
    # These call the REAL host.check_system_dir (not the fixture
    # seam), so the production invariant is proven on every runner:
    # ownership is simulated by mocking only Path.stat's uid, which
    # is deterministic whether the runner is root or not. The mock
    # keeps a real directory mode so is_dir() still works.
    def test_production_check_accepts_root_owned_directory(self):
        target = self.root / "owned"
        target.mkdir()
        with patch.object(Path, "stat",
                          lambda self, **kwargs: _stat_result(0)):
            _REAL_CHECK(target)

    def test_production_check_rejects_non_root_owned_directory(self):
        target = self.root / "owned"
        target.mkdir()
        with patch.object(Path, "stat",
                          lambda self, **kwargs: _stat_result(1000)):
            with self.assertRaises(RuntimeError):
                _REAL_CHECK(target)

    def test_production_check_rejects_symlinked_directory(self):
        real = self.root / "real-system"
        real.mkdir()
        link = self.root / "link-system"
        host.os.symlink(real, link)
        with self.assertRaises(RuntimeError):
            _REAL_CHECK(link)

    # ---- m10: ssh robustness ----
    def test_sync_sh_uses_batch_mode_and_connect_timeout(self):
        bindir = self.root / "bin"
        bindir.mkdir()
        log = self.root / "ssh-argv.log"
        ssh = bindir / "ssh"
        ssh.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit 0\n")
        ssh.chmod(0o755)
        env = {"PATH": f"{bindir}:/usr/bin:/bin"}
        result = subprocess.run(
            ["bash", str(ROOT / "deploy" / "sync.sh"), "--status", "root@host"],
            capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 0)
        argv = log.read_text()
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ConnectTimeout=30", argv)


if __name__ == "__main__":
    unittest.main()
