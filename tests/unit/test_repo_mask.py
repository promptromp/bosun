"""Unit tests for lib/repo_mask.py."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import repo_mask


class MatchesTest(unittest.TestCase):
    def test_rules(self) -> None:
        cases = [
            ("acme/*", "acme/api", True),
            ("acme", "acme/api", True),
            ("Acme/*", "ACME/Api", True),
            ("acme/web-*", "acme/api", False),
            ("acme/* other/one", "other/one", True),
            ("acme/* other/one", "personal/x", False),
            ("", "personal/x", True),
        ]
        for mask, slug, expected in cases:
            with self.subTest(mask=mask, slug=slug):
                self.assertEqual(repo_mask.matches(slug, mask), expected)


class FilterTest(unittest.TestCase):
    def run_filter(self, argv: list[str], stdin: str) -> str:
        with mock.patch("sys.stdin", io.StringIO(stdin)), mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(repo_mask.main(argv), 0)
        return out.getvalue()

    def test_first_field(self) -> None:
        out = self.run_filter(["acme/*"], "acme/api\t/dir/api\nother/x\t/dir/x\n")
        self.assertEqual(out, "acme/api\t/dir/api\n")

    def test_json_key(self) -> None:
        lines = [json.dumps({"repo": "acme/api", "n": 1}), json.dumps({"repo": "other/x"}), "not json"]
        out = self.run_filter(["acme", "--json-key", "repo"], "\n".join(lines) + "\n")
        self.assertEqual(out.splitlines(), [lines[0]])

    def test_usage(self) -> None:
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(repo_mask.main([]), 2)


if __name__ == "__main__":
    unittest.main()
