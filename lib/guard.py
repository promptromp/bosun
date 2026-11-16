#!/usr/bin/env python3
"""PreToolUse hook for the orchestrator (`bosun guard`, added at launch by guard_hook_settings in
lib/common.sh, naming the bosun that launched the window; `bosun chores --dry-run` gets it too).

The permission rules in settings.json name tools one by one and match Bash commands by prefix, so
a write tool missing from the `ask` list, or `git push -f` instead of `--force`, would slip
through. This hook is the catch-all:

- any MCP tool whose name holds a write verb (send, create, update, delete, forward, share, ...)
  as a word -> ask you. A name's words split on `_`, `-` and before an upper-case letter after a
  lower-case one or a digit (name_words: editJiraIssue is edit, jira, issue; s3Upload is s3, upload).
- Chrome's `computer` tool with an action that can change the page (a click, typing, a key, any
  action but a screenshot, scrolling, zooming, waiting or hovering) -> ask you. Its whole tools
  that change pages (javascript_tool, form_input) are `ask` rules in orchestrator/settings.json.
- `git push` with any force form (-f, --force*, +refspec) or `gh pr merge --admin`, anywhere in
  a compound command -> deny
- a gated outward command (GATED_COMMANDS: `git push`, `gh pr merge`, `gh issue comment`, ...) in a
  form the settings' `ask` rules, prefixes of a command as written, do not see -> ask you, so it
  never runs on auto mode's judgement alone, and an approved one still reaches the PermissionRequest
  hook, which uses its approval up
- what `bash|sh|zsh -c` or `eval` runs, when the guard cannot read it (a command named by a variable
  or a substitution, quotes that do not close) -> ask you

A Bash command is read as the shell would split it (parse): each simple command by its name's last
path part (/usr/bin/git is git), git's subcommand past git's own options (`git -C <dir> push` is a
push), with the commands in its substitutions ($(...) and backticks, outside single quotes, and in
unquoted heredoc bodies) and what `bash -c '...'` and `eval ...` run, read again. Best effort, as
docs/permissions.md says: a script file or stdin given to a shell, a git alias, flags in a variable
and `find -exec` stay unseen.
- `bosun delegate`, `bosun send` or `bosun ledger
  reach-out|ask|replied|propose|approve|spawn|link|unlink|fold|ticket-sync` with a command
  substitution ($(...) or backticks) that the shell would run: outside single quotes, or in an
  unquoted heredoc -> deny. Their text often comes from Slack, PRs or a tracker, so it must never
  be able to run commands.
- `bosun send <window> <message>` without `--id <ledger id>` or `--no-item`, `send --new`, and
  `send --start` with a message -> deny. Every message about a work item names its ledger id;
  updates to delegated tasks go through `bosun delegate --update`, new windows through
  `bosun delegate`. (Only the orchestrator runs this hook; you can still send by hand.)

With BOSUN_APPROVALS_ENABLED=1, `bosun launch` adds more hooks to the orchestrator
(docs/design/approvals.md), all only about the gated outward calls (GATED_MCP, GATED_COMMANDS):

- `bosun guard --approvals` (PreToolUse): deny a gated call that carries out no approved
  proposal and no standing class, saying how to propose it and record my approval
  (BOSUN_APPROVALS_DENY_UNAPPROVED=0: no decision instead, so Claude Code asks as before).
- `bosun guard --permission-request` (PermissionRequest): allow a gated call that carries out
  an approved proposal whose quote I typed in this session, or a standing class, and use that
  approval up; otherwise no decision, so Claude Code's prompt appears as before.
- `bosun guard --post-tool-use` (PostToolUse and PostToolUseFailure): once the call that used
  an approval ends, close its proposal as done (outreach: mark it carried out for `ledger sent`),
  or on failure record approval-failed and leave it open. Never decides anything.

With BOSUN_APPROVALS_CHECK_REVIEW_THREADS=1, an allowed call that asks for a re-review on my PR
(outreach with purpose review that belongs to a PR, or `gh pr edit --add-reviewer` on a my-pr item)
is first checked against the live PR (lib/pr_threads.py, read-only): while a review thread awaits
the author, or a review by someone else is newer than the proposed message, `--approvals` denies it
with what to do, and `--permission-request` does not allow it. When GitHub cannot be read (within
BUMP_CHECK_SECONDS), `--approvals` asks and `--permission-request` decides nothing, so Claude Code
asks me: the call is neither sent unchecked nor blocked.

Reads the hook event (JSON) on stdin; prints a decision (JSON) or nothing, and always exits 0 so
a malformed event never blocks the session. A ledger write the update gate refuses
(lib/update_gate.py: bosun was updated since this hook's code started, or an update was cut short)
is run again through the install link with the same event, which prints the decision.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import approvals
import events
import ledger
import pr_threads
import update_gate


# Matched against the words of the tool part of mcp__<server>__<tool> (name_words). Words that are
# also nouns or adjectives in reads' names (request, comment, label, order, pin, mark, resolve, patch,
# put, clear, drop) are left out: as verbs they would stop the orchestrator at a read such as
# get_pull_request or get_patch (docs/design/connectors.md).
WRITE_VERBS = {
    "accept",
    "add",
    "append",
    "apply",
    "approve",
    "archive",
    "assign",
    "authenticate",
    "batch",
    "cancel",
    "checkout",
    "close",
    "complete",
    "copy",
    "create",
    "decline",
    "delete",
    "download",
    "duplicate",
    "edit",
    "execute",
    "fork",
    "forward",
    "generate",
    "grant",
    "import",
    "insert",
    "invite",
    "invoke",
    "kill",
    "link",
    "merge",
    "modify",
    "move",
    "pay",
    "post",
    "promote",
    "publish",
    "purchase",
    "push",
    "react",
    "remove",
    "rename",
    "reopen",
    "reply",
    "respond",
    "restart",
    "restore",
    "retry",
    "revoke",
    "run",
    "save",
    "schedule",
    "send",
    "set",
    "share",
    "spam",
    "stop",
    "submit",
    "transition",
    "trash",
    "trigger",
    "unlabel",
    "unpin",
    "unstar",
    "untrash",
    "update",
    "upload",
    "use",
    "write",
}
COMMAND_SEPARATORS = {"&&", "||", ";", "|", "&", "\n", "(", ")"}
TEXT_COMMANDS = {"delegate", "send"}
# Ledger subcommands whose text often quotes other people (a reply, an outgoing message, a task's
# title and ask, a link's via, a fold's note, a ticket's status and title as the tracker shows them).
LEDGER_TEXT_COMMANDS = {
    "reach-out",
    "ask",
    "replied",
    "propose",
    "approve",
    "spawn",
    "link",
    "unlink",
    "fold",
    "ticket-sync",
}
# `bosun kg` subcommands that take a quote of my words, or a name, alias, summary, value or note that may
# hold text from Slack, a PR or a ticket.
KG_TEXT_COMMANDS = {"add-entity", "propose", "supersede", "confirm", "reject", "batch"}
SEND_ITEM_FLAGS = {"--id", "--no-item"}
SEND_INFO_FLAGS = {"--list", "-h", "--help"}
SEND_START_WITHOUT_MESSAGE = 2  # send --start <window>
SEND_WITHOUT_ITEM = (
    "say what this message is about. To add to or correct a delegated task: bosun delegate "
    "--update <ledger id> --details - <<'EOF'. Another message about a work item: bosun send "
    "--id <ledger id> <window> ... Only a message about no work item: bosun send --no-item "
    "<window> ..."
)
SEND_OPENS_SESSION = (
    "hand tasks to a new window or a window without Claude with bosun delegate <type> ... "
    "--new|--start (after my approval), not bosun send"
)
SUBSTITUTIONS = ("$(", "`")
MIN_COMMAND_WORDS = 2
MCP_NAME_PARTS = 3  # mcp__<server>__<tool>
# Where a tool's name breaks into words: "_", "-", and before an upper-case letter that follows a lower-case
# one or a digit (editJiraIssue, s3Upload).
NAME_WORD_BREAK = re.compile(r"[_\-]|(?<=[a-z0-9])(?=[A-Z])")
# Chrome's `computer` tool clicks, types and presses keys; a settings rule cannot ask by an MCP tool's
# input, so the guard looks at its action. These change nothing; any other action asks. (Its whole
# tools that change pages, javascript_tool and form_input, are `ask` rules in orchestrator/settings.json.)
CHROME_COMPUTER = "mcp__claude-in-chrome__computer"
CHROME_PASSIVE_ACTIONS = frozenset({"screenshot", "scroll", "scroll_to", "zoom", "wait", "hover"})
HEREDOC = re.compile(r"<<-?\s*(?:(['\"])(\w+)\1|(\w+))")
REDIRECTION = re.compile(r"^(?:\d*|&)[<>]")  # 2>/dev/null, >out, <<EOF, &>log
# Words that can precede a command name: `do git push`, `{ bosun ...`, `X=1 git push`.
SHELL_KEYWORDS = {"{", "}", "!", "do", "done", "then", "else", "elif", "fi", "if", "while", "until", "time"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Commands that run the command in their arguments (`timeout 120 gh pr merge ...`), so the guard
# judges the command they run: each with its options that take a separate value, and how many
# arguments come before the command (timeout's duration). Before 2026-10-23 they hid what they ran.
WRAPPERS: dict[str, tuple[frozenset[str], int]] = {
    "timeout": (frozenset({"-s", "--signal", "-k", "--kill-after"}), 1),
    "gtimeout": (frozenset({"-s", "--signal", "-k", "--kill-after"}), 1),
    "nice": (frozenset({"-n", "--adjustment"}), 0),
    "nohup": (frozenset(), 0),
    "command": (frozenset(), 0),
    "exec": (frozenset({"-a"}), 0),
    "env": (frozenset({"-u", "--unset", "-C", "--chdir"}), 0),
    "stdbuf": (frozenset({"-i", "-o", "-e"}), 0),
    "caffeinate": (frozenset({"-t", "-w"}), 0),
    "sudo": (frozenset({"-u", "-g", "-C", "-h", "-p", "-U"}), 0),
    "xargs": (frozenset({"-n", "-I", "-J", "-L", "-P", "-R", "-S", "-s", "-d", "-E", "-a"}), 0),
}
# Shells whose -c runs its argument, and their options that take a separate value (bash -o pipefail -c ...).
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
EVAL = "eval"
# git's global options that take a separate value (git -C <dir> push, git -c k=v push, ...).
GIT_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env", "--attr-source"}
)
# How deep the guard reads commands inside commands (a substitution in a bash -c in an eval ...).
MAX_NESTING = 5
# What a substitution the shell would run stands as in the text around it, once its command is taken out.
SUBSTITUTED = "$__substitution__"
DYNAMIC = "$"
SHOWN_CHARS = 120  # of a command the guard cannot read, in what it says


def name_words(tool_name: str) -> list[str]:
    """The words of an MCP tool's name: the tool part of mcp__<server>__<tool> split on "_", "-"
    and before each upper-case letter after a lower-case one or a digit, lowercased (editJiraIssue: edit,
    jira, issue; s3Upload: s3, upload); no words for a tool that is not an MCP tool."""
    parts = tool_name.split("__")
    if len(parts) < MCP_NAME_PARTS or parts[0] != "mcp":
        return []
    return [word.lower() for word in NAME_WORD_BREAK.split(parts[-1]) if word]


def mcp_write_verb(tool_name: str) -> str | None:
    """The write verb in an MCP tool's name, if any."""
    found = sorted(set(name_words(tool_name)) & WRITE_VERBS)
    return found[0] if found else None


