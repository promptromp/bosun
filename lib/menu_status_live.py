"""`bosun status --json`: gathers the facts lib/menu_status.py turns into the object the menu bar app reads
(design: docs/design/menu-bar.md, "The CLI side"), and prints it.

Everything here only reads, and it reads what the console's Sessions view and the watchdog's status read
already: `bosun sessions --json` and `bosun watchdog --facts` through lib/console_live.py's collector (not its
events, Slack watcher and login parts, which the menu does not show), the watchdog's checks through
`watchdog.status_data` (no alert, no de-dup state written), the quit record, the update's lock, the console's
running lock, the keep-awake record the morning writes and the last hour of the logs' ERROR lines (the
watchdog's own scan, but never its marks).

The command always prints one JSON object with its `contract` and exits 0, whatever it could not read: a
part that failed becomes a problems line and at least a warning, and a bug in here an object that says so.

    menu_status_live.py [--config-error TEXT]   --config-error: bosun.env could not be read; say so
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_files
import awake
import console_live
import console_token
import menu_status
import quit_state
import update_state
import watchdog
from console_live import LiveError, Run
from menu_status import StatusFacts


LOG_WINDOW = timedelta(hours=1)
SCAN_LINES = 5000  # the most lines read back from the end of one log
PS_TIMEOUT_SECONDS = 5

Alive = Callable[[int], bool]


# ---- the keep-awake ------------------------------------------------------------------------------


def is_caffeinate(pid: int) -> bool:
    """Whether <pid> is a running caffeinate: the morning's keep-awake (a pid can be reused later)."""
    try:
        result = subprocess.run(  # our own argv, no shell
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "caffeinate" in result.stdout


def keep_awake_until(state_dir: Path, session: str, alive: Alive = is_caffeinate) -> datetime | None:
    """When the Mac's keep-awake ends, as local naive time: the morning's keep-awake-<session>.json, while
    its caffeinate still runs. None when there is none, it is garbled, or the process is gone."""
    try:
        data = json.loads((state_dir / f"keep-awake-{session}.json").read_text())
        until, pid = datetime.fromisoformat(data["until"]), data["pid"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not alive(pid):
        return None
    return until.astimezone().replace(tzinfo=None)


# ---- the logs ------------------------------------------------------------------------------------


def line_time(line: str) -> datetime | None:
    """The time a log line starts with, if it starts with one."""
    stamped = watchdog.STAMPED.match(line)
    if stamped is None:
        return None
    try:
        return datetime.strptime(stamped["at"], watchdog.STAMP)
    except ValueError:
        return None


def errors_since(path: Path, cutoff: datetime) -> list[str]:
    """The alerting ERROR lines of <path> at or after <cutoff>, oldest first: read back from the end, up
    to the first line stamped earlier (lines without a stamp go with the one before them)."""
    found = []
    for raw in itertools.islice(agent_files.reversed_lines(path), SCAN_LINES):
        line = raw.decode(errors="replace")
        when = line_time(line)
        if when is not None and when < cutoff:
            break
        if watchdog.is_alerting_error(line):
            found.append(f"{path.name}: {watchdog.detail(line)}")
    return found[::-1]


def recent_log_errors(log_dir: Path, now: datetime) -> tuple[str, ...]:
    """The ERROR lines of the logs the watchdog scans (and only those) from the last hour, as "<log>: <line>".
    A log not written to within the hour has none. Nothing is recorded."""
    cutoff = now - LOG_WINDOW
    found: list[str] = []
    for path in watchdog.scanned_logs(log_dir):
        try:
            if path.stat().st_mtime >= cutoff.timestamp():
                found += errors_since(path, cutoff)
        except FileNotFoundError:  # rotated away while we looked
            continue
    return tuple(found)


# ---- the rest ------------------------------------------------------------------------------------


def awake_window(env: Mapping[str, str]) -> awake.AwakeWindow:
    try:
        return watchdog.Config.from_env(env).awake
    except watchdog.ConfigError as exc:
        raise LiveError(str(exc)) from exc


def update_facts(state_dir: Path) -> tuple[bool, tuple[str, ...]]:
    """Whether an update runs now, and the windows it waits for (it names none unless it is running)."""
    if not update_state.running(state_dir):
        return False, ()
    record = update_state.read(state_dir)
    waiting = record.pending if record is not None and record.step == "agents" else ()
    return True, tuple(waiting)


def gather(env: Mapping[str, str], now: datetime, run: Run) -> StatusFacts:
    """The facts, a part that could not be read left empty with its reason in `errors`. <now> is local."""
    settings = console_live.Settings.from_env(env)
    collector = console_live.Collector(settings, run)
    attempt = collector.attempt
    state_dir = settings.state_dir
    window = attempt("config", lambda: awake_window(env))
    windows = attempt("sessions", lambda: console_live.bosun_json(run, "sessions", "--json"))
    # With settings that do not load the checks cannot be made (that is said once, as the config)
    watchdog_facts = attempt("watchdog", lambda: console_live.bosun_json(run, "watchdog", "--facts"))
    checks = None
    if window is not None and watchdog_facts:
        checks = attempt("watchdog", lambda: console_live.watchdog_status(settings, watchdog_facts))
    updating, waiting_for = update_facts(state_dir)
    return StatusFacts(
        windows=windows,
        checks=checks,
        awake=window,
        quit=quit_state.read(state_dir),
        update_running=updating,
        update_waiting_for=waiting_for,
        console_running=console_token.running(state_dir),
        keep_awake_until=keep_awake_until(state_dir, settings.session),
        log_errors=attempt("logs", lambda: recent_log_errors(settings.log_dir, now)) or (),
        log_dir=str(settings.log_dir),
        errors=dict(collector.errors),
    )


def main(
    argv: list[str],
    env: Mapping[str, str] | None = None,
    run: Run = console_live.run_command,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="bosun status --json", allow_abbrev=False)
    parser.add_argument("--config-error", metavar="TEXT", help="bosun.env could not be read: say so")
    args = parser.parse_args(argv)
    if args.config_error is not None:
        print(json.dumps(menu_status.unreadable_config(args.config_error)))
        return 0
    moment = now or watchdog.current_time(None)
    try:
        out = menu_status.snapshot(gather(os.environ if env is None else env, moment, run), moment)
    except Exception as exc:  # noqa: BLE001 - the app reads one object whatever went wrong in here
        out = menu_status.failure(f"bosun status could not be completed: {exc!r}", menu_status.WARNING)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
