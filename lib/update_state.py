"""The update's record and its hold (design: docs/design/update.md, "The steps" and "Cut short").

`bosun update` records its progress in $BOSUN_STATE_DIR/update.json, whole and flushed at every
step, and holds update.lock exclusively for its whole run. While the hold is on (HOLD_STEPS, within
an hour of the start), bosun's own background work skips its runs; the agents keep working. Whether
an update runs is judged by the lock, which the system drops when its holder dies, never by a pid,
which can be anyone's later.

Code of one version reads the record another wrote: a hook or a command started before the switch,
the console. So the record only ever grows: STEPS and FREEZE_STEPS are only ever added to, never
renamed or removed (and only a new step joins FREEZE_STEPS or HOLD_STEPS: old code takes a step it
knows for what it knew it as), and a new field is optional. A new step is recorded only inside the
freeze or after the switch, where the installed bosun knows it; progress before the switch goes in a
field, or old code, still the installed bosun, would refuse every write while it is recorded. A
field this code does not know is ignored; a step it does not know was written by a newer bosun
(known() is False), which the write gate refuses and runs again on the installed version, and the
hold counts as on.

No store (ledger, knowledge) is imported here: the stores import modules that import this one."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import json
import os
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import journal


STATE_FILE = "update.json"
LOCK_FILE = "update.lock"
UPDATE_DIR = "update"
HOLD_SECONDS = 3600
POLL_SECONDS = 0.05  # how often a wait for update.lock tries again
# What one_update waits at the least: a writer's running() takes update.lock shared for an instant,
# and an update must never be refused for that.
PROBE_SECONDS = 0.5

STEPS = (
    "planned",
    "rehearsed",
    "holding",
    "marked",
    "backed-up",
    "migrated",
    "switched",
    "checked",
    "released",
    "agents",
    "done",
    "restored",
    "failed",
)
# Writes are paused from the marker to the switch: the backup is the truth there.
FREEZE_STEPS = ("marked", "backed-up", "migrated", "switched")
# Bosun's own background work is held from `holding` up to `released`; never before it.
HOLD_STEPS = STEPS[STEPS.index("holding") : STEPS.index("released")]
# An update that ended with bosun as it was: cut short before anything changed, or put back.
PUT_BACK = ("failed", "restored")
BEFORE_FREEZE = ("planned", "rehearsed", "holding")  # nothing had changed but the hold
# Past its checks: switched for good (what is left of it rolls forward), or ended with its windows.
PAST_CHECKS = ("checked", "released", "agents", "done")
BACK = "back"  # the kind of a back's record (`bosun update --back`)
# Who holds update.lock, each in a file of its own in UPDATE_DIR naming its pid and update id (marking): an
# update, from before it waits for the lock until it ends, and the check-in loop's turn at the windows an
# update left. A marker is live only while its owner holds an exclusive lock on its lock file beside it
# (<marker>.lock), which the system drops when the owner dies: a pid can be anyone's after a crash and a
# restart. The lock files stay: one removed while another process opens it would let two owners lock apart.
RUN_MARKER = "run"
TURN_MARKER = "turn"
MARKER_LOCK = "{}.lock"
# How long a process tries to lock a marker's lock file before taking it for a live owner's: a status probe
# holds a shared lock on it for an instant (_owned), and a claim at that instant must not lose its marker.
CLAIM_SECONDS = 0.5
CLAIM_POLL_SECONDS = 0.05
# The most an update waits for update.lock while the check-in loop takes its turn at the windows: a turn
# restarts a window or two (each a stop, a launch and its start) and lets go.
TURN_SECONDS = 180.0


class RecordUnreadable(Exception):
    """update.json is there but no record: it cannot be read or parsed, or a field this code knows
    is missing or of the wrong type."""

    def __init__(self, path: Path, why: str) -> None:
        super().__init__(f"{path} {why}")
        self.path = path


class UpdateRunning(Exception):
    """Another update holds the lock; says which one."""

    def __init__(self, started_at: str, from_commit: str, to_commit: str) -> None:
        super().__init__(f"an update is running (started {started_at}, {from_commit} to {to_commit})")
        self.started_at = started_at
        self.from_commit = from_commit
        self.to_commit = to_commit


class LoopTurnRunning(UpdateRunning):
    """The check-in loop's turn at the windows an update left it holds update.lock, past the wait."""

    def __init__(self, waited: float) -> None:
        super().__init__("", "", "")
        said = (
            f"the check-in loop's turn at the windows the last update left it (bosun update advance) still "
            f"holds update.lock after {waited:g} s; run bosun update again once it is done"
        )
        self.args = (said,)

    def __str__(self) -> str:
        return str(self.args[0])


