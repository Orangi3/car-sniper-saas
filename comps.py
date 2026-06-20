#!/usr/bin/env python3
"""
comps.py — sold-price comparable finder, 2025+ only, age-weighted.

Adapts to the live market by:
  - Filtering all comps to sale dates >= MIN_YEAR (default 2025-01-01)
  - Weighting more recent sales heavier in the average
    (linear ramp: 2025 → 1.0x, 2026 → 1.4x, 2027 → 1.8x)
  - Pulling from every source we can hit:

      eBay Motors completed listings (HTML)
      Bring a Trailer auction results (HTML)
      Cars & Bids auction results (HTML)
      PCarMarket sold results (HTML)
      Hemmings sold history (HTML)
      Copart salvage results (JSON) — reported separately as floor
      MarketCheck sold-prices API — if MARKETCHECK_API_KEY set

Cached 24h in comps_cache.sqlite. Each cache entry stores a 'fetched_at'
shown to the user as "comps last refreshed X ago".

Usage:
   python comps.py "2015 Mazda Miata"
   python comps.py --year 2015 --make Mazda --model Miata --miles 75000 --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
      "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 8     # tighter — parallel fetches make slow ones the bottleneck

CACHE_PATH = Path(__file__).parent / "comps_cache.sqlite"
CACHE_TTL_S = 24 * 3600

MIN_YEAR = 2025          # only count sales from this year forward
CURRENT_YEAR = datetime.now().year


# ---------- cache ---------------------------------------------------------

def _cache() -> sqlite3.Connection:
    c = sqlite3.connect(CACHE_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, "
              "value TEXT, fetched_at INTEGER)")
    return c


def cached_get(key: str, fn):
    with _cache() as c:
        row = c.execute("SELECT value, fetched_at FROM cache WHERE key=?", (key,)).fetchone()
        if row and time.time() - row[1] < CACHE_TTL_S:
            return json.loads(row[0]), row[1]
    val = fn()
    with _cache() as c:
        c.execute("INSERT OR REPLACE INTO cache(key, value, fetched_at) VALUES(?,?,?)",
                  (key, json.dumps(val), int(time.time())))
    return val, int(time.time())


# ---------- date helpers --------------------------------------------------

DATE_RE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
DATE_RE_NAMED = re.compile(r"\b([A-Z][a-z]{2,8})\s+(\d{1,2}),?\s+(\d{4})\b")


def _parse_sale_date(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    m = DATE_RE_SLASH.search(s)
    if m:
        try:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
            return datetime(y, mo, d, tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    m = DATE_RE_NAMED.search(s)
    if m:
        try:
            return datetime.strptime(m.group(0), "%b %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime.strptime(m.group(0), "%B %d, %Y").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def _date_weight(sale_dt: Optional[datetime]) -> float:
    if not sale_dt:
        return 0.5  # unknown date: half-weight
    yr = sale_dt.year
    if yr < MIN_YEAR:
        return 0.0
    return 1.0 + (yr - MIN_YEAR) * 0.4   # 2025=1.0, 2026=1.4, 2027=1.8


# ---------- source: eBay Motors completed -------------------------------

def ebay_sold(query: str, max_items: int = 80) -> list[dict]:
    base = "https://www.ebay.com/sch/i.html"
    params = {"_nkw": query, "_sacat": 6001, "LH_Sold": 1, "LH_Complete": 1, "_ipg": 240}
    try:
        r = requests.get(base + "?" + urlencode(params), headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        html = r.text
    except requests.RequestException:
        return []
    items = []
    for chunk in re.findall(r'<li[^>]+s-item[^"]*"[^>]*>(.*?)</li>', html, flags=re.DOTALL)[:max_items]:
        title_m = re.search(r'class="s-item__title"[^>]*>(?:<span[^>]*>)?([^<]+)', chunk)
        price_m = re.search(r'class="s-item__price"[^>]*>(?:<span[^>]*>)?\$([\d,]+)', chunk)
        url_m = re.search(r'href="(https://www\.ebay\.com/itm/[^"]+)"', chunk)
        date_m = re.search(r'Sold\s+(?:on\s+)?([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})', chunk)
        if not (title_m and price_m):
            continue
        try:
            price = int(price_m.group(1).replace(",", ""))
        except ValueError:
            continue
        title = re.sub(r"\s+", " ", title_m.group(1).strip())
        if title.lower().startswith("shop on ebay"):
            continue
        items.append({"price": price, "title": title,
                      "url": url_m.group(1) if url_m else None,
                      "end_date": date_m.group(1) if date_m else None,
                      "source": "ebay"})
    return items


# ---------- source: Bring a Trailer ------------------------------------

def bat_sold(query: str, max_items: int = 60) -> list[dict]:
    try:
        r = requests.get("https://bringatrailer.com/?" + urlencode({"s": query}),
                         headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        html = r.text
    except requests.RequestException:
        return []
    items = []
    for url_, title, _, price, date in re.findall(
        r'href="(https://bringatrailer\.com/listing/[^"]+)"[^>]*>([^<]+)</a>.*?'
        r'(Sold\s+for\s+USD\s+\$([\d,]+)\s+on\s+(\d{1,2}/\d{1,2}/\d{2,4}))',
        html, flags=re.DOTALL,
    )[:max_items]:
        try:
            items.append({"price": int(price.replace(",", "")),
                          "title": re.sub(r"\s+", " ", title).strip(),
                          "url": url_, "end_date": date, "source": "bat"})
        except ValueError:
            continue
    return items


# ---------- source: Cars & Bids ----------------------------------------

def carsbids_sold(query: str, max_items: int = 60) -> list[dict]:
    try:
        r = requests.get("https://carsandbids.com/search?" + urlencode({"q": query}),
                         headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        html = r.text
    except requests.RequestException:
        return []
    items = []
    for m in re.finditer(
        r'href="(/auctions/[^"]+)"[^>]*>.*?<h\d[^>]*>([^<]+)</h\d>.*?'
        r'Sold[^$]*\$([\d,]+).*?(\d{1,2}/\d{1,2}/\d{2,4})?',
        html, flags=re.DOTALL,
    ):
        try:
            items.append({"price": int(m.group(3).replace(",", "")),
                          "title": re.sub(r"\s+", " ", m.group(2)).strip(),
                          "url": "https://carsandbids.com" + m.group(1),
                          "end_date": m.group(4), "source": "carsandbids"})
        except (ValueError, IndexError):
            continue
        if len(items) >= max_items:
            break
    return items


# ---------- source: PCarMarket sold ------------------------------------

def pcarmarket_sold(query: str, max_items: int = 40) -> list[dict]:
    try:
        r = requests.get("https://www.pcarmarket.com/search/?" + urlencode({"keywords": query}),
                         headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        html = r.text
    except requests.RequestException:
        return []
    items = []
    for m in re.finditer(
        r'href="(/auction/[^"]+)"[^>]*>([^<]+)</a>.*?Sold\s+for\s+\$([\d,]+)(?:.*?(\d{1,2}/\d{1,2}/\d{2,4}))?',
        html, flags=re.DOTALL,
    ):
        try:
            items.append({"price": int(m.group(3).replace(",", "")),
                          "title": re.sub(r"\s+", " ", m.group(2)).strip(),
                          "url": "https://www.pcarmarket.com" + m.group(1),
                          "end_date": m.group(4), "source": "pcarmarket"})
        except (ValueError, IndexError):
            continue
        if len(items) >= max_items:
            break
    return items


# ---------- source: Hemmings sold history ------------------------------

def hemmings_sold(query: str, max_items: int = 40) -> list[dict]:
    try:
        r = requests.get(
            "https://www.hemmings.com/classifieds/cars-for-sale?" + urlencode({"q": query, "status": "sold"}),
            headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        html = r.text
    except requests.RequestException:
        return []
    items = []
    for m in re.finditer(
        r'href="(https://www\.hemmings\.com/classifieds/dealer/[^"]+)"[^>]*>([^<]+)</a>.*?\$([\d,]+).*?Sold\s+(\d{1,2}/\d{1,2}/\d{2,4})',
        html, flags=re.DOTALL,
    ):
        try:
            items.append({"price": int(m.group(3).replace(",", "")),
                          "title": re.sub(r"\s+", " ", m.group(2)).strip(),
                          "url": m.group(1), "end_date": m.group(4),
                          "source": "hemmings"})
        except (ValueError, IndexError):
            continue
        if len(items) >= max_items:
            break
    return items


# ---------- source: Copart salvage (price floor) -----------------------

def copart_sold(query: str, max_items: int = 30) -> list[dict]:
    payload = {"filter": {"MISC": ["sold_status:Sold"]}, "query": query,
               "watchListOnly": False, "free": True, "page": 0, "size": max_items,
               "sort": "auction_date_utc desc"}
    try:
        r = requests.post("https://www.copart.com/public/data/lotdetails/solr/lotSearch",
                          json=payload, headers={**UA, "Accept": "application/json"},
                          timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    out = []
    for lot in (data.get("data", {}).get("results", {}).get("content", []) or [])[:max_items]:
        price = lot.get("ltsg") or lot.get("hb") or lot.get("hsdb")
        if not price:
            continue
        out.append({"price": int(price),
                    "title": f"{lot.get('lcy','?')} {lot.get('lm','?')} {lot.get('mkn','?')}".strip(),
                    "url": f"https://www.copart.com/lot/{lot.get('lot_id','')}",
                    "end_date": lot.get("ad"), "source": "copart", "salvage": True})
    return out


# ---------- source: MarketCheck recent sold (paid, optional) -----------

def marketcheck_sold(query: str, year: int, make: str, model: str,
                      max_items: int = 100) -> list[dict]:
    key = os.environ.get("MARKETCHECK_API_KEY")
    if not key:
        return []
    try:
        r = requests.get(
            "https://api.marketcheck.com/v2/sales/car",
            params={"api_key": key, "year": year, "make": make, "model": model,
                    "rows": max_items, "include_relevant_links": "false"},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    out = []
    for it in (data.get("listings") or [])[:max_items]:
        if not it.get("price"):
            continue
        out.append({"price": int(it["price"]),
                    "title": it.get("heading") or "",
                    "url": it.get("vdp_url"),
                    "end_date": it.get("last_seen_date") or it.get("first_seen_date"),
                    "source": "marketcheck"})
    return out


# ---------- aggregation -------------------------------------------------

def _comp_match_pct(item: dict, year: int, make: str, model: str) -> int:
    """0-100 score for how well this comp matches the target spec.
    Year: 40 pts (exact=40, ±1=20, ±2=10, else 0)
    Make: 30 pts (exact substring match)
    Model: 30 pts (first model token matches)
    """
    t = (item.get("title") or "").lower()
    score = 0
    # Year (40 pts)
    matched_year = None
    for y in range(year - 2, year + 3):
        if str(y) in t:
            matched_year = y; break
    if   matched_year == year:                          score += 40
    elif matched_year in (year - 1, year + 1):          score += 20
    elif matched_year is not None:                      score += 10
    # Make (30 pts)
    if make.lower().strip() in t:
        score += 30
    # Model (30 pts) — first non-trivial token
    md_tokens = [tok for tok in model.lower().split() if len(tok) >= 2]
    if md_tokens and md_tokens[0] in t:
        score += 30
    return score


def _filter_by_match_quality(items: list[dict], year: int, make: str, model: str,
                              threshold: int = 100) -> tuple[list[dict], int]:
    """Score every comp + keep only those >= threshold. Annotates each item
    with `match_pct` for UI display. Returns (kept, rejected_count)."""
    rejected = 0
    out = []
    for it in items:
        pct = _comp_match_pct(it, year, make, model)
        it["match_pct"] = pct
        if pct >= threshold:
            out.append(it)
        else:
            rejected += 1
    return out, rejected


def _filter_to_recent(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split items into (recent_2025plus, older). Returns recent + older for context."""
    recent, older = [], []
    for it in items:
        sale_dt = _parse_sale_date(it.get("end_date"))
        it["_sale_dt"] = sale_dt.isoformat() if sale_dt else None
        it["_year_weight"] = _date_weight(sale_dt)
        if sale_dt and sale_dt.year >= MIN_YEAR:
            recent.append(it)
        elif not sale_dt:
            recent.append(it)  # unknown dates kept but at half weight
        else:
            older.append(it)
    return recent, older


