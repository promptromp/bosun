"""A shell script read function by function: pure, text in, structure out.

The smoke suite's selection (tools/smoke_select.py) reads bosun's shell libraries with it (lib/common.sh,
lib/agents.sh, lib/launch/*.sh, lib/harnesses/*.sh, lib/platforms/*.sh) and tests/smoke.sh's helpers, so
that a change reaches the sections that use the function it changed, not every section that sources the file.

`read(text)` gives a `Script`:

- its functions: each `name() {` (or `function name {`), at any indentation, that is not inside another
  function, with the lines it spans. A function ends on its first line when its braces close there, or at the
  first line after it that begins, outside any quote or here-document, with `}` at the indentation of its
  first line (shfmt's layout);
- its statements: the code outside the functions, each a command with the lines it runs on over (a quote, an
  array, a `case`, a here-document, a `\\` at the end of a line, or a line that ends in `|`, `&&` or `||`); a
  statement that is assignments and nothing else (`NAME=...`, `export NAME=...`) names what it assigns;
- its problems: what keeps it from reading as that (a quote, a `$(`, a `case` or a here-document that does
  not close, a function with no end or whose braces do not balance). Whoever reads a script with a problem
  fails closed.

What a piece of code calls is read loosely, which can only find more: `calls(code, names)` is every word of
it that is one of the names, in strings too (`bash -c '...; load_config'`). Where code calls a function by a
name it builds, no reading can say which: `Script.computed(lines, names)` lists those places: `eval`; a
command whose name is a variable or a command's output (`"$fn"`, `"$(pick)"`), other than the positional
parameters, whose names the caller wrote, an upper-case variable, which holds a program by this repository's
convention (`"$BOSUN" ledger`), and a path (a function's name has no `/`); and a word that glues an
expansion to letters that could spell one of the names (`"${kind}_args"` and codex_args).

The scan knows bash's quoting (single, double, `$'...'`), `$(...)`, backquotes, `${...}`, `$((...))`,
here-documents, comments and `case` patterns. Plain python3 3.9 is enough, as for the tool that uses it.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
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
DECLARES = frozenset({"export", "readonly", "declare", "typeset"})
EXPORT_OPTION = re.compile(r"^-[a-zA-Z]*x")
# Words after which a command (or a reserved word, `fi }`) may still stand.
RESERVED = frozenset(
    {"then", "do", "else", "elif", "if", "while", "until", "!", "time", "{", "}", "fi", "done"}
)
RUN = re.compile(r"[^\s\\'\"`$()#<>;&|]+")  # ordinary characters of a word, in code
HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([A-Za-z0-9_.-]+))")
VARIABLE_WORD = re.compile(r'^"?\$\{?(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\[[@*]\])?\}?"?$')
POSITIONAL = re.compile(r'^"?\$(?:[@*0-9]|\{(?:[@*]|[0-9]+)(?::[0-9]+)?\})"?$')
UPPER_VARIABLE = re.compile(r'^"?\$(?:[A-Z_][A-Z0-9_]*|\{[A-Z_][A-Z0-9_]*(?:\[[@*]\])?\})"?$')
# A word of letters, digits, `_`, expansions and double quotes, as the pieces of a function's name could be.
GLUED = re.compile(r"(?:[A-Za-z0-9_\"]|\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)+")
PIECE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\"|[A-Za-z0-9_]+")
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


@dataclass(frozen=True)
class Statement:
    first: int
    last: int
    assigns: tuple[str, ...]  # what it assigns, when it does nothing else; else ()
    exported: bool  # it exports (export NAME=..., declare -x): every program it starts reads it


@dataclass
class Script:
    lines: tuple[str, ...]
    code: str  # the text with its comments blanked out, line for line
    literal: str  # ... and what is inside single quotes and here-documents too
    undocumented: str = ""  # the code without its here-documents' text either (a usage text `cat` prints)
    functions: tuple[Function, ...] = ()
    statements: tuple[Statement, ...] = ()
    problems: tuple[str, ...] = ()
    commands: tuple[tuple[int, str], ...] = ()  # (line, word): each word where a command's name stands

    def at(self, line: int) -> Function | Statement | None:
        """The function or statement line <line> is in; None for a blank or comment line outside them."""
        return self.by_line.get(line)

    @property
    def blocks(self) -> list[Function | Statement]:
        """Its functions and statements."""
        return [*self.functions, *self.statements]

    @cached_property
    def by_line(self) -> dict[int, Function | Statement]:
        found: dict[int, Function | Statement] = {}
        for block in self.blocks:
            found.update(dict.fromkeys(range(block.first, block.last + 1), block))
        return found

    @cached_property
    def code_lines(self) -> list[str]:
        return self.code.split("\n")

    @cached_property
    def literal_lines(self) -> list[str]:
        return self.literal.split("\n")

    def code_of(self, first: int, last: int) -> str:
        """Lines <first>..<last> (from 1) without their comments."""
        return "\n".join(self.code_lines[first - 1 : last])

    @cached_property
    def commands_at(self) -> dict[int, list[str]]:
        found: dict[int, list[str]] = {}
        for line, word in self.commands:
            found.setdefault(line, []).append(word)
        return found

    def computed(self, lines: Iterable[int], names: Collection[str]) -> list[str]:
        """Where <lines> (numbers from 1: a function's, a section's) call a function by a name no reading can
        tell, as `line N: <word>`. A variable is looked up in those lines only."""
        numbers = sorted(set(lines))
        literal = [self.literal_lines[n - 1] for n in numbers]
        block = "\n".join(literal)
        found = [
            f"line {n}: {word}"
            for n in numbers
            for word in self.commands_at.get(n, ())
            if built(word) and not written_out(word, block)
        ]
        found += [f"line {numbers[k]}: {word}" for k, word in glued_names(literal, names)]
        return found


def keeps_command(word: str) -> bool:
    """Whether a command may still stand after <word>: a reserved word, or an assignment before a command."""
    return word in RESERVED or bool(ASSIGNMENT.match(word))


def built(word: str) -> bool:
    """Whether a command's name is not written out: eval, a variable's value, a command's output."""
    if word == "eval":
        return True
    if ("$" not in word and "`" not in word) or "/" in word:
        return False
    return not (POSITIONAL.match(word) or UPPER_VARIABLE.match(word))


