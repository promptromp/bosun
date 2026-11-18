"""Which sections of the smoke suite a change reaches (tools/smoke_select.py, `tests/smoke.sh --changed
<ref>` and the pre-push hook's use of it), and which of them one shard of a split run runs
(tools/smoke_shard.py, `tests/smoke.sh --shard K/N`, CI's `smoke`).

The pure parts (the globs, the section and rule files, the dependency scan, the split) are called directly.
The script's own options (`--changed`, `--pre-push` with its base, `--shard`) go through a script generated
for the test, as in test_smoke_cache.py: the real script's setup and its `section`/`check` machinery around
fake sections, in a scratch git repository with its own tests/smoke_selection.txt. No real section runs here.

The rule under test is that selection can only fail towards running more: a file nothing covers, a file on
the select-everything list, a ref git cannot resolve, a selection that crashes: the whole suite. A split
that cannot be made runs every selected section in the shard; one that can puts each in exactly one shard."""

from __future__ import annotations

import difflib
import itertools
import random
import re
import shutil
import statistics
import subprocess
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path

from test_smoke_cache import ROOT, SMOKE, Scratch, clean_env, git


sys.path.insert(0, str(ROOT / "tools"))
import smoke_select as ss
import smoke_shard as sh


SELECTION = """# a scratch selection
all shared.sh
all tests/smoke.sh
ignore docs/**
ignore *.md
ignore libexec/bosun-wake
"""

