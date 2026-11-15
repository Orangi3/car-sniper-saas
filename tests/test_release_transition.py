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


OLD, PREVIOUS, THIRD = ("a" * 40, "b" * 40, "c" * 40)


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.calls = []
        self.failure = None
        self.t = host.Transition(self.root, self.run_command)
        for revision in (OLD, PREVIOUS):
            (self.t.releases / revision).mkdir(parents=True)
        if os.name == "nt":
            # Windows denies symlink creation in this session. Model the pointer
            # with a file while exercising real temporary-file replacement.
            self.stack.enter_context(patch.object(host, "sync_dir"))
            self.stack.enter_context(patch.object(host.os, "fchmod", create=True))
            self.stack.enter_context(patch.object(host.os, "symlink", self.model_link))
            self.stack.enter_context(patch.object(host.Transition, "active", self.model_active))
        host.os.symlink(self.t.target(OLD), self.t.current)
        host.atomic_text(self.root / "current_revision", OLD)
        host.atomic_text(self.root / "previous_revision", PREVIOUS)

    @staticmethod
    def model_link(target, link):
        with link.open("x") as stream:
            stream.write(target.name)

    @staticmethod
    def model_active(transition):
        return transition.target(transition.current.read_text()).name

    def run_command(self, *cmd, **kwargs):
        self.calls.append(cmd)
        if self.failure == cmd:
            raise subprocess.CalledProcessError(1, cmd)
        if cmd[:2] == ("systemctl", "show"):
            out = "LoadState=loaded\nActiveState=failed\n"
        elif cmd[:2] == ("systemctl", "is-enabled"):
            return SimpleNamespace(stdout="disabled\n", returncode=1)
        else:
            out = '{"status":"ok"}' if cmd[0] == "curl" else ""
        return SimpleNamespace(stdout=out, returncode=0)

    def assert_state(self, active, markers):
        self.assertEqual(self.t.active(), active)
        self.assertEqual(self.t.markers(), markers)

    def recover(self):
        host.Transition(self.root, self.run_command).recover()

    def test_initial_journal_failure(self):
        with patch.object(self.t, "record", side_effect=OSError("journal")), self.assertRaises(OSError):
            self.t.rollback(OLD, PREVIOUS)
        self.assert_state(OLD, (OLD, PREVIOUS))
        self.assertIsNone(self.t.load())
        self.assertFalse(self.calls)

    def test_link_creation_failure(self):
        with patch.object(host.os, "symlink", side_effect=OSError("link")), self.assertRaises(OSError):
            self.t.rollback(OLD, PREVIOUS)
        self.assert_state(OLD, (OLD, PREVIOUS))
        self.recover()
        self.recover()
        self.assert_state(OLD, (OLD, PREVIOUS))
        self.assertFalse(self.calls)

    def test_atomic_move_failure(self):
        replace = os.replace
        def fail_pointer(source, target):
            if target == self.t.current:
                raise OSError("move")
            replace(source, target)
        with patch.object(host.os, "replace", fail_pointer), self.assertRaises(OSError):
            self.t.rollback(OLD, PREVIOUS)
        self.assert_state(OLD, (OLD, PREVIOUS))
        self.recover()
        self.assert_state(OLD, (OLD, PREVIOUS))
        self.assertFalse(self.calls)

    def test_interruption_immediately_after_switch(self):
        with patch.object(self.t, "publish", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.t.rollback(OLD, PREVIOUS)
        self.assert_state(PREVIOUS, (OLD, PREVIOUS))
        with patch.object(host.os, "symlink", side_effect=AssertionError("unexpected second flip")):
            self.recover()
            call_count = len(self.calls)
            self.recover()
            self.assertEqual(len(self.calls), call_count)
            with self.assertRaisesRegex(RuntimeError, "pins"):
                self.t.rollback(OLD, PREVIOUS)
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))

    def fail_marker(self, name, expected):
        write = host.atomic_text
        def fail(path, data):
            if path.name == name:
                raise OSError("marker")
            write(path, data)
        with patch.object(host, "atomic_text", fail), self.assertRaises(OSError):
            self.t.rollback(OLD, PREVIOUS)
        self.assert_state(PREVIOUS, expected)
        self.assertFalse(self.calls)
        self.recover()
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))

    def test_current_marker_failure(self):
        self.fail_marker("current_revision", (OLD, PREVIOUS))

    def test_previous_marker_failure(self):
        self.fail_marker("previous_revision", (PREVIOUS, PREVIOUS))

    def test_migration_failure_blocks_application_restart(self):
        self.failure = ("systemctl", "restart", "sniper-migrate.service")
        with self.assertRaises(subprocess.CalledProcessError):
            self.t.rollback(OLD, PREVIOUS)
        self.assertEqual(self.t.load()["service"], "migration")
        self.assertNotIn(("systemctl", "restart", "sniper-web.service"), self.calls)
        self.failure = None
        self.calls.clear()
        self.recover()
        self.assertLess(self.calls.index(("systemctl", "reset-failed", "sniper-migrate.service")),
                        self.calls.index(("systemctl", "restart", "sniper-migrate.service")))
        self.assertLess(self.calls.index(("systemctl", "restart", "sniper-migrate.service")),
                        self.calls.index(("systemctl", "restart", "sniper-web.service")))

    def test_application_restart_failure_leaves_recoverable_state(self):
        self.failure = ("systemctl", "restart", "sniper-web.service")
        with self.assertRaises(subprocess.CalledProcessError):
            self.t.rollback(OLD, PREVIOUS)
        self.assertEqual(self.t.load()["service"], "application")
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))
        self.failure = None
        self.recover()
        self.assertIsNone(self.t.load())

    def test_invalid_or_missing_target(self):
        for revision in ("../outside", THIRD):
            with self.subTest(revision=revision):
                host.atomic_text(self.root / "previous_revision", revision)
                with self.assertRaises(RuntimeError):
                    self.t.rollback(OLD, revision)
                self.assertEqual(self.t.active(), OLD)
                self.assertIsNone(self.t.load())
                self.assertFalse(self.calls)

    def test_stale_journal_is_preserved(self):
        (self.t.releases / THIRD).mkdir()
        j = self.t.prepare(OLD, PREVIOUS, PREVIOUS, "ready")
        self.t.current.unlink()
        host.os.symlink(self.t.target(THIRD), self.t.current)
        with self.assertRaisesRegex(RuntimeError, "stale"):
            self.recover()
        self.assert_state(THIRD, (OLD, PREVIOUS))
        self.assertEqual(self.t.load(), j)

    def test_uncertain_preparation_blocks_automatic_recovery(self):
        (self.t.releases / THIRD).mkdir()
        self.t.prepare(OLD, PREVIOUS, THIRD, "preparing")
        with self.assertRaisesRegex(RuntimeError, "preparation"):
            self.recover()
        self.assert_state(OLD, (OLD, PREVIOUS))
        self.assertFalse(self.calls)

    @staticmethod
    def archive(revision, filename="requirements.txt"):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT,
                          pax_headers={"comment": revision}) as archive:
            item = tarfile.TarInfo(filename)
            item.size = 1
            archive.addfile(item, io.BytesIO(b"x"))
        return output.getvalue()

    def deploy(self, data, restart=True, digest=None):
        with patch.object(host.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data))):
            self.t.deploy(THIRD, digest or hashlib.sha256(data).hexdigest(), restart)

    def test_staged_deploy_uses_app_account_and_prepares_before_flip(self):
        # Release-safety execution: prepare builds a per-release venv
        # inside the new release directory (never the shared venv) and
        # runs migrations/canary from it as the app account, all before
        # the flip.
        original = self.run_command
        def command(*cmd, **kwargs):
            self.assert_state(OLD, (OLD, PREVIOUS))
            self.assertEqual(self.t.load()["phase"], "preparing")
            return original(*cmd, **kwargs)
        with patch.object(self.t, "run", command):
            self.deploy(self.archive(THIRD), restart=False)
        self.assert_state(THIRD, (THIRD, OLD))
        dest = self.t.releases / THIRD
        venv_python = str(dest / ".venv" / "bin" / "python")
        self.assertEqual(self.calls[0],
                         ("python3.12", "-m", "venv", str(dest / ".venv")))
        self.assertEqual(self.calls[1][:3], (venv_python, "-m", "pip"))
        self.assertTrue(self.calls[1][-1].endswith("requirements.txt"))
        self.assertIn("--uid=sniper", self.calls[2])
        self.assertIn("EnvironmentFile=/etc/sniper/env", self.calls[2])
        self.assertEqual(self.calls[2][-2:], ("-m", "migrations.runner"))
        self.assertIn("--unit=sniper-canary", self.calls[4])
        self.assertIn("-m", self.calls[4])
        self.assertIn("gunicorn", self.calls[4])
        self.assertFalse(any("/opt/sniper/.venv" in " ".join(map(str, c))
                             for c in self.calls))
        self.assertTrue((self.t.releases / PREVIOUS).is_dir())
        self.recover()
        self.assertIsNone(self.t.load())

    def test_bad_archive_never_publishes(self):
        for data, digest, message in (
                (self.archive(THIRD), "0" * 64, "digest"),
                (self.archive(OLD), None, "commit"),
                (self.archive(THIRD, "../outside"), None, "Unsafe")):
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                self.deploy(data, digest=digest)
            self.assert_state(OLD, (OLD, PREVIOUS))
            self.assertFalse((self.t.releases / THIRD).exists())
            self.assertIsNone(self.t.load())
            self.assertFalse(self.calls)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() == 0,
                         "Requires a disposable root-owned Linux fixture")
    def test_parallel_deploy_and_rollback_share_host_lock(self):
        # Fixture adaptation (Sprint 04): main() requires release
        # directories that are not group/other writable; under a 007
        # umask the temporary fixture is 0770, so enforce the invariant
        # a real host has. No product-code change.
        os.chmod(self.root, 0o755)
        os.chmod(self.t.releases, 0o755)
        with patch.object(host, "ROOT", self.root), host.host_lock(self.root / ".release.lock"):
            for args in (["rollback", OLD, PREVIOUS], ["deploy", THIRD, "0" * 64, "restart"]):
                with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(host.main(args), 1)
                    self.assertIn("lock", error.getvalue().lower())
        self.assert_state(OLD, (OLD, PREVIOUS))
        self.assertIsNone(self.t.load())

    def unhealthy(self, *cmd, **kwargs):
        """run_command whose health probe fails (curl exit 7) until healed."""
        if cmd and cmd[0] == "curl" and not self.healed:
            self.calls.append(cmd)
            return SimpleNamespace(stdout="", returncode=7)
        return self.run_command(*cmd, **kwargs)

    def test_rollback_takes_over_a_switch_whose_health_check_failed(self):
        # A switch that fails after the pointer moved leaves its journal,
        # and --recover only retries forward. Before this fix the owner
        # had no tool path back: rollback answered "Pending transition".
        self.healed = False
        broken = host.Transition(self.root, self.unhealthy)
        with patch.object(host.time, "sleep"), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(RuntimeError, "Health check failed"):
            broken.rollback(OLD, PREVIOUS)               # switch to PREVIOUS: unhealthy
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))
        self.assertEqual(self.t.load()["service"], "health")
        with patch.object(host.time, "sleep"), \
                self.assertRaisesRegex(RuntimeError, "Health check failed"):
            host.Transition(self.root, self.unhealthy).recover()   # forward only
        self.healed = True                                # the old release is healthy
        with contextlib.redirect_stderr(io.StringIO()) as note:
            host.Transition(self.root, self.unhealthy).rollback(PREVIOUS, OLD)
        self.assertIn("Taking over the failed transition", note.getvalue())
        self.assert_state(OLD, (OLD, PREVIOUS))
        self.assertIsNone(self.t.load())

    def test_rollback_still_refuses_any_other_pending_journal(self):
        # Pins that do not describe the recorded switch, and a journal
        # whose switch never happened, still need --recover or a person.
        (self.t.releases / THIRD).mkdir()
        self.t.prepare(OLD, PREVIOUS, PREVIOUS, "ready")    # pointer never moved
        for pins in ((PREVIOUS, OLD), (OLD, PREVIOUS), (OLD, THIRD)):
            with self.subTest(pins=pins), self.assertRaisesRegex(RuntimeError, "Pending transition"):
                self.t.rollback(*pins)
        self.assert_state(OLD, (OLD, PREVIOUS))
        self.assertEqual(self.t.load()["target"], PREVIOUS)
        self.assertFalse(self.calls)

    def test_unsafe_directory_refusal_says_how_to_fix_an_older_host(self):
        message = host.unsafe_directory_message(host.ROOT)
        self.assertIn("chown root:root", message)
        self.assertIn("UPGRADE_EXISTING_HOST.md", message)


if __name__ == "__main__":
    unittest.main()
