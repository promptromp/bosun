"""tests/unit/repo_guard.py: the check that a test run left its checkout alone trips on what a test could
change there, and not on a session in another worktree of the same repository. Fictional repositories."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from repo_guard import repo_state


class RepoGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.main, self.sibling = tmp / "main", tmp / "sibling"
        self.main.mkdir()
        self.git(self.main, "init", "-q", "-b", "main")
        self.commit(self.main, "first")
        self.git(self.main, "worktree", "add", "-q", "-b", "other", str(self.sibling))

    def git(self, folder: Path, *args: str) -> None:
        identity = ("-c", "user.name=acme", "-c", "user.email=dev@example.com", "-c", "commit.gpgsign=false")
        subprocess.run(["git", "-C", str(folder), *identity, *args], check=True, capture_output=True)

    def commit(self, folder: Path, name: str) -> None:
        (folder / name).write_text(name)
        self.git(folder, "add", name)
        self.git(folder, "commit", "-q", "-m", name)

    def test_the_state_is_the_same_when_nothing_changed(self) -> None:
        self.assertEqual(repo_state(self.sibling), repo_state(self.sibling))
        self.assertTrue(all(repo_state(self.sibling)))

    def test_a_commit_in_the_main_checkout_does_not_trip_a_sibling_worktrees_guard(self) -> None:
        before = repo_state(self.sibling)
        self.commit(self.main, "second")  # the shared repository's HEAD, the main checkout's, moves
        self.git(self.main, "branch", "another")
        self.assertEqual(repo_state(self.sibling), before)

    def test_a_commit_in_a_sibling_worktree_does_not_trip_the_main_checkouts_guard(self) -> None:
        before = repo_state(self.main)
        self.commit(self.sibling, "second")
        self.git(self.sibling, "switch", "-q", "-c", "third")
        self.assertEqual(repo_state(self.main), before)

    def test_a_commit_in_the_checkout_itself_trips_it(self) -> None:
        before = repo_state(self.sibling)
        self.commit(self.sibling, "second")
        self.assertNotEqual(repo_state(self.sibling), before)

    def test_a_branch_switch_in_the_checkout_itself_trips_it(self) -> None:
        before = repo_state(self.sibling)
        self.git(self.sibling, "switch", "-q", "--detach", "main")
        self.git(self.sibling, "commit", "-q", "--allow-empty", "-m", "empty")
        self.assertNotEqual(repo_state(self.sibling), before)

    def test_something_staged_in_the_checkout_itself_trips_it(self) -> None:
        before = repo_state(self.sibling)
        (self.sibling / "staged").write_text("x")
        self.git(self.sibling, "add", "staged")
        self.assertNotEqual(repo_state(self.sibling), before)

    def test_core_bare_changing_trips_it(self) -> None:
        before = repo_state(self.sibling)
        self.git(self.main, "config", "core.bare", "true")
        self.assertNotEqual(repo_state(self.sibling), before)


if __name__ == "__main__":
    unittest.main()
