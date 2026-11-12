"""`bosun update`: the plan, the rehearsal on a copy, the hold, the freeze and the release, and an
update cut short carried on or put back (design: docs/design/update.md, "The steps" and "Cut short").

This runs in the version updated to, ROOT: the wrapper (libexec/bosun-update) fetched it into its own
version folder and handed over. The version updated from is the one the install link leads to. The
facts its plan is made from are gathered by lib/update_facts.py. The
update holds update.lock for its whole run, so one runs at a time, and records each step in
update.json (lib/update_state.py) before it starts it:

- planned, rehearsed: the plan accepted, then its migrations and checks passed on a copy of the data
  (update/rehearsal in the state folder, deleted whatever happens). Neither holds anything.
- holding: the hold. bosun's own background work (the check-in loop, the watchdog, learning, the
  Slack watcher, the events dispatcher, the morning job) asks update_state.holding before each run
  and skips it while the hold is on; a run already going finishes, its writes waiting on the freeze
  like any other. The agents keep working.
- marked, backed-up, migrated, switched: the freeze. Under both stores' write locks (the ledger's,
  then the knowledge base's) every write waits: the marker; the backup, named in the record with
  what it restores once it is taken; the migrations; the switch of the install link; then each
  store's own check. Any failure puts back the backup and the link (update_gate.put_back_held: the
  locks are held) and records failed with the error. How long writes were paused, from the locks
  taken to their release, is freeze_ms.
- checked: writes go on. released: the jobs reloaded, the check-in loop restarted while the hold is
  still on, then the hold lifted; what went wrong after the switch and what bosun doctor finds
  are kept as findings (reported, never rolled back); old backups and version folders pruned.
- agents: each window the plan gives something to do is brought along (lib/update_agents.py decides,
  lib/update_windows.py carries it out) until it answers with `bosun update note`; Ctrl-C leaves those
  still waited for to the check-in loop, whose `bosun update advance` carries on. done once none is
  left.
- the summary (lib/update_summary.py): what the update did, built from its record, where each step
  kept what it learnt (the plan when it starts, the migrations' lines and bosun.env's change at the
  switch, the jobs reloaded in the release), so an update carried on says the same. It is said when
  the update ends (also when Ctrl-C stops its wait), and saved as summary.md in the backup folder once
  it is done, by whichever run finishes it: this one, the next `bosun update`, or the check-in loop.

While it holds update.lock, this process never writes a store through the stores' write path (their
gate would see this very lock and refuse): the migrations write the files directly, and what the
update keeps goes in update.json.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import json
import os
import shutil
import signal
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import checkout_work
import install_paths
import knowledge
import knowledge_store
import ledger
import migrations
import update_agents
import update_backups
import update_facts
import update_gate
import update_notes
import update_plan
import update_state
import update_summary
import update_windows
import validation
import versions
from update_facts import Folders, UpdateRefused, folders
from update_plan import Plan
from update_state import UpdateState


ROOT = Path(__file__).resolve().parents[1]  # the version this updates to
SHORT = update_gate.SHORT
FINISHED = ("done", "failed", "restored")
ENDED = ("done", "agents")  # past its release: done, or its windows left to the check-in loop
BEFORE_FREEZE = ("planned", "rehearsed", "holding")
BLOCKED = 1
USAGE = 2
AGENT_COMMANDS = ("note", "advance")  # what the agents and the check-in loop run (lib/update_windows.py)
AFTER_UPDATE = "after-update"  # what bosun doctor runs: what the last update left me
AHEAD_ENV = "BOSUN_UPDATE_AHEAD"  # the wrapper sets it to the newest release when the install is past it


class Output:
    """Where the update's lines go: stdout, or stderr when stdout carries --json's one object."""

    to_stderr = False

    @classmethod
    def say(cls, line: str) -> None:
        print(line, file=sys.stderr if cls.to_stderr else sys.stdout, flush=True)


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


def changes_config(plan: Plan) -> bool:
    """Whether the update may change bosun.env: it renames keys bosun.env sets, or adds one with the
    value that keeps what an older install did (migrations.kept_values). The one condition for both
    writing it (_migrate) and putting it back after a failed freeze (restorable)."""
    return bool(plan.renames) or bool(migrations.kept_values(plan.keys_added))


def _migrate(
    ctx: migrations.Context, plan: Plan, now: datetime
) -> tuple[list[migrations.Applied], list[str]]:
    """The migrations, and the config's renamed and kept keys when the plan may change it
    (changes_config; the copy of bosun.env kept as it was named for <now>): each migration that ran,
    and bosun.env's line for the summary."""
    applied = migrations.run(ctx)
    changed = migrations.write_config(ctx.config_dir, now, plan.keys_added) if changes_config(plan) else []
    if not changed:
        return applied, []
    kept = migrations.config_copy(ctx.config_dir, now).name
    return applied, [f"{migrations.CONFIG_FILE}: {'; '.join(changed)} (before: {kept})"]


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
        _migrate(ctx, plan, datetime.now(UTC))
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
    """What a failed freeze puts back: what its locks cover, and bosun.env when the update may change
    it (changes_config)."""
    return (*update_backups.FROZEN, *(("config/" + migrations.CONFIG_FILE,) if changes_config(plan) else ()))


