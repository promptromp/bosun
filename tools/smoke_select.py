"""Which sections of the smoke suite a change can reach (`tests/smoke.sh --changed <ref>`, the pre-push hook).

The suite (tests/smoke.sh) is in sections. Each `section` line may end with `covers '<glob>,<glob>'`: the
files the section runs or reads (a command, the library modules it exercises, a template, a fixture). The
files that select every section, and the ones that select none, are in tests/smoke_selection.txt:

    all <glob>       a change to this runs the whole suite
    ignore <glob>    a change to this reaches no section (the docs, which other hooks check)

Globs are matched against the path from the repository root, whole: `*` is within one folder, `**` any
number of them, `?` one character.

The selection fails closed: whatever is not known to be reachable runs the whole suite. For a changed file,
in this order:

1. it matches an `all` glob, or what lib/common.sh (which every command sources) names depends on it: the
   whole suite;
2. it is tests/smoke.sh itself: the sections its changed lines are in (see Suite);
3. it, and the files that depend on it (below), are matched against the `covers` of the sections: those
   sections;
4. it matches an `ignore` glob: nothing to run for it;
5. otherwise (no section covers it, nothing says it is safe): the whole suite.

tests/smoke.sh is read with tools/shell_library.py, line by line on both sides of the diff (what it is now,
and what the change took out): a changed line in a section selects that section and the sections that need
it (they read what it leaves); in a helper (a function the suite defines), the sections that call it, through
other helpers, and those that need them; in the shared setup (before the first section, or an `always_run`
part), or in a helper the shared setup calls, the whole suite. A line of the shared setup that holds no code
(a comment, a blank line) on both sides runs nothing; its first line, the `#!`, is code. A suite that does
not read as functions, or a line that cannot be placed, runs the whole suite.

A file depends on another when its code refers to it. In Python that is an `import`, or a command it starts
(`["bosun", "ledger"`). In shell it is the file's path or name (`lib/ledger.py`, `ledger.py`), or a command it
runs (`bosun-ledger`, `bosun ledger`, `"$BOSUN" ledger`). Comment lines are not read, nor is prose. An
import is followed all the way, a call one step (see Graph). The scan is textual and loose, which only
selects more.

Run it as `smoke_select.py --root <repo> --script <tests/smoke.sh> --selection <file> --base <ref>`: the
report (which file selected which section) is on stdout, and its last line is the answer: `=> full`,
`=> none`, or `=> only <id>,<id>`. Plain python3 3.9 is enough (the pre-push hook runs it before anything
else is set up)."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import shell_library


SECTION_MARKER = re.compile(
    r"^\s*section\s+(?P<id>[a-z0-9-]+)\s+\"(?P<name>[^\"]*)\""
    r"(?:\s+needs\s+(?P<needs>[a-z0-9,-]+))?"
    r"(?:\s+covers\s+'(?P<covers>[^']*)')?\s*$"
)
COMMON = "lib/common.sh"  # sourced by every command: what it names under lib/ runs in every section
SUITE = "tests/smoke.sh"  # read section by section (Suite)
ALWAYS_RUN = re.compile(r"^\s*always_run(?:\s|$)")
HARNESS = "the shared setup"  # the suite's code outside its sections and helpers: every run runs it
BIN = "bin/bosun"  # not read for what it refers to: it names every command
CATCH_ALL = (
    "syntax"  # the section whose `covers` take in all of lib/ and libexec/: it never counts as coverage
)
# What a file's code can depend on: only these are read for references.
SOURCE_DIRS = ("lib/", "libexec/", "bin/")
SOURCE_FILES = ("install.sh", "uninstall.sh", "get.sh")

IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", re.MULTILINE)
# A command called, not talked about: `bosun-ledger` (its file); `"$BOSUN" ledger` (a variable that names
# bosun, then a command, wherever it stands: also in a string that builds a command line, such as
# `"$(printf %q "$BOSUN_CMD") guard"`); or `bosun ledger` where a shell command starts. A message that says
# "run `bosun doctor`" is not a call.
SHELL_CALL = re.compile(
    r"\bbosun-(?P<dash>[a-z][a-z0-9-]*)"
    r"""|"?\$\{?BOSUN\w*\}?"?\)?"?\s+(?P<var>[a-z][a-z0-9-]*)"""
    r"|(?:^|[;&|(`{!]|\$\(|\bexec\s|\bthen\s|\bdo\s)\s*bosun\s+(?P<shell>[a-z][a-z0-9-]*)",
    re.MULTILINE,
)
# and in Python an argument list that starts with it: ["bosun", "ledger", ...], [BOSUN_CMD, "send"
PYTHON_CALL = re.compile(r"""\b\w*bosun\w*["']?\s*,\s*["'](?P<py>[a-z][a-z0-9-]*)["']""", re.IGNORECASE)
WHOLE_SUITE_HINT = (
    "add it to a section's `covers '...'` in tests/smoke.sh, or to tests/smoke_selection.txt "
    "(`ignore` if no section can reach it, `all` if unsure)"
)


