"""lib/awake.py: the awake window rule, and its command line (what `bosun morning --now` asks)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from datetime import datetime, time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import awake


# 2026-11-12 is a Thursday ("R"); 2026-11-13 a Friday ("F"); 2026-11-14 a Saturday ("S").
THURSDAY_NOON = datetime(2026, 11, 12, 12, 0, 0)


def run_cli(*argv: str, now: datetime = THURSDAY_NOON) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = awake.main(list(argv), now=now)
    return status, out.getvalue(), err.getvalue()


def remaining(days: str, start: str, until: str, now: datetime = THURSDAY_NOON) -> tuple[int, str, str]:
    return run_cli("remaining", "--days", days, "--start", start, "--until", until, now=now)


class WindowTest(unittest.TestCase):
    def test_current_is_today_between_start_and_until(self) -> None:
        window = awake.AwakeWindow("MTWRF", time(8, 0), time(20, 0))
        self.assertEqual(
            window.current(THURSDAY_NOON), (datetime(2026, 11, 12, 8), datetime(2026, 11, 12, 20))
        )

    def test_current_is_none_outside(self) -> None:
        window = awake.AwakeWindow("MTWRF", time(8, 0), time(20, 0))
        self.assertIsNone(window.current(datetime(2026, 11, 12, 21)))


class RemainingTest(unittest.TestCase):
    def test_inside_prints_the_whole_seconds_left_and_succeeds(self) -> None:
        self.assertEqual(remaining("MTWRF", "08:00", "20:00"), (0, f"{8 * 3600}\n", ""))

    def test_a_part_of_a_second_counts_as_one(self) -> None:
        now = datetime(2026, 11, 12, 19, 59, 59, 400_000)
        self.assertEqual(remaining("MTWRF", "08:00", "20:00", now=now), (0, "1\n", ""))

    def test_outside_prints_nothing_and_exits_one(self) -> None:
        self.assertEqual(remaining("MTWRF", "13:00", "20:00"), (1, "", ""))
        self.assertEqual(remaining("MTWRF", "08:00", "11:59"), (1, "", ""))

    def test_the_end_itself_is_outside(self) -> None:
        self.assertEqual(remaining("MTWRF", "08:00", "12:00"), (1, "", ""))

    def test_an_unscheduled_day_is_outside(self) -> None:
        self.assertEqual(remaining("MTWF", "08:00", "20:00"), (1, "", ""))  # no R: Thursday
        self.assertEqual(remaining("", "08:00", "20:00"), (1, "", ""))

    def test_a_window_past_midnight_started_today(self) -> None:
        self.assertEqual(remaining("R", "08:00", "01:00"), (0, f"{13 * 3600}\n", ""))

    def test_yesterdays_window_past_midnight_is_still_running(self) -> None:
        now = datetime(2026, 11, 13, 0, 30)  # Friday: not scheduled, but Thursday's window runs to 01:00
        self.assertEqual(remaining("R", "08:00", "01:00", now=now), (0, "1800\n", ""))

    def test_yesterdays_window_that_was_not_scheduled_is_not_running(self) -> None:
        now = datetime(2026, 11, 13, 0, 30)
        self.assertEqual(remaining("F", "08:00", "01:00", now=now), (1, "", ""))

    def test_an_until_equal_to_the_start_is_a_full_day(self) -> None:
        self.assertEqual(remaining("R", "08:00", "08:00"), (0, f"{20 * 3600}\n", ""))

    def test_bad_settings_are_a_distinct_error_with_one_line(self) -> None:
        for days, start, until in (
            ("MTWRF", "8am", "20:00"),
            ("MTWRF", "08:00", "25:00"),
            ("MTWRF", "08:00", ""),
            ("MTWRX", "08:00", "20:00"),
            ("mtwrf", "08:00", "20:00"),
        ):
            with self.subTest(days=days, start=start, until=until):
                status, out, err = remaining(days, start, until)
                self.assertEqual((status, out), (2, ""))
                self.assertEqual(len(err.splitlines()), 1)
                self.assertTrue(err.startswith("awake.py: "))

    def test_the_clock_is_read_when_no_time_is_given(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = awake.main(["remaining", "--days", "MTWRFSU", "--start", "00:00", "--until", "00:00"])
        self.assertEqual(status, 0)  # an until at the start is a whole day: always inside
        self.assertTrue(0 < int(out.getvalue()) <= 86400)


if __name__ == "__main__":
    unittest.main()