# alpha covers a command, beta one more and a shell file that calls the command, gamma needs alpha and
# covers the templates. (Their checks do nothing: no section of the real suite runs here.)
FAKE_SECTIONS = """section syntax "syntax" covers 'libexec/**'
check "syntax one" true
section alpha "the first fake section" covers 'libexec/bosun-alpha'
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
    "libexec/bosun-delta": "#!/bin/bash\necho delta\n",  # only the catch-all `syntax` covers it
    "libexec/bosun-wake": "#!/bin/bash\necho needs hardware\n",  # ignored: the hardware test
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

    def test_the_commands_common_sh_runs_are_kept(self) -> None:
        g = self.graph(
            {
                "lib/common.sh": 'build() { "$BOSUN_CMD" launch "$1"; }\n# bosun alert is only said here\n',
                "libexec/bosun-launch": "",
                "libexec/bosun-alert": "",
                "libexec/bosun-ledger": "",
            }
        )
        self.assertEqual(g.common_commands, {"libexec/bosun-launch"})

    def test_what_common_sh_names_under_lib_is_shared_by_every_section(self) -> None:
        g = self.graph(
            {
                "lib/common.sh": 'python3 "$BOSUN_ROOT/lib/shared.py"\nbosun alert\n',
                "lib/shared.py": "",
                "libexec/bosun-alert": "",
            }
        )
        self.assertEqual(g.common_refers, {"lib/shared.py"})  # not the command in its text


SUITE_TEXT = """\
#!/bin/bash
check() { "$@"; }
setup_done=1
section alpha "alpha" covers 'libexec/bosun-alpha'
check true
section beta "beta" needs alpha covers 'libexec/bosun-beta'
probe() { check true; }
probe
section gamma "gamma" covers 'libexec/bosun-gamma'
probe
always_run
# the end
finish() { :; }
finish
"""


def diff_hunks(old: str, new: str) -> tuple[tuple[int, int, int, int], ...]:
    """The hunks `git diff -U0` gives from <old> to <new>, numbered as git numbers them."""
    found = []
    matcher = difflib.SequenceMatcher(a=old.split("\n"), b=new.split("\n"), autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            found.append((i1 + (i2 > i1), i2 - i1, j1 + (j2 > j1), j2 - j1))
    return tuple(found)


class SuiteEditTest(unittest.TestCase):
    """A change to tests/smoke.sh, line by line: a section's own lines select it (and those that need it), a
    helper's the sections that call it, the shared setup's (or a helper it calls) the whole suite; code the
    change left alone but moved into another region, both regions."""

    def plan(self, new: str, *hunks: tuple[int, int, int, int], old: str = SUITE_TEXT) -> ss.Plan:
        suite = ss.Suite(new)
        graph = ss.Graph(Path("/nonexistent"), [])
        return ss.plan_for(
            [ss.SUITE], suite.sections, ss.Rules((), ()), graph, suite, ss.SuiteChange(old, hunks)
        )

    def test_a_line_in_a_section_selects_it_and_what_needs_it(self) -> None:
        plan = self.plan(
            SUITE_TEXT.replace("check true\nsection beta", "check false\nsection beta"), (5, 1, 5, 1)
        )
        self.assertEqual(sorted(plan.picked), ["alpha", "beta"])  # beta needs alpha
        self.assertEqual(plan.picked["alpha"], ["tests/smoke.sh"])

    def test_a_helper_selects_the_sections_that_call_it_and_what_needs_them(self) -> None:
        plan = self.plan(
            SUITE_TEXT.replace("probe() { check true; }", "probe() { check false; }"), (7, 1, 7, 1)
        )
        self.assertEqual(sorted(plan.picked), ["beta", "gamma"])

    def test_the_shared_setup_runs_the_whole_suite_and_its_comments_nothing(self) -> None:
        plan = self.plan(SUITE_TEXT.replace("setup_done=1", "setup_done=2"), (3, 1, 3, 1))
        self.assertTrue(plan.full)
        self.assertIn("tests/smoke.sh, line 3: the shared setup", plan.reason)
        plan = self.plan(SUITE_TEXT.replace("# the end", "# the very end"), (12, 1, 12, 1))
        self.assertEqual((plan.full, plan.picked, plan.unchanged), (False, {}, ["tests/smoke.sh"]))
        plan = self.plan(SUITE_TEXT.replace("finish\n", "finish now\n"), (14, 1, 14, 1))  # after always_run
        self.assertTrue(plan.full)
        plan = self.plan(SUITE_TEXT.replace("#!/bin/bash", "#!/bin/sh"), (1, 1, 1, 1))  # (the #! is code)
        self.assertTrue(plan.full)

    def test_a_helper_the_shared_setup_calls_runs_the_whole_suite_and_one_sections_call_those(self) -> None:
        plan = self.plan(SUITE_TEXT.replace("finish() { :; }", "finish() { true; }"), (13, 1, 13, 1))
        self.assertTrue(plan.full)
        self.assertIn("tests/smoke.sh, line 13: the shared setup (or a helper it calls)", plan.reason)
        plan = self.plan(SUITE_TEXT.replace('check() { "$@"; }', 'check() { "$@" || exit 1; }'), (2, 1, 2, 1))
        self.assertEqual(sorted(plan.picked), ["alpha", "beta", "gamma"])

    def test_lines_taken_out_count_where_they_were(self) -> None:
        plan = self.plan(SUITE_TEXT.replace("probe\nsection gamma", "section gamma"), (8, 1, 7, 0))
        self.assertEqual(sorted(plan.picked), ["beta"])  # beta's own line, though next to a helper now
        plan = self.plan(SUITE_TEXT.replace("setup_done=1\n", ""), (3, 1, 2, 0))
        self.assertTrue(plan.full)

    def test_code_of_the_shared_setup_made_a_comment_runs_the_whole_suite(self) -> None:
        plan = self.plan(SUITE_TEXT.replace("setup_done=1", "# setup_done=1"), (3, 1, 3, 1))
        self.assertTrue(plan.full)
        self.assertIn("tests/smoke.sh, line 3: the shared setup", plan.reason)

    def test_a_section_taken_out_selects_none_of_its_own(self) -> None:
        new = SUITE_TEXT.replace("section gamma \"gamma\" covers 'libexec/bosun-gamma'\nprobe\n", "")
        plan = self.plan(new, (9, 2, 8, 0))
        self.assertEqual((plan.full, plan.picked), (False, {}))  # (what is gone runs nowhere)

    def moved(self, new: str, old: str = SUITE_TEXT) -> ss.Plan:
        return self.plan(new, *diff_hunks(old, new), old=old)

    def test_a_marker_taken_out_selects_the_section_its_lines_join(self) -> None:
        plan = self.moved(SUITE_TEXT.replace("section gamma \"gamma\" covers 'libexec/bosun-gamma'\n", ""))
        self.assertEqual(sorted(plan.picked), ["beta"])  # gamma's `probe` now runs in beta

    def test_a_marker_after_always_run_turns_shared_setup_into_a_section_s_code(self) -> None:
        plan = self.moved(
            SUITE_TEXT.replace("always_run\n", "always_run\nsection delta \"delta\" covers 'x'\n")
        )
        self.assertTrue(plan.full)
        self.assertIn("the shared setup", plan.reason)

    def test_a_section_split_selects_both_halves_and_what_needs_them(self) -> None:
        plan = self.moved(
            SUITE_TEXT.replace(
                "probe\nsection gamma", "section beta-tail \"tail\" covers 'x'\nprobe\nsection gamma"
            )
        )
        self.assertEqual(sorted(plan.picked), ["beta", "beta-tail"])

    def test_a_marker_moved_up_selects_the_section_that_lost_the_lines_too(self) -> None:
        new = SUITE_TEXT.replace(
            "probe\nsection gamma \"gamma\" covers 'libexec/bosun-gamma'\n",
            "section gamma \"gamma\" covers 'libexec/bosun-gamma'\nprobe\n",
        )
        plan = self.moved(new)
        self.assertEqual(sorted(plan.picked), ["beta", "gamma"])

    def test_a_helper_s_closing_brace_moved_over_shared_code_runs_the_whole_suite(self) -> None:
        """The `}` is a line of a helper only gamma calls, on both sides; `after=1`, left alone, leaves the
        shared setup for the helper."""
        old = (
            SUITE_TEXT.replace("probe\nalways_run", "probe\nlate\nalways_run") + "late() {\n  :\n}\nafter=1\n"
        )
        new = old.replace("  :\n}\nafter=1\n", "  :\nafter=1\n}\n")
        self.assertEqual(old.split("\n")[17:19], ["}", "after=1"])
        plan = self.plan(new, (18, 1, 17, 0), (19, 0, 19, 1), old=old)  # the `}` out, and in after after=1
        self.assertTrue(plan.full)
        self.assertIn("the shared setup", plan.reason)

    def test_a_marker_put_in_before_a_helper_last_in_its_section_selects_its_callers_and_both_sections(
        self,
    ) -> None:
        """probe is now defined only in a run that runs split: gamma, which calls it, must run (and fails)."""
        old = SUITE_TEXT.replace("probe() { check true; }\nprobe\n", "probe\nprobe() { check true; }\n")
        new = old.replace("probe\nprobe() {", "probe\nsection split \"split\" covers 'x'\nprobe() {")
        self.assertEqual(sorted(self.moved(new, old=old).picked), ["beta", "gamma", "split"])

    def test_a_marker_put_in_before_a_helper_the_shared_setup_defines_runs_the_whole_suite(self) -> None:
        old = SUITE_TEXT.replace("probe\nalways_run", "probe\nlate\nalways_run") + "late() { :; }\n"
        new = old.replace("late() {", "section zz \"zz\" covers 'x'\nlate() {")
        plan = self.moved(new, old=old)
        self.assertTrue(plan.full)
        self.assertIn("a helper the shared setup defines", plan.reason)

    def test_a_new_section_between_two_moves_no_code_and_selects_itself(self) -> None:
        new = SUITE_TEXT.replace("section gamma", "section new \"new\" covers 'x'\ncheck true\nsection gamma")
        self.assertEqual(sorted(self.moved(new).picked), ["new"])

    def test_what_cannot_be_read_or_placed_runs_the_whole_suite(self) -> None:
        for text, hunks, old, reason in (
            (SUITE_TEXT, ((40, 1, 40, 1),), SUITE_TEXT, "a changed line (40) that cannot be placed"),
            (SUITE_TEXT + "echo 'open\n", ((3, 1, 3, 1),), SUITE_TEXT, "does not read as functions"),
            (SUITE_TEXT, (), SUITE_TEXT, "changed, and how cannot be read"),
            (SUITE_TEXT, ((3, 1, 3, 1),), "", "a file that was not there"),
            (SUITE_TEXT, ((5, 1, 5, 1),), "echo 'open\n", "at the base does not read as functions"),
        ):
            with self.subTest(reason=reason):
                plan = self.plan(text, *hunks, old=old)
                self.assertTrue(plan.full)
                self.assertIn(reason, plan.reason)


class SuiteChangeTest(Scratch):
    """tests/smoke.sh's text at the base and the hunks since, read from git."""

    def test_a_suite_not_there_at_the_base_has_no_old_text_and_git_failing_is_an_error(self) -> None:
        root = self.dir / "repo"
        (root / "tests").mkdir(parents=True)
        (root / "README").write_text("x\n")
        git(root, "init", "-q", "-b", "main")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "base")
        (root / "tests" / "smoke.sh").write_text("#!/bin/bash\necho\n")
        git(root, "add", "-A")
        change = ss.suite_change(root, "HEAD")
        self.assertEqual((change.old, change.hunks), ("", ((0, 0, 1, 2),)))
        with self.assertRaisesRegex(ss.SelectionError, "failed"):
            ss.suite_change(root, "no-such-ref")


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
        script.write_text(text[: first.start()] + self.fake_sections + "always_run\n" + text[last.start() :])
        return script

    fake_sections = FAKE_SECTIONS

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

    def test_the_catch_all_section_alone_is_no_coverage(self) -> None:
        """`syntax` covers every file of libexec/, so it says nothing: a command only it covers runs all."""
        self.write("libexec/bosun-delta", "#!/bin/bash\necho delta changed\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        self.assertIn("the whole suite: no section covers libexec/bosun-delta", result.stdout)
        self.assertIn("covers '...'", result.stdout)
        self.assertIn("tests/smoke_selection.txt", result.stdout)
        self.assertNotIn("a partial run", result.stdout)

    def test_the_catch_all_still_runs_beside_the_sections_that_cover_the_file(self) -> None:
        self.write("libexec/bosun-alpha", "#!/bin/bash\necho alpha changed\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertIn("  syntax: libexec/bosun-alpha", result.stdout)
        self.assertIn("syntax one", result.stdout)

    def test_an_ignored_command_runs_only_the_catch_all(self) -> None:
        self.write("libexec/bosun-wake", "#!/bin/bash\necho still needs hardware\n")
        result = self.run_suite("--changed", "HEAD")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.ran(result), [])
        self.assertIn("pass  syntax one", result.stdout)
        self.assertIn("a partial run, not the whole suite: syntax", result.stdout)

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

    def test_with_list_it_prints_the_choice_and_runs_nothing(self) -> None:
        self.write("templates/job.plist", "<plist>changed</plist>\n")
        self.write("docs/guide.md", "# changed\n")
        result = self.run_suite("--changed", "HEAD", "--list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ran(result), [])
        self.assertIn("  gamma: templates/job.plist", result.stdout)
        self.assertIn("docs/guide.md is ignored", result.stdout)
        self.assertIn("=> would run: gamma", result.stdout)
        self.assertFalse(self.cache.exists())  # no section ran, so nothing was recorded

    def test_with_list_the_whole_suite_and_nothing_are_said_too(self) -> None:
        self.write("libexec/bosun-delta", "#!/bin/bash\necho delta changed\n")
        result = self.run_suite("--list", "--changed", "HEAD")
        self.assertEqual((result.returncode, self.ran(result)), (0, []), result.stdout)
        self.assertIn("=> would run: the whole suite", result.stdout)
        self.commit()
        self.write("docs/guide.md", "# changed\n")
        result = self.run_suite("--changed", "HEAD", "--list")
        self.assertEqual((result.returncode, self.ran(result)), (0, []), result.stdout)
        self.assertIn("=> would run: nothing", result.stdout)

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

    def test_the_default_base_is_origin_main_and_without_one_the_whole_suite_is_the_queue_s(self) -> None:
        self.write("extra/deep/er/file.txt", "changed\n")
        result = self.run_suite("--pre-push")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.ran(result), [])
        self.assertIn("the whole suite: git merge-base origin/main HEAD failed", result.stdout)
        self.assertIn("the change reaches the whole suite, which the merge queue runs", result.stdout)

    def test_a_change_that_reaches_every_section_is_left_to_the_merge_queue(self) -> None:
        """The hook does not wait out the whole suite that the merge queue's smoke job runs anyway."""
        self.write("shared.sh", "# changed\n")  # on the select-everything list
        result = self.run_suite("--pre-push", BOSUN_SMOKE_BASE="HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ran(result), [])
        self.assertIn("BOSUN_SMOKE_FULL=1 runs it here", result.stdout)
        ci = self.run_suite("--changed", "HEAD")  # the merge queue's way: the whole suite
        self.assertEqual(self.ran(ci), ["alpha", "beta", "gamma"])

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
        self.assertIn("BOSUN_SMOKE_NO_CACHE=1: ignoring the pass record; running the sections", result.stdout)
        self.assertIn("BOSUN_SMOKE_FULL=1 forces the whole suite", result.stdout)
        # (the clean tree against HEAD: nothing changed, so nothing is selected)
        self.assertIn("nothing to run", result.stdout)

    def test_full_forces_the_whole_suite_even_for_a_tree_that_passed(self) -> None:
        self.assertEqual(self.run_suite().returncode, 0)
        result = self.run_suite(
            "--pre-push", BOSUN_SMOKE_BASE="HEAD", PRE_COMMIT_TO_REF="HEAD", BOSUN_SMOKE_FULL="1"
        )
        self.assertNotIn("already passed", result.stdout)
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])

    def test_a_failing_section_in_a_partial_run_fails_the_push(self) -> None:
        text = self.script.read_text().replace('check "beta one" true', 'check "beta one" false')
        self.script.write_text(text)
        self.write("extra/deep/er/file.txt", "changed\n")
        result = self.run_suite("--pre-push", BOSUN_SMOKE_BASE="HEAD")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL  beta one", result.stdout)

    def test_sections_the_hook_would_take_too_long_on_are_left_to_the_merge_queue(self) -> None:
        """The time the hook would take: the longest of the shards it runs them as (--jobs), by weight."""
        self.write("tests/smoke_weights.txt", "beta 300\nalpha 100\ngamma 200\n")
        self.commit("weights")
        self.write("extra/deep/er/file.txt", "changed\n")  # beta covers it
        self.write("templates/job.plist", "<plist>changed</plist>\n")  # gamma, which needs alpha
        result = self.run_suite("--pre-push", BOSUN_SMOKE_BASE="HEAD")
        self.assertEqual((result.returncode, self.ran(result)), (0, []), result.stdout)
        self.assertIn(
            "those sections would take this hook about 600 s by smoke_weights.txt (as one run), more than "
            "BOSUN_SMOKE_DEFER_SECONDS (180), so the merge queue runs them; tests/smoke.sh --changed HEAD "
            "runs them",
            result.stdout,
        )
        as_three = self.run_suite("--pre-push", "--jobs", "3", BOSUN_SMOKE_BASE="HEAD")
        self.assertEqual(self.ran(as_three), [], as_three.stdout)
        self.assertIn(
            "about 300 s by smoke_weights.txt (the longest of 3 shards side by side)", as_three.stdout
        )
        self.assertIn("tests/smoke.sh --changed HEAD --jobs 3 runs them here", as_three.stdout)
        raised = {"BOSUN_SMOKE_BASE": "HEAD", "BOSUN_SMOKE_DEFER_SECONDS": "350"}
        self.assertEqual(
            self.ran(self.run_suite("--pre-push", "--jobs", "3", **raised)), ["alpha", "beta", "gamma"]
        )
        self.assertEqual(self.ran(self.run_suite("--pre-push", **raised)), [])  # 600 s as one run: over it
        by_hand = self.run_suite("--changed", "HEAD")  # only the hook leaves it to the queue
        self.assertEqual(self.ran(by_hand), ["alpha", "beta", "gamma"])

    def test_sections_it_cannot_weigh_run(self) -> None:
        self.write("tests/smoke_weights.txt", "beta lots\n")
        self.commit("weights")
        self.write("extra/deep/er/file.txt", "changed\n")
        for env in ({}, {"BOSUN_SMOKE_DEFER_SECONDS": "soon"}):
            with self.subTest(env=env):
                result = self.run_suite("--pre-push", BOSUN_SMOKE_BASE="HEAD", **env)
                self.assertEqual(self.ran(result), ["beta"], result.stdout)


