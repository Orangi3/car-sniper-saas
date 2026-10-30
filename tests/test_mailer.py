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
        for raw in iter(self.rfile.readline, b""):
            line = raw.decode().rstrip("\r\n")
            if in_data:
                if line == ".":
                    in_data = False
                    _Sink.box.append("\n".join(data)); data = []; w("250 ok")
                else:
                    data.append(line)
                continue
            c = line[:4].upper()
            if c == "EHLO": w("250-sink"); w("250 AUTH PLAIN LOGIN")
            elif c == "HELO": w("250 sink")
            elif c == "AUTH": w("235 ok")
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


def test_non_ascii_copy_is_delivered_not_a_crash(sink):
    """A typographic dash in the copy used to raise UnicodeDecodeError under
    cte=7bit, which turned every verification email into a 500."""
    import mailer
    link = "https://app.example.test/m#verify=AbC_-123" + "x" * 40
    mailer.send("seller@example.com", "Confirm · FlipFirst", f"Open:\n\n{link}\n\n— FlipFirst · José")
    msg = sink[0]
    assert "Content-Transfer-Encoding: 8bit" in msg
    assert link in msg and "— FlipFirst · José" in msg


# ---------- TLS: certificates are verified, passwords never go out in clear ----------

import os
import shutil
import ssl
import subprocess


@pytest.fixture()
def tls_sink(tmp_path, monkeypatch):
    """Implicit-TLS SMTP sink with a self-signed certificate for 127.0.0.1.
    Records every command it receives (to prove no AUTH was sent)."""
    if not shutil.which("openssl"):
        pytest.skip("needs openssl")
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                    "-keyout", str(key), "-out", str(crt)], check=True, capture_output=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    seen: list[str] = []

    class TLSServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

        def get_request(self):
            sock, addr = super().get_request()
            return ctx.wrap_socket(sock, server_side=True), addr

        def handle_error(self, request, client_address):
            pass                                  # handshake refused by client: expected

    class H(_Sink):
        def handle(self):
            orig = self.rfile.readline

            def rl(*a):
                line = orig(*a)
                seen.append(line.decode(errors="replace").strip()[:20])
                return line
            self.rfile.readline = rl
            super().handle()
    srv = TLSServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Sink.box = []
    for k, v in {"SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(srv.server_address[1]),
                 "SMTP_SSL": "1", "SMTP_USER": "apikey", "SMTP_PASSWORD": "s3cret-value",
                 "SMTP_FROM": "Car Sniper <no-reply@example.test>"}.items():
        monkeypatch.setenv(k, v)
    yield crt, seen
    srv.shutdown()


def test_untrusted_certificate_is_refused_before_any_password_is_sent(tls_sink, monkeypatch):
    import mailer
    crt, seen = tls_sink
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    with pytest.raises(mailer.MailerError) as e:
        mailer.send("seller@example.com", "Confirm", "link")
    assert "could not be verified" in str(e.value)
    assert "s3cret" not in str(e.value)
    assert not any(c.upper().startswith("AUTH") for c in seen)
    assert _Sink.box == []


def test_trusted_certificate_delivers(tls_sink, monkeypatch):
    import mailer
    crt, seen = tls_sink
    monkeypatch.setenv("SSL_CERT_FILE", str(crt))          # trust this CA for the test
    mailer.send("seller@example.com", "Confirm", "https://x.test/m#verify=abc")
    assert any(c.upper().startswith("AUTH") for c in seen)
    assert "https://x.test/m#verify=abc" in _Sink.box[0]


def test_plaintext_smtp_only_to_this_machine(monkeypatch):
    import mailer
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "x <x@example.test>")
    monkeypatch.setenv("SMTP_STARTTLS", "0")
    monkeypatch.delenv("SMTP_SSL", raising=False)
    with pytest.raises(mailer.MailerError, match="unencrypted"):
        mailer.send("a@example.com", "s", "b")


def test_starttls_uses_a_verifying_context(monkeypatch):
    import mailer
    got = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): got["ctx"] = context
        def login(self, *a): pass
        def send_message(self, m): pass
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    for k, v in {"SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587",
                 "SMTP_FROM": "x <x@example.test>"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SMTP_STARTTLS", raising=False)
    monkeypatch.delenv("SMTP_SSL", raising=False)
    mailer.send("a@example.com", "s", "b")
    assert got["ctx"].verify_mode == ssl.CERT_REQUIRED and got["ctx"].check_hostname
