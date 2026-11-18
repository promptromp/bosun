"""The guard on what each smoke section says it covers: every bosun command a section runs is in its `covers`.

The selection (tools/smoke_select.py) runs a section when a changed file matches its `covers` or depends on
one that does. A section that runs `bosun init` without covering libexec/bosun-init does not run when
bosun-init changes, though it would show that change failing. So every command a section runs must match one
of its own `covers` globs. What this reads as running a command, in a section's own lines, in a helper it
defines, and in a helper it calls (the shared setup's or its own, all the way):

- `"$BOSUN" ledger`, `bosun-ledger`, or `bosun ledger` where a command starts (the patterns the selection
  reads, smoke_select.SHELL_CALL), anywhere in the code (comments aside);
- `"$1" ledger` (`$1`, `"${1}"`, any digit) inside a `bash -c '...'` whose arguments after its `_` pass bosun
  at that place, and `"${@:2}" ledger` (any number) where they pass it at that place or after (bosun:
  `"$BOSUN"`, or a path that ends in `bin/bosun`);
- a helper that runs `"$BOSUN" "$@"`, or a path that ends in `bin/bosun` with `"$@"` (`dkg kg import`,
  `bb ledger list`): the word after it.

A helper counts as called where a command starts, or as the command check, fails_with, fails_with_status,
refused_quietly, wait_for or exits_0_saying_nothing runs; a call in another shape (an alias, another runner, a
name built at run time) is not followed. A command a section names only in a message is no run: ALLOWED lists
those places, each by the command and its exact line (as tests/unit/test_smoke_timing.py does), with why.

And the shared setup may only call the helpers it defines: a section's helper is defined only in a run that
runs that section, and the selection reads the shared setup's calls so (smoke_select.Suite)."""

from __future__ import annotations

import bisect
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import smoke_select as ss  # noqa: E402


# Where a command starts: a line's start, after `;`, `&`, `|`, `(`, `{`, a backquote, `$(`, `!` or a reserved
# word; and the command that check, fails_with, refused_quietly, fails_with_status, wait_for and
# exits_0_saying_nothing run.
RUNNER = (
    r"(?:(?:check|fails_with|refused_quietly)\s+\"(?:[^\"\\]|\\.)*\"\s+"
    r"|fails_with_status\s+\S+\s+\"(?:[^\"\\]|\\.)*\"\s+|wait_for\s+\S+\s+|exits_0_saying_nothing\s+)"
)
COMMAND_START = rf"(?:^|[;&|({{`!]|\$\(|\b(?:then|do|else|elif|if|while|until|time)\b)\s*{RUNNER}?"
# A helper whose arguments are a bosun command's: it runs `"$BOSUN" "$@"`, or a path ending in bin/bosun so.
BOSUN_RUNNER = re.compile(r"\"(?:\$\{?BOSUN\w*\}?|[^\"\s]*bin/bosun)\"\s+\"\$@\"")
# `bash -c '<script>' _ <arguments>` (the script's quotes as the suite writes them: '\'' inside).
BASH_C = re.compile(r"\bbash\s+-c\s+'(?P<script>(?:[^']|'\\'')*)'\s+_(?P<args>[^\n]*)")
# The arguments, up to the first unquoted `;`, `|`, `&`, `)`, `<` or `>`.
ARGUMENTS = re.compile(r"^(?:\s+(?:\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^\s;&|)<>\"']+))*")
ARGUMENT = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'[^']*'|\S+")
IS_BOSUN = re.compile(r"^\"?(?:\$\{?BOSUN\}?|[^\"\s]*bin/bosun)\"?$")
# `"${@:2}" <cmd>`: the arguments from the second on, bosun among them when it is passed at 2 or after.
SLICE_RUN = re.compile(r"(?<![\w$=])\"\$\{@:(?P<start>\d+)\}\"\s+(?P<cmd>[a-z][a-z0-9-]*)")
# (command, the whole line without its indent): where the patterns read a run that is not one, and why.
ALLOWED: dict[tuple[str, str], str] = {
    (
        "libexec/bosun-kg",
        (
            'jq -e "(.permissions.allow | index(\\"Bash(bosun kg propose:*)\\") and index(\\"Bash(bosun '
            'kg lookup:*)\\") and index(\\"Bash(bosun kg add-entity:*)\\"))'
        ),
    ): "the orchestrator section's jq check that the settings allow `Bash(bosun kg propose:*)`",
    (
        "libexec/bosun-refresh",
        (
            'check "doctor fails the window, saying what to do" bash -c \'grep -qF "FAIL  window orch '
            'no-credit every reply since" <<<"$1" && grep -qF "then tell it to carry on (bosun send '
            "orch ...); or use your subscription: remove the API key (ANTHROPIC_API_KEY in your shell "
            "profile, or apiKeyHelper in ~/.claude/settings.json), restart the window (bosun refresh "
            'orch), then /login in it" <<<"$1"\' _ "$(LC doctor)"'
        ),
    ): "expired-login greps doctor's advice: `restart the window (bosun refresh orch)`",
    (
        "libexec/bosun-send",
        (
            'check "doctor fails the window, saying what to do" bash -c \'grep -qF "FAIL  window orch '
            'no-credit every reply since" <<<"$1" && grep -qF "then tell it to carry on (bosun send '
            "orch ...); or use your subscription: remove the API key (ANTHROPIC_API_KEY in your shell "
            "profile, or apiKeyHelper in ~/.claude/settings.json), restart the window (bosun refresh "
            'orch), then /login in it" <<<"$1"\' _ "$(LC doctor)"'
        ),
    ): "expired-login greps doctor's advice: `tell it to carry on (bosun send orch ...)`",
    (
        "libexec/bosun-update",
        (
            'check "a refusal is never logged as an error (the watchdog alerts on those)" bash -c \'! '
            'grep -rqE "25:00|bosun update is running|needs your password" "$1/logs" 2>/dev/null\' _ '
            '"$SET_STATE"'
        ),
    ): "settings greps the logs for the refusal's words, `bosun update is running`",
}


