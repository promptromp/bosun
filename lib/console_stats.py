"""The console's Stats view (/api/stats?days=7|30|90): metrics from the ledger's history, plus what
bosun's own state files say about itself. Design: docs/design/console.md (section 3), where
each metric is defined.

Pure over a loaded ledger and the state lib/console_state.py reads; the time and the time zone are
passed in. The period is the last <days> local days, today included. A time is an entry's
`happened_at` (the real time, when it was given) else its `at`; an ISO time without an offset counts
as UTC. A time that does not parse leaves its metric out and is counted in `odd_times`, so a bad
entry never breaks the page.
"""

from __future__ import annotations

import re
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger
import workstreams
from console_data import Item, Params, QueryError


PERIODS = (7, 30, 90)
DEFAULT_DAYS = 7
TOP_PEOPLE = 10
MY_PR = "my-pr"
MERGED = "merged"
REVIEW_KINDS = ("review-request", "td-review")
REREVIEW = "rereview-requested"
TASK = ledger.TASK_KIND
OUTREACH = ledger.OUTREACH_KIND
TASK_OUTCOMES = ("done", "declined", "dropped")
HANDOVERS = ("delegated", "moved")  # history events that give an item to a window
REPORT = "report"
NO_REPLY = "no-reply"
REPLIED = "replied"
EVENTS_KEPT_DAYS = 7  # lib/events.py and lib/slack_watch.py keep a week of deliveries and spend
# Recipients and requesters: "Dana, Lee", "Dana and Lee", "Dana & Lee"; "(finance)" is dropped.
NAME_SEPARATORS = re.compile(r"\s*[,;]\s*|\s+(?:and|&|\+|/)\s+", re.IGNORECASE)
NAME_NOTE = re.compile(r"\s*\([^)]*\)")


def parse_days(params: Params) -> int:
    """The period from ?days=, one of PERIODS (DEFAULT_DAYS without one); QueryError otherwise."""
    values = params.get("days") or []
    if not values:
        return DEFAULT_DAYS
    if len(values) > 1 or values[0] not in {str(days) for days in PERIODS}:
        raise QueryError(f"days must be one of {', '.join(map(str, PERIODS))}")
    return int(values[0])


# ---- times -------------------------------------------------------------------------------------


