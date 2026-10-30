"""The console's Ledger views: the state groups, list rows with their filters, search and order,
the item page (lineage, links out, outreach, what I said, history) and "needs you". Design:
docs/design/console.md (section 1).

Pure over a loaded ledger (lib/console_data.py reads it as a snapshot), reusing lib/ledger.py's
state machines and lib/lineage.py's links and tree; the time they are relative to is passed in.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


sys.path.insert(0, str(Path(__file__).resolve().parent))
import activity
import approvals
import ledger
import lineage
import tickets
import workstreams
from console_data import Item, Params, QueryError, as_text, web_url
from lineage import Index


# ---- the ledger: state groups ------------------------------------------------------------------

NEEDS_ME = "needs-me"
WAITING = "waiting"
OPEN = "open"
CLOSED = "closed"
GROUPS = {NEEDS_ME: "Needs me", WAITING: "Waiting on someone", OPEN: "Open", CLOSED: "Closed"}
ALL = "all"  # the group filters beyond the groups: everything, and every group but closed
ACTIVE = "active"
GROUP_FILTERS = (ALL, ACTIVE, *GROUPS)
# The states in which an item waits on another person: the author of the PR or design I reviewed,
# the reviewers of my PR, the recipient of outreach that went out. An item also waits while it has
# open outreach or open items it is blocked_by (as `ledger list` shows them).
WAITING_STATES: dict[str, set[str]] = {
    "review-request": {"reviewed"},
    "td-review": {"commented"},
    "my-pr": {"awaiting-review"},
    "outreach": {"sent"},
}


def needs_me_states(kind: str) -> set[str]:
    """The states of <kind> only I move on: entered with a pending proposal (outreach proposed), or
    left only on my word (a proposed task: accepted takes evidence from me)."""
    spec = ledger.KINDS.get(kind, {})
    mine = spec.get("evidence_from_me", set())
    leaving = {source for source, target in spec.get("evidence_required", set()) if target in mine}
    return set(spec.get("requires_proposal", set())) | leaving


def item_open(item: Item) -> bool:
    """ledger.is_open, also for a kind this version does not know (open unless it says closed)."""
    spec = ledger.KINDS.get(str(item.get("kind")))
    return item.get("state") not in (spec["terminal"] if spec else {CLOSED})


def kind_known(item: Item) -> bool:
    return item.get("kind") in ledger.KINDS


@dataclass(frozen=True)
class LedgerView:
    """The ledger's items with what every view derives from all of them: the links, outreach by
    parent, and the tickets whose state disagrees with the work that tracks them."""

    items: dict[str, Item]
    index: Index
    outreach: dict[str, list[Item]]  # every outreach item by parent, oldest first
    mismatches: dict[str, list[str]]  # ticket id -> what to propose
    flagged: dict[str, list[str]]  # the items a mismatch asks me to decide on -> its tickets
    said: list[dict[str, Any]]
    sources: dict[str, Any]
    resolver: workstreams.Resolver  # each item's workstream (explicit, inherited, epic, rule)
    fyi: dict[str, list[str]]  # another team's ticket id -> its mismatches, as FYI (never needs me)

    @classmethod
    def of(
        cls,
        data: Mapping[str, Any],
        rules: workstreams.RuleSet | None = None,
        owned: frozenset[str] = frozenset(),
    ) -> LedgerView:
        """<owned>: the ticket projects we own (tickets.parse_owned); empty, all of them."""
        items: dict[str, Item] = dict(data.get("items") or {})
        index = Index.of(items)
        found = ticket_mismatches(items, index)
        mismatches = {key: hints for key, hints in found.items() if tickets.owned(key, owned)}
        return cls(
            items,
            index,
            outreach_children(items.values()),
            mismatches,
            flagged_items(items, index, mismatches),
            list(data.get("said") or []),
            dict(data.get("sources") or {}),
            workstreams.Resolver(items, rules, index),
            not_ours(found, mismatches),
        )


def not_ours(found: Mapping[str, list[str]], ours: Mapping[str, list[str]]) -> dict[str, list[str]]:
    """The mismatches of tickets another team owns, as FYI lines."""
    return {
        key: [tickets.NOT_OURS + hint for hint in hints] for key, hints in found.items() if key not in ours
    }


def outreach_children(items: Iterable[Item]) -> dict[str, list[Item]]:
    grouped: dict[str, list[Item]] = {}
    for item in sorted(items, key=lambda i: str(i.get("created_at", ""))):
        parent = (item.get("fields") or {}).get("parent")
        if item.get("kind") == ledger.OUTREACH_KIND and parent:
            grouped.setdefault(str(parent), []).append(item)
    return grouped


def ticket_mismatches(items: dict[str, Item], index: Index) -> dict[str, list[str]]:
    """`ledger tickets --mismatch` without the staleness hints: re-reading a ticket is the
    orchestrator's chore, not a decision of mine."""
    found = {}
    for ticket_id, ticket in sorted(items.items()):
        if ticket.get("kind") != ledger.TICKET_KIND:
            continue
        work = ledger.linked_work(items, index, ticket_id)
        hints = tickets.mismatch_hints(ticket, work, item_open, None)
        if hints:
            found[ticket_id] = list(dict.fromkeys(hints))
    return found