class SelectionError(Exception):
    """Git or the files said something that means the whole suite should run."""


@dataclass(frozen=True)
class Section:
    id: str
    name: str
    needs: tuple[str, ...]
    covers: tuple[str, ...]


@dataclass(frozen=True)
class Rules:
    everything: tuple[str, ...]
    ignore: tuple[str, ...]


@dataclass
class Plan:
    """What the change reaches. `full` says why in `reason` (`unknown`: because nothing says what the file
    reaches, not because a rule says all); otherwise `picked` maps each directly selected section to the
    changed files that did it, and `ignored` lists files that reach nothing."""

    full: bool = False
    unknown: bool = False
    reason: str = ""
    picked: dict[str, list[str]] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    needed: list[str] = field(default_factory=list)


def parse_sections(text: str) -> list[Section]:
    return [section for _, section in section_lines(text)]


def section_lines(text: str) -> list[tuple[int, Section]]:
    """Each section's marker line (from 1) and the section it begins."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        match = SECTION_MARKER.match(line) if re.match(r"^\s*section\s", line) else None
        if match is None:
            # `section() {`; a section line that does not parse is caught by test_smoke_sections.py
            continue
        needs = tuple(match["needs"].split(",")) if match["needs"] else ()
        covers = tuple(g.strip() for g in (match["covers"] or "").split(",") if g.strip())
        found.append((number, Section(match["id"], match["name"], needs, covers)))
    return found


def parse_rules(text: str) -> Rules:
    everything: list[str] = []
    ignore: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        kind, _, glob = line.partition(" ")
        glob = glob.strip()
        if kind not in ("all", "ignore") or not glob:
            raise SelectionError(f"selection file, line {number}: want all|ignore <glob>, not: {raw}")
        (everything if kind == "all" else ignore).append(glob)
    return Rules(tuple(everything), tuple(ignore))


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


@dataclass(frozen=True)
class SuiteChange:
    """How tests/smoke.sh changed since the base: its text there ("" if it was not), and the hunks of
    `git diff -U0` from there to the file on disk."""

    old: str
    hunks: tuple[tuple[int, int, int, int], ...]


def suite_change(root: Path, base: str) -> SuiteChange:
    """tests/smoke.sh at <base> (none when it was not there; git failing otherwise is an error: the whole
    suite), and the hunks since."""
    there = git(root, "ls-tree", "--name-only", base, "--", SUITE).strip()
    old = git(root, "show", f"{base}:{SUITE}") if there else ""
    diff = git(root, "diff", "-U0", "--no-renames", "--no-ext-diff", base, "--", SUITE)
    return SuiteChange(old, shell_library.parse_hunks(diff))


def tracked_files(root: Path) -> list[str]:
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    return sorted(n for n in names if n)


def is_prose(path: str) -> bool:
    return path.startswith("docs/") or ("/" not in path and path.endswith(".md"))


def is_source(path: str) -> bool:
    return path.startswith(SOURCE_DIRS) or path in SOURCE_FILES


def code_lines(text: str) -> str:
    """The text without comment lines (`#` at the start of a line, after blanks)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def is_python(path: str, text: str) -> bool:
    return path.endswith(".py") or text.startswith(("#!/usr/bin/env python", "#!/usr/bin/python"))


