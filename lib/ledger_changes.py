"""The ledger's changes (design: docs/design/sync.md, "Where the ledger's changes come from",
"Rebuilding, and what a merge cannot decide" and "Which data goes where").

A ledger write is compared, before and after, into field-level edits. The field table (`FIELDS`)
gives each field path its merge rule and its class; `diff` turns two versions of the ledger into
the edits that lead from one to the other, and `rebuild` applies a journal's changes, in the one
order, to an empty ledger: the two are exact inverses. Only `items`, `sources`, `runs` and `said`
are compared; `version` and `journal` are the file's own.

The rebuild checks again what a merge of two Macs' changes cannot keep (the ledger's own rules
come in as `Rules`), and keeps a change that breaks one as a conflict: a state change from a
state the item is no longer in, an important field set from a value it no longer has, a link
that closes a cycle or gives a second `part_of`, a change to a deleted item, an op it does not
know. An item made on two Macs (one id) is merged.

Ops and their bodies (paths relative to the item, as lists):

  item.create              {"item": <the item without its local leaves>}
  item.delete              {}
  item.set                 {"path", "value", "prev"}: a missing leaf is null, and a leaf that was
                           removed also has "unset": true (a null value stays: a cleared proposal)
  item.state               {"from", "to"}: one per transition the write recorded in the history
                           (a one-way message is sent, then applied, in one write)
  item.append              {"path", "entry"}: added at the end (HISTORY and LIST rules)
  item.drop                {"path", "entry"}: the first equal entry goes, see drop_entry (LIST)
  item.link / item.unlink  the link: {"rel", "id", "at", "via"?}
  map.put / map.delete     {"key", "value"?, "prev"?} on `sources` or `runs`
  said.append / said.drop  {"entry"}

A local leaf never goes in a body that is not local: it gets its own `item.set`, marked local.
A history entry or a link that quotes my words or keeps a call's context (`LOCAL_HISTORY_EVENTS`,
`CALL_CONTEXT`, a `via` or evidence that quotes me, `QUOTES_ME`) is a local `item.append` or
`item.link`; a field that quotes me is a local leaf. A new item's create holds its history and its
links up to the first such entry; the rest follows as appends and links in order, local or not, so
that the rebuild keeps the order exactly (and a link made with its item is not checked again).

Pure functions over the ledger document: no file access. `lib/ledger.py` imports this module, so
it never imports `ledger`.
"""

from __future__ import annotations

import copy
import heapq
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from itertools import chain
from typing import Any

from change_log import (
    DELETED,
    FIELD_CONFLICT,
    LINK_CONFLICT,
    MISSING,
    STATE_CONFLICT,
    UNKNOWN,
    Change,
    Conflict,
    Edit,
    as_text,
    digest,
    gained,
    in_order,
    where,
)
from lineage import Items, Link


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
# Where and when a call ran (approvals.CallContext): local wherever an approval, a standing
# approval or a call's outcome keeps it.
CALL_CONTEXT = ("session", "tool_use_id", "transcript")
APPROVAL = "items.*.proposal.approval"
STANDING = "items.*.proposal.auto_approved"
OUTCOMES = tuple(
    f"{grant}.{outcome}" for grant in (APPROVAL, STANDING) for outcome in ("failed", "carried_out")
)
# The keys of an item's fields that the commands and the orchestrator's procedures write, by class
# (a key not named is local until it has a row). Sealed: free text, people's names, URLs and
# permalinks. Private: ids, repo, ticket and system names, enums, times and counts.
SEALED_FIELDS = (
    "subject",
    "requester",
    "to",
    "assignee",
    "answer",
    "details",
    "ask",
    "done_when",
    "via",
    "origin",
    "options",
    "aliases",
    "reply_ref",
    "request_permalink",
    "review_thread",
    "review_url",
    "text",
)
PRIVATE_FIELDS = (
    "repo",
    "repos",
    "area",
    "parent",
    "parent_ticket",
    "epic",
    "project",
    "purpose",
    "channel",
    "channels",
    "category",
    "target",
    "run",
    "window",
    "external_status",
    "status_category",
    "synced_at",
    "replied_at",
    "expect_by",
    "started",
    "ended",
    "reviewed_sha",
    "reviewed_at",
    "last_push_at",
    "commented_at",
    "last_nudged_at",
    "nudge_count",
)
TASK_HEADER = ("ask", "link", "requester", "details")  # ledger.TASK_HEADER_FIELDS: sealed
DELEGATION = ("window", "task", "at", "updates", "last_update_at", "moved_from")
# History events whose note quotes my words (a prompt's excerpt, an approval's quote): local whole,
# as is an entry that keeps a call's context. The property test in tests/unit/test_ledger_changes.py
# fails if a renamed event lets them out.
LOCAL_HISTORY_EVENTS = ("approved", "approval-used", "user-said")
# My words where a command records what something came from or why it moved: a link's or a task's
# `via`, a transition's `evidence`, as 'user: <what I said>' (ledger.USER_EVIDENCE). Local wherever
# they are: a history entry or a link that holds one is local whole, a field that holds one a local
# leaf (decision 3 in docs/design/sync.md: my verbatim words stay on the Mac).
QUOTES_ME = "user:"
QUOTE_KEYS = ("via", "evidence")
# The history event of a state change (ledger.transition_item), with its "from" and "to": a write
# that makes two (a one-way message sent, then applied) is two state changes.
TRANSITION = "transition"


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


