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


def command_line(pid: int | str) -> str:
    result = subprocess.run(
        ["ps", "-ww", "-o", "command=", "-p", str(pid)], check=False, capture_output=True, text=True
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
    except (OSError, json.JSONDecodeError):
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
