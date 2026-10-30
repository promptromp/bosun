"""Reading the knowledge base (design: docs/design/knowledge.md, "How it is used"): lookup,
facts with --as-of (valid time), search over names, aliases and text, neighbours over entity links,
and context packs: at most about 1.5k tokens of confirmed, current, cited facts for an entity, a
repo or a topic. Identifiers (restricted) are never shown here.

Pure over a knowledge_model.KB.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

import knowledge_model as km
import knowledge_review as kr
from knowledge_model import KB, Record


DEFAULT_PACK_TOKENS = 1500
CHARS_PER_TOKEN = 4  # a rough estimate, enough to keep a pack small
MAX_HOPS = 3
DEFAULT_SEARCH_LIMIT = 10
PACK_TOPIC_ENTITIES = 3
MORE_LINE_RESERVE = 80  # room for the "(N more: ...)" line
# Context packs try these types when --for names neither an entity nor an alias.
PACK_TYPES = ("repo", "service", "process", "feature", "system", "channel", "team")


def fact_line(kb: KB, fact: Record, *, sources: bool = True, status: bool = False) -> str:
    """One fact: id, subject, predicate, object or value, and where it came from."""
    line = f"{fact['id']} {km.label(kb, fact['subject'])} {kr.statement(kb, fact)}"
    if status:
        state = km.effective_status(fact, kb.now)
        if fact.get("valid_to"):
            closed = f"closed {fact['valid_to'][:10]}"
            state += f", {closed}" + (f" -> {fact['superseded_by']}" if fact.get("superseded_by") else "")
        line += f" ({state}, {fact['authority']}, since {str(fact.get('valid_from') or '')[:10]})"
    if sources:
        line += f" [{kr.sources_text(fact, limit=3)}]"
    return line


def by_order(facts: list[Record]) -> list[Record]:
    return sorted(facts, key=lambda f: (f["predicate"], f.get("order") or 0, int(f["id"][1:])))


def outgoing(kb: KB, entity_id: str, at: datetime | None = None) -> list[Record]:
    return by_order([f for f in km.confirmed_current(kb, at) if f["subject"] == entity_id])


def incoming(kb: KB, entity_id: str, at: datetime | None = None) -> list[Record]:
    return by_order([f for f in km.confirmed_current(kb, at) if f.get("object") == entity_id])


def entity_header(kb: KB, entity: Record) -> list[str]:
    lines = [f"{entity['id']}: {entity['name']} ({entity['type']}; {kb.schema.axis(entity['type'])} axis)"]
    if entity["aliases"]["strong"] or entity["aliases"]["weak"]:
        strong = ", ".join(entity["aliases"]["strong"]) or "-"
        weak = ", ".join(entity["aliases"]["weak"]) or "-"
        lines.append(f"  aliases: {strong}; related: {weak}")
    if entity.get("summary"):
        lines.append(f"  {entity['summary']}")
    return lines


def lookup(kb: KB, ref: str) -> str:
    """An entity's current confirmed facts with sources, what links to it, and what waits."""
    entity = kb.entities[km.resolve(kb, ref)]
    entity_id = entity["id"]
    lines = entity_header(kb, entity)
    own, links = outgoing(kb, entity_id), incoming(kb, entity_id)
    lines.append("facts:" if own else "facts: none confirmed")
    lines += [f"  {fact_line(kb, f)}" for f in own]
    if links:
        lines.append("linked from:")
        lines += [f"  {fact_line(kb, f)}" for f in links]
    pending = [f for f in kr.waiting(kb) if entity_id in (f["subject"], f.get("object"))]
    if pending:
        lines.append(f"unconfirmed ({len(pending)}, not used in context packs):")
        lines += [f"  {fact_line(kb, f)}" for f in by_order(pending)]
    return "\n".join(lines)


@dataclass(frozen=True)
class FactQuery:
    ref: str
    predicate: str | None = None
    as_of: datetime | None = None
    status: str = km.CONFIRMED  # or "all"
    closed: bool = False  # also facts no longer current


def select_facts(kb: KB, query: FactQuery) -> list[Record]:
    """Facts about an entity (as subject): by default the confirmed ones holding now, or at
    --as-of (valid time); --status all also shows proposed, rejected and expired ones."""
    entity_id = km.resolve(kb, query.ref)
    found = []
    for fact in kb.facts.values():
        if fact["subject"] != entity_id or (query.predicate and fact["predicate"] != query.predicate):
            continue
        if query.status != "all" and km.effective_status(fact, kb.now) != query.status:
            continue
        if query.closed or km.is_current(fact, query.as_of):
            found.append(fact)
    return by_order(found)


# ---- search and neighbours ----------------------------------------------------------------------

SCORE_EXACT = 100
SCORE_ID = 90
SCORE_PREFIX = 70
SCORE_NAME = 60
SCORE_RELATED = 50
SCORE_SUMMARY = 30
SCORE_FACT = 20


def entity_score(entity: Record, wanted: str) -> tuple[int, str]:
    names = [km.norm(n) for n in km.names_of(entity)[2:]]
    ids = [km.norm(entity["id"]), km.norm(entity["id"].partition("/")[2])]
    weak = [km.norm(n) for n in entity["aliases"]["weak"]]
    checks = (
        (wanted in names, SCORE_EXACT, "name or alias"),
        (wanted in ids, SCORE_ID, "id"),
        (any(n.startswith(wanted) for n in names), SCORE_PREFIX, "name"),
        (any(wanted in n for n in names + ids), SCORE_NAME, "name"),
        (any(wanted in n for n in weak), SCORE_RELATED, "related term"),
        (wanted in km.norm(entity.get("summary") or ""), SCORE_SUMMARY, "summary"),
    )
    return next(((score, why) for hit, score, why in checks if hit), (0, ""))


