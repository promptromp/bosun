"""The notes the windows answer an update with (docs/design/update.md, "Bringing the agents along"):
a file per window and kind (stock or status) in the state folder, update/notes/<name>.<kind>, written
by `bosun update note` and read by the update's turns, its plan and its summary.

The window's name is percent-encoded into the file's name (letters, digits and `_.-~` stay as they
are), so every name a window can have is a note of its own in that folder, never a path elsewhere: a
window bosun brings along can always answer, and be answered for by hand.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

import update_agents
import update_state


NOTES = Path(update_state.UPDATE_DIR) / "notes"


class NoteRefused(ValueError):
    """A note for a kind there is none of, or for no window (an empty name)."""


def note_path(state_dir: Path, window: str, kind: str) -> Path:
    if kind not in update_agents.KINDS:
        raise NoteRefused(
            f"there is no {kind!r} note: a note is the window's {' or '.join(update_agents.KINDS)}"
        )
    if not window:
        raise NoteRefused("a note needs the window's name")
    return state_dir / NOTES / f"{quote(window, safe='')}.{kind}"


def note(state_dir: Path, window: str, kind: str, text: str) -> None:
    """<window>'s <kind> note: <text>, written whole."""
    path = note_path(state_dir, window, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".note.")
    with os.fdopen(fd, "w") as out:
        out.write(text.strip() + "\n")
    Path(tmp).replace(path)


def read_note(state_dir: Path, window: str, kind: str) -> str | None:
    try:
        return note_path(state_dir, window, kind).read_text().strip()
    except FileNotFoundError:
        return None


def clear_notes(state_dir: Path) -> None:
    """The notes an earlier update left: none of them answers this one."""
    shutil.rmtree(state_dir / NOTES, ignore_errors=True)


class Files:
    """update_agents.Notes as the note files in <state_dir>."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

    def read(self, window: str, kind: str) -> str | None:
        return read_note(self.state_dir, window, kind)

    def save(self, window: str, kind: str, text: str) -> None:
        note(self.state_dir, window, kind, text)

    def where(self, window: str, kind: str) -> str:
        return str(note_path(self.state_dir, window, kind))
