"""The ledger's changes (design: docs/design/sync.md, "Where the ledger's changes come from" and
"Which data goes where").

A ledger write is compared, before and after, into field-level edits. The field table (`FIELDS`)
gives each field path its merge rule and its class; `diff` turns two versions of the ledger into
the edits that lead from one to the other, and the rebuild applies them: the two are exact
inverses. Only `items`, `sources`, `runs` and `said` are compared; `version` and `journal` are the
file's own.

Ops and their bodies (paths relative to the item, as lists):

  item.create              {"item": <the item without its local leaves>}
  item.delete              {}
  item.set                 {"path", "value", "prev"}: a missing leaf is null, and a leaf that was
                           removed also has "unset": true (a null value stays: a cleared proposal)
  item.state               {"from", "to"}
  item.append              {"path", "entry"}: added at the end (HISTORY and LIST rules)
  item.drop                {"path", "entry"}: the first equal entry goes, see drop_entry (LIST)
  item.link / item.unlink  the link: {"rel", "id", "at", "via"?}
  map.put / map.delete     {"key", "value"?, "prev"?} on `sources` or `runs`
  said.append / said.drop  {"entry"}

A local leaf never goes in a body that is not local: it gets its own `item.set`, marked local.

Pure functions over the ledger document: no file access. `lib/ledger.py` imports this module, so
it never imports `ledger`.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from itertools import chain
from typing import Any

from change_log import Edit, canonical


Ledger = dict[str, Any]
Item = dict[str, Any]
Path = tuple[str, ...]

LOCAL, SEALED, PRIVATE, TEAM = "local", "sealed", "private", "team"
SET, STATE, HISTORY, LIST, LINKS = "set", "state", "history", "list", "links"

# History per item: the first entry, a trim marker and the newest entries beyond this.
MAX_HISTORY = 200
TRIMMED = "history-trimmed"
MAPS = ("sources", "runs")
SAID = "said"
APPROVAL_CONTEXT = ("quote", "session", "tool_use_id", "transcript")


@dataclass(frozen=True)
class FieldRule:
    """A field path's merge rule and class; `*` in the pattern matches one path segment."""

    pattern: tuple[str, ...]
    rule: str
    klass: str
    important: bool = False

    def matches(self, path: Path) -> bool:
        return len(path) == len(self.pattern) and _fits(self.pattern, path)


def _fits(pattern: Path, path: Path) -> bool:
    """Whether <path> and <pattern> agree as far as both go."""
    return all(want in ("*", part) for want, part in zip(pattern, path, strict=False))


def _rows(rule: str, klass: str, *patterns: str, important: bool = False) -> tuple[FieldRule, ...]:
    return tuple(FieldRule(tuple(pattern.split(".")), rule, klass, important) for pattern in patterns)


# Every field of the ledger, first match wins. A new field is a row here: an unknown one is local,
# and the fixture test fails until it has a row. Important fields conflict when two Macs change
# them at once. A container's own row is about its whole value, when it appears or goes.
FIELDS: tuple[FieldRule, ...] = (
    *_rows(STATE, PRIVATE, "items.*.state", important=True),
    *_rows(HISTORY, SEALED, "items.*.history"),
    *_rows(LINKS, PRIVATE, "items.*.links"),
    *_rows(SET, SEALED, "items.*.title", "items.*.fields.subject", "items.*.report.summary"),
    *_rows(LIST, SEALED, "items.*.fields.messages"),
    *_rows(
        SET,
        SEALED,
        "items.*.fields.requester",
        "items.*.fields.to",
        "items.*.fields.answer",
        "items.*.fields.details",
    ),
    *_rows(SET, PRIVATE, "items.*.proposal.proposed_at", "items.*.proposal.approval.used_at", important=True),
    *_rows(SET, LOCAL, *(f"items.*.proposal.approval.{leaf}" for leaf in APPROVAL_CONTEXT)),
    *_rows(SET, PRIVATE, "items.*.proposal", "items.*.proposal.approval", important=True),
    *_rows(SET, PRIVATE, "items.*.delegation", "items.*.report"),
    *_rows(SET, PRIVATE, "items.*.proposal.*", "items.*.proposal.approval.*"),
    *_rows(SET, PRIVATE, "items.*.fields.key", "items.*.fields.system", important=True),
    *_rows(
        SET,
        PRIVATE,
        "items.*.id",
        "items.*.kind",
        "items.*.url",
        "items.*.created_at",
        "items.*.updated_at",
        "items.*.fields.*",
        "items.*.delegation.*",
        "items.*.report.*",
    ),
    *_rows(SET, PRIVATE, "sources.*", "runs.*"),
    *_rows(LIST, LOCAL, SAID),
)


