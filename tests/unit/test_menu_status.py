"""lib/menu_status.py (pure: the level, the sentence and the lines of `bosun status --json`) and
lib/menu_status_live.py (what gathers its facts, and the command). Facts are recorded ones: the windows as
`bosun sessions --json` lists them and the watchdog's checks as `status_data` gives them. Fictional data:
acme's invoice exporter."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import awake
import keep_awake
import menu_status
import menu_status_live
import quit_state
import update_state
import watchdog
from console_live import LiveError
from menu_status import SessionGate, StatusFacts, WatchdogJob


NOW = datetime(2026, 11, 11, 10, 0, 0)  # a Wednesday, local time, inside the awake window
WORKDAYS = awake.AwakeWindow("MTWRF", time(8, 0), time(20, 0))
GONE_AT = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)


def window(name: str, status: str | None = "idle", exists: bool = True) -> dict[str, Any]:
    """A window as `bosun sessions --json` lists it."""
    agent = {"pid": 4101, "status": status, "statusUpdatedAt": 1700000000000} if status else None
    return {"name": name, "kind": "session", "window": exists, "agent": agent}


WINDOWS = [window("orch"), window("exporter"), window("checkin", None)]
GONE = [window(w["name"], exists=False) for w in WINDOWS]
EXPECTED = SessionGate(menu_status.EXPECTED)
RESTING = SessionGate(menu_status.RESTING)


def check(name: str, severity: str, *messages: str) -> dict[str, Any]:
    findings = [{"message": m, "decision": "alerts now"} for m in messages]
    return {"name": name, "severity": severity, "summary": "", "note": "", "findings": findings}


def watchdog_checks(*checks: dict[str, Any]) -> dict[str, Any]:
    """status_data's shape, with the all-well checks that <checks> do not replace."""
    names = {c["name"] for c in checks}
    quiet = [
        check(name, severity)
        for name, severity in (("session-missing", "error"), ("log-errors", "error"), ("waiting", "warn"))
        if name not in names
    ]
    return {"in_window": True, "checks": [*quiet, *checks]}


def facts(**changes: Any) -> StatusFacts:
    base: dict[str, Any] = {
        "windows": WINDOWS,
        "checks": watchdog_checks(),
        "awake": WORKDAYS,
        "quit": None,
        "log_dir": "/Users/me/.local/state/bosun/logs",
    }
    return StatusFacts(**{**base, **changes})


def snap(now: datetime = NOW, **changes: Any) -> dict[str, Any]:
    return menu_status.snapshot(facts(**changes), now)


class LevelsTestCase(unittest.TestCase):
    def test_all_well_is_ok(self) -> None:
        out = snap()
        self.assertEqual(out["contract"], 1)
        self.assertEqual((out["level"], out["sentence"]), ("ok", "bosun is running"))
        self.assertEqual(out["problems"], [])
        self.assertEqual(out["windows"], 3)
        self.assertEqual(out["detail"], ["3 windows open"])
        self.assertEqual(out["logs"], "/Users/me/.local/state/bosun/logs")

    def test_the_fields_that_are_not_set_are_present_and_empty(self) -> None:
        out = snap()
        self.assertEqual(out["waiting"], [])
        self.assertIsNone(out["keep_awake_until"])
        self.assertIsNone(out["stopped"])
        self.assertEqual(out["update"], {"state": "none", "waiting_for": [], "last": None})
        self.assertEqual(out["console"], {"running": False, "url": None})

    def test_a_warn_finding_is_a_warning(self) -> None:
        message = "The exporter window has waited at a prompt for 45 min."
        out = snap(checks=watchdog_checks(check("waiting", "warn", message)))
        self.assertEqual((out["level"], out["sentence"]), ("warning", "bosun is running, with a warning"))
        self.assertEqual(out["problems"], [message])

    def test_an_error_finding_is_an_error_even_with_the_windows_open(self) -> None:
        message = "Today's morning run reported problems."
        out = snap(checks=watchdog_checks(check("morning-failed", "error", message)))
        self.assertEqual((out["level"], out["sentence"]), ("error", "bosun is running, with an error"))
        self.assertEqual(out["problems"], [message])

    def test_the_worst_finding_decides_and_every_message_is_listed(self) -> None:
        out = snap(
            checks=watchdog_checks(
                check("waiting", "warn", "w1", "w2"), check("morning-failed", "error", "e1")
            )
        )
        self.assertEqual(out["level"], "error")
        self.assertEqual(sorted(out["problems"]), ["e1", "w1", "w2"])

    def test_no_session_that_the_gate_expects_is_an_error(self) -> None:
        out = snap(windows=GONE, gate=EXPECTED)
        self.assertEqual((out["level"], out["sentence"]), ("error", "bosun is not running"))
        self.assertEqual(out["windows"], 0)
        self.assertEqual(len(out["problems"]), 1)
        self.assertIn("not running", out["problems"][0])
        self.assertIn("bosun start", out["problems"][0])
        self.assertIsNone(out["stopped"])

    def test_the_watchdogs_own_session_finding_is_not_said_twice(self) -> None:
        message = "The tmux session 'acme' is not running. Start it with: bosun start"
        out = snap(
            windows=GONE, gate=EXPECTED, checks=watchdog_checks(check("session-missing", "error", message))
        )
        self.assertEqual((out["level"], out["problems"]), ("error", [message]))

    def test_a_session_you_stopped_today_with_bosun_stop_is_stopped_and_the_morning_is_not_paused(
        self,
    ) -> None:
        stopped_at = NOW.replace(hour=9, minute=30)
        out = snap(windows=GONE, gate=SessionGate(menu_status.STOPPED_BY_STOP, stopped_at))
        self.assertEqual(
            (out["level"], out["sentence"]), ("stopped", "bosun is stopped (bosun stop at 09:30)")
        )
        self.assertEqual(
            out["stopped"],
            {"since": stopped_at.astimezone().isoformat(timespec="seconds"), "morning_paused": False},
        )
        self.assertEqual(out["problems"], [])
        self.assertIn("You stopped bosun at 09:30", out["detail"][0])
        self.assertIn("morning run starts it again", out["detail"][1])

    def test_a_stop_does_not_hide_windows_that_are_open(self) -> None:
        out = snap(gate=SessionGate(menu_status.STOPPED_BY_STOP, NOW))
        self.assertEqual((out["level"], out["sentence"]), ("ok", "bosun is running"))
        self.assertIsNone(out["stopped"])

    def test_a_quit_beats_a_stop(self) -> None:
        out = snap(
            windows=GONE,
            gate=SessionGate(menu_status.STOPPED_BY_STOP, NOW),
            quit=quit_state.QuitState(GONE_AT, True),
        )
        self.assertEqual((out["sentence"], out["stopped"]["morning_paused"]), ("bosun is stopped", True))

    def test_no_session_outside_the_awake_window_is_ok_and_resting(self) -> None:
        out = snap(NOW.replace(hour=21), windows=GONE, gate=RESTING)
        self.assertEqual((out["level"], out["sentence"]), ("ok", "bosun is resting until 08:00"))
        self.assertEqual(out["problems"], [])

    def test_a_morning_run_that_is_still_going_is_ok_and_starting(self) -> None:
        out = snap(windows=GONE, gate=SessionGate(menu_status.STARTING))
        self.assertEqual((out["level"], out["sentence"]), ("ok", "bosun is starting (the morning run)"))
        self.assertEqual(out["problems"], [])

    def test_with_no_morning_run_today_only_the_watchdogs_findings_decide(self) -> None:
        gate = SessionGate(menu_status.NO_MORNING)
        message = "No morning run today."
        loud = snap(
            windows=GONE, gate=gate, checks=watchdog_checks(check("morning-missing", "error", message))
        )
        self.assertEqual(
            (loud["level"], loud["sentence"], loud["problems"]), ("error", "bosun is not running", [message])
        )
        mild = snap(
            windows=GONE, gate=gate, checks=watchdog_checks(check("morning-missing", "warn", message))
        )
        self.assertEqual(mild["level"], "warning")

    def test_resting_over_a_weekend_waits_for_the_next_scheduled_day(self) -> None:
        friday_night = datetime(2026, 11, 13, 21, 0)
        out = snap(
            friday_night,
            windows=GONE,
            gate=RESTING,
            awake=awake.AwakeWindow("MTWRF", time(7, 30), time(20, 0)),
        )
        self.assertEqual(out["sentence"], "bosun is resting until 07:30")

    def test_resting_before_the_start_on_a_scheduled_day_is_until_today(self) -> None:
        out = snap(NOW.replace(hour=6), windows=GONE, gate=RESTING)
        self.assertEqual(out["sentence"], "bosun is resting until 08:00")

    def test_resting_with_a_warning_says_so(self) -> None:
        out = snap(
            NOW.replace(hour=21),
            windows=GONE,
            gate=RESTING,
            checks=watchdog_checks(check("waiting", "warn", "w1")),
        )
        self.assertEqual(
            (out["level"], out["sentence"]), ("warning", "bosun is resting until 08:00, with a warning")
        )

    def test_an_error_while_resting_is_still_an_error(self) -> None:
        out = snap(
            NOW.replace(hour=21),
            windows=GONE,
            gate=RESTING,
            checks=watchdog_checks(check("morning-failed", "error", "e1")),
        )
        self.assertEqual(
            (out["level"], out["sentence"]), ("error", "bosun is resting until 08:00, with an error")
        )

    def test_a_morning_run_that_failed_is_never_starting(self) -> None:
        # A crashed or hung run keeps outcome "running": the gate says it is going, morning-failed says not
        gate = SessionGate(menu_status.STARTING)
        out = snap(
            windows=GONE,
            gate=gate,
            checks=watchdog_checks(check("morning-failed", "error", "The morning run did not finish.")),
        )
        self.assertEqual(
            (out["level"], out["sentence"]), ("error", "bosun is not running: the morning run failed")
        )
        other = snap(windows=GONE, gate=gate, checks=watchdog_checks(check("waiting", "error", "e1")))
        self.assertEqual((other["level"], other["sentence"]), ("error", "bosun is not running"))
        mild = snap(windows=GONE, gate=gate, checks=watchdog_checks(check("waiting", "warn", "w1")))
        self.assertEqual(mild["sentence"], "bosun is starting (the morning run), with a warning")

    def test_a_gate_that_could_not_be_told_is_said_by_the_part_that_failed(self) -> None:
        out = snap(windows=GONE, gate=None, checks=None, errors={"watchdog": "watchdog --facts exited 1"})
        self.assertEqual((out["level"], out["sentence"]), ("warning", "bosun is not running"))

    def test_a_window_that_is_configured_but_not_open_does_not_count(self) -> None:
        out = snap(windows=[window("orch"), window("exporter", exists=False)])
        self.assertEqual(out["windows"], 1)
        self.assertEqual(out["detail"], ["1 window open"])


