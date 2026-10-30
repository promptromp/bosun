"""Workstreams: the effort a ledger item is part of (design: docs/design/workstreams.md).

A workstream is a ledger item (`ws:<slug>`, kind workstream). An item belongs to at most one,
resolved on read in this order: its own `part_of` link (explicit), the workstream of where it came
from (inherited through lineage: spawned_from, produced_by, an outreach's parent; then what it was
folded into), the tracked ticket whose epic (or the ticket itself) a workstream names, the site's
rules ($BOSUN_CONFIG_DIR/workstreams.json) and a workstream's own Slack channel; otherwise it is
unassigned. When the evidence at one level disagrees, the item is unassigned with the candidates
named, never guessed. Nothing resolved here is stored; `ledger assign --apply` turns resolutions
into explicit links on the topmost items only.

Pure functions over the ledger's items: lib/ledger.py reads the rules file, stores and prints.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import lineage
from lineage import Index


Item = dict[str, Any]
Items = Mapping[str, Item]


class WorkstreamError(Exception):
    pass


KIND = lineage.WORKSTREAM_KIND
PREFIX = "ws:"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
ACTIVE, PAUSED, DONE = "active", "paused", "done"
STATES = (ACTIVE, PAUSED, DONE)
RULES_FILE = "workstreams.json"
# Kinds that are not work: a workstream is the container, a ticket mirrors a tracker.
OUT_OF_SCOPE = (KIND, lineage.TICKET_KIND)
UNASSIGNED_NAME = "unassigned"  # `--ws unassigned`

EXPLICIT = "explicit"
INHERITED = "inherited"
EPIC = "epic"
RULE = "rule"
UNASSIGNED = "unassigned"
CONFLICT = "conflict"
BASES = (EXPLICIT, INHERITED, EPIC, RULE, UNASSIGNED, CONFLICT)

# A ticket's parent chain is followed this far (sub-task -> story -> epic).
TICKET_PARENT_HOPS = 3
PARENT_TICKET_FIELD = "parent_ticket"
# Where an item names its origin's Slack channel or its repository.
SLACK_CHANNEL = re.compile(r"/archives/(?P<channel>[A-Z0-9]+)/p\d")
MENTION_ID = re.compile(r"^slack:(?P<channel>[A-Z0-9]+)/")
REPO_ID = re.compile(r"^(?:review|mypr|ghissue):(?P<repo>[^/#]+/[^#]+)#\d+$")
GITHUB_URL = re.compile(r"https://github\.com/(?P<repo>[^/\s]+/[^/\s]+)/(?:pull|issues)/\d+")
CHANNEL_REF_FIELDS = ("request_permalink", "reply_ref", "via")
TEXT_FIELDS = ("subject",)
RULE_KEYS = {"ws", "repo", "title", "channel", "kind", "note"}
# Lint: how many open unassigned roots created in a week make a finding, if more than the week before.
UNASSIGNED_WEEK_MIN = 3
# ... counting only my own work: reviews asked of me and mentions often belong to no effort of mine.
GROWTH_KINDS = ("my-pr", "task", "outreach")
WEEK = timedelta(days=7)


# ---- workstreams and their hierarchy -------------------------------------------------------------


def ws_id(name: str) -> str:
    """The id for a slug or id given on the command line (ws:<slug>), refused if malformed."""
    slug = name.removeprefix(PREFIX)
    if not SLUG.match(slug):
        raise WorkstreamError(
            f"workstream ids look like ws:<slug> (lower case letters, digits, dashes), not {name!r}"
        )
    return PREFIX + slug


def is_workstream(item: Item | None) -> bool:
    return bool(item) and (item or {}).get("kind") == KIND


def workstreams(items: Items) -> dict[str, Item]:
    return {item_id: item for item_id, item in items.items() if is_workstream(item)}


def aliases(item: Item) -> list[str]:
    return [str(alias) for alias in (item.get("fields") or {}).get("aliases") or []]


def find(items: Items, name: str) -> str:
    """The workstream a name stands for: its id, its slug, or one of its aliases (any case)."""
    wanted = name.strip()
    if wanted in items and is_workstream(items[wanted]):
        return wanted
    if PREFIX + wanted in items and is_workstream(items[PREFIX + wanted]):
        return PREFIX + wanted
    folded = wanted.removeprefix(PREFIX).casefold()
    found = [ws for ws, item in workstreams(items).items() if folded in {a.casefold() for a in aliases(item)}]
    if len(found) == 1:
        return found[0]
    known = ", ".join(sorted(workstreams(items))) or "none yet (bosun ledger ws add <slug> --title ...)"
    raise WorkstreamError(f"no workstream {name!r}; workstreams: {known}")


def alias_owner(items: Items, alias: str) -> str | None:
    """The workstream that already answers to <alias> (as an id, slug or alias), if any."""
    folded = alias.casefold()
    for ws, item in workstreams(items).items():
        if folded in {
            ws.casefold(),
            ws.removeprefix(PREFIX).casefold(),
            *(a.casefold() for a in aliases(item)),
        }:
            return ws
    return None


def parent(items: Items, ws: str) -> str | None:
    found = [p for p in lineage.stored_part_of(items.get(ws) or {}) if is_workstream(items.get(p))]
    return found[0] if found else None


def children(items: Items, ws: str) -> list[str]:
    return sorted(child for child, item in workstreams(items).items() if parent(items, child) == ws)


def family(items: Items, ws: str) -> set[str]:
    """A workstream and every workstream under it (rollups count them all)."""
    found, todo = {ws}, [ws]
    while todo:
        for child in children(items, todo.pop()):
            if child not in found:
                found.add(child)
                todo.append(child)
    return found


def ancestors(items: Items, ws: str) -> list[str]:
    """A workstream and the workstreams above it, nearest first."""
    found = [ws]
    while (up := parent(items, found[-1])) and up not in found:
        found.append(up)
    return found


def nested(items: Items, one: str, other: str) -> bool:
    """Whether one workstream is the other, or above or below it."""
    return one in ancestors(items, other) or other in ancestors(items, one)


def depth(items: Items, ws: str) -> int:
    return len(ancestors(items, ws))


def top_level(items: Items) -> list[str]:
    return sorted(ws for ws in workstreams(items) if not parent(items, ws))


# ---- what an item says about itself: repos, channels, text -----------------------------------------


@dataclass(frozen=True)
class Facts:
    kind: str
    repos: frozenset[str]
    channels: frozenset[str]
    text: str
    created: str


def channels_in(text: str) -> set[str]:
    return {m["channel"] for m in SLACK_CHANNEL.finditer(text or "")}


def item_channels(item_id: str, item: Item) -> set[str]:
    """The Slack channels an item came from: a mention's own, a review request's, an outreach's
    messages and reply, a task's via."""
    fields = item.get("fields") or {}
    mention = MENTION_ID.match(item_id)
    refs = [str(fields.get(name) or "") for name in CHANNEL_REF_FIELDS]
    refs += [*message_refs(fields), str(item.get("url") or "")]
    found = {mention["channel"]} if mention else set()
    return found | {channel for ref in refs for channel in channels_in(ref)}