def flagged_items(
    items: dict[str, Item], index: Index, mismatches: Mapping[str, list[str]]
) -> dict[str, list[str]]:
    """Which side of each mismatch to change, with the tickets that flag it: an open ticket (move it
    on), or the open work that tracks a closed one (close it, or ask whether the ticket was closed
    by mistake)."""
    flagged: dict[str, list[str]] = {}
    for ticket_id in mismatches:
        if item_open(items[ticket_id]):
            flagged.setdefault(ticket_id, []).append(ticket_id)
            continue
        for work in ledger.linked_work(items, index, ticket_id):
            if item_open(work):
                flagged.setdefault(work["id"], []).append(ticket_id)
    return flagged


def waiting_on(item: Item, view: LedgerView) -> list[dict[str, str]]:
    """Who or what the item waits on: an outreach's recipient once sent; for other items, their open
    outreach (with its recipient) and the open items they are blocked_by."""
    fields = item.get("fields") or {}
    if item.get("kind") == ledger.OUTREACH_KIND:
        sent = item.get("state") in WAITING_STATES[ledger.OUTREACH_KIND]
        return [{"id": "", "who": str(fields.get("to", "")), "state": str(item["state"])}] if sent else []
    found = [
        {"id": o["id"], "who": str(o["fields"].get("to", "")), "state": o["state"]}
        for o in view.outreach.get(item["id"], [])
        if item_open(o)
    ]
    found += [
        {"id": b["id"], "who": "", "state": b["state"]}
        for b in lineage.open_blockers(view.items, view.index, item["id"], item_open)
    ]
    return found


def waits_on_me(item: Item) -> bool:
    """A pending proposal awaiting my approval (approvals.proposal_status: none yet, or its
    approved call failed), or a state only I move on, unless its proposal is approved (bosun
    makes the call) or carried out (closing): an outreach message approved or sent no longer
    waits on me."""
    proposal = item.get("proposal")
    if approvals.needs_you(proposal):
        return True
    handed_off = approvals.proposal_status(proposal) in (approvals.APPROVED_UNUSED, approvals.USED)
    return item.get("state") in needs_me_states(str(item.get("kind"))) and not handed_off


def group_of(item: Item, view: LedgerView) -> str:
    """closed (a terminal state), needs me (a proposal awaiting my approval, a state only I move on,
    a mismatch of a ticket we own to decide), waiting on someone, or open (with me or bosun)."""
    if not item_open(item):
        return CLOSED
    kind, state = str(item.get("kind")), item.get("state")
    if waits_on_me(item) or item.get("id") in view.flagged:
        return NEEDS_ME
    if state in WAITING_STATES.get(kind, set()) or waiting_on(item, view):
        return WAITING
    return OPEN


