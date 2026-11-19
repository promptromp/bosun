"""Which parts of the Mac VM suites a change reaches (tools/vm_select.py), how the suites run the parts chosen
(tests/sandbox/e2e_parts.sh) and how tests/sandbox/mac.sh takes the choice (`--only`, `--changed`, `--list`,
`--paid`).

The pure parts (the rules, the matching of files and functions, the plan, the choice, what it prints) are
called directly on made-up suites. The whole tool runs in a scratch git repository with its own suites, rules
and lib/common.sh. The shell helper runs in bash with made-up parts. mac.sh runs as a copy in that scratch
repository, with stand-ins for `tart` and `security` first on PATH that only record that they were called: no
clone is ever made, and the cases are the ones that end before any (a listing, nothing to run, a refusal).

The rule under test: a selection can only fail towards running more, never into live, which is paid."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import selection as sel  # noqa: E402
import vm_select as vs  # noqa: E402


def parts(text: str) -> list[sel.Marker]:
    return vs.parse_parts(textwrap.dedent(text))


PARTS: dict[str, list[sel.Marker]] = {
    "run": parts("""\
        part alpha "the first" covers 'get.sh,lib/common.sh#load_config'
        part beta "the second" needs alpha covers 'lib/b.py,lib/common.sh#render_*'
        part gamma "the last" needs alpha last covers 'lib/g.py'
        """),
    "work": parts("""\
        part install "install" covers 'get.sh'
        part afternoon-update "the update" needs install covers 'lib/u.py,lib/j.py'
        part ways-back "back" needs afternoon-update covers 'docs/back.md'
        """),
    "app": parts("""\
        part install "install" covers 'get.sh'
        part menu "the menu" needs install covers 'app/**'
        """),
    "live": parts("""\
        part install "install" covers 'get.sh'
        part windows "real windows" needs install paid covers 'lib/b.py'
        part off "off again" needs install last paid covers 'lib/off.py'
        """),
}
RULES = vs.parse_rules(
    textwrap.dedent("""\
    all run,work,app shared.sh   # a comment after a rule
    all * every.sh
    none docs/**
    none lib/common.sh#notify
    year lib/j.py
    minutes run 1.0
    minutes run/alpha 0.5
    minutes run/beta 0.25
    minutes work 1
    minutes work/install 1
    minutes work/afternoon-update 2
    minutes work/year 5
    """)
)


def choose(changed: list[str], functions: frozenset[str] | None = None) -> dict[str, vs.Choice]:
    plan = vs.plan_for(changed, PARTS, RULES, lambda _: functions)
    return {suite: vs.choose(suite, PARTS[suite], plan, RULES) for suite in vs.SUITES}


class RulesTest(unittest.TestCase):
    def test_the_rules_read_as_written(self) -> None:
        self.assertEqual(
            [(w.suites, w.written, w.glob) for w in RULES.whole],
            [(("run", "work", "app"), "run,work,app", "shared.sh"), (tuple(vs.SUITES), "*", "every.sh")],
        )
        self.assertEqual(RULES.none, ("docs/**", "lib/common.sh#notify"))
        self.assertEqual(RULES.year, ("lib/j.py",))
        self.assertEqual(RULES.minutes["work/year"], 5.0)

    def test_a_line_that_is_not_a_rule_says_which_and_why(self) -> None:
        for text, said in (
            ("ignore docs/**", "line 1"),
            ("all nosuch x.sh", "no suite nosuch"),
            ("none", "want all"),
            ("minutes run lots", "could not convert"),
        ):
            with self.subTest(text=text), self.assertRaises(sel.SelectionError) as caught:
                vs.parse_rules(text)
            self.assertIn(said, str(caught.exception))

    def test_a_hash_inside_a_word_is_the_word_s_and_after_a_blank_a_comment(self) -> None:
        self.assertEqual([line for _, _, line in sel.rule_lines("none a#b  # why\n# all\n")], ["none a#b"])

    def test_a_part_s_flags_and_a_section_without_any(self) -> None:
        live = {p.id: p for p in PARTS["live"]}
        self.assertEqual((live["windows"].flags, live["off"].flags), ({"paid"}, {"last", "paid"}))
        self.assertEqual(live["off"].needs, ("install",))
        line = "section syntax \"syntax\" covers 'lib/**'"
        self.assertEqual(sel.marker_lines(line, "section")[0][1].flags, frozenset())
        self.assertEqual(sel.marker_lines("part x \"x\" bogus covers 'a'", "part", vs.FLAGS), [])


class MatchTest(unittest.TestCase):
    def test_a_function_is_named_only_by_a_hash_entry_and_a_file_only_by_a_plain_one(self) -> None:
        for item, glob, expected in (
            ("lib/common.sh#render_job", "lib/common.sh#render_*", True),
            ("lib/common.sh#render_job", "lib/common.sh#job_*", False),
            ("lib/common.sh#render_job", "lib/*.sh#render_job", True),
            ("lib/common.sh#render_job", "lib/common.sh", False),
            ("lib/common.sh#render_job", "lib/**", False),
            ("lib/common.sh", "lib/common.sh#render_*", False),
            ("lib/b.py", "lib/*.py", True),
        ):
            with self.subTest(item=item, glob=glob):
                self.assertEqual(vs.item_matches(item, glob), expected)

    def test_the_files_read_by_function_are_those_a_hash_entry_names(self) -> None:
        self.assertEqual(vs.function_files(PARTS, RULES), {"lib/common.sh"})


class PlanTest(unittest.TestCase):
    def test_a_covered_file_picks_its_parts_and_what_they_need_in_file_order(self) -> None:
        got = choose(["lib/g.py"])
        self.assertEqual(got["run"].parts, ("alpha", "gamma"))
        self.assertFalse(got["run"].whole)
        self.assertEqual([got[s].parts for s in ("work", "app", "live")], [(), (), ()])

    def test_one_file_can_pick_parts_of_several_suites_and_live_s_are_paid(self) -> None:
        got = choose(["lib/b.py"])
        self.assertEqual(got["run"].parts, ("alpha", "beta"))
        self.assertEqual((got["live"].parts, got["live"].paid), (("install", "windows"), True))

    def test_an_all_rule_runs_its_suites_whole_and_the_covers_of_the_others_still_count(self) -> None:
        got = choose(["shared.sh", "lib/off.py"])
        self.assertEqual([got[s].whole for s in vs.SUITES], [True, True, True, False])
        self.assertEqual(got["run"].parts, ("alpha", "beta", "gamma"))
        self.assertEqual(got["live"].parts, ("install", "off"))

    def test_all_star_reaches_live_too(self) -> None:
        self.assertTrue(choose(["every.sh"])["live"].whole)

    def test_a_file_nothing_names_runs_run_work_and_app_whole_never_live(self) -> None:
        plan = vs.plan_for(["notes.txt"], PARTS, RULES, lambda _: None)
        self.assertEqual(plan.unknown, ["notes.txt"])
        got = {s: vs.choose(s, PARTS[s], plan, RULES) for s in vs.SUITES}
        self.assertEqual([got[s].whole for s in vs.SUITES], [True, True, True, False])
        self.assertIn("none <path>", "\n".join(vs.plan_lines(plan)))

    def test_a_none_file_runs_nothing_and_a_covered_one_beats_its_none_rule(self) -> None:
        self.assertEqual({c.parts for c in choose(["docs/guide.md"]).values()}, {()})
        self.assertEqual(choose(["docs/back.md"])["work"].parts, ("install", "afternoon-update", "ways-back"))

    def test_a_function_read_file_is_placed_function_by_function(self) -> None:
        got = choose(["lib/common.sh"], frozenset({"render_job", "notify"}))
        self.assertEqual(got["run"].parts, ("alpha", "beta"))
        plan = vs.plan_for(["lib/common.sh"], PARTS, RULES, lambda _: frozenset({"render_job", "notify"}))
        self.assertEqual(plan.ignored, ["lib/common.sh#notify"])
        self.assertEqual(plan.picked, {("run", "beta"): ["lib/common.sh#render_job"]})

    def test_a_function_nothing_names_and_a_change_outside_the_functions_fail_closed(self) -> None:
        self.assertTrue(choose(["lib/common.sh"], frozenset({"brand_new"}))["run"].whole)
        plan = vs.plan_for(["lib/common.sh"], PARTS, RULES, lambda _: None)
        self.assertIn("outside its functions", plan.unknown[0])
        self.assertTrue(vs.choose("work", PARTS["work"], plan, RULES).whole)

    def test_no_function_changed_is_nothing_to_run(self) -> None:
        plan = vs.plan_for(["lib/common.sh"], PARTS, RULES, lambda _: frozenset())
        self.assertEqual((plan.unknown, plan.picked), ([], {}))

    def test_a_whole_work_run_gets_a_year_also_when_it_fails_closed(self) -> None:
        self.assertEqual(choose(["shared.sh"])["work"].year, vs.YEAR)
        self.assertEqual(choose(["notes.txt"])["work"].year, vs.YEAR)
        self.assertEqual(vs.only("work", PARTS["work"], ["afternoon-update"], RULES).year, vs.SHORT_YEAR)

    def test_the_freeze_s_code_gives_a_year_and_the_estimate_counts_it(self) -> None:
        short = choose(["lib/u.py"])["work"]
        year = choose(["lib/j.py"])["work"]
        self.assertEqual((short.year, year.year), (vs.SHORT_YEAR, vs.YEAR))
        self.assertEqual((short.minutes, year.minutes), (4.0, 9.0))
        self.assertEqual(choose(["get.sh"])["work"].year, vs.SHORT_YEAR)
        self.assertEqual(choose(["lib/b.py"])["run"].year, 0, "only work has a year")

    def test_only_takes_the_parts_named_with_what_they_need_and_refuses_an_unknown_one(self) -> None:
        got = vs.only("work", PARTS["work"], ["ways-back"], RULES)
        self.assertEqual(
            (got.parts, got.year, got.whole), (("install", "afternoon-update", "ways-back"), 30, False)
        )
        self.assertTrue(
            vs.choice_line(vs.only("live", PARTS["live"], ["windows"], RULES)).endswith(" min, paid")
        )
        with self.assertRaises(sel.SelectionError) as caught:
            vs.only("work", PARTS["work"], ["nope"], RULES)
        self.assertIn("work has no part nope", str(caught.exception))


class SaysTest(unittest.TestCase):
    def test_the_answer_for_one_suite(self) -> None:
        self.assertEqual(vs.answer(choose(["lib/g.py"])["run"]), "=> only alpha,gamma")
        self.assertEqual(vs.answer(choose(["shared.sh"])["run"]), "=> all")
        self.assertEqual(vs.answer(choose(["docs/x.md"])["run"]), "=> none")
        self.assertEqual(vs.answer(choose(["lib/j.py"])["work"]), "=> only install,afternoon-update year=365")
        self.assertEqual(vs.answer(choose(["shared.sh"])["work"]), "=> all year=365")

    def test_the_answer_for_every_suite_marks_live_s_paid(self) -> None:
        got = choose(["lib/b.py"])
        self.assertEqual(
            vs.summary([got[s] for s in vs.SUITES]),
            "=> run:alpha,beta work:none app:none live(paid):install,windows",
        )

    def test_the_lines_of_the_report(self) -> None:
        plan = vs.plan_for(["lib/j.py", "docs/x.md", "shared.sh"], PARTS, RULES, lambda _: None)
        choices = [vs.choose(s, PARTS[s], plan, RULES) for s in vs.SUITES]
        lines = vs.report(["lib/j.py", "docs/x.md", "shared.sh"], plan, choices)
        self.assertEqual(lines[0], "vm selection: 3 changed file(s)")
        self.assertIn("  run whole: shared.sh (all run,work,app shared.sh)", lines)
        self.assertIn("  work/afternoon-update: lib/j.py", lines)
        self.assertIn("  (no part needs it): docs/x.md", lines)
        self.assertIn("work: whole (3 parts, year 365: lib/j.py)   ~9.0 min", lines)
        self.assertIn("live: nothing to run", lines)
        self.assertEqual(lines[-1], "=> run:all work:all app:all live:none")

    def test_a_selection_that_cannot_be_made_runs_run_work_and_app_whole_and_live_nothing(self) -> None:
        self.assertEqual(
            vs.failed(OSError("broken"), list(vs.SUITES))[-1], "=> run:all work:all app:all live:none"
        )
        self.assertEqual(vs.failed(OSError("broken"), ["live"])[-1], "=> none")
        self.assertEqual(vs.failed(OSError("broken"), ["work"])[-1], "=> all")


# ---- the whole tool, git and all, in a scratch repository --------------------------------------------------

SUITE_FILES = {
    "tests/install_e2e.sh": """\
        part alpha "the first" covers 'get.sh,lib/common.sh#load_config'
        part beta "the second" needs alpha covers 'lib/b.py,lib/common.sh#render_*'
        part gamma "the last" needs alpha last covers 'lib/g.py'
        parts_end
        """,
    "tests/work_e2e.sh": """\
        part install "install" covers 'get.sh'
        part afternoon-update "the update" needs install covers 'lib/u.py,lib/j.py'
        parts_end
        """,
    "tests/app_e2e.sh": """\
        part install "install" covers 'get.sh'
        parts_end
        """,
    "tests/sandbox/live.sh": """\
        part install "install" covers 'get.sh'
        part windows "real windows" needs install paid covers 'lib/w.py'
        parts_end
        """,
}
SCRATCH_RULES = """\
all run,work,app shared.sh
none .gitignore
none docs/**
none tests/vm_selection.txt
none tests/sandbox/mac.sh
none tools/**
year lib/j.py
"""
COMMON = "load_config() {\n  :\n}\n\nrender_job() {\n  echo job\n}\n"
SCRATCH_FILES = {
    **{path: textwrap.dedent(text) for path, text in SUITE_FILES.items()},
    "tests/vm_selection.txt": SCRATCH_RULES,
    "lib/common.sh": COMMON,
    "lib/b.py": "b = 1\n",
    "lib/w.py": "w = 1\n",
    "get.sh": "#!/bin/sh\n",
    "docs/guide.md": "# guide\n",
    ".gitignore": "__pycache__/\n",
}


def git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return done.stdout.strip()


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve() / "repo"
        for path, text in SCRATCH_FILES.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        git(self.repo, "tag", "base")

    def write(self, path: str, text: str) -> None:
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(text)

    def select(self, *suites: str, base: str = "base") -> list[str]:
        return vs.select_changed(self.repo, self.repo / vs.RULES, base, list(suites or vs.SUITES))


class SelectChangedTest(Scratch):
    def test_nothing_changed_runs_nothing(self) -> None:
        self.assertEqual(self.select()[-1], "=> run:none work:none app:none live:none")

    def test_a_committed_and_an_untracked_change_both_count(self) -> None:
        self.write("lib/b.py", "b = 2\n")
        git(self.repo, "commit", "-qam", "b")
        self.write("lib/w.py", "w = 2\n")
        self.assertEqual(self.select("run")[-1], "=> only alpha,beta")
        self.assertEqual(self.select("live")[-1], "=> only install,windows")

    def test_a_function_of_lib_common_sh_and_one_added_with_its_comment(self) -> None:
        self.write(
            "lib/common.sh",
            COMMON.replace("echo job", "echo the job") + "\n# a helper\nrender_more() {\n  :\n}\n",
        )
        lines = self.select("run")
        self.assertIn("  run/beta: lib/common.sh#render_job, lib/common.sh#render_more", lines)
        self.assertEqual(lines[-1], "=> only alpha,beta")

    def test_a_git_that_colours_its_diffs_still_reads_by_function(self) -> None:
        """With color.ui=always git colours even a diff into a pipe; read as no hunk, an edit was none."""
        git(self.repo, "config", "color.ui", "always")
        git(self.repo, "config", "color.diff", "always")
        self.write("lib/common.sh", COMMON.replace("echo job", "echo the job"))
        self.assertEqual(self.select("run")[-1], "=> only alpha,beta")

    def test_a_diff_that_reads_as_no_hunk_for_a_file_that_changed_is_an_error(self) -> None:
        """The run fails closed (a SelectionError) rather than read an edit it cannot place as none."""
        self.write("lib/common.sh", COMMON.replace("echo job", "echo the job"))

        def no_hunks(root: Path, *args: str) -> str:
            return "" if args[0] == "diff" else real_git(root, *args)

        real_git = sel.git
        with mock.patch.object(sel, "git", no_hunks), self.assertRaisesRegex(sel.SelectionError, "no hunk"):
            sel.file_change(self.repo, "base", "lib/common.sh")
        with mock.patch.object(sel, "git", no_hunks):
            self.assertEqual(
                sel.file_change(self.repo, "base", "lib/b.py").hunks, ()
            )  # unchanged: none to read

    def test_the_docker_sandbox_on_its_files_and_what_it_runs(self) -> None:
        rules = self.repo / vs.RULES
        rules.write_text(SCRATCH_RULES + "docker tests/sandbox/**\ndocker lib/c*.py\n")
        self.assertEqual(vs.select_docker(self.repo, rules, "base")[-1], "=> none")
        self.write("lib/b.py", "b = 4\n")
        self.assertEqual(vs.select_docker(self.repo, rules, "base")[-1], "=> none")
        self.write("tests/sandbox/fresh_user.sh", "echo\n")
        lines = vs.select_docker(self.repo, rules, "base")
        self.assertEqual(lines, ["  docker: tests/sandbox/fresh_user.sh", "=> docker"])
        self.assertEqual(vs.select_docker(self.repo, rules, "no-such-ref")[-1], "=> docker")
        done = run_tool("--root", str(self.repo), "--rules", str(rules), "--docker", "--changed", "base")
        self.assertEqual(done.stdout.splitlines()[-1], "=> docker")
        self.assertEqual(run_tool("--root", str(self.repo), "--docker", "--only", "x").returncode, 2)

    def test_code_added_outside_the_functions_runs_run_whole(self) -> None:
        self.write("lib/common.sh", COMMON + "\nLIMIT=4\n")
        self.assertEqual(self.select("run")[-1], "=> all")
        self.assertEqual(self.select("live")[-1], "=> none")

    def test_the_year_for_the_freeze_s_code(self) -> None:
        self.write("lib/j.py", "j = 1\n")
        self.assertEqual(self.select("work")[-1], "=> only install,afternoon-update year=365")

    def test_a_ref_that_does_not_resolve_runs_run_work_and_app_whole(self) -> None:
        lines = self.select(base="no-such-ref")
        self.assertIn("run, work and app run whole", lines[0])
        self.assertEqual(lines[-1], "=> run:all work:all app:all live:none")

    def test_the_command_line(self) -> None:
        self.write("docs/guide.md", "# more\n")
        done = run_tool("--root", str(self.repo), "--suite", "run", "--changed", "base")
        self.assertEqual((done.returncode, done.stdout.splitlines()[-1]), (0, "=> none"))
        done = run_tool("--root", str(self.repo), "--suite", "run", "--only", "gamma")
        self.assertEqual(done.stdout.splitlines()[-1], "=> only alpha,gamma")
        done = run_tool("--root", str(self.repo), "--suite", "run", "--only", "nope")
        self.assertEqual(done.returncode, 2)
        self.assertIn("run has no part nope", done.stderr)


def run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "vm_select.py"), *args],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


# ---- tests/sandbox/e2e_parts.sh ----------------------------------------------------------------------------

PARTS_SCRIPT = """\
passed=0
failed=0
OUT="$(mktemp -d)"
source "$1"
part_one() { echo ran one; passed=$((passed + 1)); }
part_two_words() { echo ran two-words; failed=$((failed + 1)); }
part_three() { echo ran three; passed=$((passed + 1)); }
part one "the first" covers 'a'
part two-words "the second" needs one covers 'b'
part three "the last" needs one last covers 'c'
part_named three && echo three is named
parts_end
"""


def run_parts(**env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", "-c", PARTS_SCRIPT, "_", str(ROOT / "tests" / "sandbox" / "e2e_parts.sh")],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"], **env},
        stdin=subprocess.DEVNULL,
    )


class PartsTest(unittest.TestCase):
    def test_no_parts_named_runs_every_part_and_says_nothing_of_a_partial_run(self) -> None:
        done = run_parts()
        self.assertIn(
            "--- part one: the first\nran one\n--- part two-words: the second\nran two-words\n", done.stdout
        )
        self.assertIn("ran three", done.stdout)
        self.assertNotIn("partial run", done.stdout)
        self.assertNotIn("is named", done.stdout)
        self.assertEqual(done.stdout.splitlines()[-1], "2 passed, 1 failed")
        self.assertEqual(done.returncode, 1)

    def test_the_parts_named_run_alone_and_the_summary_says_so(self) -> None:
        done = run_parts(E2E_PARTS="one,three")
        self.assertNotIn("ran two-words", done.stdout)
        self.assertIn("three is named", done.stdout)
        self.assertIn("partial run, not the whole suite: one three", done.stdout)
        self.assertEqual((done.stdout.splitlines()[-1], done.returncode), ("2 passed, 0 failed", 0))

    def test_a_part_named_that_the_suite_does_not_have_fails_the_run(self) -> None:
        done = run_parts(E2E_PARTS="one,nosuch")
        self.assertIn("FAIL  E2E_PARTS names nosuch, which is no part of this suite", done.stdout)
        self.assertEqual((done.stdout.splitlines()[-1], done.returncode), ("1 passed, 1 failed", 1))

    def test_exitfirst_starts_no_part_after_a_failure_and_names_what_it_left_out(self) -> None:
        done = run_parts(E2E_EXITFIRST="1")
        self.assertIn("ran two-words", done.stdout)
        self.assertNotIn("ran three", done.stdout)
        self.assertIn("stopped after the first failure (-x), so these did not run: three", done.stdout)


# ---- tests/sandbox/mac.sh, as a copy in the scratch repository ---------------------------------------------

STAND_IN = '#!/bin/sh\necho "$(basename "$0") $*" >>"$CALLS"\nexit 1\n'


class MacTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        for path in (
            "tests/sandbox/mac.sh",
            "tools/vm_select.py",
            "tools/selection.py",
            "tools/shell_library.py",
        ):
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, self.repo / path)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "the tools")
        git(self.repo, "tag", "-f", "base")
        self.bin = self.repo.parent / "bin"
        self.bin.mkdir()
        for name in ("tart", "security"):
            (self.bin / name).write_text(STAND_IN)
            (self.bin / name).chmod(0o755)
        self.calls = self.repo.parent / "calls"

    def mac(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BOSUN_"))}
        env.update(PATH=f"{self.bin}:{env['PATH']}", CALLS=str(self.calls), TMPDIR=str(self.repo.parent))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            ["/bin/bash", str(self.repo / "tests" / "sandbox" / "mac.sh"), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
        )

    def made_no_clone(self) -> None:
        calls = self.calls.read_text().splitlines() if self.calls.exists() else []
        self.assertEqual([c for c in calls if not c.startswith("tart delete bosun-mac-run-")], [])

    def test_only_with_list_prints_the_parts_with_what_they_need_and_runs_nothing(self) -> None:
        done = self.mac("run", "--only", "gamma", "--list")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("vm selection: --only gamma (and, as they need them: alpha)", done.stdout)
        self.assertNotIn("=> only", done.stdout, "the answer line is the tool's, not the person's")
        self.made_no_clone()

    def test_changed_with_list_prints_the_choice_and_runs_nothing(self) -> None:
        self.write("lib/b.py", "b = 3\n")
        done = self.mac("run", "--changed", "base", "--list")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("  run/beta: lib/b.py", done.stdout)
        self.assertIn("run:  alpha beta", done.stdout)
        self.made_no_clone()

    def test_a_change_that_reaches_no_part_says_so_and_runs_nothing(self) -> None:
        self.write("lib/b.py", "b = 3\n")
        done = self.mac("work", "--changed", "base")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("the change reaches no part of work: nothing to run", done.stdout)
        self.made_no_clone()

    def test_live_s_parts_need_paid_and_no_key_is_read_without_it(self) -> None:
        self.write("lib/w.py", "w = 3\n")
        done = self.mac("live", "claude", "--changed", "base")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("live: install windows", done.stdout)
        self.assertIn("add --paid to run those parts", done.stdout)
        self.made_no_clone()

    def test_changed_alone_prints_every_suite(self) -> None:
        self.write("shared.sh", "echo\n")
        done = self.mac("--changed", "base", "--list")
        self.assertEqual(done.stdout.splitlines()[-1], "=> run:all work:all app:all live:none")
        self.made_no_clone()

    def test_what_it_refuses(self) -> None:
        for args, said in (
            (("run", "--only", "nope"), "run has no part nope"),
            (("run", "--only", "alpha", "--changed", "base"), "do not go together"),
            (("run", "--list"), "--list goes with"),
            (("live", "agy", "--only", "install"), "live agy runs whole"),
            (("run", "--only"), "--only takes a value"),
            (("run", "--bogus"), "usage: tests/sandbox.sh mac run"),
        ):
            with self.subTest(args=args):
                done = self.mac(*args)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn(said, done.stderr)
        self.made_no_clone()


if __name__ == "__main__":
    unittest.main()
