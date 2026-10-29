"""
mailer.py — transactional email over plain SMTP (stdlib only).

Disabled until configured; nothing falls back to "pretend it was sent":

    SMTP_HOST, SMTP_PORT (587 = STARTTLS, 465 = implicit TLS; SMTP_SSL=1
    forces implicit TLS on another port),
    SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM ("FlipFirst <no-reply@your-domain>")

TLS always verifies the server certificate and host name (Python's
smtplib does NOT unless given a context). Plain-text SMTP (SMTP_STARTTLS=0)
is accepted only for a server on this machine, so a password never crosses
the network unencrypted. Credentials live in /etc/sniper/env like every
other secret and are never logged or put in error messages.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

_LOCAL = ("127.0.0.1", "localhost", "::1")


class MailerUnavailable(Exception):
    """No SMTP configuration: callers must refuse, not skip, the step."""


class MailerError(Exception):
    """Sending failed (server down, TLS or auth failure). The message is
    safe to show: it never contains credentials or server responses."""


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def _tls_context() -> ssl.SSLContext:
    return ssl.create_default_context()        # CERT_REQUIRED + host name check


def send(to: str, subject: str, body: str) -> None:
    if not configured():
        raise MailerUnavailable("email delivery is not configured (SMTP_HOST, SMTP_FROM)")
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body, cte="7bit")      # plain text: links stay intact in any client
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587)
    implicit = port == 465 or os.environ.get("SMTP_SSL", "").lower() in ("1", "true", "yes")
    plain = (not implicit and
             os.environ.get("SMTP_STARTTLS", "1").lower() in ("0", "false", "no"))
    if plain and host not in _LOCAL:
        raise MailerError("refusing unencrypted SMTP to a remote server")
    try:
        if implicit:
            client = smtplib.SMTP_SSL(host, port, timeout=15, context=_tls_context())
        else:
            client = smtplib.SMTP(host, port, timeout=15)
        with client as s:
            if not implicit and not plain:
                s.starttls(context=_tls_context())
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
    except ssl.SSLError:
        raise MailerError("email server TLS certificate could not be verified") from None
    except smtplib.SMTPAuthenticationError:
        raise MailerError("email server rejected the login") from None
    except (smtplib.SMTPException, OSError):
        raise MailerError("email server unavailable") from None
