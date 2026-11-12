"""lib/update_lookup.py: the git side of `bosun update --check`, `--skip` and the release to install, against
real repositories made in a temporary folder (fictional commits, no network)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import checkout_work
import update_lookup


GIT = ("git", "-c", "user.name=acme", "-c", "user.email=dev@example.com", "-c", "commit.gpgsign=false")


def git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run([*GIT, *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return done.stdout.strip()


class LookupTest(unittest.TestCase):
    """An upstream with three commits; the install's bosun.git, cloned when it had the first, with that
    commit as its version folder (what update_lookup.ROOT is)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.up = self.tmp / "upstream"
        self.up.mkdir()
        git(self.up, "init", "-q", "-b", "main")
        self.commits = [self.commit("first")]
        self.versions = self.tmp / "share" / "bosun-versions"
        self.versions.mkdir(parents=True)
        self.repo = self.versions / "bosun.git"
        git(self.tmp, "clone", "-q", "--bare", str(self.up), str(self.repo))
        # As get.sh leaves it: the branch fetched into its remote ref, at the install's commit.
        git(
            self.tmp,
            "--git-dir",
            str(self.repo),
            "fetch",
            "-q",
            "origin",
            "+refs/heads/main:refs/remotes/origin/main",
        )
        self.commits += [self.commit("second"), self.commit("third")]
        self.state = self.tmp / "state"
        self.install(0)

    def commit(self, subject: str) -> str:
        git(self.up, "commit", "-q", "--allow-empty", "-m", subject)
        return git(self.up, "rev-parse", "HEAD")

    def tag(self, name: str, commit: int, notes: str | None = None) -> None:
        if notes is None:
            git(self.up, "tag", name, self.commits[commit])
        else:
            git(self.up, "tag", "-a", name, "-m", notes, self.commits[commit])

    def install(self, commit: int) -> None:
        """The version in use is the commit's folder (made as an update makes it)."""
        folder = self.versions / self.commits[commit][:12]
        if not folder.exists():
            git(self.tmp, "--git-dir", str(self.repo), "fetch", "-q", str(self.up), self.commits[commit])
            git(
                self.tmp,
                "--git-dir",
                str(self.repo),
                "worktree",
                "add",
                "-q",
                "--detach",
                str(folder),
                self.commits[commit],
            )
        patch = mock.patch.object(update_lookup, "ROOT", folder)
        patch.start()
        self.addCleanup(patch.stop)

    def folders(self) -> list[str]:
        return sorted(p.name for p in self.versions.iterdir())

    def answer(self, channel: str = "releases") -> dict[str, Any]:
        return update_lookup.answer(channel, self.state)

    def test_the_newest_release_is_available_with_its_notes(self) -> None:
        self.tag("v2026.01.01", 1, "- Second\n")
        self.tag("v2025.12.01", 0, "- First\n")
        before = self.folders()
        found = self.answer()
        self.assertIsNone(found["error"])
        self.assertTrue(found["available"])
        self.assertEqual(
            found["latest"], {"version": "v2026.01.01", "commit": self.commits[1], "notes": "- Second"}
        )
        self.assertEqual(found["installed"], {"version": "v2025.12.01", "commit": self.commits[0]})
        self.assertEqual(self.folders(), before, "no version folder was made")

    def uninstall_would_lose(self) -> str:
        """What uninstall's check finds in the versions folder to keep it for."""
        return checkout_work.versions_work(self.versions, [self.versions / self.commits[0][:12]])

    def test_a_check_leaves_nothing_for_uninstall_to_keep(self) -> None:
        self.tag("v2026.01.01", 1, "- Second\n")
        self.assertEqual(self.uninstall_would_lose(), "")
        self.assertTrue(self.answer()["available"])
        self.assertEqual(self.uninstall_would_lose(), "")

    def test_a_target_leaves_nothing_for_uninstall_to_keep(self) -> None:
        self.tag("v2026.01.01", 1, "- Second\n")
        self.tag("v2026.01.02", 2, "- Third\n")
        self.assertEqual(update_lookup.target(), f"commit {self.commits[2]} v2026.01.02")
        self.assertEqual(self.uninstall_would_lose(), "")

    def test_a_target_that_fails_says_why_and_never_the_password(self) -> None:
        self.tag("v2026.01.01", 1, "- Second\n")
        login = "user:0000placeholder"  # made up
        url = f"https://{login}@127.0.0.1:9/acme/bosun.git"
        git(self.tmp, "--git-dir", str(self.repo), "remote", "set-url", "origin", url)
        with self.assertRaises(update_lookup.LookupFailed) as failed:
            update_lookup.target()
        message = str(failed.exception)
        self.assertTrue(message.startswith("could not reach https://127.0.0.1:9/acme/bosun.git: "), message)
        self.assertGreater(len(message), len("could not reach https://127.0.0.1:9/acme/bosun.git: "))
        self.assertNotIn("0000placeholder", message)
        self.assertEqual(self.answer()["error"], "could not reach https://127.0.0.1:9/acme/bosun.git")

    def test_a_check_changes_no_state(self) -> None:
        self.tag("v2026.01.01", 1, "- Second\n")
        self.answer()
        self.assertFalse(self.state.exists())

    def test_an_installed_release_is_up_to_date(self) -> None:
        self.tag("v2026.01.01", 0, "- First\n")
        found = self.answer()
        self.assertFalse(found["available"] or found["ahead"])
        self.assertEqual(found["installed"]["version"], "v2026.01.01")

    def test_an_install_ahead_of_the_newest_release(self) -> None:
        self.tag("v2026.01.01", 0, "- First\n")
        self.install(2)
        found = self.answer()
        self.assertTrue(found["ahead"])
        self.assertFalse(found["available"])
        self.assertEqual(update_lookup.target(), "ahead v2026.01.01")

    def test_no_release_yet(self) -> None:
        found = self.answer()
        self.assertIsNone(found["latest"])
        self.assertIsNone(found["error"])
        with self.assertRaisesRegex(update_lookup.LookupFailed, "no release at"):
            update_lookup.target()

    def test_the_target_is_the_newest_release_fetched(self) -> None:
        self.tag("v2026.01.01", 1, "- Second\n")
        self.tag("v2026.01.01.2", 2)
        self.assertEqual(update_lookup.target(), f"commit {self.commits[2]} v2026.01.01.2")
        git(self.tmp, "--git-dir", str(self.repo), "cat-file", "-e", f"{self.commits[2]}^{{commit}}")

    def test_a_skipped_release_is_reported_until_a_newer_one(self) -> None:
        self.tag("v2026.01.01", 1, "- Second\n")
        update_lookup.write_skip(self.state, "v2026.01.01", datetime(2026, 1, 2, 9, 0, tzinfo=UTC))
        self.assertEqual(
            json.loads((self.state / "update-skip.json").read_text()),
            {"version": "v2026.01.01", "at": "2026-01-02T09:00:00+00:00"},
        )
        found = self.answer()
        self.assertTrue(found["skipped"])
        self.tag("v2026.01.02", 2, "- Third\n")
        self.assertFalse(self.answer()["skipped"])

    def test_an_unreadable_skip_file_skips_nothing(self) -> None:
        self.state.mkdir()
        (self.state / "update-skip.json").write_text("{not json")
        self.assertIsNone(update_lookup.skipped_version(self.state))
        self.assertIsNone(update_lookup.skipped_version(None))

    def test_an_origin_that_cannot_be_reached_is_the_answers_error(self) -> None:
        git(
            self.tmp,
            "--git-dir",
            str(self.repo),
            "remote",
            "set-url",
            "origin",
            "https://0000placeholder-token@127.0.0.1:9/acme/bosun.git",
        )  # gitleaks:allow
        found = self.answer()
        self.assertEqual(found["error"], "could not reach https://127.0.0.1:9/acme/bosun.git")
        self.assertFalse(found["available"])
        self.assertEqual(found["installed"]["commit"], self.commits[0])

    def test_the_main_channel_lists_the_subjects_since_the_installed_commit(self) -> None:
        found = self.answer("main")
        self.assertIsNone(found["error"])
        self.assertTrue(found["available"])
        self.assertEqual(found["latest"]["commit"], self.commits[2])
        self.assertEqual(found["latest"]["version"], self.commits[2][:12])
        self.assertEqual(found["latest"]["notes"], "- third\n- second")
        self.install(2)
        self.assertFalse(self.answer("main")["available"])

    def test_the_main_channel_follows_the_branch_the_repository_names(self) -> None:
        git(self.up, "checkout", "-q", "-b", "next", self.commits[1])
        git(self.tmp, "--git-dir", str(self.repo), "config", "bosun.branch", "next")
        self.assertEqual(self.answer("main")["latest"]["commit"], self.commits[1])

    def test_a_channel_that_is_neither_is_refused_naming_the_key(self) -> None:
        found = self.answer("nightly")
        self.assertIn("BOSUN_UPDATE_CHANNEL", found["error"])
        self.assertIn("releases or main", found["error"])

    def test_a_checkout_is_not_something_to_check(self) -> None:
        with mock.patch.object(update_lookup, "ROOT", self.up):
            found = self.answer()
        self.assertIn("is a checkout", found["error"])


