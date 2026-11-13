"""Which sections of the smoke suite a change reaches (tools/smoke_select.py, `tests/smoke.sh --changed
<ref>` and the pre-push hook's use of it).

The pure parts (the globs, the section and rule files, the dependency scan) are called directly. The
script's own options (`--changed`, `--pre-push` with its base) go through a script generated for the test,
as in test_smoke_cache.py: the real script's setup and its `section`/`check` machinery around three fake
sections, in a scratch git repository with its own tests/smoke_selection.txt. No real section runs here.

The rule under test is that selection can only fail towards running more: a file nothing covers, a file on
the select-everything list, a ref git cannot resolve, a selection that crashes: the whole suite."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from test_smoke_cache import ROOT, SMOKE, Scratch, clean_env, git


sys.path.insert(0, str(ROOT / "tools"))
import smoke_select as ss


SELECTION = """# a scratch selection
all shared.sh
all tests/smoke.sh
ignore docs/**
ignore *.md
"""

# alpha covers a command, beta one more and a shell file that calls the command, gamma needs alpha and
# covers the templates. (Their checks do nothing: no section of the real suite runs here.)
FAKE_SECTIONS = """section alpha "the first fake section" covers 'libexec/bosun-alpha'
check "alpha one" true
section beta "the second" covers 'libexec/bosun-beta,extra/**'
check "beta one" true
section gamma "the third, which needs the first" needs alpha covers 'templates/*.plist'
check "gamma one" true
"""

FILES = {
    "libexec/bosun-alpha": "#!/bin/bash\necho alpha\n",
    # runs alpha: a change to alpha reaches beta's section
    "libexec/bosun-beta": '#!/bin/bash\n"$BOSUN" alpha\n',
    "libexec/bosun-delta": "#!/bin/bash\necho delta\n",  # no section covers it
    "templates/job.plist": "<plist/>\n",
    "extra/deep/er/file.txt": "x\n",
    "shared.sh": "echo shared\n",
    "docs/guide.md": "# guide\n",
    "README.md": "# readme\n",
    "notes.txt": "nobody covers this\n",
}


class GlobTest(unittest.TestCase):
    def test_star_stays_in_a_folder_and_double_star_goes_through_them(self) -> None:
        for path, glob, expected in (
            ("lib/a.py", "lib/*.py", True),
            ("lib/sub/a.py", "lib/*.py", False),
            ("lib/sub/a.py", "lib/**", True),
            ("lib/a.py", "lib/**/a.py", True),  # `**/` may be no folder at all
            ("lib/x/y/a.py", "lib/**/a.py", True),
            ("lib/a.py", "lib/b.py", False),
            ("libexec/bosun-app", "libexec/bosun-*", True),
            ("xlib/a.py", "lib/**", False),  # from the root, whole
            ("lib/a.py.bak", "lib/*.py", False),
            ("README.md", "*.md", True),
            ("docs/README.md", "*.md", False),
            ("lib/ab_b.py", "lib/?_b.py", False),
            ("lib/a.py", "lib/?.py", True),
        ):
            with self.subTest(path=path, glob=glob):
                self.assertEqual(ss.matches(path, glob), expected)

    def test_regex_characters_in_a_glob_mean_themselves(self) -> None:
        self.assertTrue(ss.matches("lib/a+b.py", "lib/a+b.py"))
        self.assertFalse(ss.matches("lib/aab.py", "lib/a+b.py"))
        self.assertTrue(ss.matches("lib/a.py", "lib/a.py"))
        self.assertFalse(ss.matches("lib/aXpy", "lib/a.py"))