class Graph:
    """Who refers to whom, over the files of the repository (and the changed ones, which may be gone).

    Two kinds of reference. An import is followed all the way: a module's change reaches every module that
    imports it, through the others. A call (a shell script naming a file or running a command, Python
    starting one) is followed one step only: a change to lib/ledger.py reaches libexec/bosun-ledger, which
    runs it, and so the sections that cover that command, but not the commands that run `bosun ledger` in
    their turn: what they do with its answer is theirs to cover. Following those too joins nearly every
    file to every other."""

    def __init__(self, root: Path, files: Sequence[str]):
        self.files = sorted(set(files))
        # Prose is never what code needs: a message that names docs/setup.md does not read it.
        self.fileset = frozenset(f for f in self.files if not is_prose(f))
        self.by_module: dict[str, str] = {}
        self.by_name: dict[str, list[str]] = {}
        self.commands: dict[str, str] = {}
        for path in self.fileset:
            self.index(path)
        self.importers: dict[str, set[str]] = {}  # a file -> the modules that import it
        self.callers: dict[str, set[str]] = {}  # a file -> the files that run or read it
        self.common_refers: set[str] = set()  # what lib/common.sh names under lib/
        self.common_commands: set[str] = set()  # the commands its code runs ("$BOSUN_CMD" launch ...)
        for path in self.files:
            if is_source(path) and path != BIN and (root / path).is_file():
                self.record(path, *self.references(root, path))

    def index(self, path: str) -> None:
        name = path.rsplit("/", 1)[-1]
        self.by_name.setdefault(name, []).append(path)
        if path.startswith("lib/") and path.endswith(".py") and path.count("/") == 1:
            self.by_module[name[:-3]] = path
        if path.startswith("libexec/bosun-"):
            self.commands[name[len("bosun-") :]] = path

    def record(self, path: str, imports: set[str], calls: set[str]) -> None:
        if path == COMMON:  # what it names runs in every section, not only where a command names it
            self.common_refers = {t for t in imports | calls if t.startswith("lib/")}
            self.common_commands = set(calls) & set(self.commands.values())
            return
        for target in imports:
            self.importers.setdefault(target, set()).add(path)
        for target in calls:
            self.callers.setdefault(target, set()).add(path)

    def references(self, root: Path, path: str) -> tuple[set[str], set[str]]:
        """What <path> imports, and what it runs or reads: its code, not the comments."""
        try:
            raw = (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set(), set()
        text = code_lines(raw)
        if is_python(path, raw):
            imports = {self.by_module[m] for m in IMPORT.findall(text) if m in self.by_module}
            calls = self.python_calls(text)
        else:
            imports, calls = set(), self.shell_calls(text)
        return imports - {path}, calls - {path}

    def python_calls(self, text: str) -> set[str]:
        """The commands Python starts (`["bosun", "ledger"`). A docstring or a message that names a file or
        a command says nothing about what the code needs."""
        return {self.commands[m["py"]] for m in PYTHON_CALL.finditer(text) if m["py"] in self.commands}

    def shell_calls(self, text: str) -> set[str]:
        """The files shell names (`$ROOT/lib/ledger.py`, or a bare `ledger.py`) and the commands it runs."""
        found: set[str] = set()
        for token in set(re.findall(r"\w[\w./-]*", text)):
            parts = token.split("/")
            for i in range(len(parts)):  # `$ROOT/lib/ledger.py` is read as lib/ledger.py
                if "/".join(parts[i:]) in self.fileset:
                    found.add("/".join(parts[i:]))
            if "." in parts[-1]:  # and a bare `ledger.py` as every file of that name
                found.update(self.by_name.get(parts[-1], ()))
        for match in SHELL_CALL.finditer(text):
            command = match["dash"] or match["var"] or match["shell"]
            if command in self.commands:
                found.add(self.commands[command])
        return found

    def importing(self, start: set[str]) -> set[str]:
        """<start> and every module that imports one of them, through the others."""
        seen = set(start)
        queue = list(start)
        while queue:
            for importer in self.importers.get(queue.pop(), ()):
                if importer not in seen:
                    seen.add(importer)
                    queue.append(importer)
        return seen

    def affected(self, path: str) -> frozenset[str]:
        """<path>, the modules that import it, the files that run or read any of those (one step), and the
        modules that import those."""
        modules = self.importing({path})
        callers = {caller for module in modules for caller in self.callers.get(module, ())}
        return frozenset(self.importing(modules | callers))


class Suite:
    """tests/smoke.sh as the selection reads it: each line in a section (from its marker to the next marker
    or `always_run`), in a helper (a shell function the suite defines, wherever it stands), or in the shared
    setup (the rest). The shared setup can only call the helpers it defines: a section's are defined only in
    a run that runs it."""

    def __init__(self, text: str) -> None:
        self.script = shell_library.read(text)
        marked = section_lines(text)
        self.sections = [section for _, section in marked]
        self.section_at: dict[int, str] = {}
        for k, (first, section) in enumerate(marked):
            stop = marked[k + 1][0] if k + 1 < len(marked) else len(self.script.lines) + 1
            ends = [n for n in range(first + 1, stop) if ALWAYS_RUN.match(self.script.lines[n - 1])]
            self.section_at.update(dict.fromkeys(range(first, ends[0] if ends else stop), section.id))
        self.helper_at = {n: f.name for f in self.script.functions for n in range(f.first, f.last + 1)}
        self.callers = self.helper_callers()

    def region(self, line: int) -> tuple[str, str] | None:
        """("helper", name), ("section", id) or (HARNESS, "") for line <line>; None for a line of the shared
        setup that holds no code (a comment, a blank line; not the `#!` of line 1)."""
        if line in self.helper_at:
            return "helper", self.helper_at[line]
        if line in self.section_at:
            return "section", self.section_at[line]
        if line == 1 or self.script.code_lines[line - 1].strip():
            return HARNESS, ""
        return None

    def helper_callers(self) -> dict[str, set[tuple[str, str]]]:
        """Each helper -> the code that calls it: sections, other helpers, the shared setup (its own only)."""
        lines: dict[tuple[str, str], list[int]] = {}
        for number in range(1, len(self.script.lines) + 1):
            region = self.region(number)
            if region is not None:
                lines.setdefault(region, []).append(number)
        helpers = set(self.helper_at.values())
        shared = {name for n, name in self.helper_at.items() if n not in self.section_at}
        callers: dict[str, set[tuple[str, str]]] = {}
        for node, numbers in lines.items():
            code = "\n".join(self.script.code_lines[n - 1] for n in numbers)
            for name in shell_library.calls(code, helpers if node[0] != HARNESS else shared) - {node[1]}:
                callers.setdefault(name, set()).add(node)
        return callers

    def users(self, helper: str) -> set[tuple[str, str]]:
        """The sections, and the shared setup, that call <helper>, themselves or through other helpers."""
        seen: set[tuple[str, str]] = set()
        queue = [helper]
        while queue:
            for caller in self.callers.get(queue.pop(), set()) - seen:
                seen.add(caller)
                if caller[0] == "helper":
                    queue.append(caller[1])
        return {node for node in seen if node[0] != "helper"}

    def dependents(self, ids: Iterable[str]) -> set[str]:
        """<ids> and the sections that need any of them, themselves or through others."""
        found = set(ids)
        grew = True
        while grew:
            more = {s.id for s in self.sections if set(s.needs) & found} - found
            found |= more
            grew = bool(more)
        return found

    def reached(self, lines: Iterable[int]) -> set[str] | str:
        """The sections of this text <lines> reach (not yet those that need them); or why that is the whole
        suite."""
        if self.script.problems:
            return f"{SUITE} does not read as functions: {self.script.problems[0]}"
        ids: set[str] = set()
        for number in lines:
            found = self.reached_from(number)
            if isinstance(found, str):
                return found
            ids |= found
        return ids

    def reached_from(self, number: int) -> set[str] | str:
        """The sections line <number> reaches: its section, or those that call its helper; or why that is the
        whole suite."""
        if not 1 <= number <= len(self.script.lines):
            return f"{SUITE}: a changed line ({number}) that cannot be placed"
        region = self.region(number)
        if region is None:
            return set()
        nodes = self.users(region[1]) if region[0] == "helper" else {region}
        if any(kind == HARNESS for kind, _ in nodes):
            return f"{SUITE}, line {number}: the shared setup (or a helper it calls), which every run runs"
        return {sid for _, sid in nodes}


def suite_sections(suite: Suite | None, change: SuiteChange | None) -> set[str] | str:
    """The sections a change to tests/smoke.sh reaches, with those that need them; or why that is the whole
    suite (suite_reach says what it reaches)."""
    if suite is None or change is None or not change.hunks:
        return f"{SUITE} changed, and how cannot be read"
    if not change.old and any(count for _, count, _, _ in change.hunks):
        return f"{SUITE}: lines taken out of a file that was not there"
    return reached_in_suite(suite, change)


def reached_in_suite(suite: Suite, change: SuiteChange) -> set[str] | str:
    ids: set[str] = set()
    for found in suite_reach(Suite(change.old) if change.old else None, suite, change.hunks):
        if isinstance(found, str):
            return found
        ids |= found
    return suite.dependents(ids & {s.id for s in suite.sections})


def suite_reach(
    old: Suite | None, new: Suite, hunks: Sequence[tuple[int, int, int, int]]
) -> list[set[str] | str]:
    """What a change reaches: through the lines it put in (new side), through those it took out (old side;
    the sections still there count), and through the code it left alone but moved into another region (a
    marker added or taken out, a helper's `}` moved: both the old region and the new)."""
    if old is not None and old.script.problems:
        return [f"{SUITE} at the base does not read as functions: {old.script.problems[0]}"]
    found = [new.reached(shell_library.new_lines(hunks)), moved_regions(old, new, hunks)]
    if old is not None:
        found.append(old.reached(shell_library.old_lines(hunks)))
    return found


def moved_regions(
    old: Suite | None, new: Suite, hunks: Sequence[tuple[int, int, int, int]]
) -> set[str] | str:
    """The sections code the change left alone moved between: for each line whose region (a section, a helper,
    the shared setup) differs between the old file and the new, both regions; or why that is the whole suite
    (one of them the shared setup, or a side that does not read)."""
    if old is None:
        return set()
    ids: set[str] = set()
    for old_line, new_line in shell_library.unchanged_pairs(
        hunks, len(old.script.lines), len(new.script.lines)
    ):
        has_code = old.script.has_code(old_line) or new.script.has_code(new_line)
        if not has_code or old.region(old_line) == new.region(new_line):
            continue
        for suite, line in ((old, old_line), (new, new_line)):
            found = suite.reached_from(line)
            if isinstance(found, str):
                return found
            ids |= found
    return ids


def pick_suite(plan: Plan, sections: Sequence[Section], found: set[str]) -> None:
    """The sections tests/smoke.sh's change reaches, in file order, in <plan>; none: it changed no code."""
    for section in (s.id for s in sections if s.id in found):
        plan.picked.setdefault(section, []).append(SUITE)
    if not found:
        plan.unchanged.append(SUITE)


def whole_suite(path: str, graph: Graph, rules: Rules) -> Plan | None:
    """A full-run plan if <path> alone settles it: on the `all` list, or shared through lib/common.sh."""
    glob = first_match(path, rules.everything)
    if glob is not None:
        return Plan(full=True, reason=f"{path} is in the select-everything list ({glob})")
    if path in graph.common_commands:
        return Plan(
            full=True, reason=f"{path} is a command {COMMON} runs, in every section that starts a window"
        )
    shared = sorted(graph.common_refers & graph.affected(path))
    if shared:
        via = "" if shared[0] == path else f" (through {shared[0]})"
        return Plan(full=True, reason=f"{path} is used by {COMMON}{via}, which every section runs")
    return None


def reached_by(path: str, sections: Sequence[Section], graph: Graph) -> list[str]:
    """The sections whose `covers` match <path> or a file that depends on it."""
    affected = graph.affected(path)
    return [s.id for s in sections if any(matches(a, g) for a in affected for g in s.covers)]


def plan_for(
    changed: Sequence[str],
    sections: Sequence[Section],
    rules: Rules,
    graph: Graph,
    suite: Suite | None = None,
    change: SuiteChange | None = None,
) -> Plan:
    """The plan for <changed>; <suite> (as it is) and <change> say how tests/smoke.sh changed, when it did."""
    plan = Plan()
    in_suite = suite_sections(suite, change) if SUITE in changed else set()
    for path in changed:
        whole = whole_suite(path, graph, rules) or place(path, plan, sections, rules, graph, in_suite)
        if whole is not None:
            return whole
    plan.needed = needed_sections(sections, plan.picked)
    return plan


def place(
    path: str, plan: Plan, sections: Sequence[Section], rules: Rules, graph: Graph, in_suite: set[str] | str
) -> Plan | None:
    """<path>'s part in <plan>: the sections it picks, or ignored; or the whole suite's plan. <in_suite>: what
    tests/smoke.sh's change reaches, when it is <path> (suite_sections)."""
    if path == SUITE:
        if isinstance(in_suite, str):
            return Plan(full=True, reason=in_suite)
        pick_suite(plan, sections, in_suite)
        return None
    hit = reached_by(path, sections, graph)
    for section in hit:  # (the catch-all too: its check of the file is cheap)
        plan.picked.setdefault(section, []).append(path)
    if any(section != CATCH_ALL for section in hit):
        return None
    if first_match(path, rules.ignore) is None:
        return Plan(full=True, unknown=True, reason=f"no section covers {path}: {WHOLE_SUITE_HINT}")
    plan.ignored.append(path)
    return None


def needed_sections(sections: Sequence[Section], picked: dict[str, list[str]]) -> list[str]:
    """The sections the picked ones need, in file order, and what those need in turn."""
    needs = {s.id: s.needs for s in sections}
    want = set(picked)
    queue = list(picked)
    while queue:
        for need in needs.get(queue.pop(), ()):
            if need not in want:
                want.add(need)
                queue.append(need)
    return [s.id for s in sections if s.id in want and s.id not in picked]


def report(plan: Plan, changed: Sequence[str]) -> list[str]:
    lines = [f"smoke selection: {len(changed)} changed file(s)"]
    if plan.full:
        return [*lines, f"smoke selection: the whole suite: {plan.reason}", "=> full"]
    lines += [f"  {section}: {', '.join(files)}" for section, files in plan.picked.items()]
    lines += [f"  (no section): {path} is ignored" for path in plan.ignored]
    lines += [
        f"  (no section): {path}: no section it still has changed (only comments or blank lines outside "
        "them, or sections it took out)"
        for path in plan.unchanged
    ]
    if not plan.picked:
        return [*lines, "smoke selection: no change reaches a section, nothing to run", "=> none"]
    needed = f" (and, as they need them: {' '.join(plan.needed)})" if plan.needed else ""
    return [
        *lines,
        f"smoke selection: {len(plan.picked)} section(s){needed}",
        "=> only " + ",".join(plan.picked),
    ]


def select(root: Path, script: Path, selection: Path, base: str) -> list[str]:
    """The report for the change from <base>, ending in the answer. Whatever goes wrong is the whole suite."""
    try:
        text = script.read_text(encoding="utf-8")
        sections = parse_sections(text)
        rules = parse_rules(selection.read_text(encoding="utf-8"))
        if not sections:
            raise SelectionError(f"no sections found in {script}")
        since = merge_base(root, base)
        changed = changed_files(root, since)
        change = suite_change(root, since) if SUITE in changed else None
        graph = Graph(root, [*tracked_files(root), *changed])
        return report(plan_for(changed, sections, rules, graph, Suite(text), change), changed)
    except Exception as error:  # noqa: BLE001 - failing closed is the point: any trouble is the whole suite
        return [f"smoke selection: the whole suite: {error}", "=> full"]


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--base", required=True)
    args = parser.parse_args(argv)
    print("\n".join(select(args.root, args.script, args.selection, args.base)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
