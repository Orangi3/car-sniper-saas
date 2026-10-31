"""
tests/test_bootstrap_archive.py — what reaches a new server.

deploy/bootstrap_archive.sh packs the committed tree only: untracked, ignored
and uncommitted content can't enter the archive copied to the server for
install.sh. deploy/sync.sh refuses to send a working tree with uncommitted
edits. Each test builds a real archive (or runs the real script) in a
throwaway git repository and checks what actually came out.
"""
from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "deploy" / "bootstrap_archive.sh"
SYNC = ROOT / "deploy" / "sync.sh"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def sh(args, cwd, env=None, check=True):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60, check=check,
                          env={**os.environ, **GIT_ENV, **(env or {})})


def repo(tmp_path, files: dict[str, str]) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    sh(["git", "init", "-q"], r)
    for name, text in files.items():
        (r / name).parent.mkdir(parents=True, exist_ok=True)
        (r / name).write_text(text)
    sh(["git", "add", "-A"], r)
    sh(["git", "commit", "-q", "-m", "release"], r)
    return r


INSTALL = 'cp "$HERE/env.staging.example" /etc/sniper/env\ncp "$HERE/systemd/sniper-web.service" /etc/systemd/system/\n'
COMMITTED = {"deploy/install.sh": INSTALL, "deploy/env.staging.example": "SMTP_PASSWORD=\n",
             "deploy/systemd/sniper-web.service": "[Unit]\n", "server.py": "print('app')\n",
             ".env.example": "VIN_PROVIDER=nhtsa\n", ".gitignore": "*.log\n"}


def archive(r: Path, sha: str, out: Path):
    return sh(["bash", str(ARCHIVE), sha, str(out)], r, check=False)


def members(path: Path) -> set[str]:
    with tarfile.open(path) as t:
        return {m.name for m in t.getmembers() if m.isfile()}


def test_untracked_ignored_and_uncommitted_content_never_enters_the_archive(tmp_path):
    r = repo(tmp_path, COMMITTED)
    secrets = {".env": "B2_APPLICATION_KEY=do-not-ship\n", "deploy/id_ed25519": "-----BEGIN OPENSSH PRIVATE KEY-----\n",
               "listings.db": "sqlite", ".venv/lib/site.py": "x", "notes/todo.txt": "private", "debug.log": "ignored"}
    for name, text in secrets.items():
        (r / name).parent.mkdir(parents=True, exist_ok=True)
        (r / name).write_text(text)
    (r / "server.py").write_text("print('uncommitted edit')\n")        # a tracked file, edited, not committed
    out = tmp_path / "boot.tgz"
    sha = sh(["git", "rev-parse", "HEAD"], r).stdout.strip()
    res = archive(r, sha, out)
    assert res.returncode == 0, res.stdout + res.stderr
    assert members(out) == set(COMMITTED)                            # exactly the committed tree
    assert not any(n.startswith(".git/") for n in members(out))
    with tarfile.open(out) as t:
        assert t.extractfile("server.py").read() == b"print('app')\n"   # committed content, not the edit
        assert b"do-not-ship" not in b"".join(t.extractfile(m).read() for m in t.getmembers() if m.isfile())


def test_the_archive_needs_the_exact_approved_commit(tmp_path):
    r = repo(tmp_path, COMMITTED)
    out = tmp_path / "boot.tgz"
    for wrong in ("0" * 40, sh(["git", "rev-parse", "--short", "HEAD"], r).stdout.strip(), ""):
        res = archive(r, wrong, out)
        assert res.returncode != 0 and not out.exists(), (wrong, res.stdout)


def test_a_committed_secret_or_a_missing_install_file_stops_the_archive(tmp_path):
    r = repo(tmp_path, {**COMMITTED, "deploy/server.key": "-----BEGIN PRIVATE KEY-----\n"})
    out = tmp_path / "boot.tgz"
    res = archive(r, sh(["git", "rev-parse", "HEAD"], r).stdout.strip(), out)
    assert res.returncode == 1 and "look secret" in res.stdout and "deploy/server.key" in res.stdout
    assert not out.exists()
    (r / "deploy/server.key").unlink()
    (r / "deploy/systemd/sniper-web.service").unlink()
    sh(["git", "commit", "-q", "-am", "drop a unit install.sh reads"], r)
    res = archive(r, sh(["git", "rev-parse", "HEAD"], r).stdout.strip(), out)
    assert res.returncode == 1 and "deploy/systemd/sniper-web.service is missing" in res.stdout
    assert not out.exists()


