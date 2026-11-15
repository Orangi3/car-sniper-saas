# Upgrade an existing host to the restart step
Scope: a host that was installed BEFORE PR #19, so it still runs the old
`sniper-migrate.service` with `RemainAfterExit=no`. Fresh installs do not
need this procedure: `deploy/install.sh` installs all units.

Sprint 04 note: the restart step described below now lives in the host
helper `deploy/release_transition.py` (`finish()` / `restart_service()`),
reached through `deploy/sync.sh --rollback`, `--apply`, or `--recover`.
The operator-visible contract is unchanged: a restart failure exits 12
and names the failed service, the operation, and the exit status, with a
redacted journal excerpt and the locked release state. The recovery
patch does not install unit files, so this procedure is still required
on pre-PR #19 hosts.

Release-safety execution note (2026-11-04): existing hosts no longer
need this manual procedure on the normal path. The host helper now
syncs unit files itself (`sync_units`, at the start of `finish()`):
it installs the release's `.service`/`.timer` files, daemon-reloads,
and verifies the effective configuration (LoadState + FragmentPath)
before any service restarts — on deploy, on rollback, and on recovery.
A sync failure aborts before restarts and is retried by recovery.
Keep this manual procedure as the fallback if automatic sync fails,
or for hosts that cannot run a new-style deploy.

Status: PROCEDURE ONLY. It has not been executed on a disposable Linux
guest in this sprint (no guest image is available in this environment), so
native Linux verification is PENDING. Do not claim otherwise. Every step
below is executable by the operator on the host; stop at the first step
that does not match its expected output.

## Preconditions

- You are on the host (or ssh root@host), and the host already runs the
  FlipFirst deployment from `/opt/sniper`.
- You have the Sprint 04 combined candidate tree (recovery patch +
  Sprint 03 restart diagnostics, based on PR #19 head `f17caf7`)
  available to copy the unit file from.
- Abort rule: if any verification step fails, stop, keep the old unit in
  place, and report the step and its output. Do not restart services
  against a half-installed unit.
- Release directories are root-owned. The older `install.sh` created
  `/opt/sniper` (the `sniper` user's home) and `/opt/sniper/releases`
  owned by `sniper`. `release_transition.py` refuses every operation
  (status, apply, rollback, recover) on that layout, which is correct
  because a service account must not be able to swap releases. Fix it
  once, before the first release operation:
  `chown root:root /opt/sniper /opt/sniper/releases && chmod 0755 /opt/sniper /opt/sniper/releases`
  Then confirm with `stat -c '%U:%G %a %n' /opt/sniper /opt/sniper/releases`
  (expect `root:root 755` for both). Services keep reading the release
  tree; they never write to these two directories.

## Steps

1. Record the baseline.
   `systemctl cat sniper-migrate.service` and
   `systemctl show sniper-migrate.service -p RemainAfterExit --value`
   Expected: `RemainAfterExit=no` (the old unit). Also record
   `cat /opt/sniper/current_revision` and the output of
   `readlink /opt/sniper/current`.

2. Copy only the changed unit file.
   `install -m 0644 deploy/systemd/sniper-migrate.service /etc/systemd/system/sniper-migrate.service`
   No other unit file changes in this release. Do not copy the whole
   directory blindly; the point is a minimal, reviewable change.

3. Reload systemd.
   `systemctl daemon-reload`
   Expected: no output, exit 0.

4. Verify the effective configuration (from systemd, not from the file).
   `systemctl show sniper-migrate.service -p RemainAfterExit --value`
   Expected: `yes`.
   `systemctl cat sniper-migrate.service`
   Expected: the new file content, including the comment explaining why
   `RemainAfterExit=yes` exists. If either check shows the old value,
   stop: the reload did not take effect.

5. Restart services in order, using the host helper's finish step.
   The restart sequence runs inside the recovery/rollback path
   (`release_transition.py` finish: migrate, then web, then scheduler
   and worker if enabled). A failure now exits 12 and names the failed
   service, the operation, and the exit status, with a redacted journal
   excerpt and the locked release state. Do NOT re-run a rollback to fix
   a restart failure: the flip already happened, and a second rollback
   would flip back. Use `--recover` to resume the recorded transition
   after fixing the cause; recovery never moves the pointer a second
   time.

6. Verify application health and the actual running revision.
   `curl -fsS http://127.0.0.1:8765/health` (expect HTTP 200 with
   `"status":"ok"`), then confirm `cat /opt/sniper/current_revision`
   matches the revision from step 1 and `readlink /opt/sniper/current`
   points at that release directory. Health alone is not enough: the
   revision check proves the running code is the release you think it is.

7. Verify migrations do not re-run on an app restart.
   `systemctl restart sniper-web.service`, then confirm the migrate
   unit's active-enter timestamp did not change:
   `systemctl show sniper-migrate.service -p ActiveEnterTimestamp --value`
   before and after the web restart. Unchanged means the fix holds.

## Partial failure

- Failure at steps 2–4 (before any restart): the old unit is still
  effective if you have not reloaded, or the new unit is installed but
  no service has restarted. Restore the old file from backup if needed,
  `daemon-reload`, re-run step 4, and confirm the old value returns.
- Failure at step 5: use the step's own failure report (failed service,
  operation, exit status, journal excerpt, locked release state).
  Restart only the failed service by hand after fixing its cause;
  never re-run a full rollback to recover from a restart failure.
- Failure at step 6 (unhealthy or wrong revision): stop, report the
  health output and the revision mismatch, and escalate to the owner.
  A rollback decision at this point is an owner decision, not an
  automatic step.
- If the owner decides to go back after a release switch failed (unit
  sync, a restart, or the health check), roll back with exact pins:
  `--rollback --expect <new revision> --target <previous revision>`.
  It takes over the failed transition's journal only when that journal
  records the completed switch from `<previous revision>` to
  `<new revision>`, and the pointer and revision markers show it. Any
  other pending journal is still refused: use `--recover`, or reconcile
  by hand. Deleting `.release-transition.json` by hand is no longer
  needed for this case.

## First-install behaviour (for contrast)

A fresh host runs `deploy/install.sh`, which installs all ten units and
daemon-reloads, so it starts with `RemainAfterExit=yes` and never needs
this procedure. The gap this procedure closes exists only on hosts
installed before PR #19, because `deploy/sync.sh` never installs unit
files.
