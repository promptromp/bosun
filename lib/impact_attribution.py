"""Cost per task: each turn's tokens against the ledger item it worked on (design:
docs/design/impact.md, "Cost per task"). Pure: the collector passes in the ledger's items and the
turn records it cached per transcript (lib/impact_turns.py).

A turn goes to one item, by the first rule that applies:
1. hand-over: its prompt is a hand-over that names a known item (the first one it names);
2. report: the window's next ledger event after it is a report, within the horizon;
3. active: the window's latest ledger event at or before it, within the horizon, unless that was
   `idle` or the item had closed by then;
4. otherwise it stays unattributed, kept per window.
A subagent's tokens go to the item of its parent session's turn when it started.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import activity
import impact


DEFAULT_HORIZON_HOURS = 8.0  # BOSUN_IMPACT_ATTRIBUTION_HOURS: how far rules 2 and 3 reach
HANDOVER, REPORT, ACTIVE = "handover", "report", "active"
RULES = (HANDOVER, REPORT, ACTIVE)
REPORT_EVENT = "report"
TRANSITION_EVENT = "transition"
MERGED, DONE, DROPPED, OPEN = "merged", "done", "dropped", "open"
OUTCOMES = (MERGED, DONE, DROPPED, OPEN)
# Closed states that mean the work was given up rather than delivered (any kind), and the ones
# that do only for one kind: a PR of mine closed without merging.
DROPPED_STATES = frozenset({"dropped", "declined", "dismissed", "cancelled", "rejected", "no-reply"})
DROPPED_BY_KIND = frozenset({("my-pr", "closed")})
PR_PATH = "/pull/"
PRODUCES = "produces"
Item = Mapping[str, Any]
IsOpen = Callable[[Item], bool]


@dataclass(frozen=True)
class Mark:
    """A ledger event in a window: when, on which item, which event."""

    at: datetime
    item: str
    event: str


def window_marks(items: Iterable[Item]) -> dict[str, list[Mark]]:
    """Per window, the events that tell which item it was on (lib/activity.py's), oldest first."""
    marks: dict[str, list[Mark]] = {}
    for item in items:
        for entry in item.get("history") or []:
            at = impact.parse_time(entry.get("at"))
            window = entry.get("window")
            if at and window and entry.get("event") in activity.ACTIVITY_EVENTS:
                marks.setdefault(window, []).append(Mark(at, item["id"], entry["event"]))
    for found in marks.values():
        found.sort(key=lambda m: m.at)
    return marks


def closed_at(item: Item) -> datetime | None:
    """When a closed item reached its current state: the transition to it, else its last update."""
    for entry in reversed(item.get("history") or []):
        if entry.get("event") == TRANSITION_EVENT and entry.get("to") == item.get("state"):
            return impact.parse_time(entry.get("at"))
    return impact.parse_time(item.get("updated_at"))


@dataclass(frozen=True)
class Timeline:
    """What the rules read: each window's marks, when closed items closed, the known ids."""

    marks: dict[str, list[Mark]]
    closed: dict[str, datetime]
    known: frozenset[str]
    horizon: timedelta

    @classmethod
    def of(cls, items: Mapping[str, Item], is_open: IsOpen, horizon: timedelta) -> Timeline:
        closed = {i: at for i, item in items.items() if not is_open(item) and (at := closed_at(item))}
        return cls(window_marks(items.values()), closed, frozenset(items), horizon)

    def item_at(self, window: str, at: datetime, ids: Iterable[str]) -> tuple[str, str] | None:
        """(item, rule) for a turn of <window> that started at <at> and handed over <ids>."""
        handed = next((i for i in ids if i in self.known), None)
        if handed:
            return handed, HANDOVER
        marks = self.marks.get(window, [])
        index = bisect.bisect_right(marks, at, key=lambda m: m.at)
        return self.reported_next(marks[index:], at) or self.active_before(marks[:index], at)

    def reported_next(self, later: list[Mark], at: datetime) -> tuple[str, str] | None:
        after = later[0] if later else None
        if after and after.event == REPORT_EVENT and after.at - at <= self.horizon:
            return after.item, REPORT
        return None

    def active_before(self, earlier: list[Mark], at: datetime) -> tuple[str, str] | None:
        before = earlier[-1] if earlier else None
        return (before.item, ACTIVE) if before and self.still_on(before, at) else None

    def still_on(self, mark: Mark, at: datetime) -> bool:
        closed = self.closed.get(mark.item)
        return (
            mark.event != activity.IDLE_MARK
            and at - mark.at <= self.horizon
            and (closed is None or closed > at)
        )


# ---- sessions ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    """A window's session: its turn records, and each subagent's (one record list per subagent)."""

    window: str
    turns: list[dict[str, Any]]
    subagents: list[list[dict[str, Any]]] = field(default_factory=list)


def parent_turn(turns: list[dict[str, Any]], at: datetime) -> dict[str, Any] | None:
    """The parent's turn under way at <at>: the last one that started at or before it."""
    index = bisect.bisect_right(turns, at, key=lambda t: impact.parse_time(t["at"]) or at)
    return turns[index - 1] if index else None


def subagent_turns(session: Session) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    """(the turn whose item it goes to, the subagent's own record) for every subagent record."""
    for records in session.subagents:
        for record in records:
            at = impact.parse_time(record["at"])
            parent = parent_turn(session.turns, at) if at else None
            yield parent or {"at": record["at"], "ids": []}, record


# ---- the tally ---------------------------------------------------------------------------------


def empty_bucket() -> dict[str, Any]:
    return {"turns": 0, "usage": {}, "subagent_usage": {}, "windows": [], "first": None, "last": None}


def add_to(bucket: dict[str, Any], window: str, record: Mapping[str, Any], subagent: bool) -> None:
    """One turn record's tokens into a bucket; a parent turn counts as a turn, a subagent's does not."""
    impact.merge_usage(bucket["subagent_usage" if subagent else "usage"], record.get("usage") or {})
    bucket["turns"] += 0 if subagent else 1
    if window not in bucket["windows"]:
        bucket["windows"].append(window)
    day = str(record["at"])[: len("2026-01-01")]
    bucket["first"] = min(filter(None, (bucket["first"], day)))
    bucket["last"] = max(filter(None, (bucket["last"], day)))


@dataclass
class Tally:
    """Buckets per item and per window (unattributed), and tokens per rule."""

    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    unattributed: dict[str, dict[str, Any]] = field(default_factory=dict)
    rules: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)

    def add(
        self, found: tuple[str, str] | None, window: str, record: Mapping[str, Any], subagent: bool
    ) -> None:
        if found is None:
            add_to(self.unattributed.setdefault(window, empty_bucket()), window, record, subagent)
            return
        item, rule = found
        add_to(self.items.setdefault(item, empty_bucket()), window, record, subagent)
        impact.merge_usage(self.rules.setdefault(rule, {}), record.get("usage") or {})


