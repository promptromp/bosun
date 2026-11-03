"""The journal: a store's changes on disk (design: docs/design/sync.md, "The journal is the commit
point").

Each machine (replica) appends to its own `<replica>.jsonl`; changes that never leave the machine
go to `local.jsonl`. Every line is one change in canonical bytes. Files are only appended to, in
clock order, so the last line of a file holds its highest `seq` and `hlc`: a transaction starts by
reading those and nothing else.

The caller holds the store's lock (the ledger's flock, the knowledge base's lock); nothing here
locks. The pure side (ids, clocks, order) is `lib/change_log.py`.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from change_log import (
    ENVELOPE_VERSION,
    Change,
    canonical,
    change_id,
    clock_text,
    in_order,
    mint_suffix,
    tick,
)


REPLICA_FILE = "replica.json"
LOCAL = "local"
SUFFIX = ".jsonl"
_TAIL = 8192


class JournalError(Exception):
    pass


def wall_ms() -> int:
    return time.time_ns() // 1_000_000


def replica_id(state_dir: Path) -> str:
    """This machine's id (32 hex characters), made once and kept in `replica.json`."""
    path = state_dir / REPLICA_FILE
    try:
        found = json.loads(path.read_text(encoding="utf-8"))["replica"]
        if isinstance(found, str) and found:
            return found
    except (OSError, ValueError, KeyError, TypeError):
        pass
    state_dir.mkdir(parents=True, exist_ok=True)
    made = secrets.token_hex(16)
    tmp = state_dir / f".{REPLICA_FILE}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"replica": made}) + "\n")
    os.replace(tmp, path)
    return made


def _parse(line: bytes, path: Path, number: int) -> Change:
    try:
        record = json.loads(line)
    except ValueError as exc:
        raise JournalError(f"{path.name}, line {number}: not a change ({exc})") from exc
    if not isinstance(record, dict):
        raise JournalError(f"{path.name}, line {number}: not a change")
    return record


def _last_line(path: Path) -> bytes:
    """The last non-empty line of a file, read from its end."""
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        want = _TAIL
        while True:
            start = max(0, size - want)
            handle.seek(start)
            lines = handle.read(size - start).rstrip(b"\n").split(b"\n")
            if start == 0 or len(lines) > 1:
                return lines[-1]
            want *= 2


def _last_change(path: Path) -> Change | None:
    line = _last_line(path)
    if not line:
        return None
    try:
        return _parse(line, path, 0)
    except JournalError:
        count = path.read_bytes().rstrip(b"\n").count(b"\n") + 1
        return _parse(line, path, count)


def _read_all(path: Path) -> list[Change]:
    found = []
    with path.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                found.append(_parse(line, path, number))
    return found


@dataclass
class Journal:
    root: Path
    stream: str
    replica: str
    rules: int
    now_ms: Callable[[], int] = wall_ms

    def _files(self) -> list[Path]:
        return sorted(self.root.glob(f"*{SUFFIX}")) if self.root.is_dir() else []

    def changes(self) -> list[Change]:
        """Every change in every file, in the one global order."""
        return in_order(c for path in self._files() for c in _read_all(path))

    def _last_changes(self) -> dict[str, Change]:
        found = {}
        for path in self._files():
            last = _last_change(path)
            if last is not None:
                found[path.stem] = last
        return found

    def position(self) -> dict[str, int]:
        """The highest `seq` of each file, by file stem (replica ids and "local")."""
        return {stem: change["seq"] for stem, change in self._last_changes().items()}

    def begin(self, cmd: str) -> Transaction:
        last = self._last_changes()
        clock = max((c["hlc"] for c in last.values()), default=clock_text(0, 0))
        seqs = {stem: change["seq"] for stem, change in last.items()}
        return Transaction(self, cmd, clock, seqs)


@dataclass
class Transaction:
    journal: Journal
    cmd: str
    last: str
    seqs: dict[str, int]
    edits: list[tuple[str, str, dict[str, Any], bool]] = field(default_factory=list)
    n: int = 0

    def add(self, op: str, target: str, body: dict[str, Any], *, local: bool = False) -> None:
        self.edits.append((op, target, body, local))

    def mint(self) -> str:
        private = self.seqs.get(self.journal.replica, 0) + 1
        local = self.seqs.get(LOCAL, 0) + 1
        suffix = mint_suffix(self.journal.replica, f"{private}/{local}", self.n)
        self.n += 1
        return suffix

    def _envelope(self, edit: tuple[str, str, dict[str, Any], bool], hlc: str, tx: str, seq: int) -> Change:
        op, target, body, local = edit
        journal = self.journal
        change: Change = {
            "v": ENVELOPE_VERSION,
            "stream": journal.stream,
            "replica": journal.replica,
            "seq": seq,
            "hlc": hlc,
            "tx": tx,
            "cmd": self.cmd,
            "rules": journal.rules,
            "op": op,
            "target": target,
            "body": body,
        }
        if local:
            change["local"] = True
        change["id"] = change_id(change)
        return change

    def _build(self) -> tuple[list[Change], dict[str, list[Change]]]:
        """Each change with its own clock, in the order added, and the changes by file."""
        journal = self.journal
        tx = tick(self.last, journal.now_ms())
        hlc = tx
        done: list[Change] = []
        files: dict[str, list[Change]] = {}
        seqs = dict(self.seqs)
        for index, edit in enumerate(self.edits):
            if index:
                hlc = tick(hlc, journal.now_ms())
            stem = LOCAL if edit[3] else journal.replica
            seqs[stem] = seqs.get(stem, 0) + 1
            change = self._envelope(edit, hlc, tx, seqs[stem])
            done.append(change)
            files.setdefault(stem, []).append(change)
        return done, files

    def commit(self) -> list[Change]:
        if not self.edits:
            return []
        done, files = self._build()
        self.journal.root.mkdir(parents=True, exist_ok=True)
        for stem, changes in files.items():
            data = b"".join(canonical(c) + b"\n" for c in changes)
            fd = os.open(self.journal.root / f"{stem}{SUFFIX}", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
        self.edits = []
        return done
