"""The console's Knowledge view (/api/knowledge, /api/knowledge/entity/<key>, /api/knowledge/review):
the knowledge base by axis, entity pages, the review batch, and the console's one kind of write, a
review decision. Design: docs/design/console.md (section 6) and docs/design/knowledge.md.

Pure over a knowledge_model.KB and a loaded ledger; lib/console_kb.py reads them (never the
restricted identifiers.jsonl) and saves a decision. What it shows:
- Entities are named by an opaque key (a hash of the id), never by a path or a raw id in the URL.
- Four cards: People, Process, Product and Systems (the system, service and environment types,
  which the schema files under Process and Product); a site's own types go by their axis.
- About a person only whitelisted predicates are ever shown (the schema refuses others; this is
  the second fence), and a person's page says how to have them deleted.
- Sources: http(s) references become links, ledger ids that exist link to their item page,
  anything else is text.
- The review batch is the one `kg batch` uses: the batch it saved today when that still has
  proposals waiting, else the batch it would print now (kr.build_groups, kr.select_batch), lettered
  like the CLI (1a, 1b, ...). A decision names fact ids of that batch; confirm and reject go through
  km.review, as `kg batch apply` does, recorded as reviewed by the user from the console.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, tzinfo
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import console_kg_graph as graph
import console_stats
import knowledge_model as km
import knowledge_query as kq
import knowledge_review as kr
import knowledge_schema as ks
from console_data import Item, QueryError, web_url
from knowledge_model import KB, Record


KEY_LENGTH = 16
SYSTEMS = "systems"
CARDS = ((ks.PEOPLE, "People"), (ks.PROCESS, "Process"), (ks.PRODUCT, "Product"), (SYSTEMS, "Systems"))
SYSTEM_TYPES = frozenset({"system", "service", "environment"})
AUTHORITY_LABELS = {
    ks.USER_SAID: "user",
    ks.STAKEHOLDER_SAID: "stakeholder",
    ks.OBSERVED: "observed",
    ks.INFERRED: "inferred",
}
SHOWN = (km.CONFIRMED, km.PROPOSED)  # statuses of the facts an entity page lists as current
RECENT_FACTS = 5
CARD_ENTITIES = 100
SEARCH_LIMIT = 50
MAX_QUERY = 200
RELATED_ITEMS = 20
CONTEXT_FACTS = 3  # facts already known about a reviewed fact's subject, shown with it
FORGET_NOTE = "Work-operational facts only. Delete on request: bosun kg forget {id}"
EMPTY_HINT = (
    "No knowledge base yet. Seed one with bosun kg import <file> (a dry run first, then "
    "--apply), or record a fact with bosun kg propose."
)
CONSOLE_REVIEWER: Record = {"kind": ks.USER_SOURCE, "ref": "console"}
# What a source is, in words; lib/console_kg_sources.py adds memory notes' titles and session names.
SOURCE_KIND_LABELS = {
    "memory": "Memory note",
    "transcript": "Session",
    "slack": "Slack",
    "ledger": "Ledger",
    "ticket": "Ticket",
    "pr": "Pull request",
    "doc": "Document",
    "report": "Session report",
    "profile": "Profile",
    ks.USER_SOURCE: "You said",
}
ISO_MINUTE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})[T ](\d{2}:\d{2})")
SESSION_ID_SHOWN = 8  # a session id is shown by its start, as references quote it
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# Facts that place a reviewed entity (it concerns X, is part of Y): its block's subtitle.
LINK_PREDICATES = kr.PARENT_PREDICATES
SAVED = "saved"
COMPUTED = "computed"


# ---- keys, references, facts --------------------------------------------------------------------


def entity_key(entity_id: str) -> str:
    """An entity's opaque key: a hash of its id."""
    return hashlib.sha256(f"knowledge\0{entity_id}".encode()).hexdigest()[:KEY_LENGTH]


def keys(kb: KB) -> dict[str, str]:
    """key -> entity id, for every entity."""
    return {entity_key(entity_id): entity_id for entity_id in kb.entities}


def card_of(kb: KB, entity_type: str) -> str:
    return SYSTEMS if entity_type in SYSTEM_TYPES else kb.schema.axis(entity_type)


def ref(kb: KB, entity_id: str | None) -> dict[str, Any] | None:
    """How a page names an entity: its key (None if it is not one: a dangling id), name and type."""
    if not entity_id:
        return None
    entity_type = ks.entity_type(entity_id)
    return {
        "key": entity_key(entity_id) if entity_id in kb.entities else None,
        "id": entity_id,
        "name": km.label(kb, entity_id),
        "type": entity_type,
        "card": card_of(kb, entity_type),
    }


