"""Change log primitives (design: docs/design/sync.md, "Changes, not files" and "Ids").

A store is a journal of changes. This module is what every change shares: the envelope's
canonical bytes and its content-addressed id, a hybrid logical clock (Kulkarni et al.) so that
changes from different machines get one global order, that order itself, and the short id
suffixes minted for new items. Also what both stores (the ledger, the knowledge base) do alike at
their write boundary: refuse a change the journal cannot hold, naming where it is; convert, once,
data written before or outside the journal; the stamp a store's files record of the journal (the
position they reflect and a digest of what they hold), and whether they were changed outside it
since; what a list that only grows gained; the kinds of conflict a rebuild keeps; and the
placeholder a change deleted on request leaves in its place.

Pure functions and small value types: no file access. The journal that stores changes is
elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple


ENVELOPE_VERSION = 1

Change = dict[str, Any]
Place = tuple[str, ...]  # where a value sits in a record: its keys, and list positions as text

# Beside a store's journal position in its files: the key of the digest of what they hold.
DIGEST = "digest"
# The command of the transaction that puts in a store's journal what was changed in its files
# outside it (by hand, an older bosun), in both stores.
ADOPT = "adopt"
# A change deleted on request (`kg forget`, the retention purge) leaves a placeholder in its file:
# its place in the sequence, its clock and its transaction's counts, and nothing of what it held,
# so the sequence and the ids minted from it never go back and its transaction stays whole.
FORGOTTEN = "forgotten"
PLACEHOLDER_KEYS = ("v", "stream", "replica", "seq", "hlc", "tx", "of", "local", "private_of")

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


# What kind of conflict a change is (Conflict.kind), in either store's rebuild.
STATE_CONFLICT, LINK_CONFLICT, FIELD_CONFLICT = "state", "link", "field"
DELETED, MISSING, UNKNOWN = "deleted", "missing", "unknown"


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


def as_text(value: Any) -> str:
    """<value> as the text of its canonical form, never refused (a float stays one): to compare
    values by, so that what the journal cannot hold is named where it is, not failed on."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def gained(before: Iterable[Any], after: Iterable[Any]) -> list[Any]:
    """The entries of <after> that <before> lacks, in <after>'s order (compared as_text, counting
    repeats): what a list that only grows gained. An entry <after> lost is not in it."""
    left = Counter(as_text(entry) for entry in before)
    found = []
    for entry in after:
        key = as_text(entry)
        if left[key]:
            left[key] -= 1
        else:
            found.append(entry)
    return found


def change_id(record: Mapping[str, Any]) -> str:
    """SHA-256 (hex) of the canonical record without its own `id`."""
    body = {key: value for key, value in record.items() if key != "id"}
    return hashlib.sha256(canonical(body)).hexdigest()


def digest(value: Mapping[str, Any]) -> str:
    """SHA-256 (hex) of <value>'s canonical bytes: two Macs that hold the same have the same."""
    return hashlib.sha256(canonical(value)).hexdigest()


def placeholder(change: Mapping[str, Any]) -> Change:
    """What a change deleted on request leaves in its place: its envelope's place (seq, clock,
    transaction, counts), op FORGOTTEN, no target and no body, and an id of its own."""
    made: Change = {key: change[key] for key in PLACEHOLDER_KEYS if key in change}
    made["op"] = FORGOTTEN
    made["id"] = change_id(made)
    return made


# ---- the write boundary -------------------------------------------------------------------------


def where(target: str, path: Iterable[str]) -> str:
    """A place as a person finds it: the item, entity or fact, then the field path."""
    below = ".".join(path)
    return f"{target} {below}" if below else target


def unencodable_edits(
    edits: Iterable[Edit], place: Callable[[Edit, Place], str]
) -> Iterator[tuple[str, str]]:
    """Each value in <edits> that the journal cannot hold (unencodable: a float, text that is not
    valid Unicode), with its place in the store (<place> of the edit and the place in its body)."""
    for edit in edits:
        for found in unencodable(edit.body):
            yield place(edit, found.place), found.what


