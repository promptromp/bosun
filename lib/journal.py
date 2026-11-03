"""The journal: a store's changes on disk (design: docs/design/sync.md, "The journal is the commit
point").

Each machine (replica) appends to its own `<replica>.jsonl`; changes that never leave the machine
go to `local.jsonl`. Every line is one change in canonical bytes. Files are only appended to, in
clock order, so the end of a file holds its highest `seq` and `hlc`: a transaction starts by
reading those and nothing else.

A transaction counts once it is whole. Each change carries `of`, how many lines its transaction
has in that change's file, and a line in `local.jsonl` also `private_of`, how many it has in
`<replica>.jsonl`; a commit writes `local.jsonl` first, then `<replica>.jsonl`. A line is only
whole with its newline. So a write cut short (a crash, a power cut, a signal between the files)
leaves an unfinished transaction at the end of a file: readers leave it out, as if it were not
there, and `repair_tail` moves it aside, kept, before the next write. Any other damage raises
`JournalError` where the line is read.

The caller holds the store's lock (the ledger's flock, the knowledge base's lock); nothing here
locks. The pure side (ids, clocks, order) is `lib/change_log.py`.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

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
TORN = ".torn-"  # <file>.torn-<UTC time>: an unfinished transaction, moved aside
_TAIL = 8192
# What reading a change relies on (its position, its place in the order, whether its transaction
# is whole): a line without them is not a change.
_ENVELOPE_KEYS: tuple[tuple[str, type], ...] = (
    ("seq", int),
    ("hlc", str),
    ("replica", str),
    ("id", str),
    ("tx", str),
    ("of", int),
)


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


def _is_change(record: Any) -> bool:
    if not isinstance(record, dict) or not all(
        isinstance(record.get(key), kind) for key, kind in _ENVELOPE_KEYS
    ):
        return False
    return record["of"] >= 1 and (not record.get("local") or isinstance(record.get("private_of"), int))


def _parse(line: bytes, path: Path, number: int) -> Change:
    try:
        record = json.loads(line)
    except ValueError as exc:
        raise JournalError(f"{path.name}, line {number}: not a change ({exc})") from exc
    if not _is_change(record):
        raise JournalError(f"{path.name}, line {number}: not a change")
    found: Change = record
    return found


def _parse_at(path: Path, offset: int, line: bytes) -> Change:
    """The change in <line>, which starts at byte <offset> of <path> (its line number is counted
    only for the error)."""
    try:
        return _parse(line, path, 0)
    except JournalError:
        with path.open("rb") as handle:
            number = handle.read(offset).count(b"\n") + 1
        return _parse(line, path, number)


def _whole(data: bytes) -> bytes:
    """<data> up to its last newline: a line is only whole with it."""
    return data[: data.rfind(b"\n") + 1]


def _read_all(path: Path) -> list[Change]:
    """Every whole line of <path>, in order (what follows the last newline is left out)."""
    lines = _whole(path.read_bytes()).split(b"\n")
    return [_parse(line, path, number) for number, line in enumerate(lines, 1) if line.strip()]


def _back(handle: BinaryIO, size: int, count: int) -> tuple[list[tuple[int, bytes]], int]:
    """The last <count> non-blank whole lines of an open file (fewer if it has fewer), each with
    the offset it starts at, and the offset where whole lines end."""
    want = _TAIL
    while True:
        start = max(0, size - want)
        handle.seek(start)
        data = _whole(handle.read(size - start))
        lines, offset = [], start
        for line in data.split(b"\n")[:-1]:
            lines.append((offset, line))
            offset += len(line) + 1
        whole = [(at, line) for at, line in lines[1 if start else 0 :] if line.strip()]
        if start == 0 or len(whole) >= count:
            return whole[-count:], start + len(data)
        want *= 2


@dataclass
class _End:
    """The end of a journal file: the whole lines its last transaction has there (`trailing`, in
    order; fewer than their `of` when it is unfinished), the line before them (`prior`, read only
    when it is needed), the offset they start at, and the offset where whole lines end (a line cut
    short may follow, up to `size`)."""

    path: Path
    trailing: list[Change] = field(default_factory=list)
    prior: tuple[int, bytes] | None = None
    start: int = 0
    cut: int = 0
    size: int = 0

    def last(self, whole: bool) -> Change | None:
        """The file's last change, its trailing transaction counted only if <whole>."""
        if self.trailing and whole:
            return self.trailing[-1]
        return _parse_at(self.path, *self.prior) if self.prior else None


def _end(path: Path) -> _End:
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        lines, cut = _back(handle, size, 1)
        if not lines:
            return _End(path, cut=cut, size=size)
        last = _parse_at(path, *lines[0])
        lines, _ = _back(handle, size, max(1, last["of"]) + 1)
    end = _End(path, start=lines[-1][0], cut=cut, size=size)
    for offset, line in reversed(lines):
        change = None if len(end.trailing) == last["of"] else _parse_at(path, offset, line)
        if change is None or change["tx"] != last["tx"]:
            end.prior = (offset, line)
            break
        end.trailing.insert(0, change)
        end.start = offset
    return end


def _private_part_there(local: Change, private: _End | None) -> bool:
    """Whether the replica's file holds the `private_of` lines of <local>'s transaction, which a
    commit writes after local.jsonl: they end it, or a later transaction does."""
    if private is None or not private.trailing:
        return False
    tx = private.trailing[-1]["tx"]
    if tx != local["tx"]:
        return bool(tx > local["tx"])
    return bool(len(private.trailing) == local["private_of"])