def visible(kb: KB, fact: Record) -> bool:
    """Whether a fact may be shown: about a person, only whitelisted predicates."""
    return not ks.is_person(fact["subject"]) or fact["predicate"] in kb.schema.people_whitelist


def status(kb: KB, fact: Record) -> str:
    return km.effective_status(fact, kb.now)


def shown_current(kb: KB, fact: Record) -> bool:
    """Current (not closed) and confirmed or waiting for review."""
    return km.is_current(fact) and status(kb, fact) in SHOWN and visible(kb, fact)


def fact_number(fact: Record) -> int:
    digits = str(fact["id"])[1:]
    return int(digits) if digits.isdigit() else 0


def readable_times(text: str) -> str:
    """ISO minutes in a reference as a person writes them: 2026-10-18T18:56 -> Oct 18 18:56."""

    def one(match: re.Match[str]) -> str:
        month = int(match.group(2))
        name = MONTHS[month - 1] if 1 <= month <= len(MONTHS) else match.group(2)
        return f"{name} {int(match.group(3))} {match.group(4)}"

    return ISO_MINUTE.sub(one, text)


def source_label(kind: str, reference: str) -> str:
    """A source's reference in words: a memory note by its name, a ticket by its key, a session by
    its id and time (lib/console_kg_sources.py names it when it can)."""
    if kind == "memory":
        name = reference.rsplit("/", 1)[-1]
        return name.removesuffix(".md").replace("-", " ").replace("_", " ")
    if kind == "ticket":
        return reference.split(":", 1)[-1]
    if kind == "transcript":
        session, _, rest = reference.partition(" ")
        return f"session {session[:SESSION_ID_SHOWN]}" + (f", {readable_times(rest)}" if rest else "")
    return readable_times(reference)


def source_view(source: Mapping[str, Any], ledger_ids: frozenset[str]) -> dict[str, Any]:
    """One source: a web link if it is an http(s) URL, an item link if it is a ledger id we hold,
    else text, with what it is in words. A quote of my words is shown with whether it was
    verified."""
    kind, reference = str(source.get("kind", "")), str(source.get("ref", ""))
    return {
        "kind": kind,
        "kind_label": SOURCE_KIND_LABELS.get(kind, kind),
        "label": source_label(kind, reference),
        "ref": reference,
        "trusted": ks.trusted(kind),
        "url": web_url(reference),
        "item": reference if kind == "ledger" and reference in ledger_ids else None,
        "quote": str(source["quote"]) if kind == ks.USER_SOURCE and source.get("quote") else None,
        "verified": bool(source.get("verified")) if kind == ks.USER_SOURCE else None,
    }


def replaced_by(kb: KB, fact: Record) -> dict[str, Any] | None:
    other = kb.facts.get(str(fact.get("superseded_by") or ""))
    if other is None:
        return None
    return {
        "id": other["id"],
        "text": kr.statement(kb, other, limit=None),
        "sentence": sentence_view(kb, other),
        "status": status(kb, other),
    }


def changed_at(fact: Record) -> str:
    """When the fact last changed: its newest history entry."""
    return max((str(h.get("at") or "") for h in fact.get("history") or []), default="")


def sentence_view(kb: KB, fact: Record) -> list[dict[str, Any]]:
    """What the fact says after its subject (kr.sentence_parts): words, entity links, its value."""
    return [
        {"entity": ref(kb, text)} if kind == kr.ENTITY else {kind: text}
        for kind, text in kr.sentence_parts(kb, fact)
    ]


def fact_view(kb: KB, fact: Record, ledger_ids: frozenset[str]) -> dict[str, Any]:
    authority = str(fact.get("authority") or ks.OBSERVED)
    return {
        "id": fact["id"],
        "subject": ref(kb, fact["subject"]),
        "predicate": fact["predicate"],
        "object": ref(kb, fact.get("object")),
        "value": fact.get("value"),
        "order": fact.get("order"),
        "text": kr.statement(kb, fact, limit=None),
        "sentence": sentence_view(kb, fact),
        "authority": authority,
        "authority_label": AUTHORITY_LABELS.get(authority, authority),
        "status": status(kb, fact),
        "current": km.is_current(fact),
        "observed_at": fact.get("observed_at"),
        "valid_from": fact.get("valid_from"),
        "valid_to": fact.get("valid_to"),
        "changed_at": changed_at(fact),
        "replaced_by": replaced_by(kb, fact),
        "last_confirmed_at": fact.get("last_confirmed_at"),
        "reviewed_by": fact.get("reviewed_by"),
        "prevents_rework": bool(fact.get("prevents_rework")),
        "observations": int(fact.get("observations") or 1),
        "sources": [source_view(s, ledger_ids) for s in fact.get("sources") or []],
    }


