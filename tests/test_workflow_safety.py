"""
tests/test_workflow_safety.py — invariants of the GitHub workflows that keep
automation safe in a PUBLIC repository. Text checks (no YAML dependency):
they fail loudly if someone loosens a gate.
"""
from pathlib import Path

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _read(name):
    return (WF / name).read_text()


def test_no_workflow_exposes_secrets_to_untrusted_pull_requests():
    for p in WF.glob("*.yml"):
        assert "pull_request_target" not in p.read_text(), p.name


def test_ci_token_is_read_only():
    assert "permissions:\n  contents: read" in _read("ci.yml")


def test_agent_runs_only_for_owner_written_owner_labeled_issues():
    s = _read("agent-task.yml")
    on_block = s.split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
    assert "issues:" in on_block and "pull_request" not in on_block
    assert "issue_comment" not in on_block          # comments can't start runs
    for gate in ("github.event.sender.login == github.repository_owner",
                 "github.event.issue.user.login == github.repository_owner",
                 "vars.AGENT_PAUSED != 'true'", "AGENT_MAX_RUNS_PER_DAY",
                 "agent-running", "group: agent-task"):
        assert gate in s, gate


def test_agent_tools_are_allow_listed_and_untrusted_text_is_data():
    s = _read("agent-task.yml")
    assert "--allowedTools" in s and '--disallowedTools "WebFetch,WebSearch"' in s
    assert "Bash(git push origin agent/*)" in s
    assert "untrusted DATA" in s
    assert "Bash(*)" not in s and '"Bash"' not in s


def test_manual_only_workflows_pass_inputs_through_env():
    s = _read("live-source-check.yml")
    on_block = s.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch" in on_block and "push" not in on_block
    run = s.split("- name: Probe", 1)[1]
    assert "${{ inputs." not in run.split("run: |", 1)[1]   # no expression injection
