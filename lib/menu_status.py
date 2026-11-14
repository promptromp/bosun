"""How bosun is, as one object: the level, the sentence and the lines of `bosun status --json` (design:
docs/design/menu-bar.md, "The CLI side"). The menu bar app shows it and reads nothing else of bosun's.

Pure over StatusFacts, which lib/menu_status_live.py gathers, and a time (local and naive, like the
awake window's). The levels are the watchdog's own and the console's: a check's severity (lib/watchdog.py,
through `status_data`) decides error or warning, and the windows waiting at a prompt are
lib/console_sessions.py's. Whether a missing session is a problem is the watchdog's own gate
(`watchdog.session_gate_kind`, which `session-missing` uses), carried in as a SessionGate: no rule of
its own, so the menu and the watchdog never disagree.

- error, a quit that did not finish: a quit record (lib/quit_state.py) while windows are still open (its
  `bosun stop` failed or was cut short, and the watchdog is skipping); the problem says how to finish.
- stopped: you quit bosun (lib/quit_state.py), nothing else matters then; or no window is open and the
  gate says you stopped it today with `bosun stop` (the morning run starts it as usual tomorrow).
- error: no window is open and the gate says it should be running (the watchdog's own `session-missing`
  finding is then the problem listed, not a second line); or a check of severity error has a finding.
- warning: a check of severity warn has a finding, an ERROR line was logged in the last hour, the watchdog
  itself is not checking (its launchd job is installed but not loaded, or loaded and its last run is older
  than 3 of its intervals of the Mac being awake: counted from the latest of that run, the Mac's last wake and
  its boot, a time later than now, after the clock moved back, left out; not while an update holds it, and
  never in a quit, which is stopped), or a fact could not be read (an answer built on a guess is never all
  well). That
  holds when bosun is stopped too: with a part unread status cannot vouch for "stopped", so it is a warning
  (the `stopped` field stays).
- ok otherwise: "resting until 08:00" when no window is open outside the awake hours, "starting (the
  morning run)" once the window began and the run is going or not yet due (at the error level never
  "starting": a crashed or hung run keeps counting as going, and is named as failed). With no morning run
  today there is no rule here: the watchdog's `morning-missing` finding sets the level, as any finding does.

The contract number (CONTRACT) is the app's check that it understands what it reads: it only ever grows,
and a field is only ever added. A new level is a new word in `level`, which the app shows as a warning until
it knows it (never as all well); `running` says whether bosun's windows are open (null when they could not be
read), which with the level tells a bosun to start from one that runs with an error. `update.last` is the
record the last update left (`id`, `started_at`, `step`; null when there is none): an update that ended before
any look saw it running is told by it, so the app can end "Updating…" for the one it started.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
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
MORNING_FAILED = "morning-failed"
READ_PART = {
    "sessions": "the windows",
    "watchdog": "the watchdog's checks",
    "watchdog-state": "the watchdog's saved state (its checks run as if on a first run)",
    "config": "bosun's settings",
    "logs": "the logs",
}
# What a SessionGate says about a missing session (watchdog.session_gate_kind, mapped by menu_status_live)
EXPECTED = "expected"  # it should be running now
RESTING = "resting"  # outside the awake window: until its next scheduled start
STARTING = "starting"  # the morning run is going, or is about to (the awake window began, it is not yet due)
STOPPED_BY_STOP = "stopped"  # you stopped it today with bosun stop (at: when)
QUIT = "quit"  # a quit record is there (the quit verdict is its own: this is only a race between two reads)
NO_MORNING = "no-morning"  # no morning run today: morning-missing's to say
# The watchdog's launchd job runs every WATCHDOG_INTERVAL_SECONDS (lib/common.sh's, which
# tests/unit/test_menu_status.py holds this to); a last full run older than WATCHDOG_STALE_RUNS intervals is a
# watchdog that is not working.
WATCHDOG_INTERVAL_SECONDS = 300
WATCHDOG_STALE_RUNS = 3
DAYS_AHEAD = 8  # the next scheduled start is within a week; a day more for the same weekday


@dataclass(frozen=True)
class SessionGate:
    """Whether the watchdog expects the session to run now. <at>: see the kinds above."""

    kind: str
    at: datetime | None = None  # local and naive


@dataclass(frozen=True)
class WatchdogJob:
    """The watchdog's own launchd job: <installed> its plist is there (so it is wanted), <loaded> launchd has
    it, when its last run was (local and naive; None when it never ran), when the Mac last woke and when it
    started."""

    installed: bool
    loaded: bool
    last_run: datetime | None = None
    woke_at: datetime | None = None  # the Mac's last wake (local and naive); None when it could not be read
    booted_at: datetime | None = None  # the Mac's boot (local and naive); None when it could not be read


@dataclass(frozen=True)
class LastUpdate:
    """The update's record (update.json), as far as the status tells it: which update, when it started
    (UTC, ISO 8601 to the second) and the step it is at or ended at (`update_state.STEPS`, or a newer
    bosun's)."""

    id: str
    started_at: str
    step: str

    def as_json(self) -> dict[str, str]:
        return {"id": self.id, "started_at": self.started_at, "step": self.step}


@dataclass(frozen=True)
class StatusFacts:
    """What the snapshot is made of. A part that could not be read is None (or absent) and has its
    reason in <errors>, keyed by part (READ_PART)."""

    windows: list[dict[str, Any]] | None  # `bosun sessions --json`
    checks: Mapping[str, Any] | None  # watchdog.status_data
    awake: awake.AwakeWindow | None  # None when the settings did not load
    quit: quit_state.QuitState | None
    gate: SessionGate | None = None  # None when it could not be told (the checks could not be made)
    update_running: bool = False
    update_waiting_for: tuple[str, ...] = ()
    update_last: LastUpdate | None = None  # the record the last update left (it stays after it ends)
    console_running: bool = False
    keep_awake_until: datetime | None = None  # local and naive
    log_errors: tuple[str, ...] = ()  # "<log>: <line>" of the last hour's ERROR lines
    log_dir: str = ""
    errors: Mapping[str, str] = field(default_factory=dict)
    watchdog_job: WatchdogJob | None = None  # None when it could not be told, or there is no job (a checkout)


@dataclass(frozen=True)
class Verdict:
    level: str
    sentence: str
    detail: list[str]
    problems: list[str]
    stopped: dict[str, Any] | None = None  # the `stopped` field, when this is a stop


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
    return f"{plural(len(lines), 'ERROR line')} in the bosun logs in the last hour: {watchdog.listed(lines)}"


def unread_problems(errors: Mapping[str, str]) -> list[tuple[str, str]]:
    return [(WARNING, f"could not read {READ_PART.get(p, p)}: {why}") for p, why in sorted(errors.items())]


def when(moment: datetime, now: datetime) -> str:
    return f"{moment:%H:%M}" if moment.date() == now.date() else f"{moment:%Y-%m-%d %H:%M}"


def watchdog_stale(job: WatchdogJob, now: datetime) -> bool:
    """A loaded job whose last run is older than three of its intervals of the Mac being awake. A Mac that
    slept does not run it, and wakes to a last run that is hours old with the job's next run a few minutes
    away, so the wake counts as a run; a Mac that was off starts its jobs at login, the watchdog's first run
    five minutes after, so the boot counts as one too. A time later than <now> (the clock moved back since) is
    left out, never taken as now: each look would count from its own now and never be stale; with none left
    there is nothing to count from. A job that never ran is a new one."""
    if job.last_run is None:
        return False
    times = [t for t in (job.last_run, job.woke_at, job.booted_at) if t is not None and t <= now]
    if not times:
        return False
    return now - max(times) > timedelta(seconds=WATCHDOG_STALE_RUNS * WATCHDOG_INTERVAL_SECONDS)


def watchdog_problem(facts: StatusFacts, now: datetime) -> str | None:
    """What is wrong with the watchdog itself, which no check of its own can say: nothing checks on bosun, and
    nothing alerts, while its job is booted out or stuck. Only a job that is installed counts (a checkout has
    none); an update's hold is why it skips."""
    job = facts.watchdog_job
    if job is None or not job.installed or facts.update_running:
        return None
    if not job.loaded:
        return (
            "bosun's watchdog job is not loaded, so nothing is checking on bosun or alerting you. "
            "`bosun doctor` says so too; install.sh loads it again"
        )
    if job.last_run is not None and watchdog_stale(job, now):
        return (
            f"bosun's watchdog last ran at {when(job.last_run, now)}, though its job runs every "
            f"{WATCHDOG_INTERVAL_SECONDS // 60} minutes: it is loaded and not working "
            f"(see watchdog.launchd.err in {facts.log_dir or 'the log folder'})"
        )
    return None


def problems_of(facts: StatusFacts, missing: bool, now: datetime) -> list[tuple[str, str]]:
    """Every problem as (level, text), the watchdog's findings first. <missing>: no window is open and
    the watchdog's gate expects the session."""
    checked = findings(facts.checks)
    found = [(level, message) for level, _name, message in checked]
    named = {name for _level, name, _message in checked}
    if missing and SESSION_CHECK not in named:
        text = "bosun's tmux session is not running, though it should be. Start it with: bosun start"
        found.append((ERROR, text))
    if facts.log_errors and LOG_CHECK not in named:
        found.append((WARNING, log_problem(facts.log_errors)))
    unwatched = watchdog_problem(facts, now)
    if unwatched:
        found.append((WARNING, unwatched))
    return found + unread_problems(facts.errors)


# ---- the sentence ------------------------------------------------------------------------------


def with_level(base: str, level: str) -> str:
    suffix = {WARNING: ", with a warning", ERROR: ", with an error"}
    return base + suffix.get(level, "")


def failed_sentence(facts: StatusFacts) -> str:
    """The first line when the morning run is going but a check says it failed (it crashed or hung, and
    still counts as running): never "starting"."""
    failed = any(name == MORNING_FAILED for _level, name, _message in findings(facts.checks))
    return "bosun is not running: the morning run failed" if failed else "bosun is not running"


def not_running_sentence(facts: StatusFacts, level: str, now: datetime) -> str:
    """The first line when no window is open (and it is not a stop)."""
    kind = facts.gate.kind if facts.gate is not None else None
    if kind == STARTING:
        if level == ERROR:
            return failed_sentence(facts)
        return with_level("bosun is starting (the morning run)", level)
    start = next_start(facts.awake, now) if kind == RESTING else None
    if start is None:
        return "bosun is not running"
    return with_level(f"bosun is resting until {start:%H:%M}", level)


def running_sentence(facts: StatusFacts, level: str, count: int | None, now: datetime) -> str:
    """The first line of the menu while bosun is not stopped."""
    if count is None:
        return "bosun's windows could not be read"
    if count > 0:
        return with_level("bosun is running", level)
    return not_running_sentence(facts, level, now)


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


def gate_says(facts: StatusFacts, count: int | None, kind: str) -> bool:
    """Whether no window is open and the watchdog's gate says <kind> about it."""
    return count == 0 and facts.gate is not None and facts.gate.kind == kind


def running_verdict(facts: StatusFacts, now: datetime, waiting: list[str]) -> Verdict:
    count = None if facts.windows is None else open_windows(facts.windows)
    if facts.gate is not None and facts.gate.at is not None and gate_says(facts, count, STOPPED_BY_STOP):
        return stop_verdict(facts.gate.at)
    problems = problems_of(facts, gate_says(facts, count, EXPECTED), now)
    level = max((level for level, _text in problems), key=RANK.__getitem__, default=OK)
    return Verdict(
        level,
        running_sentence(facts, level, count, now),
        state_lines(facts, count or 0, waiting, now),
        [text for _level, text in problems],
    )


def stop_verdict(stopped_at: datetime) -> Verdict:
    """You stopped bosun with `bosun stop` today: stopped, but the morning start is not paused."""
    clock = f"{stopped_at:%H:%M}"
    detail = [f"You stopped bosun at {clock} (bosun stop)", "The morning run starts it again as usual"]
    since = stopped_at.astimezone().isoformat(timespec="seconds")
    return Verdict(
        STOPPED, f"bosun is stopped (bosun stop at {clock})", detail, [], stopped_fields_of(since, False)
    )


def quit_verdict(facts: StatusFacts, now: datetime, waiting: list[str]) -> Verdict:
    """The verdict while a quit record exists: stopped, unless windows are still open (the quit did not
    finish: its `bosun stop` failed or was cut short, while the watchdog skips), which is an error."""
    if facts.quit is None:
        return running_verdict(facts, now, waiting)
    if open_windows(facts.windows):
        return unfinished_verdict(facts.quit, open_windows(facts.windows))
    return stopped_verdict(facts.quit)


def unfinished_verdict(state: quit_state.QuitState, count: int) -> Verdict:
    """A quit record with windows still open: bosun quit did not finish."""
    text = (
        f"bosun quit did not finish: {plural(count, 'window')} still open, and the watchdog is skipping its "
        "runs. Run `bosun quit --stop-now` again to finish, or `bosun start` to start bosun again"
    )
    detail = [f"You quit bosun at {quit_state.clock(state)}, but {plural(count, 'window')} still open"]
    return Verdict(ERROR, "bosun quit did not finish", detail, [text], stopped_fields(state))


def stopped_verdict(state: quit_state.QuitState) -> Verdict:
    detail = [f"You quit bosun at {quit_state.clock(state)}"]
    if state.morning_paused:
        detail.append("The morning start is paused until you start bosun")
    problems = []
    if not state.readable:
        text = "The quit record is unreadable, so the morning is paused too; bosun start clears it"
        detail.append(text)
        problems.append(text)
    return Verdict(STOPPED, "bosun is stopped", detail, problems, stopped_fields(state))


# ---- the object --------------------------------------------------------------------------------


def vouched(verdict: Verdict, errors: Mapping[str, str]) -> Verdict:
    """A stopped verdict is only as good as what was read: with a part that could not be read, status cannot
    vouch for "stopped" (a window may be open, a finding missed), so it is at least a warning, and says which
    part. It keeps its `stopped` field: bosun was stopped, as far as it can tell."""
    if verdict.level != STOPPED or not errors:
        return verdict
    unread = [text for _level, text in unread_problems(errors)]
    return replace(
        verdict,
        level=WARNING,
        sentence=with_level(verdict.sentence, WARNING),
        problems=[*verdict.problems, *unread],
    )


def snapshot(facts: StatusFacts, now: datetime) -> dict[str, Any]:
    """`bosun status --json`: the object the menu bar app reads. <now> is local and naive."""
    waiting = [str(w["name"]) for w in console_sessions.waiting_in(facts.windows or [], now)]
    verdict = vouched(quit_verdict(facts, now, waiting), facts.errors)
    kept = facts.keep_awake_until
    return {
        "contract": CONTRACT,
        "level": verdict.level,
        "sentence": verdict.sentence,
        "detail": verdict.detail,
        "windows": open_windows(facts.windows),
        "running": None if facts.windows is None else open_windows(facts.windows) > 0,
        "waiting": waiting,
        "keep_awake_until": f"{kept:%H:%M}" if kept is not None and kept > now else None,
        "stopped": verdict.stopped,
        "update": {
            "state": "running" if facts.update_running else "none",
            "waiting_for": list(facts.update_waiting_for),
            "last": facts.update_last.as_json() if facts.update_last is not None else None,
        },
        "console": {"running": facts.console_running, "url": None},  # the address carries the token
        "problems": verdict.problems,
        "logs": facts.log_dir,
    }


def stopped_fields_of(since: str, morning_paused: bool) -> dict[str, Any]:
    return {"since": since, "morning_paused": morning_paused}


def stopped_fields(state: quit_state.QuitState) -> dict[str, Any]:
    return stopped_fields_of(state.since.isoformat(timespec="seconds"), state.morning_paused)


def failure(text: str, level: str = ERROR) -> dict[str, Any]:
    """The object when little could be found out: bosun's settings do not load, or the command itself
    went wrong. Every field is there, so a reader needs no special case."""
    return {
        "contract": CONTRACT,
        "level": level,
        "sentence": "bosun cannot tell how it is",
        "detail": [],
        "windows": 0,
        "running": None,  # not known
        "waiting": [],
        "keep_awake_until": None,
        "stopped": None,
        "update": {"state": "none", "waiting_for": [], "last": None},
        "console": {"running": False, "url": None},
        "problems": [text],
        "logs": "",
    }


def unreadable_config(text: str) -> dict[str, Any]:
    """bosun.env cannot be read, so none of bosun runs: <text> says why (lib/common.sh's read_config)."""
    return {**failure(text), "sentence": "bosun cannot read its settings"}
