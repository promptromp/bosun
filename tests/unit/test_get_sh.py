"""get.sh's release pick and what a first install leaves, run in a temporary folder.

get.sh ends with `main "$@"` and must stay a script people pipe, so these tests source everything but that
last line into `sh` and call its functions: nothing of it runs but what a test calls, and nothing is
installed, linked or started (check_os, init and update are never reached). The functions run under
`sh` (dash on Linux, bash 3.2 in sh mode on a Mac), as get.sh does.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import checkout_work
import update_check


GET_SH = Path(__file__).resolve().parents[2] / "get.sh"
GIT = ("git", "-c", "user.name=acme", "-c", "user.email=dev@example.com", "-c", "commit.gpgsign=false")
A, B, C, D, E = ("a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40)


def clean_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GIT_", "BOSUN_")) and k not in {"ENV", "BASH_ENV"}
    }


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run([*GIT, *args], cwd=cwd, env=clean_env(), capture_output=True, text=True, check=True)
    return done.stdout.strip()


def ls_remote(*pairs: tuple[str, str]) -> str:
    """`git ls-remote --tags` output for (ref, commit) pairs: a tab between them, as git prints."""
    return "".join(f"{sha}\trefs/tags/{ref}\n" for ref, sha in pairs)


# Each is a list of (tag, commit): the lists of tests/unit/test_update_check.py, and the edges of the rule.
TAG_LISTS = {
    "by date, then number": [
        ("v2026.11.05", A),
        ("v2026.11.12", B),
        ("v2026.11.12.10", C),
        ("v2026.11.12.9", D),
        ("app-v9.9.9", E),
        ("v2027", E),
    ],
    "the day's first release alone": [("v2026.11.05", A), ("v2026.11.12", B)],
    ".0 and .1 are not releases": [("v2026.11.12", A), ("v2026.11.12.0", B), ("v2026.11.12.1", C)],
    ".0 and .1 alone": [("v2026.11.12.0", A), ("v2026.11.12.1", B)],
    ".10 is above .9": [("v2026.11.12.9", A), ("v2026.11.12.10", B), ("v2026.11.12.2", C)],
    "a second release of the day is above the first": [("v2026.11.12", A), ("v2026.11.12.2", B)],
    "an older day with a high number loses to a newer day": [("v2026.11.11.30", A), ("v2026.11.12", B)],
    "a month is not a day": [("v2026.12.01", A), ("v2026.11.30", B), ("v2025.12.31.5", C)],
    "peeled commits win": [("v2026.11.12", A), ("v2026.11.12^{}", B), ("v2026.11.05", C)],
    "peeled commits win, listed first": [("v2026.11.12^{}", B), ("v2026.11.12", A), ("v2026.11.05", C)],
    "a light tag keeps its commit": [("v2026.11.12", A), ("v2026.11.05^{}", C), ("v2026.11.05", D)],
    "a peeled entry alone is no release": [("v2026.11.12^{}", A), ("v2026.11.05", B)],
    "tags that are not releases": [("v1", A), ("app-v1.0.0", B), ("v2026.11.12-rc1", C), ("2026.11.12", D)],
    "a longer number or date is no release": [("v2026.11.12.2.3", A), ("v20261.11.12", B), ("v2026.1.12", C)],
    "a non-ASCII digit is no release": [("v2026.11.05", A), ("v\u0662\u0660\u0662\u0667.11.12", B)],
    "a leading zero is the same number": [("v2026.11.12.02", A), ("v2026.11.12.2", B)],
    "a leading zero is the same number, listed the other way": [("v2026.11.12.2", B), ("v2026.11.12.02", A)],
    "a leading zero alone": [("v2026.11.12.02", A)],
    ".01 is not a release": [("v2026.11.12", A), ("v2026.11.12.01", B)],
    ".010 is above .9": [("v2026.11.12.9", A), ("v2026.11.12.010", B)],
    "none": [],
}


def sh_newest(source: Path, text: str) -> tuple[str, str] | None:
    """get.sh's newest_release over <text>, as (tag, commit); None when it prints nothing."""
    done = subprocess.run(
        ["sh", "-c", '. "$1"; newest_release', "_", str(source)],
        input=text,
        env={**clean_env(), "HOME": "/nonexistent"},
        capture_output=True,
        text=True,
        check=True,
    )
    words = done.stdout.split()
    if not words:
        return None
    assert len(words) == 2, done.stdout
    return words[0], words[1]