def tally(sessions: Iterable[Session], timeline: Timeline, since: datetime) -> Tally:
    """Every turn (and subagent record) that started at or after <since>, attributed."""
    found = Tally()

    def place(turn: Mapping[str, Any], window: str, record: Mapping[str, Any], subagent: bool) -> None:
        at = impact.parse_time(turn["at"])
        if at is not None and at >= since:
            found.add(timeline.item_at(window, at, turn.get("ids") or []), window, record, subagent)

    for session in sessions:
        for turn in session.turns:
            place(turn, session.window, turn, False)
        for turn, record in subagent_turns(session):
            place(turn, session.window, record, True)
    return found


# ---- the snapshot ------------------------------------------------------------------------------


def outcome(item: Item, is_open: IsOpen) -> str:
    state = str(item.get("state") or "")
    if is_open(item):
        return OPEN
    if state == MERGED:
        return MERGED
    if state in DROPPED_STATES or (item.get("kind"), state) in DROPPED_BY_KIND:
        return DROPPED
    return DONE


def pr_link(item: Item, items: Mapping[str, Item]) -> str | None:
    """The PR the item is, or the first one it produced."""
    linked = [
        items.get(link.get("id"), {}) for link in item.get("links") or [] if link.get("rel") == PRODUCES
    ]
    urls = [str(i.get("url") or "") for i in (item, *linked)]
    return next((url for url in urls if PR_PATH in url), None)


def item_row(
    item_id: str, bucket: Mapping[str, Any], items: Mapping[str, Item], is_open: IsOpen
) -> dict[str, Any]:
    item = items.get(item_id, {})
    return {
        "id": item_id,
        "kind": item.get("kind") or "",
        "task": (item.get("delegation") or {}).get("task") or "",
        "title": item.get("title") or "",
        "state": item.get("state") or "",
        "outcome": outcome(item, is_open),
        "pr": pr_link(item, items),
        **bucket,
    }


def snapshot_tasks(
    sessions: Iterable[Session],
    items: Mapping[str, Item],
    is_open: IsOpen,
    since: datetime,
    horizon: timedelta,
) -> dict[str, Any]:
    """The snapshot's `tasks`: a row per item with tokens, the unattributed rest per window, and
    tokens per rule."""
    found = tally(sessions, Timeline.of(items, is_open, horizon), since)
    return {
        "since": since.isoformat(),
        "items": [item_row(i, bucket, items, is_open) for i, bucket in sorted(found.items.items())],
        "unattributed": [{"window": w, **bucket} for w, bucket in sorted(found.unattributed.items())],
        "rules": found.rules,
    }
