"""What bosun changed in the agents' own files, and the folders it made beside a window's repository, on
record so that `bosun uninstall` undoes exactly that.

Those files are not bosun's. An entry there looks the same whoever wrote it: a folder you accepted yourself
is just as trusted as one bosun marked. So nothing is guessed from what is there, the way a package
manager's receipt records what it installed. Losing this file, or an entry in it that is not well formed,
only means those changes stay, which is harmless: they are what you would have got by accepting the prompt
yourself. The same goes for a repo window's worktrees folder (`<repo>.worktrees`, which `bosun launch` makes
beside the repository before the agent starts): only one bosun made goes on record, never one that was there,
and uninstall removes it only while it is empty. A folder entry is well formed only as a full path named
`<something>.worktrees`, written one way only, and neither your home folder, /, nor in one of bosun's own
folders (folder_allowed). Writers hold a lock, for LOCK_WAIT_SECONDS at the most, and write back the keys
they do not know as they were. Design: docs/design/uninstall.md.

Usage: agent_changes.py make-folder <state dir> <folder>
           make <folder> (its parent must be there), and put it on record unless it was there already; exit 0
           made or there already, 1 with a line on stderr when it could not be made or put on record, or may
           not be (BOSUN_CONFIG_DIR, BOSUN_LOG_DIR and BOSUN_INSTALL name bosun's folders, or the defaults)
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import install_paths
import run_locked
from agent_files import read_object, write_atomically


FILE = "agent-changes.json"  # in bosun's state dir
LOCK = "agent-changes.lock"  # held while a change is written: windows starting together each add their folder
LOCK_WAIT_SECONDS = 10.0  # a write takes milliseconds: past this, the one holding it is stuck
PAIR = 2  # [harness, folder]
TRUST = "trust"
NOTES = "memory_notes"
SETUPS = "setups"  # the agents bosun made a one-time setup in (AgentSetup in lib/agent_files.py)
FOLDERS = "folders"  # the worktrees folders bosun made beside a window's repository, absolute paths
KNOWN = (TRUST, NOTES, SETUPS, FOLDERS)  # another key, a newer bosun's, is written back as it was
SUFFIX = ".worktrees"
ROOT = Path(__file__).resolve().parents[1]  # this bosun's own folder
USAGE = 2


class Busy(OSError):
    """Another writer held the record past LOCK_WAIT_SECONDS."""


def well_formed_pairs(value: Any) -> list[list[str]]:
    entries = value if isinstance(value, list) else []
    return [
        entry
        for entry in entries
        if isinstance(entry, list) and len(entry) == PAIR and all(isinstance(part, str) for part in entry)
    ]


def well_formed_names(value: Any) -> list[str]:
    return [name for name in value if isinstance(name, str)] if isinstance(value, list) else []


def bosuns_folders(state_dir: Path, env: Mapping[str, str]) -> list[Path]:
    """Bosun's own folders, which no worktrees folder may be or be in: the state folder, and the config, log
    and install folders as the environment names them, or where they are by default."""
    install = Path(env.get("BOSUN_INSTALL") or install_paths.install_path(ROOT))
    return [
        state_dir,
        Path(env.get("BOSUN_CONFIG_DIR") or Path.home() / ".config" / "bosun"),
        Path(env.get("BOSUN_LOG_DIR") or state_dir / "logs"),
        ROOT,
        install,
        install_paths.versions_dir(install),
    ]


def spellings(path: str | Path) -> set[Path]:
    """<path> as written and as it resolves, so that another spelling of a folder is still that folder."""
    return {Path(os.path.normpath(path)), Path(os.path.realpath(path))}


def shaped_as_worktrees(path: str) -> bool:
    """A full path written one way only (no trailing slash, no `.` or `..`), named <something>.worktrees."""
    name = os.path.basename(path)
    return (
        os.path.isabs(path)
        and os.path.normpath(path) == path
        and len(name) > len(SUFFIX)
        and name.endswith(SUFFIX)
    )


def folder_allowed(path: str, state_dir: Path, env: Mapping[str, str] = os.environ) -> bool:
    """Whether <path> may be a worktrees folder on record: shaped as one (shaped_as_worktrees), neither your
    home folder nor /, and neither one of bosun's own folders (bosuns_folders) nor in one."""
    if not shaped_as_worktrees(path):
        return False
    candidates = spellings(path)
    if candidates & (spellings(Path.home()) | spellings("/")):
        return False
    own = set().union(*(spellings(folder) for folder in bosuns_folders(state_dir, env)))
    return not any(
        folder == candidate or folder in candidate.parents for candidate in candidates for folder in own
    )


