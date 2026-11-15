"""
tests/test_sync_recovery.py — Sprint 03 restart semantics, ported to the
Sprint 04 combined candidate.

Sprint 03 tested the old deploy/sync.sh RESTART_SERVICES block under stub
binaries. The recovery patch replaced that script with a thin wrapper
around deploy/release_transition.py, so the restart step now lives in
Transition.finish() / restart_service(). These tests port Sprint 03's
eight behavioural tests to that real code path, driving rollback() and
recover() through a fake command runner (no production host, no real
systemctl): every test verifies behaviour, not text.

Preserved Sprint 03 contract:
- a restart failure names the service, the operation, the exit status
  and the kind (migration vs application);
- the failure report prints the locked release state and a redacted
  40-line journal excerpt;
- a start-limit-hit service gets exactly one reset-failed and one
  retry; a persistent crash loop is reported, not reset endlessly;
- migration restarts before application services;
- a restart failure raises RestartFailure (exit code 12 at the CLI);
- the restart step never writes revision files and never moves the
  symlink, so recovery cannot switch releases a second time.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
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


class FakeRun:
    """Simulates systemctl / journalctl / curl for one Transition."""

    def __init__(self):
        self.calls = []
        self.fail = set()
        self.limit = set()
        self.failed_state = set()
        self.reset_done = set()
        self.enabled = {SCHEDULER, WORKER}
        self.journal_lines = "unit failed to start: api_key=SECRETVALUE123\n"

    def __call__(self, *cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[:2] == ("systemctl", "restart"):
            service = cmd[2]
            if service in self.limit and service not in self.reset_done:
                raise subprocess.CalledProcessError(3, cmd)
            if service in self.fail:
                raise subprocess.CalledProcessError(1, cmd)
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[:2] == ("systemctl", "show"):
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
        if cmd[0] == "journalctl":
            return SimpleNamespace(stdout=self.journal_lines, returncode=0)
        if cmd[0] == "curl":
            return SimpleNamespace(stdout='{"status":"ok"}', returncode=0)
        raise AssertionError(f"unexpected command {cmd}")

    def restarts(self, service):
        return [c for c in self.calls if c[:2] == ("systemctl", "restart")
                and c[2] == service]

    def resets(self, service):
        return [c for c in self.calls if c[:2] == ("systemctl", "reset-failed")
                and c[2] == service]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stdout = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stderr = self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.fake = FakeRun()
        self.t = host.Transition(self.root, self.fake)
        for revision in (OLD, PREVIOUS):
            (self.t.releases / revision).mkdir(parents=True)
        host.os.symlink(self.t.target(OLD), self.t.current)
        host.atomic_text(self.root / "current_revision", OLD)
        host.atomic_text(self.root / "previous_revision", PREVIOUS)

    def rollback(self):
        return host.Transition(self.root, self.fake).rollback(OLD, PREVIOUS)

    def recover(self):
        return host.Transition(self.root, self.fake).recover()

    def assert_state(self, active, markers):
        self.assertEqual(self.t.active(), active)
        self.assertEqual(self.t.markers(), markers)

    def test_migration_restart_failure_names_migrate_and_exits_12(self):
        self.fake.fail = {MIGRATE}
        with self.assertRaises(host.RestartFailure) as caught:
            self.rollback()
        self.assertEqual(caught.exception.returncode, 12)
        self.assertEqual(caught.exception.exit_status, 1)
        self.assertEqual(caught.exception.service, MIGRATE)
        self.assertEqual(caught.exception.kind, "migration")
        self.assertEqual(caught.exception.operation, "restart")
        self.assertEqual(self.t.load()["service"], "migration")
        err = self.stderr.getvalue()
        self.assertIn("service=sniper-migrate.service", err)
        self.assertIn("kind=migration", err)
        self.assertIn("operation=restart", err)
        self.assertIn("locked release state:", err)
        self.assertIn("current_revision=" + PREVIOUS, err)
        self.assertNotIn("SECRETVALUE123", err)
        self.assertIn("REDACTED", err)

    def test_web_restart_failure_names_web_as_application(self):
        self.fake.fail = {WEB}
        with self.assertRaises(host.RestartFailure) as caught:
            self.rollback()
        self.assertEqual(caught.exception.service, WEB)
        self.assertEqual(caught.exception.kind, "application")
        self.assertEqual(self.t.load()["service"], "application")
        restarts = [c[2] for c in self.fake.calls if c[:2] == ("systemctl", "restart")]
        self.assertLess(restarts.index(MIGRATE), restarts.index(WEB))

    def test_scheduler_restart_failure_and_disabled_worker_not_attempted(self):
        self.fake.fail = {SCHEDULER}
        self.fake.enabled = {SCHEDULER}
        with self.assertRaises(host.RestartFailure) as caught:
            self.rollback()
        self.assertEqual(caught.exception.service, SCHEDULER)
        self.assertEqual(self.fake.restarts(WORKER), [])

    def test_start_limit_retry_succeeds_with_exactly_one_reset(self):
        self.fake.limit = {MIGRATE}
        self.rollback()
        self.assertEqual(len(self.fake.resets(MIGRATE)), 1)
        self.assertEqual(len(self.fake.restarts(MIGRATE)), 2)
        self.assertIn("hit its start limit", self.stdout.getvalue())
        self.assertIsNone(self.t.load())

    def test_persistent_crash_loop_is_reported_not_reset_endlessly(self):
        self.fake.fail = {WEB}
        self.fake.limit = {WEB}
        with self.assertRaises(host.RestartFailure) as caught:
            self.rollback()
        self.assertEqual(caught.exception.service, WEB)
        self.assertEqual(caught.exception.operation, "reset-failed+restart")
        self.assertEqual(len(self.fake.resets(WEB)), 1)
        self.assertEqual(len(self.fake.restarts(WEB)), 2)

    def test_recovery_after_interrupted_transition_then_clean_run(self):
        self.fake.fail = {WEB}
        with self.assertRaises(host.RestartFailure):
            self.rollback()
        self.assertEqual(self.t.load()["service"], "application")
        pointer_before = os.readlink(self.t.current)
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))
        self.fake.fail = set()
        self.fake.calls.clear()
        self.recover()
        self.assertIsNone(self.t.load())
        # Recovery resumed the recorded transition; it did not move the
        # pointer a second time.
        self.assertEqual(os.readlink(self.t.current), pointer_before)
        self.assert_state(PREVIOUS, (PREVIOUS, OLD))

    def test_restart_order_is_migration_before_application(self):
        self.rollback()
        restarts = [c[2] for c in self.fake.calls if c[:2] == ("systemctl", "restart")]
        order = [restarts.index(s) for s in (MIGRATE, WEB, SCHEDULER, WORKER)]
        self.assertEqual(order, sorted(order))

    def test_three_consecutive_rollbacks_with_explicit_revisions(self):
        for expected, target in ((OLD, PREVIOUS), (PREVIOUS, OLD), (OLD, PREVIOUS)):
            host.Transition(self.root, self.fake).rollback(expected, target)
            self.assert_state(target, (target, expected))
            self.assertIsNone(self.t.load())
        self.recover()
        self.assertIsNone(self.t.load())


class MainExitCodeTests(unittest.TestCase):
    """Exit code 12 through the real CLI entry point, guarded to the
    same disposable-root fixture the recovery tests use for locking."""

    @unittest.skipUnless(os.name == "posix" and os.geteuid() == 0,
                         "Requires a disposable root-owned Linux fixture")
    def test_restart_failure_exits_12_through_main(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        stdout = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stderr = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        fake = FakeRun()
        fake.fail = {WEB}

        def fake_run(cmd, text=True, capture_output=False, check=True, **kwargs):
            result = fake(*cmd, capture=capture_output, check=check)
            if check and result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, cmd)
            return result

        t = host.Transition(root, fake)
        for revision in (OLD, PREVIOUS):
            (t.releases / revision).mkdir(parents=True)
        # main() requires release directories that are not group/other
        # writable; TemporaryDirectory under a 007 umask is 0770, so
        # enforce the same invariant a real host has.
        os.chmod(root, 0o755)
        os.chmod(t.releases, 0o755)
        host.os.symlink(t.target(OLD), t.current)
        host.atomic_text(root / "current_revision", OLD)
        host.atomic_text(root / "previous_revision", PREVIOUS)
        with patch.object(host, "ROOT", root), patch.object(host.subprocess, "run", fake_run):
            self.assertEqual(host.main(["rollback", OLD, PREVIOUS]), 12)
            self.assertIn("service=sniper-web.service", stderr.getvalue())
            self.assertEqual(t.load()["service"], "application")
            fake.fail = set()
            self.assertEqual(host.main(["recover"]), 0)
            self.assertIsNone(t.load())


if __name__ == "__main__":
    unittest.main()
