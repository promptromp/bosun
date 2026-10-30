#!/usr/bin/env python3
"""Watchdog: finds problems that need you and alerts you once per problem.

Usage: watchdog.py [--once | --dry-run | --status] [--only CHECK[,CHECK...]]   (facts JSON on stdin)

Run it as `bosun watchdog`: that collects the live facts (the tmux windows and the Claude in
each, the check-in loop, the morning launchd job) and passes the settings in the environment.
Everything else (logs, the ledger, Claude's session records, the de-dup state) is read here.

Each check in CHECKS looks at the facts and returns findings, each with a de-duplication key. A
run sends every new finding through the sinks (`bosun alert`), combined into one message, and
records its key so it never alerts twice (a check that `rearms` forgets its keys once it finds
nothing again, so the next outage alerts). Alerts go out only inside the awake window (a scheduled
day, BOSUN_START_TIME to BOSUN_AWAKE_UNTIL); findings outside it are held until then.
Findings marked `confirm` must persist across two runs in a row first, so a transient state (the
morning job starting a few seconds after the Mac wakes, a window being recreated) never alerts.
State: $BOSUN_STATE_DIR/watchdog.json. BOSUN_WATCHDOG_NOW overrides the time (tests).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import awake
import claude_state
import event_model
import events
import impact
import impact_overhead
import learn
import ledger
import lineage
import login
import slack_watch


BOSUN = Path(__file__).resolve().parent.parent / "bin" / "bosun"
STAMP = "%Y-%m-%d %H:%M:%S"
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
# A `confirm` finding alerts once it has been seen for CONFIRM_SECONDS by runs at most
# CONFIRM_MAX_GAP_SECONDS apart (launchd runs the watchdog every 300s). A longer gap means the
# Mac slept in between, so the observation starts over: right after a wake, the morning job may
# not have written its log yet.
CONFIRM_SECONDS = 240
CONFIRM_MAX_GAP_SECONDS = 900
# A morning run takes about a minute; network (120s) and login (300s per step) timeouts can
# stretch it. One still unfinished after this long has hung or was killed.
MORNING_MAX_MINUTES = 45
ALERTED_RETENTION_DAYS = 14
MAX_ALERT_ITEMS = 5
MAX_DETAIL_LINES = 3
DETAIL_CHARS = 160
STUCK_STATES = ("delegated",)
# A PR in these states waits on someone else: its reviewers (`awaiting-review`), or, once approved,
# whoever merges it. The review nudges and merge proposals follow it, not the watchdog.
PR_WAIT_STATES = ("awaiting-review", "approved")
REPORT_EVENT = "report"
STATE_FILE = "watchdog.json"
LOCK_FILE = "watchdog.lock"

SEND = "send"
ALREADY = "already alerted"
CONFIRMING = "confirming"
HELD = "held"
DECISION_TEXT = {
    SEND: "alerts now",
    ALREADY: "already alerted",
    CONFIRMING: "alerts if still so on the next run",
    HELD: "held until the awake window",
}
SEVERITY_RANK = {"error": 0, "warn": 1}
SEVERITY_LABEL = {"error": "FAIL", "warn": "WARN"}

LOG_LINE = r"^(?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(?P<command>[^\]]+)\] "
RUN_HEADER = re.compile(
    LOG_LINE + r"=== morning run \(trigger=(?P<trigger>[^,]*), [^)]*dry_run=(?P<dry_run>\d)\) ==="
)
STAMPED = re.compile(LOG_LINE)
ERROR_LINE = re.compile(LOG_LINE + r"(?:ERROR|FAIL):")
RUN_COMPLETE = "] morning run complete"
RUN_PROBLEMS = "] morning run finished with problems"
MORNING_PROBLEM = re.compile(
    r"\] (?:ERROR|FAIL):|finished with problems|could not open the window|skipping the window"
)
# Their own failures would otherwise alert about alerts, every run; learn and slack-watch have their
# own checks.
UNALERTED_COMMANDS = ("alert", "watchdog", "learn", "slack-watch")
# A scheduled learning run that has not started this long after BOSUN_LEARN_AT is missing.
LEARN_GRACE_MINUTES = 60
ERROR_CHARS = 200


class ConfigError(Exception):
    pass


# ---- inputs ------------------------------------------------------------------------------------


def parse_hhmm(value: str, name: str) -> time:
    try:
        return awake.parse_hhmm(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be HH:MM, got {value!r}") from exc


@dataclass(frozen=True)
class Config:
    state_dir: Path
    log_dir: Path
    session: str
    orchestrator: str
    days: str
    start: time
    wake_delay_minutes: float
    awake_until: time
    grace_minutes: float
    waiting_minutes: float
    checkin_minutes: float
    delegated_hours: float
    learn_enabled: bool = False
    learn_at: time = time(18, 30)
    learn_timeout_minutes: float = 30
    events_enabled: bool = False
    events_without_checkins: bool = False
    events_interval_seconds: float = 30
    slack_watch_enabled: bool = False
    slack_watch_minutes: float = 15
    claude_bin: str = "claude"
    long_shell_hours: float = 4
    context_warn_tokens: int = 300_000
    overhead_warn_share: float = 0.5
    orchestrator_down_minutes: float = 15
    orchestrator_restart: bool = True

    @property
    def loop_wanted(self) -> bool:
        """Whether the checkin window runs its loop (lib/common.sh's checkin_loop_wanted): check-ins
        are on, or events run without them."""
        if not self.orchestrator:
            return False
        return self.checkin_minutes > 0 or (self.events_enabled and self.events_without_checkins)

    @property
    def awake(self) -> awake.AwakeWindow:
        return awake.AwakeWindow(self.days, self.start, self.awake_until)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        def text(name: str) -> str:
            value = env.get(f"BOSUN_{name}")
            if value is None:
                raise ConfigError(f"BOSUN_{name} is not set; run this as `bosun watchdog`")
            return value

        def number(name: str) -> float:
            try:
                return float(text(name))
            except ValueError as exc:
                raise ConfigError(f"BOSUN_{name} must be a number, got {text(name)!r}") from exc

        return cls(
            state_dir=Path(text("STATE_DIR")),
            log_dir=Path(text("LOG_DIR")),
            session=text("SESSION_NAME"),
            orchestrator=text("ORCHESTRATOR"),
            days=text("DAYS"),
            start=parse_hhmm(text("START_TIME"), "BOSUN_START_TIME"),
            wake_delay_minutes=number("WAKE_DELAY_MINUTES"),
            awake_until=parse_hhmm(text("AWAKE_UNTIL"), "BOSUN_AWAKE_UNTIL"),
            grace_minutes=number("WATCHDOG_GRACE_MINUTES"),
            waiting_minutes=number("ALERT_WAITING_MINUTES"),
            checkin_minutes=number("CHECKIN_MINUTES"),
            delegated_hours=number("WATCHDOG_DELEGATED_HOURS"),
            learn_enabled=text("LEARN_ENABLED") == "1",
            learn_at=parse_hhmm(text("LEARN_AT"), "BOSUN_LEARN_AT"),
            learn_timeout_minutes=number("LEARN_TIMEOUT_MINUTES"),
            events_enabled=env.get("BOSUN_EVENTS_ENABLED") == "1",
            events_without_checkins=env.get("BOSUN_EVENTS_WITHOUT_CHECKINS") == "1",
            events_interval_seconds=float(env.get("BOSUN_EVENTS_INTERVAL_SECONDS") or 30),
            slack_watch_enabled=env.get("BOSUN_SLACK_WATCH_ENABLED") == "1",
            slack_watch_minutes=float(env.get("BOSUN_SLACK_WATCH_MINUTES") or 15),
            claude_bin=env.get("BOSUN_CLAUDE_BIN") or "claude",
            long_shell_hours=float(env.get("BOSUN_LONG_SHELL_HOURS") or 4),
            context_warn_tokens=int(float(env.get("BOSUN_CONTEXT_WARN_TOKENS") or 300_000)),
            overhead_warn_share=float(env.get("BOSUN_OVERHEAD_WARN_SHARE") or 0.5),
            orchestrator_down_minutes=float(env.get("BOSUN_WATCHDOG_ORCHESTRATOR_DOWN_MINUTES") or 15),
            orchestrator_restart=env.get("BOSUN_WATCHDOG_ORCHESTRATOR_RESTART", "1") == "1",
        )


@dataclass(frozen=True)
class WindowFact:
    name: str
    claude_pid: int | None


@dataclass(frozen=True)
class Facts:
    """What `bosun watchdog` saw of the live system."""

    session_exists: bool = False
    windows: tuple[WindowFact, ...] = ()
    checkin_window: bool = False
    checkin_loop: bool = False
    morning_job_state: str = ""  # launchctl's "state" of the morning job; "" if not loaded
    morning_job_exit: str = ""  # its "last exit code"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Facts:
        job = data.get("morning_job") or {}
        return cls(
            session_exists=bool(data.get("session_exists")),
            windows=tuple(
                WindowFact(str(w["name"]), w["claude_pid"] if isinstance(w.get("claude_pid"), int) else None)
                for w in data.get("windows") or []
            ),
            checkin_window=bool(data.get("checkin_window")),
            checkin_loop=bool(data.get("checkin_loop")),
            morning_job_state=str(job.get("state") or ""),
            morning_job_exit=str(job.get("last_exit_code") or ""),
        )


def current_time(override: str | None) -> datetime:
    """Now, local and naive; BOSUN_WATCHDOG_NOW (epoch seconds or ISO local time) for tests."""
    if not override:
        return datetime.now().replace(microsecond=0)
    try:
        return (
            datetime.fromtimestamp(int(override)) if override.isdigit() else datetime.fromisoformat(override)
        )
    except ValueError as exc:
        raise ConfigError(
            f"BOSUN_WATCHDOG_NOW must be epoch seconds or YYYY-MM-DD HH:MM, got {override!r}"
        ) from exc


def local_time(iso: str) -> datetime:
    """An ISO timestamp (the ledger's, in UTC) as local naive time."""
    moment = datetime.fromisoformat(iso)
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def stamp(moment: datetime) -> str:
    return moment.strftime(STAMP)


# ---- schedule ----------------------------------------------------------------------------------


def scheduled(config: Config, day: date) -> bool:
    return config.awake.scheduled(day)


def awake_bounds(config: Config, day: date) -> tuple[datetime, datetime]:
    """The awake window on <day>; an AWAKE_UNTIL at or before the start time means midnight."""
    return config.awake.bounds(day)


def in_awake_window(config: Config, now: datetime) -> bool:
    return config.awake.contains(now)


def morning_due(config: Config, day: date) -> datetime:
    """When the morning run should have started, plus the grace period."""
    minutes = config.wake_delay_minutes + config.grace_minutes
    return datetime.combine(day, config.start) + timedelta(minutes=minutes)


def awake_seconds(config: Config, since: datetime, until: datetime) -> float:
    """Seconds between <since> and <until> that fall inside awake windows ("working hours")."""
    total = 0.0
    day = since.date() - timedelta(days=1)  # yesterday's window may run past midnight
    while day <= until.date():
        if scheduled(config, day):
            begin, end = awake_bounds(config, day)
            total += max(0.0, (min(end, until) - max(begin, since)).total_seconds())
        day += timedelta(days=1)
    return total


# ---- the morning log ---------------------------------------------------------------------------


@dataclass
class MorningRun:
    started: datetime
    trigger: str
    outcome: str = "running"  # complete, problems or running (no end line yet)
    problems: list[str] = field(default_factory=list)


def detail(line: str) -> str:
    """A log line without its timestamp, shortened for an alert."""
    text = STAMPED.sub(lambda m: f"[{m['command']}] ", line).strip()
    return text if len(text) <= DETAIL_CHARS else text[: DETAIL_CHARS - 3] + "..."


def parse_run(header: re.Match[str], body: list[str]) -> MorningRun:
    run = MorningRun(datetime.strptime(header["at"], STAMP), header["trigger"])
    for line in body:
        if RUN_COMPLETE in line:
            run.outcome = "complete"
        elif RUN_PROBLEMS in line:
            run.outcome = "problems"
        if MORNING_PROBLEM.search(line):
            run.problems.append(detail(line))
    return run


def last_real_run(log: Path) -> MorningRun | None:
    """The last morning run in <log> that was not a dry run, if any."""
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return None
    last: tuple[re.Match[str], list[str]] | None = None
    current: tuple[re.Match[str], list[str]] | None = None
    for line in lines:
        header = RUN_HEADER.match(line)
        if header:
            current = (header, [])
            if header["dry_run"] == "0":
                last = current
        elif current:
            current[1].append(line)
    return parse_run(*last) if last else None


# ---- state -------------------------------------------------------------------------------------


@dataclass
class State:
    alerted: dict[str, str] = field(default_factory=dict)  # key -> when it alerted
    pending: dict[str, dict[str, str]] = field(default_factory=dict)  # key -> first, last seen
    logs: dict[str, dict[str, int]] | None = None  # path -> inode, offset; None: never scanned
    last_run: str | None = None  # the last full run (not --only)
    last_alert: str | None = None
    shells: dict[str, dict[str, int]] = field(default_factory=dict)  # window -> its shell episode
    limits: dict[str, dict[str, Any]] = field(default_factory=dict)  # window -> its usage-limit stop
    limit_probe: dict[str, str] | None = None  # the last probe while a window was stopped: at, outcome
    orchestrator_down: dict[str, str] = field(default_factory=dict)  # since, reason, restart_at

    @classmethod
    def load(cls, state_dir: Path) -> State:
        try:
            data = json.loads((state_dir / STATE_FILE).read_text())
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as exc:
            log(f"unreadable {state_dir / STATE_FILE} ({exc}); starting afresh")
            return cls()
        return cls(
            alerted=dict(data.get("alerted") or {}),
            pending=dict(data.get("pending") or {}),
            logs=data.get("logs"),
            last_run=data.get("last_run"),
            last_alert=data.get("last_alert"),
            shells=dict(data.get("shells") or {}),
            limits=dict(data.get("limits") or {}),
            limit_probe=data.get("limit_probe"),
            orchestrator_down=dict(data.get("orchestrator_down") or {}),
        )

    def save(self, state_dir: Path) -> None:
        path = state_dir / STATE_FILE
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=2, sort_keys=True))
        tmp.replace(path)