def commands() -> dict[str, str]:
    """`ledger` -> libexec/bosun-ledger, for every command there is."""
    return {p.name[len("bosun-") :]: f"libexec/{p.name}" for p in (ROOT / "libexec").glob("bosun-*")}


class Code:
    """A node's code (comments blanked) with the line each character came from."""

    def __init__(self) -> None:
        self.text = ""
        self.starts: list[int] = []  # where each line begins in text
        self.numbers: list[int] = []  # and its number in the file

    def add(self, number: int, line: str) -> None:
        self.starts.append(len(self.text))
        self.numbers.append(number)
        self.text += line + "\n"

    def line_at(self, offset: int) -> int:
        return self.numbers[bisect.bisect_right(self.starts, offset) - 1]


class SuiteRuns:
    """What the sections of tests/smoke.sh run, through their helpers too."""

    def __init__(self, text: str) -> None:
        self.suite = ss.Suite(text)
        self.commands = commands()
        self.code: dict[tuple[str, str], Code] = {}
        for number in range(1, len(self.suite.script.lines) + 1):
            region = self.suite.region(number)
            if region is not None:
                self.code.setdefault(region, Code()).add(number, self.suite.script.code_lines[number - 1])
        helpers = sorted(set(self.suite.helper_at.values()), key=len, reverse=True)
        names = "|".join(map(re.escape, helpers))
        self.call = re.compile(
            rf"{COMMAND_START}(?P<helper>{names})(?=[\s;&|)]|$)(?:\s+(?P<arg>[a-z][a-z0-9-]*))?", re.MULTILINE
        )
        self.runners = {
            name
            for (kind, name), code in self.code.items()
            if kind == "helper" and BOSUN_RUNNER.search(code.text)
        }

    def line(self, number: int) -> str:
        return self.suite.script.lines[number - 1].strip()

    def own(self, sid: str) -> set[tuple[str, str]]:
        """A section's own code: its lines, and the helpers defined in them."""
        mine = {n for n, s in self.suite.section_at.items() if s == sid}
        return {("section", sid)} | {
            ("helper", name) for n, name in self.suite.helper_at.items() if n in mine
        }

    def runs(self, sid: str) -> dict[tuple[str, int], str]:
        """(command, line) -> where, for each command section <sid> runs (its own code, or the helper that
        runs it)."""
        found: dict[tuple[str, int], str] = {}
        todo = list(self.own(sid))
        seen = set(todo)
        while todo:
            node = todo.pop()
            code = self.code.get(node, Code())
            where = "its own code" if node in self.own(sid) else f"the helper {node[1]}"
            for command, number in self.direct(code):
                found.setdefault((command, number), where)
            for helper in self.helper_calls(code, found, where):
                if helper not in seen:
                    seen.add(helper)
                    todo.append(helper)
        return found

    def helper_calls(
        self, code: Code, found: dict[tuple[str, int], str], where: str
    ) -> list[tuple[str, str]]:
        """The helpers <code> calls; a bosun runner's command goes in <found> as it is read."""
        calls = []
        for match in self.call.finditer(code.text):
            number = code.line_at(match.start("helper"))
            if match["helper"] in self.runners and match["arg"] in self.commands:
                found.setdefault(
                    (self.commands[match["arg"]], number), f"the helper {match['helper']} ({where})"
                )
            calls.append(("helper", match["helper"]))
        return calls

    def direct(self, code: Code) -> list[tuple[str, int]]:
        """The commands <code> runs itself: the selection's patterns, and bosun passed into a `bash -c`."""
        found = []
        for match in ss.SHELL_CALL.finditer(code.text):
            command = match["dash"] or match["var"] or match["shell"]
            if command in self.commands:
                found.append((self.commands[command], code.line_at(match.start())))
        for match in BASH_C.finditer(code.text):
            found.extend(self.passed_in(code, match))
        return found

    def passed_in(self, code: Code, match: re.Match[str]) -> list[tuple[str, int]]:
        """The commands a `bash -c` runs through bosun passed in as a positional parameter (`"$2" <cmd>`), or
        among those a `"${@:N}"` expands to."""
        places = bosun_places(match["args"])
        runs = [run for place in places for run in re.finditer(positional_run(place), match["script"])]
        runs += [
            run for run in SLICE_RUN.finditer(match["script"]) if places and int(run["start"]) <= max(places)
        ]
        return [
            (self.commands[run["cmd"]], code.line_at(match.start("script") + run.start()))
            for run in runs
            if run["cmd"] in self.commands
        ]

    def shared_calls_to_sections(self) -> list[str]:
        """The shared setup's calls (where a command starts) to a helper a section defines."""
        defined_in_sections = {name for n, name in self.suite.helper_at.items() if n in self.suite.section_at}
        code = self.code.get((ss.HARNESS, ""), Code())
        return [
            f"line {code.line_at(match.start('helper'))}: {match['helper']}"
            for match in self.call.finditer(code.text)
            if match["helper"] in defined_in_sections
        ]


