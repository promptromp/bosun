"""The closed schema of the knowledge base (design: docs/design/knowledge.md): entity types per
axis, predicates (what they connect, whether they hold one value, how risky an unreviewed one
is), the People whitelist, source kinds and their trust, authority levels and sensitivity.

Pure: lib/knowledge.py stores and queries facts and calls check_fact() before anything is
written. A site may add entity types and predicates in $BOSUN_CONFIG_DIR/knowledge/schema.json
(load_site_schema); it can never add a predicate about a person, so the People whitelist stays
the one list below.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


PEOPLE = "people"
PROCESS = "process"
PRODUCT = "product"
AXES = (PEOPLE, PROCESS, PRODUCT)

PERSON = "person"
TEAM = "team"

ENTITY_TYPES: dict[str, str] = {
    PERSON: PEOPLE,
    TEAM: PEOPLE,
    "process": PROCESS,
    "step": PROCESS,
    "gate": PROCESS,
    "channel": PROCESS,
    "system": PROCESS,
    "repo": PRODUCT,
    "service": PRODUCT,
    "environment": PRODUCT,
    "feature": PRODUCT,
    "decision": PRODUCT,
    "principle": PRODUCT,
    "lesson": PRODUCT,
}

SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# How bad an unreviewed fact would be: only low-risk facts are confirmed by two sources alone.
LOW = "low"
HIGH = "high"

# Who stands behind a fact, strongest first.
USER_SAID = "user_said"
STAKEHOLDER_SAID = "stakeholder_said"
OBSERVED = "observed"
INFERRED = "inferred"
AUTHORITIES = (USER_SAID, STAKEHOLDER_SAID, OBSERVED, INFERRED)
STATED = frozenset({USER_SAID, STAKEHOLDER_SAID})

# Where a fact came from. Text other people wrote (Slack, PRs, tickets, documents) is untrusted:
# it never counts toward confirming a fact on its own (anyone could plant a "fact" there).
SOURCE_KINDS: dict[str, bool] = {
    "user": True,  # my own words; counts only once the quote is verified in a transcript
    "ledger": True,
    "report": True,  # a repo session's report, a learning run's report
    "memory": True,  # a Claude Code memory note on this machine
    "transcript": True,  # what happened in a session on this machine
    "profile": True,  # a structured profile field (directory title, timezone)
    "slack": False,
    "pr": False,
    "ticket": False,
    "doc": False,
}
USER_SOURCE = "user"
PROFILE_SOURCE = "profile"

INTERNAL = "internal"
PERSONAL = "personal"
RESTRICTED = "restricted"
SENSITIVITIES = (INTERNAL, PERSONAL, RESTRICTED)

ANY_NON_PEOPLE = frozenset(name for name, axis in ENTITY_TYPES.items() if axis != PEOPLE)
PEOPLE_TYPES = frozenset({PERSON, TEAM})
WORK_AREAS = frozenset({"repo", "service", "feature", "process", "system", "environment", "channel", "gate"})


TARGET = "{target}"  # in a predicate's sentence: its object or value
WHEN = "{when}"  # in a predicate's sentence: when it took effect (valid_from)


@dataclass(frozen=True)
class Predicate:
    """One predicate: the subject types it applies to, the entity types its object may have
    (empty: none), whether it takes a literal value instead, and how it behaves."""

    name: str
    subjects: frozenset[str]
    objects: frozenset[str] = frozenset()
    value: bool = False
    single: bool = False  # one current value per subject; a second is a contradiction
    risk: str = LOW
    profile: bool = False  # a structured profile source may confirm it on its own
    stated: bool = False  # only what the person (or I) said, never observed or inferred
    description: str = ""
    # How a page says it, after the subject: "{target}" is the object or value, "{when}" the date
    # a decision took effect (" on Oct 15, 2026"). A site predicate without one: "<name>: {target}".
    sentence: str = ""

    @property
    def phrase(self) -> str:
        return self.sentence or f"{self.name.replace('_', ' ')}: {TARGET}"


def _p(name: str, subjects: set[str] | frozenset[str], description: str, **options: Any) -> Predicate:
    objects = frozenset(options.pop("objects", ()))
    return Predicate(name, frozenset(subjects), objects, description=description, **options)


PREDICATE_LIST = (
    # People: the whitelist. Factual and work-operational only, written so the person could read it.
    _p(
        "member_of",
        {PERSON},
        "the team a person belongs to",
        objects={TEAM},
        sentence="is a member of {target}",
    ),
    _p(
        "role",
        PEOPLE_TYPES,
        "their work role (for example 'PM for the exporter')",
        value=True,
        sentence="has the role: {target}",
    ),
    _p(
        "title",
        {PERSON},
        "their job title",
        value=True,
        single=True,
        profile=True,
        sentence="has the title {target}",
    ),
    _p(
        "timezone",
        {PERSON},
        "their timezone",
        value=True,
        single=True,
        profile=True,
        sentence="works in time zone {target}",
    ),
    _p(
        "stated_working_hours",
        {PERSON},
        "working hours they stated",
        value=True,
        single=True,
        stated=True,
        sentence="said their working hours are {target}",
    ),
    _p(
        "prefers_channel",
        PEOPLE_TYPES,
        "where they prefer to be asked",
        value=True,
        objects={"channel"},
        sentence="prefers to be asked in {target}",
    ),
    _p(
        "stated_preference",
        PEOPLE_TYPES,
        "a work preference they stated",
        value=True,
        stated=True,
        sentence="said they prefer: {target}",
    ),
    _p(
        "reviews",
        PEOPLE_TYPES,
        "an area or repo they review",
        value=True,
        objects=WORK_AREAS,
        sentence="reviews {target}",
    ),
    _p(
        "owns",
        PEOPLE_TYPES,
        "an area they own",
        value=True,
        objects=ANY_NON_PEOPLE,
        risk=HIGH,
        sentence="owns {target}",
    ),
    _p(
        "approves",
        PEOPLE_TYPES,
        "what they approve",
        value=True,
        objects=ANY_NON_PEOPLE,
        risk=HIGH,
        sentence="approves {target}",
    ),
    _p(
        "alias_of",
        PEOPLE_TYPES,
        "the same person or team as",
        objects=PEOPLE_TYPES,
        single=True,
        sentence="is the same as {target}",
    ),
    _p(
        "distinct_from",
        PEOPLE_TYPES,
        "not the same as (despite a similar name)",
        objects=PEOPLE_TYPES,
        sentence="is not the same as {target}",
    ),
    # Process.
    _p(
        "has_step",
        {"process"},
        "a step of the process (ordered)",
        objects={"step", "gate"},
        sentence="includes the step {target}",
    ),
    _p(
        "actor",
        {"process", "step", "gate"},
        "who carries it out",
        objects={PERSON, TEAM, "system"},
        sentence="is carried out by {target}",
    ),
    _p(
        "gated_by",
        {"process", "step"},
        "a gate it waits for",
        objects={"gate"},
        risk=HIGH,
        sentence="waits for the gate {target}",
    ),
    _p(
        "entry_point",
        {"process"},
        "where it starts (a form, a channel)",
        value=True,
        objects={"channel", "system"},
        single=True,
        sentence="starts at {target}",
    ),
    _p(
        "uses",
        {"process", "step", "gate", "repo", "service", "feature", "system"},
        "a system or service it relies on",
        objects={"system", "service", "repo"},
        sentence="relies on {target}",
    ),
    _p(
        "posts_to",
        {"process", "step", "gate", "system", "service"},
        "a channel it posts to",
        objects={"channel"},
        sentence="posts to {target}",
    ),
    _p(
        "escalates_to",
        {"process", "gate", "channel", "system"},
        "where problems go",
        objects={PERSON, TEAM, "channel"},
        risk=HIGH,
        sentence="sends problems to {target}",
    ),
    _p(
        "purpose",
        ANY_NON_PEOPLE | {TEAM},
        "what it is for",
        value=True,
        single=True,
        sentence="is for: {target}",
    ),
    _p(
        "rule",
        ANY_NON_PEOPLE - {"decision"},
        "a standing rule or convention",
        value=True,
        risk=HIGH,
        sentence="has the rule: {target}",
    ),
    _p("caveat", ANY_NON_PEOPLE, "a known gotcha", value=True, sentence="has a gotcha: {target}"),
    # Product.
    _p(
        "depends_on",
        {"repo", "service", "feature", "environment"},
        "what it depends on",
        objects={"repo", "service", "system", "feature"},
        sentence="depends on {target}",
    ),
    _p(
        "replicates_from",
        {"environment", "service"},
        "where its data comes from",
        objects={"environment", "service"},
        single=True,
        sentence="gets its data from {target}",
    ),
    _p(
        "status",
        {"environment", "service", "feature", "repo", "process", "decision", "system"},
        "its current status (for example retired)",
        value=True,
        single=True,
        risk=HIGH,
        sentence="is currently: {target}",
    ),
    _p(
        "decided",
        {"decision"},
        "the ruling, dated by valid_from",
        value=True,
        single=True,
        risk=HIGH,
        sentence="was decided{when}: {target}",
    ),
    _p(
        "supersedes",
        {"decision"},
        "an earlier decision it replaces",
        objects={"decision"},
        risk=HIGH,
        sentence="replaces the earlier decision {target}",
    ),
    _p(
        "partially_reversed_by",
        {"decision"},
        "a later decision that undid part of it",
        objects={"decision"},
        risk=HIGH,
        sentence="was partly undone by {target}",
    ),
    _p(
        "states",
        {"principle", "lesson"},
        "what it says",
        value=True,
        single=True,
        risk=HIGH,
        sentence="says: {target}",
    ),
    _p(
        "learned_from",
        {"lesson", "principle", "decision"},
        "the episode behind it",
        value=True,
        sentence="was learned from: {target}",
    ),
    _p(
        "about",
        {"decision", "principle", "lesson"},
        "what it concerns",
        objects=ANY_NON_PEOPLE,
        sentence="concerns {target}",
    ),
    _p(
        "part_of",
        {"step", "gate", "feature", "service", "repo", "environment", "system"},
        "what it belongs to",
        objects={"process", "feature", "service", "system", "repo"},
        sentence="is part of {target}",
    ),
    _p(
        "lives_in",
        {"feature", "service"},
        "the repo that holds it",
        objects={"repo"},
        sentence="lives in the repo {target}",
    ),
)


class SchemaError(Exception):
    """A fact or entity the schema refuses; `whitelist` marks the People whitelist's refusals."""

    def __init__(self, message: str, *, whitelist: bool = False) -> None:
        super().__init__(message)
        self.whitelist = whitelist