@contextlib.contextmanager
def locked_state(state_dir: Path) -> Iterator[State]:
    """The state, read and written under a lock: launchd and the check-in loop may run at once."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / LOCK_FILE).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = State.load(state_dir)
        yield state
        state.save(state_dir)


# ---- checks ------------------------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # someone else's process: alive
    return True


@dataclass(frozen=True)
class Finding:
    key: str  # starts with the check's name; one alert per key, ever
    message: str
    confirm: bool = False
    # A marker file the check-in loop used before the watchdog (alerted-<window>, holding
    # "<pid>:<statusUpdatedAt>"): honoured and kept up to date, so the two never alert twice.
    legacy_marker: Path | None = None
    legacy_value: str = ""


@dataclass
class Result:
    note: str = ""  # what the check saw, for --status
    findings: list[Finding] = field(default_factory=list)
    offsets: dict[str, dict[str, int]] | None = None  # log positions after this scan
    shells: dict[str, dict[str, int]] | None = None  # the windows' shell episodes after this run
    limits: dict[str, dict[str, Any]] | None = None  # the windows a usage limit stops now
    down: dict[str, str] | None = None  # the orchestrator's outage after this run ({}: running)


@dataclass
class Context:
    config: Config
    facts: Facts
    now: datetime
    state: State
    record: Callable[[int], dict[str, Any] | None] = claude_state.record
    alive: Callable[[int], bool] = pid_alive
    auth_status: Callable[[str], claude_state.AuthStatus] = claude_state.auth_status
    morning_run: MorningRun | None = field(init=False)

    def __post_init__(self) -> None:
        self.morning_run = last_real_run(self.morning_log)

    @property
    def today(self) -> date:
        return self.now.date()

    @property
    def morning_log(self) -> Path:
        return self.config.log_dir / f"morning-{self.today:%Y-%m-%d}.log"


def check_morning_missing(ctx: Context) -> Result:
    if not scheduled(ctx.config, ctx.today):
        return Result("not a scheduled day")
    due = morning_due(ctx.config, ctx.today)
    if ctx.now < due:
        return Result(f"not due until {due:%H:%M}")
    if ctx.morning_run:
        return Result(f"ran at {ctx.morning_run.started:%H:%M} ({ctx.morning_run.trigger})")
    expected = datetime.combine(ctx.today, ctx.config.start) + timedelta(
        minutes=ctx.config.wake_delay_minutes
    )
    message = (
        f"No morning run today: it was due at {expected:%H:%M} and {ctx.morning_log.name} has none. "
        "Check that the Mac woke and the launchd job is loaded (bosun doctor), "
        "then run bosun morning."
    )
    return Result(findings=[Finding(f"morning-missing:{ctx.today}", message, confirm=True)])


def unfinished_run(ctx: Context, run: MorningRun) -> Result:
    """A morning run without an end line: still going, or it crashed or hung."""
    if run.trigger == "launchd" and ctx.facts.morning_job_state == "running":
        return Result(f"running since {run.started:%H:%M}")
    crashed = (
        run.trigger == "launchd"
        and ctx.facts.morning_job_state == "not running"
        and ctx.facts.morning_job_exit not in ("", "0")
    )
    overdue = ctx.now - run.started >= timedelta(minutes=MORNING_MAX_MINUTES)
    if not (crashed or overdue):
        return Result(f"running since {run.started:%H:%M}")
    how = f"exited with code {ctx.facts.morning_job_exit}" if crashed else "is still not done"
    message = (
        f"The morning run that started at {run.started:%H:%M} {how} without finishing; see {ctx.morning_log}."
    )
    return Result(findings=[Finding(f"morning-failed:{ctx.today}", message, confirm=True)])


def check_morning_failed(ctx: Context) -> Result:
    run = ctx.morning_run
    if run is None:
        return Result("no morning run today")
    if run.outcome == "running":
        return unfinished_run(ctx, run)
    if not run.problems:
        return Result(f"the {run.started:%H:%M} run completed")
    shown = "; ".join(run.problems[:MAX_DETAIL_LINES])
    more = f" (+{len(run.problems) - MAX_DETAIL_LINES} more)" if len(run.problems) > MAX_DETAIL_LINES else ""
    message = (
        f"The morning run at {run.started:%H:%M} reported problems: {shown}{more}. Log: {ctx.morning_log}"
    )
    return Result(findings=[Finding(f"morning-failed:{ctx.today}", message)])


def waiting_finding(ctx: Context, window: WindowFact, record: dict[str, Any]) -> Finding | None:
    updated = record.get("statusUpdatedAt")
    if record.get("status") != "waiting" or not isinstance(updated, (int, float)):
        return None
    waited = max(0.0, (ctx.now - datetime.fromtimestamp(updated / 1000)).total_seconds())
    if waited < ctx.config.waiting_minutes * SECONDS_PER_MINUTE:
        return None
    orchestrator = window.name == ctx.config.orchestrator
    paused = "; check-ins are paused until you answer it" if orchestrator else ""
    message = (
        f"The {window.name} session has waited {int(waited // SECONDS_PER_MINUTE)} min at a permission "
        f"prompt or question{paused} (bosun attach, or Remote Control)."
    )
    return Finding(
        f"waiting:{window.name}:{window.claude_pid}:{updated}",
        message,
        legacy_marker=ctx.config.state_dir / f"alerted-{window.name}" if orchestrator else None,
        legacy_value=f"{window.claude_pid}:{updated}",
    )


def check_waiting(ctx: Context) -> Result:
    if ctx.config.waiting_minutes <= 0:
        return Result("off (BOSUN_ALERT_WAITING_MINUTES=0)")
    if not ctx.facts.session_exists:
        return Result("no tmux session")
    findings = []
    for window in ctx.facts.windows:
        record = ctx.record(window.claude_pid) if window.claude_pid else None
        finding = waiting_finding(ctx, window, record) if record else None
        if finding:
            findings.append(finding)
    minutes = int(ctx.config.waiting_minutes)
    return Result(f"no Claude window waiting at a prompt for {minutes}+ min", findings)


def scanned_logs(log_dir: Path) -> list[Path]:
    """The logs to watch for ERROR lines: all but the morning logs (the morning checks read those),
    launchd.out (a copy of them) and the watchdog's own."""
    candidates = [*log_dir.glob("*.log"), *log_dir.glob("*.err")]
    return sorted(
        p
        for p in candidates
        if p.is_file() and not p.name.startswith("morning-") and "watchdog" not in p.name
    )


