"""`bosun update`: the plan, the rehearsal on a copy, the hold, the freeze and the release, and an
update cut short carried on or put back (design: docs/design/update.md, "The steps" and "Cut short").

This runs in the version updated to, ROOT: the wrapper (libexec/bosun-update) fetched it into its own
version folder and handed over. The version updated from is the one the install link leads to. The
update holds update.lock for its whole run, so one runs at a time, and records each step in
update.json (lib/update_state.py) before it starts it:

- planned, rehearsed: the plan accepted, then its migrations and checks passed on a copy of the data
  (update/rehearsal in the state folder, deleted whatever happens). Neither holds anything (R24).
- holding: the hold. bosun's own background work (the check-in loop, the watchdog, learning, the
  Slack watcher, the events dispatcher, the morning job) asks update_state.holding before each run
  and skips it while the hold is on; a run already going finishes, its writes waiting on the freeze
  like any other. The agents keep working.
- marked, backed-up, migrated, switched: the freeze. Under both stores' write locks (the ledger's,
  then the knowledge base's) every write waits: the marker; the backup, named in the record with
  what it restores once it is taken; the migrations; the switch of the install link; then each
  store's own check. Any failure puts back the backup and the link (update_gate.put_back_held: the
  locks are held) and records failed with the error. How long writes waited is freeze_ms.
- checked: writes go on. released: the jobs reloaded, the check-in loop restarted while the hold is
  still on (R36), then the hold lifted; what went wrong after the switch and what bosun doctor finds
  are kept as findings (reported, never rolled back); old backups and version folders pruned. done.

While it holds update.lock, this process never writes a store through the stores' write path (their
gate would see this very lock and refuse): the migrations write the files directly, and what the
update keeps goes in update.json.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import console_token
import install_paths
import knowledge
import knowledge_store
import launch_record
import ledger
import migrations
import update_backups
import update_gate
import update_plan
import update_state
import validation
import versions
from journal import JournalError
from knowledge_model import KnowledgeError
from update_plan import Plan, VersionFacts, WindowFacts
from update_state import UpdateState


ROOT = Path(__file__).resolve().parents[1]  # the version this updates to
SHORT = update_gate.SHORT
FINISHED = ("done", "failed", "restored")
BEFORE_FREEZE = ("planned", "rehearsed", "holding")
BLOCKED = 1
# A key in lib/common.sh's defaults: an assignment indented by two (as the example configs' check reads them).
CONFIG_KEY = re.compile(r"^  (BOSUN_[A-Z0-9_]+)=", re.MULTILINE)
WINDOW_KINDS = {"orchestrator": "orchestrator", "checkin": "checkin"}  # any other is a repo session's
COMMAND_SECONDS = 600  # the most a bosun command the update runs may take
DOCTOR_LINE = re.compile(r"^  (FAIL|warn)  (.*)$", re.MULTILINE)
PREFLIGHT_NEXT = "\nWhat to do next:"
STORE_ERRORS = (ledger.LedgerError, knowledge_store.StoreError, KnowledgeError, JournalError)


class UpdateRefused(Exception):
    """What stops an update before it changes anything, said in one line."""


@dataclass(frozen=True)
class Folders:
    """The state and config folders, and the tmux session, as `bosun update` passes them."""

    state_dir: Path
    config_dir: Path
    session: str


def folders() -> Folders:
    try:
        return Folders(
            Path(os.environ["BOSUN_STATE_DIR"]),
            Path(os.environ["BOSUN_CONFIG_DIR"]),
            os.environ.get("BOSUN_SESSION_NAME") or "bosun",
        )
    except KeyError as exc:
        raise UpdateRefused(f"{exc.args[0]} is not set: run it as bosun update") from exc


class Output:
    """Where the update's lines go: stdout, or stderr when stdout carries --json's one object."""

    to_stderr = False

    @classmethod
    def say(cls, line: str) -> None:
        print(line, file=sys.stderr if cls.to_stderr else sys.stdout, flush=True)


def _command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """<argv> run, its output kept; one that outlasts COMMAND_SECONDS stopped, as a failure."""
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, errors="replace", check=False, timeout=COMMAND_SECONDS
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 1, "", f"{' '.join(argv[:3])} took over {COMMAND_SECONDS} s")


def _bosun(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """`bosun <args>` of the version in <root>."""
    return _command([str(root / "bin" / "bosun"), *args])


def _shell(root: Path, script: str) -> subprocess.CompletedProcess[str]:
    """<script> run by bash with the config loaded by the version in <root> (lib/common.sh)."""
    return _command(["bash", "-c", f'source "$1/lib/common.sh"; load_config; {script}', "_", str(root)])


def _git(root: Path, *args: str) -> str:
    """git in <root>, with no inherited GIT_* variables and none of the user's hooks; its output."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
        check=False,
    )
    return done.stdout if done.returncode == 0 else ""