def chrome_action_changes(tool_name: str, tool_input: Mapping[str, Any]) -> str | None:
    """The action of a Chrome `computer` call that can change a page (a click, typing, a key, one
    Chrome adds later, or none given), or None: another tool, or an action in CHROME_PASSIVE_ACTIONS."""
    if tool_name != CHROME_COMPUTER:
        return None
    action = str(tool_input.get("action") or "")
    return None if action in CHROME_PASSIVE_ACTIONS else action or "(no action)"


def without_prefix(words: list[str]) -> list[str]:
    """<words> from the command name on: without keywords like `do`, assignments like `X=1` and
    wrappers like `timeout 120` (WRAPPERS)."""
    index = 0
    while index < len(words):
        if words[index] in SHELL_KEYWORDS or ASSIGNMENT.match(words[index]):
            index += 1
        elif words[index] in WRAPPERS:
            index = after_wrapper(words, index)
        else:
            break
    return words[index:]


def after_wrapper(words: list[str], index: int) -> int:
    """Where the command run by the wrapper at <index> starts: after its options and arguments."""
    value_options, arguments = WRAPPERS[words[index]]
    index += 1
    while index < len(words) and words[index].startswith("-") and words[index] != "--":
        index += 2 if words[index] in value_options else 1
    if index < len(words) and words[index] == "--":
        index += 1
    return min(index + arguments, len(words))