def ledger_ids_of(ledger: Mapping[str, Any]) -> frozenset[str]:
    items = ledger.get("items")
    return frozenset(items) if isinstance(items, dict) else frozenset()


# ---- the overview -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Search:
    """The overview's search: text (entities by name and alias, facts by text) and a type."""

    q: str = ""
    type: str = ""

    @classmethod
    def parse(cls, params: Mapping[str, list[str]]) -> Search:
        values = {name: params.get(name) or [""] for name in ("q", "type")}
        if any(len(v) > 1 for v in values.values()):
            raise QueryError("q and type take one value each")
        q, entity_type = values["q"][0].strip(), values["type"][0].strip()
        if len(q) > MAX_QUERY:
            raise QueryError(f"q takes at most {MAX_QUERY} characters")
        return cls(q, entity_type)

    def check(self, kb: KB) -> None:
        if self.type and self.type not in kb.schema.entity_types:
            raise QueryError(f"type must be one of {', '.join(sorted(kb.schema.entity_types))}")

    @property
    def active(self) -> bool:
        return bool(self.q or self.type)


def counts_by_entity(kb: KB) -> dict[str, Counter[str]]:
    """entity id -> confirmed and proposed counts of its current facts (as subject)."""
    found: dict[str, Counter[str]] = {}
    for fact in kb.facts.values():
        if shown_current(kb, fact):
            found.setdefault(fact["subject"], Counter())[status(kb, fact)] += 1
    return found


def entity_row(kb: KB, entity: Record, counts: Mapping[str, Counter[str]]) -> dict[str, Any]:
    tally = counts.get(entity["id"], Counter())
    return {
        **(ref(kb, entity["id"]) or {}),
        "confirmed": tally[km.CONFIRMED],
        "proposed": tally[km.PROPOSED],
    }


def by_name(entities: Iterable[Record]) -> list[Record]:
    return sorted(entities, key=lambda e: (km.norm(e.get("name") or e["id"]), e["id"]))


def recent_facts(kb: KB, facts: list[Record], ledger_ids: frozenset[str]) -> list[dict[str, Any]]:
    newest = sorted(facts, key=lambda f: (changed_at(f), fact_number(f)), reverse=True)
    return [fact_view(kb, f, ledger_ids) for f in newest[:RECENT_FACTS]]


def card_model(
    kb: KB, card: tuple[str, str], counts: Mapping[str, Counter[str]], ledger_ids: frozenset[str]
) -> dict[str, Any]:
    name, title = card
    entities = by_name(e for e in kb.entities.values() if card_of(kb, e["type"]) == name)
    members = {e["id"] for e in entities}
    facts = [f for f in kb.facts.values() if f["subject"] in members and visible(kb, f)]
    current = Counter(status(kb, f) for f in facts if shown_current(kb, f))
    return {
        "name": name,
        "title": title,
        "entities": len(entities),
        "confirmed": current[km.CONFIRMED],
        "proposed": current[km.PROPOSED],
        "list": [entity_row(kb, e, counts) for e in entities[:CARD_ENTITIES]],
        "more": max(0, len(entities) - CARD_ENTITIES),
        "recent": recent_facts(kb, facts, ledger_ids),
    }


def matching_entities(kb: KB, search: Search) -> list[tuple[str, str]]:
    """(entity id, why) for the search: by name, alias, related term or fact text, best first."""
    if search.q:
        hits = [(entity_id, why) for _, entity_id, why in kq.search(kb, search.q, SEARCH_LIMIT * 2)]
    else:
        hits = [(e["id"], "type") for e in by_name(kb.entities.values())]
    found = [(i, why) for i, why in hits if i in kb.entities]
    return [(i, why) for i, why in found if not search.type or ks.entity_type(i) == search.type]


def matching_facts(kb: KB, search: Search) -> list[Record]:
    if not search.q:
        return []
    wanted = km.norm(search.q)
    facts = [
        f
        for f in kb.facts.values()
        if shown_current(kb, f)
        and wanted in km.norm(f.get("value") or "")
        and (not search.type or ks.entity_type(f["subject"]) == search.type)
    ]
    return sorted(facts, key=fact_number)