# ---- the facts each version gives the plan ------------------------------------------------------


def prompts(root: Path) -> dict[str, str]:
    """Each prompt file of the version in <root> (update_plan.PROMPTS), by its path there, and the
    SHA-256 of its content."""
    found: dict[str, str] = {}
    for pattern in update_plan.PROMPTS:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def config_keys(root: Path) -> frozenset[str]:
    """The config keys the version in <root> has a default for (lib/common.sh)."""
    try:
        return frozenset(CONFIG_KEY.findall((root / "lib" / "common.sh").read_text()))
    except OSError:
        return frozenset()


def _source_int(path: Path, name: str) -> int:
    """The whole number <name> is set to at the top level of the Python file <path>; 0 if it is not.
    Read from the source, so that no other version's code runs in this process."""
    try:
        found = re.search(rf"^{name} = (\d+)$", path.read_text(), re.MULTILINE)
    except OSError:
        return 0
    return int(found.group(1)) if found else 0


def rules(root: Path) -> dict[str, int]:
    """The rules version of each store's journal in the version in <root>."""
    return {
        "ledger": _source_int(root / "lib" / "ledger.py", "RULES_VERSION"),
        "knowledge": _source_int(root / "lib" / "knowledge.py", "KG_RULES_VERSION"),
    }


def store_formats(state_dir: Path, config_dir: Path) -> tuple[dict[str, int], list[str]]:
    """Each store's format on disk (0: none yet), and a blocker for each one whose format cannot be
    read: a store this code cannot tell the format of is never migrated, nor backed up and put back."""
    ctx = migrations.Context(state_dir, config_dir)
    found: dict[str, int] = {}
    problems: list[str] = []
    for store in migrations.FORMATS:
        try:
            found[store] = migrations.store_format(ctx, store)
        except STORE_ERRORS as exc:
            problems.append(f"the {store}'s format cannot be read: {exc}")
    return found, problems


def live_facts(state_dir: Path, config_dir: Path, old_root: Path) -> VersionFacts:
    """The running version's facts: its code in <old_root>, its stores' formats on disk (a store
    whose format cannot be read left out: store_formats says why)."""
    return VersionFacts(
        commit=versions.commit_of(old_root),
        formats=store_formats(state_dir, config_dir)[0],
        rules=rules(old_root),
        keys=config_keys(old_root),
        prompts=prompts(old_root),
    )


def new_facts(root: Path) -> VersionFacts:
    """The new version's facts: <root> is this code's folder, whose formats are migrations.FORMATS."""
    return VersionFacts(
        commit=versions.commit_of(root),
        formats=dict(migrations.FORMATS),
        rules=rules(root),
        keys=config_keys(root),
        prompts=prompts(root),
    )


def launch_changed(root: Path, name: str, state_dir: Path, session: str) -> bool:
    """Whether window <name> would be launched now, by the version in <root>, with anything other
    than what its last launch recorded. `launch --fingerprint` of that version reads the files under
    the install from <root> itself, so this holds before the switch too (R35). A window with no
    record, or one the fingerprint refuses, counts as changed: what it runs with is unknown."""
    recorded = launch_record.recorded(state_dir, session, name)
    done = _bosun(root, "launch", "--fingerprint", name)
    if recorded is None or done.returncode != 0:
        return True
    try:
        now = json.loads(done.stdout)
    except ValueError:
        return True
    return bool(recorded.get("fingerprint") != now)