@dataclass(frozen=True)
class UpdateState:
    id: str
    from_root: str
    to_root: str
    from_commit: str
    to_commit: str
    step: str
    started_at: str
    hold_until: int
    backup: str = ""
    restorable: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()  # windows still to bring along
    user_changes: tuple[str, ...] = ()  # what to change in my own files, from the plan
    freeze_ms: int = 0
    error: str = ""
    findings: tuple[str, ...] = ()  # what went wrong after the switch, and what bosun doctor found then
    # Each window brought along (lib/update_agents.py): its kind and the plan's treatment for it, then
    # what was done to it, in order.
    windows: Mapping[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)
    reread: tuple[str, ...] = ()  # the files this update changed that the orchestrator re-reads
    # What the summary says (lib/update_summary.py), kept as each step learns it, so an update carried
    # on by a later run, or finished by the check-in loop, says the same as one that ran in one go: the
    # plan said yes to (update_plan.as_record), each migration that ran (migrations.Applied.as_record),
    # what changed in bosun.env and the copy kept of it, and the launchd jobs reloaded.
    plan: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    migrated: tuple[str, ...] = ()
    config_changed: tuple[str, ...] = ()
    reloaded: tuple[str, ...] = ()
    # What bosun doctor failed at in a run while planning, before the hold: the summary lists only the
    # FAIL lines after the switch that this does not hold.
    doctor_before: tuple[str, ...] = ()
    # When the update's own run stopped waiting for the windows it brings along (ISO 8601, UTC; "" while
    # it waits, or never did): from then on whoever holds update.lock at `agents` is the check-in loop's
    # turn (`bosun update advance`), not the update, which the status then never calls running.
    handed_over: str = ""
    # The last update's record (as `write` writes one), whose windows still waited for this update took
    # over when it started (lib/update_agents.py, take_over): brought along with its own once it
    # switched, and its summary finished then. Put back as the record if this one fails or is put back
    # before that (carried_back), so they stay the check-in loop's.
    carried: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    # Which of the carried record's windows this update took over (update_agents.take_over): the others
    # had closed, or neither update gives them anything; an older record has none.
    taken_over: tuple[str, ...] = ()
    # What this record is: "" an update (every record before `bosun update --back` too), BACK a back
    # (lib/update_back.py), which goes from the version installed to the one the last update came from.
    kind: str = ""
    # The last finished record when this one started (last_finished, as `write` writes one, its own
    # records left out): a back after this update failed still finds the update to go back over, and a
    # back's is the update it goes back over. An older record has none.
    previous: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    # A back's: what each store restored, a line each, as the step learnt it (the summary's).
    restored: tuple[str, ...] = ()
    # The launchd jobs the release unloaded, as the version updated to has none of them.
    unloaded: tuple[str, ...] = ()


_TUPLES = (
    "restorable",
    "pending",
    "user_changes",
    "findings",
    "reread",
    "migrated",
    "config_changed",
    "reloaded",
    "doctor_before",
    "taken_over",
    "restored",
    "unloaded",
)
_MAPPINGS = ("windows",)  # of window names to lists of strings
_OBJECTS = ("plan", "carried", "previous")  # JSON objects, read as they are (their reader checks their parts)