def message_refs(fields: Mapping[str, Any]) -> list[str]:
    """The refs of an outreach's messages (permalinks, for Slack)."""
    return [str(m.get("ref") or "") for m in fields.get("messages") or [] if isinstance(m, Mapping)]


def item_repos(item_id: str, item: Item) -> set[str]:
    fields = item.get("fields") or {}
    found = set()
    by_id = REPO_ID.match(item_id)
    if by_id:
        found.add(by_id["repo"])
    if fields.get("repo"):
        found.add(str(fields["repo"]))
    by_url = GITHUB_URL.search(str(item.get("url") or ""))
    if by_url:
        found.add(by_url["repo"])
    return found


def item_facts(item_id: str, item: Item) -> Facts:
    fields = item.get("fields") or {}
    text = " ".join([str(item.get("title") or ""), *(str(fields.get(name) or "") for name in TEXT_FIELDS)])
    return Facts(
        str(item.get("kind", "")),
        frozenset(item_repos(item_id, item)),
        frozenset(item_channels(item_id, item)),
        text.casefold(),
        str(item.get("created_at") or ""),
    )


# ---- rules -------------------------------------------------------------------------------------------


def keyword_found(keyword: str, text: str) -> bool:
    """A keyword or phrase, as whole words (any case)."""
    return bool(re.search(rf"(?<!\w){re.escape(keyword.casefold())}(?!\w)", text))


