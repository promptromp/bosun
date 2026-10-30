"""Work-item lineage: typed links between ledger items (design: docs/design/lineage.md).

A link is stored once, on the item it starts from: `links: [{rel, id, via?, at}]`. Its inverse
(`spawned` for `spawned_from`, `produced_by` for `produces`, ...) is computed on read. An outreach
item's `parent` field stays what it is (it also scopes the outreach id) and reads as a computed
link: the parent is `blocked_by` the outreach.

Pure functions over the ledger's items: no file access. lib/ledger.py stores and prints.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from similar import containment, words


Item = dict[str, Any]
Items = Mapping[str, Item]
IsOpen = Callable[[Item], bool]


class LineageError(Exception):
    pass


@dataclass(frozen=True)
class Relation:
    inverse: str
    meaning: str
    # Cycles are refused among the links of one family ("": cycles do not matter).
    family: str = ""


LINEAGE = "lineage"  # the family of spawned_from and produces (and outreach under its parent)
DUPLICATE = "duplicate"  # the family of duplicate_of and folded_into: what carries an item on
WORKSTREAM = "workstream"  # the family of part_of: a workstream's parent workstreams
# A new relation is one more row here, plus its line in the README's lineage section.
RELATIONS: dict[str, Relation] = {
    "spawned_from": Relation(
        "spawned",
        "provenance: this item came from <other> (via: the message, or 'user: <what I said>')",
        LINEAGE,
    ),
    "produces": Relation("produced_by", "this item's work produced <other>, e.g. a task's PR", LINEAGE),
    "blocked_by": Relation("blocks", "this item is not finished while <other> is open", "blocking"),
    "duplicate_of": Relation(
        "duplicates", "this item is the same as <other>, which carries it on", DUPLICATE
    ),
    "folded_into": Relation(
        "absorbs", "this item's work is carried on as part of <other> (e.g. in its PR)", DUPLICATE
    ),
    "tracks": Relation("tracked_by", "<other> is the external ticket that tracks this item"),
    "part_of": Relation(
        "has_part",
        "this item belongs to workstream <other> (at most one; a workstream: its parent workstream)",
        WORKSTREAM,
    ),
    "relates_to": Relation("relates_to", "loosely related"),
}
TRACKS = "tracks"
PRODUCES = "produces"
SPAWNED_FROM = "spawned_from"
BLOCKED_BY = "blocked_by"
DUPLICATE_OF = "duplicate_of"
FOLDED_INTO = "folded_into"
PART_OF = "part_of"
RELATES_TO = "relates_to"
# How listings word a relation, where its name reads badly in a sentence (default: the name).
PHRASES = {FOLDED_INTO: "folded into", PART_OF: "part of"}
TICKET_KIND = "ticket"
WORKSTREAM_KIND = "workstream"
# A workstream and its parent: at most this many levels (an item may belong to either).
MAX_WORKSTREAM_DEPTH = 2
# What a workstream may be linked with: it is a container, not work.
WORKSTREAM_RELATIONS = (PART_OF, RELATES_TO)
OUTREACH_KIND = "outreach"
TASK_KIND = "task"
# Lineage edges, as (relation, direction): "down" points from an item to what came from it.
TREE_DOWN = {SPAWNED_FROM: "in", PRODUCES: "out"}
SIMILAR_TITLE = 0.5
MORE_SHOWN = 1  # ids shown in a list hint before "+n"


@dataclass(frozen=True)
class Link:
    source: str
    rel: str
    target: str
    via: str = ""
    at: str = ""
    computed: bool = False

    def outward(self) -> dict[str, Any]:
        """As seen from its source."""
        shown = {"rel": self.rel, "id": self.target, "via": self.via, "at": self.at}
        return {k: v for k, v in shown.items() if v} | (
            {"computed": "outreach parent"} if self.computed else {}
        )

    def inward(self) -> dict[str, Any]:
        """As seen from its target: the inverse relation, pointing back at the source."""
        shown = {"rel": RELATIONS[self.rel].inverse, "id": self.source, "via": self.via, "at": self.at}
        return {k: v for k, v in shown.items() if v} | (
            {"computed": "outreach parent"} if self.computed else {}
        )


def item_links(item_id: str, item: Item) -> list[Link]:
    """The links stored on <item>, and the computed one an outreach parent stands for."""
    found = [
        Link(
            item_id, str(entry["rel"]), str(entry["id"]), str(entry.get("via", "")), str(entry.get("at", ""))
        )
        for entry in item.get("links") or []
    ]
    parent = (item.get("fields") or {}).get("parent") if item.get("kind") == OUTREACH_KIND else None
    if parent:
        found.append(Link(str(parent), BLOCKED_BY, item_id, "", str(item.get("created_at", "")), True))
    return found


@dataclass
class Index:
    """Every link of the ledger, by source and by target."""

    outgoing: dict[str, list[Link]]
    incoming: dict[str, list[Link]]
    ids: frozenset[str] = frozenset()

    @classmethod
    def of(cls, items: Items) -> Index:
        outgoing: dict[str, list[Link]] = {}
        incoming: dict[str, list[Link]] = {}
        for item_id, item in items.items():
            for link in item_links(item_id, item):
                outgoing.setdefault(link.source, []).append(link)
                incoming.setdefault(link.target, []).append(link)
        return cls(outgoing, incoming, frozenset(items))

    def out(self, item_id: str, rel: str | None = None) -> list[Link]:
        return [link for link in self.outgoing.get(item_id, []) if rel is None or link.rel == rel]

    def into(self, item_id: str, rel: str | None = None) -> list[Link]:
        return [link for link in self.incoming.get(item_id, []) if rel is None or link.rel == rel]

    def has(self, item_id: str) -> bool:
        """Whether the ledger the index was built from holds this item."""
        return item_id in self.ids


# ---- validation and change ---------------------------------------------------------------------


def check_relation(rel: str) -> Relation:
    if rel not in RELATIONS:
        names = ", ".join(RELATIONS)
        raise LineageError(
            f"unknown relation {rel!r}; one of: {names}. For the other direction, swap the ids "
            "(e.g. link '<task id>' produces '<mypr id>', not '<mypr id>' produced_by ...)"
        )
    return RELATIONS[rel]


def family_edge(link: Link) -> tuple[str, str]:
    """The link as an edge from the earlier item to the later one (ancestor -> descendant), so
    spawned_from and produces form one graph."""
    return (link.target, link.source) if link.rel == SPAWNED_FROM else (link.source, link.target)


def reaches(edges: Iterable[tuple[str, str]], start: str, goal: str) -> bool:
    following: dict[str, list[str]] = {}
    for a, b in edges:
        following.setdefault(a, []).append(b)
    seen, todo = {start}, [start]
    while todo:
        for nxt in following.get(todo.pop(), []):
            if nxt == goal:
                return True
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return False


def in_family(link: Link, family: str) -> bool:
    """An outreach parent (computed) counts in the lineage too: the tree shows the outreach under
    its parent, so nothing it leads to may produce or spawn that parent."""
    known = RELATIONS.get(link.rel)
    return bool(known and known.family == family) or (link.computed and family == LINEAGE)


def check_cycle(items: Items, new: Link) -> None:
    family = RELATIONS[new.rel].family
    if not family:
        return
    edges = [
        family_edge(link)
        for item_id, item in items.items()
        for link in item_links(item_id, item)
        if in_family(link, family)
    ]
    start, goal = family_edge(new)
    if reaches(edges, goal, start):
        raise LineageError(
            f"{new.source} {new.rel} {new.target} would close a cycle of {family} links "
            f"(`bosun ledger tree '{new.source}'` shows them)"
        )


def check_kinds(source: Item, rel: str, target: Item) -> None:
    """Tickets are reached only through tracks, and track nothing themselves."""
    if (rel == TRACKS) != (target.get("kind") == TICKET_KIND):
        what = "a ticket id (jira:<KEY>-<n>, ghissue:<owner>/<repo>#<n>)" if rel == TRACKS else "a work item"
        raise LineageError(f"{rel} takes {what}; {target['id']} is a {target.get('kind')}")
    if source.get("kind") == TICKET_KIND and rel != "relates_to":
        raise LineageError(
            f"{source['id']} is a ticket; link from the work item instead "
            f"(link '<item id>' {TRACKS} '{source['id']}')"
        )


def check_workstream_kinds(source: Item, rel: str, target: Item) -> None:
    """part_of points at a workstream; a workstream is linked only with part_of (to its parent
    workstream) and relates_to: it is a container, not work with a lineage of its own."""
    if rel == PART_OF and target.get("kind") != WORKSTREAM_KIND:
        raise LineageError(
            f"{PART_OF} takes a workstream id (ws:<slug>); {target['id']} is a {target.get('kind')}"
        )
    ends = [end for end in (source, target) if end.get("kind") == WORKSTREAM_KIND]
    if ends and rel not in WORKSTREAM_RELATIONS:
        raise LineageError(
            f"{ends[0]['id']} is a workstream; it takes only {' and '.join(WORKSTREAM_RELATIONS)} "
            f"(link '<item id>' {PART_OF} '{ends[0]['id']}')"
        )


def stored_part_of(item: Item) -> list[str]:
    """The workstream(s) an item is stored part_of (at most one, once validated)."""
    return [str(entry["id"]) for entry in item.get("links") or [] if entry.get("rel") == PART_OF]


def workstream_depth_above(items: Items, ws_id: str) -> int:
    """How many levels a workstream has above it, itself included (1: it has no parent)."""
    depth, seen, current = 1, {ws_id}, ws_id
    while True:
        parents = [p for p in stored_part_of(items.get(current) or {}) if p in items and p not in seen]
        if not parents:
            return depth
        current = parents[0]
        seen.add(current)
        depth += 1


def workstream_depth_below(items: Items, ws_id: str, seen: frozenset[str] = frozenset()) -> int:
    """How many levels a workstream holds, itself included (1: no child workstream)."""
    children = [
        item_id
        for item_id, item in items.items()
        if item.get("kind") == WORKSTREAM_KIND and ws_id in stored_part_of(item) and item_id not in seen
    ]
    below = (workstream_depth_below(items, child, seen | {ws_id}) for child in children)
    return 1 + max(below, default=0)


def check_part_of(items: Items, new: Link) -> None:
    """One workstream per item (no double counting; relates_to for a second one), and at most
    MAX_WORKSTREAM_DEPTH levels of workstreams."""
    current = [ws for ws in stored_part_of(items[new.source]) if ws != new.target]
    if current:
        raise LineageError(
            f"{new.source} is already {PART_OF} {current[0]}; an item belongs to one workstream. To move "
            f"it: bosun ledger unlink '{new.source}' {PART_OF} '{current[0]}', then link it again; for "
            f"a second, looser tie: link '{new.source}' {RELATES_TO} '{new.target}'"
        )
    if items[new.source].get("kind") != WORKSTREAM_KIND:
        return
    levels = workstream_depth_above(items, new.target) + workstream_depth_below(items, new.source)
    if levels > MAX_WORKSTREAM_DEPTH:
        raise LineageError(
            f"{new.source} {PART_OF} {new.target} would make {levels} levels of workstreams; at most "
            f"{MAX_WORKSTREAM_DEPTH} (a workstream and its parent)"
        )


def check_link(items: Items, new: Link) -> bool:
    """Refuse a link that is invalid; False if it exists already (nothing to do)."""
    check_relation(new.rel)
    if new.source == new.target:
        raise LineageError(f"{new.source} cannot be linked to itself")
    for end in (new.source, new.target):
        if end not in items:
            raise LineageError(f"no item {end!r}; links join items that exist (upsert or spawn it first)")
    check_kinds(items[new.source], new.rel, items[new.target])
    check_workstream_kinds(items[new.source], new.rel, items[new.target])
    if any(
        link.rel == new.rel and link.target == new.target
        for link in item_links(new.source, items[new.source])
    ):
        return False
    check_cycle(items, new)
    if new.rel == PART_OF:
        check_part_of(items, new)
    return True


def add_link(item: Item, new: Link) -> None:
    entry = {"rel": new.rel, "id": new.target, "at": new.at}
    if new.via:
        entry["via"] = new.via
    item.setdefault("links", []).append(entry)


def remove_link(item: Item, rel: str, target: str) -> bool:
    """Remove a stored link; False if there is none."""
    links = item.get("links") or []
    kept = [entry for entry in links if (entry["rel"], entry["id"]) != (rel, target)]
    if len(kept) == len(links):
        return False
    item["links"] = kept
    return True


def similar_tasks(items: Items, index: Index, source: str | None, title: str, is_open: IsOpen) -> list[Item]:
    """Open tasks spawned from the same source (or, without one, from none) with a similar title."""
    wanted = words(title)
    found = []
    for item in items.values():
        if item.get("kind") != TASK_KIND or not is_open(item):
            continue
        sources = [link.target for link in index.out(item["id"], SPAWNED_FROM)]
        same_source = source in sources if source else not sources
        same_title = item.get("title", "").strip().lower() == title.strip().lower()
        if same_source and (same_title or containment(words(item.get("title", "")), wanted) >= SIMILAR_TITLE):
            found.append(item)
    return found


# ---- reading -----------------------------------------------------------------------------------


def label(items: Items, item_id: str, delegated: Callable[[Item], str] | None = None) -> str:
    """One item, compactly: id [kind: state @window] title."""
    item = items.get(item_id)
    if item is None:
        return f"{item_id} (missing: pruned or never recorded)"
    window = delegated(item) if delegated else ""
    title = f" {item['title']}" if item.get("title") else ""
    return f"{item_id} [{item['kind']}: {item['state']}{window}]{title}"


def ups(index: Index, item_id: str) -> list[str]:
    """Where an item came from: what it was spawned from, what produced it, an outreach's parent."""
    found = [link.target for link in index.out(item_id, SPAWNED_FROM)]
    found += [link.source for link in index.into(item_id, PRODUCES)]
    found += [link.source for link in index.into(item_id, BLOCKED_BY) if link.computed]
    return list(dict.fromkeys(found))


