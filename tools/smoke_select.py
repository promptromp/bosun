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

1. it matches an `all` glob: the whole suite;
2. what it changed (below), and what depends on that, is matched against the sections: those whose `covers`
   match a file it reaches, and those whose own text uses a function it reaches. When it reaches code that
   every section runs (a shell library's top-level code, the suite's shared setup) or a file on the `all`
   list (bin/bosun), the whole suite;
3. it matches an `ignore` glob: nothing to run for it;
4. otherwise (no section reaches it, nothing says it is safe): the whole suite.

A file depends on another when its code refers to it. In Python that is an `import`, or a command it starts
(`["bosun", "ledger"`), or a data file it names in a string (`ROOT / "orchestrator" / "tasks"`: a path, a
folder, a glob). In shell it is the file's path or name (`lib/ledger.py`, `ledger.py`, a data folder
`$ROOT/orchestrator/plugin`), or a command it runs (`bosun-ledger`, `bosun ledger`, `"$BOSUN" ledger`).
Comment lines are not read, nor is prose. An import is followed all the way, a call one step (see Graph). The
scan is textual and loose, which only selects more.

The shell libraries every command sources (LIBRARIES: lib/common.sh, lib/agents.sh, lib/launch/*.sh,
lib/harnesses/*.sh, lib/platforms/*.sh) are read function by function (tools/shell_library.py). Each function
is a node of its own: what it names and runs, and the functions it calls, all the way, as imports are; a
file, another function or a section's text that calls it depends on it. A changed library maps each changed
line (on both sides of the diff) to the function it is in, and a function whose code changed (not only its
comments) reaches what uses it. Top-level code: an assignment (`NAME=...`) reaches what reads `$NAME`; any
other line (and an `export`) runs the whole suite, as does a library that does not read as functions or a
line that cannot be placed. Code that calls a function by a name it builds (`"$fn"`, `eval`) may call any
function: a change to any reaches it. tests/smoke.sh is read the same way: a changed line in a section
selects the section and those that need it; in a helper (a function the suite defines), the sections that use
it; in the shared setup (outside the sections, or in an `always_run` part), the whole suite.

Run it as `smoke_select.py --root <repo> --script <tests/smoke.sh> --selection <file> --base <ref>`: the
report (which file selected which section) is on stdout, and its last line is the answer: `=> full`,
`=> none`, or `=> only <id>,<id>`. Plain python3 3.9 is enough (the pre-push hook runs it before anything
else is set up)."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import shell_library


SECTION_MARKER = re.compile(
    r"^\s*section\s+(?P<id>[a-z0-9-]+)\s+\"(?P<name>[^\"]*)\""
    r"(?:\s+needs\s+(?P<needs>[a-z0-9,-]+))?"
    r"(?:\s+covers\s+'(?P<covers>[^']*)')?\s*$"
)
ALWAYS_RUN = re.compile(r"^\s*always_run(?:\s|$)")
SUITE = "tests/smoke.sh"
# The shell function libraries, read function by function, each with the process that sources it: code that
# builds a function's name may call any function of its own process's libraries, but no other.
LIBRARIES = (
    ("lib/common.sh", "commands"),  # every command sources it
    ("lib/agents.sh", "commands"),  # lib/common.sh sources it
    ("lib/launch/*.sh", "commands"),  # `bosun launch` sources its agent's
    ("lib/harnesses/*.sh", "preflight"),  # lib/preflight.sh sources them, a plain sh of its own
    ("lib/platforms/*.sh", "preflight"),
)
SUITE_PROCESS = "suite"  # tests/smoke.sh's helpers: its own process sources no library
# Read only for the functions it calls outside its usage text, not the files and commands it names (all).
BIN = "bin/bosun"
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
# A string in Python (one line), and strings joined into a path: ROOT / "orchestrator" / "tasks".
PYTHON_STRING = re.compile(r"""(?P<q>["'])(?P<s>[^"'\n\\]*)(?P=q)""")
PYTHON_PATH = re.compile(r"""["'][^"'\n\\]*["'](?:\s*/\s*["'][^"'\n\\]*["'])+""")
URL = re.compile(
    r"\b[a-z][a-z0-9+.-]*://\S*"
)  # names no file of the repository: https://example.com/install.sh
TRIPLE_QUOTED = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'')  # docstrings, and texts for people
LABEL_NAMES = 6  # how many of a file's changed functions the report names
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
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


@dataclass(frozen=True)
class Edit:
    """What a change did to one file: its text at the base ("" if new), on disk ("" if gone), and the hunks of
    `git diff -U0` between them, (old start, old count, new start, new count)."""

    old: str
    new: str
    hunks: tuple[tuple[int, int, int, int], ...]


@dataclass
class Plan:
    """What the change reaches. `full` says why in `reason` (`unknown`: because nothing says what the file
    reaches, not because a rule says all); otherwise `picked` maps each directly selected section to the
    changed files that did it, `ignored` lists files that reach nothing, and `unchanged` the files whose code
    did not change (comments, blank lines)."""

    full: bool = False
    unknown: bool = False
    reason: str = ""
    picked: dict[str, list[str]] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    needed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Start:
    """Where a changed file's change begins in the graph: the nodes, a label for the report, or why it is the
    whole suite."""

    nodes: frozenset[str] = frozenset()
    label: str = ""
    full: str = ""


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


@cache
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


def library_process(path: str) -> str | None:
    """The process that sources the library <path> (LIBRARIES); None when it is no library."""
    return next((process for glob, process in LIBRARIES if matches(path, glob)), None)


def is_library(path: str) -> bool:
    return library_process(path) is not None


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
    """What differs from <base> (where this branch left the ref): committed since, staged, unstaged, and
    untracked (not ignored) files, since the tree on disk is what runs. A rename is its two paths."""
    names: set[str] = set()
    names.update(git(root, "diff", "--name-only", "--no-renames", "-z", base).split("\0"))
    names.update(git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    return sorted(n for n in names if n)


def tracked_files(root: Path) -> list[str]:
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    return sorted(n for n in names if n)


def parse_hunks(diff: str) -> tuple[tuple[int, int, int, int], ...]:
    """The hunks of a `git diff -U0`: (old start, old count, new start, new count); a count left out is 1."""
    return tuple((int(m[1]), hunk_count(m[2]), int(m[3]), hunk_count(m[4])) for m in HUNK.finditer(diff))


def hunk_count(text: str | None) -> int:
    return 1 if text is None else int(text)


def edit_of(root: Path, base: str, path: str) -> Edit:
    """What the change did to <path> (a library, the suite), line by line, from <base> to the tree on disk."""
    try:
        old = git(root, "show", f"{base}:{path}")
    except SelectionError:
        old = ""  # new since the base
    file = root / path
    new = file.read_text(encoding="utf-8", errors="replace") if file.is_file() else ""
    hunks = parse_hunks(git(root, "diff", "-U0", "--no-renames", "--no-ext-diff", base, "--", path))
    if not hunks and old != new:  # an untracked file: git diff does not show it
        hunks = ((0, 0, 1, len(new.split("\n"))),)
    return Edit(old, new, hunks)


def is_prose(path: str) -> bool:
    return path.startswith("docs/") or ("/" not in path and path.endswith(".md"))


def is_source(path: str) -> bool:
    return path.startswith(SOURCE_DIRS) or path in SOURCE_FILES


def is_data(path: str) -> bool:
    """A file code reads (a template, a prompt, a fixture), not code itself, and not prose."""
    return not is_source(path) and not is_prose(path)


def code_lines(text: str) -> str:
    """The text without comment lines (`#` at the start of a line, after blanks)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def is_python(path: str, text: str) -> bool:
    return path.endswith(".py") or text.startswith(("#!/usr/bin/env python", "#!/usr/bin/python"))


