"""
scripts/check_migrations.py — structural migration checks (no DB needed).

  * every migration has both a SQLite and a .postgres.sql variant
  * ids are contiguous (0000, 0001, ...) with no duplicates

Applying them (fresh + re-run is a no-op) is done by the maintenance
workflow against both engines with `python -m migrations.runner`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "migrations"
RE = re.compile(r"^(\d{4})_([a-z0-9_]+?)(\.postgres)?\.sql$")


def problems() -> list[str]:
    seen: dict[str, dict] = {}
    for p in DIR.glob("*.sql"):
        m = RE.match(p.name)
        if not m:
            return [f"unexpected file name {p.name}"]
        seen.setdefault(m.group(1), {}).setdefault(m.group(2), set()).add(
            "postgres" if m.group(3) else "sqlite")
    out = []
    for num, names in sorted(seen.items()):
        if len(names) > 1:
            out.append(f"{num}: duplicate ids {sorted(names)}")
        for name, dialects in names.items():
            if dialects != {"sqlite", "postgres"}:
                out.append(f"{num}_{name}: missing {({'sqlite', 'postgres'} - dialects).pop()} variant")
    nums = sorted(int(n) for n in seen)
    if nums != list(range(len(nums))):
        out.append(f"non-contiguous ids: {nums}")
    return out


if __name__ == "__main__":
    ps = problems()
    for p in ps:
        print("FAIL", p)
    print("migrations ok" if not ps else f"{len(ps)} problem(s)")
    sys.exit(1 if ps else 0)
