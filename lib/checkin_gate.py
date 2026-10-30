#!/usr/bin/env python3
"""Whether an hourly check-in has anything to do: something new since the last one.

Usage: checkin_gate.py --state-dir DIR [--max-quiet-hours H] [--slack-watch 0|1] [--explain]

The check-in loop asks before it sends `/bosun:chores check-in`, because every check-in makes
the orchestrator re-read its whole conversation. Without a Claude call it looks for, since the last
check-in the ledger recorded (`runs["check-in"]`, marked by the chores):
  - events queued and not yet delivered;
  - Slack: a watcher run that found messages, a failing watcher, or none since (the check-in's
    own scan covers it), or the watcher off (it cannot tell, so every check-in goes out);
  - a repo session's report;
  - an outreach whose wait crossed a business day (a nudge may be due) or whose expect_by passed;
  - a watchdog alert;
  - more than --max-quiet-hours since the last check-in, so the chores still run.
Prints `news: <reason>` and exits 0 when the check-in should go out, `nothing new since HH:MM` and
exits 1 when not. --explain prints every finding. Reads only; never writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger


NEWS, QUIET = 0, 1
CHECKIN_RUN = "check-in"
SENT = "sent"
WATCHDOG_TIME = "%Y-%m-%d %H:%M:%S"  # watchdog.json's local times
DEFAULT_MAX_QUIET_HOURS = 4.0


@dataclass(frozen=True)
class Inputs:
    now: datetime
    last: datetime | None  # the last check-in the ledger recorded
    items: Mapping[str, Any]
    pending_events: int
    slack: Mapping[str, Any] | None  # slack-watch's state.json; None: the watcher is off
    last_alert: datetime | None
    max_quiet: timedelta


def parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def after(value: Any, moment: datetime) -> bool:
    found = parse(value)
    return found is not None and found > moment


def quiet_too_long(inputs: Inputs, last: datetime) -> list[str]:
    hours = (inputs.now - last).total_seconds() / 3600
    return [f"{hours:.1f} h since the last check-in"] if inputs.now - last >= inputs.max_quiet else []


def events_news(inputs: Inputs, _last: datetime) -> list[str]:
    return [f"{inputs.pending_events} event(s) not yet delivered"] if inputs.pending_events else []


def slack_news(inputs: Inputs, last: datetime) -> list[str]:
    state = inputs.slack
    if state is None:
        return ["the Slack watcher is off, so the check-in scans Slack"]
    if state.get("failures"):
        return [f"the Slack watcher is failing ({state['failures']} in a row)"]
    runs = [run for run in state.get("runs") or [] if after(run.get("started"), last)]
    if not runs:
        return ["the Slack watcher has not run since the last check-in"]
    found = sum(int(run.get("found") or 0) for run in runs)
    return [f"{found} Slack message(s) found since"] if found else []


def report_news(inputs: Inputs, last: datetime) -> list[str]:
    reported = sorted(
        item["id"] for item in inputs.items.values() if after((item.get("report") or {}).get("at"), last)
    )
    return [f"report(s) on {', '.join(reported)}"] if reported else []


def outreach_news(inputs: Inputs, last: datetime) -> list[str]:
    found = sorted(item["id"] for item in inputs.items.values() if outreach_due(item, inputs, last))
    return [f"outreach waiting a business day more: {', '.join(found)}"] if found else []


def outreach_due(item: Mapping[str, Any], inputs: Inputs, last: datetime) -> bool:
    """A sent outreach whose wait crossed a business day, or whose expect_by passed, since <last>."""
    if item.get("kind") != ledger.OUTREACH_KIND or item.get("state") != SENT:
        return False
    fields = item.get("fields") or {}
    messages = fields.get("messages") or []
    if messages and crossed_a_business_day(ledger.last_message_at(messages), last.date(), inputs.now.date()):
        return True
    return after(fields.get("expect_by"), last) and not after(fields.get("expect_by"), inputs.now)


def crossed_a_business_day(sent_at: str, before: date, today: date) -> bool:
    return ledger.business_days_since(sent_at, today) > ledger.business_days_since(sent_at, before)


def alert_news(inputs: Inputs, last: datetime) -> list[str]:
    return ["a new watchdog alert"] if inputs.last_alert and inputs.last_alert > last else []


CHECKS: tuple[Callable[[Inputs, datetime], list[str]], ...] = (
    quiet_too_long,
    events_news,
    slack_news,
    report_news,
    outreach_news,
    alert_news,
)


def findings(inputs: Inputs) -> list[str]:
    """Everything new since the last check-in (empty: nothing to do)."""
    if inputs.last is None:
        return ["no check-in recorded yet"]
    return [line for check in CHECKS for line in check(inputs, inputs.last)]


# ---- reading the state ---------------------------------------------------------------------------


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value: dict[str, Any] = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value


def pending_events(state_dir: Path) -> int:
    directory = state_dir / "events"
    delivered = (read_json(directory / "state.json") or {}).get("delivered") or {}
    try:
        lines = (directory / "queue.jsonl").read_text().splitlines()
    except OSError:
        return 0
    ids: Iterable[str] = (str(json.loads(line).get("id")) for line in lines if line.strip())
    return sum(1 for event_id in ids if event_id not in delivered)


def local_time(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value), WATCHDOG_TIME).astimezone() if value else None
    except ValueError:
        return None


def read_inputs(args: argparse.Namespace, now: datetime) -> Inputs:
    state_dir = Path(args.state_dir)
    book = read_json(state_dir / "ledger.json") or {}
    return Inputs(
        now=now,
        last=parse((book.get("runs") or {}).get(CHECKIN_RUN)),
        items=book.get("items") or {},
        pending_events=pending_events(state_dir),
        slack=(read_json(state_dir / "slack-watch" / "state.json") or {})
        if args.slack_watch == "1"
        else None,
        last_alert=local_time((read_json(state_dir / "watchdog.json") or {}).get("last_alert")),
        max_quiet=timedelta(hours=args.max_quiet_hours),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--max-quiet-hours", type=float, default=DEFAULT_MAX_QUIET_HOURS)
    parser.add_argument("--slack-watch", choices=("0", "1"), default="1")
    parser.add_argument("--explain", action="store_true")
    args = parser.parse_args(argv)
    inputs = read_inputs(args, datetime.now(UTC))
    found = findings(inputs)
    if args.explain:
        for line in found:
            print(f"  new: {line}")
    if found:
        print(f"news: {found[0]}")
        return NEWS
    since = inputs.last.astimezone().strftime("%H:%M") if inputs.last else "?"
    print(f"nothing new since {since}")
    return QUIET


if __name__ == "__main__":
    sys.exit(main())
