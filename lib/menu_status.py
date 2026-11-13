"""How bosun is, as one object: the level, the sentence and the lines of `bosun status --json` (design:
docs/design/menu-bar.md, "The CLI side"). The menu bar app shows it and reads nothing else of bosun's.

Pure over StatusFacts, which lib/menu_status_live.py gathers, and a time (local and naive, like the
awake window's). The levels are the watchdog's own and the console's: a check's severity (lib/watchdog.py,
through `status_data`) decides error or warning, and the windows waiting at a prompt are
lib/console_sessions.py's. What this adds is what the menu needs and the watchdog's checks do not say:

- stopped: you quit bosun (lib/quit_state.py). Nothing else matters then.
- error: no window is open inside the awake hours and bosun is not stopped (the watchdog's own
  `session-missing` finding is then the problem listed, not a second line); or a check of severity error
  has a finding.
- warning: a check of severity warn has a finding, an ERROR line was logged in the last hour, or a fact
  could not be read (an answer built on a guess is never all well).
- ok otherwise, and "resting until 08:00" when no window is open outside the awake hours.

The contract number (CONTRACT) is the app's check that it understands what it reads: it only ever grows,
and a field is only ever added.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import awake
import console_sessions
import quit_state
import watchdog


CONTRACT = 1
OK, WARNING, ERROR, STOPPED = "ok", "warning", "error", "stopped"
RANK = {OK: 0, WARNING: 1, ERROR: 2}
SEVERITY_LEVEL = {"warn": WARNING, "error": ERROR}
SESSION_CHECK = "session-missing"
LOG_CHECK = "log-errors"
READ_PART = {
    "sessions": "the windows",
    "watchdog": "the watchdog's checks",
    "config": "bosun's settings",
    "logs": "the logs",
}
DAYS_AHEAD = 8  # the next scheduled start is within a week; a day more for the same weekday


@dataclass(frozen=True)
class StatusFacts:
    """What the snapshot is made of. A part that could not be read is None (or absent) and has its
    reason in <errors>, keyed by part (READ_PART)."""

    windows: list[dict[str, Any]] | None  # `bosun sessions --json`
    checks: Mapping[str, Any] | None  # watchdog.status_data
    awake: awake.AwakeWindow | None  # None when the settings did not load
    quit: quit_state.QuitState | None
    update_running: bool = False
    update_waiting_for: tuple[str, ...] = ()
    console_running: bool = False
    keep_awake_until: datetime | None = None  # local and naive
    log_errors: tuple[str, ...] = ()  # "<log>: <line>" of the last hour's ERROR lines
    log_dir: str = ""
    errors: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    level: str
    sentence: str
    detail: list[str]
    problems: list[str]


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# ---- what the facts say ------------------------------------------------------------------------


def open_windows(windows: Iterable[Mapping[str, Any]] | None) -> int:
    return sum(1 for w in windows or [] if w.get("window"))


def findings(checks: Mapping[str, Any] | None) -> list[tuple[str, str, str]]:
    """Each finding of the watchdog's checks as (level, check name, message)."""
    found = []
    for item in (checks or {}).get("checks", []):
        level = SEVERITY_LEVEL.get(item["severity"], WARNING)
        found += [(level, item["name"], f["message"]) for f in item["findings"]]
    return found


def next_start(window: awake.AwakeWindow | None, now: datetime) -> datetime | None:
    """When the awake window next begins after <now>; None without one or with no day scheduled."""
    if window is None:
        return None
    for ahead in range(DAYS_AHEAD):
        day = now.date() + timedelta(days=ahead)
        if window.scheduled(day) and window.bounds(day)[0] > now:
            return window.bounds(day)[0]
    return None


def log_problem(lines: tuple[str, ...]) -> str:
    shown = "; ".join(lines[: watchdog.MAX_DETAIL_LINES])
    more = (
        f" (+{len(lines) - watchdog.MAX_DETAIL_LINES} more)" if len(lines) > watchdog.MAX_DETAIL_LINES else ""
    )
    return f"{plural(len(lines), 'ERROR line')} in the bosun logs in the last hour: {shown}{more}"


def unread_problems(errors: Mapping[str, str]) -> list[tuple[str, str]]:
    return [(WARNING, f"could not read {READ_PART.get(p, p)}: {why}") for p, why in sorted(errors.items())]


def problems_of(facts: StatusFacts, missing: bool) -> list[tuple[str, str]]:
    """Every problem as (level, text), the watchdog's findings first. <missing>: no window is open
    inside the awake hours."""
    checked = findings(facts.checks)
    found = [(level, message) for level, _name, message in checked]
    named = {name for _level, name, _message in checked}
    if missing and SESSION_CHECK not in named:
        text = "bosun's tmux session is not running, inside its awake hours. Start it with: bosun start"
        found.append((ERROR, text))
    if facts.log_errors and LOG_CHECK not in named:
        found.append((WARNING, log_problem(facts.log_errors)))
    return found + unread_problems(facts.errors)


