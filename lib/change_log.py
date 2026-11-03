"""Change log primitives (design: docs/design/sync.md, "Changes, not files" and "Ids").

A store is a journal of changes. This module is what every change shares: the envelope's
canonical bytes and its content-addressed id, a hybrid logical clock (Kulkarni et al.) so that
changes from different machines get one global order, that order itself, and the short id
suffixes minted for new items.

Pure functions and small value types: no file access. The journal that stores changes is
elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


ENVELOPE_VERSION = 1

Change = dict[str, Any]

CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"  # pragma: allowlist secret (the base32 alphabet)
SUFFIX_LENGTH = 6
MAX_COUNTER = 9999
_CLOCK = re.compile(r"(\d{13})\.(\d{4})")


class ChangeLogError(Exception):
    pass


@dataclass(frozen=True)
class Edit:
    """A change before the journal gives it an envelope."""

    op: str
    target: str
    body: dict[str, Any]
    local: bool


@dataclass(frozen=True)
class Conflict:
    """A change the rebuild kept but did not apply. `kind` is one of "state", "link", "field",
    "deleted"."""

    change: str
    target: str
    kind: str
    detail: str


def _refuse_floats(value: Any) -> None:
    if isinstance(value, float):
        raise ChangeLogError(f"a change holds a float ({value!r}); use integers or strings")
    if isinstance(value, Mapping):
        for item in value.values():
            _refuse_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _refuse_floats(item)


def canonical(record: Mapping[str, Any]) -> bytes:
    """The bytes a record is hashed and stored as: sorted keys, no spaces, UTF-8, no floats."""
    _refuse_floats(record)
    text = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def change_id(record: Mapping[str, Any]) -> str:
    """SHA-256 (hex) of the canonical record without its own `id`."""
    body = {key: value for key, value in record.items() if key != "id"}
    return hashlib.sha256(canonical(body)).hexdigest()


def clock_text(ms: int, counter: int) -> str:
    return f"{ms:013d}.{counter:04d}"


def parse_clock(text: str) -> tuple[int, int]:
    match = _CLOCK.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise ChangeLogError(f"not a clock value: {text!r}")
    return int(match.group(1)), int(match.group(2))


def _carry(ms: int, counter: int) -> str:
    if counter > MAX_COUNTER:
        return clock_text(ms + 1, 0)
    return clock_text(ms, counter)


def tick(last: str, now_ms: int) -> str:
    """The clock for a change made here: wall time if it moved on, else the counter."""
    last_ms, last_counter = parse_clock(last)
    if now_ms > last_ms:
        return clock_text(now_ms, 0)
    return _carry(last_ms, last_counter + 1)


def observe(last: str, seen: str, now_ms: int, max_skew_ms: int) -> tuple[str, bool]:
    """The clock after seeing another machine's `seen`, and whether `seen` was more than
    `max_skew_ms` ahead of wall time, in which case it is not adopted."""
    seen_ms, seen_counter = parse_clock(seen)
    if seen_ms - now_ms > max_skew_ms:
        return tick(last, now_ms), True
    last_ms, last_counter = parse_clock(last)
    top = max(last_ms, seen_ms, now_ms)
    if top == last_ms == seen_ms:
        counter = max(last_counter, seen_counter) + 1
    elif top == last_ms:
        counter = last_counter + 1
    elif top == seen_ms:
        counter = seen_counter + 1
    else:
        counter = 0
    return _carry(top, counter), False


def order_key(change: Mapping[str, Any]) -> tuple[str, str, str]:
    return (change["hlc"], change["replica"], change["id"])


def in_order(changes: Iterable[Change]) -> list[Change]:
    """The changes in the one global order, each id once."""
    unique: dict[str, Change] = {}
    for change in changes:
        unique.setdefault(change["id"], change)
    return sorted(unique.values(), key=order_key)


def mint_suffix(replica: str, seed: str, n: int = 0) -> str:
    """Six Crockford base32 characters (30 bits) from a hash of the replica, a seed and a
    counter: stable for the same arguments."""
    digest = hashlib.sha256(f"{replica}|{seed}|{n}".encode()).digest()
    bits = int.from_bytes(digest[:4], "big") >> 2
    return "".join(CROCKFORD[(bits >> shift) & 31] for shift in range(25, -1, -5))