def fact_text_hits(kb: KB, wanted: str) -> list[tuple[Record, str]]:
    """Current confirmed or proposed facts whose value contains <wanted>, with why they match."""
    hits = []
    for fact in kb.facts.values():
        status = km.effective_status(fact, kb.now)
        searchable = status in (km.CONFIRMED, km.PROPOSED) and km.is_current(fact)
        if searchable and wanted in km.norm(fact.get("value") or ""):
            hits.append((fact, f"fact {fact['id']}" + ("" if status == km.CONFIRMED else " (unconfirmed)")))
    return hits


def search(kb: KB, text: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[tuple[int, str, str]]:
    """(score, entity id, why) for entities matching <text> in a name, alias, summary or the value
    of a fact about them (current facts only; unconfirmed ones count too, marked as such)."""
    wanted = km.norm(text)
    if not wanted:
        return []
    found: dict[str, tuple[int, str]] = {}
    for entity in kb.entities.values():
        score, why = entity_score(entity, wanted)
        if score:
            found[entity["id"]] = (score, why)
    for fact, why in fact_text_hits(kb, wanted):
        found.setdefault(fact["subject"], (SCORE_FACT, why))
    ranked = sorted(((s, e, w) for e, (s, w) in found.items()), key=lambda r: (-r[0], r[1]))
    return ranked[:limit]


def neighbors(kb: KB, ref: str, hops: int = 1) -> list[tuple[int, Record]]:
    """(distance, fact) for the confirmed current links within <hops> of an entity, both ways."""
    start = km.resolve(kb, ref)
    hops = max(1, min(hops, MAX_HOPS))
    links = [f for f in km.confirmed_current(kb) if f.get("object")]
    seen = {start}
    found: list[tuple[int, Record]] = []
    queue = deque([(start, 0)])
    while queue:
        entity_id, depth = queue.popleft()
        if depth >= hops:
            continue
        for fact in links_of(entity_id, links, {f["id"] for _, f in found}):
            found.append((depth + 1, fact))
            other = fact["object"] if fact["subject"] == entity_id else fact["subject"]
            if other not in seen:
                seen.add(other)
                queue.append((other, depth + 1))
    return found


def links_of(entity_id: str, links: list[Record], used: set[str]) -> list[Record]:
    """The links touching an entity that were not walked yet."""
    return [f for f in links if entity_id in (f["subject"], f["object"]) and f["id"] not in used]


# ---- context packs ------------------------------------------------------------------------------


def pack_targets(kb: KB, target: str) -> list[str]:
    """The entities a pack is for: the one <target> names (or <type>/<target> for the usual types),
    else the best search matches for it as a topic."""
    try:
        return [km.resolve(kb, target)]
    except km.KnowledgeError:
        pass
    for entity_type in PACK_TYPES:
        candidate = f"{entity_type}/{target.strip().lower()}"
        if candidate in kb.entities:
            return [candidate]
    return [entity_id for _, entity_id, _ in search(kb, target, PACK_TOPIC_ENTITIES)]


def cite(fact: Record) -> str:
    source = fact["sources"][0] if fact["sources"] else {"kind": "?", "ref": "?"}
    return f"[{fact['id']}; {source['kind']}:{kr.short(source['ref'], 40)}]"


def pack_line(kb: KB, fact: Record) -> str:
    value = km.label(kb, fact["object"]) if fact.get("object") else kr.short(fact.get("value") or "", 300)
    order = f" (step {fact['order']})" if fact.get("order") else ""
    return (
        f"- {km.label(kb, fact['subject'])} {fact['predicate'].replace('_', ' ')} {value}{order} {cite(fact)}"
    )


def pack_facts(kb: KB, targets: list[str]) -> list[Record]:
    """The targets' own facts and the links to them, then their neighbours' own facts."""
    ordered: list[Record] = []
    seen: set[str] = set()

    def add(facts: list[Record]) -> None:
        for fact in facts:
            if fact["id"] not in seen:
                seen.add(fact["id"])
                ordered.append(fact)

    for entity_id in targets:
        add(outgoing(kb, entity_id))
        add(incoming(kb, entity_id))
    for entity_id in targets:
        for _, fact in neighbors(kb, entity_id, 1):
            add(outgoing(kb, fact["object"] if fact["subject"] == entity_id else fact["subject"]))
    return ordered


def context_pack(kb: KB, target: str, max_tokens: int = DEFAULT_PACK_TOKENS) -> str:
    """At most about <max_tokens> of confirmed, current facts, each citing its id and source."""
    targets = pack_targets(kb, target)
    if not targets:
        return f"No knowledge recorded for {target!r}."
    names = ", ".join(f"{km.label(kb, t)} ({t})" for t in targets)
    lines = [f"Knowledge for {names}: confirmed facts only; cite the [id] when you rely on one."]
    lines += [f"{t}: {kb.entities[t]['summary']}" for t in targets if kb.entities[t].get("summary")]
    budget = max_tokens * CHARS_PER_TOKEN - MORE_LINE_RESERVE
    used = sum(len(line) + 1 for line in lines)
    facts = pack_facts(kb, targets)
    for index, fact in enumerate(facts):
        line = pack_line(kb, fact)
        if used + len(line) + 1 > budget:
            lines.append(f"({len(facts) - index} more: kg lookup {targets[0]})")
            break
        lines.append(line)
        used += len(line) + 1
    if not facts:
        lines.append("(no confirmed facts yet)")
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