def well_formed(found: Mapping[str, Any], state_dir: Path) -> dict[str, Any]:
    notes = found.get(NOTES)
    folders = well_formed_names(found.get(FOLDERS))
    return {
        TRUST: well_formed_pairs(found.get(TRUST)),
        NOTES: [note for note in notes if isinstance(note, str)] if isinstance(notes, list) else [],
        SETUPS: well_formed_names(found.get(SETUPS)),
        FOLDERS: [folder for folder in folders if folder_allowed(folder, state_dir)],
    }


def read(state_dir: Path) -> dict[str, Any]:
    """The record, with only its well-formed entries: anyone may have edited it."""
    return well_formed(read_object(state_dir / FILE) or {}, state_dir)


def write(state_dir: Path, changes: dict[str, Any]) -> None:
    write_atomically(state_dir / FILE, json.dumps(changes, indent=2, sort_keys=True))


@contextlib.contextmanager
def changing(state_dir: Path) -> Iterator[dict[str, Any]]:
    """The record to change in the block, written back after it if it changed, all under the lock: two
    writers reading it at once would each write back their own change only. A key this bosun does not know
    is written back as it was. Busy if another writer held the lock past LOCK_WAIT_SECONDS."""
    fd = run_locked.acquire(str(state_dir / LOCK), LOCK_WAIT_SECONDS)
    if fd is None:
        raise Busy(f"another bosun held {state_dir / LOCK} for over {LOCK_WAIT_SECONDS:g} seconds")
    try:
        found = read_object(state_dir / FILE) or {}
        changes = well_formed(found, state_dir)
        before = json.dumps(changes, sort_keys=True)
        yield changes
        if json.dumps(changes, sort_keys=True) != before:
            write(state_dir, {**{key: found[key] for key in found if key not in KNOWN}, **changes})
    finally:
        os.close(fd)


def change(state_dir: Path, what: str, edit: Callable[[dict[str, Any]], None]) -> bool:
    """Make <edit> to the record (changing); whether it is made. Not when the record was Busy: one line on
    stderr says that <what> is not on record, and the caller carries on."""
    try:
        with changing(state_dir) as changes:
            edit(changes)
    except Busy as busy:
        print(f"bosun: {busy}, so {what} is not on record for bosun uninstall", file=sys.stderr)
        return False
    return True


def add(state_dir: Path, key: str, entry: Any) -> bool:
    """Whether <entry> is on record under <key> now."""

    def edit(changes: dict[str, Any]) -> None:
        if entry not in changes[key]:
            changes[key].append(entry)

    return change(state_dir, str(entry), edit)


def remove(state_dir: Path, key: str, entry: Any) -> None:
    def edit(changes: dict[str, Any]) -> None:
        if entry in changes[key]:
            changes[key].remove(entry)

    change(state_dir, f"the removal of {entry}", edit)


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
    change(state_dir, str(note), lambda changes: changes[NOTES].append(str(note)))


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


def record_folder(state_dir: Path, folder: str) -> bool:
    """Whether <folder> is on record now: never one that is not folder_allowed."""
    return folder_allowed(folder, state_dir) and add(state_dir, FOLDERS, folder)


def forget_folder(state_dir: Path, folder: str) -> None:
    remove(state_dir, FOLDERS, folder)


def failed(line: str) -> int:
    print(line, file=sys.stderr)
    return 1


def make_folder(state_dir: Path, folder: str) -> int:
    """Make <folder> and put it on record; one that is there already is not bosun's, and is left off it. Only
    the folder itself is made (os.mkdir, not makedirs), so that the record says exactly what bosun made, and
    never one that may not be on record (folder_allowed). Each failure is one line on stderr, exit 1."""
    if not folder_allowed(folder, state_dir):
        return failed(f"{folder} is not a folder bosun makes for worktrees, so the window starts without it")
    try:
        os.mkdir(folder)
    except FileExistsError:
        if os.path.isdir(folder):
            return 0
        return failed(f"{folder} is there, but is not a folder, so the window starts without it")
    except OSError as error:
        return failed(f"could not make {folder} ({error.strerror or error}), so the window starts without it")
    try:
        recorded = record_folder(state_dir, folder)
    except OSError as error:
        return failed(
            f"made {folder}, but could not put it on record for bosun uninstall: {error.strerror or error}"
        )
    return 0 if recorded else 1  # its line is said already


def main(argv: list[str]) -> int:
    match argv:
        case ["make-folder", state_dir, folder] if os.path.isabs(folder):
            return make_folder(Path(state_dir), folder)
    print(__doc__.split("Usage: ", 1)[1].strip(), file=sys.stderr)
    return USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