# ---- the ledger: list rows, filters, search and sorting --------------------------------------


SINCE = re.compile(r"^(?P<count>\d+)(?P<unit>[mhdw])$")
SINCE_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
SORTS = ("updated", "created", "kind")
QUERY_KEYS = ("kind", "group", "window", "person", "q", "since", "sort", "ws")


def parse_since(value: str, now: datetime) -> datetime | None:
    """The cutoff of "updated within": a span back from <now> (90m, 24h, 7d, 2w) or an ISO time
    (without an offset: UTC). Empty: no cutoff."""
    text = value.strip()
    if not text:
        return None
    span = SINCE.match(text)
    if span:
        return now - timedelta(**{SINCE_UNITS[span["unit"]]: int(span["count"])})
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise QueryError(f"since takes a span such as 24h or 7d, or an ISO time, not {value!r}") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def choice(name: str, value: str, allowed: Iterable[str]) -> str:
    if value and value not in allowed:
        raise QueryError(f"unknown {name} {value!r}; one of: {', '.join(allowed)}")
    return value


@dataclass(frozen=True)
class Query:
    """The list's filters, search and order
    (/api/items?kind=&group=&window=&person=&q=&since=&sort=&ws=). Person and search are
    case-insensitive substrings; every word of <q> must occur; ws: a workstream (with the
    workstreams under it) or 'unassigned'."""

    kind: str = ""
    group: str = ALL
    window: str = ""
    person: str = ""
    q: str = ""
    since: datetime | None = None
    sort: str = SORTS[0]
    ws: str = ""
    family: frozenset[str] = frozenset()  # set by items_model: the ws filter with what is under it

    @classmethod
    def parse(cls, params: Params, now: datetime) -> Query:
        value = {key: (params.get(key) or [""])[0].strip() for key in QUERY_KEYS}
        return cls(
            kind=choice("kind", value["kind"], ledger.KINDS),
            group=choice("group", value["group"], GROUP_FILTERS) or ALL,
            window=value["window"],
            person=value["person"].lower(),
            q=value["q"].lower(),
            since=parse_since(value["since"], now),
            sort=choice("sort", value["sort"], SORTS) or SORTS[0],
            ws=value["ws"],
        )

    def group_matches(self, group: str) -> bool:
        return self.group in (ALL, group) or (self.group == ACTIVE and group != CLOSED)

    def matches(self, item: Item, row: dict[str, Any], view: LedgerView) -> bool:
        """Every filter but the group (the list counts the groups of what the others leave)."""
        return self.row_matches(row) and self.item_matches(item, view)

    def row_matches(self, row: dict[str, Any]) -> bool:
        return (
            (not self.kind or row["kind"] == self.kind)
            and (not self.window or row["window"] == self.window)
            and (not self.ws or row["workstream"] in self.wanted_ws)
        )

    @property
    def wanted_ws(self) -> frozenset[str]:
        """The workstream ids the ws filter takes ("" stands for unassigned)."""
        return frozenset({""}) if self.ws == workstreams.UNASSIGNED_NAME else self.family

    def item_matches(self, item: Item, view: LedgerView) -> bool:
        return (
            (not self.person or any(self.person in name.lower() for name in people(item, view)))
            and (self.since is None or updated_after(item, self.since))
            and (not self.q or all(word in haystack(item) for word in self.q.split()))
        )


def updated_after(item: Item, cutoff: datetime) -> bool:
    try:
        return ledger.parse_time(str(item.get("updated_at") or item.get("created_at"))) >= cutoff
    except ValueError:
        return False