def fn(name: str) -> str:
    return f"fn:{name}"


def var(name: str) -> str:
    return f"var:{name}"


def helper(name: str) -> str:
    return f"helper:{name}"


def section_node(sid: str) -> str:
    return f"section:{sid}"


HARNESS = "suite:harness"  # the suite's code outside its sections and helpers: every run runs it
NODE_KINDS = ("fn:", "var:", "helper:", "section:", "suite:", "top:")


def is_file_node(node: str) -> bool:
    return not node.startswith(NODE_KINDS)


def statement_node(
    script: shell_library.Script, statement: shell_library.Statement, functions: Collection[str]
) -> str:
    """`var:<NAME>` for a top-level assignment, which only what reads NAME depends on; `top` for any other
    top-level code (and an export, which every program it starts reads, and an assignment that runs a
    library function as the library is sourced), which runs wherever the library is sourced."""
    if statement.assigns and not statement.exported:
        code = script.code_of(statement.first, statement.last)
        if not shell_library.calls(code, functions):
            return var(statement.assigns[0])
    return "top"


def section_spans(marked: Sequence[tuple[int, Section]], lines: Sequence[str]) -> dict[str, tuple[int, int]]:
    """Each section's first and last line: its marker, and the line before the next marker or `always_run`."""
    spans = {}
    for k, (first, section) in enumerate(marked):
        stop = marked[k + 1][0] if k + 1 < len(marked) else len(lines) + 1
        ends = [n for n in range(first + 1, stop) if ALWAYS_RUN.match(lines[n - 1])]
        spans[section.id] = (first, (ends[0] if ends else stop) - 1)
    return spans


