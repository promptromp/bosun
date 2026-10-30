"""Review of the knowledge base (design: docs/design/knowledge.md, "Curation without the spam"):
the daily batch (proposals grouped by entity and axis, capped, rework-preventing groups first, one
numbered list), the one-line decisions that answer it ("confirm all except 2b"), and lint (stale
facts, contradictions, orphans, alias collisions, retention, near duplicates).

Pure over a knowledge_model.KB; lib/knowledge.py saves the batch it printed and applies decisions.
"""

from __future__ import annotations

import difflib
import re
import string
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import knowledge_model as km
import knowledge_schema as ks
from knowledge_model import KB, Record


# A batch stays painless: at most this many groups, facts per group and facts in all
# (BOSUN_KNOWLEDGE_BATCH_CAP, _GROUP_FACTS, _BATCH_FACTS); the rest waits for a later batch.
DEFAULT_BATCH_CAP = 10
DEFAULT_GROUP_FACTS = 8
DEFAULT_BATCH_FACTS = 40
LETTERS = string.ascii_lowercase
MAX_GROUP_FACTS = len(LETTERS)  # each fact of a group gets a letter
# Facts that name an area are grouped by the area: "Reviewers for repo X: 3 facts".
AREA_GROUPS = {"reviews": "Reviewers for", "owns": "Owners of", "approves": "Approvers of"}
AREA_SEPARATOR = "#"
AXIS_PREFIX = "axis:"
# A step's facts go with its process, a decision's or lesson's with what it is about (has_step
# names the child as its object; the others name the parent).
CHILD_PREDICATES = frozenset({"has_step"})
PARENT_PREDICATES = frozenset({"part_of", "about", "lives_in"})
MIN_GROUP_FACTS = 5
LINE_LIMIT = 160
VALUE_LIMIT = 120  # a fact's value in a one-line statement (kg batch, lint, lookups)
DEFAULT_STALE_DAYS = 180
NEAR_DUPLICATE_RATIO = 0.85


# ---- the batch ----------------------------------------------------------------------------------


@dataclass
class Group:
    key: str
    title: str
    axis: str
    fact_ids: list[str] = field(default_factory=list)
    rework: bool = False
    observations: int = 0
    oldest: str = ""


def parents(kb: KB) -> dict[str, str]:
    """Each entity's parent, for grouping: the process a step belongs to, what a decision or
    lesson is about, the repo a feature lives in (current facts, reviewed or not)."""
    found: dict[str, str] = {}
    for fact in sorted(kb.facts.values(), key=lambda f: int(f["id"][1:])):
        if not fact.get("object") or not km.is_current(fact) or fact["status"] == km.REJECTED:
            continue
        if fact["predicate"] in CHILD_PREDICATES:
            found.setdefault(fact["object"], fact["subject"])
        elif fact["predicate"] in PARENT_PREDICATES:
            found.setdefault(fact["subject"], fact["object"])
    return found


def anchor(entity_id: str, links: dict[str, str]) -> str:
    """The top of an entity's parent chain."""
    seen = {entity_id}
    while entity_id in links and links[entity_id] not in seen:
        entity_id = links[entity_id]
        seen.add(entity_id)
    return entity_id


def entity_key(fact: Record, links: dict[str, str]) -> str:
    """The group a fact belongs to: its subject's anchor, or for reviewers, owners and approvers of
    an area, that area's ('repo/x#reviews')."""
    if fact["predicate"] in AREA_GROUPS and fact.get("object"):
        return f"{anchor(fact['object'], links)}{AREA_SEPARATOR}{fact['predicate']}"
    return anchor(str(fact["subject"]), links)


def axis_of_key(kb: KB, key: str) -> str:
    if key.startswith(AXIS_PREFIX):
        return key[len(AXIS_PREFIX) :]
    entity_id, area, _ = key.partition(AREA_SEPARATOR)
    return ks.PEOPLE if area else kb.schema.axis(ks.entity_type(entity_id))


def group_keys(kb: KB) -> dict[str, str]:
    """fact id -> group key for every waiting proposal. Groups of fewer than MIN_GROUP_FACTS
    facts fold into one group per axis ('axis:process'), so a big import arrives as a dozen groups,
    not one per entity."""
    links = parents(kb)
    keys = {f["id"]: entity_key(f, links) for f in waiting(kb)}
    sizes: dict[str, int] = defaultdict(int)
    for key in keys.values():
        sizes[key] += 1
    return {
        fact_id: key if sizes[key] >= MIN_GROUP_FACTS else AXIS_PREFIX + axis_of_key(kb, key)
        for fact_id, key in keys.items()
    }


