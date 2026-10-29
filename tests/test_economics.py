"""tests/test_economics.py — the economics model runs and keeps its key
properties (data cost tied to territories, not subscribers)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "economics"))
import model  # noqa: E402


def test_data_cost_does_not_grow_within_a_territory():
    a = model.data_cost(10, "base")
    b = model.data_cost(150, "base")
    assert a == b and a["territories"] == 1


def test_pro_breaks_even_before_lite_and_conservative_is_worse():
    for case in model.CASES:
        assert model.breakeven(case, "pro") < model.breakeven(case, "lite")
    assert model.breakeven("conservative", "mixed") > model.breakeven("base", "mixed")


def test_generated_tables_are_current():
    p = Path(__file__).resolve().parent.parent / "economics" / "tables.md"
    assert p.read_text().strip() == model.render().strip(), \
        "run: python economics/model.py > economics/tables.md"


def test_provider_matrix_applies_rights_gate():
    import provider_matrix as pm
    assert sum(pm.CRITERIA.values()) == 100
    rows = pm.ranked()
    usable = [n for n, _, ok in rows if ok]
    blocked = [n for n, _, ok in rows if not ok]
    assert "Craigslist (unlicensed)" in blocked
    assert rows.index(next(r for r in rows if r[0] == blocked[0])) >= len(usable)
    for n in pm.P:
        assert set(pm.P[n]) == set(pm.CRITERIA)
        assert all(tag in ("V", "E") and 0 <= sc <= 5 for sc, tag, _ in pm.P[n].values())
