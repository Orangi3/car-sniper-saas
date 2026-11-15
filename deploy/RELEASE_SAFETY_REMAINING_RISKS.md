# Remaining release-safety risks (Sprint 03, carried into the Sprint 04 combined candidate)

Status: DOCUMENTATION ONLY. No architectural migration was performed.
Nothing below is fixed by the restart step, and a successful
rollback (symlink pointing at the previous release) must NOT be read as
proof that rollback is safe. Both risks below can silently invalidate an
otherwise successful rollback.

Combined-candidate note (Sprint 04): the restart diagnostics described
below now live in `deploy/release_transition.py` (`finish()` /
`restart_service()`), not in the old `deploy/sync.sh` restart block —
the recovery patch replaced that script with a thin wrapper around the
host helper. The two risks are unchanged by that move: the recovery
code still installs the new release's requirements into the shared
venv before the flip (`prepare_services`), and the rollback path still
contains no pip step.

## Risk 1 — Shared virtual environment (ADDRESSED by per-release venvs,
release-safety execution 2026-11-04)

Previous fact (now historical): `/opt/sniper/.venv` was persistent and
shared by every release; deploy pip-installed the new release's
requirements into it, and rollback had no pip step, so old code ran
against new dependencies after a rollback.

What changed: each release now builds its own venv inside its
(immutable) release directory during `prepare_services`
(`deploy/release_transition.py`), and the units run the interpreter
through the `current` symlink (`/opt/sniper/current/.venv/bin/python3`,
web via `python3 -m gunicorn`). The venv therefore switches
atomically with the pointer, and a rollback restores the old
dependencies by construction — rollback runs no pip at all. A
fail-closed guard (`require_release_venv`) refuses a rollback or a
unit sync whose target ships current-venv units but carries no venv,
before the pointer moves (rollback) or before any restart (finish).
Tests in `tests/test_release_safety.py` prove: deploy builds the
venv in the release dir and never touches the shared venv; rollback
runs zero pip commands; rollback to a venvless new-style target fails
with pointer, markers, journal, units, and restarts untouched; and a
dependency-changing deploy followed by a rollback lands on the old
release's venv.

What remains (read before trusting this on an old host):
- Old-style releases (built before this change) carry no venv and
  their units still reference the shared venv. Rollback to one works
  (the era bridge: its units are synced back too, and deploy never
  touches the shared venv anymore), but that path still depends on
  the shared venv's contents, which nothing now maintains. Once every
  release on a host is new-style, the shared venv is dead weight an
  operator may remove — the helper never deletes it itself.
- Unit sync replaces files but never changes enablement: a brand-new
  unit added by a release is installed but not enabled. Enabling new
  units stays an operator/install step.
- Nothing here executes venv console scripts by path (always
  `<venv>/bin/python -m <module>`), so no shebang can point at a
  staging path. Keep that rule: it is load-bearing for the design.

## Risk 2 — Database schema compatibility (PARTIALLY ADDRESSED)

Fact: migrations are forward-only and additive, each migration runs in
its own transaction, and the runner skips already-applied migrations
idempotently (migrations 0000–0021 as of this tree). The deploy
comment asserts each schema stays compatible with the previous code
release. That assertion is a comment, not a test and not a CI gate, so
a future migration that breaks it (a renamed column, a dropped table,
a tightened constraint the old code violates) would be caught only by
a human reading the diff — or by a failed rollback in production.

Recommendation (not implemented — needs an owner design decision):
adopt an explicit backward-compatibility policy and enforce it. The
policy: a migration may only add (new tables, new nullable columns,
new indexes) and never rename, drop, or tighten in the same release
that ships code depending on it; destructive changes ship one release
later, after the old code is gone (expand/contract). Enforcement: a CI
check that diffs each migration against the previous release's schema
expectations, plus a disposable-host rehearsal that deploys the new
release, runs its migrations, rolls back, and runs the old release's
test suite against the migrated database. Until that gate exists, every
migration must be reviewed for rollback compatibility by hand.

## What Sprint 03 DID fix (for contrast)

