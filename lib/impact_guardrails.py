"""The Overhead panel's guardrails, per local day (design: docs/design/impact.md, "Overhead and
guardrails"): the work the framework exists for, so a lower cost is not bought with worse work.

Reads only bosun's own state: the ledger's item histories, the events queue and its delivery
record (lib/events.py), and the check-in logs. `lib/impact_collect.py` stores the rows in the
snapshot; lib/impact_overhead.py turns them into weeks and a before/after comparison.

  tasks_reported, tasks_done        report events (any item); task items moved to done
  handover_to_report_hours          from an item's latest hand-over (delegated) to its next report
  approval_failures                 approvals whose call failed (approval-failed)
  outreach_replied, _dropped        outreach moved to replied, or dropped
  events_queued, _delivered         events queued for the orchestrator, and delivered
  event_delay_minutes               from an event's queueing to its delivery
  events_pending_end                events queued but not delivered at the day's end
  checkins_delivered                check-ins that went out (the loop's `[send] delivered` lines)
  checkins_skipped_nothing_new      check-ins skipped because nothing had changed
  checkins_skipped_other            check-ins skipped for another reason (busy, typing, ...)
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any

import events
import impact
import ledger


DELEGATED, REPORT, TRANSITION = "delegated", "report", "transition"
TASK_KIND, DONE = "task", "done"
OUTREACH_STATES = {"replied": "outreach_replied", "dropped": "outreach_dropped"}
SECONDS_PER_MINUTE = 60
CHECKIN_SENT = "[send] delivered to "
CHECKIN_SKIP = "[checkin] skip:"
NOTHING_NEW = re.compile(r"nothing (new|changed|to do|happened)", re.IGNORECASE)
LOG_NAME = "checkin-{day}.log"


def empty_row(day: date) -> dict[str, Any]:
    return {
        "day": day.isoformat(),
        "handover_to_report_hours": [],
        "event_delay_minutes": [],
    }


def bump(rows: dict[date, dict[str, Any]], day: date, name: str, amount: int = 1) -> None:
    row = rows.setdefault(day, empty_row(day))
    row[name] = int(row.get(name) or 0) + amount


def local(moment: datetime | None, zone: tzinfo) -> date | None:
    return moment.astimezone(zone).date() if moment else None


# ---- the ledger --------------------------------------------------------------------------------


def item_days(item: Mapping[str, Any], zone: tzinfo) -> Iterator[tuple[date, str, float | None]]:
    """(day, counter, hours) for each history entry that counts: hours only for a report that
    follows a hand-over."""
    handed: datetime | None = None
    for entry in item.get("history") or []:
        at = impact.parse_time(entry.get("at"))
        day = local(at, zone)
        if at is None or day is None:
            continue
        event = entry.get("event")
        if event == DELEGATED:
            handed = at
        elif event == REPORT:
            yield day, "tasks_reported", impact.hours(handed, at) if handed else None
            handed = None
        elif event == ledger.APPROVAL_FAILED:
            yield day, "approval_failures", None
        elif event == TRANSITION:
            yield from transition_counts(item, entry, day)


def transition_counts(
    item: Mapping[str, Any], entry: Mapping[str, Any], day: date
) -> Iterator[tuple[date, str, None]]:
    target = entry.get("to")
    if item.get("kind") == TASK_KIND and target == DONE:
        yield day, "tasks_done", None
    if item.get("kind") == ledger.OUTREACH_KIND and target in OUTREACH_STATES:
        yield day, OUTREACH_STATES[target], None


def add_ledger(rows: dict[date, dict[str, Any]], items: Mapping[str, Any], first: date, zone: tzinfo) -> None:
    for item in items.values():
        for day, name, hours in item_days(item, zone):
            if day < first:
                continue
            bump(rows, day, name)
            if hours is not None:
                rows[day]["handover_to_report_hours"].append(round(hours, 2))


# ---- the events queue --------------------------------------------------------------------------


def read_events(state_dir: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The events queue and the delivery record (event id -> "<via> <time>")."""
    folder = state_dir / events.SUBDIR
    try:
        queued = [
            json.loads(line) for line in (folder / events.QUEUE_FILE).read_text().splitlines() if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        queued = []
    try:
        delivered = dict((json.loads((folder / events.STATE_FILE).read_text()) or {}).get("delivered") or {})
    except (OSError, json.JSONDecodeError):
        delivered = {}
    return [e for e in queued if isinstance(e, dict)], delivered


def delivered_at(record: str | None) -> datetime | None:
    return impact.parse_time(record.split(" ", 1)[1]) if record and " " in record else None


def add_events(
    rows: dict[date, dict[str, Any]], state_dir: Path, first: date, today: date, zone: tzinfo
) -> None:
    queued, delivered = read_events(state_dir)
    spans = [(impact.parse_time(e.get("at")), delivered_at(delivered.get(str(e.get("id"))))) for e in queued]
    for start, end in spans:
        if start is None:
            continue
        if (day := local(start, zone)) and day >= first:
            bump(rows, day, "events_queued")
        if end and (day := local(end, zone)) and day >= first:
            bump(rows, day, "events_delivered")
            rows[day]["event_delay_minutes"].append(
                round((end - start).total_seconds() / SECONDS_PER_MINUTE, 1)
            )
    add_pending(rows, spans, first, today, zone)


def add_pending(
    rows: dict[date, dict[str, Any]],
    spans: list[tuple[datetime | None, datetime | None]],
    first: date,
    today: date,
    zone: tzinfo,
) -> None:
    """Events queued by a day's end and not delivered by then, for each day."""
    day = first
    while day <= today:
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        pending = sum(1 for start, done in spans if start and start < end and (done is None or done >= end))
        rows.setdefault(day, empty_row(day))["events_pending_end"] = pending
        day += timedelta(days=1)


# ---- the check-in logs -------------------------------------------------------------------------


def checkin_lines(log_dir: Path, day: date) -> Iterable[str]:
    try:
        return (log_dir / LOG_NAME.format(day=day.isoformat())).read_text(errors="replace").splitlines()
    except OSError:
        return []


def add_checkins(rows: dict[date, dict[str, Any]], log_dir: Path, first: date, today: date) -> None:
    day = first
    while day <= today:
        for line in checkin_lines(log_dir, day):
            if CHECKIN_SENT in line:
                bump(rows, day, "checkins_delivered")
            elif CHECKIN_SKIP in line:
                reason = line.split(CHECKIN_SKIP, 1)[1]
                bump(
                    rows,
                    day,
                    "checkins_skipped_nothing_new"
                    if NOTHING_NEW.search(reason)
                    else "checkins_skipped_other",
                )
        day += timedelta(days=1)


def collect(
    state_dir: Path, log_dir: Path, items: Mapping[str, Any], first: date, zone: tzinfo, now: datetime
) -> list[dict[str, Any]]:
    """The guardrail rows, one per local day from <first> to today."""
    today = now.astimezone(zone).date()
    rows: dict[date, dict[str, Any]] = {}
    add_ledger(rows, items, first, zone)
    add_events(rows, state_dir, first, today, zone)
    add_checkins(rows, log_dir, first, today)
    return [rows[day] for day in sorted(rows) if first <= day <= today]
