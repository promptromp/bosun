"""The knowledge base's changes (design: docs/design/sync.md, "Knowledge changes" and "Which data
goes where"; docs/design/knowledge.md, "Shape").

A write to the knowledge base becomes edits, one journal transaction: each fact-log record it
appended, as it is (`fact.add`, `fact.update`), and each entity it made, changed or removed
(`entity.put` with the whole entity, `entity.delete`). A knowledge base from before the journal
enters it once as the same edits for everything it holds (`snapshot`, the genesis transaction),
with what the journal cannot hold converted once (`taken_in`).

  fact.add / fact.update   target: the fact id; body: the record ({"op", "at", "fact"} or
                           {"op", "id", "at", "event", "set", "prev", "sources"?, "note"?})
  entity.put               target: the entity id; body: the entity
  entity.delete            target: the entity id; body: {}

An edit is `local`, never leaving this Mac, when it names a person: a fact whose subject or object
is a `person/` entity (every record of it, as the fact never changes its subject or object), or a
`person/` entity. So is every record of a fact I stated, whole: one whose sources hold my words
or name my sessions (`holds_my_words`: a `user` source with its quote, a `transcript` source),
from the write that brings them on (its records are classed by the fact as the write leaves it,
so genesis keeps such a fact's whole history here). An update for a fact this knowledge base no
longer holds is local too (fail closed). Everything else is private. A delete on request
removes only local changes: `removed` says what a deletion took out, and `held_privately` which
of it the private journal holds, which a deletion refuses.

`state_hash` is the digest of what the knowledge files hold (entities.jsonl and facts.jsonl),
which view.json records beside the journal position.

Pure functions over a KB: no file access. lib/knowledge.py journals the edits under the knowledge
base's lock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import change_log
import knowledge_model as km
import knowledge_schema as ks
from change_log import Change, Edit, Place, digest, where
from knowledge_model import KB, Record


FACT_ADD = "fact.add"
FACT_UPDATE = "fact.update"
ENTITY_PUT = "entity.put"
ENTITY_DELETE = "entity.delete"
# Source kinds that hold my words or name my sessions: a fact with one is mine and stays on this Mac
# whole (docs/design/sync.md, "Which data goes where"), as the ledger keeps a `via` that quotes me.
MY_WORDS = frozenset({ks.USER_SOURCE, ks.TRANSCRIPT_SOURCE})


def holds_my_words(sources: Any) -> bool:
    """Whether sources hold my words or name one of my sessions (MY_WORDS: a `user` source, with its
    quote and its transcript, or a `transcript` source)."""
    return isinstance(sources, list) and any(
        isinstance(s, dict) and s.get("kind") in MY_WORDS for s in sources
    )


def is_local_fact(fact: Record | None) -> bool:
    """Whether a fact's records stay on this Mac: it names a person, I stated it (its sources hold
    my words), or it is not known (fail closed)."""
    return fact is None or km.is_personal(fact) or holds_my_words(fact.get("sources"))


def record_edit(record: Record, facts: Mapping[str, Record]) -> Edit:
    """The edit of one fact-log record: the record as its body, whole, local as its fact is as the
    write leaves it (<facts>), or when the record itself brings my words."""
    fact_id = km.record_fact_id(record)
    added = record.get("op") == "add"
    own = record["fact"].get("sources") if added else record.get("sources")
    local = is_local_fact(facts.get(fact_id)) or holds_my_words(own)
    return Edit(FACT_ADD if added else FACT_UPDATE, fact_id, record, local)


def entity_edits(before: Mapping[str, Record], after: Mapping[str, Record]) -> tuple[list[Edit], list[Edit]]:
    """The entities made or changed (puts) and removed (deletes) from <before> to <after>, by id."""
    puts = [
        Edit(ENTITY_PUT, entity_id, entity, ks.is_person(entity_id))
        for entity_id, entity in sorted(after.items())
        if before.get(entity_id) != entity
    ]
    deletes = [
        Edit(ENTITY_DELETE, entity_id, {}, ks.is_person(entity_id))
        for entity_id in sorted(before)
        if entity_id not in after
    ]
    return puts, deletes


def edits(before: Mapping[str, Record], kb: KB) -> list[Edit]:
    """What a write changed, in the order a rebuild applies it: the entities it made or changed
    (from <before>, the entities as the write found them), the records it appended
    (kb.new_records), then the entities it removed."""
    puts, deletes = entity_edits(before, kb.entities)
    return [*puts, *(record_edit(r, kb.facts) for r in kb.new_records), *deletes]


def snapshot(kb: KB) -> list[Edit]:
    """Everything the knowledge base holds, as edits (genesis): every entity, then every record in
    the order of facts.jsonl."""
    puts, _ = entity_edits({}, kb.entities)
    return [*puts, *(record_edit(r, kb.facts) for r in kb.records)]


def removed(before: Mapping[str, Record], kb: KB) -> set[str]:
    """What a deletion (kg forget, the retention purge) took out, by target: the facts it removed
    and the entities gone since <before>."""
    return set(kb.removed) | {entity_id for entity_id in before if entity_id not in kb.entities}


def held_privately(changes: Iterable[Change], targets: set[str]) -> list[str]:
    """Which of <targets> the private journal holds a change of: a deletion never rewrites it, so it
    refuses to remove one."""
    return sorted({str(c["target"]) for c in changes if c.get("target") in targets and not c.get("local")})


def place(edit: Edit, inner: Place) -> str:
    """Where a value inside an edit's body is, as a person finds it: the fact or entity, then the
    path in its record ("f-3k9x2a fact.value", "repo/x summary")."""
    return where(edit.target, inner)


def files(kb: KB) -> dict[str, Any]:
    """What the knowledge files hold: the entities by id, and the records of facts.jsonl in order."""
    return {"entities": kb.entities, "records": kb.records}


def taken_in(kb: KB, source: str) -> list[str]:
    """A knowledge base written before the journal (genesis), as the journal can hold it: each
    value the journal cannot (a float, text that is not valid Unicode) converted once in <kb>
    (change_log.taken_in), with a note naming the entity or fact and the path."""

    def place_of(path: Place) -> str:
        head, key, below = path[0], path[1], path[2:]
        target = km.record_fact_id(kb.records[int(key)]) if head == "records" else key
        return where(target, below)

    made, notes = change_log.taken_in(files(kb), source, place_of)
    if notes:
        kb.entities, kb.records = made["entities"], made["records"]
        kb.facts = km.fold_all(kb.records)
    return notes


def state_hash(entities: Mapping[str, Record], records: list[Record]) -> str:
    """The digest of what the knowledge files hold (change_log.digest): view.json records it beside
    the journal position, so that a change made to the files outside the journal shows."""
    return digest({"entities": entities, "records": records})