def test_the_real_release_archive_holds_everything_install_sh_reads(tmp_path):
    out = tmp_path / "boot.tgz"
    sha = sh(["git", "rev-parse", "HEAD"], ROOT).stdout.strip()
    res = archive(ROOT, sha, out)
    assert res.returncode == 0, res.stdout + res.stderr
    names = members(out)
    tracked = set(sh(["git", "ls-files", "-z"], ROOT).stdout.split("\0")) - {""}
    assert names <= tracked                                           # nothing untracked
    for f in ("deploy/install.sh", "deploy/Caddyfile.staging", "deploy/env.staging.example",
              "deploy/logrotate/sniper", "deploy/systemd/sniper-web.service", "deploy/golive.sh",
              "deploy/backup.sh", "requirements.txt", "server.py"):
        assert f in names, f


def test_sync_refuses_uncommitted_edits_before_contacting_the_host(tmp_path):
    r = repo(tmp_path, COMMITTED)
    fake = tmp_path / "bin"
    fake.mkdir()
    called = tmp_path / "called"
    for tool in ("ssh", "rsync"):
        (fake / tool).write_text(f"#!/bin/sh\necho {tool} >> {called}\nexit 99\n")
        (fake / tool).chmod(0o755)
    env = {"PATH": f"{fake}:{os.environ['PATH']}"}
    (r / "server.py").write_text("print('uncommitted edit')\n")
    res = sh(["bash", str(SYNC), "--apply", "root@example.invalid"], r, env=env, check=False)
    assert res.returncode == 4 and "uncommitted changes" in res.stderr, res.stdout + res.stderr
    assert not called.exists()                                        # no ssh, no rsync
    sh(["git", "checkout", "--", "server.py"], r)
    sh(["bash", str(SYNC), "--apply", "root@example.invalid"], r, env=env, check=False)
    assert called.read_text().startswith("ssh")                       # a clean tree goes on to the host


def test_the_docs_use_the_committed_tree_archive():
    docs = {p.name: p.read_text() for p in ROOT.glob("*.md")}
    for name, text in docs.items():
        assert "--files-from <(git ls-files)" not in text, name      # the old command packed the whole directory
    for name in ("FIRST_DEPLOY.md", "STAGING_RUNBOOK.md"):
        assert "deploy/bootstrap_archive.sh" in docs[name], name


def test_a_failed_write_leaves_no_archive_partial_or_stale(tmp_path):
    big = {"assets/blob.bin": ""}
    r = repo(tmp_path, {**COMMITTED, **big})
    (r / "assets/blob.bin").write_bytes(os.urandom(400_000))          # incompressible
    sh(["git", "commit", "-q", "-am", "big file"], r)
    out = tmp_path / "out" / "boot.tgz"
    out.parent.mkdir()
    out.write_text("an older archive")                                 # must not survive a failed run
    sha = sh(["git", "rev-parse", "HEAD"], r).stdout.strip()
    res = sh(["bash", "-c", f'ulimit -f 64; exec bash "{ARCHIVE}" {sha} "{out}"'], r, check=False)
    assert res.returncode != 0
    assert list(out.parent.iterdir()) == [], list(out.parent.iterdir())  # no archive, no *.partial.*


def test_install_paths_with_spaces_and_a_relative_output(tmp_path):
    install = INSTALL + 'cp "$HERE/units with space/a.service" /etc/systemd/system/\n'
    r = repo(tmp_path, {**COMMITTED, "deploy/install.sh": install, "deploy/units with space/a.service": "[Unit]\n"})
    sha = sh(["git", "rev-parse", "HEAD"], r).stdout.strip()
    (r / "deploy").mkdir(exist_ok=True)
    res = sh(["bash", str(ARCHIVE), sha, "boot.tgz"], r / "deploy", check=False)   # relative to the cwd
    assert res.returncode == 0, res.stdout + res.stderr
    assert "deploy/units with space/a.service" in members(r / "deploy" / "boot.tgz")
    (r / "deploy" / "boot.tgz").unlink()
    sh(["git", "rm", "-q", "deploy/units with space/a.service"], r)
    sh(["git", "commit", "-q", "-m", "drop the spaced unit"], r)
    res = archive(r, sh(["git", "rev-parse", "HEAD"], r).stdout.strip(), tmp_path / "b.tgz")
    assert res.returncode == 1 and "deploy/units with space/a.service is missing" in res.stdout