def _window(root: Path, window: dict[str, Any], state_dir: Path, session: str) -> WindowFacts:
    name = str(window.get("name"))
    kind = WINDOW_KINDS.get(str(window.get("kind")), "repo")
    agent = window.get("agent")
    status = str(agent.get("status") or "unknown") if isinstance(agent, dict) else "exited"
    changed = kind != "checkin" and launch_changed(root, name, state_dir, session)
    return WindowFacts(name, kind, status, changed)


def _open_windows(root: Path) -> list[dict[str, Any]]:
    """The windows open in the session (`bosun sessions --json` of the version in <root>)."""
    try:
        listed = json.loads(_bosun(root, "sessions", "--json").stdout or "[]")
    except ValueError:
        return []
    return [w for w in listed if isinstance(w, dict) and w.get("window")] if isinstance(listed, list) else []


def window_facts(root: Path) -> list[WindowFacts]:
    """Each window open in the session: its kind, its agent's status, and whether what the version in
    <root> would launch it with differs from what it was launched with (launch_changed)."""
    where = folders()
    return [_window(root, w, where.state_dir, where.session) for w in _open_windows(root)]


def missing(root: Path) -> list[str]:
    """What the version in <root> needs and this Mac lacks (its `bosun preflight`), as blockers;
    nothing with BOSUN_SKIP_PREFLIGHT=1, as for `bosun init`."""
    if os.environ.get("BOSUN_SKIP_PREFLIGHT") == "1":
        return []
    done = _bosun(root, "preflight", "--quiet")
    if done.returncode == 0:
        return []
    said = done.stdout.split(PREFLIGHT_NEXT, 1)[0]
    lines = [" ".join(line.split()) for line in said.splitlines() if line.startswith("  ")]
    if not lines:
        last = (done.stdout + done.stderr).strip().splitlines()[-1:] or ["it failed"]
        return [f"the new version's preflight: {last[0]}"]
    return [f"the new version needs what this Mac lacks: {line} (bosun preflight says how)" for line in lines]


def console_running(state_dir: Path) -> bool:
    """Whether a console runs on the stores in <state_dir> (it holds a lock in its state folder)."""
    return console_token.running(state_dir)


def in_flight(state_dir: Path, config_dir: Path) -> list[tuple[str, str]]:
    """Each item handed to a repo session whose work is not done, and its task type, where the
    session got the type's built-in text: a type the site replaces in $BOSUN_CONFIG_DIR/tasks/ is
    left out, since an update never changes the site's file."""
    try:
        items = ledger.load_at(state_dir)["items"]
    except ledger.LedgerError:
        return []  # store_formats names it
    found = []
    for item_id, item in items.items():
        delegation = item.get("delegation")
        if not isinstance(delegation, dict) or not ledger.is_open(item) or not ledger.awaiting_report(item):
            continue
        task = str(delegation.get("task") or "")
        if task and not (config_dir / "tasks" / f"{task}.md").is_file():
            found.append((item_id, task))
    return sorted(found)


def deletion_cut_short(state_dir: Path, config_dir: Path) -> tuple[bool, list[str]]:
    """Whether a deletion of a person in the knowledge base was cut short (knowledge.
    deletion_cut_short), and a blocker if the knowledge base cannot be read to tell."""
    knowledge.configure(config_dir / update_gate.KNOWLEDGE_DIR, git=False, state_dir=str(state_dir))
    try:
        return knowledge.deletion_cut_short(), []
    except (*STORE_ERRORS, OSError) as exc:
        return False, [f"the knowledge base cannot be read to check for a deletion cut short: {exc}"]


def dangling_links(state_dir: Path, config_dir: Path) -> list[str]:
    """A blocker for each link among what a backup copies (update_backups.backup_set) that leads
    nowhere: the backup could not copy it, and would fail inside the freeze."""
    return [
        f"{path} is a link to {os.readlink(path)}, which is not there, so no backup can copy it: "
        "remove the link, or put back what it leads to"
        for _relative, path in update_backups.backup_set(state_dir, config_dir)
        if path.is_symlink() and not path.exists()
    ]