@dataclass
class QuoteState:
    """Which quotes are open while scanning a command line by line."""

    single: bool = False
    double: bool = False

    def toggle(self, char: str) -> bool:
        """Open or close a quote at <char>; True if it was a quote character in effect."""
        if char == "'" and not self.double:
            self.single = not self.single
            return True
        if char == '"' and not self.single:
            self.double = not self.double
            return True
        return False


def line_runs_substitution(line: str, quotes: QuoteState) -> bool:
    """Scan one whole line, updating <quotes>; True if it has a $( or ` the shell would run."""
    found = False
    position = 0
    while position < len(line):
        if line[position] == "\\" and not quotes.single:
            position += 2
            continue
        is_quote = quotes.toggle(line[position])
        if not is_quote and not quotes.single and line.startswith(SUBSTITUTIONS, position):
            found = True
        position += 1
    return found


@dataclass(frozen=True)
class Scan:
    """A command line read for what it runs: its text without heredoc bodies (data), the bodies of its
    unquoted heredocs (where $(...) and backticks run), and whether the shell would run a substitution."""

    text: str
    expanding: tuple[str, ...]
    runs_in_text: bool  # a $(...) or `...` outside single quotes in <text>

    @property
    def runs(self) -> bool:
        return self.runs_in_text or any(marker in body for body in self.expanding for marker in SUBSTITUTIONS)


@dataclass(frozen=True)
class Part:
    """Simple commands, each from its command's name on, and the text they were written in: the command
    line itself, or the command a substitution or a shell wrapper in it runs."""

    text: str
    commands: list[list[str]]


@dataclass(frozen=True)
class Parsed:
    """What a Bash command line runs, as far as the guard reads it: its parts (the line itself first),
    its own simple commands as written (keywords, assignments and wrappers kept: what a settings rule's
    prefix sees), and why the guard cannot read a command a wrapper runs, if it cannot."""

    parts: list[Part]
    written: list[list[str]]
    unread: str = ""

    @property
    def commands(self) -> list[list[str]]:
        """Every simple command it runs: its own, and what its substitutions and wrappers run."""
        return [words for part in self.parts for words in part.commands]

    def with_inner(self, inner: Parsed) -> Parsed:
        return Parsed([*self.parts, *inner.parts], self.written, self.unread or inner.unread)


def heredoc_body(lines: list[str], index: int, delimiter: str) -> tuple[list[str], int]:
    """The body of a heredoc that starts at <index>, and the index after its closing delimiter."""
    end = index
    while end < len(lines) and lines[end].strip() != delimiter:
        end += 1
    return lines[index:end], end + 1


def heredoc_scan(command: str) -> Scan:
    """<command> without its heredoc bodies, the bodies of its unquoted heredocs, and whether the shell
    would run a $(...) or `...` in it: outside single quotes, or in the body of an unquoted heredoc
    (bodies of quoted heredocs, <<'EOF', are literal)."""
    lines = command.split("\n")
    quotes = QuoteState()
    kept: list[str] = []
    expanding: list[str] = []
    runs = False
    index = 0
    while index < len(lines):
        runs = line_runs_substitution(lines[index], quotes) or runs
        kept.append(lines[index])
        heredoc = None if quotes.single or quotes.double else HEREDOC.search(lines[index])
        index += 1
        if heredoc:
            body, index = heredoc_body(lines, index, heredoc.group(2) or heredoc.group(3))
            expanding += [] if heredoc.group(1) else ["\n".join(body)]
    return Scan("\n".join(kept), tuple(expanding), runs)


def matching_paren(text: str, start: int) -> int:
    """The index of the ")" that closes a "$(" whose command starts at <start> (the end when none does)."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            if depth == 0:
                return index
            depth -= 1
    return len(text)


def substitution_at(text: str, position: int) -> tuple[str, int] | None:
    """The command of a substitution that starts at <position> ($(...) or `...`), and where the text goes
    on after it; None if none starts there."""
    if text.startswith("$(", position):
        close = matching_paren(text, position + 2)
        return text[position + 2 : close], close + 1
    if text.startswith("`", position):
        close = text.find("`", position + 1)
        close = len(text) if close < 0 else close
        return text[position + 1 : close], close + 1
    return None


def take_substitutions(text: str, shell_quotes: bool = True) -> tuple[str, list[str]]:
    """<text> with each substitution the shell would run put as SUBSTITUTED, and the commands they hold:
    $(...) and `...` outside single quotes, inside double quotes too (<shell_quotes> False: a heredoc
    body, whose quotes are text)."""
    kept: list[str] = []
    held: list[str] = []
    quotes = QuoteState()
    position = 0
    while position < len(text):
        if text[position] == "\\" and not quotes.single:
            kept.append(text[position : position + 2])
            position += 2
            continue
        found = None if quotes.single else substitution_at(text, position)
        if found:
            held.append(found[0])
            kept.append(SUBSTITUTED)
            position = found[1]
            continue
        if shell_quotes:
            quotes.toggle(text[position])
        kept.append(text[position])
        position += 1
    return "".join(kept), held


def split_simple(text: str) -> tuple[list[list[str]], bool]:
    """<text>'s simple commands as written (one per line, `;`, `&&`, `|`, ...), and whether its quotes
    close (when they do not, what was read up to there)."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"  # a newline ends a command, like `;`
    lexer.whitespace_split = True
    commands: list[list[str]] = []
    current: list[str] = []
    try:
        for token in lexer:
            if token in COMMAND_SEPARATORS or set(token) <= set(";&|()\n"):
                commands.append(current)
                current = []
            else:
                current.append(token)
    except ValueError:  # unbalanced quotes: what was parsed
        commands.append(current)
        return [words for words in commands if words], False
    commands.append(current)
    return [words for words in commands if words], True


