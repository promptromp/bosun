#!/usr/bin/env python3
"""The dated release tag and the release notes (used by .github/workflows/release.yml).

Usage: release.py next-tag
       release.py latest
       release.py notes <range>   (<from>..<to>, or a commit for all its history)
       release.py gate --required-file F --runs-file F --pr-tree T --main-tree T [--head-runs-file F]

`next-tag` prints the tag for today (UTC), `v<YYYY.MM.DD>`, or `v<YYYY.MM.DD>.2`, `.3`, ... when
there is one already, from the repository's `v*` tags. `latest` prints the newest release tag
(nothing when there is none). `notes` prints one `- <subject>` line per commit of the range, newest
first, which are the pull requests' titles on a squash-merged `main`. `gate` prints why main's head
is not to be released and exits 1, or exits 0. Through the merge queue main's head is the merge
group's own commit, which the required checks ran on, so it reads the checks on main's head itself
(`--head-runs-file`); for a commit merged before the queue, none of them ran there and it reads the
pull request main's head came from instead (the files are what the workflow gathered: the required
check names one per line, and the check runs as JSON).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from update_check import release_key  # the tag rule, once: what `bosun update` finds the newest release by


NOTES_ARGS = 2  # `notes` and its range
PASSING = ("success", "neutral", "skipped")  # what GitHub accepts for a required check


def next_tag(existing: Iterable[str], today: date) -> str:
    """The tag for today: the day's first is plain, the later ones count from 2."""
    taken = 0
    for tag in existing:
        key = release_key(tag)
        if key and key[:3] == (today.year, today.month, today.day):
            taken = max(taken, key[3])
    base = f"v{today:%Y.%m.%d}"
    return base if taken == 0 else f"{base}.{taken + 1}"


def latest(existing: Iterable[str]) -> str | None:
    """The newest release tag, by date then number; None when there is none."""
    keyed = [(k, t.strip()) for t in existing if (k := release_key(t))]
    return max(keyed)[1] if keyed else None


def _failing_check(required: Sequence[str], runs: Sequence[Mapping[str, object]]) -> str | None:
    """The first required check that is missing or whose newest run did not pass (None: all green)."""
    for name in required:
        named = [r for r in runs if r.get("name") == name]
        if not named:
            return f"{name}: missing"
        newest = max(named, key=lambda r: int(str(r.get("id", 0))))
        if newest.get("conclusion") not in PASSING:
            return f"{name}: {newest.get('conclusion') or 'not finished'}"
    return None


def gate(
    required: Sequence[str],
    runs: Sequence[Mapping[str, object]],
    pr_head_tree: str | None,
    main_tree: str,
    head_runs: Sequence[Mapping[str, object]] = (),
) -> str | None:
    """Why main's head is not to be released (None: release it).

    When any required check ran on main's head commit itself (the merge queue's commit), each
    required check's newest run there must have concluded as GitHub accepts for a required check:
    those runs are on exactly this commit, so no tree comparison is needed. Otherwise (a commit
    merged before the queue) it needs the pull request main's head came from, checked on the tree
    main now has, and the same of the pull request's runs."""
    if any(r.get("name") in required for r in head_runs):
        return _failing_check(required, head_runs)
    if pr_head_tree is None:
        return "main's head came from no pull request"
    if pr_head_tree != main_tree:
        return "main's head is not the tree its pull request was checked on"
    return _failing_check(required, runs)


def notes(subjects: Sequence[str]) -> str:
    """One bullet per subject, in the order given (git log's: newest first)."""
    return "".join(f"- {s.strip()}\n" for s in subjects if s.strip())


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _read_runs(text: str) -> list[Mapping[str, object]]:
    """Check runs as one JSON array, or as the objects `gh api --paginate --jq` prints in a row."""
    decoder = json.JSONDecoder()
    found: list[Mapping[str, object]] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        value, pos = decoder.raw_decode(text, pos)
        found.extend(value if isinstance(value, list) else [value])
    return found


def _gate_cli(argv: Sequence[str]) -> int:
    opts = dict(zip(argv[::2], argv[1::2], strict=False))
    try:
        required_text = Path(opts["--required-file"]).read_text()
        runs = _read_runs(Path(opts["--runs-file"]).read_text())
        head_file = opts.get("--head-runs-file")
        head_runs = _read_runs(Path(head_file).read_text()) if head_file else []
        main_tree = opts["--main-tree"]
    except (KeyError, OSError, ValueError) as err:
        print(f"release.py gate: {err!r}", file=sys.stderr)
        return 2
    required = [line for line in required_text.splitlines() if line.strip()]
    why = gate(required, runs, opts.get("--pr-tree") or None, main_tree, head_runs)
    if why:
        print(why)
        return 1
    return 0


def main(argv: Sequence[str], today: date | None = None) -> int:
    if argv == ["next-tag"]:
        print(next_tag(_git("tag", "--list", "v*").split(), today or datetime.now(UTC).date()))
        return 0
    if argv == ["latest"]:
        print(latest(_git("tag", "--list", "v*").split()) or "")
        return 0
    if len(argv) == NOTES_ARGS and argv[0] == "notes":
        print(notes(_git("log", "--format=%s", argv[1]).splitlines()), end="")
        return 0
    if argv[:1] == ["gate"]:
        return _gate_cli(argv[1:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
