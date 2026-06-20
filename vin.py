#!/usr/bin/env python3
"""
vin.py — production VIN check.

Honest by default: every piece of data is tagged with where it came from
and how confident we are. If a provider doesn't return title/owner/accident
data, the report says "not available from current provider" — never fakes it.

Two layers:
  1. Free NHTSA decode (works without any setup).
  2. Optional paid history-data provider (Bumper / ClearVin / NMVTIS / etc.)
     configured via VIN_PROVIDER + VIN_PROVIDER_API_KEY env vars.

Key public functions:
  validate_vin(vin)   -> {ok, normalized, errors, region, check_digit_ok}
  decode_vin(vin)     -> NHTSA decoded fields (cached)
  check(vin, listing) -> full report: decode + history + risk + confidence +
                         data_completeness + listing-mismatch
  diagnostics()       -> NHTSA reachable / provider configured/reachable /
                         cache stats / last error / avg response time
  provider_status()   -> {name, configured, key_present, base_url, reachable}

CLI:
   python vin.py 1HGCM82633A004352
   python vin.py 1HGCM82633A004352 --json
   python vin.py --diagnostics
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

import requests

UA = {"User-Agent": "sniper-vin/3.0 (+personal use)"}
TIMEOUT = 20
_LOG = logging.getLogger("sniper.vin")

# ---------- Cache (vin_cache.sqlite, colocated with this file) ------------

_CACHE_PATH = Path(__file__).resolve().parent / "vin_cache.sqlite"
DECODE_TTL_SEC = 30 * 86400   # 30 days
HISTORY_TTL_SEC = 7 * 86400   # 7 days
RECALLS_TTL_SEC = 7 * 86400


def _cache_conn() -> sqlite3.Connection:
    c = sqlite3.connect(_CACHE_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS vin_cache (
        vin TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        ttl_seconds INTEGER NOT NULL,
        provider TEXT,
        PRIMARY KEY (vin, kind)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS vin_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        when_iso TEXT NOT NULL,
        op TEXT NOT NULL,
        provider TEXT,
        success INTEGER NOT NULL,
        ms INTEGER NOT NULL,
        error TEXT
    )""")
    return c


def cache_get(vin: str, kind: str) -> Optional[dict]:
    """Returns cached payload if fresh (fetched_at + ttl > now); else None."""
    try:
        with _cache_conn() as c:
            row = c.execute(
                "SELECT payload, fetched_at, ttl_seconds FROM vin_cache "
                "WHERE vin=? AND kind=?", (vin, kind)).fetchone()
            if not row:
                return None
            fetched = datetime.fromisoformat(row["fetched_at"])
            age = (datetime.now(timezone.utc) - fetched).total_seconds()
            if age > row["ttl_seconds"]:
                return None
            data = json.loads(row["payload"])
            data["_cache_age_seconds"] = int(age)
            return data
    except (sqlite3.Error, ValueError, json.JSONDecodeError) as e:
        _LOG.warning(f"cache_get failed: {e}")
        return None


def cache_put(vin: str, kind: str, payload: dict, ttl_seconds: int,
              provider: Optional[str] = None) -> None:
    try:
        with _cache_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO vin_cache "
                "(vin, kind, payload, fetched_at, ttl_seconds, provider) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (vin, kind, json.dumps(payload, default=str),
                 datetime.now(timezone.utc).isoformat(),
                 int(ttl_seconds), provider))
            c.commit()
    except sqlite3.Error as e:
        _LOG.warning(f"cache_put failed: {e}")


def _record_metric(op: str, provider: Optional[str], success: bool,
                   ms: int, error: Optional[str] = None) -> None:
    try:
        with _cache_conn() as c:
            c.execute(
                "INSERT INTO vin_metrics(when_iso, op, provider, success, ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), op, provider,
                 1 if success else 0, int(ms),
                 (error or "")[:300] if error else None))
            # Prune to last 500 rows so this file doesn't grow forever.
            c.execute(
                "DELETE FROM vin_metrics WHERE id NOT IN ("
                "  SELECT id FROM vin_metrics ORDER BY id DESC LIMIT 500)")
            c.commit()
    except sqlite3.Error:
        pass


def cache_stats() -> dict:
    try:
        with _cache_conn() as c:
            rows = c.execute(
                "SELECT kind, COUNT(*) n, MAX(fetched_at) latest "
                "FROM vin_cache GROUP BY kind").fetchall()
            return {"by_kind": [dict(r) for r in rows],
                    "path": str(_CACHE_PATH)}
    except sqlite3.Error as e:
        return {"error": str(e), "by_kind": []}


# ---------- VIN validation (length, IOQ, check digit) --------------------

# Per ISO 3779 transliteration table. Note I, O, Q are forbidden.
_VIN_VALUES = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9,
}
_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_check_digit(vin: str) -> Optional[str]:
    """Compute the expected check digit (position 9) for a 17-char VIN.
    Returns None if the VIN can't be transliterated."""
    if len(vin) != 17:
        return None
    total = 0
    for i, ch in enumerate(vin):
        v = _VIN_VALUES.get(ch.upper())
        if v is None:
            return None
        total += v * _VIN_WEIGHTS[i]
    rem = total % 11
    return "X" if rem == 10 else str(rem)


def _vin_region(first: str) -> str:
    """Coarse region from the first digit/letter of the WMI."""
    if first in "12345": return "north_america"
    if first in "67":    return "oceania"
    if first in "89":    return "south_america"
    if first in "ABCDEFGH": return "africa"
    if first in "JKLMNPR": return "asia"
    if first in "STUVWXYZ": return "europe"
    return "unknown"


def validate_vin(vin: Any) -> dict:
    """Strict VIN validation. Returns:
        {ok, normalized, errors, region, check_digit, check_digit_ok}
    `check_digit_ok` is None for non-North-American VINs (enforcement varies).
    """
    out = {"ok": False, "normalized": None, "errors": [],
           "region": None, "check_digit_expected": None,
           "check_digit_actual": None, "check_digit_ok": None}
    if vin is None:
        out["errors"].append("VIN is required")
        return out
    s = str(vin).strip().upper()
    out["normalized"] = s
    if len(s) != 17:
        out["errors"].append(f"VIN must be exactly 17 characters (got {len(s)})")
        return out
    bad_chars = [c for c in s if c in "IOQ"]
    if bad_chars:
        out["errors"].append(f"VIN cannot contain I, O, or Q (found: {''.join(sorted(set(bad_chars)))})")
        return out
    non_alnum = [c for c in s if not c.isalnum()]
    if non_alnum:
        out["errors"].append("VIN must be alphanumeric")
        return out
    out["region"] = _vin_region(s[0])
    expected = vin_check_digit(s)
    actual = s[8]
    out["check_digit_expected"] = expected
    out["check_digit_actual"] = actual
    if out["region"] == "north_america":
        out["check_digit_ok"] = (expected == actual)
        if not out["check_digit_ok"]:
            out["errors"].append(
                f"Invalid check digit (position 9): expected '{expected}', got '{actual}'")
            return out
    else:
        # Non-NA VINs aren't required to use the ISO check digit. Still report
        # the comparison so the UI can show "info" instead of "fail".
        out["check_digit_ok"] = None
    out["ok"] = True
    return out


