"""Files of the knowledge base under $BOSUN_CONFIG_DIR/knowledge/ (design:
docs/design/knowledge.md): reading and appending JSON lines under a lock, atomic rewrites, and the
optional local git history (one commit per change).

- entities.jsonl: one entity per line, rewritten atomically when an entity changes.
- facts.jsonl: append-only log of fact records ({"op": "add"} and {"op": "update"}); only a
  delete on request (`kg forget`) or the retention purge rewrites it.
- identifiers.jsonl: Slack, tracker and code-host ids of people (restricted: mode 0600).
- forgotten.jsonl: what was deleted on request or by retention (hashed ids and counts, no content).
- batch.json: the last review batch `kg batch` printed, so `kg batch apply` refers to its numbers.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


Record = dict[str, Any]
PRIVATE_MODE = 0o600
GIT_IGNORE = "knowledge.lock\n*.tmp\nbatch.json\n"
GIT_IDENTITY = (
    "-c",
    "user.name=bosun",
    "-c",
    "user.email=bosun@localhost",
    "-c",
    "commit.gpgsign=false",
)
GIT_TIMEOUT_SECONDS = 30


class StoreError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def entities(self) -> Path:
        return self.root / "entities.jsonl"

    @property
    def facts(self) -> Path:
        return self.root / "facts.jsonl"

    @property
    def identifiers(self) -> Path:
        return self.root / "identifiers.jsonl"

    @property
    def forgotten(self) -> Path:
        return self.root / "forgotten.jsonl"

    @property
    def batch(self) -> Path:
        return self.root / "batch.json"

    @property
    def schema(self) -> Path:
        return self.root / "schema.json"

    @property
    def lock(self) -> Path:
        return self.root / "knowledge.lock"


def read_jsonl(path: Path) -> list[Record]:
    """The records of a JSON-lines file ([] if it does not exist); a bad line is an error that
    names it, never silently skipped."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    records = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StoreError(f"{path}:{number} is not valid JSON ({exc}); fix or remove that line") from exc
        if not isinstance(record, dict):
            raise StoreError(f"{path}:{number} is not a JSON object")
        records.append(record)
    return records


def jsonl(records: list[Record]) -> str:
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in records)


def append_records(path: Path, records: list[Record], mode: int | None = None) -> None:
    """Append whole lines in one write and flush them to disk (callers hold the lock)."""
    if not records:
        return
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, mode if mode is not None else 0o644)
    try:
        os.write(fd, jsonl(records).encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomically(path: Path, text: str, mode: int | None = None) -> None:
    """Write a temporary file, flush it to disk, then rename it over <path>."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        tmp.chmod(mode)
    tmp.replace(path)


@contextlib.contextmanager
def locked(paths: Paths, write: bool) -> Iterator[None]:
    """Hold the knowledge base's lock (shared to read, exclusive to write). Reading a knowledge
    base that does not exist yet needs no lock and creates nothing."""
    if not write and not paths.root.is_dir():
        yield
        return
    paths.root.mkdir(parents=True, exist_ok=True)
    with paths.lock.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        yield


# ---- git history --------------------------------------------------------------------------------


def git_environment(root: Path, *, pinned: bool = True) -> dict[str, str]:
    """The environment for git: no inherited GIT_* variables (run from a hook or another
    repository's tooling, GIT_DIR and GIT_INDEX_FILE would send every write to that repository),
    and, once the repository exists, its own .git and work tree pinned explicitly, so a knowledge
    directory inside another checkout never writes there either."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    if pinned:
        env.update(GIT_DIR=str(root / ".git"), GIT_WORK_TREE=str(root))
    return env


def git(root: Path, *args: str, pinned: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *GIT_IDENTITY, "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=GIT_TIMEOUT_SECONDS,
        env=git_environment(root, pinned=pinned),
    )


def git_available() -> bool:
    return shutil.which("git") is not None


def ensure_repository(root: Path) -> bool:
    """Whether <root> has its own repository (created if missing)."""
    if not (root / ".git").is_dir():
        git(root, "init", "-q", str(root), pinned=False)
    ignore = root / ".gitignore"
    if not ignore.exists():
        ignore.write_text(GIT_IGNORE)
    return (root / ".git").is_dir()


def commit(root: Path, message: str) -> str:
    """Commit every change in the knowledge directory; returns a warning, or "" (also when git is
    not installed: history is optional)."""
    if not git_available():
        return ""
    if not ensure_repository(root):
        return f"warning: could not create a git repository in {root}; no history recorded"
    git(root, "add", "-A")
    if not git(root, "status", "--porcelain").stdout.strip():
        return ""
    result = git(root, "commit", "-q", "--no-verify", "-m", message)
    return "" if result.returncode == 0 else f"warning: git commit failed: {result.stderr.strip()}"


def restart_history(root: Path, message: str) -> str:
    """Drop the git history and start a new one with the current files: after a delete on request
    the old versions must not survive in earlier commits."""
    if not git_available():
        return ""
    shutil.rmtree(root / ".git", ignore_errors=True)
    return commit(root, message)