def install_problem(old_root: Path, new_root: Path) -> list[str]:
    """A blocker unless both versions are folders beside the one install link."""
    install = install_paths.install_path(new_root)
    if install == new_root or install_paths.install_path(old_root) != install or not install.is_symlink():
        problem = (
            f"bosun update moves an install with its versions side by side ({install} a link to one of "
            f"them); {old_root} and {new_root} are not that"
        )
        return [problem]
    return []


def config_renames(config_dir: Path) -> list[tuple[str, str]]:
    """Each config key bosun.env sets that this version renamed or removed, and its name now ("":
    removed)."""
    try:
        text = (config_dir / migrations.CONFIG_FILE).read_bytes().decode("utf-8", "surrogateescape")
    except FileNotFoundError:
        return []
    assigned = {m["key"] for line in text.splitlines() if (m := migrations.ASSIGNMENT.match(line))}
    return sorted((old, new) for old, new in migrations.config_names().items() if old in assigned)


def jobs(root: Path) -> tuple[list[str], list[str]]:
    """The launchd jobs the version in <root> would reload (reload_changed_jobs --dry-run), and a
    blocker if it cannot render them."""
    done = _shell(root, "reload_changed_jobs --dry-run")
    if done.returncode != 0:
        return [], [f"the new version cannot render its launchd jobs: {done.stderr.strip() or 'it failed'}"]
    return done.stdout.split(), []


def commits(root: Path, old: str, new: str) -> list[str]:
    """The commits from <old> to <new>, newest first, as "<short> <subject>"."""
    return _git(root, "log", "--format=%h %s", f"{old}..{new}").splitlines()


def _steps(store: str, found: int, target: int) -> list[tuple[int, int]]:
    return [(m.from_version, m.to_version) for m in migrations.path(store, found, target)]


def plan_for(old_root: Path, new_root: Path, state_dir: Path, config_dir: Path) -> Plan:
    """The plan from the version in <old_root> to the one in <new_root> (this code), for the data in
    <state_dir> and <config_dir>: update_plan.make_plan over the facts gathered here, with what else
    stops an update (a store whose format cannot be read, a link a backup cannot copy, versions not
    side by side) among its blockers."""
    old, new = live_facts(state_dir, config_dir, old_root), new_facts(new_root)
    cut_short, unreadable_kb = deletion_cut_short(state_dir, config_dir)
    reloads, unrenderable = jobs(new_root)
    plan = update_plan.make_plan(
        old,
        new,
        commits=commits(new_root, old.commit, new.commit),
        windows=window_facts(new_root),
        jobs=reloads,
        renames=config_renames(config_dir),
        user_changes=migrations.USER_FILE_CHANGES,
        in_flight=in_flight(state_dir, config_dir),
        missing=missing(new_root),
        deletion_cut_short=cut_short,
        console=console_running(state_dir),
        steps=_steps,
    )
    extra = [
        *store_formats(state_dir, config_dir)[1],
        *unreadable_kb,
        *unrenderable,
        *dangling_links(state_dir, config_dir),
        *install_problem(old_root, new_root),
    ]
    return dataclasses.replace(plan, blockers=(*plan.blockers, *extra))


# ---- the rehearsal -----------------------------------------------------------------------------


@contextlib.contextmanager
def _read_locks(state_dir: Path, config_dir: Path) -> Iterator[None]:
    """Both stores' locks, shared, in the one order: a copy taken under them is whole, and writes
    wait only while it is taken (APFS clones)."""
    with (
        ledger.lock_at(state_dir, fcntl.LOCK_SH),
        knowledge_store.locked(knowledge_store.Paths(config_dir / update_gate.KNOWLEDGE_DIR), False),
    ):
        yield


def check_stores(state_dir: Path, config_dir: Path) -> list[str]:
    """What each store's own check finds that refuses writes or would lose data (validation.ERROR),
    a line each. It takes no lock: the caller holds them, or the folders are a copy."""
    found = [("the ledger", f) for f in ledger.validate_unlocked(state_dir)]
    kb = knowledge_store.Paths(config_dir / update_gate.KNOWLEDGE_DIR)
    if kb.root.is_dir():
        found += [("the knowledge base", f) for f in knowledge.validate_unlocked(kb)]
    return [
        f"{store}: {validation.finding_line(f)}" for store, f in found if f["severity"] == validation.ERROR
    ]


