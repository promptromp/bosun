"""The check that the commit hook's test selection keeps up with the tests (a pytest plugin that
conftest.py registers): a test that reads a file of this repository, or runs one as a subprocess,
fails unless a change to that file would select it (impacted_rules.selected_by). pytest-impacted
sees only Python imports; this sees what a test file opens and starts, through an audit hook, from
its collection to its last test, so that a test that runs bosun's programs or reads the docs cannot
be left out of the selection unnoticed.

What it cannot see: the files the subprocesses open. Those are bosun's own programs, and what they
read at run time is under a rule that runs every test, or that maps to bin/bosun."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from pathlib import PurePosixPath
from types import FrameType
from typing import Any

import impacted_rules as rules
import pytest


ROOT = str(rules.ROOT)
RUNNING: list[str] = []  # the test file being collected or run, relative to the repository
TOUCHED: dict[str, set[str]] = {}  # test file -> the files it read or ran
PROGRAMS = "bin/bosun"  # a change to any of bosun's programs counts as one to it (impacted_map)
REPO_PATH = re.compile(re.escape(ROOT) + r"/([^\s'\";:|&)]+)")
SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
# A shell script that runs bosun's programs without naming them in the repository: `bosun ledger`
# through a PATH holding a link to bin/bosun, or the shell files of a copy of the tree.
RUNS_PROGRAMS = re.compile(r"(?:^|[\s;&|(`])bosun\s|lib/[\w/.-]+\.sh\b|/libexec/bosun-|/bin/bosun\b")
COMMENT = re.compile(r"(?m)(?:^|\s)#.*$")
# Who opens a module's source without the test asking for it: imports, tracebacks, pytest.
INCIDENTAL = ("<frozen importlib", "/linecache.py", "/tokenize.py", "/_pytest/")
FILES = rules.repository_files()  # listed now: the audit hook must not start git itself


def relative(path: str) -> str | None:
    """<path> relative to the repository, if it is one of the files git lists there."""
    absolute = os.path.normpath(os.path.join(os.getcwd(), path))
    if not absolute.startswith(ROOT + os.sep):
        return None
    name = absolute[len(ROOT) + 1 :]
    return name if name in FILES else None


def incidental() -> bool:
    frame: FrameType | None = sys._getframe(2)
    while frame is not None:
        if any(part in frame.f_code.co_filename for part in INCIDENTAL):
            return True
        frame = frame.f_back
    return False


def runs_programs(parts: list[str]) -> bool:
    """Whether a command line runs bosun's programs: bin/bosun (by name, or a path to this one),
    or a shell script that does."""
    program = parts[0]
    name = os.path.basename(program)
    if name == "bosun":
        return program == "bosun" or os.path.realpath(program) == f"{ROOT}/bin/bosun"
    if name in SHELLS and "-c" in parts[1:3]:
        script = parts[parts.index("-c") + 1] if parts.index("-c") + 1 < len(parts) else ""
        return bool(RUNS_PROGRAMS.search(COMMENT.sub("", script)))
    return False


def started(argv: Any) -> set[str]:
    """The repository's files a command line names, and bin/bosun for one that runs bosun's programs."""
    items = argv if isinstance(argv, (list, tuple)) else [argv]
    parts = [os.fsdecode(part) if isinstance(part, (bytes, os.PathLike)) else str(part) for part in items]
    line = " ".join(parts)
    named = {found for match in REPO_PATH.finditer(line) if (found := relative(f"{ROOT}/{match.group(1)}"))}
    if parts and runs_programs(parts):
        named.add(PROGRAMS)
    return named


def audit(event: str, args: tuple[Any, ...]) -> None:
    if not RUNNING:
        return
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(args[0])
        name = relative(path) if ROOT in path or not os.path.isabs(path) else None
        if name and not (name.endswith(".py") and incidental()):
            TOUCHED.setdefault(RUNNING[0], set()).add(name)
    elif event == "subprocess.Popen":
        TOUCHED.setdefault(RUNNING[0], set()).update(started(args[1]))


sys.addaudithook(audit)


def in_repository(path: object) -> str:
    return os.path.relpath(str(path), ROOT)


def pytest_collectstart(collector: pytest.Collector) -> None:
    if isinstance(collector, pytest.Module):
        RUNNING[:] = [in_repository(collector.path)]


def pytest_collectreport(report: pytest.CollectReport) -> None:
    RUNNING.clear()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    RUNNING[:] = [in_repository(item.path)]


def hint(name: str, test: str) -> str:
    if name == PROGRAMS or rules.matches(name, "lib/*.py"):
        return f'"{PROGRAMS}={test}"'
    if PurePosixPath(name).parts[0] == "docs" or name == "mkdocs.yml":
        return f'"mkdocs.yml={test}"'
    return f'"{name}={test}", or a glob in impacted_invalidate_all'


@pytest.fixture(autouse=True)
def selection_guard(request: pytest.FixtureRequest) -> Iterator[None]:
    yield
    RUNNING.clear()  # what the check itself reads is not the test's
    test = in_repository(request.node.path)
    unselected = sorted(name for name in TOUCHED.pop(test, set()) if not rules.covers(test, name))
    if unselected:
        lines = "\n".join(f"  {name}: add {hint(name, test)}" for name in unselected)
        pytest.fail(
            f"{test} reads or runs files whose change would not select it in the commit hook's run "
            f"(pytest-impacted; pyproject.toml's impacted_map):\n{lines}",
            pytrace=False,
        )