class StoppedTestCase(unittest.TestCase):
    def test_a_quit_is_stopped_whatever_the_windows_and_checks_say(self) -> None:
        out = snap(
            windows=GONE,
            quit=quit_state.QuitState(GONE_AT, False),
            checks=watchdog_checks(check("morning-failed", "error", "e1")),
        )
        self.assertEqual((out["level"], out["sentence"]), ("stopped", "bosun is stopped"))
        self.assertEqual(out["stopped"], {"since": "2026-11-12T12:00:00+00:00", "morning_paused": False})
        self.assertEqual(out["problems"], [])
        self.assertEqual(
            out["detail"], [f"You quit bosun at {quit_state.clock(quit_state.QuitState(GONE_AT, False))}"]
        )

    def test_a_paused_morning_is_in_the_detail_and_the_fields(self) -> None:
        out = snap(windows=GONE, quit=quit_state.QuitState(GONE_AT, True))
        self.assertTrue(out["stopped"]["morning_paused"])
        self.assertEqual(len(out["detail"]), 2)
        self.assertIn("morning start is paused", out["detail"][1])

    def test_a_quit_record_that_cannot_be_read_says_so_and_how_to_clear_it(self) -> None:
        out = snap(windows=GONE, quit=quit_state.QuitState(GONE_AT, True, readable=False))
        self.assertEqual(out["level"], "stopped")
        self.assertEqual(len(out["problems"]), 1)
        for text in (out["problems"][0], out["detail"][-1]):
            self.assertIn("quit record", text)
            self.assertIn("unreadable", text)
            self.assertIn("bosun start", text)

    def test_a_quit_record_with_windows_still_open_is_a_quit_that_did_not_finish(self) -> None:
        out = snap(quit=quit_state.QuitState(GONE_AT, False), windows=WINDOWS)
        self.assertEqual((out["level"], out["sentence"]), ("error", "bosun quit did not finish"))
        self.assertEqual(out["windows"], 3)
        self.assertEqual(len(out["problems"]), 1)
        for text in (out["problems"][0],):
            self.assertIn("3 windows still open", text)
            self.assertIn("bosun quit --stop-now", text)
            self.assertIn("bosun start", text)
        self.assertEqual(out["stopped"], {"since": "2026-11-12T12:00:00+00:00", "morning_paused": False})

    def test_one_window_open_after_a_quit_says_so_in_the_singular(self) -> None:
        out = snap(quit=quit_state.QuitState(GONE_AT, True), windows=[window("orch")])
        self.assertIn("1 window still open", out["problems"][0])

    def test_a_quit_record_whose_windows_could_not_be_read_is_a_warning_that_still_says_it_is_stopped(
        self,
    ) -> None:
        out = snap(quit=quit_state.QuitState(GONE_AT, False), windows=None, errors={"sessions": "x"})
        self.assertEqual(out["level"], "warning")
        self.assertEqual(out["sentence"], "bosun is stopped, with a warning")
        self.assertEqual(out["stopped"], {"since": "2026-11-12T12:00:00+00:00", "morning_paused": False})
        self.assertEqual(out["problems"], ["could not read the windows: x"])

    def test_a_stop_with_a_part_that_could_not_be_read_is_a_warning_too(self) -> None:
        gate = SessionGate(menu_status.STOPPED_BY_STOP, NOW.replace(hour=9, minute=30))
        out = snap(windows=GONE, gate=gate, checks=None, errors={"watchdog": "watchdog --facts exited 1"})
        self.assertEqual(
            (out["level"], out["sentence"]),
            ("warning", "bosun is stopped (bosun stop at 09:30), with a warning"),
        )
        self.assertIsNotNone(out["stopped"])
        self.assertIn("could not read the watchdog's checks", out["problems"][0])

    def test_a_stopped_bosun_with_everything_readable_stays_stopped(self) -> None:
        quit_out = snap(windows=GONE, quit=quit_state.QuitState(GONE_AT, False))
        stop_out = snap(windows=GONE, gate=SessionGate(menu_status.STOPPED_BY_STOP, NOW))
        for out in (quit_out, stop_out):
            self.assertEqual((out["level"], out["problems"]), ("stopped", []))

    def test_an_unread_part_does_not_turn_a_quit_that_did_not_finish_into_a_warning(self) -> None:
        out = snap(quit=quit_state.QuitState(GONE_AT, False), windows=WINDOWS, errors={"logs": "x"})
        self.assertEqual(out["level"], "error")