def moment(value: Any) -> datetime | None:
    """An ISO time as an aware datetime (no offset: UTC); None if it is not one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    """end - start in seconds; None if either is missing or the end comes first (an odd record)."""
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds()


def median(values: Iterable[float]) -> float | None:
    found = list(values)
    return round(statistics.median(found), 1) if found else None


@dataclass(frozen=True)
class Period:
    """The last <days> local days up to <now>."""

    days: int
    now: datetime
    zone: tzinfo

    @property
    def first_day(self) -> date:
        return self.now.astimezone(self.zone).date() - timedelta(days=self.days - 1)

    @property
    def start(self) -> datetime:
        return datetime.combine(self.first_day, time(0), tzinfo=self.zone)

    def day_list(self) -> list[str]:
        return [(self.first_day + timedelta(days=n)).isoformat() for n in range(self.days)]

    def day_of(self, when: datetime) -> str:
        return when.astimezone(self.zone).date().isoformat()

    def has(self, when: datetime | None) -> bool:
        return when is not None and self.start <= when <= self.now

    def time(self, value: Any) -> datetime | None:
        return moment(value)

    def entry_time(self, entry: Mapping[str, Any]) -> datetime | None:
        return moment(entry.get("happened_at")) or moment(entry.get("at"))


# ---- reading items -----------------------------------------------------------------------------


def items_of(data: Mapping[str, Any]) -> list[Item]:
    items = data.get("items")
    return [i for i in items.values() if isinstance(i, dict)] if isinstance(items, dict) else []


def history(item: Item) -> list[dict[str, Any]]:
    entries = item.get("history")
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def fields_of(item: Item) -> dict[str, Any]:
    found = item.get("fields")
    return found if isinstance(found, dict) else {}


def created(item: Item, period: Period) -> datetime | None:
    """When the ledger first recorded the item: created_at, else its first history entry."""
    entries = history(item)
    return period.time(item.get("created_at")) or (period.entry_time(entries[0]) if entries else None)


def state_changes(item: Item, period: Period) -> Iterator[tuple[str, datetime | None]]:
    """(state entered, when) for each history entry that set the state, oldest first."""
    for entry in history(item):
        if entry.get("event") == "created":
            yield str(entry.get("state", "")), period.entry_time(entry)
        elif entry.get("event") == "transition":
            yield str(entry.get("to", "")), period.entry_time(entry)


def last_entered(item: Item, states: Iterable[str], period: Period) -> datetime | None:
    """When the item last moved into one of <states>, if it is in one now (else None). Falls back to
    updated_at when the history does not say (trimmed, or an odd time)."""
    wanted = set(states)
    if item.get("state") not in wanted:
        return None
    times = [when for state, when in state_changes(item, period) if state in wanted]
    return (times[-1] if times else None) or period.time(item.get("updated_at"))


def terminal(kind: str) -> set[str]:
    return set(ledger.KINDS.get(kind, {}).get("terminal", set()))


def of_kind(items: Iterable[Item], *kinds: str) -> list[Item]:
    return [i for i in items if i.get("kind") in kinds]


# ---- throughput --------------------------------------------------------------------------------


def throughput(items: list[Item], period: Period) -> dict[str, Any]:
    """Items opened (first recorded) and closed (moved into a terminal state of their kind, and
    still there) per local day, by kind."""
    days = {day: {"opened": Counter[str](), "closed": Counter[str]()} for day in period.day_list()}
    for item in items:
        kind = str(item.get("kind", "?"))
        for what, when in (
            ("opened", created(item, period)),
            ("closed", last_entered(item, terminal(kind), period)),
        ):
            if when is not None and period.has(when):
                days[period.day_of(when)][what][kind] += 1
    totals = {what: sum((d[what] for d in days.values()), Counter[str]()) for what in ("opened", "closed")}
    kinds = sorted(set(totals["opened"]) | set(totals["closed"]))
    return {
        "kinds": kinds,
        "days": [
            {"date": day, "opened": dict(c["opened"]), "closed": dict(c["closed"])} for day, c in days.items()
        ],
        "opened": sum(totals["opened"].values()),
        "closed": sum(totals["closed"].values()),
        "by_kind": {k: {"opened": totals["opened"][k], "closed": totals["closed"][k]} for k in kinds},
    }


# ---- PRs and reviews ---------------------------------------------------------------------------


def my_prs(items: list[Item], period: Period) -> dict[str, Any]:
    """My PRs merged in the period, and the median time from first recorded to merged."""
    durations, merged = [], 0
    for item in of_kind(items, MY_PR):
        when = last_entered(item, {MERGED}, period)
        if not period.has(when):
            continue
        merged += 1
        took = seconds_between(created(item, period), when)
        if took is not None:
            durations.append(took)
    return {"merged": merged, "median_open_to_merge_seconds": median(durations)}


def review_rounds(item: Item, period: Period) -> list[tuple[datetime | None, datetime | None]]:
    """(asked, done) per review round: asked is when the item was first recorded or a re-review was
    requested; done is when it moved into its kind's work_done state (reviewed, commented)."""
    done_states = set(ledger.KINDS.get(str(item.get("kind")), {}).get("work_done", set()))
    asked, rounds = created(item, period), []
    for state, when in state_changes(item, period):
        if state == REREVIEW:
            asked = when
        elif state in done_states:
            rounds.append((asked, when))
            asked = None
    return rounds


def reviews(items: list[Item], period: Period) -> dict[str, Any]:
    """Review rounds finished in the period (a re-review is a round of its own), the items they
    were on, and the median turnaround from asked to done."""
    done, on, turnaround = 0, set(), []
    for item in of_kind(items, *REVIEW_KINDS):
        for asked, finished in review_rounds(item, period):
            if not period.has(finished):
                continue
            done += 1
            on.add(str(item.get("id")))
            took = seconds_between(asked, finished)
            if took is not None:
                turnaround.append(took)
    return {"done": done, "items": len(on), "median_turnaround_seconds": median(turnaround)}


# ---- tasks and hand-overs ----------------------------------------------------------------------


def handover_window(entry: Mapping[str, Any]) -> str:
    return str(entry.get("to") if entry.get("event") == "moved" else entry.get("window") or "")


def handovers(item: Item, period: Period) -> Iterator[tuple[str, datetime | None, datetime | None]]:
    """(window, handed over, next report after it) for each hand-over of the item."""
    entries = history(item)
    for index, entry in enumerate(entries):
        if entry.get("event") not in HANDOVERS:
            continue
        report = next((e for e in entries[index + 1 :] if e.get("event") == REPORT), None)
        yield handover_window(entry), period.entry_time(entry), period.entry_time(report) if report else None