class Source(unittest.TestCase):
    """get.sh without its last line, in a file of its own."""

    source: Path
    _sources: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        text = GET_SH.read_text().splitlines(keepends=True)
        assert text[-1].strip() == 'main "$@"', 'get.sh must end with `main "$@"`'
        cls._sources = tempfile.TemporaryDirectory()
        cls.source = Path(cls._sources.name) / "get-functions.sh"
        cls.source.write_text("".join(text[:-1]))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._sources.cleanup()


class NewestReleaseTwin(Source):
    """get.sh cannot run Python (it runs before bosun's own checks), so its newest_release is a twin of
    update_check.newest_release: the same answer for the same `git ls-remote --tags` output."""

    def test_the_same_answer_as_update_check(self) -> None:
        for name, tags in TAG_LISTS.items():
            text = ls_remote(*tags)
            with self.subTest(name):
                self.assertEqual(
                    sh_newest(self.source, text),
                    update_check.newest_release(update_check.parse_ls_remote(text)),
                )

    def test_the_answers_the_rule_gives(self) -> None:
        # What the lists above come to, so that neither side could drift to another answer together.
        expected = {
            "by date, then number": ("v2026.11.12.10", C),
            ".0 and .1 are not releases": ("v2026.11.12", A),
            ".0 and .1 alone": None,
            ".10 is above .9": ("v2026.11.12.10", B),
            "a second release of the day is above the first": ("v2026.11.12.2", B),
            "an older day with a high number loses to a newer day": ("v2026.11.12", B),
            "a month is not a day": ("v2026.12.01", A),
            "peeled commits win": ("v2026.11.12", B),
            "peeled commits win, listed first": ("v2026.11.12", B),
            "a light tag keeps its commit": ("v2026.11.12", A),
            "a peeled entry alone is no release": ("v2026.11.05", B),
            "tags that are not releases": None,
            "a longer number or date is no release": None,
            "a non-ASCII digit is no release": ("v2026.11.05", A),
            "a leading zero is the same number": ("v2026.11.12.2", B),
            "a leading zero is the same number, listed the other way": ("v2026.11.12.2", B),
            "a leading zero alone": ("v2026.11.12.02", A),
            ".01 is not a release": ("v2026.11.12", A),
            ".010 is above .9": ("v2026.11.12.010", B),
            "none": None,
        }
        for name, answer in expected.items():
            with self.subTest(name):
                self.assertEqual(sh_newest(self.source, ls_remote(*TAG_LISTS[name])), answer)

    def test_lines_that_are_not_tag_lines_are_left_out(self) -> None:
        text = f"\n{A}\trefs/tags/v2026.11.05\nnot a line\n{B}\n\t\n"
        self.assertEqual(sh_newest(self.source, text), ("v2026.11.05", A))
        self.assertEqual(
            sh_newest(self.source, text), update_check.newest_release(update_check.parse_ls_remote(text))
        )


class RerunLine(Source):
    """The line get.sh prints to run it again: what this run was asked for, as a shell reads it back."""

    HEAD = "curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | "

    def line(self, **env: str) -> str:
        done = subprocess.run(
            ["sh", "-c", '. "$1"; rerun_line', "_", str(self.source)],
            env={**clean_env(), "HOME": "/home/acme", **env},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_with_nothing_asked_for_it_is_the_plain_line(self) -> None:
        self.assertEqual(self.line(), self.HEAD + "sh")

    def test_it_keeps_the_repository_the_folder_the_branch_and_the_agent_in_that_order(self) -> None:
        line = self.line(
            BOSUN_HARNESS="codex",
            BOSUN_BRANCH="feature",
            BOSUN_DIR="/opt/acme/bosun",
            BOSUN_REPO="https://git.example.com/acme/bosun.git",
        )
        self.assertEqual(
            line,
            self.HEAD
            + "BOSUN_REPO=https://git.example.com/acme/bosun.git BOSUN_DIR=/opt/acme/bosun "
            + "BOSUN_BRANCH=feature BOSUN_HARNESS=codex sh",
        )

    def test_an_empty_setting_is_left_out(self) -> None:
        self.assertEqual(self.line(BOSUN_DIR="", BOSUN_REPO=""), self.HEAD + "sh")

    def test_a_value_the_shell_would_split_or_expand_is_quoted_and_reads_back_as_it_was(self) -> None:
        awkward = {
            "BOSUN_DIR": "/Users/me/my bosun/$HOME;`x`",
            "BOSUN_REPO": "/srv/it's here/bosun.git",
            "BOSUN_BRANCH": "feat/it's*",
        }
        line = self.line(**awkward)
        # The line without its curl, ending in a command that prints what it was given.
        command = (
            line.removeprefix(self.HEAD)[: -len("sh")]
            + 'sh -c \'printf "%s\\n" "$BOSUN_DIR" "$BOSUN_REPO" "$BOSUN_BRANCH"\''
        )
        done = subprocess.run(
            ["sh", "-c", command], env=clean_env(), capture_output=True, text=True, check=False
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), list(awkward.values()))

    def test_a_password_in_the_repository_address_is_not_printed(self) -> None:
        line = self.line(BOSUN_REPO="https://user:secret-token@git.example.com/acme/bosun.git")
        self.assertNotIn("secret-token", line)
        self.assertIn("BOSUN_REPO='https://***@git.example.com/acme/bosun.git' ", line)

    def test_a_credential_in_any_form_of_address_is_not_in_the_line(self) -> None:
        for address in CREDENTIAL_FORMS:
            with self.subTest(address=address):
                line = self.line(BOSUN_REPO=address)
                self.assertNotIn("ghp_secret", line)
                self.assertNotIn("hunter2", line)