@dataclass(frozen=True)
class Schema:
    entity_types: dict[str, str] = field(default_factory=lambda: dict(ENTITY_TYPES))
    predicates: dict[str, Predicate] = field(default_factory=lambda: {p.name: p for p in PREDICATE_LIST})

    @property
    def people_whitelist(self) -> frozenset[str]:
        """The predicates a person may be the subject of."""
        return frozenset(n for n, p in self.predicates.items() if PERSON in p.subjects)

    def axis(self, entity_type: str) -> str:
        return self.entity_types.get(entity_type, PRODUCT)

    def predicate(self, name: str) -> Predicate:
        if name not in self.predicates:
            raise SchemaError(f"unknown predicate {name!r}; known: {', '.join(sorted(self.predicates))}")
        return self.predicates[name]


DEFAULT = Schema()


# ---- entity ids ---------------------------------------------------------------------------------


def split_id(entity_id: str, schema: Schema = DEFAULT) -> tuple[str, str]:
    """(type, slug) of an entity id such as person/dana; SchemaError if it is not one."""
    entity_type, sep, slug = entity_id.partition("/")
    if not sep or entity_type not in schema.entity_types or not SLUG.match(slug):
        raise SchemaError(
            f"{entity_id!r} is not an entity id: <type>/<slug>, type one of "
            f"{', '.join(sorted(schema.entity_types))}, slug lowercase letters, digits, '.', '_', '-'"
        )
    return entity_type, slug


