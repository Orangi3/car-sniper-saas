"""
geo.py — ZIP centroids and distances for buying-territory matching.

Coordinates come from data/zip_centroids.csv.gz (GeoNames, CC BY 4.0 — see
data/ATTRIBUTION.md), loaded lazily once per process. A listing gets
coordinates only from its provider's structured lat/lon or the centroid of
a provider-supplied ZIP — never from parsing free text.
"""
from __future__ import annotations

import csv
import gzip
import math
from pathlib import Path
from typing import Optional

_PATH = Path(__file__).resolve().parent / "data" / "zip_centroids.csv.gz"
_ZIPS: Optional[dict[str, tuple[float, float]]] = None


def _load() -> dict[str, tuple[float, float]]:
    global _ZIPS
    if _ZIPS is None:
        with gzip.open(_PATH, "rt") as f:
            _ZIPS = {r["zip"]: (float(r["lat"]), float(r["lon"]))
                     for r in csv.DictReader(f)}
    return _ZIPS


def zip_centroid(z: Optional[str]) -> Optional[tuple[float, float]]:
    if not z:
        return None
    return _load().get(str(z).strip()[:5])


def valid_zip(z: Optional[str]) -> bool:
    return zip_centroid(z) is not None


def haversine_mi(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def distance_mi(home_zip: Optional[str], lat, lon) -> Optional[float]:
    """Miles from a home ZIP centroid to a listing's coordinates, or None if
    either side is unknown."""
    h = zip_centroid(home_zip)
    if h is None or lat is None or lon is None:
        return None
    return round(haversine_mi(h, (float(lat), float(lon))), 1)