@dataclass(frozen=True)
class Rule:
    """One way to recognise a workstream's items: every criterion given must hold; within one, any
    value. Repos may be globs (acme/widget-*); title takes words or phrases."""

    ws: str
    label: str
    repos: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()

    def matches(self, facts: Facts) -> bool:
        return all(test(self, facts) for given, test in RULE_TESTS if getattr(self, given))

    def why(self) -> str:
        parts = [
            f"{name} {', '.join(values)}"
            for name, values in (
                ("kind", self.kinds),
                ("repo", self.repos),
                ("channel", self.channels),
                ("title", self.titles),
            )
            if values
        ]
        return f"{self.label} ({'; '.join(parts)})"


def kind_matches(rule: Rule, facts: Facts) -> bool:
    return facts.kind in rule.kinds


def repo_matches(rule: Rule, facts: Facts) -> bool:
    return any(fnmatch.fnmatchcase(repo, pattern) for repo in facts.repos for pattern in rule.repos)


def channel_matches(rule: Rule, facts: Facts) -> bool:
    return bool(facts.channels & set(rule.channels))


def title_matches(rule: Rule, facts: Facts) -> bool:
    return any(keyword_found(keyword, facts.text) for keyword in rule.titles)


# A rule's criteria: (the attribute that holds it, its test), each applied only when given.
RULE_TESTS: tuple[tuple[str, Callable[[Rule, Facts], bool]], ...] = (
    ("kinds", kind_matches),
    ("repos", repo_matches),
    ("channels", channel_matches),
    ("titles", title_matches),
)


def strings(value: Any, where: str) -> tuple[str, ...]:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise WorkstreamError(f"{where}: expected a string or a list of strings")
    return tuple(v.strip() for v in values)


def parse_rule(items: Items, raw: Any, number: int) -> Rule:
    where = f"{RULES_FILE} rule {number}"
    if not isinstance(raw, Mapping):
        raise WorkstreamError(f"{where}: expected an object")
    unknown = set(raw) - RULE_KEYS
    if unknown:
        raise WorkstreamError(
            f"{where}: unknown key(s) {', '.join(sorted(unknown))}; keys: {', '.join(sorted(RULE_KEYS))}"
        )
    criteria = {
        key: strings(raw[key], f"{where} {key}") for key in ("repo", "title", "channel", "kind") if key in raw
    }
    if not criteria:
        raise WorkstreamError(f"{where}: give at least one of repo, title, channel, kind")
    if set(criteria) == {"kind"}:
        raise WorkstreamError(
            f"{where}: kind alone would take every item of that kind; add repo, title or channel"
        )
    note = f": {raw['note']}" if isinstance(raw.get("note"), str) and raw["note"] else ""
    return Rule(
        find(items, str(raw.get("ws") or "")),
        f"rule {number}{note}",
        criteria.get("repo", ()),
        criteria.get("title", ()),
        criteria.get("channel", ()),
        criteria.get("kind", ()),
    )


@dataclass
class RuleSet:
    rules: list[Rule] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # rules that could not be used, and why


def parse_rules(items: Items, data: Any) -> RuleSet:
    """The site's rules ({"rules": [...]}), plus a rule for each workstream's own Slack channel(s).
    A rule that cannot be used (unknown workstream, malformed) is left out and named in problems."""
    found = RuleSet(own_channel_rules(items))
    if data is None:
        return found
    raw_rules = data.get("rules") if isinstance(data, Mapping) else None
    if not isinstance(raw_rules, list):
        found.problems.append(f'{RULES_FILE}: expected {{"rules": [...]}}')
        return found
    for number, raw in enumerate(raw_rules, start=1):
        try:
            found.rules.append(parse_rule(items, raw, number))
        except WorkstreamError as exc:
            found.problems.append(str(exc))
    return found