def people(item: Item, view: LedgerView) -> list[str]:
    """The people an item names: who asked for it (requester), who outreach went to (its own, or
    that of its outreach items)."""
    fields, delegation = item.get("fields") or {}, item.get("delegation") or {}
    names = [fields.get("requester"), delegation.get("requester"), fields.get("to")]
    names += [o["fields"].get("to") for o in view.outreach.get(str(item.get("id")), [])]
    return [str(name) for name in names if name]


def strings(value: Any) -> Iterator[str]:
    """Every string in a value, nested lists and objects included."""
    if isinstance(value, Mapping):
        for nested in value.values():
            yield from strings(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            yield from strings(nested)
    elif value is not None:
        yield str(value)


def haystack(item: Item) -> str:
    """What the search looks in, lower case: id, title, url, fields, history notes, the latest
    report and the pending proposal."""
    report = item.get("report") or {}
    parts = [
        item.get("id"),
        item.get("title"),
        item.get("url"),
        *strings(item.get("fields")),
        *(entry.get("note") for entry in item.get("history") or []),
        report.get("summary"),
        report.get("open"),
        *(report.get("results") or []),
        (item.get("proposal") or {}).get("action"),
    ]
    return "\n".join(str(part) for part in parts if part).lower()


def row_model(item: Item, view: LedgerView) -> dict[str, Any]:
    """One list row."""
    delegation = item.get("delegation") or {}
    is_open = item_open(item)
    return {
        "id": item["id"],
        "kind": item.get("kind", ""),
        "state": item.get("state", ""),
        "group": group_of(item, view),
        "title": item.get("title", ""),
        "window": str(delegation.get("window", "")),
        "handover": ledger.handover_word(item) if delegation and is_open and kind_known(item) else "",
        "waiting_on": waiting_on(item, view) if is_open else [],
        "updated": item.get("updated_at"),
        "created": item.get("created_at"),
        "url": web_url(item.get("url")),
        "pending": approvals.needs_you(item.get("proposal")),
        "proposal_status": approvals.proposal_label(item.get("proposal")),
        "workstream": row_workstream(item, view),
    }


def row_workstream(item: Item, view: LedgerView) -> str | None:
    """The item's workstream ("" for none); a workstream is its own; a ticket has none (None),
    so the unassigned filter leaves it out."""
    if workstreams.is_workstream(item):
        return str(item["id"])
    return view.resolver.resolve(str(item["id"])).ws if workstreams.in_scope(item) else None


def sorted_rows(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    """Newest first by <sort> time; by kind: kinds in order, each newest updated first."""
    key = "created" if sort == "created" else "updated"
    ordered = sorted(rows, key=lambda r: (str(r[key] or ""), r["id"]), reverse=True)
    return sorted(ordered, key=lambda r: str(r["kind"])) if sort == "kind" else ordered


def with_family(query: Query, view: LedgerView) -> Query:
    """The ws filter resolved to the workstream and those under it; an unknown one is refused."""
    if not query.ws or query.ws == workstreams.UNASSIGNED_NAME:
        return query
    try:
        found = workstreams.find(view.items, query.ws)
    except workstreams.WorkstreamError as exc:
        raise QueryError(str(exc)) from exc
    return dataclasses.replace(query, family=frozenset(workstreams.family(view.items, found)))


def items_model(
    data: Mapping[str, Any],
    query: Query,
    rules: workstreams.RuleSet | None = None,
    owned: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """/api/items: the rows that pass the query, the group counts of those that pass every other
    filter, and the windows and kinds to filter by."""
    view = LedgerView.of(data, rules, owned)
    query = with_family(query, view)
    rows = [row_model(item, view) for item in view.items.values()]
    passing = [row for row in rows if query.matches(view.items[row["id"]], row, view)]
    shown = [row for row in passing if query.group_matches(row["group"])]
    return {
        "total": len(rows),
        "count": len(shown),
        "groups": group_counts(passing),
        "windows": sorted({row["window"] for row in rows} - {""}),
        "kinds": list(ledger.KINDS),
        "workstreams": sorted(workstreams.workstreams(view.items)),
        "rows": sorted_rows(shown, query.sort),
    }


def group_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(GROUPS, 0)
    for row in rows:
        counts[row["group"]] += 1
    return counts


# ---- the ledger: links out (anchors) -----------------------------------------------------------

URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
URL_TRAILING = ".,;:!?)]}>"
SYSTEMS = {"github": "GitHub", "jira": "Jira", "confluence": "Confluence", "slack": "Slack", "other": "Web"}
JIRA_BROWSE = re.compile(r"/browse/[A-Z][A-Z0-9_]+-\d+")
UNSEARCHED_HISTORY = ("at", "event")


def host_in(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def system_of(url: str) -> str:
    """Which system a URL points into: github, slack, jira, confluence, or other."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host_in(host, "github.com"):
        return "github"
    if host_in(host, "slack.com"):
        return "slack"
    if JIRA_BROWSE.search(parts.path) or "jira" in host:
        return "jira"
    if "confluence" in host or (host_in(host, "atlassian.net") and parts.path.startswith("/wiki")):
        return "confluence"
    return "other"


def urls_in(text: str) -> list[str]:
    found = (match.rstrip(URL_TRAILING) for match in URL_IN_TEXT.findall(text))
    return [url for url in found if web_url(url)]


def history_places(item: Item) -> Iterator[tuple[str, str]]:
    for entry in item.get("history") or []:
        for name, value in entry.items():
            if name not in UNSEARCHED_HISTORY:
                yield f"history {entry.get('event')}", " ".join(strings(value))


def record_places(item: Item) -> Iterator[tuple[str, str]]:
    """(where, text) for the item's own parts: url, fields, delegation, report, proposal, links."""
    yield "url", str(item.get("url") or "")
    for name, value in (item.get("fields") or {}).items():
        yield from ((f"field {name}", text) for text in strings(value))
    for part in ("delegation", "report", "proposal"):
        yield from ((part, text) for text in strings(item.get(part)))
    yield from ((f"link {link.get('rel')}", str(link.get("via") or "")) for link in item.get("links") or [])


def source_places(item: Item, view: LedgerView) -> Iterator[tuple[str, str]]:
    """The sources recorded for the item (their keys are Slack permalinks)."""
    for key, source in view.sources.items():
        if isinstance(source, Mapping) and source.get("item") == item.get("id"):
            yield "source", key


def url_places(item: Item, view: LedgerView) -> Iterator[tuple[str, str]]:
    """(where, text) for every part of the item that may hold a URL."""
    yield from record_places(item)
    yield from history_places(item)
    yield from source_places(item, view)


def anchors_model(item: Item, view: LedgerView) -> list[dict[str, Any]]:
    """Every http(s) URL in the item, once, labelled by system, with where it was found."""
    found: dict[str, dict[str, Any]] = {}
    for where, text in url_places(item, view):
        for url in urls_in(text):
            system = system_of(url)
            anchor = found.setdefault(
                url, {"url": url, "system": system, "label": SYSTEMS[system], "where": []}
            )
            if where not in anchor["where"]:
                anchor["where"].append(where)
    return list(found.values())


# ---- the ledger: the item page -----------------------------------------------------------------

HISTORY_MAIN = ("at", "event", "note")
TREE_ALONE = 1  # a tree of the item alone says nothing


def summary(view: LedgerView, item_id: str) -> dict[str, Any]:
    """What a link to another item shows: its kind, state and title, or that it is missing."""
    other = view.items.get(item_id)
    if other is None:
        return {"id": item_id, "missing": True, "kind": "", "state": "", "title": ""}
    return {
        "id": item_id,
        "missing": False,
        **{key: other.get(key, "") for key in ("kind", "state", "title")},
    }


def tree_node(view: LedgerView, item_id: str, how: str, focus: str, shown: set[str]) -> dict[str, Any]:
    """One node of the lineage tree and, the first time it is shown, its descendants."""
    node = {**summary(view, item_id), "how": how, "focus": item_id == focus, "repeat": item_id in shown}
    children: list[dict[str, Any]] = []
    if not node["repeat"]:
        shown.add(item_id)
        children = [
            tree_node(view, child, rel, focus, shown) for child, rel in lineage.downs(view.index, item_id)
        ]
    return {**node, "children": children}


def tree_model(view: LedgerView, item_id: str) -> list[dict[str, Any]]:
    """`ledger tree` as data: from the topmost ancestors down (spawned, produces, outreach); empty
    when the item is alone in it."""
    shown: set[str] = set()
    tree = [tree_node(view, root, "", item_id, shown) for root in lineage.roots(view.index, item_id)]
    return tree if len(shown) > TREE_ALONE else []


def link_chip(view: LedgerView, rel: str, link: lineage.Link, other: str) -> dict[str, Any]:
    return {
        **summary(view, other),
        "rel": rel,
        "label": lineage.PHRASES.get(rel, rel.replace("_", " ")),
        "via": link.via,
        "via_url": web_url(link.via),
        "at": link.at,
    }


def links_model(view: LedgerView, item_id: str) -> list[dict[str, Any]]:
    """The links outside the tree, both ways (the inverse computed): duplicate_of / duplicates,
    folded_into / absorbs, blocked_by / blocks, tracks / tracked_by, relates_to."""
    index = view.index
    chips = [
        link_chip(view, link.rel, link, link.target)
        for link in index.out(item_id)
        if link.rel not in lineage.TREE_DOWN and not link.computed
    ]
    chips += [
        link_chip(view, lineage.RELATIONS[link.rel].inverse, link, link.source)
        for link in index.into(item_id)
        if link.rel in lineage.RELATIONS and link.rel not in lineage.TREE_DOWN and not link.computed
    ]
    return chips


def hints_model(view: LedgerView, item: Item) -> list[str]:
    """`ledger show`'s hints: a task's landed or unlinked PRs, and the mismatches of the tickets
    tracking the item (or of the ticket itself)."""
    if not kind_known(item):
        return []
    if item["kind"] == ledger.TICKET_KIND:
        return [*view.mismatches.get(item["id"], []), *view.fyi.get(item["id"], [])]
    tracking = ledger.tracked_tickets(view.index, item["id"])
    found = ledger.task_hints(view.items, view.index, item)
    found += [hint for ticket in tracking for hint in view.mismatches.get(ticket, [])]
    found += [hint for ticket in tracking for hint in view.fyi.get(ticket, [])]
    return list(dict.fromkeys(found))


def delegation_model(item: Item) -> dict[str, Any] | None:
    """Where the item was handed over, and whether that hand-over still awaits a report."""
    delegation = item.get("delegation")
    if not delegation:
        return None
    active = item_open(item) and kind_known(item)
    return {
        **{key: as_text(value) for key, value in delegation.items()},
        "link_url": web_url(delegation.get("link")),
        "active": active,
        "awaiting_report": ledger.awaiting_report(item) if active else False,
        "handover": ledger.handover_word(item) if active else "",
    }


def report_model(item: Item) -> dict[str, Any] | None:
    report = item.get("report")
    if not report:
        return None
    return {
        "at": report.get("at"),
        "window": report.get("window") or "",
        "summary": report.get("summary", ""),
        "results": [{"text": str(r), "url": web_url(r)} for r in report.get("results") or []],
        "open": report.get("open") or "",
    }


def message_model(message: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ref": str(message.get("ref", "")),
        "url": web_url(message.get("ref")),
        "channel": str(message.get("channel", "")),
        "at": message.get("at"),
    }


def waiting_seconds(item: Item, messages: list[dict[str, Any]], now: datetime) -> int | None:
    """How long outreach that went out has waited since its latest message."""
    if item.get("state") not in WAITING_STATES[ledger.OUTREACH_KIND] or not messages:
        return None
    latest = max(ledger.parse_time(str(m["at"])) for m in messages)
    return max(0, int((now - latest).total_seconds()))


def outreach_model(item: Item, now: datetime) -> dict[str, Any] | None:
    """For out: items: where, to whom, why, what went out (the first message and the nudges after
    it), the reply, and how long it has waited."""
    if item.get("kind") != ledger.OUTREACH_KIND:
        return None
    fields = item.get("fields") or {}
    messages = sorted((m for m in fields.get("messages") or [] if m.get("at")), key=lambda m: str(m["at"]))
    return {
        **outreach_text(fields),
        "options": [str(option) for option in fields.get("options") or []],
        "messages": [message_model(m) for m in messages],
        "nudges": max(0, len(messages) - 1),
        "reply_url": web_url(fields.get("reply_ref")),
        "replied_at": fields.get("replied_at"),
        "waiting_seconds": waiting_seconds(item, messages, now),
    }


OUTREACH_TEXT = ("channel", "to", "purpose", "subject", "details", "expect_by", "answer", "reply_ref")


def outreach_text(fields: Mapping[str, Any]) -> dict[str, Any]:
    """An outreach's one-line fields as text ("" when unset), and its parent's id (None without)."""
    return {key: as_text(fields.get(key) or "") for key in OUTREACH_TEXT} | {
        "parent": str(fields["parent"]) if fields.get("parent") else None
    }


def said_model(view: LedgerView, item_id: str) -> list[dict[str, Any]]:
    """What I typed in a repo window about the item, oldest first, as recorded; <held_only>: the
    window held it next to the item the words were recorded on."""
    return [
        {
            "at": entry.get("at"),
            "window": entry.get("window", ""),
            "text": entry.get("text", ""),
            "held_only": item_id not in (entry.get("items") or []),
        }
        for entry in view.said
        if item_id in activity.said_items(entry)
    ]


def history_model(item: Item) -> list[dict[str, Any]]:
    """The history, oldest first: time, event and note, the rest as name/value details."""
    return [
        {
            "at": entry.get("at"),
            "event": entry.get("event", ""),
            "note": as_text(entry.get("note") or ""),
            "details": [
                {"name": name, "value": as_text(value)}
                for name, value in sorted(entry.items())
                if name not in HISTORY_MAIN
            ],
        }
        for entry in item.get("history") or []
    ]


def fields_model(item: Item) -> list[dict[str, str]]:
    """The fields as name/value text; an outreach's messages show in its outreach block."""
    hidden = {"messages"} if item.get("kind") == ledger.OUTREACH_KIND else set()
    return [
        {"name": name, "value": as_text(value)}
        for name, value in sorted((item.get("fields") or {}).items())
        if name not in hidden
    ]


def proposal_model(item: Item) -> dict[str, Any] | None:
    proposal = item.get("proposal")
    if not proposal:
        return None
    return {
        "action": proposal.get("action", ""),
        "at": proposal.get("proposed_at"),
        "status": approvals.proposal_label(proposal),
        "needs_you": approvals.needs_you(proposal),
    }


def item_model(
    data: Mapping[str, Any],
    item_id: str,
    now: datetime,
    rules: workstreams.RuleSet | None = None,
    owned: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """/api/items/<id>: everything the item page shows; None for an unknown id."""
    view = LedgerView.of(data, rules, owned)
    item = view.items.get(item_id)
    if item is None:
        return None
    return {
        **row_model(item, view),
        "open": item_open(item),
        "url_text": str(item.get("url") or ""),
        "description": str(ledger.KINDS.get(str(item.get("kind")), {}).get("description", "")),
        "fields": fields_model(item),
        "delegation": delegation_model(item),
        "report": report_model(item),
        "outreach": outreach_model(item, now),
        "proposal": proposal_model(item),
        "said": said_model(view, item_id),
        "tree": tree_model(view, item_id),
        "links": links_model(view, item_id),
        "hints": hints_model(view, item),
        "anchors": anchors_model(item, view),
        "history": history_model(item),
        "workstream_how": view.resolver.resolve(item_id).as_dict(),
    }


# ---- the ledger: "needs you" --------------------------------------------------------------------


def summary_fields(item: Item) -> dict[str, Any]:
    return {key: item.get(key, "") for key in ("id", "kind", "state", "title")}


def proposal_entry(item: Item) -> dict[str, Any]:
    proposal = item.get("proposal") or {}
    return {
        **summary_fields(item),
        "action": proposal.get("action", ""),
        "at": proposal.get("proposed_at"),
        "status": approvals.proposal_label(item.get("proposal")),
    }


def outreach_entry(item: Item) -> dict[str, Any]:
    fields = item.get("fields") or {}
    return {
        **proposal_entry(item),
        **{key: as_text(fields.get(key) or "") for key in ("channel", "to", "purpose", "subject")},
        "parent": str(fields["parent"]) if fields.get("parent") else None,
    }


def is_proposed_outreach(item: Item) -> bool:
    return item.get("kind") == ledger.OUTREACH_KIND and waits_on_me(item)


def mismatch_entry(view: LedgerView, item_id: str, ticket_ids: list[str]) -> dict[str, Any]:
    """A flagged item (the side of a mismatch to change), the tickets that flag it and their hints."""
    item = view.items[item_id]
    hints = [hint for ticket in ticket_ids for hint in view.mismatches.get(ticket, [])]
    return {
        **summary_fields(item),
        "url": web_url(item.get("url")),
        "tickets": ticket_ids,
        "hints": list(dict.fromkeys(hints)),
    }


def open_by_proposal_time(view: LedgerView) -> list[Item]:
    return sorted(
        (item for item in view.items.values() if item_open(item)),
        key=lambda i: str((i.get("proposal") or {}).get("proposed_at", "")),
    )


def pending_entries(view: LedgerView) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The open items with a proposal awaiting my approval (oldest first), and the outreach awaiting
    my approval."""
    pending = open_by_proposal_time(view)
    proposals = [
        proposal_entry(i)
        for i in pending
        if approvals.needs_you(i.get("proposal")) and i.get("kind") != ledger.OUTREACH_KIND
    ]
    return proposals, [outreach_entry(i) for i in pending if is_proposed_outreach(i)]


def in_flight_entries(view: LedgerView) -> list[dict[str, Any]]:
    """Pending proposals I approved: the call not made yet, or made and closing. Shown, not counted."""
    return [
        proposal_entry(i) for i in open_by_proposal_time(view) if i.get("proposal") and not waits_on_me(i)
    ]


def needs_you_model(
    data: Mapping[str, Any],
    windows: list[dict[str, Any]] | None = None,
    owned: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """/api/needs-you: what waits on me. Proposals awaiting my approval with the exact action
    (oldest first; one whose approved call failed too, it needs a new approval), outreach whose
    message awaits my approval, the items a mismatch of a ticket we own (<owned>) asks me to decide
    on (one entry per item, as the list's "needs me" group counts them), and the windows waiting at
    a prompt (lib/console_sessions.py; None when the live readers could not say). The count is of
    distinct items, plus the windows. Approved proposals (the call not made yet, or made and
    closing) are listed under in_flight, not counted."""
    view = LedgerView.of(data, owned=owned)
    proposals, outreach = pending_entries(view)
    mismatches = [mismatch_entry(view, item_id, view.flagged[item_id]) for item_id in sorted(view.flagged)]
    items = {entry["id"] for entry in [*proposals, *outreach, *mismatches]}
    return {
        "count": len(items) + len(windows or []),
        "items": len(items),
        "proposals": proposals,
        "outreach": outreach,
        "mismatches": mismatches,
        "in_flight": in_flight_entries(view),
        "windows": windows,
    }
