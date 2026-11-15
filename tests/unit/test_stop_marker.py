"""lib/stop_marker.py: the one reader of what `bosun stop` leaves (stopped-<session>), which the watchdog and
the login start both read."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import stop_marker


class StopMarkerTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)

    def test_reads_the_time_bosun_stop_wrote(self) -> None:
        (self.state / "stopped-acme").write_text("2026-11-16 10:15:00\n")  # date '+%F %T'
        self.assertEqual(stop_marker.read(self.state, "acme"), datetime(2026, 11, 16, 10, 15))

    def test_the_marker_is_the_session_s_own(self) -> None:
        self.assertEqual(stop_marker.path(self.state, "acme"), self.state / "stopped-acme")
        (self.state / "stopped-other").write_text("2026-11-16 10:15:00\n")
        self.assertIsNone(stop_marker.read(self.state, "acme"))

    def test_a_marker_that_says_no_time_is_none(self) -> None:
        marker = self.state / "stopped-acme"
        for content in (b"sometime\n", b"", b"2026-11-16\n", b"\xff\xfe"):
            with self.subTest(content):
                marker.write_bytes(content)
                self.assertIsNone(stop_marker.read(self.state, "acme"))

    def test_read_stamp_reads_any_marker_in_the_format(self) -> None:
        marker = self.state / "login-reload"
        marker.write_text("2026-11-16 07:59:30")
        self.assertEqual(stop_marker.read_stamp(marker), datetime(2026, 11, 16, 7, 59, 30))
        self.assertIsNone(stop_marker.read_stamp(self.state / "missing"))


if __name__ == "__main__":
    unittest.main()
