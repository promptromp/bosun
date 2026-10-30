"""External tickets (Jira, GitHub issues) mirrored in the ledger (design: docs/design/lineage.md).

A ticket item's state is the tracker's status, mapped to one of our categories. Bosun never
changes a ticket: the orchestrator reads it with the tracker's read tools and records what it
read (`ledger ticket-sync`); where the ticket and our work disagree, the hints here say which
change to propose. Pure functions: lib/ledger.py stores and prints.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


Item = dict[str, Any]
IsOpen = Callable[[Item], bool]


class TicketError(Exception):
    pass


CATEGORIES = ("todo", "in-progress", "in-review", "done", "cancelled")
CLOSED = ("done", "cancelled")
UNSYNCED = "unsynced"
STATUS_MAP_SETTING = "BOSUN_TICKET_STATUS_MAP"
OVERRIDE_SEPARATORS = re.compile(r"[;\n]")
# Our work, against the ticket's category: a PR in review, work in progress, work that landed.
REVIEW_STATES = {"my-pr": {"awaiting-review", "changes-requested", "addressing", "approved"}}
WORKING_STATES = {"task": {"accepted", "delegated"}}
LANDED_STATES = {"my-pr": {"merged"}, "task": {"done"}}


@dataclass(frozen=True)
class Backend:
    """One tracker: its key format, how the orchestrator reads it, how an approved change is
    made, and how its statuses map to our categories (names compared in lower case)."""

    key: re.Pattern[str]
    example: str
    read: str
    write: str
    # The tracker's own status categories (Jira's statusCategory), for --category.
    categories: dict[str, str]
    statuses: dict[str, str] = field(default_factory=dict)

    def project(self, key: str) -> str:
        """Jira: the project key (EXP of EXP-123); GitHub: owner/repo."""
        return key.split("#", 1)[0] if "#" in key else key.rsplit("-", 1)[0]


JIRA_STATUSES = {
    **dict.fromkeys(("to do", "open", "backlog", "new", "selected for development", "reopened"), "todo"),
    **dict.fromkeys(("in progress", "in development", "doing"), "in-progress"),
    **dict.fromkeys(("in review", "code review", "review", "in qa", "qa", "testing"), "in-review"),
    **dict.fromkeys(("done", "closed", "resolved", "released"), "done"),
    **dict.fromkeys(("won't do", "won't fix", "cancelled", "canceled", "declined", "duplicate"), "cancelled"),
}
# A new tracker is one more row here (and its line in the README's tickets section).
BACKENDS: dict[str, Backend] = {
    "jira": Backend(
        key=re.compile(r"^[A-Z][A-Z0-9_]+-\d+$"),
        example="jira:EXP-123",
        read=(
            "the site's Jira read tool (an Atlassian MCP get-issue tool): status name, statusCategory, "
            "summary, assignee, browse URL"
        ),
        write=(
            "the site's Jira transition tool, only after I approved that exact transition (list the "
            "issue's transitions with the read tools first)"
        ),
        categories={
            "to do": "todo",
            "new": "todo",
            "in progress": "in-progress",
            "indeterminate": "in-progress",
        }
        | {"done": "done"},
        statuses=JIRA_STATUSES,
    ),
    "ghissue": Backend(
        key=re.compile(r"^[\w.-]+/[\w.-]+#\d+$"),
        example="ghissue:acme/exporter#7",
        read="gh issue view <n> --repo <owner>/<repo> --json state,stateReason,title,url,assignees",
        write="gh issue close|reopen <n> --repo <owner>/<repo>, only after I approved it",
        categories={"open": "todo", "closed": "done"},
        statuses={"open": "todo", "reopened": "todo", "completed": "done", "not_planned": "cancelled"}
        | {"not planned": "cancelled", "closed": "done"},
    ),
}


@dataclass(frozen=True)
class TicketId:
    system: str
    key: str

    @property
    def project(self) -> str:
        return BACKENDS[self.system].project(self.key)

    def __str__(self) -> str:
        return f"{self.system}:{self.key}"


def parse_id(ticket_id: str) -> TicketId:
    system, sep, key = ticket_id.partition(":")
    if not sep or system not in BACKENDS:
        known = ", ".join(f"{name} ({b.example})" for name, b in BACKENDS.items())
        raise TicketError(f"{ticket_id!r} is not a ticket id; ticket ids are <system>:<key>: {known}")
    backend = BACKENDS[system]
    if not backend.key.match(key):
        raise TicketError(f"{system} ticket ids look like {backend.example}, not {ticket_id!r}")
    return TicketId(system, key)


def ticket_ref(value: str, system: str) -> str:
    """A ticket named as <system>:<key>, or by its bare key in <system> (a Jira parent 'EXP-100'
    of jira:EXP-123 is jira:EXP-100); refused if it is neither."""
    given = value.strip()
    return str(parse_id(given if given.partition(":")[0] in BACKENDS else f"{system}:{given}"))


def is_ticket_id(ticket_id: str) -> bool:
    system = ticket_id.partition(":")[0]
    return system in BACKENDS


def github_repo(ticket: TicketId) -> str | None:
    """owner/repo of a GitHub issue (for the repo mask)."""
    return ticket.project if ticket.system == "ghissue" else None


Overrides = dict[tuple[str, str], dict[str, str]]


def parse_overrides(text: str) -> Overrides:
    """BOSUN_TICKET_STATUS_MAP: '<system>[/<project>]:<status name>=<category>' entries,
    separated by ';' or newlines, e.g. 'jira:Ready for Review=in-review; jira/EXP:On Hold=todo'."""
    overrides: Overrides = {}
    for entry in (e.strip() for e in OVERRIDE_SEPARATORS.split(text or "")):
        if not entry:
            continue
        scope, sep, rest = entry.partition(":")
        status, eq, category = rest.rpartition("=")
        system, _, project = scope.strip().partition("/")
        category = category.strip()
        if not sep or not eq or system not in BACKENDS or category not in CATEGORIES or not status.strip():
            raise TicketError(
                f"{STATUS_MAP_SETTING}: cannot read {entry!r}; entries look like "
                f"'jira[/<project>]:<status name>=<category>', categories: {', '.join(CATEGORIES)}"
            )
        overrides.setdefault((system, project.strip()), {})[status.strip().lower()] = category
    return overrides


def categorize(ticket: TicketId, status: str, hint: str | None, overrides: Overrides) -> str:
    """Our category for a raw status: the site's override (project, then system), the built-in
    status names, then --category (ours, or the tracker's own category); else refused."""
    backend, name = BACKENDS[ticket.system], status.strip().lower()
    for scope in ((ticket.system, ticket.project), (ticket.system, "")):
        if name in overrides.get(scope, {}):
            return overrides[scope][name]
    if name in backend.statuses:
        return backend.statuses[name]
    given = (hint or "").strip().lower()
    category = given if given in CATEGORIES else backend.categories.get(given)
    if category:
        return category
    raise TicketError(
        f"status {status!r} of {ticket} maps to no category; pass --category <{'|'.join(CATEGORIES)}> "
        f"(or {ticket.system}'s own category: {', '.join(sorted(backend.categories))}), or map it in "
        f"{STATUS_MAP_SETTING} (e.g. '{ticket.system}/{ticket.project}:{status}=<category>')"
    )


# ---- hints: where the ticket and our work disagree ---------------------------------------------


def closed(ticket: Item) -> bool:
    return ticket["state"] in CLOSED


def raw(ticket: Item) -> str:
    status = ticket["fields"].get("external_status")
    return f"{ticket['state']} ('{status}')" if status else ticket["state"]


def closing_hints(ticket: Item, linked: list[Item], is_open: IsOpen) -> list[str]:
    if not closed(ticket):
        return []
    return [
        f"{ticket['id']} is {raw(ticket)} but {item['id']} is still {item['state']}: propose closing "
        f"{item['id']} (or ask whether the ticket was closed by mistake)"
        for item in linked
        if is_open(item)
    ]


def landed_hints(ticket: Item, linked: list[Item], is_open: IsOpen) -> list[str]:
    landed = [i for i in linked if i["state"] in LANDED_STATES.get(i["kind"], set())]
    if closed(ticket) or not landed or any(is_open(i) for i in linked):
        return []
    names = ", ".join(f"{i['id']} {i['state']}" for i in landed)
    return [f"{names}, but {ticket['id']} is {raw(ticket)}: propose transitioning {ticket['id']} to done"]


def started_hints(ticket: Item, linked: list[Item]) -> list[str]:
    if ticket["state"] != "todo":
        return []
    for states, target in ((REVIEW_STATES, "in-review"), (WORKING_STATES, "in-progress")):
        busy = [i for i in linked if i["state"] in states.get(i["kind"], set())]
        if busy:
            ours = f"{busy[0]['id']} is {busy[0]['state']}"
            return [f"{ours} but {ticket['id']} is {raw(ticket)}: propose moving it to {target}"]
    return []


def sync_hints(
    ticket: Item, linked: list[Item], is_open: IsOpen, stale_after: timedelta, now: datetime
) -> list[str]:
    if not any(is_open(item) for item in linked):
        return []
    synced = ticket["fields"].get("synced_at")
    if not synced:
        return [f"{ticket['id']} was never synced: read it and record it with bosun ledger ticket-sync"]
    age = now - datetime.fromisoformat(synced)
    if age > stale_after:
        hours = int(age.total_seconds() // 3600)
        return [f"{ticket['id']} was last synced {hours}h ago: read it again (bosun ledger ticket-sync)"]
    return []


@dataclass(frozen=True)
class Staleness:
    after: timedelta
    now: datetime


# ---- owned projects ----------------------------------------------------------------------------

OWNED_SETTING = "BOSUN_TICKET_OWNED_PROJECTS"
OWNED_SEPARATORS = re.compile(r"[\s,]+")
NOT_OURS = "FYI, owned by another team; not ours to move: "


def parse_owned(text: str) -> frozenset[str]:
    """BOSUN_TICKET_OWNED_PROJECTS: project keys (EXP) or owner/repo, separated by spaces or
    commas, compared case-insensitively. Empty: every project is ours."""
    return frozenset(part.casefold() for part in OWNED_SEPARATORS.split(text or "") if part)


def owned(ticket_id: str, projects: frozenset[str]) -> bool:
    """Whether a ticket is in a project we own: always, when none are configured (or the id is not
    one we can read the project of)."""
    if not projects:
        return True
    try:
        project = parse_id(ticket_id).project
    except TicketError:
        return True
    return project.casefold() in projects


def mismatch_hints(
    ticket: Item, linked: Iterable[Item], is_open: IsOpen, stale: Staleness | None
) -> list[str]:
    """What to propose for a ticket, given the work items that track it (and what they produced).
    Never automatic: a change in the tracker is a tier-2 proposal."""
    work = list(linked)
    hints = [
        *closing_hints(ticket, work, is_open),
        *landed_hints(ticket, work, is_open),
        *started_hints(ticket, work),
    ]
    if stale:
        hints += sync_hints(ticket, work, is_open, stale.after, stale.now)
    return hints


@dataclass(frozen=True)
class Hints:
    """A ticket's hints, split: <propose> for one we own, <fyi> (prefixed NOT_OURS) for another
    team's mismatches; re-reading it stays a chore either way."""

    propose: list[str]
    fyi: list[str]

    def lines(self) -> list[str]:
        return [*self.propose, *self.fyi]


def ticket_hints(
    ticket: Item, linked: Iterable[Item], is_open: IsOpen, stale: Staleness | None, projects: frozenset[str]
) -> Hints:
    """mismatch_hints, with the mismatches of a ticket in a project we do not own (<projects>) as
    FYI: never a change to propose, never something that needs me."""
    work = list(linked)
    if owned(str(ticket["id"]), projects):
        return Hints(mismatch_hints(ticket, work, is_open, stale), [])
    chores = sync_hints(ticket, work, is_open, stale.after, stale.now) if stale else []
    mismatches = mismatch_hints(ticket, work, is_open, None)
    return Hints(chores, [NOT_OURS + hint for hint in mismatches])