def _frozen(state: UpdateState, plan: Plan, state_dir: Path, config_dir: Path) -> UpdateState:
    """The steps under the locks, each recorded before it starts; `backup` and `restorable` once
    the backup is taken, so no step past backed-up is ever recorded without them; what the
    migrations did with the switch. The backup and the copy of bosun.env have the same time."""
    ctx = migrations.Context(state_dir, config_dir)
    now = datetime.now(UTC)
    state = _step(state_dir, state, "marked")
    state = _step(state_dir, state, "backed-up")
    formats = migrations.store_formats(ctx)
    backup = update_backups.take(state_dir, config_dir, state.from_commit, state.to_commit, formats, now)
    state = _record(state_dir, dataclasses.replace(state, backup=str(backup), restorable=restorable(plan)))
    state = _step(state_dir, state, "migrated")
    applied, config = _migrate(ctx, plan, now)
    migrated = tuple(step.as_record() for step in applied)
    state = _switch(state_dir, state, migrated=migrated, config_changed=tuple(config))
    problems = check_stores(state_dir, config_dir)
    if problems:
        raise UpdateFailed("the stores' checks failed after the switch: " + "; ".join(problems))
    return _step(state_dir, state, "checked")


def _switch(state_dir: Path, state: UpdateState, **fields: Any) -> UpdateState:
    """`switched` recorded with <fields>, then the install led to the new version in one step
    (versions.switch). From the switch on, the record names the version updated from where the switch
    leaves it: a checkout from before versions in its legacy folder, where a put back leads the link
    (update_gate's _point_back), never the install path, which is the link by then."""
    to_root = Path(state.to_root)
    install = install_paths.install_path(to_root)
    from_root = versions.switched_from(install, to_root, Path(state.from_root), state.from_commit)
    state = _step(state_dir, state, "switched", from_root=str(from_root), **fields)
    versions.switch(install, to_root, from_root)  # a checkout goes where the record now names it
    return state


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
    were paused, freeze_ms: from the locks taken to their release (a writer that held a lock when the
    freeze began delays it, but paused nothing meanwhile)."""
    with update_gate.store_locks(state_dir, config_dir):
        started = time.monotonic()
        try:
            state = _frozen(state, plan, state_dir, config_dir)
        except BaseException as exc:  # noqa: BLE001 - every failure inside the freeze is put back
            state = _put_back(state_dir, config_dir, state, exc)
    freeze_ms = round((time.monotonic() - started) * 1000)
    return _record(state_dir, dataclasses.replace(state, freeze_ms=freeze_ms))


# ---- the release --------------------------------------------------------------------------------


def _reload_jobs(root: Path) -> tuple[list[str], list[str]]:
    """The launchd jobs whose plist the version in <root> changes, reloaded: their labels, and a
    finding if one could not be."""
    done = update_facts.shell(root, "reload_changed_jobs")
    labels = done.stdout.split()
    for label in labels:
        Output.say(f"  reloaded the launchd job {label}")
    found = (
        [] if done.returncode == 0 else [f"launchd: {done.stderr.strip() or 'a job could not be reloaded'}"]
    )
    return labels, found


def _restart_checkin_loop(root: Path) -> list[str]:
    """The check-in loop restarted on the version in <root>, if a checkin window is open; a finding
    if that fails."""
    if not update_facts.loop_runs(root):
        return []
    done = update_facts.bosun(root, "refresh", "--checkin-loop")
    if done.returncode == 0:
        Output.say("  restarted the check-in loop")
        return []
    return [f"the check-in loop: {done.stderr.strip() or 'it could not be restarted'}"]


def _doctor(root: Path) -> list[str]:
    """What `bosun doctor` of the version in <root> fails or warns about, a line each, its detail
    joined onto it (update_summary.doctor_found)."""
    return update_summary.doctor_found(update_facts.bosun(root, "doctor").stdout)


def _doctor_fails(root: Path) -> tuple[str, ...]:
    """What `bosun doctor` of the version in <root> fails at (_doctor): run while planning, before the
    hold, so the summary lists only what fails after the switch that did not before."""
    return tuple(line for line in _doctor(root) if line.startswith(update_summary.FAIL))


def _started_time(state: UpdateState) -> float:
    """When the update <state> records started, as time.time() tells it; 0 if that cannot be read."""
    try:
        started = datetime.fromisoformat(state.started_at)
    except ValueError:
        return 0
    return (started if started.tzinfo else started.replace(tzinfo=UTC)).timestamp()


def _kept_as(folder: Path) -> str:
    """What a folder prune kept is, for its finding."""
    if folder.name.startswith(versions.LEGACY_PREFIX):
        return "the checkout from before versions"
    return "an older version" if checkout_work.is_version(folder) else "a folder that is not a version"


def _prune(state_dir: Path, state: UpdateState) -> list[str]:
    """Old backups, version folders and checkouts from before versions deleted, but for the two
    versions of this update; the temporary links a switch cut short before this update started. One
    that holds anything of yours stays (versions.kept: as bosun uninstall checks a checkout, and an older
    version for a change or a commit of its own), and is a finding: said once, in the summary."""
    for gone in update_backups.prune(state_dir, current=Path(state.backup) if state.backup else None):
        Output.say(f"  deleted the old backup {gone.name}")
    to_root = Path(state.to_root)
    install = install_paths.install_path(to_root)
    folder = install_paths.versions_dir(install)
    keep = (to_root, Path(state.from_root))
    for gone in versions.prune(folder, keep=keep):
        Output.say(f"  deleted the old version {gone.name}")
    versions.sweep_links(install, before=_started_time(state))
    return [f"kept {kept}, {_kept_as(kept)}: {why}" for kept, why in versions.held_back(folder, keep)]


def release(state: UpdateState) -> UpdateState:
    """After the freeze: the launchd jobs whose plist changed reloaded, the check-in loop restarted
    while the hold is still on, then the hold lifted (`released`); old backups and version
    folders pruned; what went wrong there, a checkout from before versions kept for what it holds, and
    what the new version's `bosun doctor` finds kept as findings, reported, never rolled back (writes
    may have landed since)."""
    where = folders()
    to_root = Path(state.to_root)
    labels, found = _reload_jobs(to_root)
    if labels:  # kept at once, with those of a release before this one (carried on)
        reloaded = tuple(dict.fromkeys((*state.reloaded, *labels)))
        state = _record(where.state_dir, dataclasses.replace(state, reloaded=reloaded))
    found += _restart_checkin_loop(to_root)
    _unmark(where.state_dir)  # a conversion's own work: the reload and the restart brought it back
    state = _step(where.state_dir, state, "released")
    Output.say("  lifted the hold on bosun's background work")
    kept = _prune(where.state_dir, state)
    Output.say("Asking bosun doctor what fails after the switch...")
    doctor = _doctor(to_root)
    return _record(where.state_dir, dataclasses.replace(state, findings=(*found, *kept, *doctor)))


# ---- an update cut short ------------------------------------------------------------------------


class WaitStopped(Exception):
    """Ctrl-C stopped the wait of an update carried on: the command ends there."""

    def __init__(self, state: UpdateState) -> None:
        super().__init__("stopped waiting")
        self.state = state


def _windows_brought_along(state: UpdateState) -> UpdateState:
    """A released update's windows brought along by a plan made again from its two versions: the link
    already leads to the new one, which the plan compares each window's launch with, as before the
    switch, and a window opened since the plan the go-ahead was given on (the orchestrator delegating
    during the prompt or the hold) is in it."""
    where = folders()
    plan = update_facts.plan_for(
        Path(state.from_root), Path(state.to_root), where.state_dir, where.config_dir
    )
    return update_windows.bring_along(state, plan, say=Output.say)


def _brought_along(state: UpdateState) -> UpdateState:
    """An update cut short after its release: its windows brought along (_windows_brought_along).
    WaitStopped when Ctrl-C stopped that wait."""
    state = _windows_brought_along(state)
    if state.pending:
        raise WaitStopped(state)
    return state


def _carry_on(state_dir: Path) -> UpdateState | None:
    """Under update.lock: the record read again, then an update cut short before the freeze recorded
    as failed (only the hold had changed, and it lapsed), one past its checks rolled forward: released,
    then its windows brought along. Windows still waited for at `agents` are the check-in loop's,
    and are left to it."""
    state = update_state.read(state_dir)
    if state is None or state.step in FINISHED:
        return state
    if state.step in BEFORE_FREEZE:
        return _step(state_dir, state, "failed", error=f"cut short at step {state.step}; nothing had changed")
    if state.step == "checked":
        state = release(state)
    if state.step == "released":
        return _brought_along(state)
    if state.step == "agents":
        return _settled(state_dir, state)
    return _step(state_dir, state, "done")


def _settled(state_dir: Path, state: UpdateState) -> UpdateState:
    """An update cut short while it brought windows along: those that answered since (their own note,
    or one written by hand) no longer waited for, and `done` once none is; the rest are the check-in
    loop's, never acted on here."""
    after = update_agents.settled(state, update_notes.Files(state_dir))
    return _record(state_dir, after) if after != state else after