class Suite:
    """tests/smoke.sh as the selection reads it: each line in a section (from its marker to the next marker
    or `always_run`), in a helper (a shell function the suite defines, wherever it stands), or in the shared
    setup (the rest: before the first section and in the `always_run` parts)."""

    def __init__(self, text: str) -> None:
        self.script = shell_library.read(text)
        marked = section_lines(text)
        self.sections = [section for _, section in marked]
        self.spans = section_spans(marked, self.script.lines)
        self.section_at = {n: sid for sid, (a, b) in self.spans.items() for n in range(a, b + 1)}
        self.helper_at = {n: f.name for f in self.script.functions for n in range(f.first, f.last + 1)}

    def region(self, line: int) -> str | None:
        """The node line <line> is in: `helper:<name>`, `section:<id>` or the shared setup (HARNESS); None for
        a line of the shared setup that holds no code (blank, a comment)."""
        if line in self.helper_at:
            return helper(self.helper_at[line])
        if line in self.section_at:
            return section_node(self.section_at[line])
        if self.script.code_lines[line - 1].strip():
            return HARNESS
        return None

    def node_lines(self) -> dict[str, list[int]]:
        """The lines of each node the suite's code makes: its sections, helpers and shared setup."""
        found: dict[str, list[int]] = {}
        for number in range(1, len(self.script.lines) + 1):
            node = self.region(number)
            if node is not None:
                found.setdefault(node, []).append(number)
        return found

    def shared(self, line: int) -> bool:
        """Whether line <line> is outside every section: in the shared setup, or a helper defined there."""
        return line not in self.section_at

    def dependents(self, sid: str) -> set[str]:
        """The sections that need <sid>, themselves or through others."""
        found = {sid}
        grew = True
        while grew:
            more = {s.id for s in self.sections if set(s.needs) & found} - found
            found |= more
            grew = bool(more)
        return found