def own_channel_rules(items: Items) -> list[Rule]:
    return [
        Rule(ws, "its channel", channels=tuple(channels))
        for ws, item in sorted(workstreams(items).items())
        if (channels := list((item.get("fields") or {}).get("channels") or []))
    ]


def in_window(ws_item: Item, created: str) -> bool:
    """Whether an item created at <created> falls in the workstream's started..ended dates (open
    ends allowed); rules only apply there."""
    fields = ws_item.get("fields") or {}
    day = created[:10]
    started, ended = str(fields.get("started") or ""), str(fields.get("ended") or "")
    return bool(day) and (not started or day >= started) and (not ended or day <= ended)


# ---- resolution ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    """Which workstream an item belongs to and how that was found. via: the ancestor it was
    inherited from, the ticket chain, or the rule; origin: how the ancestor got it."""

    ws: str = ""
    basis: str = UNASSIGNED
    via: str = ""
    origin: str = ""
    candidates: tuple[str, ...] = ()

    def describe(self) -> str:
        found = {
            EXPLICIT: f"explicit (part_of {self.ws})",
            INHERITED: f"inherited from {self.via} ({self.origin})",
            EPIC: f"epic: tracks {self.via}",
            RULE: f"rule: {self.via}",
        }
        if self.basis == CONFLICT:
            return self.describe_conflict()
        return found.get(self.basis, UNASSIGNED)

    def describe_conflict(self) -> str:
        options = ", ".join(self.candidates)
        if self.origin == INHERITED:
            return f"unassigned: inherited from {self.via}, which could be {options}"
        where = f"where it came from ({self.via})" if self.via else "its own evidence"
        return f"unassigned: {where} points at {options} (pick one with part_of)"

    def as_dict(self) -> dict[str, Any]:
        shown = {
            "id": self.ws,
            "basis": self.basis,
            "how": self.describe(),
            "via": self.via,
            "candidates": list(self.candidates),
        }
        return {key: value for key, value in shown.items() if value}


NONE = Resolution()


def most_specific(items: Items, found: Iterable[str]) -> list[str]:
    """Distinct workstreams, with one dropped where another lies below it (the deeper one is the
    more precise answer, and within the other)."""
    distinct = sorted(set(found))
    return [ws for ws in distinct if not any(o != ws and ws in ancestors(items, o) for o in distinct)]


class Resolver:
    """Resolves every item's workstream once (memoised), over one ledger snapshot."""

    def __init__(self, items: Items, rules: RuleSet | None = None, index: Index | None = None) -> None:
        self.items = items
        self.rules = rules or RuleSet()
        self.index = index or Index.of(items)
        self.memo: dict[str, Resolution] = {}
        self.epics = epic_map(items)

    def resolve(self, item_id: str) -> Resolution:
        return self.walk(item_id, frozenset())

    def walk(self, item_id: str, stack: frozenset[str]) -> Resolution:
        if item_id in self.memo:
            return self.memo[item_id]
        item = self.items.get(item_id)
        if item is None or item.get("kind") in OUT_OF_SCOPE or item_id in stack:
            return NONE
        found = self.explicit(item_id) or self.inherited(item_id, stack | {item_id}) or self.own(item_id)
        self.memo[item_id] = found
        return found

    def explicit(self, item_id: str) -> Resolution | None:
        stored = [
            ws for ws in lineage.stored_part_of(self.items[item_id]) if is_workstream(self.items.get(ws))
        ]
        if len(stored) > 1:
            return Resolution(basis=CONFLICT, candidates=tuple(sorted(stored)))
        return Resolution(stored[0], EXPLICIT) if stored else None

    def parents(self, item_id: str) -> list[str]:
        return lineage.ups(self.index, item_id)

    def carriers(self, item_id: str) -> list[str]:
        """What carries the item on: it was folded into, or is a duplicate of."""
        rels = (lineage.FOLDED_INTO, lineage.DUPLICATE_OF)
        return [link.target for link in self.index.out(item_id) if link.rel in rels]

    def inherited(self, item_id: str, stack: frozenset[str]) -> Resolution | None:
        for sources in (self.parents(item_id), self.carriers(item_id)):
            found = [(source, self.walk(source, stack)) for source in sources]
            found = [(source, r) for source, r in found if r.ws or r.basis == CONFLICT]
            if found:
                return self.combine(found)
        return None

    def combine(self, found: list[tuple[str, Resolution]]) -> Resolution:
        conflicts = [(source, r) for source, r in found if r.basis == CONFLICT]
        if conflicts:
            source, r = conflicts[0]
            return Resolution(basis=CONFLICT, via=source, origin=INHERITED, candidates=r.candidates)
        chosen = most_specific(self.items, (r.ws for _, r in found))
        if len(chosen) > 1:
            sources = ", ".join(source for source, _ in found)
            return Resolution(basis=CONFLICT, via=sources, candidates=tuple(chosen))
        source, r = next((source, r) for source, r in found if r.ws == chosen[0])
        return Resolution(r.ws, INHERITED, source, r.origin if r.basis == INHERITED else r.basis)

    def own(self, item_id: str) -> Resolution:
        """What the item's own evidence says, beneath explicit and inherited: its tickets' epics,
        then the rules."""
        return self.by_epic(item_id) or self.by_rule(item_id) or NONE

    def by_epic(self, item_id: str) -> Resolution | None:
        found: dict[str, str] = {}
        for link in self.index.out(item_id, lineage.TRACKS):
            for ws, chain in ticket_workstreams(self.items, self.epics, link.target):
                found.setdefault(ws, chain)
        chosen = most_specific(self.items, found)
        if len(chosen) > 1:
            return Resolution(basis=CONFLICT, candidates=tuple(chosen))
        return Resolution(chosen[0], EPIC, found[chosen[0]]) if chosen else None

    def by_rule(self, item_id: str) -> Resolution | None:
        facts = item_facts(item_id, self.items[item_id])
        matched: dict[str, Rule] = {}
        for rule in self.rules.rules:
            if rule.matches(facts) and in_window(self.items.get(rule.ws) or {}, facts.created):
                matched.setdefault(rule.ws, rule)
        chosen = most_specific(self.items, matched)
        if len(chosen) > 1:
            return Resolution(basis=CONFLICT, candidates=tuple(chosen))
        return Resolution(chosen[0], RULE, matched[chosen[0]].why()) if chosen else None

    def members(self, wanted: set[str]) -> list[str]:
        """Items resolved into any of the <wanted> workstreams."""
        return [item_id for item_id in self.items if self.resolve(item_id).ws in wanted]

    def unassigned(self) -> list[str]:
        return [
            item_id
            for item_id, item in self.items.items()
            if item.get("kind") not in OUT_OF_SCOPE and not self.resolve(item_id).ws
        ]


