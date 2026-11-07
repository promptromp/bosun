"""lib/update_notes.py: the notes windows answer an update with, a file per window and kind, whatever
the window's name. Fictional windows."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import update_notes


class NoteTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.state_dir = tmp / "state"
        self.notes = self.state_dir / "update" / "notes"

    def test_a_note_is_kept_by_window_and_kind(self) -> None:
        update_notes.note(self.state_dir, "api", "stock", "sleep 900")
        update_notes.note(self.state_dir, "api", "status", "waiting on CI")
        self.assertEqual(sorted(p.name for p in self.notes.iterdir()), ["api.status", "api.stock"])
        self.assertEqual(update_notes.read_note(self.state_dir, "api", "stock"), "sleep 900")
        self.assertIsNone(update_notes.read_note(self.state_dir, "web", "status"))
        files = update_notes.Files(self.state_dir)
        self.assertEqual(
            (files.read("api", "status"), files.where("api", "stock")),
            ("waiting on CI", str(self.notes / "api.stock")),
        )

    def test_nothing_running_is_a_stock_note(self) -> None:
        update_notes.note(self.state_dir, "api", "stock", "nothing running")
        self.assertEqual(update_notes.read_note(self.state_dir, "api", "stock"), "nothing running")

    def test_any_window_name_is_a_note_of_its_own_in_the_notes_folder(self) -> None:
        """A name a hand-written sessions.yml or `send --new` gave (a space, a slash, dots) is encoded
        into the file's name: it can always be answered, and never reaches another folder."""
        for window in ("web app", "../api", "a/b", "..", "export: nightly"):
            self.assertIsNone(update_notes.read_note(self.state_dir, window, "status"), window)
            update_notes.note(self.state_dir, window, "status", f"answered {window}")
            self.assertEqual(update_notes.read_note(self.state_dir, window, "status"), f"answered {window}")
            self.assertEqual(
                update_notes.note_path(self.state_dir, window, "status").parent,
                self.state_dir / update_notes.NOTES,
            )
        self.assertEqual(update_notes.note_path(self.state_dir, "web app", "status").name, "web%20app.status")
        self.assertEqual(sorted(p.parent for p in self.state_dir.rglob("*.status")), [self.notes] * 5)

    def test_a_long_name_is_a_note_of_its_own_whose_file_name_fits(self) -> None:
        """Minor 5: a name whose encoding passes what a file name may hold (255 bytes) is cut short, the
        whole name's digest added: the same name always finds its note, and two never share one."""
        long = "acme-" + "x" * 295
        other = "acme-" + "x" * 294 + "y"  # the same first 299 bytes
        for window in (long, other, "é" * 150):
            update_notes.note(self.state_dir, window, "status", f"answered {window[-1]}")
            name = update_notes.note_path(self.state_dir, window, "status").name
            self.assertLessEqual(len(name.encode()), 255, window[:10])
        self.assertEqual(update_notes.read_note(self.state_dir, long, "status"), "answered x")
        self.assertEqual(update_notes.read_note(self.state_dir, other, "status"), "answered y")
        self.assertEqual(update_notes.Files(self.state_dir).read("é" * 150, "status"), "answered é")
        self.assertEqual(len(list(self.notes.iterdir())), 3)
        self.assertEqual(update_notes.note_path(self.state_dir, "api", "status").name, "api.status")

    def test_an_unknown_kind_or_no_window_is_refused(self) -> None:
        for window, kind in (("api", "mood"), ("", "stock")):
            with self.assertRaises(update_notes.NoteRefused, msg=(window, kind)):
                update_notes.note(self.state_dir, window, kind, "x")
        self.assertFalse((self.state_dir / "update").exists())

    def test_notes_an_earlier_update_left_are_cleared(self) -> None:
        update_notes.note(self.state_dir, "api", "status", "from the update before")
        update_notes.clear_notes(self.state_dir)
        self.assertIsNone(update_notes.read_note(self.state_dir, "api", "status"))


if __name__ == "__main__":
    unittest.main()
