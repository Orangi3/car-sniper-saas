"""tests/test_mailer.py — real SMTP exchange against an in-process sink."""
import socketserver
import threading

import pytest


class _Sink(socketserver.StreamRequestHandler):
    box: list = []

    def handle(self):
        w = lambda s: (self.wfile.write((s + "\r\n").encode()), self.wfile.flush())
        w("220 sink")
        data, in_data = [], False
        for raw in self.rfile:
            line = raw.decode().rstrip("\r\n")
            if in_data:
                if line == ".":
                    in_data = False
                    _Sink.box.append("\n".join(data)); data = []; w("250 ok")
                else:
                    data.append(line)
                continue
            c = line[:4].upper()
            if c in ("EHLO", "HELO"): w("250 sink")
            elif c == "DATA": in_data = True; w("354 go")
            elif c == "QUIT": w("221 bye"); return
            else: w("250 ok")


@pytest.fixture()
def sink(monkeypatch):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Sink.box = []
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(srv.server_address[1]))
    monkeypatch.setenv("SMTP_FROM", "Car Sniper <no-reply@example.test>")
    monkeypatch.setenv("SMTP_STARTTLS", "0")
    yield _Sink.box
    srv.shutdown()


def test_unconfigured_mailer_refuses(monkeypatch):
    import mailer
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert not mailer.configured()
    with pytest.raises(mailer.MailerUnavailable):
        mailer.send("a@example.com", "s", "b")


def test_link_survives_plain_text_delivery(sink):
    import mailer
    link = "https://carsniper.test/m#verify=AbC_-123" + "x" * 40
    mailer.send("seller@example.com", "Confirm", f"Open:\n\n{link}\n")
    msg = sink[0]
    assert "To: seller@example.com" in msg and "Content-Transfer-Encoding: 7bit" in msg
    assert link in msg                       # not quoted-printable mangled
