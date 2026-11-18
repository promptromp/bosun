"""A shell script read as its functions: pure, text in, structure out.

`read(text)` gives a `Script`:

- its functions: each `name() {` (or `function name {`), at any indentation, that is not inside another
  function, with the lines it spans. A function ends on its first line when its braces close there, or at the
  first line after it that begins, outside any quote or here-document, with `}` at the indentation of its
  first line (shfmt's layout); every other line is top-level code (or a comment, a blank line);
- its code: the text with its comments blanked out, line for line;
- its problems: what keeps it from reading as that (a quote, a `$(`, a `case` or a here-document that does
  not close, a function with no end or whose braces do not balance). Whoever reads a script with a problem
  fails closed.

`changed_functions(new, hunks, old)` maps a diff's hunks (`parse_hunks` of a `git diff -U0` from <old> to
<new>) to the functions they changed, failing closed: None (the whole file) when a line a hunk put in or took
out is outside every function, cannot be placed, or a side has a problem, and when a line the diff left alone
is code that a change moved between functions or into or out of one (a `}` moved over top-level code: that
code no longer runs when the file is sourced). `unchanged_pairs(hunks, old_length, new_length)` is that walk:
each line the diff left alone, as (old line, new line). A changed line is a change: nothing is compared, but
with `prose_is_no_change` a blank line or a comment outside the functions, where a statement could begin (not
in a quote, a here-document or after a `\\` line), is none (tools/vm_select.py reads lib/common.sh so).

`calls(code, names)` is every word of the code that is one of the names, in strings too (`bash -c '...; f'`):
loose, so it can only find more calls than there are.

The scan knows bash's quoting (single, double, `$'...'`), `$(...)`, backquotes, `${...}`, `$((...))`,
here-documents, comments and `case` patterns. Plain python3 3.9 is enough, as for the tools that use it
(tools/smoke_select.py reads tests/smoke.sh's helpers with it).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from functools import cached_property


NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A word as a call: not part of a path, a file name, a variable's name or a longer word.
CALL_WORD = re.compile(r"(?<![\w./$-])([A-Za-z_][A-Za-z0-9_]*)(?![\w./-])")
DEFINITION = re.compile(
    r"^(?P<indent>[ \t]*)(?:function[ \t]+(?P<kw>[A-Za-z_][\w-]*)[ \t]*(?:\([ \t]*\))?"
    r"|(?P<name>[A-Za-z_][\w-]*)[ \t]*\([ \t]*\))[ \t]*(?P<body>[{(])"
)
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?\+?=")
# Words after which a command (or a reserved word, `fi }`) may still stand.
RESERVED = frozenset(
    {"then", "do", "else", "elif", "if", "while", "until", "!", "time", "{", "}", "fi", "done"}
)
RUN = re.compile(r"[^\s\\'\"`$()#<>;&|]+")  # ordinary characters of a word, in code
HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([A-Za-z0-9_.-]+))")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
WORDY = frozenset({"top", "subst", "paren", "array", "case", "backquote"})  # where words and commands are
WHAT = {
    "subst": "$(",
    "paren": "(",
    "array": "array",
    "case": "case",
    "backquote": "backquote",
    "dq": "double quote",
    "sq": "single quote",
    "ansi": "$'",
    "param": "${",
    "arith": "$((",
}


@dataclass(frozen=True)
class Function:
    name: str
    first: int  # the line of `name() {`, from 1
    last: int  # the line that closes it


@dataclass
class Script:
    lines: tuple[str, ...]
    code: str  # the text with its comments blanked out, line for line
    functions: tuple[Function, ...] = ()
    problems: tuple[str, ...] = ()
    starts: tuple[bool, ...] = ()  # per line: it begins outside every quote, here-document and `\` line

    @cached_property
    def code_lines(self) -> list[str]:
        return self.code.split("\n")

    @cached_property
    def by_line(self) -> dict[int, Function]:
        found: dict[int, Function] = {}
        for function in self.functions:
            found.update(dict.fromkeys(range(function.first, function.last + 1), function))
        return found

    def function_at(self, line: int) -> Function | None:
        """The function line <line> is in; None outside every function."""
        return self.by_line.get(line)

    def has_code(self, line: int) -> bool:
        """Whether line <line> holds code: not blank, not only a comment."""
        return 1 <= line <= len(self.lines) and bool(self.code_lines[line - 1].strip())

    def prose_at(self, line: int) -> bool:
        """Whether line <line> is prose where a statement could begin: blank or only a comment, and outside
        every quote and here-document, and not after a line that runs on with `\\` (there a blank line or a
        comment ends the command, and what follows runs as a command of its own)."""
        begins = 1 <= line <= len(self.starts) and self.starts[line - 1]
        return begins and not self.has_code(line)


def parse_hunks(diff: str) -> tuple[tuple[int, int, int, int], ...]:
    """The hunks of a `git diff -U0`: (old start, old count, new start, new count); a count left out is 1."""
    return tuple((int(m[1]), hunk_count(m[2]), int(m[3]), hunk_count(m[4])) for m in HUNK.finditer(diff))


def hunk_count(text: str | None) -> int:
    return 1 if text is None else int(text)


def new_lines(hunks: Iterable[tuple[int, int, int, int]]) -> list[int]:
    """The lines a diff's hunks put in the new file (none where a hunk only took lines out)."""
    return [n for _, _, start, count in hunks for n in range(start, start + count)]