def sections(*specs: str) -> list[ss.Section]:
    """Sections from `id` or `id:need,need` specs, in that order."""
    found = []
    for spec in specs:
        sid, _, needs = spec.partition(":")
        found.append(ss.Section(sid, sid, tuple(n for n in needs.split(",") if n), ()))
    return found


# A made-up suite for the split: c needs a, e needs c, h needs f, k needs j; i and k are not weighed.
MANY = sections("syntax", "a", "b", "c:a", "d", "e:c", "f", "g", "h:f", "i", "j", "uninstall-files", "k:j")
WEIGHTS = {"syntax": 3, "a": 40, "b": 5, "c": 7, "d": 60, "e": 2, "f": 9, "g": 1, "h": 30, "j": 50}


class ShardSplitTest(unittest.TestCase):
    """The split itself (tools/smoke_shard.py), on sections made up for each test."""

    def test_every_section_is_in_exactly_one_shard_and_brings_what_it_needs(self) -> None:
        ids = [s.id for s in MANY]
        for count in range(1, 6):
            with self.subTest(count=count):
                shards = sh.split(MANY, ids, WEIGHTS, count)
                self.assertEqual(len(shards), count)
                owned = [i for s in shards for i in s.own if i not in sh.EVERY_SHARD]
                self.assertCountEqual(owned, [i for i in ids if i not in sh.EVERY_SHARD])
                for shard in shards:
                    self.assert_runs_what_it_needs(shard, ids)

    def assert_runs_what_it_needs(self, shard: sh.Shard, ids: list[str]) -> None:
        """<shard> runs what each of its sections needs, apart from them, in file order."""
        needs = sh.requirements(MANY)
        runs = set(shard.own) | set(shard.needed)
        for own in shard.own:
            self.assertLessEqual(needs[own], runs, f"{own} runs without what it needs")
        self.assertFalse(set(shard.own) & set(shard.needed))
        self.assertEqual(shard.needed, tuple(i for i in ids if i in shard.needed))

    def test_what_a_section_needs_is_followed_all_the_way(self) -> None:
        self.assertEqual(sh.requirements(MANY)["e"], {"c", "a"})
        shards = sh.split(MANY, ["e"], WEIGHTS, 2)
        self.assertEqual((shards[0].own, shards[0].needed), (("e",), ("a", "c")))
        self.assertEqual(shards[1].own, ())

    def test_the_shards_are_balanced_by_weight_not_by_count(self) -> None:
        flat = sections("big", "s1", "s2", "s3", "s4", "s5")
        weights = {"big": 100, "s1": 20, "s2": 20, "s3": 20, "s4": 20, "s5": 20}
        shards = sh.split(flat, [s.id for s in flat], weights, 2)
        self.assertEqual([s.own for s in shards], [("big",), ("s1", "s2", "s3", "s4", "s5")])
        self.assertEqual([s.seconds for s in shards], [100, 100])

    def test_the_longest_shard_is_as_short_as_any_split_can_make_it(self) -> None:
        """d (60), j with k (58), a with c and e (49) start the three shards; f with h (39) must join one
        of them, which the shortest takes: 88, no split does better. uninstall-files (8, the median) is in
        each."""
        shards = sh.split(MANY, [s.id for s in MANY], WEIGHTS, 3)
        self.assertEqual(max(s.seconds for s in shards), 88 + 8, [s.seconds for s in shards])
        longest = max(shards, key=lambda s: s.seconds)
        self.assertEqual({*longest.own, *longest.needed}, {"a", "c", "e", "f", "h", "uninstall-files"})

    def test_on_a_selection_it_comes_close_to_the_best_split_there_is(self) -> None:
        """Against every way there is to split a few of the made-up sections (a seeded sample of them)."""
        needs = sh.requirements(MANY)
        fallback = round(statistics.median(WEIGHTS.values()))

        def longest(groups: Sequence[Sequence[str]]) -> int:
            runs = [set().union(*({s} | needs[s] for s in group)) for group in groups]
            return max(sum(WEIGHTS.get(i, fallback) for i in run) for run in runs)

        rng = random.Random(7)  # noqa: S311 - a seeded sample of test cases, not a secret
        placeable = [s.id for s in MANY if s.id not in sh.EVERY_SHARD]
        for _ in range(15):
            chosen = rng.sample(placeable, rng.randint(3, 7))
            count = rng.choice((2, 3))
            best = min(
                longest(
                    [[s for s, k in zip(chosen, ks, strict=True) if k == shard] for shard in range(count)]
                )
                for ks in itertools.product(range(count), repeat=len(chosen))
            )
            got = max(s.seconds for s in sh.split(MANY, chosen, WEIGHTS, count))
            self.assertLessEqual(got, best * 1.05, f"{chosen} in {count}")

    def test_a_section_that_needs_a_long_one_goes_where_that_one_runs(self) -> None:
        chain = sections("update", "small1", "small2", "convert:update")
        weights = {"update": 300, "small1": 50, "small2": 50, "convert": 40}
        shards = sh.split(chain, ["update", "small1", "small2", "convert"], weights, 2)
        together = next(s for s in shards if "convert" in s.own)
        self.assertIn("update", together.own)
        self.assertEqual(sum(s.seconds for s in shards), 440)  # update runs once

    def test_a_section_the_weights_do_not_list_counts_as_their_median(self) -> None:
        flat = sections("x", "y", "z", "new")
        shards = sh.split(flat, ["x", "y", "z", "new"], {"x": 10, "y": 30, "z": 20}, 1)
        self.assertEqual(shards[0].seconds, 80)
        self.assertEqual(sh.split(flat, ["new"], {}, 1)[0].seconds, 1)  # no weights: each counts one

    def test_the_section_that_checks_what_the_run_left_runs_in_every_shard_that_runs_anything(self) -> None:
        shards = sh.split(MANY, ["a", "d", "j", "uninstall-files"], WEIGHTS, 5)
        busy = [s for s in shards if set(s.own) - {"uninstall-files"}]
        self.assertEqual(len(busy), 3)
        self.assertTrue(all("uninstall-files" in s.own for s in busy))
        self.assertTrue(all(s.own == () for s in shards if s not in busy))
        alone = sh.split(MANY, ["uninstall-files"], WEIGHTS, 3)
        self.assertEqual([s.own for s in alone], [("uninstall-files",), (), ()])

    def test_the_split_is_the_same_every_time_and_a_repeated_id_counts_once(self) -> None:
        ids = [s.id for s in MANY]
        first = sh.split(MANY, ids, WEIGHTS, 3)
        self.assertEqual(sh.split(MANY, list(reversed(ids)), WEIGHTS, 3), first)
        self.assertEqual(sh.split(MANY, ids + ids, WEIGHTS, 3), first)

    def test_an_id_that_is_no_section_is_an_error(self) -> None:
        with self.assertRaisesRegex(sh.ShardError, "no section nope"):
            sh.split(MANY, ["a", "nope"], {}, 2)

    def test_k_of_n(self) -> None:
        self.assertEqual(sh.parse_shard("2/3"), (2, 3))
        self.assertEqual(sh.parse_shard("1/1"), (1, 1))
        for text in ("0/3", "4/3", "3", "1/0", "a/b", "01/3", "1/3/4", " 1/3", "", "-1/3"):
            with self.subTest(text=text), self.assertRaisesRegex(sh.ShardError, "1 <= K <= N"):
                sh.parse_shard(text)

    def test_a_weights_file_is_id_and_seconds_with_comments_and_anything_else_names_its_line(self) -> None:
        self.assertEqual(sh.parse_weights("# c\nalpha 12  # why\n\nbeta 0\n"), {"alpha": 12, "beta": 0})
        for text in ("alpha\n", "alpha 1.5\n", "alpha -3\n", "Alpha 3\n", "alpha 3 4\n"):
            with self.subTest(text=text), self.assertRaisesRegex(sh.ShardError, "line 1"):
                sh.parse_weights(text)

    def test_a_record_writes_the_weights_anew_keeping_what_it_did_not_measure(self) -> None:
        text = sh.refreshed(MANY[:4], {"a": 40, "b": 5, "gone": 9}, {"syntax": 0, "b": 6, "c": 12})
        self.assertTrue(text.startswith(sh.WEIGHTS_HEADER))
        self.assertEqual(sh.parse_weights(text), {"syntax": 1, "a": 40, "b": 6, "c": 12})
        self.assertIn("--record tests/.smoke-cache/durations", text)

    def test_the_tool_says_all_when_it_cannot_split(self) -> None:
        missing = Path("/nonexistent/smoke.sh")
        out = sh.shard_report(missing, missing, "1/2", None)
        self.assertEqual(out[-1], "=> all")
        self.assertIn("this shard runs every selected section", out[0])


