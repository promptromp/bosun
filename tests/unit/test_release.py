"""Unit tests for tools/release.py: the dated release tag, its notes and the gate."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import release


TODAY = date(2026, 11, 12)


class NextTagTest(unittest.TestCase):
    def test_first_tag_of_the_day(self) -> None:
        self.assertEqual(release.next_tag([], TODAY), "v2026.11.12")
        self.assertEqual(release.next_tag(["v2026.11.05", "v2026.11.11.2"], TODAY), "v2026.11.12")

    def test_second_tag_the_same_day(self) -> None:
        self.assertEqual(release.next_tag(["v2026.11.12"], TODAY), "v2026.11.12.2")
        self.assertEqual(release.next_tag(["v2026.11.12", "v2026.11.12.2"], TODAY), "v2026.11.12.3")

    def test_other_tags_are_ignored(self) -> None:
        tags = ["app-v1.0.0", "v2026.11.12-rc1", "v2026.11.12.x", "latest", "v1"]
        self.assertEqual(release.next_tag(tags, TODAY), "v2026.11.12")

    def test_dot_zero_is_not_the_plain_tag(self) -> None:
        self.assertEqual(release.next_tag(["v2026.11.12.0"], TODAY), "v2026.11.12")
        self.assertEqual(release.next_tag(["v2026.11.12.1"], TODAY), "v2026.11.12")


class LatestTest(unittest.TestCase):
    def test_newest_by_date_then_number(self) -> None:
        tags = ["v2026.11.05", "v2026.11.12", "v2026.11.12.2", "v2026.11.12.10", "v2026.10.30.3"]
        self.assertEqual(release.latest(tags), "v2026.11.12.10")

    def test_stray_tags_are_ignored_and_none_is_none(self) -> None:
        self.assertIsNone(release.latest(["v1", "v2026.11.12-rc1", "app-v1.0.0"]))
        self.assertEqual(release.latest(["v1", "v2026.11.12-rc1", "v2026.11.01"]), "v2026.11.01")


def run(name: str, conclusion: str | None, run_id: int) -> dict[str, object]:
    return {"name": name, "conclusion": conclusion, "id": run_id}


REQUIRED = ["lint", "test (3.13)", "smoke"]
GREEN = [run("lint", "success", 1), run("test (3.13)", "success", 2), run("smoke", "success", 3)]
MISMATCH = "main's head is not the tree its pull request was checked on"


class GateTest(unittest.TestCase):
    def gate(
        self, runs: list[dict[str, object]], pr_tree: str | None = "t1", main_tree: str = "t1"
    ) -> str | None:
        return release.gate(REQUIRED, runs, pr_tree, main_tree)

    def test_all_green(self) -> None:
        self.assertIsNone(self.gate(GREEN))

    def test_one_failed(self) -> None:
        self.assertEqual(self.gate([*GREEN, run("smoke", "failure", 9)]), "smoke: failure")

    def test_failed_then_successful_rerun_passes(self) -> None:
        runs = [run("lint", "failure", 1), run("lint", "success", 7), *GREEN[1:]]
        self.assertIsNone(self.gate(runs))

    def test_success_then_newer_failure_is_refused(self) -> None:
        self.assertEqual(self.gate([*GREEN, run("lint", "failure", 9)]), "lint: failure")

    def test_missing(self) -> None:
        self.assertEqual(self.gate(GREEN[:2]), "smoke: missing")

    def test_skipped_and_neutral_pass(self) -> None:
        runs = [run("lint", "skipped", 1), run("test (3.13)", "neutral", 2), run("smoke", "success", 3)]
        self.assertIsNone(self.gate(runs))

    def test_unfinished_is_refused(self) -> None:
        self.assertEqual(self.gate([*GREEN, run("lint", None, 9)]), "lint: not finished")

    def test_no_pull_request(self) -> None:
        self.assertEqual(self.gate(GREEN, pr_tree=None), "main's head came from no pull request")

    def test_tree_mismatch(self) -> None:
        self.assertEqual(self.gate(GREEN, main_tree="t2"), MISMATCH)

    def test_merge_queue_commit_is_read_on_its_own_checks(self) -> None:
        """Checks that ran on main's head itself decide, whatever the pull request says."""
        self.assertIsNone(release.gate(REQUIRED, [], None, "t1", GREEN))
        self.assertIsNone(release.gate(REQUIRED, [run("lint", "failure", 1)], "t0", "t1", GREEN))

    def test_merge_queue_commit_with_a_failing_or_missing_check(self) -> None:
        bad = [*GREEN, run("smoke", "failure", 9)]
        self.assertEqual(release.gate(REQUIRED, GREEN, "t1", "t1", bad), "smoke: failure")
        self.assertEqual(release.gate(REQUIRED, GREEN, "t1", "t1", GREEN[:2]), "smoke: missing")

    def test_unrelated_runs_on_main_head_fall_back_to_the_pull_request(self) -> None:
        """A squash merged before the queue has only push-triggered runs (CodeQL, docs) on its head."""
        other = [run("Analyze (python)", "success", 7), run("deploy", "success", 8)]
        self.assertIsNone(release.gate(REQUIRED, GREEN, "t1", "t1", other))
        self.assertEqual(release.gate(REQUIRED, GREEN, "t1", "t2", other), MISMATCH)
        self.assertEqual(
            release.gate(REQUIRED, GREEN, None, "t1", other), "main's head came from no pull request"
        )


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.addCleanup(os.chdir, os.getcwd())

    def git(self, *args: str) -> None:
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        who = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", *who, *args], cwd=self.dir, check=True, capture_output=True, env=env)

    def call(self, *argv: str, today: date | None = None) -> tuple[int, str]:
        out = io.StringIO()
        os.chdir(self.dir)
        with contextlib.redirect_stdout(out):
            code = release.main(list(argv), today)
        return code, out.getvalue()

    def test_tags_and_notes_from_a_repository(self) -> None:
        self.git("init", "-q")
        self.git("commit", "-q", "--allow-empty", "-m", "First (#1)")
        self.git("tag", "-a", "v2026.11.05", "-m", "first")
        self.git("tag", "v1")
        self.git("commit", "-q", "--allow-empty", "-m", "Second (#2)")
        self.git("commit", "-q", "--allow-empty", "-m", "Third (#3)")
        self.assertEqual(self.call("latest"), (0, "v2026.11.05\n"))
        self.assertEqual(self.call("notes", "v2026.11.05..HEAD"), (0, "- Third (#3)\n- Second (#2)\n"))
        self.assertEqual(self.call("next-tag", today=TODAY), (0, "v2026.11.12\n"))
        self.assertEqual(self.call("next-tag", today=date(2026, 11, 5)), (0, "v2026.11.05.2\n"))

    def test_latest_prints_nothing_without_a_release(self) -> None:
        self.git("init", "-q")
        self.assertEqual(self.call("latest"), (0, "\n"))

    def test_gate_reads_the_files(self) -> None:
        (self.dir / "req").write_text("lint\ntest (3.13)\n")
        runs = [run("lint", "success", 1), run("test (3.13)", "failure", 2)]
        (self.dir / "runs").write_text("".join(json.dumps(r) + "\n" for r in runs))
        args = ["gate", "--required-file", "req", "--runs-file", "runs", "--pr-tree", "t", "--main-tree", "t"]
        self.assertEqual(self.call(*args), (1, "test (3.13): failure\n"))
        both = [run("lint", "success", 1), run("test (3.13)", "success", 2)]
        (self.dir / "runs").write_text(json.dumps(both))
        self.assertEqual(self.call(*args), (0, ""))
        self.assertEqual(self.call(*args[:-1], "x"), (1, MISMATCH + "\n"))
        self.assertEqual(self.call("gate")[0], 2)

    def test_gate_reads_the_checks_on_main_head_when_they_exist(self) -> None:
        (self.dir / "req").write_text("lint\ntest (3.13)\n")
        (self.dir / "runs").write_text("")
        head = [run("lint", "success", 1), run("test (3.13)", "success", 2)]
        (self.dir / "head").write_text("".join(json.dumps(r) + "\n" for r in head))
        args = ["gate", "--required-file", "req", "--runs-file", "runs", "--pr-tree", "", "--main-tree", "t"]
        self.assertEqual(self.call(*args, "--head-runs-file", "head"), (0, ""))
        self.assertEqual(self.call(*args), (1, "main's head came from no pull request\n"))
        (self.dir / "head").write_text(
            json.dumps([run("lint", "success", 1), run("test (3.13)", "failure", 2)])
        )
        self.assertEqual(self.call(*args, "--head-runs-file", "head"), (1, "test (3.13): failure\n"))


class NotesTest(unittest.TestCase):
    def test_notes_list_subjects_newest_first(self) -> None:
        subjects = ["Fix the thing (#12)", "Add the other thing (#11)"]
        self.assertEqual(release.notes(subjects), "- Fix the thing (#12)\n- Add the other thing (#11)\n")

    def test_no_subjects_no_notes(self) -> None:
        self.assertEqual(release.notes([]), "")
        self.assertEqual(release.notes(["", "  "]), "")


if __name__ == "__main__":
    unittest.main()
