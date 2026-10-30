"""tools/check_links.py: relative links and anchors in Markdown."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import check_links


class CheckLinksTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        (self.base / "docs").mkdir()
        (self.base / "docs" / "guide.md").write_text(
            "# Guide\n\n## How `it` works: well\n\n## Again\n\n## Again\n"
        )
        (self.base / "logo.png").write_text("not really")

    def broken(self, text: str) -> list[str]:
        page = self.base / "page.md"
        page.write_text(text)
        return check_links.broken_links(page)

    def test_links_that_exist_are_fine(self) -> None:
        self.assertEqual(self.broken('[a](docs/guide.md) ![b](logo.png) <img src="logo.png">\n'), [])

    def test_a_missing_file_is_named_with_its_line(self) -> None:
        found = self.broken("first\n\n[a](docs/gone.md)\n")
        self.assertEqual(len(found), 1)
        self.assertIn("page.md:3: docs/gone.md: no such file", found[0])

    def test_html_links_count_too(self) -> None:
        self.assertIn("no such file", self.broken('<img src="gone.png">\n')[0])

    def test_anchors_are_github_s(self) -> None:
        self.assertEqual(self.broken("[a](docs/guide.md#how-it-works-well)\n"), [])
        self.assertIn("no heading", self.broken("[a](docs/guide.md#how)\n")[0])

    def test_a_repeated_heading_gets_a_number(self) -> None:
        self.assertEqual(self.broken("[a](docs/guide.md#again) [b](docs/guide.md#again-1)\n"), [])
        self.assertIn("no heading", self.broken("[a](docs/guide.md#again-2)\n")[0])

    def test_an_anchor_in_the_same_file(self) -> None:
        self.assertEqual(self.broken("# Top\n\n[a](#top)\n"), [])
        self.assertIn("no heading", self.broken("# Top\n\n[a](#bottom)\n")[0])

    def test_the_web_is_left_out(self) -> None:
        self.assertEqual(self.broken("[a](https://example.com/gone) [b](mailto:me@example.com)\n"), [])

    def test_code_is_left_out(self) -> None:
        self.assertEqual(self.broken("`[a](gone.md)`\n\n```\n[b](gone.md)\n```\n"), [])

    def test_a_heading_inside_code_is_not_a_heading(self) -> None:
        self.assertIn("no heading", self.broken("```\n# Fake\n```\n\n[a](#fake)\n")[0])

    def test_the_exit_status_says_so(self) -> None:
        page = self.base / "page.md"
        page.write_text("[a](gone.md)\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(check_links.main([str(page)]), check_links.EXIT_BROKEN)
        self.assertIn("gone.md", out.getvalue())
        page.write_text("nothing\n")
        self.assertEqual(check_links.main([str(page)]), 0)


if __name__ == "__main__":
    unittest.main()
