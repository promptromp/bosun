"""What bosun changed in the agents' own files, and the folders it made beside a window's repository, on
record so that `bosun uninstall` undoes exactly that.

Those files are not bosun's. An entry there looks the same whoever wrote it: a folder you accepted yourself
is just as trusted as one bosun marked. So nothing is guessed from what is there, the way a package
manager's receipt records what it installed. Losing this file, or an entry in it that is not well formed,
only means those changes stay, which is harmless: they are what you would have got by accepting the prompt
yourself. The same goes for a repo window's worktrees folder (`<repo>.worktrees`, which `bosun launch` makes
beside the repository before the agent starts): only one bosun made goes on record, never one that was there,
and uninstall removes it only while it is empty. Design: docs/design/uninstall.md.

Usage: agent_changes.py make-folder <state dir> <folder>
           make <folder> (its parent must be there), and put it on record unless it was there already; exit 0
           made or there already, 1 with a line on stderr when it could not be made or put on record
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_files import read_object, write_atomically


FILE = "agent-changes.json"  # in bosun's state dir
LOCK = "agent-changes.lock"  # held while a change is written: windows starting together each add their folder
PAIR = 2  # [harness, folder]
TRUST = "trust"
NOTES = "memory_notes"
SETUPS = "setups"  # the agents bosun made a one-time setup in (AgentSetup in lib/agent_files.py)
FOLDERS = "folders"  # the worktrees folders bosun made beside a window's repository, absolute paths
USAGE = 2


def well_formed_pairs(value: Any) -> list[list[str]]:
    entries = value if isinstance(value, list) else []
    return [
        entry
        for entry in entries
        if isinstance(entry, list) and len(entry) == PAIR and all(isinstance(part, str) for part in entry)
    ]


def well_formed_names(value: Any) -> list[str]:
    return [name for name in value if isinstance(name, str)] if isinstance(value, list) else []


def well_formed_folders(value: Any) -> list[str]:
    """Absolute paths only: a relative one would be read against wherever uninstall runs."""
    return [name for name in well_formed_names(value) if os.path.isabs(name)]


def read(state_dir: Path) -> dict[str, Any]:
    """The record, with only its well-formed entries: anyone may have edited it."""
    found = read_object(state_dir / FILE) or {}
    notes = found.get(NOTES)
    return {
        TRUST: well_formed_pairs(found.get(TRUST)),
        NOTES: [note for note in notes if isinstance(note, str)] if isinstance(notes, list) else [],
        SETUPS: well_formed_names(found.get(SETUPS)),
        FOLDERS: well_formed_folders(found.get(FOLDERS)),
    }


def write(state_dir: Path, changes: dict[str, Any]) -> None:
    write_atomically(state_dir / FILE, json.dumps(changes, indent=2, sort_keys=True))


@contextlib.contextmanager
def changing(state_dir: Path) -> Iterator[dict[str, Any]]:
    """The record to change in the block, written back after it if it changed, all under the lock: two
    writers reading it at once would each write back their own change only."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / LOCK).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        changes = read(state_dir)
        before = json.dumps(changes, sort_keys=True)
        yield changes
        if json.dumps(changes, sort_keys=True) != before:
            write(state_dir, changes)


def add(state_dir: Path, key: str, entry: Any) -> None:
    with changing(state_dir) as changes:
        if entry not in changes[key]:
            changes[key].append(entry)


def remove(state_dir: Path, key: str, entry: Any) -> None:
    with changing(state_dir) as changes:
        if entry in changes[key]:
            changes[key].remove(entry)


def trust_given(state_dir: Path) -> tuple[tuple[str, str], ...]:
    """(harness, folder), for each folder bosun marked as trusted by that agent."""
    return tuple((harness, folder) for harness, folder in read(state_dir)[TRUST])


def record_trust(state_dir: Path, harness: str, directory: str) -> None:
    add(state_dir, TRUST, [harness, os.path.realpath(directory)])


def forget_trust(state_dir: Path, harness: str, directory: str) -> None:
    remove(state_dir, TRUST, [harness, os.path.realpath(directory)])


def notes(state_dir: Path) -> tuple[str, ...]:
    """The memory notes bosun wrote in an agent's memory."""
    return tuple(read(state_dir)[NOTES])


def record_note(state_dir: Path, note: Path) -> None:
    with changing(state_dir) as changes:
        changes[NOTES].append(str(note))


def forget_note(state_dir: Path, note: str) -> None:
    remove(state_dir, NOTES, note)


def setups_made(state_dir: Path) -> tuple[str, ...]:
    """The agents bosun made a one-time setup in."""
    return tuple(read(state_dir)[SETUPS])


def record_setup(state_dir: Path, harness: str) -> None:
    add(state_dir, SETUPS, harness)


def forget_setup(state_dir: Path, harness: str) -> None:
    remove(state_dir, SETUPS, harness)


def folders_made(state_dir: Path) -> tuple[str, ...]:
    """The worktrees folders bosun made beside a window's repository."""
    return tuple(read(state_dir)[FOLDERS])


def record_folder(state_dir: Path, folder: str) -> None:
    add(state_dir, FOLDERS, folder)


def forget_folder(state_dir: Path, folder: str) -> None:
    remove(state_dir, FOLDERS, folder)


def failed(line: str) -> int:
    print(line, file=sys.stderr)
    return 1


def make_folder(state_dir: Path, folder: str) -> int:
    """Make <folder> and put it on record; one that is there already is not bosun's, and is left off it. Only
    the folder itself is made (os.mkdir, not makedirs), so that the record says exactly what bosun made."""
    try:
        os.mkdir(folder)
    except FileExistsError:
        return 0 if os.path.isdir(folder) else failed(f"{folder} is there, but is not a folder")
    except OSError as error:
        return failed(f"could not make {folder}: {error.strerror or error}")
    try:
        record_folder(state_dir, folder)
    except OSError as error:
        why = error.strerror or error
        return failed(f"made {folder}, but could not put it on record for uninstall: {why}")
    return 0


def main(argv: list[str]) -> int:
    match argv:
        case ["make-folder", state_dir, folder] if os.path.isabs(folder):
            return make_folder(Path(state_dir), folder)
    print(__doc__.split("Usage: ", 1)[1].strip(), file=sys.stderr)
    return USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