def _migrate(ctx: migrations.Context, plan: Plan) -> None:
    """The migrations, and the config's renamed keys when the plan has any."""
    migrations.run(ctx)
    if plan.renames:
        migrations.write_config(ctx.config_dir, datetime.now(UTC))


def rehearse(state_dir: Path, config_dir: Path, plan: Plan) -> list[str]:
    """The new version's migrations and checks run on a copy of the data (update/rehearsal in the
    state folder, taken under the stores' shared locks): what failed, nothing when they passed. The
    copy is deleted whatever happens."""
    copy = state_dir / update_backups.REHEARSAL
    shutil.rmtree(copy, ignore_errors=True)
    try:
        with _read_locks(state_dir, config_dir):
            update_backups.copy_set(state_dir, config_dir, copy)
        ctx = migrations.Context(copy / "state", copy / "config")
        _migrate(ctx, plan)
        return check_stores(ctx.state_dir, ctx.config_dir)
    except Exception as exc:  # noqa: BLE001 - whatever a migration raises stops the update, nothing changed
        return [f"{type(exc).__name__}: {exc}"]
    finally:
        shutil.rmtree(copy, ignore_errors=True)


# ---- the freeze ---------------------------------------------------------------------------------


class UpdateFailed(Exception):
    """A store's own check failed after the migrations and the switch."""


def _record(state_dir: Path, state: UpdateState) -> UpdateState:
    update_state.write(state_dir, state)
    return state


def _step(state_dir: Path, state: UpdateState, step: str, **fields: Any) -> UpdateState:
    return _record(state_dir, update_state.advanced(state, step, **fields))


def restorable(plan: Plan) -> tuple[str, ...]:
    """What a failed freeze puts back: what its locks cover, and bosun.env when it renames keys."""
    return (*update_backups.FROZEN, *(("config/" + migrations.CONFIG_FILE,) if plan.renames else ()))


def _frozen(state: UpdateState, plan: Plan, state_dir: Path, config_dir: Path) -> UpdateState:
    """The steps under the locks, each recorded before it starts; `backup` and `restorable` once
    the backup is taken, so no step past backed-up is ever recorded without them."""
    ctx = migrations.Context(state_dir, config_dir)
    state = _step(state_dir, state, "marked")
    state = _step(state_dir, state, "backed-up")
    formats = migrations.store_formats(ctx)
    backup = update_backups.take(
        state_dir, config_dir, state.from_commit, state.to_commit, formats, datetime.now(UTC)
    )
    state = _record(state_dir, dataclasses.replace(state, backup=str(backup), restorable=restorable(plan)))
    state = _step(state_dir, state, "migrated")
    _migrate(ctx, plan)
    state = _step(state_dir, state, "switched")
    to_root = Path(state.to_root)
    versions.point(install_paths.install_path(to_root), to_root)
    problems = check_stores(state_dir, config_dir)
    if problems:
        raise UpdateFailed("the stores' checks failed after the switch: " + "; ".join(problems))
    return _step(state_dir, state, "checked")


def _error_of(exc: BaseException) -> str:
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted (Ctrl-C)"
    return str(exc) or type(exc).__name__


def _put_back(state_dir: Path, config_dir: Path, state: UpdateState, exc: BaseException) -> UpdateState:
    """The freeze put back (the locks held) and `failed` recorded with <exc>, from the step the
    record had reached (<state> is where the freeze began). If that cannot be done, the record keeps
    its step, so writes stay refused until `bosun update` puts it back."""
    error = _error_of(exc)
    state = update_state.read(state_dir) or state
    try:
        restored = update_gate.put_back_held(state_dir, config_dir, state)
    except Exception as again:  # noqa: BLE001 - whatever stops the put back is said, writes stay paused
        stuck = dataclasses.replace(state, error=f"{error}; putting it back failed too: {again}")
        with contextlib.suppress(OSError):
            update_state.write(state_dir, stuck)
        return stuck
    return _step(state_dir, restored, "failed", error=error)