class FilesTest(unittest.TestCase):
    def test_a_section_line_with_needs_and_covers(self) -> None:
        sections = ss.parse_sections(
            "section() {\n"
            "  section one \"One\" covers 'a/*.sh, b/**'\n"
            "  section two \"Two (with parentheses)\" needs one covers 'c'\n"
            '  section three "Three" needs one,two\n'
        )
        self.assertEqual(
            sections,
            [
                ss.Section("one", "One", (), ("a/*.sh", "b/**")),
                ss.Section("two", "Two (with parentheses)", ("one",), ("c",)),
                ss.Section("three", "Three", ("one", "two"), ()),
            ],
        )

    def test_a_selection_file_has_all_and_ignore_rules_and_comments(self) -> None:
        rules = ss.parse_rules("# c\nall a.sh  # why\n\nignore docs/**\nall b/**\n")
        self.assertEqual((rules.everything, rules.ignore), (("a.sh", "b/**"), ("docs/**",)))

    def test_anything_else_in_it_is_an_error_naming_the_line(self) -> None:
        for text in ("some a\n", "all\n", "ignore \n"):
            with self.subTest(text=text), self.assertRaisesRegex(ss.SelectionError, "line 1"):
                ss.parse_rules(text)

    def test_the_real_files_parse(self) -> None:
        sections = ss.parse_sections(SMOKE.read_text())
        self.assertGreater(len(sections), 50)
        rules = ss.parse_rules((ROOT / "tests" / "smoke_selection.txt").read_text())
        self.assertTrue(rules.everything and rules.ignore)


class DependenciesTest(Scratch):
    """What the scan counts as one file needing another, in a scratch folder."""

    def graph(self, files: dict[str, str]) -> ss.Graph:
        for name, text in files.items():
            path = self.dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return ss.Graph(self.dir, sorted(files))

    def test_an_import_reaches_the_importers_through_the_others(self) -> None:
        g = self.graph(
            {
                "lib/a.py": "x = 1\n",
                "lib/b.py": "import a\n",
                "lib/c.py": "from b import y\n",
                "lib/d.py": "import os\n",
            }
        )
        self.assertEqual(g.affected("lib/a.py"), {"lib/a.py", "lib/b.py", "lib/c.py"})
        self.assertEqual(g.affected("lib/d.py"), {"lib/d.py"})

    def test_a_shell_script_that_names_a_file_or_runs_a_command_reaches_it(self) -> None:
        g = self.graph(
            {
                "lib/tool.py": "x = 1\n",
                "libexec/bosun-one": 'exec python3 "$BOSUN_ROOT/lib/tool.py" "$@"\n',
                "libexec/bosun-two": 'out="$("$BOSUN" one)"\n',
                "libexec/bosun-three": "bosun one\n",
            }
        )
        self.assertIn("libexec/bosun-one", g.affected("lib/tool.py"))
        # one step: what runs `bosun one` is reached by a change to the command, not to what the command runs
        self.assertIn("libexec/bosun-two", g.affected("libexec/bosun-one"))
        self.assertIn("libexec/bosun-three", g.affected("libexec/bosun-one"))
        self.assertNotIn("libexec/bosun-two", g.affected("lib/tool.py"))

    def test_python_calling_a_command_reaches_it(self) -> None:
        g = self.graph({"lib/m.py": 'run(["bosun", "one"])\n', "libexec/bosun-one": "#!/bin/bash\n"})
        self.assertIn("lib/m.py", g.affected("libexec/bosun-one"))

    def test_a_comment_or_a_message_is_not_a_reference(self) -> None:
        g = self.graph(
            {
                "libexec/bosun-one": "#!/bin/bash\n",
                "libexec/bosun-two": '#!/bin/bash\n# runs bosun one\necho "run bosun one first"\n',
                "lib/m.py": '"""Says bosun one."""\nprint("bosun-one is not a call here")\n',
            }
        )
        self.assertEqual(g.affected("libexec/bosun-one"), {"libexec/bosun-one"})

    def test_prose_is_never_a_dependency(self) -> None:
        g = self.graph({"docs/setup.md": "# x\n", "libexec/bosun-one": 'echo "see docs/setup.md"\n'})
        self.assertEqual(g.affected("docs/setup.md"), {"docs/setup.md"})

    def test_what_common_sh_names_under_lib_is_shared_by_every_section(self) -> None:
        g = self.graph(
            {
                "lib/common.sh": 'python3 "$BOSUN_ROOT/lib/shared.py"\nbosun alert\n',
                "lib/shared.py": "",
                "libexec/bosun-alert": "",
            }
        )
        self.assertEqual(g.common_refers, {"lib/shared.py"})  # not the command in its text


