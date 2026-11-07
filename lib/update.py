"""`bosun update`: the plan, the rehearsal on a copy, the hold, the freeze and the release, and an
update cut short carried on or put back (design: docs/design/update.md, "The steps" and "Cut short").

This runs in the version updated to, ROOT: the wrapper (libexec/bosun-update) fetched it into its own
version folder and handed over. The version updated from is the one the install link leads to. The
facts its plan is made from are gathered by lib/update_facts.py. The
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
  locks are held) and records failed with the error. How long writes were paused, from the locks
  taken to their release, is freeze_ms.
- checked: writes go on. released: the jobs reloaded, the check-in loop restarted while the hold is
  still on (R36), then the hold lifted; what went wrong after the switch and what bosun doctor finds
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
import re
import shutil
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
DOCTOR_LINE = re.compile(r"^  (FAIL|warn)  (.*)$", re.MULTILINE)


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


def _migrate(
    ctx: migrations.Context, plan: Plan, now: datetime
) -> tuple[list[migrations.Applied], list[str]]:
    """The migrations, and the config's renamed keys when the plan has any (the copy of bosun.env
    kept as it was named for <now>): each migration that ran, and bosun.env's line for the summary."""
    applied = migrations.run(ctx)
    changed = migrations.write_config(ctx.config_dir, now) if plan.renames else []
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
    """What a failed freeze puts back: what its locks cover, and bosun.env when it renames keys."""
    return (*update_backups.FROZEN, *(("config/" + migrations.CONFIG_FILE,) if plan.renames else ()))


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
    state = _step(state_dir, state, "switched", migrated=migrated, config_changed=tuple(config))
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
    if not any(w.get("kind") == "checkin" for w in update_facts.open_windows(root)):
        return []
    done = update_facts.bosun(root, "refresh", "--checkin-loop")
    if done.returncode == 0:
        Output.say("  restarted the check-in loop")
        return []
    return [f"the check-in loop: {done.stderr.strip() or 'it could not be restarted'}"]


def _doctor(root: Path) -> list[str]:
    """What `bosun doctor` of the version in <root> fails or warns about, a line each."""
    done = update_facts.bosun(root, "doctor")
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
    labels, found = _reload_jobs(to_root)
    if labels:  # kept at once, with those of a release before this one (carried on)
        reloaded = tuple(dict.fromkeys((*state.reloaded, *labels)))
        state = _record(where.state_dir, dataclasses.replace(state, reloaded=reloaded))
    found += _restart_checkin_loop(to_root)
    state = _step(where.state_dir, state, "released")
    Output.say("  lifted the hold on bosun's background work")
    state = _record(where.state_dir, dataclasses.replace(state, findings=(*found, *_doctor(to_root))))
    _prune(where.state_dir, Path(state.from_root), to_root)
    return state


# ---- an update cut short ------------------------------------------------------------------------


class WaitStopped(Exception):
    """Ctrl-C stopped the wait of an update carried on: the command ends there."""

    def __init__(self, state: UpdateState) -> None:
        super().__init__("stopped waiting")
        self.state = state


def _brought_along(state: UpdateState) -> UpdateState:
    """An update cut short after its release: its windows brought along by a plan made again from its
    two versions (the link already leads to the new one, which the plan compares each window's launch
    with, as before the switch). WaitStopped when Ctrl-C stopped that wait."""
    where = folders()
    plan = update_facts.plan_for(
        Path(state.from_root), Path(state.to_root), where.state_dir, where.config_dir
    )
    state = update_windows.bring_along(state, plan, say=Output.say)
    if state.pending:
        raise WaitStopped(state)
    return state


def _carry_on(state_dir: Path) -> UpdateState | None:
    """Under update.lock: the record read again, then an update cut short before the freeze recorded
    as failed (only the hold had changed, and it lapsed), one past its checks rolled forward: released,
    then its windows brought along. Windows still waited for at `agents` are the check-in loop's (R6),
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
    loop's (R6), never acted on here."""
    after = update_agents.settled(state, update_notes.Files(state_dir))
    return _record(state_dir, after) if after != state else after


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
        plan=update_plan.as_record(plan),
    )


