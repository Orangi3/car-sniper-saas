# Engineering agents: roles, coordination and safeguards

How AI agents (and people) work on Car Sniper without supervision gaps.
Everything here is enforced by GitHub — issues, pull requests, required
checks, branch protection — not by trusting an agent to behave.

## Status

| Piece | State |
|---|---|
| Nightly maintenance (`.github/workflows/maintenance.yml`) | Active once merged. Plain CI, no AI. Opens/updates one deduplicated `maintenance-failure` issue on failure. |
| Live data probe (`live-source-check.yml`) | Manual dispatch; needs provider secrets. |
| Agent runs (`agent-task.yml`) | **Inactive.** Needs the owner's `ANTHROPIC_API_KEY` secret and an `AGENT_MODEL` variable. Not yet verified end to end. |
| Branch protection on `main` | **Not enabled** — owner action (see below). `agent-task` refuses to start until it is. |

Nothing runs continuously. Agent work happens only when the owner starts it,
and stops when the owner's API budget or the run limits are reached.

## Roles

| Role | Owns (see `OWNERSHIP.md`) | Typical tasks | Done means |
|---|---|---|---|
| Architect | data model, migrations, jobs, CI, deploy | schema changes, queue/locking, workflow changes | both DB engines pass; migrations re-run as no-ops |
| Backend | API, auth, billing, alerts | endpoints, plan limits, notification channels | API tests; billing tests untouched or reviewed |
| Data | sources, licensing, ingestion | provider adapters, schema checks, dedup | fixture tests + a `verify_sources` run when keys exist |
| Valuation | valuation, profit, opportunity, benchmark | comparable selection, scenarios, confidence | unit tests; `check_drift.py` clean or baseline change justified |
| Frontend | `mobile.html`, dashboard | deal card, onboarding | browser smoke passes; screenshots in PR |
| QA | tests, evaluation, preflight | regression tests, evaluation metrics | gates unchanged or stricter |
| Product Intelligence | economics, competition, docs | pricing models, competitor research | sources cited; estimates labeled as estimates |

One person or one agent may hold several roles; the role decides which
checks and reviews apply.

## Coordination

1. **Work starts as an issue** from the *Agent task* template: owning role,
   priority (P0–P5), evidence, acceptance criteria, required tests, risk
   class. Anything needing credentials, money, agreements or a human
   decision goes in an *Owner action* issue instead.
2. **One issue → one branch → one pull request** (`agent/issue-<n>` or
   `feat/...`). The PR template lists acceptance criteria, verification
   and what was *not* verified.
3. **Required checks** (`ci.yml`): SQLite + Postgres suites, benchmark,
   browser smoke. A PR that changes `benchmarks/baseline.json` must say why.
4. **Merge is a human act.** High-risk PRs (auth, billing, licensing gates,
   migrations, data deletion, workflows, deploy) need the owner's review.
5. **Deploys are owner-only** (`DEPLOY.md`). No workflow deploys.

Event-driven entry points: a nightly failure opens an issue; the owner
labels an issue `agent-go` to start an agent run on it.

## Model routing and budgets

Use the cheapest thing that can do the job:

| Tier | Use for | Examples |
|---|---|---|
| Deterministic scripts (no model) | anything checkable | tests, `check_drift.py`, `check_migrations.py`, `pip-audit`, `verify_sources`, `preflight` |
| Small/fast model | mechanical edits with tight acceptance criteria | doc updates, adding a fixture, renaming |
| Mid-tier model | ordinary feature work and bug fixes | adapters, endpoints, UI changes |
| Largest model | architecture, valuation methodology, security-sensitive changes, ambiguous debugging | migrations, licensing logic, SSRF, pricing strategy |

The owner picks the model per repository variable `AGENT_MODEL` (or per
run). Model names are deliberately not hard-coded here.

Limits (repository variables): `AGENT_MAX_TURNS` (default 30),
`AGENT_TIMEOUT_MIN` (default 45), one agent run at a time (workflow
concurrency group). Spend control belongs on the API key itself: set a
monthly limit in the provider console. When credits run out, runs fail and
work stops — nothing retries around a limit.

## Safeguards

Agents must never: merge or push to `main`; deploy; buy, subscribe or
accept terms; create or change live billing; add, print or commit secrets;
delete production data; weaken licensing (`sources.licensing_block`),
security (`safe_http`), auth, or `preflight` gates; bypass CAPTCHAs, rate
limits or access controls; present synthetic data as real results.

When blocked by any of those, an agent comments on the issue with the
blocker and stops.

## Pause and resume

- Pause agent runs: set repository variable `AGENT_PAUSED=true`.
- Pause nightly maintenance: disable the workflow in the Actions tab.
- Stop a run in progress: cancel it in the Actions tab.
- Resume: unset the variable / re-enable the workflow.

## Activation (owner)

1. Enable branch protection on `main`: require PRs, require the `ci`
   checks, disallow force pushes; do not exempt admins or bots.
2. Add secret `ANTHROPIC_API_KEY` (a key with a spend limit), variable
   `AGENT_MODEL`, optionally `AGENT_MAX_TURNS` / `AGENT_TIMEOUT_MIN`.
3. Create labels `agent-task` and `agent-go` (the nightly job creates
   `agent-task` on first failure).
4. Try one low-risk issue first and review the PR before trusting more.