def _under(container: str, *keys: str) -> tuple[str, ...]:
    return tuple(f"{container}.{key}" for key in keys)


# Every field of the ledger, each key named: no wildcard below an item, so a key nobody named is
# local (fail closed) and the totality test fails until it has a row. Each container (the item, its
# fields, delegation, report, proposal, the grants and their outcomes) has a row of its own, for
# its whole value when it appears or goes. Important fields conflict when two Macs change them at
# once. Classes: docs/design/sync.md, "Which data goes where".
FIELDS: tuple[FieldRule, ...] = (
    *_rows(STATE, PRIVATE, "items.*.state", important=True),
    *_rows(HISTORY, SEALED, "items.*.history"),
    *_rows(LINKS, PRIVATE, "items.*.links"),
    *_rows(LIST, SEALED, "items.*.fields.messages"),
    # Free text, people's names, URLs and permalinks.
    *_rows(
        SET,
        SEALED,
        *_under("items.*", "title", "url"),
        *_under("items.*.fields", *SEALED_FIELDS),
        *_under("items.*.delegation", *TASK_HEADER),
        *_under("items.*.report", "summary", "open", "results"),
        *_under("items.*.proposal", "action", "spec"),
        *(f"{outcome}.reason" for outcome in OUTCOMES if outcome.endswith(".failed")),
        "sources.*",
    ),
    # My approving words, and where and when a call ran.
    *_rows(
        SET,
        LOCAL,
        f"{APPROVAL}.quote",
        *(f"{where}.{name}" for where in (APPROVAL, STANDING, *OUTCOMES) for name in CALL_CONTEXT),
    ),
    # One proposal per item, an approval used once, one ticket per item, one finding per learn:
    # item (its key is a slug the model wrote, which can carry a colleague's name: sealed).
    *_rows(
        SET,
        PRIVATE,
        "items.*.proposal",
        "items.*.proposal.proposed_at",
        APPROVAL,
        f"{APPROVAL}.used_at",
        "items.*.fields.system",
        important=True,
    ),
    *_rows(SET, SEALED, "items.*.fields.key", important=True),
    # Ids, names, enums, times and counts; and the containers themselves.
    *_rows(
        SET,
        PRIVATE,
        "items.*",
        *_under("items.*", "id", "kind", "created_at", "updated_at", "fields", "delegation", "report"),
        *_under("items.*.fields", *PRIVATE_FIELDS),
        *_under("items.*.delegation", *DELEGATION),
        *_under("items.*.report", "at", "ci", "window"),
        *_under(APPROVAL, "at", "hash", "call"),
        STANDING,
        *_under(STANDING, "at", "class", "call"),
        *OUTCOMES,
        *(f"{outcome}.{name}" for outcome in OUTCOMES for name in ("at", "tool")),
        "runs.*",
    ),
    *_rows(LIST, LOCAL, SAID),
)