def classify(path: Path) -> FieldRule:
    """The row of a full path (("items", <id>, "fields", "repo")); an unknown path is local."""
    return next((row for row in FIELDS if row.matches(path)), FieldRule(path, SET, LOCAL))


def _has_rows_below(path: Path) -> bool:
    return any(len(row.pattern) > len(path) and _fits(row.pattern, path) for row in FIELDS)


def _is_container(path: Path, value: Any) -> bool:
    """A dict the table looks into field by field (an item, its fields, a proposal, an approval)."""
    return isinstance(value, dict) and _has_rows_below(path)


def _is_local(path: Path, value: Any) -> bool:
    return not _is_container(path, value) and classify(path).klass == LOCAL


def strip_local(path: Path, value: Any) -> Any:
    """<value>, found at the full <path>, without its local leaves (a container comes back as a
    new dict). The rebuild compares an important field's `prev` with the current value stripped so."""
    if not _is_container(path, value):
        return value
    return {
        key: strip_local((*path, key), child)
        for key, child in value.items()
        if not _is_local((*path, key), child)
    }


def _local_leaves(path: Path, value: Any) -> list[tuple[Path, Any]]:
    """The local leaves inside <value> at <path>, in path order."""
    if not _is_container(path, value):
        return []
    found: list[tuple[Path, Any]] = []
    for key in sorted(value):
        inner = (*path, key)
        found += [(inner, value[key])] if _is_local(inner, value[key]) else _local_leaves(inner, value[key])
    return found


# ---- history ----------------------------------------------------------------------------------


def trim_history(item: Item) -> None:
    """Keep at most MAX_HISTORY entries: the first ("created"), a marker counting what was
    dropped, and the newest ones."""
    history = item["history"]
    if len(history) <= MAX_HISTORY:
        return
    marker = history[1] if history[1].get("event") == TRIMMED else None
    keep = history[-(MAX_HISTORY - 2) :]
    dropped = len(history) - 1 - (1 if marker else 0) - len(keep) + int(marker["count"] if marker else 0)
    item["history"] = [history[0], {"at": keep[0]["at"], "event": TRIMMED, "count": dropped}, *keep]


def _history_added(before: list[Any], after: list[Any]) -> list[Any]:
    """The entries of <after> that <before> lacks, in order (as canonical bytes, counting repeats).
    Entries that went (trimmed) do not count, and a trim marker is never an entry."""
    left = Counter(canonical(entry) for entry in before)
    added = []
    for entry in after:
        key = canonical(entry)
        if left[key]:
            left[key] -= 1
        elif not (isinstance(entry, dict) and entry.get("event") == TRIMMED):
            added.append(entry)
    return added


# ---- lists ------------------------------------------------------------------------------------


def drop_entry(entries: list[Any], entry: Any) -> bool:
    """Remove the first entry equal to <entry> (as canonical bytes); False if there is none."""
    key = canonical(entry)
    for index, candidate in enumerate(entries):
        if canonical(candidate) == key:
            del entries[index]
            return True
    return False


def _kept_start(before: list[bytes], after: list[bytes]) -> set[int]:
    """The positions in <before> of the longest start of <after> that <before> holds in order."""
    kept: set[int] = set()
    position = 0
    for key in after:
        while position < len(before) and before[position] != key:
            position += 1
        if position == len(before):
            break
        kept.add(position)
        position += 1
    return kept


def list_edits(before: list[Any], after: list[Any]) -> tuple[list[Any], list[Any]]:
    """The entries to drop from <before>, one drop_entry each, then the entries to append, that
    give <after>. The longest start of <after> that <before> holds in order stays; where repeated
    entries would come back in another order, everything is dropped and appended again."""
    old, new = [canonical(entry) for entry in before], [canonical(entry) for entry in after]
    kept = _kept_start(old, new)
    remaining = list(old)
    for index in range(len(old)):
        if index not in kept:
            remaining.remove(old[index])
    if remaining != new[: len(kept)]:
        return list(before), list(after)
    return [entry for index, entry in enumerate(before) if index not in kept], after[len(kept) :]