def old_lines(hunks: Iterable[tuple[int, int, int, int]]) -> list[int]:
    """The lines a diff's hunks took out of the old file (none where a hunk only put lines in)."""
    return [n for start, count, _, _ in hunks for n in range(start, start + count)]


def unchanged_pairs(
    hunks: Iterable[tuple[int, int, int, int]], old_length: int, new_length: int
) -> list[tuple[int, int]]:
    """Each line a diff left alone, as (its line in the old file, in the new): the lines between its hunks.
    A hunk that only puts lines in comes after its old line (`-4,0`); one that only takes lines out, after its
    new line."""
    pairs: list[tuple[int, int]] = []
    old = new = 1
    for old_start, old_count, new_start, new_count in sorted(hunks):
        while old <= (old_start if old_count == 0 else old_start - 1):
            pairs.append((old, new))
            old, new = old + 1, new + 1
        old = old_start + (old_count or 1)
        new = new_start + (new_count or 1)
    while old <= old_length and new <= new_length:
        pairs.append((old, new))
        old, new = old + 1, new + 1
    return pairs


def changed_functions(
    new: Script, hunks: Iterable[tuple[int, int, int, int]], old: Script, prose_is_no_change: bool = False
) -> frozenset[str] | None:
    """The names of the functions <hunks> (from <old> to <new>) changed; None, the whole file, when a line
    they put in or took out is outside every function (top-level code, a comment, a blank line) or past a
    file's end, when a line they left alone is code that moved between functions or into or out of one, or
    when a side does not read as functions. With <prose_is_no_change>, a line outside every function that is
    prose where a statement could begin (Script.prose_at: a function added with the comment and blank line
    above it) changes nothing that runs, and is no change."""
    if new.problems or old.problems:
        return None
    hunks = list(hunks)
    names = functions_at(new, new_lines(hunks), prose_is_no_change)
    gone = functions_at(old, old_lines(hunks), prose_is_no_change)
    moved = moved_functions(new, hunks, old)
    if names is None or gone is None or moved is None:
        return None
    return frozenset(names | gone | moved)


def moved_functions(new: Script, hunks: list[tuple[int, int, int, int]], old: Script) -> set[str] | None:
    """The functions that code the diff left alone moved between; None when it moved into or out of one."""
    names: set[str] = set()
    for old_line, new_line in unchanged_pairs(hunks, len(old.lines), len(new.lines)):
        before, after = old.function_at(old_line), new.function_at(new_line)
        if not (old.has_code(old_line) or new.has_code(new_line)) or name_of(before) == name_of(after):
            continue
        if before is None or after is None:
            return None
        names |= {before.name, after.name}
    return names


def name_of(function: Function | None) -> str | None:
    return function.name if function else None


def functions_at(script: Script, lines: Iterable[int], prose_is_no_change: bool = False) -> set[str] | None:
    """The functions <lines> are in; None when one is outside every function (but for prose where a statement
    could begin, with <prose_is_no_change>) or past the file's end."""
    names: set[str] = set()
    for number in lines:
        function = script.function_at(number) if 1 <= number <= len(script.lines) else None
        if function is not None:
            names.add(function.name)
        elif not (prose_is_no_change and script.prose_at(number)):
            return None
    return names


