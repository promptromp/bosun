"""What bosun changed in the agents' own files, on record so that `bosun uninstall` undoes exactly that.

Those files are not bosun's. An entry there looks the same whoever wrote it: a folder you accepted yourself
is just as trusted as one bosun marked. So nothing is guessed from what is there, the way a package
manager's receipt records what it installed. Losing this file, or an entry in it that is not well formed,
only means those changes stay, which is harmless: they are what you would have got by accepting the prompt
yourself. Design: docs/design/uninstall.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent_files import read_object, write_atomically


FILE = "agent-changes.json"  # in bosun's state dir
PAIR = 2  # [harness, folder]
TRUST = "trust"
NOTES = "memory_notes"


def well_formed_pairs(value: Any) -> list[list[str]]:
    entries = value if isinstance(value, list) else []
    return [
        entry
        for entry in entries
        if isinstance(entry, list) and len(entry) == PAIR and all(isinstance(part, str) for part in entry)
    ]


def read(state_dir: Path) -> dict[str, Any]:
    """The record, with only its well-formed entries: anyone may have edited it."""
    found = read_object(state_dir / FILE) or {}
    notes = found.get(NOTES)
    return {
        TRUST: well_formed_pairs(found.get(TRUST)),
        NOTES: [note for note in notes if isinstance(note, str)] if isinstance(notes, list) else [],
    }


def write(state_dir: Path, changes: dict[str, Any]) -> None:
    write_atomically(state_dir / FILE, json.dumps(changes, indent=2, sort_keys=True))


def trust_given(state_dir: Path) -> tuple[tuple[str, str], ...]:
    """(harness, folder), for each folder bosun marked as trusted by that agent."""
    return tuple((harness, folder) for harness, folder in read(state_dir)[TRUST])


def record_trust(state_dir: Path, harness: str, directory: str) -> None:
    changes = read(state_dir)
    pair = [harness, os.path.realpath(directory)]
    if pair not in changes[TRUST]:
        changes[TRUST].append(pair)
        write(state_dir, changes)


def forget_trust(state_dir: Path, harness: str, directory: str) -> None:
    changes = read(state_dir)
    pair = [harness, os.path.realpath(directory)]
    if pair in changes[TRUST]:
        changes[TRUST].remove(pair)
        write(state_dir, changes)


def notes(state_dir: Path) -> tuple[str, ...]:
    """The memory notes bosun wrote in an agent's memory."""
    return tuple(read(state_dir)[NOTES])


def record_note(state_dir: Path, note: Path) -> None:
    changes = read(state_dir)
    changes[NOTES].append(str(note))
    write(state_dir, changes)


def forget_note(state_dir: Path, note: str) -> None:
    changes = read(state_dir)
    if note in changes[NOTES]:
        changes[NOTES].remove(note)
        write(state_dir, changes)