class Graph:
    """Who refers to whom, over the files of the repository (and the changed ones, which may be gone), the
    functions of the shell libraries, their top-level variables, and the suite's sections and helpers.

    Two kinds of reference. An import is followed all the way: a module's change reaches every module that
    imports it, through the others; so is a function call, and the reading of a library's variable, which
    happen in the process that sourced the library. A call (a shell script or function naming a file or
    running a command, Python starting one or naming a data file) is followed one step only: a change to
    lib/ledger.py reaches libexec/bosun-ledger, which runs it, and so the sections that cover that command,
    but not the commands that run `bosun ledger` in their turn: what they do with its answer is theirs to
    cover. Following those too joins nearly every file to every other."""

    def __init__(
        self, root: Path, files: Sequence[str], suite: Suite | None = None, gone: Collection[str] = ()
    ) -> None:
        self.files = sorted(set(files))
        # Prose is never what code needs: a message that names docs/setup.md does not read it.
        self.fileset = frozenset(f for f in self.files if not is_prose(f))
        self.by_module: dict[str, str] = {}
        self.by_name: dict[str, list[str]] = {}
        self.commands: dict[str, str] = {}
        self.dirs: dict[str, list[str]] = {}  # a folder of data files -> the data files under it
        for path in sorted(self.fileset):
            self.index(path)
        self.importers: dict[str, set[str]] = {}  # a node -> the nodes that import (or call, read) it
        self.callers: dict[str, set[str]] = {}  # a file -> the nodes that run or read it
        self.everywhere: set[str] = set()  # code that runs wherever its library is sourced, or in every run
        # Code that calls a function by a name it builds -> the processes whose functions it may be calling.
        self.wild: dict[str, frozenset[str]] = {}
        self.defined: dict[str, set[str]] = {}  # a function -> the processes whose libraries define it
        self.unreadable: list[str] = []  # why a library does not read as functions
        self.suite = suite
        self.libraries = self.read_libraries(root)
        self.functions, self.helpers, self.variables = self.names(suite, gone)
        self.link_libraries()
        for path in self.files:
            if is_source(path) and not is_library(path) and (root / path).is_file():
                self.read_source(root, path)
        if suite is not None:
            self.link_suite(suite)

    def names(self, suite: Suite | None, gone: Collection[str]) -> tuple[set[str], set[str], set[str]]:
        """The libraries' functions (and the processes that define each), the suite's helpers, the libraries'
        top-level variables, with those the change took out."""
        for path, script in self.libraries.items():
            for function in script.functions:
                self.defined.setdefault(function.name, set()).add(library_process(path) or "")
        helpers = {f.name for f in suite.script.functions} if suite else set()
        assigned = {
            s.assigns[0] for script in self.libraries.values() for s in script.statements if s.assigns
        }
        return (
            set(self.defined) | names_in(gone, "fn:"),
            helpers | names_in(gone, "helper:"),
            assigned | names_in(gone, "var:"),
        )

    def index(self, path: str) -> None:
        name = path.rsplit("/", 1)[-1]
        self.by_name.setdefault(name, []).append(path)
        if path.startswith("lib/") and path.endswith(".py") and path.count("/") == 1:
            self.by_module[name[:-3]] = path
        if path.startswith("libexec/bosun-"):
            self.commands[name[len("bosun-") :]] = path
        if is_data(path):
            parts = path.split("/")
            for k in range(1, len(parts)):
                self.dirs.setdefault("/".join(parts[:k]), []).append(path)

    # --- reading -----------------------------------------------------------------------------------------

    def read_libraries(self, root: Path) -> dict[str, shell_library.Script]:
        found = {}
        for path in self.files:
            if is_library(path) and (root / path).is_file():
                script = shell_library.read((root / path).read_text(encoding="utf-8", errors="replace"))
                self.unreadable += [f"{path}: {problem}" for problem in script.problems]
                found[path] = script
        return found

    def link_libraries(self) -> None:
        for path, script in self.libraries.items():
            process = frozenset({library_process(path) or ""})
            for function in script.functions:
                lines = range(function.first, function.last + 1)
                self.link_block(fn(function.name), script, lines, process, own=function.name)
            for statement in script.statements:
                lines = range(statement.first, statement.last + 1)
                if statement_node(script, statement, self.functions) == "top":
                    self.everywhere.add(f"top:{path}")
                    self.link_block(f"top:{path}", script, lines, process)
                    continue
                for name in statement.assigns:
                    self.link_block(var(name), script, lines, process, own_variable=name)

    def link_block(
        self,
        node: str,
        script: shell_library.Script,
        lines: range,
        process: frozenset[str],
        own: str = "",
        own_variable: str = "",
    ) -> None:
        """The edges of a library's function or statement, and whether it calls a function by a built name."""
        self.link_shell(node, script.code_of(lines.start, lines.stop - 1), own=own, own_variable=own_variable)
        if script.computed(lines, self.functions):
            self.wild[node] = process

    def read_source(self, root: Path, path: str) -> None:
        """A file of code that is not a library: what it imports, runs, names and calls."""
        raw = (root / path).read_text(encoding="utf-8", errors="replace")
        if is_python(path, raw):
            self.read_python(path, raw)
            return
        script = shell_library.read(raw)
        code = code_lines(raw) if script.problems else script.undocumented if path == BIN else script.code
        self.link_shell(path, code, names_files=path != BIN)
        if script.problems or script.computed(range(1, len(script.lines) + 1), self.functions):
            # (one that does not read as shell is read as well as it can be) Which libraries it sources is not
            # read: it may call the functions of any.
            self.wild[path] = frozenset(process for _, process in LIBRARIES)

    def read_python(self, path: str, raw: str) -> None:
        text = code_lines(raw)
        for module in {self.by_module[m] for m in IMPORT.findall(text) if m in self.by_module} - {path}:
            self.importers.setdefault(module, set()).add(path)
        for target in self.python_calls(text) - {path}:
            self.callers.setdefault(target, set()).add(path)
        for target in self.python_data(text) - {path}:
            self.importers.setdefault(target, set()).add(path)
        if any(library.rsplit("/", 1)[-1] in text for library in self.libraries):  # it runs a library's code
            for name in shell_library.calls(text, self.functions):
                self.importers.setdefault(fn(name), set()).add(path)

    def link_shell(
        self,
        node: str,
        code: str,
        own: str = "",
        own_variable: str = "",
        names_files: bool = True,
    ) -> None:
        """The edges of shell code: the library functions it calls and the variables and data files it reads,
        in its own process (imports); and the code it names or runs, in another (calls)."""
        reads = self.importers
        for name in shell_library.calls(code, self.functions) - {own}:
            reads.setdefault(fn(name), set()).add(node)
        for name in self.variables - {own_variable}:
            if name in code and shell_library.mentions(code, name):
                reads.setdefault(var(name), set()).add(node)
        if not names_files:
            return
        paths, names = self.shell_names(code)
        for target in (paths | names) - {node}:
            if not is_library(target):  # a data file is read in the process, as code is imported
                (reads if is_data(target) else self.callers).setdefault(target, set()).add(node)

    def link_suite(self, suite: Suite) -> None:
        """The suite's sections, helpers and shared setup: the library functions and helpers they call and the
        variables they read; and the data files a section (or a helper of its own) names: what it runs is what
        its `covers` says. The shared setup can only call the helpers it defines (a section's are defined only
        in a run that runs it), and what it and they read is the suite's machinery, which the selection file
        lists."""
        nodes = suite.node_lines()
        shared = {h for h, lines in nodes.items() if h.startswith("helper:") and suite.shared(lines[0])}
        for node, lines in nodes.items():
            self.link_suite_node(node, lines, suite.script, shared)
        self.everywhere.add(HARNESS)

    def link_suite_node(
        self, node: str, lines: list[int], script: shell_library.Script, shared: set[str]
    ) -> None:
        code = "\n".join(script.code_lines[n - 1] for n in lines)
        own = node[len("helper:") :] if node.startswith("helper:") else ""
        machinery = node == HARNESS or node in shared  # the shared setup and its helpers
        self.link_shell(node, code, own=own, names_files=False)
        for name in shell_library.calls(code, self.helpers) - {own}:
            if node != HARNESS or helper(name) in shared:
                self.importers.setdefault(helper(name), set()).add(node)
        if not machinery:
            self.read_data(node, code)
        if script.computed(lines, self.helpers):
            self.wild[node] = frozenset({SUITE_PROCESS})  # (the suite's own process sources no library)

    def read_data(self, node: str, code: str) -> None:
        """The data files a section's code names by their paths: read where it runs, as code is imported."""
        for target in self.shell_names(code)[0]:
            if is_data(target):
                self.importers.setdefault(target, set()).add(node)

    def python_calls(self, text: str) -> set[str]:
        """The commands Python starts (`["bosun", "ledger"`). A docstring or a message that names a file or
        a command says nothing about what the code needs."""
        return {self.commands[m["py"]] for m in PYTHON_CALL.finditer(text) if m["py"] in self.commands}

    def python_data(self, text: str) -> set[str]:
        """The data files Python's strings name: by its path, a folder of them (`"orchestrator/tasks"`, or
        pieces joined with `/`: `ROOT / "orchestrator" / "tasks"`), or a glob (`"orchestrator/tasks/*.md"`,
        `f"{ROOT}/orchestrator/tasks/{name}.md"`). Not a docstring or a message (a string with a space), and
        not a string without a `/` (a file's bare name): too loose to count."""
        text = TRIPLE_QUOTED.sub("", text)
        strings = [m["s"] for m in PYTHON_STRING.finditer(text) if not re.search(r"\s", m["s"])]
        strings += [
            "/".join(m.strip("\"' ") for m in chain.group(0).split("/"))
            for chain in PYTHON_PATH.finditer(text)
        ]
        found: set[str] = set()
        for value in strings:
            found |= self.data_named(value)
        return found

    def data_named(self, value: str) -> set[str]:
        """The data files a path names (`{...}` in it: anything)."""
        path = path_pattern(value)
        if path is None:
            return set()
        if re.search(r"[*?[]", path):
            return {f for f in self.fileset if is_data(f) and matches(f, path)}
        if path in self.dirs:
            return set(self.dirs[path])
        return {path} if path in self.fileset and is_data(path) else set()

    def shell_calls(self, text: str) -> set[str]:
        """The files shell names and the commands it runs (shell_names, both kinds)."""
        paths, names = self.shell_names(text)
        return paths | names

    def shell_names(self, text: str) -> tuple[set[str], set[str]]:
        """What shell names: by its path (`$ROOT/lib/ledger.py`, a folder of data files with a `/` in its
        word: `$ROOT/orchestrator/plugin`) or the command it runs; and, loosely, code by its name after a
        folder this cannot read (`$dir/ledger.py`: every file of that name). A word without a folder is a
        message's (`run install.sh`): a run has a path (`"$BOSUN_ROOT/install.sh"`). A data file's bare name
        is not counted either: those are the names of the person's own files (`$BOSUN_CONFIG_DIR/bosun.env`),
        not of the repository's."""
        paths: set[str] = set()
        names: set[str] = set()
        for token in set(re.findall(r"\w[\w./-]*", URL.sub(" ", text))):
            parts = token.split("/")
            if len(parts) > 1:  # (a word without a folder: a message's)
                paths |= self.path_named(parts)
                if "." in parts[-1]:
                    names.update(f for f in self.by_name.get(parts[-1], ()) if not is_data(f))
        for match in SHELL_CALL.finditer(text):
            command = match["dash"] or match["var"] or match["shell"]
            if command in self.commands:
                paths.add(self.commands[command])
        return paths, names - paths

    def path_named(self, parts: list[str]) -> set[str]:
        """What a word with a folder in it names: a file (`$ROOT/lib/ledger.py` is lib/ledger.py), or the
        data files of a folder (`$ROOT/orchestrator/plugin`)."""
        found: set[str] = set()
        for i in range(len(parts)):
            tail = "/".join(parts[i:])
            if tail in self.fileset:
                found.add(tail)
            elif tail in self.dirs:
                found.update(self.dirs[tail])
        return found

    # --- reaching ----------------------------------------------------------------------------------------

    def importing(self, start: Iterable[str]) -> set[str]:
        """<start> and every node that imports, calls or reads one of them, through the others. Code that
        calls a function by a name it builds may be calling any of its process's."""
        seen = set(start)
        queue = list(seen)
        while queue:
            node = queue.pop()
            more = self.importers.get(node, set()) | self.wild_callers(node)
            for importer in more - seen:
                seen.add(importer)
                queue.append(importer)
        return seen

    def wild_callers(self, node: str) -> set[str]:
        """The code that may call the function <node> by a name it builds: in a process that defines it (any,
        for a function the change took out)."""
        if node.startswith("helper:"):
            processes = {SUITE_PROCESS}
        elif node.startswith("fn:"):
            processes = self.defined.get(node[len("fn:") :]) or {process for _, process in LIBRARIES}
        else:
            return set()
        return {caller for caller, theirs in self.wild.items() if theirs & processes}

    def affected(self, start: Iterable[str]) -> frozenset[str]:
        """<start>, what imports it, the nodes that run or read any of those (one step), and what imports
        those."""
        modules = self.importing(start)
        callers = {caller for module in modules for caller in self.callers.get(module, ())}
        return frozenset(self.importing(modules | callers))


