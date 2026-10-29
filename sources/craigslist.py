"""
sources/craigslist.py — by-owner cars+trucks from Craigslist regional RSS.

Polls a configurable list of CL subdomains. Defaults to every Alabama region
plus the neighbouring metros a flipper will drive to from Tuscaloosa.
A region that answers 403/429 is backed off for a few minutes so one block
doesn't escalate into an IP ban across every region.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlencode

import feedparser
import requests

from . import health
from .base import (NormalizedListing, extract_int, extract_price, extract_make_model,
                    extract_miles, extract_year, looks_like_dealer,
                    looks_cash_only, PRICE_RE)

SOURCE_ID = "craigslist"
SOURCE_NAME = "Craigslist (by owner)"
# Data-licensing class (see SOURCES.md): Craigslist terms bar automated collection and commercial reuse without a written license.
LICENSING = "terms_prohibit_automation"

DEFAULT_REGIONS = ["tuscaloosa", "bham", "montgomery", "huntsville", "auburn",
                   "gadsden", "shoals", "dothan", "mobile", "meridian",
                   "columbusga", "chattanooga", "atlanta"]

# Subdomain → 2-letter state. Anything not listed falls back to AL.
REGION_STATE = {
    "tuscaloosa": "AL", "bham": "AL", "montgomery": "AL", "huntsville": "AL",
    "auburn": "AL", "gadsden": "AL", "shoals": "AL", "dothan": "AL",
    "mobile": "AL", "meridian": "MS", "jackson": "MS", "hattiesburg": "MS",
    "northmiss": "MS", "gulfport": "MS", "columbusga": "GA", "atlanta": "GA",
    "macon": "GA", "savannah": "GA", "augusta": "GA", "chattanooga": "TN",
    "nashville": "TN", "memphis": "TN", "knoxville": "TN", "pensacola": "FL",
    "panamacity": "FL", "tallahassee": "FL", "neworleans": "LA",
}

# Region → monotonic time before which we skip it (after a 403/429)
_BACKOFF_UNTIL: dict[str, float] = {}
BACKOFF_S = 300
# Browser-like UA — Craigslist actively 403's any UA containing "bot", "python",
# or unrecognized client strings. This is the single most common cause of empty results.
UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, text/html;q=0.7, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

# Diagnostics — last poll results per region, surfaced via sniper.SOURCE_DIAG
LAST_RESULTS: dict = {}


def enabled(cfg: dict) -> bool:
    return cfg.get("sources", {}).get(SOURCE_ID, {}).get("enabled", True)


def _feed_url(region: str, by_owner: bool) -> str:
    section = "cto" if by_owner else "cta"
    return f"https://{region}.craigslist.org/search/{section}?{urlencode({'format': 'rss'})}"


def _html_url(region: str, by_owner: bool) -> str:
    section = "cto" if by_owner else "cta"
    return f"https://{region}.craigslist.org/search/{section}"


def _post_id(url: str) -> str | None:
    m = re.search(r"/(\d{9,12})\.html", url or "")
    return m.group(1) if m else None


def _parse_html_fallback(html: str, region: str) -> list:
    """When RSS returns 0 entries, scrape the HTML search page. Craigslist
    embeds a JSON blob with all results in <script id="ld_searchpage_results">."""
    out = []
    # Try the modern JSON-LD blob first
    m = re.search(r'<script id="ld_searchpage_results"[^>]*>(.*?)</script>',
                  html, flags=re.DOTALL)
    if m:
        try:
            import json
            data = json.loads(m.group(1))
            items = data.get("itemListElement") or []
            for it in items:
                item = it.get("item", {})
                url = item.get("url") or ""
                name = item.get("name") or ""
                offer = item.get("offers") or {}
                price = offer.get("price")
                if not (url and name):
                    continue
                fake = type("E", (dict,), {"get": dict.get})({
                    "link": url, "title": f"{name} - ${price}" if price else name,
                    "summary": item.get("description", ""),
                    "updated": item.get("datePosted"),
                })
                out.append(fake)
        except (ValueError, TypeError):
            pass

    # Older CL layouts use <li class="cl-static-search-result">
    if not out:
        for chunk in re.findall(
            r'<li[^>]*cl-static-search-result[^>]*>(.*?)</li>',
            html, flags=re.DOTALL):
            link_m = re.search(r'href="(https?://[^"]+\.html)"', chunk)
            title_m = re.search(r'<div[^>]*class="title"[^>]*>([^<]+)', chunk)
            price_m = re.search(r'class="price"[^>]*>\$([\d,]+)', chunk)
            if not (link_m and title_m):
                continue
            title = title_m.group(1).strip()
            if price_m:
                title += f" - ${price_m.group(1)}"
            out.append(type("E", (dict,), {"get": dict.get})({
                "link": link_m.group(1), "title": title,
                "summary": "", "updated": None,
            }))
    return out


def _fetch_region(region: str, by_owner: bool, session: requests.Session) -> tuple[str, list, str]:
    """Fetch one region: RSS first, HTML search page as fallback. Every
    request outcome goes into the poll's health report, so a blocked or
    malformed region is never mistaken for 'no new listings'."""
    rep = health.report(SOURCE_ID)
    wait_s = _BACKOFF_UNTIL.get(region, 0) - time.monotonic()
    if wait_s > 0:
        return region, [], f"backing off ({int(wait_s)}s left after block)"
    entries: list = []
    r = rep.fetch(f"{region}:rss", _feed_url(region, by_owner), session=session,
                  headers=UA, timeout=8, allow_redirects=True)
    if r is None:
        last = rep.attempts[-1]
        if last["outcome"] in (health.BLOCKED, health.RATE_LIMITED):
            _BACKOFF_UNTIL[region] = time.monotonic() + BACKOFF_S
            return region, [], f"rss {last['outcome']} → backing off"
        note = f"rss {last['outcome']}"
    else:
        feed = feedparser.parse(r.content)
        entries = list(feed.entries)
        if entries:
            rep.parsed(f"{region}:rss", len(entries))
            return region, entries, f"rss 200: {len(entries)} entries"
        note = "rss 200 but no entries"
        if feed.bozo:
            rep.malformed(f"{region}:rss", "response is not a valid RSS feed")
        else:
            rep.parsed(f"{region}:rss", 0)
    r2 = rep.fetch(f"{region}:html", _html_url(region, by_owner), session=session,
                   headers=UA, timeout=8)
    if r2 is None:
        return region, [], note + f" → html {rep.attempts[-1]['outcome']}"
    entries = _parse_html_fallback(r2.text, region)
    if entries:
        rep.parsed(f"{region}:html", len(entries))
    elif ("ld_searchpage_results" in r2.text
          or "cl-static-search-result" in r2.text):
        rep.parsed(f"{region}:html", 0)          # recognizable page, just empty
    else:
        rep.malformed(f"{region}:html", "search page layout not recognized")
    return region, entries, note + f" → html 200: {len(entries)} parsed"


def poll(cfg: dict) -> list[NormalizedListing]:
    from concurrent.futures import ThreadPoolExecutor
    cl_cfg = cfg.get("sources", {}).get(SOURCE_ID, {})
    regions = cl_cfg.get("regions", DEFAULT_REGIONS)
    by_owner = cl_cfg.get("by_owner", True)

    session = requests.Session()
    out: list[NormalizedListing] = []

    # Fan out all regions in parallel with timeout cap. Use wait() so any
    # slow region just yields 0 instead of raising TimeoutError.
    from concurrent.futures import wait
    ex = ThreadPoolExecutor(max_workers=max(1, min(16, len(regions))))
    region_results = []
    try:
        futs = {ex.submit(_fetch_region, r, by_owner, session): r for r in regions}
        done, not_done = wait(list(futs.keys()), timeout=10)
        for f in done:
            try:
                region_results.append(f.result(timeout=0.5))
            except Exception as e:
                region_results.append((futs[f], [], f"err: {type(e).__name__}"))
        for f in not_done:
            f.cancel()
            region_results.append((futs[f], [], "timed out (>10s)"))
    finally:
        ex.shutdown(wait=False)

    for region, entries, status_note in region_results:
            LAST_RESULTS[region] = {"count": len(entries), "note": status_note}
            for entry in entries:
                link = entry.get("link") or ""
                pid = _post_id(link)
                if not pid:
                    continue
                title = entry.get("title") or ""
                desc = entry.get("summary") or entry.get("description") or ""
                price = extract_price(title) or extract_price(desc)
                mk, mdl = extract_make_model(title)
                is_dealer = looks_like_dealer(title, desc)
                # Pull image URLs from the description HTML (CL embeds img tags)
                image_urls = re.findall(r'<img[^>]+src="([^"]+\.(?:jpg|jpeg|png|webp))"',
                                         desc, flags=re.IGNORECASE)
                out.append(NormalizedListing(
                    source=SOURCE_ID, source_id=f"{region}:{pid}", url=link, title=title.strip(),
                    price=price, year=extract_year(title), make=mk, model=mdl,
                    odometer=extract_miles(title) or extract_miles(desc),
                    location=(entry.get("where") or "").strip() or None,
                    state=REGION_STATE.get(region, "AL"), description=desc,
                    posted_at=entry.get("updated") or entry.get("published"),
                    is_dealer=is_dealer, is_auction=False,
                    is_cash_only=(by_owner and not is_dealer) or looks_cash_only(title, desc),
                    accepts_financing=is_dealer,
                    image_urls=image_urls[:8],
                    extras={"region": region},
                ))
    return out