def epic_map(items: Items) -> dict[str, list[str]]:
    """Ticket id -> the workstreams that name it as their epic."""
    found: dict[str, list[str]] = {}
    for ws, item in sorted(workstreams(items).items()):
        epic = str((item.get("fields") or {}).get("epic") or "")
        if epic:
            found.setdefault(epic, []).append(ws)
    return found


def ticket_chain(items: Items, ticket_id: str) -> list[str]:
    """A ticket and its parents as ticket-sync recorded them (sub-task -> story -> epic)."""
    chain = [ticket_id]
    while len(chain) <= TICKET_PARENT_HOPS:
        up = str(((items.get(chain[-1]) or {}).get("fields") or {}).get(PARENT_TICKET_FIELD) or "")
        if not up or up in chain:
            break
        chain.append(up)
    return chain


def ticket_workstreams(items: Items, epics: Mapping[str, list[str]], ticket_id: str) -> list[tuple[str, str]]:
    """The workstreams the nearest ticket in the chain maps to, with the chain up to it."""
    chain = ticket_chain(items, ticket_id)
    for position, ticket in enumerate(chain):
        if ticket in epics:
            shown = " -> ".join(chain[: position + 1])
            return [(ws, f"{shown} (the epic of {ws})") for ws in epics[ticket]]
    return []


