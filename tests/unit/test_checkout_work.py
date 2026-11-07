"""lib/checkout_work.py: what deleting a checkout of bosun, or a version of it, would lose (bosun uninstall,
and bosun update's conversion and prune). Fictional data, real git in a scratch folder."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import checkout_work as cw
from checkout_work import Checkout


IDENTITY = ("-c", "user.name=acme", "-c", "user.email=dev@example.com", "-c", "commit.gpgsign=false")


def git(*args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(["git", *IDENTITY, *args], env=env, check=True, capture_output=True, text=True)
    return done.stdout.strip()


class World(unittest.TestCase):
    """An upstream with two releases, a checkout of it, and versions side by side: bosun.git (a bare clone
    of the branch, marked), and a folder per release."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.origin = self.tmp / "origin.git"
        source = self.tmp / "source"
        git("init", "-q", "--bare", str(self.origin))
        git("init", "-q", "-b", "main", str(source))
        self.commits = []
        for number in range(2):
            (source / "bosun").write_text(f"release {number}\n")
            git("-C", str(source), "add", ".")
            git("-C", str(source), "commit", "-qm", f"release {number}")
            self.commits.append(git("-C", str(source), "rev-parse", "HEAD"))
        git("-C", str(source), "push", "-q", str(self.origin), "main")
        self.versions = self.tmp / "share" / "bosun-versions"
        self.repo = self.versions / "bosun.git"
        git("clone", "-q", "--bare", "--single-branch", "--branch", "main", str(self.origin), str(self.repo))
        git("--git-dir", str(self.repo), "config", "bosun.managed", "true")
        git("--git-dir", str(self.repo), "config", "bosun.branch", "main")
        git("--git-dir", str(self.repo), "fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main")
        self.folders = []
        for commit in self.commits:
            folder = self.versions / commit[:12]
            git("--git-dir", str(self.repo), "worktree", "add", "-q", "--detach", str(folder), commit)
            self.folders.append(folder)
        self.checkout = self.tmp / "checkout"
        git("clone", "-q", str(self.origin), str(self.checkout))
        git("-C", str(self.checkout), "config", "bosun.managed", "true")


class CheckoutTest(World):
    def test_a_clean_checkout_holds_nothing(self) -> None:
        self.assertEqual(cw.checkout_work(self.checkout), "")
        self.assertEqual(cw.checkout(self.checkout), Checkout(str(self.checkout), True, ""))
        self.assertEqual(cw.kept(self.checkout), "")

    def test_what_is_yours_in_it(self) -> None:
        (self.checkout / "notes.txt").write_text("mine\n")
        self.assertEqual(cw.checkout_work(self.checkout), "uncommitted changes")
        self.assertIn("it has uncommitted changes", cw.kept(self.checkout))

    def test_one_the_installer_did_not_make_is_yours(self) -> None:
        git("-C", str(self.checkout), "config", "--unset", "bosun.managed")
        self.assertEqual(cw.kept(self.checkout), cw.NOT_THE_INSTALLERS)

    def test_git_never_looks_above_the_folder_it_checks(self) -> None:
        """A folder that is no repository, inside one: its parent's state is not its own."""
        inner = self.checkout / "notes"
        inner.mkdir()
        self.assertEqual(cw.checkout_work(inner), cw.UNCHECKED)

    def test_a_git_variable_of_the_caller_s_never_sends_it_elsewhere(self) -> None:
        (self.checkout / "notes.txt").write_text("mine\n")
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.repo), "GIT_WORK_TREE": str(self.tmp)}):
            self.assertEqual(cw.checkout_work(self.checkout), "uncommitted changes")


class VersionTest(World):
    def test_a_version_as_it_was_checked_out_holds_nothing(self) -> None:
        self.assertEqual(cw.version_work(self.folders[0]), "")
        self.assertEqual(cw.version_kept(self.folders[0]), "")

    def test_its_files_changed_are_yours(self) -> None:
        (self.folders[0] / "bosun").write_text("edited\n")
        self.assertEqual(cw.version_work(self.folders[0]), "uncommitted changes")
        self.assertIn("uncommitted changes", cw.version_kept(self.folders[0]))

    def test_a_commit_made_in_it_is_yours(self) -> None:
        git("-C", str(self.folders[0]), "commit", "-q", "--allow-empty", "-m", "mine")
        self.assertEqual(cw.version_work(self.folders[0]), "commits not pushed")

    def test_a_release_upstream_rewrote_is_not_yours(self) -> None:
        """Its commit is on no remote any more, after a force-push upstream: it is still the release bosun
        checked out there, not a commit of yours."""
        git("--git-dir", str(self.repo), "update-ref", "refs/remotes/origin/main", self.commits[0])
        self.assertEqual(cw.version_work(self.folders[1]), "")

    def test_the_repository_holds_nothing_with_its_own_branch_behind_a_rewrite(self) -> None:
        git("--git-dir", str(self.repo), "update-ref", "refs/remotes/origin/main", self.commits[0])
        self.assertEqual(cw.versions_work(self.versions, self.folders), "")

    def test_a_branch_of_your_own_in_the_repository_is_yours(self) -> None:
        git("-C", str(self.folders[0]), "commit", "-q", "--allow-empty", "-m", "mine")
        git("-C", str(self.folders[0]), "branch", "mine")
        self.assertEqual(cw.versions_work(self.versions, self.folders), "commits not pushed")

    def test_a_worktree_elsewhere_and_what_bosun_does_not_put_there_are_yours(self) -> None:
        git("--git-dir", str(self.repo), "worktree", "add", "-q", "--detach", str(self.tmp / "mine"))
        self.assertEqual(cw.versions_work(self.versions, self.folders), "other worktrees")
        git("--git-dir", str(self.repo), "worktree", "remove", str(self.tmp / "mine"))
        (self.versions / "notes.txt").write_text("mine\n")
        self.assertEqual(
            cw.versions_work(self.versions, self.folders), "what bosun does not know (notes.txt)"
        )

    def test_the_versions_git_lists_in_the_folder(self) -> None:
        self.assertEqual(cw.version_folders(self.versions), sorted(self.folders))


if __name__ == "__main__":
    unittest.main()