# ---- comparing --------------------------------------------------------------------------------


class _Missing:
    """A key that is not there, as distinct from a null value."""

    def __repr__(self) -> str:
        return "MISSING"


_MISSING: Any = _Missing()


@dataclass
class _ItemEdits:
    """One existing item's edits, by kind, so that `diff` can put them in order."""

    sets: list[Edit] = field(default_factory=list)
    lists: list[Edit] = field(default_factory=list)
    states: list[Edit] = field(default_factory=list)
    appends: list[Edit] = field(default_factory=list)
    unlinks: list[Edit] = field(default_factory=list)
    links: list[Edit] = field(default_factory=list)

    def body(self) -> list[Edit]:
        """Everything but the links: sets by path, list edits, the state, then history."""
        by_path = sorted(self.sets, key=lambda edit: edit.body["path"])
        return [*by_path, *self.lists, *self.states, *self.appends]


def _text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _same(before: Any, after: Any) -> bool:
    if before is _MISSING or after is _MISSING:
        return before is after
    return bool(before == after) and _text(before) == _text(after)


def _plain(value: Any) -> Any:
    """A copy of <value> for a body (null for a missing one), sharing nothing with the ledger."""
    return None if value is _MISSING else copy.deepcopy(value)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _entrywise(before: Any, after: Any) -> bool:
    """Whether a list's rule applies entry by entry: both are lists, or the list is new and not
    empty. A list that goes, turns into something else or appears empty is one set."""
    return isinstance(after, list) and (isinstance(before, list) or (before is _MISSING and bool(after)))


def _set_edit(path: Path, value: Any, prev: Any, *, local: bool) -> Edit:
    body = {"path": list(path[2:]), "value": _plain(value), "prev": _plain(prev)}
    if value is _MISSING:
        body["unset"] = True
    return Edit("item.set", path[1], body, local)


def _entry_edit(op: str, path: Path, rule: FieldRule, entry: Any) -> Edit:
    return Edit(op, path[1], {"path": list(path[2:]), "entry": _plain(entry)}, rule.klass == LOCAL)


def _whole_set(path: Path, before: Any, after: Any) -> list[Edit]:
    """One set of the whole value at <path>; each local leaf inside it in a local set of its own."""
    if _is_local(path, after):
        return [_set_edit(path, after, before, local=True)]
    edits = [_set_edit(path, strip_local(path, after), strip_local(path, before), local=False)]
    return edits + [
        _set_edit(leaf, value, _MISSING, local=True) for leaf, value in _local_leaves(path, after)
    ]


def _on_set(path: Path, _rule: FieldRule, before: Any, after: Any, out: _ItemEdits) -> None:
    if _is_container(path, before) and _is_container(path, after):
        _compare_dict(path, before, after, out)
    else:
        out.sets += _whole_set(path, before, after)


def _on_state(path: Path, rule: FieldRule, before: Any, after: Any, out: _ItemEdits) -> None:
    body = {"from": _plain(before), "to": _plain(after)}
    out.states.append(Edit("item.state", path[1], body, rule.klass == LOCAL))


def _on_history(path: Path, rule: FieldRule, before: Any, after: Any, out: _ItemEdits) -> None:
    if not _entrywise(before, after):
        out.sets += _whole_set(path, before, after)
        return
    out.appends += [
        _entry_edit("item.append", path, rule, e) for e in _history_added(_as_list(before), after)
    ]


def _on_list(path: Path, rule: FieldRule, before: Any, after: Any, out: _ItemEdits) -> None:
    if not _entrywise(before, after):
        out.sets += _whole_set(path, before, after)
        return
    drops, appends = list_edits(_as_list(before), after)
    out.lists += [_entry_edit("item.drop", path, rule, entry) for entry in drops]
    out.lists += [_entry_edit("item.append", path, rule, entry) for entry in appends]


