"""The commit hook's test selection (pytest-impacted, `--impacted`; pyproject.toml's impacted_*
settings, read by impacted_rules.py): every file has a rule, every mapped path is a file, and the
plugin selects each test for a change to any module the test imports, directly or not: a module of
lib/ (a flat folder of modules on sys.path, an import root through pytest's `pythonpath`) or a helper
of tests/unit (which has no __init__.py). The Python files the plugin follows need no rule of their
own; everything else (shell, templates, docs, data) does.

pyproject.toml maps every change to this file, so it runs in each commit's selection: a new kind of
file fails here until it has a rule, rather than selecting nothing. What a test reads or runs as it
runs is checked by impacted_guard.py."""

from __future__ import annotations

import importlib.util
import unittest
from functools import cache
from pathlib import Path
from unittest import mock

import impacted_rules as rules


HAVE_PLUGIN = importlib.util.find_spec("pytest_impacted") is not None
NO_PLUGIN = "pytest-impacted is not installed (uv sync installs the dev group)"
HAVE_PYTEST = importlib.util.find_spec("pytest") is not None  # impacted_guard is a pytest plugin
CATCH_ALL = "*"  # the glob that maps every change to this test
DEPENDENCY_FILES = ("pyproject.toml", "uv.lock")  # the plugin runs every test for a change to one


def has_rule(name: str) -> bool:
    return (
        rules.is_test_file(name)
        or rules.is_followed(name)
        or name in DEPENDENCY_FILES
        or any(rules.matches(name, glob) for glob in rules.invalidate_all())
        or any(rules.matches(name, glob) for glob, _ in rules.file_map() if glob != CATCH_ALL)
    )


@unittest.skipUnless(HAVE_PLUGIN, NO_PLUGIN)  # the rules are matched with the plugin's own matcher
class RulesTest(unittest.TestCase):
    def test_every_file_has_a_rule(self) -> None:
        missing = sorted(name for name in rules.repository_files() if not has_rule(name))
        self.assertEqual(
            missing,
            [],
            "no rule says which tests a change to these selects: add one to pyproject.toml's "
            "impacted_map or impacted_invalidate_all",
        )

    def test_every_mapped_path_is_a_file(self) -> None:
        targets = sorted({target for _, target in rules.file_map()})
        self.assertEqual([t for t in targets if t not in rules.repository_files()], [])

    def test_a_change_to_a_program_selects_the_tests_that_run_them(self) -> None:
        for name in ("lib/common.sh", "libexec/bosun-ledger", "lib/ledger.py", "get.sh"):
            with self.subTest(name):
                self.assertIn("tests/unit/test_update.py", rules.selected_by(name))

    def test_a_change_to_what_bosun_reads_runs_every_test(self) -> None:
        for name in ("orchestrator/policy.md", "templates/launchd.plist", "console/app.js"):
            with self.subTest(name):
                self.assertIs(rules.selected_by(name), rules.ALL)

    def test_the_plugin_s_matcher_reads_a_double_star_across_folders(self) -> None:
        self.assertTrue(rules.matches("docs/design/prototypes/a/b.html", "docs/**"))
        self.assertFalse(rules.matches("docs/design/a.md", "docs/*"))
        self.assertTrue(rules.matches("docs/a.md", "docs/*"))

    def test_the_python_files_the_plugin_follows_need_no_rule(self) -> None:
        self.assertTrue(rules.is_followed("lib/awake.py"))
        self.assertTrue(rules.is_followed("tests/unit/minted.py"))
        self.assertFalse(rules.is_followed("lib/common.sh"))
        self.assertFalse(rules.is_followed("tests/sandbox/drive.py"))

    def test_a_module_a_test_imports_covers_it_without_a_rule(self) -> None:
        self.assertTrue(rules.covers("tests/unit/test_ledger_protocol.py", "lib/ledger.py"))
        self.assertFalse(rules.covers("tests/unit/test_tickets.py", "lib/common.sh"))