def downs(index: Index, item_id: str) -> list[tuple[str, str]]:
    """What came from an item, with how: spawned, produces, outreach."""
    found = [(link.source, "spawned") for link in index.into(item_id, SPAWNED_FROM)]
    found += [(link.target, PRODUCES) for link in index.out(item_id, PRODUCES)]
    found += [(link.target, OUTREACH_KIND) for link in index.out(item_id, BLOCKED_BY) if link.computed]
    return found


def roots(index: Index, item_id: str) -> list[str]:
    """The topmost ancestors of <item_id> (itself if it has none)."""
    seen, todo, found = {item_id}, [item_id], []
    while todo:
        current = todo.pop()
        parents = [p for p in ups(index, current) if p not in seen]
        if not ups(index, current):
            found.append(current)
        seen.update(parents)
        todo.extend(parents)
    return found or [item_id]


def side_links(items: Items, index: Index, item_id: str) -> str:
    """Links outside the tree: blocked_by (stored), duplicate_of, folded_into, tracks, relates_to,
    both ways."""
    shown = [
        f"{PHRASES.get(link.rel, link.rel)} {link.target}{state_of(items, link.target)}"
        for link in index.out(item_id)
        if link.rel not in TREE_DOWN and not link.computed
    ]
    shown += [
        f"{RELATIONS[link.rel].inverse} {link.source}{state_of(items, link.source)}"
        for link in index.into(item_id)
        if link.rel not in TREE_DOWN and not link.computed
    ]
    return f"  | {'; '.join(shown)}" if shown else ""