def freeze(state: UpdateState, plan: Plan, state_dir: Path, config_dir: Path) -> UpdateState:
    """Back up, migrate, switch and check under both stores' write locks (update_gate.store_locks:
    the ledger's, then the knowledge base's), each step recorded before it starts. Any failure,
    Ctrl-C included, puts it all back and records `failed` with the error. Records how long writes
    waited, freeze_ms."""
    started = time.monotonic()
    with update_gate.store_locks(state_dir, config_dir):
        try:
            state = _frozen(state, plan, state_dir, config_dir)
        except BaseException as exc:  # noqa: BLE001 - every failure inside the freeze is put back
            state = _put_back(state_dir, config_dir, state, exc)
    freeze_ms = round((time.monotonic() - started) * 1000)
    return _record(state_dir, dataclasses.replace(state, freeze_ms=freeze_ms))


# ---- the release --------------------------------------------------------------------------------


def _reload_jobs(root: Path) -> list[str]:
    """The launchd jobs whose plist the version in <root> changes, reloaded; a finding if not."""
    done = _shell(root, "reload_changed_jobs")
    for label in done.stdout.split():
        Output.say(f"  reloaded the launchd job {label}")
    return (
        [] if done.returncode == 0 else [f"launchd: {done.stderr.strip() or 'a job could not be reloaded'}"]
    )


def _restart_checkin_loop(root: Path) -> list[str]:
    """The check-in loop restarted on the version in <root>, if a checkin window is open; a finding
    if that fails."""
    if not any(w.get("kind") == "checkin" for w in _open_windows(root)):
        return []
    done = _bosun(root, "refresh", "--checkin-loop")
    if done.returncode == 0:
        Output.say("  restarted the check-in loop")
        return []
    return [f"the check-in loop: {done.stderr.strip() or 'it could not be restarted'}"]


def _doctor(root: Path) -> list[str]:
    """What `bosun doctor` of the version in <root> fails or warns about, a line each."""
    done = _bosun(root, "doctor")
    return [f"{kind}: {what}" for kind, what in DOCTOR_LINE.findall(done.stdout)]


def _prune(state_dir: Path, from_root: Path, to_root: Path) -> None:
    for gone in update_backups.prune(state_dir):
        Output.say(f"  deleted the old backup {gone.name}")
    install = install_paths.install_path(to_root)
    for gone in versions.prune(install_paths.versions_dir(install), keep=(to_root, from_root)):
        Output.say(f"  deleted the old version {gone.name}")


def release(state: UpdateState) -> UpdateState:
    """After the freeze: the launchd jobs whose plist changed reloaded, the check-in loop restarted
    while the hold is still on (R36), then the hold lifted (`released`); what went wrong there and
    what the new version's `bosun doctor` finds kept as findings, reported, never rolled back (writes
    may have landed since); old backups and version folders pruned."""
    where = folders()
    to_root = Path(state.to_root)
    found = [*_reload_jobs(to_root), *_restart_checkin_loop(to_root)]
    state = _step(where.state_dir, state, "released")
    Output.say("  lifted the hold on bosun's background work")
    state = _record(where.state_dir, dataclasses.replace(state, findings=(*found, *_doctor(to_root))))
    _prune(where.state_dir, Path(state.from_root), to_root)
    return state


# ---- an update cut short ------------------------------------------------------------------------


def _carry_on(state_dir: Path) -> UpdateState | None:
    """Under update.lock: the record read again, then an update cut short before the freeze recorded
    as failed (only the hold had changed, and it lapsed), one past its checks rolled forward."""
    state = update_state.read(state_dir)
    if state is None or state.step in FINISHED:
        return state
    if state.step in BEFORE_FREEZE:
        return _step(state_dir, state, "failed", error=f"cut short at step {state.step}; nothing had changed")
    if state.step == "checked":
        state = release(state)
    return _step(state_dir, state, "done")