def path_pattern(value: str) -> str | None:
    """A path in a string as a glob, `{...}` in it anything, from its first written-out folder on: None for
    one without a `/`, or with no folder written out (`{prefix}{scope}/`)."""
    if "/" not in value:
        return None
    parts = [p for p in re.sub(r"\{[^}]*\}", "*", value).split("/") if p]
    while parts and not parts[0].strip("*"):  # `{ROOT}/...`: from what follows the placeholder
        parts.pop(0)
    return "/".join(parts) if parts and not re.search(r"[*?[]", parts[0]) else None


def names_in(nodes: Collection[str], kind: str) -> set[str]:
    return {node[len(kind) :] for node in nodes if node.startswith(kind)}


@cache
def covering(path: str, sections: tuple[Section, ...]) -> tuple[str, ...]:
    """The sections whose `covers` match <path>."""
    return tuple(s.id for s in sections if any(matches(path, g) for g in s.covers))


@dataclass(frozen=True)
class Reach:
    """What a changed file reaches: the sections, or why that is the whole suite (`full`), or that it is code
    nothing that runs uses (`unused`: a function no file, section or helper calls)."""

    sections: tuple[str, ...] = ()
    full: str = ""
    unused: bool = False


def reached(start: Start, sections: tuple[Section, ...], rules: Rules, graph: Graph) -> Reach:
    """The sections what <start> changed reaches, or why that is the whole suite."""
    if start.full:
        return Reach(full=start.full)
    hit = graph.affected(start.nodes)
    files = {f for f in hit if is_file_node(f)}
    why = everything_reason(start, hit, files, rules, graph)
    if why:
        return Reach(full=why)
    ids = sections_hit(hit, files, sections)
    return Reach(tuple(s.id for s in sections if s.id in ids), unused=not files and not ids)


