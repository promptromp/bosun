"""lib/quit_state.py: what `bosun quit` leaves (quit.json), read back by the watchdog, learning, the morning
run and `bosun status --json`."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import quit_state


NOON = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)


def run_cli(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = quit_state.main(list(argv))
    return status, out.getvalue()


class QuitStateTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"  # not there yet: write makes it
        self.file = self.state / "quit.json"

    def test_no_file_is_not_stopped(self) -> None:
        self.assertIsNone(quit_state.read(self.state))

    def test_the_round_trip_keeps_the_time_and_the_choice(self) -> None:
        quit_state.write(self.state, True, now=NOON)
        read = quit_state.read(self.state)
        assert read is not None
        self.assertEqual(read, quit_state.QuitState(since=NOON, morning_paused=True))
        self.assertIsNotNone(read.since.tzinfo)
        quit_state.write(self.state, False, now=NOON)
        again = quit_state.read(self.state)
        assert again is not None
        self.assertFalse(again.morning_paused)

    def test_the_file_is_the_documented_json_and_leaves_no_temporary_file(self) -> None:
        quit_state.write(self.state, True, now=NOON)
        self.assertEqual(
            json.loads(self.file.read_text()), {"since": "2026-11-12T12:00:00+00:00", "morning_paused": True}
        )
        self.assertEqual([p.name for p in self.state.iterdir()], ["quit.json"])

    def test_write_stamps_the_time_it_ran_at(self) -> None:
        before = datetime.now(UTC).replace(microsecond=0)
        quit_state.write(self.state, False)
        read = quit_state.read(self.state)
        assert read is not None
        self.assertTrue(before <= read.since <= datetime.now(UTC))

    def test_a_second_quit_replaces_the_first(self) -> None:
        quit_state.write(self.state, True, now=NOON)
        later = NOON.replace(hour=18)
        quit_state.write(self.state, False, now=later)
        self.assertEqual(quit_state.read(self.state), quit_state.QuitState(later, False))

    def test_clear_removes_it_and_is_fine_when_there_is_none(self) -> None:
        quit_state.clear(self.state)
        quit_state.write(self.state, True, now=NOON)
        quit_state.clear(self.state)
        self.assertFalse(self.file.exists())
        self.assertIsNone(quit_state.read(self.state))

    def test_a_file_that_cannot_be_read_counts_as_stopped_and_paused(self) -> None:
        # bosun writes it whole, so garbage means something is wrong: the safe side is that bosun is
        # stopped (nothing restarts or alerts) with the morning paused, until you start it.
        self.state.mkdir()
        garbage = (
            "{not json",
            "",
            "[]",
            '{"since": "yesterday", "morning_paused": true}',
            '{"since": "2026-11-12T12:00:00", "morning_paused": true}',  # no time zone
            '{"since": "2026-11-12T12:00:00+00:00"}',
            '{"since": "2026-11-12T12:00:00+00:00", "morning_paused": "yes"}',
            '{"since": 5, "morning_paused": false}',
        )
        for text in garbage:
            with self.subTest(text=text):
                self.file.write_text(text)
                os.utime(self.file, (NOON.timestamp(), NOON.timestamp()))
                self.assertEqual(quit_state.read(self.state), quit_state.QuitState(NOON, True))

    def test_a_file_that_is_not_text_counts_the_same(self) -> None:
        self.state.mkdir()
        self.file.write_bytes(b"\xff\xfe\x00")
        os.utime(self.file, (NOON.timestamp(), NOON.timestamp()))
        self.assertEqual(quit_state.read(self.state), quit_state.QuitState(NOON, True))

    def test_the_time_is_shown_in_local_time_as_hours_and_minutes(self) -> None:
        state = quit_state.QuitState(NOON, False)
        self.assertEqual(quit_state.clock(state), NOON.astimezone().strftime("%H:%M"))


class CommandLineTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.args = ("--state-dir", str(self.state))
        self.at = NOON.astimezone().strftime("%H:%M")

    def test_with_no_file_neither_is_so(self) -> None:
        self.assertEqual(run_cli("stopped", *self.args), (1, ""))
        self.assertEqual(run_cli("paused", *self.args), (1, ""))

    def test_stopped_says_when_and_paused_only_when_the_morning_is(self) -> None:
        quit_state.write(self.state, False, now=NOON)
        self.assertEqual(run_cli("stopped", *self.args), (0, self.at + "\n"))
        self.assertEqual(run_cli("paused", *self.args), (1, ""))
        quit_state.write(self.state, True, now=NOON)
        self.assertEqual(run_cli("stopped", *self.args), (0, self.at + "\n"))
        self.assertEqual(run_cli("paused", *self.args), (0, self.at + "\n"))

    def test_a_file_that_cannot_be_read_is_both(self) -> None:
        (self.state / "quit.json").write_text("{")
        self.assertEqual(run_cli("stopped", *self.args)[0], 0)
        self.assertEqual(run_cli("paused", *self.args)[0], 0)


if __name__ == "__main__":
    unittest.main()
