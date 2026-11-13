"""`bosun status --json`: gathers the facts lib/menu_status.py turns into the object the menu bar app reads
(design: docs/design/menu-bar.md, "The CLI side"), and prints it.

Everything here only reads, and it reads what the console's Sessions view and the watchdog's status read
already: `bosun sessions --json` and `bosun watchdog --facts` through lib/console_live.py's collector (not its
events, Slack watcher and login parts, which the menu does not show), the watchdog's checks through
`watchdog.status_data` (no alert, no de-dup state written) and its gate on a missing session
(`watchdog.session_gate_kind`), the quit record, the update's lock, the console's
running lock, the keep-awake record the morning writes and the last hour of the logs' ERROR lines (the
watchdog's own scan, but never its marks).

The command always prints one JSON object with its `contract` and exits 0, whatever it could not read: a
part that failed becomes a problems line and at least a warning, and a bug in here an object that says so.

    menu_status_live.py [--config-error TEXT]   --config-error: bosun.env could not be read; say so
"""

from __future__ import annotations

import argparse
import contextlib
import io
import itertools
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_files
import console_live
import console_token
import keep_awake
import menu_status
import quit_state
import update_state
import watchdog
from console_live import LiveError, Run
from menu_status import SessionGate, StatusFacts, WatchdogJob


LOG_WINDOW = timedelta(hours=1)
SCAN_LINES = 5000  # the most lines read back from the end of one log


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


GATE_KINDS = {
    watchdog.GATE_OUTSIDE: menu_status.RESTING,
    # The awake window began and the morning run is not yet due (it may be going, or about to): starting
    watchdog.GATE_NOT_DUE: menu_status.STARTING,
    watchdog.GATE_MORNING_RUNNING: menu_status.STARTING,
    watchdog.GATE_NO_MORNING: menu_status.NO_MORNING,
    watchdog.GATE_QUIT: menu_status.QUIT,
}


def gate_of(ctx: watchdog.Context) -> SessionGate:
    """Whether the watchdog expects the session to run now: its own `session_gate_kind` over the context its
    checks are made from, as a fact (never its text)."""
    found = watchdog.session_gate_kind(ctx)
    if found is None:
        return SessionGate(menu_status.EXPECTED)
    if found[0] == watchdog.GATE_STOPPED:
        return SessionGate(menu_status.STOPPED_BY_STOP, watchdog.stopped_today(ctx))
    return SessionGate(GATE_KINDS[found[0]])


def load_state(state_dir: Path) -> tuple[watchdog.State, str]:
    """The watchdog's saved state, and what it said if it could not be read (it prints that and starts
    afresh, which must not reach this command's stdout)."""
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        state = watchdog.State.load(state_dir)
    text = said.getvalue().strip()
    return state, watchdog.detail(text) if text else ""


def watchdog_view(
    config: watchdog.Config, watchdog_facts: dict[str, Any], now: datetime
) -> tuple[dict[str, Any], SessionGate, str]:
    """The watchdog's checks as data and its gate, from one set of inputs and one loading of its state
    (read-only), and what it said about the state if that could not be read."""
    inputs = console_live.watchdog_inputs(config, watchdog_facts, now)
    state, note = load_state(config.state_dir)
    checks = watchdog.status_data(inputs, watchdog.CHECKS, state)
    return checks, gate_of(inputs.context(state)), note


def update_facts(state_dir: Path) -> tuple[bool, tuple[str, ...]]:
    """Whether an update runs now, and the windows it waits for (it names none unless it is running)."""
    if not update_state.running(state_dir):
        return False, ()
    record = update_state.read(state_dir)
    waiting = record.pending if record is not None and record.step == "agents" else ()
    return True, tuple(waiting)


def read_list(run: Run, *args: str) -> list[dict[str, Any]]:
    data = console_live.bosun_json(run, *args)
    if not isinstance(data, list):
        raise LiveError(f"{' '.join(args)} gave no list")
    return data


def read_watchdog_facts(run: Run) -> dict[str, Any]:
    data = console_live.bosun_json(run, "watchdog", "--facts")
    if not isinstance(data, dict) or not data:
        raise LiveError("watchdog --facts gave no facts")
    return data


def launched_at(log_dir: Path) -> datetime | None:
    """When launchd last ran the watchdog, whatever the run did: its output file (which every run that skipped
    writes a line to, in a quit or an update's hold, where `last_run` stays as it was). Local and naive."""
    try:
        return datetime.fromtimestamp((log_dir / "watchdog.launchd.out").stat().st_mtime)
    except OSError:
        return None


WAKETIME = re.compile(r"\bsec = (\d+)\b")


def parse_waketime(text: str) -> datetime | None:
    """`sysctl -n kern.waketime` as local time: `{ sec = 1794543736, usec = 756800 } Fri Nov 13 06:22:16`.
    A sec of 0 is a Mac that has not woken since it started; anything else it prints is no time."""
    found = WAKETIME.search(text)
    if found is None or int(found[1]) == 0:
        return None
    try:
        return datetime.fromtimestamp(int(found[1]))
    except (OverflowError, OSError, ValueError):
        return None