_INTS = ("hold_until", "freeze_ms")
_NAMES = tuple(f.name for f in dataclasses.fields(UpdateState))
_REQUIRED = tuple(
    f.name
    for f in dataclasses.fields(UpdateState)
    if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _is_mapping_of_strings(value: Any) -> bool:
    return isinstance(value, dict) and all(isinstance(k, str) and _is_strings(v) for k, v in value.items())


def _right_type(name: str, value: Any) -> bool:
    if name in _TUPLES:
        return _is_strings(value)
    if name in _MAPPINGS:
        return _is_mapping_of_strings(value)
    if name in _OBJECTS:
        return isinstance(value, dict)
    if name in _INTS:
        return _is_int(value)
    return isinstance(value, str)


def _known_fields(data: Any) -> dict[str, Any] | None:
    """The fields of <data> this version knows, when it is an object holding every required one,
    each of the right type; else None. Fields it does not know (a newer version's) are left out."""
    if not isinstance(data, dict) or not set(_REQUIRED) <= data.keys():
        return None
    known = {name: value for name, value in data.items() if name in _NAMES}
    if not all(_right_type(name, value) for name, value in known.items()):
        return None
    return known


def load(state_dir: Path) -> UpdateState | None:
    """The record (its step may be a newer bosun's: known()), None where there is none, and
    RecordUnreadable where update.json is there but no record (a .tmp left by a cut is not it)."""
    path = state_dir / STATE_FILE
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RecordUnreadable(path, f"cannot be read ({exc.strerror or exc})") from exc
    except ValueError as exc:
        raise RecordUnreadable(path, "is not JSON") from exc
    state = from_object(data)
    if state is None:
        raise RecordUnreadable(path, "is not an update record bosun can read")
    return state


def from_object(data: Any) -> UpdateState | None:
    """The record <data> holds (a JSON object, as `write` writes one, or the `carried` field), or None
    when it is no record this code can read (_known_fields)."""
    known = _known_fields(data)
    if known is None:
        return None
    for name in _TUPLES:
        known[name] = tuple(known.get(name, ()))
    for name in _MAPPINGS:
        known[name] = {key: tuple(values) for key, values in known.get(name, {}).items()}
    return UpdateState(**known)


def as_object(state: UpdateState) -> dict[str, Any]:
    """<state> as `write` writes it, JSON's types only: what from_object reads back."""
    return dict(json.loads(json.dumps(dataclasses.asdict(state))))


def read(state_dir: Path) -> UpdateState | None:
    """The record, or None where there is none or it is no record (load says which)."""
    try:
        return load(state_dir)
    except RecordUnreadable:
        return None


def known(state: UpdateState) -> bool:
    """Whether this code knows <state>'s step: one it does not was written by a newer bosun."""
    return state.step in STEPS


def write(state_dir: Path, state: UpdateState) -> None:
    """The record, whole and flushed: a .tmp beside it, renamed over it."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / STATE_FILE
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(as_object(state), indent=2).encode()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        journal.write_all(fd, data)
        journal.flush_to_disk(fd)
    finally:
        os.close(fd)
    journal.replace_durably(tmp, path)


def advanced(state: UpdateState, step: str, **fields: Any) -> UpdateState:
    if step not in STEPS:
        raise ValueError(f"unknown update step {step!r}")
    return dataclasses.replace(state, step=step, **fields)


def _holds(state: UpdateState) -> bool:
    """Whether <state>'s step is one the hold is on at: HOLD_STEPS, or a newer bosun's step (taken
    for one, as this code cannot tell)."""
    return state.step in HOLD_STEPS or not known(state)


def hold_active(state: UpdateState | None, now: float) -> bool:
    return state is not None and _holds(state) and now < state.hold_until


def in_freeze(state: UpdateState | None) -> bool:
    return state is not None and state.step in FREEZE_STEPS


def running(state_dir: Path) -> bool:
    """Whether an update holds update.lock: a shared lock cannot be taken beside its exclusive one."""
    try:
        fd = os.open(state_dir / LOCK_FILE, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)  # closing releases a lock taken
    return False


def _taken(fd: int, wait: float) -> bool:
    """Whether <fd>'s exclusive lock was taken within <wait> seconds (tried at once, then every
    POLL_SECONDS), never blocking past it."""
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_SECONDS)
        else:
            return True


@contextlib.contextmanager
def one_update(state_dir: Path, wait: float = 0.0) -> Iterator[None]:
    """update.lock, exclusive, for the whole update; UpdateRunning if another still holds it after
    <wait> seconds (a write putting back an update cut short waits a little for another doing it),
    and never less than PROBE_SECONDS, which a writer's running() probe does not outlast."""
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        waited = max(wait, PROBE_SECONDS)
        if not _taken(fd, waited):
            if turn_holds(state_dir):
                raise LoopTurnRunning(waited)
            other = read(state_dir)
            raise UpdateRunning(
                other.started_at if other else "",
                other.from_commit if other else "",
                other.to_commit if other else "",
            )
        yield
    finally:
        os.close(fd)


def holding(state_dir: Path, now: float) -> bool:
    """Whether the hold counts: an update is running (the lock) and its hold is within the hour. A
    hold an update that died left lapses at once."""
    return running(state_dir) and hold_active(read(state_dir), now)


def stuck(state_dir: Path, now: float) -> str:
    """Why an update was cut short and left its hold, or "": a record at a step the hold is on at
    with nothing running it, or a running one whose hold outlived its hour."""
    state = read(state_dir)
    if state is None or not _holds(state):
        return ""
    if not running(state_dir):
        newer = "" if known(state) else " (a step of a newer bosun)"
        return f"an update stopped at step {state.step}{newer} and left its hold; run bosun update"
    if now >= state.hold_until:
        return f"an update's hold (step {state.step}) outlived its hour"
    return ""