def read_new_lines(path: Path, mark: dict[str, int] | None) -> tuple[list[str], dict[str, int]]:
    """Complete lines added to <path> since <mark>, and the new mark. A different inode (the file
    was replaced) or a shorter file (truncated) is read from the start."""
    info = path.stat()
    same_file = (
        mark is not None and mark.get("inode") == info.st_ino and mark.get("offset", 0) <= info.st_size
    )
    offset = mark["offset"] if mark is not None and same_file else 0
    with path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read()
    complete = data.rfind(b"\n") + 1
    lines = data[:complete].decode(errors="replace").splitlines()
    return lines, {"inode": info.st_ino, "offset": offset + complete}


def is_alerting_error(line: str) -> bool:
    match = ERROR_LINE.match(line)
    return match is not None and match["command"] not in UNALERTED_COMMANDS


def check_log_errors(ctx: Context) -> Result:
    files = scanned_logs(ctx.config.log_dir)
    if ctx.state.logs is None:
        offsets = {str(p): {"inode": p.stat().st_ino, "offset": p.stat().st_size} for p in files}
        return Result(
            f"first scan: {len(files)} log file(s) baselined; only later lines alert", offsets=offsets
        )
    offsets, found = {}, []
    for path in files:
        lines, offsets[str(path)] = read_new_lines(path, ctx.state.logs.get(str(path)))
        found += [f"{path.name}: {detail(line)}" for line in lines if is_alerting_error(line)]
    note = f"no new ERROR lines in {len(files)} log file(s)"
    if not found:
        return Result(note, offsets=offsets)
    shown = "; ".join(found[:MAX_DETAIL_LINES])
    more = f" (+{len(found) - MAX_DETAIL_LINES} more)" if len(found) > MAX_DETAIL_LINES else ""
    digest = hashlib.sha256("\n".join(found).encode()).hexdigest()[:16]
    message = f"{len(found)} new ERROR line(s) in the bosun logs: {shown}{more}"
    return Result(note, [Finding(f"log-errors:{digest}", message)], offsets)


def stopped_today(ctx: Context) -> datetime | None:
    """When you stopped the session today with `bosun stop`, if you did."""
    try:
        stopped = datetime.strptime(
            (ctx.config.state_dir / f"stopped-{ctx.config.session}").read_text().strip(), STAMP
        )
    except (OSError, ValueError):
        return None
    return stopped if stopped.date() == ctx.today else None


def session_gate(ctx: Context) -> str | None:
    """Why the session should not be expected to run now, or None if it should."""
    if not in_awake_window(ctx.config, ctx.now):
        return "outside the awake window"
    due = morning_due(ctx.config, ctx.today)
    if ctx.now < due:
        return f"not expected before {due:%H:%M}"
    if ctx.morning_run is None:
        return "no morning run today (see morning-missing)"
    if ctx.morning_run.outcome == "running":
        return "the morning run is still going"
    stopped = stopped_today(ctx)
    if stopped:
        return f"you stopped it at {stopped:%H:%M} (bosun stop)"
    return None


def check_session(ctx: Context) -> Result:
    reason = session_gate(ctx)
    if reason:
        return Result(reason)
    if ctx.facts.session_exists:
        return Result(f"running, {len(ctx.facts.windows)} window(s)")
    message = (
        f"The tmux session '{ctx.config.session}' is not running, though the morning run at "
        f"{ctx.morning_run.started:%H:%M} should have started it. Start it with: bosun start"
        if ctx.morning_run
        else f"The tmux session '{ctx.config.session}' is not running."
    )
    return Result(findings=[Finding(f"session-missing:{ctx.today}", message, confirm=True)])


def check_checkin(ctx: Context) -> Result:
    if not ctx.config.loop_wanted:
        return Result("check-ins are off (and events do not run without them)")
    reason = session_gate(ctx) or (
        "" if ctx.facts.session_exists else "no tmux session (see session-missing)"
    )
    if reason:
        return Result(reason)
    if ctx.facts.checkin_loop:
        return Result("the check-in loop is running")
    if ctx.facts.checkin_window:
        problem, fix = (
            "is not running the check-in loop",
            "close that window, then run bosun refresh checkin",
        )
    else:
        problem, fix = "is missing", "run bosun refresh checkin"
    gets = "check-ins" if ctx.config.checkin_minutes > 0 else "events"
    message = f"The checkin window {problem}, so the orchestrator gets no {gets}: {fix}."
    return Result(findings=[Finding(f"checkin-missing:{ctx.today}", message, confirm=True)])


# The orchestrator's Claude not running inside the awake window: nothing reads the events, the
# check-ins or the repo sessions' reports meanwhile, and no other check notices. The check keeps
# the outage in state (since, reason) and alerts once it has lasted
# BOSUN_WATCHDOG_ORCHESTRATOR_DOWN_MINUTES of awake time. With BOSUN_WATCHDOG_ORCHESTRATOR_RESTART=1,
# a full run then relaunches it once per outage through `bosun refresh --unless-active`, which
# starts Claude only in a pane that runs nothing else and never while you are typing.
ORCHESTRATOR_DOWN = "orchestrator-down"


def orchestrator_down_reason(ctx: Context) -> str | None:
    """Why the orchestrator's Claude is not running, or None if it is."""
    window = next((w for w in ctx.facts.windows if w.name == ctx.config.orchestrator), None)
    if window is None:
        return f"its window '{ctx.config.orchestrator}' is missing"
    if window.claude_pid is None:
        return f"no Claude runs in its window '{ctx.config.orchestrator}'"
    return None


def orchestrator_down_finding(ctx: Context, down: dict[str, str]) -> Finding | None:
    since = datetime.fromisoformat(down["since"])
    minutes = awake_seconds(ctx.config, since, ctx.now) / 60
    if minutes < ctx.config.orchestrator_down_minutes:
        return None
    action = (
        f"bosun is relaunching it once (bosun refresh --unless-active {ctx.config.orchestrator})"
        if ctx.config.orchestrator_restart
        else f"start it with: bosun refresh {ctx.config.orchestrator}"
    )
    message = (
        f"The orchestrator has been down for {minutes:.0f} min (since {since:%a %H:%M}): {down['reason']}. "
        f"Events, check-ins and reports wait meanwhile; {action}."
    )
    return Finding(f"{ORCHESTRATOR_DOWN}:{down['since']}", message)


def check_orchestrator_down(ctx: Context) -> Result:
    if not ctx.config.orchestrator or ctx.config.orchestrator_down_minutes <= 0:
        return Result("off (no orchestrator, or BOSUN_WATCHDOG_ORCHESTRATOR_DOWN_MINUTES=0)", down={})
    gate = session_gate(ctx) or ("" if ctx.facts.session_exists else "no tmux session (see session-missing)")
    if gate:
        return Result(gate, down={})
    reason = orchestrator_down_reason(ctx)
    if reason is None:
        return Result("the orchestrator's Claude is running", down={})
    down = dict(ctx.state.orchestrator_down) or {"since": stamp(ctx.now)}
    down["reason"] = reason
    finding = orchestrator_down_finding(ctx, down)
    return Result(f"down since {down['since']}: {reason}", [finding] if finding else [], down=down)


