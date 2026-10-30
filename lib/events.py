#!/usr/bin/env python3
"""Events from repo sessions, pushed to the orchestrator (design: docs/design/events.md).

Usage: events.py dispatch [--dry-run]    (facts JSON on stdin; run it as `bosun events`)
       events.py covered                 mark every pending event delivered (a check-in went out)
       events.py pasted <window>         `bosun send` pastes the text on stdin into <window>
       events.py status | list [--all]

With BOSUN_EVENTS_ENABLED=1, the ledger appends an event when a repo session reports, records
outreach or spawns a task, and when I type in a repo window (user-said); the dispatcher adds one
when a repo window waits at a prompt. Events are kept in $BOSUN_STATE_DIR/events/queue.jsonl
(append-only, one JSON object per line, each with an id); state.json next to it records which were
delivered, when and how, and the dispatcher's last run.

The dispatcher runs from the check-in loop every BOSUN_EVENTS_INTERVAL_SECONDS. It delivers one
batched summary to the orchestrator, through `bosun send --no-item` (the transport), only when:
- events are pending that the orchestrator did not cause itself;
- a lane is due (lib/event_lanes.py): its newest event is its debounce old (a burst becomes one
  delivery), or its oldest has waited its longest wait. The fast lane (BOSUN_EVENTS_FAST_KINDS)
  uses BOSUN_EVENTS_FAST_DEBOUNCE_SECONDS / _FAST_MAX_WAIT_SECONDS, the routine lane
  BOSUN_EVENTS_DEBOUNCE_SECONDS / _MAX_WAIT_SECONDS, or the AWAY_ ones while nobody typed in
  tmux for BOSUN_EVENTS_AWAY_MINUTES; a delivery takes every pending event;
- the orchestrator is on today's conversation and its turn ended BOSUN_EVENTS_MIN_IDLE_SECONDS
  ago: idle, or shell (only a background command still runs; not busy, never at a prompt), and
  nobody typed in tmux within BOSUN_ACTIVE_CLIENT_MINUTES;
- fewer than BOSUN_EVENTS_MAX_PER_DAY deliveries went out today (the next check-in covers
  the rest).
The summary (lib/event_summary.py) shows BOSUN_EVENTS_MAX_LINES events, each with up to
BOSUN_EVENTS_INLINE_CHARS of what was recorded.
A delivery marks its events delivered (at least once: a crash between the send and the mark sends
them again). A check-in that goes out marks the pending events as covered by it.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import activity
import agent_state
import event_lanes
import event_summary
import signals
from event_lanes import Lanes, Timing
from event_model import SUMMARY_LENGTH, Event, EventsError, new_event, parse_kinds, short
from event_summary import Render


BOSUN = Path(__file__).resolve().parent.parent / "bin" / "bosun"
SUBDIR = "events"
QUEUE_FILE = "queue.jsonl"
STATE_FILE = "state.json"
LOCK_FILE = "lock"
# Prompts bosun pasted into a window (`bosun send`), by hash, so the window's hook does
# not take them for mine; how long one counts (a queued prompt may wait for a long turn), and how
# many lines the file keeps.
PASTED_FILE = "pasted.jsonl"
PASTED_HOURS = 12
PASTED_KEEP = 500
SECONDS_PER_HOUR = 3600
DELIVERED_RETENTION_DAYS = 7
WAITING_SEEN_KEEP = 100  # prompts remembered as already turned into waiting events
SECONDS_PER_DAY = 86400
MS_PER_SECOND = 1000
VIA_SEND = "send"
VIA_CHECKIN = "check-in"
VIA_OWN = "own"  # caused by the orchestrator itself: never delivered to it
NOTHING_PENDING = "nothing pending"
# The orchestrator's turn is over: idle, or shell (only a background command still runs). It takes a
# prompt in both, and the command's completion notice queues behind it. In both, statusUpdatedAt is
# when the turn ended, so the idle time counts from there (agent_turn_over in lib/common.sh).
TURN_OVER = ("idle", "shell")


# ---- the queue ---------------------------------------------------------------------------------


def events_dir(state_dir: Path) -> Path:
    return state_dir / SUBDIR


@contextlib.contextmanager
def locked(state_dir: Path) -> Iterator[Path]:
    directory = events_dir(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / LOCK_FILE).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield directory


def write(directory: Path, event: Event) -> None:
    """Append <event> to the queue (the caller holds the lock)."""
    with (directory / QUEUE_FILE).open("a") as queue:
        queue.write(json.dumps(asdict(event), sort_keys=True) + "\n")


def append(state_dir: Path, event: Event) -> Event:
    """Add <event> (event_model.new_event) to the queue."""
    with locked(state_dir) as directory:
        write(directory, event)
    return event


def read_queue(directory: Path) -> list[Event]:
    try:
        lines = (directory / QUEUE_FILE).read_text().splitlines()
    except FileNotFoundError:
        return []
    found = []
    for line in lines:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            found.append(Event.of(json.loads(line)))
    return sorted(found, key=lambda e: e.id)


@dataclass
class State:
    delivered: dict[str, str] = field(default_factory=dict)  # event id -> "<via> <when>"
    deliveries: list[dict[str, Any]] = field(default_factory=list)  # when, via, events (recent)
    waiting_seen: list[str] = field(default_factory=list)  # prompts already turned into events
    last_tick: str | None = None
    last_error: dict[str, str] | None = None  # when, what: the last failed delivery
    last_reason: str = ""  # why the last run delivered nothing (logged only when it changes)

    @classmethod
    def load(cls, directory: Path) -> State:
        try:
            data = json.loads((directory / STATE_FILE).read_text())
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as exc:
            raise EventsError(f"unreadable {directory / STATE_FILE} ({exc}); move it aside") from exc
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    def save(self, directory: Path) -> None:
        tmp = directory / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))
        tmp.replace(directory / STATE_FILE)

    def mark(self, events: list[Event], via: str, when: datetime) -> None:
        for event in events:
            self.delivered[event.id] = f"{via} {when.isoformat(timespec='seconds')}"

    def deliveries_on(self, day: date) -> int:
        return sum(
            1
            for d in self.deliveries
            if d["via"] == VIA_SEND and datetime.fromisoformat(d["at"]).astimezone().date() == day
        )

    def forget(self, old: set[str], cutoff: float) -> None:
        """Drop the marks of pruned events, and deliveries older than <cutoff>."""
        self.delivered = {k: v for k, v in self.delivered.items() if k not in old}
        self.deliveries = [d for d in self.deliveries if not before(d["at"], cutoff)]


def pending(directory: Path, state: State) -> list[Event]:
    return [e for e in read_queue(directory) if e.id not in state.delivered]


def prune(directory: Path, state: State, now: datetime) -> None:
    """Drop delivered events (and their marks) older than the retention, under the lock."""
    cutoff = now.timestamp() - DELIVERED_RETENTION_DAYS * SECONDS_PER_DAY
    events = read_queue(directory)
    old = {e.id for e in events if e.id in state.delivered and before(e.at, cutoff)}
    if not old:
        return
    rewrite_queue(directory, [e for e in events if e.id not in old])
    state.forget(old, cutoff)


def rewrite_queue(directory: Path, events: list[Event]) -> None:
    tmp = directory / (QUEUE_FILE + ".tmp")
    tmp.write_text("".join(json.dumps(asdict(e), sort_keys=True) + "\n" for e in events))
    tmp.replace(directory / QUEUE_FILE)


def before(at: str, cutoff: float) -> bool:
    return datetime.fromisoformat(at).timestamp() < cutoff


# ---- prompts bosun pasted ---------------------------------------------------------------------


def note_pasted(state_dir: Path, window: str, text: str) -> None:
    """`bosun send` is about to paste <text> into <window>."""
    line = json.dumps(
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "window": window,
            "hash": activity.prompt_hash(text),
        }
    )
    with locked(state_dir) as directory:
        path = directory / PASTED_FILE
        lines = path.read_text().splitlines() if path.exists() else []
        path.write_text("\n".join([*lines[-PASTED_KEEP:], line]) + "\n")


def pasted_hashes(state_dir: Path, window: str, now: datetime) -> set[str]:
    """Hashes of what bosun pasted into <window> in the last PASTED_HOURS."""
    try:
        lines = (events_dir(state_dir) / PASTED_FILE).read_text().splitlines()
    except FileNotFoundError:
        return set()
    cutoff = now.timestamp() - PASTED_HOURS * SECONDS_PER_HOUR
    found = set()
    for line in lines:
        with contextlib.suppress(json.JSONDecodeError, KeyError, TypeError, ValueError):
            entry = json.loads(line)
            if entry["window"] == window and not before(entry["at"], cutoff):
                found.add(str(entry["hash"]))
    return found


# ---- the dispatcher ----------------------------------------------------------------------------


# The shortest inline text that still shows something besides the pointer to the rest.
MIN_INLINE_CHARS = 80


@dataclass(frozen=True)
class Settings:
    """The BOSUN_* settings `bosun events` passes (defaults: lib/common.sh)."""

    env: Mapping[str, str]

    def text(self, name: str) -> str:
        value = self.env.get(f"BOSUN_{name}")
        if value is None:
            raise EventsError(f"BOSUN_{name} is not set; run this as `bosun events`")
        return value

    def number(self, name: str, minimum: float = 0) -> float:
        try:
            value = float(self.text(name))
        except ValueError as exc:
            raise EventsError(f"BOSUN_{name} must be a number, got {self.text(name)!r}") from exc
        if value < minimum:
            raise EventsError(f"BOSUN_{name} must be at least {minimum:g}, got {value:g}")
        return value

    def timing(self, lane: str) -> Timing:
        """BOSUN_EVENTS_<lane>DEBOUNCE_SECONDS and ..._MAX_WAIT_SECONDS."""
        return Timing(
            self.number(f"EVENTS_{lane}DEBOUNCE_SECONDS"), self.number(f"EVENTS_{lane}MAX_WAIT_SECONDS")
        )

    def lanes(self) -> Lanes:
        return Lanes(
            routine=self.timing(""),
            fast=self.timing("FAST_"),
            away=self.timing("AWAY_"),
            fast_kinds=parse_kinds(self.text("EVENTS_FAST_KINDS"), "BOSUN_EVENTS_FAST_KINDS"),
            away_minutes=self.number("EVENTS_AWAY_MINUTES"),
        )

    def render(self) -> Render:
        return Render(
            inline_chars=int(self.number("EVENTS_INLINE_CHARS", MIN_INLINE_CHARS)),
            max_lines=int(self.number("EVENTS_MAX_LINES", 1)),
        )

    def transport(self) -> str:
        transport = self.text("EVENTS_TRANSPORT")
        if transport not in TRANSPORTS:
            raise EventsError(f"BOSUN_EVENTS_TRANSPORT must be one of {', '.join(TRANSPORTS)}")
        return transport


@dataclass(frozen=True)
class Config:
    state_dir: Path
    orchestrator: str
    lanes: Lanes
    render: Render
    min_idle: float
    max_per_day: int
    waiting_seconds: float
    active_client_minutes: float
    transport: str = VIA_SEND
    hook_signals: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        settings = Settings(env)
        return cls(
            state_dir=Path(settings.text("STATE_DIR")),
            orchestrator=settings.text("ORCHESTRATOR"),
            lanes=settings.lanes(),
            render=settings.render(),
            min_idle=settings.number("EVENTS_MIN_IDLE_SECONDS"),
            max_per_day=int(settings.number("EVENTS_MAX_PER_DAY")),
            waiting_seconds=settings.number("EVENTS_WAITING_SECONDS"),
            active_client_minutes=settings.number("ACTIVE_CLIENT_MINUTES"),
            transport=settings.transport(),
            hook_signals=env.get("BOSUN_HOOK_SIGNALS", "1") == "1",
        )


@dataclass(frozen=True)
class Orchestrator:
    pid: int | None = None
    name: str = ""  # its Claude session's name
    expected_name: str = ""  # today's conversation


@dataclass(frozen=True)
class Facts:
    """What `bosun events` saw of tmux: the orchestrator, the repo windows' Claude pids, and
    minutes since the last keystroke in an attached client (None: no client attached)."""

    orchestrator: Orchestrator = Orchestrator()
    windows: tuple[tuple[str, int], ...] = ()
    client_idle_minutes: float | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Facts:
        orch = data.get("orchestrator") or {}
        pid = orch.get("pid")
        idle = data.get("client_idle_minutes")
        return cls(
            Orchestrator(
                pid if isinstance(pid, int) else None,
                str(orch.get("name") or ""),
                str(orch.get("expected_name") or ""),
            ),
            tuple(
                (str(w["name"]), int(w["agent_pid"]))
                for w in data.get("windows") or []
                if isinstance(w.get("agent_pid"), int)
            ),
            float(idle) if isinstance(idle, (int, float)) else None,
        )


Record = Callable[[int], "dict[str, Any] | None"]


def seconds_in_status(record: Mapping[str, Any], now: datetime) -> float:
    updated = record.get("statusUpdatedAt")
    return now.timestamp() - updated / MS_PER_SECOND if isinstance(updated, (int, float)) else 0.0


def asked(config: Config, window: str, entry: Mapping[str, Any]) -> str:
    """What the waiting window was asked, from its hook signals (" (Bash: make test)"), or ""."""
    updated = entry.get("statusUpdatedAt")
    if not config.hook_signals or not isinstance(updated, (int, float)):
        return ""
    since = datetime.fromtimestamp(updated / MS_PER_SECOND) - signals.WAITING_SLACK
    question = signals.Signals.load(config.state_dir, window, entry.get("sessionId")).asking(since)
    return f" ({question})" if question else ""


def waiting_windows(config: Config, facts: Facts, state: State, now: datetime, record: Record) -> list[Event]:
    """New events for repo windows waiting at a prompt for BOSUN_EVENTS_WAITING_SECONDS: once
    per prompt (pid and status time)."""
    found = []
    for name, pid in facts.windows:
        entry = record(pid) if name != config.orchestrator else None
        if not entry or entry.get("status") != "waiting":
            continue
        key = f"{pid}:{entry.get('statusUpdatedAt')}"
        waited = seconds_in_status(entry, now)
        if key in state.waiting_seen or waited < config.waiting_seconds:
            continue
        state.waiting_seen = [*state.waiting_seen, key][-WAITING_SEEN_KEEP:]
        question = asked(config, name, entry)
        summary = f"waiting at a permission prompt or a question for {waited / 60:.0f} min{question}"
        found.append(new_event("waiting", window=name, origin=name, summary=summary, now=now))
    return found


def orchestrator_gate(config: Config, facts: Facts, now: datetime, record: Record) -> str | None:
    """Why the orchestrator cannot take a summary now, or None if it can."""
    orch = facts.orchestrator
    entry = record(orch.pid) if orch.pid else None
    if entry is None:
        return "the orchestrator's Claude is not running"
    status, idle = str(entry.get("status") or "unknown"), seconds_in_status(entry, now)
    if status not in TURN_OVER:
        return f"the orchestrator is {status} (for {idle:.0f}s)"
    if idle < config.min_idle:
        return f"the orchestrator has been {status} for only {idle:.0f}s (waits for {config.min_idle:.0f}s)"
    if orch.expected_name and orch.name != orch.expected_name:
        return (
            "the orchestrator is still on yesterday's conversation (a check-in, or bosun refresh"
            " of its window, starts today's)"
        )
    if facts.client_idle_minutes is not None and facts.client_idle_minutes < config.active_client_minutes:
        return f"you typed in tmux {facts.client_idle_minutes:.0f} minute(s) ago"
    return None


def send_via_bosun(config: Config, message: str) -> str | None:
    """The send transport: paste into the orchestrator's window like a check-in. None, or the error."""
    done = subprocess.run(
        [str(BOSUN), "send", "--no-item", config.orchestrator, "-"],
        input=message,
        capture_output=True,
        text=True,
        check=False,
    )
    return None if done.returncode == 0 else (done.stderr or done.stdout).strip()[-SUMMARY_LENGTH:]


