"""Unit tests for tools/forbidden_terms.py: the pre-commit check against a local term list."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import forbidden_terms


TERMS = """\
# organization and product names (placeholders)
Initech
tps[-_ ]?report

   # an indented comment and blank lines are ignored
lumbergh
"""


class ForbiddenTermsTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.config = self.base / "config"
        self.config.mkdir()
        (self.config / "forbidden-terms.txt").write_text(TERMS)
        environment = mock.patch.dict(os.environ, {"BOSUN_CONFIG_DIR": str(self.config)})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("FORBIDDEN_TERMS_REDACT", None)  # CI sets it

    def write(self, name: str, text: str) -> str:
        path = self.base / name
        path.write_text(text)
        return str(path)

    def run_check(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = forbidden_terms.main(list(argv))
        return status, out.getvalue(), err.getvalue()

    def test_clean_files_pass(self) -> None:
        clean = self.write("clean.md", "Acme's invoice exporter\nnothing to see\n")
        self.assertEqual(self.run_check(clean), (0, "", ""))

    def test_terms_match_case_insensitively_as_regexes(self) -> None:
        path = self.write("notes.md", "intro\nsee the INITECH wiki\nfile a TPS_report today\n")
        status, out, err = self.run_check(path)
        self.assertEqual(status, forbidden_terms.EXIT_FOUND)
        self.assertIn(f"{path}:2: 'INITECH' matches forbidden term on line 2 of the list", out)
        self.assertIn(f"{path}:3: 'TPS_report' matches forbidden term on line 3 of the list", out)
        self.assertIn("2 match(es)", err)

    def test_comment_lines_are_not_terms(self) -> None:
        path = self.write("comment.md", "organization and product names (placeholders)\n")
        self.assertEqual(self.run_check(path)[0], 0)

    def test_the_path_is_checked_too(self) -> None:
        path = self.write("lumbergh-notes.txt", "harmless\n")
        status, out, _ = self.run_check(path)
        self.assertEqual(status, forbidden_terms.EXIT_FOUND)
        self.assertIn(f"{path} (path): 'lumbergh'", out)

    def test_contents_only_skips_the_path(self) -> None:
        clean = self.write("lumbergh-message.txt", "harmless\n")
        self.assertEqual(self.run_check("--contents-only", clean), (0, "", ""))
        dirty = self.write("lumbergh-dirty.txt", "ask Lumbergh\n")
        status, out, _ = self.run_check("--contents-only", dirty)
        self.assertEqual(status, forbidden_terms.EXIT_FOUND)
        self.assertEqual(out, f"{dirty}:1: 'Lumbergh' matches forbidden term on line 6 of the list\n")

    def test_redact_leaves_out_the_matched_text(self) -> None:
        path = self.write("notes.md", "intro\nsee the INITECH wiki\n")
        expected = f"{path}:2: matches forbidden term on line 2 of the list\n"
        status, out, _ = self.run_check("--redact", path)
        self.assertEqual((status, out), (forbidden_terms.EXIT_FOUND, expected))
        with mock.patch.dict(os.environ, {"FORBIDDEN_TERMS_REDACT": "1"}):
            self.assertEqual(self.run_check(path)[1], expected)

    def test_without_a_list_everything_passes(self) -> None:
        (self.config / "forbidden-terms.txt").unlink()
        path = self.write("notes.md", "Initech\n")
        self.assertEqual(self.run_check(path), (0, "", ""))

    def test_an_explicit_list_overrides_the_config_dir(self) -> None:
        other = self.write("other-terms.txt", "exporter\n")
        path = self.write("notes.md", "Initech invoice exporter\n")
        status, out, _ = self.run_check("--terms", other, path)
        self.assertEqual(status, forbidden_terms.EXIT_FOUND)
        self.assertIn("'exporter' matches forbidden term on line 1", out)
        self.assertNotIn("Initech", out)

    def test_a_missing_explicit_list_is_an_error(self) -> None:
        status, _, err = self.run_check("--terms", str(self.base / "missing.txt"), self.write("a.md", "x"))
        self.assertEqual(status, forbidden_terms.EXIT_ERROR)
        self.assertIn("no such list", err)

    def test_bad_patterns_are_errors(self) -> None:
        for pattern, message in [("tps(", "invalid pattern"), ("x*", "matches empty text")]:
            with self.subTest(pattern=pattern):
                (self.config / "forbidden-terms.txt").write_text(f"Initech\n{pattern}\n")
                status, _, err = self.run_check(self.write("a.md", "x"))
                self.assertEqual(status, forbidden_terms.EXIT_ERROR)
                self.assertIn(f"forbidden-terms.txt:2: {message}", err)

    def test_unreadable_files_are_errors(self) -> None:
        status, _, err = self.run_check(str(self.base / "gone.md"))
        self.assertEqual(status, forbidden_terms.EXIT_ERROR)
        self.assertIn("cannot read", err)

    def test_invalid_utf8_is_still_scanned(self) -> None:
        path = self.base / "binary.dat"
        path.write_bytes(b"\xff\xfe Initech \x00")
        self.assertEqual(self.run_check(str(path))[0], forbidden_terms.EXIT_FOUND)

    def test_a_binary_file_is_only_checked_in_its_readable_text(self) -> None:
        path = self.base / "image.png"
        path.write_bytes(b"\x89PNG\x00\xffInitech\x00")  # seven bytes of compressed data, by chance
        self.assertEqual(self.run_check(str(path))[0], 0)
        path.write_bytes(b"\x89PNG\x00\xffSoftware: Initech 1.0\x00")  # metadata
        self.assertEqual(self.run_check(str(path))[0], forbidden_terms.EXIT_FOUND)

    def test_default_list_location(self) -> None:
        with mock.patch.dict(os.environ, {"BOSUN_CONFIG_DIR": ""}):
            expected = Path("~/.config/bosun/forbidden-terms.txt").expanduser()
            self.assertEqual(forbidden_terms.default_terms_file(), expected)


if __name__ == "__main__":
    unittest.main()