def restart_due(config: Config, down: dict[str, str], now: datetime) -> bool:
    """Whether a full run should relaunch the orchestrator now: down long enough, not tried yet."""
    if not config.orchestrator_restart or not down or down.get("restart_at"):
        return False
    since = datetime.fromisoformat(down["since"])
    return awake_seconds(config, since, now) / 60 >= config.orchestrator_down_minutes


def entered_state_at(item: dict[str, Any]) -> datetime:
    """When <item> moved into its current state (notes and field updates do not count)."""
    for entry in reversed(item.get("history") or []):
        if entry.get("event") == "transition" and entry.get("to") == item["state"]:
            return local_time(entry["at"])
    return local_time(item["created_at"])


def last_event_at(item: dict[str, Any], events: tuple[str, ...]) -> datetime | None:
    """When <item>'s history last recorded one of <events>, if it did."""
    for entry in reversed(item.get("history") or []):
        if entry.get("event") in events:
            return local_time(entry["at"])
    return None


def last_report_at(item: dict[str, Any]) -> datetime | None:
    """When the repo session holding <item> last reported on it, if it did."""
    return last_event_at(item, (REPORT_EVENT,))


def last_moved_at(item: dict[str, Any]) -> datetime:
    """When <item> was last moved on: it entered its state, or work was handed to its window
    (`delegate`, `delegate --update`, a move to another window)."""
    entered, handed = entered_state_at(item), last_event_at(item, ledger.HANDOVER_EVENTS)
    return max(entered, handed) if handed else entered


def stuck_finding(ctx: Context, item: dict[str, Any]) -> Finding | None:
    """An item in a hand-over state with no progress for the threshold: counted from the latest
    hand-over or the session's latest report, whichever is later, so a session that keeps reporting
    is not stuck, nor is one just handed more work, and one that reported long ago with nothing
    since (nobody moved the item on) is."""
    moved, reported = last_moved_at(item), last_report_at(item)
    since = max(moved, reported) if reported else moved
    hours = awake_seconds(ctx.config, since, ctx.now) / SECONDS_PER_HOUR
    if hours < ctx.config.delegated_hours:
        return None
    if reported and reported >= moved:
        what = f"reported at {reported:%a %H:%M} and nothing has moved it on since"
        fix = "act on the report, or move the item on with bosun ledger."
    else:
        no_report = "with no report since its last hand-over" if reported else "with no report"
        what = f"been {item['state']} for {hours:.0f} awake hours (since {since:%a %H:%M}) {no_report}"
        fix = "check the repo session that has it, or move the item on with bosun ledger."
    return Finding(f"ledger-stuck:{item['id']}:{since:%Y-%m-%dT%H:%M:%S}", f"{item['id']} has {what}: {fix}")


