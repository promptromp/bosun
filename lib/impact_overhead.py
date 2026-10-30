"""The Impact view's overhead and guardrails (design: docs/design/impact.md, "Overhead and
guardrails"). Pure: no I/O.

Overhead is what bosun itself costs: every turn a session takes is started by something (a
prompt I typed, a task handed over, a check-in, an events summary, a background-task
notification, a resume prompt, another session's message, a site's own scheduled prompt), and the
tokens of that turn are counted against what started it. Each turn also re-reads the session's
whole conversation, so the context it re-read at its first call is kept too: that is what makes a
one-line check-in expensive in a long conversation.

Guardrails are the work the framework exists for, counted per day, so a lower cost is not bought
with worse work: tasks reported and done, how long a hand-over takes to a report, approval
failures, outreach answered or given up, events waiting for the orchestrator, and check-ins
delivered or skipped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

import delegate
import impact


# What started a turn. Work: something asked for (my prompt, a hand-over, a subagent's own turns).
WORK, HANDOVER, SUBAGENT = "work", "handover", "subagent"
CHECK_IN, EVENTS, NOTIFICATION, RESUME, CROSS_SESSION, SCHEDULED = (
    "check-in",
    "events",
    "notification",
    "resume",
    "cross-session",
    "scheduled",
)
CLASSES = (WORK, HANDOVER, SUBAGENT, CHECK_IN, EVENTS, NOTIFICATION, RESUME, CROSS_SESSION, SCHEDULED)
OVERHEAD = frozenset({CHECK_IN, EVENTS, NOTIFICATION, RESUME, CROSS_SESSION, SCHEDULED})
LABELS = {
    WORK: "my requests and the work they set off",
    HANDOVER: "tasks and updates handed to a window",
    SUBAGENT: "subagents' own turns",
    CHECK_IN: "check-ins (chores runs)",
    EVENTS: "events summaries to the orchestrator",
    NOTIFICATION: "background-task notifications",
    RESUME: "resume prompts after a restart or a lifted limit",
    CROSS_SESSION: "messages between sessions",
    SCHEDULED: "a site's scheduled prompts",
}
# The turns whose cost per turn the page follows: the overhead that fires most.
PER_TURN_CLASSES = (CHECK_IN, NOTIFICATION, RESUME, EVENTS)

# How bosun's own prompts open (lib/activity.py's BOSUN_PREFIXES, the check-in loop's
# prompt, lib/events.py's summary header) and how Claude Code's injected messages do.
CHORES_PREFIX = "/bosun:chores"
EVENTS_PREFIX = "[bosun"
NOTIFICATION_PREFIX = "<task-notification"
CROSS_SESSION_PREFIXES = ("<cross-session-message", "[Cross-session", "Another Claude session sent a message")
HANDOVER_PREFIXES = delegate.HANDOVER_OPENINGS
RESUME_MATCH_CHARS = 80  # a resume prompt is recognised by its opening
RULES_VERSION = 2  # bump when the rules change: the per-transcript cache is keyed by it

OVERHEAD_WEEKS = 8  # weeks the panel shows
COMPARE_DAYS = 7  # the last N days against the N before (and the N on each side of a fix)
CONTEXT_UNIT = 1000  # contexts are shown in thousands of tokens
P90 = 0.9


def normalized(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class Classifier:
    """What started a turn, from the prompt (or injected message) that opened it."""

    resume_prompts: tuple[str, ...] = ()
    scheduled_prefixes: tuple[str, ...] = ()

    def key(self) -> str:
        """Changes when the classification would: the per-transcript cache is dropped then."""
        return normalized("|".join([str(RULES_VERSION), *self.resume_prompts, "#", *self.scheduled_prefixes]))

    def classify(self, text: str, scheduled: bool = False) -> str:
        """The class of a turn's start; <scheduled>: Claude Code sent it on a schedule, so it is
        overhead (`scheduled`) unless its opening names another class."""
        found = self.by_opening(text)
        return SCHEDULED if scheduled and found in (WORK, HANDOVER) else found

    def by_opening(self, text: str) -> str:
        opening = normalized(text)
        rules: tuple[tuple[str, tuple[str, ...]], ...] = (
            (NOTIFICATION, (NOTIFICATION_PREFIX,)),
            (CROSS_SESSION, CROSS_SESSION_PREFIXES),
            (CHECK_IN, (CHORES_PREFIX,)),
            (EVENTS, (EVENTS_PREFIX,)),
            (RESUME, tuple(normalized(p)[:RESUME_MATCH_CHARS] for p in self.resume_prompts if p.strip())),
            (SCHEDULED, tuple(p for p in self.scheduled_prefixes if p)),
            (HANDOVER, HANDOVER_PREFIXES),
        )
        return next((name for name, prefixes in rules if prefixes and opening.startswith(prefixes)), WORK)


def scheduled_prefixes(setting: str) -> tuple[str, ...]:
    """BOSUN_IMPACT_OVERHEAD_PREFIXES: prompt openings separated by '|'."""
    return tuple(part.strip() for part in setting.split("|") if part.strip())


# ---- one day's triggers, as the collector builds them -----------------------------------------


def empty_trigger() -> dict[str, Any]:
    return {"turns": 0, "usage": {}, "contexts": []}


def context_tokens(usage: Iterable[tuple[str, int]]) -> int:
    """What a call re-read: its input, cache reads and cache writes."""
    counts = dict(usage)
    return int(counts.get("input", 0) + counts.get("cache_read", 0) + counts.get("cache_write", 0))


def add_call(
    triggers: dict[str, dict[str, Any]], cls: str, model: str, usage: Iterable[tuple[str, int]], first: bool
) -> None:
    """One assistant call's tokens, against the class that started its turn; the turn's first call
    also counts the turn and the context it re-read."""
    pairs = list(usage)
    bucket = triggers.setdefault(cls, empty_trigger())
    tokens = bucket["usage"].setdefault(model or "unknown", dict.fromkeys(impact.TOKEN_KINDS, 0))
    for kind, count in pairs:
        tokens[kind] += count
    if first:
        bucket["turns"] += 1
        bucket["contexts"].append(context_tokens(pairs))


def merge_triggers(total: dict[str, dict[str, Any]], day: Mapping[str, Any]) -> None:
    for cls, bucket in day.items():
        mine = total.setdefault(cls, empty_trigger())
        mine["turns"] += int(bucket.get("turns") or 0)
        mine["contexts"].extend(int(c) for c in bucket.get("contexts") or [])
        impact.merge_usage(mine["usage"], bucket.get("usage") or {})


def bucket_cost(bucket: Mapping[str, Any]) -> float:
    return sum(impact.usage_cost(model, usage) for model, usage in (bucket.get("usage") or {}).items())


# ---- the model ---------------------------------------------------------------------------------


@dataclass
class Tally:
    """Cost, turns and contexts per class, over some days."""

    cost: dict[str, float]
    turns: dict[str, int]
    contexts: list[int]

    @classmethod
    def empty(cls) -> Tally:
        return cls({}, {}, [])

    def add(self, triggers: Mapping[str, Any]) -> None:
        for cls, bucket in triggers.items():
            self.cost[cls] = self.cost.get(cls, 0.0) + bucket_cost(bucket)
            self.turns[cls] = self.turns.get(cls, 0) + int(bucket.get("turns") or 0)
            self.contexts.extend(int(c) for c in bucket.get("contexts") or [])

    def view(self) -> dict[str, Any]:
        total = sum(self.cost.values())
        overhead = sum(cost for cls, cost in self.cost.items() if cls in OVERHEAD)
        return {
            "total": round(total, 2),
            "overhead": round(overhead, 2),
            "share": round(overhead / total, 3) if total else None,
            "cost": {cls: round(self.cost[cls], 2) for cls in CLASSES if cls in self.cost},
            "turns": {cls: self.turns[cls] for cls in CLASSES if cls in self.turns},
            "per_turn": {
                cls: round(self.cost[cls] / self.turns[cls], 2)
                for cls in PER_TURN_CLASSES
                if self.turns.get(cls)
            },
        }


def day_of(row: Mapping[str, Any]) -> date | None:
    return impact.parse_day(row.get("day"))


def tally_days(rows: Iterable[Mapping[str, Any]], first: date, last: date) -> Tally:
    tally = Tally.empty()
    for row in rows:
        day = day_of(row)
        if day is not None and first <= day <= last:
            tally.add(row.get("triggers") or {})
    return tally


def week_rows(rows: Sequence[Mapping[str, Any]], today: date) -> list[dict[str, Any]]:
    """The last OVERHEAD_WEEKS weeks that have any turns: per class cost and turns, the overhead
    share, and the cost per turn of the overhead that fires most."""
    tallies: dict[date, Tally] = {}
    for row in rows:
        day = day_of(row)
        if day is not None and row.get("triggers"):
            tallies.setdefault(impact.monday(day), Tally.empty()).add(row["triggers"])
    weeks = sorted(tallies)[-OVERHEAD_WEEKS:]
    return [
        {"week": week.isoformat(), "partial": week == impact.monday(today), **tallies[week].view()}
        for week in weeks
    ]


def contexts_view(contexts: list[int]) -> dict[str, Any]:
    values = [c / CONTEXT_UNIT for c in contexts]
    return {
        "n": len(values),
        "median_k": impact.rounded(impact.median(values), 0),
        "p90_k": impact.rounded(impact.percentile(values, P90), 0),
    }


def row_contexts(row: Mapping[str, Any]) -> list[int]:
    return [int(c) for bucket in (row.get("triggers") or {}).values() for c in bucket.get("contexts") or []]


def window_contexts(rows: Sequence[Mapping[str, Any]], today: date) -> list[dict[str, Any]]:
    """Per window, the context each turn re-read, per week (thousands of tokens): median and p90.
    Largest latest p90 first."""
    found: dict[str, dict[date, list[int]]] = {}
    first = impact.monday(today) - timedelta(weeks=OVERHEAD_WEEKS - 1)
    for row in rows:
        day = day_of(row)
        if day is not None and day >= first:
            weeks = found.setdefault(str(row.get("window") or ""), {})
            weeks.setdefault(impact.monday(day), []).extend(row_contexts(row))
    views = [
        {
            "window": window,
            "weeks": [{"week": w.isoformat(), **contexts_view(weeks[w])} for w in sorted(weeks)],
        }
        for window, weeks in found.items()
        if any(weeks.values())
    ]
    return sorted(views, key=lambda v: -(v["weeks"][-1]["p90_k"] or 0))


def after_days(pivot: date, today: date) -> int:
    """How many days from <pivot> on have passed (today counts), at most COMPARE_DAYS."""
    return max(0, min(COMPARE_DAYS, (today - pivot).days + 1))


def compare_days(rows: Sequence[Mapping[str, Any]], pivot: date, today: date) -> dict[str, Any]:
    """Up to COMPARE_DAYS days from <pivot> on against the COMPARE_DAYS before it, as daily averages
    (the after side counts only the days that passed)."""
    before = tally_days(rows, pivot - timedelta(days=COMPARE_DAYS), pivot - timedelta(days=1))
    days = after_days(pivot, today)
    after = tally_days(rows, pivot, pivot + timedelta(days=max(days, 1) - 1))
    return {
        "pivot": pivot.isoformat(),
        "before": daily(before, COMPARE_DAYS),
        "after": daily(after, max(days, 1)),
    }


def daily(tally: Tally, days: int) -> dict[str, Any]:
    view = tally.view()
    return {
        **view,
        "days": days,
        "per_day": round(view["total"] / days, 2),
        "overhead_per_day": round(view["overhead"] / days, 2),
        "contexts": contexts_view(tally.contexts),
    }


def overhead_model(
    rows: Sequence[Mapping[str, Any]],
    guardrail_rows: Sequence[Mapping[str, Any]],
    today: date,
    fixed_at: date | None,
) -> dict[str, Any]:
    """The Overhead panel and its guardrails; `exists` False until a snapshot has turn triggers."""
    if not any(row.get("triggers") for row in rows):
        return {"exists": False}
    last = tally_days(rows, today - timedelta(days=COMPARE_DAYS - 1), today)
    prev = tally_days(
        rows, today - timedelta(days=2 * COMPARE_DAYS - 1), today - timedelta(days=COMPARE_DAYS)
    )
    return {
        "exists": True,
        "classes": [{"key": c, "label": LABELS[c], "overhead": c in OVERHEAD} for c in CLASSES],
        "weeks": week_rows(rows, today),
        "last": daily(last, COMPARE_DAYS),
        "previous": daily(prev, COMPARE_DAYS),
        "windows": window_contexts(rows, today),
        "fixed_at": fixed_at.isoformat() if fixed_at else None,
        "fix": compare_days(rows, fixed_at, today) if fixed_at else None,
        "guardrails": guardrails_model(guardrail_rows, today, fixed_at),
    }


# ---- guardrails --------------------------------------------------------------------------------

GUARDRAIL_COUNTS = (
    "tasks_reported",
    "tasks_done",
    "approval_failures",
    "outreach_replied",
    "outreach_dropped",
    "events_queued",
    "events_delivered",
    "checkins_delivered",
    "checkins_skipped_nothing_new",
    "checkins_skipped_other",
)
GUARDRAIL_LISTS = ("handover_to_report_hours", "event_delay_minutes")
GUARDRAIL_LEVELS = ("events_pending_end",)  # a level at the day's end: the week shows its maximum


def field_sum(rows: Sequence[Mapping[str, Any]], name: str) -> int:
    return sum(int(r.get(name) or 0) for r in rows)


def field_median(rows: Sequence[Mapping[str, Any]], name: str) -> float | None:
    return impact.rounded(impact.median([float(v) for r in rows for v in r.get(name) or []]))


def field_max(rows: Sequence[Mapping[str, Any]], name: str) -> int:
    return max((int(r.get(name) or 0) for r in rows), default=0)


def guardrail_tally(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts summed, lists as medians and levels as the maximum, over <rows>."""
    counts = {name: field_sum(rows, name) for name in GUARDRAIL_COUNTS}
    medians = {f"{name}_median": field_median(rows, name) for name in GUARDRAIL_LISTS}
    levels = {f"{name}_max": field_max(rows, name) for name in GUARDRAIL_LEVELS}
    return {"days": len(rows), **counts, **medians, **levels}


