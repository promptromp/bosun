"""Files of the knowledge base under $BOSUN_CONFIG_DIR/knowledge/ (design:
docs/design/knowledge.md): reading and appending JSON lines under a lock, atomic rewrites, and the
optional local git history (one commit per change).

- journal/: every change, one transaction per write (lib/journal.py, docs/design/sync.md): this
  Mac's changes in <replica>.jsonl, those naming a person or holding my words in local.jsonl,
  which never leaves it. A write appends there first, then writes the files below. What a write
  cut short left there (<file>.torn-<time>) is ignored by the history.
- view.json: the journal position the files reflect and a digest of what each data file holds.
- entities.jsonl: one entity per line, rewritten atomically when an entity changes.
- facts.jsonl: append-only log of fact records ({"op": "add"} and {"op": "update"}); only a
  delete on request (`kg forget`) or the retention purge rewrites it.
- identifiers.jsonl: Slack, tracker and code-host ids of people (restricted: mode 0600).
- forgotten.jsonl: what was deleted on request or by retention (hashed ids and counts, no content).
- secret.key: the key of the forgotten hashes (mode 0600, ignored by the history, never leaves this Mac).
- batch.json: the last review batch `kg batch` printed, so `kg batch apply` refers to its numbers.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from journal import TORN


Record = dict[str, Any]
PRIVATE_MODE = 0o600
KEY_BYTES = 32
KEY_FILE = "secret.key"
# What the history never holds: the lock, temporary files, the last batch, the forgotten hashes'
# key, and what a write cut short left in the journal (a torn tail can name someone kg forget
# deletes; forget deletes the local ones).
GIT_IGNORE = f"knowledge.lock\n*.tmp\nbatch.json\n{KEY_FILE}\n*{TORN}*\n"
GIT_IDENTITY = (
    "-c",
    "user.name=bosun",
    "-c",
    "user.email=bosun@localhost",
    "-c",
    "commit.gpgsign=false",
)
GIT_TIMEOUT_SECONDS = 30
# A git index lock older than this, found under the knowledge base's exclusive lock, was left by a git
# command cut short (git holds it for one command, and bosun commits only under that lock).
STALE_GIT_LOCK_SECONDS = 30


class StoreError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def journal(self) -> Path:
        return self.root / "journal"

    @property
    def view(self) -> Path:
        return self.root / "view.json"

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
    def secret(self) -> Path:
        return self.root / KEY_FILE

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


def read_secret(path: Path) -> bytes:
    """The key as bytes; empty when the file is missing, unreadable or not a key."""
    try:
        key = bytes.fromhex(path.read_text().strip())
    except (OSError, ValueError):
        return b""
    return key if len(key) == KEY_BYTES else b""


def create_secret(path: Path) -> bytes:
    """Make the key (32 random bytes as hex, mode 0600); never replaces a file that is there."""
    key = secrets.token_bytes(KEY_BYTES)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_MODE)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(key.hex() + "\n")
    return key


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
    else:
        text = ignore.read_text()
        have = text.splitlines()
        wanted = [line for line in GIT_IGNORE.splitlines() if line not in have]  # one made before them
        if wanted:
            lead = "" if text.endswith("\n") or not text else "\n"
            with ignore.open("a") as handle:
                handle.write(lead + "".join(f"{line}\n" for line in wanted))
    return (root / ".git").is_dir()


def clear_stale_git_lock(root: Path) -> None:
    """Remove the history's index lock when a git command cut short left it (a power cut during a
    write's commit), with one note on stderr; else every later commit fails and the history stops.
    The caller holds the knowledge base's exclusive lock, under which alone bosun commits, and git
    holds its lock for one command: so one older than STALE_GIT_LOCK_SECONDS is left over. A
    younger one is left alone (the commit then fails, with a warning)."""
    lock = root / ".git" / "index.lock"
    try:
        age = time.time() - lock.stat().st_mtime
    except FileNotFoundError:
        return
    if age <= STALE_GIT_LOCK_SECONDS:
        return
    lock.unlink(missing_ok=True)
    print(
        f"kg: removed {lock}, which a git command cut short left {int(age)} s ago; the knowledge "
        "history records again",
        file=sys.stderr,
    )


def commit(root: Path, message: str) -> str:
    """Commit every change in the knowledge directory, under the knowledge base's exclusive lock
    (clear_stale_git_lock relies on it); returns a warning, or "" (also when git is not installed:
    history is optional)."""
    if not git_available():
        return ""
    if not ensure_repository(root):
        return f"warning: could not create a git repository in {root}; no history recorded"
    clear_stale_git_lock(root)
    git(root, "add", "-A")
    if not git(root, "status", "--porcelain").stdout.strip():
        return ""
    result = git(root, "commit", "-q", "--no-verify", "-m", message)
    return "" if result.returncode == 0 else f"warning: git commit failed: {result.stderr.strip()}"


def drop_history(root: Path) -> None:
    """Delete the git history: after a delete on request the old versions must not survive in
    earlier commits. The next commit starts a new one with the files as they are then (with the
    history off, a history kept here before is deleted all the same)."""
    shutil.rmtree(root / ".git", ignore_errors=True)