class ScratchSuite(Scratch):
    """tests/smoke.sh's options around fake sections, in a scratch checkout with its own selection."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.dir / "checkout"
        self.root.mkdir()
        (self.root / "tests").mkdir()
        (self.root / "lib").symlink_to(ROOT / "lib")
        (self.root / "tools").symlink_to(ROOT / "tools")
        shutil.copy(ROOT / "tests" / "smoke_cache.sh", self.root / "tests" / "smoke_cache.sh")
        (self.root / "tests" / "smoke_selection.txt").write_text(SELECTION)
        for name, text in FILES.items():
            self.write(name, text)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        self.script = self.suite()
        self.cache = self.dir / "cache"

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def suite(self) -> Path:
        """tests/smoke.sh with its sections replaced by the fake ones: its setup, then them, then its end."""
        text = SMOKE.read_text()
        first = re.search(r"^section syntax ", text, re.MULTILINE)
        last = re.search(r'^always_run "the repository this ran from"', text, re.MULTILINE)
        assert first is not None and last is not None
        script = self.dir / "smoke-fake.sh"
        script.write_text(text[: first.start()] + FAKE_SECTIONS + "always_run\n" + text[last.start() :])
        return script

    def run_suite(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(self.script), *args],
            capture_output=True,
            text=True,
            check=False,
            env=clean_env(SMOKE_ROOT=str(self.root), SMOKE_CACHE_DIR=str(self.cache), **env),
            stdin=subprocess.DEVNULL,
        )

    def ran(self, result: subprocess.CompletedProcess[str]) -> list[str]:
        """The fake sections that ran, from their headings."""
        names = {"alpha": "the first fake section", "beta": "the second", "gamma": "the third"}
        return [section for section, heading in names.items() if f"\n{heading}" in "\n" + result.stdout]

    def commit(self, message: str = "change") -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)


class ChangedTest(ScratchSuite):
    def test_a_covered_file_selects_its_section_and_what_it_needs(self) -> None:
        self.write("templates/job.plist", "<plist>changed</plist>\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ran(result), ["alpha", "gamma"])  # gamma covers it, and needs alpha
        self.assertIn("  gamma: templates/job.plist", result.stdout)
        self.assertIn("1 section(s) (and, as they need them: alpha)", result.stdout)
        self.assertIn("a partial run, not the whole suite: gamma + alpha", result.stdout)

    def test_a_change_reaches_the_sections_of_the_files_that_run_what_changed(self) -> None:
        self.write("libexec/bosun-alpha", "#!/bin/bash\necho alpha changed\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(self.ran(result), ["alpha", "beta"])  # beta's command runs alpha's
        self.assertNotIn("gamma", result.stdout)

    def test_the_report_says_which_file_selected_which_section(self) -> None:
        self.write("extra/deep/er/file.txt", "y\n")
        self.write("docs/guide.md", "# changed\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertIn("2 changed file(s)", result.stdout)
        self.assertIn("  beta: extra/deep/er/file.txt", result.stdout)
        self.assertIn("docs/guide.md is ignored", result.stdout)

    def test_a_file_nothing_covers_runs_the_whole_suite_and_says_what_to_add(self) -> None:
        self.write("libexec/bosun-delta", "#!/bin/bash\necho delta changed\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        self.assertIn("the whole suite: no section covers libexec/bosun-delta", result.stdout)
        self.assertIn("covers '...'", result.stdout)
        self.assertIn("tests/smoke_selection.txt", result.stdout)
        self.assertNotIn("a partial run", result.stdout)

    def test_a_file_on_the_select_everything_list_runs_the_whole_suite(self) -> None:
        self.write("shared.sh", "echo shared changed\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        self.assertIn("shared.sh is in the select-everything list (shared.sh)", result.stdout)

    def test_one_file_that_needs_everything_outweighs_the_ones_that_do_not(self) -> None:
        self.write("templates/job.plist", "<plist>changed</plist>\n")
        self.write("shared.sh", "echo shared changed\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])

    def test_docs_only_has_nothing_to_run(self) -> None:
        self.write("docs/guide.md", "# changed\n")
        self.write("README.md", "# changed\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no change reaches a section, nothing to run", result.stdout)
        self.assertEqual(self.ran(result), [])
        self.assertNotIn("passed", result.stdout)

    def test_no_change_at_all_has_nothing_to_run(self) -> None:
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(result.returncode, 0)
        self.assertIn("0 changed file(s)", result.stdout)
        self.assertIn("nothing to run", result.stdout)
        self.assertEqual(self.ran(result), [])

    def test_an_untracked_new_file_counts(self) -> None:
        self.write("templates/new.plist", "<plist/>\n")
        self.assertEqual(self.ran(self.run_suite("--changed", "HEAD")), ["alpha", "gamma"])
        self.write("brand-new-file.txt", "nobody covers this either\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertIn("no section covers brand-new-file.txt", result.stdout)
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])

    def test_a_staged_and_an_unstaged_change_count(self) -> None:
        self.write("extra/deep/er/file.txt", "staged\n")
        git(self.root, "add", "extra/deep/er/file.txt")
        self.write("templates/job.plist", "unstaged\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        self.assertIn("2 changed file(s)", result.stdout)

    def test_a_ref_is_compared_from_the_merge_base_so_commits_since_count(self) -> None:
        git(self.root, "switch", "-q", "-c", "feature")
        self.write("extra/deep/er/file.txt", "committed\n")
        self.commit()
        self.write("templates/job.plist", "uncommitted\n")
        result = self.run_suite("--changed", "main")
        self.assertIn("2 changed file(s)", result.stdout)
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        # and what main gained after the branch left it is not the branch's change
        git(self.root, "checkout", "-q", "--", "templates/job.plist")
        git(self.root, "switch", "-q", "main")
        self.write("libexec/bosun-delta", "#!/bin/bash\nmain moved on\n")
        self.commit("main moves")
        git(self.root, "switch", "-q", "feature")
        result = self.run_suite("--changed", "main")
        self.assertIn("1 changed file(s)", result.stdout)
        self.assertEqual(self.ran(result), ["beta"])

    def test_a_ref_that_does_not_resolve_runs_the_whole_suite(self) -> None:
        result = self.run_suite("--changed", "origin/nowhere")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        self.assertIn("the whole suite: git merge-base origin/nowhere HEAD failed", result.stdout)

    def test_a_broken_selection_file_runs_the_whole_suite(self) -> None:
        (self.root / "tests" / "smoke_selection.txt").write_text("nonsense\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        self.assertIn("the whole suite: selection file, line 1", result.stdout)

    def test_a_tool_that_cannot_run_runs_the_whole_suite(self) -> None:
        (self.root / "tools").unlink()
        (self.root / "tools").mkdir()  # no smoke_select.py in it
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        self.assertIn("the whole suite: tools/smoke_select.py gave no answer", result.stdout)

    def test_a_whole_run_chosen_this_way_records_its_pass_and_a_partial_one_never_does(self) -> None:
        self.write("templates/job.plist", "<plist>changed</plist>\n")
        partial = self.run_suite("--changed", "HEAD")
        self.assertEqual(partial.returncode, 0, partial.stdout)
        self.assertFalse((self.cache / "passes").exists())
        self.write("notes.txt", "now a whole run\n")
        whole = self.run_suite("--changed", "HEAD")
        self.assertEqual(whole.returncode, 0, whole.stdout)
        self.assertRegex((self.cache / "passes").read_text(), r"^[0-9a-f]{40} \d+ \d+\n$")

    def test_it_does_not_combine_with_the_other_ways_to_choose(self) -> None:
        for args in (("--only", "alpha"), ("--last-failed",)):
            with self.subTest(args=args):
                result = self.run_suite("--changed", "HEAD", *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)
        self.assertEqual(self.run_suite("--changed").returncode, 2)


class PrePushTest(ScratchSuite):
    """--pre-push runs what the change from origin/main (BOSUN_SMOKE_BASE here: the scratch repository has no
    origin) can reach; the tree cache and the escape hatches stay."""

    def test_a_covered_change_runs_only_its_sections(self) -> None:
        self.write("extra/deep/er/file.txt", "changed\n")
        result = self.run_suite("--pre-push", BOSUN_SMOKE_BASE="HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ran(result), ["beta"])
        self.assertFalse((self.cache / "passes").exists())

    def test_the_default_base_is_origin_main_and_without_one_the_whole_suite_runs(self) -> None:
        self.write("extra/deep/er/file.txt", "changed\n")
        result = self.run_suite("--pre-push")
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        self.assertIn("the whole suite: git merge-base origin/main HEAD failed", result.stdout)

    def test_a_remote_main_is_the_base_when_there_is_one(self) -> None:
        remote = self.dir / "remote.git"
        git(self.dir, "init", "-q", "--bare", "-b", "main", str(remote))
        git(self.root, "remote", "add", "origin", str(remote))
        git(self.root, "push", "-q", "origin", "main")
        git(self.root, "fetch", "-q", "origin")
        git(self.root, "switch", "-q", "-c", "feature")
        self.write("templates/job.plist", "<plist>changed</plist>\n")
        self.commit()
        result = self.run_suite("--pre-push")
        self.assertEqual(self.ran(result), ["alpha", "gamma"])

    def test_full_forces_the_whole_suite(self) -> None:
        self.write("extra/deep/er/file.txt", "changed\n")
        result = self.run_suite("--pre-push", BOSUN_SMOKE_BASE="HEAD", BOSUN_SMOKE_FULL="1")
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        self.assertIn("BOSUN_SMOKE_FULL=1: running the whole suite", result.stdout)

    def test_docs_only_pushes_run_nothing(self) -> None:
        self.write("docs/guide.md", "# changed\n")
        result = self.run_suite("--pre-push", BOSUN_SMOKE_BASE="HEAD")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.ran(result), [])
        self.assertIn("nothing to run", result.stdout)

    def test_a_tree_that_passed_a_whole_run_still_skips_everything(self) -> None:
        self.assertEqual(self.run_suite().returncode, 0)  # a whole run records this tree
        result = self.run_suite("--pre-push", BOSUN_SMOKE_BASE="HEAD", PRE_COMMIT_TO_REF="HEAD")
        self.assertIn("smoke already passed on this exact tree", result.stdout)
        self.assertNotIn("smoke selection", result.stdout)
        self.assertEqual(self.ran(result), [])

    def test_the_cache_escape_hatch_still_gets_the_selection_of_the_change(self) -> None:
        self.assertEqual(self.run_suite().returncode, 0)
        result = self.run_suite(
            "--pre-push", BOSUN_SMOKE_BASE="HEAD", PRE_COMMIT_TO_REF="HEAD", BOSUN_SMOKE_NO_CACHE="1"
        )
        self.assertIn("BOSUN_SMOKE_NO_CACHE=1: running the whole suite", result.stdout)
        # (the clean tree against HEAD: nothing changed, so nothing is selected)
        self.assertIn("nothing to run", result.stdout)

    def test_a_failing_section_in_a_partial_run_fails_the_push(self) -> None:
        text = self.script.read_text().replace('check "beta one" true', 'check "beta one" false')
        self.script.write_text(text)
        self.write("extra/deep/er/file.txt", "changed\n")
        result = self.run_suite("--pre-push", BOSUN_SMOKE_BASE="HEAD")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL  beta one", result.stdout)


if __name__ == "__main__":
    unittest.main()