@unittest.skipUnless(HAVE_PYTEST, "pytest is not installed (uv sync installs the dev group)")
class GuardTest(unittest.TestCase):
    """What impacted_guard takes a subprocess's command line to run."""

    def started(self, *argv: str) -> set[str]:
        import impacted_guard

        return impacted_guard.started(list(argv))

    def test_bosun_s_programs_by_name_path_or_shell_file(self) -> None:
        root = str(rules.ROOT)
        for argv in (
            (f"{root}/bin/bosun", "ledger", "show"),
            ("bash", "-c", 'L() { bosun ledger "$@"; }\nL transition x done'),
            ("bash", "-c", 'source "$1/lib/common.sh"; load_config', "_", "/work/a-copy-of-the-tree"),
        ):
            with self.subTest(argv[1]):
                self.assertIn("bin/bosun", self.started(*argv))

    def test_what_only_mentions_bosun_runs_nothing_of_it(self) -> None:
        self.assertEqual(self.started("/work/x/claude", "-p", "run bosun ledger show"), set())
        self.assertEqual(self.started("bash", "-c", "HOME=/x\n# bosun configuration\nX=1"), set())
        self.assertEqual(self.started("cp", "/work/a/setup.sh", "/work/b/setup.sh"), set())

    def test_the_repository_s_files_on_a_command_line(self) -> None:
        root = str(rules.ROOT)
        self.assertEqual(self.started("python3", f"{root}/lib/ledger.py", "show"), {"lib/ledger.py"})
        self.assertEqual(self.started("cp", f"{root}/lib/common.sh", "/work/x"), {"lib/common.sh"})


@unittest.skipUnless(HAVE_PLUGIN, NO_PLUGIN)
class PluginTest(unittest.TestCase):
    """The plugin itself, with the changed files given rather than read from git."""

    def selected(self, name: str) -> set[str]:
        from pytest_impacted import api, file_map, ini
        from pytest_impacted.git import GitMode

        options = rules.options()
        with (
            mock.patch.object(api, "find_impacted_files_in_repo", return_value=[name]),
            mock.patch.object(api, "notify"),
            mock.patch.object(api, "warn"),
            mock.patch.object(file_map, "files_at", files_at()),
        ):
            found = api.get_impacted_tests(
                impacted_git_mode=GitMode.UNSTAGED,
                impacted_base_branch="main",
                root_dir=rules.ROOT,
                ns_module=str(options["impacted_module"]),
                tests_dir=str(options["impacted_tests_dir"]),
                invalidate_all_patterns=rules.invalidate_all(),
                file_map=file_map.parse_file_map(options.get("impacted_map", [])),
                pythonpath=ini.find_pytest_config(rules.ROOT).paths("pythonpath"),
            )
        return {str(Path(path).resolve().relative_to(rules.ROOT)) for path in found or []}

    def test_a_change_to_a_module_selects_every_test_that_imports_it(self) -> None:
        tests = sorted(name for name in rules.repository_files() if rules.is_test_file(name))
        importers: dict[str, set[str]] = {}
        for test in tests:
            for module in rules.import_closure(test):
                importers.setdefault(module, set()).add(test)
        for module, expected in sorted(importers.items()):
            with self.subTest(module):
                self.assertEqual(sorted(expected - self.selected(module)), [])

    def test_a_change_to_a_test_selects_only_it_and_the_rules(self) -> None:
        name = "tests/unit/test_tickets.py"
        self.assertEqual(self.selected(name), {name, "tests/unit/test_impacted_rules.py"})

    def test_a_change_to_the_docs_selects_only_the_tests_that_read_them(self) -> None:
        self.assertEqual(
            self.selected("docs/ledger.md"),
            {
                "tests/unit/test_docs_examples.py",
                "tests/unit/test_update.py",
                "tests/unit/test_update_facts.py",  # it imports test_update
                "tests/unit/test_update_summary.py",
                "tests/unit/test_impacted_rules.py",
            },
        )


@cache
def files_at() -> object:
    """The plugin's files_at, remembered: each lookup runs git, and these tests make many."""
    from pytest_impacted import file_map

    return cache(file_map.files_at)
