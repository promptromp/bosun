"""The knowledge base in memory (design: docs/design/knowledge.md): entities, the fact log folded
into facts with two timelines, entity resolution, and every write operation (propose, confirm,
reject, supersede, forget, expire) with the auto-confirm rules of "Curation without the spam".

Pure over a KB value: operations append fact-log records to kb.new_records (and mark what else
changed); lib/knowledge.py loads and saves a KB under the store's lock, so a dry run is simply an
operation whose KB is never saved.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import knowledge_schema as ks
from knowledge_schema import Claim, Schema


Record = dict[str, Any]

PROPOSED = "proposed"
CONFIRMED = "confirmed"
REJECTED = "rejected"
EXPIRED = "expired"
STATUSES = (PROPOSED, CONFIRMED, REJECTED, EXPIRED)

# Unreviewed proposals expire unless something observes them again; person facts not re-observed
# for a year are deleted (retention).
PROPOSAL_DAYS = 30
RETENTION_DAYS = 365
HASH_LENGTH = 16
NEAR_MATCH_CUTOFF = 0.8
MAX_SUGGESTIONS = 3

# Why a fact was confirmed without a review.
USER_WORDS = "your own words (quote verified in the transcript)"
TWO_SOURCES = "two independent trusted sources"
PROFILE = "a structured profile source"
INDEPENDENT_SOURCES = 2
REVIEWED = "reviewed"
CORRECTION = "a correction"

FORGET_REQUEST = "request"
FORGET_RETENTION = "retention"
IDENTIFIER_SYSTEMS = ("slack", "github", "jira", "other")


class KnowledgeError(Exception):
    pass


# ---- time ---------------------------------------------------------------------------------------


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    """An ISO time; a bare date is its midnight UTC; a time without offset is UTC."""
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise KnowledgeError(f"{value!r} is not a date (YYYY-MM-DD) or an ISO time") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def norm(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def hash_id(entity_id: str) -> str:
    return hashlib.sha256(entity_id.encode()).hexdigest()[:HASH_LENGTH]


# ---- the knowledge base ---------------------------------------------------------------------------


@dataclass
class KB:
    """Everything loaded, plus what an operation changed (for the store to write)."""

    schema: Schema
    now: datetime
    entities: dict[str, Record] = field(default_factory=dict)
    records: list[Record] = field(default_factory=list)
    facts: dict[str, Record] = field(default_factory=dict)
    identifiers: list[Record] = field(default_factory=list)
    forgotten: list[Record] = field(default_factory=list)
    new_records: list[Record] = field(default_factory=list)
    new_forgotten: list[Record] = field(default_factory=list)
    entities_changed: bool = False
    identifiers_changed: bool = False
    rewrite: bool = False  # facts.jsonl is rewritten from records (a deletion)
    auto_confirm: bool = True  # kg import without --auto-confirm: everything waits for review
    restart_history: bool = False

    def add_record(self, record: Record) -> None:
        self.records.append(record)
        self.new_records.append(record)
        fold(self.facts, record)

    @property
    def changed(self) -> bool:
        return bool(
            self.new_records or self.new_forgotten or self.entities_changed or self.identifiers_changed
        )


def source_key(source: Record) -> tuple[str, str]:
    return str(source.get("kind")), str(source.get("ref"))


def fold(facts: dict[str, Record], record: Record) -> None:
    """Apply one fact-log record: an add creates the fact, an update sets fields, adds sources and
    extends its history. An update for a fact that is gone (deleted on request) is ignored."""
    if record.get("op") == "add":
        fact = copy.deepcopy(record["fact"])
        fact["history"] = [{"at": record["at"], "event": "added", "note": record.get("note", "")}]
        facts[fact["id"]] = fact
        return
    fact = facts.get(str(record.get("id")))
    if fact is None:
        return
    fact.update(copy.deepcopy(record.get("set") or {}))
    known = {source_key(s) for s in fact["sources"]}
    fact["sources"].extend(s for s in record.get("sources") or [] if source_key(s) not in known)
    fact["history"].append(
        {"at": record["at"], "event": record.get("event", "update"), "note": record.get("note", "")}
    )


def fold_all(records: list[Record]) -> dict[str, Record]:
    facts: dict[str, Record] = {}
    for record in records:
        fold(facts, record)
    return facts


def record_fact_id(record: Record) -> str:
    return str(record["fact"]["id"] if record.get("op") == "add" else record.get("id"))


# ---- timelines ----------------------------------------------------------------------------------


def last_seen(fact: Record) -> datetime:
    stamps = [fact["observed_at"], fact.get("last_confirmed_at") or fact["observed_at"]]
    return max(parse_time(s) for s in stamps)


def effective_status(fact: Record, now: datetime) -> str:
    """The stored status, except that an unreviewed proposal not observed for PROPOSAL_DAYS has
    expired (whether or not `kg expire` recorded it yet)."""
    if fact["status"] == PROPOSED and now - parse_time(fact["observed_at"]) > timedelta(days=PROPOSAL_DAYS):
        return EXPIRED
    return str(fact["status"])


def is_current(fact: Record, at: datetime | None = None) -> bool:
    """Whether the fact holds now (not closed), or held at <at> (valid time)."""
    if at is None:
        return fact.get("valid_to") is None
    start = parse_time(fact.get("valid_from") or fact["recorded_at"])
    end = fact.get("valid_to")
    return start <= at and (end is None or at < parse_time(end))


def is_personal(fact: Record) -> bool:
    return ks.is_person(fact["subject"]) or ks.is_person(fact.get("object"))


def fact_key(subject: str, predicate: str, obj: str | None, value: str | None) -> tuple[str, str, str]:
    return subject, predicate, obj if obj is not None else "=" + norm(value or "")


def key_of(fact: Record) -> tuple[str, str, str]:
    return fact_key(fact["subject"], fact["predicate"], fact.get("object"), fact.get("value"))


def confirmed_current(kb: KB, at: datetime | None = None) -> list[Record]:
    return [f for f in kb.facts.values() if f["status"] == CONFIRMED and is_current(f, at)]


def get_fact(kb: KB, fact_id: str) -> Record:
    if fact_id not in kb.facts:
        raise KnowledgeError(f"no fact {fact_id!r} (ids look like f12; see kg facts <entity> --status all)")
    return kb.facts[fact_id]


def next_fact_id(kb: KB) -> str:
    numbers = [int(i[1:]) for i in kb.facts if i[1:].isdigit()]
    return f"f{max(numbers, default=0) + 1}"


# ---- entities and resolution --------------------------------------------------------------------


def names_of(entity: Record) -> list[str]:
    slug = entity["id"].partition("/")[2]
    return [entity["id"], slug, entity.get("name") or slug, *entity["aliases"]["strong"]]


def label(kb: KB, entity_id: str | None) -> str:
    entity = kb.entities.get(entity_id or "")
    return str(entity.get("name") or entity_id) if entity else str(entity_id)


def near_matches(kb: KB, text: str, exclude: str = "") -> list[str]:
    """Entities whose id, name or strong alias is close to <text> (merge or typo suggestions)."""
    by_name: dict[str, set[str]] = {}
    for entity in kb.entities.values():
        if entity["id"] != exclude:
            for name in names_of(entity):
                by_name.setdefault(norm(name), set()).add(entity["id"])
    close = difflib.get_close_matches(norm(text), list(by_name), n=MAX_SUGGESTIONS, cutoff=NEAR_MATCH_CUTOFF)
    return sorted({entity_id for name in close for entity_id in by_name[name]})


def resolve(kb: KB, ref: str) -> str:
    """The entity <ref> names: its id, slug, name or a strong alias (case and spacing ignored).
    Weak aliases are related terms, used by search only."""
    ref = ref.strip()
    if ref in kb.entities:
        return ref
    wanted = norm(ref)
    hits = sorted({e["id"] for e in kb.entities.values() if wanted in {norm(n) for n in names_of(e)}})
    if len(hits) == 1:
        return str(hits[0])
    if hits:
        raise KnowledgeError(f"{ref!r} names several entities: {', '.join(hits)}; use the id")
    near = near_matches(kb, ref)
    hint = (
        f"; did you mean {', '.join(near)}?"
        if near
        else "; add it with kg add-entity <type>/<slug> --name ..."
    )
    raise KnowledgeError(f"no entity {ref!r}{hint}")


def forgotten_hashes(kb: KB) -> set[str]:
    return {str(r["hash"]) for r in kb.forgotten + kb.new_forgotten if r.get("hash")}


def alias_collisions(kb: KB, entity: Record) -> list[str]:
    """Other entities sharing a name or strong alias with <entity> (and, for people, a first name)."""
    mine = {norm(n) for n in names_of(entity)[2:]}
    found = []
    for other in kb.entities.values():
        if other["id"] == entity["id"]:
            continue
        shared = mine & {norm(n) for n in names_of(other)[2:]}
        if shared:
            found.append(f"warning: {entity['id']} and {other['id']} share {min(shared)!r}")
    return found


def check_new_entity(kb: KB, entity_id: str, summary: str, allow_forgotten: bool) -> None:
    entity_type, _ = ks.split_id(entity_id, kb.schema)
    if hash_id(entity_id) in forgotten_hashes(kb) and not allow_forgotten:
        raise KnowledgeError(
            f"{entity_id} was deleted on request; add it again only if they asked (--allow-forgotten)"
        )
    if entity_type == ks.PERSON and summary:
        raise KnowledgeError(
            "people whitelist: a person has no free-text summary; record their role, team or title as facts"
        )


def merge_aliases(entity: Record, strong: list[str], weak: list[str]) -> bool:
    changed = False
    for kind, names in (("strong", strong), ("weak", weak)):
        for name in names:
            if name.strip() and norm(name) not in {norm(n) for n in entity["aliases"][kind]}:
                entity["aliases"][kind].append(name.strip())
                changed = True
    return changed


@dataclass
class EntitySpec:
    id: str
    name: str = ""
    strong: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    summary: str = ""


def new_entity(kb: KB, spec: EntitySpec) -> tuple[Record, list[str]]:
    near = near_matches(kb, spec.name or spec.id)
    entity = {
        "id": spec.id,
        "type": ks.entity_type(spec.id),
        "name": spec.name.strip() or spec.id.partition("/")[2],
        "aliases": {"strong": [], "weak": []},
        "summary": spec.summary.strip(),
        "created_at": iso(kb.now),
        "updated_at": iso(kb.now),
    }
    kb.entities[spec.id] = entity
    hints = [f"suggestion: {spec.id} may be the same as {n}; if so, add an alias instead" for n in near]
    return entity, [f"ok: added {spec.id}", *hints]


def update_entity(entity: Record, spec: EntitySpec) -> bool:
    changed = False
    for key, given in (("name", spec.name.strip()), ("summary", spec.summary.strip())):
        if given and given != entity[key]:
            entity[key] = given
            changed = True
    return changed


def add_entity(kb: KB, spec: EntitySpec, *, allow_forgotten: bool = False) -> list[str]:
    """Add an entity, or merge new aliases, name and summary into an existing one. Near matches
    are reported as merge suggestions (nothing is merged automatically)."""
    check_new_entity(kb, spec.id, spec.summary, allow_forgotten)
    entity = kb.entities.get(spec.id)
    messages: list[str] = []
    if entity is None:
        entity, messages = new_entity(kb, spec)
    changed = update_entity(entity, spec) or bool(messages)
    changed = merge_aliases(entity, spec.strong, spec.weak) or changed
    if changed:
        entity["updated_at"] = iso(kb.now)
        kb.entities_changed = True
    messages = messages or [f"ok: {'updated' if changed else 'unchanged'} {spec.id}"]
    return messages + alias_collisions(kb, entity)


# ---- proposing ----------------------------------------------------------------------------------


@dataclass
class Proposal:
    subject: str
    predicate: str
    object: str | None = None
    value: str | None = None
    sources: list[Record] = field(default_factory=list)
    authority: str = ks.OBSERVED
    valid_from: str | None = None
    order: int | None = None
    rework: bool = False
    note: str = ""
    observations: int = 1


@dataclass
class Outcome:
    fact_id: str
    action: str  # added, observed
    status: str
    reason: str = ""
    messages: list[str] = field(default_factory=list)

    def line(self) -> str:
        why = f" ({self.reason})" if self.reason else ""
        return f"ok: {self.fact_id} {self.action}, {self.status}{why}"


def parse_source(text: str, now: datetime) -> Record:
    """A --source kind:ref (ref: a ledger id, a permalink, a file, a transcript)."""
    kind, sep, ref = text.partition(":")
    if not sep or not ref.strip():
        raise KnowledgeError(f"--source takes kind:ref, got {text!r} (kinds: {', '.join(ks.SOURCE_KINDS)})")
    if kind not in ks.SOURCE_KINDS:
        raise KnowledgeError(f"unknown source kind {kind!r}; one of {', '.join(ks.SOURCE_KINDS)}")
    return {"kind": kind, "ref": ref.strip(), "at": iso(now)}


def verified_user(sources: list[Record]) -> bool:
    return any(s["kind"] == ks.USER_SOURCE and s.get("verified") for s in sources)


def find_same(kb: KB, key: tuple[str, str, str]) -> Record | None:
    """The current fact with this subject, predicate and object or value, preferring one not
    rejected (the newest of each)."""
    same = [f for f in kb.facts.values() if key_of(f) == key and is_current(f)]
    same.sort(key=lambda f: (f["status"] != REJECTED, f["recorded_at"], f["id"]))
    return same[-1] if same else None


def check_claim(kb: KB, proposal: Proposal) -> tuple[str, str | None, ks.Predicate]:
    subject = resolve(kb, proposal.subject)
    obj = resolve(kb, proposal.object) if proposal.object is not None else None
    if not proposal.sources:
        raise KnowledgeError("a fact needs at least one --source kind:ref (or --from-user)")
    claim = Claim(
        subject,
        proposal.predicate,
        obj,
        proposal.value,
        proposal.authority,
        tuple(s["kind"] for s in proposal.sources),
    )
    return subject, obj, ks.check_fact(claim, kb.schema)


def propose(kb: KB, proposal: Proposal) -> Outcome:
    """Propose a fact. The same fact again only re-observes it (newer observed_at, more sources);
    then the auto-confirm rules decide whether it waits for review."""
    subject, obj, predicate = check_claim(kb, proposal)
    value = proposal.value.strip() if proposal.value is not None else None
    existing = find_same(kb, fact_key(subject, predicate.name, obj, value))
    if existing:
        return observe(kb, existing, proposal)
    fact_id = next_fact_id(kb)
    now = iso(kb.now)
    fact = {
        "id": fact_id,
        "subject": subject,
        "predicate": predicate.name,
        "object": obj,
        "value": value,
        "order": proposal.order,
        "sources": proposal.sources,
        "authority": proposal.authority,
        "observed_at": now,
        "recorded_at": now,
        "valid_from": iso(parse_time(proposal.valid_from)) if proposal.valid_from else now,
        "valid_to": None,
        "superseded_by": None,
        "status": PROPOSED,
        "last_confirmed_at": None,
        "sensitivity": ks.sensitivity_of(subject, obj),
        "prevents_rework": proposal.rework,
        "observations": max(proposal.observations, 1),
        "note": proposal.note,
    }
    kb.add_record({"op": "add", "at": now, "fact": fact})
    return settle(kb, kb.facts[fact_id], "added")


def stronger(one: str, other: str) -> bool:
    return ks.AUTHORITIES.index(one) < ks.AUTHORITIES.index(other)


def observe(kb: KB, fact: Record, proposal: Proposal) -> Outcome:
    known = {source_key(s) for s in fact["sources"]}
    changes: Record = {
        "observed_at": iso(kb.now),
        "observations": int(fact.get("observations", 1)) + max(proposal.observations, 1),
    }
    revive = verified_user(proposal.sources) or fact["status"] != REJECTED
    if effective_status(fact, kb.now) in (EXPIRED, REJECTED) and revive:
        changes["status"] = PROPOSED
    if proposal.rework:
        changes["prevents_rework"] = True
    if stronger(proposal.authority, fact["authority"]):
        changes["authority"] = proposal.authority
    new_sources = [s for s in proposal.sources if source_key(s) not in known]
    kb.add_record(
        {
            "op": "update",
            "id": fact["id"],
            "at": iso(kb.now),
            "event": "observed",
            "set": changes,
            "sources": new_sources,
        }
    )
    if not revive:
        return Outcome(
            fact["id"],
            "observed",
            REJECTED,
            "rejected earlier; it stays rejected unless your own words confirm it (--from-user)",
        )
    return settle(kb, fact, "observed")


def auto_confirm_reason(kb: KB, fact: Record) -> str | None:
    """Why the fact needs no review, or None: your own words (verified); a profile source for a
    person's title or timezone; two trusted sources of different kinds for a low-risk fact that
    names no person. Slack, PR, ticket and document text never counts."""
    predicate = kb.schema.predicate(fact["predicate"])
    sources = fact["sources"]
    if verified_user(sources):
        return USER_WORDS
    if is_personal(fact):
        profiled = predicate.profile and any(s["kind"] == ks.PROFILE_SOURCE for s in sources)
        return PROFILE if profiled else None
    independent = predicate.risk == ks.LOW and len(trusted_kinds(sources)) >= INDEPENDENT_SOURCES
    return TWO_SOURCES if independent else None


def trusted_kinds(sources: list[Record]) -> set[str]:
    """The trusted source kinds behind a fact (an unverified claim of my words is not one)."""
    return {s["kind"] for s in sources if ks.trusted(s["kind"]) and s["kind"] != ks.USER_SOURCE}


def conflicting(kb: KB, fact: Record) -> list[Record]:
    """Current confirmed facts a single-valued fact would contradict."""
    if not kb.schema.predicate(fact["predicate"]).single:
        return []
    return [
        f
        for f in confirmed_current(kb)
        if f["id"] != fact["id"]
        and (f["subject"], f["predicate"]) == (fact["subject"], fact["predicate"])
        and key_of(f) != key_of(fact)
    ]


def settle(kb: KB, fact: Record, action: str) -> Outcome:
    status = effective_status(fact, kb.now)
    if status != PROPOSED:
        return Outcome(fact["id"], action, status)
    reason = auto_confirm_reason(kb, fact) if kb.auto_confirm else None
    if reason is None:
        return Outcome(fact["id"], action, PROPOSED, "waits for review: kg batch")
    conflicts = conflicting(kb, fact)
    if conflicts and reason != USER_WORDS:
        ids = ", ".join(f["id"] for f in conflicts)
        return Outcome(fact["id"], action, PROPOSED, f"not confirmed automatically: it contradicts {ids}")
    messages = confirm_fact(kb, fact, reason)
    return Outcome(fact["id"], action, CONFIRMED, reason, messages)


# ---- reviewing ----------------------------------------------------------------------------------


def close(kb: KB, fact: Record, by: str | None, note: str) -> None:
    changes = {"valid_to": iso(kb.now), "superseded_by": by}
    kb.add_record(
        {
            "op": "update",
            "id": fact["id"],
            "at": iso(kb.now),
            "event": "superseded",
            "set": changes,
            "note": note,
        }
    )


def decided_by(kb: KB, by: Record | None) -> Record:
    """The fields that say who reviewed a fact and when (the console records {"kind": "user",
    "ref": "console"}); nothing when the caller does not say."""
    return {"reviewed_by": {**by, "at": iso(kb.now)}} if by else {}


def confirm_fact(kb: KB, fact: Record, reason: str, note: str = "", *, by: Record | None = None) -> list[str]:
    """Confirm a fact; a single-valued one closes the values it replaces (never deletes them)."""
    messages = []
    for other in conflicting(kb, fact):
        close(kb, other, fact["id"], f"superseded by {fact['id']}")
        messages.append(f"ok: {other['id']} superseded by {fact['id']} ({fact['predicate']} holds one value)")
    changes = {
        "status": CONFIRMED,
        "last_confirmed_at": iso(kb.now),
        "confirmed_by": reason,
        **decided_by(kb, by),
    }
    kb.add_record(
        {
            "op": "update",
            "id": fact["id"],
            "at": iso(kb.now),
            "event": "confirmed",
            "set": changes,
            "note": note,
        }
    )
    return messages


def reject_fact(kb: KB, fact: Record, note: str = "", *, by: Record | None = None) -> None:
    kb.add_record(
        {
            "op": "update",
            "id": fact["id"],
            "at": iso(kb.now),
            "event": "rejected",
            "set": {"status": REJECTED, **decided_by(kb, by)},
            "note": note,
        }
    )


def reviewable(kb: KB, fact: Record) -> bool:
    return effective_status(fact, kb.now) in (PROPOSED, EXPIRED) and is_current(fact)


def review(
    kb: KB, fact_ids: list[str], confirm: bool, note: str = "", *, by: Record | None = None
) -> list[str]:
    """Confirm or reject facts after a review; facts already decided are reported, not changed.
    <by> says who decided (recorded with the time as the fact's reviewed_by)."""
    messages = []
    for fact_id in fact_ids:
        fact = get_fact(kb, fact_id)
        if not reviewable(kb, fact):
            messages.append(f"skipped: {fact_id} is {effective_status(fact, kb.now)}")
            continue
        if confirm:
            messages += confirm_fact(kb, fact, REVIEWED, note, by=by)
            messages.append(f"ok: {fact_id} confirmed")
        else:
            reject_fact(kb, fact, note, by=by)
            messages.append(f"ok: {fact_id} rejected")
    return messages


def replacement_fact(
    kb: KB, replacement: Proposal | None, by: str | None, note: str
) -> tuple[str | None, list[str]]:
    """The confirmed fact that replaces another (an existing one, or a new one), if any."""
    if by is None and replacement is None:
        return None, []
    messages: list[str] = []
    new_id = by
    if by is None and replacement is not None:
        outcome = propose(kb, replacement)
        new_id, messages = outcome.fact_id, outcome.messages
    new = get_fact(kb, str(new_id))
    if new["status"] != CONFIRMED:
        messages += confirm_fact(kb, new, CORRECTION, note)
    return new_id, messages


def supersede(kb: KB, old_id: str, replacement: Proposal | None, by: str | None, note: str = "") -> list[str]:
    """Close a current fact: replaced by an existing fact (--by), by a new one (confirmed: a
    correction is a decision), or by nothing (it simply stopped holding)."""
    old = get_fact(kb, old_id)
    if not is_current(old):
        raise KnowledgeError(
            f"{old_id} is already closed (superseded by {old.get('superseded_by') or 'nothing'})"
        )
    if by == old_id:
        raise KnowledgeError("a fact cannot supersede itself")
    new_id, messages = replacement_fact(kb, replacement, by, note)
    if is_current(kb.facts[old_id]):
        default = f"superseded by {new_id}" if new_id else "no longer holds"
        close(kb, kb.facts[old_id], new_id, note or default)
        messages.append(f"ok: {old_id} closed" + (f", superseded by {new_id}" if new_id else ""))
    return messages


# ---- deletion and retention ---------------------------------------------------------------------


def remove_facts(kb: KB, fact_ids: set[str]) -> None:
    kb.records = [r for r in kb.records if record_fact_id(r) not in fact_ids]
    kb.new_records = [r for r in kb.new_records if record_fact_id(r) not in fact_ids]
    kb.facts = fold_all(kb.records)
    kb.rewrite = True
    kb.restart_history = True


def forget(kb: KB, ref: str) -> dict[str, int]:
    """Delete a person on request: the entity, every fact naming them and their identifiers. What
    is kept is only a hash of the id (so a re-import is noticed) and the counts."""
    entity_id = ref.strip() if ref.strip() in kb.entities else resolve(kb, ref)
    if ks.entity_type(entity_id) != ks.PERSON:
        raise KnowledgeError(f"kg forget deletes a person on request; {entity_id} is not a person")
    fact_ids = {i for i, f in kb.facts.items() if entity_id in (f["subject"], f.get("object"))}
    before = len(kb.identifiers)
    kb.identifiers = [i for i in kb.identifiers if i.get("entity") != entity_id]
    counts = {"facts": len(fact_ids), "identifiers": before - len(kb.identifiers)}
    remove_facts(kb, fact_ids)
    kb.entities.pop(entity_id, None)
    kb.entities_changed = kb.identifiers_changed = True
    kb.new_forgotten.append(
        {"at": iso(kb.now), "hash": hash_id(entity_id), "reason": FORGET_REQUEST, **counts}
    )
    return counts


def past_retention(kb: KB) -> list[Record]:
    limit = timedelta(days=RETENTION_DAYS)
    return [f for f in kb.facts.values() if is_personal(f) and kb.now - last_seen(f) > limit]


def stale_proposals(kb: KB) -> list[Record]:
    return [
        f for f in kb.facts.values() if f["status"] == PROPOSED and effective_status(f, kb.now) == EXPIRED
    ]


def expire(kb: KB) -> tuple[list[str], int]:
    """Record the expiry of stale proposals, and delete person facts past retention."""
    expired = []
    for fact in stale_proposals(kb):
        kb.add_record(
            {
                "op": "update",
                "id": fact["id"],
                "at": iso(kb.now),
                "event": "expired",
                "set": {"status": EXPIRED},
            }
        )
        expired.append(fact["id"])
    purged = {f["id"] for f in past_retention(kb)}
    if purged:
        remove_facts(kb, purged)
        kb.new_forgotten.append({"at": iso(kb.now), "reason": FORGET_RETENTION, "facts": len(purged)})
    return expired, len(purged)


# ---- identifiers (restricted) -------------------------------------------------------------------


def add_identifier(kb: KB, ref: str, system: str, value: str) -> str:
    entity_id = resolve(kb, ref)
    if ks.entity_type(entity_id) not in ks.PEOPLE_TYPES:
        raise KnowledgeError("identifiers are for people and teams (mentions and routing)")
    if system not in IDENTIFIER_SYSTEMS or not value.strip():
        raise KnowledgeError(
            f"kg identify <entity> <system> <id>; system one of {', '.join(IDENTIFIER_SYSTEMS)}"
        )
    if any(
        (i["entity"], i["system"], i["value"]) == (entity_id, system, value.strip()) for i in kb.identifiers
    ):
        return f"ok: {entity_id} already has that {system} id"
    kb.identifiers.append({"entity": entity_id, "system": system, "value": value.strip(), "at": iso(kb.now)})
    kb.identifiers_changed = True
    return f"ok: recorded a {system} id for {entity_id} (restricted: never in context packs)"