def resume(state_dir: Path) -> UpdateState | None:
    """An update recorded and not running, carried on from where it stopped: before the freeze, its
    hold lifted; inside it, put back (update_gate.recover); after `checked`, on from the step after
    the one recorded. A conversion's own background work, stopped and left so by a crash, is brought
    back once it is put back (_own_work_back). Returns where it ended; None when there was nothing to
    carry on (no record, one finished, or one still running, which one_update refuses next)."""
    state = update_state.load(state_dir)
    if update_state.running(state_dir):
        return None
    if state is None:  # a mark left with no record (moved aside, or deleted) is still acted on
        _own_work_left(state_dir)
        return None
    if not update_state.known(state):
        raise UpdateRefused(
            f"the update's record is at step {state.step}, which a newer bosun wrote: carry it on with "
            f"{state.to_root}/bin/bosun update"
        )
    if state.step in FINISHED:  # a write put a conversion's freeze back first
        _own_work_left(state_dir)
        return None
    if update_state.in_freeze(state):
        update_gate.recover(state_dir, folders().config_dir)
        _own_work_left(state_dir)
        return update_state.read(state_dir)
    with update_state.one_update(state_dir):
        carried = _carry_on(state_dir)
        _own_work_back(state_dir)
        return carried


# ---- the command --------------------------------------------------------------------------------


def _short(commit: str) -> str:
    return commit[:SHORT]


def _show(plan: Plan, as_json: bool) -> None:
    if not as_json:
        Output.say(update_plan.render_plan(plan).rstrip("\n"))


AHEAD = "bosun update --yes goes ahead"


def _go_ahead(yes: bool, ahead: str = AHEAD) -> bool:
    """--yes, or yes to the question in a terminal; without one, the plan alone, and <ahead>: what
    goes ahead without asking."""
    if yes:
        return True
    if not sys.stdin.isatty():
        Output.say(f"Nothing has changed: in a terminal it asks first, and {ahead}.")
        return False
    print("Update now? [y/N] ", end="", file=sys.stderr if Output.to_stderr else sys.stdout, flush=True)
    answer = input().strip().lower()
    if answer in ("y", "yes"):
        return True
    Output.say("Nothing has changed.")
    return False


def _started(plan: Plan, old_root: Path, new_root: Path, doctor_before: Sequence[str]) -> UpdateState:
    """The record of an update starting to carry out <plan>, with what bosun doctor failed at while
    planning."""
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
        plan=update_plan.as_record(plan),
        doctor_before=tuple(doctor_before),
    )


def _again(from_root: Path) -> str:
    """What carries on, or puts back, an update stopped short: `bosun update`; or the install line,
    which hands over to the new version's, where the bosun installed now is a checkout from before
    versions, which has no `bosun update` (a conversion stopped before its swap, or put back)."""
    return "the install line" if versions.before_versions(installed(from_root)) else "bosun update"


