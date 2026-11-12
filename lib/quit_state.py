"""What `bosun quit` leaves behind: bosun is stopped until it is started again (docs/design/menu-bar.md).

One file in the state folder, quit.json, `{"since": <ISO time with its zone>, "morning_paused": <bool>}`.
While it is there the watchdog and the scheduled learning run skip their runs, and while it says the
morning is paused the morning run does too. `bosun start` (and a morning run that is not paused) clears it.

A file that is there but cannot be read (not JSON, a field missing or of the wrong kind) was written whole
by bosun, so something is wrong; it counts as a quit with the morning paused, since when the file was last
changed. That is the safe side: nothing restarts or alerts, and starting bosun clears it. It is never
read as "bosun is running", and never raises.

Run by libexec scripts:

    quit_state.py stopped --state-dir D   exit 0 and print the local time (HH:MM) of the quit when stopped
    quit_state.py paused  --state-dir D   the same when the morning start is paused too

Both print nothing and exit 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import agent_files


QUIT_FILE = "quit.json"


@dataclass(frozen=True)
class QuitState:
    since: datetime  # when bosun was quit; always timezone-aware
    morning_paused: bool  # whether the morning run stays off until bosun is started


def _parse(text: str) -> QuitState | None:
    """The state <text> holds, or None when it is not a well-formed one."""
    try:
        data = json.loads(text)
        since = datetime.fromisoformat(data["since"])
        paused = data["morning_paused"]
    except (ValueError, KeyError, TypeError):
        return None
    if since.tzinfo is None or not isinstance(paused, bool):
        return None
    return QuitState(since, paused)


def _unreadable(path: Path) -> QuitState:
    try:
        changed = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        changed = datetime.now(UTC)
    return QuitState(changed, True)


def read(state_dir: Path) -> QuitState | None:
    """The quit in <state_dir>, or None when bosun has not been quit. A file that cannot be read is a quit
    with the morning paused (see the top)."""
    path = state_dir / QUIT_FILE
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):  # unreadable, or not text at all
        return _unreadable(path)
    return _parse(text) or _unreadable(path)


def write(state_dir: Path, morning_paused: bool, now: datetime | None = None) -> None:
    """Record that bosun is quit as of <now>, replacing the file whole."""
    since = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    agent_files.write_atomically(
        state_dir / QUIT_FILE, json.dumps({"since": since, "morning_paused": morning_paused}) + "\n"
    )


def clear(state_dir: Path) -> None:
    """Bosun is started again: forget the quit (there may be none)."""
    (state_dir / QUIT_FILE).unlink(missing_ok=True)


def clock(state: QuitState) -> str:
    """When it was quit, as the local HH:MM that messages show."""
    return state.since.astimezone().strftime("%H:%M")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="quit_state.py", allow_abbrev=False)
    parser.add_argument("command", choices=("stopped", "paused"))
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    state = read(args.state_dir)
    if state is None or (args.command == "paused" and not state.morning_paused):
        return 1
    print(clock(state))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