def group_title(kb: KB, key: str) -> tuple[str, str]:
    """(title, axis) of a group key."""
    axis = axis_of_key(kb, key)
    if key.startswith(AXIS_PREFIX):
        return f"{axis.capitalize()}: other facts", axis
    entity_id, _, predicate = key.partition(AREA_SEPARATOR)
    name = km.label(kb, entity_id)
    if predicate:
        return f"{AREA_GROUPS[predicate]} {name}", axis
    return f"{name} ({ks.entity_type(entity_id)})", axis


def waiting(kb: KB) -> list[Record]:
    """Proposals that wait for review: current, unreviewed, not expired."""
    return [
        f for f in kb.facts.values() if km.effective_status(f, kb.now) == km.PROPOSED and km.is_current(f)
    ]


def fact_order(fact: Record) -> tuple[bool, int, str, int]:
    return (not fact.get("prevents_rework"), fact.get("order") or 0, fact["predicate"], int(fact["id"][1:]))


@dataclass(frozen=True)
class Limits:
    groups: int = DEFAULT_BATCH_CAP
    facts: int = DEFAULT_BATCH_FACTS
    group_facts: int = DEFAULT_GROUP_FACTS

    def __post_init__(self) -> None:
        if not (
            self.groups >= 1 and 1 <= self.group_facts <= MAX_GROUP_FACTS and self.facts >= self.group_facts
        ):
            raise km.KnowledgeError(
                f"batch limits: at least 1 group, 1 to {MAX_GROUP_FACTS} facts a group, and at least "
                "as many facts a batch as a group holds"
            )


def split_group(group: Group, facts: list[Record], size: int) -> list[Group]:
    """Groups of at most <size> facts (each fact gets a letter)."""
    parts = [facts[i : i + size] for i in range(0, len(facts), size)]
    result = []
    for number, part in enumerate(parts, start=1):
        title = group.title if len(parts) == 1 else f"{group.title}, part {number}"
        result.append(
            Group(
                group.key,
                title,
                group.axis,
                [f["id"] for f in part],
                any(f.get("prevents_rework") for f in part),
                sum(int(f.get("observations", 1)) for f in part),
                min(f["observed_at"] for f in part),
            )
        )
    return result


def priority(group: Group) -> tuple[bool, int, int, str, str]:
    """Rework-preventing groups first, then the most observed, the largest, the oldest."""
    return (not group.rework, -group.observations, -len(group.fact_ids), group.oldest, group.key)


def build_groups(kb: KB, group_facts: int = DEFAULT_GROUP_FACTS) -> list[Group]:
    """Every waiting proposal in groups, highest priority first."""
    by_key: dict[str, list[Record]] = defaultdict(list)
    for fact_id, key in group_keys(kb).items():
        by_key[key].append(kb.facts[fact_id])
    groups = []
    for key, facts in by_key.items():
        title, axis = group_title(kb, key)
        groups += split_group(Group(key, title, axis), sorted(facts, key=fact_order), group_facts)
    return sorted(groups, key=priority)


def select_batch(groups: list[Group], limits: Limits) -> list[Group]:
    """The groups of one batch, in priority order, until the group or fact limit is reached."""
    chosen: list[Group] = []
    facts = 0
    for group in groups:
        if len(chosen) >= limits.groups or facts + len(group.fact_ids) > limits.facts:
            break
        chosen.append(group)
        facts += len(group.fact_ids)
    return chosen