def written_out(word: str, block: str) -> bool:
    """Whether the variable <word> runs (`"$v"`, `"${v[@]}"`) only ever holds, in <block>, what is written out
    there or what the caller passed: each of its assignments (`v=...`, `v=(first ...)`, `local v=...`, `for v
    in ...`) is a literal or a positional parameter. A function's name among them is a word the calls see."""
    match = VARIABLE_WORD.match(word)
    if match is None:
        return False
    values = assigned_values(match["name"], block)
    return bool(values) and all(literal_value(v) for v in values)


def assigned_values(name: str, block: str) -> list[str]:
    """What <block> assigns to <name>: the word after `name=` (an array's first); each of `for name in`."""
    assignment = rf"(?<![\w$]){name}\+?=\(?[ \t]*(?P<value>[^\s);&|]*)"
    values = [m["value"] for m in re.finditer(assignment, block)]
    for loop in re.finditer(rf"\bfor[ \t]+{name}[ \t]+in[ \t]+(?P<words>[^;\n]*)", block):
        values += loop["words"].split()
    return values


def literal_value(value: str) -> bool:
    return ("$" not in value and "`" not in value) or bool(POSITIONAL.match(value))


def glued_names(lines: Sequence[str], names: Collection[str]) -> list[tuple[int, str]]:
    """(index of the line, word) for each word of <lines> that glues an expansion to letters, digits or `_`
    so that one of <names> fits (`run_$mode`, with a run_step): a function called by a name it builds."""
    found = []
    for number, line in enumerate(lines):
        for match in GLUED.finditer(line):
            shape = name_shape(match.group(0))
            if shape is not None and any(shape.fullmatch(name) for name in names):
                found.append((number, match.group(0)))
    return found


def name_shape(word: str) -> re.Pattern[str] | None:
    """The names <word> could spell, each expansion in it any letters: None unless it has an expansion and a
    piece of its own with an `_` in it, as the name of a function it builds would (`${n}s` is a unit)."""
    pieces = [p for p in PIECE.findall(word) if p != '"']
    if not any(p.startswith("$") for p in pieces) or not any("_" in p for p in pieces if p[0] != "$"):
        return None
    return re.compile("".join("[A-Za-z0-9_]*" if p.startswith("$") else re.escape(p) for p in pieces))


def calls(code: str, names: Collection[str]) -> set[str]:
    """The words of <code> that are among <names>: loose, so it can only find more calls than there are."""
    return {word for word in CALL_WORD.findall(code) if word in names}


def mentions(code: str, name: str) -> bool:
    """Whether <code> uses the word <name> (a variable's: `$NAME`, `${NAME:-}`, or NAME passed by name)."""
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", code) is not None


