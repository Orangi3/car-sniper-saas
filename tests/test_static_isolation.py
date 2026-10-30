"""
tests/test_static_isolation.py — only static/ is web-served.

Nothing next to the code can be downloaded: source, SQLite databases, .env
files, deploy scripts and config, keys, backups, logs, docs, .git — not by
direct path, not through traversal or encoded paths out of /static. Decoy
"secret" files are planted in the app directory for the duration of a test
to prove it. Also: static/ itself holds only web asset types, and the
content security policy keeps its protections. SQLite and Postgres.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DECOYS = {   # planted next to the code, removed afterwards
    ".env": "SNIPER_DECOY_ENV=1",
    "zz-decoy.db": "SQLite format 3 decoy",
    "zz-decoy-backup.sql.gz": "decoy backup",
    "zz-decoy_ed25519": "-----BEGIN OPENSSH PRIVATE KEY----- decoy",
    "zz-decoy.pem": "-----BEGIN PRIVATE KEY----- decoy",
    "server.py.bak": "decoy backup of the source",
    "deploy/zz-decoy.env": "SNIPER_DECOY_DEPLOY=1",
}
MARK = "decoy"


@pytest.fixture()
def planted():
    import server
    made = []
    try:
        for rel, body in DECOYS.items():
            p = server.ROOT / rel
            if p.exists():
                pytest.skip(f"{rel} already exists; not overwriting it")
            p.write_text(body)
            made.append(p)
        yield server.ROOT
    finally:
        for p in made:
            p.unlink(missing_ok=True)


def _paths(root: Path) -> list[str]:
    d = root.name
    targets = ["server.py", "auth.py", "listings.db", ".env", ".env.example", "requirements.txt",
               "README.md", "FIRST_DEPLOY.md", "OWNER_ACTIONS.md", "sniper-requests.log",
               ".git/config", ".git/HEAD", "deploy/install.sh", "deploy/env.staging.example",
               "deploy/Caddyfile.staging", "deploy/backup.sh", "deploy/systemd/sniper-web.service",
               "migrations/0001_users_and_isolation.sql", "tests/conftest.py", *DECOYS]
    out = []
    for t in targets:
        out += [f"/{t}", f"/{d}/{t}", f"/static/../{t}", f"/static/%2e%2e/{t}",
                f"/static/..%2f{t}", f"/static/%2e%2e%2f{t}", f"/static/%252e%252e/{t}",
                f"/static/..%5c{t}", f"/static/..\\{t}", f"/static/.%2e/{t}", f"/static/./../{t}",
                f"/static/brand/../../{t}", f"/static/..;/{t}", f"/static/{root}/{t}",
                f"/static//{root}/{t}", f"/static/%00../{t}"]
    out += ["/static/../../etc/passwd", "/static/%2e%2e/%2e%2e/etc/passwd", "/static//etc/passwd",
            "/static/....//server.py", "/static/ff.css/../../server.py"]
    return out


def test_nothing_outside_static_can_be_downloaded(client, planted):
    leaks = []
    for path in _paths(planted):
        r = client.get(path, follow_redirects=True)   # "//" is merged by a 308 to /static/…
        body = r.get_data()
        if r.status_code == 200 or MARK.encode() in body or b"def api_" in body \
                or body.startswith(b"SQLite format") or b"PRIVATE KEY" in body \
                or b"root:x:0:0" in body or b"DATABASE_URL" in body:
            leaks.append(f"{path} -> {r.status_code}")
    assert leaks == []


def test_the_static_assets_themselves_are_served(client):
    for path, kind in (("/static/ff.css", "text/css"), ("/static/app.js", "javascript"),
                       ("/static/brand/favicon.svg", "image/svg+xml"), ("/favicon.ico", "image/x-icon")):
        r = client.get(path)
        assert r.status_code == 200 and kind in r.headers["Content-Type"], path
        assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_static_holds_only_web_asset_types():
    import server
    allowed = {".css", ".js", ".svg", ".png", ".ico", ".webmanifest"}
    files = [p for p in server.STATIC.rglob("*") if p.is_file()]
    assert files and {p.suffix for p in files} <= allowed, {p.name for p in files
                                                             if p.suffix not in allowed}
    for p in files:
        text = p.read_bytes()
        for secret in (b"PRIVATE KEY", b"sk_live_", b"whsec_", b"DATABASE_URL", b"AGE-SECRET-KEY"):
            assert secret not in text, f"{p.name} contains {secret!r}"


def test_the_content_security_policy_keeps_its_protections(client):
    csp = client.get("/login").headers["Content-Security-Policy"]
    rules = dict(part.strip().split(" ", 1) for part in csp.split(";") if part.strip())
    assert rules["default-src"] == "'self'"
    assert rules["script-src"] == "'self' 'unsafe-inline'"            # no 'unsafe-eval', no hosts
    assert rules["connect-src"] == "'self'" and rules["frame-ancestors"] == "'none'"
    assert rules["base-uri"] == "'self'" and rules["form-action"] == "'self'"
    assert "unsafe-eval" not in csp
