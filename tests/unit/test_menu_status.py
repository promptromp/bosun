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
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import awake
import menu_status
import menu_status_live
import quit_state
import update_state
from console_live import LiveError
from menu_status import StatusFacts


NOW = datetime(2026, 11, 11, 10, 0, 0)  # a Wednesday, local time, inside the awake window
WORKDAYS = awake.AwakeWindow("MTWRF", time(8, 0), time(20, 0))
GONE_AT = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)


def window(name: str, status: str | None = "idle", exists: bool = True) -> dict[str, Any]:
    """A window as `bosun sessions --json` lists it."""
    agent = {"pid": 4101, "status": status, "statusUpdatedAt": 1700000000000} if status else None
    return {"name": name, "kind": "session", "window": exists, "agent": agent}


WINDOWS = [window("orch"), window("exporter"), window("checkin", None)]


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
        self.assertEqual(out["update"], {"state": "none", "waiting_for": []})
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

    def test_no_session_inside_the_awake_window_is_an_error(self) -> None:
        gone = [window(w["name"], exists=False) for w in WINDOWS]
        out = snap(windows=gone)
        self.assertEqual((out["level"], out["sentence"]), ("error", "bosun is not running"))
        self.assertEqual(out["windows"], 0)
        self.assertEqual(len(out["problems"]), 1)
        self.assertIn("not running", out["problems"][0])
        self.assertIn("bosun start", out["problems"][0])

    def test_the_watchdogs_own_session_finding_is_not_said_twice(self) -> None:
        gone = [window(w["name"], exists=False) for w in WINDOWS]
        message = "The tmux session 'acme' is not running. Start it with: bosun start"
        out = snap(windows=gone, checks=watchdog_checks(check("session-missing", "error", message)))
        self.assertEqual((out["level"], out["problems"]), ("error", [message]))

    def test_no_session_outside_the_awake_window_is_ok_and_resting(self) -> None:
        gone = [window(w["name"], exists=False) for w in WINDOWS]
        out = snap(NOW.replace(hour=21), windows=gone)
        self.assertEqual((out["level"], out["sentence"]), ("ok", "bosun is resting until 08:00"))
        self.assertEqual(out["problems"], [])

    def test_resting_over_a_weekend_waits_for_the_next_scheduled_day(self) -> None:
        gone = [window(w["name"], exists=False) for w in WINDOWS]
        friday_night = datetime(2026, 11, 13, 21, 0)
        out = snap(friday_night, windows=gone, awake=awake.AwakeWindow("MTWRF", time(7, 30), time(20, 0)))
        self.assertEqual(out["sentence"], "bosun is resting until 07:30")

    def test_resting_before_the_start_on_a_scheduled_day_is_until_today(self) -> None:
        gone = [window(w["name"], exists=False) for w in WINDOWS]
        out = snap(NOW.replace(hour=6), windows=gone)
        self.assertEqual(out["sentence"], "bosun is resting until 08:00")

    def test_resting_with_a_warning_says_so(self) -> None:
        gone = [window(w["name"], exists=False) for w in WINDOWS]
        out = snap(NOW.replace(hour=21), windows=gone, checks=watchdog_checks(check("waiting", "warn", "w1")))
        self.assertEqual(
            (out["level"], out["sentence"]), ("warning", "bosun is resting until 08:00, with a warning")
        )

    def test_an_error_while_resting_is_still_an_error(self) -> None:
        gone = [window(w["name"], exists=False) for w in WINDOWS]
        out = snap(
            NOW.replace(hour=21),
            windows=gone,
            checks=watchdog_checks(check("morning-failed", "error", "e1")),
        )
        self.assertEqual((out["level"], out["sentence"]), ("error", "bosun is not running"))

    def test_a_window_that_is_configured_but_not_open_does_not_count(self) -> None:
        out = snap(windows=[window("orch"), window("exporter", exists=False)])
        self.assertEqual(out["windows"], 1)
        self.assertEqual(out["detail"], ["1 window open"])


class StoppedTestCase(unittest.TestCase):
    def test_a_quit_is_stopped_whatever_the_windows_and_checks_say(self) -> None:
        out = snap(
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
        out = snap(quit=quit_state.QuitState(GONE_AT, True))
        self.assertTrue(out["stopped"]["morning_paused"])
        self.assertEqual(len(out["detail"]), 2)
        self.assertIn("morning start is paused", out["detail"][1])

    def test_a_quit_record_that_cannot_be_read_says_so_and_how_to_clear_it(self) -> None:
        out = snap(quit=quit_state.QuitState(GONE_AT, True, readable=False))
        self.assertEqual(out["level"], "stopped")
        self.assertEqual(len(out["problems"]), 1)
        for text in (out["problems"][0], out["detail"][-1]):
            self.assertIn("quit record", text)
            self.assertIn("unreadable", text)
            self.assertIn("bosun start", text)

    def test_the_quit_is_never_read_as_running_when_the_windows_are_open(self) -> None:
        out = snap(quit=quit_state.QuitState(GONE_AT, False), windows=WINDOWS)
        self.assertEqual(out["level"], "stopped")
        self.assertEqual(out["windows"], 3)


class StateTestCase(unittest.TestCase):
    def test_an_update_running_is_still_ok_and_names_the_windows_it_waits_for(self) -> None:
        out = snap(update_running=True, update_waiting_for=("exporter", "docs"))
        self.assertEqual((out["level"], out["sentence"]), ("ok", "bosun is running"))
        self.assertEqual(out["update"], {"state": "running", "waiting_for": ["exporter", "docs"]})
        self.assertIn("An update is running, waiting for exporter, docs", out["detail"])

    def test_an_update_that_waits_for_nothing(self) -> None:
        out = snap(update_running=True)
        self.assertEqual(out["update"], {"state": "running", "waiting_for": []})
        self.assertIn("An update is running", out["detail"])

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
        gone = [window(w["name"], exists=False) for w in WINDOWS]
        out = snap(windows=gone, awake=None, errors={"config": "BOSUN_START_TIME must be HH:MM"})
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
        json.dumps(out)


class KeepAwakeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.file = self.state / "keep-awake-acme.json"

    def read(self, alive: bool = True) -> datetime | None:
        return menu_status_live.keep_awake_until(self.state, "acme", lambda _pid: alive)

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

        menu_status_live.keep_awake_until(self.state, "acme", alive)
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
        self.assertFalse(menu_status_live.is_caffeinate(os.getpid()))
        self.assertFalse(menu_status_live.is_caffeinate(2**22 + 12345))


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
        return menu_status_live.gather(self.env, NOW, run or good_commands())

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
        # the record of an update that is not running names nobody to wait for
        self.assertEqual(self.gather().update_waiting_for, ())

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


if __name__ == "__main__":
    unittest.main()