def refusal(edits: Iterable[Edit], place: Callable[[Edit, Place], str]) -> str:
    """Why a write is refused at the boundary, before anything is journaled: the first value it
    would journal that the journal cannot hold, named by its place; "" when there is none."""
    found = next(unencodable_edits(edits, place), None)
    if found is None:
        return ""
    return (
        f"nothing was written: {found[0]} holds {found[1]}, which the journal cannot hold "
        "(it keeps whole numbers and text in UTF-8)"
    )


def taken_in(value: Any, source: str, place_of: Callable[[Place], str]) -> tuple[Any, list[str]]:
    """<value>, written before or outside the journal (<source>), as the journal can hold it: each
    value it cannot converted once (encodable), with a note naming its place (<place_of>); <value>
    itself when there is nothing to convert."""
    found = list(unencodable(value))
    notes = [
        f"{place_of(place)}: {what} in {source}, which the journal cannot hold; taken in as {made!r}"
        for place, what, made in found
    ]
    return (encodable(value) if found else value), notes


def format_version(value: Any) -> int | None:
    """A store's format version as its file records it: a positive whole number (2, or 2.0 written
    by hand: JSON has one kind of number), else None (a bool, a string, 1.9, 0 or less)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and value >= 1 else None


# ---- what a store's files record of the journal -------------------------------------------------


def stamped(position: Mapping[str, int], held: str | Mapping[str, str]) -> dict[str, Any]:
    """What a store's files record of the journal: the position they reflect (the highest seq of
    each journal file) and the digest of what they hold (DIGEST: one, or one per file for a store
    of several files), so that a change made to them outside the journal shows."""
    return {**position, DIGEST: held if isinstance(held, str) else dict(held)}


def changed_outside(stamp: Any, held: Callable[[], str]) -> bool:
    """Whether a store's files were changed since a write saved them (by hand, a checkout of an
    older version, or a bosun that does not journal and leaves the stamp as it found it): what
    they hold now (<held>, their digest) is not what the stamp records, or holds what the journal
    cannot. Files saved before digests are trusted, and get one when they are saved next."""
    recorded = stamp.get(DIGEST) if isinstance(stamp, dict) else None
    try:
        now = held()
    except ChangeLogError:
        return True
    return isinstance(recorded, str) and now != recorded


def position_in(stamp: Any) -> dict[str, int]:
    """The journal position a stamp records; {} for none (files from before the journal)."""
    if not isinstance(stamp, dict):
        return {}
    return {str(stem): seq for stem, seq in stamp.items() if isinstance(seq, int)}


def behind(journal_has: Mapping[str, int], file_has: Mapping[str, int], file_name: str) -> list[str]:
    """Each journal file that is behind the position <file_name> reflects (it lost changes the file
    has): a rebuild from it would lose them too."""
    return [
        f"{stem}.jsonl ends at seq {journal_has.get(stem, 0)}, {file_name} reflects seq {seq}"
        for stem, seq in sorted(file_has.items())
        if journal_has.get(stem, 0) < seq
    ]


def lost(
    written: Mapping[str, int], position: Mapping[str, int], saved: Mapping[str, int], file_name: str
) -> list[str]:
    """Each journal file behind the position <file_name> reflects (<saved>; behind), judged first
    as a writer judges it, by what each file has written (<written>, whole transactions or not), so
    that only a file that lost lines is named: a lost file can take with it the private lines of
    every transaction left in local.jsonl, which then has no whole transaction (<position>) but
    lost nothing. Then by the whole transactions (a reader's warning, validate)."""
    # Even with no whole transaction left: stricter than a writer, which sets such lines aside and
    # starts again from the files (genesis), on purpose; past the first check only damage gets here.
    return behind(written, saved, file_name) or behind(position, saved, file_name)


def reader_rebuilds(
    position: Mapping[str, int], saved: Mapping[str, int], file_name: str, *, written: Mapping[str, int]
) -> tuple[bool, str]:
    """Whether a reader takes the journal's rebuild (at <position>, its whole transactions) instead
    of the files (at <saved>, which <file_name> records): only where the files are behind the
    journal, a write cut short between the two. A journal behind the files (lost) is never rebuilt
    from: the second value says how (for a warning); "" otherwise. Only a journal with no line at
    all (<written>) is none yet (or moved aside), which the next write starts again."""
    if not written:
        return False, ""
    gaps = lost(written, position, saved, file_name)
    if gaps:
        return False, "; ".join(gaps)
    return saved != position, ""


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