def _failed(state: UpdateState) -> int:
    """The failure said, naming the backup if the update took one (in case I need it)."""
    backup = (
        f" The backup it took: {update_summary.tilde(state.backup, str(Path.home()))}."
        if state.backup
        else ""
    )
    if state.step in update_state.FREEZE_STEPS:
        Output.say(
            f"bosun update failed and could not put everything back: {state.error}. Writes stay "
            f"paused until it is put back: run {_again(Path(state.from_root))} again.{backup}"
        )
    else:
        Output.say(
            f"bosun update failed: {state.error}. Everything is as it was ({_short(state.from_commit)})."
            + backup
        )
    return 1


def _rehearsed(state: UpdateState, plan: Plan, where: Folders) -> UpdateState:
    """The rehearsal; `rehearsed` recorded if it passed, else `failed` with what it found."""
    Output.say("Rehearsing on a copy of the data...")
    found = rehearse(where.state_dir, where.config_dir, plan)
    if found:
        return _step(where.state_dir, state, "failed", error="the rehearsal failed: " + "; ".join(found))
    return _step(where.state_dir, state, "rehearsed")


def _prepared(plan: Plan, old_root: Path, new_root: Path, where: Folders) -> UpdateState:
    """Recorded from `planned` on: what bosun doctor fails at before anything changes (outside the
    hold and the freeze), then the rehearsal; `rehearsed`, or `failed` with what it found."""
    Output.say("Asking bosun doctor what fails before the update...")
    before = _doctor_fails(new_root)
    return _rehearsed(_record(where.state_dir, _started(plan, old_root, new_root, before)), plan, where)


def _carried_out(plan: Plan, old_root: Path, new_root: Path, where: Folders) -> UpdateState:
    """Recorded from `planned` on: rehearsal, hold, freeze, release, done (or where it stopped)."""
    state = _prepared(plan, old_root, new_root, where)
    return state if state.step == "failed" else _through(state, plan, where)


def _through(state: UpdateState, plan: Plan, where: Folders) -> UpdateState:
    """From a rehearsal that passed: the hold, the freeze, the release and the windows brought along
    (by a plan made again then, _windows_brought_along: <plan>, the one the go-ahead was given on, may
    lack a window opened since), or where it stopped."""
    hold_until = int(time.time()) + update_state.HOLD_SECONDS
    state = _step(where.state_dir, state, "holding", hold_until=hold_until)
    Output.say("Holding bosun's background work; pausing writes to back up, migrate, switch and check...")
    state = freeze(state, plan, where.state_dir, where.config_dir)
    if state.step != "checked":
        return state
    Output.say(f"Writes went on after {state.freeze_ms} ms. Releasing...")
    state = release(state)
    Output.say("Bringing the agents along...")
    return _windows_brought_along(state)


# ---- the summary --------------------------------------------------------------------------------


def _seconds(state: UpdateState) -> int:
    """From the update's start (a carried-on one's first) to now, the end of its wait."""
    started = _started_time(state)
    return max(0, round(time.time() - started)) if started else 0


def _size(folder: Path) -> int:
    """The size of the files under <folder>, in bytes (their st_size), links not followed."""
    total = 0
    for parent, _dirs, names in os.walk(folder):
        for name in names:
            with contextlib.suppress(OSError):
                total += (Path(parent) / name).lstat().st_size
    return total


def summary_of(state_dir: Path, state: UpdateState) -> update_summary.Summary:
    """The summary of the update <state> records (update_summary.of_record), with what is there now:
    what came of each window (their notes), and whether the backup is still there and its size."""
    backup = Path(state.backup) if state.backup else None
    there = backup is not None and backup.is_dir()
    return update_summary.of_record(
        state,
        update_agents.outcomes(state, update_notes.Files(state_dir)),
        there,
        _size(backup) if backup is not None and there else 0,
        _seconds(state),
    )


def _text(state: UpdateState, summary: update_summary.Summary) -> str:
    """<summary> as text: the backup's path with ~ for the home folder, the jobs named after the
    launchd label the new version's config has."""
    label = update_facts.launchd_label(Path(state.to_root))
    return update_summary.render(summary, home=str(Path.home()), launchd_label=label)


def _saved(state: UpdateState, text: str, say: Callable[[str], None]) -> Path | None:
    """<text> as summary.md in the update's backup folder, once the update is done and if the folder
    is still there (a kg forget deletes every backup, and may do so meanwhile: its deletion tries a
    folder this wrote into again); where, or None. One that cannot be written is said."""
    folder = Path(state.backup) if state.backup else None
    if state.step != "done" or folder is None or not folder.is_dir():
        return None
    path = folder / update_backups.SUMMARY
    tmp = folder / f".{update_backups.SUMMARY}.tmp"
    try:
        tmp.write_text(f"```text\n{text}```\n", encoding="utf-8", errors="replace")
        tmp.replace(path)
    except OSError as exc:
        if folder.is_dir():  # not a forget deleting it meanwhile
            say(f"could not save the summary as {path}: {exc}")
        return None
    return path


def _say_summary(state_dir: Path, state: UpdateState, said: Said) -> None:
    """The summary of the update <state> records, said, kept for --json, and saved once it is done."""
    said.summary = summary_of(state_dir, state)
    text = _text(state, said.summary)
    Output.say(text.rstrip("\n"))
    _saved(state, text, Output.say)


@dataclass
class Said:
    """What --json prints besides the update's record: the plan, once one is made, and the summary,
    once an update ends."""

    plan: Plan | None = None
    summary: update_summary.Summary | None = None