CREDENTIAL_FORMS = [
    "x-access-token:ghp_secret@github.com:acme/bosun.git",
    "https://git.example.com/acme/bosun.git?token=ghp_secret",
    "https://git.example.com/acme/bosun.git?a=1&access_token=ghp_secret",
    "https://ghp_secret@github.com/acme/bosun.git",
    "https://u:hunter2@git.example.com/a.git?key=ghp_secret",  # pragma: allowlist secret gitleaks:allow
]


class Shown(Source):
    """`shown`: a repository address as get.sh prints it, with no secret in it."""

    def shown(self, address: str) -> str:
        done = subprocess.run(
            ["sh", "-c", '. "$1"; shown "$2"', "_", str(self.source), address],
            env=clean_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_the_userinfo_of_a_url_is_hidden_with_or_without_a_colon(self) -> None:
        address = "https://u:pw@h.example.com/a/b.git"  # pragma: allowlist secret gitleaks:allow
        self.assertEqual(self.shown(address), "https://***@h.example.com/a/b.git")
        self.assertEqual(
            self.shown("https://ghp_secret@github.com/a/b.git"), "https://***@github.com/a/b.git"
        )

    def test_the_userinfo_of_an_scp_style_address_is_hidden(self) -> None:
        shown = self.shown("x-access-token:ghp_secret@github.com:acme/bosun.git")
        self.assertEqual(shown, "***@github.com:acme/bosun.git")

    def test_a_query_string_is_hidden(self) -> None:
        self.assertEqual(
            self.shown("https://h.example.com/a.git?token=ghp_secret"), "https://h.example.com/a.git?***"
        )
        self.assertEqual(
            self.shown("https://u:p@h.example.com?access_token=x"), "https://***@h.example.com?***"
        )

    def test_an_address_with_no_secret_is_left_as_it_is(self) -> None:
        for address in (
            "git@github.com:acme/bosun.git",
            "https://github.com/acme/bosun.git",
            "/srv/acme/bosun.git",
            "../a path/bosun.git",
        ):
            with self.subTest(address=address):
                self.assertEqual(self.shown(address), address)

    def test_a_value_with_spaces_is_handled(self) -> None:
        self.assertEqual(
            self.shown("https://u:p w@h.example.com/my repo.git"), "https://***@h.example.com/my repo.git"
        )
        self.assertEqual(self.shown("tok en@h.example.com:my repo.git"), "***@h.example.com:my repo.git")


class Upstream(Source):
    """An upstream with three commits and a place for the install, in a home of its own."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.up = self.tmp / "upstream"
        self.up.mkdir()
        git(self.up, "init", "-q", "-b", "main")
        self.commits = [self.commit(subject) for subject in ("first", "second", "third")]
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.install = self.tmp / "share" / "bosun"
        self.versions = self.tmp / "share" / "bosun-versions"

    def commit(self, subject: str) -> str:
        git(self.up, "commit", "-q", "--allow-empty", "-m", subject)
        return git(self.up, "rev-parse", "HEAD")

    def tag(self, name: str, commit: int, notes: str | None = None) -> None:
        if notes is None:
            git(self.up, "tag", name, self.commits[commit])
        else:
            git(self.up, "tag", "-a", name, "-m", notes, self.commits[commit])

    def run_get(self, script: str, **env: str) -> subprocess.CompletedProcess[str]:
        """<script> after get.sh's functions, as a new install: nothing is at the install path."""
        full = {
            **clean_env(),
            "HOME": str(self.home),
            "BOSUN_DIR": str(self.install),
            "BOSUN_REPO": str(self.up),
            **env,
        }
        return subprocess.run(
            ["sh", "-c", f'. "$1"; clean_env; KIND=new; {script}', "_", str(self.source)],
            env=full,
            capture_output=True,
            text=True,
            check=False,
        )

    def folders(self) -> list[str]:
        return sorted(p.name for p in self.versions.iterdir()) if self.versions.exists() else []

    def bare(self, *args: str) -> str:
        return git(self.tmp, "--git-dir", str(self.versions / "bosun.git"), *args)

    def would_lose(self) -> str:
        """What uninstall's check finds in the versions folder to keep it for."""
        return checkout_work.versions_work(self.versions, checkout_work.version_folders(self.versions))


class FirstInstall(Upstream):
    def test_the_newest_release_is_what_a_first_install_gets(self) -> None:
        self.tag("v2026.11.05", 0)
        self.tag("v2026.11.12", 1, "- Second\n")  # annotated: its commit is the peeled one
        self.tag("v2026.11.12.2", 2)
        self.commits.append(self.commit("after the release"))  # the head of main is past it
        self.tag("app-v1.0.0", 3)  # a tag of someone else's, on that head
        done = self.run_get("get_bosun")
        self.assertEqual(done.returncode, 0, done.stderr)
        folder = self.versions / self.commits[2][:12]
        self.assertEqual(self.folders(), sorted(["bosun.git", self.commits[2][:12]]))
        self.assertEqual(git(folder, "rev-parse", "HEAD"), self.commits[2])
        self.assertEqual(os.readlink(self.install), str(folder))
        self.assertIn("v2026.11.12.2", done.stdout)
        self.assertEqual(self.would_lose(), "")

    def test_it_follows_main_as_a_repository_and_takes_the_release_into_a_remote_ref(self) -> None:
        self.tag("v2026.11.12", 1, "- Second\n")
        self.run_get("get_bosun")
        self.assertEqual(self.bare("config", "bosun.branch"), "main")
        self.assertEqual(self.bare("config", "bosun.managed"), "true")
        self.assertEqual(self.bare("rev-parse", "refs/remotes/origin/release^{commit}"), self.commits[1])
        self.assertEqual(self.bare("for-each-ref", "refs/tags"), "", "no tag of its own")
        # Nothing of the release is a ref uninstall would take for work of yours.
        self.assertEqual(self.would_lose(), "")

    def test_a_tag_that_main_does_not_hold_is_no_work_either(self) -> None:
        git(self.up, "checkout", "-q", "-b", "release-line", self.commits[0])
        self.commits.append(self.commit("only on the release line"))
        git(self.up, "checkout", "-q", "main")
        self.tag("v2026.11.12", 3)
        done = self.run_get("get_bosun")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(git(self.versions / self.commits[3][:12], "rev-parse", "HEAD"), self.commits[3])
        self.assertEqual(self.would_lose(), "")

    def test_a_repository_with_no_release_gets_the_head_of_main_and_says_so(self) -> None:
        self.tag("app-v1.0.0", 0)
        done = self.run_get("get_bosun")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"There is no release of bosun at {self.up} yet: getting the head of main", done.stdout)
        self.assertEqual(self.folders(), sorted(["bosun.git", self.commits[2][:12]]))
        self.assertEqual(self.would_lose(), "")

    def test_a_branch_is_its_head_and_no_release_is_looked_for(self) -> None:
        self.tag("v2026.11.12", 1)
        git(self.up, "branch", "feature", self.commits[1])
        done = self.run_get("get_bosun", BOSUN_BRANCH="feature")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.folders(), sorted(["bosun.git", self.commits[1][:12]]))
        self.assertEqual(self.bare("config", "bosun.branch"), "feature")
        self.assertNotIn("release", done.stdout.lower())
        self.assertEqual(self.would_lose(), "")

    def test_an_unreachable_repository_still_stops_the_install(self) -> None:
        done = self.run_get("get_bosun", BOSUN_REPO=str(self.tmp / "nowhere"))
        self.assertEqual(done.returncode, 1)
        self.assertIn("bosun: could not reach", done.stderr)
        self.assertFalse(self.versions.exists())

    def test_the_dry_run_names_the_release_and_changes_nothing(self) -> None:
        self.tag("v2026.11.12", 1)
        done = self.run_get("DRY_RUN=1; get_bosun")
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        short = self.commits[1][:12]
        self.assertIn(f"  $ ln -s {self.versions}/{short} {self.install}", lines)
        self.assertTrue(
            any("v2026.11.12" in line and line.startswith("Getting bosun") for line in lines), lines
        )
        self.assertFalse(self.versions.exists())
        self.assertFalse(self.install.exists())


if __name__ == "__main__":
    unittest.main()