def _whole_end(stem: str, end: _End, ends: Mapping[str, _End]) -> bool:
    """Whether the transaction that ends a file is whole: all its `of` lines in that file, and for
    local.jsonl, its `private_of` lines in the replica's file."""
    if not end.trailing:
        return True
    last = end.trailing[-1]
    if len(end.trailing) < last["of"]:
        return False
    if stem != LOCAL or not last.get("private_of"):
        return True
    return _private_part_there(last, ends.get(last["replica"]))


def _set_aside(path: Path, end: int, stamp: str) -> Path:
    """Copy what follows byte <end> of <path> to a file of its own (never over another), then cut
    <path> at <end>."""
    with path.open("rb") as handle:
        handle.seek(end)
        torn = handle.read()
    aside, n = path.with_name(f"{path.name}{TORN}{stamp}"), 1
    while aside.exists():
        n += 1
        aside = path.with_name(f"{path.name}{TORN}{stamp}-{n}")
    fd = os.open(aside, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, torn)
        os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(path, os.O_WRONLY)
    try:
        os.ftruncate(fd, end)
        os.fsync(fd)
    finally:
        os.close(fd)
    return aside


@dataclass
class Journal:
    root: Path
    stream: str
    replica: str
    rules: int
    now_ms: Callable[[], int] = wall_ms

    def _files(self) -> list[Path]:
        return sorted(self.root.glob(f"*{SUFFIX}")) if self.root.is_dir() else []

    def _ends(self) -> dict[str, _End]:
        return {path.stem: _end(path) for path in self._files()}

    def changes(self) -> list[Change]:
        """Every change of a whole transaction in every file, in the one global order."""
        ends = self._ends()
        found: list[Change] = []
        for path in self._files():
            lines = _read_all(path)
            end = ends[path.stem]
            if not _whole_end(path.stem, end, ends):
                del lines[len(lines) - len(end.trailing) :]
            found += lines
        return in_order(found)

    def _last_changes(self) -> dict[str, Change]:
        """The last change of each file that belongs to a whole transaction."""
        ends = self._ends()
        found = {}
        for stem, end in ends.items():
            last = end.last(_whole_end(stem, end, ends))
            if last is not None:
                found[stem] = last
        return found

    def position(self) -> dict[str, int]:
        """The highest `seq` of each file's whole transactions, by file stem (replica ids and
        "local")."""
        return {stem: change["seq"] for stem, change in self._last_changes().items()}

    def written(self) -> dict[str, int]:
        """The highest `seq` of each file's whole lines, whether their transaction is whole or not."""
        return {stem: end.trailing[-1]["seq"] for stem, end in self._ends().items() if end.trailing}

    def repair_tail(self) -> list[Path]:
        """Before a write: an unfinished transaction at the end of a file (fewer lines than it says,
        a line without its newline, or local lines whose private lines never came) is moved aside
        to `<file>.torn-<UTC time>`, kept, and its file cut where it began, with a warning on
        stderr. Returns the files moved aside. Other damage stays, and raises where it is read."""
        ends = self._ends()
        stamp = datetime.fromtimestamp(self.now_ms() // 1000, UTC).strftime("%Y%m%dT%H%M%SZ")
        moved = []
        for path in self._files():
            end = ends[path.stem]
            at = end.cut if _whole_end(path.stem, end, ends) else end.start
            if at == end.size:
                continue
            aside = _set_aside(path, at, stamp)
            print(
                f"journal: {path} ended in a transaction a write left unfinished; moved it aside to "
                f"{aside.name}",
                file=sys.stderr,
            )
            moved.append(aside)
        return moved

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
    committed: bool = False
    _locals: int = 0  # how many of the edits are local, once commit counts them

    def _check_open(self) -> None:
        """A committed transaction's sequence numbers and clock are spent: begin a new one."""
        if self.committed:
            raise JournalError("this transaction is committed; begin a new one")

    def add(self, op: str, target: str, body: dict[str, Any], *, local: bool = False) -> None:
        self._check_open()
        self.edits.append((op, target, body, local))

    def mint(self) -> str:
        self._check_open()
        private = self.seqs.get(self.journal.replica, 0) + 1
        local = self.seqs.get(LOCAL, 0) + 1
        suffix = mint_suffix(self.journal.replica, f"{private}/{local}", self.n)
        self.n += 1
        return suffix

    def _envelope(self, edit: tuple[str, str, dict[str, Any], bool], hlc: str, tx: str, seq: int) -> Change:
        op, target, body, local = edit
        journal = self.journal
        locals_ = self._locals
        change: Change = {
            "v": ENVELOPE_VERSION,
            "stream": journal.stream,
            "replica": journal.replica,
            "seq": seq,
            "hlc": hlc,
            "tx": tx,
            "of": locals_ if local else len(self.edits) - locals_,
            "cmd": self.cmd,
            "rules": journal.rules,
            "op": op,
            "target": target,
            "body": body,
        }
        if local:
            change["local"] = True
            change["private_of"] = len(self.edits) - locals_
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
        self._locals = sum(1 for edit in self.edits if edit[3])
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
        """Append the transaction: local.jsonl first, then the replica's file, each with fsync, so
        a cut leaves local lines whose private lines are missing, which readers leave out."""
        self._check_open()
        self.committed = True
        if not self.edits:
            return []
        done, files = self._build()
        self.journal.root.mkdir(parents=True, exist_ok=True)
        for stem, changes in sorted(files.items(), key=lambda pair: pair[0] != LOCAL):
            data = b"".join(canonical(c) + b"\n" for c in changes)
            fd = os.open(self.journal.root / f"{stem}{SUFFIX}", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
        self.edits = []
        return done