def accepted_in(item: Item, period: Period) -> bool:
    return any(
        e.get("event") == "transition" and e.get("from") == "proposed" and e.get("to") == "accepted"
        and period.has(period.entry_time(e))
        for e in history(item)
    )  # fmt: skip


def handover_counts(items: list[Item], period: Period) -> dict[str, Any]:
    """Hand-overs (delegated, moved) of any item in the period per window, and the median time from
    a hand-over to the report that followed it (counted when the report falls in the period)."""
    per_window: Counter[str] = Counter()
    report_after = []
    for item in items:
        for window, given, reported in handovers(item, period):
            if period.has(given):
                per_window[window or "?"] += 1
            took = seconds_between(given, reported)
            if period.has(reported) and took is not None:
                report_after.append(took)
    ranked = sorted(per_window.items(), key=lambda p: (-p[1], p[0]))
    return {
        "handovers": sum(per_window.values()),
        "per_window": [{"window": w, "count": n} for w, n in ranked],
        "median_handover_to_report_seconds": median(report_after),
    }


def count(items: Iterable[Item], test: Callable[[Item], bool]) -> int:
    return sum(1 for item in items if test(item))


def closed_as(items: list[Item], outcome: str, period: Period) -> int:
    return count(items, lambda i: period.has(last_entered(i, {outcome}, period)))


def tasks(items: list[Item], period: Period) -> dict[str, Any]:
    """Tasks spawned (first recorded), accepted (proposed -> accepted) and closed by outcome in the
    period, and the hand-overs (handover_counts)."""
    task_items = of_kind(items, TASK)
    outcomes = {outcome: closed_as(task_items, outcome, period) for outcome in TASK_OUTCOMES}
    return {
        "spawned": count(task_items, lambda i: period.has(created(i, period))),
        "accepted": count(task_items, lambda i: accepted_in(i, period)),
        **outcomes,
        **handover_counts(items, period),
    }


# ---- outreach ----------------------------------------------------------------------------------


@dataclass
class Sent:
    """One outreach item whose first message went out in the period."""

    item: Item
    first: datetime
    nudges: int
    one_way: bool
    replied: bool
    replied_at: datetime | None


def message_times(item: Item, period: Period) -> list[datetime]:
    messages = fields_of(item).get("messages")
    if not isinstance(messages, list):
        return []
    times = (period.time(m.get("at")) for m in messages if isinstance(m, dict))
    return sorted(t for t in times if t is not None)


def sent_in(items: list[Item], period: Period) -> list[Sent]:
    found = []
    for item in of_kind(items, OUTREACH):
        times = message_times(item, period)
        if times and period.has(times[0]):  # replied to before any message was recorded: never sent
            fields = fields_of(item)
            replied = bool(fields.get("answer")) or item.get("state") == REPLIED
            replied_at = period.time(fields.get("replied_at")) or last_reply(item, period)
            one_way = fields.get("purpose") == ledger.ONE_WAY_PURPOSE
            found.append(
                Sent(item, times[0], len(times) - 1, one_way, replied, replied_at if replied else None)
            )
    return found


def last_reply(item: Item, period: Period) -> datetime | None:
    times = [when for state, when in state_changes(item, period) if state == REPLIED]
    return times[-1] if times else None


def ended(two_way: list[Sent], state: str) -> int:
    return sum(1 for s in two_way if s.item.get("state") == state and not s.replied)


def outreach(sent: list[Sent]) -> dict[str, Any]:
    """Outreach whose first message went out in the period: how it ended for the two-way ones
    (replied, gave up with no reply, still waiting, dropped), the median time from the first
    message to the reply, and nudges (follow-up messages) per reply."""
    two_way = [s for s in sent if not s.one_way]
    replied = [s for s in two_way if s.replied]
    return {
        "sent": len(sent),
        "one_way": len(sent) - len(two_way),
        "replied": len(replied),
        "no_reply": ended(two_way, NO_REPLY),
        "waiting": ended(two_way, "sent"),
        "dropped": ended(two_way, "dropped"),
        "nudges": sum(s.nudges for s in sent),
        **reply_stats(replied),
    }


def reply_stats(replied: list[Sent]) -> dict[str, Any]:
    """Nudges per reply, and the median time from the first message to the reply."""
    times = [seconds_between(s.first, s.replied_at) for s in replied]
    nudged = sum(s.nudges for s in replied)
    return {
        "nudges_per_reply": round(nudged / len(replied), 2) if replied else None,
        "median_reply_seconds": median(t for t in times if t is not None),
    }


