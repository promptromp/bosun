#!/usr/bin/env python3
"""The dated release tag and the release notes (used by .github/workflows/release.yml).

Usage: release.py next-tag
       release.py notes <range>   (<from>..<to>, or a commit for all its history)

`next-tag` prints the tag for today (UTC), `v<YYYY.MM.DD>`, or `v<YYYY.MM.DD>.2`, `.3`, ... when
there is one already, from `git tag --list 'v*'`. `notes` prints one `- <subject>` line per commit
of the range, newest first, which are the pull requests' titles on a squash-merged `main`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime


NOTES_ARGS = 2  # `notes` and its range
TAG = re.compile(r"^v(\d{4})\.(\d{2})\.(\d{2})(?:\.(\d+))?$")


def next_tag(existing: Iterable[str], today: date) -> str:
    """The tag for today: the day's first is plain, the later ones count from 2."""
    base = f"v{today:%Y.%m.%d}"
    taken = 0
    for tag in existing:
        m = TAG.match(tag.strip())
        if m and f"v{m[1]}.{m[2]}.{m[3]}" == base:
            taken = max(taken, int(m[4] or 1))
    return base if taken == 0 else f"{base}.{taken + 1}"


def notes(subjects: Sequence[str]) -> str:
    """One bullet per subject, in the order given (git log's: newest first)."""
    return "".join(f"- {s.strip()}\n" for s in subjects if s.strip())


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def main(argv: Sequence[str]) -> int:
    if len(argv) == 1 and argv[0] == "next-tag":
        print(next_tag(_git("tag", "--list", "v*").split(), datetime.now(UTC).date()))
        return 0
    if len(argv) == NOTES_ARGS and argv[0] == "notes":
        print(notes(_git("log", "--format=%s", argv[1]).splitlines()), end="")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
