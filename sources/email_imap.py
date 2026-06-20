"""
sources/email_imap.py — pull Marketplace/Nextdoor/OfferUp/eBay digest emails
straight from your own inbox via IMAP, then parse listings out.

This is the LEGAL way to "scrape" Facebook Marketplace and similar.
You're not hitting their site — you're processing emails THEY send YOU.
Set up saved searches on each platform → they email you matches → this
source reads your inbox and pulls every listing URL out.

Setup (in config.py or env):
    EMAIL_IMAP_HOST = "imap.gmail.com"   # imap.mail.me.com / outlook.office365.com / etc
    EMAIL_IMAP_PORT = 993
    EMAIL_IMAP_USER = "you@gmail.com"
    EMAIL_IMAP_PASS = "app-specific password"  # NOT your real password
    EMAIL_IMAP_FOLDER = "INBOX"
    EMAIL_IMAP_SENDERS = "facebookmail.com,nextdoor.com,offerup.com,ebay.com"

For Gmail: enable 2FA, then create an App Password at
   https://myaccount.google.com/apppasswords
For iCloud Mail: Settings → Apple ID → Sign-In & Security → App-Specific Passwords.
For Outlook/Office365: enable 2FA + App Password in Security settings.

The source remembers which message UIDs it has already processed (cache file
imap_seen.json) so each email is only imported once.
"""
from __future__ import annotations

import email
import imaplib
import json
import os
import re
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path
from typing import Optional

from .base import (NormalizedListing, extract_make_model, extract_miles,
                    extract_year)

SOURCE_ID = "email_imap"
SOURCE_NAME = "Email digest (IMAP)"

SEEN_PATH = Path(__file__).resolve().parent.parent / "imap_seen.json"

# Diagnostics for the dashboard panel
LAST_RESULTS: dict = {}


def _cfg(cfg: dict, key: str, env_key: str, default=None):
    val = cfg.get(key) if cfg else None
    if val is None:
        val = os.environ.get(env_key)
    if val is None:
        # Try config.py fallback
        try:
            from config import CONFIG
            val = CONFIG.get(key)
        except Exception:
            pass
    return val if val not in (None, "") else default


def enabled(cfg: dict) -> bool:
    src = cfg.get("sources", {}).get(SOURCE_ID, {})
    if not src.get("enabled", True):
        return False
    return bool(_cfg(cfg, "imap_user", "EMAIL_IMAP_USER")
                and _cfg(cfg, "imap_pass", "EMAIL_IMAP_PASS"))


def _load_seen() -> set:
    if not SEEN_PATH.exists():
        return set()
    try:
        return set(json.loads(SEEN_PATH.read_text()))
    except Exception:
        return set()


def _save_seen(seen: set) -> None:
    SEEN_PATH.write_text(json.dumps(sorted(seen)))


def _decode_text(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _email_body(msg: email.message.Message) -> str:
    """Concatenate text/plain + text/html parts."""
    parts = []
    if msg.is_multipart():
        for p in msg.walk():
            ct = p.get_content_type()
            if ct in ("text/plain", "text/html"):
                parts.append(_decode_text(p))
    else:
        parts.append(_decode_text(msg))
    return "\n\n".join(parts)


def poll(cfg: dict) -> list[NormalizedListing]:
    host = _cfg(cfg, "imap_host", "EMAIL_IMAP_HOST", "imap.gmail.com")
    port = int(_cfg(cfg, "imap_port", "EMAIL_IMAP_PORT", 993))
    user = _cfg(cfg, "imap_user", "EMAIL_IMAP_USER")
    pw   = _cfg(cfg, "imap_pass", "EMAIL_IMAP_PASS")
    folder = _cfg(cfg, "imap_folder", "EMAIL_IMAP_FOLDER", "INBOX")
    senders = _cfg(cfg, "imap_senders", "EMAIL_IMAP_SENDERS",
                   "facebookmail.com,nextdoor.com,offerup.com,ebay.com,craigslist.org")
    if not (user and pw):
        LAST_RESULTS["status"] = "no creds"
        return []

    sender_list = [s.strip() for s in senders.split(",") if s.strip()]
    seen = _load_seen()
    new_listings: list[NormalizedListing] = []

    try:
        M = imaplib.IMAP4_SSL(host, port)
        M.login(user, pw)
        M.select(folder)
    except Exception as e:
        LAST_RESULTS["status"] = f"login failed: {type(e).__name__}: {e}"
        return []

    try:
        # Build OR'd FROM filter for all configured senders
        # IMAP search with multiple ORs requires the prefix syntax.
        if sender_list:
            crit_parts = " ".join(f'OR FROM "{s}"' for s in sender_list[:-1])
            crit = f'({crit_parts} FROM "{sender_list[-1]}") UNSEEN'
        else:
            crit = "UNSEEN"
        typ, data = M.search(None, crit)
        if typ != "OK":
            LAST_RESULTS["status"] = f"search failed: {typ}"
            M.logout()
            return []
        ids = (data[0] or b"").split()
        LAST_RESULTS["matched"] = len(ids)

        from .marketplace_import import parse_email_digest
        for uid in ids[-100:]:  # cap for safety
            uid_s = uid.decode()
            if uid_s in seen:
                continue
            try:
                typ2, msg_data = M.fetch(uid, "(RFC822)")
                if typ2 != "OK":
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                body = _email_body(msg)
                imported = parse_email_digest(body)
                # Convert to NormalizedListing objects so the sniper sees them right away
                for it in imported:
                    new_listings.append(NormalizedListing(
                        source="marketplace",  # share source-id with extension push
                        source_id=str(it.get("id") or it.get("url") or uid_s),
                        url=it.get("url") or "",
                        title=it.get("title") or "",
                        price=it.get("price"),
                        year=it.get("year"),
                        make=extract_make_model(it.get("title") or "")[0],
                        model=extract_make_model(it.get("title") or "")[1],
                        odometer=it.get("miles"),
                        description=it.get("description") or "",
                        is_dealer=False, is_cash_only=True,
                        extras={"via": "imap", "platform": it.get("platform")},
                    ))
                seen.add(uid_s)
            except Exception:
                continue
        _save_seen(seen)
        LAST_RESULTS["status"] = f"imported {len(new_listings)} from {len(ids)} unread"
    finally:
        try: M.logout()
        except Exception: pass

    return new_listings
