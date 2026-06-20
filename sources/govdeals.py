"""
sources/govdeals.py — GovDeals state/local government surplus auctions.

Often *great* deals on fleet trucks, police cruisers, school district vans,
public-works vehicles. Filtered to nearby states by default (AL, MS, GA, TN, FL).

GovDeals exposes RSS feeds per category.
"""
from __future__ import annotations

import feedparser
import re
import requests
from urllib.parse import urlencode

from .base import (NormalizedListing, extract_int, extract_make_model,
                    extract_miles, extract_year, PRICE_RE)

SOURCE_ID = "govdeals"
SOURCE_NAME = "GovDeals (gov surplus)"
# Category 13: Cars/Trucks/Vehicles. They deprecated several feed URLs;
# we try both the old and new patterns. If both fail this source silently
# returns 0 and the diagnostics panel will say so.
FEED_TEMPLATES = [
    "https://www.govdeals.com/rss/RSSFeed.aspx?categoryID=13&stateID={state}",
    "https://www.govdeals.com/index.cfm?fa=Main.RSSFeed&categoryID=13&stateID={state}",
]
FEED_TEMPLATE = FEED_TEMPLATES[0]  # legacy
LAST_RESULTS: dict = {}
DEFAULT_STATES = ["AL", "MS", "GA", "TN", "FL"]
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
      "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5",
      "Accept-Language": "en-US,en;q=0.9"}


def enabled(cfg: dict) -> bool:
    return cfg.get("sources", {}).get(SOURCE_ID, {}).get("enabled", True)


def _fetch_state(st: str):
    """Fetch one state's GovDeals feed. Tight 5s timeout per template."""
    for tmpl in FEED_TEMPLATES:
        try:
            r = requests.get(tmpl.format(state=st), headers=UA, timeout=5)
            LAST_RESULTS[st] = {"status": r.status_code, "url": tmpl}
            if r.status_code == 200 and len(r.content) > 200:
                return st, r.content
        except requests.RequestException as e:
            LAST_RESULTS[st] = {"error": type(e).__name__}
            continue
    return st, None


def poll(cfg: dict) -> list[NormalizedListing]:
    from concurrent.futures import ThreadPoolExecutor, wait
    states = cfg.get("sources", {}).get(SOURCE_ID, {}).get("states", DEFAULT_STATES)
    out: list[NormalizedListing] = []
    # Fan out states in parallel with wait() — never raises TimeoutError.
    state_contents = []
    ex = ThreadPoolExecutor(max_workers=min(8, len(states)))
    try:
        futs = [ex.submit(_fetch_state, st) for st in states]
        done, not_done = wait(futs, timeout=8)
        for f in done:
            try:
                st, content = f.result(timeout=0.5)
                if content:
                    state_contents.append((st, content))
            except Exception:
                continue
        for f in not_done:
            f.cancel()
    finally:
        ex.shutdown(wait=False)

    for st, content in state_contents:
        feed = feedparser.parse(content)
        for e in feed.entries:
            url = e.get("link") or ""
            if not url:
                continue
            slug = re.search(r"acctid=(\d+)", url) or re.search(r"itemid=(\d+)", url)
            sid = slug.group(1) if slug else url.split("/")[-1]
            title = (e.get("title") or "").strip()
            desc = e.get("summary") or e.get("description") or ""
            mk, mdl = extract_make_model(title)
            out.append(NormalizedListing(
                source=SOURCE_ID, source_id=f"{st}:{sid}", url=url, title=title,
                price=extract_int(PRICE_RE, desc),  # current bid
                year=extract_year(title), make=mk, model=mdl,
                odometer=extract_miles(desc),
                location=None, state=st, description=desc,
                posted_at=e.get("published"),
                is_dealer=False, is_auction=True,
                is_cash_only=True, accepts_financing=False,  # govt = cash on win
                extras={"fleet": True},
            ))
    return out
