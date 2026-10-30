"""The console's live facts: what the Sessions view and the health panel show, collected with the
readers `bosun sessions --json`, `bosun status` and the watchdog already use. Design:
docs/design/console.md (section 2). lib/console_sessions.py turns them into the API's models.

Everything here only reads, and is cheap enough to call every 15 s (LiveCache keeps one collection
for CACHE_SECONDS, shared by the request threads):
- `bosun sessions --json` (tmux list-windows, ps and Claude Code's session records) and
  `bosun watchdog --facts` (the same, plus the check-in loop and the morning launchd job). No
  keys are sent and no pane is captured.
- The watchdog's checks through lib/watchdog.py's status_data: no alert goes out and no de-dup
  state is written. Its `claude auth status` is replaced by "not asked" (NOT_ASKED).
- The state files of the events dispatcher, the Slack watcher and the login (login.json), the
  morning log, and each window's transcript for its login verdict (lib/login.py).

Left out because only a side effect could tell them: whether the login works right now (`claude
auth status` starts Claude Code; the login check request costs money), and a window's visible text
(capture-pane), which the login's pattern fallback reads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import event_model
import events
import login
import slack_watch
import watchdog


BOSUN = Path(__file__).resolve().parent.parent / "bin" / "bosun"
CACHE_SECONDS = 10.0
COMMAND_TIMEOUT_SECONDS = 30
NOT_ASKED = claude_state.AuthStatus(None, "not asked")
LOGIN_CHECK_NOTE = (
    "Claude Code's stored login is not asked from the console (claude auth status starts Claude); "
    "see Login for the last login check and the windows' replies"
)
DEFAULT_EVENTS_MAX_PER_DAY = 30
DEFAULT_SLACK_MAX_USD_PER_DAY = 1.5

Run = Callable[[list[str]], str]


class LiveError(Exception):
    """A reader that failed: its message is shown in place of what it would have told."""


def number(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """What the console's live views read from the config (passed in by `bosun console`)."""

    state_dir: Path
    log_dir: Path
    session: str = ""
    tmux_socket: str = ""
    events_enabled: bool = False
    events_max_per_day: int = DEFAULT_EVENTS_MAX_PER_DAY
    slack_watch_enabled: bool = False
    slack_max_usd_per_day: float = DEFAULT_SLACK_MAX_USD_PER_DAY
    learn_enabled: bool = False
    env: Mapping[str, str] = field(default_factory=dict)  # for watchdog.Config.from_env

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        state_dir = Path(env.get("BOSUN_STATE_DIR") or ".")
        return cls(
            state_dir=state_dir,
            log_dir=Path(env.get("BOSUN_LOG_DIR") or state_dir / "logs"),
            session=env.get("BOSUN_SESSION_NAME", ""),
            tmux_socket=env.get("BOSUN_TMUX_SOCKET", ""),
            events_enabled=env.get("BOSUN_EVENTS_ENABLED") == "1",
            events_max_per_day=int(number(env, "BOSUN_EVENTS_MAX_PER_DAY", DEFAULT_EVENTS_MAX_PER_DAY)),
            slack_watch_enabled=env.get("BOSUN_SLACK_WATCH_ENABLED") == "1",
            slack_max_usd_per_day=number(
                env, "BOSUN_SLACK_WATCH_MAX_USD_PER_DAY", DEFAULT_SLACK_MAX_USD_PER_DAY
            ),
            learn_enabled=env.get("BOSUN_LEARN_ENABLED") == "1",
            env=dict(env),
        )


@dataclass(frozen=True)
class Facts:
    """One collection. A reader that failed leaves its part None (or empty) and says why in errors."""

    at: datetime  # UTC
    settings: Settings
    windows: list[dict[str, Any]] | None = None  # `bosun sessions --json`
    checks: dict[str, Any] | None = None  # watchdog.status_data
    morning: watchdog.MorningRun | None = None
    events: events.State | None = None
    events_pending: int = 0
    slack: slack_watch.State | None = None
    login_state: login.State = field(default_factory=login.State)
    window_logins: dict[str, login.Verdict] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # part -> what went wrong