def _failed(state: UpdateState) -> int:
    """The failure said, naming the backup if the update took one (in case I need it)."""
    backup = f" The backup it took: {update_summary.tilde(state.backup)}." if state.backup else ""
    if state.step in update_state.FREEZE_STEPS:
        Output.say(
            f"bosun update failed and could not put everything back: {state.error}. Writes stay "
            f"paused until bosun update puts it back; run it again.{backup}"
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
    state = release(state)
    Output.say("Bringing the agents along...")
    return update_windows.bring_along(state, plan, say=Output.say)


# ---- the summary --------------------------------------------------------------------------------


def _seconds(state: UpdateState) -> int:
    """From the update's start (a carried-on one's first) to now, the end of its wait."""
    try:
        started = datetime.fromisoformat(state.started_at)
    except ValueError:
        return 0
    started = started if started.tzinfo else started.replace(tzinfo=UTC)
    return max(0, round((datetime.now(UTC) - started).total_seconds()))


def _bytes(folder: Path) -> int:
    """The size of the files under <folder>, links not followed."""
    total = 0
    for parent, _dirs, names in os.walk(folder):
        for name in names:
            with contextlib.suppress(OSError):
                total += (Path(parent) / name).lstat().st_size
    return total


def summary_of(state_dir: Path, state: UpdateState) -> update_summary.Summary:
    """The summary of the update <state> records (update_summary.of_record), with what is on disk
    now: what came of each window (their notes), and whether the backup is still there and its size."""
    backup = Path(state.backup) if state.backup else None
    there = backup is not None and backup.is_dir()
    return update_summary.of_record(
        state,
        update_agents.outcomes(state, update_notes.Files(state_dir)),
        there,
        _bytes(backup) if backup is not None and there else 0,
        _seconds(state),
    )


def _saved(state: UpdateState, text: str) -> Path | None:
    """<text> as summary.md in the update's backup folder, once the update is done and if the folder
    is still there (a kg forget deletes every backup, and may do so meanwhile); where, or None."""
    folder = Path(state.backup) if state.backup else None
    if state.step != "done" or folder is None or not folder.is_dir():
        return None
    path = folder / update_backups.SUMMARY
    tmp = folder / f".{update_backups.SUMMARY}.tmp"
    try:
        tmp.write_text(f"```text\n{text}```\n")
        tmp.replace(path)
    except OSError:
        return None
    return path


def _say_summary(state_dir: Path, state: UpdateState, said: Said) -> None:
    """The summary of the update <state> records, said, kept for --json, and saved once it is done."""
    said.summary = summary_of(state_dir, state)
    text = update_summary.render(said.summary)
    Output.say(text.rstrip("\n"))
    _saved(state, text)


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


def _dry_run(where: Folders, args: argparse.Namespace, said: Said) -> int:
    """The plan alone. An update cut short is never carried on or put back here (that changes
    things): it is said first, and while it is there bosun is not called up to date."""
    recorded = update_state.read(where.state_dir)
    if update_state.running(where.state_dir):
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
    old_root = installed(Path(args.from_root))
    code = 0
    if not _is_this_one(old_root):
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
        saved = _saved(state, update_summary.render(summary_of(state_dir, state)))
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


def main(argv: Sequence[str]) -> int:
    """The command, and with --json one object on stdout on every path: the plan (null when none was
    made: up to date, refused, a failure before it), the update's record as it stands, and the summary
    (null unless an update ended in this run). `note` and `advance` are what the agents and the
    check-in loop run (agents_command)."""
    if argv and argv[0] in AGENT_COMMANDS:
        return agents_command(argv)
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
        print(
            "\nbosun update: stopped; bosun update again carries on, or puts back, what it started",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
