"""bosun's log folder kept in bounds, once a day by the morning run (`tidy_logs` in libexec/bosun-morning).

- A day's file (`<command>-YYYY-MM-DD.log`, which lib/common.sh's `log` and the jobs write: WRITERS, bosun's
  own names, so that another program's day files in a shared folder are never taken) older than KEEP_DAYS, by
  the date in its name, is removed.
- What an agent writes in the folder (its profile's log_files: agy's logs, say) not written for KEEP_DAYS is
  removed.
- What launchd captures from a job (`*.launchd.out`, `*.launchd.err`) has no day in its name and only grows:
  past LAUNCHD_MAX_BYTES it keeps its last LAUNCHD_KEEP_BYTES, from the start of a line. It is cut in place,
  the same file, because launchd keeps it open and appends to it; a new file would leave launchd writing to
  the removed one.

Nothing else in the folder is touched. Each change is one line on stdout (the morning run logs it).

    log_files.py prune --log-dir <folder> [--today YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

import agent_state


KEEP_DAYS = 30
LAUNCHD_MAX_BYTES = 1024 * 1024
LAUNCHD_KEEP_BYTES = 256 * 1024
# Whose day files are bosun's: lib/common.sh's `log` names a file after the command bin/bosun runs
# (BOSUN_COMMAND, a libexec/bosun-<command>), install.sh's own, and "bosun" when no command is set; the jobs
# and the other writers (the morning run, the check-in loop, the hook, learning, delegate, impact, launch) are
# commands too. tests/unit/test_uninstall_plan.py holds it to libexec/.
WRITERS = (
    *("alert", "app", "attach", "checkin", "chores", "console", "context", "delegate", "doctor", "events"),
    *("guard", "hook", "impact", "init", "kg", "launch", "learn", "ledger", "morning", "my-prs", "orphans"),
    *(
        "pr-threads",
        "preflight",
        "quit",
        "refresh",
        "resolve",
        "review-watch",
        "send",
        "sessions",
        "settings",
    ),
    *("setup", "slack-watch", "start", "status", "stop", "trust", "uninstall", "update", "wake-test"),
    *("watchdog", "install", "bosun"),
)
DAY_FILE = re.compile(rf"^(?:{'|'.join(map(re.escape, WRITERS))})-(?P<day>\d{{4}}-\d{{2}}-\d{{2}})\.log$")
LAUNCHD_SUFFIXES = (".launchd.out", ".launchd.err")


def day_of(name: str) -> date | None:
    """The day in a day's file's name; None for any other file."""
    found = DAY_FILE.match(name)
    if not found:
        return None
    try:
        return date.fromisoformat(found["day"])
    except ValueError:
        return None


def agent_logs() -> tuple[str, ...]:
    """The patterns of what the agents write in bosun's log folder (each one's profile)."""
    return tuple(pattern for agent in agent_state.AGENTS.values() for pattern in agent.PROFILE.log_files)


def is_agent_log(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in agent_logs())


def trim_in_place(path: Path, keep: int, now: datetime) -> int:
    """Keep the last <keep> bytes of <path>, from the start of a line, under a line that says so, in the same
    file. Returns how many bytes went."""
    with path.open("r+b") as handle:
        size = handle.seek(0, 2)
        handle.seek(max(size - keep, 0))
        tail = handle.read()
        cut = tail.find(b"\n")
        if size > keep and cut != -1:
            tail = tail[cut + 1 :]
        note = f"{now:%Y-%m-%d %H:%M:%S} [logs] trimmed to its last {keep // 1024} KB\n".encode()
        handle.seek(0)
        handle.write(note + tail)
        handle.truncate()
    return size - len(tail)


def prune(folder: Path, today: date, now: datetime | None = None) -> list[str]:
    """Remove the days past KEEP_DAYS and trim launchd's files; what it did, one line each."""
    if not folder.is_dir():
        return []
    oldest = today - timedelta(days=KEEP_DAYS)
    said = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and not path.is_symlink():
            line = tidy(path, oldest, now or datetime.now())
            said += [line] if line else []
    return said


def tidy(path: Path, oldest: date, now: datetime) -> str:
    """<path> removed or trimmed if it is one to (a day before <oldest>), and what was done; "" if nothing."""
    day = day_of(path.name)
    if day is not None and day < oldest:
        path.unlink()
        return f"removed {path.name} (older than {KEEP_DAYS} days)"
    if is_agent_log(path.name) and date.fromtimestamp(path.stat().st_mtime) < oldest:
        path.unlink()
        return f"removed {path.name} (not written for {KEEP_DAYS} days)"
    if path.name.endswith(LAUNCHD_SUFFIXES) and path.stat().st_size > LAUNCHD_MAX_BYTES:
        gone = trim_in_place(path, LAUNCHD_KEEP_BYTES, now)
        return f"trimmed {path.name} to its last {LAUNCHD_KEEP_BYTES // 1024} KB ({gone // 1024} KB went)"
    return ""


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="log_files.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pruner = sub.add_parser("prune")
    pruner.add_argument("--log-dir", required=True, type=Path)
    pruner.add_argument("--today", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    for line in prune(args.log_dir, args.today or date.today()):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
