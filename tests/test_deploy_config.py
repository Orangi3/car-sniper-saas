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
              "EBAY_EPN_CAMPAIGN_ID", "EBAY_COMMERCIAL_AGREEMENT", "OPS_HOSTING_MONTHLY_USD"):
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


def test_proxy_sets_the_visitor_address_and_the_app_trusts_only_this_host(tmp_path):
    """Every reverse_proxy block SETS X-Forwarded-For to the connecting
    address (a visitor's own header is discarded), and the app trusts it
    only from loopback (auth.client_ip); install.sh adds the setting to an
    env file that predates it."""
    caddy = (DEPLOY / "Caddyfile.staging").read_text()
    blocks = re.findall(r"reverse_proxy 127\.0\.0\.1:8765 \{(.*?)\n\s*\}", caddy, re.S)
    assert len(blocks) == caddy.count("reverse_proxy") == 3
    assert all("header_up X-Forwarded-For {remote_host}" in b for b in blocks)
    env = systemd_env((DEPLOY / "env.staging.example").read_text())
    assert env["SNIPER_TRUSTED_PROXIES"] == "127.0.0.1,::1"
    install = (DEPLOY / "install.sh").read_text()
    snippet = re.search(r"(    if ! grep -q '\^SNIPER_TRUSTED_PROXIES=' /etc/sniper/env; then.*?\n    fi)\n",
                        install, re.S).group(1)
    old_env = tmp_path / "env"
    old_env.write_text("ALLOW_REGISTRATION=0\nBILLING_ORIGIN=https://app.example.test\n")
    import subprocess
    for _ in range(2):                                  # second run: already there, no duplicate
        subprocess.run(["bash", "-c", snippet.replace("/etc/sniper/env", str(old_env))], check=True,
                       capture_output=True)
    added = systemd_env(old_env.read_text())
    assert added["SNIPER_TRUSTED_PROXIES"] == "127.0.0.1,::1" and added["ALLOW_REGISTRATION"] == "0"
    assert old_env.read_text().count("SNIPER_TRUSTED_PROXIES=") == 1


def test_deploy_runs_app_steps_as_the_app_user_with_the_production_env():
    """Deploys go in as root (the sniper account has no shell); the canary
    and pre-flip migration must see /etc/sniper/env, or they silently test
    a throwaway SQLite file instead of PostgreSQL."""
    sync = (DEPLOY / "sync.sh").read_text()
    assert "sniper@<host>" not in sync and "root@<host>" in sync
    assert "--uid=sniper" in sync and "EnvironmentFile=/etc/sniper/env" in sync
    assert "runuser -u sniper -- /opt/sniper/.venv/bin/pip install" in sync
    canary = sync.index("--unit=sniper-canary")
    flip = sync.index("Canary OK — flipping current symlink")
    assert sync.index("migrations.runner") < canary < flip
    install = (DEPLOY / "install.sh").read_text()
    assert "nologin" in install          # the reason root is the deploy user


def test_app_logs_are_rotated_and_kept_14_days():
    """The request log records the signed-in user's email and client
    addresses: it must not be kept forever (privacy policy) or fill the
    disk. install.sh installs the rotation policy and the tool."""
    conf = (DEPLOY / "logrotate" / "sniper").read_text()
    assert "/var/log/sniper/*.log" in conf and re.search(r"^\s*rotate 14$", conf, re.M)
    assert "copytruncate" in conf and "su sniper sniper" in conf
    install = (DEPLOY / "install.sh").read_text()
    assert 'install -m 0644 "$HERE/logrotate/sniper" /etc/logrotate.d/sniper' in install
    assert re.search(r"apt-get -y install[^#]*\blogrotate\b", install, re.S)


def test_web_writes_logs_outside_the_read_only_release_dir():
    unit = (DEPLOY / "systemd" / "sniper-web.service").read_text()
    assert "Environment=SNIPER_LOG_DIR=/var/log/sniper" in unit
    assert "/var/log/sniper" in unit.split("ReadWritePaths=")[1].splitlines()[0]


def test_app_modules_load_under_the_shipped_env_template(tmp_path):
    """Every empty KEY= in the template must mean "not set". COST_SMS_CENTS=
    made `import plans` raise, which 500'd saved-search creation and failed
    every alert delivery job on a host configured from the template."""
    import os
    import subprocess
    import sys
    env = {k: v for k, v in systemd_env((DEPLOY / "env.staging.example").read_text()).items()
           if k != "DATABASE_URL"}
    env.update(PATH=os.environ.get("PATH", ""), SNIPER_DB_PATH=str(tmp_path / "t.db"))
    code = ("import plans, notifications, seller_listings, mailer, config, preflight, ops\n"
            "assert plans.UNIT_COST_CENTS['sms'] == 1.0\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]


def test_backup_and_restore_sign_for_the_endpoints_b2_region(tmp_path):
    import subprocess
    for name in ("backup.sh", "restore.sh"):
        text = (DEPLOY / name).read_text()
        derive = "\n".join(l for l in text.splitlines() if l.startswith("B2_REGION="))
        assert "AWS_DEFAULT_REGION=us-east" not in text, name   # no hard-coded region
        assert text.count('AWS_DEFAULT_REGION="$B2_REGION"') == text.count("aws --endpoint-url"), name
        for endpoint, region in (("https://s3.us-west-004.backblazeb2.com", "us-west-004"),
                                 ("https://s3.eu-central-003.backblazeb2.com/", "eu-central-003"),
                                 ("https://s3.us-east-005.backblazeb2.com", "us-east-005"),
                                 ("http://127.0.0.1:5000", "us-east-005")):
            out = subprocess.run(["bash", "-c", f'B2_ENDPOINT="{endpoint}"\n{derive}\necho "$B2_REGION"'],
                                 capture_output=True, text=True, check=True).stdout.strip()
            assert out == region, (name, endpoint, out)


def test_golive_checks_settings_by_name_and_never_prints_their_values():
    import subprocess
    text = (DEPLOY / "golive.sh").read_text()
    subprocess.run(["bash", "-n", str(DEPLOY / "golive.sh")], check=True)
    # values are read only into comparisons and the public origin, never echoed
    assert not re.search(r'(echo|printf|pass|fail)[^\n]*\$\(val (?!BILLING_ORIGIN)', text)
    for k in ("SMTP_PASSWORD", "B2_APPLICATION_KEY", "AGE_RECIPIENT", "HEALTHCHECK_BACKUP_URL"):
        assert k in text, k
    assert 'grep -q "invitation only"' in text           # sign-up probe checks why it was refused
    assert "disable -q --now sniper-reconcile.timer" in text
    assert "STRIPE_SECRET_KEY" in text                   # the free pilot refuses billing keys