# ---------- Free NHTSA endpoints ------------------------------------------

VPIC_DECODE = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin}?format=json"
RECALLS_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
COMPLAINTS_URL = "https://api.nhtsa.gov/complaints/complaintsByVehicle"
SAFETY_URL = "https://api.nhtsa.gov/SafetyRatings/modelyear/{year}/make/{make}/model/{model}"
SAFETY_DETAIL_URL = "https://api.nhtsa.gov/SafetyRatings/VehicleId/{vid}"

# EPA fuel economy
FE_OPTIONS_URL = "https://www.fueleconomy.gov/ws/rest/vehicle/menu/options?year={year}&make={make}&model={model}"
FE_VEHICLE_URL = "https://www.fueleconomy.gov/ws/rest/vehicle/{id}"

# Wikipedia REST API for vehicle photo
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"


def _looks_like_vin(vin: str) -> bool:
    """Legacy quick-shape check. Use validate_vin() for full validation
    including check digit. Kept for backward-compat with old callers."""
    return bool(vin and len(vin) == 17
                and all(c.isalnum() and c.upper() not in "IOQ" for c in vin))


def decode_vin(vin: str, force_refresh: bool = False) -> dict:
    """NHTSA VIN decode with cache + metrics.

    Raises ValueError if the VIN can't be normalized. Otherwise returns the
    decoded dict (NHTSA-shaped, only non-empty fields kept). Cache TTL is
    DECODE_TTL_SEC; pass force_refresh=True to bypass.
    """
    v = validate_vin(vin)
    if not v["ok"]:
        raise ValueError("; ".join(v["errors"]) or "invalid VIN")
    vin_n = v["normalized"]
    if not force_refresh:
        cached = cache_get(vin_n, "decode")
        if cached is not None:
            return cached
    t0 = time.monotonic()
    err = None
    try:
        r = requests.get(VPIC_DECODE.format(vin=quote(vin_n)),
                         headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        rec = (r.json().get("Results") or [{}])[0]
        clean = {k: vv for k, vv in rec.items() if vv not in ("", None, "0", 0)}
        cache_put(vin_n, "decode", clean, DECODE_TTL_SEC, provider="nhtsa")
        return clean
    except (requests.RequestException, ValueError) as e:
        err = str(e)
        raise
    finally:
        _record_metric("nhtsa_decode", "nhtsa", err is None,
                       int((time.monotonic() - t0) * 1000), err)


def recalls(year: int, make: str, model: str) -> list[dict]:
    r = requests.get(RECALLS_URL, params={"modelYear": year, "make": make, "model": model},
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("results", []) or []


def complaints(year: int, make: str, model: str) -> list[dict]:
    r = requests.get(COMPLAINTS_URL, params={"modelYear": year, "make": make, "model": model},
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("results", []) or []


def safety_ratings(year: int, make: str, model: str) -> dict:
    """NHTSA 5-Star Safety Ratings. Returns averaged ratings across trims."""
    try:
        r = requests.get(SAFETY_URL.format(year=year, make=quote(make), model=quote(model)),
                         headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        results = r.json().get("Results", []) or []
        if not results:
            return {}
        # Pull detail for first result (typical trim)
        vid = results[0].get("VehicleId")
        if not vid:
            return {}
        r2 = requests.get(SAFETY_DETAIL_URL.format(vid=vid), headers=UA, timeout=TIMEOUT)
        r2.raise_for_status()
        d = (r2.json().get("Results", []) or [{}])[0]
        def _f(k):
            try:
                v = d.get(k)
                return float(v) if v not in (None, "", "Not Rated") else None
            except (TypeError, ValueError):
                return None
        return {
            "overall":          _f("OverallRating"),
            "frontal":          _f("OverallFrontCrashRating"),
            "side":             _f("OverallSideCrashRating"),
            "rollover":         _f("RolloverRating"),
            "frontal_driver":   _f("FrontCrashDriversideRating"),
            "frontal_passenger":_f("FrontCrashPassengersideRating"),
            "side_driver":      _f("SideCrashDriversideRating"),
            "side_passenger":   _f("SideCrashPassengersideRating"),
            "vehicle_desc":     d.get("VehicleDescription"),
        }
    except (requests.RequestException, ValueError):
        return {}


def fuel_economy(year: int, make: str, model: str) -> dict:
    """EPA fuel economy via fueleconomy.gov. Returns first matching trim's MPG."""
    try:
        r = requests.get(FE_OPTIONS_URL.format(year=year, make=quote(make), model=quote(model)),
                         headers={**UA, "Accept": "application/json"}, timeout=TIMEOUT)
        if r.status_code != 200:
            return {}
        opts = r.json().get("menuItem") or []
        if isinstance(opts, dict): opts = [opts]
        if not opts:
            return {}
        vid = opts[0].get("value")
        r2 = requests.get(FE_VEHICLE_URL.format(id=vid),
                          headers={**UA, "Accept": "application/json"}, timeout=TIMEOUT)
        d = r2.json() if r2.status_code == 200 else {}
        return {
            "city":     d.get("city08"),       # mpg city
            "highway":  d.get("highway08"),    # mpg highway
            "combined": d.get("comb08"),       # combined
            "fuel_type":d.get("fuelType1"),
            "annual_fuel_cost": d.get("fuelCost08"),  # $/year
            "vehicle_desc": " ".join(filter(None, [d.get("year",""),
                            d.get("make",""), d.get("model",""), d.get("trany","")])).strip(),
        }
    except (requests.RequestException, ValueError):
        return {}


def vehicle_photo(year: int, make: str, model: str) -> str | None:
    """Best-effort: Wikipedia REST API for a canonical vehicle photo URL.

    Tries "Year Make Model", then "Make Model", then "Make Model (generation)".
    Returns the thumbnail URL or None.
    """
    candidates = [
        f"{make}_{model}".replace(" ", "_"),
        f"{year}_{make}_{model}".replace(" ", "_"),
        f"{make.title()}_{model.title()}".replace(" ", "_"),
    ]
    for slug in candidates:
        try:
            r = requests.get(WIKI_SUMMARY.format(slug=quote(slug)),
                             headers=UA, timeout=10)
            if r.status_code != 200:
                continue
            data = r.json()
            thumb = (data.get("thumbnail") or {}).get("source")
            if thumb:
                return thumb
            orig = (data.get("originalimage") or {}).get("source")
            if orig:
                return orig
        except (requests.RequestException, ValueError):
            continue
    return None


# ---------- Paid history-data providers -----------------------------------
# Both providers return very different shapes — we normalize to:
#    {title_status, title_brand, owners, maintenance_records, last_service,
#     accidents, odometer_readings, raw}

def _from_config(key: str) -> str:
    try:
        from config import CONFIG
        return CONFIG.get(key, "")
    except Exception:
        return ""


def bumper_report(vin: str) -> dict | None:
    key = os.environ.get("BUMPER_API_KEY") or _from_config("bumper_api_key")
    if not key:
        return None
    url = f"https://api.bumper.com/v1/vehicle/{quote(vin)}"
    try:
        r = requests.get(url, headers={**UA, "X-API-Key": key}, timeout=30)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        return {"_error": str(e)}
    title = d.get("title", {}) or {}
    owners = d.get("owners", []) or []
    history = d.get("service_records", []) or []
    accidents = d.get("accidents", []) or []
    odo = d.get("odometer_readings", []) or []
    return {
        "_provider": "bumper",
        "title_status": (title.get("brand_clean") and "clean") or title.get("status") or "unknown",
        "title_brand": title.get("brand"),
        "owners": len(owners) or d.get("owner_count"),
        "maintenance_records": len(history),
        "last_service": history[0] if history else None,
        "accidents": len(accidents),
        "odometer_readings": odo,
        "raw": d,
    }


def clearvin_report(vin: str) -> dict | None:
    key = os.environ.get("CLEARVIN_API_KEY") or _from_config("clearvin_api_key")
    if not key:
        return None
    url = f"https://www.clearvin.com/api/v1/vin/{quote(vin)}/full?api_key={key}"
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        return {"_error": str(e)}
    title = d.get("title_records", [{}])[0] if d.get("title_records") else {}
    return {
        "_provider": "clearvin",
        "title_status": title.get("brand", "unknown") or "unknown",
        "title_brand": title.get("brand"),
        "owners": d.get("owner_count") or len(d.get("registration_records", [])),
        "maintenance_records": len(d.get("service_records", []) or []),
        "last_service": (d.get("service_records") or [None])[0],
        "accidents": len(d.get("accident_records", []) or []),
        "odometer_readings": d.get("odometer_records", []),
        "raw": d,
    }


def history_report(vin: str) -> dict:
    """Returns the normalized history report from whichever provider is configured."""
    provider_pref = (_from_config("vin_provider") or "auto").lower()
    if provider_pref == "bumper":
        return bumper_report(vin) or {"_provider": None}
    if provider_pref == "clearvin":
        return clearvin_report(vin) or {"_provider": None}
    # auto: prefer bumper (cheaper), fall back to clearvin
    return bumper_report(vin) or clearvin_report(vin) or {"_provider": None}


# ---------- Top-level report ----------------------------------------------

def report(vin: str) -> dict:
    out: dict = {"vin": vin, "errors": []}

    # 1. Decode (free)
    try:
        decoded = decode_vin(vin)
        # Combine trim signals — NHTSA splits across Trim, Trim2, Series, Series2
        trim_parts = [decoded.get(k) for k in ("Trim", "Trim2", "Series", "Series2")]
        trim_combined = " ".join(filter(None, [t for t in trim_parts if t]))

        # Combine engine signals
        engine_parts = []
        if decoded.get("DisplacementL"):       engine_parts.append(f"{decoded['DisplacementL']}L")
        if decoded.get("EngineCylinders"):     engine_parts.append(f"{decoded['EngineCylinders']}cyl")
        if decoded.get("EngineConfiguration"): engine_parts.append(decoded["EngineConfiguration"])
        if decoded.get("Turbo") == "Yes":      engine_parts.append("turbo")
        if decoded.get("ValveTrainDesign"):    engine_parts.append(decoded["ValveTrainDesign"])
        engine_full = " ".join(engine_parts)

        out["decoded"] = {
            "year": decoded.get("ModelYear"), "make": decoded.get("Make"),
            "model": decoded.get("Model"),
            "trim": trim_combined or decoded.get("Trim") or decoded.get("Series"),
            "trim_parts": {k: decoded.get(k) for k in ("Trim", "Trim2", "Series", "Series2")
                           if decoded.get(k)},
            "vehicle_type": decoded.get("VehicleType"),
            "body": decoded.get("BodyClass"),
            "doors": decoded.get("Doors"),
            "engine_cylinders": decoded.get("EngineCylinders"),
            "engine_displacement_l": decoded.get("DisplacementL"),
            "engine_displacement_cc": decoded.get("DisplacementCC"),
            "engine_hp": decoded.get("EngineHP"),
            "engine_config": decoded.get("EngineConfiguration"),  # I4 / V6 / V8
            "engine_kw": decoded.get("EngineKW"),
            "engine_full": engine_full,
            "turbo": decoded.get("Turbo"),
            "fuel": decoded.get("FuelTypePrimary"),
            "fuel_secondary": decoded.get("FuelTypeSecondary"),
            "transmission": decoded.get("TransmissionStyle"),
            "transmission_speeds": decoded.get("TransmissionSpeeds"),
            "drive_type": decoded.get("DriveType"),
            "plant": " / ".join(filter(None, [decoded.get("PlantCity"),
                       decoded.get("PlantState"), decoded.get("PlantCountry")])),
            "plant_company": decoded.get("PlantCompanyName"),
            "manufacturer": decoded.get("ManufacturerName"),
            "gvwr": decoded.get("GVWR"),
            "curb_weight_lb": decoded.get("CurbWeightLB"),
            "wheel_base_inches": decoded.get("WheelBaseShort") or decoded.get("WheelBaseLong"),
            "abs_brakes": decoded.get("ABS"), "esc": decoded.get("ESC"),
            "tpms": decoded.get("TPMS"), "airbags": decoded.get("AirBagLocFront"),
            "seatbelt_type": decoded.get("SeatBeltsAll"),
            "blind_spot": decoded.get("BlindSpotMon"),
            "lane_keep": decoded.get("LaneKeepSystem"),
            "adaptive_cruise": decoded.get("AdaptiveCruiseControl"),
            "auto_brake": decoded.get("PedestrianAutomaticEmergencyBraking"),
            "backup_camera": decoded.get("RearVisibilitySystem"),
            "note": decoded.get("Note"),  # NHTSA freeform notes
        }
        # Capture any error from NHTSA itself
        if decoded.get("ErrorCode") and decoded.get("ErrorCode") != "0":
            out["errors"].append(
                f"NHTSA error code {decoded['ErrorCode']}: {decoded.get('ErrorText','')}")
    except Exception as e:
        out["errors"].append(f"decode failed: {e}")
        out["decoded"] = {}

    d = out["decoded"]

    # 2. PRIORITY: Title / Owners / Maintenance (paid provider)
    hist = history_report(vin)
    out["history"] = hist
    out["top_fields"] = {
        "title": {
            "value": (hist.get("title_status") or "—").lower(),
            "is_clean": (hist.get("title_status") or "").lower() in ("clean", "clear"),
            "brand": hist.get("title_brand"),
            "configured": bool(hist.get("_provider")),
        },
        "owners": {
            "value": hist.get("owners"),
            "configured": bool(hist.get("_provider")),
        },
        "maintenance": {
            "records": hist.get("maintenance_records"),
            "last_service": hist.get("last_service"),
            "configured": bool(hist.get("_provider")),
        },
        "accidents": {
            "count": hist.get("accidents"),
            "configured": bool(hist.get("_provider")),
        },
    }

    # 2b. Vehicle photo via Wikipedia (free)
    if d.get("year") and d.get("make") and d.get("model"):
        try:
            out["photo_url"] = vehicle_photo(int(d["year"]), d["make"], d["model"])
        except Exception:
            out["photo_url"] = None

        # NHTSA safety star ratings + EPA fuel economy
        try:
            out["safety"] = safety_ratings(int(d["year"]), d["make"], d["model"])
        except Exception:
            out["safety"] = {}
        try:
            out["fuel_economy"] = fuel_economy(int(d["year"]), d["make"], d["model"])
        except Exception:
            out["fuel_economy"] = {}

    # 3. NHTSA recalls + complaints (free)
    if d.get("year") and d.get("make") and d.get("model"):
        try:
            yr = int(d["year"])
            recs = recalls(yr, d["make"], d["model"])
            out["recalls"] = [{
                "campaign": r.get("NHTSACampaignNumber"),
                "summary": r.get("Summary"),
                "consequence": r.get("Consequence"),
                "remedy": r.get("Remedy"),
                "component": r.get("Component"),
                "report_date": r.get("ReportReceivedDate"),
            } for r in recs]
        except Exception as e:
            out["errors"].append(f"recalls failed: {e}")
            out["recalls"] = []
        try:
            cmps = complaints(yr, d["make"], d["model"])
            buckets: dict[str, int] = {}
            for c in cmps:
                comp = (c.get("components") or "Unknown").split(",")[0].strip() or "Unknown"
                buckets[comp] = buckets.get(comp, 0) + 1
            out["complaint_summary"] = sorted(
                [{"component": k, "count": v} for k, v in buckets.items()],
                key=lambda x: -x["count"])[:10]
            out["complaints_total"] = len(cmps)
        except Exception as e:
            out["errors"].append(f"complaints failed: {e}")
            out["complaints_total"] = 0

    # 4. Setup hint when no history provider is configured
    if not hist.get("_provider"):
        out["setup_hint"] = {
            "needed_for": ["title", "owners", "maintenance"],
            "options": [
                {"name": "Bumper.com", "url": "https://www.bumper.com/api",
                 "cost": "$1 first report, ~$25/mo unlimited", "env": "BUMPER_API_KEY"},
                {"name": "ClearVin",   "url": "https://www.clearvin.com/api",
                 "cost": "~$2/VIN", "env": "CLEARVIN_API_KEY"},
            ],
            "instructions": "Edit config.py (bumper_api_key) OR set env var, then restart.",
        }

    return out


def print_report(r: dict) -> None:
    d = r.get("decoded") or {}
    tf = r.get("top_fields") or {}
    print(f"\n=== VIN {r['vin']} ===")

    # PRIORITY block
    title = tf.get("title", {})
    owners = tf.get("owners", {})
    maint = tf.get("maintenance", {})
    print(f"\n  [1] TITLE:        ", end="")
    if title.get("configured"):
        flag = "✓ CLEAN" if title["is_clean"] else f"⚠ {title['value'].upper()}"
        brand = f" ({title['brand']})" if title.get("brand") else ""
        print(f"{flag}{brand}")
    else:
        print("set up — add a Bumper or ClearVin API key")
    print(f"  [2] OWNERS:       ", end="")
    print(f"{owners['value']}" if owners.get("configured") and owners.get("value") is not None
          else "set up")
    print(f"  [3] MAINTENANCE:  ", end="")
    if maint.get("configured"):
        n = maint.get("records") or 0
        last = maint.get("last_service")
        last_s = f" — last: {last.get('date','?')} {last.get('description','')[:50]}" if last else ""
        print(f"{n} record(s){last_s}")
    else:
        print("set up")

    # Decoded
    print(f"\n  {d.get('year','?')} {d.get('make','?')} {d.get('model','?')} {d.get('trim') or ''}")
    print(f"  Body:    {d.get('body','?')}")
    print(f"  Engine:  {d.get('engine_displacement_l','?')}L "
          f"{d.get('engine_cylinders','?')}cyl · {d.get('engine_hp','?')}hp · {d.get('fuel','?')}")
    print(f"  Trans:   {d.get('transmission','?')} · {d.get('drive_type','?')}")
    print(f"  Plant:   {d.get('plant','?')}")
    print(f"  Mfr:     {d.get('manufacturer','?')}")

    # Safety
    safety = []
    for k, label in [("abs_brakes","ABS"),("esc","ESC"),("tpms","TPMS"),
                     ("blind_spot","Blind-spot"),("lane_keep","Lane-keep"),
                     ("adaptive_cruise","Adaptive cruise"),("auto_brake","Auto-brake"),
                     ("backup_camera","Backup cam")]:
        if d.get(k): safety.append(label)
    if safety:
        print(f"  Safety:  {', '.join(safety)}")

    recs = r.get("recalls") or []
    print(f"\n  Open NHTSA recalls: {len(recs)}")
    for rec in recs[:5]:
        print(f"    - {rec.get('component','?')}: {rec.get('summary','')[:120]}")

    csum = r.get("complaint_summary") or []
    if csum:
        print(f"\n  Top complaint categories ({r.get('complaints_total',0)} total):")
        for c in csum[:5]:
            print(f"    - {c['component']:30s}  {c['count']}")

    if r.get("errors"):
        print("\n  Warnings:", "; ".join(r["errors"]))


# =========================================================================
# v3 production VIN check — clean, honest, scored.
# Everything below this line is the new spec. The functions above are kept
# for backward-compat with existing /api/vin and CLI callers.
# =========================================================================

# ---------- Provider adapters --------------------------------------------
# All adapters return a normalized history dict (see NORMALIZED_KEYS below)
# or None if not configured. They never raise; on transport errors they
# return {"_error": "...", "_provider": <name>}.

NORMALIZED_BRANDS = (
    "salvage", "rebuilt", "flood", "fire", "lemon", "junk", "hail",
    "theft_open", "theft_recovered",
    "not_actual_mileage", "odometer_rollback", "odometer_inconsistent",
    "total_loss", "export",
)

_BRAND_KEYWORDS = {
    "salvage":               ["salvage"],
    "rebuilt":               ["rebuilt", "reconstructed", "prior salvage"],
    "flood":                 ["flood", "water damage"],
    "fire":                  ["fire damage"],
    "lemon":                 ["lemon", "manufacturer buyback"],
    "junk":                  ["junk", "nonrepairable", "non-repairable", "scrap"],
    "hail":                  ["hail"],
    "theft_open":            ["theft"],
    "theft_recovered":       ["recovered theft"],
    "not_actual_mileage":    ["not actual mileage", "tmu", "true mileage unknown"],
    "odometer_rollback":     ["odometer rollback", "rollback"],
    "odometer_inconsistent": ["odometer discrepancy", "inconsistent odometer"],
    "total_loss":            ["total loss"],
    "export":                ["export only"],
}


def _detect_brands(blob: str) -> dict:
    """Best-effort keyword scan over a free-text blob (brand strings, notes).
    Returns {brand_key: True} for every match. Conservative — only flags
    on direct keyword hits. Lower-case the blob before calling."""
    out = {b: False for b in NORMALIZED_BRANDS}
    if not blob:
        return out
    lo = blob.lower()
    for brand, kws in _BRAND_KEYWORDS.items():
        for kw in kws:
            if kw in lo:
                out[brand] = True
                break
    return out


def _empty_history(provider_name: Optional[str], reason: str = "") -> dict:
    """Honest empty shape — every field marked unavailable. Used when no
    provider is configured or the provider returned nothing."""
    return {
        "_provider": provider_name,
        "_configured": bool(provider_name),
        "_fetched_at": None,
        "_error": None,
        "_reason": reason or None,
        "title_status": None,
        "title_brand": None,
        "title_state": None,
        "title_date": None,
        "brands": {b: None for b in NORMALIZED_BRANDS},
        "owners": None,
        "owner_history": None,
        "accidents": None,
        "maintenance_records": None,
        "last_service": None,
        "odometer_readings": None,
        "available_fields": [],
        "unavailable_fields": [
            "title_status", "title_brand", "owners", "accidents",
            "maintenance_records", "odometer_readings",
        ],
    }


def _normalize_bumper(d: dict) -> dict:
    """Turn a Bumper raw response into the normalized history shape."""
    title = d.get("title", {}) or {}
    owners = d.get("owners", []) or []
    history = d.get("service_records", []) or []
    accidents = d.get("accidents", []) or []
    odo = d.get("odometer_readings", []) or []
    brand_text = " ".join(filter(None, [
        title.get("brand"), title.get("status"),
        " ".join(b.get("description", "") for b in (d.get("brands") or [])),
    ]))
    brands = _detect_brands(brand_text)
    title_status = (title.get("status") or "").lower() or None
    if not title_status:
        title_status = "clean" if title.get("brand_clean") else None
    avail = []
    if title_status:        avail.append("title_status")
    if title.get("brand"):  avail.append("title_brand")
    if owners or d.get("owner_count") is not None: avail.append("owners")
    if history:             avail.append("maintenance_records")
    if accidents:           avail.append("accidents")
    if odo:                 avail.append("odometer_readings")
    unavail = [f for f in (
        "title_status", "title_brand", "owners", "maintenance_records",
        "accidents", "odometer_readings") if f not in avail]
    return {
        "_provider": "bumper", "_configured": True,
        "_fetched_at": datetime.now(timezone.utc).isoformat(),
        "_error": None, "_reason": None,
        "title_status": title_status,
        "title_brand": title.get("brand"),
        "title_state": title.get("state"),
        "title_date": title.get("last_title_date") or title.get("date"),
        "brands": brands,
        "owners": (len(owners) if owners else d.get("owner_count")),
        "owner_history": owners or None,
        "accidents": len(accidents) if accidents else None,
        "maintenance_records": len(history) if history else None,
        "last_service": history[0] if history else None,
        "odometer_readings": odo or None,
        "available_fields": avail,
        "unavailable_fields": unavail,
    }


def _normalize_clearvin(d: dict) -> dict:
    title_records = d.get("title_records") or []
    title = title_records[0] if title_records else {}
    brand_text = " ".join(filter(None, [
        title.get("brand"),
        " ".join((tr.get("brand") or "") for tr in title_records),
    ]))
    brands = _detect_brands(brand_text)
    owners_count = d.get("owner_count")
    reg = d.get("registration_records") or []
    if owners_count is None and reg:
        owners_count = len(reg)
    svc = d.get("service_records") or []
    acc = d.get("accident_records") or []
    odo = d.get("odometer_records") or []
    avail = []
    if title.get("brand"):  avail.extend(["title_status", "title_brand"])
    if owners_count is not None: avail.append("owners")
    if svc: avail.append("maintenance_records")
    if acc: avail.append("accidents")
    if odo: avail.append("odometer_readings")
    unavail = [f for f in (
        "title_status", "title_brand", "owners", "maintenance_records",
        "accidents", "odometer_readings") if f not in avail]
    return {
        "_provider": "clearvin", "_configured": True,
        "_fetched_at": datetime.now(timezone.utc).isoformat(),
        "_error": None, "_reason": None,
        "title_status": (title.get("brand") or "").lower() or None,
        "title_brand": title.get("brand"),
        "title_state": title.get("state"),
        "title_date": title.get("date"),
        "brands": brands,
        "owners": owners_count,
        "owner_history": reg or None,
        "accidents": len(acc) if acc else None,
        "maintenance_records": len(svc) if svc else None,
        "last_service": svc[0] if svc else None,
        "odometer_readings": odo or None,
        "available_fields": avail,
        "unavailable_fields": unavail,
    }


def _normalize_generic(d: dict, provider_name: str) -> dict:
    """For a custom provider via VIN_PROVIDER_BASE_URL. We don't know its
    shape, so we scan the JSON for likely fields and treat everything else as
    unavailable. Never invents data — only reflects what's in the response."""
    blob = json.dumps(d).lower() if d else ""
    brands = _detect_brands(blob)
    # Try a handful of common field names — but ONLY use them if literally
    # present. Otherwise mark unavailable.
    def _pick(*keys):
        for k in keys:
            if isinstance(d, dict) and k in d and d[k] not in (None, "", []):
                return d[k]
        return None
    title_status = _pick("title_status", "title", "titleBrand", "brand")
    if isinstance(title_status, dict):
        title_status = title_status.get("status") or title_status.get("brand")
    owners = _pick("owners", "owner_count", "previousOwners")
    if isinstance(owners, list):
        owners = len(owners)
    avail, unavail = [], []
    for f, val in (
        ("title_status", title_status),
        ("title_brand", _pick("title_brand", "brand")),
        ("owners", owners),
        ("maintenance_records", _pick("service_records", "maintenance")),
        ("accidents", _pick("accidents", "accident_count")),
        ("odometer_readings", _pick("odometer_readings", "odometer")),
    ):
        (avail if val not in (None, "", []) else unavail).append(f)
    return {
        "_provider": provider_name, "_configured": True,
        "_fetched_at": datetime.now(timezone.utc).isoformat(),
        "_error": None, "_reason": None,
        "title_status": (str(title_status).lower() if title_status else None),
        "title_brand": _pick("title_brand", "brand"),
        "title_state": _pick("title_state", "state"),
        "title_date": _pick("title_date", "date"),
        "brands": brands,
        "owners": owners,
        "owner_history": None,
        "accidents": (len(_pick("accidents") or []) if isinstance(_pick("accidents"), list)
                       else _pick("accident_count")),
        "maintenance_records": (len(_pick("service_records") or [])
                                 if isinstance(_pick("service_records"), list) else None),
        "last_service": None,
        "odometer_readings": _pick("odometer_readings", "odometer"),
        "available_fields": avail,
        "unavailable_fields": unavail,
    }


# Provider names accepted by VIN_PROVIDER. "auto" picks the first configured.
_KNOWN_PROVIDERS = ("none", "auto", "bumper", "clearvin",
                    "nmvtis", "autocheck", "carfax", "generic")


def _provider_choice() -> str:
    return (os.environ.get("VIN_PROVIDER")
            or _from_config("vin_provider") or "auto").lower().strip()


def _provider_key(name: str) -> str:
    """Resolve the API key for a given provider name. Env > config."""
    if name == "bumper":
        return os.environ.get("BUMPER_API_KEY") or _from_config("bumper_api_key") or ""
    if name == "clearvin":
        return os.environ.get("CLEARVIN_API_KEY") or _from_config("clearvin_api_key") or ""
    # Generic / nmvtis / autocheck / carfax all use the unified env var.
    return os.environ.get("VIN_PROVIDER_API_KEY") or _from_config("vin_provider_api_key") or ""


def _provider_base_url(name: str) -> str:
    if name == "bumper":   return "https://api.bumper.com/v1/vehicle"
    if name == "clearvin": return "https://www.clearvin.com/api/v1/vin"
    return (os.environ.get("VIN_PROVIDER_BASE_URL")
            or _from_config("vin_provider_base_url") or "")


def _fetch_provider(name: str, vin: str) -> dict:
    """Hit a paid provider. Always returns the normalized shape (with
    _error set if the call failed). Never raises."""
    key = _provider_key(name)
    base = _provider_base_url(name)
    if not key:
        return {**_empty_history(name, "no API key set"), "_error": "no api key"}
    t0 = time.monotonic()
    err = None
    try:
        if name == "bumper":
            url = f"{base}/{quote(vin)}"
            r = requests.get(url, headers={**UA, "X-API-Key": key}, timeout=30)
            r.raise_for_status()
            return _normalize_bumper(r.json() or {})
        if name == "clearvin":
            url = f"{base}/{quote(vin)}/full?api_key={quote(key)}"
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            return _normalize_clearvin(r.json() or {})
        # nmvtis / autocheck / carfax / generic — all go through the
        # custom-URL path. The caller MUST set VIN_PROVIDER_BASE_URL.
        if not base:
            out = _empty_history(name, "VIN_PROVIDER_BASE_URL not configured")
            out["_error"] = "no base url"
            return out
        # Inject {vin} placeholder if present, else append.
        url = base.replace("{vin}", quote(vin)) if "{vin}" in base else f"{base.rstrip('/')}/{quote(vin)}"
        r = requests.get(url, headers={**UA, "Authorization": f"Bearer {key}"}, timeout=30)
        r.raise_for_status()
        try:
            raw = r.json()
        except ValueError as e:
            err = f"non-JSON response: {e}"
            out = _empty_history(name, "provider returned non-JSON")
            out["_error"] = err
            return out
        return _normalize_generic(raw, name)
    except requests.RequestException as e:
        err = str(e)
        out = _empty_history(name, f"transport error: {e}")
        out["_error"] = err
        return out
    except (ValueError, json.JSONDecodeError) as e:
        # Malformed JSON from a paid provider — surface, don't fake.
        err = f"malformed response: {e}"
        out = _empty_history(name, "provider returned malformed JSON")
        out["_error"] = err
        return out
    finally:
        _record_metric("provider", name, err is None,
                       int((time.monotonic() - t0) * 1000), err)


def history(vin: str, force_refresh: bool = False) -> dict:
    """Get paid-provider history for a VIN. Honors VIN_PROVIDER choice,
    caches results per HISTORY_TTL_SEC, and returns the honest empty shape
    if no provider is configured."""
    choice = _provider_choice()
    if choice == "none":
        out = _empty_history(None, "VIN_PROVIDER=none — paid history disabled")
        return out
    # "auto" tries bumper, then clearvin
    candidates = ([choice] if choice in _KNOWN_PROVIDERS and choice != "auto"
                  else ["bumper", "clearvin"])
    if not force_refresh:
        cached = cache_get(vin, "history")
        if cached:
            return cached
    last: Optional[dict] = None
    for name in candidates:
        if name in ("none", "auto"):
            continue
        if not _provider_key(name) and name not in ("generic", "nmvtis",
                                                     "autocheck", "carfax"):
            continue
        result = _fetch_provider(name, vin)
        last = result
        if not result.get("_error") and result.get("_configured"):
            cache_put(vin, "history", result, HISTORY_TTL_SEC, provider=name)
            return result
    if last is not None:
        return last
    return _empty_history(None, "no provider configured")


# ---------- Listing-mismatch detection -----------------------------------

def compare_listing(decoded: dict, listing: Optional[dict]) -> dict:
    """Compare NHTSA-decoded VIN data against listing-supplied year/make/model
    /trim. Returns mismatches and a single high-level message. Never raises."""
    out = {"checked": False, "mismatches": [], "message": None, "severity": "none"}
    if not listing or not decoded:
        return out
    out["checked"] = True

    def _norm(x):
        return str(x).strip().lower() if x not in (None, "") else None

    decoded_year  = _norm(decoded.get("ModelYear") or decoded.get("year"))
    decoded_make  = _norm(decoded.get("Make") or decoded.get("make"))
    decoded_model = _norm(decoded.get("Model") or decoded.get("model"))
    decoded_trim  = _norm(decoded.get("Trim") or decoded.get("trim"))

    listing_year  = _norm(listing.get("year"))
    listing_make  = _norm(listing.get("make"))
    listing_model = _norm(listing.get("model"))
    listing_trim  = _norm(listing.get("trim"))

    if listing_year and decoded_year and listing_year != decoded_year:
        out["mismatches"].append({"field": "year", "listing": listing_year,
                                   "vin": decoded_year})
    if listing_make and decoded_make and listing_make != decoded_make:
        # Tolerate common aliases
        aliases = {"chevy": "chevrolet", "vw": "volkswagen",
                   "mercedes": "mercedes-benz", "mercedes-benz": "mercedes"}
        if aliases.get(listing_make) != decoded_make and \
           aliases.get(decoded_make) != listing_make:
            out["mismatches"].append({"field": "make", "listing": listing_make,
                                       "vin": decoded_make})
    if listing_model and decoded_model and listing_model != decoded_model:
        # Model strings get fuzzy: "x3 m40i" vs "x3" — only flag if neither
        # is a prefix/substring of the other.
        if (listing_model not in decoded_model
                and decoded_model not in listing_model):
            out["mismatches"].append({"field": "model", "listing": listing_model,
                                       "vin": decoded_model})
    if listing_trim and decoded_trim and listing_trim != decoded_trim:
        if (listing_trim not in decoded_trim
                and decoded_trim not in listing_trim):
            out["mismatches"].append({"field": "trim", "listing": listing_trim,
                                       "vin": decoded_trim})

    if out["mismatches"]:
        severities = {"year": "high", "make": "high",
                      "model": "medium", "trim": "low"}
        sev = max((severities.get(m["field"], "low")
                   for m in out["mismatches"]),
                  key=lambda s: ["none", "low", "medium", "high"].index(s))
        out["severity"] = sev
        parts = [f"{m['field']} (listing {m['listing']!r} vs VIN {m['vin']!r})"
                 for m in out["mismatches"]]
        ymm = " ".join(p for p in (decoded_year, decoded_make, decoded_model) if p)
        out["message"] = f"VIN decodes as {ymm}; listing claims " \
                          + ", ".join(parts)
    return out


# ---------- Risk + confidence scoring ------------------------------------

RISK_LABELS = (
    (20, "clean"),
    (45, "caution"),
    (70, "high_risk"),
    (100, "avoid"),
)


def _label_for(score: int) -> str:
    for cap, lbl in RISK_LABELS:
        if score <= cap:
            return lbl
    return "avoid"


def compute_risk(decoded: dict, history_data: dict,
                 recalls_list: list, mismatch: dict) -> dict:
    """Returns risk score (0-100), label, breakdown, confidence (0-100),
    and data_completeness. Never invents data — missing fields lower
    confidence but don't raise risk."""
    score = 0
    breakdown: list[dict] = []

    def add(reason: str, points: int):
        nonlocal score
        score = max(0, min(100, score + points))
        breakdown.append({"reason": reason, "points": points})

    brands = (history_data or {}).get("brands") or {}
    # Brand-driven risk
    if brands.get("salvage"):                add("Salvage title", 60)
    if brands.get("junk"):                   add("Junk / non-repairable", 60)
    if brands.get("rebuilt"):                add("Rebuilt title", 35)
    if brands.get("flood"):                  add("Flood damage", 50)
    if brands.get("fire"):                   add("Fire damage", 45)
    if brands.get("lemon"):                  add("Lemon / manufacturer buyback", 40)
    if brands.get("hail"):                   add("Hail damage", 15)
    if brands.get("theft_open"):             add("Active theft record", 50)
    elif brands.get("theft_recovered"):      add("Recovered theft", 10)
    if brands.get("odometer_rollback"):      add("Odometer rollback", 50)
    if brands.get("not_actual_mileage"):     add("Not actual mileage (TMU)", 35)
    if brands.get("odometer_inconsistent"):  add("Odometer inconsistency", 25)
    if brands.get("total_loss"):             add("Total loss", 45)
    if brands.get("export"):                 add("Export only", 5)

    # Listing mismatch
    if mismatch and mismatch.get("mismatches"):
        for m in mismatch["mismatches"]:
            if m["field"] == "year":  add("Year mismatch (listing vs VIN)", 25)
            if m["field"] == "make":  add("Make mismatch (listing vs VIN)", 40)
            if m["field"] == "model": add("Model mismatch (listing vs VIN)", 20)

    # Owners (only if we actually have a number)
    owners = (history_data or {}).get("owners")
    if isinstance(owners, int):
        if owners >= 5:   add(f"{owners} prior owners", 10)
        elif owners == 4: add("4 prior owners", 5)

    # Open recalls (cap)
    open_recalls = [r for r in (recalls_list or [])
                    if not r.get("remedy")]  # no remedy = still open
    if open_recalls:
        pts = min(15, 3 * len(open_recalls))
        add(f"{len(open_recalls)} open recall(s)", pts)

    # ---------- Confidence + data completeness ---------------------------
    completeness: list[dict] = []
    confidence = 100

    def field(name: str, present: bool, source: str,
              note: Optional[str] = None):
        completeness.append({
            "field": name,
            "status": "present" if present else "not_available",
            "source": source if present else "—",
            "note": note,
        })

    field("vin_decode", bool(decoded), "NHTSA vPIC",
          None if decoded else "decode failed / unreachable")
    if not decoded:
        confidence -= 25

    provider = (history_data or {}).get("_provider")
    configured = bool((history_data or {}).get("_configured"))
    provider_err = (history_data or {}).get("_error")

    field("title_status", bool((history_data or {}).get("title_status")),
          provider or "—",
          None if configured else "Not available from current provider")
    field("title_brand", bool((history_data or {}).get("title_brand")),
          provider or "—",
          None if configured else "Not available from current provider")
    field("owners", (history_data or {}).get("owners") is not None,
          provider or "—",
          None if configured else "Not available from current provider")
    field("accidents", (history_data or {}).get("accidents") is not None,
          provider or "—",
          None if configured else "Not available from current provider")
    field("maintenance", (history_data or {}).get("maintenance_records") is not None,
          provider or "—",
          None if configured else "Not available from current provider")
    field("odometer_readings",
          bool((history_data or {}).get("odometer_readings")),
          provider or "—",
          None if configured else "Not available from current provider")
    field("recalls", recalls_list is not None, "NHTSA",
          None if recalls_list is not None else "recall lookup failed")

    if not configured:
        # Honest: we don't have title/brand/etc data. Big confidence hit but
        # no risk hit — "we don't know" must not look like "it's bad".
        confidence -= 55
    elif provider_err:
        confidence -= 35
    else:
        # Per-missing-field penalties
        for slot in ("title_status", "owners", "odometer_readings"):
            if (history_data or {}).get(slot) in (None, [], {}):
                confidence -= 5

    if mismatch and mismatch.get("checked") and not mismatch.get("mismatches"):
        # Listing-comparison ran and agreed — small confidence boost
        confidence = min(100, confidence + 5)

    confidence = max(0, min(100, confidence))

    return {
        "risk_score": score,
        "risk_label": _label_for(score),
        "risk_breakdown": breakdown,
        "confidence_score": confidence,
        "data_completeness": completeness,
    }


# ---------- Reachability checks ------------------------------------------

def nhtsa_reachable(timeout_s: float = 4.0) -> dict:
    t0 = time.monotonic()
    try:
        r = requests.get(
            "https://vpic.nhtsa.dot.gov/api/vehicles/GetAllMakes?format=json",
            headers=UA, timeout=timeout_s)
        ok = r.ok
        return {"reachable": ok, "status": r.status_code,
                "ms": int((time.monotonic() - t0) * 1000)}
    except requests.RequestException as e:
        return {"reachable": False, "status": None,
                "ms": int((time.monotonic() - t0) * 1000), "error": str(e)}


def provider_status() -> dict:
    """What's configured, what's not. Never reveals the key value itself —
    only whether one is present and the last 4 chars for visual confirmation."""
    choice = _provider_choice()
    out = {
        "choice": choice,
        "known": list(_KNOWN_PROVIDERS),
        "providers": [],
    }
    for name in ("bumper", "clearvin", "nmvtis", "autocheck", "carfax", "generic"):
        key = _provider_key(name)
        out["providers"].append({
            "name": name,
            "configured": bool(key),
            "key_last4": (key[-4:] if key and len(key) >= 4 else None),
            "base_url": _provider_base_url(name),
        })
    out["any_configured"] = any(p["configured"] for p in out["providers"])
    return out


# ---------- Top-level check() — what the new endpoint returns ------------

def check(vin: Any, listing: Optional[dict] = None,
          force_refresh: bool = False) -> dict:
    """Full VIN report: validation + decode + history + recalls + risk.
    NEVER raises — always returns a dict the UI can render. Errors flow
    into the `errors` list and into individual `status` fields."""
    started = time.monotonic()
    val = validate_vin(vin)
    out: dict[str, Any] = {
        "vin": val.get("normalized") or (str(vin or "").upper().strip() or None),
        "validation": val,
        "errors": [],
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "decoded": None,
        "decoded_summary": None,
        "history": _empty_history(None, "VIN invalid"),
        "recalls": None,
        "open_recalls": None,
        "historical_recalls": None,
        "listing_mismatch": {"checked": False, "mismatches": [], "message": None,
                              "severity": "none"},
        "provider": None,
        "cache": {"decode_from_cache": False, "history_from_cache": False},
        "elapsed_ms": 0,
    }

    if not val["ok"]:
        out["errors"].append(("; ".join(val["errors"])) or "invalid VIN")
        # Even on invalid we want consistent shape
        risk = compute_risk({}, out["history"], None, out["listing_mismatch"])
        out.update(risk)
        out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return out

    vin_n = val["normalized"]

    # --- decode ---------------------------------------------------------
    try:
        cached = None if force_refresh else cache_get(vin_n, "decode")
        if cached is not None:
            decoded = cached
            out["cache"]["decode_from_cache"] = True
        else:
            decoded = decode_vin(vin_n, force_refresh=force_refresh)
        out["decoded"] = decoded
        out["decoded_summary"] = {
            "year":  decoded.get("ModelYear"),
            "make":  decoded.get("Make"),
            "model": decoded.get("Model"),
            "trim":  decoded.get("Trim") or decoded.get("Series"),
            "body":  decoded.get("BodyClass"),
            "engine":(f"{decoded.get('DisplacementL','')}L "
                       f"{decoded.get('EngineCylinders','')}cyl").strip(),
            "fuel":  decoded.get("FuelTypePrimary"),
            "drive": decoded.get("DriveType"),
            "transmission": decoded.get("TransmissionStyle"),
            "plant": " / ".join(filter(None, [decoded.get("PlantCity"),
                       decoded.get("PlantState"), decoded.get("PlantCountry")])),
            "manufacturer": decoded.get("ManufacturerName"),
            "gvwr":  decoded.get("GVWR"),
            "doors": decoded.get("Doors"),
        }
    except Exception as e:
        out["errors"].append(f"decode failed: {e}")

    # --- history (paid) -------------------------------------------------
    hist = history(vin_n, force_refresh=force_refresh)
    if "_cache_age_seconds" in hist:
        out["cache"]["history_from_cache"] = True
    out["history"] = hist
    out["provider"] = hist.get("_provider")

    # --- recalls --------------------------------------------------------
    d = out["decoded_summary"] or {}
    recs: Optional[list] = None
    if d.get("year") and d.get("make") and d.get("model"):
        try:
            cached_r = None if force_refresh else cache_get(vin_n, "recalls")
            if cached_r is not None:
                recs = cached_r.get("list", [])
            else:
                recs = recalls(int(d["year"]), d["make"], d["model"])
                cache_put(vin_n, "recalls", {"list": recs}, RECALLS_TTL_SEC,
                          provider="nhtsa")
        except Exception as e:
            out["errors"].append(f"recalls failed: {e}")
            recs = None
    out["recalls"] = recs or []
    if recs is not None:
        out["open_recalls"] = [r for r in recs if not r.get("Remedy")]
        out["historical_recalls"] = [r for r in recs if r.get("Remedy")]
    else:
        out["open_recalls"] = None
        out["historical_recalls"] = None

    # --- listing mismatch ----------------------------------------------
    out["listing_mismatch"] = compare_listing(out["decoded_summary"] or {}, listing)

    # --- risk + confidence ---------------------------------------------
    risk = compute_risk(out["decoded_summary"] or {}, hist, recs,
                        out["listing_mismatch"])
    out.update(risk)

    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    # Save the whole report (light TTL) so /api/vin/report/<vin> is fast
    try:
        cache_put(vin_n, "report", out, HISTORY_TTL_SEC, provider=hist.get("_provider"))
    except Exception:
        pass
    return out


# ---------- Diagnostics --------------------------------------------------

def diagnostics() -> dict:
    """Everything the dashboard needs to render an honest VIN diagnostics
    panel. No keys leak — only configured/reachable flags."""
    nh = nhtsa_reachable()
    ps = provider_status()

    # Average response time + last error per op from metrics table
    avg_ms = {}
    last_err = {}
    last_ok = {}
    try:
        with _cache_conn() as c:
            rows = c.execute(
                "SELECT op, AVG(ms) avg_ms, MAX(when_iso) last "
                "FROM vin_metrics GROUP BY op").fetchall()
            for r in rows:
                avg_ms[r["op"]] = round(r["avg_ms"], 1) if r["avg_ms"] else None
            for op in set(avg_ms):
                r1 = c.execute(
                    "SELECT when_iso, error FROM vin_metrics "
                    "WHERE op=? AND success=0 ORDER BY id DESC LIMIT 1",
                    (op,)).fetchone()
                if r1: last_err[op] = {"when": r1["when_iso"], "error": r1["error"]}
                r2 = c.execute(
                    "SELECT when_iso FROM vin_metrics "
                    "WHERE op=? AND success=1 ORDER BY id DESC LIMIT 1",
                    (op,)).fetchone()
                if r2: last_ok[op] = r2["when_iso"]
    except sqlite3.Error:
        pass

    # Provider reachability: probe each *configured* provider quickly. We
    # don't burn quota by calling the actual data endpoint — we just check
    # whether the base URL resolves with a HEAD/GET to root.
    provider_reach = []
    for p in ps["providers"]:
        if not p["configured"]:
            continue
        base = p["base_url"]
        if not base:
            provider_reach.append({"name": p["name"], "reachable": False,
                                    "error": "no base url"})
            continue
        t0 = time.monotonic()
        try:
            r = requests.head(base, headers=UA, timeout=4,
                              allow_redirects=True)
            provider_reach.append({"name": p["name"],
                                    "reachable": r.status_code < 500,
                                    "status": r.status_code,
                                    "ms": int((time.monotonic() - t0) * 1000)})
        except requests.RequestException as e:
            provider_reach.append({"name": p["name"], "reachable": False,
                                    "error": str(e)[:120],
                                    "ms": int((time.monotonic() - t0) * 1000)})

    return {
        "nhtsa": nh,
        "provider_choice": ps["choice"],
        "providers": ps["providers"],
        "providers_reachable": provider_reach,
        "any_configured": ps["any_configured"],
        "cache": cache_stats(),
        "avg_ms": avg_ms,
        "last_error": last_err,
        "last_success": last_ok,
    }


# ---------- CLI ----------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="VIN check (decode + history + risk)")
    p.add_argument("vin", nargs="?")
    p.add_argument("--json", action="store_true")
    p.add_argument("--listing", help='JSON listing for mismatch check, e.g. {"year":2018,"make":"BMW","model":"X3"}')
    p.add_argument("--diagnostics", action="store_true",
                   help="Print VIN system diagnostics and exit")
    p.add_argument("--legacy", action="store_true",
                   help="Use the legacy report() shape (pre-v3)")
    p.add_argument("--force-refresh", action="store_true")
    args = p.parse_args()
    if args.diagnostics:
        print(json.dumps(diagnostics(), indent=2, default=str))
        return
    if not args.vin:
        p.error("vin is required (or use --diagnostics)")
    try:
        if args.legacy:
            r = report(args.vin.upper().strip())
        else:
            listing = json.loads(args.listing) if args.listing else None
            r = check(args.vin, listing=listing, force_refresh=args.force_refresh)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); sys.exit(2)
    if args.json or not args.legacy:
        print(json.dumps(r, indent=2, default=str))
    else:
        print_report(r)


if __name__ == "__main__":
    main()
