"""A stand-up draft for a workstream, from the ledger alone (design: docs/design/workstreams.md):
what shipped and which decisions were recorded since a time, and, as of now, what is in review,
what waits on someone, what is blocked and what comes next.

Pure over the items and a workstreams.Resolver; lib/ledger.py parses the options and prints.
Every open item lands in one section only (blocked, then in review, then waiting, then next).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import lineage
import workstreams as ws_lib
from workstreams import Item, Resolver


IsOpen = Callable[[Item], bool]

SHIPPED = "Shipped"
IN_REVIEW = "In review"
WAITING = "Waiting on others"
DECISIONS = "Decisions"
BLOCKED = "Blocked"
NEXT = "Next"
SECTIONS = (SHIPPED, IN_REVIEW, WAITING, DECISIONS, BLOCKED, NEXT)
UNASSIGNED_TITLE = "Unassigned"

# What counts as shipped: entering one of these states since the start.
SHIPPED_STATES = {
    "my-pr": ("merged", "merged"),
    "task": ("done", "done"),
    "review-request": ("reviewed", "reviewed"),
    "td-review": ("commented", "commented on design"),
}
REVIEW_PHRASES = {
    "awaiting-review": "waiting on reviewers",
    "changes-requested": "changes requested: on me",
    "addressing": "addressing review comments",
    "approved": "approved: ready to merge",
}
NEXT_PHRASES = {
    ("task", "proposed"): "proposed (awaits my yes)",
    ("task", "accepted"): "accepted, to hand over",
    ("task", "delegated"): "in progress",
    ("review-request", "requested"): "review asked of me",
    ("review-request", "rereview-requested"): "re-review asked of me",
    ("review-request", "delegated"): "review in progress",
    ("td-review", "requested"): "design review asked of me",
    ("td-review", "rereview-requested"): "design re-review asked of me",
    ("td-review", "prepared"): "design review prepared",
    ("mention", "new"): "mention to answer",
    ("mention", "surfaced"): "mention to answer",
    ("outreach", "draft"): "to send",
    ("outreach", "proposed"): "to send (awaits my yes)",
    ("outreach", "replied"): "reply to act on",
}
WAITING_STATE = "sent"
GAVE_UP_STATE = "no-reply"
DECISION_PURPOSES = ("decision", "approval", "clarification")
# What a summary's header says about the workstream: (label, field).
HEADER_FIELDS = (("epic", "epic"), ("channel", "channels"), ("repos", "repos"), ("target", "target"))
MONDAY = 0


@dataclass(frozen=True)
class Entry:
    item: str
    text: str
    ws: str = ""
    url: str = ""


@dataclass
class Summary:
    title: str
    ws: str  # "" for the unassigned section
    since: datetime
    header: list[str] = field(default_factory=list)
    sections: dict[str, list[Entry]] = field(default_factory=lambda: {name: [] for name in SECTIONS})

    def counts(self) -> dict[str, int]:
        return {name: len(entries) for name, entries in self.sections.items()}

    def empty(self) -> bool:
        return not any(self.sections.values())


def week_start(now: datetime) -> datetime:
    """Monday 00:00 of <now>'s week, in <now>'s timezone."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=(day.weekday() - MONDAY) % 7)


def moment(value: Any) -> datetime | None:
    try:
        found = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return found if found.tzinfo else found.astimezone()


def entered_since(item: Item, state: str, since: datetime) -> bool:
    for entry in item.get("history") or []:
        at = moment(entry.get("at"))
        if entry.get("event") == "transition" and entry.get("to") == state and at and at >= since:
            return True
    return False


def title_of(item: Item) -> str:
    fields = item.get("fields") or {}
    return str(item.get("title") or fields.get("subject") or item.get("id"))