def short(text: str, limit: int = LINE_LIMIT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---- sentences: a fact in plain words -----------------------------------------------------------

TEXT = "text"
ENTITY = "entity"
VALUE = "value"
Part = tuple[str, str]  # (TEXT, words) | (ENTITY, entity id) | (VALUE, the fact's value)


def effective_since(fact: Record) -> str:
    """' on Oct 15, 2026' when the fact names the day it took effect (a valid_from given apart from
    when it was recorded), else '': an import's own time is not when a decision was made."""
    given, recorded = str(fact.get("valid_from") or ""), str(fact.get("recorded_at") or "")
    if not given or given == recorded:
        return ""
    try:
        moment = km.parse_time(given)
    except km.KnowledgeError:
        return ""
    return f" on {moment.strftime('%b')} {moment.day}, {moment.year}"


def sentence_parts(kb: KB, fact: Record) -> list[Part]:
    """What the fact says after its subject, from its predicate's sentence (knowledge_schema)."""
    predicate = kb.schema.predicates.get(fact["predicate"])
    template = predicate.phrase if predicate else f"{fact['predicate'].replace('_', ' ')}: {ks.TARGET}"
    before, _, after = template.replace(ks.WHEN, effective_since(fact)).partition(ks.TARGET)
    target = (ENTITY, str(fact["object"])) if fact.get("object") else (VALUE, str(fact.get("value") or ""))
    parts = [(TEXT, before), target, (TEXT, after)]
    if fact.get("order"):
        parts.append((TEXT, f" (step {fact['order']})"))
    return [part for part in parts if part[1]]


def sentence(kb: KB, fact: Record, limit: int | None = VALUE_LIMIT) -> str:
    """The fact as a sentence: its subject, then what it says (values shortened to <limit>)."""
    words = []
    for kind, text in sentence_parts(kb, fact):
        if kind == ENTITY:
            words.append(km.label(kb, text))
        else:
            words.append(text if kind == TEXT or limit is None else short(text, limit))
    return f"{km.label(kb, fact['subject'])} {''.join(words)}"


def statement(kb: KB, fact: Record, limit: int | None = VALUE_LIMIT) -> str:
    """The fact as a line: predicate and object (or value), with its order if any. <limit> shortens
    the value; None keeps all of it (the console, where a reviewer must read the whole fact)."""
    value = str(fact.get("value") or "")
    shown = value if limit is None else short(value, limit)
    target = km.label(kb, fact["object"]) if fact.get("object") else repr(shown)
    order = f" (step {fact['order']})" if fact.get("order") else ""
    return f"{fact['predicate']} {target}{order}"


def sources_text(fact: Record, limit: int = 2) -> str:
    refs = [f"{s['kind']}:{short(s['ref'], 40)}" for s in fact["sources"][:limit]]
    more = len(fact["sources"]) - limit
    return "; ".join(refs) + (f"; +{more}" if more > 0 else "")


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def render_batch(kb: KB, groups: list[Group], total: int) -> str:
    if not groups:
        return "Nothing to review: no proposed facts wait."
    facts = sum(len(g.fact_ids) for g in groups)
    later = total - len(groups)
    lines = [
        f"Knowledge to review: {plural(len(groups), 'group')}, {plural(facts, 'fact')}"
        + (
            f" ({plural(later, 'more group')} {'waits' if later == 1 else 'wait'} for a later batch)."
            if later > 0
            else "."
        ),
        (
            "Answer in one line: kg batch apply 'confirm all except 2b' (excepted and unanswered facts "
            "stay proposed; 'confirm 1,3 reject 2b' rejects 2b)."
        ),
    ]
    for number, group in enumerate(groups, start=1):
        flag = " [prevents rework]" if group.rework else ""
        lines.append(f"\n{number}. {group.title}: {plural(len(group.fact_ids), 'fact')}{flag}")
        for letter, fact_id in zip(LETTERS, group.fact_ids, strict=False):
            fact = kb.facts[fact_id]
            lines.append(f"   {letter}. {fact_id} {sentence(kb, fact)} [{sources_text(fact)}]")
    return "\n".join(lines)


def batch_record(kb: KB, groups: list[Group]) -> Record:
    return {
        "created_at": km.iso(kb.now),
        "applied_at": None,
        "groups": [{"key": g.key, "title": g.title, "facts": g.fact_ids} for g in groups],
    }


def batches_needed(groups: list[Group], limits: Limits) -> int:
    """How many daily batches it takes to show every group once."""
    count, remaining = 0, groups
    while remaining:
        remaining = remaining[len(select_batch(remaining, limits)) :]
        count += 1
    return count


# ---- decisions ----------------------------------------------------------------------------------

CONFIRM = "confirm"
REJECT = "reject"
SKIP = "skip"
VERBS = {
    "confirm": CONFIRM,
    "yes": CONFIRM,
    "accept": CONFIRM,
    "ok": CONFIRM,
    "reject": REJECT,
    "no": REJECT,
    "drop": REJECT,
    "skip": SKIP,
    "later": SKIP,
}
EXCEPT = "except"
ALL = "all"
FILLER = {"and", "but", "the", "groups", "group", "facts", "fact"}
WORDS = re.compile(r"\d+[a-z]*|[a-z]+")
REF = re.compile(r"^(\d+)([a-z]?)$")


@dataclass
class Clause:
    verb: str
    targets: list[str] = field(default_factory=list)
    excepts: list[str] = field(default_factory=list)
    excepting: bool = False  # parsing: the refs after 'except'


def add_word(clauses: list[Clause], word: str) -> None:
    """A ref, 'all' or 'except' in the current clause."""
    if not clauses:
        raise km.KnowledgeError(f"a decision starts with confirm, reject or skip, not {word!r}")
    clause = clauses[-1]
    if word == EXCEPT:
        clause.excepting = True
    elif word == ALL or REF.match(word):
        (clause.excepts if clause.excepting else clause.targets).append(word)
    else:
        raise km.KnowledgeError(f"cannot read {word!r} in the decision (refs look like 2 or 2b)")


def parse_clauses(text: str) -> list[Clause]:
    """'confirm all except 2b', 'confirm 1,3 reject 2b', 'reject 4; skip 5' as clauses."""
    clauses: list[Clause] = []
    for word in WORDS.findall(text.lower()):
        if word in VERBS:
            clauses.append(Clause(VERBS[word]))
        elif word not in FILLER:
            add_word(clauses, word)
    for clause in clauses:
        if not clause.targets:
            raise km.KnowledgeError(f"'{clause.verb}' names no groups (say all, or numbers like 1,3 or 2b)")
    return clauses


def expand(ref: str, groups: list[list[str]]) -> list[str]:
    """The fact ids a ref names: all, a group number, or a group number and a letter."""
    if ref == ALL:
        return [fact_id for group in groups for fact_id in group]
    match = REF.match(ref)
    number = int(match.group(1)) if match else 0
    if not 1 <= number <= len(groups):
        raise km.KnowledgeError(f"{ref}: the batch has groups 1 to {len(groups)}")
    group = groups[number - 1]
    letter = match.group(2) if match else ""
    if not letter:
        return list(group)
    index = LETTERS.index(letter)
    if index >= len(group):
        raise km.KnowledgeError(f"{ref}: group {number} has facts a to {LETTERS[len(group) - 1]}")
    return [group[index]]


def decide(clauses: list[Clause], groups: list[list[str]]) -> dict[str, str]:
    """fact id -> confirm or reject. Later clauses win; the facts after 'except' are deferred
    (they stay proposed, as with skip): only an explicit 'reject 2b' rejects."""
    decisions: dict[str, str] = {}
    for clause in clauses:
        for ref in clause.targets:
            for fact_id in expand(ref, groups):
                decisions[fact_id] = clause.verb
        for ref in clause.excepts:
            for fact_id in expand(ref, groups):
                decisions[fact_id] = SKIP
    return {fact_id: verb for fact_id, verb in decisions.items() if verb != SKIP}


def flag_clauses(confirm: str | None, reject: str | None, skip: str | None) -> list[Clause]:
    """--confirm 1,3 --reject 2b as clauses."""
    found = []
    for verb, refs in ((CONFIRM, confirm), (REJECT, reject), (SKIP, skip)):
        if refs:
            found.append(Clause(verb, [r for r in re.split(r"[\s,]+", refs.lower()) if r]))
    return found


# ---- lint ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    kind: str
    message: str
    ids: tuple[str, ...] = ()


def stale(kb: KB, days: int) -> list[Finding]:
    limit = timedelta(days=days)
    return [
        Finding(
            "stale",
            f"{f['id']} not observed for over {days} days: {km.label(kb, f['subject'])} {statement(kb, f)}",
            (f["id"],),
        )
        for f in km.confirmed_current(kb)
        if kb.now - km.last_seen(f) > limit
    ]


def contradictions(kb: KB) -> list[Finding]:
    by_slot: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for fact in km.confirmed_current(kb):
        if kb.schema.predicate(fact["predicate"]).single:
            by_slot[(fact["subject"], fact["predicate"])].append(fact)
    found = []
    for (subject, predicate), facts in sorted(by_slot.items()):
        if len({km.key_of(f) for f in facts}) > 1:
            ids = tuple(sorted(f["id"] for f in facts))
            found.append(
                Finding(
                    "contradiction",
                    f"{subject} has {len(facts)} current values for {predicate} ({', '.join(ids)}); "
                    "keep one: kg supersede <old> --by <new>",
                    ids,
                )
            )
    return found


def orphans(kb: KB) -> list[Finding]:
    used = {f["subject"] for f in kb.facts.values()} | {
        f["object"] for f in kb.facts.values() if f.get("object")
    }
    return [Finding("orphan", f"{e} has no facts", (e,)) for e in sorted(kb.entities) if e not in used]


def distinct_pairs(kb: KB) -> set[frozenset[str]]:
    return {
        frozenset((f["subject"], f["object"]))
        for f in km.confirmed_current(kb)
        if f["predicate"] == "distinct_from" and f.get("object")
    }


def first_name(entity: Record) -> str:
    words = km.norm(entity.get("name") or "").split()
    return words[0] if words else ""


def shared(names: dict[str, set[str]]) -> list[tuple[str, tuple[str, ...]]]:
    return [(name, tuple(sorted(ids))) for name, ids in sorted(names.items()) if len(ids) > 1]


def alias_findings(kb: KB) -> list[Finding]:
    """Names and strong aliases two entities share, and people sharing a first name (unless a
    confirmed distinct_from says they are two people)."""
    by_name: dict[str, set[str]] = defaultdict(set)
    by_first: dict[str, set[str]] = defaultdict(set)
    for entity in kb.entities.values():
        for name in km.names_of(entity)[2:]:
            by_name[km.norm(name)].add(entity["id"])
        if entity["type"] == ks.PERSON and first_name(entity):
            by_first[first_name(entity)].add(entity["id"])
    known = distinct_pairs(kb)
    found = [
        Finding("alias-collision", f"{', '.join(ids)} share the name {n!r}", ids)
        for n, ids in shared(by_name)
    ]
    for name, ids in shared(by_first):
        if not all(frozenset(p) in known for p in pairs(list(ids))):
            message = f"{', '.join(ids)} share the first name {name!r}; record distinct_from"
            found.append(Finding("alias-collision", message, ids))
    return found


def pairs(ids: list[str]) -> list[tuple[str, str]]:
    return [(a, b) for index, a in enumerate(ids) for b in ids[index + 1 :]]


def near_duplicates(kb: KB) -> list[Finding]:
    found = []
    entities = sorted(kb.entities.values(), key=lambda e: e["id"])
    for one, other in pairs([e["id"] for e in entities]):
        a, b = kb.entities[one], kb.entities[other]
        if a["type"] != b["type"]:
            continue
        ratio = difflib.SequenceMatcher(None, km.norm(a["name"]), km.norm(b["name"])).ratio()
        if ratio >= NEAR_DUPLICATE_RATIO:
            found.append(Finding("merge-suggestion", f"{one} and {other} may be the same", (one, other)))
    return found


def dangling(kb: KB) -> list[Finding]:
    return [
        Finding("dangling", f"{f['id']} names {ref}, which is not an entity", (f["id"],))
        for f in kb.facts.values()
        for ref in (f["subject"], f.get("object"))
        if ref and ref not in kb.entities
    ]


def lifecycle(kb: KB) -> list[Finding]:
    found = []
    retention = km.past_retention(kb)
    if retention:
        found.append(
            Finding(
                "retention",
                f"{len(retention)} person facts unseen for {km.RETENTION_DAYS} days (kg expire deletes them)",
                tuple(sorted(f["id"] for f in retention)),
            )
        )
    expired = km.stale_proposals(kb)
    if expired:
        found.append(
            Finding(
                "expired",
                f"{len(expired)} proposals unreviewed for {km.PROPOSAL_DAYS} days (kg expire records it)",
                tuple(sorted(f["id"] for f in expired)),
            )
        )
    return found


def lint(kb: KB, stale_days: int = DEFAULT_STALE_DAYS) -> list[Finding]:
    return [
        *contradictions(kb),
        *lifecycle(kb),
        *stale(kb, stale_days),
        *alias_findings(kb),
        *near_duplicates(kb),
        *orphans(kb),
        *dangling(kb),
    ]


def finding_json(finding: Finding) -> dict[str, Any]:
    return {"kind": finding.kind, "message": finding.message, "ids": list(finding.ids)}
