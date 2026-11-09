"""
tests/test_deploy_modes.py — deploy scripts that systemd or an operator
executes directly must keep their executable bit.

b384f50 changed deploy/healthcheck.sh, install.sh and sync.sh from 100755
to 100644. systemd then refuses sniper-healthcheck.service's ExecStart
("Permission denied"), so the dead-man probe fails every 5 minutes and
golive.sh gate 7 fails; ./deploy/sync.sh exits 126.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")

RUN_DIRECTLY = ("deploy/healthcheck.sh", "deploy/install.sh", "deploy/sync.sh",
                "deploy/backup.sh", "deploy/restore.sh", "deploy/bootstrap_archive.sh")


def test_scripts_named_in_systemd_exec_lines_are_executable():
    found = []
    for unit in sorted((ROOT / "deploy" / "systemd").glob("*.service")):
        for line in unit.read_text().splitlines():
            m = re.match(r"Exec(?:Start|StartPre|StartPost|Stop)=-?/opt/sniper/current/(\S+)", line)
            if m and not m.group(1).startswith(".venv/"):
                found.append(m.group(1))
                assert os.access(ROOT / m.group(1), os.X_OK), \
                    f"{unit.name} runs {m.group(1)}, which is not executable"
    assert "deploy/healthcheck.sh" in found


def test_deploy_scripts_run_directly_are_executable():
    for rel in RUN_DIRECTLY:
        assert os.access(ROOT / rel, os.X_OK), f"{rel} is not executable"