def search_model(
    kb: KB, search: Search, counts: Mapping[str, Counter[str]], ledger_ids: frozenset[str]
) -> dict[str, Any] | None:
    if not search.active:
        return None
    entities = matching_entities(kb, search)
    facts = matching_facts(kb, search)
    return {
        "q": search.q,
        "type": search.type,
        "entities": [
            {**entity_row(kb, kb.entities[i], counts), "why": why} for i, why in entities[:SEARCH_LIMIT]
        ],
        "facts": [fact_view(kb, f, ledger_ids) for f in facts[:SEARCH_LIMIT]],
        "more": max(0, len(entities) - SEARCH_LIMIT) + max(0, len(facts) - SEARCH_LIMIT),
    }


def overview_model(
    kb: KB, ledger: Mapping[str, Any], search: Search, review: Mapping[str, Any]
) -> dict[str, Any]:
    """/api/knowledge: the four cards, the search's results, and the review batch's summary."""
    search.check(kb)
    ledger_ids = ledger_ids_of(ledger)
    counts = counts_by_entity(kb)
    statuses = Counter(status(kb, f) for f in kb.facts.values() if visible(kb, f))
    return {
        "exists": True,
        "error": None,
        "hint": None,
        "totals": {"entities": len(kb.entities), "facts": sum(statuses.values()), **dict(statuses)},
        "cards": [card_model(kb, card, counts, ledger_ids) for card in CARDS],
        "types": sorted(kb.schema.entity_types),
        "search": search_model(kb, search, counts, ledger_ids),
        "review": review["summary"],
    }


def empty_overview(error: str | None = None) -> dict[str, Any]:
    """/api/knowledge when there is no knowledge base (or it cannot be read: <error>)."""
    return {
        "exists": False,
        "error": error,
        "hint": None if error else EMPTY_HINT,
        "totals": {"entities": 0, "facts": 0},
        "cards": [],
        "types": [],
        "search": None,
        "review": None,
    }


# ---- an entity's page ---------------------------------------------------------------------------


def predicate_rank(kb: KB) -> dict[str, int]:
    return {name: index for index, name in enumerate(kb.schema.predicates)}


def fact_sort(fact: Record) -> tuple[int, int]:
    return (int(fact.get("order") or 0), fact_number(fact))


def grouped(kb: KB, facts: list[Record], ledger_ids: frozenset[str]) -> list[dict[str, Any]]:
    """Facts grouped by predicate (in the schema's order), each group in step order, then by id."""
    rank = predicate_rank(kb)
    names = sorted({f["predicate"] for f in facts}, key=lambda n: (rank.get(n, len(rank)), n))
    groups = []
    for name in names:
        predicate = kb.schema.predicates.get(name)
        members = sorted((f for f in facts if f["predicate"] == name), key=fact_sort)
        groups.append(
            {
                "predicate": name,
                "description": predicate.description if predicate else "",
                "single": bool(predicate and predicate.single),
                "facts": [fact_view(kb, f, ledger_ids) for f in members],
            }
        )
    return groups


def history_facts(kb: KB, entity_id: str) -> list[Record]:
    """Facts about the entity that no longer hold or were turned down: closed (superseded or
    stopped), rejected or expired; newest change first."""
    facts = [
        f
        for f in kb.facts.values()
        if f["subject"] == entity_id and visible(kb, f) and not shown_current(kb, f)
    ]
    return sorted(facts, key=lambda f: (changed_at(f), fact_number(f)), reverse=True)


def backlinks(kb: KB, entity_id: str, ledger_ids: frozenset[str]) -> list[dict[str, Any]]:
    """Current facts of other entities that point here."""
    facts = [
        f
        for f in kb.facts.values()
        if f.get("object") == entity_id and f["subject"] != entity_id and shown_current(kb, f)
    ]
    return [fact_view(kb, f, ledger_ids) for f in sorted(facts, key=fact_number)]


def header(kb: KB, entity: Record) -> dict[str, Any]:
    aliases = entity.get("aliases") or {}
    person = entity["type"] == ks.PERSON
    return {
        **(ref(kb, entity["id"]) or {}),
        "axis": kb.schema.axis(entity["type"]),
        "aliases": {"strong": list(aliases.get("strong") or []), "weak": list(aliases.get("weak") or [])},
        "summary": "" if person else str(entity.get("summary") or ""),
        "person": person,
        "created_at": entity.get("created_at"),
        "updated_at": entity.get("updated_at"),
    }


