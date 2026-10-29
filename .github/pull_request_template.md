## What and why

## Acceptance criteria (from the issue)
- [ ]

## Verification
- [ ] `python -m pytest -q` (SQLite)
- [ ] `SNIPER_TEST_PG_URL=… python -m pytest -q` (Postgres)
- [ ] `python benchmarks/check_drift.py` (if valuation/risk/parsing changed, baseline updated deliberately and explained)
- [ ] Browser smoke (if UI changed)

## Risk
Risk class: low / medium / high. High = auth, billing, licensing gates, migrations, data deletion — owner review required.

## Not verified / limitations