def entity_type(entity_id: str) -> str:
    return entity_id.partition("/")[0]


def is_person(entity_id: str | None) -> bool:
    return bool(entity_id) and entity_type(str(entity_id)) == PERSON


# ---- facts --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """What check_fact looks at: the triple, its authority and where it came from."""

    subject: str
    predicate: str
    object: str | None = None
    value: str | None = None
    authority: str = OBSERVED
    source_kinds: tuple[str, ...] = ()


def check_people(claim: Claim, predicate: Predicate) -> None:
    """The People whitelist: a person is the subject only of whitelisted predicates, the object
    only of predicates that name who does something, and never of an inferred fact (check_fact
    has already refused a predicate outside the whitelist)."""
    involves_person = is_person(claim.subject) or is_person(claim.object)
    if involves_person and claim.authority == INFERRED:
        raise SchemaError(
            "people whitelist: nothing inferred is kept about a person, only what was stated or observed",
            whitelist=True,
        )
    if is_person(claim.subject) and predicate.stated and claim.authority not in STATED:
        raise SchemaError(
            f"people whitelist: {claim.predicate!r} records only what the person (or you) stated "
            f"(--authority {USER_SAID} or {STAKEHOLDER_SAID})",
            whitelist=True,
        )


def check_object(claim: Claim, predicate: Predicate) -> None:
    has_object, has_value = claim.object is not None, claim.value is not None
    if has_object == has_value:
        raise SchemaError(
            f"{claim.predicate!r} takes either an object entity or a --value, not both or neither"
        )
    if has_value and not predicate.value:
        raise SchemaError(
            f"{claim.predicate!r} links to an entity ({', '.join(sorted(predicate.objects))}), not a value"
        )
    if has_value and not str(claim.value).strip():
        raise SchemaError(f"{claim.predicate!r} needs a non-empty value")
    if has_object and entity_type(str(claim.object)) not in predicate.objects:
        whitelist = is_person(claim.object)
        prefix = "people whitelist: " if whitelist else ""
        allowed = ", ".join(sorted(predicate.objects)) or "none (it takes a --value)"
        raise SchemaError(
            f"{prefix}{claim.predicate!r} does not link to a {entity_type(str(claim.object))} "
            f"(objects: {allowed})",
            whitelist=whitelist,
        )