def entity_model(kb: KB, entity_id: str, ledger: Mapping[str, Any]) -> dict[str, Any]:
    """/api/knowledge/entity/<key>: the header, current facts by predicate, history, backlinks,
    related ledger items, a process's flow and the neighbourhood graph."""
    entity = kb.entities[entity_id]
    ledger_ids = ledger_ids_of(ledger)
    current = [f for f in kb.facts.values() if f["subject"] == entity_id and shown_current(kb, f)]
    person = entity["type"] == ks.PERSON
    return {
        "entity": header(kb, entity),
        "privacy": FORGET_NOTE.format(id=entity_id) if person else None,
        "facts": grouped(kb, current, ledger_ids),
        "history": [fact_view(kb, f, ledger_ids) for f in history_facts(kb, entity_id)],
        "backlinks": backlinks(kb, entity_id, ledger_ids),
        "related": related_items(kb, entity, ledger),
        "flow": flow_model(kb, entity_id),
        "graph": graph_model(kb, entity_id),
    }


# ---- a process's flow ---------------------------------------------------------------------------

STEP_LINKS = {"actor": "actors", "gated_by": "gates", "uses": "systems", "posts_to": "channels"}
STEP_NOTES = ("rule", "caveat")
PROCESS = "process"


def linked_facts(kb: KB, entity_id: str, predicate: str) -> list[Record]:
    """An entity's current <predicate> facts, in step order (unordered last), then by id."""
    facts = [
        f
        for f in kb.facts.values()
        if f["subject"] == entity_id and f["predicate"] == predicate and shown_current(kb, f)
    ]
    return sorted(facts, key=lambda f: (f.get("order") is None, int(f.get("order") or 0), fact_number(f)))


def linked(kb: KB, entity_id: str, predicate: str) -> list[dict[str, Any]]:
    """The objects or values of those facts, with each fact's status."""
    return [
        {"ref": ref(kb, f.get("object")), "text": f.get("value"), "status": status(kb, f), "fact": f["id"]}
        for f in linked_facts(kb, entity_id, predicate)
    ]


def step_details(kb: KB, entity_id: str) -> dict[str, Any]:
    details = {name: linked(kb, entity_id, predicate) for predicate, name in STEP_LINKS.items()}
    details["entry_points"] = linked(kb, entity_id, "entry_point")
    details["notes"] = [note for predicate in STEP_NOTES for note in linked(kb, entity_id, predicate)]
    return details


def flow_model(kb: KB, entity_id: str) -> dict[str, Any] | None:
    """A process's steps in order (has_step's order, then the fact id), each with its actors,
    gates, systems, channels and entry points; None unless the entity is a process with steps."""
    if ks.entity_type(entity_id) != PROCESS:
        return None
    steps = [f for f in linked_facts(kb, entity_id, "has_step") if f.get("object")]
    if not steps:
        return None
    return {
        "process": step_details(kb, entity_id),
        "steps": [
            {
                "number": number,
                "order": fact.get("order"),
                "fact": fact["id"],
                "status": status(kb, fact),
                "step": ref(kb, fact["object"]),
                **step_details(kb, fact["object"]),
            }
            for number, fact in enumerate(steps, start=1)
        ],
    }


# ---- the neighbourhood graph --------------------------------------------------------------------


def graph_links(kb: KB) -> list[graph.Link]:
    """Every current link between two entities (confirmed, or waiting for review), in fact order."""
    return [
        graph.Link(f["id"], f["subject"], f["object"], f["predicate"], status(kb, f) == km.CONFIRMED)
        for f in sorted(kb.facts.values(), key=fact_number)
        if f.get("object") and shown_current(kb, f)
    ]


def graph_view(kb: KB, ego: graph.Ego) -> dict[str, Any]:
    """A laid-out ego graph as the page draws it: nodes named like refs, edges by node index."""
    nodes = [
        {
            **(ref(kb, node.id) or {}),
            "label": graph.short_label(km.label(kb, node.id)),
            "depth": node.depth,
            "x": node.x,
            "y": node.y,
        }
        for node in ego.nodes
    ]
    edges = [
        {"from": e.source, "to": e.target, "predicates": list(e.predicates), "confirmed": e.confirmed}
        for e in ego.edges
    ]
    return {"width": graph.WIDTH, "height": graph.HEIGHT, "nodes": nodes, "edges": edges, "more": ego.more}


def graph_model(kb: KB, entity_id: str) -> dict[str, Any]:
    """The 1-2 hop ego graph over current links (confirmed, or waiting: drawn dashed), laid out by
    lib/console_kg_graph.py; nodes link to their pages, at most graph.MAX_NODES of them."""
    return graph_view(kb, graph.ego(entity_id, graph_links(kb)))


# ---- related ledger items -----------------------------------------------------------------------

