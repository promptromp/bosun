"""lib/update_check.py: the release tag rule and the answer to "is there an update?"."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import update_check


OLD, NEW = "a" * 40, "b" * 40


class ReleaseKeyTest(unittest.TestCase):
    def test_the_days_first_and_later_releases(self) -> None:
        self.assertEqual(update_check.release_key("v2026.11.12"), (2026, 11, 12, 1))
        self.assertEqual(update_check.release_key("v2026.11.12.2"), (2026, 11, 12, 2))

    def test_what_is_not_a_release(self) -> None:
        for tag in ("v2026.11.12.0", "v2026.11.12.1", "v1", "app-v1.0.0", "v2026.11.12-rc1", "2026.11.12"):
            self.assertIsNone(update_check.release_key(tag), tag)

    def test_only_ascii_digits_make_a_release(self) -> None:
        # What get.sh's `[0-9]` twin refuses too (tests/unit/test_get_sh.py).
        for tag in ("v\u0662\u0660\u0662\u0667.11.12", "v2026.11.12.\u0662\u0663"):
            self.assertIsNone(update_check.release_key(tag), tag)


class NewestReleaseTest(unittest.TestCase):
    def test_newest_release_by_date_then_number(self) -> None:
        tags = [
            ("v2026.11.05", "c1"),
            ("v2026.11.12", "c2"),
            ("v2026.11.12.10", "c4"),
            ("v2026.11.12.9", "c3"),
            ("app-v9.9.9", "c5"),
            ("v2027", "c6"),
        ]
        self.assertEqual(update_check.newest_release(tags), ("v2026.11.12.10", "c4"))
        self.assertEqual(update_check.newest_release(tags[:2]), ("v2026.11.12", "c2"))

    def test_none_without_a_release(self) -> None:
        self.assertIsNone(update_check.newest_release([]))
        self.assertIsNone(update_check.newest_release([("app-v1.0.0", "x")]))

    def test_peeled_commits_win(self) -> None:
        tags = [("v2026.11.12", "tagobject"), ("v2026.11.12^{}", "commit"), ("v2026.11.05", "light")]
        self.assertEqual(update_check.newest_release(tags), ("v2026.11.12", "commit"))
        self.assertEqual(update_check.newest_release(tags[::-1]), ("v2026.11.12", "commit"))
        self.assertEqual(update_check.release_commits(tags)["v2026.11.05"], "light")

    def test_ls_remote_output(self) -> None:
        text = (
            f"t1\trefs/tags/v2026.11.12\n{NEW}\trefs/tags/v2026.11.12^{{}}\n\n{OLD}\trefs/tags/v2026.11.05\n"
        )
        tags = update_check.parse_ls_remote(text)
        self.assertEqual(update_check.newest_release(tags), ("v2026.11.12", NEW))
        self.assertEqual(update_check.version_at(OLD, tags), "v2026.11.05")
        self.assertIsNone(update_check.version_at("c" * 40, tags))


class CheckTest(unittest.TestCase):
    def test_available_when_newer(self) -> None:
        answer = update_check.check(OLD, "v2026.11.05", ("v2026.11.12", NEW), None, False, notes="- A fix\n")
        self.assertTrue(answer["available"])
        self.assertFalse(answer["skipped"] or answer["ahead"])
        self.assertEqual(answer["latest"], {"version": "v2026.11.12", "commit": NEW, "notes": "- A fix\n"})
        self.assertEqual(answer["installed"], {"version": "v2026.11.05", "commit": OLD})
        self.assertEqual((answer["contract"], answer["channel"], answer["error"]), (1, "releases", None))
        self.assertEqual(
            update_check.describe(answer), "bosun v2026.11.12 is available (you have v2026.11.05)"
        )

    def test_not_available_when_installed(self) -> None:
        answer = update_check.check(NEW, "v2026.11.12", ("v2026.11.12", NEW), None, True)
        self.assertFalse(answer["available"] or answer["ahead"])
        self.assertEqual(update_check.describe(answer), "bosun is up to date (v2026.11.12)")

    def test_no_release_yet(self) -> None:
        answer = update_check.check(OLD, None, None, None, False)
        self.assertFalse(answer["available"])
        self.assertIsNone(answer["latest"])
        self.assertIn("no release yet (you have aaaaaaaaaaaa)", update_check.describe(answer))

    def test_skipped_version_is_reported(self) -> None:
        answer = update_check.check(OLD, None, ("v2026.11.12", NEW), "v2026.11.12", False)
        self.assertTrue(answer["skipped"])
        self.assertTrue(answer["available"])
        self.assertTrue(update_check.describe(answer).endswith("(skipped)"))
        newer = update_check.check(OLD, None, ("v2026.11.13", NEW), "v2026.11.12", False)
        self.assertFalse(newer["skipped"])

    def test_an_install_ahead_of_the_newest_release_stays(self) -> None:
        answer = update_check.check(NEW, None, ("v2026.11.05", OLD), None, True)
        self.assertTrue(answer["ahead"])
        self.assertFalse(answer["available"])
        self.assertEqual(update_check.describe(answer), "bosun is ahead of the newest release (v2026.11.05)")

    def test_main_channel_lists_subjects_since_installed(self) -> None:
        notes = "- Third\n- Second\n"
        answer = update_check.check(OLD, None, (NEW[:12], NEW), None, False, channel="main", notes=notes)
        self.assertTrue(answer["available"])
        self.assertEqual(answer["channel"], "main")
        self.assertEqual(answer["latest"]["notes"], notes)
        self.assertEqual(update_check.describe(answer), "2 commits on main since yours")
        one = update_check.check(OLD, None, (NEW[:12], NEW), None, False, channel="main", notes="- One\n")
        self.assertEqual(update_check.describe(one), "1 commit on main since yours")
        ahead = update_check.check(NEW, None, (OLD[:12], OLD), None, True, channel="main")
        self.assertEqual(update_check.describe(ahead), f"bosun is ahead of the head of main ({OLD[:12]})")

    def test_a_problem_is_the_error_and_nothing_else(self) -> None:
        answer = update_check.failed("releases", OLD, "could not reach https://example.com/acme/bosun.git")
        self.assertFalse(answer["available"])
        self.assertIsNone(answer["latest"])
        self.assertEqual(
            update_check.describe(answer),
            "bosun update --check: could not reach https://example.com/acme/bosun.git",
        )


class UrlTest(unittest.TestCase):
    def test_credentials_are_taken_out_of_a_line_of_git_s(self) -> None:
        login = "user:0000placeholder"  # made up
        line = f"fatal: unable to access 'https://{login}@example.com/a.git/': Could not resolve host"
        stripped = update_check.strip_credentials(line)
        self.assertEqual(
            stripped, "fatal: unable to access 'https://example.com/a.git/': Could not resolve host"
        )
        both = update_check.strip_credentials(
            "https://a:b@one.example/x https://tok@two.example/y"
        )  # gitleaks:allow
        self.assertEqual(both, "https://one.example/x https://two.example/y")
        self.assertEqual(
            update_check.strip_credentials("git@example.com:acme/x.git"), "git@example.com:acme/x.git"
        )

    def test_credentials_are_taken_off(self) -> None:
        shown = update_check.url_without_credentials
        self.assertEqual(
            shown("https://token@example.com/acme/bosun.git"), "https://example.com/acme/bosun.git"
        )
        credentialed = (
            "https://user:0000placeholder@example.com:9/a.git"  # pragma: allowlist secret  # gitleaks:allow
        )
        self.assertEqual(shown(credentialed), "https://example.com:9/a.git")
        self.assertEqual(shown("git@example.com:acme/bosun.git"), "git@example.com:acme/bosun.git")
        self.assertEqual(shown("/srv/upstream"), "/srv/upstream")


if __name__ == "__main__":
    unittest.main()
