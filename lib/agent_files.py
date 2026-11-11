"""What reading an agent's own files takes, whichever agent: shared by its adapters, lib/claude_state.py
and lib/codex_state.py, so that neither depends on the other. Design: docs/design/harnesses.md."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple


NOT_FOUND = 3
USAGE = 2
TAIL_CHUNK_BYTES = 1 << 16

Command = tuple[int | None, Callable[[list[str]], int]]  # how many arguments (None: any), and what runs


class LaunchSettings(NamedTuple):
    """How a running agent was launched, from its command line. None: not set at launch."""

    model: str | None
    effort: str | None
    by_bosun: bool


def never_asks(_capture: str) -> bool:
    """For an agent whose questions never wait on its screen: it is not asking one."""
    return False


def never_rejects(_capture: str) -> list[str]:
    """For an agent that never takes a pasted prompt for an unknown command: no replies say so."""
    return []


@dataclass(frozen=True)
class Screen:
    """How an agent's window is read from a capture of its pane (pure; lib/screen_text.py has the helpers)."""

    typed: Callable[[list[str]], str | None]  # the text in its input box from a capture's lines; None: no box
    question: Callable[[str], bool]  # whether it is asking something that Enter would answer
    rejected: Callable[[str], list[str]]  # its replies saying a pasted prompt ran as an unknown command


@dataclass(frozen=True)
class TurnReport:
    """What an agent's report at the end of a turn says about the session that ended it."""

    session_id: str
    workdir: str
    prompts: int  # how many prompts the session has had, in all
    new: tuple[str, ...]  # the ones not seen in an earlier report


@dataclass(frozen=True)
class TurnEnd:
    """How bosun takes what an agent runs when a turn ends (`bosun hook turn-end --harness <agent>`)."""

    # (the window's conversation, the agent's JSON) -> its report, or why it is ignored
    report: Callable[[str, Mapping[str, Any]], TurnReport | str]
    # (conversation, session id, workdir, prompts seen) -> makes the name stand for the session
    remember: Callable[[str, str, str, int], None]
    # an event name `bosun hook` still takes for this agent's windows started before `turn-end --harness`
    # (their launch command outlives the bosun that wrote it), or "" for none
    legacy_event: str = ""


@dataclass(frozen=True)
class Profile:
    """What an agent is and can do, declared by its module so that shared code never names the agent."""

    bin_key: str  # the config key naming its executable
    flags_key: str  # the config key holding the flags its repo sessions launch with
    model_key: str  # the config key holding the model its repo sessions run
    names_sessions: bool  # whether a session can be given a name to resume it by
    statuses: frozenset[str]  # the window statuses it can show, of idle, busy, shell and waiting
    remote_control: bool  # whether it has Claude Code's remote control
    autocompact: bool  # whether it compacts a long conversation itself
    subagents: bool  # whether it can run subagents at a model and effort of their own
    state_files: tuple[str, ...]  # the files it keeps in bosun's state folder
    display_name: str  # how bosun names it to a person
    setup_preview: tuple[str, ...]  # the lines `bosun init`'s agent question shows for it
    screen: Screen  # how its window's input box and questions are read
    # the module that holds its windows to their lists of connectors (lib/connectors_<agent>.py)
    connectors: str
    setup_sessions: str = ""  # how `bosun init` says its repo sessions will run
    setup_note: str = ""  # what `bosun init` tells the person about running them on it
    turn_end: TurnEnd | None = None  # how it reports the end of a turn; None: it does not


def command_line(pid: int | str) -> str:
    result = subprocess.run(
        ["ps", "-ww", "-o", "command=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return result.stdout.strip()


def process_start(pid: int) -> float | None:
    """Start time of <pid> as a Unix timestamp, from ps."""
    result = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], check=False, capture_output=True, text=True
    )
    try:
        return time.mktime(time.strptime(result.stdout.strip(), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def read_object(path: Path) -> dict[str, Any] | None:
    """The JSON object in <path>, or None if it is missing, unreadable or not an object."""
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):  # ValueError: not JSON, or not text at all
        return None
    return value if isinstance(value, dict) else None


def write_atomically(path: Path, text: str, mode: int | None = None) -> None:
    """Replace <path> at once, so a reader never sees half a file. It keeps its permissions unless told."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", delete=False) as temp:
        temp.write(text)
    if mode is None and path.exists():
        mode = path.stat().st_mode & 0o777
    if mode is not None:
        os.chmod(temp.name, mode)
    os.replace(temp.name, path)


def reversed_lines(path: Path) -> Iterator[bytes]:
    """The lines of <path>, last first, read from the end in chunks (transcripts grow large)."""
    with path.open("rb") as fh:
        position = fh.seek(0, os.SEEK_END)
        rest = b""
        while position > 0:
            size = min(TAIL_CHUNK_BYTES, position)
            position -= size
            fh.seek(position)
            lines = (fh.read(size) + rest).split(b"\n")
            rest = lines.pop(0)
            yield from reversed(lines)
        yield rest


def emit(value: object) -> int:
    """Print <value> and exit 0, or exit NOT_FOUND if there is none."""
    if value is None:
        return NOT_FOUND
    print(value)
    return 0


def run_command(doc: str | None, commands: Mapping[str, Command], argv: list[str]) -> int:
    """Each adapter's command line: `-h`, its table of commands, and a usage error for anything else."""
    usage = (doc or "").strip()
    if argv[:1] in (["-h"], ["--help"]):
        print(usage)
        return 0
    command = commands.get(argv[0]) if argv else None
    if command is None or (command[0] is not None and len(argv) - 1 != command[0]):
        print(usage, file=sys.stderr)
        return USAGE
    return command[1](argv[1:])
