# Ownership map

Which agent role (or person acting in it) owns which part of the code, and
which changes need the owner's review before merge regardless of who wrote
them. Roles are defined in `AGENTS.md`. "Owner" means the repository owner.

| Area | Paths | Owning role | Owner review required |
|---|---|---|---|
| Architecture, data model | `db.py`, `migrations/`, `jobqueue.py`, `joblock.py`, `jobs/` | Architect | **Yes** — every migration |
| Web API, auth, plans | `server.py`, `auth.py`, `plans.py`, `config.py` | Backend | **Yes** for `auth.py`, plan limits |
| Billing | `billing.py`, `jobs/reconcile_billing.py`, `STRIPE_TESTING.md` | Backend | **Yes, always** |
| Outbound HTTP / alerts | `safe_http.py`, `notifications.py`, `saved_search_alerts.py` | Backend | Yes for `safe_http.py` (SSRF) |
| Sources and licensing | `sources/`, `provider_budget.py`, `verify_sources.py`, `SOURCES.md`, `DATA_PROVIDER_DECISION.md` | Data | **Yes** for `LICENSING` / `AGREEMENT_ENV`, `sources/__init__.py` licensing gate |
| Ingestion and scoring | `sniper.py`, `scam_detector.py`, `vin.py`, `geo.py`, `data/` | Data | No (tests + drift guard) |
| Valuation and profit | `valuation.py`, `comps.py`, `profit.py`, `opportunity.py`, `benchmarks/` | Valuation | Yes when `benchmarks/baseline.json` changes |
| Evaluation | `evaluation.py`, `EVALUATION.md` | QA | No |
| Readiness gates | `preflight.py`, `LAUNCH_CHECKLIST.md`, `RELEASE_READINESS.md` | QA | **Yes** — gates may not be weakened |
| UI | `mobile.html`, `dashboard.html`, `login.html`, `tests/browser/` | Frontend | No |
| CI and automation | `.github/` | Architect | **Yes** — workflows hold tokens |
| Deployment | `deploy/`, `DEPLOY.md`, `RUNBOOK.md` | Architect | **Yes** — and deploys are owner-only |
| Market, pricing, economics | `economics/`, `UNIT_ECONOMICS.md`, `COMPETITIVE_ANALYSIS.md`, `CURRENT_STATE.md` | Product Intelligence | Yes for pricing changes |

Tests live next to the area they cover (`tests/test_<area>.py`); the owning
role of the code owns its tests.