class CommandTest(unittest.TestCase):
    def run_main(self, *argv: str, **env: str) -> tuple[int, str]:
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(
                update_lookup,
                "answer",
                side_effect=lambda channel, state: {"channel": channel, "state": str(state)},
            ),
            contextlib.redirect_stdout(out),
        ):
            return update_lookup.main(argv), out.getvalue()

    def test_check_is_json_with_the_channel_and_state_folder_it_is_given(self) -> None:
        code, out = self.run_main("check", "--json", BOSUN_UPDATE_CHANNEL="main", BOSUN_STATE_DIR="/x/state")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"channel": "main", "state": "/x/state"})

    def test_skip_writes_the_version_and_refuses_what_is_not_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_main("skip", "v2026.11.12", BOSUN_STATE_DIR=tmp)
            self.assertEqual(code, 0)
            self.assertIn("v2026.11.12", out)
            self.assertEqual(update_lookup.skipped_version(Path(tmp)), "v2026.11.12")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code, _ = self.run_main("skip", "main", BOSUN_STATE_DIR=tmp)
            self.assertEqual(code, 2)
            self.assertIn("--skip takes a release version", err.getvalue())
            self.assertEqual(update_lookup.skipped_version(Path(tmp)), "v2026.11.12")

    def test_target_refuses_the_main_channel_and_a_bad_one_in_one_line(self) -> None:
        for channel in ("main", "nightly"):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code, _ = self.run_main("target", BOSUN_UPDATE_CHANNEL=channel)
            self.assertEqual(code, 1)
            self.assertTrue(err.getvalue().startswith("bosun update: "), err.getvalue())


if __name__ == "__main__":
    unittest.main()