def owing(state: UpdateState | None, abandoned: bool = False) -> UpdateState | None:
    """The record of the update that still waits for windows, as <state> tells it: <state> itself at
    `agents` with windows pending; the record it carried (carried) when <state> failed or was put back
    before it took them over, or stopped before its freeze with nothing carrying it on (<abandoned>: a
    takeover killed while it planned or held, which the check-in loop then ends and gives them back);
    else None."""
    if state is None:
        return None
    if state.step == "agents":
        return state if state.pending else None
    ended = state.step in PUT_BACK or (abandoned and state.step in BEFORE_FREEZE)
    if ended and state.carried:
        return owing(from_object(state.carried))
    return None


def last_finished(state: UpdateState | None) -> UpdateState | None:
    """The last record that changed the version, as <state> tells it: <state> itself once past its checks
    (PAST_CHECKS: what is left of it rolls forward); else, for one that ended with bosun as it was or will
    be put back (before its checks), the one before it (`previous`); None when there is none."""
    if state is None:
        return None
    if state.step in PAST_CHECKS:
        return state
    return from_object(state.previous) if state.previous else None


def bare(state: UpdateState | None) -> dict[str, Any]:
    """<state> as `write` writes it, without the records it keeps (`carried`, `previous`): what another
    record keeps of it, so records never nest; {} for None."""
    if state is None:
        return {}
    return as_object(dataclasses.replace(state, carried={}, previous={}))


def carried_back(state_dir: Path) -> UpdateState | None:
    """For a caller that holds update.lock: an update that failed or was put back before it took over
    the windows the last one still waited for (its `carried` record) gives them back, the last
    record written again as it was, so the check-in loop carries on with them; the record as it is
    then (None where there is none). One stopped before its freeze is recorded `failed` first
    (end_abandoned)."""
    state = end_abandoned(state_dir)
    if state is None or state.step not in PUT_BACK or not state.carried:
        return state
    last = from_object(state.carried)
    if last is None:
        return state
    write(state_dir, last)
    return last


def end_abandoned(state_dir: Path, holder: bool = False) -> UpdateState | None:
    """For a caller that holds update.lock: an update stopped before its freeze that no process carries
    on recorded `failed`, as nothing had changed but the hold, which lapsed; the record as it is then.
    No process carries it on when no RUN_MARKER is live, or when the caller is an update that holds the
    lock and has not written its own record yet (<holder>: the marker is its own)."""
    state = read(state_dir)
    carried_on = not holder and marked(state_dir, RUN_MARKER)
    if state is None or state.step not in BEFORE_FREEZE or carried_on:
        return state
    failed = advanced(state, "failed", error=f"cut short at step {state.step}; nothing had changed")
    write(state_dir, failed)
    return failed


def windows_pending(state_dir: Path) -> bool:
    """Whether an update is bringing windows along and some are still waited for (owing): the
    check-in loop then takes a turn at them (`bosun update advance`)."""
    return owing(read(state_dir), abandoned=abandoned(state_dir)) is not None


def handed(state: UpdateState) -> UpdateState:
    """<state> with its windows handed over to the check-in loop now (`handed_over`), unless they were."""
    if state.handed_over:
        return state
    return dataclasses.replace(state, handed_over=datetime.now(UTC).isoformat(timespec="seconds"))


# ---- who holds update.lock: each holder says so in a marker of its own ----------------------------


def _marker(state_dir: Path, name: str) -> Path:
    return state_dir / UPDATE_DIR / name


def _lock_path(state_dir: Path, name: str) -> Path:
    return _marker(state_dir, MARKER_LOCK.format(name))


def _owned(state_dir: Path, name: str) -> bool:
    """Whether marker <name>'s owner lives: some process holds its lock file exclusively, so a shared lock
    cannot be taken beside it (as `running` asks of update.lock; the asker may be the owner itself, whose
    lock is on another open file)."""
    try:
        fd = os.open(_lock_path(state_dir, name), os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)  # closing releases a lock taken
    return False


def _read_marker(path: Path) -> tuple[int, str]:
    """The pid a marker names and the update id after it ("" while it has none); (0, "") unreadable."""
    try:
        first, _, rest = path.read_text().partition("\n")
        return int(first.strip()), rest.strip()
    except (OSError, ValueError):
        return 0, ""


def _live_marker(state_dir: Path, name: str) -> tuple[int, str] | None:
    """Marker <name>'s pid and id while its owner holds its lock file; None when nothing holds it (a marker
    left by a crash, whatever pid it names) or when there is no marker. (0, "") for one held but not yet
    written."""
    if not _owned(state_dir, name):
        return None
    return _read_marker(_marker(state_dir, name))


