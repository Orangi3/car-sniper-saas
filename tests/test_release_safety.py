"""
tests/test_release_safety.py — release-safety execution tests for the
Sprint 04 combined candidate plus the unit-sync and per-release-venv
changes.

Two guarantees are tested against the real Transition code with a fake
command runner (no production host, no real systemctl):

1. Existing-host unit safety: a deploy/rollback installs the release's
   unit files, daemon-reloads, and verifies the EFFECTIVE
   configuration (LoadState + FragmentPath) before any service
   restarts. A sync failure aborts before restarts and is retried by
   recovery — never stranded, never silent.
2. Dependency rollback safety: each release carries its own venv, the
   units run the interpreter through the `current` symlink, and a
   rollback either restores the target release's venv (no pip runs
   during rollback at all) or — when the target's units need a venv
   the target does not carry — refuses BEFORE the pointer moves.
   A rollback can therefore never silently claim success on an
   incompatible dependency environment.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy import release_transition as host


OLD, PREVIOUS, THIRD = ("a" * 40, "b" * 40, "c" * 40)
MIGRATE = "sniper-migrate.service"
WEB = "sniper-web.service"
SCHEDULER = "sniper-scheduler.service"
WORKER = "sniper-worker.service"
ROOT = Path(__file__).resolve().parent.parent

NEW_UNITS = {
    WEB: "ExecStart=/opt/sniper/current/.venv/bin/python3 -m gunicorn server:app\n",
    MIGRATE: "ExecStart=/opt/sniper/current/.venv/bin/python3 -m migrations.runner\n",
}
OLD_UNITS = {
    WEB: "ExecStart=/opt/sniper/.venv/bin/gunicorn server:app\n",
    MIGRATE: "ExecStart=/opt/sniper/.venv/bin/python3 -m migrations.runner\n",
}


def _test_system_dir_check(path):
    """Test seam for simulated deployments: enforce that the unit
    directory is a real directory and not a symlink, but skip the
    root-ownership check, which a temp directory on an unprivileged
    CI runner cannot satisfy. The ownership check itself is NOT
    skipped in production (sync_units always calls the real
    host.check_system_dir) and is covered by dedicated tests in
    test_review_fixes.py calling that function directly."""
    host.require(path.is_dir() and not path.is_symlink(),
                 f"Systemd unit directory is not a real directory: {path}")


class FakeRun:
    def __init__(self, system_dir):
        self.calls = []
        self.fail = set()
        self.limit = set()
        self.failed_state = set()
        self.reset_done = set()
        self.enabled = {SCHEDULER, WORKER}
        self.reload_fails = False
        self.verify_mismatch = None
        self.journal_lines = "unit failed to start: api_key=SECRETVALUE123\n"
        self.system_dir = system_dir

    def __call__(self, *cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[:2] == ("systemctl", "daemon-reload"):
            if self.reload_fails:
                raise subprocess.CalledProcessError(1, cmd)
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[:2] == ("systemctl", "restart"):
            service = cmd[2]
            if service in self.limit and service not in self.reset_done:
                raise subprocess.CalledProcessError(3, cmd)
            if service in self.fail:
                raise subprocess.CalledProcessError(1, cmd)
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[:2] == ("systemctl", "show"):
            if "FragmentPath" in cmd:
                service = cmd[2]
                if self.verify_mismatch == service:
                    return SimpleNamespace(
                        stdout=f"LoadState=loaded\nFragmentPath=/elsewhere/{service}\n",
                        returncode=0)
                return SimpleNamespace(
                    stdout=f"LoadState=loaded\nFragmentPath={self.system_dir}/{service}\n",
                    returncode=0)
            service = cmd[-1]
            result = "start-limit-hit" if service in self.limit else "success"
            active = "failed" if service in self.failed_state else "active"
            return SimpleNamespace(
                stdout=f"LoadState=loaded\nActiveState={active}\nResult={result}\n",
                returncode=0)
        if cmd[:2] == ("systemctl", "is-enabled"):
            service = cmd[2]
            return SimpleNamespace(
                stdout="enabled\n" if service in self.enabled else "disabled\n",
                returncode=0)
        if cmd[:2] == ("systemctl", "reset-failed"):
            self.reset_done.add(cmd[2])
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[:2] == ("systemctl", "stop"):
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
            return SimpleNamespace(stdout="", returncode=0)
        raise AssertionError(f"unexpected command {cmd}")

    def kinds(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == prefix]

    def has(self, *prefix):
        return bool(self.kinds(*prefix))


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        # Simulated deployments run with a temp unit directory: keep
        # the directory/symlink checks, skip only the root-ownership
        # check (see _test_system_dir_check). Production sync always
        # uses the real host.check_system_dir.
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

    def recover(self):
        return self.transition().recover()

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

    def deploy(self, revision, files, restart=True):
        data = self.archive(revision, files)
        with patch.object(host.sys, "stdin",
                          SimpleNamespace(buffer=io.BytesIO(data))):
            self.transition().deploy(
                revision, hashlib.sha256(data).hexdigest(), restart)

    def assert_state(self, active, markers):
        self.assertEqual(self.t.active(), active)
        self.assertEqual(self.t.markers(), markers)

    # ---- unit sync ----

    def test_unit_sync_installs_units_and_verifies_before_restarts(self):
        self.make_release(PREVIOUS, units=NEW_UNITS, venv=True)
        self.rollback()
        for name, text in NEW_UNITS.items():
            installed = self.system_dir / name
            self.assertEqual(installed.read_text(), text)
            self.assertEqual(oct(installed.stat().st_mode & 0o777), "0o644")
        reload_at = next(i for i, c in enumerate(self.fake.calls)
                         if c[:2] == ("systemctl", "daemon-reload"))
        first_restart = next(i for i, c in enumerate(self.fake.calls)
                             if c[:2] == ("systemctl", "restart"))
        self.assertLess(reload_at, first_restart)
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))
        self.assertIsNone(self.t.load())

    def test_unit_sync_failure_aborts_before_restarts_and_recovers(self):
        self.make_release(PREVIOUS, units=NEW_UNITS, venv=True)
        self.fake.reload_fails = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.rollback()
        failed_calls = list(self.fake.calls)
        self.assertFalse([c for c in failed_calls
                          if c[:2] == ("systemctl", "restart")])
        # The flip already happened, but no service restarted: the
        # journal still records the transition for recovery.
        self.assertEqual(self.t.load()["service"], "pending")
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))
        self.fake.reload_fails = False
        self.fake.calls.clear()
        self.recover()
        self.assertIsNone(self.t.load())
        self.assertTrue(self.fake.has("systemctl", "restart"))
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))

    def test_unit_verify_mismatch_aborts_before_restarts(self):
        self.make_release(PREVIOUS, units=NEW_UNITS, venv=True)
        self.fake.verify_mismatch = WEB
        with self.assertRaises(RuntimeError):
            self.rollback()
        self.assertFalse([c for c in self.fake.calls
                          if c[:2] == ("systemctl", "restart")])

    def test_unit_sync_skips_releases_without_units(self):
        self.make_release(PREVIOUS)
        self.rollback()
        self.assertFalse(self.fake.has("systemctl", "daemon-reload"))
        self.assertEqual(list(self.system_dir.iterdir()), [])
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))

    # ---- per-release venvs and rollback ----

    def test_deploy_builds_per_release_venv_and_never_touches_shared_venv(self):
        files = {"requirements.txt": b"requests>=2.31\n"}
        for name, text in NEW_UNITS.items():
            files[f"deploy/systemd/{name}"] = text.encode()
        self.deploy(THIRD, files)
        dest = self.t.releases / THIRD
        venv_python = str(dest / ".venv" / "bin" / "python")
        self.assertIn(("python3.12", "-m", "venv", str(dest / ".venv")),
                      self.fake.calls)
        pip_calls = [c for c in self.fake.calls if "-m" in c and "pip" in c]
        self.assertEqual(len(pip_calls), 1)
        self.assertEqual(pip_calls[0][0], venv_python)
        self.assertTrue(pip_calls[0][-1].endswith("requirements.txt"))
        self.assertFalse(any("/opt/sniper/.venv" in " ".join(map(str, c))
                             for c in self.fake.calls))
        canary = [c for c in self.fake.calls if "--unit=sniper-canary" in c]
        self.assertEqual(len(canary), 1)
        self.assertIn(venv_python, canary[0])
        self.assertIn("-m", canary[0])
        self.assertIn("gunicorn", canary[0])
        self.assert_state(THIRD, (THIRD, OLD))

    def test_rollback_to_venv_release_runs_no_pip_and_syncs_target_units(self):
        self.make_release(PREVIOUS, units=NEW_UNITS, venv=True)
        self.rollback()
        self.assertFalse([c for c in self.fake.calls
                          if "-m" in c and "pip" in c])
        self.assertFalse([c for c in self.fake.calls
                          if c[:3] == ("python3.12", "-m", "venv")])
        for name, text in NEW_UNITS.items():
            self.assertEqual((self.system_dir / name).read_text(), text)
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))

    def test_rollback_to_venvless_new_units_fails_closed_before_flip(self):
        self.make_release(PREVIOUS, units=NEW_UNITS, venv=False)
        with self.assertRaises(RuntimeError):
            self.rollback()
        # Nothing moved: pointer, markers, journal, units, restarts.
        self.assert_state(OLD, (OLD, PREVIOUS))
        self.assertIsNone(self.t.load())
        self.assertEqual(list(self.system_dir.iterdir()), [])
        self.assertFalse(self.fake.has("systemctl", "restart"))
        self.assertFalse(self.fake.has("systemctl", "daemon-reload"))

    def test_rollback_to_old_style_release_succeeds_era_bridge(self):
        self.make_release(PREVIOUS, units=OLD_UNITS, venv=False)
        self.rollback()
        for name, text in OLD_UNITS.items():
            self.assertEqual((self.system_dir / name).read_text(), text)
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))

    def test_dependency_change_rollback_restores_old_venv(self):
        # OLD (A) is a new-style release with its own venv.
        self.make_release(OLD, units=NEW_UNITS, venv=True)
        # Deploy B with different requirements: B builds its own venv.
        files = {"requirements.txt": b"requests>=2.99\n"}
        for name, text in NEW_UNITS.items():
            files[f"deploy/systemd/{name}"] = text.encode()
        self.deploy(THIRD, files)
        self.assert_state(THIRD, (THIRD, OLD))
        self.assertTrue((self.t.releases / THIRD / ".venv" / "bin" / "python").is_file())
        # Roll back to A: no pip runs, A's units are synced, and the
        # pointer lands on A — whose venv is A's, not B's. A rollback
        # therefore cannot silently succeed on B's dependencies.
        self.fake.calls.clear()
        host.Transition(self.root, self.fake,
                        system_dir=self.system_dir).rollback(THIRD, OLD)
        self.assertFalse([c for c in self.fake.calls
                          if "-m" in c and "pip" in c])
        for name, text in NEW_UNITS.items():
            self.assertEqual((self.system_dir / name).read_text(), text)
        self.assert_state(OLD, (OLD, THIRD))

    def test_staged_deploy_defers_unit_sync_until_recover(self):
        files = {"requirements.txt": b"requests>=2.31\n"}
        for name, text in NEW_UNITS.items():
            files[f"deploy/systemd/{name}"] = text.encode()
        self.deploy(THIRD, files, restart=False)
        self.assertFalse(self.fake.has("systemctl", "daemon-reload"))
        self.assertEqual(list(self.system_dir.iterdir()), [])
        self.assertIsNotNone(self.t.load())
        self.recover()
        self.assertIsNone(self.t.load())
        self.assertTrue(self.fake.has("systemctl", "daemon-reload"))
        self.assertTrue(self.fake.has("systemctl", "restart"))

    # ---- repo-level unit consistency ----

    def test_repo_units_run_through_the_active_release_venv(self):
        unit_dir = ROOT / "deploy" / "systemd"
        checked = 0
        for path in sorted(unit_dir.glob("*.service")):
            text = path.read_text()
            self.assertNotIn("/opt/sniper/.venv", text, path.name)
            if ".venv" in text:
                self.assertIn("/opt/sniper/current/.venv", text, path.name)
                checked += 1
        self.assertGreaterEqual(checked, 5)
        web = (unit_dir / "sniper-web.service").read_text()
        self.assertIn("/opt/sniper/current/.venv/bin/python3 -m gunicorn", web)


if __name__ == "__main__":
    unittest.main()
