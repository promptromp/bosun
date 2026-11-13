"""The guard on the smoke suite's selection (tools/smoke_select.py): the pre-push hook runs the sections a
change can reach, so a file that no section covers, that no rule lists, would run the whole suite (it fails
closed), and a section that says nothing about what it covers would never be chosen. Both are caught here,
on the commit, with what to add:

- every file git lists is on the select-everything list of tests/smoke_selection.txt, on its ignore list,
  reached by the `covers` of a section (directly, or through the code that depends on it), or the test
  fails and names it;
- every section has a `covers '<glob>,...'` clause, and every glob of it and of the selection file matches
  a file (a renamed or deleted file leaves a glob that selects nothing).

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
        cls.sections = ss.parse_sections((ROOT / "tests" / "smoke.sh").read_text())
        cls.rules = ss.parse_rules((ROOT / "tests" / "smoke_selection.txt").read_text())
        cls.graph = ss.Graph(ROOT, cls.files)

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

    def test_the_unknown_file_gets_the_whole_suite_and_the_message_says_what_to_add(self) -> None:
        plan = ss.plan_for(["brand_new_thing.txt"], self.sections, self.rules, self.graph)
        self.assertTrue(plan.full and plan.unknown)
        self.assertIn("add it to a section's `covers", plan.reason)
        self.assertIn("tests/smoke_selection.txt", plan.reason)

    def test_the_shell_libraries_and_the_dispatcher_run_everything(self) -> None:
        for path in ("lib/common.sh", "bin/bosun", "tests/smoke.sh"):
            with self.subTest(path=path):
                plan = ss.plan_for([path], self.sections, self.rules, self.graph)
                self.assertTrue(plan.full and not plan.unknown, plan.reason)

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
