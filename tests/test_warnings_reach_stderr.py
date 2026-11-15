"""
tests/test_warnings_reach_stderr.py — the app's own warnings and errors are
written to stderr as well as to its log file.

In the one-container deploy (Render) the log file lives inside the
container: the host never shows it and every restart wipes it. A failed
verification email or an unexpected error was invisible to the owner
there. stderr is what a container host shows in its log stream and what
systemd puts in the journal. Request lines (INFO) stay in the file only.
SYNTHETIC accounts; no email is sent.
"""
from __future__ import annotations

import logging

from tests.conftest import login, register

SELLER = "seller@example.com"


def test_warning_and_error_lines_reach_stderr(client, capsys):
    log = logging.getLogger("sniper")
    log.warning("a warning line")
    log.error("an error line")
    err = capsys.readouterr().err
    assert "WARNING a warning line" in err
    assert "ERROR an error line" in err


def test_request_lines_stay_in_the_file(client, capsys):
    logging.getLogger("sniper").info("a request line")
    assert client.get("/login").status_code == 200
    assert capsys.readouterr().err == ""


def test_a_failed_verification_email_is_visible_to_the_owner(client, monkeypatch, capsys):
    import mailer

    def down(to, subject, body):
        raise mailer.MailerError("email server unavailable")

    monkeypatch.setenv("SNIPER_SELLER_SUBMISSIONS", "1")
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", down)
    register(client, SELLER); login(client, SELLER)
    capsys.readouterr()
    r = client.post("/api/seller/contact", json={})
    assert r.status_code == 503
    err = capsys.readouterr().err
    assert "WARNING seller verification email not sent: email server unavailable" in err
    assert SELLER not in err                     # the line names no address


def test_an_unexpected_error_is_visible_to_the_owner(client, monkeypatch, capsys):
    import seller_listings as sl

    def broken(conn, user):
        raise RuntimeError("database detail that only the owner should see")

    monkeypatch.setattr(sl, "contact", broken)
    register(client, SELLER); login(client, SELLER)
    capsys.readouterr()
    assert client.get("/api/seller/contact").status_code == 500
    err = capsys.readouterr().err
    assert "UNCAUGHT" in err and "GET /api/seller/contact" in err
    assert "database detail that only the owner should see" in err
