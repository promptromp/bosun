"""The guard on the Mac VM suites' selection (tools/vm_select.py, `tests/sandbox.sh mac --changed`, CI's
install job): a run chooses parts by their `covers` and tests/vm_selection.txt, so a file that nothing names
would run run, work and app whole (it fails closed), and a part that says nothing about what it covers would
never be chosen. Caught here, on the commit, with what to add:

- every file git lists is on an `all` or `none` rule of tests/vm_selection.txt, or covered by a part; every
  function of lib/common.sh (read by function) is named by a part's `lib/common.sh#<glob>` or a rule;
- every part has a `covers`, every glob of it and of the rules matches a file (or a function), and a glob
  without `#` never names a file read by function;
- each suite's main body is its `part` lines and then `parts_end`, each part has its `part_<id>` and nothing
  else calls it, a `needs` names a part above it that is not `last`, and the `last` parts come after the
  others;
- every part has its `minutes` line, every `year` file is one the afternoon update covers;
- and the answers it gives for the cases it is for (a library of the suites, the docs, a module, a function).

Like test_smoke_selection.py for the smoke suite: a new file cannot fall silently outside it."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import selection as sel  # noqa: E402
import shell_library as sl  # noqa: E402
import vm_select as vs  # noqa: E402


ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# What the suites source, whose functions a statement outside every part might call.
HELPERS = (
    "tests/sandbox/e2e_lib.sh",
    "tests/sandbox/e2e_parts.sh",
    "tests/sandbox/acme_week.sh",
    "tests/sandbox/live_agy.sh",
)
# The calls a suite's top level may make, outside its parts: its guards, the halves and steps it dispatches
# to, and the parts themselves.
TOP_LEVEL_CALLS = frozenset({"copied_in", "after_reboot", "other_steps", "part", "parts_end"})
# A plain assignment, which calls nothing unless it holds a command substitution.
PLAIN_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=")
# What CI's install job started on before the suites had parts (main's .github/workflows/install.yml
# `paths:`): the Docker sandbox's first day must still run on every one (tests/vm_selection.txt's `docker`).
OLD_INSTALL_PATHS = (
    "get.sh",
    "install.sh",
    "uninstall.sh",
    "bin/**",
    "libexec/bosun-init",
    "libexec/bosun-uninstall",
    "libexec/bosun-preflight",
    "libexec/bosun-launch",
    "libexec/bosun-doctor",
    "libexec/bosun-trust",
    "libexec/bosun-start",
    "libexec/bosun-stop",
    "libexec/bosun-refresh",
    "libexec/bosun-setup",
    "lib/setup-helpers.sh",
    "lib/login.py",
    "libexec/bosun-morning",
    "lib/common.sh",
    "lib/agents.sh",
    "lib/preflight.sh",
    "lib/platforms/**",
    "lib/harnesses/**",
    "lib/launch/**",
    "lib/init*.py",
    "lib/uninstall*.py",
    "lib/agent_*.py",
    "lib/claude_state.py",
    "lib/codex_state.py",
    "templates/**",
    "tests/install_e2e.sh",
    "tests/sandbox.sh",
    "tests/sandbox/**",
    ".github/workflows/install.yml",
)


def repository_files() -> list[str]:
    """The files git lists here: tracked, and untracked but not ignored."""
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return sorted(listed.stdout.splitlines())


def function_of(part_id: str) -> str:
    return "part_" + part_id.replace("-", "_")


class Guard(unittest.TestCase):
    files: list[str]
    parts: dict[str, list[sel.Marker]]
    rules: vs.Rules
    texts: dict[str, str]
    functions: dict[str, list[str]]  # a function-read file -> its functions

    @classmethod
    def setUpClass(cls) -> None:
        cls.files = repository_files()
        cls.texts = {suite: (ROOT / script).read_text() for suite, script in vs.SUITES.items()}
        cls.parts = {suite: vs.parse_parts(text) for suite, text in cls.texts.items()}
        cls.rules = vs.parse_rules((ROOT / vs.RULES).read_text())
        cls.functions = {
            path: [f.name for f in sl.read((ROOT / path).read_text()).functions]
            for path in vs.function_files(cls.parts, cls.rules)
        }

    def placed(self, item: str) -> vs.Plan:
        plan = vs.Plan()
        vs.place(item, plan, self.parts, self.rules)
        return plan

    def all_parts(self) -> list[tuple[str, sel.Marker]]:
        return [(suite, part) for suite, parts in self.parts.items() for part in parts]

    def names_something(self, glob: str) -> bool:
        if "#" not in glob:
            return any(sel.matches(path, glob) for path in self.files)
        files, _, functions = glob.partition("#")
        return any(
            sel.matches(path, files) and any(sel.matches(name, functions) for name in names)
            for path, names in self.functions.items()
        )


class CoverageTest(Guard):
    def test_lib_common_sh_is_read_by_function(self) -> None:
        self.assertEqual(set(self.functions), {"lib/common.sh"})
        self.assertGreater(len(self.functions["lib/common.sh"]), 100)

    def test_every_file_is_covered_or_on_a_rule(self) -> None:
        unknown = [f for f in self.files if f not in self.functions and self.placed(f).unknown]
        self.assertEqual(
            unknown,
            [],
            "\n\nA change to each of these would run run, work and app whole in the Mac VM, because no part "
            "covers them and no rule lists them. For each file, one of:\n"
            "  - the part whose behaviour on a real Mac rests on it: add the file (or a glob) to its `covers "
            "'...'` on the `part <id> ...` line of its suite's script (tests/install_e2e.sh, "
            "tests/work_e2e.sh, tests/app_e2e.sh, tests/sandbox/live.sh);\n"
            "  - no VM part needs it (the smoke suite and the unit tests show it): `none <path>` in "
            "tests/vm_selection.txt;\n"
            "  - unsure, or it reaches a whole suite: `all <suite>[,<suite>...] <path>` there.\n"
            "Files:",
        )

    def test_every_function_of_lib_common_sh_is_named(self) -> None:
        unnamed = [
            f"{path}#{name}"
            for path, names in self.functions.items()
            for name in names
            if self.placed(f"{path}#{name}").unknown
        ]
        self.assertEqual(
            unnamed,
            [],
            "\n\nlib/common.sh is read by function: a change to each of these functions would run run, work "
            "and app whole. Name it in the covers of the part that rests on it (`lib/common.sh#<name>`, or a "
            "glob such as `lib/common.sh#render_*`), or `none lib/common.sh#<name>` in "
            "tests/vm_selection.txt. Functions:",
        )

    def test_every_part_says_what_it_covers(self) -> None:
        bare = [f"{suite}/{part.id}" for suite, part in self.all_parts() if not part.covers]
        self.assertEqual(bare, [], "\n\nA part with no `covers '...'` is never chosen by --changed. Parts:")

    def test_every_covers_glob_names_something(self) -> None:
        dead = [
            f"{suite}/{part.id}: {glob}"
            for suite, part in self.all_parts()
            for glob in part.covers
            if not self.names_something(glob)
        ]
        self.assertEqual(
            dead, [], "\n\nThese `covers` globs match no file (or no function). Fix or remove them:"
        )

    def test_every_rule_glob_names_something(self) -> None:
        globs = [w.glob for w in self.rules.whole] + [*self.rules.none, *self.rules.year, *self.rules.docker]
        dead = [glob for glob in globs if not self.names_something(glob)]
        self.assertEqual(dead, [], "\n\nThese rules of tests/vm_selection.txt match nothing. Remove them:")

    def test_no_plain_glob_names_a_file_read_by_function(self) -> None:
        """Such a glob would never match (a change to lib/common.sh is placed function by function)."""
        globs = [g for _, part in self.all_parts() for g in part.covers]
        globs += [w.glob for w in self.rules.whole] + list(self.rules.none)
        plain = [g for g in globs if "#" not in g and any(sel.matches(path, g) for path in self.functions)]
        self.assertEqual(plain, [], "\n\nName its functions instead: `lib/common.sh#<glob>`. Globs:")

    def test_every_part_has_its_minutes_and_every_minutes_line_names_one(self) -> None:
        keys = {f"{suite}/{part.id}" for suite, part in self.all_parts()} | set(vs.SUITES) | {vs.YEAR_MINUTES}
        self.assertEqual(sorted(keys - set(self.rules.minutes)), [], "parts with no `minutes` line")
        self.assertEqual(sorted(set(self.rules.minutes) - keys), [], "`minutes` lines that name no part")

    def test_every_year_file_is_one_the_afternoon_update_covers(self) -> None:
        afternoon = next(p for p in self.parts[vs.YEAR_SUITE] if p.id == vs.YEAR_PART)
        loose = [
            path
            for path in self.files
            if any(sel.matches(path, g) for g in self.rules.year)
            and not any(sel.matches(path, c) for c in afternoon.covers)
        ]
        self.assertEqual(loose, [], f"`year` files that {vs.YEAR_SUITE}/{vs.YEAR_PART} does not cover")


class DockerTest(Guard):
    """The Docker sandbox's first day (CI's install job, `sandbox`): it runs on a change to a `docker` file"""

    def test_every_file_the_old_paths_started_it_on_still_does(self) -> None:
        dropped = [
            path
            for path in self.files
            if any(sel.matches(path, g) for g in OLD_INSTALL_PATHS)
            and not any(sel.matches(path, g) for g in self.rules.docker)
        ]
        self.assertEqual(
            dropped,
            [],
            "files the job's old `paths:` ran the Docker sandbox on, which no `docker` rule names",
        )

    def test_its_first_day_on_codex_and_agy_and_its_own_files_reach_it(self) -> None:
        for path in (
            "tests/sandbox/codex-stand-in",
            "tests/sandbox/agy-stand-in",
            "tests/sandbox/fresh_user.sh",
            "tests/sandbox/Dockerfile",
            "tests/sandbox/macos/launchctl",
            "lib/codex_state.py",
            "lib/agy_state.py",
            "lib/connectors_codex.py",
            "lib/launch/codex.sh",
            "libexec/bosun-refresh",
            "agents/agy-plugin/plugin.json",
        ):
            with self.subTest(path=path):
                self.assertTrue(any(sel.matches(path, g) for g in self.rules.docker))
        self.assertFalse(any(sel.matches("docs/index.md", g) for g in self.rules.docker))

    def test_ci_asks_the_docker_rules_and_runs_the_sandbox_on_their_answer(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "install.yml").read_text()
        self.assertIn("python3 tools/vm_select.py --root . --docker --changed", workflow)
        self.assertIn("if: ${{ needs.install-select.outputs.sandbox == 'true' }}", workflow)


class ScriptsTest(Guard):
    def test_every_part_line_parses_and_its_id_is_new_and_kebab_case(self) -> None:
        for suite, text in self.texts.items():
            with self.subTest(suite=suite):
                lines = [line for line in text.splitlines() if re.match(r"^\s*part\s", line)]
                self.assertEqual(
                    len(lines), len(self.parts[suite]), f"a `part` line that does not parse in {suite}"
                )
                ids = [p.id for p in self.parts[suite]]
                self.assertEqual(len(ids), len(set(ids)), f"an id twice in {suite}")
                self.assertEqual([i for i in ids if not ID.match(i)], [])

    def test_needs_names_a_part_above_that_is_not_last(self) -> None:
        for suite, parts in self.parts.items():
            above: dict[str, sel.Marker] = {}
            for part in parts:
                for need in part.needs:
                    with self.subTest(part=f"{suite}/{part.id}", needs=need):
                        self.assertIn(need, list(above), "needs a part that is not above it")
                        self.assertNotIn("last", above[need].flags, "needs a `last` part")
                above[part.id] = part

    def test_the_last_parts_come_after_the_others(self) -> None:
        for suite, parts in self.parts.items():
            with self.subTest(suite=suite):
                flags = ["last" in p.flags for p in parts]
                self.assertEqual(flags, sorted(flags), "a part after a `last` one")

    def test_each_part_has_its_function_and_nothing_else_calls_it(self) -> None:
        for suite, text in self.texts.items():
            script = sl.read(text)
            defined = {f.name: f for f in script.functions}
            for part in self.parts[suite]:
                name = function_of(part.id)
                with self.subTest(part=f"{suite}/{part.id}"):
                    self.assertIn(name, list(defined), f"no {name}() in {vs.SUITES[suite]}")
                    calls = [
                        n
                        for n, line in enumerate(script.code_lines, 1)
                        if re.search(rf"(?<![\w-]){name}(?![\w-])", line) and n != defined[name].first
                    ]
                    self.assertEqual(calls, [], f"{name} is called outside `part`: lines {calls}")

    def test_no_statement_outside_the_parts_calls_a_suite_s_step(self) -> None:
        """What runs at the top level runs in every partial run, unchosen: it may only guard, dispatch and run
        the parts (TOP_LEVEL_CALLS), wherever it stands in the script, above the parts too."""
        steps = {f.name for path in HELPERS for f in sl.read((ROOT / path).read_text()).functions}
        for suite, text in self.texts.items():
            with self.subTest(suite=suite):
                script = sl.read(text)
                names = (steps | {f.name for f in script.functions}) - TOP_LEVEL_CALLS
                calls = sl.calls(top_level_code(script, text), names)
                self.assertEqual(sorted(calls), [], f"called outside every part in {vs.SUITES[suite]}")

    def test_the_main_body_is_the_part_lines_then_parts_end(self) -> None:
        """What runs outside a part runs in every partial run, unchosen: from the first `part` line on, only
        part lines, then parts_end."""
        for suite, text in self.texts.items():
            with self.subTest(suite=suite):
                script = sl.read(text)
                markers = {n for n, _ in sel.marker_lines(text, vs.KEYWORD, vs.FLAGS)}
                code = [
                    (n, line.strip())
                    for n, line in enumerate(script.code_lines, 1)
                    if n >= min(markers) and line.strip()
                ]
                stray = [f"{n}: {line}" for n, line in code if n not in markers][:-1]
                self.assertEqual(stray, [], f"code between the part lines of {vs.SUITES[suite]}")
                self.assertEqual(code[-1][1], "parts_end", f"{vs.SUITES[suite]} does not end with parts_end")


def top_level_code(script: sl.Script, text: str) -> str:
    """The code of <script> outside every function, but its part lines (checked apart) and its plain
    assignments (a string's words are no calls), with the lines a string of theirs runs on over."""
    markers = {n for n, _ in sel.marker_lines(text, vs.KEYWORD, vs.FLAGS)}
    kept: list[str] = []
    skipping = False
    for number, line in enumerate(script.code_lines, 1):
        begins = script.starts[number - 1]
        if begins:
            skipping = bool(PLAIN_ASSIGNMENT.match(line)) and "$(" not in line and "`" not in line
        if script.function_at(number) is None and number not in markers and not skipping:
            kept.append(line)
    return "\n".join(kept)


class AnswersTest(Guard):
    """What it chooses for the cases it was made for."""

    def choices(self, changed: list[str], functions: frozenset[str] | None = None) -> dict[str, vs.Choice]:
        plan = vs.plan_for(changed, self.parts, self.rules, lambda _: functions)
        return {suite: vs.choose(suite, self.parts[suite], plan, self.rules) for suite in vs.SUITES}

    def test_the_suites_library_runs_run_work_and_app_whole_and_live_not(self) -> None:
        got = self.choices(["tests/sandbox/e2e_lib.sh"])
        self.assertEqual([got[s].whole for s in vs.SUITES], [True, True, True, False])
        self.assertEqual(got["live"].parts, ())

    def test_the_docs_alone_run_nothing(self) -> None:
        got = self.choices(["docs/menu-bar-app.md", "README.md", "docs/design/update.md"])
        self.assertEqual({s: c.parts for s, c in got.items()}, dict.fromkeys(vs.SUITES, ()))

    def test_the_guide_to_going_back_runs_work_s_ways_back(self) -> None:
        got = self.choices(["docs/updating.md"])
        self.assertEqual(got["work"].parts, ("install", "week", "afternoon-update", "ways-back"))
        self.assertEqual((got["run"].parts, got["app"].parts, got["live"].parts), ((), (), ()))

    def test_going_back_runs_run_branch_work_ways_back_and_names_live_update_as_paid(self) -> None:
        got = self.choices(["lib/update_back.py"])
        self.assertEqual(got["run"].parts, ("install", "console-job", "releases", "branch"))
        self.assertEqual(got["work"].parts, ("install", "week", "afternoon-update", "ways-back"))
        self.assertEqual(got["app"].parts, ())
        self.assertEqual((got["live"].parts, got["live"].paid), (("install", "windows", "update"), True))
        self.assertEqual(got["work"].year, vs.SHORT_YEAR, "update_back.py is not the freeze's code")

    def test_a_function_of_lib_common_sh_runs_the_parts_that_name_it(self) -> None:
        got = self.choices(["lib/common.sh"], frozenset({"render_job"}))
        self.assertEqual(got["run"].parts, ("install", "launchd"))
        self.assertIn("settings", got["app"].parts)
        self.assertFalse(any(c.whole for c in got.values()))

    def test_a_change_to_lib_common_sh_outside_its_functions_fails_closed(self) -> None:
        got = self.choices(["lib/common.sh"], None)
        self.assertEqual([got[s].whole for s in vs.SUITES], [True, True, True, False])

    def test_a_file_nothing_names_fails_closed_but_never_into_live(self) -> None:
        got = self.choices(["brand-new-thing.txt"])
        self.assertEqual([got[s].whole for s in vs.SUITES], [True, True, True, False])
        self.assertEqual(got["work"].year, vs.YEAR, "failing closed proves what a whole run proves")

    def test_the_freeze_s_code_gives_the_afternoon_update_a_year(self) -> None:
        got = self.choices(["lib/journal.py"])
        self.assertIn("afternoon-update", got["work"].parts)
        self.assertEqual(got["work"].year, vs.YEAR)

    def test_a_headless_call_runs_live_s_headless_part_with_the_install_only(self) -> None:
        got = self.choices(["lib/learn.py"])
        self.assertEqual((got["live"].parts, got["live"].paid), (("install", "headless"), True))
        self.assertEqual((got["run"].parts, got["work"].parts, got["app"].parts), ((), (), ()))

    def test_a_suite_s_own_script_runs_that_suite_whole(self) -> None:
        for suite, script in vs.SUITES.items():
            with self.subTest(suite=suite):
                got = self.choices([script])
                self.assertEqual([s for s in vs.SUITES if got[s].whole], [suite])


if __name__ == "__main__":
    unittest.main()
