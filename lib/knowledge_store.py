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
- secret.key: the key of the forgotten hashes (mode 0600, ignored by the history, never leaves this Mac);
  a file there that was not a key, and that no keyed record needed, kept as secret.key.aside-<time>.
- batch.json: the last review batch `kg batch` printed, so `kg batch apply` refers to its numbers.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orphans
from journal import TORN, JournalError, create_new, flush_to_disk, replace_durably, write_all


Record = dict[str, Any]
PRIVATE_MODE = 0o600
KEY_BYTES = 32
KEY_FILE = "secret.key"
KEY_ASIDE = f"{KEY_FILE}.aside-"  # <KEY_FILE>.aside-<UTC time>: a key file that was not a key, kept
# What the history never holds: the lock, temporary files, the last batch, the forgotten hashes'
# key (and a file kept aside that was meant to be it), and what a write cut short left in the
# journal (a torn tail can name someone kg forget deletes; forget deletes the local ones).
GIT_IGNORE = f"knowledge.lock\n*.tmp\nbatch.json\n{KEY_FILE}\n{KEY_ASIDE}*\n*{TORN}*\n"
# Also: no git process stays in the history after a command returns, as a write that deletes it
# (kg forget) would race it, and a repack could make .git again after the delete, a folder that is
# no repository. So git's housekeeping after a commit (a repack, once it counts enough loose
# objects) runs inside the commit, not detached (maintenance.autoDetach; and gc.autoDetach, its
# fallback and older gits' setting); a core.fsmonitor=true in my own git config starts no
# file-watching daemon here; and no hook runs (core.hooksPath, which also covers a template's
# hooks; --no-verify skips only two): a hook could leave a process behind, and it sees the diffs,
# which name the people a forget deletes. The history is made without a template (init).
GIT_IDENTITY = (
    "-c",
    "user.name=bosun",
    "-c",
    "user.email=bosun@localhost",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "maintenance.autoDetach=false",
    "-c",
    "gc.autoDetach=false",
    "-c",
    "core.fsmonitor=false",
    "-c",
    f"core.hooksPath={os.devnull}",
)
GIT_TIMEOUT_SECONDS = 30
# How long the processes of a git command killed on its timeout may take to be gone.
GIT_TREE_GONE_SECONDS = 5
# What every git repository has in its folder; a .git without one of them is no repository.
GIT_ESSENTIALS = ("HEAD", "objects", "refs")
# A git lock older than this, found under the knowledge base's exclusive lock, was left by a git
# command cut short (git holds one for one command, and bosun commits only under that lock).
STALE_GIT_LOCK_SECONDS = 30


class StoreError(Exception):
    pass


class HistoryProblem(Exception):
    """Why a commit to the history could not be made, said as a warning: the write is saved."""


class GitTimeout(HistoryProblem):
    """A git command that ran past GIT_TIMEOUT_SECONDS, killed with every process it started."""


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
    def key_file(self) -> Path:
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


def _parsed(line: str, path: Path, number: int) -> tuple[Record | None, str]:
    """The record on line <number> of <path>, or why it is not one."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        return None, f"{path}:{number} is not valid JSON ({exc})"
    if not isinstance(record, dict):
        return None, f"{path}:{number} is not a JSON object"
    return record, ""


def read_lines(path: Path) -> tuple[list[Record], str]:
    """The records of a JSON-lines file that parse ([] if it does not exist), and why the first
    line that does not parse is not a record ("" when every line is): what a write cut short left
    (a torn last line) is told, never silently skipped."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return [], ""
    records, problem = [], ""
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        record, why = _parsed(line, path, number)
        if record is None:
            problem = problem or why
        else:
            records.append(record)
    return records, problem


def read_jsonl(path: Path) -> list[Record]:
    """The records of a JSON-lines file ([] if it does not exist); a bad line is an error that
    names it (for a file that nothing else holds, such as identifiers.jsonl)."""
    records, problem = read_lines(path)
    if problem:
        raise StoreError(unreadable(problem))
    return records


def unreadable(problem: str) -> str:
    """A file that does not parse and that nothing can rebuild: what to do."""
    return f"{problem}; fix or remove that line"


def jsonl(records: list[Record]) -> str:
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in records)


def _ends_cut_short(fd: int) -> bool:
    """Whether the file open on <fd> ends in a line without its newline (a write cut short)."""
    size = os.fstat(fd).st_size
    return size > 0 and os.pread(fd, 1, size - 1) != b"\n"