def shell_command_at(words: list[str]) -> int | None:
    """Where the command string of `bash|sh|zsh -c <command>` is in <words> (the first argument after the
    shell's options, one of which holds c: -c, -lc, -ec); None when no option says -c (a script file or
    stdin, which the guard cannot see)."""
    index, command = 1, False
    while index < len(words) and words[index].startswith(("-", "+")) and words[index] != "--":
        command = command or says_c(words[index])
        index += 2 if words[index] in SHELL_VALUE_OPTIONS else 1
    index += 1 if words[index : index + 1] == ["--"] else 0
    return index if command else None


def says_c(option: str) -> bool:
    """Whether a shell's option (a cluster of short ones: -lc) holds -c."""
    return option.startswith("-") and not option.startswith("--") and "c" in option[1:]


def wrapped(words: list[str]) -> str | None:
    """What a shell wrapper runs, to be read again: the command string of `bash|sh|zsh|dash|ksh -c ...`
    ("" when it lacks one: it runs nothing), or eval's arguments joined; None for any other command."""
    name = command_name(words[0])
    if name == EVAL:
        return " ".join(words[1:])
    at = shell_command_at(words) if name in SHELLS else None
    if at is None:
        return None
    return words[at] if at < len(words) else ""


def unreadable(wrapper: str, command: str, commands: list[list[str]], closed: bool) -> str:
    """Why the guard cannot read what the wrapper <wrapper> runs (<command>, split into <commands>), or "":
    its quotes do not close, or a command in it is named by a variable or a substitution, which decide
    what runs only when it runs. Not a wrapper's (<wrapper> ""): nothing to say."""
    named = any(words[0].startswith(DYNAMIC) for words in commands)
    if not wrapper or (closed and not named):
        return ""
    shown = " ".join(command.replace(SUBSTITUTED, "$(...)").split())
    return f"{wrapper} runs a command bosun guard cannot read ({shown[:SHOWN_CHARS]})"


def parse(command: str, wrapper: str = "", depth: int = 0) -> Parsed:
    """What a Bash command line runs (Parsed): its own simple commands, and those its substitutions and
    shell wrappers run, read again as far as MAX_NESTING. <wrapper>: the wrapper (eval, bash -c) whose
    argument <command> is, which the guard must be able to read (unreadable)."""
    if depth > MAX_NESTING:
        return Parsed([], [], f"commands nested more than {MAX_NESTING} deep")
    scan = heredoc_scan(command)
    text, held = take_substitutions(scan.text)
    held += [inner for body in scan.expanding for inner in take_substitutions(body, shell_quotes=False)[1]]
    written, closed = split_simple(text)
    commands = [words for words in map(without_prefix, written) if words]
    parsed = Parsed([Part(command, commands)], written, unreadable(wrapper, command, commands, closed))
    for inner in held:
        parsed = parsed.with_inner(parse(inner, depth=depth + 1))
    for words in commands:
        argument = wrapped(words)
        if argument is not None:
            parsed = parsed.with_inner(parse(argument, command_name(words[0]), depth + 1))
    return parsed


def simple_commands(command: str) -> list[list[str]]:
    """Every simple command a shell command line runs (word lists, each from its command's name on):
    one per line, `;`, `&&`, `|`, etc., leaving out heredoc bodies (data, not commands), leading keywords
    (`do`, `then`, `{`), variable assignments and wrappers (`timeout 120`); with those its substitutions
    ($(...), `...`) and shell wrappers (`bash -c '...'`, `eval ...`) run."""
    return parse(command).commands


def runs_substitution(command: str) -> bool:
    return heredoc_scan(command).runs


def command_name(word: str) -> str:
    """A command word as the guard compares it: its last path part (/usr/bin/git is git)."""
    return word.rsplit("/", 1)[-1]


def git_subcommand(arguments: list[str]) -> int:
    """Where git's subcommand is in <arguments> (what follows `git`): after its global options."""
    index = 0
    while index < len(arguments) and arguments[index].startswith("-"):
        index += 2 if arguments[index] in GIT_VALUE_OPTIONS else 1
    return min(index, len(arguments))


def compared(words: list[str]) -> list[str]:
    """A simple command as the gates and denials compare it: its command's last path part, and for git
    the subcommand right after it (`/usr/bin/git -C <dir> push` is `git push`)."""
    name = command_name(words[0])
    rest = words[1:]
    return [name, *rest[git_subcommand(rest) :]] if name == "git" else [name, *rest]


def is_force_flag(arg: str) -> bool:
    """-f, --force*, a +refspec, or a cluster of short flags that includes f (e.g. -uf)."""
    short_cluster = arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:]
    return arg.startswith(("--force", "+")) or short_cluster


def is_bosun(words: list[str], command: str) -> bool:
    return command_name(words[0]) == "bosun" and words[1] == command


def send_without_item(words: list[str]) -> str | None:
    """bosun send that does not say which work item it is about (or opens a session)."""
    if not is_bosun(words, "send"):
        return None
    arguments = [word for word in words[2:] if not REDIRECTION.match(word)]
    if SEND_INFO_FLAGS & set(arguments):
        return None
    if "--new" in arguments or ("--start" in arguments and len(arguments) > SEND_START_WITHOUT_MESSAGE):
        return SEND_OPENS_SESSION
    if "--start" in arguments or SEND_ITEM_FLAGS & set(arguments):
        return None
    return SEND_WITHOUT_ITEM


def carries_text(words: list[str]) -> bool:
    """A bosun command whose arguments hold text from a person (mine, or someone's I pass on)."""
    if any(is_bosun(words, name) for name in TEXT_COMMANDS):
        return True
    if len(words) <= MIN_COMMAND_WORDS:
        return False
    return (is_bosun(words, "ledger") and words[2] in LEDGER_TEXT_COMMANDS) or (
        is_bosun(words, "kg") and words[2] in KG_TEXT_COMMANDS
    )


