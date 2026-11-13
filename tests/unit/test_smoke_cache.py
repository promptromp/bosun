"""What the smoke suite keeps between runs (tests/smoke_cache.sh, tests/.smoke-cache/): each section's last
result for `--last-failed`, and the tree a whole run passed for `--pre-push`. Checked here without running
the suite: the shell functions are sourced and called on their own, in scratch folders and a scratch git
repository, and the options of tests/smoke.sh that reach no section (a usage error, nothing to run, a skip)
are run for real.

The rule under test is that the cache can only make the suite run: an unreadable or odd record, another tree,
a record a day old, a failed or partial or changed run all mean the whole suite runs."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "tests" / "smoke.sh"
LIB = ROOT / "tests" / "smoke_cache.sh"
NOW = 1_800_000_000
TREE = "a" * 40
OTHER = "b" * 40


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PRE_COMMIT_", "BOSUN_SMOKE_"))}
    env.update(extra)
    return env


def sh(script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", "-c", f'source "{LIB}"\n{script}', "_", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else clean_env(),
        stdin=subprocess.DEVNULL,
    )


def smoke(*args: str, cache: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(SMOKE), *args],
        capture_output=True,
        text=True,
        check=False,
        env=clean_env(SMOKE_CACHE_DIR=str(cache), **env),
        stdin=subprocess.DEVNULL,
    )


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=clean_env(),
    )
    return result.stdout.strip()


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name).resolve()

    def lines(self, name: str) -> list[str]:
        path = self.dir / name
        return path.read_text().splitlines() if path.exists() else []


class SectionResultsTest(Scratch):
    def update(self, key: str, status: str) -> None:
        result = sh('cache_update "$1" "$2" "$2 $3"', str(self.dir / "sections"), key, status)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_update_replaces_only_its_own_line(self) -> None:
        """A partial run changes the sections it ran, and the others keep their earlier result."""
        for key, status in (("ledger", "pass"), ("console", "fail"), ("quit", "fail")):
            self.update(key, status)
        self.update("quit", "pass")  # the partial run that reran one section
        self.update("syntax", "pass")  # and one that was not there before
        self.assertEqual(
            sorted(self.lines("sections")),
            ["console fail", "ledger pass", "quit pass", "syntax pass"],
        )

    def test_the_file_is_kept_to_its_last_two_hundred_lines(self) -> None:
        for i in range(210):
            self.update(f"s{i}", "pass")
        lines = self.lines("sections")
        self.assertEqual(len(lines), 200)
        self.assertEqual(lines[-1], "s209 pass")

    def test_keeping_it_never_fails(self) -> None:
        """Where nothing can be written, the call still succeeds, and leaves nothing behind."""
        blocker = self.dir / "file"
        blocker.write_text("not a folder")
        result = sh('cache_update "$1/sub/sections" a "a pass"', str(blocker))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["file"])


class RecordedPassesTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.passes = self.dir / "passes"

    def record(self, start: str, end: str, failed: int = 0, passed: int = 10, now: int = NOW) -> int:
        script = 'record_full_run "$1" "$2" "$3" "$4" "$5" "$6"'
        return sh(script, str(self.passes), start, end, str(failed), str(passed), str(now)).returncode

    def recently(self, tree: str, now: int = NOW) -> bool:
        return sh('passed_recently "$1" "$2" "$3"', str(self.passes), tree, str(now)).returncode == 0

    def skips(self, ref: str, now: int = NOW, **env: str) -> subprocess.CompletedProcess[str]:
        return sh(
            'pre_push_skip "$1" "$2" "$3" "$4"',
            str(ROOT),
            str(self.passes),
            ref,
            str(now),
            env=clean_env(**env),
        )

    def test_the_same_tree_within_a_day_is_recorded_as_passing(self) -> None:
        self.assertEqual(self.record(TREE, TREE, passed=1492), 0)
        self.assertEqual(self.lines("passes"), [f"{TREE} 1492 {NOW}"])
        self.assertTrue(self.recently(TREE, NOW + 3600))
        self.assertTrue(self.recently(TREE, NOW + 86399))

    def test_the_time_it_passed_is_what_is_printed(self) -> None:
        self.record(TREE, TREE)
        result = sh('passed_recently "$1" "$2" "$3"', str(self.passes), TREE, str(NOW + 5))
        self.assertEqual(result.stdout.strip(), str(NOW))

    def test_another_tree_runs_the_suite(self) -> None:
        self.record(TREE, TREE)
        self.assertFalse(self.recently(OTHER))

    def test_a_record_a_day_old_runs_the_suite(self) -> None:
        self.record(TREE, TREE)
        self.assertFalse(self.recently(TREE, NOW + 86400))
        self.assertFalse(self.recently(TREE, NOW + 86400 * 30))

    def test_a_record_from_the_future_runs_the_suite(self) -> None:
        self.record(TREE, TREE, now=NOW + 7200)
        self.assertFalse(self.recently(TREE, NOW))

    def test_a_run_with_a_failure_records_nothing_and_forgets_the_earlier_pass(self) -> None:
        self.assertEqual(self.record(TREE, TREE), 0)
        self.assertEqual(self.record(TREE, TREE, failed=1), 1)
        self.assertFalse(self.recently(TREE))
        self.assertEqual(self.record(OTHER, OTHER, failed=3), 1)
        self.assertEqual(self.lines("passes"), [])

    def test_a_run_that_changed_the_tree_or_does_not_know_it_records_nothing(self) -> None:
        self.assertEqual(self.record(TREE, OTHER), 2)
        self.assertEqual(self.record(TREE, ""), 2)
        self.assertEqual(self.record("", ""), 2)
        self.assertEqual(self.record("not-a-tree", "not-a-tree"), 2)
        self.assertEqual(self.lines("passes"), [])

    def test_a_partial_run_leaves_no_record(self) -> None:
        """Only the whole run records a pass: a partial one never reaches the passes file."""
        cache = self.dir / "cache"
        result = smoke("--only", "resolve", cache=cache)
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
        self.assertEqual((cache / "sections").read_text().split(), ["resolve", "pass"])
        self.assertFalse((cache / "passes").exists())

    def test_an_unreadable_or_odd_record_runs_the_suite(self) -> None:
        for content in (
            "",
            "garbage\n",
            f"{TREE}\n",
            f"{TREE} 10\n",
            f"{TREE} ten {NOW}\n",
            f"{TREE} 10 soon\n",
            f"{TREE} 0 {NOW}\n",  # a pass of no checks is no pass
            f"{TREE} 10 {NOW} extra\n",
            f"{TREE} 10 -5\n",
            f"{TREE} 99999999999999999999 {NOW}\n",
            f"{TREE} 10 {'9' * 40}\n",
        ):
            with self.subTest(content=content):
                self.passes.write_text(content)
                self.assertFalse(self.recently(TREE))

    def test_a_cache_that_is_missing_or_not_a_file_runs_the_suite(self) -> None:
        self.assertFalse(self.recently(TREE))  # missing
        self.passes.mkdir()  # a folder in its place
        self.assertFalse(self.recently(TREE))

    @unittest.skipIf(os.geteuid() == 0, "root reads what it likes")
    def test_a_cache_that_cannot_be_read_runs_the_suite(self) -> None:
        self.record(TREE, TREE)
        self.passes.chmod(0)
        self.addCleanup(self.passes.chmod, 0o600)
        self.assertFalse(self.recently(TREE))

    def test_a_tree_that_is_not_a_hash_is_never_found(self) -> None:
        self.passes.write_text(f" {NOW}\n")
        for tree in ("", " ", "HEAD", "abc"):
            with self.subTest(tree=tree):
                self.assertFalse(self.recently(tree))


class PrePushTest(RecordedPassesTest):
    def head_tree(self) -> str:
        return git(ROOT, "rev-parse", "HEAD^{tree}")

    def test_a_tree_that_passed_a_whole_run_is_skipped_with_one_line(self) -> None:
        self.record(self.head_tree(), self.head_tree())
        result = self.skips("HEAD", now=NOW + 60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(
            result.stdout, r"^smoke already passed on this exact tree at \S+ \d\d:\d\d: skipping\n$"
        )

    def test_a_commit_with_another_tree_runs_the_suite(self) -> None:
        self.record(self.head_tree(), self.head_tree())
        self.assertEqual(self.skips("HEAD~1", now=NOW + 60).returncode, 1)

    def test_a_stale_record_runs_the_suite(self) -> None:
        self.record(self.head_tree(), self.head_tree())
        self.assertEqual(self.skips("HEAD", now=NOW + 86400).returncode, 1)

    def test_the_escape_hatch_runs_the_suite_even_with_a_record(self) -> None:
        self.record(self.head_tree(), self.head_tree())
        result = self.skips("HEAD", now=NOW + 60, BOSUN_SMOKE_NO_CACHE="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("BOSUN_SMOKE_NO_CACHE=1", result.stdout)

    def test_no_ref_or_one_git_cannot_find_runs_the_suite(self) -> None:
        self.record(self.head_tree(), self.head_tree())
        for ref in ("", "no-such-ref", "1234567"):
            with self.subTest(ref=ref):
                self.assertEqual(self.skips(ref, now=NOW + 60).returncode, 1)

    def test_a_branch_deletion_runs_nothing(self) -> None:
        for ref in ("0" * 40, "0" * 64):
            with self.subTest(ref=ref):
                result = self.skips(ref)
                self.assertEqual(result.returncode, 3)
                self.assertIn("deletion", result.stdout)
        # (even with the escape hatch: there is nothing to test)
        self.assertEqual(self.skips("0" * 40, BOSUN_SMOKE_NO_CACHE="1").returncode, 3)

    def test_the_script_skips_and_runs_nothing_for_a_pushed_tree_that_passed(self) -> None:
        """tests/smoke.sh --pre-push as the hook runs it: one line, exit 0, no section run."""
        cache = self.dir / "cache"
        cache.mkdir()
        (cache / "passes").write_text(f"{self.head_tree()} 1492 {int(time.time()) - 60}\n")
        result = smoke("--pre-push", cache=cache, PRE_COMMIT_TO_REF="HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"^smoke already passed on this exact tree at .*: skipping\n$")
        self.assertEqual(sorted(p.name for p in cache.iterdir()), ["passes"])

    def test_the_script_does_nothing_for_a_deletion(self) -> None:
        cache = self.dir / "cache"
        result = smoke("--pre-push", cache=cache, PRE_COMMIT_TO_REF="0" * 40)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "a branch deletion: nothing to test")
        self.assertFalse(cache.exists())


class TreeOfCheckoutTest(Scratch):
    """The tree a run is recorded under is the files on disk, never the real index, and cannot be thrown
    off by what is ignored."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / ".gitignore").write_text("cache/\n__pycache__/\n")
        (self.repo / "tracked.txt").write_text("one\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "first")
        self.passes = self.dir / "passes"

    def tree(self) -> str:
        result = sh('tree_of_checkout "$1"', str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def status(self) -> str:
        return sh('status_of_checkout "$1"', str(self.repo)).stdout

    def commit_tree(self) -> str:
        return git(self.repo, "rev-parse", "HEAD^{tree}")

    def end_of_run(self, start: str, end: str, *, failed: int = 0) -> int:
        script = 'record_full_run "$1" "$2" "$3" "$4" 7 "$5"'
        return sh(script, str(self.passes), start, end, str(failed), str(NOW)).returncode

    def test_a_clean_checkout_is_its_commit(self) -> None:
        self.assertEqual(self.tree(), self.commit_tree())

    def test_uncommitted_changes_are_in_the_tree(self) -> None:
        (self.repo / "tracked.txt").write_text("one!\n")
        self.assertNotEqual(self.tree(), self.commit_tree())

    def test_the_real_index_is_not_touched(self) -> None:
        (self.repo / "new.txt").write_text("new\n")
        before = (self.repo / ".git" / "index").read_bytes()
        self.tree()
        self.assertEqual((self.repo / ".git" / "index").read_bytes(), before)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "?? new.txt")
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only"), "")

    def test_an_untracked_file_at_the_start_means_no_match_with_a_commit_that_lacks_it(self) -> None:
        """It could have influenced the run, and the pushed commit does not have it: the suite runs."""
        (self.repo / "scratch.txt").write_text("was there\n")
        start = self.tree()
        self.assertEqual(self.end_of_run(start, self.tree()), 0)  # recorded under the tree it ran with
        committed = self.commit_tree()
        self.assertNotEqual(start, committed)
        found = sh('passed_recently "$1" "$2" "$3"', str(self.passes), committed, str(NOW + 1))
        self.assertNotEqual(found.returncode, 0)

    def test_a_file_created_mid_run_records_no_pass_and_is_named(self) -> None:
        start, before = self.tree(), self.status()
        (self.repo / "left-behind.txt").write_text("a test wrote this\n")
        (self.repo / "sub").mkdir()
        (self.repo / "sub" / "also.txt").write_text("and this\n")
        self.assertEqual(self.end_of_run(start, self.tree()), 2)
        self.assertFalse(self.passes.exists())
        named = sh('changed_paths "$1" "$2"', before, self.status()).stdout.strip()
        self.assertEqual(sorted(named.split()), ["left-behind.txt", "sub/"])

    def test_a_tracked_file_changed_mid_run_records_no_pass_and_is_named(self) -> None:
        start, before = self.tree(), self.status()
        (self.repo / "tracked.txt").write_text("changed by a test\n")
        self.assertEqual(self.end_of_run(start, self.tree()), 2)
        self.assertEqual(sh('changed_paths "$1" "$2"', before, self.status()).stdout.strip(), "tracked.txt")

    def test_an_ignored_artifact_created_mid_run_still_records_the_pass(self) -> None:
        start = self.tree()
        (self.repo / "cache").mkdir()
        (self.repo / "cache" / "sections").write_text("ledger pass\n")
        (self.repo / "__pycache__").mkdir()
        (self.repo / "__pycache__" / "x.pyc").write_bytes(b"\0")
        end = self.tree()
        self.assertEqual(start, end)
        self.assertEqual(self.end_of_run(start, end), 0)
        self.assertEqual(self.passes.read_text(), f"{start} 7 {NOW}\n")

    def test_a_file_that_was_there_at_the_start_and_is_unchanged_is_not_a_change(self) -> None:
        (self.repo / "scratch.txt").write_text("x\n")
        before = self.status()
        self.assertEqual(sh('changed_paths "$1" "$2"', before, self.status()).stdout.strip(), "")

    def test_git_that_cannot_say_means_no_tree(self) -> None:
        result = sh('tree_of_checkout "$1"', str(self.dir / "not-a-repo"))
        self.assertEqual((result.returncode, result.stdout), (1, ""))


class OptionsTest(Scratch):
    def test_last_failed_with_nothing_recorded_says_so_in_one_line_and_runs_nothing(self) -> None:
        cache = self.dir / "cache"
        for flag in ("--last-failed", "--lf"):
            with self.subTest(flag=flag):
                result = smoke(flag, cache=cache)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(result.stdout.splitlines()), 1)
                self.assertIn("no section is recorded as failed", result.stdout)
        self.assertFalse(cache.exists())

    def test_last_failed_ignores_what_is_no_section_now_and_what_passed(self) -> None:
        cache = self.dir / "cache"
        cache.mkdir()
        (cache / "sections").write_text("ledger pass\nrenamed-away fail\n")
        result = smoke("--last-failed", cache=cache)
        self.assertEqual(result.returncode, 0)
        self.assertIn("no section is recorded as failed", result.stdout)

    def test_last_failed_reruns_the_failed_section_and_records_its_new_result(self) -> None:
        cache = self.dir / "cache"
        cache.mkdir()
        (cache / "sections").write_text("resolve pass\ntemplates fail\n")
        result = smoke("--last-failed", "-x", cache=cache)
        self.assertEqual(result.returncode, 0, result.stdout[-500:] + result.stderr)
        self.assertIn("--last-failed: rerunning the sections that failed: templates", result.stdout)
        self.assertIn("a partial run, not the whole suite: templates", result.stdout)
        self.assertNotIn("\nresolve\n", result.stdout)
        self.assertEqual(
            sorted((cache / "sections").read_text().splitlines()), ["resolve pass", "templates pass"]
        )
        self.assertFalse((cache / "passes").exists())

    def test_the_options_that_choose_sections_do_not_combine(self) -> None:
        cache = self.dir / "cache"
        for args in (
            ["--last-failed", "--only", "resolve"],
            ["--only", "resolve", "--lf"],
            ["--pre-push", "--only", "resolve"],
            ["--pre-push", "--last-failed"],
        ):
            with self.subTest(args=args):
                result = smoke(*args, cache=cache)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)

    def test_the_new_options_are_in_the_usage_and_the_unknown_ones_still_fail(self) -> None:
        result = smoke("--bogus", cache=self.dir / "cache")
        self.assertEqual(result.returncode, 2)
        for option in ("-x", "--last-failed", "--pre-push"):
            self.assertIn(option, result.stderr)

    def test_exitfirst_is_accepted_in_both_spellings_with_list(self) -> None:
        for flag in ("-x", "--exitfirst"):
            with self.subTest(flag=flag):
                result = smoke(flag, "--list", cache=self.dir / "cache")
                self.assertEqual(result.returncode, 0)
                self.assertIn("syntax", result.stdout)


if __name__ == "__main__":
    unittest.main()