def _update(old_root: Path, where: Folders, args: argparse.Namespace, said: Said) -> int:
    """Under update.lock: the plan, shown; then, said yes to, carried out."""
    plan = said.plan = update_facts.plan_for(old_root, ROOT, where.state_dir, where.config_dir)
    _show(plan, args.json)
    if plan.blockers or not _go_ahead(args.yes):
        return BLOCKED if plan.blockers else 0
    state = _carried_out(plan, old_root, ROOT, where)
    if state.step not in ENDED:
        return _failed(state)
    _say_summary(where.state_dir, state, said)
    return 0


# ---- the first update: a checkout from before versions, converted at a quiet moment -------------

QUIET_STATUSES = ("idle", "exited")  # an agent with nothing running, or no agent at all
QUIET_POLL_SECONDS = 5
# The most it waits for a quiet moment with bosun's own work stopped, before it starts that work
# again and waits with everything running.
QUIET_AFTER_STOP_SECONDS = 120
QUIET, STOPPED, LATE = "quiet", "stopped", "late"  # how a wait for a quiet moment ends
NOT_QUIET = "it was not quiet soon enough after bosun's own work stopped; nothing had changed"
# What a conversion stopped of bosun's own work, in the state folder (STATE_FILES' `update` row):
# a file of its own, as an older version's write gate rewrites update.json with only the fields it knows.
OWN_WORK = Path(update_state.UPDATE_DIR) / "own-work.json"
LOOP_STOP_SECONDS = 20  # the most the check-in loop may take to stop
NOTHING_CHANGED = (*BEFORE_FREEZE, "failed", "restored")  # what a conversion leaves as it was
CONVERT_AHEAD = "get.sh goes ahead with --unattended (sh -s -- --unattended after the pipe)"


def waiting_for(quiet: update_facts.Quiet) -> list[str]:
    """What keeps it from being a quiet moment, a phrase each: each window whose agent is not idle
    with nothing running, and someone who typed in the session within BOSUN_ACTIVE_CLIENT_MINUTES."""
    found = [f"{name} ({status})" for name, status in quiet.statuses if status not in QUIET_STATUSES]
    if quiet.typed is not None and quiet.typed < quiet.minutes:
        found.append(f"someone typed in the session {quiet.typed} min ago (quiet after {quiet.minutes})")
    return found


def _quiet_moment(root: Path, within: float | None = None) -> str:
    """Waits until every window is idle with nothing running and nobody has typed for a while, saying
    what it waits for whenever that changes: QUIET then; STOPPED when Ctrl-C stopped the wait, with
    nothing changed; LATE once <within> seconds passed without one."""
    said = ""
    deadline = None if within is None else time.monotonic() + within
    try:
        while waiting := waiting_for(update_facts.quiet_facts(root)):
            if deadline is not None and time.monotonic() >= deadline:
                return LATE
            line = "Waiting for a quiet moment to convert: " + "; ".join(waiting)
            if line != said:
                Output.say(line)
                said = line
            time.sleep(QUIET_POLL_SECONDS)
    except KeyboardInterrupt:
        Output.say("\nStopped waiting. Nothing has changed: run get.sh again at a quieter moment.")
        return STOPPED
    return QUIET


def _stop_loop(pid: int) -> bool:
    """The check-in loop at <pid> stopped (SIGTERM) and waited for; False if it would not stop."""
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + LOOP_STOP_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(update_state.POLL_SECONDS)
    return False


@dataclass(frozen=True)
class OwnWork:
    """What a conversion stopped of bosun's own background work, as its mark keeps it: the launchd jobs
    (None: the mark could not be read, so every one of them), and whether the check-in loop."""

    jobs: tuple[str, ...] | None
    loop: bool


def _mark(state_dir: Path, own: OwnWork) -> None:
    """The mark of what a conversion is about to stop, written whole before anything stops."""
    path = state_dir / OWN_WORK
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps({"jobs": list(own.jobs or ()), "loop": own.loop}), encoding="utf-8")
    tmp.replace(path)


def _marked(state_dir: Path) -> OwnWork | None:
    """The mark of what a conversion stopped, None when there is none (its work runs). One that cannot
    be read names every job, and the loop."""
    try:
        data = json.loads((state_dir / OWN_WORK).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return OwnWork(None, True)
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list) or not all(isinstance(job, str) for job in jobs):
        return OwnWork(None, True)
    return OwnWork(tuple(jobs), data.get("loop") is not False)


def _unmark(state_dir: Path) -> None:
    (state_dir / OWN_WORK).unlink(missing_ok=True)


def _stopped(root: Path, state_dir: Path) -> str:
    """The mark written of what runs of bosun's own work (the watchdog's and learning's launchd jobs
    that are loaded, the check-in loop), then each stopped and said; what could not be stopped, or ""."""
    pid = update_facts.checkin_loop_pid(root)
    _mark(state_dir, OwnWork(tuple(update_facts.own_jobs_loaded(root)), pid is not None))
    done = update_facts.shell(root, "stop_own_jobs")
    for label in done.stdout.split():
        Output.say(f"  stopped the launchd job {label}")
    if done.returncode != 0:
        return f"launchd: {done.stderr.strip() or 'a job would not stop'}"
    if pid is None:
        return ""
    if not _stop_loop(pid):
        return f"the check-in loop (pid {pid}) would not stop"
    Output.say("  stopped the check-in loop")
    return ""


EVERY_OWN_JOB = 'start_jobs "$(watchdog_label)" "$(learn_label)"'  # for a mark that cannot be read


