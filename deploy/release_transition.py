#!/usr/bin/env python3
"""Host-side release transaction. The CLI always uses /opt/sniper."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager

REVISION = re.compile(r"[0-9a-f]{7,40}\Z")
ROOT = Path("/opt/sniper")
REDACT = re.compile(
    r"([Pp]assword|[Pp]asswd|[Tt]oken|[Ss]ecret|[Aa]pi[_-]?[Kk]ey|"
    r"[Aa]pplication[_-]?[Kk]ey|[Kk]ey[_-]?[Ii][Dd])"
    r"(\s*\"?\s*[=:]\s*\"?[^\s\",]+\"?)",
    re.IGNORECASE)
REDACT_URL_CREDENTIALS = re.compile(r"://[^/\s@]+@")
REDACT_BEARER = re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.IGNORECASE)


class RestartFailure(subprocess.CalledProcessError):
    """A service restart failure.

    Subclasses CalledProcessError so existing callers that expect a
    failed systemctl invocation still see one, while main() can map it
    to exit code 12 — the operator-visible restart-failure contract
    from Sprint 03. The exception's returncode is 12 (the CLI exit
    code); the original systemctl exit status is kept separately in
    exit_status and in the failure report.
    """

    def __init__(self, service, operation, exit_status, kind):
        super().__init__(12, ("systemctl", operation, service))
        self.service = service
        self.operation = operation
        self.exit_status = exit_status
        self.kind = kind


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_text(path, text):
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".release-", delete=False
    ) as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stream.name, path)
    sync_dir(path.parent)


@contextmanager
def host_lock(path):
    import fcntl

    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022, "Unsafe lock file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Release lock is held by another operation") from error
        yield fd
    finally:
        os.close(fd)


def check_system_dir(path):
    """Enforce the production unit-directory invariant: a real
    directory, not a symlink, owned by root (uid 0). Called by
    sync_units() on every deploy/rollback/recovery; never bypassed
    in production (main() runs as root and passes no override).
    Tests that simulate deployment with a temp directory patch this
    function with a stub that keeps the directory/symlink checks
    and skips only the uid check — the uid check itself is covered
    by dedicated tests calling this function directly."""
    require(path.is_dir()
            and not path.is_symlink()
            and path.stat().st_uid == 0,
            f"Systemd unit directory is not a root-owned directory: {path}")


class Transition:
    def __init__(self, root, run, system_dir=None):
        self.root = root
        self.run = run
        self._resets_used = set()
        # Directory systemd reads units from. Overridable for tests;
        # production always uses /etc/systemd/system.
        self.system_dir = system_dir or Path("/etc/systemd/system")
        self.journal = root / ".release-transition.json"
        self.current = root / "current"
        self.releases = root / "releases"

    def target(self, revision):
        require(isinstance(revision, str) and REVISION.fullmatch(revision),
                "Invalid release revision")
        path = self.releases / revision
        require(path.is_dir() and not path.is_symlink() and path.resolve() == path,
                f"Release missing or unsafe: {revision}")
        return path

    def active(self):
        if not os.path.lexists(self.current):
            return None
        require(self.current.is_symlink(), "Active pointer is not a symlink")
        path = self.current.resolve(strict=True)
        require(path.parent == self.releases, "Active pointer is outside releases")
        require(self.target(path.name) == path, "Invalid active pointer")
        return path.name

    def marker(self, name):
        path = self.root / name
        if not os.path.lexists(path):
            return None
        require(path.is_file() and not path.is_symlink(), f"Unsafe marker {name}")
        value = path.read_text(encoding="ascii").strip()
        self.target(value)
        return value

    def markers(self):
        return self.marker("current_revision"), self.marker("previous_revision")

    def record(self, journal):
        atomic_text(self.journal, json.dumps(journal) + "\n")

    def load(self):
        require(not self.journal.is_symlink(), "Unsafe journal path")
        if not self.journal.exists():
            return None
        require(self.journal.is_file(), "Journal is not a regular file")
        j = json.loads(self.journal.read_text(encoding="utf-8"))
        require(isinstance(j, dict) and set(j) == {
            "schema", "old", "previous", "target", "phase", "service"
        } and j["schema"] == 1 and j["phase"] in ("preparing", "ready")
            and j["service"] in ("pending", "migration", "application", "health", "complete"),
            "Unrecognized journal. Operator reconciliation required")
        require(j["target"] != j["old"], "Journal does not describe a transition")
        require(j["old"] is not None or j["previous"] is None,
                "Journal contains previous metadata without an active release")
        for name in ("old", "previous", "target"):
            if j[name] is not None:
                require(isinstance(j[name], str) and REVISION.fullmatch(j[name]),
                        "Invalid revision in journal")
        require(j["target"] is not None, "Journal missing target")
        return j

    def clean_state(self):
        require(self.load() is None, "Pending transition. Use --recover or reconcile with operator")
        active = self.active()
        current, previous = self.markers()
        require(current == active and (active is not None or previous is None)
                and (previous is None or previous != active),
                "Active pointer and revision markers disagree. Operator reconciliation required")
        return active, previous

    def prepare(self, old, previous, target, phase):
        require(old != target, "Target is already active")
        j = {"schema": 1, "old": old, "previous": previous, "target": target,
             "phase": phase, "service": "pending"}
        self.record(j)
        return j

    def flip(self, j):
        require(self.active() == j["old"] and self.markers() == (j["old"], j["previous"]),
                "State changed before pointer switch")
        replacement = self.root / (".current-" + uuid.uuid4().hex)
        os.symlink(self.target(j["target"]), replacement)
        os.replace(replacement, self.current)
        self.publish(j)

    def publish(self, j):
        require(self.active() == j["target"], "Active pointer does not match intended release")
        sync_dir(self.root)
        current, previous = self.markers()
        require((current, previous) in (
            (j["old"], j["previous"]), (j["target"], j["previous"]),
            (j["target"], j["old"])
        ), "Unexpected revision metadata. Operator reconciliation required")
        atomic_text(self.root / "current_revision", j["target"] + "\n")
        if j["old"] is not None:
            atomic_text(self.root / "previous_revision", j["old"] + "\n")
        require(self.active() == j["target"] and self.markers() == (j["target"], j["old"]),
                "Pointer or metadata failed verification")

    def report_restart_failure(self, service, operation, exit_status, kind):
        """Print the Sprint 03 failure report: named service, operation,
        exit status and kind, the locked release state read at failure
        time, and a redacted 40-line journal excerpt. Never raises: the
        report must not mask the failure it describes."""
        print(f"FATAL: restart failed: service={service} operation={operation} "
              f"exit={exit_status} kind={kind}", file=sys.stderr)
        try:
            current, previous = self.markers()
        except Exception:
            current, previous = "unknown", "unknown"
        try:
            target = os.readlink(self.current)
        except Exception:
            target = "unknown"
        print(f"locked release state: current_revision={current} "
              f"previous_revision={previous} current_target={target}", file=sys.stderr)
        try:
            excerpt = self.run("journalctl", "-u", service, "-n", "40", "--no-pager",
                               capture=True, check=False)
            text = REDACT.sub(r"\1=***REDACTED***", excerpt.stdout)
            # Credential-bearing URLs (postgres://user:pass@host) carry
            # no keyword the pattern above can see; scrub the whole
            # userinfo. Same for Bearer tokens and other auth headers.
            text = REDACT_URL_CREDENTIALS.sub("://***@", text)
            text = REDACT_BEARER.sub(r"\1***", text)
        except Exception:
            text = ""
        print(f"journal excerpt for {service} (last 40 lines, secrets redacted):",
              file=sys.stderr)
        print(text, file=sys.stderr)

    def restart_service(self, service, kind):
        """Restart one service with Sprint 03 semantics.

        The one-reset budget (`_resets_used`) is per finish() call —
        i.e. per attempt — not per incident: an operator who invokes
        --recover repeatedly gets one reset per invocation. Each
        attempt stays bounded (one reset, then a reported failure).

        On failure, check whether the unit reports start-limit-hit; if
        so, reset-failed exactly once and retry exactly once, unless a
        reset was already used for this service in this finish() call
        (the migration unit's failed-state pre-reset counts). A
        persistent failure — including a persistent crash loop — is
        reported, not reset endlessly, and raises RestartFailure.
        This helper never writes revision files and never moves the
        symlink, so a recovery run cannot switch releases a second time.
        """
        operation = "restart"
        try:
            self.run("systemctl", "restart", service)
            return
        except subprocess.CalledProcessError as error:
            exit_status = error.returncode
        result = self.run("systemctl", "show", "-p", "Result", "--value", service,
                          capture=True, check=False)
        text = result.stdout.strip()
        start_limit = text == "start-limit-hit" or "Result=start-limit-hit" in text
        if start_limit and service not in self._resets_used:
            self._resets_used.add(service)
            print(f"▸ {service} hit its start limit; resetting once and retrying "
                  f"a single time.")
            self.run("systemctl", "reset-failed", service, check=False)
            operation = "reset-failed+restart"
            try:
                self.run("systemctl", "restart", service)
                return
            except subprocess.CalledProcessError as error:
                exit_status = error.returncode
        self.report_restart_failure(service, operation, exit_status, kind)
        raise RestartFailure(service, operation, exit_status, kind)

    def unit_source_files(self, release_dir):
        """Unit files a release ships, sorted by name. Only .service
        and .timer files count; anything else in deploy/systemd is
        ignored, and symlinks are never followed."""
        unit_dir = release_dir / "deploy" / "systemd"
        if not unit_dir.is_dir():
            return []
        sources = []
        for path in sorted(unit_dir.iterdir()):
            if path.suffix in (".service", ".timer") and path.is_file() \
                    and not path.is_symlink():
                sources.append((path.name, path))
        return sources

    def require_release_venv(self, release_dir):
        """Fail closed before any mutation: if a release's own units
        run the interpreter from the active release's venv
        (/opt/sniper/current/.venv), that release must actually carry
        one. Releases that ship no units, or units that still use the
        legacy shared venv, pass: they are era-consistent with whatever
        units are already installed, exactly as before this change."""
        texts = [path.read_text(encoding="utf-8")
                 for _, path in self.unit_source_files(release_dir)]
        if any("/opt/sniper/current/.venv" in text for text in texts):
            require((release_dir / ".venv" / "bin" / "python").is_file(),
                    f"Release {release_dir.name} ships units that need a "
                    f"per-release venv but carries none; refusing to "
                    f"switch releases")

    def sync_units(self, release_dir):
        """Install a release's unit files on an existing host (B3).

        This is the step the old sync path never had: fresh installs
        get units from install.sh, but an existing host kept whatever
        units it was first installed with, so a fix like
        RemainAfterExit=yes never reached it. sync_units copies the
        release's .service/.timer files into the systemd unit
        directory (atomic replace, mode 0644), daemon-reloads, and
        verifies the EFFECTIVE configuration: every synced unit must
        report LoadState=loaded with FragmentPath equal to the file
        just installed. Enablement is deliberately untouched: replacing
        files never enables or disables a unit.

        Ordering (see finish()): sync runs before any restart, so a
        failure here aborts before a service ever runs against
        unverified units, and recovery retries the sync. It never
        removes units a release drops; that stays an operator action,
        but any installed `sniper-*` unit the release does not ship
        is printed as drift so it cannot linger silently.
        Returns the installed unit names (empty when the release ships
        no units, in which case nothing is read from or written to the
        unit directory).

        Verification scope (narrowed deliberately): the check proves
        fragment identity — the loaded unit IS the installed file —
        not the full effective configuration. A `.d/` drop-in override
        can change effective settings without changing FragmentPath
        and is NOT detected here; drop-ins must be reviewed by hand.
        """
        sources = self.unit_source_files(release_dir)
        if not sources:
            return []
        self.require_release_venv(release_dir)
        check_system_dir(self.system_dir)
        installed = []
        for name, path in sources:
            text = path.read_text(encoding="utf-8")
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.system_dir,
                    prefix=".unit-", delete=False) as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(stream.name, 0o644)
            os.replace(stream.name, self.system_dir / name)
            installed.append(name)
        sync_dir(self.system_dir)
        self.run("systemctl", "daemon-reload")
        for name in installed:
            state = self.run("systemctl", "show", name,
                             "-p", "LoadState", "-p", "FragmentPath", capture=True)
            properties = dict(line.split("=", 1)
                              for line in state.stdout.splitlines() if "=" in line)
            require(properties.get("LoadState") == "loaded",
                    f"Unit {name} did not load after daemon-reload")
            require(properties.get("FragmentPath") == str(self.system_dir / name),
                    f"Unit {name} effective path is not the installed file: "
                    f"{properties.get('FragmentPath')}")
        print(f"Synced {len(installed)} unit files from release "
              f"{release_dir.name}; daemon-reload verified.")
        shipped = {name for name, _ in sources}
        drift = sorted(path.name for path in self.system_dir.iterdir()
                       if path.name.startswith("sniper-")
                       and path.suffix in (".service", ".timer")
                       and path.name not in shipped)
        if drift:
            print(f"Unit drift: installed but not shipped by this release "
                  f"(not removed; operator action required): "
                  f"{', '.join(drift)}", file=sys.stderr)
        return installed

    def finish(self, j):
        self._resets_used = set()
        # Units first, before any restart: services must never restart
        # against unverified units, and recovery re-runs finish(), so a
        # failed sync is retried here rather than stranded.
        self.sync_units(self.target(j["target"]))
        j["service"] = "migration"
        self.record(j)
        state = self.run("systemctl", "show", "-p", "LoadState", "-p", "ActiveState",
                         "sniper-migrate.service", capture=True)
        properties = dict(line.split("=", 1) for line in state.stdout.splitlines() if "=" in line)
        require(properties.get("LoadState") == "loaded", "Migration unit is not loaded")
        if properties.get("ActiveState") == "failed":
            # Recovery-patch behaviour, preserved: a unit already in
            # failed state gets one reset before its restart. This is
            # conditioned on observed failure, not a blanket pre-reset,
            # and it counts against the one-reset budget above.
            self.run("systemctl", "reset-failed", "sniper-migrate.service")
            self._resets_used.add("sniper-migrate.service")
        self.restart_service("sniper-migrate.service", "migration")
        j["service"] = "application"
        self.record(j)
        self.restart_service("sniper-web.service", "application")
        for service in ("sniper-scheduler.service", "sniper-worker.service"):
            state = self.run("systemctl", "is-enabled", service, capture=True, check=False)
            status = state.stdout.strip()
            require(status in ("enabled", "enabled-runtime", "disabled", "static", "masked",
                               "masked-runtime", "indirect", "not-found"),
                    f"Could not determine enablement of {service}: {status!r}")
            if status in ("enabled", "enabled-runtime"):
                self.restart_service(service, "application")
            elif status == "not-found" and service in \
                    {name for name, _ in self.unit_source_files(self.target(j["target"]))}:
                # A unit this release ships was verified present by
                # sync_units minutes ago; its absence now is genuinely
                # broken, not an admin choice. Fail closed rather than
                # print "complete" with a service missing.
                raise RuntimeError(
                    f"Unit {service} shipped by release {j['target']} was not "
                    f"found after sync; refusing to report success")
            else:
                # Disabled, masked, static, or indirect units are an
                # admin choice and stay untouched — but the skip must
                # be visible: a running-but-disabled unit keeps the
                # previous release's code after a "complete" deploy.
                print(f"Skipping {service}: {status} (not enabled; a running "
                      f"instance keeps the previous release's code)",
                      file=sys.stderr)
        j["service"] = "health"
        self.record(j)
        self.health(8765)
        require(self.active() == j["target"] and self.markers() == (j["target"], j["old"]),
                "Release changed before completion")
        j["service"] = "complete"
        self.record(j)
        self.clear()
        print("Transition complete. Pointer and metadata verified, restart and health checks passed.")

    def health(self, port):
        for attempt in range(15):
            result = self.run("curl", "-fsS", "-m", "5",
                              f"http://127.0.0.1:{port}/health", capture=True, check=False)
            if result.returncode == 0:
                try:
                    if json.loads(result.stdout).get("status") == "ok":
                        return
                except (ValueError, AttributeError):
                    pass
            if attempt < 14:
                time.sleep(2)
        raise RuntimeError(f"Health check failed on port {port}")

    def clear(self):
        self.journal.unlink()
        sync_dir(self.root)

    def rollback(self, expected, target):
        old, previous = self.clean_state()
        require(old == expected and previous == target,
                "Rollback pins do not match the active release and rollback target")
        release_dir = self.target(target)
        # Fail closed BEFORE the journal is written and before the
        # pointer moves: a target whose units need a per-release venv
        # must carry one, or the rollback is refused with state intact.
        self.require_release_venv(release_dir)
        j = self.prepare(old, previous, target, "ready")
        self.flip(j)
        self.finish(j)

    def recover(self):
        j = self.load()
        if j is None:
            self.clean_state()
            print("No pending transition.")
            return
        require(j["phase"] == "ready",
                "Interrupted preparation may have changed dependencies or schema. Operator reconciliation required")
        self.target(j["target"])
        if j["old"] is not None:
            self.target(j["old"])
        if self.active() == j["old"]:
            require(j["service"] == "pending" and self.markers() == (j["old"], j["previous"]),
                    "Pre-flip state disagrees with journal. Operator reconciliation required")
            self.clear()
            print("Uncommitted transition canceled. Active pointer preserved.")
            return
        require(self.active() == j["target"], "Journal is stale. Operator reconciliation required")
        if j["service"] != "pending":
            require(self.markers() == (j["target"], j["old"]),
                    "Metadata changed after restart began. Operator reconciliation required")
        self.publish(j)
        if j["service"] == "complete":
            self.clear()
            print("Recorded completion reconciled. Active pointer preserved.")
        else:
            self.finish(j)

    def deploy(self, revision, digest, restart):
        old, previous = self.clean_state()
        require(re.fullmatch(r"[0-9a-f]{40}", revision) and re.fullmatch(r"[0-9a-f]{64}", digest),
                "Invalid release or archive digest")
        dest = self.releases / revision
        require(not os.path.lexists(dest), "Release already exists. Historical releases are immutable")
        with tempfile.TemporaryFile(dir=self.root) as archive:
            sha = hashlib.sha256()
            size = 0
            while chunk := sys.stdin.buffer.read(1024 * 1024):
                size += len(chunk)
                require(size <= 256 * 1024 * 1024, "Archive exceeds size limit")
                archive.write(chunk)
                sha.update(chunk)
            require(sha.hexdigest() == digest, "Archive digest mismatch")
            archive.seek(0)
            stage = Path(tempfile.mkdtemp(prefix=".incoming-", dir=self.releases))
            self.extract(archive, stage, revision)
        os.replace(stage, dest)
        sync_dir(self.releases)
        j = self.prepare(old, previous, revision, "preparing")
        try:
            self.prepare_services(dest)
        except Exception:
            # Fail-closed, but say what the operator must do: the
            # partial release directory and the journal remain, and
            # retrying this revision is refused ("Release already
            # exists"). A failed migration may have partially changed
            # the shared database, so this is deliberately NOT
            # auto-retried: reconcile the database first, then remove
            # the release directory and .release-transition.json by
            # hand before retrying. The original error is re-raised
            # unchanged below.
            print(f"Prepare failed for {revision}: the partial release "
                  f"directory {dest} and the journal remain. Reconcile "
                  f"the shared database first, then remove both by hand "
                  f"before retrying.", file=sys.stderr)
            raise
        j["phase"] = "ready"
        self.record(j)
        self.flip(j)
        if restart:
            self.finish(j)
        else:
            print("STAGED. Active pointer verified. Service restarts pending. Run --recover.")

    @staticmethod
    def extract(archive, stage, revision):
        with tarfile.open(fileobj=archive, mode="r:") as bundle:
            require(bundle.pax_headers.get("comment") == revision, "Archive commit does not match requested release")
            size = 0
            for member in bundle:
                size += member.size
                require(size <= 256 * 1024 * 1024, "Extracted content exceeds size limit")
                parts = member.name.split("/")
                require(all(part and part not in (".", "..") and not re.search(r"[:\\]", part)
                            for part in parts)
                        and (member.isdir() or member.isfile()),
                        f"Unsafe archive entry: {member.name!r}")
                path = stage.joinpath(*parts)
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as src, path.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
                        os.fchmod(dst.fileno(), 0o755 if member.mode & 0o111 else 0o644)
                        dst.flush()
                        os.fsync(dst.fileno())
        stage.chmod(0o755)
        for directory, _, _ in os.walk(stage, topdown=False):
            # Directory modes must not depend on root's umask: a
            # restrictive umask (e.g. 077) would leave nested code
            # directories unreadable by the sniper user. Files already
            # carry forced modes from extraction above.
            os.chmod(directory, 0o755)
            sync_dir(Path(directory))

    @staticmethod
    def fix_venv_modes(venv):
        """Make a release venv readable/executable by the sniper user
        regardless of root's umask. Directories become 0755; files keep
        executability (0755 if any execute bit is set, else 0644).
        Runs after pip install (the last writer) and before migrations.
        """
        for directory, _, files in os.walk(venv):
            os.chmod(directory, 0o755)
            for name in files:
                path = Path(directory) / name
                mode = path.stat().st_mode
                os.chmod(path, 0o755 if mode & 0o111 else 0o644)

    def prepare_services(self, dest):
        """Build the release's OWN venv and prove it before the flip.

        The old code pip-installed the new release's requirements into
        the shared /opt/sniper/.venv, so a rollback ran old code
        against new dependencies. Each release now carries its own
        venv inside its (immutable) release directory, and the units
        run the interpreter through the `current` symlink, so the venv
        switches atomically with the pointer and a rollback restores
        the old dependencies by construction.

        The venv is built after the staged directory is renamed to
        its final release directory, so its paths are stable from
        birth; it is root-owned and world-readable (services only
        execute and read it, never write to it). As belt-and-braces,
        no venv console script is ever executed by path — everything
        runs as `<venv>/bin/python -m <module>` — so nothing depends
        on a console script's shebang."""
        venv = dest / ".venv"
        venv_python = venv / "bin" / "python"
        env = ("systemd-run", "--quiet", "--collect", "--uid=sniper", "--gid=sniper",
               "-p", "EnvironmentFile=/etc/sniper/env",
               "-p", "Environment=SNIPER_LOG_DIR=/var/log/sniper",
               "-p", f"WorkingDirectory={dest}")
        self.run("python3.12", "-m", "venv", str(venv))
        self.run(str(venv_python), "-m", "pip",
                 "install", "--quiet", "-r", str(dest / "requirements.txt"))
        self.fix_venv_modes(venv)
        self.run(*env, "--wait", "--pipe", str(venv_python),
                 "-m", "migrations.runner")
        self.run("systemctl", "stop", "sniper-canary.service", check=False)
        try:
            self.run(*env, "--unit=sniper-canary", str(venv_python), "-m", "gunicorn",
                     "server:app", "--bind", "127.0.0.1:18765", "--workers", "1")
            self.health(18765)
        finally:
            self.run("systemctl", "stop", "sniper-canary.service")

    def status(self):
        evidence = {}
        for name, read in (("active", self.active), ("metadata", self.markers), ("journal", self.load)):
            try:
                evidence[name] = read()
            except Exception as error:
                evidence[name] = {"error": str(error)}
        print(json.dumps(evidence, sort_keys=True))
        print("Running service revision requires separate runtime evidence.")


def main(args):
    t = None
    try:
        require(os.geteuid() == 0, "Root operator access required")
        for directory in (ROOT, ROOT / "releases"):
            info = directory.lstat()
            require(stat.S_ISDIR(info.st_mode) and directory.resolve() == directory
                    and info.st_uid == 0 and not info.st_mode & 0o022,
                    f"Unsafe release directory ownership or permissions: {directory}")
        with host_lock(ROOT / ".release.lock") as fd:
            def run(*command, capture=False, check=True):
                return subprocess.run(command, text=True, capture_output=capture,
                                      check=check, pass_fds=(fd,))
            t = Transition(ROOT, run)
            mode, *params = args
            if mode == "status" and not params:
                t.status()
            elif mode == "recover" and not params:
                t.recover()
            elif mode == "rollback" and len(params) == 2:
                t.rollback(*params)
            elif mode == "deploy" and len(params) == 3 and params[2] in ("restart", "defer"):
                t.deploy(params[0], params[1], params[2] == "restart")
            else:
                raise RuntimeError("Invalid release operation")
    except RestartFailure as error:
        print(f"Release operation failed ({args[0] if args else 'unknown'}): "
              f"restart failed: service={error.service} operation={error.operation} "
              f"exit={error.exit_status} kind={error.kind}", file=sys.stderr)
        if t is not None:
            t.status()
        return 12
    except Exception as error:
        print(f"Release operation failed ({args[0] if args else 'unknown'}): {error}", file=sys.stderr)
        if t is not None:
            t.status()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
