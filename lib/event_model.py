"""What an event is: its types, its fields and how one is made (design: docs/design/events.md).

A leaf: the queue and dispatcher (lib/events.py), the lanes (lib/event_lanes.py) and the summary
(lib/event_summary.py) build on it, and it imports nothing from bosun.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


# What an event can be; each is one line in the summary.
TYPES = {
    "report": "a repo session reported on an item",
    "user-said": "I typed something in a repo window about an item",
    "reach-out": "a repo session recorded outreach (a draft for me to approve)",
    "spawn": "a repo session recorded a follow-up task",
    "waiting": "a repo window waits at a permission prompt or a question",
    "slack": "new Slack activity for me: a mention, a DM or a reply in my thread (`bosun slack-watch`)",
    "review": "a new review on my PR after its last hand-over or report (`bosun review-watch`)",
}
SLACK = "slack"
REVIEW = "review"
# The short form of a text (status lines, a Slack excerpt's room).
SUMMARY_LENGTH = 160
# What the queue keeps of an event's summary and detail; the summary shows at most
# BOSUN_EVENTS_INLINE_CHARS of them, and the ledger keeps the full text.
STORED_LENGTH = 2000
CUT = "..."


class EventsError(Exception):
    pass


@dataclass(frozen=True)
class Event:
    id: str
    at: str  # ISO UTC
    type: str
    item: str = ""
    window: str = ""  # the repo window it is about
    origin: str = ""  # the window whose command caused it ("" if unknown)
    summary: str = ""
    ref: str = ""  # what it is about outside the ledger (a Slack permalink)
    detail: str = ""  # what the orchestrator needs besides the summary (open points, links, the ask)

    @classmethod
    def of(cls, data: Mapping[str, Any]) -> Event:
        return cls(**{k: str(data.get(k) or "") for k in cls.__dataclass_fields__})


def short(text: str, limit: int = SUMMARY_LENGTH) -> str:
    """<text> on one line, cut to <limit> characters."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - len(CUT)] + CUT


# The fields an event is made with (Event's, besides id, at and type).
EVENT_FIELDS = ("item", "window", "origin", "summary", "ref", "detail")


def new_event(kind: str, *, now: datetime | None = None, **fields: str) -> Event:
    """An event of <kind> (at <now>, else the current time) with <fields> (EVENT_FIELDS); its id
    sorts by time."""
    if kind not in TYPES:
        raise EventsError(f"unknown event type {kind!r}; types: {', '.join(TYPES)}")
    unknown = set(fields) - set(EVENT_FIELDS)
    if unknown:
        raise EventsError(f"unknown event field(s) {', '.join(sorted(unknown))}")
    stamp = int(now.timestamp() * 1e9) if now else time.time_ns()
    at = datetime.fromtimestamp(stamp / 1e9, UTC).isoformat(timespec="seconds")
    for name in ("summary", "detail"):
        fields[name] = short(fields.get(name, ""), STORED_LENGTH)
    return Event(f"{stamp}-{os.getpid()}", at, kind, **fields)


def parse_kinds(value: str, setting: str) -> frozenset[str]:
    """Event types separated by spaces or commas (a setting's value), each one of TYPES."""
    kinds = frozenset(value.replace(",", " ").split())
    unknown = kinds - set(TYPES)
    if unknown:
        raise EventsError(
            f"{setting}: unknown event type(s) {', '.join(sorted(unknown))}; types: {', '.join(TYPES)}"
        )
    return kinds
