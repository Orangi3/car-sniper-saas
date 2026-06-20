"""
sources/__init__.py — auto-discovery + registry.

Every .py module in this directory (except base and __init__) is treated as a
source plugin. A valid source defines SOURCE_ID, SOURCE_NAME, poll(cfg),
enabled(cfg).

Usage:
    from sources import iter_sources, poll_all
    for src in iter_sources(cfg):
        print(src.SOURCE_ID, src.SOURCE_NAME)
    listings = poll_all(cfg)
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
from types import ModuleType

from . import base  # noqa: F401  (re-export point)
from .base import NormalizedListing


def _discover() -> list[ModuleType]:
    mods = []
    for info in pkgutil.iter_modules(__path__):
        if info.name in ("base", "__init__"):
            continue
        try:
            m = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as e:
            print(f"[sources] failed to import {info.name}: {e}", file=sys.stderr)
            continue
        if not all(hasattr(m, x) for x in ("SOURCE_ID", "SOURCE_NAME", "poll", "enabled")):
            continue
        mods.append(m)
    return mods


_ALL: list[ModuleType] | None = None


def all_sources() -> list[ModuleType]:
    global _ALL
    if _ALL is None:
        _ALL = _discover()
    return list(_ALL)


def iter_sources(cfg: dict) -> list[ModuleType]:
    """Return enabled sources in stable order."""
    enabled = []
    for m in all_sources():
        try:
            if m.enabled(cfg):
                enabled.append(m)
        except Exception as e:
            print(f"[sources] {m.SOURCE_ID} enabled() error: {e}", file=sys.stderr)
    return sorted(enabled, key=lambda x: x.SOURCE_ID)


def poll_all(cfg: dict) -> dict[str, list[NormalizedListing]]:
    """Run poll() on every enabled source. Errors in one don't kill the rest."""
    out: dict[str, list[NormalizedListing]] = {}
    for m in iter_sources(cfg):
        try:
            out[m.SOURCE_ID] = list(m.poll(cfg))
        except Exception as e:
            print(f"[{m.SOURCE_ID}] poll failed: {e}", file=sys.stderr)
            out[m.SOURCE_ID] = []
    return out