Transport = Callable[[Config, str], "str | None"]
TRANSPORTS: dict[str, Transport] = {VIA_SEND: send_via_bosun}


@dataclass
class Tick:
    """What one dispatcher run decided: deliver these events, or why not."""

    deliver: list[Event] = field(default_factory=list)
    reason: str = ""


def decide(
    config: Config, facts: Facts, state: State, events: list[Event], now: datetime, record: Record
) -> Tick:
    """Whether the pending <events> (the orchestrator's own already removed) go out now."""
    if not events:
        return Tick(reason=NOTHING_PENDING)
    if state.deliveries_on(now.astimezone().date()) >= config.max_per_day:
        return Tick(
            reason=(
                f"{config.max_per_day} deliveries today already; the rest wait for the next check-in"
                " or tomorrow"
            )
        )
    reason = event_lanes.due(config.lanes, events, facts.client_idle_minutes, now) or orchestrator_gate(
        config, facts, now, record
    )
    return Tick(reason=reason) if reason else Tick(deliver=events)


@dataclass(frozen=True)
class Inputs:
    config: Config
    facts: Facts
    now: datetime
    record: Record = agent_state.record
    transports: Mapping[str, Transport] = field(default_factory=lambda: TRANSPORTS)


def dispatch(inputs: Inputs, dry_run: bool = False) -> str:
    """One dispatcher run: note waiting windows, drop the orchestrator's own events, and deliver
    the rest if the gates allow; returns what it did, for the log. The lock is not held while the
    transport runs, so ledger writes never wait for a delivery."""
    config, now = inputs.config, inputs.now
    if dry_run:
        with locked(config.state_dir) as directory:
            tick = plan(inputs, State.load(directory), directory)
        return (
            f"dry-run: would deliver:\n{event_summary.compose(tick.deliver, config.render)}"
            if tick.deliver
            else f"dry-run: {tick.reason}"
        )
    with locked(config.state_dir) as directory:
        state = State.load(directory)
        for event in waiting_windows(config, inputs.facts, state, now, inputs.record):
            write(directory, event)
        tick = plan(inputs, state, directory)
        state.last_tick = now.isoformat(timespec="seconds")
        reason = re.sub(r"\d+", "#", tick.reason)  # "busy (for 27s)" and "(for 37s)" are one reason
        repeated, state.last_reason = reason == state.last_reason, reason
        state.save(directory)
    if not tick.deliver:
        return "" if repeated or tick.reason == NOTHING_PENDING else f"skip: {tick.reason}"
    error = inputs.transports[config.transport](config, event_summary.compose(tick.deliver, config.render))
    with locked(config.state_dir) as directory:
        state = State.load(directory)
        outcome = record_delivery(config, state, tick.deliver, now, error)
        prune(directory, state, now)
        state.save(directory)
    return outcome