The restart step now names the failed service, its operation, and its
exit status; distinguishes migration from application failures; prints
a redacted journal excerpt and the locked release state; retries a
start-limit-hit service exactly once after one reset and reports a
persistent crash loop instead of masking it; preserves migration-first
ordering; and exits 12 on any restart failure in both paths. None of
that changes the two risks above: the step reports failures well, but
it cannot make old code safe on new dependencies or on a schema the
old code cannot read.

## Review findings (independent review, 2026-11-04) — fixed or documented

- Redaction coverage (was MAJOR): the journal-excerpt scrubber was
  case-sensitive and missed ALL-CAPS env names (`SMTP_PASSWORD`,
  `API_KEY`), credential-bearing URLs, and Bearer tokens. Fixed:
  case-insensitive matching, broader key names (`application-key`,
  `key-id`), full userinfo scrubbing, Bearer scrubbing. Regression
  tests in `tests/test_review_fixes.py`. Residual: a secret in a form
  none of these patterns match (e.g. a bare hex string with no key
  name) still passes through — excerpts must still be handled as
  potentially sensitive, never pasted blindly.
- Silent scheduler/worker skip (was MAJOR): `finish()` skipped
  non-enabled units with no output. Fixed: every skip is printed
  with its reason, and a unit this release ships that reports
  `not-found` after verified sync now fails closed instead of
  reporting success. Disabled/masked units remain an admin choice
  (printed, not restarted, not failed).
- Effective-config verification scope: sync proves fragment identity
  (LoadState + FragmentPath), NOT the full effective configuration.
  A `.d/` drop-in override can change effective settings undetected.
  No drop-ins are shipped; drop-ins must be reviewed by hand.
- Dropped-unit drift: sync never removes units a release drops; it
  now prints installed-but-not-shipped `sniper-*` units as drift on
  every sync. No units are dropped in this release.
- Failed-prepare cleanup: a `prepare_services()` failure leaves the
  partial release directory and the journal, and retrying the same
  revision is refused. `deploy()` now prints the manual cleanup
  procedure (reconcile the shared database first, then remove the
  release directory and the journal by hand) and re-raises the
  original error unchanged.
- Umask dependence: extraction now forces 0755 on all directories,
  and the venv is mode-fixed after pip install (dirs 0755, files
  0755 if executable else 0644), so a restrictive root umask cannot
  leave the release unreadable by the sniper user.
- Single-step rollback limit: rollback targets `previous_revision`
  only; two-step rollback and re-deploying an existing revision are
  unsupported by design (immutability). To re-deploy an older
  revision, remove its release directory first — only when it is
  neither active nor previous — then deploy. If the previous release
  is itself broken, redeploy a known-good revision or restore from
  backup per FIRST_DEPLOY; do not remove the active or previous
  directories to force a rollback.
- Reset budget: exactly one reset per `finish()` call (per attempt),
  not per incident; repeated `--recover` invocations each get one.
  Each attempt stays bounded and reports failure.
- Unit-directory hardening: `sync_units()` now requires the unit
  directory to be a root-owned real directory (not a symlink).
- SSH robustness: `sync.sh` now uses `BatchMode=yes` and
  `ConnectTimeout=30` so a prompting or unreachable host fails fast
  instead of hanging the operator run.

## Database compatibility — corrected (independent review N2)

The earlier claim "zero destructive ops, all ALTERs ADD COLUMN" is
WRONG and is corrected here. Migration 0018 (`hashed_session_tokens`)
is data-mutating: on SQLite it DELETEs legacy-format session rows
(users sign in again once); on PostgreSQL it hashes tokens in place
(one-way — pre-0018 code cannot validate those sessions). It is
pre-existing, bounded to sessions, documented in the migration
headers, and irrelevant on hosts that already applied 0018 (the
runner skips applied IDs; each file is transactional). Every OTHER
migration is ADD COLUMN. Rollback safety therefore rests on
additive-only discipline plus the 0018 caveat: a rollback to pre-0018
code invalidates sessions created under the new scheme, and there is
no down-migration path — schema rollback is never automatic.
