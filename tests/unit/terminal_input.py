"""A terminal as the input, for the tests of the commands bosun runs for an answer.

`get.sh` starts `bosun init` with its input on /dev/tty, so whatever init runs inherits a terminal
unless it is given another input. Claude Code runs on Bun, which cannot watch /dev/tty on macOS
(`error: EINVAL: invalid argument, kqueue`) and exits 1 before it answers: its login check then read as
"not logged in" (2026-11-14, the first real `curl | sh` with the real Claude Code). A command bosun asks
for an answer therefore gets no terminal: `stdin=subprocess.DEVNULL` in Python, `</dev/null` in sh.

The stand-ins here refuse a terminal as that does, and the guard finds a call that does not say
what its input is.
"""

from __future__ import annotations

import ast
import inspect
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType


# What a stand-in does first: as Claude Code on /dev/tty, it fails before it answers.
REFUSES_A_TERMINAL = '[ -t 0 ] && { echo "error: EINVAL: invalid argument, kqueue" >&2; exit 1; }'
SUBPROCESS_CALLS = frozenset({"run", "Popen", "call", "check_call", "check_output"})


@contextmanager
def a_terminal_as_input() -> Iterator[None]:
    """This process's input is a terminal (a pty) while the block runs, as init's is under get.sh."""
    primary, secondary = os.openpty()
    saved = os.dup(0)
    os.dup2(secondary, 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        for fd in (saved, secondary, primary):
            os.close(fd)


def stand_in(directory: Path, name: str, answer: str, status: int = 0) -> Path:
    """An executable <name> in <directory> that refuses a terminal as its input, else prints <answer>."""
    path = directory / name
    path.write_text(f"#!/bin/sh\n{REFUSES_A_TERMINAL}\ncat <<'ANSWER'\n{answer}\nANSWER\nexit {status}\n")
    path.chmod(0o755)
    return path


def calls_without_input(module: ModuleType) -> list[str]:
    """The subprocess calls in <module> that do not say what their input is (no `stdin=`), as
    `<function>:<line>`."""
    tree = ast.parse(inspect.getsource(module))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        is_subprocess = (
            isinstance(owner, ast.Name) and owner.id == "subprocess" and node.func.attr in SUBPROCESS_CALLS
        )
        if is_subprocess and not any(keyword.arg == "stdin" for keyword in node.keywords):
            found.append(f"{node.func.attr}:{node.lineno}")
    return found