def plan(inputs: Inputs, state: State, directory: Path) -> Tick:
    """What to deliver: the pending events, minus those the orchestrator caused itself (marked
    delivered as its own), if the gates allow."""
    config = inputs.config
    queued = pending(directory, state)
    own = [e for e in queued if config.orchestrator and e.origin == config.orchestrator]
    state.mark(own, VIA_OWN, inputs.now)
    others = [e for e in queued if e not in own]
    return decide(config, inputs.facts, state, others, inputs.now, inputs.record)


def record_delivery(
    config: Config, state: State, events: list[Event], now: datetime, error: str | None
) -> str:
    if error:
        state.last_error = {"at": now.isoformat(timespec="seconds"), "error": error}
        return f"delivery failed ({len(events)} event(s) stay pending; the watchdog reports it): {error}"
    state.mark(events, VIA_SEND, now)
    state.deliveries.append(
        {"at": now.isoformat(timespec="seconds"), "via": VIA_SEND, "events": [e.id for e in events]}
    )
    state.last_error = None
    return f"delivered {len(events)} event(s) to {config.orchestrator}"


def cover(state_dir: Path, now: datetime) -> str:
    """A check-in went out: it reads the reports and what I said since its scan start, so the
    pending events need no delivery of their own."""
    with locked(state_dir) as directory:
        state = State.load(directory)
        queued = pending(directory, state)
        state.mark(queued, VIA_CHECKIN, now)
        if queued:
            state.deliveries.append(
                {
                    "at": now.isoformat(timespec="seconds"),
                    "via": VIA_CHECKIN,
                    "events": [e.id for e in queued],
                }
            )
        state.save(directory)
    return f"{len(queued)} pending event(s) covered by the check-in"