def text_command_risk(words: list[str], command: str) -> str | None:
    """bosun delegate/send (or a ledger command carrying someone's text) whose text would run
    a command substitution."""
    if carries_text(words) and runs_substitution(command):
        return (
            "text passed to bosun delegate/send, to ledger reach-out/ask/replied/propose/approve/"
            "spawn/link/unlink/fold/ticket-sync, or to kg add-entity/propose/supersede/confirm/reject/batch "
            "(its --name, --alias, --summary, --value or --quote), must not be able to run commands. "
            "This checks the "
            "whole Bash call: a $(...) or backticks anywhere in it count, also in another step joined "
            "with ; && | or in a variable set before the command. Run the bosun command as its own "
            "Bash call, with every value in single quotes or on stdin with a quoted heredoc "
            "(--details - <<'EOF'); fetch values such as a PR title in an earlier call and paste them in"
        )
    return None


def force_push(words: list[str]) -> str | None:
    words = compared(words)
    pushes = words[0] == "git" and "push" in words[1:]
    if pushes and any(is_force_flag(arg) for arg in words[words.index("push") + 1 :]):
        return "git push with force (-f, --force*, or a +refspec) is never allowed"
    return None


def admin_merge(words: list[str]) -> str | None:
    if compared(words)[:3] == ["gh", "pr", "merge"] and "--admin" in words:
        return "gh pr merge --admin is never allowed"
    return None


def forbidden(parsed: Parsed) -> str | None:
    """Why what <parsed> runs is never allowed, or None (each command judged with the text it was
    written in, so a substitution inside `bash -c '...'` counts as the inner shell would run it)."""
    for part in parsed.parts:
        for words in part.commands:
            if len(words) < MIN_COMMAND_WORDS:
                continue
            reason = (
                text_command_risk(words, part.text)
                or send_without_item(words)
                or force_push(words)
                or admin_merge(words)
            )
            if reason:
                return reason
    return None


def forbidden_bash(command: str) -> str | None:
    """Why a Bash command is never allowed, or None."""
    return forbidden(parse(command))


def unseen_gate(parsed: Parsed) -> str | None:
    """A gated outward command (GATED_COMMANDS) that the settings' `ask` rules do not see, as they match
    a command as written by its first words (a full path, git's options before its subcommand, a wrapper,
    a substitution, bash -c or eval): what it is, or None."""
    for words in parsed.commands:
        gate = gate_of(words, GATED_COMMANDS)
        if gate and not any(tuple(written[: len(gate[0])]) == gate[0] for written in parsed.written):
            return gate[1]
    return None


def bash_decision(command: str) -> dict[str, str] | None:
    """The plain guard's answer to a Bash command: deny what is never allowed; ask before a gated
    outward command the permission rules do not see, and before a command a wrapper runs that the
    guard cannot read; else no decision."""
    parsed = parse(command)
    reason = forbidden(parsed)
    if reason:
        return {"permissionDecision": "deny", "permissionDecisionReason": f"bosun guard: {reason}"}
    unseen = unseen_gate(parsed)
    if unseen:
        return ask(
            f"this runs {unseen}, an outward action, in a form the permission rules do not see (a full path, "
            "git's options before its subcommand, a wrapper, a substitution, bash -c or eval); confirm it"
        )
    return ask(f"{parsed.unread}; confirm it") if parsed.unread else None


def decide(event: dict[str, Any]) -> dict[str, str] | None:
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    if tool == "Bash":
        return bash_decision(str(tool_input.get("command", "")))
    verb = mcp_write_verb(tool)
    if verb:
        return ask(f"{tool} can change things ('{verb}'); confirm it")
    action = chrome_action_changes(tool, tool_input)
    if action:
        return ask(f"Chrome's {action} can change the page; confirm it")
    return None


def ask(reason: str) -> dict[str, str]:
    return {"permissionDecision": "ask", "permissionDecisionReason": f"bosun guard: {reason}"}


# ---- approvals (BOSUN_APPROVALS_ENABLED; docs/design/approvals.md) -------------------------
#
# Outward calls the approvals hooks gate. The orchestrator settings' `ask` rules name the Slack and
# gh ones; the rest (CI builds, tracker and wiki writes) get their prompt from the plain guard's
# write verbs. Either prompt stays as the backstop (a hook that fails or decides nothing leaves
# Claude Code's prompt); for a gated command in a form those rules do not see, the plain guard asks
# (unseen_gate). MCP tools by their tool part (any server), Bash commands by their leading words
# (compared: the command's name, git's subcommand past its options) anywhere in what the call runs
# (parse).
GATED_MCP = {
    "slack_send_message": "a Slack message",
    "slack_schedule_message": "a scheduled Slack message",
    "slack_add_reaction": "a Slack reaction",
    "slack_create_canvas": "a Slack canvas",
    "slack_update_canvas": "a Slack canvas edit",
    "slack_create_conversation": "a new Slack conversation",
    # CI build tools (as a CI server's MCP tools name them): gated so an approved re-run goes
    # through the PermissionRequest hook instead of waiting at the plain guard's prompt.
    "restart_build": "a CI build restart",
    "create_build": "a new CI build",
    "promote_build": "a build promotion",
    "stop_build": "stopping a CI build",
}
# Tracker and wiki tools are gated when their name has one of these words and a write verb
# (jira_add_comment, editJiraIssue, createConfluencePage).
GATED_MCP_FAMILIES = ("jira", "confluence")
GATED_COMMANDS = (
    (("gh", "pr", "merge"), "a merge"),
    (("gh", "pr", "ready"), "marking a PR ready for review"),
    (("gh", "pr", "update-branch"), "updating a PR branch from its base (a push)"),
    (("gh", "pr", "edit"), "a PR edit"),
    (("gh", "pr", "comment"), "a PR comment"),
    (("gh", "pr", "review"), "a PR review"),
    (("gh", "pr", "close"), "closing a PR"),
    (("gh", "pr", "create"), "a new PR"),
    (("gh", "issue", "comment"), "an issue comment"),
    (("gh", "issue", "create"), "a new issue"),
    (("gh", "issue", "edit"), "an issue edit"),
    (("gh", "issue", "close"), "closing an issue"),
    (("gh", "release", "create"), "a new release"),
    (("gh", "workflow", "run"), "starting a workflow"),
    (("gh", "run", "rerun"), "running a workflow run again"),
    (("gh", "api"), "a GitHub API call"),
    (("git", "push"), "a push"),
)
HOOK_PRE_TOOL_USE = "PreToolUse"
HOOK_PERMISSION_REQUEST = "PermissionRequest"
PROPOSE_HINT = (
    "Propose it with its exact call: bosun ledger propose '<id>' --send slack --channel <channel "
    "id> [--thread <ts>] --text - <<'EOF' ... EOF (or --run '<the exact command>', or --tool <tool> "
    "--args-json - for other MCP tools), list it under Needs your approval, and once the user "
    "approves, run bosun ledger approve '<id>' --quote '<their exact words>' and retry exactly "
    "the same call."
)


