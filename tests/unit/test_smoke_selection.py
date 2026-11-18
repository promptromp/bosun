"""The guard on the smoke suite's selection (tools/smoke_select.py): the pre-push hook runs the sections a
change can reach, so a file that no section covers, that no rule lists, would run the whole suite (it fails
closed), and a section that says nothing about what it covers would never be chosen. Both are caught here,
on the commit, with what to add:

- every file git lists is on the select-everything list of tests/smoke_selection.txt, on its ignore list,
  reached by the `covers` of a section (directly, or through the code that depends on it), or the test
  fails and names it;
- every section has a `covers '<glob>,...'` clause, and every glob of it and of the selection file matches
  a file (a renamed or deleted file leaves a glob that selects nothing);
- every shell library reads as functions (tools/shell_library.py): one that did not would make every push run
  the whole suite.

Like test_impacted_rules.py for the commit hook's tests: a new file cannot fall silently outside it."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import smoke_select as ss  # noqa: E402


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


class SelectionRulesTest(unittest.TestCase):
    files: list[str]
    sections: list[ss.Section]
    rules: ss.Rules
    graph: ss.Graph

    @classmethod
    def setUpClass(cls) -> None:
        cls.files = repository_files()
        suite = (ROOT / "tests" / "smoke.sh").read_text()
        cls.sections = ss.parse_sections(suite)
        cls.rules = ss.parse_rules((ROOT / "tests" / "smoke_selection.txt").read_text())
        cls.graph = ss.Graph(ROOT, cls.files, ss.Suite(suite))

    def test_every_file_is_covered_listed_or_ignored(self) -> None:
        unknown = [
            path for path in self.files if ss.plan_for([path], self.sections, self.rules, self.graph).unknown
        ]
        self.assertEqual(
            unknown,
            [],
            "\n\nThe pre-push hook would run the whole smoke suite for a change to each of these, because no "
            "section covers them and no rule lists them. For each file, one of:\n"
            "  - the section that runs or reads it: add the file (or a glob) to its `covers '...'` on the "
            "`section <id> ...` line in tests/smoke.sh;\n"
            "  - no section can reach it (docs, other suites' files): add `ignore <path>` to "
            "tests/smoke_selection.txt;\n"
            "  - unsure, or it reaches every section: add `all <path>` to tests/smoke_selection.txt.\n"
            "Files:",
        )

    def test_every_section_says_what_it_covers(self) -> None:
        bare = [section.id for section in self.sections if not section.covers]
        self.assertEqual(
            bare,
            [],
            "\n\nA section with no `covers` is never chosen by the pre-push hook. Add, to its `section <id> "
            "\"<name>\" [needs ...]` line in tests/smoke.sh, ` covers '<glob>,<glob>'`: the files it runs or "
            "reads (its command in libexec/, the lib/ modules it exercises, a template or a fixture). "
            "Sections:",
        )

    def test_every_covers_glob_matches_a_file(self) -> None:
        dead = [
            f"{section.id}: {glob}"
            for section in self.sections
            for glob in section.covers
            if not any(ss.matches(path, glob) for path in self.files)
        ]
        self.assertEqual(
            dead,
            [],
            "\n\nThese `covers` globs (in tests/smoke.sh) match no file, so a renamed or deleted file is no "
            "longer selected by them. Fix or remove them:",
        )

    def test_every_rule_of_the_selection_file_matches_a_file(self) -> None:
        dead = [
            glob
            for glob in (*self.rules.everything, *self.rules.ignore)
            if not any(ss.matches(path, glob) for path in self.files)
        ]
        self.assertEqual(dead, [], "\n\nThese rules of tests/smoke_selection.txt match no file. Remove them:")

    def test_every_shell_library_reads_as_functions(self) -> None:
        self.assertEqual(
            self.graph.unreadable,
            [],
            "\n\nA shell library that does not read as functions (tools/shell_library.py) makes every change "
            "run the whole suite. Write it in shfmt's layout (`name() {` ... `}` at the function's "
            "indentation), or fix what does not close:",
        )
        self.assertGreater(len(self.graph.functions), 200)

    def test_a_command_a_library_function_runs_reaches_the_sections_that_use_that_function(self) -> None:
        """lib/common.sh's build_launch_command runs `"$BOSUN_CMD" launch`: a change to bosun-launch reaches
        the sections of every command that starts a window through it, not a command's that does not."""
        self.assertIn(ss.fn("build_launch_command"), self.graph.callers["libexec/bosun-launch"])
        launch = ss.plan_for(["libexec/bosun-launch"], self.sections, self.rules, self.graph)
        self.assertFalse(launch.full, launch.reason)
        self.assertIn("tmux-session", launch.picked)  # bosun start, through build_launch_command
        self.assertNotIn("app", launch.picked)
        self.assertFalse(ss.plan_for(["libexec/bosun-app"], self.sections, self.rules, self.graph).full)

    def test_a_new_command_nothing_covers_is_flagged_not_covered_by_syntax(self) -> None:
        """`syntax` parses all of libexec/ and lib/, so it is no sign that a section runs the file."""
        plan = ss.plan_for(["libexec/bosun-brand-new"], self.sections, self.rules, self.graph)
        self.assertTrue(plan.full and plan.unknown, plan.reason)
        self.assertIn("no section covers libexec/bosun-brand-new", plan.reason)
        self.assertIn("syntax", [s.id for s in self.sections])

    def test_the_unknown_file_gets_the_whole_suite_and_the_message_says_what_to_add(self) -> None:
        plan = ss.plan_for(["brand_new_thing.txt"], self.sections, self.rules, self.graph)
        self.assertTrue(plan.full and plan.unknown)
        self.assertIn("add it to a section's `covers", plan.reason)
        self.assertIn("tests/smoke_selection.txt", plan.reason)

    def test_the_dispatcher_runs_everything_and_so_does_a_library_or_the_suite_changed_in_full(self) -> None:
        """bin/bosun is on the select-everything list; lib/common.sh changed in full (no diff says which
        lines) changes its top-level code, which runs wherever it is sourced; tests/smoke.sh, the same."""
        for path, reason in (
            ("bin/bosun", "select-everything list"),
            ("lib/common.sh", "top-level code"),
            ("tests/smoke.sh", "cannot be read"),
        ):
            with self.subTest(path=path):
                plan = ss.plan_for([path], self.sections, self.rules, self.graph)
                self.assertTrue(plan.full and not plan.unknown, plan.reason)
                self.assertIn(reason, plan.reason)

    def test_the_orchestrator_s_files_reach_the_sections_whose_code_reads_them_not_everything(self) -> None:
        """Data the code reads: the task files through lib/delegate.py and `bosun delegate`."""
        plan = ss.plan_for(["orchestrator/tasks/review.md"], self.sections, self.rules, self.graph)
        self.assertFalse(plan.full, plan.reason)
        self.assertIn("delegate", plan.picked)
        self.assertLess(len(plan.picked) + len(plan.needed), len(self.sections))

    def test_the_weights_reach_nothing(self) -> None:
        plan = ss.plan_for(["tests/smoke_weights.txt"], self.sections, self.rules, self.graph)
        self.assertEqual((plan.full, plan.picked, plan.ignored), (False, {}, ["tests/smoke_weights.txt"]))

    def test_docs_alone_run_nothing_and_a_command_alone_runs_something_smaller_than_everything(self) -> None:
        docs = ss.plan_for(["docs/design/menu-bar.md", "README.md"], self.sections, self.rules, self.graph)
        self.assertFalse(docs.full)
        self.assertEqual(docs.picked, {})
        command = ss.plan_for(["libexec/bosun-app"], self.sections, self.rules, self.graph)
        self.assertFalse(command.full)
        self.assertIn("app", command.picked)
        self.assertLess(len(command.picked), len(self.sections) // 2)


if __name__ == "__main__":
    unittest.main()
