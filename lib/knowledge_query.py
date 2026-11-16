"""Reading the knowledge base (design: docs/design/knowledge.md, "How it is used"): lookup (with
where the people who answer for an entity prefer to be asked), facts with --as-of (valid time),
search over names, aliases and text (by phrase, then by word), neighbours over entity links, and
context packs: at most about 1.5k tokens of confirmed, current, cited facts for an entity, a repo
or a topic. Identifiers (restricted) are never shown here.

Pure over a knowledge_model.KB.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime

import knowledge_model as km
import knowledge_review as kr
import knowledge_schema as ks
from knowledge_model import KB, Record


DEFAULT_PACK_TOKENS = 1500
CHARS_PER_TOKEN = 4  # a rough estimate, enough to keep a pack small
MAX_HOPS = 3
LOOKUP_TOKENS = 800  # what `kg lookup` prints at most (its --json stays complete)
LOOKUP_PENDING = 5  # waiting facts a lookup lists; it counts the rest
SEARCH_HITS = 10
PACK_TOPIC_ENTITIES = 3
MORE_LINE_RESERVE = 80  # room for the "(N more: ...)" line
# Context packs try these types when --for names neither an entity nor an alias.
PACK_TYPES = ("repo", "service", "process", "feature", "system", "channel", "team")
# Who answers for an entity (the subject of a link to it: a person or team, as the schema has it),
# and where each prefers to be asked: a lookup of the entity says both.
ANSWERS_FOR = frozenset({"reviews", "owns", "approves"})
ASKED_ON = (
    "reviews"  # the line that says where to ask them, when they have one: who reviews is the usual question
)
ASKED_IN = "prefers_channel"


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
    return sorted(facts, key=lambda f: (f["predicate"], f.get("order") or 0, km.creation_key(f)))


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


def within(lines: list[str], budget: int) -> tuple[list[str], int]:
    """All of <lines> if they fit in <budget> characters, else the leading ones that fit with room for a
    "more" line; returns them and how many facts were left out (a "linked from:" heading is no fact)."""
    if sum(len(line) + 1 for line in lines) <= budget:
        return lines, 0
    used = 0
    for index, line in enumerate(lines):
        used += len(line) + 1
        if used > budget - MORE_LINE_RESERVE:
            return lines[:index], sum(1 for rest in lines[index:] if rest[0] == " ")
    return lines, 0


def waiting_lines(kb: KB, entity_id: str) -> list[str]:
    """The facts about <entity_id> that wait for review: LOOKUP_PENDING of them, then a count."""
    pending = by_order([f for f in kr.waiting(kb) if entity_id in (f["subject"], f.get("object"))])
    if not pending:
        return []
    lines = [f"unconfirmed ({len(pending)}, not used in context packs):"]
    lines += [f"  {fact_line(kb, f)}" for f in pending[:LOOKUP_PENDING]]
    if len(pending) > LOOKUP_PENDING:
        lines.append(f"(+{len(pending) - LOOKUP_PENDING} more waiting for review)")
    return lines


def asking_links(links: list[Record]) -> set[str]:
    """The ids of the links that say where to ask their subject: for each person or team that answers
    for the entity, its ASKED_ON link, else its first."""
    chosen: dict[str, Record] = {}
    for fact in links:
        held = chosen.get(fact["subject"])
        better = held is None or (fact["predicate"] == ASKED_ON and held["predicate"] != ASKED_ON)
        if fact["predicate"] in ANSWERS_FOR and better:
            chosen[fact["subject"]] = fact
    return {fact["id"] for fact in chosen.values()}


def link_lines(kb: KB, links: list[Record]) -> list[str]:
    """The links to an entity, a line each; for each person or team that reviews, owns or approves it,
    one of its lines (asking_links) also says where they prefer to be asked: their own confirmed,
    current preferences with their ids (never their team's)."""
    asked_in: dict[str, list[Record]] = {}
    for fact in by_order([f for f in km.confirmed_current(kb) if f["predicate"] == ASKED_IN]):
        asked_in.setdefault(fact["subject"], []).append(fact)
    asking = asking_links(links)
    lines = []
    for fact in links:
        line = f"  {fact_line(kb, fact)}"
        if fact["id"] in asking:
            line += "".join(f"; {kr.saying(kb, f)} [{f['id']}]" for f in asked_in.get(fact["subject"], []))
        lines.append(line)
    return lines


def lookup(kb: KB, ref: str, max_tokens: int = LOOKUP_TOKENS) -> str:
    """An entity's current confirmed facts with sources, what links to it (with where the people who
    answer for it prefer to be asked), and what waits: the facts and links within <max_tokens> (then
    how many more, and the command that shows them), at most LOOKUP_PENDING waiting ones."""
    entity = kb.entities[km.resolve(kb, ref)]
    entity_id = entity["id"]
    own, links = outgoing(kb, entity_id), incoming(kb, entity_id)
    head = [*entity_header(kb, entity), "facts:" if own else "facts: none confirmed"]
    tail = waiting_lines(kb, entity_id)
    body = [f"  {fact_line(kb, f)}" for f in own]
    if links:
        body += ["linked from:", *link_lines(kb, links)]
    fixed = sum(len(line) + 1 for line in head + tail)
    shown, left = within(body, max_tokens * CHARS_PER_TOKEN - fixed)
    if left:
        shown.append(f"({left} more: kg lookup {entity_id} --max-tokens {max_tokens * 4})")
    return "\n".join(head + shown + tail)


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
# A text also matches by its words, below every phrase match (at most SCORE_FACT - 1): each word
# counts by where it begins a word: in a name, a strong alias or the id's slug (never its type, such
# as person/), or only in a related term, the summary or a fact's value; and the most matched words
# in a row in one name or alias ("pdf renderer") add SCORE_WORD_RUN for each after the first.
SCORE_WORD_NAMED = 4
SCORE_WORD_OTHER = 1
SCORE_WORD_RUN = 2
WORD_MIN = 3  # shorter words never count on their own
STOP_WORDS = frozenset(
    {"about", "and", "are", "ask", "can", "could", "does", "for", "from", "how", "should", "that", "the"}
    | {"them", "they", "this", "what", "when", "where", "which", "who", "whom", "why", "with", "would"}
)
WORD = re.compile(r"\w+")


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


def searchable_facts(kb: KB) -> Iterator[tuple[Record, str]]:
    """The current confirmed or proposed facts, with their status: what search reads."""
    for fact in kb.facts.values():
        status = km.effective_status(fact, kb.now)
        if status in (km.CONFIRMED, km.PROPOSED) and km.is_current(fact):
            yield fact, status


def fact_text_hits(kb: KB, wanted: str) -> list[tuple[Record, str]]:
    """Current confirmed or proposed facts whose value contains <wanted>, with why they match."""
    return [
        (fact, f"fact {fact['id']}" + ("" if status == km.CONFIRMED else " (unconfirmed)"))
        for fact, status in searchable_facts(kb)
        if wanted in km.norm(fact.get("value") or "")
    ]


def phrase_hits(kb: KB, wanted: str) -> dict[str, tuple[int, str]]:
    """Entity id: (score, why) for the entities <wanted> is a part of, as one phrase."""
    found: dict[str, tuple[int, str]] = {}
    for entity in kb.entities.values():
        score, why = entity_score(entity, wanted)
        if score:
            found[entity["id"]] = (score, why)
    for fact, why in fact_text_hits(kb, wanted):
        found.setdefault(fact["subject"], (SCORE_FACT, why))
    return found


def query_words(wanted: str) -> list[str]:
    """The words a text is also searched by, each once: WORD_MIN characters or more, no stop word."""
    return list(dict.fromkeys(w for w in WORD.findall(wanted) if len(w) >= WORD_MIN and w not in STOP_WORDS))


def words_of(text: str) -> list[str]:
    return WORD.findall(km.norm(text))


def starts_a_word(word: str, words: Iterable[str]) -> bool:
    return any(other.startswith(word) for other in words)


def longest_run(words: list[str], names: list[list[str]]) -> int:
    """The most words in a row of one of <names> (each a list of words) that each begin with one of
    <words>."""
    best = 0
    for name in names:
        run = 0
        for part in name:
            run = run + 1 if any(part.startswith(word) for word in words) else 0
            best = max(best, run)
    return best


@dataclass(frozen=True)
class WordHits:
    """Where the words of a text begin a word of an entity: in its names (a name, a strong alias, the
    id's slug), or only elsewhere (a related term, the summary, a fact's value)."""

    names: list[list[str]]  # the words of each name
    named: list[str]
    elsewhere: list[str]


def other_words(entity: Record, values: list[str]) -> set[str]:
    """The words of an entity's related terms, its summary and <values>, the facts about it."""
    texts = [*entity["aliases"]["weak"], entity.get("summary") or "", *values]
    return {part for text in texts for part in words_of(text)}


def word_hits_in(entity: Record, words: list[str], values: list[str]) -> WordHits:
    names = [words_of(name) for name in km.names_of(entity)[1:]]  # not the id, whose type is no word
    named = [w for w in words if any(starts_a_word(w, name) for name in names)]
    other = other_words(entity, values)
    return WordHits(names, named, [w for w in words if w not in named and starts_a_word(w, other)])


def word_score(entity: Record, words: list[str], values: list[str]) -> tuple[int, str]:
    """An entity's score for the <words> that begin a word of it, and which ones; (0, "") for none.
    A word in its names counts SCORE_WORD_NAMED, one only elsewhere (the <values> of the facts
    about it included) SCORE_WORD_OTHER, and a run of matched words in one name adds SCORE_WORD_RUN
    for each word after its first: so a word in the name outweighs several elsewhere, and "pdf
    renderer" in a name outweighs "pdf" and "renderer" apart. Every score stays below SCORE_FACT,
    a cap that a long text matching many words can reach, where they tie."""
    hits = word_hits_in(entity, words, values)
    if not hits.named and not hits.elsewhere:
        return 0, ""
    score = SCORE_WORD_NAMED * len(hits.named) + SCORE_WORD_OTHER * len(hits.elsewhere)
    score += SCORE_WORD_RUN * max(0, longest_run(hits.named, hits.names) - 1)
    matched = [w for w in words if w in hits.named or w in hits.elsewhere]
    return min(SCORE_FACT - 1, score), "words: " + ", ".join(matched)


def word_hits(kb: KB, words: list[str]) -> dict[str, tuple[int, str]]:
    """Entity id: (score, why) for the entities that match any of <words> (word_score)."""
    if not words:
        return {}
    values: dict[str, list[str]] = {}
    for fact, _ in searchable_facts(kb):
        if fact.get("value"):
            values.setdefault(fact["subject"], []).append(str(fact["value"]))
    found: dict[str, tuple[int, str]] = {}
    for entity in kb.entities.values():
        score, why = word_score(entity, words, values.get(entity["id"], []))
        if score:
            found[entity["id"]] = (score, why)
    return found


def search(kb: KB, text: str, limit: int = SEARCH_HITS, *, words: bool = True) -> list[tuple[int, str, str]]:
    """(score, entity id, why) for entities matching <text> in a name, alias, summary or the value
    of a fact about them (current facts only; unconfirmed ones count too, marked as such). With
    <words> (not for a pack's topic, which must not pull in what is only loosely related), the text
    also matches by its words (query_words, word_score), each at the start of a word, an entity found
    by the phrase keeping that score, every word match below a phrase match. For a single plain word
    that adds nothing (its phrase finds all of it); it finds "renderer?" or "pdf renderer review"."""
    wanted = km.norm(text)
    if not wanted:
        return []
    found = phrase_hits(kb, wanted)
    if words:
        for entity_id, hit in word_hits(kb, query_words(wanted)).items():
            found.setdefault(entity_id, hit)
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


def repo_by_last_segment(kb: KB, target: str) -> str:
    """The repo a path or URL names by its last segment (owner/repo, remotes/repo, a local path or
    host/owner/repo.git, which is how a remote that is rewritten to a folder reads): the repo's slug,
    name or strong alias. "" if <target> has no slash or the segment names no repo."""
    segment = target.strip().rstrip("/").rpartition("/")[2].removesuffix(".git")
    if "/" not in target or not segment:
        return ""
    if f"repo/{segment}" in kb.entities:
        return f"repo/{segment}"
    try:
        found = km.resolve(kb, segment)
    except km.KnowledgeError:
        return ""
    return found if found.startswith("repo/") else ""


def named_entity(kb: KB, target: str) -> str:
    """The entity <target> names, or ""."""
    try:
        return km.resolve(kb, target)
    except km.KnowledgeError:
        return ""


def pack_targets(kb: KB, target: str, *, topics: bool = True) -> list[str]:
    """The entities a pack is for: the one <target> names (or <type>/<target> for the usual types;
    for owner/repo, repo/<repo>), else, with <topics>, the best search matches for it as a topic
    (by phrase only: no word search), never a person: a person is the target only when <target>
    names them, so a fact of theirs that matches a topic never puts their own profile in the pack.
    Without <topics> (a brief's repo or area), a target that names a person is none: a person's own
    profile is nothing to put in a brief."""
    named = named_entity(kb, target)
    if named:
        return [named] if topics or not ks.is_person(named) else []
    slug = target.strip().lower()
    candidates = [f"{entity_type}/{slug}" for entity_type in PACK_TYPES]
    for candidate in candidates:
        if candidate in kb.entities:
            return [candidate]
    repo = repo_by_last_segment(kb, slug)
    if repo:
        return [repo]
    return topic_targets(kb, target) if topics else []


def topic_targets(kb: KB, topic: str) -> list[str]:
    """The best PACK_TOPIC_ENTITIES search matches for <topic>, by phrase only, leaving out people."""
    found = [entity_id for _, entity_id, _ in search(kb, topic, len(kb.entities), words=False)]
    return [entity_id for entity_id in found if not ks.is_person(entity_id)][:PACK_TOPIC_ENTITIES]


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
    """The targets' own facts and the links to them, then their neighbours' own facts, but a
    neighbouring person's: who reviews or owns the target is one of the links to it, and that
    person's own profile (timezone, hours, preferences, title) is nothing to put in a brief."""
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
            other = fact["object"] if fact["subject"] == entity_id else fact["subject"]
            if not ks.is_person(other):
                add(outgoing(kb, other))
    return ordered


@dataclass(frozen=True)
class Pack:
    """A context pack's lines and how many of them are facts."""

    lines: tuple[str, ...]
    facts: int


def section_head(kb: KB, entities: list[str], budget: int) -> list[str]:
    """A section's header and the summaries that fit in <budget> characters after it; none when
    the header itself does not fit."""
    names = ", ".join(f"{km.label(kb, t)} ({t})" for t in entities)
    lines = [f"Knowledge for {names}: confirmed facts only; cite the [id] when you rely on one."]
    used = len(lines[0]) + 1
    if used > budget:
        return []
    for entity_id in entities:
        summary = kb.entities[entity_id].get("summary")
        line = f"{entity_id}: {summary}"
        if summary and used + len(line) + 1 <= budget:
            lines.append(line)
            used += len(line) + 1
    return lines


def pack_section(kb: KB, entities: list[str], budget: int, shown: set[str]) -> tuple[list[str], int, int]:
    """The header, summaries and facts of one target (not those <shown> already), within <budget>
    characters; returns its lines, the facts among them and the characters those took (the line
    that counts what did not fit is not among them: pack_of keeps room for it)."""
    lines = section_head(kb, entities, budget)
    if not lines:
        return [], 0, 0
    used = sum(len(line) + 1 for line in lines)
    facts = [f for f in pack_facts(kb, entities) if f["id"] not in shown]
    count = 0
    for index, fact in enumerate(facts):
        line = pack_line(kb, fact)
        if used + len(line) + 1 > budget:
            lines.append(f"({len(facts) - index} more: kg lookup {entities[0]})")
            break
        lines.append(line)
        shown.add(fact["id"])
        used += len(line) + 1
        count += 1
    if not facts:
        lines.append("(no confirmed facts yet)")
    return lines, count, used


def pack_of(
    kb: KB, targets: Sequence[str], max_tokens: int = DEFAULT_PACK_TOKENS, *, topics: bool = True
) -> Pack:
    """A section per target that names something (one shared budget: each gets an equal share of
    what is left, so the first cannot use it all), or no lines when none does."""
    sections: list[list[str]] = []
    for target in targets:
        entities = pack_targets(kb, target, topics=topics)
        if entities and entities not in sections:
            sections.append(entities)
    remaining = max_tokens * CHARS_PER_TOKEN - MORE_LINE_RESERVE * len(sections)
    lines: list[str] = []
    facts = 0
    shown: set[str] = set()
    for index, entities in enumerate(sections):
        later = len(sections) - index - 1
        section, count, used = pack_section(kb, entities, remaining // (later + 1), shown)
        if not section:
            # its share cannot hold even its header: it takes what is left, the room kept for the
            # later sections' last lines too, and no later section comes
            section, count, _ = pack_section(kb, entities, remaining + MORE_LINE_RESERVE * later, shown)
            lines += section
            facts += count
            break
        remaining -= used
        lines += section
        facts += count
    return Pack(tuple(lines), facts)


def context_pack(
    kb: KB, targets: Sequence[str] | str, max_tokens: int = DEFAULT_PACK_TOKENS, *, topics: bool = True
) -> str:
    """At most about <max_tokens> of confirmed, current facts, each citing its id and source: a
    section per target (a single <targets> string is one target)."""
    named = [targets] if isinstance(targets, str) else list(targets)
    pack = pack_of(kb, named, max_tokens, topics=topics)
    if not pack.lines:
        return f"No knowledge recorded for {', '.join(repr(t) for t in named)}."
    return "\n".join(pack.lines)


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
