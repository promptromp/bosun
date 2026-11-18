"""What the two test selections share: tools/smoke_select.py (the smoke suite's sections, `tests/smoke.sh
--changed`) and tools/vm_select.py (the Mac VM suites' parts, `tests/sandbox.sh mac --changed`).

- A marker line: a `section` of tests/smoke.sh or a `part` of a VM suite, `<keyword> <id> "<name>" [needs
  <id>,...] [<flag>...] [covers '<glob>,...']`, where the flags are the keyword's own (a part's `last` and
  `paid`; a section has none). `marker_lines` reads them, in file order.
- What a marker needs, in turn: `needed`.
- Globs, matched against the path from the repository root, whole: `*` is within one folder, `**` any number
  of them, `?` one character.
- A rules file's lines: `rule_lines`, each without its comment.
- What git says changed since a ref: the merge-base with HEAD, the files changed since (committed, staged,
  unstaged and untracked, since the tree on disk is what runs), what git lists, and one file's change as the
  hunks of `git diff -U0` with its text at the base (`file_change`, which tools/shell_library.py maps to the
  functions it changed).

Whatever goes wrong raises SelectionError, which each tool turns into running more, never less. Plain python3
3.9 is enough, as for the tools that use it."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import shell_library


COMMENT = re.compile(r"(?:^|\s)#.*$")


class SelectionError(Exception):
    """Git or the files said something that means the whole suite should run."""


@dataclass(frozen=True)
class Marker:
    """A section of the smoke suite, or a part of a VM suite: what its marker line says."""

    id: str
    name: str
    needs: tuple[str, ...]
    covers: tuple[str, ...]
    flags: frozenset[str] = frozenset()


def marker_pattern(keyword: str, flags: Sequence[str] = ()) -> re.Pattern[str]:
    """The marker line of <keyword>, with its own <flags> (each at most once, in any order) after `needs`."""
    flag_words = "|".join(re.escape(flag) for flag in flags)
    flag_part = rf"(?P<flags>(?:\s+(?:{flag_words}))*)" if flags else "(?P<flags>)"
    return re.compile(
        rf"^\s*{re.escape(keyword)}\s+(?P<id>[a-z0-9-]+)\s+\"(?P<name>[^\"]*)\""
        r"(?:\s+needs\s+(?P<needs>[a-z0-9,-]+))?"
        rf"{flag_part}"
        r"(?:\s+covers\s+'(?P<covers>[^']*)')?\s*$"
    )


def marker_lines(text: str, keyword: str, flags: Sequence[str] = ()) -> list[tuple[int, Marker]]:
    """Each marker line of <keyword> in <text> (its number, from 1) and the marker it begins. A line that does
    not parse (or `<keyword>() {`, the function) is not one: each tool's guard test checks that every line
    that starts with the keyword parses."""
    pattern = marker_pattern(keyword, flags)
    start = re.compile(rf"^\s*{re.escape(keyword)}\s")
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        match = pattern.match(line) if start.match(line) else None
        if match is None:
            continue
        needs = tuple(match["needs"].split(",")) if match["needs"] else ()
        covers = tuple(g.strip() for g in (match["covers"] or "").split(",") if g.strip())
        marked = frozenset(match["flags"].split())
        found.append((number, Marker(match["id"], match["name"], needs, covers, marked)))
    return found


def needed(markers: Sequence[Marker], picked: Iterable[str]) -> list[str]:
    """The markers the <picked> ones need, in file order, and what those need in turn (not the picked)."""
    needs = {m.id: m.needs for m in markers}
    chosen = set(picked)
    want = set(chosen)
    queue = list(chosen)
    while queue:
        for need in needs.get(queue.pop(), ()):
            if need not in want:
                want.add(need)
                queue.append(need)
    return [m.id for m in markers if m.id in want and m.id not in chosen]


def rule_lines(text: str) -> list[tuple[int, str, str]]:
    """A rules file's lines that say something: (its number, the line as written, the line without its
    comment, stripped). A comment begins with a `#` at the start of the line or after a blank: one inside a
    word is the word's (`lib/common.sh#load_config`)."""
    found = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = COMMENT.sub("", raw).strip()
        if line:
            found.append((number, raw, line))
    return found


def glob_regex(glob: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path: str, glob: str) -> bool:
    return glob_regex(glob).match(path) is not None


def first_match(path: str, globs: Sequence[str]) -> str | None:
    return next((g for g in globs if matches(path, g)), None)


def git(root: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SelectionError(f"git did not run: {error}") from error
    if done.returncode != 0:
        raise SelectionError(f"git {' '.join(args)} failed: {done.stderr.strip() or done.returncode}")
    return done.stdout


def merge_base(root: Path, ref: str) -> str:
    base = git(root, "merge-base", ref, "HEAD").strip()
    if not base:
        raise SelectionError(f"no merge-base of {ref} and HEAD")
    return base


def changed_files(root: Path, base: str) -> list[str]:
    """What differs from <base>, where this branch left the ref: committed since, staged, unstaged, and
    untracked (not ignored) files, since the tree on disk is what runs. A rename is its two paths."""
    names: set[str] = set()
    names.update(git(root, "diff", "--name-only", "--no-renames", "-z", base).split("\0"))
    names.update(git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    return sorted(n for n in names if n)


def tracked_files(root: Path) -> list[str]:
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    return sorted(n for n in names if n)


@dataclass(frozen=True)
class FileChange:
    """How a file changed since the base: its text there ("" if it was not), and the hunks of `git diff -U0`
    from there to the file on disk (tools/shell_library.py's parse_hunks)."""

    old: str
    hunks: tuple[tuple[int, int, int, int], ...]


def file_change(root: Path, base: str, path: str) -> FileChange:
    """<path> at <base> ("" when it was not there; git failing otherwise is a SelectionError), and the hunks
    since."""
    there = git(root, "ls-tree", "--name-only", base, "--", path).strip()
    old = git(root, "show", f"{base}:{path}") if there else ""
    diff = git(root, "diff", "-U0", "--no-renames", "--no-ext-diff", base, "--", path)
    return FileChange(old, shell_library.parse_hunks(diff))