ITEM_REPO = re.compile(r"^(?:mypr|review|ghissue|td):([^#\s]+/[^#\s]+)#")
URL_REPO = re.compile(r"^https?://[^/\s]+/([^/\s]+/[^/\s]+)/(?:pull|issues)/", re.IGNORECASE)


def person_names(kb: KB, entity: Record) -> set[str]:
    """What the ledger may call a person: name, slug, strong aliases, and the first name when no
    other person shares it."""
    found = {km.norm(n) for n in km.names_of(entity)[1:]}
    first = km.norm(entity.get("name") or "").split()
    if first:
        others = [
            e
            for e in kb.entities.values()
            if e["type"] == ks.PERSON
            and e["id"] != entity["id"]
            and km.norm(e.get("name") or "").split()[:1] == first[:1]
        ]
        if not others:
            found.add(first[0])
    return found


def person_roles(item: Item) -> list[tuple[str, str]]:
    """(name, how) for the people a ledger item names: requesters and outreach recipients."""
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    delegation = item.get("delegation") if isinstance(item.get("delegation"), dict) else {}
    found = []
    for value, how in (
        ((fields or {}).get("requester"), "requester"),
        ((delegation or {}).get("requester"), "requester"),
        ((fields or {}).get("to"), "recipient"),
    ):
        found += [(km.norm(name), how) for name in console_stats.names(value)]
    return found


def item_repos(item: Item) -> set[str]:
    """The owner/repo names a ledger item is about: its repo field, its id, its URL."""
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    found = {str((fields or {}).get("repo") or "")}
    for pattern, text in ((ITEM_REPO, str(item.get("id") or "")), (URL_REPO, str(item.get("url") or ""))):
        match = pattern.match(text)
        if match:
            found.add(match.group(1))
    return {km.norm(r) for r in found if r}


def repo_match(names: set[str], repos: set[str]) -> bool:
    return any(r in names or r.rpartition("/")[2] in names for r in repos)


def matched_how(kb: KB, entity: Record, item: Item, names: set[str]) -> str:
    if entity["type"] == ks.PERSON:
        return next((how for name, how in person_roles(item) if name in names), "")
    return "repo" if repo_match(names, item_repos(item)) else ""


def related_items(kb: KB, entity: Record, ledger: Mapping[str, Any]) -> dict[str, Any] | None:
    """Ledger items a person asked for or was reached out to, or about a repo; newest first."""
    if entity["type"] not in (ks.PERSON, "repo"):
        return None
    names = (
        person_names(kb, entity)
        if entity["type"] == ks.PERSON
        else {km.norm(n) for n in km.names_of(entity)[1:]}
    )
    items = ledger.get("items") if isinstance(ledger.get("items"), dict) else {}
    rows = []
    for item in (items or {}).values():
        how = matched_how(kb, entity, item, names) if isinstance(item, dict) else ""
        if how:
            rows.append(
                {
                    "id": item.get("id"),
                    "kind": item.get("kind", ""),
                    "state": item.get("state", ""),
                    "title": item.get("title", ""),
                    "updated": item.get("updated_at"),
                    "how": how,
                }
            )
    rows.sort(key=lambda r: (str(r["updated"] or ""), str(r["id"])), reverse=True)
    return {"items": rows[:RELATED_ITEMS], "total": len(rows)}


# ---- the review batch ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchGroup:
    key: str
    title: str
    fact_ids: tuple[str, ...]


def local_date(value: Any, zone: tzinfo) -> date | None:
    try:
        return km.parse_time(str(value)).astimezone(zone).date()
    except km.KnowledgeError:
        return None


def todays(saved: Any, today: date, zone: tzinfo) -> bool:
    """Whether <saved> is a batch made today and not answered yet."""
    return (
        isinstance(saved, dict)
        and not saved.get("applied_at")
        and isinstance(saved.get("groups"), list)
        and local_date(saved.get("created_at"), zone) == today
    )


def saved_group(group: Mapping[str, Any]) -> BatchGroup:
    facts = group.get("facts")
    ids = tuple(str(i) for i in facts) if isinstance(facts, list) else ()
    return BatchGroup(str(group.get("key", "")), str(group.get("title", "")), ids)


def usable_saved(kb: KB, saved: Any, today: date, zone: tzinfo) -> list[BatchGroup] | None:
    """The batch `kg batch` saved, if it is today's, unanswered, names only known facts and still
    has a proposal waiting."""
    if not todays(saved, today, zone):
        return None
    groups = [saved_group(g) for g in saved["groups"] if isinstance(g, dict)]
    ids = {i for g in groups for i in g.fact_ids}
    known = bool(ids) and ids <= set(kb.facts)
    return groups if known and ids & {f["id"] for f in kr.waiting(kb)} else None