class StateTestCase(unittest.TestCase):
    def test_an_update_running_is_still_ok_and_names_the_windows_it_waits_for(self) -> None:
        out = snap(update_running=True, update_waiting_for=("exporter", "docs"))
        self.assertEqual((out["level"], out["sentence"]), ("ok", "bosun is running"))
        self.assertEqual(
            out["update"], {"state": "running", "waiting_for": ["exporter", "docs"], "last": None}
        )
        self.assertIn("An update is running, waiting for exporter, docs", out["detail"])

    def test_an_update_that_waits_for_nothing(self) -> None:
        out = snap(update_running=True)
        self.assertEqual(out["update"], {"state": "running", "waiting_for": [], "last": None})
        self.assertIn("An update is running", out["detail"])

    def test_the_record_the_last_update_left_is_told_after_it_ended(self) -> None:
        last = menu_status.LastUpdate(id="u1", started_at="2026-11-11T09:59:30+00:00", step="done")
        out = snap(update_last=last)
        self.assertEqual(
            out["update"],
            {
                "state": "none",
                "waiting_for": [],
                "last": {"id": "u1", "started_at": "2026-11-11T09:59:30+00:00", "step": "done"},
            },
        )
        self.assertEqual(out["level"], "ok")
        self.assertNotIn("An update is running", out["detail"])

    def test_windows_waiting_at_a_prompt_are_named_and_counted(self) -> None:
        windows = [window("orch"), window("exporter", "waiting"), window("docs", "waiting")]
        out = snap(windows=windows)
        self.assertEqual(sorted(out["waiting"]), ["docs", "exporter"])
        self.assertIn("2 waiting for you", out["detail"][1])
        self.assertEqual(out["level"], "ok")

    def test_keep_awake_is_shown_as_the_local_time(self) -> None:
        out = snap(keep_awake_until=NOW.replace(hour=20, minute=5))
        self.assertEqual(out["keep_awake_until"], "20:05")
        self.assertIn("Mac kept awake until 20:05", out["detail"])

    def test_a_keep_awake_that_has_ended_is_not_shown(self) -> None:
        out = snap(keep_awake_until=NOW - timedelta(minutes=1))
        self.assertIsNone(out["keep_awake_until"])

    def test_the_console_running_and_its_url_never_there(self) -> None:
        self.assertEqual(snap(console_running=True)["console"], {"running": True, "url": None})


class UnreadFactsTestCase(unittest.TestCase):
    def test_windows_that_could_not_be_read_are_a_problem_and_a_warning(self) -> None:
        out = snap(windows=None, errors={"sessions": "sessions --json exited 1: boom"})
        self.assertEqual(out["level"], "warning")
        self.assertEqual(out["sentence"], "bosun's windows could not be read")
        self.assertEqual(out["windows"], 0)
        self.assertEqual(out["waiting"], [])
        self.assertIn("boom", out["problems"][0])

    def test_watchdog_checks_that_could_not_be_read_are_a_warning_not_all_well(self) -> None:
        out = snap(checks=None, errors={"watchdog": "watchdog --facts exited 1: tmux is gone"})
        self.assertEqual((out["level"], out["sentence"]), ("warning", "bosun is running, with a warning"))
        self.assertIn("tmux is gone", out["problems"][0])

    def test_an_unread_part_does_not_hide_an_error_from_the_others(self) -> None:
        out = snap(
            windows=None,
            errors={"sessions": "boom"},
            checks=watchdog_checks(check("morning-failed", "error", "e1")),
        )
        self.assertEqual(out["level"], "error")
        self.assertEqual(len(out["problems"]), 2)

    def test_without_the_awake_hours_no_session_is_not_called_an_error(self) -> None:
        out = snap(windows=GONE, awake=None, errors={"config": "BOSUN_START_TIME must be HH:MM"})
        self.assertEqual((out["level"], out["sentence"]), ("warning", "bosun is not running"))


class LogErrorsTestCase(unittest.TestCase):
    LINES = ("check-in.log: [checkin] ERROR: the ledger is busy",)

    def test_an_error_line_in_the_last_hour_is_a_warning(self) -> None:
        out = snap(log_errors=self.LINES)
        self.assertEqual((out["level"], out["sentence"]), ("warning", "bosun is running, with a warning"))
        self.assertEqual(len(out["problems"]), 1)
        self.assertIn("1 ERROR line in the bosun logs in the last hour", out["problems"][0])
        self.assertIn("the ledger is busy", out["problems"][0])

    def test_many_lines_are_counted_and_only_the_first_few_shown(self) -> None:
        lines = tuple(f"check-in.log: [checkin] ERROR: step {n}" for n in range(8))
        text = snap(log_errors=lines)["problems"][0]
        self.assertIn("8 ERROR lines", text)
        self.assertIn("step 0", text)
        self.assertNotIn("step 7", text)

    def test_the_watchdogs_own_log_finding_is_not_said_twice(self) -> None:
        message = "2 new ERROR line(s) in the bosun logs: check-in.log: [checkin] ERROR: x"
        out = snap(log_errors=self.LINES, checks=watchdog_checks(check("log-errors", "error", message)))
        self.assertEqual((out["level"], out["problems"]), ("error", [message]))


KEYS = [
    "contract",
    "level",
    "sentence",
    "detail",
    "windows",
    "running",
    "waiting",
    "keep_awake_until",
    "stopped",
    "update",
    "console",
    "problems",
    "logs",
]


class JsonTestCase(unittest.TestCase):
    def test_every_level_serialises_to_the_documented_keys(self) -> None:
        for out in (
            snap(),
            snap(windows=GONE, quit=quit_state.QuitState(GONE_AT, True)),
            snap(quit=quit_state.QuitState(GONE_AT, True)),
            snap(windows=None, checks=None, errors={"sessions": "x"}),
        ):
            data = json.loads(json.dumps(out))
            self.assertEqual(
                sorted(data),
                sorted(KEYS),
            )

    def test_unreadable_configuration_is_an_error_with_the_reason(self) -> None:
        out = menu_status.unreadable_config("bosun.env line 3 is not a setting")
        self.assertEqual((out["contract"], out["level"]), (1, "error"))
        self.assertEqual(out["problems"], ["bosun.env line 3 is not a setting"])
        self.assertEqual(sorted(out), sorted(KEYS))
        self.assertIsNone(out["running"])  # not known
        json.dumps(out)