def sections_hit(hit: frozenset[str], files: set[str], sections: tuple[Section, ...]) -> set[str]:
    """The sections whose `covers` match a file <hit> holds, and those whose own code it holds."""
    ids = {sid for f in files for sid in covering(f, sections)}
    return ids | {node[len("section:") :] for node in hit if node.startswith("section:")}


def everything_reason(start: Start, hit: frozenset[str], files: set[str], rules: Rules, graph: Graph) -> str:
    """Why what <start> changed is the whole suite: it reaches code every section runs (a library's top-level
    code, the suite's shared setup) or a file on the select-everything list; "" if it does not."""
    everywhere = sorted(hit & graph.everywhere)
    if everywhere:
        return f"{start.label} reaches {where(everywhere[0])}"
    listed = sorted(f for f in files - start.nodes if first_match(f, rules.everything))
    return f"{start.label} reaches {listed[0]}, which is in the select-everything list" if listed else ""


def where(node: str) -> str:
    if node == HARNESS:
        return "the suite's shared setup (tests/smoke.sh outside its sections), which every run runs"
    return f"the top-level code of {node[len('top:') :]}, which runs wherever it is sourced"


def start_for(path: str, graph: Graph, edits: Mapping[str, Edit]) -> Start:
    """Where <path>'s change begins: a library's changed functions and variables, the suite's changed sections
    and helpers, or the file itself."""
    if is_library(path):
        return library_start(path, edits.get(path) or whole_file(graph.libraries.get(path)), graph)
    if path == SUITE:
        edit = edits.get(path)
        if edit is None or graph.suite is None:
            return Start(full=f"{SUITE} changed, and how cannot be read")
        return suite_start(edit, graph, graph.suite)
    return Start(frozenset({path}), path)