@dataclass
class _Context:
    kind: str  # one of WORDY, or a quote or expansion: dq, sq, ansi, param, arith
    line: int  # where it opened
    command: bool = True  # the next word stands where a command's name does
    word: int = -1  # where the word being read began, or -1
    word_line: int = 0
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
    literal: list[str] = field(default_factory=list)
    undocumented: list[str] = field(default_factory=list)
    starts: list[bool] = field(default_factory=list)  # per line: it begins outside everything
    braces: list[int] = field(
        default_factory=list
    )  # per line: `{` minus `}` where commands stand, at the top
    opened: list[int] = field(default_factory=list)  # per line: `{` where commands stand, at the top
    pending: list[_Heredoc] = field(default_factory=list)
    commands: list[tuple[int, str]] = field(default_factory=list)
    top_words: list[tuple[int, str]] = field(default_factory=list)  # (line, word or separator) at the top
    problems: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.stack = [_Context("top", 1)]
        self.code = list(self.text)
        self.literal = list(self.text)
        self.undocumented = list(self.text)
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
            self.blank(self.i, end, literal_only=True)
            self.undocumented[self.i : end] = " " * (end - self.i)
            self.i = end
            done = (body.lstrip("\t") if heredoc.strip_tabs else body) == heredoc.delimiter
            if end < len(self.text):
                self.i += 1
                self.add_line(False)
            if done:
                heredoc.line = -1
                return True
        return False

    def blank(self, start: int, end: int, literal_only: bool = False) -> None:
        for k in range(start, min(end, len(self.text))):
            if self.text[k] != "\n":
                self.literal[k] = " "
                if not literal_only:
                    self.code[k] = " "
                    self.undocumented[k] = " "

    # --- words and separators, in code -------------------------------------------------------------------

    def begin_word(self) -> None:
        context = self.top
        if context.word < 0:
            context.word = self.i
            context.word_line = self.line
            context.word_command = context.command and context.kind != "array" and context.mode != "pattern"
            context.word_command = context.word_command and not context.target

    def end_word(self) -> None:
        context = self.top
        if context.word < 0:
            return
        word = self.text[context.word : self.i]
        context.word = -1
        if context.word_command and not ASSIGNMENT.match(word):  # (`x=1 cmd`: the command is cmd)
            self.commands.append((context.word_line, word))
        if len(self.stack) == 1:
            self.top_words.append((context.word_line, word))
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
        if len(self.stack) == 1:
            self.top_words.append((self.line, text))
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
        self.blank(self.i, end)
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
        for k in range(self.i, stop):
            if self.text[k] == "\n":
                self.add_line(False)
            else:
                self.literal[k] = " "
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
            self.literal[self.i] = " "
            self.escaped()
        else:
            self.literal[self.i] = " "
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
    """<text> as functions and statements, with what keeps it from reading as them (see the docstring)."""
    scan = _Scan(text)
    scan.run()
    lines = tuple(text.split("\n"))
    starts = scan.starts + [True] * (len(lines) + 1 - len(scan.starts))
    problems = list(scan.problems)
    functions = find_functions(lines, starts, scan, problems)
    script = Script(
        lines,
        "".join(scan.code),
        "".join(scan.literal),
        "".join(scan.undocumented),
        functions=tuple(functions),
    )
    script.statements = tuple(find_statements(script, starts, scan.top_words))
    script.problems = tuple(problems)
    script.commands = tuple(scan.commands)
    return script


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


def find_statements(script: Script, starts: list[bool], words: list[tuple[int, str]]) -> list[Statement]:
    inside = {n for f in script.functions for n in range(f.first, f.last + 1)}
    code_lines = script.code_lines
    by_line: dict[int, list[str]] = {}
    for line, word in words:
        by_line.setdefault(line, []).append(word)
    found: list[Statement] = []
    number = 1
    while number <= len(code_lines):
        if number in inside or not starts[number - 1] or not code_lines[number - 1].strip():
            number += 1
            continue
        last = statement_end(code_lines, starts, inside, number)
        found.append(classify(number, last, [w for n in range(number, last + 1) for w in by_line.get(n, ())]))
        number = last + 1
    return found


def statement_end(code_lines: list[str], starts: list[bool], inside: set[int], first: int) -> int:
    """The last line of the statement begun on line <first> (see the docstring for what it runs on over)."""
    last = first
    while last < len(code_lines) and last + 1 not in inside:
        runs_on = not starts[last] or code_lines[last - 1].rstrip().endswith(("|", "&&"))
        if not runs_on:
            break
        last += 1
    return last


def classify(first: int, last: int, words: list[str]) -> Statement:
    """A statement's assignments, when it is assignments and nothing else (apart from `;` and new lines)."""
    names: list[str] = []
    exported = False
    for word in words:
        if word in (";", "\n"):
            continue
        if word in DECLARES or (EXPORT_OPTION.match(word) and not names):
            exported = exported or word == "export" or bool(EXPORT_OPTION.match(word))
            continue
        if not ASSIGNMENT.match(word):
            return Statement(first, last, (), exported)
        names.append(re.split(r"[\[+=]", word, maxsplit=1)[0])
    return Statement(first, last, tuple(dict.fromkeys(names)), exported)