def _weighted_average(items: list[dict]) -> tuple[Optional[float], Optional[float], int]:
    if not items:
        return None, None, 0
    prices = [it["price"] for it in items if it.get("price") and 1000 <= it["price"] <= 200_000]
    weights = [it.get("_year_weight", 0.5) for it in items
               if it.get("price") and 1000 <= it["price"] <= 200_000]
    if not prices:
        return None, None, 0
    if len(prices) >= 5:
        s = sorted(zip(prices, weights))
        k = max(1, int(len(s) * 0.10))
        s = s[k:-k]
        prices, weights = zip(*s)
        prices, weights = list(prices), list(weights)
    total_w = sum(weights) or 1.0
    avg = sum(p * w for p, w in zip(prices, weights)) / total_w
    median = statistics.median(prices)
    return round(avg, 2), round(median, 2), len(prices)


def comps_for_listing(year: int, make: str, model: str,
                       odometer: int | None = None) -> dict:
    q = f"{year} {make} {model}".strip()

    def _fetch():
        # PARALLEL fetch — all 7 comp sources concurrently. wait() is used
        # instead of as_completed(timeout=N) so slow sources don't raise
        # TimeoutError — they just return [] and we move on.
        from concurrent.futures import ThreadPoolExecutor, wait
        sources = {
            "ebay":        lambda: ebay_sold(q),
            "bat":         lambda: bat_sold(q),
            "carsandbids": lambda: carsbids_sold(q),
            "pcarmarket":  lambda: pcarmarket_sold(q),
            "hemmings":    lambda: hemmings_sold(q),
            "copart":      lambda: copart_sold(q),
            "marketcheck": lambda: marketcheck_sold(q, year, make, model),
        }
        results = {k: [] for k in sources}
        ex = ThreadPoolExecutor(max_workers=len(sources))
        try:
            futures = {ex.submit(fn): name for name, fn in sources.items()}
            done, not_done = wait(list(futures.keys()), timeout=10)
            for f in done:
                try:
                    results[futures[f]] = f.result(timeout=0.5)
                except Exception:
                    pass
            for f in not_done:
                f.cancel()
                # results[name] stays as the empty default
        finally:
            ex.shutdown(wait=False)
        return results

    raw, fetched_at_ts = cached_get(f"comps:{q}", _fetch)

    # Headline pool: everything except salvage
    main = []
    for s in ("ebay", "bat", "carsandbids", "pcarmarket", "hemmings", "marketcheck"):
        main.extend(raw.get(s, []))

    # Quality threshold — each comp scored 0-100. Default 100 = perfect match only.
    # Configurable via CONFIG["comp_match_threshold_pct"].
    try:
        from config import CONFIG
        threshold = int(CONFIG.get("comp_match_threshold_pct", 100))
    except Exception:
        threshold = 100
    pre_strict = len(main)
    # Tiered match-quality relaxation. Try the user's preferred threshold
    # first; if that yields too few comps to be reliable, step down so we
    # still return a verified number instead of nothing. The threshold
    # actually used is reported back as effective_match_threshold_pct.
    effective = threshold
    all_main = list(main)  # keep the full pool for relaxation retries
    main, strict_rejected = _filter_by_match_quality(main, year, make, model, threshold)
    for relaxed in (70, 50):
        if len(main) >= 3 or relaxed >= threshold:
            break
        effective = relaxed
        main, strict_rejected = _filter_by_match_quality(
            all_main, year, make, model, relaxed)
    print(f"[comps quality] requested={threshold}% effective={effective}% "
          f"kept {len(main)}/{pre_strict}")

    recent_main, older_main = _filter_to_recent(main)

    # Salvage gets reported separately (price floor)
    salvage_recent, _ = _filter_to_recent(raw.get("copart", []))
    salvage_prices = [it["price"] for it in salvage_recent if it.get("price")]

    avg, median, n = _weighted_average(recent_main)
    out = {
        "query": q, "min_sale_year": MIN_YEAR,
        "match_threshold_pct": threshold,
        "effective_match_threshold_pct": effective,
        "match_rejected": strict_rejected,
        "n": n,
        "n_raw_recent": len(recent_main),
        "n_raw_older": len(older_main),
        "avg": avg, "median": median,
        "min": min((it["price"] for it in recent_main if it.get("price")), default=None),
        "max": max((it["price"] for it in recent_main if it.get("price")), default=None),
        "sources": [s for s in ("ebay","bat","carsandbids","pcarmarket","hemmings","marketcheck")
                    if raw.get(s)],
        "salvage_avg": round(statistics.mean(salvage_prices), 2) if salvage_prices else None,
        "salvage_n":   len(salvage_prices),
        "examples":    sorted(recent_main, key=lambda x: -(x.get("_year_weight") or 0))[:12],
        "fetched_at":  datetime.fromtimestamp(fetched_at_ts, timezone.utc).isoformat(),
        "fetched_at_age_min": int((time.time() - fetched_at_ts) / 60),
    }
    if odometer and out["avg"] and odometer > 100_000:
        out["mileage_adjusted_avg"] = round(out["avg"] - (odometer - 100_000) * 0.08, 2)
    return out