def check_fact(claim: Claim, schema: Schema = DEFAULT) -> Predicate:
    """The predicate of a valid claim; SchemaError says why one is refused."""
    split_id(claim.subject, schema)
    if is_person(claim.subject) and claim.predicate not in schema.people_whitelist:
        raise SchemaError(
            f"people whitelist: {claim.predicate!r} is not a fact kept about a person (allowed: "
            f"{', '.join(sorted(schema.people_whitelist))})",
            whitelist=True,
        )
    predicate = schema.predicate(claim.predicate)
    if claim.authority not in AUTHORITIES:
        raise SchemaError(f"unknown authority {claim.authority!r}; one of {', '.join(AUTHORITIES)}")
    for kind in claim.source_kinds:
        if kind not in SOURCE_KINDS:
            raise SchemaError(f"unknown source kind {kind!r}; one of {', '.join(SOURCE_KINDS)}")
    check_people(claim, predicate)
    if entity_type(claim.subject) not in predicate.subjects:
        raise SchemaError(
            f"{claim.predicate!r} does not apply to a {entity_type(claim.subject)} "
            f"(subjects: {', '.join(sorted(predicate.subjects))})"
        )
    if claim.object is not None:
        split_id(claim.object, schema)
    check_object(claim, predicate)
    return predicate


def sensitivity_of(subject: str, obj: str | None) -> str:
    """A fact about a person (or naming one) is personal data; everything else is internal."""
    return PERSONAL if is_person(subject) or is_person(obj) else INTERNAL


def trusted(kind: str) -> bool:
    return SOURCE_KINDS.get(kind, False)


# ---- site extension -----------------------------------------------------------------------------


def site_predicate(name: str, spec: dict[str, Any], types: dict[str, str]) -> Predicate:
    """One predicate from schema.json; never one about a person, never a redefinition."""
    if name in DEFAULT.predicates:
        raise SchemaError(f"schema.json: {name!r} is a built-in predicate and cannot be redefined")
    subjects = frozenset(spec.get("subjects") or ())
    objects = frozenset(spec.get("objects") or ())
    if PERSON in subjects or PERSON in objects:
        raise SchemaError(f"schema.json: {name!r} names a person; the People whitelist is not extensible")
    unknown = sorted((subjects | objects) - set(types))
    if not subjects or unknown:
        raise SchemaError(f"schema.json: {name!r} needs known subject types (unknown: {unknown})")
    risk = spec.get("risk", HIGH)
    if risk not in (LOW, HIGH):
        raise SchemaError(f"schema.json: {name!r} risk must be {LOW} or {HIGH}")
    return Predicate(
        name,
        subjects,
        objects,
        value=bool(spec.get("value", not objects)),
        single=bool(spec.get("single", False)),
        risk=risk,
        description=str(spec.get("description", "")),
        sentence=site_sentence(name, spec),
    )


def site_sentence(name: str, spec: dict[str, Any]) -> str:
    """A site predicate's own sentence, if it gives one: it must say where the target goes."""
    sentence = str(spec.get("sentence", ""))
    if sentence and TARGET not in sentence:
        raise SchemaError(f"schema.json: {name!r} sentence must contain {TARGET}")
    return sentence


def site_types(spec: dict[str, Any]) -> dict[str, str]:
    types = dict(ENTITY_TYPES)
    for name, axis in (spec.get("entity_types") or {}).items():
        if name in ENTITY_TYPES or not SLUG.match(name) or axis not in (PROCESS, PRODUCT):
            raise SchemaError(
                f"schema.json: entity type {name!r} must be new, a slug, and on the process or product axis"
            )
        types[name] = axis
    return types


def extend(spec: dict[str, Any]) -> Schema:
    """The default schema plus a site's entity types and predicates."""
    types = site_types(spec)
    predicates = {p.name: p for p in PREDICATE_LIST}
    for name, options in (spec.get("predicates") or {}).items():
        predicates[name] = site_predicate(name, options, types)
    for name in spec.get("single_valued") or ():
        if name not in predicates:
            raise SchemaError(f"schema.json: single_valued names unknown predicate {name!r}")
        predicates[name] = replace(predicates[name], single=True)
    return Schema(types, predicates)


def load_site_schema(path: Path) -> Schema:
    """The schema with the site's schema.json applied, if there is one."""
    try:
        spec = json.loads(path.read_text())
    except FileNotFoundError:
        return DEFAULT
    except json.JSONDecodeError as exc:
        raise SchemaError(f"{path} is not valid JSON ({exc})") from exc
    if not isinstance(spec, dict):
        raise SchemaError(f"{path} must hold a JSON object")
    return extend(spec)
