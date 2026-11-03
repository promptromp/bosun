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
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple


ENVELOPE_VERSION = 1

Change = dict[str, Any]
Place = tuple[str, ...]  # where a value sits in a record: its keys, and list positions as text

CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"  # pragma: allowlist secret (the base32 alphabet)
SUFFIX_LENGTH = 6
MAX_COUNTER = 9999
_CLOCK = re.compile(r"(\d{13})\.(\d{4})", re.ASCII)
# A lone surrogate: what Python makes of bytes that are not UTF-8 (argv, surrogateescape). A str
# never holds a valid pair as two code points, so each one is lone and has no UTF-8 bytes.
_SURROGATE = re.compile("[\ud800-\udfff]")
# What holds nothing further: most of what a record holds, so checked first.
_SCALARS = (str, int, bool, type(None))
_SHOWN = 60  # characters of a value an error shows


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
    "deleted", "missing" (a change for an item never made), "unknown" (an op or a body this
    version cannot apply: kept, not dropped, for a newer version to apply)."""

    change: str
    target: str
    kind: str
    detail: str


def _a_float(value: Any) -> float | None:
    """A float anywhere in <value> (in mappings, lists and tuples), or None. Iterative, and by
    exact type first: every change and every write's digest goes through it."""
    stack = [value]
    while stack:
        item = stack.pop()
        kind = type(item)
        if kind in _SCALARS:
            continue
        if kind is dict or isinstance(item, Mapping):
            stack.extend(item.values())
        elif kind is list or kind is tuple:
            stack.extend(item)
        elif isinstance(item, float):
            return item
    return None


def _shown(text: str) -> str:
    return repr(text if len(text) <= _SHOWN else f"{text[: _SHOWN - 3]}...")


def canonical(record: Mapping[str, Any]) -> bytes:
    """The bytes a record is hashed and stored as: sorted keys, no spaces, UTF-8. A float, or text
    that is not valid Unicode, has none (`unencodable` says where they are)."""
    found = _a_float(record)
    if found is not None:
        raise ChangeLogError(f"a change holds a float ({found!r}); use integers or strings")
    text = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        around = text[max(0, exc.start - _SHOWN // 2) : exc.end + _SHOWN // 2]
        raise ChangeLogError(f"a change holds text that is not valid Unicode (near {around!r})") from exc


class Unencodable(NamedTuple):
    """A value canonical bytes cannot hold: where it is, what it is, and what encodable() makes
    of it."""

    place: Place
    what: str
    made: Any


def unencodable(value: Any, place: Place = ()) -> Iterator[Unencodable]:
    """Each value in <value> that canonical bytes cannot hold, in order: a float, or text (a value
    or a key) with a lone surrogate."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and _SURROGATE.search(key):
                made = encodable(key)
                yield Unencodable((*place, key), f"a key that is not valid Unicode ({_shown(key)})", made)
            yield from unencodable(item, (*place, str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from unencodable(item, (*place, str(index)))
    elif isinstance(value, float):
        yield Unencodable(place, f"a float ({value!r})", encodable(value))
    elif isinstance(value, str) and _SURROGATE.search(value):
        yield Unencodable(place, f"text that is not valid Unicode ({_shown(value)})", encodable(value))


def encodable(value: Any) -> Any:
    """A copy of <value> that canonical bytes can hold: each float as its text ("2.5"), each lone
    surrogate (in a value or a key) as U+FFFD."""
    if isinstance(value, Mapping):
        return {_SURROGATE.sub("\ufffd", str(key)): encodable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encodable(item) for item in value]
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _SURROGATE.sub("\ufffd", value)
    return value


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
