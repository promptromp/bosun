"""Whether a login starts bosun (docs/design/control-panel.md, "Start at login").

bosun's login job runs `bosun morning --at-login` at every login, and at every load, when an install or
`bosun update` reloads its jobs. That command asks this module first, before the morning run writes anything:

    login_start.py decide --state-dir D --session S --days MTWRF --start 08:00 --until 20:00 \\
        --enabled 0|1 --running 0|1

prints one line, for the log, and exits 0 to start or 3 to skip (not 1, which is what Python exits with after
a crash). Settings that do not parse are one line on stderr and exit 2. It removes the reload marker after
reading it, whatever the answer, so that the next login is taken for one.

The reasons not to start are checked in the order of REASONS: the first that holds wins. An automatic start
never overrides a Quit that paused the morning, a Stop earlier the same day, or the hours.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import awake
import quit_state


RELOAD_SECONDS = 120  # a reload marker younger than this makes the run a reload of the jobs, not a login
RELOAD_MARKER = "login-reload"  # in the state folder; the job's reload writes it (STAMP)
STAMP = "%Y-%m-%d %H:%M:%S"  # the stop marker's and the reload marker's time, as `date '+%F %T'` writes it
SKIP = 3  # the exit status of `decide` for "do not start"
STARTING = "starting bosun: you logged in during your hours"


@dataclass(frozen=True)
class LoginFacts:
    enabled: bool  # BOSUN_START_AT_LOGIN == "1"
    window: awake.AwakeWindow
    now: datetime  # local, naive
    quit: quit_state.QuitState | None
    stopped_at: datetime | None  # the stop marker's time (stopped-<session>)
    running: bool  # the tmux session exists
    reloaded_at: datetime | None  # the reload marker's time


def _setting_off(facts: LoginFacts) -> str | None:
    return None if facts.enabled else "BOSUN_START_AT_LOGIN is 0"


def _reload(facts: LoginFacts) -> str | None:
    marker = facts.reloaded_at
    if marker is not None and timedelta(0) <= facts.now - marker < timedelta(seconds=RELOAD_SECONDS):
        return "a reload of bosun's jobs, not a login"
    return None


def _quit_paused(facts: LoginFacts) -> str | None:
    record = facts.quit
    if record is not None and record.morning_paused:
        return f"bosun was quit at {quit_state.clock(record)} with the morning start paused"
    return None


def _not_a_day(facts: LoginFacts) -> str | None:
    if facts.window.scheduled(facts.now.date()) or facts.window.contains(facts.now):
        return None
    return f"not one of your days ({facts.window.days})"


def _outside_hours(facts: LoginFacts) -> str | None:
    if facts.window.contains(facts.now):
        return None
    window = facts.window
    return f"outside your hours ({window.start:%H:%M}-{window.until:%H:%M})"


def _stopped_today(facts: LoginFacts) -> str | None:
    stopped = facts.stopped_at
    if stopped is not None and stopped.date() == facts.now.date():
        return f"you stopped bosun today at {stopped:%H:%M}"
    return None


def _running(facts: LoginFacts) -> str | None:
    return "bosun is running already" if facts.running else None


# Each answers the reason not to start, or None; the first that holds wins.
REASONS: tuple[Callable[[LoginFacts], str | None], ...] = (
    _setting_off,
    _reload,
    _quit_paused,
    _not_a_day,
    _outside_hours,
    _stopped_today,
    _running,
)


def decide(facts: LoginFacts) -> tuple[bool, str]:
    """(start?, the line for the log)."""
    for reason in REASONS:
        why = reason(facts)
        if why is not None:
            return False, f"not starting: {why}"
    return True, STARTING


def _read_stamp(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.read_text().strip(), STAMP)
    except (OSError, ValueError):
        return None


def main(argv: Sequence[str], now: datetime | None = None) -> int:
    parser = awake.OneLineParser(prog="login_start.py", allow_abbrev=False)
    parser.add_argument("command", choices=("decide",))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--session", required=True)
    for name in ("--days", "--start", "--until"):
        parser.add_argument(name, required=True)
    parser.add_argument("--enabled", choices=("0", "1"), required=True)
    parser.add_argument("--running", choices=("0", "1"), required=True)
    try:
        args = parser.parse_args(argv)
        window = awake.window_from_settings(args.days, args.start, args.until)
    except awake.SettingsError as exc:
        print(f"login_start.py: {exc}", file=sys.stderr)
        return 2
    state: Path = args.state_dir
    marker = state / RELOAD_MARKER
    facts = LoginFacts(
        enabled=args.enabled == "1",
        window=window,
        now=now or datetime.now(),
        quit=quit_state.read(state),
        stopped_at=_read_stamp(state / f"stopped-{args.session}"),
        running=args.running == "1",
        reloaded_at=_read_stamp(marker),
    )
    marker.unlink(missing_ok=True)
    start, line = decide(facts)
    print(line)
    return 0 if start else SKIP


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