def _start_own(root: Path, own: OwnWork) -> None:
    """What <own> names started again as it was, each said: its launchd jobs loaded from their plists as
    they are on disk (start_jobs: nothing rendered, no other job, one loaded already left), and the
    check-in loop, if it was stopped and none runs now (one started since, or never stopped, is left to
    its tick), restarted by refresh --checkin-loop, which runs the install path's bosun: the old code
    again, once a put back has led the install path there."""
    if own.jobs != ():
        script = EVERY_OWN_JOB if own.jobs is None else 'start_jobs "$@"'
        done = update_facts.shell(root, script, *(own.jobs or ()))
        said = "loaded again" if done.returncode == 0 else done.stderr.strip()
        Output.say(f"  the launchd jobs it stopped: {said}")
    if own.loop and update_facts.checkin_loop_pid(root) is None:
        for line in _restart_checkin_loop(root):
            Output.say(f"  {line}")


def _own_work_back(state_dir: Path) -> None:
    """bosun's own background work a conversion stopped (its mark), started again as it was, once:
    where the conversion left everything as it was (no record, or one at NOTHING_CHANGED, whoever wrote
    it); past the switch the release brings it back. Then the mark goes. The caller holds update.lock."""
    own = _marked(state_dir)
    if own is None:
        return
    state = update_state.read(state_dir)
    if state is not None and state.step not in NOTHING_CHANGED:
        return
    to_root = Path(state.to_root) if state is not None else ROOT
    Output.say("Starting bosun's own background work again, as it was...")
    _start_own(to_root if (to_root / "bin" / "bosun").is_file() else ROOT, own)
    _unmark(state_dir)


def _own_work_left(state_dir: Path) -> None:
    """_own_work_back, under update.lock, for an update found cut short."""
    with update_state.one_update(state_dir):
        _own_work_back(state_dir)


def _stopped_then_through(state: UpdateState, plan: Plan, new_root: Path, where: Folders) -> UpdateState:
    """bosun's own work stopped; a quiet moment again, as the doctor run and the rehearsal took a while
    and the old check-in loop could have prompted a window meanwhile (with it stopped, that settles
    soon), but for QUIET_AFTER_STOP_SECONDS at the most, as a busy window would otherwise keep that
    work off for as long as its turn runs; then the hold, the freeze, the release and the windows
    brought along (_through)."""
    Output.say("Stopping bosun's own background work, which the version it converts from may not hold...")
    problem = _stopped(new_root, where.state_dir)
    if problem:
        return _step(where.state_dir, state, "failed", error=problem)
    waited = _quiet_moment(new_root, within=QUIET_AFTER_STOP_SECONDS)
    if waited == STOPPED:
        error = "stopped while it waited for a quiet moment again (Ctrl-C); nothing had changed"
        return _step(where.state_dir, state, "failed", error=error)
    if waited == LATE:
        Output.say(
            f"Not quiet within {QUIET_AFTER_STOP_SECONDS} s of stopping bosun's own work: starting it again, "
            "and waiting for a quiet moment with everything running."
        )
        return _step(where.state_dir, state, "failed", error=NOT_QUIET)
    return _through(state, plan, where)


def _converted(plan: Plan, old_checkout: Path, new_root: Path, where: Folders) -> UpdateState:
    """As _carried_out, with bosun's own background work stopped from the rehearsal on (the version it
    converts from holds none of it for an update), its mark written before anything stops, and a quiet
    moment checked again once it is. Whatever ends it short of the switch, a put back included, brings
    that work back (_own_work_back); past the switch the release does."""
    state = _prepared(plan, old_checkout, new_root, where)
    if state.step == "failed":
        return state
    try:
        return _stopped_then_through(state, plan, new_root, where)
    finally:
        _own_work_back(where.state_dir)


def _refuse_work(checkout: Path) -> None:
    """UpdateRefused while the checkout holds anything of yours, as bosun uninstall checks one: the
    conversion sets it aside, and a later update deletes it."""
    work = checkout_work.checkout_work(checkout)
    if work:
        raise UpdateRefused(
            f"{checkout} has {work}, and the first update sets this checkout aside (a later one deletes "
            "it): keep what is yours elsewhere, then run get.sh again"
        )


def _converts(old_checkout: Path, new_root: Path, commit: str) -> str:
    """What converting adds to the plan, in a paragraph."""
    install = install_paths.install_path(new_root)
    kept = versions.switched_from(install, new_root, old_checkout, commit)
    return (
        f"This first update converts {install}, a checkout from before versions: once every window is "
        "idle with nothing running and nobody has typed for a while, it stops bosun's own background "
        f"work (the watchdog, learning and the check-in loop), then switches {install} to a link to "
        f"{new_root} in one step, keeping the checkout as {kept}, and restarts each of bosun's own windows "
        "(the orchestrator, those in sessions.yml) on the new version."
    )


def _convert_now(old_checkout: Path, new_root: Path, args: argparse.Namespace, said: Said) -> int:
    """The conversion at a quiet moment, from a plan made again then (windows may have come and gone, or
    something blocks now, while it waited); and so again after a wait with everything running, when the
    moment after the stop was not quiet soon enough (NOT_QUIET)."""
    where = folders()
    state: UpdateState | None = None
    while state is None or state.error == NOT_QUIET:
        if state is not None and _quiet_moment(new_root) != QUIET:
            return BLOCKED
        plan = said.plan = update_facts.plan_for(old_checkout, new_root, where.state_dir, where.config_dir)
        if plan.blockers:
            _show(plan, args.json)
            return BLOCKED
        state = _converted(plan, old_checkout, new_root, where)
    if state.step not in ENDED:
        return _failed(state)
    Output.say(
        f"{install_paths.install_path(new_root)} is now a link to {new_root}. The checkout is kept as "
        f"{state.from_root} until the next update, which deletes it if nothing in it is yours."
    )
    _say_summary(where.state_dir, state, said)
    return 0