def gated_mcp(tool: str) -> str | None:
    if not tool.startswith("mcp__"):
        return None
    name = approvals.tool_suffix(tool)
    if name in GATED_MCP:
        return GATED_MCP[name]
    family = next((word for word in GATED_MCP_FAMILIES if word in name_words(tool)), None)
    if family and mcp_write_verb(tool):
        return f"a {family} change"
    return None


Gate = tuple[tuple[str, ...], str]


def extra_gates(setting: str) -> tuple[Gate, ...]:
    """A site's own outward commands (BOSUN_APPROVALS_GATED_COMMANDS: prefixes separated by ',')."""
    prefixes = (tuple(part.split()) for part in setting.split(","))
    return tuple((prefix, f"a site-gated command ({' '.join(prefix)})") for prefix in prefixes if prefix)


def gate_of(words: list[str], gates: tuple[Gate, ...]) -> Gate | None:
    """The gate a simple command falls under, compared by its command's name (compared), or None."""
    seen = compared(words)
    return next(((prefix, what) for prefix, what in gates if tuple(seen[: len(prefix)]) == prefix), None)


def gated_bash(commands: list[list[str]], extra: tuple[Gate, ...] = ()) -> str | None:
    gates = (*GATED_COMMANDS, *extra)
    return next((gate[1] for words in commands if (gate := gate_of(words, gates))), None)


def outward_call(event: dict[str, Any], extra: tuple[Gate, ...] = ()) -> tuple[approvals.Call, str] | None:
    """The call in a hook event and what it does, if it is a gated outward call."""
    tool = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    if tool == "Bash":
        commands = simple_commands(str(tool_input.get("command", "")))
        what = gated_bash(commands, extra)
        return (approvals.Call(tool, tool_input, commands), what) if what else None
    what = gated_mcp(tool)
    return (approvals.Call(tool, tool_input), what) if what else None


@dataclass(frozen=True)
class ApprovalSettings:
    """What `bosun guard` passes from the config (load_config does not export it)."""

    enabled: bool
    deny_unapproved: bool
    state_dir: str
    speaker: approvals.Speaker
    standing: approvals.Standing
    gates: tuple[Gate, ...] = ()
    check_review_threads: bool = False
    reviewer_bots: str = ""  # BOSUN_REVIEWER_BOTS (lib/pr_threads.py), read when a PR is
    verdict_pattern: str = ""  # BOSUN_REVIEWER_BOT_VERDICT_PATTERN

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ApprovalSettings:
        state_dir = env.get("BOSUN_STATE_DIR", "")
        return cls(
            enabled=env.get("BOSUN_APPROVALS_ENABLED") == "1" and bool(state_dir),
            deny_unapproved=env.get("BOSUN_APPROVALS_DENY_UNAPPROVED", "1") != "0",
            state_dir=state_dir,
            speaker=events.speaker(
                state_dir, env.get("BOSUN_ORCHESTRATOR", ""), env.get("BOSUN_DRAIN_PROMPT", "")
            ),
            standing=approvals.Standing.from_settings(
                env.get("BOSUN_STANDING_CLASSES", ""),
                env.get("BOSUN_STANDING_SLACK_CHANNELS", ""),
                env.get("BOSUN_STANDING_SLACK_MENTIONS", ""),
                env.get("BOSUN_STANDING_GH_REVIEWERS", ""),
                env.get("BOSUN_STANDING_GH_COMMENTS", ""),
                env.get("BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR", ""),
            ),
            gates=extra_gates(env.get("BOSUN_APPROVALS_GATED_COMMANDS", "")),
            # `bosun guard` always passes it (default 1); unset (tests): off, no network.
            check_review_threads=env.get("BOSUN_APPROVALS_CHECK_REVIEW_THREADS", "0") == "1",
            reviewer_bots=env.get(pr_threads.REVIEWER_BOTS_SETTING, ""),
            verdict_pattern=env.get(pr_threads.VERDICT_PATTERN_SETTING, ""),
        )


def use_ledger(settings: ApprovalSettings) -> None:
    """The ledger in <settings>' state dir, the guard's writes named so in its journal."""
    ledger.configure(settings.state_dir)
    ledger.configure_command("guard")


def judge(
    event: dict[str, Any], call: approvals.Call, settings: ApprovalSettings, claim: bool
) -> approvals.Verdict:
    """The verdict on a gated call; with <claim>, an allowed call uses up its approval (recorded,
    with the call's hash, session and transcript for the PostToolUse hook)."""
    use_ledger(settings)
    prompts = approvals.transcript_prompts(str(event.get("transcript_path") or ""))
    with ledger.locked_ledger(write=claim) as book:
        verdict = approvals.evaluate(book["items"], call, prompts, settings.speaker, settings.standing)
        if claim and verdict.allowed:
            ledger.use_approval(
                book["items"][verdict.item_id], verdict, call, approvals.CallContext.of(event)
            )
    return verdict


# ---- the review-bump check (BOSUN_APPROVALS_CHECK_REVIEW_THREADS) ---------------------------
#
# A message that asks for a re-review says the review is addressed. Before it goes out, the live PR
# must agree: no thread awaits the author, and no review came after the message was proposed.