@dataclass
class Context:
    items: Mapping[str, Item]
    resolver: Resolver
    is_open: IsOpen
    since: datetime
    focus: str  # the workstream summarised ("" for unassigned): entries of others are tagged

    def entry(self, item_id: str, text: str) -> Entry:
        item = self.items[item_id]
        ws = self.resolver.resolve(item_id).ws
        return Entry(item_id, text, ws if ws != self.focus else "", str(item.get("url") or ""))

    def open_outreach(self, item_id: str) -> list[Item]:
        return [
            o
            for o in self.items.values()
            if o.get("kind") == lineage.OUTREACH_KIND
            and (o.get("fields") or {}).get("parent") == item_id
            and self.is_open(o)
        ]


def shipped(ctx: Context, item: Item) -> Entry | None:
    state, word = SHIPPED_STATES.get(str(item.get("kind")), ("", ""))
    if state and entered_since(item, state, ctx.since):
        return ctx.entry(item["id"], f"{word}: {title_of(item)}")
    return None


def decision(ctx: Context, item: Item) -> Entry | None:
    fields = item.get("fields") or {}
    replied = moment(fields.get("replied_at"))
    if (
        item.get("kind") != lineage.OUTREACH_KIND
        or fields.get("purpose") not in DECISION_PURPOSES
        or not fields.get("answer")
        or not replied
        or replied < ctx.since
    ):
        return None
    return ctx.entry(item["id"], f"{title_of(item)}: {fields['answer']} ({fields.get('to', '?')})")


def waiting_on(ctx: Context, item_id: str) -> str:
    people = [f"{o['fields'].get('to', '?')} ({o['id']})" for o in ctx.open_outreach(item_id)]
    return f"; waiting on {', '.join(people)}" if people else ""


Placed = tuple[str, str] | None  # (section, text)


def blocked(ctx: Context, item: Item) -> Placed:
    blockers = lineage.open_blockers(ctx.items, ctx.resolver.index, item["id"], ctx.is_open)
    if blockers:
        names = ", ".join(f"{b['id']} ({b['state']})" for b in blockers)
        return BLOCKED, f"{title_of(item)}: blocked by {names}"
    return None


def outreach_place(_ctx: Context, item: Item) -> Placed:
    """Outreach given up on blocks; outreach that went out waits on its recipient."""
    if item.get("kind") != lineage.OUTREACH_KIND:
        return None
    to = (item.get("fields") or {}).get("to", "?")
    if item.get("state") == GAVE_UP_STATE:
        return BLOCKED, f"no reply from {to}: {title_of(item)}"
    return (WAITING, f"{to}: {title_of(item)}") if item.get("state") == WAITING_STATE else None


def review_place(ctx: Context, item: Item) -> Placed:
    phrase = REVIEW_PHRASES.get(str(item.get("state"))) if item.get("kind") == "my-pr" else None
    return (IN_REVIEW, f"{title_of(item)}: {phrase}{waiting_on(ctx, item['id'])}") if phrase else None


def next_place(_ctx: Context, item: Item) -> Placed:
    state = str(item.get("state"))
    phrase = NEXT_PHRASES.get((str(item.get("kind")), state))
    if not phrase:
        return None
    window = (item.get("delegation") or {}).get("window") if state == "delegated" else None
    return NEXT, f"{title_of(item)}: {phrase}{f' @{window}' if window else ''}"


# Tried in order: the first that places an open item decides its one section.
PLACES = (blocked, outreach_place, review_place, next_place)


def open_section(ctx: Context, item: Item) -> tuple[str, Entry] | None:
    """Where an open item goes: blocked, in review, waiting on others, or next."""
    for place in PLACES:
        found = place(ctx, item)
        if found:
            return found[0], ctx.entry(item["id"], found[1])
    return None


def gave_up_on_open_work(ctx: Context, item: Item) -> bool:
    """Outreach with no reply (we gave up) for work that is still open: that work is stuck."""
    if item.get("kind") != lineage.OUTREACH_KIND or item.get("state") != GAVE_UP_STATE:
        return False
    parent = ctx.items.get(str((item.get("fields") or {}).get("parent") or ""))
    return bool(parent) and ctx.is_open(parent or {})