def open_prs_produced(item: dict[str, Any], items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The open items <item> produces (`ledger link <task> produces <mypr>`)."""
    ids = [link.get("id") for link in item.get("links") or [] if link.get("rel") == lineage.PRODUCES]
    return [items[i] for i in ids if i in items and ledger.is_open(items[i])]


def waits_on_review(item: dict[str, Any], items: dict[str, dict[str, Any]]) -> bool:
    """The session reported, and every open PR the item produced waits on someone else
    (PR_WAIT_STATES: its reviewers, or the merge once approved)."""
    reported = last_report_at(item)
    if not reported or reported < entered_state_at(item):
        return False
    still_open = open_prs_produced(item, items)
    return bool(still_open) and all(pr.get("state") in PR_WAIT_STATES for pr in still_open)


def check_ledger_stuck(ctx: Context) -> Result:
    if ctx.config.delegated_hours <= 0:
        return Result("off (BOSUN_WATCHDOG_DELEGATED_HOURS=0)")
    ledger.configure(ctx.config.state_dir)
    try:
        by_id = ledger.load()["items"]
    except ledger.LedgerError as exc:
        return Result(f"cannot read the ledger: {exc}")
    items = list(by_id.values())
    # An item that waits on someone else is followed by the nudges instead: open outreach, or a
    # reported task whose open PRs all wait on their reviewers or their merge.
    waiting = set(ledger.outreach_by_parent(items)) | {i["id"] for i in items if waits_on_review(i, by_id)}
    findings = [
        f
        for i in items
        if i.get("state") in STUCK_STATES and i["id"] not in waiting and (f := stuck_finding(ctx, i))
    ]
    hours = f"{ctx.config.delegated_hours:g}"
    return Result(f"no item {'/'.join(STUCK_STATES)} for {hours}+ awake hours", findings)


def learn_running(ctx: Context, running: dict[str, Any]) -> Result:
    """A run marked as running: still going, gone without an outcome, or going for too long."""
    started = local_time(str(running["started"]))
    if not ctx.alive(int(running.get("pid") or 0)):
        message = (
            f"The learning run that started at {started:%a %H:%M} ended without recording an outcome "
            "(crashed or killed); see the learn log in the bosun logs."
        )
        return Result(findings=[Finding(f"learn:crashed:{started:%Y-%m-%dT%H:%M}", message)])
    minutes = (ctx.now - started).total_seconds() / SECONDS_PER_MINUTE
    if minutes < ctx.config.learn_timeout_minutes:
        return Result(f"running since {started:%H:%M}")
    message = (
        f"The learning run that started at {started:%a %H:%M} is still going after {minutes:.0f} min "
        f"(pid {running.get('pid')}): it may hang; kill it, then bosun learn --status."
    )
    return Result(findings=[Finding(f"learn:stalled:{started:%Y-%m-%dT%H:%M}", message)])


def learn_missing(ctx: Context, runs: list[dict[str, Any]]) -> Result:
    """No run today, though the agent should have started one by BOSUN_LEARN_AT + grace."""
    due = datetime.combine(ctx.today, ctx.config.learn_at)
    if not scheduled(ctx.config, ctx.today) or ctx.now < due + timedelta(minutes=LEARN_GRACE_MINUTES):
        return Result(f"next run due at {ctx.config.learn_at:%H:%M} on a scheduled day")
    if any(local_time(str(r["started"])) >= due for r in runs):
        return Result(f"ran today after {due:%H:%M}")
    message = (
        f"No learning run today: it was due at {due:%H:%M}. Check its launchd agent (bosun doctor), "
        "or run bosun learn."
    )
    return Result(findings=[Finding(f"learn:missing:{ctx.today}", message, confirm=True)])


def check_learn(ctx: Context) -> Result:
    if not ctx.config.learn_enabled:
        return Result("off (BOSUN_LEARN_ENABLED=0)")
    try:
        state = learn.State.load(ctx.config.state_dir / learn.STATE_SUBDIR)
    except learn.LearnError as exc:
        return Result(findings=[Finding(f"learn:unreadable:{ctx.today}", str(exc))])
    if state.running:
        return learn_running(ctx, state.running)
    last = state.runs[-1] if state.runs else None
    if last and last.get("outcome") == "failed":
        started = local_time(str(last["started"]))
        error = str(last.get("error", ""))[:ERROR_CHARS]
        message = f"The learning run at {started:%a %H:%M} failed: {error}"
        return Result(findings=[Finding(f"learn:failed:{started:%Y-%m-%dT%H:%M}", message)])
    return learn_missing(ctx, state.runs)


# The dispatcher is stalled when it has not run for this many of its intervals (at least
# EVENTS_STALL_MIN_MINUTES) while the check-in loop that runs it is up.
EVENTS_STALL_INTERVALS = 10
EVENTS_STALL_MIN_MINUTES = 10


def events_stalled(ctx: Context, state: events.State) -> Result:
    limit = max(
        EVENTS_STALL_MIN_MINUTES * SECONDS_PER_MINUTE,
        EVENTS_STALL_INTERVALS * ctx.config.events_interval_seconds,
    )
    last = local_time(state.last_tick) if state.last_tick else None
    if last and (ctx.now - last).total_seconds() < limit:
        return Result(f"the dispatcher ran at {last:%H:%M}")
    since = f"since {last:%a %H:%M}" if last else "ever"
    message = (
        f"The check-in loop has not dispatched events to the orchestrator {since}: it may run code from "
        "before BOSUN_EVENTS_ENABLED=1. Close the checkin window, then bosun refresh checkin."
    )
    return Result(findings=[Finding(f"events:stalled:{last or ctx.today}", message, confirm=True)])


def check_events(ctx: Context) -> Result:
    if not ctx.config.events_enabled:
        return Result("off (BOSUN_EVENTS_ENABLED=0)")
    try:
        state = events.State.load(events.events_dir(ctx.config.state_dir))
    except event_model.EventsError as exc:
        return Result(findings=[Finding(f"events:unreadable:{ctx.today}", str(exc))])
    if state.last_error:
        failed_at = local_time(state.last_error["at"])
        error = state.last_error["error"]
        message = f"Events could not be delivered to the orchestrator at {failed_at:%H:%M}: {error}"
        # Once a day: a failing transport fails on every dispatcher run until it is fixed.
        return Result(findings=[Finding(f"events:failed:{ctx.today}", message)])
    if not (ctx.facts.session_exists and ctx.facts.checkin_loop):
        return Result("the check-in loop is not running (see checkin-missing)")
    return events_stalled(ctx, state)


# The Slack watcher has stalled when no run started for this many of its intervals (at least
# SLACK_WATCH_STALL_MIN_MINUTES) inside the awake window, while the check-in loop that runs it is up.
SLACK_WATCH_STALL_INTERVALS = 2
SLACK_WATCH_STALL_MIN_MINUTES = 10


def slack_watch_problems(ctx: Context, state: slack_watch.State) -> list[Finding]:
    """Failed runs in a row, and a daily budget spent today."""
    found = []
    if state.failures >= slack_watch.MAX_FAILURES and state.last_error:
        error = state.last_error["error"][:ERROR_CHARS]
        message = (
            f"The Slack watcher failed {state.failures} times in a row (last: {error}); see "
            "bosun slack-watch --status. The check-ins still scan Slack."
        )
        found.append(Finding(f"slack-watch:failing:{state.failing_since}", message))
    if state.budget_spent_on == ctx.today.isoformat():
        message = (
            "The Slack watcher spent its daily budget (BOSUN_SLACK_WATCH_MAX_USD_PER_DAY) and stops "
            "until tomorrow; the check-ins still scan Slack."
        )
        found.append(Finding(f"slack-watch:budget:{ctx.today}", message))
    return found


def slack_watch_stalled(ctx: Context, state: slack_watch.State) -> Result:
    if not (ctx.facts.session_exists and ctx.facts.checkin_loop):
        return Result("the check-in loop is not running (see checkin-missing)")
    limit = timedelta(
        minutes=max(
            SLACK_WATCH_STALL_MIN_MINUTES, SLACK_WATCH_STALL_INTERVALS * ctx.config.slack_watch_minutes
        )
    )
    window = ctx.config.awake.current(ctx.now)
    if window is None or ctx.now - window[0] < limit:
        return Result("runs inside the awake window")
    last = local_time(state.last_started) if state.last_started else None
    if last and ctx.now - last < limit:
        return Result(f"the last run started at {last:%H:%M}")
    since = f"since {last:%a %H:%M}" if last else "yet"
    message = (
        f"The Slack watcher has not run {since}: the check-in loop may run code from before it was "
        "enabled. Close the checkin window, then bosun refresh checkin; bosun slack-watch --status."
    )
    return Result(findings=[Finding(f"slack-watch:stalled:{last or ctx.today}", message, confirm=True)])


def check_slack_watch(ctx: Context) -> Result:
    if not (ctx.config.slack_watch_enabled and ctx.config.events_enabled):
        return Result("off (BOSUN_SLACK_WATCH_ENABLED=0, or events off)")
    try:
        state = slack_watch.State.load(ctx.config.state_dir / slack_watch.SUBDIR)
    except slack_watch.WatchError as exc:
        return Result(findings=[Finding(f"slack-watch:unreadable:{ctx.today}", str(exc))])
    findings = slack_watch_problems(ctx, state)
    return Result(findings=findings) if findings else slack_watch_stalled(ctx, state)


# An expired or revoked Claude Code login: every prompt gets an error reply and nothing else.
LOGIN_CHECK = "login-expired"


def window_logins(ctx: Context) -> list[str]:
    """The Claude windows whose latest replies are all login errors, with since when."""
    found = []
    for window in ctx.facts.windows if ctx.facts.session_exists else ():
        entry = ctx.record(window.claude_pid) if window.claude_pid else None
        transcript = claude_state.transcript_of(entry) if entry else None
        verdict = login.window_verdict(transcript, None) if transcript else None
        if verdict and verdict.state == login.NEEDS_LOGIN:
            found.append(f"{window.name} (since {login.shown(verdict.since)})")
    return found


def check_login(ctx: Context) -> Result:
    """The login itself (`claude auth status`, the last login check) and every Claude window's
    latest replies. One alert per outage: the key stays the same until everything works again."""
    overall = login.global_verdict(
        ctx.auth_status(ctx.config.claude_bin), login.State.load(ctx.config.state_dir).last_probe
    )
    windows = window_logins(ctx)
    if overall.state != login.NEEDS_LOGIN and not windows:
        return Result(f"Claude Code {overall.detail}; no window answers only with login errors")
    problems = [overall.detail] if overall.state == login.NEEDS_LOGIN else []
    if windows:
        problems.append(f"every prompt in {', '.join(windows)} gets a login error")
    message = (
        f"Claude Code needs a login: {'; '.join(problems)}. Run /login in any Claude window (bosun "
        "attach) or claude auth login; check-ins pause until the orchestrator works again."
    )
    return Result(findings=[Finding(f"{LOGIN_CHECK}:outage", message)])


# A background command (a Bash tool call run in the background) keeps a Claude in "shell" once its
# turn is over. An episode starts when a window's Claude is first seen in shell (at its
# statusUpdatedAt: when that turn ended) and lasts through "busy" (a turn while the command still
# runs: a check-in, an event, the command's own completion notice) until any other status, or
# another Claude in the window. It is measured in awake hours, like ledger-stuck.
LONG_SHELL = "long-shell"
SHELL = "shell"
EPISODE_STATUSES = (SHELL, "busy")


def shell_episode(prior: dict[str, int] | None, pid: int, record: dict[str, Any]) -> dict[str, int] | None:
    """The window's shell episode after seeing <record> of its Claude <pid>, or None."""
    status, updated = record.get("status"), record.get("statusUpdatedAt")
    if prior and prior.get("pid") == pid and status in EPISODE_STATUSES:
        return prior
    if status == SHELL and isinstance(updated, (int, float)):
        return {"pid": pid, "since": int(updated)}
    return None


def long_shell_finding(ctx: Context, window: str, episode: dict[str, int]) -> Finding | None:
    since = datetime.fromtimestamp(episode["since"] / 1000)
    hours = awake_seconds(ctx.config, since, ctx.now) / SECONDS_PER_HOUR
    if hours < ctx.config.long_shell_hours:
        return None
    message = (
        f"A background command has kept {window} in shell for {hours:.0f}h (awake hours, since "
        f"{since:%a %H:%M}): check it is still wanted. Meanwhile refresh does not restart that Claude."
    )
    return Finding(f"{LONG_SHELL}:{window}:{episode['pid']}:{episode['since']}", message)


def check_long_shell(ctx: Context) -> Result:
    if ctx.config.long_shell_hours <= 0:
        return Result("off (BOSUN_LONG_SHELL_HOURS=0)", shells={})
    if not ctx.facts.session_exists:
        return Result("no tmux session", shells={})
    shells, findings = {}, []
    for window in ctx.facts.windows:
        record = ctx.record(window.claude_pid) if window.claude_pid else None
        if record is None or window.claude_pid is None:
            continue
        episode = shell_episode(ctx.state.shells.get(window.name), window.claude_pid, record)
        if episode is None:
            continue
        shells[window.name] = episode
        finding = long_shell_finding(ctx, window.name, episode) if record.get("status") == SHELL else None
        if finding:
            findings.append(finding)
    hours = f"{ctx.config.long_shell_hours:g}"
    return Result(f"no Claude window kept in shell for {hours}+ awake hours", findings, shells=shells)


# A Claude that hits a usage limit (an individual spend limit, or a plan's usage limit) stops:
# every reply is that error until the limit is raised or resets, and nothing else tells you. The
# check names the windows whose latest reply is that error, or one of whose subagents stopped on it
# in the last SUBAGENT_HOURS, with the ledger items each holds. The watchdog remembers them
# (state.limits); while one is stopped, a full run makes the login check request at most every
# LIMIT_PROBE_MINUTES to see whether the limit is lifted, and once it is (or the window's latest
# reply is a normal one again) it sends that window RESUME_PROMPT once. A restarted Claude gets the
# drain prompt instead.
SPEND_LIMIT = "spend-limit"
SUBAGENT_HOURS = 12
LIMIT_PROBE_MINUTES = 30
MAX_HELD_ITEMS = 4
RESUME_PROMPT = (
    "Bosun: the usage limit (spend limit) that stopped work in this session is lifted. If work "
    "here was cut off by it, yours or a subagent's, resume it and finish. Otherwise reply with a "
    "one-line status and stay idle for my next command."
)


def naive(moment: datetime | None) -> datetime | None:
    return moment.astimezone().replace(tzinfo=None) if moment else None


def limited(error: claude_state.ApiError | None) -> tuple[datetime, datetime] | None:
    """When a usage-limit error row began and when its latest error came, local and naive; None
    for anything else."""
    if error is None or error.category != claude_state.USAGE_LIMIT:
        return None
    since, latest = naive(error.since), naive(error.at)
    return (since, latest) if since and latest else None


def stopped_subagents(transcript: Path, now: datetime) -> list[tuple[datetime, datetime]]:
    """When each of the session's recently active subagents stopped on a usage limit."""
    cutoff = (now - timedelta(hours=SUBAGENT_HOURS)).timestamp()
    found = []
    for path in claude_state.subagent_transcripts(transcript):
        with contextlib.suppress(OSError):
            if path.stat().st_mtime >= cutoff and (row := limited(claude_state.last_api_error(path, True))):
                found.append(row)
    return found


def window_stop(ctx: Context, window: WindowFact) -> dict[str, Any] | None:
    """The usage-limit stop of the Claude in <window>: pid, since, its latest error (at) and how
    many subagents."""
    entry = ctx.record(window.claude_pid) if window.claude_pid else None
    transcript = claude_state.transcript_of(entry) if entry else None
    if transcript is None:
        return None
    main = limited(claude_state.last_api_error(transcript))
    rows = ([main] if main else []) + stopped_subagents(transcript, ctx.now)
    if not rows:
        return None
    return {
        "pid": window.claude_pid,
        "since": stamp(min(since for since, _ in rows)),
        "at": stamp(max(latest for _, latest in rows)),
        "main": main is not None,
        "subagents": len(rows) - (main is not None),
    }


def held_items(state_dir: Path) -> dict[str, list[str]]:
    """The open ledger items each window holds (its current hand-over)."""
    ledger.configure(state_dir)
    try:
        items = ledger.load()["items"].values()
    except ledger.LedgerError:
        return {}
    held: dict[str, list[str]] = {}
    for item in items:
        window = (item.get("delegation") or {}).get("window")
        if window and ledger.is_open(item):
            held.setdefault(window, []).append(item["id"])
    return held


def stop_finding(window: str, stop: dict[str, Any], items: list[str]) -> Finding:
    since = datetime.strptime(stop["since"], STAMP)
    who = "it" if stop["main"] else "its subagents"
    if stop["main"] and stop["subagents"]:
        who = f"it and {stop['subagents']} subagent(s)"
    elif not stop["main"]:
        who = f"{stop['subagents']} of its subagent(s)"
    more = f" and {len(items) - MAX_HELD_ITEMS} more" if len(items) > MAX_HELD_ITEMS else ""
    holds = f"; it holds {', '.join(items[:MAX_HELD_ITEMS])}{more}" if items else ""
    message = (
        f"{window}: {who} stopped on a usage limit (spend limit) at {since:%a %H:%M}{holds}. Raise "
        "the limit (/usage-credits in a Claude window asks your admin) or wait for it to reset; the "
        "watchdog then asks the window to resume."
    )
    return Finding(f"{SPEND_LIMIT}:{window}:{stop['pid']}:{since:%Y-%m-%dT%H:%M}", message)


def probe_note(probe: dict[str, str] | None) -> str:
    if not probe:
        return "no probe yet"
    lifted = probe.get("outcome") == claude_state.PROBE_OK
    return f"last probe {probe.get('at')}: {'the limit is lifted' if lifted else probe.get('outcome')}"


def check_spend_limit(ctx: Context) -> Result:
    if not ctx.facts.session_exists:
        return Result("no tmux session", limits={})
    stops = {w.name: stop for w in ctx.facts.windows if (stop := window_stop(ctx, w))}
    if not stops:
        return Result("no Claude window or subagent stopped on a usage limit", limits={})
    held = held_items(ctx.config.state_dir)
    findings = [stop_finding(window, stop, held.get(window, [])) for window, stop in stops.items()]
    return Result(probe_note(ctx.state.limit_probe), findings, limits=stops)


def merge_limits(
    prior: dict[str, dict[str, Any]], now: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """The stops to remember: those seen now (keeping when the same Claude was last asked to
    resume), and earlier ones, cleared since, that may still be owed a resume."""
    merged = {window: {**stop, "stopped": False} for window, stop in prior.items()}
    for window, stop in now.items():
        earlier = prior.get(window) or {}
        kept = earlier.get("resumed_at") if earlier.get("pid") == stop["pid"] else None
        merged[window] = {**stop, "stopped": True, **({"resumed_at": kept} if kept else {})}
    return merged


# What bosun itself costs (docs/design/impact.md, "Overhead and guardrails"). Every turn re-reads
# the session's whole conversation, so a window whose conversation stays long makes each of its
# check-ins, notifications and resumes expensive: `context-size` names it once per conversation.
# `overhead-share` reads the Impact snapshot (`bosun impact collect`) and warns once a week when
# the turns bosun started itself cost more than the configured share of the last 7 days.
CONTEXT_SIZE = "context-size"
CONTEXT_HOURS = 24
OVERHEAD_SHARE = "overhead-share"
SNAPSHOT_MAX_DAYS = 3  # an older snapshot says nothing about this week


def naive_local(moment: datetime) -> datetime:
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def context_finding(ctx: Context, window: WindowFact) -> Finding | None:
    entry = ctx.record(window.claude_pid) if window.claude_pid else None
    transcript = claude_state.transcript_of(entry) if entry else None
    run = claude_state.context_run(transcript, ctx.config.context_warn_tokens) if transcript else None
    if run is None or ctx.now - naive_local(run.since) < timedelta(hours=CONTEXT_HOURS):
        return None
    hours = (ctx.now - naive_local(run.since)).total_seconds() / SECONDS_PER_HOUR
    session = str((entry or {}).get("sessionId") or window.claude_pid)
    return Finding(
        f"{CONTEXT_SIZE}:{window.name}:{session}",
        f"the {window.name} window's conversation has re-read {run.tokens // 1000}k tokens a turn for "
        f"{hours:.0f} h (over {ctx.config.context_warn_tokens // 1000}k): every check-in, notification "
        "and resume there re-reads it all. Start it on a fresh conversation when it is idle and holds "
        "no unfinished work.",
    )


def check_context_size(ctx: Context) -> Result:
    if ctx.config.context_warn_tokens <= 0:
        return Result("off (BOSUN_CONTEXT_WARN_TOKENS=0)")
    if not ctx.facts.session_exists:
        return Result("no tmux session")
    findings = [f for w in ctx.facts.windows if (f := context_finding(ctx, w))]
    limit = ctx.config.context_warn_tokens // 1000
    return Result(f"no window has re-read over {limit}k tokens a turn for {CONTEXT_HOURS}+ h", findings)


def check_overhead_share(ctx: Context) -> Result:
    if ctx.config.overhead_warn_share <= 0:
        return Result("off (BOSUN_OVERHEAD_WARN_SHARE=0)")
    try:
        snapshot = json.loads((ctx.config.state_dir / "impact" / "snapshot.json").read_text())
    except (OSError, json.JSONDecodeError):
        return Result("no Impact snapshot (bosun impact collect)")
    collected = impact.parse_time(snapshot.get("collected_at"))
    now = ctx.now.astimezone()
    if collected is None or now - collected > timedelta(days=SNAPSHOT_MAX_DAYS):
        return Result(f"the Impact snapshot is over {SNAPSHOT_MAX_DAYS} days old")
    model = impact_overhead.from_snapshot(snapshot, now, now.tzinfo or UTC)
    share = (model.get("last") or {}).get("share") if model.get("exists") else None
    limit = ctx.config.overhead_warn_share
    if share is None:
        return Result("no turn data in the Impact snapshot")
    note = f"overhead {round(share * 100)}% of the last 7 days' cost (warns over {round(limit * 100)}%)"
    if share <= limit:
        return Result(note)
    year, week, _ = ctx.today.isocalendar()
    last = model["last"]
    message = (
        "bosun's own turns (check-ins, events, notifications, resumes) cost "
        f"{round(share * 100)}% of the last 7 days (${last['overhead']:.0f} of ${last['total']:.0f}, "
        f"list prices), over the {round(limit * 100)}% set: see the Impact view's Overhead panel"
    )
    return Result(note, [Finding(f"{OVERHEAD_SHARE}:{year}-W{week:02d}", message)])


@dataclass(frozen=True)
class Check:
    name: str
    severity: str  # error or warn
    summary: str
    run: Callable[[Context], Result]
    # Its alerts are forgotten once the check finds nothing, so the next occurrence alerts again.
    rearms: bool = False


CHECKS: tuple[Check, ...] = (
    Check(
        "morning-missing",
        "error",
        "no morning run by BOSUN_START_TIME + wake delay + BOSUN_WATCHDOG_GRACE_MINUTES; once a day",
        check_morning_missing,
    ),
    Check(
        "morning-failed",
        "error",
        f"today's morning run reported problems, or did not finish (crashed, or {MORNING_MAX_MINUTES}+ min)",
        check_morning_failed,
    ),
    Check(
        "session-missing",
        "error",
        "no tmux session after the morning run (not after bosun stop); once a day",
        check_session,
    ),
    Check(
        "checkin-missing",
        "warn",
        "the checkin window is missing or not running the check-in loop; once a day",
        check_checkin,
    ),
    Check(
        ORCHESTRATOR_DOWN,
        "error",
        "the orchestrator's Claude has not run for BOSUN_WATCHDOG_ORCHESTRATOR_DOWN_MINUTES+ awake "
        "minutes after the morning run; relaunches it once (BOSUN_WATCHDOG_ORCHESTRATOR_RESTART); "
        "once per outage",
        check_orchestrator_down,
    ),
    Check(
        "waiting",
        "warn",
        "a Claude window waits at a prompt for BOSUN_ALERT_WAITING_MINUTES+; once per prompt",
        check_waiting,
    ),
    Check(
        "log-errors",
        "error",
        "new ERROR/FAIL lines in the check-in and launchd logs since the last scan, grouped",
        check_log_errors,
    ),
    Check(
        "ledger-stuck",
        "warn",
        "a ledger item delegated for BOSUN_WATCHDOG_DELEGATED_HOURS+ awake hours; once per item",
        check_ledger_stuck,
    ),
    Check(
        LONG_SHELL,
        "warn",
        "a background command has kept a Claude window in shell for BOSUN_LONG_SHELL_HOURS+ awake "
        "hours; once per episode",
        check_long_shell,
    ),
    Check(
        SPEND_LIMIT,
        "error",
        "a Claude window, or a subagent of its, stopped on a usage limit (spend limit); names the "
        "items it holds, then asks it to resume once the limit is lifted; once per stop",
        check_spend_limit,
    ),
    Check(
        CONTEXT_SIZE,
        "warn",
        "a Claude window's conversation has re-read BOSUN_CONTEXT_WARN_TOKENS+ tokens a turn for "
        f"{CONTEXT_HOURS}+ h, which makes every turn there expensive; once per conversation",
        check_context_size,
    ),
    Check(
        OVERHEAD_SHARE,
        "warn",
        "the turns bosun started itself cost more than BOSUN_OVERHEAD_WARN_SHARE of the last 7 "
        "days, from the Impact snapshot; once a week",
        check_overhead_share,
    ),
    Check(
        "learn",
        "warn",
        "with BOSUN_LEARN_ENABLED=1: the daily learning run is missing, failed, crashed, or "
        "runs past BOSUN_LEARN_TIMEOUT_MINUTES",
        check_learn,
    ),
    Check(
        "events",
        "warn",
        "with BOSUN_EVENTS_ENABLED=1: the check-in loop stopped dispatching events to the "
        "orchestrator, or a delivery failed",
        check_events,
    ),
    Check(
        "slack-watch",
        "warn",
        "with BOSUN_SLACK_WATCH_ENABLED=1 and events on: no Slack watch for 2 intervals in the "
        f"awake window, {slack_watch.MAX_FAILURES} failed runs in a row, or the daily budget spent",
        check_slack_watch,
    ),
    Check(
        LOGIN_CHECK,
        "error",
        "Claude Code is logged out, the last login check failed, or a Claude window answers only "
        "with login errors; once per outage",
        check_login,
        rearms=True,
    ),
)


# ---- a run -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    check: Check
    finding: Finding
    decision: str


@dataclass
class Report:
    results: list[tuple[Check, Result]]
    verdicts: list[Verdict]
    in_window: bool

    def to_send(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.decision == SEND]


def legacy_alerted(finding: Finding) -> bool:
    if finding.legacy_marker is None:
        return False
    try:
        return finding.legacy_marker.read_text().strip() == finding.legacy_value
    except OSError:
        return False


def confirmed(seen: dict[str, str] | None, now: datetime) -> bool:
    """Whether a condition first seen at seen[first] has persisted, without a long gap since."""
    if not seen:
        return False
    first, last = datetime.strptime(seen["first"], STAMP), datetime.strptime(seen["last"], STAMP)
    gap_ok = (now - last).total_seconds() <= CONFIRM_MAX_GAP_SECONDS
    return gap_ok and (now - first).total_seconds() >= CONFIRM_SECONDS


def decide(ctx: Context, finding: Finding, in_window: bool) -> str:
    if finding.key in ctx.state.alerted or legacy_alerted(finding):
        return ALREADY
    if not in_window:
        return HELD
    if finding.confirm and not confirmed(ctx.state.pending.get(finding.key), ctx.now):
        return CONFIRMING
    return SEND


def batch(ctx: Context, verdicts: list[Verdict]) -> list[Verdict]:
    """While something is being confirmed, hold what is ready (once) so that both go out in one
    message on the next run; after a wake, that is the whole morning's news in one alert."""
    if not any(v.decision == CONFIRMING for v in verdicts):
        return verdicts
    return [
        replace(v, decision=CONFIRMING)
        if v.decision == SEND and v.finding.key not in ctx.state.pending
        else v
        for v in verdicts
    ]


def evaluate(ctx: Context, checks: tuple[Check, ...]) -> Report:
    results = [(check, check.run(ctx)) for check in checks]
    in_window = in_awake_window(ctx.config, ctx.now)
    verdicts = [
        Verdict(check, finding, decide(ctx, finding, in_window))
        for check, result in results
        for finding in result.findings
    ]
    return Report(results, batch(ctx, verdicts), in_window)


def next_seen(seen: dict[str, str] | None, now: datetime) -> dict[str, str]:
    if seen and (now - datetime.strptime(seen["last"], STAMP)).total_seconds() <= CONFIRM_MAX_GAP_SECONDS:
        return {"first": seen["first"], "last": stamp(now)}
    return {"first": stamp(now), "last": stamp(now)}


def record_alerts(state: State, report: Report, now: datetime) -> None:
    for verdict in report.to_send():
        state.alerted[verdict.finding.key] = stamp(now)
        if verdict.finding.legacy_marker is not None:
            verdict.finding.legacy_marker.write_text(verdict.finding.legacy_value + "\n")
        state.last_alert = stamp(now)
    cutoff = now - timedelta(days=ALERTED_RETENTION_DAYS)
    state.alerted = {k: at for k, at in state.alerted.items() if datetime.strptime(at, STAMP) >= cutoff}


def record_pending(state: State, report: Report, checks: tuple[Check, ...], now: datetime) -> None:
    """Keep observing what is still being confirmed; forget what this run no longer sees."""
    names = {check.name for check in checks}
    pending = {key: seen for key, seen in state.pending.items() if key.split(":", 1)[0] not in names}
    for verdict in report.verdicts:
        if verdict.decision == CONFIRMING:
            pending[verdict.finding.key] = next_seen(state.pending.get(verdict.finding.key), now)
    state.pending = pending


def rearm(state: State, report: Report) -> None:
    """Forget the alerts of a re-arming check that now finds nothing: the problem went away, and
    the next time it comes back it alerts again."""
    cleared = {check.name for check, result in report.results if check.rearms and not result.findings}
    state.alerted = {k: at for k, at in state.alerted.items() if k.split(":", 1)[0] not in cleared}


def commit(ctx: Context, report: Report, checks: tuple[Check, ...], full_run: bool) -> None:
    """Record what this run alerts, what it is still confirming and how far it read the logs."""
    record_alerts(ctx.state, report, ctx.now)
    record_pending(ctx.state, report, checks, ctx.now)
    rearm(ctx.state, report)
    for _check, result in report.results:
        # Outside the awake window the logs are not consumed, so their errors alert later.
        if result.offsets is not None and (ctx.state.logs is None or report.in_window):
            ctx.state.logs = result.offsets
        if result.shells is not None:
            ctx.state.shells = result.shells
        if result.limits is not None:
            ctx.state.limits = merge_limits(ctx.state.limits, result.limits)
        if result.down is not None:
            ctx.state.orchestrator_down = result.down
    if full_run:
        ctx.state.last_run = stamp(ctx.now)


def compose(verdicts: list[Verdict]) -> str:
    """One alert for everything a run found, most severe first."""
    ordered = sorted(verdicts, key=lambda v: SEVERITY_RANK[v.check.severity])
    if len(ordered) == 1:
        return ordered[0].finding.message
    lines = [f"{len(ordered)} problems:"]
    lines += [f"- {v.finding.message}" for v in ordered[:MAX_ALERT_ITEMS]]
    if len(ordered) > MAX_ALERT_ITEMS:
        lines.append(f"- and {len(ordered) - MAX_ALERT_ITEMS} more: bosun watchdog --status")
    return "\n".join(lines)


# Where alerts go: each sink gets the message and the dry-run flag and says whether it delivered.
# A webhook sink would be one more function here.
Sink = Callable[[str, bool], bool]


def bosun_alert(message: str, dry_run: bool) -> bool:
    command = [str(BOSUN), "alert", *(["--dry-run"] if dry_run else []), message]
    return subprocess.run(command, check=False).returncode == 0


SINKS: tuple[Sink, ...] = (bosun_alert,)


def deliver(message: str, dry_run: bool, sinks: tuple[Sink, ...]) -> bool:
    delivered = [sink(message, dry_run) for sink in sinks]  # every sink, even after a failure
    return all(delivered)


def log(message: str) -> None:
    print(f"{datetime.now():{STAMP}} [watchdog] {message}", flush=True)


# ---- modes -------------------------------------------------------------------------------------


def bosun_send(window: str, prompt: str) -> bool:
    """Paste <prompt> into <window>'s Claude (`bosun send` refuses one waiting at a prompt)."""
    return subprocess.run([str(BOSUN), "send", window, prompt], check=False).returncode == 0


def bosun_restart(window: str) -> bool:
    """Relaunch <window>'s Claude unless you are typing (`refresh` never replaces a running command)."""
    return subprocess.run([str(BOSUN), "refresh", "--unless-active", window], check=False).returncode == 0


@dataclass(frozen=True)
class Inputs:
    config: Config
    facts: Facts
    now: datetime
    record: Callable[[int], dict[str, Any] | None] = claude_state.record
    alive: Callable[[int], bool] = pid_alive
    auth_status: Callable[[str], claude_state.AuthStatus] = claude_state.auth_status
    probe: Callable[[str], claude_state.ProbeResult] = claude_state.probe
    send: Callable[[str, str], bool] = bosun_send
    restart: Callable[[str], bool] = bosun_restart

    def context(self, state: State) -> Context:
        return Context(self.config, self.facts, self.now, state, self.record, self.alive, self.auth_status)


# ---- resuming after a usage limit ----------------------------------------------------------------

RESUME = "resume"
DROP = "drop"  # a different Claude (restarted, or the window is gone): its drain prompt covers it
WAIT = "wait"


def probe_due(probe: dict[str, str] | None, now: datetime) -> bool:
    if not probe:
        return True
    age = (now - datetime.strptime(probe["at"], STAMP)).total_seconds()
    return age >= LIMIT_PROBE_MINUTES * SECONDS_PER_MINUTE


def lifted_after(probe: dict[str, str] | None, since: str) -> bool:
    """A probe that worked, made after the stop began."""
    return probe is not None and probe.get("outcome") == claude_state.PROBE_OK and probe.get("at", "") > since


def answered_resume(stop: dict[str, Any]) -> bool:
    """It was asked to resume after its latest error (it has not failed again since)."""
    return bool(stop.get("resumed_at")) and str(stop["resumed_at"]) >= str(stop.get("at") or stop["since"])


def resume_action(stop: dict[str, Any], live_pid: int | None, probe: dict[str, str] | None) -> str:
    """RESUME once the stop is over (a normal reply, or a probe that worked after its latest error),
    DROP when it is settled or the Claude is a new one, else WAIT. A window asked to resume is not
    asked again until it fails again and a newer probe works."""
    if live_pid != stop.get("pid"):
        return DROP
    if answered_resume(stop):
        return WAIT if stop.get("stopped") else DROP
    if not stop.get("stopped") or lifted_after(probe, str(stop.get("at") or stop["since"])):
        return RESUME
    return WAIT


def needs_probe(stop: dict[str, Any]) -> bool:
    return bool(stop.get("stopped")) and not answered_resume(stop)


def probe_if_due(inputs: Inputs, state: State) -> dict[str, str] | None:
    """The latest probe: a new one if a window is still stopped and the last is old enough."""
    if not any(needs_probe(stop) for stop in state.limits.values()) or not probe_due(
        state.limit_probe, inputs.now
    ):
        return state.limit_probe
    result = inputs.probe(inputs.config.claude_bin)
    log(f"usage-limit probe: {result.outcome}")
    return {"at": stamp(inputs.now), "outcome": result.outcome}


def settle(inputs: Inputs, window: str, stop: dict[str, Any], action: str) -> str:
    """Carry out RESUME (send, and remember when); the action left for the stop afterwards."""
    if action != RESUME:
        return action
    if not inputs.send(window, RESUME_PROMPT):
        log(f"resume: could not reach {window}; trying again on the next run")
        return WAIT
    log(f"resume: asked {window} to resume after its usage-limit stop at {stop['since']}")
    stop["resumed_at"] = stamp(inputs.now)
    return WAIT if stop.get("stopped") else DROP  # a still stopped one: until it replies


def resume_stopped(inputs: Inputs) -> None:
    """After a full run: probe if a window is still stopped (outside the state lock: it can take a
    minute), then ask each window whose stop is over to resume, once; a failed send is retried."""
    state = State.load(inputs.config.state_dir)
    if not state.limits:
        return
    probe = probe_if_due(inputs, state)
    live = {w.name: w.claude_pid for w in inputs.facts.windows}
    with locked_state(inputs.config.state_dir) as locked:
        locked.limit_probe = probe
        for window, stop in list(locked.limits.items()):
            if settle(inputs, window, stop, resume_action(stop, live.get(window), probe)) == DROP:
                del locked.limits[window]


def resume_plan(state: State, facts: Facts, now: datetime) -> list[str]:
    """What resume_stopped would do now, from the last probe (a dry run makes none)."""
    live = {w.name: w.claude_pid for w in facts.windows}
    lines = []
    for window, stop in state.limits.items():
        action = resume_action(stop, live.get(window), state.limit_probe)
        if action == WAIT and answered_resume(stop):
            lines.append(f"{window}: asked to resume at {stop['resumed_at']}; waiting for its reply")
        elif action == WAIT and probe_due(state.limit_probe, now):
            lines.append(f"{window}: still stopped; a run would probe whether the limit is lifted")
        elif action == WAIT:
            lines.append(f"{window}: still stopped ({probe_note(state.limit_probe)})")
        else:
            what = "send the resume prompt" if action == RESUME else "forget it (a new Claude)"
            lines.append(f"{window}: would {what}")
    return lines


def restart_orchestrator(inputs: Inputs) -> None:
    """After a full run: relaunch a down orchestrator once per outage (restart_due), and remember
    that it was tried so the next run only alerts."""
    config = inputs.config
    if not restart_due(config, State.load(config.state_dir).orchestrator_down, inputs.now):
        return
    with locked_state(config.state_dir) as locked:
        if not restart_due(config, locked.orchestrator_down, inputs.now):
            return
        locked.orchestrator_down["restart_at"] = stamp(inputs.now)
    ok = inputs.restart(config.orchestrator)
    outcome = "ok" if ok else "failed (see its log)"
    log(f"{ORCHESTRATOR_DOWN}: ran bosun refresh --unless-active {config.orchestrator}: {outcome}")


def run_once(
    inputs: Inputs, checks: tuple[Check, ...], full_run: bool, sinks: tuple[Sink, ...] = SINKS
) -> int:
    """Check, record and alert. The keys are recorded before sending, so a failing sink is not
    retried every five minutes (the notification and the log still have it)."""
    with locked_state(inputs.config.state_dir) as state:
        ctx = inputs.context(state)
        report = evaluate(ctx, checks)
        commit(ctx, report, checks, full_run)
    if full_run:
        resume_stopped(inputs)
        restart_orchestrator(inputs)
    to_send = report.to_send()
    if not to_send:
        return 0
    log(f"alerting: {', '.join(v.finding.key for v in to_send)}")
    return 0 if deliver(compose(to_send), False, sinks) else 1


def run_dry(inputs: Inputs, checks: tuple[Check, ...], sinks: tuple[Sink, ...] = SINKS) -> int:
    """Report what a run would alert; send nothing and write no state."""
    state = State.load(inputs.config.state_dir)
    report = evaluate(inputs.context(state), checks)
    for verdict in report.verdicts:
        log(f"dry-run: {verdict.check.name}: {DECISION_TEXT[verdict.decision]}: {verdict.finding.message}")
    for line in resume_plan(state, inputs.facts, inputs.now):
        log(f"dry-run: {SPEND_LIMIT}: {line}")
    to_send = report.to_send()
    if not to_send:
        log("dry-run: nothing would be sent")
        return 0
    return 0 if deliver(compose(to_send), True, sinks) else 1


def window_text(config: Config, now: datetime, in_window: bool) -> str:
    begin, end = config.awake.current(now) or awake_bounds(config, now.date())
    where = "inside it: alerts go out" if in_window else "outside it: alerts are held"
    return f"awake window {config.days} {begin:%H:%M}-{end:%H:%M}, {where}"


def status_data(inputs: Inputs, checks: tuple[Check, ...]) -> dict[str, Any]:
    """Every check and its current result, as data (`--status`, and the console's health panel).
    Reads only: nothing is recorded or sent."""
    state = State.load(inputs.config.state_dir)
    report = evaluate(inputs.context(state), checks)
    decisions = {v.finding.key: v.decision for v in report.verdicts}

    def decided(finding: Finding) -> str:
        return DECISION_TEXT[decisions[finding.key]]

    return {
        "at": stamp(inputs.now),
        "awake_window": window_text(inputs.config, inputs.now, report.in_window),
        "in_window": report.in_window,
        "checks": [
            {
                "name": check.name,
                "severity": check.severity,
                "summary": check.summary,
                "note": result.note,
                "findings": [{"message": f.message, "decision": decided(f)} for f in result.findings],
            }
            for check, result in report.results
        ],
        "last_run": state.last_run,
        "last_alert": state.last_alert,
    }


def render_status(inputs: Inputs, checks: tuple[Check, ...]) -> str:
    data = status_data(inputs, checks)
    lines = [f"watchdog at {inputs.now:%Y-%m-%d %H:%M} ({data['awake_window']})"]
    for check in data["checks"]:
        if not check["findings"]:
            lines.append(f"  ok    {check['name']:<16} {check['note']}")
        for finding in check["findings"]:
            label = SEVERITY_LABEL[check["severity"]]
            lines.append(f"  {label}  {check['name']:<16} {finding['message']} [{finding['decision']}]")
    lines.append(f"last full run: {data['last_run'] or 'never'}; last alert: {data['last_alert'] or 'never'}")
    return "\n".join(lines)


def select_checks(only: str | None) -> tuple[Check, ...]:
    if not only:
        return CHECKS
    names = only.split(",")
    unknown = [n for n in names if n not in {c.name for c in CHECKS}]
    if unknown:
        raise ConfigError(
            f"unknown check(s) {', '.join(unknown)}; checks: {', '.join(c.name for c in CHECKS)}"
        )
    return tuple(c for c in CHECKS if c.name in names)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun watchdog", description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--list", action="store_true", help="print the checks")
    parser.add_argument("--only")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        print("\n".join(f"{c.name:<16} {c.severity:<5} {c.summary}" for c in CHECKS))
        return 0
    try:
        checks = select_checks(args.only)
        inputs = Inputs(
            Config.from_env(os.environ),
            Facts.from_json(json.load(sys.stdin)),
            current_time(os.environ.get("BOSUN_WATCHDOG_NOW")),
        )
    except (ConfigError, json.JSONDecodeError) as exc:
        print(f"watchdog: {exc}", file=sys.stderr)
        return 2
    if args.status:
        print(render_status(inputs, checks))
        return 0
    if args.dry_run:
        return run_dry(inputs, checks)
    return run_once(inputs, checks, full_run=args.only is None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