def read_waketime() -> datetime | None:
    """When the Mac last woke, from the kernel; None when it cannot say (no sysctl, another system)."""
    try:
        done = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "kern.waketime"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_waketime(done.stdout) if done.returncode == 0 else None


def watchdog_job_of(
    watchdog_facts: Mapping[str, Any] | None,
    checks: Mapping[str, Any] | None,
    launched: datetime | None = None,
    woke: datetime | None = None,
) -> WatchdogJob | None:
    """The watchdog's own job: what `watchdog --facts` says of it (loaded, plist installed) and when it last
    ran: its last full run (its saved state) or <launched>, whichever is later (a run that skipped is a run).
    None when the facts do not say (an older bosun's, or not read)."""
    job = (watchdog_facts or {}).get("watchdog_job")
    if not isinstance(job, dict):
        return None
    last = (checks or {}).get("last_run")
    try:
        last_run = datetime.strptime(last, watchdog.STAMP) if isinstance(last, str) else None
    except ValueError:
        last_run = None
    ran = max((t for t in (last_run, launched) if t is not None), default=None)
    return WatchdogJob(bool(job.get("installed")), bool(job.get("state")), ran, woke)


def watchdog_parts(
    collector: console_live.Collector,
    config: watchdog.Config | None,
    run: Run,
    now: datetime,
    log_dir: Path,
    waketime: Callable[[], datetime | None] = read_waketime,
) -> tuple[dict[str, Any] | None, SessionGate | None, WatchdogJob | None]:
    """The watchdog's checks and gate, and its own job; None for each that could not be made (the reason is in
    collector.errors). The checks need the settings, which are said once, as the config."""
    attempt = collector.attempt
    watchdog_facts = attempt("watchdog", lambda: read_watchdog_facts(run))
    if not watchdog_facts:
        return None, None, None
    woke = waketime() if isinstance(watchdog_facts.get("watchdog_job"), dict) else None
    view = attempt("watchdog", lambda: watchdog_view(config, watchdog_facts, now)) if config else None
    if view is None:
        return None, None, watchdog_job_of(watchdog_facts, None, launched_at(log_dir), woke)
    checks, gate, note = view
    if note:
        collector.errors["watchdog-state"] = note
    return checks, gate, watchdog_job_of(watchdog_facts, checks, launched_at(log_dir), woke)


def gather(
    env: Mapping[str, str],
    now: datetime,
    run: Run,
    waketime: Callable[[], datetime | None] = read_waketime,
) -> StatusFacts:
    """The facts, a part that could not be read left empty with its reason in `errors`. <now> is local."""
    settings = console_live.Settings.from_env(env)
    collector = console_live.Collector(settings, run)
    attempt = collector.attempt
    state_dir = settings.state_dir
    config = attempt("config", lambda: console_live.watchdog_config(settings))
    windows = attempt("sessions", lambda: read_list(run, "sessions", "--json"))
    checks, gate, job = watchdog_parts(collector, config, run, now, settings.log_dir, waketime)
    updating, waiting_for = update_facts(state_dir)
    return StatusFacts(
        windows=windows,
        checks=checks,
        awake=config.awake if config else None,
        quit=quit_state.read(state_dir),
        gate=gate,
        update_running=updating,
        update_waiting_for=waiting_for,
        console_running=console_token.running(state_dir),
        keep_awake_until=keep_awake.keep_awake_until(state_dir, settings.session),
        log_errors=attempt("logs", lambda: recent_log_errors(settings.log_dir, now)) or (),
        log_dir=str(settings.log_dir),
        errors=dict(collector.errors),
        watchdog_job=job,
    )


def main(
    argv: list[str],
    env: Mapping[str, str] | None = None,
    run: Run = console_live.run_command,
    now: datetime | None = None,
    waketime: Callable[[], datetime | None] = read_waketime,
) -> int:
    parser = argparse.ArgumentParser(prog="bosun status --json", allow_abbrev=False)
    parser.add_argument("--config-error", metavar="TEXT", help="bosun.env could not be read: say so")
    args = parser.parse_args(argv)
    if args.config_error is not None:
        print(json.dumps(menu_status.unreadable_config(args.config_error)))
        return 0
    moment = now or watchdog.current_time(None)
    try:
        # Whatever anything prints while the facts are gathered (the watchdog's state loading says
        # what it could not read) goes to stderr: stdout is the one object
        with contextlib.redirect_stdout(sys.stderr):
            facts = gather(os.environ if env is None else env, moment, run, waketime)
        out = menu_status.snapshot(facts, moment)
    except Exception as exc:  # noqa: BLE001 - the app reads one object whatever went wrong in here
        out = menu_status.failure(f"bosun status could not be completed: {exc!r}", menu_status.WARNING)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
