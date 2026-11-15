"""lib/login_start.py: whether a login starts bosun (docs/design/control-panel.md, "Start at login"), and the
command line `bosun morning --at-login` asks."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import awake
import login_start
import quit_state


MONDAY_NOON = datetime(2026, 11, 16, 12, 0, 0)  # a Monday
WINDOW = awake.window_from_settings("MTWRF", "08:00", "20:00")


def facts(**changes: object) -> login_start.LoginFacts:
    """A login in the hours of a scheduled day, with nothing in the way."""
    base: dict[str, object] = {
        "enabled": True,
        "window": WINDOW,
        "now": MONDAY_NOON,
        "quit": None,
        "stopped_at": None,
        "running": False,
        "reloaded_at": None,
    }
    base.update(changes)
    return login_start.LoginFacts(**base)  # type: ignore[arg-type]


class DecideTest(unittest.TestCase):
    def test_a_login_in_your_hours_starts(self) -> None:
        self.assertEqual(
            login_start.decide(facts()), (True, "starting bosun: you logged in during your hours")
        )

    def test_setting_off(self) -> None:
        self.assertEqual(
            login_start.decide(facts(enabled=False)),
            (False, "not starting: BOSUN_START_AT_LOGIN is 0"),
        )

    def test_a_reload_is_not_a_login(self) -> None:
        reloaded = MONDAY_NOON - timedelta(seconds=login_start.RELOAD_SECONDS - 1)
        self.assertEqual(
            login_start.decide(facts(reloaded_at=reloaded)),
            (False, "not starting: a reload of bosun's jobs, not a login"),
        )

    def test_stale_reload_marker_is_a_login(self) -> None:
        reloaded = MONDAY_NOON - timedelta(minutes=10)
        self.assertTrue(login_start.decide(facts(reloaded_at=reloaded))[0])

    def test_quit_with_the_morning_paused(self) -> None:
        record = quit_state.QuitState(datetime(2026, 11, 16, 9, 30, tzinfo=UTC), True)
        start, line = login_start.decide(facts(quit=record))
        self.assertFalse(start)
        self.assertEqual(
            line,
            f"not starting: bosun was quit at {quit_state.clock(record)} with the morning start paused",
        )

    def test_quit_not_paused_starts(self) -> None:
        record = quit_state.QuitState(datetime(2026, 11, 16, 9, 30, tzinfo=UTC), False)
        self.assertTrue(login_start.decide(facts(quit=record))[0])

    def test_not_a_scheduled_day(self) -> None:
        saturday = datetime(2026, 11, 14, 12, 0, 0)
        self.assertEqual(
            login_start.decide(facts(now=saturday)),
            (False, "not starting: not one of your days (MTWRF)"),
        )

    def test_outside_your_hours(self) -> None:
        late = datetime(2026, 11, 16, 21, 0, 0)
        self.assertEqual(
            login_start.decide(facts(now=late)),
            (False, "not starting: outside your hours (08:00-20:00)"),
        )

    def test_until_midnight(self) -> None:
        window = awake.window_from_settings("MTWRF", "08:00", "00:00")
        late = datetime(2026, 11, 16, 23, 30, 0)
        self.assertTrue(login_start.decide(facts(window=window, now=late))[0])

    def test_stopped_today(self) -> None:
        stopped = MONDAY_NOON - timedelta(hours=1)
        self.assertEqual(
            login_start.decide(facts(stopped_at=stopped)),
            (False, "not starting: you stopped bosun today at 11:00"),
        )

    def test_stopped_yesterday_starts(self) -> None:
        stopped = MONDAY_NOON - timedelta(days=1)
        self.assertTrue(login_start.decide(facts(stopped_at=stopped))[0])

    def test_running_already(self) -> None:
        self.assertEqual(
            login_start.decide(facts(running=True)),
            (False, "not starting: bosun is running already"),
        )

    def test_the_reasons_come_in_order(self) -> None:
        record = quit_state.QuitState(datetime(2026, 11, 14, 9, 30, tzinfo=UTC), True)
        saturday_night = datetime(2026, 11, 14, 23, 0, 0)
        everything = facts(
            enabled=False,
            reloaded_at=saturday_night,
            quit=record,
            now=saturday_night,
            stopped_at=datetime(2026, 11, 14, 22, 0, 0),
            running=True,
        )
        expected = (
            "BOSUN_START_AT_LOGIN is 0",
            "a reload of bosun's jobs",
            "with the morning start paused",
            "not one of your days",
        )
        changes: dict[str, Any] = {}
        undos: tuple[dict[str, Any], ...] = ({"enabled": True}, {"reloaded_at": None}, {"quit": None}, {})
        for text, undo in zip(expected, undos, strict=True):
            self.assertIn(text, login_start.decide(replace(everything, **changes))[1])
            changes.update(undo)

    def test_outside_hours_comes_before_stopped_and_running(self) -> None:
        late = datetime(2026, 11, 16, 21, 0, 0)
        both = facts(now=late, stopped_at=late - timedelta(hours=1), running=True)
        self.assertIn("outside your hours", login_start.decide(both)[1])
        both = facts(stopped_at=MONDAY_NOON - timedelta(hours=1), running=True)
        self.assertIn("you stopped bosun today", login_start.decide(both)[1])

    def test_friday_night_past_midnight_is_still_friday_s_hours(self) -> None:
        window = awake.window_from_settings("MTWRF", "08:00", "02:00")
        saturday_1am = datetime(2026, 11, 14, 1, 0, 0)
        self.assertTrue(login_start.decide(facts(window=window, now=saturday_1am))[0])


def run_cli(state: Path, **overrides: str) -> tuple[int, str]:
    argv = {
        "--state-dir": str(state),
        "--session": "smoke",
        "--days": "MTWRF",
        "--start": "08:00",
        "--until": "20:00",
        "--enabled": "1",
        "--running": "0",
    }
    argv.update(overrides)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = login_start.main(["decide", *(part for pair in argv.items() for part in pair)], MONDAY_NOON)
    return status, out.getvalue()


class CommandTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)

    def test_start_exits_0_and_prints_the_line(self) -> None:
        self.assertEqual(run_cli(self.state), (0, "starting bosun: you logged in during your hours\n"))

    def test_skip_exits_3(self) -> None:
        status, line = run_cli(self.state, **{"--enabled": "0"})
        self.assertEqual(status, 3)
        self.assertEqual(line, "not starting: BOSUN_START_AT_LOGIN is 0\n")

    def test_reads_the_stop_marker_of_the_session(self) -> None:
        (self.state / "stopped-smoke").write_text("2026-11-16 10:15:00\n")
        self.assertEqual(run_cli(self.state), (3, "not starting: you stopped bosun today at 10:15\n"))

    def test_another_session_s_stop_marker_is_not_read(self) -> None:
        (self.state / "stopped-other").write_text("2026-11-16 10:15:00\n")
        self.assertEqual(run_cli(self.state)[0], 0)

    def test_a_garbled_stop_marker_is_none(self) -> None:
        (self.state / "stopped-smoke").write_text("sometime\n")
        self.assertEqual(run_cli(self.state)[0], 0)

    def test_reads_the_quit_record(self) -> None:
        quit_state.write(self.state, True)
        status, line = run_cli(self.state)
        self.assertEqual(status, 3)
        self.assertIn("with the morning start paused", line)

    def test_a_quit_today_that_left_the_morning_on_logs_the_stop_it_made(self) -> None:
        # bosun quit stops bosun with `bosun stop`, which leaves the stop marker. A quit that did not pause
        # the morning start is no reason of its own, so the login that day says it was stopped, as for a stop.
        quit_state.write(self.state, False, datetime(2026, 11, 16, 10, 15).astimezone())
        (self.state / "stopped-smoke").write_text("2026-11-16 10:15:00\n")
        self.assertEqual(run_cli(self.state), (3, "not starting: you stopped bosun today at 10:15\n"))

    def test_the_reload_marker_is_read_then_removed(self) -> None:
        marker = self.state / login_start.RELOAD_MARKER
        marker.write_text(MONDAY_NOON.strftime("%Y-%m-%d %H:%M:%S\n"))
        status, line = run_cli(self.state)
        self.assertEqual((status, line), (3, "not starting: a reload of bosun's jobs, not a login\n"))
        self.assertFalse(marker.exists())
        self.assertEqual(run_cli(self.state)[0], 0)  # the next login is one

    def test_the_marker_is_removed_even_when_an_earlier_reason_wins(self) -> None:
        marker = self.state / login_start.RELOAD_MARKER
        marker.write_text(MONDAY_NOON.strftime("%Y-%m-%d %H:%M:%S\n"))
        self.assertEqual(run_cli(self.state, **{"--enabled": "0"})[0], 3)
        self.assertFalse(marker.exists())

    def test_bad_settings_are_one_line_and_exit_2(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status, _ = run_cli(self.state, **{"--days": "X"})
        self.assertEqual(status, 2)
        self.assertIn("BOSUN_DAYS has 'X'", err.getvalue())
        self.assertEqual(err.getvalue().count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