def classify(path: Path) -> FieldRule:
    """The row of a full path (("items", <id>, "fields", "repo")); an unknown path is local."""
    return next((row for row in FIELDS if row.matches(path)), FieldRule(path, SET, LOCAL))


def _has_rows_below(path: Path) -> bool:
    return any(len(row.pattern) > len(path) and _fits(row.pattern, path) for row in FIELDS)


def is_container(path: Path, value: Any) -> bool:
    """A dict the table looks into key by key (an item, its fields, a proposal, an approval): diff
    compares it that way, and any other value at a path as one."""
    return isinstance(value, dict) and _has_rows_below(path)


def quotes_me(value: Any) -> bool:
    """Whether <value> is my words as a via or evidence records them ('user: ...')."""
    return isinstance(value, str) and value.lstrip().lower().startswith(QUOTES_ME)


def _is_local(path: Path, value: Any) -> bool:
    """A leaf that never leaves the Mac: by its row, or because it quotes me (a task's via)."""
    if is_container(path, value):
        return False
    return classify(path).klass == LOCAL or (path[-1] in QUOTE_KEYS and quotes_me(value))


def strip_local(path: Path, value: Any) -> Any:
    """<value>, found at the full <path>, without its local leaves (a container comes back as a
    new dict). The rebuild compares an important field's `prev` with the current value stripped so."""
    if not is_container(path, value):
        return value
    return {
        key: strip_local((*path, key), child)
        for key, child in value.items()
        if not _is_local((*path, key), child)
    }


def _local_leaves(path: Path, value: Any) -> list[tuple[Path, Any]]:
    """The local leaves inside <value> at <path>, in path order."""
    if not is_container(path, value):
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


def is_local_entry(entry: Any) -> bool:
    """A history entry or a link that quotes my words (by its event, or a via or evidence that
    quotes me), or keeps a call's context: it never leaves the Mac."""
    return isinstance(entry, dict) and (
        entry.get("event") in LOCAL_HISTORY_EVENTS
        or any(name in entry for name in CALL_CONTEXT)
        or any(quotes_me(entry.get(key)) for key in QUOTE_KEYS)
    )


def _entry_event(entry: Any) -> Any:
    return entry.get("event") if isinstance(entry, dict) else None


def _split_entries(entries: list[Any]) -> tuple[list[Any], list[Any]]:
    """A new item's history or links: what its create holds (up to the first local entry), and the
    rest."""
    first = next((index for index, entry in enumerate(entries) if is_local_entry(entry)), len(entries))
    return entries[:first], entries[first:]


def _key(entry: Any) -> str:
    """An entry as the text of its canonical bytes, to compare entries by: never refused, so that
    diff names a value the journal cannot hold (a float) instead of failing on it."""
    return as_text(entry)


def _history_added(before: list[Any], after: list[Any]) -> list[Any]:
    """The entries of <after> that <before> lacks, in order (gained: as canonical text, counting
    repeats). Entries that went (trimmed) do not count, and a trim marker is never an entry."""
    return [entry for entry in gained(before, after) if _entry_event(entry) != TRIMMED]


# ---- lists ------------------------------------------------------------------------------------


def drop_entry(entries: list[Any], entry: Any) -> bool:
    """Remove the first entry equal to <entry> (as canonical text); False if there is none."""
    key = _key(entry)
    for index, candidate in enumerate(entries):
        if _key(candidate) == key:
            del entries[index]
            return True
    return False


def _kept_start(before: list[str], after: list[str]) -> set[int]:
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
    old, new = [_key(entry) for entry in before], [_key(entry) for entry in after]
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


def _same(before: Any, after: Any) -> bool:
    if before is _MISSING or after is _MISSING:
        return before is after
    return bool(before == after) and as_text(before) == as_text(after)


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


def _history_edit(path: Path, entry: Any) -> Edit:
    local = classify(path).klass == LOCAL or is_local_entry(entry)
    return Edit("item.append", path[1], {"path": list(path[2:]), "entry": _plain(entry)}, local)