# ---- the sentence ------------------------------------------------------------------------------


def with_level(base: str, level: str, running: bool) -> str:
    if level == WARNING:
        return f"{base}, with a warning"
    if level == ERROR and running:
        return f"{base}, with an error"
    return base


def running_sentence(facts: StatusFacts, level: str, count: int | None, now: datetime) -> str:
    """The first line of the menu while bosun is not stopped."""
    if count is None:
        return "bosun's windows could not be read"
    if count > 0:
        return with_level("bosun is running", level, True)
    if facts.awake is not None and facts.awake.contains(now):
        return "bosun is not running"
    start = next_start(facts.awake, now)
    if start is None or level == ERROR:
        return "bosun is not running"
    return with_level(f"bosun is resting until {start:%H:%M}", level, False)


def state_lines(facts: StatusFacts, count: int, waiting: list[str], now: datetime) -> list[str]:
    lines = [f"{plural(count, 'window')} open"] if count else []
    if waiting:
        lines.append(f"{len(waiting)} waiting for you: {', '.join(waiting)}")
    if facts.keep_awake_until is not None and facts.keep_awake_until > now:
        lines.append(f"Mac kept awake until {facts.keep_awake_until:%H:%M}")
    if facts.update_running:
        wait = f", waiting for {', '.join(facts.update_waiting_for)}" if facts.update_waiting_for else ""
        lines.append(f"An update is running{wait}")
    return lines


def running_verdict(facts: StatusFacts, now: datetime, waiting: list[str]) -> Verdict:
    count = None if facts.windows is None else open_windows(facts.windows)
    inside = facts.awake is not None and facts.awake.contains(now)
    problems = problems_of(facts, missing=count == 0 and inside)
    level = max((level for level, _text in problems), key=RANK.__getitem__, default=OK)
    return Verdict(
        level,
        running_sentence(facts, level, count, now),
        state_lines(facts, count or 0, waiting, now),
        [text for _level, text in problems],
    )


def stopped_verdict(state: quit_state.QuitState) -> Verdict:
    detail = [f"You quit bosun at {quit_state.clock(state)}"]
    if state.morning_paused:
        detail.append("The morning start is paused until you start bosun")
    problems = []
    if not state.readable:
        text = "The quit record is unreadable, so the morning is paused too; bosun start clears it"
        detail.append(text)
        problems.append(text)
    return Verdict(STOPPED, "bosun is stopped", detail, problems)


# ---- the object --------------------------------------------------------------------------------


def snapshot(facts: StatusFacts, now: datetime) -> dict[str, Any]:
    """`bosun status --json`: the object the menu bar app reads. <now> is local and naive."""
    waiting = [str(w["name"]) for w in console_sessions.waiting_in(facts.windows or [], now)]
    verdict = stopped_verdict(facts.quit) if facts.quit else running_verdict(facts, now, waiting)
    kept = facts.keep_awake_until
    return {
        "contract": CONTRACT,
        "level": verdict.level,
        "sentence": verdict.sentence,
        "detail": verdict.detail,
        "windows": open_windows(facts.windows),
        "waiting": waiting,
        "keep_awake_until": f"{kept:%H:%M}" if kept is not None and kept > now else None,
        "stopped": stopped_fields(facts.quit),
        "update": {
            "state": "running" if facts.update_running else "none",
            "waiting_for": list(facts.update_waiting_for),
        },
        "console": {"running": facts.console_running, "url": None},  # the address carries the token
        "problems": verdict.problems,
        "logs": facts.log_dir,
    }


def stopped_fields(state: quit_state.QuitState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {"since": state.since.isoformat(timespec="seconds"), "morning_paused": state.morning_paused}


def failure(text: str, level: str = ERROR) -> dict[str, Any]:
    """The object when little could be found out: bosun's settings do not load, or the command itself
    went wrong. Every field is there, so a reader needs no special case."""
    return {
        "contract": CONTRACT,
        "level": level,
        "sentence": "bosun cannot tell how it is",
        "detail": [],
        "windows": 0,
        "waiting": [],
        "keep_awake_until": None,
        "stopped": None,
        "update": {"state": "none", "waiting_for": []},
        "console": {"running": False, "url": None},
        "problems": [text],
        "logs": "",
    }


def unreadable_config(text: str) -> dict[str, Any]:
    """bosun.env cannot be read, so none of bosun runs: <text> says why (lib/common.sh's read_config)."""
    return {**failure(text), "sentence": "bosun cannot read its settings"}