class RunningTestCase(unittest.TestCase):
    """`running`: whether bosun's windows are open, so the app tells a bosun to start from one that runs
    with an error; null when the windows could not be read."""

    def test_open_windows_are_running_whatever_the_level(self) -> None:
        self.assertIs(snap()["running"], True)
        out = snap(checks=watchdog_checks(check("morning-failed", "error", "e1")))
        self.assertEqual((out["level"], out["running"]), ("error", True))

    def test_no_window_open_is_not_running(self) -> None:
        for out in (
            snap(windows=GONE, gate=EXPECTED),
            snap(windows=GONE, quit=quit_state.QuitState(GONE_AT, True)),
            snap(windows=[]),
        ):
            with self.subTest(level=out["level"]):
                self.assertIs(out["running"], False)

    def test_windows_that_could_not_be_read_are_not_known(self) -> None:
        out = snap(windows=None, errors={"sessions": "tmux did not answer"})
        self.assertIsNone(out["running"])
        self.assertEqual(out["windows"], 0)

    def test_a_quit_that_did_not_finish_still_runs(self) -> None:
        out = snap(quit=quit_state.QuitState(GONE_AT, False))
        self.assertEqual((out["level"], out["running"]), ("error", True))
        self.assertIsNotNone(out["stopped"])


class KeepAwakeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.file = self.state / "keep-awake-acme.json"

    def read(self, alive: bool = True) -> datetime | None:
        return keep_awake.keep_awake_until(self.state, "acme", lambda _pid: alive)

    def test_absent_is_none(self) -> None:
        self.assertIsNone(self.read())

    def test_the_time_of_a_live_hold_as_local_time(self) -> None:
        self.file.write_text('{"until": "2026-11-11T20:00:00+0200", "pid": 4242}\n')
        read = self.read()
        assert read is not None
        self.assertIsNone(read.tzinfo)
        local = datetime.fromisoformat("2026-11-11T20:00:00+02:00").astimezone().replace(tzinfo=None)
        self.assertEqual(read, local)

    def test_a_pid_that_is_gone_is_none(self) -> None:
        self.file.write_text('{"until": "2026-11-11T20:00:00+0200", "pid": 4242}\n')
        self.assertIsNone(self.read(alive=False))

    def test_the_pid_is_the_one_asked_about(self) -> None:
        self.file.write_text('{"until": "2026-11-11T20:00:00+0200", "pid": 4242}\n')
        asked: list[int] = []

        def alive(pid: int) -> bool:
            asked.append(pid)
            return True

        keep_awake.keep_awake_until(self.state, "acme", alive)
        self.assertEqual(asked, [4242])

    def test_a_record_that_cannot_be_read_is_none(self) -> None:
        for text in (
            "{",
            "[]",
            '{"until": "soon", "pid": 1}',
            '{"until": "2026-11-11T20:00:00"}',
            '{"pid": 1}',
            '{"until": "2026-11-11T20:00:00+0200", "pid": "x"}',
            '{"until": 5, "pid": 1}',
        ):
            with self.subTest(text=text):
                self.file.write_text(text)
                self.assertIsNone(self.read())

    def test_the_real_check_knows_this_process_is_not_caffeinate(self) -> None:
        self.assertFalse(keep_awake.is_caffeinate(os.getpid()))
        self.assertFalse(keep_awake.is_caffeinate(2**22 + 12345))


class RecentLogErrorsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.logs = Path(tmp.name)

    def write(self, name: str, *lines: str) -> None:
        (self.logs / name).write_text("\n".join(lines) + "\n")

    def read(self) -> tuple[str, ...]:
        return menu_status_live.recent_log_errors(self.logs, NOW)

    def test_only_error_lines_of_the_last_hour(self) -> None:
        self.write(
            "check-in.log",
            "2026-11-11 08:30:00 [checkin] ERROR: too old",
            "2026-11-11 09:30:00 [checkin] ERROR: the ledger is busy",
            "2026-11-11 09:40:00 [checkin] sent the check-in",
            "2026-11-11 09:50:00 [checkin] FAIL: step two",
        )
        self.assertEqual(
            self.read(),
            ("check-in.log: [checkin] ERROR: the ledger is busy", "check-in.log: [checkin] FAIL: step two"),
        )

    def test_the_logs_the_watchdog_leaves_out_and_the_commands_it_does_not_alert_about(self) -> None:
        line = "2026-11-11 09:55:00 [checkin] ERROR: x"
        self.write("morning-2026-11-11.log", line)
        self.write("watchdog.log", line)
        self.write("launchd.err", "2026-11-11 09:55:00 [learn] ERROR: its own failure")
        self.assertEqual(self.read(), ())

    def test_lines_without_a_time_are_skipped_and_do_not_end_the_scan(self) -> None:
        self.write(
            "check-in.log",
            "2026-11-11 09:30:00 [checkin] ERROR: first",
            "  a second line of the same message",
            "2026-11-11 09:31:00 [checkin] ok",
        )
        self.assertEqual(self.read(), ("check-in.log: [checkin] ERROR: first",))

    def test_a_log_not_written_for_two_hours_is_not_read(self) -> None:
        self.write("check-in.log", "2026-11-11 09:55:00 [checkin] ERROR: x")
        old = (NOW - timedelta(hours=2)).timestamp()
        os.utime(self.logs / "check-in.log", (old, old))
        self.assertEqual(self.read(), ())

    def test_no_logs_folder_is_no_errors(self) -> None:
        self.assertEqual(menu_status_live.recent_log_errors(self.logs / "nope", NOW), ())

    def test_nothing_is_written_or_changed(self) -> None:
        self.write("check-in.log", "2026-11-11 09:55:00 [checkin] ERROR: x")
        before = {p.name: p.stat().st_mtime for p in self.logs.iterdir()}
        self.read()
        self.assertEqual({p.name: p.stat().st_mtime for p in self.logs.iterdir()}, before)


def environment(state: Path) -> dict[str, str]:
    return {
        "BOSUN_STATE_DIR": str(state),
        "BOSUN_LOG_DIR": str(state / "logs"),
        "BOSUN_SESSION_NAME": "acme",
        "BOSUN_ORCHESTRATOR": "orch",
        "BOSUN_DAYS": "MTWRFSU",
        "BOSUN_START_TIME": "08:00",
        "BOSUN_WAKE_DELAY_MINUTES": "1",
        "BOSUN_AWAKE_UNTIL": "20:00",
        "BOSUN_ALERT_WAITING_MINUTES": "30",
        "BOSUN_CHECKIN_MINUTES": "60",
        "BOSUN_WATCHDOG_GRACE_MINUTES": "15",
        "BOSUN_WATCHDOG_DELEGATED_HOURS": "8",
        "BOSUN_LEARN_ENABLED": "0",
        "BOSUN_LEARN_AT": "18:30",
        "BOSUN_LEARN_TIMEOUT_MINUTES": "30",
        "BOSUN_EVENTS_ENABLED": "0",
        "BOSUN_EVENTS_INTERVAL_SECONDS": "30",
        "BOSUN_SLACK_WATCH_ENABLED": "0",
        "BOSUN_SLACK_WATCH_MINUTES": "15",
        "BOSUN_CLAUDE_BIN": "/nonexistent/claude",
        "BOSUN_TMUX_SOCKET": "acme",
    }


WATCHDOG_FACTS = {
    "session_exists": True,
    "windows": [{"name": "orch", "agent_pid": 4101}],
    "checkin_window": True,
    "checkin_loop": True,
    "morning_job": {"state": "not running", "last_exit_code": "0"},
}


class FakeCommands:
    def __init__(self, outputs: dict[str, str]) -> None:
        self.outputs = outputs
        self.ran: list[str] = []

    def __call__(self, argv: list[str]) -> str:
        key = " ".join(argv[1:])
        self.ran.append(key)
        if key not in self.outputs:
            raise LiveError(f"{key} exited 1: boom")
        return self.outputs[key]