def resume(state_dir: Path) -> UpdateState | None:
    """An update recorded and not running, carried on from where it stopped: before the freeze, its
    hold lifted; inside it, put back (update_gate.recover); after `checked`, on from the step after
    the one recorded. Returns where it ended; None when there was nothing to carry on (no record,
    one finished, or one still running, which one_update refuses next)."""
    state = update_state.load(state_dir)
    if state is None or state.step in FINISHED or update_state.running(state_dir):
        return None
    if not update_state.known(state):
        raise UpdateRefused(
            f"the update's record is at step {state.step}, which a newer bosun wrote: carry it on with "
            f"{state.to_root}/bin/bosun update"
        )
    if update_state.in_freeze(state):
        update_gate.recover(state_dir, folders().config_dir)
        return update_state.read(state_dir)
    with update_state.one_update(state_dir):
        return _carry_on(state_dir)


# ---- the command --------------------------------------------------------------------------------


def _short(commit: str) -> str:
    return commit[:SHORT]


def _show(plan: Plan, as_json: bool) -> None:
    if not as_json:
        Output.say(update_plan.render_plan(plan).rstrip("\n"))


def _go_ahead(yes: bool) -> bool:
    """--yes, or yes to the question in a terminal; without one, the plan alone."""
    if yes:
        return True
    if not sys.stdin.isatty():
        Output.say("Nothing has changed: in a terminal it asks first, and bosun update --yes goes ahead.")
        return False
    print("Update now? [y/N] ", end="", file=sys.stderr if Output.to_stderr else sys.stdout, flush=True)
    answer = input().strip().lower()
    if answer in ("y", "yes"):
        return True
    Output.say("Nothing has changed.")
    return False


def _started(plan: Plan, old_root: Path, new_root: Path) -> UpdateState:
    now = datetime.now(UTC)
    return UpdateState(
        id=f"{now:%Y%m%d-%H%M%S}-{_short(plan.from_commit)}-{_short(plan.to_commit)}",
        from_root=str(old_root),
        to_root=str(new_root),
        from_commit=plan.from_commit,
        to_commit=plan.to_commit,
        step="planned",
        started_at=now.isoformat(timespec="seconds"),
        hold_until=0,
        user_changes=plan.user_changes,
    )


def _failed(state: UpdateState) -> int:
    if state.step in update_state.FREEZE_STEPS:
        Output.say(
            f"bosun update failed and could not put everything back: {state.error}. Writes stay "
            "paused until bosun update puts it back; run it again."
        )
    else:
        Output.say(
            f"bosun update failed: {state.error}. Everything is as it was ({_short(state.from_commit)})."
        )
    return 1


def _rehearsed(state: UpdateState, plan: Plan, where: Folders) -> UpdateState:
    """The rehearsal; `rehearsed` recorded if it passed, else `failed` with what it found."""
    Output.say("Rehearsing on a copy of the data...")
    found = rehearse(where.state_dir, where.config_dir, plan)
    if found:
        return _step(where.state_dir, state, "failed", error="the rehearsal failed: " + "; ".join(found))
    return _step(where.state_dir, state, "rehearsed")


def _carried_out(plan: Plan, old_root: Path, new_root: Path, where: Folders) -> UpdateState:
    """Recorded from `planned` on: rehearsal, hold, freeze, release, done (or where it stopped)."""
    state = _rehearsed(_record(where.state_dir, _started(plan, old_root, new_root)), plan, where)
    if state.step == "failed":
        return state
    hold_until = int(time.time()) + update_state.HOLD_SECONDS
    state = _step(where.state_dir, state, "holding", hold_until=hold_until)
    Output.say("Holding bosun's background work; pausing writes to back up, migrate, switch and check...")
    state = freeze(state, plan, where.state_dir, where.config_dir)
    if state.step != "checked":
        return state
    Output.say(f"Writes went on after {state.freeze_ms} ms. Releasing...")
    return _step(where.state_dir, release(state), "done")


def _said_done(state: UpdateState) -> int:
    Output.say(f"bosun updated: {_short(state.from_commit)} -> {_short(state.to_commit)}")
    Output.say(f"  writes paused for {state.freeze_ms} ms; the backup: {state.backup}")
    for finding in state.findings:
        Output.say(f"  {finding}")
    return 0