def convert(old_checkout: Path, new_root: Path, args: argparse.Namespace, said: Said | None = None) -> int:
    """`bosun update --from` a checkout from before versions: the install path's own checkout, or the
    legacy one a conversion put back leads the link to (docs/design/update.md, "The first update
    converts, at a quiet moment"). Refused while the checkout holds anything of yours. A dry run shows
    the plan and what converting adds; otherwise, under update.lock (the caller holds it), the plan is
    said yes to, it waits for a quiet moment (Ctrl-C there changes nothing), then carries the update
    out (_converted): the checkout swapped for the link in one call and kept as legacy-<its commit>,
    and every window of bosun's brought along as one whose launch changed, as the version converted
    from recorded no launches (one outside the window list is left as it is: update_facts._window).
    <said> keeps the plan and the summary for --json."""
    said = said if said is not None else Said()
    _refuse_work(old_checkout)
    where = folders()
    plan = said.plan = update_facts.plan_for(old_checkout, new_root, where.state_dir, where.config_dir)
    _show(plan, args.json)
    Output.say(_converts(old_checkout, new_root, plan.from_commit))
    if args.dry_run or plan.blockers:
        return BLOCKED if plan.blockers else 0
    if not _go_ahead(args.yes, CONVERT_AHEAD):
        return 0
    return _convert_now(old_checkout, new_root, args, said) if _quiet_moment(new_root) == QUIET else BLOCKED


def _who(state: UpdateState | None) -> tuple[str, str, str]:
    return (state.started_at, state.from_commit, state.to_commit) if state else ("", "", "")


def installed(from_root: Path) -> Path:
    """The version the install link leads to now (an update put back may have moved it since the
    wrapper handed over); <from_root> where the install is no link."""
    current = versions.current(install_paths.install_path(ROOT))
    return current.resolve() if current is not None else from_root.resolve()


def _is_this_one(old_root: Path) -> bool:
    """Whether the version the link leads to is this one (nothing new on the branch)."""
    return versions.commit_of(old_root) == versions.commit_of(ROOT)


def _say_up_to_date() -> None:
    """Nothing newer: up to date, or, when the wrapper found the install past the newest release (it
    sets AHEAD_ENV to its tag), ahead of it."""
    ahead = os.environ.get(AHEAD_ENV)
    if ahead:
        Output.say(f"bosun is ahead of the newest release ({ahead}): nothing to update to.")
        return
    Output.say(f"bosun is up to date ({_short(versions.commit_of(ROOT))}).")


def _resumed_line(state: UpdateState) -> str:
    """What became of an update found cut short (resume): put back, ended, carried on to the end, or,
    at `agents`, nothing: its windows are still brought along, and who finishes them."""
    versions_ = f"An update from {_short(state.from_commit)} to {_short(state.to_commit)}"
    if state.step == "agents":
        finishes = update_windows.who_finishes(Path(state.to_root), state.pending)
        return f"{versions_} still brings windows along: {finishes}."
    what = {"restored": "put back", "failed": "ended (it was cut short before anything changed)"}
    return f"{versions_} that was cut short: {what.get(state.step, 'carried on to the end')}."


def _said_recorded(state_dir: Path) -> bool:
    """For a dry run, what the update's record shows first: an update running is refused, one cut short
    said (and True: bosun is then not called up to date), one still bringing windows along said."""
    recorded = update_state.read(state_dir)
    if update_state.running(state_dir):
        raise update_state.UpdateRunning(*_who(recorded))
    stopped = recorded is not None and recorded.step not in (*FINISHED, "agents")
    if recorded is not None and stopped:
        Output.say(
            f"(an update stopped at step {recorded.step}: bosun update without --dry-run carries it on, "
            "or puts it back)"
        )
    elif recorded is not None and recorded.pending:
        Output.say(
            f"(an update is still bringing along {', '.join(recorded.pending)}: the check-in loop carries on)"
        )
    return stopped


def _dry_run(where: Folders, args: argparse.Namespace, said: Said) -> int:
    """The plan alone, or for a checkout from before versions the conversion's. An update cut short is
    never carried on or put back here (that changes things): it is said first, and while it is there
    bosun is not called up to date."""
    stopped = _said_recorded(where.state_dir)
    old_root = installed(Path(args.from_root))
    code = 0
    if versions.before_versions(old_root):
        code = convert(old_root, ROOT, args, said)
    elif not _is_this_one(old_root):
        said.plan = update_facts.plan_for(old_root, ROOT, where.state_dir, where.config_dir)
        _show(said.plan, args.json)
        code = BLOCKED if said.plan.blockers else 0
    elif not stopped:
        _say_up_to_date()
    Output.say("A dry run: nothing has changed.")
    return code


def _carried_on(state_dir: Path, said: Said) -> bool:
    """An update cut short carried on first (resume), and what became of it said, with its summary once
    it brought the windows along; False when Ctrl-C stopped that wait, which ends the command."""
    try:
        resumed = resume(state_dir)
    except WaitStopped as stopped:
        _say_summary(state_dir, stopped.state, said)
        return False
    if resumed is not None:
        Output.say(_resumed_line(resumed))
        if resumed.step in ENDED:
            _say_summary(state_dir, resumed, said)
    return True


def _for_real(where: Folders, args: argparse.Namespace, said: Said) -> int:
    """An update cut short carried on first; then, under update.lock (an update running is refused
    here, whatever its step, before anything is called up to date), up to date or the update."""
    if not _carried_on(where.state_dir, said):
        return 0
    with update_state.one_update(where.state_dir):
        old_root = installed(Path(args.from_root))
        if versions.before_versions(old_root):
            return convert(old_root, ROOT, args, said)
        if _is_this_one(old_root):
            _say_up_to_date()
            return 0
        return _update(old_root, where, args, said)