def _whole_set(path: Path, before: Any, after: Any) -> list[Edit]:
    """One set of the whole value at <path>; each local leaf inside it in a local set of its own. A
    leaf that was local before stays local (its old value rides along as the set's prev)."""
    if _is_local(path, after) or _is_local(path, before):
        return [_set_edit(path, after, before, local=True)]
    edits = [_set_edit(path, strip_local(path, after), strip_local(path, before), local=False)]
    return edits + [
        _set_edit(leaf, value, _MISSING, local=True) for leaf, value in _local_leaves(path, after)
    ]


def _on_set(path: Path, _rule: FieldRule, before: Any, after: Any, out: _ItemEdits) -> None:
    if is_container(path, before) and is_container(path, after):
        _compare_dict(path, before, after, out)
    else:
        out.sets += _whole_set(path, before, after)


def _on_state(path: Path, rule: FieldRule, before: Any, after: Any, out: _ItemEdits) -> None:
    body = {"from": _plain(before), "to": _plain(after)}
    out.states.append(Edit("item.state", path[1], body, rule.klass == LOCAL))


def _on_history(path: Path, _rule: FieldRule, before: Any, after: Any, out: _ItemEdits) -> None:
    if not _entrywise(before, after):
        out.sets += _whole_set(path, before, after)
        return
    out.appends += [_history_edit(path, entry) for entry in _history_added(_as_list(before), after)]


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
        Edit("item.unlink", path[1], _plain(link), local or is_local_entry(link))
        for key, link in old.items()
        if key not in new
    ]
    out.links += [
        Edit("item.link", path[1], _plain(link), local or is_local_entry(link))
        for key, link in new.items()
        if key not in old
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


def _added_after(path: Path, rule: str, entry: Any) -> Edit:
    """An entry of a new item's history or links that its create does not hold, as its own edit."""
    if rule == LINKS:
        return Edit("item.link", path[1], _plain(entry), is_local_entry(entry))
    return _history_edit(path, entry)


def _created(item_id: str, item: Item) -> list[Edit]:
    """A new item: its create without its local leaves, its history and links from the first local
    entry on as appends and links in order, then each local leaf, by path."""
    path = ("items", item_id)
    body = _plain(strip_local(path, item))
    after = []
    for key, value in list(body.items()):
        rule = classify((*path, key)).rule
        if isinstance(value, list) and rule in (HISTORY, LINKS):
            body[key], rest = _split_entries(value)
            after += [_added_after((*path, key), rule, entry) for entry in rest]
    leaves = [_set_edit(leaf, value, _MISSING, local=True) for leaf, value in _local_leaves(path, item)]
    return [Edit("item.create", item_id, {"item": body}, False), *after, *leaves]


def _transitions(appends: list[Edit]) -> list[tuple[Any, Any]]:
    """The (from, to) of each transition among the history entries a write added, in order."""
    entries = [append.body["entry"] for append in appends]
    return [(entry.get("from"), entry.get("to")) for entry in entries if _entry_event(entry) == TRANSITION]


def _steps(edit: Edit, appends: list[Edit]) -> list[Edit]:
    """A state change as the transitions the write recorded in the history, when they lead from
    its `from` to its `to` (so the rebuild checks each against the kind); else as it is."""
    steps = _transitions(appends)
    states = [edit.body["from"], *(to for _, to in steps)]
    if [was for was, _ in steps] != states[:-1] or states[-1] != edit.body["to"]:
        return [edit]
    return [Edit(edit.op, edit.target, {"from": was, "to": to}, edit.local) for was, to in steps]


def _changed(item_id: str, before: Item, after: Item) -> _ItemEdits:
    out = _ItemEdits()
    _compare_dict(("items", item_id), before, after, out)
    out.states = [step for edit in out.states for step in _steps(edit, out.appends)]
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


# ---- rebuilding -------------------------------------------------------------------------------

# The empty ledger a rebuild starts from (never changed: the rebuild works on a copy). A first
# write's genesis is diff(EMPTY, ledger).
EMPTY: Ledger = {"version": 1, "items": {}, "sources": {}, "runs": {}, SAID: []}
# The file's own: the journal position it reflects, and the digest of the rest (ledger.stamp).
# Never compared, hashed or rebuilt.
JOURNAL = "journal"


@dataclass(frozen=True)
class Rules:
    """The ledger's rules that a merge cannot keep, checked again in the rebuild: whether an item
    of a kind may move from one state to another, and why a link may not be made (None: it may).
    `ledger.RULES` is the real one; it is passed in because `ledger` imports this module."""

    allowed: Callable[[str, str, str], bool]
    link_problem: Callable[[Items, Link], str | None]


@dataclass
class Rebuilt:
    ledger: Ledger
    conflicts: list[Conflict]


class _Refused(Exception):
    """A change the rebuild keeps but does not apply: its conflict."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


Transaction = tuple[str, str]  # a change's (replica, tx)


@dataclass
class _Merged:
    """A merged item's links from the other Mac, checked once its transaction's items are made."""

    change: Change
    item_id: str
    links: list[Any]


@dataclass
class _Rebuild:
    """The ledger as the changes so far make it; the change being applied; the replicas that
    deleted each deleted item (delete wins); the links of merged items still to check; and the
    transaction that made each item new (not merged into), whose links to it are not checked."""

    ledger: Ledger
    rules: Rules
    current: Change = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    deleted: dict[str, set[str]] = field(default_factory=dict)
    merged: dict[Transaction, list[_Merged]] = field(default_factory=dict)
    made_by: dict[str, Transaction] = field(default_factory=dict)

    @property
    def items(self) -> dict[str, Item]:
        items: dict[str, Item] = self.ledger["items"]
        return items

    def conflict(self, kind: str, detail: str, change: Change | None = None) -> None:
        """A conflict for <change> (the one being applied, unless given)."""
        of = self.current if change is None else change
        self.conflicts.append(Conflict(str(of.get("id", "")), str(of.get("target", "")), kind, detail))

    def item(self, item_id: str) -> Item:
        """The item a change edits; a deleted one, or one never made, refuses the change."""
        if item_id in self.deleted:
            raise _Refused(DELETED, f"{item_id} was deleted, and a delete wins")
        found = self.items.get(item_id)
        if not isinstance(found, dict):
            raise _Refused(MISSING, f"no item {item_id}")
        return found


def _path(body: Mapping[str, Any]) -> Path:
    path = tuple(body["path"])
    if not path or not all(isinstance(key, str) for key in path):
        raise _Refused(UNKNOWN, f"not a field path: {body['path']!r}")
    return path


def _value_at(node: Any, path: Path) -> Any:
    """The value at <path> below <node>; _MISSING where a key (or a dict on the way) is not there."""
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]
    return node


def _parent(item: Item, path: Path) -> dict[str, Any]:
    """The dict that holds <path>'s last key, making the missing dicts on the way. A value there
    that is not a dict (a cleared proposal) refuses the change."""
    node = item
    for depth, key in enumerate(path[:-1]):
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise _Refused(FIELD_CONFLICT, f"{'.'.join(path[: depth + 1])} holds no fields any more")
    return node


def _check_prev(item_id: str, path: Path, current: Any, prev: Any) -> None:
    """An important field is set only from the value it had where the change was made (my local
    leaves aside); else two Macs changed it at once."""
    full = ("items", item_id, *path)
    here = strip_local(full, None if current is _MISSING else current)
    if not _same(here, prev):
        raise _Refused(
            FIELD_CONFLICT, f"{'.'.join(path)} changed elsewhere first: not set from what it is here"
        )


def _set(state: _Rebuild, item_id: str, body: dict[str, Any]) -> None:
    item, path = state.item(item_id), _path(body)
    current = _value_at(item, path)
    if classify(("items", item_id, *path)).important:
        _check_prev(item_id, path, current, body.get("prev"))
    if not body.get("unset"):
        _parent(item, path)[path[-1]] = copy.deepcopy(body["value"])
    elif current is not _MISSING:
        del _parent(item, path)[path[-1]]


def _state(state: _Rebuild, item_id: str, body: dict[str, Any]) -> None:
    item = state.item(item_id)
    was, to, kind = body["from"], body["to"], item.get("kind")
    if item.get("state") != was:
        raise _Refused(STATE_CONFLICT, f"{was} -> {to}, but it is {item.get('state')} here")
    if not state.rules.allowed(str(kind), str(was), str(to)):
        raise _Refused(STATE_CONFLICT, f"{was} -> {to} is not a transition of a {kind}")
    item["state"] = to


def _append(state: _Rebuild, item_id: str, body: dict[str, Any]) -> None:
    item, path = state.item(item_id), _path(body)
    entries = _parent(item, path).setdefault(path[-1], [])
    if not isinstance(entries, list):
        raise _Refused(FIELD_CONFLICT, f"{'.'.join(path)} is not a list here")
    entries.append(copy.deepcopy(body["entry"]))


def _drop(state: _Rebuild, item_id: str, body: dict[str, Any]) -> None:
    """The first equal entry goes; an entry that is not there is nothing to do."""
    entries = _value_at(state.item(item_id), _path(body))
    if isinstance(entries, list):
        drop_entry(entries, body["entry"])


def _as_link(source: str, entry: Mapping[str, Any]) -> Link:
    return Link(
        source, str(entry["rel"]), str(entry["id"]), str(entry.get("via", "")), str(entry.get("at", ""))
    )


def _add_link(
    state: _Rebuild, item_id: str, item: Item, entry: Mapping[str, Any], *, checked: bool = True
) -> str | None:
    """Add a stored link unless the item has it (the same relation, other end and time) or the
    rules refuse it (if <checked>); the refusal, else None. A link made again with a new time is
    another link, so an unlink on another Mac of the old one leaves it (add wins)."""
    if any(_link_key(link) == _link_key(entry) for link in item.get("links") or []):
        return None
    problem = state.rules.link_problem(state.items, _as_link(item_id, entry)) if checked else None
    if problem is None:
        item.setdefault("links", []).append(copy.deepcopy(entry))
    return problem


def _link(state: _Rebuild, item_id: str, body: dict[str, Any]) -> None:
    """A link the rules allow. One the write that made the item adds (after its create, from the
    first that quotes me) is not checked, as its create's links are not: no other Mac knows the
    item yet, and its write checked them."""
    with_its_item = state.made_by.get(item_id) == _transaction(state.current)
    problem = _add_link(state, item_id, state.item(item_id), body, checked=not with_its_item)
    if problem:
        raise _Refused(LINK_CONFLICT, problem)


def _unlink(state: _Rebuild, item_id: str, body: dict[str, Any]) -> None:
    """Only the link with this relation, other end and time goes."""
    item = state.item(item_id)
    if isinstance(item.get("links"), list):
        item["links"] = [link for link in item["links"] if _link_key(link) != _link_key(body)]


def _entry_time(entry: Any) -> str:
    return str(entry.get("at", "")) if isinstance(entry, dict) else ""


def _union_history(history: list[Any], other: list[Any]) -> list[Any]:
    """Both Macs' entries, each once: <other>'s that <history> lacks merged in by time, each side
    in its own order (an equal time keeps <history>'s first). The first entry and a trim marker
    stay where they are; <other>'s markers are not entries."""
    head = history[:2] if len(history) > 1 and _entry_event(history[1]) == TRIMMED else history[:1]
    added = copy.deepcopy(_history_added(history, other))
    return [*head, *heapq.merge(history[len(head) :], added, key=_entry_time)]


def _keep_earlier(state: _Rebuild, path: Path, mine: Any, theirs: Any) -> None:
    """An important field (the state, a proposal, a ticket key) both Macs made: the earlier one's
    stays, whole; another value from the later one is a conflict."""
    if theirs is None or _same(strip_local(path, mine), strip_local(path, theirs)):
        return
    if classify(path).rule == STATE:
        state.conflict(STATE_CONFLICT, f"made on two Macs, {theirs} there and {mine} here: the earlier stays")
    else:
        state.conflict(
            FIELD_CONFLICT, f"made on two Macs with another {'.'.join(path[2:])}: the earlier stays"
        )


def _add_missing(state: _Rebuild, path: Path, into: dict[str, Any], other: Mapping[str, Any]) -> None:
    """The fields of <other> that <into> lacks (an empty important one too), into it, container by
    container. An important container (a proposal, its approval) is one value: never blended. The
    history and links are merged apart."""
    for key, value in other.items():
        inner = (*path, key)
        rule = classify(inner)
        if rule.rule in (HISTORY, LINKS):
            continue
        if key not in into or (rule.important and into[key] is None):
            into[key] = copy.deepcopy(value)
        elif rule.important:
            _keep_earlier(state, inner, into[key], value)
        elif is_container(inner, into[key]) and is_container(inner, value):
            _add_missing(state, inner, into[key], value)


def _transaction(change: Change) -> Transaction:
    return (str(change.get("replica", "")), str(change.get("tx", "")))


def _merge(state: _Rebuild, item_id: str, item: Item, other: Item) -> None:
    """The same id made on two Macs (one PR, one Slack message): what the item lacks is added, a
    different state or important field is a conflict (the earlier stays), the histories are
    unioned, and the other's links are checked once its transaction's items are all made (a link
    may point at one made after this one)."""
    _add_missing(state, ("items", item_id), item, other)
    item["history"] = _union_history(_as_list(item.get("history")), _as_list(other.get("history")))
    links = _as_list(other.get("links"))
    if links:
        state.merged.setdefault(_transaction(state.current), []).append(
            _Merged(state.current, item_id, links)
        )


def _check_merged_links(state: _Rebuild, transaction: Transaction) -> None:
    """The links of the items <transaction> merged into: each one the rules refuse is left out,
    and a conflict of the change that brought it."""
    for merged in state.merged.pop(transaction, []):
        item = state.items.get(merged.item_id)
        if item is None:
            problems = [f"{merged.item_id} is gone"]
        else:
            refused = (_add_link(state, merged.item_id, item, link) for link in merged.links)
            problems = [problem for problem in refused if problem]
        if problems:
            detail = f"merged, but without its link(s): {'; '.join(problems)}"
            state.conflict(LINK_CONFLICT, detail, merged.change)


def _restart(state: _Rebuild, item_id: str) -> None:
    """A deleted id made again. By the replica that deleted it, and no other, the item starts
    again: later in that replica's own sequence, it was made after the delete was seen (`prune`,
    then a link that tracks the ticket again). By another replica, delete wins: it may not have
    seen the delete."""
    deleters = state.deleted.get(item_id)
    if deleters is None:
        return
    if deleters != {state.current.get("replica")}:
        raise _Refused(DELETED, f"{item_id} was deleted on another Mac, and a delete wins")
    del state.deleted[item_id]


def _create(state: _Rebuild, item_id: str, body: dict[str, Any]) -> None:
    """A new item as made, its links unchecked (they were checked where it was made); an id that
    exists is merged into."""
    made = body["item"]
    if not isinstance(made, dict):
        raise _Refused(UNKNOWN, "an item that is not an object")
    _restart(state, item_id)
    if item_id in state.items:
        _merge(state, item_id, state.items[item_id], made)
    else:
        state.items[item_id] = copy.deepcopy(made)
        state.made_by[item_id] = _transaction(state.current)


def _delete(state: _Rebuild, item_id: str, _body: dict[str, Any]) -> None:
    """A tombstone: the item goes, and every later change for it but a delete (or a create by the
    replica that deleted it) is a conflict."""
    state.items.pop(item_id, None)
    state.deleted.setdefault(item_id, set()).add(str(state.current.get("replica", "")))


def _map(state: _Rebuild, name: str) -> dict[str, Any]:
    if name not in MAPS:
        raise _Refused(UNKNOWN, f"no map {name!r} in the ledger")
    found: dict[str, Any] = state.ledger[name]
    return found


def _map_put(state: _Rebuild, name: str, body: dict[str, Any]) -> None:
    _map(state, name)[body["key"]] = copy.deepcopy(body["value"])


def _map_delete(state: _Rebuild, name: str, body: dict[str, Any]) -> None:
    _map(state, name).pop(body["key"], None)


def _said_append(state: _Rebuild, _target: str, body: dict[str, Any]) -> None:
    state.ledger[SAID].append(copy.deepcopy(body["entry"]))


def _said_drop(state: _Rebuild, _target: str, body: dict[str, Any]) -> None:
    drop_entry(state.ledger[SAID], body["entry"])


# Each op diff emits, and how the rebuild applies it.
_Applier = Callable[[_Rebuild, str, dict[str, Any]], None]
_APPLIERS: dict[str, _Applier] = {
    "item.create": _create,
    "item.delete": _delete,
    "item.set": _set,
    "item.state": _state,
    "item.append": _append,
    "item.drop": _drop,
    "item.link": _link,
    "item.unlink": _unlink,
    "map.put": _map_put,
    "map.delete": _map_delete,
    "said.append": _said_append,
    "said.drop": _said_drop,
}


def _apply(state: _Rebuild, change: Change) -> None:
    """One change, by its op's applier. An op this bosun does not know is kept as a conflict, not
    dropped (a newer bosun's change), and so is a body it cannot read."""
    op = change.get("op")
    applier = _APPLIERS.get(op) if isinstance(op, str) else None
    if applier is None:
        raise _Refused(UNKNOWN, f"an op this bosun does not know ({op!r}): update bosun")
    try:
        applier(state, change["target"], change["body"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise _Refused(UNKNOWN, f"a body this bosun cannot apply ({type(exc).__name__}: {exc})") from exc


def rebuild(changes: Iterable[Change], rules: Rules) -> Rebuilt:
    """The ledger the changes make: applied in the one order (each id once) to an empty ledger,
    then each item's history trimmed. A change that breaks a rule a merge cannot keep stays in
    the journal, has no effect and is a conflict: a state change whose `from` no longer holds or
    that the kind does not allow, an important field set from a value it no longer has, a link
    the rules refuse, any change to a deleted item but a delete (or a create by the replica that
    deleted it), and an op or body this bosun cannot apply. An item made on two Macs is merged,
    with a conflict for each state, important field or link the two do not agree on."""
    state = _Rebuild(copy.deepcopy(EMPTY), rules)
    ordered = in_order(changes)
    last_creates = {
        _transaction(change): index
        for index, change in enumerate(ordered)
        if change.get("op") == "item.create"
    }
    for index, change in enumerate(ordered):
        state.current = change
        try:
            _apply(state, change)
        except _Refused as refused:
            state.conflict(refused.kind, refused.detail)
        if last_creates.get(_transaction(change)) == index:
            _check_merged_links(state, _transaction(change))
    for item in state.items.values():
        if isinstance(item.get("history"), list):
            trim_history(item)
    return Rebuilt(state.ledger, state.conflicts)


def state_hash(ledger: Ledger) -> str:
    """The digest of the ledger without its journal key (change_log.digest): two Macs that read
    the same changes have the same, and ledger.json records it as the digest of what it holds."""
    return digest({key: value for key, value in ledger.items() if key != JOURNAL})


# ---- what the journal cannot hold -------------------------------------------------------------

# The keys of an edit's body that hold the ledger's own values (an item, a field's value and the
# value before, a list's entry); below them a place is the ledger's path. A link's or a state
# change's body is the link or the move itself.
_HELD = ("item", "value", "prev", "entry")
_OWN_PLACE = {"item.link": "links", "item.unlink": "links", "item.state": "state"}


def ledger_place(path: Path) -> str:
    """where() for a full path in the ledger (("items", <id>, "fields", "x"), ("sources", <key>))."""
    head = path[:2] if path[:1] == ("items",) else path[:1]
    return where(head[-1] if head else "", path[len(head) :])


def place(edit: Edit, inner: Path) -> str:
    """where() for <inner>, a place inside <edit>'s body."""
    head, below = inner[0], list(inner[1:])
    if head == "item":
        path = below
    elif head in _HELD:
        start = edit.body.get("path") or ([edit.body["key"]] if "key" in edit.body else [])
        path = [*map(str, start), *below]
    else:
        path = [*([_OWN_PLACE[edit.op]] if edit.op in _OWN_PLACE else []), *inner]
    return where(edit.target, path)
