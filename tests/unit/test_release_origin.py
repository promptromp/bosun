"""tests/sandbox/release_origin.sh: the origin a first install takes, with one release in it, made from a
checkout named by a relative path too (CI's install job passes `.`)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "tests" / "sandbox" / "release_origin.sh"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL
    ).stdout.strip()


class ReleaseOriginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="release-origin."))
        self.checkout = self.tmp / "checkout"
        self.checkout.mkdir()
        git(self.checkout, "init", "-q")
        (self.checkout / "a.txt").write_text("a\n")
        git(self.checkout, "add", "a.txt")
        git(self.checkout, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "one")
        self.head = git(self.checkout, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def make(self, checkout: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), checkout, "HEAD", str(self.tmp / "origin.git"), "v2026.1.1"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )

    def assert_origin(self) -> None:
        origin = self.tmp / "origin.git"
        self.assertEqual(git(origin, "rev-parse", "refs/heads/main"), self.head)
        self.assertEqual(git(origin, "cat-file", "-t", "v2026.1.1"), "tag")
        self.assertEqual(git(origin, "rev-parse", "v2026.1.1^{commit}"), self.head)

    def test_a_relative_checkout_path_is_the_checkout_not_the_new_repository(self) -> None:
        result = self.make(".", self.checkout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_origin()

    def test_an_absolute_checkout_path(self) -> None:
        result = self.make(str(self.checkout), self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_origin()

    def test_an_origin_that_is_there_already_is_refused(self) -> None:
        (self.tmp / "origin.git").mkdir()
        result = self.make(str(self.checkout), self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is there already", result.stderr)


if __name__ == "__main__":
    unittest.main()