# ---- people ------------------------------------------------------------------------------------


def names(value: Any) -> list[str]:
    """The people in a recipient or requester field: split at commas, 'and', '&'; notes in
    parentheses and a leading @ dropped."""
    if not isinstance(value, str):
        return []
    parts = (p.strip().lstrip("@").strip() for p in NAME_SEPARATORS.split(NAME_NOTE.sub("", value)))
    return [p for p in parts if p]


@dataclass
class Tally:
    """Counts per person, case-insensitive, shown under the first spelling seen."""

    counts: Counter[str] = field(default_factory=Counter)
    shown: dict[str, str] = field(default_factory=dict)

    def add(self, people: Iterable[str]) -> None:
        for person in dict.fromkeys(p.casefold() for p in people):
            self.counts[person] += 1
        for person in people:
            self.shown.setdefault(person.casefold(), person)

    def model(self) -> dict[str, Any]:
        top = sorted(self.counts.items(), key=lambda p: (-p[1], p[0]))[:TOP_PEOPLE]
        return {"distinct": len(self.counts), "top": [{"name": self.shown[k], "count": n} for k, n in top]}


def requesters(item: Item) -> list[str]:
    delegation = item.get("delegation") if isinstance(item.get("delegation"), dict) else {}
    found = [*names(fields_of(item).get("requester")), *names((delegation or {}).get("requester"))]
    return list(dict.fromkeys(found))


def people(items: list[Item], sent: list[Sent], period: Period) -> dict[str, Any]:
    """Distinct people reached out to (outreach sent in the period) and people we worked for
    (requesters of items first recorded in the period), with the top TOP_PEOPLE by count."""
    recipients, asked_by = Tally(), Tally()
    for one in sent:
        recipients.add(names(fields_of(one.item).get("to")))
    for item in items:
        if period.has(created(item, period)):
            asked_by.add(requesters(item))
    return {"recipients": recipients.model(), "requesters": asked_by.model()}


# ---- bosun itself --------------------------------------------------------------------------


@dataclass(frozen=True)
class Own:
    """What bosun's own state files say (lib/console_state.py reads them)."""

    deliveries: list[dict[str, Any]] = field(default_factory=list)  # events: at, via, events
    slack_spend: dict[str, float] = field(default_factory=dict)  # local day -> USD
    slack_runs: list[dict[str, Any]] = field(default_factory=list)  # started, outcome, cost
    learn_runs: list[dict[str, Any]] = field(default_factory=list)  # started, cost, dry_run, outcome
    errors: dict[str, str] = field(default_factory=dict)  # part -> why it is missing


def deliveries(own: Own, period: Period) -> dict[str, Any]:
    by_via: Counter[str] = Counter()
    events = 0
    for delivery in own.deliveries:
        if period.has(period.time(delivery.get("at"))):
            by_via[str(delivery.get("via", "?"))] += 1
            events += len(delivery.get("events") or [])
    return {
        "by_via": dict(by_via),
        "events": events,
        "kept_days": EVENTS_KEPT_DAYS,
        "error": own.errors.get("events"),
    }


def spend_days(own: Own, period: Period) -> dict[str, dict[str, float]]:
    """USD per local day of the period: the Slack watcher's daily spend, learning runs' cost."""
    days = {day: {"slack_watch": 0.0, "learn": 0.0} for day in period.day_list()}
    for day, usd in own.slack_spend.items():
        if day in days and isinstance(usd, int | float):
            days[day]["slack_watch"] += float(usd)
    for run in own.learn_runs:
        started = period.time(run.get("started"))
        if period.has(started) and started is not None and isinstance(run.get("cost"), int | float):
            days[period.day_of(started)]["learn"] += float(run["cost"])
    return days


def learn_runs(own: Own, period: Period) -> dict[str, Any]:
    chosen = [r for r in own.learn_runs if period.has(period.time(r.get("started")))]
    return {
        "runs": sum(1 for r in chosen if not r.get("dry_run")),
        "dry_runs": sum(1 for r in chosen if r.get("dry_run")),
        "failed": sum(1 for r in chosen if r.get("outcome") == "failed"),
        "error": own.errors.get("learn"),
    }


def slack_runs(own: Own, period: Period) -> dict[str, Any]:
    chosen = [r for r in own.slack_runs if period.has(period.time(r.get("started")))]
    return {
        "runs": len(chosen),
        "failed": sum(1 for r in chosen if r.get("outcome") == "failed"),
        "kept_days": EVENTS_KEPT_DAYS,
        "error": own.errors.get("slack_watch"),
    }


