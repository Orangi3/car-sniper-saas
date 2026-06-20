"""
sources/craigslist.py — by-owner cars+trucks from Craigslist regional RSS.

Polls a configurable list of CL subdomains. Defaults to the three regions
covering Tuscaloosa, AL within ~100mi (tuscaloosa, bham, montgomery).
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

import feedparser
import requests

from .base import (NormalizedListing, extract_int, extract_make_model,
                    extract_miles, extract_year, looks_like_dealer,
                    looks_cash_only, PRICE_RE)

SOURCE_ID = "craigslist"
SOURCE_NAME = "Craigslist (by owner)"

DEFAULT_REGIONS = ["tuscaloosa", "bham", "montgomery"]
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
    url = _feed_url(region, by_owner)
    entries = []
    status_note = ""
    try:
        r = session.get(url, headers=UA, timeout=8, allow_redirects=True)
        status_note = f"rss {r.status_code}"
        if r.status_code == 200:
            feed = feedparser.parse(r.content)
            entries = list(feed.entries)
    except requests.RequestException as e:
        status_note = f"rss error: {type(e).__name__}"
    if not entries:
        try:
            r2 = session.get(_html_url(region, by_owner), headers=UA, timeout=8)
            if r2.status_code == 200:
                entries = _parse_html_fallback(r2.text, region)
                status_note += f" → html {r2.status_code}: {len(entries)} parsed"
        except requests.RequestException as e:
            status_note += f" → html error: {type(e).__name__}"
    return region, entries, status_note


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
    ex = ThreadPoolExecutor(max_workers=min(8, len(regions)))
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
                price = extract_int(PRICE_RE, title) or extract_int(PRICE_RE, desc)
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
                    state="AL", description=desc,
                    posted_at=entry.get("updated") or entry.get("published"),
                    is_dealer=is_dealer, is_auction=False,
                    is_cash_only=(by_owner and not is_dealer) or looks_cash_only(title, desc),
                    accepts_financing=is_dealer,
                    image_urls=image_urls[:8],
                    extras={"region": region},
                ))
    return out
