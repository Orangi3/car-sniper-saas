"""
mailer.py — transactional email over plain SMTP (stdlib only).

Disabled until configured; nothing falls back to "pretend it was sent":

    SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM ("Car Sniper <no-reply@your-domain>"), SMTP_STARTTLS (1)

Any SMTP service works (the mailbox provider you already use, or a
transactional provider's free tier). Credentials live in /etc/sniper/env
like every other secret and are never logged.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


class MailerUnavailable(Exception):
    """No SMTP configuration: callers must refuse, not skip, the step."""


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send(to: str, subject: str, body: str) -> None:
    if not configured():
        raise MailerUnavailable("email delivery is not configured (SMTP_HOST, SMTP_FROM)")
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body, cte="7bit")      # plain text: links stay intact in any client
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587)
    with smtplib.SMTP(host, port, timeout=15) as s:
        if os.environ.get("SMTP_STARTTLS", "1") not in ("0", "false", "no"):
            s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