REVIEW_PURPOSE = "review"
BUMP_CHECK_SECONDS = 20.0  # within the hooks' 30 s timeout (orchestrator_extra_settings)
ADD_REVIEWER = "--add-reviewer"
GH_PR_EDIT = ["gh", "pr", "edit"]
MAX_LISTED = 3
# How the check reads a PR (tests replace it): (url, seconds) -> the PR.
FETCH_PR: pr_threads.Fetch = pr_threads.read


@dataclass(frozen=True)
class Bump:
    """A call asking for a re-review: the item it carries out, the PR, and when the message was
    proposed."""

    item_id: str
    pr_url: str
    since: str


@dataclass(frozen=True)
class BumpCheck:
    problem: str = ""  # why the live PR disagrees with the message
    error: str = ""  # why the PR could not be read


def outreach_pr(item: dict[str, Any]) -> str:
    """The PR an outreach item is about: its parent's, else a PR URL in its url, subject or details."""
    fields = item.get("fields") or {}
    parent = str(fields.get("parent") or "")
    if parent.startswith(ledger.id_prefix(approvals.MY_PR_KIND)):
        return approvals.pr_url(parent)
    for text in (item.get("url"), fields.get("subject"), fields.get("details")):
        match = ledger.PR_URL.search(str(text or ""))
        if match:
            return f"https://github.com/{match['repo']}/pull/{match['number']}"
    return ""


def adds_reviewer(call: approvals.Call) -> bool:
    return any(
        words[: len(GH_PR_EDIT)] == GH_PR_EDIT and any(word.startswith(ADD_REVIEWER) for word in words)
        for words in call.commands
    )


def posts_comment(call: approvals.Call, bodies: frozenset[str]) -> bool:
    """A `gh pr comment` whose body is one of the standing comments (a re-review command)."""
    return any(approvals.posted_comment(words) in bodies for words in call.commands)


def bump_pr(item: dict[str, Any], call: approvals.Call, bodies: frozenset[str] = frozenset()) -> str:
    """The PR whose re-review <call> asks for on behalf of <item>, or "": review outreach, adding a
    reviewer, or posting one of the standing comments <bodies> on my PR."""
    if item.get("kind") == ledger.OUTREACH_KIND:
        is_review = (item.get("fields") or {}).get("purpose") == REVIEW_PURPOSE
        return outreach_pr(item) if is_review else ""
    if item.get("kind") == approvals.MY_PR_KIND and (adds_reviewer(call) or posts_comment(call, bodies)):
        return approvals.pr_url(str(item["id"]))
    return ""


def proposed_at(item: dict[str, Any]) -> str:
    """When the message was written: its proposal's time, else the item's last change."""
    proposal = item.get("proposal") or {}
    return str(proposal.get("proposed_at") or item.get("updated_at") or item.get("created_at") or "")


def bump_target(
    items: dict[str, Any], item_id: str, call: approvals.Call, bodies: frozenset[str] = frozenset()
) -> Bump | None:
    """The re-review request <call> makes for <item_id>, if it is one."""
    item = items.get(item_id) or {}
    url = bump_pr(item, call, bodies) if item else ""
    return Bump(item_id, url, proposed_at(item)) if url else None


def listed(urls: list[str]) -> str:
    more = f" and {len(urls) - MAX_LISTED} more" if len(urls) > MAX_LISTED else ""
    return ", ".join(urls[:MAX_LISTED]) + more


def bump_problem(pr: pr_threads.PullRequest, bump: Bump) -> str:
    """Why the live PR disagrees with a re-review request proposed at bump.since, or ""."""
    problems = []
    waiting = pr_threads.awaiting(pr)
    if waiting:
        urls = [t.first.url for t in waiting]
        problems.append(f"{len(waiting)} review thread(s) await a reply from the author ({listed(urls)})")
    newer = pr_threads.reviews_after(pr, bump.since) if bump.since else []
    if newer:
        last = newer[-1]
        problems.append(
            f"{len(newer)} review(s) came after the message was proposed at {bump.since} (the latest: "
            f"{last.state} by {last.author} at {last.at})"
        )
    if not problems:
        return ""
    return (
        f"this asks for a re-review of {bump.pr_url} for {bump.item_id}, but the live PR disagrees: "
        f"{'; '.join(problems)} (rounds: {pr_threads.round_summary(pr)}). Run bosun pr-threads "
        f"'{bump.pr_url}' --awaiting-author, hand what it lists to the PR's window (bosun delegate "
        "--update '<mypr id>'), and once nothing awaits the author, write the message again from that "
        "output and propose it again."
    )


def check_bump(call: approvals.Call, verdict: approvals.Verdict, settings: ApprovalSettings) -> BumpCheck:
    """Whether an allowed call that asks for a re-review agrees with the live PR (no network for
    any other call, or with the check off)."""
    if not (settings.check_review_threads and verdict.allowed):
        return BumpCheck()
    with ledger.locked_ledger(write=False) as book:
        bump = bump_target(book["items"], verdict.item_id, call, settings.standing.gh_comments)
    if bump is None:
        return BumpCheck()
    try:
        reviewers = pr_threads.ReviewerBots.from_settings(settings.reviewer_bots, settings.verdict_pattern)
        pr = FETCH_PR(bump.pr_url, BUMP_CHECK_SECONDS, reviewers)
    except pr_threads.PrThreadsError as exc:
        return BumpCheck(error=f"could not read {bump.pr_url} to check its review threads ({exc})")
    return BumpCheck(problem=bump_problem(pr, bump))


def bump_decision(check: BumpCheck) -> dict[str, str] | None:
    """PreToolUse's answer to a re-review request: deny what the PR disagrees with, ask (Claude
    Code's prompt) when the PR could not be read, otherwise no decision."""
    if check.problem:
        return {"permissionDecision": "deny", "permissionDecisionReason": f"bosun guard: {check.problem}"}
    if check.error:
        reason = f"bosun guard: {check.error}; confirm only if no review thread awaits the author"
        return {"permissionDecision": "ask", "permissionDecisionReason": reason}
    return None