def calls(code: str, names: Collection[str]) -> set[str]:
    """The words of <code> that are among <names>: loose, so it can only find more calls than there are."""
    return {word for word in CALL_WORD.findall(code) if word in names}


def keeps_command(word: str) -> bool:
    """Whether a command may still stand after <word>: a reserved word, or an assignment before a command."""
    return word in RESERVED or bool(ASSIGNMENT.match(word))


@dataclass
class _Context:
    kind: str  # one of WORDY, or a quote or expansion: dq, sq, ansi, param, arith
    line: int  # where it opened
    command: bool = True  # the next word stands where a command's name does
    word: int = -1  # where the word being read began, or -1
    word_command: bool = False
    named: bool = False  # the last word was a name where a command's stands: `name ()` defines it
    mode: str = ""  # a case's: head (before `in`), pattern, body; `function`'s: name
    target: bool = False  # the next word is what a redirection reads or writes, not a command
    depth: int = 0  # an arithmetic expansion's own parentheses
    in_double: bool = False  # a ${...} inside double quotes, where a single quote is a character


@dataclass
class _Heredoc:
    delimiter: str
    strip_tabs: bool
    line: int


@dataclass
class _Scan:
    """The scan behind read(): the text a character (or a run of them) at a time, contexts on a stack."""

    text: str
    i: int = 0
    line: int = 1
    stack: list[_Context] = field(default_factory=list)
    code: list[str] = field(default_factory=list)
    starts: list[bool] = field(default_factory=list)  # per line: it begins outside everything
    braces: list[int] = field(
        default_factory=list
    )  # per line: `{` minus `}` where commands stand, at the top
    opened: list[int] = field(default_factory=list)  # per line: `{` where commands stand, at the top
    pending: list[_Heredoc] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.stack = [_Context("top", 1)]
        self.code = list(self.text)
        self.starts, self.braces, self.opened = [True], [0], [0]

    @property
    def top(self) -> _Context:
        return self.stack[-1]

    def run(self) -> None:
        while self.i < len(self.text):
            if self.top.kind in WORDY:
                CODE.get(self.text[self.i], _Scan.on_word_char)(self)
            else:
                QUOTED[self.top.kind](self)
        self.end_word()
        for context in self.stack[1:]:
            self.problems.append(f"line {context.line}: a {WHAT[context.kind]} that does not close")
        for heredoc in self.pending:
            self.problems.append(
                f"line {heredoc.line}: a here-document ({heredoc.delimiter}) that does not end"
            )

    # --- lines -------------------------------------------------------------------------------------------

    def add_line(self, starts: bool) -> None:
        self.line += 1
        self.starts.append(starts)
        self.braces.append(0)
        self.opened.append(0)

    def code_newline(self) -> None:
        """Past the newline at i, in code: the next line, and the here-documents that begin on it."""
        self.i += 1
        self.add_line(len(self.stack) == 1 and not self.pending)
        if self.pending:
            self.heredoc_bodies()

    def quoted_newline(self) -> None:
        self.i += 1
        self.add_line(False)

    def heredoc_bodies(self) -> None:
        """The pending here-documents' lines, each up to its delimiter: text, not code."""
        for heredoc in self.pending:
            if not self.heredoc_body(heredoc):
                return  # it does not end: run() says so
        self.pending = []
        if self.i <= len(self.text) and self.text[self.i - 1 : self.i] == "\n":
            self.starts[-1] = len(self.stack) == 1  # the line after the last delimiter

    def heredoc_body(self, heredoc: _Heredoc) -> bool:
        while self.i < len(self.text):
            end = self.text.find("\n", self.i)
            end = len(self.text) if end < 0 else end
            body = self.text[self.i : end]
            self.i = end
            done = (body.lstrip("\t") if heredoc.strip_tabs else body) == heredoc.delimiter
            if end < len(self.text):
                self.i += 1
                self.add_line(False)
            if done:
                heredoc.line = -1
                return True
        return False

    # --- words and separators, in code -------------------------------------------------------------------

    def begin_word(self) -> None:
        context = self.top
        if context.word < 0:
            context.word = self.i
            command = context.command and context.kind != "array" and context.mode != "pattern"
            context.word_command = command and not context.target

    def end_word(self) -> None:
        context = self.top
        if context.word < 0:
            return
        word = self.text[context.word : self.i]
        context.word = -1
        if context.target:  # `>file`: the redirection's, and where commands stand is as it was
            context.target = False
            return
        self.after_word(context, word)

    def count_brace(self, word: str) -> None:
        """A `{` or `}` where a command stands, at the top: what a function's body opens and closes with."""
        if len(self.stack) == 1 and word in ("{", "}"):
            self.braces[-1] += 1 if word == "{" else -1
            self.opened[-1] += word == "{"

    def after_word(self, context: _Context, word: str) -> None:
        """Where commands stand after <word>: still, after a reserved word or an assignment (`x=1 cmd`)."""
        command = context.word_command
        context.named = command and bool(NAME.fullmatch(word))
        if command:
            self.count_brace(word)
        if context.mode == "name":  # `function name`: what follows is its body
            context.mode, context.command, context.named = "", True, True
        elif command and word in ("function", "case"):
            self.opens(context, word)
        elif context.kind == "case":
            self.case_word(context, word)
        else:
            context.command = command and keeps_command(word)

    def opens(self, context: _Context, word: str) -> None:
        """`function` (its name follows) or `case` (its patterns follow)."""
        context.command = False
        if word == "function":
            context.mode = "name"
        else:
            self.stack.append(_Context("case", self.line, command=False, mode="head"))

    def case_word(self, context: _Context, word: str) -> None:
        if context.mode == "head" and word == "in":
            context.mode = "pattern"
        elif word == "esac" and (context.mode == "pattern" or context.word_command):
            self.stack.pop()
            self.top.command = True  # (`esac }`)
        elif context.mode == "body":
            context.command = context.word_command and keeps_command(word)

    def separator(self, text: str) -> None:
        self.end_word()
        context = self.top
        context.named = False
        if context.kind == "case" and context.mode == "body" and text in (";;", ";&", ";;&"):
            context.mode = "pattern"
        elif not (context.kind == "case" and context.mode == "pattern"):  # (`a|b)` is still the pattern)
            context.command = context.kind != "array"

    def on_word_char(self) -> None:
        run = RUN.match(self.text, self.i)
        self.begin_word()
        self.i = run.end() if run else self.i + 1

    def on_space(self) -> None:
        self.end_word()
        self.i += 1

    def on_newline(self) -> None:
        self.end_word()  # (which may close a case: `esac`)
        if not (self.top.kind == "case" and self.top.mode == "pattern"):  # a pattern runs on over lines
            self.separator("\n")
        self.code_newline()

    def on_backslash(self) -> None:
        if self.text.startswith("\n", self.i + 1):  # the line runs on
            self.i += 2
            self.add_line(False)
            return
        self.begin_word()
        self.i += 2

    def on_hash(self) -> None:
        if self.top.word >= 0:  # `a#b`: inside a word
            self.i += 1
            return
        end = self.text.find("\n", self.i)
        end = len(self.text) if end < 0 else end
        self.code[self.i : end] = " " * (end - self.i)
        self.i = end

    def on_operator(self) -> None:
        self.end_word()
        if self.text.startswith("&>", self.i):  # a redirection: &>file, &>>file
            self.i += 3 if self.text.startswith("&>>", self.i) else 2
            self.top.target = True
            return
        start = self.i
        while self.i < len(self.text) and self.i < start + 3 and self.text[self.i] in ";&|":
            self.i += 1
        self.separator(self.text[start : self.i])

    def on_open(self) -> None:
        context = self.top
        if context.word >= 0 and self.text[self.i - 1] == "=":  # `a=(...)`: an array, inside the word
            self.push("array", command=False)
            self.i += 1
            return
        if context.kind == "case" and context.mode == "pattern":  # `(a|b)`: a pattern's optional `(`
            self.i += 1
            return
        self.end_word()
        definition = re.compile(r"\([ \t]*\)").match(self.text, self.i)
        if context.named and definition:  # `name()`: a function's body follows
            context.command = True
            self.i = definition.end()
        elif context.command and self.text.startswith("((", self.i):
            self.push("arith")
            self.i += 2
        else:
            self.push("paren")
            self.i += 1

    def on_close(self) -> None:
        self.end_word()
        context = self.top
        self.i += 1
        if context.kind == "case" and context.mode == "pattern":
            context.mode, context.command = "body", True
        elif context.kind in ("subst", "paren", "array"):
            self.stack.pop()
            if context.kind == "paren":
                self.top.command = False

    def on_less(self) -> None:
        text, i = self.text, self.i
        if text.startswith("<(", i):
            self.begin_word()
            self.push("subst")
            self.i += 2
            return
        self.end_word()
        heredoc = HEREDOC.match(text, i) if not text.startswith("<<<", i) else None
        if heredoc is not None:
            delimiter = next(g for g in heredoc.groups()[1:] if g is not None)
            self.pending.append(_Heredoc(delimiter, heredoc.group(1) == "-", self.line))
            self.i = heredoc.end()
        else:
            self.i += 3 if text.startswith("<<<", i) else 2 if text.startswith(("<&", "<>"), i) else 1
            self.top.target = True

    def on_greater(self) -> None:
        if self.text.startswith(">(", self.i):
            self.begin_word()
            self.push("subst")
            self.i += 2
            return
        self.end_word()
        self.i += 2 if self.text.startswith((">&", ">>", ">|"), self.i) else 1
        self.top.target = True

    # --- quotes and expansions ---------------------------------------------------------------------------

    def push(self, kind: str, command: bool = True) -> None:
        inside = self.top.kind == "dq" or self.top.in_double
        self.stack.append(_Context(kind, self.line, command=command, in_double=inside))

    def on_quote(self) -> None:
        self.begin_word()
        self.push({"'": "sq", '"': "dq"}[self.text[self.i]])
        self.i += 1

    def on_backquote(self) -> None:
        if self.top.kind == "backquote":
            self.end_word()
            self.stack.pop()
            self.i += 1
            return
        self.begin_word()
        self.push("backquote")
        self.i += 1

    def on_dollar(self) -> None:
        self.begin_word()
        self.dollar()

    def dollar(self) -> None:
        text, i = self.text, self.i
        quoted = self.top.kind == "dq" or self.top.in_double
        if text.startswith("$((", i):
            self.push("arith")
            self.i += 3
        elif text.startswith("$(", i):
            self.push("subst")
            self.i += 2
        elif text.startswith("${", i):
            self.push("param")
            self.i += 2
        elif text.startswith(("$'", '$"'), i) and not quoted:
            self.push("ansi" if text[i + 1] == "'" else "dq")
            self.i += 2
        else:  # $name, $1, $@, ...; a `$` before anything else is the character
            name = NAME.match(text, i + 1)
            special = text[i + 1 : i + 2] in tuple("@*#?$!-0123456789")
            self.i = name.end() if name else i + 2 if special else i + 1

    def escaped(self) -> None:
        """Past a backslash and what it escapes, in a quote or an expansion (a newline: the line runs on)."""
        if self.text.startswith("\n", self.i + 1):
            self.i += 1
            self.quoted_newline()
        else:
            self.i += 2

    def in_single(self) -> None:
        end = self.text.find("'", self.i)
        stop = len(self.text) if end < 0 else end
        for _ in range(self.text.count("\n", self.i, stop)):
            self.add_line(False)
        self.i = stop + 1
        if end >= 0:
            self.stack.pop()

    def in_ansi(self) -> None:
        ch = self.text[self.i]
        if ch == "'":
            self.stack.pop()
            self.i += 1
        elif ch == "\n":
            self.quoted_newline()
        elif ch == "\\":
            self.escaped()
        else:
            self.i += 1

    def in_double(self) -> None:
        ch = self.text[self.i]
        if ch == '"':
            self.stack.pop()
            self.i += 1
        elif ch == "\\":
            self.escaped()
        else:
            self.in_expanding(ch)

    def in_expanding(self, ch: str) -> None:
        """A character where expansions happen but words do not end (in double quotes, in ${...})."""
        if ch == "$":
            self.dollar()
        elif ch == "`":
            self.push("backquote")
            self.i += 1
        elif ch == "\n":
            self.quoted_newline()
        else:
            self.i += 1

    def in_param(self) -> None:
        ch = self.text[self.i]
        if ch == "}":
            self.stack.pop()
            self.i += 1
        elif ch == "\\":
            self.escaped()
        elif ch == '"' or (ch == "'" and not self.top.in_double):
            self.push("dq" if ch == '"' else "sq")
            self.i += 1
        else:
            self.in_expanding(ch)

    def in_arith(self) -> None:
        context, ch = self.top, self.text[self.i]
        if ch == "(":
            context.depth += 1
            self.i += 1
        elif ch == ")" and context.depth:
            context.depth -= 1
            self.i += 1
        elif ch == ")":
            self.stack.pop()
            self.i += 2 if self.text.startswith("))", self.i) else 1
        else:
            self.in_expanding(ch)