def in_scope(item: Item) -> bool:
    return item.get("kind") not in OUT_OF_SCOPE


def is_root(resolver: Resolver, item_id: str) -> bool:
    """No lineage parent and nothing it was folded into: where a tag belongs."""
    return not resolver.parents(item_id) and not resolver.carriers(item_id)


# ---- lint ---------------------------------------------------------------------------------------------

WARN, INFO, PROPOSE = "warn", "info", "propose"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    item: str
    message: str

    def line(self) -> str:
        return f"{self.severity}: {self.code}: {self.item + ': ' if self.item else ''}{self.message}"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "item": self.item, "message": self.message}


def link_command(item_id: str, ws: str) -> str:
    return f"bosun ledger link '{item_id}' {lineage.PART_OF} '{ws}'"


def item_findings(resolver: Resolver, item_id: str) -> list[Finding]:
    """What is doubtful about one item's workstream."""
    found = resolver.resolve(item_id)
    if found.basis == CONFLICT and found.origin != INHERITED:  # where it arises, not each descendant
        options = " or ".join(link_command(item_id, ws) for ws in found.candidates)
        return [
            Finding(
                "ambiguous", PROPOSE, item_id, f"evidence points at {', '.join(found.candidates)}: {options}"
            )
        ]
    if found.basis == INHERITED:
        return disagreement(resolver, item_id, found)
    if found.basis == EXPLICIT:
        return override_findings(resolver, item_id, found)
    return []


def disagreement(resolver: Resolver, item_id: str, found: Resolution) -> list[Finding]:
    own = resolver.own(item_id)
    if not own.ws or nested(resolver.items, own.ws, found.ws):
        return []
    return [
        Finding(
            "rule-vs-inherited",
            WARN,
            item_id,
            f"inherits {found.ws} from {found.via}, but its {own.basis} says {own.ws} ({own.describe()}); "
            f"if it belongs there: {link_command(item_id, own.ws)}",
        )
    ]


def override_findings(resolver: Resolver, item_id: str, found: Resolution) -> list[Finding]:
    """An explicit tag below a parent: redundant when the parent says the same, an override when
    it says another workstream (fine when meant, worth a look)."""
    above = resolver.inherited(item_id, frozenset({item_id}))
    if not above or not above.ws:
        return []
    if above.ws == found.ws:
        return [
            Finding(
                "redundant",
                INFO,
                item_id,
                f"inherits {found.ws} from {above.via} anyway; only roots need the tag: "
                f"bosun ledger unlink '{item_id}' {lineage.PART_OF} '{found.ws}'",
            )
        ]
    if nested(resolver.items, above.ws, found.ws):
        return []
    return [
        Finding(
            "overrides-parent",
            WARN,
            item_id,
            f"is part_of {found.ws} while {above.via} (where it came from) is in {above.ws}; "
            "fine if meant, otherwise unlink it",
        )
    ]


def created_between(item: Item, start: datetime, end: datetime) -> bool:
    try:
        created = datetime.fromisoformat(str(item.get("created_at")))
    except ValueError:
        return False
    return start <= created < end


def open_unassigned_root(resolver: Resolver, is_open: Any, item_id: str) -> bool:
    """My own open work (GROWTH_KINDS), in no workstream for lack of evidence (not a conflict),
    with nothing above it."""
    mine = resolver.items[item_id].get("kind") in GROWTH_KINDS
    plain = mine and resolver.resolve(item_id).basis == UNASSIGNED
    return plain and bool(is_open(resolver.items[item_id])) and is_root(resolver, item_id)