def good_commands() -> FakeCommands:
    return FakeCommands(
        {"sessions --json": json.dumps(WINDOWS), "watchdog --facts": json.dumps(WATCHDOG_FACTS)}
    )


class GatherTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        (self.state / "logs").mkdir()
        self.env = environment(self.state)

    def gather(self, run: FakeCommands | None = None) -> StatusFacts:
        return menu_status_live.gather(self.env, NOW, run or good_commands(), kernel_time=lambda _name: None)

    def test_a_quiet_system(self) -> None:
        found = self.gather()
        self.assertEqual(found.windows, WINDOWS)
        self.assertIsNotNone(found.checks)
        self.assertIsNone(found.quit)
        self.assertFalse(found.update_running or found.console_running)
        self.assertEqual((found.update_waiting_for, found.log_errors, found.errors), ((), (), {}))
        self.assertEqual(found.awake, awake.AwakeWindow("MTWRFSU", time(8, 0), time(20, 0)))
        self.assertEqual(found.log_dir, str(self.state / "logs"))

    def test_it_runs_only_the_two_readers_the_console_uses(self) -> None:
        run = good_commands()
        self.gather(run)
        self.assertEqual(sorted(run.ran), ["sessions --json", "watchdog --facts"])

    def test_the_quit_the_update_and_the_logs_are_read_from_the_state_folder(self) -> None:
        quit_state.write(self.state, True, now=GONE_AT)
        (self.state / "logs" / "check-in.log").write_text("2026-11-11 09:55:00 [checkin] ERROR: x\n")
        found = self.gather()
        self.assertEqual(found.quit, quit_state.QuitState(GONE_AT, True))
        self.assertEqual(found.log_errors, ("check-in.log: [checkin] ERROR: x",))

    def test_the_gate_is_the_watchdogs_own_judgement_of_a_missing_session(self) -> None:
        # What session_gate_kind says at each point of the day, as a fact: never the text of its reason.
        def at(hour: int, minute: int = 0) -> datetime:
            return NOW.replace(hour=hour, minute=minute)

        header = (
            "2026-11-11 08:00:30 [morning] === morning run (trigger=launchd, session=acme, dry_run=0) ==="
        )
        log = self.state / "logs" / "morning-2026-11-11.log"

        def gate(moment: datetime) -> SessionGate | None:
            return menu_status_live.gather(self.env, moment, good_commands()).gate

        self.assertEqual(gate(at(21)), SessionGate(menu_status.RESTING))
        # the window began, the morning is not due yet (08:16): starting, not resting until a past time
        self.assertEqual(gate(at(8, 5)), SessionGate(menu_status.STARTING))
        self.assertEqual(gate(at(10)), SessionGate(menu_status.NO_MORNING))
        log.write_text(header + "\n")
        self.assertEqual(gate(at(10)), SessionGate(menu_status.STARTING))
        self.assertEqual(gate(at(8, 5)), SessionGate(menu_status.STARTING))  # going, though not yet due
        log.write_text(header + "\n2026-11-11 08:03:00 [morning] morning run complete\n")
        self.assertEqual(gate(at(10)), SessionGate(menu_status.EXPECTED))
        (self.state / "stopped-acme").write_text("2026-11-11 09:30:00\n")
        self.assertEqual(gate(at(10)), SessionGate(menu_status.STOPPED_BY_STOP, at(9, 30)))
        (self.state / "stopped-acme").write_text("2026-11-10 09:30:00\n")  # yesterday's stop is over
        self.assertEqual(gate(at(10)), SessionGate(menu_status.EXPECTED))

    def test_a_morning_that_crashed_is_not_called_starting(self) -> None:
        # No end line, the launchd job not running with exit 1: outcome stays "running"
        header = (
            "2026-11-11 08:00:30 [morning] === morning run (trigger=launchd, session=acme, dry_run=0) ==="
        )
        (self.state / "logs" / "morning-2026-11-11.log").write_text(header + "\n")
        facts_json = {**WATCHDOG_FACTS, "session_exists": False, "windows": []}
        facts_json["morning_job"] = {"state": "not running", "last_exit_code": "1"}
        gone = [window(w["name"], exists=False) for w in WINDOWS]
        run = FakeCommands({"sessions --json": json.dumps(gone), "watchdog --facts": json.dumps(facts_json)})
        found = self.gather(run)
        self.assertEqual(found.gate, SessionGate(menu_status.STARTING))
        out = menu_status.snapshot(found, NOW)
        self.assertEqual(out["level"], "error")
        self.assertEqual(out["sentence"], "bosun is not running: the morning run failed")

    def test_the_state_is_read_once_and_an_unreadable_one_is_a_problem(self) -> None:
        (self.state / "watchdog.json").write_text("")
        loads: list[Path] = []
        real = watchdog.State.load.__func__  # type: ignore[attr-defined]

        def counting(cls: type[watchdog.State], state_dir: Path) -> watchdog.State:
            loads.append(state_dir)
            return real(cls, state_dir)  # type: ignore[no-any-return]

        said = io.StringIO()
        with (
            mock.patch.object(watchdog.State, "load", classmethod(counting)),
            contextlib.redirect_stdout(said),
        ):
            found = self.gather()
        self.assertEqual(len(loads), 1)
        self.assertEqual(said.getvalue(), "")  # what it said was caught, not printed
        self.assertIn("watchdog.json", found.errors["watchdog-state"])
        self.assertIn("starting afresh", found.errors["watchdog-state"])
        self.assertIsNotNone(found.checks)
        self.assertIsNotNone(found.gate)
        out = menu_status.snapshot(found, NOW)
        self.assertNotEqual(out["level"], "ok")
        self.assertIn("the watchdog's saved state", " ".join(out["problems"]))

    def test_facts_that_are_not_facts_are_a_problem_not_a_pass(self) -> None:
        for text in ("null", "{}", "[]", '"x"'):
            with self.subTest(text=text):
                run = FakeCommands({"sessions --json": json.dumps(WINDOWS), "watchdog --facts": text})
                found = self.gather(run)
                self.assertIn("watchdog --facts", found.errors["watchdog"])
                self.assertIsNone(found.checks)
        for text in ("null", "{}", '"x"'):
            with self.subTest(sessions=text):
                run = FakeCommands({"sessions --json": text, "watchdog --facts": json.dumps(WATCHDOG_FACTS)})
                found = self.gather(run)
                self.assertIsNone(found.windows)
                self.assertIn("sessions --json", found.errors["sessions"])

    def test_without_the_checks_there_is_no_gate(self) -> None:
        self.assertIsNone(self.gather(FakeCommands({"sessions --json": json.dumps(WINDOWS)})).gate)
        self.env["BOSUN_START_TIME"] = "soon"
        self.assertIsNone(self.gather().gate)

    def test_a_reader_that_fails_leaves_its_part_empty_and_says_why(self) -> None:
        found = self.gather(FakeCommands({"watchdog --facts": json.dumps(WATCHDOG_FACTS)}))
        self.assertIsNone(found.windows)
        self.assertIn("boom", found.errors["sessions"])
        self.assertIsNotNone(found.checks)
        found = self.gather(FakeCommands({}))
        self.assertEqual((found.windows, found.checks), (None, None))
        self.assertEqual(sorted(found.errors), ["sessions", "watchdog"])

    def test_settings_that_do_not_load_are_said_and_the_rest_still_read(self) -> None:
        self.env["BOSUN_START_TIME"] = "soon"
        found = self.gather()
        self.assertIsNone(found.awake)
        self.assertIn("BOSUN_START_TIME", found.errors["config"])
        self.assertEqual(found.windows, WINDOWS)

    def test_an_update_waits_for_the_windows_its_record_names(self) -> None:
        record = update_state.UpdateState(
            id="u1",
            from_root="/a",
            to_root="/b",
            from_commit="1",
            to_commit="2",
            step="agents",
            started_at="2026-11-11T09:00:00",
            hold_until=0,
            pending=("exporter",),
        )
        update_state.write(self.state, record)
        with update_state.one_update(self.state):
            found = self.gather()
        self.assertTrue(found.update_running)
        self.assertEqual(found.update_waiting_for, ("exporter",))
        self.assertEqual(found.update_last, menu_status.LastUpdate("u1", "2026-11-11T09:00:00", "agents"))
        # the record of an update that is not running names nobody to wait for, and is still told
        after = self.gather()
        self.assertEqual(after.update_waiting_for, ())
        self.assertEqual(after.update_last, menu_status.LastUpdate("u1", "2026-11-11T09:00:00", "agents"))

    def test_no_record_or_one_that_cannot_be_read_tells_no_last_update(self) -> None:
        self.assertIsNone(self.gather().update_last)
        (self.state / update_state.STATE_FILE).write_text("{not json")
        found = self.gather()
        self.assertIsNone(found.update_last)
        self.assertEqual(found.errors, {})

    def test_an_unreadable_state_folder_part_does_not_stop_the_rest(self) -> None:
        (self.state / "logs").rmdir()
        found = self.gather()
        self.assertEqual(found.log_errors, ())
        self.assertEqual(found.windows, WINDOWS)


class CommandTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        (self.state / "logs").mkdir()
        self.env = environment(self.state)

    def run_main(self, *argv: str, run: Any = None) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = menu_status_live.main(list(argv), self.env, run or good_commands(), NOW)
        return status, out.getvalue()

    def test_it_prints_one_json_object_with_the_contract_and_exits_0(self) -> None:
        status, text = self.run_main()
        self.assertEqual(status, 0)
        self.assertEqual(text.count("\n"), 1)
        data = json.loads(text)
        self.assertEqual(data["contract"], 1)
        self.assertEqual(data["windows"], 3)
        self.assertIsNone(data["console"]["url"])

    def test_what_is_printed_while_gathering_does_not_reach_stdout(self) -> None:
        (self.state / "watchdog.json").write_text("")  # the watchdog says so, and starts afresh

        def noisy(argv: list[str]) -> str:
            print("[bosun] noise from a reader")
            return good_commands()(argv)

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status, text = self.run_main(run=noisy)
        data = json.loads(text)  # one object, nothing before it
        self.assertEqual((status, text.count("\n")), (0, 1))
        self.assertIn("watchdog.json", " ".join(data["problems"]))
        self.assertIn(data["level"], ("warning", "error"))
        self.assertIn("noise from a reader", err.getvalue())

    def test_nothing_readable_still_prints_the_object(self) -> None:
        status, text = self.run_main(run=FakeCommands({}))
        data = json.loads(text)
        self.assertEqual((status, data["contract"], data["level"]), (0, 1, "warning"))
        self.assertEqual(len(data["problems"]), 2)

    def test_a_crash_inside_is_an_object_not_a_traceback(self) -> None:
        def crash(_argv: list[str]) -> str:
            raise RuntimeError("something nobody planned for")

        status, text = self.run_main(run=crash)
        data = json.loads(text)
        self.assertEqual((status, data["contract"]), (0, 1))
        self.assertNotEqual(data["level"], "ok")
        self.assertIn("something nobody planned for", " ".join(data["problems"]))

    def test_a_configuration_that_could_not_be_read_is_the_object_too(self) -> None:
        status, text = self.run_main("--config-error", "bosun.env line 3 is not a setting")
        data = json.loads(text)
        self.assertEqual((status, data["level"]), (0, "error"))
        self.assertEqual(data["problems"], ["bosun.env line 3 is not a setting"])

    def test_an_argument_it_does_not_know_is_a_usage_error(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            self.run_main("--nope")
        self.assertEqual(raised.exception.code, 2)


class WatchdogJobTestCase(unittest.TestCase):
    """The watchdog itself not running, which none of its own checks can say."""

    def test_the_interval_is_the_one_install_sh_gives_the_job(self) -> None:
        text = (Path(__file__).resolve().parents[2] / "lib" / "common.sh").read_text()
        self.assertIn(f"\nWATCHDOG_INTERVAL_SECONDS={menu_status.WATCHDOG_INTERVAL_SECONDS}\n", text)

    def test_a_job_that_is_installed_and_not_loaded_is_a_warning_that_says_so(self) -> None:
        out = snap(watchdog_job=WatchdogJob(installed=True, loaded=False))
        self.assertEqual((out["level"], out["sentence"]), ("warning", "bosun is running, with a warning"))
        self.assertEqual(len(out["problems"]), 1)
        self.assertIn("watchdog job is not loaded", out["problems"][0])
        self.assertIn("install.sh", out["problems"][0])

    def test_a_job_that_last_ran_more_than_three_intervals_ago_is_a_warning_with_the_time(self) -> None:
        out = snap(watchdog_job=WatchdogJob(True, True, NOW - timedelta(minutes=16)))
        self.assertEqual(out["level"], "warning")
        self.assertIn("last ran at 09:44", out["problems"][0])
        self.assertIn("every 5 minutes", out["problems"][0])
        self.assertIn("/Users/me/.local/state/bosun/logs", out["problems"][0])

    def test_a_run_of_another_day_says_the_day(self) -> None:
        out = snap(watchdog_job=WatchdogJob(True, True, NOW - timedelta(days=2)))
        self.assertIn("last ran at 2026-11-09 10:00", out["problems"][0])

    def test_a_job_that_ran_within_three_intervals_is_fine(self) -> None:
        for minutes in (0, 5, 14, 15):
            with self.subTest(minutes=minutes):
                out = snap(watchdog_job=WatchdogJob(True, True, NOW - timedelta(minutes=minutes)))
                self.assertEqual((out["level"], out["problems"]), ("ok", []))

    def test_a_loaded_job_that_never_ran_is_a_new_one(self) -> None:
        self.assertEqual(snap(watchdog_job=WatchdogJob(True, True, None))["level"], "ok")

    def test_no_job_installed_is_no_problem_as_in_a_checkout(self) -> None:
        for job in (WatchdogJob(False, False), WatchdogJob(False, False, NOW - timedelta(days=9)), None):
            with self.subTest(job=job):
                self.assertEqual(snap(watchdog_job=job)["level"], "ok")

    def test_an_update_that_holds_the_watchdog_is_not_a_stale_one(self) -> None:
        job = WatchdogJob(True, True, NOW - timedelta(minutes=40))
        self.assertEqual(snap(watchdog_job=job, update_running=True)["level"], "ok")

    def test_it_does_not_hide_a_worse_finding_and_is_listed_after_the_checks(self) -> None:
        out = snap(
            watchdog_job=WatchdogJob(True, False),
            checks=watchdog_checks(check("morning-failed", "error", "e1")),
        )
        self.assertEqual(out["level"], "error")
        self.assertEqual(out["problems"][0], "e1")
        self.assertIn("watchdog job is not loaded", out["problems"][1])

    def test_after_a_quit_the_loaded_job_that_skips_is_stopped_not_yellow(self) -> None:
        stale = WatchdogJob(True, True, NOW - timedelta(hours=3))
        out = snap(windows=GONE, quit=quit_state.QuitState(GONE_AT, True), watchdog_job=stale)
        self.assertEqual((out["level"], out["problems"]), ("stopped", []))
        gone = WatchdogJob(True, False)
        out = snap(windows=GONE, quit=quit_state.QuitState(GONE_AT, True), watchdog_job=gone)
        self.assertEqual((out["level"], out["problems"]), ("stopped", []))

    def test_a_stop_of_today_is_stopped_too(self) -> None:
        gate = SessionGate(menu_status.STOPPED_BY_STOP, NOW - timedelta(hours=1))
        out = snap(windows=GONE, gate=gate, watchdog_job=WatchdogJob(True, False))
        self.assertEqual((out["level"], out["problems"]), ("stopped", []))


class WatchdogJobFactsTestCase(unittest.TestCase):
    JOB: ClassVar[dict[str, Any]] = {"state": "not running", "installed": True}

    def test_a_loaded_job_and_the_last_run_its_state_records(self) -> None:
        job = menu_status_live.watchdog_job_of(
            {"watchdog_job": self.JOB}, {"last_run": "2026-11-11 09:55:00"}
        )
        self.assertEqual(job, WatchdogJob(True, True, datetime(2026, 11, 11, 9, 55, 0)))

    def test_no_state_is_not_loaded(self) -> None:
        job = menu_status_live.watchdog_job_of({"watchdog_job": {"state": "", "installed": True}}, None)
        self.assertEqual(job, WatchdogJob(True, False, None))

    def test_no_plist_is_not_installed(self) -> None:
        job = menu_status_live.watchdog_job_of({"watchdog_job": {"state": "", "installed": False}}, {})
        self.assertEqual(job, WatchdogJob(False, False, None))

    def test_facts_that_do_not_say_are_no_job(self) -> None:
        for facts_seen in (None, {}, {"watchdog_job": "x"}):
            with self.subTest(facts=facts_seen):
                self.assertIsNone(menu_status_live.watchdog_job_of(facts_seen, {"last_run": "x"}))

    def test_a_last_run_that_is_not_a_time_is_never_run(self) -> None:
        for last in ("yesterday", None, 7):
            with self.subTest(last=last):
                job = menu_status_live.watchdog_job_of({"watchdog_job": self.JOB}, {"last_run": last})
                self.assertEqual(job, WatchdogJob(True, True, None))

    def test_a_run_that_skipped_is_a_run_the_later_of_the_state_and_the_output_file(self) -> None:
        job = {"watchdog_job": self.JOB}
        saved = {"last_run": "2026-11-11 09:00:00"}
        later = datetime(2026, 11, 11, 9, 58, 0)
        self.assertEqual(menu_status_live.watchdog_job_of(job, saved, later).last_run, later)  # type: ignore[union-attr]
        earlier = datetime(2026, 11, 11, 8, 0, 0)
        found = menu_status_live.watchdog_job_of(job, saved, earlier)
        self.assertEqual(found.last_run, datetime(2026, 11, 11, 9, 0, 0))  # type: ignore[union-attr]
        found = menu_status_live.watchdog_job_of(job, {}, later)
        self.assertEqual(found.last_run, later)  # type: ignore[union-attr]

    def test_launched_at_is_the_output_files_time_and_none_without_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(menu_status_live.launched_at(Path(tmp)))
            out = Path(tmp) / "watchdog.launchd.out"
            out.write_text("bosun is stopped (bosun quit at 09:30): skipping this run\n")
            os.utime(out, (1700000000, 1700000000))
            self.assertEqual(menu_status_live.launched_at(Path(tmp)), datetime.fromtimestamp(1700000000))

    def test_a_quit_that_ended_long_after_the_last_full_run_is_not_yellow_while_the_job_still_runs(
        self,
    ) -> None:
        # A quit's skipped runs leave last_run old; their lines in the output file are the proof the job runs.
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "logs").mkdir()
            (state / "watchdog.json").write_text(json.dumps({"last_run": "2026-11-11 05:00:00"}))
            out = state / "logs" / "watchdog.launchd.out"
            out.write_text("bosun is stopped (bosun quit at 05:05): skipping this run\n")
            stamp = (NOW - timedelta(minutes=2)).timestamp()
            os.utime(out, (stamp, stamp))
            run = FakeCommands(
                {
                    "sessions --json": json.dumps(WINDOWS),
                    "watchdog --facts": json.dumps({**WATCHDOG_FACTS, "watchdog_job": self.JOB}),
                }
            )
            found = menu_status_live.gather(environment(state), NOW, run, kernel_time=lambda _name: None)
        problems = menu_status.snapshot(found, NOW)["problems"]
        self.assertFalse(any("watchdog last ran" in p for p in problems), problems)

    def test_gather_reads_it_from_the_facts_and_the_saved_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "logs").mkdir()
            (state / "watchdog.json").write_text(json.dumps({"last_run": "2026-11-11 09:30:00"}))
            run = FakeCommands(
                {
                    "sessions --json": json.dumps(WINDOWS),
                    "watchdog --facts": json.dumps({**WATCHDOG_FACTS, "watchdog_job": self.JOB}),
                }
            )
            found = menu_status_live.gather(environment(state), NOW, run, kernel_time=lambda _name: None)
        self.assertEqual(found.watchdog_job, WatchdogJob(True, True, datetime(2026, 11, 11, 9, 30, 0)))
        problems = menu_status.snapshot(found, NOW)["problems"]  # 30 minutes: not working
        self.assertTrue(any("watchdog last ran at 09:30" in p for p in problems), problems)

    def test_gather_without_the_job_in_the_facts_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "logs").mkdir()
            found = menu_status_live.gather(
                environment(Path(tmp)), NOW, good_commands(), kernel_time=lambda _name: None
            )
        self.assertIsNone(found.watchdog_job)