CODE = {
    " ": _Scan.on_space,
    "\t": _Scan.on_space,
    "\n": _Scan.on_newline,
    "\\": _Scan.on_backslash,
    "#": _Scan.on_hash,
    ";": _Scan.on_operator,
    "&": _Scan.on_operator,
    "|": _Scan.on_operator,
    "(": _Scan.on_open,
    ")": _Scan.on_close,
    "<": _Scan.on_less,
    ">": _Scan.on_greater,
    "'": _Scan.on_quote,
    '"': _Scan.on_quote,
    "`": _Scan.on_backquote,
    "$": _Scan.on_dollar,
}
QUOTED = {
    "sq": _Scan.in_single,
    "ansi": _Scan.in_ansi,
    "dq": _Scan.in_double,
    "param": _Scan.in_param,
    "arith": _Scan.in_arith,
}


def read(text: str) -> Script:
    """<text> as its functions, with what keeps it from reading as them (see the docstring)."""
    scan = _Scan(text)
    scan.run()
    lines = tuple(text.split("\n"))
    starts = scan.starts + [True] * (len(lines) + 1 - len(scan.starts))
    problems = list(scan.problems)
    functions = tuple(find_functions(lines, starts, scan, problems))
    return Script(
        lines,
        "".join(scan.code),
        functions=functions,
        problems=tuple(problems),
        starts=tuple(starts[: len(lines)]),
    )