class RealSplitTest(unittest.TestCase):
    """The real suite and its weights file."""

    sections: list[ss.Section]
    weights: dict[str, int]

    @classmethod
    def setUpClass(cls) -> None:
        cls.sections = ss.parse_sections(SMOKE.read_text())
        cls.weights = sh.parse_weights((ROOT / "tests" / "smoke_weights.txt").read_text())

    def test_the_weights_name_only_sections_there_are_and_most_of_them(self) -> None:
        ids = {s.id for s in self.sections}
        self.assertEqual(
            sorted(set(self.weights) - ids), [], "weights of sections there are not: remove them"
        )
        self.assertGreater(len(self.weights), len(ids) // 2)

    def test_the_sections_that_run_in_every_shard_are_sections(self) -> None:
        self.assertLessEqual(set(sh.EVERY_SHARD), {s.id for s in self.sections})

    def test_three_shards_take_every_section_once_and_each_about_a_third(self) -> None:
        ids = [s.id for s in self.sections]
        shards = sh.split(self.sections, ids, self.weights, 3)
        owned = [i for s in shards for i in s.own if i not in sh.EVERY_SHARD]
        self.assertCountEqual(owned, [i for i in ids if i not in sh.EVERY_SHARD])
        whole = sum(self.weights.get(i, 0) for i in ids)
        self.assertLess(max(s.seconds for s in shards), whole * 0.36, [s.seconds for s in shards])


# The fake sections of a split run: what --shard sends where, through the script. delta needs gamma, which
# needs alpha; uninstall-files checks what the run left, so it runs in every shard that runs anything.
SHARD_SECTIONS = (
    FAKE_SECTIONS
    + """section delta "the fourth, which needs the third" needs gamma covers 'extra/**'
check "delta one" true
section uninstall-files "the one in every shard" covers 'templates/*.plist'
check "uninstall-files one" true
"""
)
OWN = re.compile(
    r"^partial run, not the whole suite: (?P<own>.*?)(?: \(and, as they need them: (?P<needs>.*)\))?$"
)


class JobsOptionTest(ScratchSuite):
    """`tests/smoke.sh --jobs N`: what the run would run as N shards side by side, around the fake sections
    (the split of ShardOptionTest: beta alone in one of two shards, the rest in the other)."""

    fake_sections = SHARD_SECTIONS
    TOTAL = re.compile(r"^(\d+) passed, (\d+) failed(?: \((.*)\))?$", re.MULTILINE)

    def setUp(self) -> None:
        super().setUp()
        self.write(
            "tests/smoke_weights.txt", "syntax 1\nalpha 10\nbeta 30\ngamma 5\ndelta 5\nuninstall-files 1\n"
        )
        self.commit("weights")

    def run_jobs(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(self.script), *args],
            capture_output=True,
            text=True,
            check=False,
            env=clean_env(SMOKE_ROOT=str(self.root), SMOKE_CACHE_DIR=str(self.cache), **env),
            stdin=subprocess.DEVNULL,
            timeout=300,  # a failsafe against a shard that is never stopped, no measurement
        )

    def totals(self, result: subprocess.CompletedProcess[str]) -> list[tuple[int, int, str]]:
        return [(int(m[1]), int(m[2]), m[3] or "") for m in self.TOTAL.finditer(result.stdout)]

    def change_check(self, name: str, command: str) -> None:
        self.script.write_text(
            self.script.read_text().replace(f'check "{name}" true', f'check "{name}" {command}')
        )

    def test_the_shards_run_side_by_side_and_one_summary_counts_them_all(self) -> None:
        result = self.run_jobs("--jobs", "2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "2 shards side by side (--jobs 2), each a run of its own: tests/smoke.sh --shard K/2",
            result.stdout,
        )
        self.assertIn("=== shard 1 of 2 (exit 0) ===", result.stdout)
        self.assertIn("=== shard 2 of 2 (exit 0) ===", result.stdout)
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        *shards, whole = self.totals(result)
        self.assertEqual(len(shards), 2)
        self.assertEqual(whole, (sum(s[0] for s in shards), 0, "2 shards side by side"))

    def test_with_only_the_shards_split_those_sections(self) -> None:
        result = self.run_jobs("--jobs", "2", "--only", "beta")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ran(result), ["beta"])
        self.assertIn("smoke shard 2 of 2: no section falls in this shard, nothing to run", result.stdout)
        self.assertEqual(
            self.totals(result)[-1][2], "2 shards side by side, a partial run, not the whole suite: beta"
        )

    def test_a_failure_in_any_shard_fails_the_run_and_is_recorded(self) -> None:
        self.change_check("beta one", "false")
        result = self.run_jobs("--jobs", "2")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.totals(result)[-1][1], 1)
        self.assertIn("=== shard 1 of 2 (exit 1) ===", result.stdout)
        self.assertIn("beta fail", (self.cache / "sections").read_text())
        self.assertIn("alpha pass", (self.cache / "sections").read_text())

    def test_with_x_the_first_shard_that_fails_stops_the_others(self) -> None:
        self.change_check(
            "beta one", "false"
        )  # alone in shard 1; delta, in shard 2, waits for what never comes
        self.change_check("delta one", 'bash -c \'until [ -e "$1" ]; do sleep 0.1; done\' _ "$WORK/never"')
        result = self.run_jobs("-x", "--jobs", "2")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("stopped at the first failure (-x): section beta, check: beta one", result.stdout)
        self.assertIn("shard 2 of 2: stopped (-x), when another shard failed", result.stdout)
        self.assertEqual(self.totals(result)[-1][1], 1)  # the failure that stopped it, not one more for it

    def test_the_shards_results_are_merged_not_their_times_and_a_whole_run_records_its_tree(self) -> None:
        """Durations under the other shards' load would skew the weights a serial run measures."""
        result = self.run_jobs("--jobs", "2")
        self.assertEqual(result.returncode, 0, result.stdout)
        results = (self.cache / "sections").read_text().split("\n")
        for section in ("syntax", "alpha", "beta", "gamma", "delta", "uninstall-files"):
            self.assertIn(f"{section} pass", results)
        self.assertFalse((self.cache / "durations").exists())
        self.assertRegex((self.cache / "passes").read_text(), r"^[0-9a-f]{40} \d+ \d+\n$")

    def test_a_shard_does_not_start_with_sigint_ignored(self) -> None:
        """A script's background job starts with SIGINT ignored, which its children could not undo; a check
        that interrupts a command would pass serially and fail under --jobs."""
        python = "import signal, sys; sys.exit(signal.getsignal(signal.SIGINT) is signal.SIG_IGN)"
        self.change_check("beta one", f"python3 -c '{python}'")
        self.change_check("alpha one", f"python3 -c '{python}'")
        result = self.run_jobs("--jobs", "2")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("pass  beta one", result.stdout)

    def test_with_unit_and_x_a_failing_unit_run_starts_no_shard(self) -> None:
        self.script.write_text(
            self.script.read_text().replace(
                "always_run\n", 'always_run\n[ "$WITH_UNIT" = 0 ] || check "units" false\n', 1
            )
        )
        result = self.run_jobs("-x", "--jobs", "2", "--with-unit")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("=== the unit tests (exit 1) ===", result.stdout)
        self.assertNotIn("=== shard", result.stdout)

    def test_a_partial_run_records_no_tree(self) -> None:
        self.assertEqual(self.run_jobs("--jobs", "2", "--only", "gamma").returncode, 0)
        self.assertFalse((self.cache / "passes").exists())

    def test_with_unit_the_unit_tests_run_once_before_the_shards(self) -> None:
        """The shared setup and its always_run parts once, with --with-unit, before the shards, which run
        without it (the fake suite here has no unit tests of its own: the run is what shows)."""
        result = self.run_jobs("--jobs", "2", "--with-unit")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("=== the unit tests (exit 0) ==="), 1, result.stdout)
        self.assertLess(result.stdout.index("=== the unit tests"), result.stdout.index("=== shard"))
        self.assertIn("(a partial run, not the whole suite: the unit tests (--with-unit))", result.stdout)

    def test_jobs_does_not_combine_with_shard_or_list_and_wants_a_count(self) -> None:
        for args in (("--jobs", "2", "--shard", "1/2"), ("--jobs", "2", "--list"), ("--jobs",)):
            with self.subTest(args=args):
                result = self.run_jobs(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)
        for value in ("0", "x", "100", "-1"):
            with self.subTest(value=value):
                result = self.run_jobs("--jobs", value)
                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    f"--jobs wants how many shards to run side by side, 1 to 99, not '{value}'", result.stderr
                )