def fill(summary: Summary, ctx: Context, ids: Iterable[str]) -> Summary:
    for item_id in sorted(ids, key=lambda i: (str(ctx.items[i].get("created_at")), i)):
        item = ctx.items[item_id]
        for name, found in ((SHIPPED, shipped(ctx, item)), (DECISIONS, decision(ctx, item))):
            if found:
                summary.sections[name].append(found)
        if ctx.is_open(item) or gave_up_on_open_work(ctx, item):
            placed = open_section(ctx, item)
            if placed:
                summary.sections[placed[0]].append(placed[1])
    return summary


def header_lines(items: Mapping[str, Item], ws: str) -> list[str]:
    fields = items[ws].get("fields") or {}
    facts = [
        f"{label} {', '.join(value) if isinstance(value, list) else value}"
        for label, key in HEADER_FIELDS
        if (value := fields.get(key))
    ]
    below = sorted(ws_lib.family(items, ws) - {ws})
    lines = [" | ".join(facts), f"includes {', '.join(below)}" if below else ""]
    return [line for line in lines if line]


def workstream_summary(resolver: Resolver, ws: str, since: datetime, is_open: IsOpen) -> Summary:
    """The summary of <ws>, rolling up the workstreams under it."""
    items = resolver.items
    item = items[ws]
    title = f"{item.get('title') or ws} ({ws}, {item.get('state')})"
    ctx = Context(items, resolver, is_open, since, ws)
    summary = Summary(title, ws, since, header_lines(items, ws))
    return fill(summary, ctx, resolver.members(ws_lib.family(items, ws)))


def unassigned_summary(resolver: Resolver, since: datetime, is_open: IsOpen) -> Summary:
    ctx = Context(resolver.items, resolver, is_open, since, "")
    return fill(Summary(UNASSIGNED_TITLE, "", since), ctx, resolver.unassigned())


def all_summaries(resolver: Resolver, since: datetime, is_open: IsOpen) -> list[Summary]:
    """One summary per top-level workstream that is not done or has something to show, then the
    unassigned items."""
    found = []
    for ws in ws_lib.top_level(resolver.items):
        summary = workstream_summary(resolver, ws, since, is_open)
        if resolver.items[ws].get("state") != ws_lib.DONE or not summary.empty():
            found.append(summary)
    return [*found, unassigned_summary(resolver, since, is_open)]


# ---- rendering ---------------------------------------------------------------------------------------


def since_text(since: datetime) -> str:
    return since.strftime("%a %Y-%m-%d %H:%M").replace(" 00:00", "")


def entry_text(entry: Entry, markdown: bool) -> str:
    tag = f" [{entry.ws}]" if entry.ws else ""
    ref = (
        f"[{entry.item}]({entry.url})"
        if markdown and entry.url
        else (f"`{entry.item}`" if markdown else entry.item)
    )
    return f"{entry.text} ({ref}){tag}"


def render(summary: Summary, markdown: bool = False) -> list[str]:
    if markdown:
        lines = [
            f"**{summary.title}**, since {since_text(summary.since)}",
            *(f"_{h}_" for h in summary.header),
        ]
    else:
        lines = [f"{summary.title}, since {since_text(summary.since)}", *(f"  {h}" for h in summary.header)]
    for name, entries in summary.sections.items():
        heading = f"**{name}** ({len(entries)})" if markdown else f"{name} ({len(entries)})"
        bullets = [
            f"- {entry_text(e, markdown)}" if markdown else f"  - {entry_text(e, markdown)}" for e in entries
        ]
        lines += ["", heading, *(bullets or (["- none"] if markdown else ["  - none"]))]
    return lines


def render_all(summaries: list[Summary], markdown: bool = False) -> str:
    blocks = ["\n".join(render(summary, markdown)) for summary in summaries]
    return ("\n\n---\n\n" if markdown else "\n\n").join(blocks)
