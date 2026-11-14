"""lib/log_files.py: the log folder kept in bounds (the days past the keep go, launchd's files trimmed)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import log_files


TODAY = date(2026, 11, 14)
NOW = datetime(2026, 11, 14, 8, 1)


class PruneTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def day_file(self, command: str, days_ago: int) -> Path:
        path = self.dir / f"{command}-{TODAY - timedelta(days=days_ago):%Y-%m-%d}.log"
        path.write_text("a line\n")
        return path

    def test_days_past_the_keep_go_by_the_date_in_their_name(self) -> None:
        old = self.day_file("launch", log_files.KEEP_DAYS + 1)
        kept = self.day_file("launch", log_files.KEEP_DAYS)
        today = self.day_file("checkin", 0)
        said = log_files.prune(self.dir, TODAY, NOW)
        self.assertFalse(old.exists())
        self.assertTrue(kept.exists() and today.exists())
        self.assertEqual(said, [f"removed {old.name} (older than {log_files.KEEP_DAYS} days)"])

    def test_other_files_are_never_touched(self) -> None:
        others = [
            self.dir / name
            for name in ("notes.txt", "launch-latest.log", "x-2026-13-40.log", "Launch-2020-01-01.log")
        ]
        for path in others:
            path.write_text("keep me\n")
        self.assertEqual(log_files.prune(self.dir, TODAY, NOW), [])
        self.assertTrue(all(path.exists() for path in others))

    def test_a_large_launchd_file_keeps_its_end_in_the_same_file(self) -> None:
        path = self.dir / "console.launchd.err"
        lines = [
            f"2026-11-14 09:{n // 60:02d}:{n % 60:02d} [console] GET /api/version 401\n" for n in range(40000)
        ]
        path.write_text("".join(lines))
        inode = path.stat().st_ino
        said = log_files.prune(self.dir, TODAY, NOW)
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith("trimmed console.launchd.err to its last 256 KB"))
        self.assertEqual(path.stat().st_ino, inode)  # launchd's open file, not a new one
        text = path.read_text()
        self.assertLessEqual(path.stat().st_size, log_files.LAUNCHD_KEEP_BYTES + 100)
        self.assertTrue(text.startswith("2026-11-14 08:01:00 [logs] trimmed to its last 256 KB\n"))
        self.assertTrue(text.endswith(lines[-1]))
        self.assertIn(text.splitlines(keepends=True)[1], lines)  # whole lines only

    def test_a_small_launchd_file_is_left_alone(self) -> None:
        path = self.dir / "watchdog.launchd.out"
        path.write_text("a few lines\n")
        self.assertEqual(log_files.prune(self.dir, TODAY, NOW), [])
        self.assertEqual(path.read_text(), "a few lines\n")

    def test_no_folder_is_nothing_to_do(self) -> None:
        self.assertEqual(log_files.prune(self.dir / "missing", TODAY, NOW), [])

    def test_the_command_prints_what_it_did(self) -> None:
        old = self.day_file("refresh", 90)
        with mock.patch("sys.stdout") as out:
            code = log_files.main(["prune", "--log-dir", str(self.dir), "--today", TODAY.isoformat()])
        self.assertEqual(code, 0)
        self.assertFalse(old.exists())
        out.write.assert_any_call(f"removed {old.name} (older than {log_files.KEEP_DAYS} days)")


if __name__ == "__main__":
    unittest.main()