def decide_approvals(event: dict[str, Any], settings: ApprovalSettings) -> dict[str, str] | None:
    """PreToolUse: deny a gated outward call that is neither approved nor covered by a standing
    class, telling the orchestrator how to get it approved; otherwise no decision (an approved call
    goes on to the PermissionRequest hook, which allows it). An allowed re-review request goes
    through bump_decision first."""
    found = outward_call(event, settings.gates) if settings.enabled else None
    if found is None:
        return None
    call, what = found
    verdict = judge(event, call, settings, claim=False)
    if verdict.allowed:
        return bump_decision(check_bump(call, verdict, settings))
    if not settings.deny_unapproved:
        return None
    reason = approved_inside(event, call, settings) or (
        f"bosun guard: this is {what}, an outward action that needs the user's approval first: "
        f"{verdict.reason}. {PROPOSE_HINT}"
    )
    return {"permissionDecision": "deny", "permissionDecisionReason": reason}


def command_words(command: str) -> list[str]:
    """A command's words as the shell splits them (empty for unbalanced quotes)."""
    try:
        return shlex.split(command)
    except ValueError:
        return []


def approved_commands(event: dict[str, Any], settings: ApprovalSettings) -> list[tuple[str, str]]:
    """(item id, command) of each approved, unused run proposal whose approval would count now."""
    prompts = approvals.transcript_prompts(str(event.get("transcript_path") or ""))
    use_ledger(settings)
    with ledger.locked_ledger(write=False) as book:
        return [
            (item["id"], proposal["spec"]["command"])
            for item, proposal in approvals.pending_specs(book["items"])
            if proposal["spec"].get("type") == approvals.RUN
            and approvals.approval_problem(proposal, prompts, settings.speaker, item["id"]) is None
        ]


def approved_inside(event: dict[str, Any], call: approvals.Call, settings: ApprovalSettings) -> str | None:
    """The denial reason for a Bash call that holds an approved command with more around it (a
    wrapper such as timeout, a pipe, another step): approvals match the exact command only."""
    if call.tool != "Bash":
        return None
    ran = [[word for word in words if not REDIRECTION.match(word)] for words in call.commands]
    for item_id, command in approved_commands(event, settings):
        if command_words(command) in ran:
            return (
                f"bosun guard: this call holds the approved command of {item_id} with more "
                f"around it (a wrapper such as timeout, a pipe or another step), and an approval "
                f"matches only the exact command. Run exactly `{command}` as a Bash call of its "
                f"own, then check the result in a separate call."
            )
    return None


def decide_permission_request(event: dict[str, Any], settings: ApprovalSettings) -> dict[str, Any] | None:
    """PermissionRequest: allow a gated call that carries out an approved proposal (or a standing
    class), using the approval up; anything else gets no decision, so Claude Code asks as before.
    A re-review request is allowed only while the live PR agrees with it (check_bump)."""
    found = outward_call(event, settings.gates) if settings.enabled else None
    if found is None:
        return None
    if settings.check_review_threads:
        check = check_bump(found[0], judge(event, found[0], settings, claim=False), settings)
        if check.problem or check.error:
            return None
    verdict = judge(event, found[0], settings, claim=True)
    return {"behavior": "allow"} if verdict.allowed else None


def after_call(event: dict[str, Any], settings: ApprovalSettings) -> str:
    """PostToolUse / PostToolUseFailure: if the gated call that ended used an approval (the same
    call, session and time window), settle its proposal by the result (ledger.settle_call). Never
    decides anything; returns what it recorded, for tests. Reads first and takes the write lock
    only for a match, which it checks again under the lock (another result may have settled it)."""
    found = outward_call(event, settings.gates) if settings.enabled else None
    if found is None or event.get("hook_event_name") not in POST_EVENTS:
        return ""
    call, context, moment = found[0], approvals.CallContext.of(event), datetime.now(UTC)
    use_ledger(settings)
    with ledger.locked_ledger(write=False) as book:
        if approvals.carried_out_by(book["items"], call, context, moment) is None:
            return ""
    with ledger.locked_ledger(write=True) as book:
        item = approvals.carried_out_by(book["items"], call, context, moment)
        return ledger.settle_call(item, call, approvals.call_outcome(event), context) if item else ""


MODES = {
    "": HOOK_PRE_TOOL_USE,
    "--approvals": HOOK_PRE_TOOL_USE,
    "--permission-request": HOOK_PERMISSION_REQUEST,
    "--post-tool-use": approvals.POST_TOOL_USE,
}
POST_EVENTS = (approvals.POST_TOOL_USE, approvals.POST_TOOL_USE_FAILURE)


def respond(mode: str, event: dict[str, Any], env: Mapping[str, str]) -> dict[str, Any] | None:
    """The hook output for <event> in <mode> (see MODES), or None for no decision."""
    if mode == "":
        decision = decide(event)
        return {"hookEventName": HOOK_PRE_TOOL_USE, **decision} if decision else None
    settings = ApprovalSettings.from_env(env)
    if mode == "--post-tool-use":
        after_call(event, settings)
        return None
    if mode == "--approvals":
        denial = decide_approvals(event, settings)
        return {"hookEventName": HOOK_PRE_TOOL_USE, **denial} if denial else None
    allowed = decide_permission_request(event, settings)
    return {"hookEventName": HOOK_PERMISSION_REQUEST, "decision": allowed} if allowed else None


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    mode = argv[0] if argv else ""
    if mode not in MODES:
        print(f"bosun guard: unknown mode {mode!r}", file=sys.stderr)
        return 0
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    try:
        output = respond(mode, event, os.environ if env is None else env)
    except update_gate.WriteRefused:
        raise  # update_gate.guarded runs it again on the installed version, which decides
    except Exception as exc:  # noqa: BLE001  (a hook never blocks the session: Claude Code then asks)
        print(f"bosun guard {mode}: {exc!r}; no decision", file=sys.stderr)
        return 0
    if output:
        print(json.dumps({"hookSpecificOutput": output}))
    return 0


if __name__ == "__main__":
    update_gate.guarded("guard", lambda argv: main(argv, os.environ), sys.argv[1:])
    sys.exit(0)  # always: a hook never blocks the session, and a refusal was said on stderr