def chosen_batch(
    kb: KB, saved: Any, limits: kr.Limits, today: date, zone: tzinfo
) -> tuple[list[BatchGroup], str, list[kr.Group]]:
    """Today's batch (saved or computed now), where it came from, and every waiting group."""
    every = kr.build_groups(kb, limits.group_facts)
    kept = usable_saved(kb, saved, today, zone)
    if kept is not None:
        return kept, SAVED, every
    chosen = kr.select_batch(every, limits)
    return [BatchGroup(g.key, g.title, tuple(g.fact_ids)) for g in chosen], COMPUTED, every


def context_facts(kb: KB, fact: Record) -> list[dict[str, Any]]:
    """What is already known about the fact's subject, to judge it in context: up to
    CONTEXT_FACTS other current facts, confirmed ones first."""
    others = [
        f
        for f in kb.facts.values()
        if f["subject"] == fact["subject"] and f["id"] != fact["id"] and shown_current(kb, f)
    ]
    others.sort(key=lambda f: (status(kb, f) != km.CONFIRMED, fact_number(f)))
    return [known_view(kb, f) for f in others[:CONTEXT_FACTS]]


def known_view(kb: KB, fact: Record) -> dict[str, Any]:
    return {
        "id": fact["id"],
        "text": kr.statement(kb, fact, limit=None),
        "sentence": sentence_view(kb, fact),
        "status": status(kb, fact),
    }


def already_known(kb: KB, subject: str, shown: set[str]) -> list[dict[str, Any]]:
    """What is already known about a reviewed entity, apart from the facts the batch shows: up to
    CONTEXT_FACTS current facts, confirmed ones first."""
    others = [
        f
        for f in kb.facts.values()
        if f["subject"] == subject and f["id"] not in shown and shown_current(kb, f)
    ]
    others.sort(key=lambda f: (status(kb, f) != km.CONFIRMED, fact_number(f)))
    return [known_view(kb, f) for f in others[:CONTEXT_FACTS]]


def block_head(kb: KB, subject: str) -> dict[str, Any]:
    """A block's heading: the entity, its type in words and its summary."""
    entity = kb.entities.get(subject) or {}
    return {
        **(ref(kb, subject) or {}),
        "type_label": ks.entity_type(subject).replace("_", " "),
        "summary": str(entity.get("summary") or ""),
    }


def review_blocks(kb: KB, views: list[dict[str, Any]], shown: set[str]) -> list[dict[str, Any]]:
    """A group's facts by entity, in the order they come: each block names the entity, the facts
    that place it (LINK_PREDICATES: its subtitle, unless they are all it has), the facts to judge
    (by ref), and what is already known about it."""
    blocks: dict[str, dict[str, Any]] = {}
    for view in views:
        block = blocks.setdefault(view["subject"]["id"], {"links": [], "facts": []})
        placing = view["predicate"] in LINK_PREDICATES and view["object"] is not None
        block["links" if placing else "facts"].append(view["ref"])
    return [
        {
            "subject": block_head(kb, subject),
            "links": block["links"] if block["facts"] else [],
            "facts": block["facts"] or block["links"],
            "known": already_known(kb, subject, shown),
        }
        for subject, block in blocks.items()
    ]


def review_fact(kb: KB, group: BatchGroup, fact: Record, ledger_ids: frozenset[str]) -> dict[str, Any]:
    return {
        "decidable": km.reviewable(kb, fact) and status(kb, fact) == km.PROPOSED,
        "same_subject": fact["subject"] == group.key,
        **fact_view(kb, fact, ledger_ids),
        "context": context_facts(kb, fact),
    }


def review_group(kb: KB, number: int, group: BatchGroup, ledger_ids: frozenset[str]) -> dict[str, Any]:
    """One group, its facts lettered as `kg batch` prints them (1a, 1b, ...)."""
    facts = [kb.facts[i] for i in group.fact_ids if i in kb.facts and visible(kb, kb.facts[i])]
    views = [
        {"ref": f"{number}{letter}", "letter": letter, **review_fact(kb, group, fact, ledger_ids)}
        for letter, fact in zip(kr.LETTERS, facts, strict=False)
    ]
    return {
        "number": number,
        "key": group.key,
        "title": group.title,
        "axis": kr.axis_of_key(kb, group.key),
        "rework": any(f.get("prevents_rework") for f in facts),
        "facts": views,
        "blocks": review_blocks(kb, views, {f["id"] for f in facts}),
        "waiting": sum(1 for v in views if v["decidable"]),
    }


