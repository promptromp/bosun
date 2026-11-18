"""The guard on what each smoke section says it covers: every bosun command a section runs is in its `covers`.

The selection (tools/smoke_select.py) runs a section when a changed file matches its `covers` or depends on
one that does. A section that runs `bosun init` without covering libexec/bosun-init does not run when
bosun-init changes, though it would show that change failing. So every command a section runs, in its own
lines, in a helper it defines, or through a helper it calls (one of the shared setup's or of its own, all the
way), must match one of its own `covers` globs. What counts as running a command:

- `"$BOSUN" ledger`, `bosun-ledger`, `bosun ledger` where a command starts (the patterns the selection reads,
  smoke_select.SHELL_CALL), anywhere in the code (comments aside);
- a helper called where a command starts, or as the command a check runs (`check "..." helper`, `fails_with
  "..." helper`, `wait_for 30 helper`): its own code, read the same way;
- a helper that runs `"$BOSUN" "$@"` (`dkg kg import`): the word after it is the command it runs.

A command a section names only in a message is no run; ALLOWED lists the few places where the patterns read
one, each with why."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import shell_library  # noqa: E402
import smoke_select as ss  # noqa: E402


# Where a command starts: a line's start, after `;`, `&`, `|`, `(`, `{`, a backquote, `$(`, `!` or a reserved
# word; and the command that check, fails_with, refused_quietly, fails_with_status, wait_for and
# exits_0_saying_nothing run.
RUNNER = (
    r"(?:(?:check|fails_with|refused_quietly)\s+\"(?:[^\"\\]|\\.)*\"\s+"
    r"|fails_with_status\s+\S+\s+\"(?:[^\"\\]|\\.)*\"\s+|wait_for\s+\S+\s+|exits_0_saying_nothing\s+)"
)
COMMAND_START = rf"(?:^|[;&|({{`!]|\$\(|\b(?:then|do|else|elif|if|while|until|time)\b)\s*{RUNNER}?"
BOSUN_RUNNER = re.compile(r"\"\$\{?BOSUN\w*\}?\"\s+\"\$@\"")  # a helper whose arguments are a bosun command's
# (section, command): where the patterns read a run that is not one, and why.
ALLOWED: dict[tuple[str, str], str] = {
    ("orchestrator", "libexec/bosun-kg"): "a jq check that the settings allow `Bash(bosun kg propose:*)`",
    (
        "expired-login",
        "libexec/bosun-refresh",
    ): "doctor's advice, grepped for: `restart the window (bosun refresh orch)`",
    (
        "expired-login",
        "libexec/bosun-send",
    ): "doctor's advice, grepped for: `tell it to carry on (bosun send orch ...)`",
    ("settings", "libexec/bosun-update"): "a log grepped for the refusal's words, `bosun update is running`",
}


def commands() -> dict[str, str]:
    """`ledger` -> libexec/bosun-ledger, for every command there is."""
    return {p.name[len("bosun-") :]: f"libexec/{p.name}" for p in (ROOT / "libexec").glob("bosun-*")}


class SuiteRuns:
    """What the sections of tests/smoke.sh run, through their helpers too."""

    def __init__(self, text: str) -> None:
        self.suite = ss.Suite(text)
        self.commands = commands()
        self.code: dict[tuple[str, str], str] = {}
        for number in range(1, len(self.suite.script.lines) + 1):
            region = self.suite.region(number)
            if region is not None:
                line = self.suite.script.code_lines[number - 1]
                self.code[region] = self.code.get(region, "") + line + "\n"
        helpers = sorted(set(self.suite.helper_at.values()), key=len, reverse=True)
        names = "|".join(map(re.escape, helpers))
        self.call = re.compile(
            rf"{COMMAND_START}(?P<helper>{names})(?=[\s;&|)]|$)(?:\s+(?P<arg>[a-z][a-z0-9-]*))?", re.MULTILINE
        )
        self.runners = {
            name for (kind, name), code in self.code.items() if kind == "helper" and BOSUN_RUNNER.search(code)
        }

    def own(self, sid: str) -> set[tuple[str, str]]:
        """A section's own code: its lines, and the helpers defined in them."""
        mine = {n for n, s in self.suite.section_at.items() if s == sid}
        return {("section", sid)} | {
            ("helper", name) for n, name in self.suite.helper_at.items() if n in mine
        }

    def runs(self, sid: str) -> dict[str, str]:
        """Each command section <sid> runs -> where (its own code, or the helper that runs it)."""
        found: dict[str, str] = {}
        todo = list(self.own(sid))
        seen = set(todo)
        while todo:
            node = todo.pop()
            where = (
                "its own code" if node[0] == "section" or node in self.own(sid) else f"the helper {node[1]}"
            )
            for command in self.direct(self.code.get(node, "")):
                found.setdefault(command, where)
            for match in self.call.finditer(self.code.get(node, "")):
                helper = ("helper", match["helper"])
                if match["helper"] in self.runners and match["arg"] in self.commands:
                    found.setdefault(self.commands[match["arg"]], f"the helper {match['helper']}")
                if helper not in seen:
                    seen.add(helper)
                    todo.append(helper)
        return found

    def direct(self, code: str) -> set[str]:
        found = set()
        for match in ss.SHELL_CALL.finditer(code):
            command = match["dash"] or match["var"] or match["shell"]
            if command in self.commands:
                found.add(self.commands[command])
        return found


class CoversTest(unittest.TestCase):
    runs: SuiteRuns

    @classmethod
    def setUpClass(cls) -> None:
        cls.runs = SuiteRuns((ROOT / "tests" / "smoke.sh").read_text())

    def test_every_command_a_section_runs_is_in_its_covers(self) -> None:
        missing = [
            f"{section.id}: {command} (run by {where})"
            for section in self.runs.suite.sections
            for command, where in sorted(self.runs.runs(section.id).items())
            if not any(ss.matches(command, glob) for glob in section.covers)
            and (section.id, command) not in ALLOWED
        ]
        self.assertEqual(
            missing,
            [],
            "\n\nThese sections run a bosun command their `covers` do not name, so a change to the command "
            "would not run them. Add the command (libexec/bosun-<name>) to the section's `covers '...'` in "
            "tests/smoke.sh; if the patterns read a run that is not one (a message), add it to ALLOWED in "
            "tests/unit/test_smoke_covers.py with why:",
        )

    def test_each_allowed_place_is_still_read_as_a_run(self) -> None:
        """An allowance that no longer matches anything is left over: remove it."""
        for sid, command in ALLOWED:
            with self.subTest(section=sid, command=command):
                self.assertIn(command, self.runs.runs(sid))


class ReadingTest(unittest.TestCase):
    """What the guard counts as running a command, on a made-up suite."""

    SUITE = """\\
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
"""

    def test_runs_direct_through_a_helper_and_through_a_bosun_runner(self) -> None:
        runs = SuiteRuns(self.SUITE)
        self.assertEqual(sorted(runs.runs("one")), ["libexec/bosun-ledger"])
        self.assertEqual(sorted(runs.runs("two")), ["libexec/bosun-kg"])
        self.assertEqual(sorted(runs.runs("three")), ["libexec/bosun-init", "libexec/bosun-quit"])
        self.assertEqual(
            shell_library.calls("printf '%q'", {"q"}), {"q"}
        )  # (what the loose reading would see)


if __name__ == "__main__":
    unittest.main()