def append_records(path: Path, records: list[Record], mode: int | None = None) -> None:
    """Append whole lines and flush them to the disk itself, as the journal does
    (journal.write_all: a short write is finished; journal.flush_to_disk); never onto a last line
    cut short before its newline, which would join two records (callers hold the lock)."""
    if not records:
        return
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, mode if mode is not None else 0o644)
    try:
        lead = b"\n" if _ends_cut_short(fd) else b""
        write_all(fd, lead + jsonl(records).encode())
        flush_to_disk(fd)
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
    """Make the key (32 random bytes as hex, mode 0600) as replica.json is made
    (journal.create_new: written whole and flushed, then linked to its name and the folder flushed),
    so that it is on the disk before anything hashed with it is journaled, and a power cut never
    leaves a key file that is not a key. Never replaces a file that is there (FileExistsError)."""
    key = secrets.token_bytes(KEY_BYTES)
    try:
        create_new(path, (key.hex() + "\n").encode(), PRIVATE_MODE)
    except JournalError as exc:
        raise StoreError(str(exc)) from exc
    return key


def set_key_aside(path: Path) -> Path:
    """<path>, a key file that is not a key, moved aside and kept (never over another file):
    <path>.aside-<UTC time>, which the history ignores (KEY_ASIDE)."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    aside, n = path.with_name(f"{KEY_ASIDE}{stamp}"), 1
    while os.path.lexists(aside):
        n += 1
        aside = path.with_name(f"{KEY_ASIDE}{stamp}-{n}")
    path.rename(aside)
    return aside


def write_atomically(path: Path, text: str, mode: int | None = None) -> None:
    """Write a temporary file, flush it to the disk itself (journal.flush_to_disk), then rename it
    over <path> and flush the folder (journal.replace_durably)."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as handle:
        handle.write(text)
        handle.flush()
        flush_to_disk(handle.fileno())
    if mode is not None:
        tmp.chmod(mode)
    replace_durably(tmp, path)


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
    """git in <root>, in the command's own process group, so that a Ctrl-C, a closed terminal or a
    stop sent to the command reaches git too. Past GIT_TIMEOUT_SECONDS, git and every process it
    started are killed (kill_tree; GitTimeout): the housekeeping a commit runs (maintenance, repack,
    pack-objects) is its children, which killing git alone would leave writing in .git."""
    command = ["git", *GIT_IDENTITY, "-C", str(root), *args]
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=git_environment(root, pinned=pinned),
    ) as process:
        try:
            out, err = process.communicate(timeout=GIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            left = kill_tree(process)
            still = (
                f"; {left} process(es) it started were still there {GIT_TREE_GONE_SECONDS} s later"
                if left
                else ""
            )
            raise GitTimeout(
                f"git {args[0]} ran past {GIT_TIMEOUT_SECONDS} s and was stopped{still}"
            ) from None
    return subprocess.CompletedProcess(command, process.returncode, out, err)


def process_tree(pid: int) -> set[int]:
    """<pid> and every process descending from it now (from `ps -A`: orphans.parse_ps); just <pid>
    where ps cannot be run."""
    try:
        procs = orphans.parse_ps(orphans.read_ps("-A"))
    except OSError:
        return {pid}
    children: dict[int, list[int]] = {}
    for proc in procs:
        children.setdefault(proc.ppid, []).append(proc.pid)
    found: set[int] = set()
    todo = [pid]
    while todo:
        current = todo.pop()
        if current not in found:
            found.add(current)
            todo += children.get(current, [])
    return found


def signal_all(pids: set[int], sig: signal.Signals) -> None:
    for pid in pids:
        with contextlib.suppress(OSError):  # gone (ESRCH), or no longer one we may signal (EPERM)
            os.kill(pid, sig)


def kill_tree(leader: subprocess.Popen[str]) -> int:
    """Kill <leader> and every process descending from it: each listed and stopped (SIGSTOP)
    until a listing finds no new one, so that none starts another unseen or is adopted by launchd
    before it is listed; then all killed, the leader reaped, and the others waited for (at most
    GIT_TREE_GONE_SECONDS; a process gone or no longer ours counts as gone). Returns how many
    were still there."""
    tree: set[int] = set()
    while found := process_tree(leader.pid) - tree:
        tree |= found
        signal_all(found, signal.SIGSTOP)
    signal_all(tree, signal.SIGKILL)
    leader.wait()
    left = tree - {leader.pid}
    deadline = time.monotonic() + GIT_TREE_GONE_SECONDS
    while left and time.monotonic() < deadline:
        left = {pid for pid in left if alive(pid)}
        time.sleep(0.01)
    return len(left)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:  # ESRCH: gone; EPERM: a zombie being reaped, or the pid someone else's now
        return False
    return True


def git_available() -> bool:
    return shutil.which("git") is not None


def ensure_repository(root: Path) -> bool:
    """Whether <root> has its own repository: made if missing, without a template (which could
    bring hooks and files from my git config), and made again where .git is debris
    (drop_broken_history). The caller holds the exclusive lock."""
    history = root / ".git"
    drop_broken_history(root)
    if not history.is_dir():
        git(root, "init", "-q", "--template=", str(root), pinned=False)
    ensure_ignored(root)
    return history.is_dir()


def drop_broken_history(root: Path) -> None:
    """A .git that is debris, not a repository: without one of what every repository has
    (GIT_ESSENTIALS; what a process an outside writer left made again after a history restart
    deleted it), deleted, with one line on stderr, so the next commit starts the history again. A
    .git that has them all stays as it is, whatever git says of it (a typo in my git config, a
    newer git's repository format, which this git does not know): a commit that fails then warns.
    Deleting goes through drop_history, which fails closed: what cannot be deleted is a
    HistoryProblem."""
    history = root / ".git"
    if not os.path.lexists(history) or all(os.path.lexists(history / name) for name in GIT_ESSENTIALS):
        return
    try:
        drop_history(root)
    except StoreError:
        raise HistoryProblem(
            f"{history} is no git repository and could not be deleted whole; the knowledge history "
            f"records nothing until it is gone (rm -rf {history})"
        ) from None
    print(
        f"kg: {history} was no git repository; deleted it, and the knowledge history starts again",
        file=sys.stderr,
    )


def ensure_ignored(root: Path) -> None:
    """.gitignore with every line of GIT_IGNORE (one made before some of them gains them)."""
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


def git_locks(git_dir: Path) -> list[Path]:
    """The locks a git command takes in <git_dir> while it runs: the index's, HEAD's and the other
    files' at its top (packed-refs, ORIG_HEAD), and each ref's under refs/."""
    return sorted([*git_dir.glob("*.lock"), *(git_dir / "refs").rglob("*.lock")])


def clear_stale_git_locks(root: Path) -> None:
    """Remove each lock of the history's that a git command cut short left (a power cut during a
    write's commit), with one note on stderr each; else every later commit fails and the history
    stops. The caller holds the knowledge base's exclusive lock, under which alone bosun commits,
    and git holds a lock for one command: so one older than STALE_GIT_LOCK_SECONDS is left over. A
    younger one is left alone (the commit may then fail, with a warning)."""
    for lock in git_locks(root / ".git"):
        try:
            age = time.time() - lock.stat().st_mtime
        except FileNotFoundError:
            continue
        if age <= STALE_GIT_LOCK_SECONDS:
            continue
        lock.unlink(missing_ok=True)
        print(
            f"kg: removed {lock}, which a git command cut short left {int(age)} s ago; the knowledge "
            "history records again",
            file=sys.stderr,
        )


def commit(root: Path, message: str) -> str:
    """Commit every change in the knowledge directory, under the knowledge base's exclusive lock
    (clear_stale_git_locks relies on it); returns a warning, or "" (also when git is not installed:
    history is optional). It never raises: it runs after the write is saved (view.json), so a git
    that fails or runs past its time (GitTimeout), a .git that cannot be made again
    (HistoryProblem) or a file it cannot write is that warning."""
    if not git_available():
        return ""
    try:
        return record(root, message)
    except (HistoryProblem, OSError) as exc:
        return f"warning: git commit failed: {exc}"


def record(root: Path, message: str) -> str:
    """commit's steps; a git that fails is never silent (a lock held now, a .git gone wrong)."""
    if not ensure_repository(root):
        return f"warning: could not create a git repository in {root}; no history recorded"
    clear_stale_git_locks(root)
    added = git(root, "add", "-A")
    status = git(root, "status", "--porcelain") if added.returncode == 0 else added
    if status.returncode != 0:
        return f"warning: git commit failed: {status.stderr.strip()}"
    if not status.stdout.strip():
        return ""
    result = git(root, "commit", "-q", "--no-verify", "-m", message)
    return "" if result.returncode == 0 else f"warning: git commit failed: {result.stderr.strip()}"


def remove_temporaries(root: Path) -> None:
    """Delete every temporary file in the knowledge folder (*.tmp: what a write cut short before its
    rename left, such as batch.json.tmp, which names people by their groups and titles). The caller
    holds the exclusive lock, under which alone they are written."""
    for path in root.glob("*.tmp"):
        path.unlink(missing_ok=True)


def drop_history(root: Path) -> None:
    """Delete the git history: after a delete on request the old versions must not survive in
    earlier commits. The next commit starts a new one with the files as they are then (with the
    history off, a history kept here before is deleted all the same). Fails closed: a history
    still there after the delete (a file in it that cannot be deleted) is a StoreError naming it,
    so that the write stops before view.json and the next write owes the restart again."""
    history = root / ".git"
    shutil.rmtree(history, ignore_errors=True)
    if os.path.lexists(history):
        raise StoreError(
            f"{history}, the knowledge history, could not be deleted, and it holds what this write "
            f"deletes; the write stopped there. Delete it (rm -rf {history}): fixing its permissions "
            "instead keeps what it holds. Once it is gone, the next write starts a new history"
        )