def whole_file(script: shell_library.Script | None) -> Edit:
    """A library changed in full (when no diff says how: what the guard of the selection asks)."""
    text = "\n".join(script.lines) if script else ""
    return Edit("", text, ((0, 0, 1, len(text.split("\n"))),))


def library_start(path: str, edit: Edit, graph: Graph) -> Start:
    """The functions and variables of a library whose code a change changed (on either side of the diff)."""
    old, new = shell_library.read(edit.old), shell_library.read(edit.new)
    problems = [*old.problems, *new.problems]
    if problems:
        return Start(full=f"{path} does not read as functions: {problems[0]}")
    names = graph.functions
    nodes: set[str] = set()
    for side, lines, other in ((new, new_lines(edit), old), (old, old_lines(edit), new)):
        kept = keys(other, names)
        for number in lines:
            if number > len(side.lines):
                return Start(full=f"{path}: a changed line ({number}) that cannot be placed")
            block = side.at(number)
            if block is None or key(block, side, names) in kept:  # a comment, or a block as it was
                continue
            if node_of(block, side, names) == "top":
                return Start(
                    full=f"{path}, line {block.first}: top-level code that is not an assignment, which "
                    "runs wherever it is sourced"
                )
            nodes.update(block_nodes(block))
    return Start(frozenset(nodes), labelled(path, nodes))


def labelled(path: str, nodes: Collection[str]) -> str:
    """<path> with what in it changed: `lib/common.sh (window_specs, BOSUN_CMD)`, the first few of them."""
    names = sorted({n.split(":", 1)[1] for n in nodes})
    if not names:
        return path
    more = f" and {len(names) - LABEL_NAMES} more" if len(names) > LABEL_NAMES else ""
    return f"{path} ({', '.join(names[:LABEL_NAMES])}{more})"


def new_lines(edit: Edit) -> list[int]:
    return [n for _, _, start, count in edit.hunks for n in range(start, start + count)]


def old_lines(edit: Edit) -> list[int]:
    return [n for start, count, _, _ in edit.hunks for n in range(start, start + count)]


def node_of(
    block: shell_library.Function | shell_library.Statement,
    script: shell_library.Script,
    names: Collection[str],
) -> str:
    if isinstance(block, shell_library.Function):
        return fn(block.name)
    return statement_node(script, block, names)


def block_nodes(block: shell_library.Function | shell_library.Statement) -> set[str]:
    if isinstance(block, shell_library.Statement):
        return {var(name) for name in block.assigns}
    return {fn(block.name)}


def key(
    block: shell_library.Function | shell_library.Statement,
    script: shell_library.Script,
    names: Collection[str],
) -> tuple[str, str]:
    """What a block is and its code, comments and spacing aside: equal on both sides of a diff when only its
    comments changed, or it moved."""
    return node_of(block, script, names), " ".join(script.code_of(block.first, block.last).split())


def keys(script: shell_library.Script, names: Collection[str]) -> set[tuple[str, str]]:
    return {key(block, script, names) for block in script.blocks}