def state_of(items: Items, item_id: str) -> str:
    item = items.get(item_id)
    return f" [{item['state']}]" if item else " [missing]"


@dataclass
class TreeView:
    items: Items
    index: Index
    focus: str
    delegated: Callable[[Item], str] | None = None
    shown: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        printed: set[str] = set()
        out: list[str] = []
        for root in roots(self.index, self.focus):
            self.walk(root, "", 0, printed, out)
        return out

    def walk(self, item_id: str, how: str, depth: int, printed: set[str], out: list[str]) -> None:
        mark = "*" if item_id == self.focus else "-"
        via = f" ({how})" if how else ""
        again = " (shown above)" if item_id in printed else ""
        out.append(
            f"{'  ' * depth}{mark} {label(self.items, item_id, self.delegated)}{via}{again}"
            f"{side_links(self.items, self.index, item_id) if not again else ''}"
        )
        if again:
            return
        printed.add(item_id)
        self.shown.append(item_id)
        for child, relation in downs(self.index, item_id):
            self.walk(child, relation, depth + 1, printed, out)


def list_hint(index: Index, item_id: str) -> str:
    """A short lineage hint for `ledger list`: ' | from <id>', ' | -> <id>', ' | tracks <id>',
    ' | folded into <id>', ' | absorbs <id>'."""
    parts = []
    for word, found in (
        ("from", ups(index, item_id)),
        ("->", [child for child, how in downs(index, item_id) if how != OUTREACH_KIND]),
        (TRACKS, [link.target for link in index.out(item_id, TRACKS)]),
        (PHRASES[FOLDED_INTO], [link.target for link in index.out(item_id, FOLDED_INTO)]),
        (RELATIONS[FOLDED_INTO].inverse, [link.source for link in index.into(item_id, FOLDED_INTO)]),
    ):
        if found:
            more = f" +{len(found) - MORE_SHOWN}" if len(found) > MORE_SHOWN else ""
            parts.append(f"{word} {', '.join(found[:MORE_SHOWN])}{more}")
    return "".join(f" | {part}" for part in parts)


def open_blockers(items: Items, index: Index, item_id: str, is_open: IsOpen) -> list[Item]:
    """Open items <item_id> is blocked_by through a stored link (outreach parents are separate)."""
    return [
        items[link.target]
        for link in index.out(item_id, BLOCKED_BY)
        if not link.computed and link.target in items and is_open(items[link.target])
    ]