def review_model(
    kb: KB, saved: Any, limits: kr.Limits, ledger: Mapping[str, Any], zone: tzinfo
) -> dict[str, Any]:
    """/api/knowledge/review: today's batch, its groups and lettered facts, and a summary."""
    today = kb.now.astimezone(zone).date()
    batch, source, every = chosen_batch(kb, saved, limits, today, zone)
    ledger_ids = ledger_ids_of(ledger)
    groups = [review_group(kb, n, g, ledger_ids) for n, g in enumerate(batch, start=1)]
    in_batch = {i for g in batch for i in g.fact_ids}
    later = [g for g in every if not in_batch & set(g.fact_ids)]
    summary = {
        "source": source,
        "created_at": saved["created_at"] if source == SAVED else None,
        **batch_counts(groups),
        "later_groups": len(later),
        "later_facts": sum(len(g.fact_ids) for g in later),
    }
    return {"exists": True, "error": None, "hint": None, "summary": summary, "groups": groups}


def batch_counts(groups: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "groups": len(groups),
        "facts": sum(len(g["facts"]) for g in groups),
        "waiting": sum(g["waiting"] for g in groups),
    }


def empty_review(error: str | None = None) -> dict[str, Any]:
    return {
        "exists": False,
        "error": error,
        "hint": None if error else EMPTY_HINT,
        "summary": None,
        "groups": [],
    }


def batch_answered(kb: KB, saved: Any) -> bool:
    """Whether every fact of the saved batch is decided (none still waits): then the console marks
    it answered, as `kg batch apply` does. A deferred fact still waits, so it keeps the batch open."""
    if not isinstance(saved, dict) or not isinstance(saved.get("groups"), list):
        return False
    ids = {i for g in saved["groups"] if isinstance(g, dict) for i in saved_group(g).fact_ids}
    return bool(ids) and not ids & {f["id"] for f in kr.waiting(kb)}


def batch_fact_ids(model: Mapping[str, Any]) -> set[str]:
    """The fact ids a review model shows that still wait for a decision."""
    return {f["id"] for g in model["groups"] for f in g["facts"] if f["decidable"]}


# ---- decisions ----------------------------------------------------------------------------------

ACTIONS = {"confirm": kr.CONFIRM, "reject": kr.REJECT, "defer": kr.SKIP}
FACT_ID = re.compile(r"^f\d{1,9}$")
MAX_DECISION_FACTS = 200


class ReviewError(Exception):
    """A decision the console refuses, with the HTTP status to answer."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Decision:
    """One review decision from the console: confirm, reject or defer some facts of the batch."""

    action: str
    facts: tuple[str, ...]

    @classmethod
    def parse(cls, body: Any) -> Decision:
        if not isinstance(body, dict) or set(body) != {"action", "facts"}:
            raise QueryError('a decision is {"action": "confirm"|"reject"|"defer", "facts": ["f1", ...]}')
        action, facts = body["action"], body["facts"]
        if action not in ACTIONS:
            raise QueryError(f"action must be one of {', '.join(ACTIONS)}")
        if not isinstance(facts, list) or not 1 <= len(facts) <= MAX_DECISION_FACTS:
            raise QueryError(f"facts must list 1 to {MAX_DECISION_FACTS} fact ids")
        if not all(isinstance(f, str) and FACT_ID.match(f) for f in facts):
            raise QueryError("fact ids look like f12")
        return cls(str(action), tuple(dict.fromkeys(facts)))


def apply_decision(kb: KB, decision: Decision, batch_ids: set[str]) -> dict[str, Any]:
    """Apply a decision to facts of today's batch, as `kg batch apply` does (km.review), recorded
    as reviewed by the user from the console; defer changes nothing (the facts stay proposed)."""
    outside = [f for f in decision.facts if f not in batch_ids]
    if outside:
        raise ReviewError(409, f"not waiting in today's batch: {', '.join(outside)}; reload the review")
    verb = ACTIONS[decision.action]
    ids = list(decision.facts)
    messages = (
        [f"deferred: {i} stays proposed" for i in ids]
        if verb == kr.SKIP
        else km.review(kb, ids, verb == kr.CONFIRM, "console", by=CONSOLE_REVIEWER)
    )
    after = Counter(status(kb, kb.facts[i]) for i in ids)
    return {
        "action": decision.action,
        "facts": ids,
        "confirmed": after[km.CONFIRMED],
        "rejected": after[km.REJECTED],
        "left_proposed": after[km.PROPOSED],
        "messages": messages,
        "at": km.iso(kb.now),
    }