class WatchdogWakeTestCase(unittest.TestCase):
    """A Mac that slept runs no watchdog: awake time counts from the later of its last run and its wake."""

    def test_woken_two_minutes_ago_after_a_long_sleep_is_not_yellow(self) -> None:
        job = WatchdogJob(True, True, NOW - timedelta(hours=9), NOW - timedelta(minutes=2))
        self.assertEqual((snap(watchdog_job=job)["level"], snap(watchdog_job=job)["problems"]), ("ok", []))

    def test_awake_sixteen_minutes_with_no_run_is_yellow_and_says_the_last_run(self) -> None:
        job = WatchdogJob(True, True, NOW - timedelta(hours=9), NOW - timedelta(minutes=16))
        out = snap(watchdog_job=job)
        self.assertEqual(out["level"], "warning")
        self.assertIn("last ran at 01:00", out["problems"][0])

    def test_the_edge_is_three_intervals_awake(self) -> None:
        woke = NOW - timedelta(minutes=15)
        self.assertEqual(
            snap(watchdog_job=WatchdogJob(True, True, NOW - timedelta(hours=9), woke))["level"], "ok"
        )
        woke = NOW - timedelta(minutes=15, seconds=1)
        job = WatchdogJob(True, True, NOW - timedelta(hours=9), woke)
        self.assertEqual(snap(watchdog_job=job)["level"], "warning")

    def test_a_wake_before_the_last_run_changes_nothing(self) -> None:
        old_wake = NOW - timedelta(hours=2)
        self.assertEqual(
            snap(watchdog_job=WatchdogJob(True, True, NOW - timedelta(minutes=1), old_wake))["level"], "ok"
        )
        stale = WatchdogJob(True, True, NOW - timedelta(minutes=20), NOW - timedelta(hours=2))
        self.assertEqual(snap(watchdog_job=stale)["level"], "warning")

    def test_without_a_waketime_it_is_the_rule_of_the_last_run(self) -> None:
        self.assertEqual(
            snap(watchdog_job=WatchdogJob(True, True, NOW - timedelta(minutes=16), None))["level"], "warning"
        )
        self.assertEqual(
            snap(watchdog_job=WatchdogJob(True, True, NOW - timedelta(minutes=14), None))["level"], "ok"
        )

    def test_a_job_that_is_not_loaded_is_yellow_at_once_whatever_the_wake(self) -> None:
        job = WatchdogJob(True, False, None, NOW - timedelta(seconds=30))
        out = snap(watchdog_job=job)
        self.assertEqual(out["level"], "warning")
        self.assertIn("not loaded", out["problems"][0])

    def test_a_quit_is_stopped_after_a_wake_too(self) -> None:
        job = WatchdogJob(True, True, NOW - timedelta(hours=9), NOW - timedelta(hours=1))
        out = snap(windows=GONE, quit=quit_state.QuitState(GONE_AT, True), watchdog_job=job)
        self.assertEqual((out["level"], out["problems"]), ("stopped", []))

    def test_a_fresh_boot_with_no_run_since_is_not_yellow(self) -> None:
        # Off overnight: no wake since the boot, the last run was yesterday's, and the job's first run is five
        # minutes after login
        job = WatchdogJob(True, True, NOW - timedelta(hours=14), None, NOW - timedelta(minutes=2))
        self.assertEqual((snap(watchdog_job=job)["level"], snap(watchdog_job=job)["problems"]), ("ok", []))

    def test_a_boot_counts_like_a_wake_three_intervals_on(self) -> None:
        boot = NOW - timedelta(minutes=15)
        self.assertEqual(
            snap(watchdog_job=WatchdogJob(True, True, NOW - timedelta(hours=14), None, boot))["level"], "ok"
        )
        boot = NOW - timedelta(minutes=16)
        out = snap(watchdog_job=WatchdogJob(True, True, NOW - timedelta(hours=14), None, boot))
        self.assertEqual(out["level"], "warning")
        self.assertIn("last ran at 2026-11-10 20:00", out["problems"][0])

    def test_the_latest_of_run_wake_and_boot_counts(self) -> None:
        # Booted days ago, woke an hour ago, ran a minute ago: the run
        job = WatchdogJob(
            True, True, NOW - timedelta(minutes=1), NOW - timedelta(hours=1), NOW - timedelta(days=3)
        )
        self.assertEqual(snap(watchdog_job=job)["level"], "ok")
        # Booted and woke long ago, no run for 20 minutes: stale
        job = WatchdogJob(
            True, True, NOW - timedelta(minutes=20), NOW - timedelta(hours=1), NOW - timedelta(days=3)
        )
        self.assertEqual(snap(watchdog_job=job)["level"], "warning")

    def test_a_time_after_now_is_left_out_not_taken_as_now(self) -> None:
        # The clock moved back: a wake or boot "in the future" would make every look count from its own now
        later = NOW + timedelta(hours=2)
        for job in (
            WatchdogJob(True, True, NOW - timedelta(minutes=20), later),
            WatchdogJob(True, True, NOW - timedelta(minutes=20), None, later),
            WatchdogJob(True, True, NOW - timedelta(minutes=20), later, later),
        ):
            with self.subTest(job=job):
                self.assertEqual(snap(watchdog_job=job)["level"], "warning")
        # ... and the rule goes on with what is left: a real wake two minutes ago
        job = WatchdogJob(True, True, NOW - timedelta(hours=9), NOW - timedelta(minutes=2), later)
        self.assertEqual(snap(watchdog_job=job)["level"], "ok")

    def test_a_last_run_after_now_falls_back_to_the_wake_and_boot(self) -> None:
        later = NOW + timedelta(days=1)
        self.assertEqual(
            snap(watchdog_job=WatchdogJob(True, True, later, NOW - timedelta(minutes=2)))["level"], "ok"
        )
        stale = WatchdogJob(True, True, later, NOW - timedelta(hours=2), NOW - timedelta(days=1))
        self.assertEqual(snap(watchdog_job=stale)["level"], "warning")
        # nothing left to count from: not stale (the watchdog's next run writes a last run of now)
        self.assertEqual(snap(watchdog_job=WatchdogJob(True, True, later))["level"], "ok")

    def test_the_kernels_waketime_as_local_time(self) -> None:
        text = "{ sec = 1794543736, usec = 756800 } Fri Nov 13 06:22:16 2026\n"
        self.assertEqual(menu_status_live.parse_kernel_time(text), datetime.fromtimestamp(1794543736))

    def test_the_kernels_boottime_reads_the_same_way(self) -> None:
        text = "{ sec = 1794500000, usec = 123456 } Fri Nov 13 01:33:20 2026\n"
        self.assertEqual(menu_status_live.parse_kernel_time(text), datetime.fromtimestamp(1794500000))

    def test_a_waketime_that_is_zero_or_not_one_is_none(self) -> None:
        for text in (
            "{ sec = 0, usec = 0 } Thu Jan  1 00:00:00 1970",
            "",
            "nonsense",
            "sysctl: unknown oid",
            "sec = x",
        ):
            with self.subTest(text=text):
                self.assertIsNone(menu_status_live.parse_kernel_time(text))

    def test_the_wake_and_boot_are_read_once_and_only_when_the_facts_have_the_job(self) -> None:
        asked: list[str] = []
        times = {"kern.waketime": NOW - timedelta(minutes=1), "kern.boottime": NOW - timedelta(days=3)}

        def woke(name: str) -> datetime | None:
            asked.append(name)
            return times[name]

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "logs").mkdir()
            (state / "watchdog.json").write_text(json.dumps({"last_run": "2026-11-11 01:00:00"}))
            job = {"state": "not running", "installed": True}
            run = FakeCommands(
                {
                    "sessions --json": json.dumps(WINDOWS),
                    "watchdog --facts": json.dumps({**WATCHDOG_FACTS, "watchdog_job": job}),
                }
            )
            found = menu_status_live.gather(environment(state), NOW, run, kernel_time=woke)
            self.assertEqual(
                found.watchdog_job,
                WatchdogJob(
                    True,
                    True,
                    datetime(2026, 11, 11, 1, 0, 0),
                    NOW - timedelta(minutes=1),
                    NOW - timedelta(days=3),
                ),
            )
            self.assertEqual(asked, ["kern.waketime", "kern.boottime"])
            menu_status_live.gather(environment(state), NOW, good_commands(), kernel_time=woke)
            self.assertEqual(asked, ["kern.waketime", "kern.boottime"])  # no job in those facts: not asked


if __name__ == "__main__":
    unittest.main()
