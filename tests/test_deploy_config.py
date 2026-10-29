"""
tests/test_deploy_config.py — static checks on deploy/ files that no other
test executes: the env template as systemd parses it, and the backup /
proxy hardening that a fresh host depends on.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def systemd_env(text: str) -> dict:
    """systemd EnvironmentFile= semantics for unquoted values: whole-line
    '#'/';' comments only; a value runs to end of line minus trailing
    whitespace — an inline '# note' becomes part of the value."""
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "#;":
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    return out


def test_env_template_has_no_inline_comments_systemd_would_keep():
    env = systemd_env((DEPLOY / "env.staging.example").read_text())
    bad = {k: v for k, v in env.items() if "#" in v}
    assert not bad, f"inline comments become part of the value under systemd: {sorted(bad)}"
    assert env["GUNICORN_WORKERS"].isdigit()


def test_env_template_defaults_to_internal_staging_and_holds_no_secrets():
    env = systemd_env((DEPLOY / "env.staging.example").read_text())
    assert env["ALLOW_REGISTRATION"] == "0"
    assert env["SNIPER_SELLER_SUBMISSIONS"] == "0"
    assert env["SNIPER_COMMERCIAL_MODE"] == "1"
    assert "SNIPER_ALLOW_MANUAL_VERIFY_LINKS" not in env
    for k in ("SMTP_HOST", "SMTP_PASSWORD", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
              "B2_KEY_ID", "B2_APPLICATION_KEY", "AGE_RECIPIENT", "EBAY_CLIENT_SECRET",
              "MARKETCHECK_API_KEY", "TWILIO_AUTH_TOKEN"):
        assert env[k] == "", k
    # every variable the mailer and seller flow read is documented
    for k in ("SMTP_PORT", "SMTP_USER", "SMTP_FROM", "SMTP_SSL", "EBAY_ENVIRONMENT",
              "EBAY_EPN_CAMPAIGN_ID", "OPS_HOSTING_MONTHLY_USD"):
        assert k in env, k


def test_fresh_host_gets_every_backup_tool_and_backup_checks_them():
    install = (DEPLOY / "install.sh").read_text()
    assert re.search(r"apt-get -y install[^#]*\bzstd\b", install, re.S)
    assert re.search(r'awscli==\$AWSCLI_VERSION', install)
    assert "ln -sf /opt/sniper-tools/bin/aws /usr/local/bin/aws" in install
    backup = (DEPLOY / "backup.sh").read_text()
    pre = backup.index("for tool in pg_dump zstd age aws")
    assert pre < backup.index("pg_dump --no-owner")


def test_proxy_sends_hsts():
    assert 'Strict-Transport-Security "max-age=' in (DEPLOY / "Caddyfile.staging").read_text()