def suite_start(edit: Edit, graph: Graph, new: Suite) -> Start:
    """The sections and helpers of tests/smoke.sh that a change changed, and the sections that need a section
    whose code changed (its own, or a helper's it uses): what it leaves for them may have changed too."""
    old = Suite(edit.old) if edit.old else None
    problems = [*new.script.problems, *(old.script.problems if old else ())]
    if problems:
        return Start(full=f"{SUITE} does not read as functions: {problems[0]}")
    nodes: set[str] = set()
    sides = [(new, new_lines(edit))] + ([(old, old_lines(edit))] if old is not None else [])
    for suite, lines in sides:
        found = suite_nodes(suite, lines, new)
        if isinstance(found, str):
            return Start(full=found)
        nodes |= found
    return Start(frozenset(nodes | needing_changed(nodes, graph, new)), labelled(SUITE, nodes))


def needing_changed(nodes: set[str], graph: Graph, suite: Suite) -> set[str]:
    """The sections that need a section whose code <nodes> changed (its own, or a helper's it uses)."""
    changed = [n[len("section:") :] for n in graph.importing(nodes) if n.startswith("section:")]
    return {section_node(s) for sid in changed for s in suite.dependents(sid)}


def suite_nodes(suite: Suite, lines: Iterable[int], new: Suite) -> set[str] | str:
    """The sections and helpers of <suite> (either side of the diff) that <lines> are in, those still in
    <new>; or why that is the whole suite (a line of its shared setup)."""
    nodes: set[str] = set()
    for number in lines:
        node = suite.region(number) if number <= len(suite.script.lines) else HARNESS
        if node == HARNESS:
            return f"{SUITE}, line {number}: the suite's shared setup, which every run runs"
        if node is not None and (not node.startswith("section:") or node[len("section:") :] in new.spans):
            nodes.add(node)
    return nodes


def gone_names(edits: Mapping[str, Edit]) -> set[str]:
    """The functions, variables and helpers a change took out (on the base side only): the code that still
    names them depends on them."""
    found: set[str] = set()
    for path, edit in edits.items():
        old, new = shell_library.read(edit.old), shell_library.read(edit.new)
        kind = "helper:" if path == SUITE else "fn:"
        found |= {kind + f.name for f in old.functions} - {kind + f.name for f in new.functions}
        if path != SUITE:
            found |= {var(n) for s in old.statements for n in s.assigns} - {
                var(n) for s in new.statements for n in s.assigns
            }
    return found


def plan_for(
    changed: Sequence[str],
    sections: Sequence[Section],
    rules: Rules,
    graph: Graph,
    edits: Mapping[str, Edit] | None = None,
) -> Plan:
    if graph.unreadable:
        return Plan(full=True, reason=f"a shell library does not read as functions: {graph.unreadable[0]}")
    plan = Plan()
    ordered = tuple(sections)
    for path in changed:
        whole = place(path, ordered, rules, graph, edits or {}, plan)
        if whole is not None:
            return whole
    plan.needed = needed_sections(sections, plan.picked)
    return plan


def place(
    path: str,
    sections: tuple[Section, ...],
    rules: Rules,
    graph: Graph,
    edits: Mapping[str, Edit],
    plan: Plan,
) -> Plan | None:
    """<path>'s part in <plan>: the sections it picks, or ignored, or unchanged; or the whole suite's plan."""
    glob = first_match(path, rules.everything)
    if glob is not None:
        return Plan(full=True, reason=f"{path} is in the select-everything list ({glob})")
    start = start_for(path, graph, edits)
    reach = reached(start, sections, rules, graph)
    if reach.full:
        return Plan(full=True, reason=reach.full)
    if not start.nodes or reach.unused:  # comments only, or code that nothing that runs uses
        plan.unchanged.append(start.label)
        return None
    for section in reach.sections:  # (the catch-all too: its check of the file is cheap)
        plan.picked.setdefault(section, []).append(start.label)
    if any(section != CATCH_ALL for section in reach.sections):
        return None
    if first_match(path, rules.ignore) is None:
        return Plan(full=True, unknown=True, reason=f"no section covers {start.label}: {WHOLE_SUITE_HINT}")
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
        f"  (no section): {label}: no code that runs changed (comments, blank lines, or code nothing calls)"
        for label in plan.unchanged
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
        edits = {p: edit_of(root, since, p) for p in changed if is_library(p) or p == SUITE}
        graph = Graph(root, [*tracked_files(root), *changed], Suite(text), gone_names(edits))
        return report(plan_for(changed, sections, rules, graph, edits), changed)
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
