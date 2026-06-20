"""
scam_detector.py — multi-signal scam scoring for vehicle listings.

Returns (score, reasons[]) where score is 0–100.
  ≥ 50 → flag in UI
  ≥ 80 → auto-hide unless user toggles "show scams"

Signals (weighted):
  payment red flags        wire / WU / MoneyGram / crypto / nonrefundable deposit
  shipping red flags       overseas / out-of-country / "I'll ship it"
  sob-story red flags      deployed / dying spouse / estate
  eBay-impersonation       "eBay Motors Vehicle Protection Program" etc
  comms red flags          email-only / no-phone
  price red flag           >X% below comp average (computed elsewhere)
  thin-profile red flag    one-photo / no-VIN
"""
from __future__ import annotations

import re
from typing import Optional

# (regex, weight, label)
PATTERNS: list[tuple[str, int, str]] = [
    # Payment scam patterns (very high weight)
    (r"\bwire\s*transfer\b",                            30, "wire transfer requested"),
    (r"\bwestern\s*union\b",                            35, "Western Union requested"),
    (r"\bmoneygram\b",                                  35, "MoneyGram requested"),
    (r"\bzelle\s*only\b|\bonly\s*zelle\b",              25, "Zelle-only payment"),
    (r"\bcashier'?s?\s*check\s*only\b",                 20, "cashier's check only"),
    (r"\bbitcoin\b|\bcrypto(currency)?\b|\busdt\b|\beth\b",  30, "crypto payment"),
    (r"\bgift\s*card(s)?\b",                            40, "gift card payment"),
    (r"\bnon[-\s]?refundable\s*deposit\b",              30, "nonrefundable deposit"),
    (r"\bsend\s*deposit\s*(first|before|via)\b",        25, "deposit before viewing"),
    (r"\bpaypal\s*friends\s*and\s*family\b",            30, "PayPal F&F (no buyer protection)"),

    # Shipping red flags — private parties don't ship cars
    (r"\boverseas\b|\bout\s*of\s*country\b",            30, "overseas / out of country"),
    (r"\bship(ping)?\s*at\s*my\s*expense\b",            30, "shipping at seller's expense (rare)"),
    (r"\bcan\s*ship\s*nationwide\b|\bnationwide\s*shipping\s*available\b",
                                                        20, "nationwide shipping (private)"),
    (r"\bshipping\s*included\s*in\s*the\s*price\b",     25, "shipping included"),

    # Sob-story patterns
    (r"\b(currently\s*)?deployed\b|\bdeployment\b",     30, "deployed military story"),
    (r"\bmilitary\s*orders\b",                          25, "military orders story"),
    (r"\bmoving\s*overseas\b",                          25, "moving overseas story"),
    (r"\bdying\s*(husband|wife|father|mother)\b",       40, "dying spouse story"),
    (r"\blate\s*(husband|wife|father|mother)\b",        25, "late spouse / inherited"),
    (r"\bestate\s*sale\b",                              10, "estate sale (some legit)"),
    (r"\bcancer\b.*\b(must\s*sell|need\s*to\s*sell)\b",  35, "cancer-must-sell story"),
    (r"\bgoing\s*through\s*divorce\b",                  10, "divorce story"),

    # eBay Motors impersonation (people copy from old scam emails)
    (r"\bebay\s*motors\s*vehicle\s*protection\s*program\b", 60, "eBay Motors VPP impersonation"),
    (r"\bebay\s*purchase\s*protection\b",                  50, "eBay Purchase Protection scam"),
    (r"\bvehicle\s*purchase\s*protection\s*program\b",     45, "VPP scam phrasing"),
    (r"\b(ebay|amazon|google)\s*will\s*hold\s*the\s*funds\b",
                                                           45, "fake escrow story"),
    (r"\bsecond\s*chance\s*offer\b",                       35, "eBay 'second chance' scam"),

    # Communications red flags
    (r"\bemail\s*only\s*please\b|\bno\s*phone\s*calls\b",  15, "email-only contact"),
    (r"\bno\s*calls\s*please\b",                            8, "no calls"),
    (r"\bcontact\s*me\s*at\s*\S+@",                        10, "contact email in body"),

    # Generic high-risk phrasing
    (r"\btitle\s*in\s*the\s*mail\b|\btitle\s*coming\s*in\s*mail\b",
                                                           25, "title-in-the-mail"),
    (r"\blost\s*the\s*title\b",                            15, "lost title"),
    (r"\bduplicate\s*title\s*pending\b",                   15, "duplicate title pending"),
    (r"\bbought\s*at\s*auction\s*never\s*titled\b",        20, "untitled auction buy"),

    # Stock-photo language
    (r"\bstock\s*photo(s)?\b",                             25, "explicit stock-photo"),
    (r"\b(photos|pictures)\s*coming\s*soon\b",             10, "no photos yet"),

    # Too-good-to-be-true language
    (r"\bbrand\s*new\b.*\$[12]\d\d\b",                     20, "'brand new' tiny price"),
    (r"\bgift\b.*\bcar\b|\bgifting\b.*\bvehicle\b",        35, "gifting car story"),

    # Foreign-language red flag (non-English in a CL US listing is suspicious for high-end cars)
    # We could add this but skipping — not a reliable signal alone.
]

COMPILED = [(re.compile(p, re.IGNORECASE), w, label) for p, w, label in PATTERNS]


def score_listing(title: str, description: str = "",
                   price: Optional[int] = None, comp_avg: Optional[float] = None) -> tuple[int, list[str]]:
    """Return (scam_score 0-100, list of triggered reasons)."""
    blob = f"{title or ''}\n{description or ''}"
    score = 0
    reasons: list[str] = []
    for rx, weight, label in COMPILED:
        if rx.search(blob):
            score += weight
            reasons.append(label)

    # Price-too-good red flag — only count when comp data exists
    if price and comp_avg and comp_avg > 0:
        ratio = price / comp_avg
        if ratio < 0.30:
            score += 40
            reasons.append(f"price {ratio*100:.0f}% of comp avg (extreme)")
        elif ratio < 0.45:
            score += 20
            reasons.append(f"price {ratio*100:.0f}% of comp avg (suspicious)")

    # Cap at 100
    score = min(100, score)
    return score, reasons
