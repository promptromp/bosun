"""Unit tests for tools/release.py: the dated release tag and its notes."""

from __future__ import annotations

import sys
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


class NotesTest(unittest.TestCase):
    def test_notes_list_subjects_newest_first(self) -> None:
        subjects = ["Fix the thing (#12)", "Add the other thing (#11)"]
        self.assertEqual(release.notes(subjects), "- Fix the thing (#12)\n- Add the other thing (#11)\n")

    def test_no_subjects_no_notes(self) -> None:
        self.assertEqual(release.notes([]), "")
        self.assertEqual(release.notes(["", "  "]), "")


if __name__ == "__main__":
    unittest.main()