def unassigned_growth(resolver: Resolver, is_open: Any, now: datetime) -> list[Finding]:
    """Open unassigned roots: more created this week than the week before (and a few at least)."""
    roots = [resolver.items[i] for i in resolver.unassigned() if open_unassigned_root(resolver, is_open, i)]
    recent = sum(1 for item in roots if created_between(item, now - WEEK, now))
    before = sum(1 for item in roots if created_between(item, now - 2 * WEEK, now - WEEK))
    if recent < UNASSIGNED_WEEK_MIN or recent <= before:
        return []
    return [
        Finding(
            "unassigned-growing",
            WARN,
            "",
            f"{recent} open unassigned item(s) created in the last 7 days ({before} the week before): "
            "bosun ledger assign --dry-run, or add a rule to workstreams.json",
        )
    ]


def workstream_findings(resolver: Resolver, is_open: Any, today: date) -> list[Finding]:
    """A finished workstream with open items; an active one past its target date; an epic named
    by two workstreams."""
    items = resolver.items
    found = [
        finding
        for ws in sorted(workstreams(items))
        for finding in (done_with_open(resolver, is_open, ws), past_target(items[ws], today))
        if finding
    ]
    return found + [
        Finding("epic-claimed-twice", WARN, epic, f"is the epic of {', '.join(claimed)}; keep it on one")
        for epic, claimed in sorted(epic_map(items).items())
        if len(claimed) > 1
    ]


def done_with_open(resolver: Resolver, is_open: Any, ws: str) -> Finding | None:
    if resolver.items[ws].get("state") != DONE:
        return None
    open_members = [i for i in resolver.members({ws}) if is_open(resolver.items[i])]
    if not open_members:
        return None
    many = f"{len(open_members)} open item(s) belong to it, e.g. {open_members[0]}"
    return Finding("done-with-open-items", WARN, ws, f"is done but {many}")


def past_target(item: Item, today: date) -> Finding | None:
    target = str((item.get("fields") or {}).get("target") or "")
    if item.get("state") != ACTIVE or not target or target >= today.isoformat():
        return None
    return Finding(
        "past-target",
        INFO,
        str(item["id"]),
        f"is active past its target date {target}: extend it (ws add --target) or finish it "
        "(ws set-state done)",
    )


def lint(resolver: Resolver, is_open: Any, now: datetime) -> list[Finding]:
    findings = [Finding("rules", WARN, "", problem) for problem in resolver.rules.problems]
    findings += workstream_findings(resolver, is_open, now.date())
    findings += [
        finding
        for item_id in sorted(resolver.items)
        if in_scope(resolver.items[item_id])
        for finding in item_findings(resolver, item_id)
    ]
    return findings + unassigned_growth(resolver, is_open, now)


# ---- bulk assignment ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    item: str
    ws: str
    basis: str
    why: str

    def line(self) -> str:
        return f"{self.item} -> {self.ws} ({self.basis}: {self.why})"


def assign_plan(resolver: Resolver, only: set[str] | None = None) -> tuple[list[Change], list[Finding]]:
    """The part_of links that make epic and rule resolutions explicit, and the uncertain cases as
    proposals instead of changes. An item resolves by its own epic or rules only when nothing it
    came from has a workstream (inherited comes first), so these are the topmost items of their
    lineage: what came from them inherits the tag. <only>: the workstreams to assign to (None: all)."""
    changes, uncertain = [], []
    for item_id in sorted(resolver.items):
        if not in_scope(resolver.items[item_id]):
            continue
        found = resolver.resolve(item_id)
        if found.basis in (EPIC, RULE) and (only is None or found.ws in only):
            changes.append(Change(item_id, found.ws, found.basis, found.via))
        uncertain += [
            f
            for f in item_findings(resolver, item_id)
            if f.severity == PROPOSE and (only is None or set(found.candidates) & only)
        ]
    return changes, uncertain


# ---- counts --------------------------------------------------------------------------------------------


def basis_counts(resolver: Resolver, ids: Iterable[str]) -> dict[str, int]:
    counts = dict.fromkeys(BASES, 0)
    for item_id in ids:
        counts[resolver.resolve(item_id).basis] += 1
    return counts