def render_status(state_dir: Path, now: datetime) -> str:
    directory = events_dir(state_dir)
    state = State.load(directory)
    waiting = pending(directory, state)
    error = f"{state.last_error['at']}: {state.last_error['error']}" if state.last_error else "none"
    return "\n".join(
        [
            f"pending: {len(waiting)}" + (f" (oldest {waiting[0].at})" if waiting else ""),
            f"last dispatcher run: {state.last_tick or 'never'}",
            f"deliveries today: {state.deliveries_on(now.astimezone().date())}",
            f"last delivery error: {error}",
        ]
    )


def render_list(state_dir: Path, show_all: bool) -> str:
    directory = events_dir(state_dir)
    state = State.load(directory)
    rows = [
        f"{e.at} {e.type:<9} {e.window or e.origin or '-':<14} {e.item or e.ref or '-'}  {short(e.summary)}"
        + (f"  [{state.delivered[e.id]}]" if e.id in state.delivered else "  [pending]")
        for e in read_queue(directory)
        if show_all or e.id not in state.delivered
    ]
    return "\n".join(rows) or "(no events)"


def current_time(env: Mapping[str, str]) -> datetime:
    """Now (UTC); BOSUN_EVENTS_NOW (epoch seconds) for tests."""
    override = env.get("BOSUN_EVENTS_NOW")
    return datetime.fromtimestamp(float(override), UTC) if override else datetime.now(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun events", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    dispatch_parser = sub.add_parser("dispatch")
    dispatch_parser.add_argument("--dry-run", action="store_true")
    sub.add_parser("covered")
    pasted_parser = sub.add_parser("pasted")
    pasted_parser.add_argument("window")
    sub.add_parser("status")
    list_parser = sub.add_parser("list")
    list_parser.add_argument("--all", action="store_true")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    now = current_time(os.environ)
    try:
        state_dir = Path(os.environ["BOSUN_STATE_DIR"])
        if args.command == "covered":
            print(cover(state_dir, now))
        elif args.command == "pasted":
            note_pasted(state_dir, args.window, sys.stdin.read())
        elif args.command == "status":
            print(render_status(state_dir, now))
        elif args.command == "list":
            print(render_list(state_dir, args.all))
        else:
            inputs = Inputs(Config.from_env(os.environ), Facts.from_json(json.load(sys.stdin)), now)
            outcome = dispatch(inputs, args.dry_run)
            if outcome:
                print(outcome)
    except (EventsError, KeyError, json.JSONDecodeError) as exc:
        print(f"events: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