def run(args: argparse.Namespace, said: Said) -> int:
    """`bosun update`, handed over by the wrapper: a dry run, or the update itself."""
    where = folders()
    return _dry_run(where, args, said) if args.dry_run else _for_real(where, args, said)


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


def _record_now() -> dict[str, Any] | None:
    """The update's record as it stands, for --json; None without one (or one that cannot be read)."""
    try:
        state = update_state.load(folders().state_dir)
    except (UpdateRefused, update_state.RecordUnreadable):
        return None
    return dataclasses.asdict(state) if state else None


# ---- what the agents and the check-in loop run -------------------------------------------------


def agents_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun update", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    note = commands.add_parser("note", allow_abbrev=False, help="a window's note, the text on stdin")
    note.add_argument("--window", required=True)
    kind = note.add_mutually_exclusive_group(required=True)
    kind.add_argument("--stock", action="store_true", help="its background work, before its restart")
    kind.add_argument("--status", action="store_true", help="its one-line status, once it carried on")
    commands.add_parser("advance", allow_abbrev=False, help="the check-in loop's turn at the windows")
    return parser


def _note(state_dir: Path, window: str, kind: str) -> int:
    """`bosun update note`: <window>'s <kind> note, read from stdin."""
    if sys.stdin.isatty():
        print(
            "bosun update note: the note is read from stdin; pass it in a quoted heredoc (<<'EOF')",
            file=sys.stderr,
        )
        return USAGE
    text = sys.stdin.read()
    if not text.strip():
        print("bosun update note: the note on stdin is empty; nothing saved", file=sys.stderr)
        return 1
    try:
        update_notes.note(state_dir, window, kind, text)
    except update_notes.NoteRefused as exc:
        print(f"bosun update note: {exc}", file=sys.stderr)
        return USAGE
    print(f"bosun update: noted the {kind} of {window}")
    return 0


def _say_advance(line: str) -> None:
    print(f"bosun update: {line.strip()}", flush=True)


def _advance(state_dir: Path) -> int:
    """`bosun update advance`, each tick of the check-in loop while windows are pending: a turn at
    them, under update.lock; quietly nothing while the update itself still runs (and holds it). The
    turn that takes the last answer ends the update: its summary is saved next to the backup."""
    try:
        with update_state.one_update(state_dir):
            if update_state.windows_pending(state_dir):
                update_windows.turn(state_dir, _say_advance)
                _finished_by_the_loop(state_dir)
    except update_state.UpdateRunning:
        pass  # the update brings them along itself
    return 0


def _finished_by_the_loop(state_dir: Path) -> None:
    state = update_state.read(state_dir)
    if state is not None and state.step == "done":
        saved = _saved(state, _text(state, summary_of(state_dir, state)), _say_advance)
        _say_advance(f"done; its summary is in {saved}" if saved else "done")


def agents_command(argv: Sequence[str]) -> int:
    args = agents_parser().parse_args(list(argv))
    try:
        state_dir = folders().state_dir
    except UpdateRefused as exc:
        print(f"bosun update: {exc}", file=sys.stderr)
        return 1
    if args.command == "note":
        return _note(state_dir, args.window, "stock" if args.stock else "status")
    return _advance(state_dir) if update_state.windows_pending(state_dir) else 0


def after_update_command(argv: Sequence[str]) -> int:
    """`after-update --state-dir <dir> --config-dir <dir>`, which bosun doctor runs: what the last update
    left me (update_summary.left_to_me, from its record and bosun.env), a line each, its kind and a tab
    first, and for a key its note after another tab when it has one (update_summary.key_note); nothing
    when there is none."""
    parser = argparse.ArgumentParser(prog=f"update.py {AFTER_UPDATE}", allow_abbrev=False)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    args = parser.parse_args(list(argv))
    state = update_state.read(args.state_dir)
    for kind, what in update_summary.left_to_me(state, migrations.assigned_keys(args.config_dir)):
        note = update_summary.key_note(what) if kind == "key" else ""
        print("\t".join(" ".join(part.split()) for part in (kind, what, note) if part))
    return 0


def main(argv: Sequence[str]) -> int:
    """The command, and with --json one object on stdout on every path: the plan (null when none was
    made: up to date, refused, a failure before it), the update's record as it stands, and the summary
    (null unless an update ended in this run). `note` and `advance` are what the agents and the
    check-in loop run (agents_command); `after-update` is bosun doctor's (after_update_command)."""
    if argv and argv[0] in AGENT_COMMANDS:
        return agents_command(argv)
    if argv and argv[0] == AFTER_UPDATE:
        return after_update_command(argv[1:])
    args = build_parser().parse_args(list(argv))
    Output.to_stderr = args.json
    said = Said()
    code = _said(args, said)
    if args.json:
        plan = dataclasses.asdict(said.plan) if said.plan else None
        summary = update_summary.as_json(said.summary) if said.summary else None
        print(json.dumps({"plan": plan, "update": _record_now(), "summary": summary}, indent=2))
    return code


def _said(args: argparse.Namespace, said: Said) -> int:
    """run, with what stops it said in one line (exit 1)."""
    try:
        return run(args, said)
    except update_state.RecordUnreadable as exc:
        print(f"bosun update: {_unreadable(exc)}", file=sys.stderr)
    except (UpdateRefused, update_state.UpdateRunning) as exc:
        print(f"bosun update: {exc}", file=sys.stderr)
    except update_backups.BackupError as exc:
        print(f"bosun update: an update cut short could not be put back: {exc}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        again = _again(Path(args.from_root))
        print(
            f"\nbosun update: stopped; run {again} again: it carries on, or puts back, what it started",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
