"""bosun's log folder kept in bounds, once a day by the morning run (`tidy_logs` in libexec/bosun-morning).

- A day's file (`<command>-YYYY-MM-DD.log`, which lib/common.sh's `log` and the jobs write) older than
  KEEP_DAYS, by the date in its name, is removed.
- What launchd captures from a job (`*.launchd.out`, `*.launchd.err`) has no day in its name and only grows:
  past LAUNCHD_MAX_BYTES it keeps its last LAUNCHD_KEEP_BYTES, from the start of a line. It is cut in place,
  the same file, because launchd keeps it open and appends to it; a new file would leave launchd writing to
  the removed one.

Nothing else in the folder is touched. Each change is one line on stdout (the morning run logs it).

    log_files.py prune --log-dir <folder> [--today YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path


KEEP_DAYS = 30
LAUNCHD_MAX_BYTES = 1024 * 1024
LAUNCHD_KEEP_BYTES = 256 * 1024
DAY_FILE = re.compile(r"^[a-z][a-z0-9-]*-(?P<day>\d{4}-\d{2}-\d{2})\.log$")
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
        if not path.is_file() or path.is_symlink():
            continue
        day = day_of(path.name)
        if day is not None and day < oldest:
            path.unlink()
            said.append(f"removed {path.name} (older than {KEEP_DAYS} days)")
        elif path.name.endswith(LAUNCHD_SUFFIXES) and path.stat().st_size > LAUNCHD_MAX_BYTES:
            gone = trim_in_place(path, LAUNCHD_KEEP_BYTES, now or datetime.now())
            said.append(
                f"trimmed {path.name} to its last {LAUNCHD_KEEP_BYTES // 1024} KB ({gone // 1024} KB went)"
            )
    return said


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