def _print_summary(c: dict) -> None:
    print(f"\n=== Comps for: {c['query']}  (sold ≥ {c['min_sale_year']}, age-weighted) ===")
    print(f"  comps last refreshed {c['fetched_at_age_min']} min ago")
    if not c["avg"]:
        print(f"  No recent sold prices found across {', '.join(c['sources']) or 'any source'}.")
    else:
        print(f"  n={c['n']}  (recent {c['n_raw_recent']}, older skipped {c['n_raw_older']})")
        print(f"  avg=${c['avg']:,.0f}  median=${c['median']:,.0f}  range ${c['min']:,}–${c['max']:,}")
        print(f"  sources: {', '.join(c['sources'])}")
        if c.get("mileage_adjusted_avg"):
            print(f"  mileage-adjusted avg: ${c['mileage_adjusted_avg']:,.0f}")
    if c["salvage_avg"]:
        print(f"  salvage (Copart) avg ${c['salvage_avg']:,.0f}  n={c['salvage_n']}")
    print("\n  Recent sales:")
    for ex in c["examples"][:10]:
        tag = f"[{ex['source']}]"
        d = ex.get("end_date") or "?"
        print(f"    {tag:13s} ${ex['price']:>7,}  {d:14s}  {ex['title'][:70]}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("query", nargs="?")
    p.add_argument("--year", type=int); p.add_argument("--make"); p.add_argument("--model")
    p.add_argument("--miles", type=int); p.add_argument("--json", action="store_true")
    a = p.parse_args()
    if a.year and a.make and a.model:
        c = comps_for_listing(a.year, a.make, a.model, a.miles)
    elif a.query:
        m = re.match(r"\s*(\d{4})\s+(\S+)\s+(.+?)\s*$", a.query)
        if not m:
            print("usage: comps.py 'YYYY Make Model'  OR  --year/--make/--model", file=sys.stderr)
            sys.exit(2)
        c = comps_for_listing(int(m.group(1)), m.group(2), m.group(3), a.miles)
    else:
        p.print_help(); sys.exit(2)
    print(json.dumps(c, indent=2, default=str)) if a.json else _print_summary(c)


if __name__ == "__main__":
    main()