def rows_between(rows: Iterable[Mapping[str, Any]], first: date, last: date) -> list[Mapping[str, Any]]:
    return [r for r in rows if (day := day_of(r)) is not None and first <= day <= last]


def guardrails_model(rows: Sequence[Mapping[str, Any]], today: date, fixed_at: date | None) -> dict[str, Any]:
    if not rows:
        return {"exists": False}
    by_week: dict[date, list[Mapping[str, Any]]] = {}
    for row in rows:
        day = day_of(row)
        if day is not None:
            by_week.setdefault(impact.monday(day), []).append(row)
    weeks = sorted(by_week)[-OVERHEAD_WEEKS:]
    fix = None
    if fixed_at:
        before = rows_between(rows, fixed_at - timedelta(days=COMPARE_DAYS), fixed_at - timedelta(days=1))
        after = rows_between(
            rows, fixed_at, fixed_at + timedelta(days=max(after_days(fixed_at, today), 1) - 1)
        )
        fix = {
            "pivot": fixed_at.isoformat(),
            "before": guardrail_tally(before),
            "after": guardrail_tally(after),
        }
    return {
        "exists": True,
        "weeks": [
            {"week": w.isoformat(), "partial": w == impact.monday(today), **guardrail_tally(by_week[w])}
            for w in weeks
        ],
        "fix": fix,
    }


def from_snapshot(snapshot: Mapping[str, Any] | None, now: datetime, zone: tzinfo) -> dict[str, Any]:
    """The Overhead panel of a snapshot `bosun impact collect` wrote."""
    if not snapshot:
        return {"exists": False}
    fixed_at = impact.parse_day((snapshot.get("settings") or {}).get("overhead_fixed_at"))
    today = now.astimezone(zone).date()
    return overhead_model(
        list(snapshot.get("activity") or []), list(snapshot.get("guardrails") or []), today, fixed_at
    )
