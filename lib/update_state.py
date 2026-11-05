"""The update's record and its hold (design: docs/design/update.md, "The steps" and "Cut short").

`bosun update` records its progress in $BOSUN_STATE_DIR/update.json, whole and flushed at every
step, and holds update.lock exclusively for its whole run. While the hold is on (a step before
`released`, within an hour of the start), bosun's own background work skips its runs; the agents
keep working. Whether an update runs is judged by the lock, which the system drops when its holder
dies, never by a pid, which can be anyone's later.

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
_HOLD_ENDS = STEPS.index("released")


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


def read(state_dir: Path) -> UpdateState | None:
    """The record, or None where there is none or it is unreadable (a .tmp left by a cut is not it)."""
    try:
        data = json.loads((state_dir / STATE_FILE).read_text())
        for name in _TUPLES:
            data[name] = tuple(data.get(name, ()))
        return UpdateState(**data)
    except (OSError, ValueError, TypeError):
        return None


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


def hold_active(state: UpdateState | None, now: float) -> bool:
    return state is not None and STEPS.index(state.step) < _HOLD_ENDS and now < state.hold_until


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


@contextlib.contextmanager
def one_update(state_dir: Path) -> Iterator[None]:
    """update.lock, exclusive, for the whole update; UpdateRunning if another holds it."""
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            other = read(state_dir)
            raise UpdateRunning(
                other.started_at if other else "",
                other.from_commit if other else "",
                other.to_commit if other else "",
            ) from None
        yield
    finally:
        os.close(fd)


def stuck(state_dir: Path, now: float) -> str:
    """Why an update was cut short and left things held, or "" (a hold past hold_until, or a
    freeze step recorded with nothing running)."""
    state = read(state_dir)
    if state is None or STEPS.index(state.step) >= _HOLD_ENDS:
        return ""
    if now >= state.hold_until:
        return f"an update's hold (step {state.step}) outlived its hour"
    if in_freeze(state) and not running(state_dir):
        return f"an update stopped at step {state.step} while writes were paused, and nothing is running it"
    return ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="update_state.py")
    parser.add_argument("command", choices=("hold-active", "stuck"))
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "hold-active":
        return 0 if hold_active(read(args.state_dir), time.time()) else 1
    why = stuck(args.state_dir, time.time())
    if why:
        print(why)
    return 0 if why else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
