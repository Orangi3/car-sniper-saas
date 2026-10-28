"""
tests/test_agent_gate.py — EXECUTES the agent-task gate script (extracted
from .github/workflows/agent-task.yml) under bash with a stub `gh` CLI, so
the authorization decisions are tested as code, not as text.

The stub answers the same API calls GitHub would and applies the workflow's
real --jq filters with jq. The job-level `if:` (sender/author must be the
owner, kill switch) is a GitHub expression and is pinned separately in
tests/test_workflow_safety.py.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "agent-task.yml"
pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("jq")),
                                reason="needs bash and jq")

STUB = r'''#!/usr/bin/env bash
# minimal gh stand-in: logs every call, answers from $STUB_DIR/*.json
echo "$*" >> "$STUB_DIR/calls.log"
jqf=""; args=("$@")
for ((i=0; i<${#args[@]}; i++)); do [ "${args[$i]}" = "--jq" ] && jqf="${args[$((i+1))]}"; done
case "$1 $2" in
  "api repos/o/r/branches/main") jq -r "$jqf" "$STUB_DIR/branch.json" ;;
  "api --paginate") jq -r "$jqf" "$STUB_DIR/comments.json" ;;
  "api --method") jq -r "$jqf" "$STUB_DIR/runs.json" ;;
  *) : ;;
esac
'''


def _gate_script() -> str:
    text = WF.read_text()
    block = text.split("      - id: check\n", 1)[1].split("\n  run:\n", 1)[0]
    body = block.split("        run: |\n", 1)[1]
    return textwrap.dedent(body)


def _run(tmp_path, *, has_key=True, protected=True, comments=(), labels=("agent-task", "agent-go"),
         runs_today=1, max_runs="3"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(STUB)
    (bindir / "gh").chmod(0o755)
    (stub_dir / "branch.json").write_text(json.dumps({"name": "main", "protected": protected}))
    (stub_dir / "comments.json").write_text(json.dumps(
        [{"user": {"login": u, "type": t}, "body": "x"} for u, t in comments]))
    (stub_dir / "runs.json").write_text(json.dumps(
        {"workflow_runs": [{"conclusion": None}] * runs_today + [{"conclusion": "skipped"}] * 5}))
    out = tmp_path / "out"
    out.write_text("")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "STUB_DIR": str(stub_dir),
           "GITHUB_OUTPUT": str(out), "GITHUB_RUN_ID": "42", "REPO": "o/r", "ISSUE": "7",
           "OWNER": "owner", "DEFAULT_BRANCH": "main", "HAS_KEY": "true" if has_key else "false",
           "MAX_RUNS": max_runs, "LABELS": json.dumps(list(labels)), "GH_TOKEN": "x"}
    r = subprocess.run(["bash", "-c", _gate_script()], env=env, capture_output=True, text=True,
                       timeout=30)
    calls = (stub_dir / "calls.log").read_text() if (stub_dir / "calls.log").exists() else ""
    return r.returncode, out.read_text().strip(), calls


def test_happy_path_starts_labels_and_locks(tmp_path):
    code, out, calls = _run(tmp_path, comments=[("owner", "User"), ("github-actions[bot]", "Bot")])
    assert code == 0 and out == "go=true"
    assert "issue lock 7" in calls and "--add-label agent-running" in calls


def test_refuses_without_api_key(tmp_path):
    _, out, calls = _run(tmp_path, has_key=False)
    assert out == "go=false" and "ANTHROPIC_API_KEY" in calls and "issue lock" not in calls


def test_refuses_while_default_branch_unprotected(tmp_path):
    _, out, calls = _run(tmp_path, protected=False)
    assert out == "go=false" and "not protected" in calls and "issue lock" not in calls


def test_refuses_when_a_stranger_commented(tmp_path):
    _, out, calls = _run(tmp_path, comments=[("owner", "User"), ("mallory", "User")])
    assert out == "go=false" and "prompt injection" in calls and "issue lock" not in calls


@pytest.mark.parametrize("label", ["agent-running", "agent-pr-opened"])
def test_duplicate_runs_are_refused(tmp_path, label):
    _, out, _ = _run(tmp_path, labels=("agent-task", "agent-go", label))
    assert out == "go=false"


def test_daily_cap_counts_this_run_and_ignores_skipped(tmp_path):
    assert _run(tmp_path, runs_today=3, max_runs="3")[1] == "go=true"
    assert _run(tmp_path / "b", runs_today=4, max_runs="3")[1] == "go=false"


def test_unexpected_protection_value_fails_closed(tmp_path):
    # unknown branch -> the stub's jq fails -> set -e aborts before go=true
    stub_err = tmp_path / "x"
    stub_err.mkdir()
    code, out, _ = _run(stub_err, protected="not-json-bool")
    assert out != "go=true"
