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
  deletion                 target: "", body: {}, local: a delete on request or the retention
                           purge happened in this transaction (it moves the position, so a
                           write cut short after it is finished by the next; the rebuild skips it)

An edit is `local`, never leaving this Mac, when it names a person: a fact whose subject or object
is a `person/` entity (every record of it, as the fact never changes its subject or object), or a
`person/` entity. So is every record of a fact I stated, whole: one whose sources hold my words
or name my sessions (`holds_my_words`: a `user` source with its quote, a `transcript` source),
from the write that brings them on (its records are classed by the fact as the write leaves it,
so genesis keeps such a fact's whole history here). An update for a fact this knowledge base no
longer holds is local too (fail closed). Everything else is private. A delete on request
removes only local changes: `removed` says what a deletion took out, and `held_privately` which
of it the private journal holds, which a deletion refuses.

`file_digests` is the digest of what each knowledge file holds (entities.jsonl, facts.jsonl),
which view.json records beside the journal position; `edited_files` says which files were
changed outside the journal: each holding neither what view.json recorded nor what the journal's
rebuild makes (one that is either is a write cut short, never an edit).

`rebuild` is the other way: the files the journal's changes make, applied in the one order
(change_log.in_order) whatever order they come in. An entity is put whole or deleted; the fact
log's records stay in that order, and fold the way the files do (knowledge_model.Fold: an update
that comes before its fact's add waits for it, and one whose add never comes is `pending`). An
update that finds a CHECKED field no longer as it was where it was made (its `prev`: two Macs
decided one proposal at once) is a conflict and not applied; so is an op or a body this bosun
cannot apply. A deletion's placeholders and its marker are skipped (SKIPPED). `outside` is what
files changed outside the journal hold beyond what they held when a write last saved them, as
edits to adopt, never a person deleted on request (`returning`); `forgotten_outside` is who a
deletion on request made outside the journal took out of them (an older bosun's kg forget), to
forget in the journal too; `differences` is where the rebuild and the files differ.

Pure functions over a KB: no file access. lib/knowledge.py journals the edits under the knowledge
base's lock, and rebuilds the files where they are behind the journal.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import change_log
import knowledge_model as km
import knowledge_schema as ks
from change_log import (
    FIELD_CONFLICT,
    UNKNOWN,
    Change,
    ChangeLogError,
    Conflict,
    Edit,
    Place,
    digest,
    gained,
    in_order,
    where,
)
from knowledge_model import KB, Record
from validation import Finding, differing, finding


FACT_ADD = "fact.add"
FACT_UPDATE = "fact.update"
ENTITY_PUT = "entity.put"
ENTITY_DELETE = "entity.delete"
DELETION = "deletion"
ENTITIES_FILE, FACTS_FILE = "entities.jsonl", "facts.jsonl"
# Part of the rebuild's rules (with APPLIED, the ops its dispatch table applies, and SKIPPED; a
# unit test pins their digest to knowledge.KG_RULES_VERSION): the fields an update must find as
# it left them where it was made (its `prev`), or it is a conflict and not applied.
CHECKED = ("status",)
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
    write leaves it (<facts>), or as an add's own fact is (two adds can share a counted id in a
    file edited by hand, and the fold keeps only the last), or when an update brings my words."""
    fact_id = km.record_fact_id(record)
    added = record.get("op") == "add"
    local = (
        is_local_fact(facts.get(fact_id))
        or (added and is_local_fact(record["fact"]))
        or holds_my_words(record.get("sources"))
    )
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


def deletion_marker() -> Edit:
    """What a deletion's transaction (kg forget, the retention purge) also holds: a local change
    with no target and nothing in it, so that the position moves even when the deletion's own
    changes are placeholders (a write cut short after it is then finished by the next one)."""
    return Edit(DELETION, "", {}, True)


def held_digests(entities: Mapping[str, Record], records: list[Record]) -> dict[str, str | None]:
    """The digest of what each knowledge file holds (change_log.digest), by name: None for one that
    holds what the journal cannot."""
    found: dict[str, str | None] = {}
    for name, held in ((ENTITIES_FILE, {"entities": entities}), (FACTS_FILE, {"records": records})):
        try:
            found[name] = digest(held)
        except ChangeLogError:
            found[name] = None
    return found


def file_digests(entities: Mapping[str, Record], records: list[Record]) -> dict[str, str]:
    """The digest of what each knowledge file holds, by name, as view.json records it beside the
    journal position, so that a change made to one outside the journal shows. A file that holds
    what the journal cannot has none: ChangeLogError."""
    found = {name: held for name, held in held_digests(entities, records).items() if held is not None}
    if len(found) != len((ENTITIES_FILE, FACTS_FILE)):
        raise ChangeLogError("the knowledge files hold what the journal cannot")
    return found


def edited_files(
    recorded: Any, held: Mapping[str, str | None], rebuilt: Mapping[str, str | None]
) -> list[str]:
    """The files changed outside the journal: each holding neither what view.json recorded of it
    (<recorded>) nor what the journal's rebuild makes (<rebuilt>), and each holding what the
    journal cannot. [] when every file is one or the other: as saved, or a write cut short between
    them. Files saved before a digest per file are trusted, and get them at their next save."""
    unreadable = [name for name, digest_ in held.items() if digest_ is None]
    if unreadable or not isinstance(recorded, dict):
        return unreadable
    return [name for name, digest_ in held.items() if digest_ not in (recorded.get(name), rebuilt.get(name))]


# ---- the rebuild --------------------------------------------------------------------------------


@dataclass
class RebuiltKB:
    """The knowledge files the journal makes: the entities, the fact log's records in the one
    order; and what it kept but did not apply (conflicts), and the facts whose updates wait for an
    add the journal does not hold (pending)."""

    entities: dict[str, Record]
    records: list[Record]
    conflicts: list[Conflict]
    pending: list[str]


class _Refused(Exception):
    """A change the rebuild keeps but cannot apply."""


def crossed(fact: Record, update: Record) -> str:
    """Why <update> cannot apply to <fact> as it now is: a CHECKED field no longer holds what it
    held where the update was made (its `prev`); "" when it can. A record from before `prev`
    (no prev) is applied as written."""
    prev, changes = update.get("prev"), update.get("set")
    if not isinstance(prev, dict):
        return ""
    for key in CHECKED:
        if key in prev and fact.get(key) != prev[key]:
            to = changes.get(key) if isinstance(changes, dict) else None
            return f"{key}: {prev[key]} -> {to}, but it is {fact.get(key)} here"
    return ""


@dataclass
class _Rebuild:
    """The rebuild so far: the entities, each fact-log change applied in order with its record (a
    copy), and the fact log folded with the rebuild's check."""

    entities: dict[str, Record] = field(default_factory=dict)
    records: list[tuple[Change, Record]] = field(default_factory=list)
    fold: km.Fold = field(default_factory=lambda: km.Fold(crossed))


def _put(state: _Rebuild, change: Change) -> None:
    if not isinstance(change["body"], dict):
        raise _Refused("an entity that is not an object")
    state.entities[change["target"]] = copy.deepcopy(change["body"])


def _delete(state: _Rebuild, change: Change) -> None:
    state.entities.pop(change["target"], None)


def _objects(sources: Any) -> bool:
    return isinstance(sources, list) and all(isinstance(source, dict) for source in sources)


def _readable_add(record: Record) -> bool:
    fact = record.get("fact")
    return isinstance(fact, dict) and isinstance(fact.get("id"), str) and _objects(fact.get("sources"))


def _readable_update(record: Record) -> bool:
    return (
        isinstance(record.get("id"), str)
        and isinstance(record.get("set") or {}, dict)
        and _objects(record.get("sources") or [])
    )


def _readable(record: Any, op: str) -> bool:
    """Whether <record> is a fact-log record of the kind <op> names, whole enough to fold: one
    that is not would be applied in part, so it is kept as a conflict instead."""
    added = op == FACT_ADD
    if (
        not isinstance(record, dict)
        or "at" not in record
        or record.get("op") != ("add" if added else "update")
    ):
        return False
    return _readable_add(record) if added else _readable_update(record)


def _record(state: _Rebuild, change: Change) -> None:
    if not _readable(change["body"], change["op"]):
        raise _Refused(f"a {change['op']} whose record this bosun cannot read")
    record = copy.deepcopy(change["body"])
    state.records.append((change, record))
    state.fold.take(record)


_APPLIERS: dict[str, Callable[[_Rebuild, Change], None]] = {
    ENTITY_PUT: _put,
    ENTITY_DELETE: _delete,
    FACT_ADD: _record,
    FACT_UPDATE: _record,
}
# The rest of the rebuild's rules, pinned with CHECKED: the ops its dispatch applies (any other, a
# newer bosun's, is kept as a conflict), and the ops it skips: a deletion's placeholders and its
# marker, which hold nothing to apply.
APPLIED = tuple(sorted(_APPLIERS))
SKIPPED = (change_log.FORGOTTEN, DELETION)


def _apply(state: _Rebuild, change: Change) -> None:
    """One change, by its op's applier. An op this bosun does not know is kept as a conflict, not
    dropped (a newer bosun's change), and so is a body it cannot apply."""
    op = change.get("op")
    applier = _APPLIERS.get(op) if isinstance(op, str) else None
    if applier is None:
        raise _Refused(f"an op this bosun does not know ({op!r}): update bosun")
    try:
        applier(state, change)
    except (KeyError, TypeError, AttributeError) as exc:
        raise _Refused(f"a body this bosun cannot apply ({type(exc).__name__}: {exc})") from exc


def rebuild(changes: Iterable[Change]) -> RebuiltKB:
    """The knowledge files <changes> make, applied in the one order (each id once), a deletion's
    placeholders and marker skipped. The fact log keeps that order, an update that comes before
    its fact's add waits for it (pending if it never comes); an update CHECKED fields no longer
    allow (crossed), and a change this bosun cannot apply, are conflicts and have no effect."""
    state = _Rebuild()
    order: dict[str, int] = {}
    unknown: list[Conflict] = []
    for index, change in enumerate(in_order(changes)):
        order[change["id"]] = index
        if change.get("op") in SKIPPED:
            continue
        try:
            _apply(state, change)
        except _Refused as exc:
            unknown.append(Conflict(change["id"], str(change.get("target", "")), UNKNOWN, str(exc)))
    refused = {id(record): why for record, why in state.fold.refused}
    crossings = [
        Conflict(change["id"], change["target"], FIELD_CONFLICT, refused[id(record)])
        for change, record in state.records
        if id(record) in refused
    ]
    return RebuiltKB(
        state.entities,
        [record for _, record in state.records if id(record) not in refused],
        sorted([*unknown, *crossings], key=lambda conflict: order[conflict.change]),
        list(state.fold.waiting),
    )


# ---- the files against the journal --------------------------------------------------------------


def people(entities: Mapping[str, Record], facts: Mapping[str, Record]) -> set[str]:
    """The people the files name: a person entity, or a fact's subject or object."""
    named = {entity_id for entity_id in entities if ks.is_person(entity_id)}
    for fact in facts.values():
        named |= {str(e) for e in (fact.get("subject"), fact.get("object")) if ks.is_person(e)}
    return named


def naming(persons: set[str], facts: Mapping[str, Record]) -> set[str]:
    """<persons>, and every fact that names one of them (its subject or object)."""
    return persons | {i for i, f in facts.items() if {f.get("subject"), f.get("object")} & persons}


def forgotten_outside(base: RebuiltKB, kb: KB) -> set[str]:
    """Who a deletion on request made outside the journal (an older bosun's kg forget) took out of
    the files (<kb>): named by what the journal has of them (<base>), named nowhere in them, and
    matched by forgotten.jsonl (a plain sha256 record or a keyed one)."""
    gone = people(base.entities, km.fold_all(base.records)) - people(kb.entities, kb.facts)
    return {person for person in gone if km.is_forgotten(kb, person)}


def returning(base: RebuiltKB, kb: KB) -> set[str]:
    """The people the files (<kb>) name and the journal (<base>) does not, whom forgotten.jsonl
    names: deleted on request and not added again (a re-add with --allow-forgotten is in the
    journal), so never adopted (files restored from a backup made before kg forget)."""
    back = people(kb.entities, kb.facts) - people(base.entities, km.fold_all(base.records))
    return {person for person in back if km.is_forgotten(kb, person)}


def _names_any(record: Record, facts: Mapping[str, Record], persons: set[str]) -> bool:
    fact = facts.get(km.record_fact_id(record)) or (record.get("fact") if record.get("op") == "add" else None)
    return isinstance(fact, dict) and bool({fact.get("subject"), fact.get("object")} & persons)


def outside(base: RebuiltKB, kb: KB, refused: frozenset[str] | set[str] = frozenset()) -> list[Edit]:
    """What the files (<kb>) hold that <base> lacks, as the edits that adopt it into the journal:
    <base> is what they held when a write last saved them, as the journal has it. Each entity made,
    changed or removed, and each record the fact log gained, in its order; nothing of the people
    <refused> (returning). A record it lost cannot go in (the fact log only grows): it stays, and
    an edited one goes in beside it, as gained (an edited add takes the fact over)."""
    puts, deletes = entity_edits(base.entities, kb.entities)
    records = [r for r in gained(base.records, kb.records) if not _names_any(r, kb.facts, set(refused))]
    kept = [put for put in puts if put.target not in refused]
    return [*kept, *(record_edit(r, kb.facts) for r in records), *deletes]


def _by_fact(records: list[Record]) -> dict[str, list[Record]]:
    found: dict[str, list[Record]] = {}
    for record in records:
        found.setdefault(km.record_fact_id(record), []).append(record)
    return found


def differences(rebuilt: RebuiltKB, entities: Mapping[str, Record], records: list[Record]) -> list[Finding]:
    """Where the journal's rebuild and the knowledge files (both at the same position) differ:
    each entity, and each fact's records, by the first path that differs (validation.differing);
    the fact log's order, when nothing else does."""
    found = differing(rebuilt.entities, entities, ENTITIES_FILE)
    facts = differing(_by_fact(rebuilt.records), _by_fact(records), FACTS_FILE)
    if not facts and rebuilt.records != records:
        detail = "the rebuild holds the same records in another order"
        facts.append(finding("differs", detail, FACTS_FILE))
    return [*found, *facts]