def positional_run(place: int) -> str:
    """`"$1" <cmd>` (or `$1`, `"${1}"`), not an assignment's value (`want="$1" status`)."""
    quoted = rf'(?<![\w$=])"\$\{{?{place}\}}?"'
    bare = rf'(?<![\w$="])\$\{{?{place}\}}?(?![\w"}}])'
    return rf"(?:{quoted}|{bare})\s+(?P<cmd>[a-z][a-z0-9-]*)"


def bosun_places(args: str) -> list[int]:
    """The positional parameters (1, 2, ...) a `bash -c`'s arguments after `_` pass bosun at, up to the first
    unquoted `;`, `|`, `&`, `)`, `<` or `>`."""
    places = []
    prefix = ARGUMENTS.match(args)
    for place, argument in enumerate(ARGUMENT.findall(prefix.group(0) if prefix else ""), 1):
        if IS_BOSUN.match(argument):
            places.append(place)
    return places


class CoversTest(unittest.TestCase):
    runs: SuiteRuns

    @classmethod
    def setUpClass(cls) -> None:
        cls.runs = SuiteRuns((ROOT / "tests" / "smoke.sh").read_text())

    def test_every_command_a_section_runs_is_in_its_covers(self) -> None:
        missing = [
            f"{section.id}: {command} (line {number}, run by {where})"
            for section in self.runs.suite.sections
            for (command, number), where in sorted(self.runs.runs(section.id).items())
            if not any(ss.matches(command, glob) for glob in section.covers)
            and (command, self.runs.line(number)) not in ALLOWED
        ]
        self.assertEqual(
            missing,
            [],
            "\n\nThese sections run a bosun command their `covers` do not name, so a change to the command "
            "would not run them. Add the command (libexec/bosun-<name>) to the section's `covers '...'` in "
            "tests/smoke.sh; if the patterns read a run that is not one (a message), add the command and the "
            "whole line to ALLOWED in tests/unit/test_smoke_covers.py, with why:",
        )

    def test_each_allowed_line_is_in_the_suite_once_and_still_read_as_that_run(self) -> None:
        """An allowance whose line changed, went or was copied is looked at again."""
        lines = [line.strip() for line in self.runs.suite.script.lines]
        runs = {
            (command, self.runs.line(number))
            for s in self.runs.suite.sections
            for command, number in self.runs.runs(s.id)
        }
        for command, line in ALLOWED:
            with self.subTest(line=line):
                self.assertEqual(lines.count(line), 1)
                self.assertIn((command, line), runs)

    def test_the_shared_setup_calls_no_helper_a_section_defines(self) -> None:
        self.assertEqual(
            self.runs.shared_calls_to_sections(),
            [],
            "\n\nThe shared setup (outside the sections, or an always_run part) calls a helper a section "
            "defines: it does not exist in a run that skips that section, and the selection would not see "
            "the call. Move the helper to the shared setup:",
        )