def marked(state_dir: Path, name: str) -> bool:
    """Whether marker <name> (RUN_MARKER, TURN_MARKER) has a live owner (its lock file held); a marker
    whose owner is gone (a crash), or that cannot be read, counts as absent."""
    return _live_marker(state_dir, name) is not None


def _claimed(state_dir: Path, name: str) -> int | None:
    """The open lock file of marker <name>, locked exclusively by this process, which now owns the marker
    (written anew: one left by a crash is replaced); None while another live process owns it, tried for
    CLAIM_SECONDS, past a reader's brief shared lock."""
    fd = os.open(_lock_path(state_dir, name), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + CLAIM_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:  # a live owner: a reader's shared lock is gone long before
                os.close(fd)
                return None
            time.sleep(CLAIM_POLL_SECONDS)
    _write_marker(_marker(state_dir, name), f"{os.getpid()}\n")
    return fd


def _write_marker(path: Path, text: str) -> None:
    """<text> in marker <path> at once (a temp file renamed into place), for its owner only."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


@contextlib.contextmanager
def marking(state_dir: Path, name: str) -> Iterator[None]:
    """Marker <name> naming this process, from now until the block ends: RUN_MARKER from before an update
    waits for update.lock until it ends, TURN_MARKER around the check-in loop's turn. Owned by whoever
    holds its lock file, so of two at once only one owns it; another live process's marker is left as it
    is (a second update, to be refused, never takes the first one's away). Removed as it ends, while its
    lock is still held, then the lock let go."""
    path = _marker(state_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _claimed(state_dir, name)
    try:
        yield
    finally:
        if fd is not None:
            path.unlink(missing_ok=True)
            os.close(fd)


def mark_update(state_dir: Path, update_id: str) -> None:
    """The update id <update_id> added to this process's RUN_MARKER (once its record has one), so the
    status tells its record from one an earlier update left."""
    path = _marker(state_dir, RUN_MARKER)
    pid, known = _read_marker(path)
    if pid != os.getpid() or known == update_id:
        return
    _write_marker(path, f"{pid}\n{update_id}\n")


def record_runs(state_dir: Path, state: UpdateState | None) -> bool:
    """Whether the update <state> records is the one running now: the live RUN_MARKER names its id; or,
    with no live marker, the lock is held and not by the check-in loop's turn (an older bosun's update)."""
    if state is None:
        return False
    live = _live_marker(state_dir, RUN_MARKER)
    if live is not None:
        return live[1] == state.id
    return running(state_dir) and not marked(state_dir, TURN_MARKER)


def turn_holds(state_dir: Path) -> bool:
    """Whether the check-in loop's turn counts as the holder of update.lock: its marker is live and no
    other process's RUN_MARKER is (the turn writes its marker before it tries the lock)."""
    run = _live_marker(state_dir, RUN_MARKER)
    return marked(state_dir, TURN_MARKER) and (run is None or run[0] == os.getpid())


def update_runs(state_dir: Path) -> bool:
    """Whether an update runs now, as the holders of update.lock say (decision: never guessed from the
    record): an update's marker naming a live process (it waits for the lock or holds it), or the lock
    held with no live marker of the check-in loop's turn (a writer putting back a freeze, an older bosun)."""
    return marked(state_dir, RUN_MARKER) or (running(state_dir) and not marked(state_dir, TURN_MARKER))


def abandoned(state_dir: Path) -> bool:
    """Whether nothing carries on the update the record names: no process holds update.lock, and no
    update waits for it (a live RUN_MARKER)."""
    return not running(state_dir) and not marked(state_dir, RUN_MARKER)


def lock_wait(state_dir: Path) -> float:
    """How long an update waits for update.lock: TURN_SECONDS while the check-in loop takes its turn at
    the windows (a live TURN_MARKER), which gives way between windows once the update's own marker is
    there; else no longer than one_update's least."""
    return TURN_SECONDS if turn_holds(state_dir) else 0.0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="update_state.py")
    parser.add_argument("command", choices=("hold-active", "running", "stuck", "windows-pending"))
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "running":
        return 0 if running(args.state_dir) else 1
    if args.command == "hold-active":
        return 0 if holding(args.state_dir, time.time()) else 1
    if args.command == "windows-pending":
        return 0 if windows_pending(args.state_dir) else 1
    why = stuck(args.state_dir, time.time())
    if why:
        print(why)
    return 0 if why else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