def _update(
    old_root: Path, new_root: Path, where: Folders, args: argparse.Namespace
) -> tuple[int, Plan, UpdateState | None]:
    """Under update.lock: the plan, shown; then, said yes to, carried out."""
    plan = plan_for(old_root, new_root, where.state_dir, where.config_dir)
    _show(plan, args.json)
    if plan.blockers or not _go_ahead(args.yes):
        return (BLOCKED if plan.blockers else 0), plan, None
    state = _carried_out(plan, old_root, new_root, where)
    return (_said_done(state) if state.step == "done" else _failed(state)), plan, state


def _dry_run(old_root: Path, new_root: Path, where: Folders, args: argparse.Namespace) -> tuple[int, Plan]:
    running = update_state.read(where.state_dir)
    if update_state.running(where.state_dir):
        raise update_state.UpdateRunning(*_who(running))
    if running is not None and running.step not in FINISHED:
        Output.say(
            f"(an update stopped at step {running.step}: bosun update without --dry-run carries it on)"
        )
    plan = plan_for(old_root, new_root, where.state_dir, where.config_dir)
    _show(plan, args.json)
    Output.say("A dry run: nothing has changed.")
    return (BLOCKED if plan.blockers else 0), plan


def _who(state: UpdateState | None) -> tuple[str, str, str]:
    return (state.started_at, state.from_commit, state.to_commit) if state else ("", "", "")


def installed(from_root: Path) -> Path:
    """The version the install link leads to now (an update put back may have moved it since the
    wrapper handed over); <from_root> where the install is no link."""
    current = versions.current(install_paths.install_path(ROOT))
    return current.resolve() if current is not None else from_root.resolve()


def _resumed_line(state: UpdateState) -> str:
    what = {"restored": "put back", "failed": "ended (it was cut short before anything changed)"}
    return (
        f"An update from {_short(state.from_commit)} to {_short(state.to_commit)} that was cut short: "
        f"{what.get(state.step, 'carried on to the end')}."
    )


def run(args: argparse.Namespace) -> int:
    """`bosun update`, handed over by the wrapper: an update cut short carried on first (not in a dry
    run), then up to date, or the plan and, said yes to, the update."""
    where = folders()
    Output.to_stderr = args.json
    if not args.dry_run and (resumed := resume(where.state_dir)) is not None:
        Output.say(_resumed_line(resumed))
    old_root, new_root = installed(Path(args.from_root)), ROOT
    if versions.commit_of(old_root) == versions.commit_of(new_root):
        Output.say(f"bosun is up to date ({_short(versions.commit_of(new_root))}).")
        return 0
    state: UpdateState | None = None
    if args.dry_run:
        code, plan = _dry_run(old_root, new_root, where, args)
    else:
        with update_state.one_update(where.state_dir):
            code, plan, state = _update(old_root, new_root, where, args)
    if args.json:
        update = dataclasses.asdict(state) if state else None
        print(json.dumps({"plan": dataclasses.asdict(plan), "update": update}, indent=2))
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun update", allow_abbrev=False)
    parser.add_argument("--from", dest="from_root", required=True, help="the version the wrapper ran")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _unreadable(exc: update_state.RecordUnreadable) -> str:
    return (
        f"{exc}, so it cannot tell whether an update was cut short while writes were paused (its backup "
        f"would be the newest in backups/ in the state folder). Every write refuses while it is there: "
        f"move {exc.path.name} aside to go on"
    )


def main(argv: Sequence[str]) -> int:
    args = build_parser().parse_args(list(argv))
    try:
        return run(args)
    except update_state.RecordUnreadable as exc:
        print(f"bosun update: {_unreadable(exc)}", file=sys.stderr)
    except (UpdateRefused, update_state.UpdateRunning) as exc:
        print(f"bosun update: {exc}", file=sys.stderr)
    except update_backups.BackupError as exc:
        print(f"bosun update: an update cut short could not be put back: {exc}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print(
            "\nbosun update: stopped; bosun update again carries on, or puts back, what it started",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
