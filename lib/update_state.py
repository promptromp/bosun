"""The update's record and its hold (design: docs/design/update.md, "The steps" and "Cut short").

`bosun update` records its progress in $BOSUN_STATE_DIR/update.json, whole and flushed at every
step, and holds update.lock exclusively for its whole run. While the hold is on (HOLD_STEPS, within
an hour of the start), bosun's own background work skips its runs; the agents keep working. Whether
an update runs is judged by the lock, which the system drops when its holder dies, never by a pid,
which can be anyone's later.

Code of one version reads the record another wrote: a hook or a command started before the switch,
the console. So the record only ever grows: STEPS and FREEZE_STEPS are only ever added to, never
renamed or removed (and only a new step joins FREEZE_STEPS or HOLD_STEPS: old code takes a step it
knows for what it knew it as), and a new field is optional. A field this code does not know is
ignored; a step it does not know was written by a newer bosun (known() is False), which the write
gate refuses and runs again on the installed version, and the hold counts as on.

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
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import journal


STATE_FILE = "update.json"
LOCK_FILE = "update.lock"
UPDATE_DIR = "update"
HOLD_SECONDS = 3600
POLL_SECONDS = 0.05  # how often a wait for update.lock tries again

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


_TUPLES = ("restorable", "pending", "user_changes")


_INTS = ("hold_until", "freeze_ms")
_NAMES = tuple(f.name for f in dataclasses.fields(UpdateState))
_REQUIRED = tuple(f.name for f in dataclasses.fields(UpdateState) if f.default is dataclasses.MISSING)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _right_type(name: str, value: Any) -> bool:
    if name in _TUPLES:
        return _is_strings(value)
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
    known = _known_fields(data)
    if known is None:
        raise RecordUnreadable(path, "is not an update record bosun can read")
    for name in _TUPLES:
        known[name] = tuple(known.get(name, ()))
    return UpdateState(**known)


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
    data = json.dumps(dataclasses.asdict(state), indent=2).encode()
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
    <wait> seconds (a write putting back an update cut short waits a little for another doing it)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _taken(fd, wait):
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="update_state.py")
    parser.add_argument("command", choices=("hold-active", "stuck"))
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "hold-active":
        return 0 if holding(args.state_dir, time.time()) else 1
    why = stuck(args.state_dir, time.time())
    if why:
        print(why)
    return 0 if why else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
