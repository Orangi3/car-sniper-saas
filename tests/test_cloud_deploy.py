"""
tests/test_cloud_deploy.py — the one-container cloud deploy (Dockerfile,
deploy/paas/start.sh, render.yaml) and the owner's create-admin command.
Runs on SQLite and (SNIPER_TEST_PG_URL) Postgres.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------- create-admin ---------------------------------------------

def test_create_admin_creates_promotes_and_refuses_weak_passwords(app):
    import accounts
    import auth
    with pytest.raises(accounts.Refused):
        accounts.create_admin("owner@example.com", "short")
    assert auth.get_user_by_email("owner@example.com") is None

    r = accounts.create_admin("Owner@Example.com", "a-long-admin-pass")
    assert r == {"email": "owner@example.com", "created": True}
    u = auth.get_user_by_email("owner@example.com")
    assert u.is_admin and u.plan == "pro"

    auth.create_user("pat@example.com", "patpass123")
    r = accounts.create_admin("pat@example.com", None)      # promote: no password needed
    assert r["created"] is False and auth.get_user_by_email("pat@example.com").is_admin


def test_create_admin_cli_reads_the_password_from_stdin_not_argv(app, monkeypatch, capsys):
    import io
    import accounts
    import auth
    monkeypatch.setattr("sys.stdin", io.StringIO("piped-admin-pass-1\n"))
    assert accounts.main(["create-admin", "boss@example.com"]) == 0
    assert "created admin boss@example.com" in capsys.readouterr().out
    assert auth.verify_password("piped-admin-pass-1",
                                _hash_for("boss@example.com"))


def _hash_for(email):
    from tests.test_matching_and_history import q
    return q("SELECT password_hash FROM users WHERE email = ?", (email,))[0]["password_hash"]


# ---------- behind a TLS-terminating host -----------------------------

def test_browser_writes_work_behind_https_proxy_once_the_origin_is_known(client, monkeypatch):
    """Render/Railway end TLS at their proxy, so Flask sees http://host while
    the browser sends Origin: https://host. Without BILLING_ORIGIN every
    login and save would be refused as cross-site — start.sh sets it from
    RENDER_EXTERNAL_URL."""
    origin = "https://flipfirst.onrender.com"
    body = {"email": "a@example.com", "password": "longenough123"}
    hdrs = {"Origin": origin}
    monkeypatch.delenv("BILLING_ORIGIN", raising=False)
    r = client.post("/api/auth/register", json=body, headers=hdrs,
                    base_url="http://flipfirst.onrender.com")
    assert r.status_code == 403                      # the bug start.sh prevents
    monkeypatch.setenv("BILLING_ORIGIN", origin)
    r = client.post("/api/auth/register", json=body, headers=hdrs,
                    base_url="http://flipfirst.onrender.com")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert "Secure" in r.headers.get("Set-Cookie", "")   # cookie stays https-only


def test_invite_links_use_the_render_address_when_no_origin_is_set(app, monkeypatch, capsys):
    import invites
    monkeypatch.delenv("BILLING_ORIGIN", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://flipfirst.onrender.com")
    assert invites._main(["issue", "--count", "1"]) == 0
    assert "https://flipfirst.onrender.com/login#invite=" in capsys.readouterr().out


# ---------- start script and image contents ---------------------------

def test_start_script_refuses_to_run_without_postgres(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    r = subprocess.run(["bash", str(ROOT / "deploy/paas/start.sh")], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "DATABASE_URL" in r.stderr


def test_start_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(ROOT / "deploy/paas/start.sh")], check=True)


def test_image_never_contains_secrets_or_local_databases():
    rules = set((ROOT / ".dockerignore").read_text().split())
    for must_exclude in (".env", "overrides.json", "*.db", "*.sqlite", ".git/", "*.log"):
        assert must_exclude in rules, must_exclude


def test_image_keeps_every_file_the_app_reads_at_runtime():
    text = (ROOT / ".dockerignore").read_text()
    for needed in ("static", "migrations", "jobs", "sources", "data", "legal", "deploy"):
        assert f"\n{needed}/\n" not in text and f"\n{needed}\n" not in text, needed
    assert "!legal/*.md" in text            # terms and privacy pages ship


def test_render_blueprint_keeps_the_pilot_safe():
    y = (ROOT / "render.yaml").read_text()
    assert 'key: SNIPER_COMMERCIAL_MODE\n        value: "1"' in y
    assert 'key: ALLOW_REGISTRATION\n        value: "0"' in y
    assert "autoDeploy: false" in y                     # deploys stay owner-only
    assert "ipAllowList: []" in y                       # database not public
    assert "plan: free" not in y                        # free web plan sleeps: alerts stop
    assert "sk_live" not in y and "sk_test" not in y