def bosun(data: Mapping[str, Any], own: Own, period: Period) -> dict[str, Any]:
    """Its last chores runs (the ledger keeps only the latest of each), event deliveries by how
    they went out, Slack-watch runs, learning runs, and spend per day."""
    runs = data.get("runs") if isinstance(data.get("runs"), dict) else {}
    days = spend_days(own, period)
    return {
        "chores": [{"name": str(n), "at": str(at)} for n, at in sorted((runs or {}).items())],
        "deliveries": deliveries(own, period),
        "slack_watch": {
            **slack_runs(own, period),
            "usd": round(sum(d["slack_watch"] for d in days.values()), 4),
        },
        "learn": {**learn_runs(own, period), "usd": round(sum(d["learn"] for d in days.values()), 4)},
        "spend": [{"date": day, **{k: round(v, 4) for k, v in usd.items()}} for day, usd in days.items()],
    }


# ---- odd times ---------------------------------------------------------------------------------


def time_values(item: Item) -> Iterator[Any]:
    """Every time the metrics read from an item."""
    yield item.get("created_at")
    yield item.get("updated_at")
    for entry in history(item):
        yield entry.get("at")
        yield entry.get("happened_at")
    fields = fields_of(item)
    yield fields.get("replied_at")
    messages = fields.get("messages")
    for message in messages if isinstance(messages, list) else []:
        yield message.get("at") if isinstance(message, dict) else message


def odd_times(items: Iterable[Item]) -> int:
    """How many times in the items are there but do not parse (their metrics leave them out)."""
    return sum(
        1
        for item in items
        for value in time_values(item)
        if value not in (None, "") and moment(value) is None
    )


# ---- the model ---------------------------------------------------------------------------------


def linkable(item: Item) -> bool:
    """Whether the item's links and fields can be read (an odd one is left out of the rollup)."""
    links = item.get("links") or []
    return (
        isinstance(links, list)
        and all(isinstance(entry, dict) and "rel" in entry and "id" in entry for entry in links)
        and isinstance(item.get("fields") or {}, dict)
    )


def opened_closed(item: Item, period: Period) -> list[str]:
    """'opened' if the item was first recorded in the period, 'closed' if it reached a terminal
    state in it (as throughput counts them)."""
    moments = (
        ("opened", created(item, period)),
        ("closed", last_entered(item, terminal(str(item.get("kind"))), period)),
    )
    return [what for what, when in moments if when is not None and period.has(when)]


def by_workstream(items: list[Item], period: Period, rules: workstreams.RuleSet | None) -> dict[str, Any]:
    """Items opened and closed in the period per top-level workstream (the workstreams under it
    rolled up), and those in none, as `ledger` resolves them."""
    found = {str(item.get("id")): item for item in items if linkable(item)}
    resolver = workstreams.Resolver(found, rules)
    counts: dict[str, Counter[str]] = {}
    for item_id, item in found.items():
        if workstreams.in_scope(item):
            ws = resolver.resolve(item_id).ws
            top = workstreams.ancestors(found, ws)[-1] if ws else ""
            counts.setdefault(top, Counter()).update(opened_closed(item, period))
    rows = [
        {"ws": ws, "title": str(found[ws].get("title") or ws), "opened": c["opened"], "closed": c["closed"]}
        for ws, c in sorted(counts.items())
        if ws
    ]
    none = counts.get("", Counter())
    return {"rows": rows, "unassigned": {"opened": none["opened"], "closed": none["closed"]}}


def stats_model(
    data: Mapping[str, Any],
    own: Own,
    days: int,
    now: datetime,
    zone: tzinfo,
    rules: workstreams.RuleSet | None = None,
) -> dict[str, Any]:
    """/api/stats: every metric for the last <days> local days up to <now>."""
    period = Period(days, now, zone)
    items = items_of(data)
    sent = sent_in(items, period)
    return {
        "days": days,
        "periods": list(PERIODS),
        "from": period.start.isoformat(timespec="seconds"),
        "to": now.astimezone(zone).isoformat(timespec="seconds"),
        "throughput": throughput(items, period),
        "prs": my_prs(items, period),
        "reviews": reviews(items, period),
        "tasks": tasks(items, period),
        "outreach": outreach(sent),
        "people": people(items, sent, period),
        "bosun": bosun(data, own, period),
        "workstreams": by_workstream(items, period, rules),
        "odd_times": odd_times(items),
    }