class ShardOptionTest(ScratchSuite):
    """`tests/smoke.sh --shard K/N` around the fake sections, with a weights file of the scratch checkout."""

    fake_sections = SHARD_SECTIONS

    def setUp(self) -> None:
        super().setUp()
        self.weights("syntax 1\nalpha 10\nbeta 30\ngamma 5\ndelta 5\nuninstall-files 1\n")

    def weights(self, text: str) -> None:
        self.write("tests/smoke_weights.txt", text)
        self.commit("weights")

    def own(self, result: subprocess.CompletedProcess[str]) -> tuple[list[str], list[str]]:
        """The sections the shard ran as its own, and the ones it ran as they were needed."""
        for line in result.stdout.splitlines():
            match = OWN.match(line)
            if match:
                return match["own"].split(), sorted((match["needs"] or "").split())
        return [], []

    def test_every_section_runs_in_exactly_one_shard_whatever_the_count(self) -> None:
        every = ["syntax", "alpha", "beta", "gamma", "delta"]
        for count in (1, 2, 3, 4):
            owned: list[str] = []
            for k in range(1, count + 1):
                with self.subTest(shard=f"{k}/{count}"):
                    result = self.run_suite("--shard", f"{k}/{count}")
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    own, _ = self.own(result)
                    owned += [s for s in own if s != "uninstall-files"]
                    if own:
                        self.assertIn(f"failed (shard {k} of {count}, a partial run", result.stdout)
                        self.assertIn("uninstall-files", own)
                    else:
                        self.assertIn(
                            f"smoke shard {k} of {count}: no section falls in this shard", result.stdout
                        )
            self.assertCountEqual(owned, every, f"--shard K/{count}")

    def test_a_section_goes_where_what_it_needs_runs_and_the_line_says_how_long(self) -> None:
        result = self.run_suite("--shard", "1/2")
        own, needs = self.own(result)
        self.assertEqual((own, needs), (["beta", "uninstall-files"], []))  # beta (30) as long as the rest
        result = self.run_suite("--shard", "2/2")
        own, needs = self.own(result)
        self.assertEqual((own, needs), (["syntax", "alpha", "gamma", "delta", "uninstall-files"], []))
        self.assertIn("about 22 s by smoke_weights.txt (the shards: 31 s, 22 s)", result.stdout)

    def test_with_only_the_split_is_of_the_named_sections_and_a_shard_runs_what_they_need(self) -> None:
        self.weights("alpha 50\ngamma 5\ndelta 5\n")
        first = self.run_suite("--only", "delta,beta", "--shard", "1/2")
        second = self.run_suite("--only", "delta,beta", "--shard", "2/2")
        self.assertEqual(self.own(first), (["delta"], ["alpha", "gamma"]))  # 60 s with what it needs
        self.assertEqual(self.own(second), (["beta"], []))
        self.assertIn("pass  alpha one", first.stdout)
        self.assertNotIn("the second", first.stdout)
        self.assertEqual(self.ran(first), ["alpha", "gamma"])

    def test_with_changed_the_split_is_of_what_the_change_selected(self) -> None:
        self.write("extra/deep/er/file.txt", "changed\n")  # beta and delta cover it
        runs = [self.run_suite("--changed", "HEAD", "--shard", f"{k}/3") for k in (1, 2, 3)]
        owned = sorted(s for r in runs for s in self.own(r)[0])
        self.assertEqual(owned, ["beta", "delta"])
        self.assertIn("smoke selection: 2 section(s)", runs[0].stdout)
        self.assertIn("no section falls in this shard, nothing to run", runs[2].stdout)
        self.assertEqual(runs[2].returncode, 0)

    def test_a_bad_k_of_n_is_a_usage_error(self) -> None:
        for value in ("0/2", "3/2", "2", "1/0", "x/2", "1/2/3"):
            with self.subTest(value=value):
                result = self.run_suite("--shard", value)
                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    f"--shard wants K/N with 1 <= K <= N (2/3: the second of three), not '{value}'",
                    result.stderr,
                )
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.run_suite("--shard").returncode, 2)

    def test_the_pre_push_hook_does_not_split_and_an_unknown_id_is_still_a_usage_error(self) -> None:
        result = self.run_suite("--pre-push", "--shard", "1/2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)
        result = self.run_suite("--only", "alpha,nope", "--shard", "1/2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no section 'nope'", result.stderr)

    def test_a_split_that_cannot_be_made_runs_every_selected_section_here(self) -> None:
        self.weights("alpha lots\n")
        result = self.run_suite("--only", "beta,delta", "--shard", "2/2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("weights file, line 1", result.stdout)
        self.assertIn("this shard runs every selected section", result.stdout)
        self.assertEqual(self.own(result), (["beta", "delta"], ["alpha", "gamma"]))
        self.assertIn("(shard 2 of 2, a partial run", result.stdout)

    def test_a_tool_that_cannot_run_runs_the_whole_suite_in_the_shard(self) -> None:
        (self.root / "tools").unlink()
        (self.root / "tools").mkdir()
        result = self.run_suite("--shard", "1/3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "tools/smoke_shard.py gave no answer, so this shard runs every selected section", result.stdout
        )
        self.assertEqual(self.ran(result), ["alpha", "beta", "gamma"])
        self.assertIn("passed, 0 failed (shard 1 of 3)\n", result.stdout)

    def test_without_a_weights_file_each_section_counts_the_same(self) -> None:
        (self.root / "tests" / "smoke_weights.txt").unlink()
        result = self.run_suite("--shard", "1/2")
        self.assertIn("by count (smoke_weights.txt is missing)", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_a_failure_in_a_shard_fails_it(self) -> None:
        text = self.script.read_text().replace('check "beta one" true', 'check "beta one" false')
        self.script.write_text(text)
        result = self.run_suite("--shard", "1/2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL  beta one", result.stdout)
        self.assertIn("2 passed, 1 failed (shard 1 of 2", result.stdout)

    def test_each_section_that_passed_records_how_long_it_took(self) -> None:
        text = self.script.read_text().replace('check "beta one" true', 'check "beta one" false')
        self.script.write_text(text)
        self.run_suite()
        durations = sh.parse_weights((self.cache / "durations").read_text())
        self.assertEqual(sorted(durations), ["alpha", "delta", "gamma", "syntax", "uninstall-files"])
        self.assertTrue(all(0 <= seconds < 60 for seconds in durations.values()))


if __name__ == "__main__":
    unittest.main()