def _link_key(link: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    """A link is known by its relation, its other end and its time: a link made again is new."""
    return (link.get("rel"), link.get("id"), link.get("at"))


def _on_links(path: Path, rule: FieldRule, before: Any, after: Any, out: _ItemEdits) -> None:
    if not _entrywise(before, after):
        out.sets += _whole_set(path, before, after)
        return
    old = {_link_key(link): link for link in _as_list(before)}
    new = {_link_key(link): link for link in after}
    local = rule.klass == LOCAL
    out.unlinks += [
        Edit("item.unlink", path[1], _plain(link), local) for key, link in old.items() if key not in new
    ]
    out.links += [
        Edit("item.link", path[1], _plain(link), local) for key, link in new.items() if key not in old
    ]


_Handler = Callable[[Path, FieldRule, Any, Any, _ItemEdits], None]
_HANDLERS: dict[str, _Handler] = {
    SET: _on_set,
    STATE: _on_state,
    HISTORY: _on_history,
    LIST: _on_list,
    LINKS: _on_links,
}


def _compare(path: Path, before: Any, after: Any, out: _ItemEdits) -> None:
    if not _same(before, after):
        rule = classify(path)
        _HANDLERS[rule.rule](path, rule, before, after, out)


def _compare_dict(path: Path, before: Mapping[str, Any], after: Mapping[str, Any], out: _ItemEdits) -> None:
    for key in sorted(before.keys() | after.keys()):
        _compare((*path, key), before.get(key, _MISSING), after.get(key, _MISSING), out)


def _created(item_id: str, item: Item) -> list[Edit]:
    """A new item: its create without its local leaves, then each of them, by path."""
    path = ("items", item_id)
    create = Edit("item.create", item_id, {"item": _plain(strip_local(path, item))}, False)
    return [
        create,
        *(_set_edit(leaf, value, _MISSING, local=True) for leaf, value in _local_leaves(path, item)),
    ]


def _changed(item_id: str, before: Item, after: Item) -> _ItemEdits:
    out = _ItemEdits()
    _compare_dict(("items", item_id), before, after, out)
    return out


def _map_edits(name: str, before: Mapping[str, Any], after: Mapping[str, Any]) -> list[Edit]:
    """A put per new or changed entry of <name> (sources, runs), a delete per entry that went."""
    edits = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key, _MISSING), after.get(key, _MISSING)
        if _same(old, new):
            continue
        body: dict[str, Any] = {"key": key}
        body.update({"value": _plain(new)} if new is not _MISSING else {})
        body.update({"prev": _plain(old)} if old is not _MISSING else {})
        op = "map.put" if new is not _MISSING else "map.delete"
        edits.append(Edit(op, name, body, classify((name, key)).klass == LOCAL))
    return edits


def _said_edits(before: Any, after: Any) -> list[Edit]:
    local = classify((SAID,)).klass == LOCAL
    drops, appends = list_edits(_as_list(before), _as_list(after))
    return [
        *(Edit("said.drop", SAID, {"entry": _plain(entry)}, local) for entry in drops),
        *(Edit("said.append", SAID, {"entry": _plain(entry)}, local) for entry in appends),
    ]


def _section(ledger: Ledger, name: str) -> dict[str, Any]:
    value = ledger.get(name)
    return value if isinstance(value, dict) else {}


def _item_diff(old: Mapping[str, Item], new: Mapping[str, Item]) -> list[Edit]:
    changed = [_changed(item_id, old[item_id], new[item_id]) for item_id in sorted(new.keys() & old.keys())]
    return [
        *chain.from_iterable(_created(item_id, new[item_id]) for item_id in sorted(new.keys() - old.keys())),
        *chain.from_iterable(item.body() for item in changed),
        *chain.from_iterable(item.unlinks for item in changed),
        *chain.from_iterable(item.links for item in changed),
        *(Edit("item.delete", item_id, {}, False) for item_id in sorted(old.keys() - new.keys())),
    ]


def diff(before: Ledger, after: Ledger) -> list[Edit]:
    """The edits that turn <before> into <after>, in one order: each new item (its create, then
    its local leaves); each changed item's sets, list edits, state and history; every unlink, then
    every link (after the creates, so that a link can point at a new item; unlinks first, so that
    a move is never a second part_of); deletes; sources and runs; said. Items go by id, map
    entries by key."""
    edits = _item_diff(_section(before, "items"), _section(after, "items"))
    for name in MAPS:
        edits += _map_edits(name, _section(before, name), _section(after, name))
    return edits + _said_edits(before.get(SAID), after.get(SAID))