def run_command(argv: list[str]) -> str:
    """<argv>'s output; LiveError if it fails or takes longer than COMMAND_TIMEOUT_SECONDS."""
    try:
        result = subprocess.run(  # our own commands, no shell
            argv,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LiveError(f"{' '.join(argv[1:])}: {exc}") from exc
    if result.returncode != 0:
        last = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise LiveError(f"{' '.join(argv[1:])} exited {result.returncode}: {last}")
    return result.stdout


def bosun_json(run: Run, *args: str) -> Any:
    text = run([str(BOSUN), *args])
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LiveError(f"{' '.join(args)} gave no JSON: {exc}") from exc


def watchdog_status(settings: Settings, facts: dict[str, Any]) -> dict[str, Any]:
    """The watchdog's checks as data (read-only), its login check without `claude auth status`."""
    try:
        config = watchdog.Config.from_env(settings.env)
    except watchdog.ConfigError as exc:
        raise LiveError(str(exc)) from exc
    inputs = watchdog.Inputs(
        config,
        watchdog.Facts.from_json(facts),
        watchdog.current_time(None),
        auth_status=lambda _bin: NOT_ASKED,
    )
    data = watchdog.status_data(inputs, watchdog.CHECKS)
    for check in data["checks"]:
        if check["name"] == watchdog.LOGIN_CHECK and not check["findings"]:
            check["note"] = LOGIN_CHECK_NOTE
    return data


def window_logins(windows: list[dict[str, Any]], state: login.State) -> dict[str, login.Verdict]:
    """Each Claude window's login verdict from its transcript (none without one: no pane is read)."""
    found = {}
    for window in windows:
        transcript = (window.get("claude") or {}).get("transcript")
        if transcript:
            found[str(window["name"])] = login.window_verdict(Path(transcript), state.last_probe)
    return found


def morning_log(settings: Settings) -> Path:
    return settings.log_dir / f"morning-{datetime.now():%Y-%m-%d}.log"


@dataclass
class Collector:
    """Gathers one Facts; <run> runs a command and returns its output (a fake in the tests)."""

    settings: Settings
    run: Run = run_command
    errors: dict[str, str] = field(default_factory=dict)

    def attempt(self, part: str, read: Callable[[], Any]) -> Any:
        try:
            return read()
        except (LiveError, event_model.EventsError, slack_watch.WatchError, OSError) as exc:
            self.errors[part] = str(exc)
            return None

    def collect(self) -> Facts:
        self.errors = {}
        settings = self.settings
        windows = self.attempt("sessions", lambda: bosun_json(self.run, "sessions", "--json"))
        wfacts = self.attempt("watchdog", lambda: bosun_json(self.run, "watchdog", "--facts"))
        status = self.attempt("watchdog", lambda: watchdog_status(settings, wfacts)) if wfacts else None
        directory = events.events_dir(settings.state_dir)
        queue = self.attempt("events", lambda: events.State.load(directory))
        pending = self.attempt("events", lambda: len(events.pending(directory, queue))) if queue else 0
        slack = self.attempt(
            "slack_watch", lambda: slack_watch.State.load(settings.state_dir / slack_watch.SUBDIR)
        )
        login_state = login.State.load(settings.state_dir)
        return Facts(
            at=datetime.now(UTC),
            settings=settings,
            windows=windows,
            checks=status,
            morning=watchdog.last_real_run(morning_log(settings)),
            events=queue,
            events_pending=pending or 0,
            slack=slack,
            login_state=login_state,
            window_logins=window_logins(windows or [], login_state),
            errors=dict(self.errors),
        )


class LiveCache:
    """The latest Facts, collected again once they are CACHE_SECONDS old. Requests that come in
    while a collection runs wait for it instead of starting their own."""

    def __init__(
        self,
        collect: Callable[[], Facts],
        ttl_seconds: float = CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.collect = collect
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._facts: Facts | None = None
        self._at = 0.0

    def get(self) -> Facts:
        with self._lock:
            now = self.clock()
            if self._facts is None or now - self._at >= self.ttl_seconds:
                self._facts = self.collect()
                self._at = now
            return self._facts


def live_cache(env: Mapping[str, str] = os.environ) -> LiveCache:
    return LiveCache(Collector(Settings.from_env(env)).collect)