class ReadingTest(unittest.TestCase):
    """What the guard counts as running a command, on a made-up suite."""

    SUITE = """\
#!/bin/bash
L() { "$BOSUN" ledger "$@"; }
dkg() { BOSUN_CONFIG_DIR=x "$BOSUN" "$@"; }
q() { "$BOSUN" quit "$@"; }
section one "one" covers 'libexec/bosun-ledger'
check "a ledger item" L add
section two "two" covers 'libexec/bosun-kg'
dkg kg import x
wrapper="$(printf '%q' "$BOSUN")"  # a %q is no call of q
echo "run bosun doctor first"  # a message
section three "three" covers 'x'
out="$("$BOSUN" init --answers x)"
fails_with "busy" q --stop-now
check "facts" bash -c 'out="$("$2" watchdog --facts)"; [ -n "$out" ]
  "$2" sessions --json >/dev/null' _ "$ROOT" "$BOSUN" 2>&1
check "local is no run" bash -c 'local want="$1" status' _ "$BOSUN"
section four "four" covers 'x'
helper4() { :; }
always_run
helper4
section five "five" covers 'x'
lb() { "${L_ENV[@]}" "$L_LINK/bin/bosun" "$@"; }
lb ledger list
check "slice" bash -c 'cd "$1" && "${@:2}" launch alpha' _ "$REPO" "${ENV[@]}" "$BOSUN"
check "a slice past bosun's place" bash -c '"${@:3}" doctor' _ "$BOSUN" x y
"""

    def test_runs_direct_through_a_helper_a_bosun_runner_and_a_bash_c(self) -> None:
        runs = SuiteRuns(self.SUITE)

        def commands(sid: str) -> list[str]:
            return sorted({command for command, _ in runs.runs(sid)})

        self.assertEqual(commands("one"), ["libexec/bosun-ledger"])
        self.assertEqual(commands("two"), ["libexec/bosun-kg"])
        self.assertEqual(
            commands("three"),
            ["libexec/bosun-init", "libexec/bosun-quit", "libexec/bosun-sessions", "libexec/bosun-watchdog"],
        )
        self.assertEqual(sorted(n for c, n in runs.runs("three") if c == "libexec/bosun-sessions"), [15])
        self.assertEqual(commands("five"), ["libexec/bosun-launch", "libexec/bosun-ledger"])  # (no doctor)

    def test_the_shared_setup_calling_a_section_s_helper_is_found(self) -> None:
        self.assertEqual(SuiteRuns(self.SUITE).shared_calls_to_sections(), ["line 20: helper4"])

    def test_bosun_s_places_in_a_bash_c_s_arguments(self) -> None:
        self.assertEqual(bosun_places(' "$ROOT" "$BOSUN" 2>&1'), [2])
        self.assertEqual(bosun_places(' "$WORK/u/bin/bosun" x'), [1])
        self.assertEqual(bosun_places(' "$ROOT"; echo "$BOSUN"'), [])  # (after the `;`: another command's)


if __name__ == "__main__":
    unittest.main()