def find_functions(
    lines: tuple[str, ...], starts: list[bool], scan: _Scan, problems: list[str]
) -> list[Function]:
    found: list[Function] = []
    number = 1
    while number <= len(lines):
        match = DEFINITION.match(lines[number - 1]) if starts[number - 1] else None
        if match is None:
            number += 1
            continue
        name = match["kw"] or match["name"]
        last = function_end(lines, starts, scan, number, match)
        if last is None:
            problems.append(f"line {number}: the function {name} does not end where its braces balance")
            return found
        found.append(Function(name, number, last))
        number = last + 1
    return found


def function_end(
    lines: tuple[str, ...], starts: list[bool], scan: _Scan, first: int, match: re.Match[str]
) -> int | None:
    """The line that closes the function begun on line <first>: that line when its braces close there, or the
    first line after it that begins outside everything with `}` (`)`) at its indentation, if its braces
    balance; None if there is none."""
    closer = "}" if match["body"] == "{" else ")"
    if starts[first] and closes_on_its_line(lines[first - 1], closer, scan, first):
        return first
    indent = match["indent"]
    for number in range(first + 1, len(lines) + 1):
        if lines[number - 1].startswith(indent + closer) and outside_after(closer, starts, number):
            balanced = closer == ")" or sum(scan.braces[first - 1 : number]) == 0
            return number if balanced else None
    return None


def outside_after(closer: str, starts: list[bool], number: int) -> bool:
    """Whether line <number>, which begins with <closer>, closes a function: a `}` line begins outside
    everything; a subshell body's `)` line begins inside it, and the line after it outside."""
    return starts[number - 1] if closer == "}" else starts[number]


def closes_on_its_line(line: str, closer: str, scan: _Scan, number: int) -> bool:
    """Whether the function begun on line <number> (<line>; the next line begins outside everything) ends
    there: its braces open and close on it, or it ends with the `)` of a subshell body."""
    if closer == ")":
        return line.rstrip().endswith(")")
    return bool(scan.opened[number - 1]) and scan.braces[number - 1] == 0
