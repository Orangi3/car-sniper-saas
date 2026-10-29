"""
sources/schema.py — lightweight response-schema checks for provider APIs.

Providers change payloads without notice. Each adapter declares the fields
it depends on; `check_items()` reports which required fields are missing or
of the wrong type, per item, and field coverage across the response. An
adapter reports `malformed` (schema drift) when most items fail, instead of
silently ingesting half-empty records.

A spec maps dotted paths to a type (or tuple of types). Paths ending in '?'
are optional: counted for coverage, never a failure.
"""
from __future__ import annotations

from typing import Any

NUM = (int, float, str)      # providers send numbers as strings too


def _get(obj: Any, path: str):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _ok_type(v, t) -> bool:
    if t is NUM or t == NUM:
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return False
    return isinstance(v, t)


def check_items(items: list, spec: dict[str, Any]) -> dict:
    """Returns {'n', 'bad_items', 'problems': {path: count}, 'coverage': {path: pct}}."""
    problems: dict[str, int] = {}
    present: dict[str, int] = {}
    bad = 0
    for it in items:
        item_bad = False
        for raw, t in spec.items():
            optional = raw.endswith("?")
            path = raw.rstrip("?")
            v = _get(it, path)
            if v not in (None, ""):
                present[path] = present.get(path, 0) + 1
                if not _ok_type(v, t):
                    problems[f"{path}: wrong type"] = problems.get(f"{path}: wrong type", 0) + 1
                    item_bad = item_bad or not optional
            elif not optional:
                problems[f"{path}: missing"] = problems.get(f"{path}: missing", 0) + 1
                item_bad = True
        bad += item_bad
    n = len(items)
    return {"n": n, "bad_items": bad, "problems": problems,
            "coverage": {p.rstrip("?"): round(100 * present.get(p.rstrip("?"), 0) / n, 1)
                         if n else 0.0 for p in spec}}


def drifted(result: dict, threshold: float = 0.5) -> bool:
    """True if more than `threshold` of items fail required fields."""
    return result["n"] > 0 and result["bad_items"] / result["n"] > threshold


def describe(result: dict) -> str:
    top = sorted(result["problems"].items(), key=lambda kv: -kv[1])[:4]
    return (f"{result['bad_items']}/{result['n']} items fail schema: "
            + ", ".join(f"{k} ×{v}" for k, v in top))
