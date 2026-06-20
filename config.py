"""
config.py — central knobs for the sniper.

Most knobs can also be changed LIVE from the dashboard's Settings card.
Anything you set there is written to overrides.json and re-read on every
poll, so it takes effect on the next tick (no restart needed).

Edit this file for changes that the dashboard doesn't expose, then
double-click START HERE.command to apply.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

_OVERRIDES_PATH = Path(__file__).parent / "overrides.json"


def _load_overrides() -> dict:
    if not _OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(_OVERRIDES_PATH.read_text())
    except Exception:
        return {}


def save_override(key: str, value) -> None:
    cur = _load_overrides()
    cur[key] = value
    _OVERRIDES_PATH.write_text(json.dumps(cur, indent=2))


def reload() -> None:
    """Re-read overrides.json into CONFIG. Defensively skip bad values."""
    o = _load_overrides()
    if not isinstance(o, dict):
        return
    for k, v in o.items():
        try:
            if k == "craigslist_regions":
                if isinstance(v, list) and v:
                    CONFIG["sources"]["craigslist"]["regions"] = [str(x) for x in v]
            elif k == "govdeals_states":
                if isinstance(v, list) and v:
                    states = [str(x).upper()[:2] for x in v]
                    for src_id in ("govdeals",):  # publicsurplus etc are stubbed out
                        if src_id in CONFIG["sources"]:
                            CONFIG["sources"][src_id]["states"] = states
            elif k in ("zip",):
                if isinstance(v, str) and len(v) == 5 and v.isdigit():
                    CONFIG[k] = v
                    if "ebay" in CONFIG["sources"]:        CONFIG["sources"]["ebay"]["zip"] = v
                    if "marketcheck" in CONFIG["sources"]: CONFIG["sources"]["marketcheck"]["zip"] = v
            elif k in ("radius_mi",):
                rv = int(v)
                if 1 <= rv <= 1000:
                    CONFIG[k] = rv
                    if "ebay" in CONFIG["sources"]:        CONFIG["sources"]["ebay"]["radius_mi"] = rv
                    if "marketcheck" in CONFIG["sources"]: CONFIG["sources"]["marketcheck"]["radius_mi"] = rv
            elif k in ("min_price", "max_price", "max_listing_age_min",
                       "deal_threshold_pct", "closing_soon_hours",
                       "comp_match_threshold_pct", "notify_min_score"):
                CONFIG[k] = int(v)
            elif k == "notify_phone":
                CONFIG[k] = str(v)
            elif k == "notify_text_enabled":
                CONFIG[k] = bool(v)
            elif k in ("share_username", "share_password"):
                CONFIG[k] = str(v)
            elif k == "share_public":
                CONFIG[k] = bool(v)
            elif k in ("quiet_start_hour", "quiet_end_hour"):
                # Accept None to clear, otherwise clamp to 0..23
                if v is None or v == "":
                    CONFIG[k] = None
                else:
                    try:
                        CONFIG[k] = max(0, min(23, int(v)))
                    except (TypeError, ValueError):
                        pass
            else:
                CONFIG[k] = v
        except (ValueError, TypeError, KeyError) as e:
            # Skip bad override — never let one bad value break the whole load
            print(f"[config.reload] skip {k}: {e}")
            continue


_OVERRIDES = _load_overrides()


CONFIG: dict = {
    # ----- Where you are (live-editable from dashboard) -----------------
    "zip": _OVERRIDES.get("zip", "35401"),
    "radius_mi": _OVERRIDES.get("radius_mi", 100),
    "states_nearby": _OVERRIDES.get("states_nearby", ["AL", "MS", "GA", "TN", "FL"]),

    # ----- Price band ---------------------------------------------------
    "min_price": _OVERRIDES.get("min_price", 1500),
    "max_price": _OVERRIDES.get("max_price", 60000),

    # ----- Deal alert threshold (DEFAULT 25%, dashboard slider) ---------
    "deal_threshold_pct": _OVERRIDES.get("deal_threshold_pct", 25),

    # ----- Comp match quality threshold (DEFAULT 100% = perfect match only)
    # 100 = title must contain exact year + make + model (every comp scored
    #       40 yr + 30 make + 30 model, must reach this number to count)
    #  70 = allows ±1 year OR missing make (looser, more comp volume)
    #  60 = allows ±1 year AND looser make match
    "comp_match_threshold_pct": _OVERRIDES.get("comp_match_threshold_pct", 100),

    # ----- Listing age cap (live-editable) ------------------------------
    # Listings older than this are dropped from the dashboard's "All listings"
    # view by default. Set in MINUTES. 0 or null = no age filter.
    # Default: only show listings posted in the last 24 hours
    "max_listing_age_min": _OVERRIDES.get("max_listing_age_min", 1440),

    # ----- Polling tiers ------------------------------------------------
    "poll_interval_sec": _OVERRIDES.get("poll_interval_sec", 60),
    "craigslist_every_n_ticks": 1,
    "auction_sites_every_n_ticks": 2,
    "salvage_every_n_ticks": 4,

    # ----- Listing requirements -----------------------------------------
    "require_price": True,
    "closing_soon_hours": _OVERRIDES.get("closing_soon_hours", 24),

    # ----- Source priority weights --------------------------------------
    "source_weights": _OVERRIDES.get("source_weights", {
        "craigslist":  1.5,
        "marketplace": 1.4,
        "email_imap":  1.3,
        "ebay":        1.0,
        "marketcheck": 0.9,
        "govdeals":    0.95,
        "bat":         0.7,
        "hemmings":    0.7,
    }),

    # ----- External sharing -------------------------------------------
    # If share_password is set, the dashboard requires HTTP Basic Auth.
    # Leave share_username/share_password BLANK to keep dashboard open
    # (only safe on localhost). Set them BEFORE enabling ngrok tunnel.
    "share_username": _OVERRIDES.get("share_username", ""),
    "share_password": _OVERRIDES.get("share_password", ""),
    # If share_public is true the dashboard is reachable through the tunnel
    # with NO login at all. Owner opt-in only — set from the dashboard.
    "share_public": _OVERRIDES.get("share_public", False),
    # Quiet hours — local-time window during which text alerts are skipped.
    # Both unset (None) means no quiet hours. Wrap-around windows OK
    # (e.g. start=22 end=7 = quiet from 10pm through 7am).
    "quiet_start_hour": _OVERRIDES.get("quiet_start_hour"),
    "quiet_end_hour":   _OVERRIDES.get("quiet_end_hour"),

    # ----- Text-message alerts via macOS Messages.app -------------------
    "notify_phone":         _OVERRIDES.get("notify_phone", "+17044881208"),
    "notify_text_enabled":  _OVERRIDES.get("notify_text_enabled", True),
    # Only text on alerts whose score >= this. Higher = fewer texts.
    "notify_min_score":     _OVERRIDES.get("notify_min_score", 30),

    # ----- IMAP email digest polling -----------------------------------
    # Setup: turn on saved searches in Marketplace/Nextdoor/OfferUp/eBay,
    # then create a Gmail App Password and paste it here. The sniper will
    # auto-import any matching digest emails on each poll. Fully legal —
    # you're processing your own inbox, no scraping.
    "imap_host":    os.environ.get("EMAIL_IMAP_HOST", "imap.gmail.com"),
    "imap_port":    int(os.environ.get("EMAIL_IMAP_PORT", "993")),
    "imap_user":    os.environ.get("EMAIL_IMAP_USER", _OVERRIDES.get("imap_user", "")),
    "imap_pass":    os.environ.get("EMAIL_IMAP_PASS", _OVERRIDES.get("imap_pass", "")),
    "imap_folder":  os.environ.get("EMAIL_IMAP_FOLDER", "INBOX"),
    "imap_senders": os.environ.get("EMAIL_IMAP_SENDERS",
                       "facebookmail.com,nextdoor.com,offerup.com,ebay.com,craigslist.org"),

    # ----- VIN history provider -----------------------------------------
    "vin_provider": os.environ.get("VIN_PROVIDER", "auto"),
    "bumper_api_key":   os.environ.get("BUMPER_API_KEY", "") or _OVERRIDES.get("bumper_api_key", ""),
    "clearvin_api_key": os.environ.get("CLEARVIN_API_KEY", "") or _OVERRIDES.get("clearvin_api_key", ""),

    # ----- Per-source settings ------------------------------------------
    "sources": {
        "craigslist":     {"enabled": True,
                           "regions": _OVERRIDES.get("craigslist_regions",
                                       ["tuscaloosa", "bham", "montgomery"]),
                           "by_owner": True},
        "ebay":           {"enabled": True,
                           "zip": _OVERRIDES.get("zip", "35401"),
                           "radius_mi": _OVERRIDES.get("radius_mi", 100)},
        "bat":            {"enabled": True},
        "hemmings":       {"enabled": True},
        "govdeals":       {"enabled": True,
                           "states": _OVERRIDES.get("govdeals_states",
                                      ["AL","MS","GA","TN","FL"])},
        "marketcheck":    {"enabled": True,
                           "zip": _OVERRIDES.get("zip", "35401"),
                           "radius_mi": _OVERRIDES.get("radius_mi", 100)},
        "marketplace":    {"enabled": True},
        "email_imap":     {"enabled": True},   # auto-import digest emails (needs IMAP creds)
        # Removed (no working public access): carsandbids, gsa, publicsurplus, copart_live, iaa_live
    },
}
