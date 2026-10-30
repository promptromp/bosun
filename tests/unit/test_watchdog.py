"""Unit tests for lib/watchdog.py: each check, the awake window, confirmation, de-duplication,
log offsets and the dry-run and status modes. Time is passed in; nothing sleeps."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import claude_state
import login
import signals
import watchdog
from watchdog import CHECKS, Config, Facts, Inputs, WindowFact


MONDAY = date(2026, 1, 5)
TUESDAY = MONDAY + timedelta(days=1)
FRIDAY = MONDAY + timedelta(days=4)
SATURDAY = MONDAY + timedelta(days=5)
ORCH_PID = 101
REPO_PID = 102
FIVE_MINUTES = timedelta(minutes=5)


def at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm))


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


PERMISSION = {
    "session_id": "alpha-session",
    "hook_event_name": "PermissionRequest",
    "tool_name": "Bash",
    "tool_input": {"command": "make deploy"},
}


def add_signal(state_dir: Path, window: str, moment: datetime, payload: dict[str, Any]) -> None:
    """A hook signal recorded at local time <moment>."""
    line = signals.signal_from(payload, moment.astimezone(UTC))
    assert line is not None
    signals.append(state_dir, window, line, 1 << 20)


class WatchdogCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "state"
        self.log_dir = self.state_dir / "logs"
        self.log_dir.mkdir(parents=True)
        self.config = Config(
            state_dir=self.state_dir,
            log_dir=self.log_dir,
            session="main",
            orchestrator="orch",
            days="MTWRF",
            start=time(8, 0),
            wake_delay_minutes=1,
            awake_until=time(20, 0),
            grace_minutes=15,
            waiting_minutes=30,
            checkin_minutes=60,
            delegated_hours=8,
        )
        self.facts = Facts(
            session_exists=True,
            windows=(
                WindowFact("orch", ORCH_PID),
                WindowFact("alpha", REPO_PID),
                WindowFact("checkin", None),
            ),
            checkin_window=True,
            checkin_loop=True,
            morning_job_state="not running",
            morning_job_exit="0",
        )
        self.records: dict[int, dict[str, Any]] = {
            ORCH_PID: {"status": "idle", "statusUpdatedAt": ms(at(MONDAY, "07:00"))},
            REPO_PID: {"status": "busy", "statusUpdatedAt": ms(at(MONDAY, "07:00"))},
        }
        self.sent: list[str] = []
        self.dry_sent: list[str] = []
        self.sink_ok = True
        self.logged_in: bool | None = True

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def sink(self, message: str, dry_run: bool) -> bool:
        (self.dry_sent if dry_run else self.sent).append(message)
        return self.sink_ok

    def inputs(self, now: datetime, facts: Facts | None = None) -> Inputs:
        return Inputs(self.config, facts or self.facts, now, self.records.get, auth_status=self.auth_status)

    def auth_status(self, _claude_bin: str) -> claude_state.AuthStatus:
        return claude_state.AuthStatus(self.logged_in, "claude.ai")

    def watch(self, now: datetime, only: str | None = None, facts: Facts | None = None) -> list[str]:
        """One --once run; returns the alerts it sent."""
        before = len(self.sent)
        with redirect_stdout(io.StringIO()):
            watchdog.run_once(
                self.inputs(now, facts), watchdog.select_checks(only), only is None, sinks=(self.sink,)
            )
        return self.sent[before:]

    def runs(self, start: datetime, count: int, **kwargs: Any) -> list[str]:
        """<count> runs five minutes apart, like launchd; returns everything they sent."""
        sent = []
        for n in range(count):
            sent += self.watch(start + n * FIVE_MINUTES, **kwargs)
        return sent

    def state(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads((self.state_dir / watchdog.STATE_FILE).read_text())
        return value

    def morning_run(self, day: date, body: list[str] | None = None, **header: str) -> None:
        values = {"start": "08:01:05", "trigger": "launchd", "dry_run": "0", **header}
        run = f"trigger={values['trigger']}, session=main, dry_run={values['dry_run']}"
        lines = [
            f"{day} {values['start']} [morning] === morning run ({run}) ===",
            *(body if body is not None else [f"{day} 08:01:50 [morning] morning run complete"]),
        ]
        with (self.log_dir / f"morning-{day}.log").open("a") as fh:
            fh.write("\n".join(lines) + "\n")

    def append(self, name: str, *lines: str) -> None:
        with (self.log_dir / name).open("a") as fh:
            fh.write("".join(f"{line}\n" for line in lines))

    def waiting(self, pid: int, since: datetime) -> None:
        self.records[pid] = {"status": "waiting", "statusUpdatedAt": ms(since)}


class ScheduleTest(WatchdogCase):
    def test_awake_seconds_skip_nights_and_weekends(self) -> None:
        hours = watchdog.awake_seconds(self.config, at(FRIDAY, "18:00"), at(MONDAY + timedelta(7), "10:00"))
        self.assertEqual(hours / 3600, 4)

    def test_window_bounds(self) -> None:
        self.assertTrue(watchdog.in_awake_window(self.config, at(MONDAY, "08:00")))
        self.assertFalse(watchdog.in_awake_window(self.config, at(MONDAY, "20:00")))
        self.assertFalse(watchdog.in_awake_window(self.config, at(MONDAY, "07:59")))
        self.assertFalse(watchdog.in_awake_window(self.config, at(SATURDAY, "12:00")))

    def test_awake_until_at_or_before_the_start_means_midnight(self) -> None:
        config = replace(self.config, awake_until=time(0, 0))
        self.assertTrue(watchdog.in_awake_window(config, at(MONDAY, "23:59")))
        self.assertFalse(watchdog.in_awake_window(config, at(MONDAY + timedelta(1), "00:00")))

    def test_a_window_past_midnight_ends_the_next_morning(self) -> None:
        config = replace(self.config, awake_until=time(1, 0))
        tuesday = MONDAY + timedelta(1)
        self.assertTrue(watchdog.in_awake_window(config, at(MONDAY, "23:30")))
        self.assertTrue(watchdog.in_awake_window(config, at(tuesday, "00:59")))
        self.assertFalse(watchdog.in_awake_window(config, at(tuesday, "01:00")))
        self.assertFalse(watchdog.in_awake_window(config, at(tuesday, "07:59")))
        # Friday's window runs into Saturday; Saturday's own (unscheduled) does not exist.
        self.assertTrue(watchdog.in_awake_window(config, at(SATURDAY, "00:30")))
        self.assertFalse(watchdog.in_awake_window(config, at(SATURDAY + timedelta(1), "00:30")))
        # Monday 08:00 to Tuesday 01:00 is 17 awake hours.
        hours = watchdog.awake_seconds(config, at(MONDAY, "07:00"), at(tuesday, "07:00")) / 3600
        self.assertEqual(hours, 17)


class MorningMissingTest(WatchdogCase):
    def test_unscheduled_day_never_alerts(self) -> None:
        self.assertEqual(self.runs(at(SATURDAY, "09:00"), 4), [])

    def test_nothing_before_the_grace_period_ends(self) -> None:
        self.assertEqual(self.runs(at(MONDAY, "08:00"), 4), [])  # 08:00 to 08:15; due 08:16

    def test_a_morning_run_means_no_alert(self) -> None:
        self.morning_run(MONDAY)
        self.assertEqual(self.runs(at(MONDAY, "08:20"), 3), [])

    def test_missing_alerts_once_after_two_runs(self) -> None:
        self.assertEqual(self.watch(at(MONDAY, "08:20")), [])  # seen once: confirming
        sent = self.watch(at(MONDAY, "08:25"))
        self.assertEqual(len(sent), 1)
        self.assertIn("No morning run today: it was due at 08:01", sent[0])
        self.assertEqual(self.runs(at(MONDAY, "08:30"), 6), [])

    def test_the_next_day_alerts_again(self) -> None:
        self.runs(at(MONDAY, "08:20"), 2)
        sent = self.runs(at(TUESDAY, "08:20"), 3)
        self.assertEqual(len(sent), 1)

    def test_a_late_wake_that_runs_the_morning_job_does_not_alert(self) -> None:
        self.watch(at(MONDAY, "08:20"))  # seen once, then the Mac slept
        self.assertEqual(self.watch(at(MONDAY, "14:00")), [])  # long gap: observation starts over
        self.morning_run(MONDAY, start="14:00:04")
        self.assertEqual(self.runs(at(MONDAY, "14:05"), 3), [])

    def test_a_late_wake_without_a_morning_run_alerts_once(self) -> None:
        sent = self.runs(at(MONDAY, "14:00"), 6)
        self.assertEqual(len(sent), 1)
        self.assertIn("No morning run today", sent[0])

    def test_what_one_run_finds_goes_out_as_one_message(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "07:00"))
        self.waiting(REPO_PID, at(MONDAY, "07:00"))
        # The Mac woke late: the missing run is seen for the first time, so the open prompts wait
        # one run for it and all three go out together.
        self.assertEqual(self.watch(at(MONDAY, "14:00")), [])
        sent = self.runs(at(MONDAY, "14:05"), 3)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith("3 problems:\n- No morning run today"))
        self.assertIn("The alpha session has waited", sent[0])

    def test_a_ready_alert_waits_for_company_at_most_once(self) -> None:
        self.morning_run(MONDAY)
        self.waiting(ORCH_PID, at(MONDAY, "07:00"))
        dead = replace(self.facts, checkin_loop=False)
        self.assertEqual(self.watch(at(MONDAY, "10:00"), facts=dead), [])  # check-in loop: confirming
        sent = self.watch(at(MONDAY, "11:00"), facts=dead)  # a long gap: the loop is still confirming
        self.assertEqual(len(sent), 1)
        self.assertIn("The orch session has waited", sent[0])

    def test_outside_the_awake_window_nothing_alerts(self) -> None:
        self.assertEqual(self.runs(at(MONDAY, "20:00"), 6), [])

    def test_a_dry_run_does_not_count_as_a_morning_run(self) -> None:
        self.morning_run(MONDAY, dry_run="1")
        self.assertEqual(len(self.runs(at(MONDAY, "08:20"), 2)), 1)


class MorningFailedTest(WatchdogCase):
    def test_problems_alert_once_with_the_failing_step(self) -> None:
        self.morning_run(
            MONDAY,
            [
                f"{MONDAY} 08:06:20 [setup] FAIL: aws profile dev still invalid after sso login",
                f"{MONDAY} 08:06:20 [setup] setup finished with 1 failed step(s)",
                f"{MONDAY} 08:06:30 [morning] morning run finished with problems: setup (log: x)",
            ],
        )
        sent = self.watch(at(MONDAY, "08:10"))
        self.assertEqual(len(sent), 1)
        self.assertIn("reported problems: [setup] FAIL: aws profile dev still invalid", sent[0])
        self.assertEqual(self.runs(at(MONDAY, "08:15"), 3), [])

    def test_window_failures_alert_even_if_the_run_completed(self) -> None:
        self.morning_run(
            MONDAY,
            [
                f"{MONDAY} 08:01:30 [start] beta: directory /x does not exist; skipping the window",
                f"{MONDAY} 08:01:50 [morning] morning run complete",
            ],
        )
        self.assertIn("skipping the window", self.watch(at(MONDAY, "08:10"))[0])

    def test_a_later_dry_run_with_errors_is_ignored(self) -> None:
        self.morning_run(MONDAY)
        self.morning_run(
            MONDAY, [f"{MONDAY} 15:30:00 [refresh] ERROR: no tmux session"], start="15:30:00", dry_run="1"
        )
        self.assertEqual(self.runs(at(MONDAY, "15:35"), 3), [])

    def test_a_run_in_progress_does_not_alert(self) -> None:
        self.morning_run(MONDAY, [f"{MONDAY} 08:01:06 [morning] network is up"])
        running = replace(self.facts, morning_job_state="running")
        self.assertEqual(self.runs(at(MONDAY, "08:05"), 3, facts=running), [])

    def test_a_run_that_never_finishes_alerts(self) -> None:
        self.morning_run(MONDAY, [f"{MONDAY} 08:01:06 [morning] network is up"], trigger="manual")
        self.assertEqual(self.runs(at(MONDAY, "08:20"), 3), [])
        sent = self.runs(at(MONDAY, "08:50"), 3)
        self.assertEqual(len(sent), 1)
        self.assertIn("is still not done", sent[0])

    def test_a_launchd_run_that_crashed_alerts(self) -> None:
        self.morning_run(MONDAY, [f"{MONDAY} 08:01:06 [morning] network is up"])
        crashed = replace(self.facts, morning_job_exit="2")
        sent = self.runs(at(MONDAY, "08:05"), 2, facts=crashed)
        self.assertEqual(len(sent), 1)
        self.assertIn("exited with code 2 without finishing", sent[0])


class WaitingTest(WatchdogCase):
    def setUp(self) -> None:
        super().setUp()
        self.morning_run(MONDAY)

    def test_a_short_wait_does_not_alert(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "09:40"))
        self.assertEqual(self.watch(at(MONDAY, "10:09")), [])

    def test_a_long_wait_alerts_once_per_prompt(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "09:40"))
        sent = self.watch(at(MONDAY, "10:10"))
        self.assertEqual(len(sent), 1)
        self.assertIn("The orch session has waited 30 min", sent[0])
        self.assertIn("check-ins are paused", sent[0])
        self.assertEqual(self.runs(at(MONDAY, "10:15"), 6), [])
        self.waiting(ORCH_PID, at(MONDAY, "11:00"))  # answered, then a new prompt
        self.assertEqual(len(self.runs(at(MONDAY, "11:30"), 3)), 1)

    def test_busy_and_idle_windows_do_not_alert(self) -> None:
        self.assertEqual(self.runs(at(MONDAY, "10:00"), 3), [])

    def test_repo_windows_alert_too(self) -> None:
        self.waiting(REPO_PID, at(MONDAY, "09:00"))
        sent = self.watch(at(MONDAY, "10:00"))
        self.assertIn("The alpha session has waited 60 min", sent[0])
        self.assertFalse((self.state_dir / "alerted-alpha").exists())

    def test_a_prompt_the_check_in_loop_already_alerted_about_is_skipped(self) -> None:
        since = at(MONDAY, "09:00")
        self.waiting(ORCH_PID, since)
        (self.state_dir / "alerted-orch").write_text(f"{ORCH_PID}:{ms(since)}\n")
        self.assertEqual(self.watch(at(MONDAY, "10:00")), [])

    def test_an_alert_updates_the_check_in_loop_marker(self) -> None:
        since = at(MONDAY, "09:00")
        self.waiting(ORCH_PID, since)
        self.watch(at(MONDAY, "10:00"))
        self.assertEqual((self.state_dir / "alerted-orch").read_text(), f"{ORCH_PID}:{ms(since)}\n")

    def test_a_wait_overnight_alerts_when_the_window_opens(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "19:50"))
        self.assertEqual(self.runs(at(MONDAY, "19:55"), 12), [])  # until 20:50
        self.morning_run(TUESDAY)
        sent = self.runs(at(TUESDAY, "08:02"), 3)
        self.assertEqual(len(sent), 1)
        self.assertIn("has waited 732 min", sent[0])

    def test_a_resolved_wait_does_not_alert(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "06:00"))
        self.records[ORCH_PID] = {"status": "idle", "statusUpdatedAt": ms(at(MONDAY, "09:00"))}
        self.assertEqual(self.watch(at(MONDAY, "10:00")), [])

    def test_the_alert_says_what_the_window_was_asked(self) -> None:
        self.waiting(REPO_PID, at(MONDAY, "09:00"))
        self.records[REPO_PID]["sessionId"] = "alpha-session"
        add_signal(self.state_dir, "alpha", at(MONDAY, "09:00"), PERMISSION)
        sent = self.watch(at(MONDAY, "10:00"))
        self.assertIn("at a permission prompt or question (Bash: make deploy)", sent[0])

    def test_without_hook_signals_the_alert_has_no_question(self) -> None:
        self.config = replace(self.config, hook_signals=False)
        self.waiting(REPO_PID, at(MONDAY, "09:00"))
        self.records[REPO_PID]["sessionId"] = "alpha-session"
        add_signal(self.state_dir, "alpha", at(MONDAY, "09:00"), PERMISSION)
        self.assertNotIn("make deploy", self.watch(at(MONDAY, "10:00"))[0])

    def test_off_when_the_threshold_is_zero(self) -> None:
        self.config = replace(self.config, waiting_minutes=0)
        self.waiting(ORCH_PID, at(MONDAY, "06:00"))
        self.assertEqual(self.watch(at(MONDAY, "10:00")), [])


class LogErrorsTest(WatchdogCase):
    def setUp(self) -> None:
        super().setUp()
        self.morning_run(MONDAY)
        self.checkin = f"checkin-{MONDAY}.log"

    def error(self, text: str, command: str = "checkin") -> str:
        return f"{MONDAY} 09:00:00 [{command}] ERROR: {text}"

    def test_the_first_run_baselines_old_lines(self) -> None:
        self.append(self.checkin, self.error("old"))
        self.assertEqual(self.watch(at(MONDAY, "09:00")), [])
        self.assertIn(str(self.log_dir / self.checkin), self.state()["logs"])

    def test_new_lines_alert_once_grouped(self) -> None:
        self.watch(at(MONDAY, "09:00"))
        self.append(self.checkin, self.error("one"), f"{MONDAY} 09:01:00 [checkin] fine", self.error("two"))
        self.append("launchd.err", self.error("three", "morning"), self.error("four", "send"))
        sent = self.watch(at(MONDAY, "09:05"))
        self.assertEqual(len(sent), 1)
        self.assertIn("4 new ERROR line(s)", sent[0])
        self.assertIn("ERROR: one", sent[0])
        self.assertIn("(+1 more)", sent[0])
        self.assertEqual(self.runs(at(MONDAY, "09:10"), 3), [])

    def test_alert_and_watchdog_errors_never_alert(self) -> None:
        self.watch(at(MONDAY, "09:00"))
        self.append(self.checkin, self.error("not delivered", "alert"), self.error("x", "watchdog"))
        self.assertEqual(self.watch(at(MONDAY, "09:05")), [])

    def test_morning_logs_are_left_to_the_morning_checks(self) -> None:
        self.watch(at(MONDAY, "09:00"))
        self.append(f"morning-{MONDAY}.log", self.error("reported by morning-failed", "refresh"))
        self.assertEqual(self.watch(at(MONDAY, "09:05"), only="log-errors"), [])

    def test_a_replaced_file_is_read_from_the_start(self) -> None:
        self.append(self.checkin, *(f"{MONDAY} 08:00:00 [checkin] ok {n}" for n in range(20)))
        self.watch(at(MONDAY, "09:00"))
        (self.log_dir / self.checkin).unlink()
        self.append(self.checkin, self.error("after rotation"))
        self.assertIn("after rotation", self.watch(at(MONDAY, "09:05"))[0])

    def test_a_new_day_file_is_read_from_the_start(self) -> None:
        self.watch(at(MONDAY, "09:00"))
        self.morning_run(TUESDAY)
        self.append(f"checkin-{TUESDAY}.log", self.error("tuesday"))
        self.assertIn("tuesday", self.watch(at(TUESDAY, "09:00"))[0])

    def test_a_partial_line_waits_until_it_is_complete(self) -> None:
        self.watch(at(MONDAY, "09:00"))
        with (self.log_dir / self.checkin).open("a") as fh:
            fh.write(self.error("half"))
        self.assertEqual(self.watch(at(MONDAY, "09:05")), [])
        self.append(self.checkin, " written")
        self.assertIn("half written", self.watch(at(MONDAY, "09:10"))[0])

    def test_lines_found_outside_the_window_alert_when_it_opens(self) -> None:
        self.watch(at(MONDAY, "09:00"))
        self.append(self.checkin, self.error("at night"))
        self.assertEqual(self.runs(at(MONDAY, "21:00"), 3), [])
        self.morning_run(TUESDAY)
        self.assertIn("at night", self.watch(at(TUESDAY, "08:00"))[0])


class SessionTest(WatchdogCase):
    def setUp(self) -> None:
        super().setUp()
        self.gone = Facts(morning_job_state="not running", morning_job_exit="0")

    def test_a_missing_session_alerts_once_after_two_runs(self) -> None:
        self.morning_run(MONDAY)
        self.assertEqual(self.watch(at(MONDAY, "10:00"), facts=self.gone), [])
        sent = self.runs(at(MONDAY, "10:05"), 4, facts=self.gone)
        self.assertEqual(len(sent), 1)
        self.assertIn("tmux session 'main' is not running", sent[0])
        self.assertNotIn("checkin", sent[0])  # no separate check-in alert without a session

    def test_a_session_you_stopped_today_does_not_alert(self) -> None:
        self.morning_run(MONDAY)
        (self.state_dir / "stopped-main").write_text(f"{MONDAY} 09:30:00\n")
        self.assertEqual(self.runs(at(MONDAY, "10:00"), 4, facts=self.gone), [])

    def test_a_stop_on_an_earlier_day_does_not_count(self) -> None:
        self.morning_run(TUESDAY)
        (self.state_dir / "stopped-main").write_text(f"{MONDAY} 19:30:00\n")
        self.assertEqual(len(self.runs(at(TUESDAY, "10:00"), 2, facts=self.gone)), 1)

    def test_no_session_alert_while_the_morning_run_is_going_or_missing(self) -> None:
        self.morning_run(MONDAY, [f"{MONDAY} 08:01:06 [morning] network is up"])
        running = replace(self.gone, morning_job_state="running")
        self.assertEqual(self.runs(at(MONDAY, "08:20"), 3, facts=running), [])

    def test_no_session_alert_after_the_awake_window(self) -> None:
        self.morning_run(MONDAY)
        self.assertEqual(self.runs(at(MONDAY, "20:00"), 3, facts=self.gone), [])


class CheckinTest(WatchdogCase):
    def setUp(self) -> None:
        super().setUp()
        self.morning_run(MONDAY)

    def test_a_dead_check_in_loop_alerts_once(self) -> None:
        dead = replace(self.facts, checkin_loop=False)
        sent = self.runs(at(MONDAY, "10:00"), 4, facts=dead)
        self.assertEqual(len(sent), 1)
        self.assertIn("checkin window is not running the check-in loop", sent[0])

    def test_a_missing_checkin_window(self) -> None:
        gone = replace(self.facts, checkin_window=False, checkin_loop=False)
        self.assertIn("bosun refresh checkin", self.runs(at(MONDAY, "10:00"), 2, facts=gone)[0])

    def test_nothing_when_check_ins_are_off(self) -> None:
        self.config = replace(self.config, checkin_minutes=0)
        dead = replace(self.facts, checkin_loop=False)
        self.assertEqual(self.runs(at(MONDAY, "10:00"), 3, facts=dead), [])

    def test_a_dead_loop_alerts_when_events_run_without_check_ins(self) -> None:
        self.config = replace(
            self.config, checkin_minutes=0, events_enabled=True, events_without_checkins=True
        )
        dead = replace(self.facts, checkin_loop=False)
        sent = self.runs(at(MONDAY, "10:00"), 4, facts=dead)
        self.assertEqual(len(sent), 1)
        self.assertIn("the orchestrator gets no events", sent[0])

    def test_the_loop_is_wanted_only_with_check_ins_or_events_alone(self) -> None:
        for minutes, events_on, alone, wanted in (
            (60, False, False, True),
            (0, True, False, False),
            (0, False, True, False),
            (0, True, True, True),
        ):
            config = replace(
                self.config, checkin_minutes=minutes, events_enabled=events_on, events_without_checkins=alone
            )
            self.assertEqual(config.loop_wanted, wanted, (minutes, events_on, alone))
        self.assertFalse(replace(self.config, orchestrator="").loop_wanted)


class OrchestratorDownTest(WatchdogCase):
    def setUp(self) -> None:
        super().setUp()
        self.morning_run(MONDAY)
        self.restarts: list[str] = []
        self.down = replace(self.facts, windows=(WindowFact("orch", None), WindowFact("alpha", REPO_PID)))

    def restart(self, window: str) -> bool:
        self.restarts.append(window)
        return True

    def inputs(self, now: datetime, facts: Facts | None = None) -> Inputs:
        return replace(super().inputs(now, facts), restart=self.restart)

    def test_alerts_once_after_the_threshold_and_relaunches_once(self) -> None:
        self.assertEqual(self.runs(at(MONDAY, "10:00"), 3, facts=self.down), [])  # 10:00 to 10:10
        self.assertEqual(self.restarts, [])
        sent = self.runs(at(MONDAY, "10:15"), 4, facts=self.down)
        self.assertEqual(len(sent), 1)
        self.assertIn("orchestrator has been down for 15 min", sent[0])
        self.assertIn("no Claude runs in its window 'orch'", sent[0])
        self.assertIn("relaunching it once", sent[0])
        self.assertEqual(self.restarts, ["orch"])

    def test_a_missing_window_is_named(self) -> None:
        gone = replace(self.facts, windows=(WindowFact("alpha", REPO_PID),))
        sent = self.runs(at(MONDAY, "10:00"), 4, facts=gone)
        self.assertIn("its window 'orch' is missing", sent[0])

    def test_a_running_orchestrator_clears_the_outage(self) -> None:
        self.runs(at(MONDAY, "10:00"), 2, facts=self.down)
        self.assertIn("since", self.state()["orchestrator_down"])
        self.watch(at(MONDAY, "10:10"))
        self.assertEqual(self.state()["orchestrator_down"], {})
        # A new outage starts its own clock.
        self.assertEqual(self.runs(at(MONDAY, "11:00"), 3, facts=self.down), [])

    def test_without_restart_it_only_says_how(self) -> None:
        self.config = replace(self.config, orchestrator_restart=False)
        sent = self.runs(at(MONDAY, "10:00"), 5, facts=self.down)
        self.assertIn("start it with: bosun refresh orch", sent[0])
        self.assertEqual(self.restarts, [])

    def test_quiet_before_the_morning_run_and_when_off(self) -> None:
        self.assertEqual(
            self.runs(at(TUESDAY, "08:30"), 6, facts=self.down, only="orchestrator-down"), []
        )  # no Tuesday run
        self.config = replace(self.config, orchestrator_down_minutes=0)
        self.assertEqual(self.runs(at(MONDAY, "10:00"), 6, facts=self.down), [])
        self.assertEqual(self.restarts, [])


class LedgerStuckTest(WatchdogCase):
    def setUp(self) -> None:
        super().setUp()
        self.morning_run(MONDAY)

    def delegate(
        self,
        item_id: str,
        since: datetime,
        notes: list[datetime] | None = None,
        reports: list[datetime] | None = None,
        updates: list[datetime] | None = None,
    ) -> None:
        history = [{"at": iso(since), "event": "transition", "from": "requested", "to": "delegated"}]
        history += [{"at": iso(note), "event": "note", "note": "x"} for note in notes or []]
        history += [{"at": iso(report), "event": "report", "note": "done"} for report in reports or []]
        history += [
            {"at": iso(update), "event": "update", "note": "more", "window": "w"} for update in updates or []
        ]
        history.sort(key=lambda entry: entry["at"])
        item = {
            "id": item_id,
            "kind": "review-request",
            "state": "delegated",
            "created_at": iso(since - timedelta(hours=1)),
            "history": history,
        }
        path = self.state_dir / "ledger.json"
        ledger_data = json.loads(path.read_text()) if path.exists() else {"items": {}}
        ledger_data["items"][item_id] = item
        path.write_text(json.dumps(ledger_data))

    def test_an_item_delegated_for_a_working_day_alerts_once(self) -> None:
        self.delegate("review:o/r#1", at(MONDAY, "08:30"), notes=[at(MONDAY, "15:00")])
        self.assertEqual(self.watch(at(MONDAY, "16:00")), [])
        sent = self.watch(at(MONDAY, "16:30"))
        self.assertEqual(len(sent), 1)
        self.assertIn("review:o/r#1 has been delegated for 8 awake hours", sent[0])
        self.assertIn("with no report", sent[0])
        self.assertEqual(self.runs(at(MONDAY, "16:35"), 3), [])

    def test_a_session_that_reported_counts_from_its_report(self) -> None:
        self.delegate("review:o/r#4", at(MONDAY, "08:30"), reports=[at(MONDAY, "12:00")])
        self.assertEqual(self.watch(at(MONDAY, "16:30")), [])
        tuesday = MONDAY + timedelta(days=1)
        self.morning_run(tuesday)
        sent = self.watch(at(tuesday, "10:00"))
        self.assertEqual(len(sent), 1)
        self.assertIn("reported at Mon 12:00 and nothing has moved it on since", sent[0])

    def test_an_item_waiting_on_outreach_is_left_to_the_nudges(self) -> None:
        self.delegate("review:o/r#5", at(MONDAY, "08:30"))
        path = self.state_dir / "ledger.json"
        data = json.loads(path.read_text())
        data["items"]["out:review:o/r#5/1"] = {
            "id": "out:review:o/r#5/1",
            "kind": "outreach",
            "state": "sent",
            "created_at": iso(at(MONDAY, "09:00")),
            "fields": {"parent": "review:o/r#5"},
            "history": [],
        }
        path.write_text(json.dumps(data))
        self.assertEqual(self.watch(at(MONDAY, "18:00")), [])

    def produce(self, item_id: str, pr_states: list[str]) -> None:
        """Link <item_id> to one my-pr item per state, as `ledger link <task> produces <mypr>` does."""
        path = self.state_dir / "ledger.json"
        data = json.loads(path.read_text())
        links = []
        for number, state in enumerate(pr_states, start=1):
            pr_id = f"mypr:o/r#{100 + number}"
            data["items"][pr_id] = {
                "id": pr_id,
                "kind": "my-pr",
                "state": state,
                "created_at": iso(at(MONDAY, "09:00")),
                "history": [],
            }
            links.append({"at": iso(at(MONDAY, "09:00")), "id": pr_id, "rel": "produces"})
        data["items"][item_id]["links"] = links
        path.write_text(json.dumps(data))

    def test_a_reported_task_whose_prs_wait_on_review_is_left_to_the_nudges(self) -> None:
        self.delegate("task:2026-01-05/1", at(MONDAY, "08:30"), reports=[at(MONDAY, "09:00")])
        self.produce("task:2026-01-05/1", ["awaiting-review", "merged"])
        self.assertEqual(self.watch(at(MONDAY, "18:00")), [])

    def test_a_task_is_still_stuck_when_a_pr_is_not_waiting_on_reviewers(self) -> None:
        for item_id, pr_states, reports in [
            ("task:2026-01-05/2", ["awaiting-review", "addressing"], [at(MONDAY, "09:00")]),
            ("task:2026-01-05/3", ["merged"], [at(MONDAY, "09:00")]),
            ("task:2026-01-05/4", ["awaiting-review"], []),  # no report: the session may be stuck
        ]:
            with self.subTest(item_id=item_id):
                self.delegate(item_id, at(MONDAY, "08:30"), reports=reports)
                self.produce(item_id, pr_states)
                self.assertTrue(any(item_id in message for message in self.watch(at(MONDAY, "18:00"))))

    def test_an_update_after_the_report_restarts_the_clock(self) -> None:
        self.delegate(
            "review:o/r#6", at(MONDAY, "08:30"), reports=[at(MONDAY, "09:00")], updates=[at(MONDAY, "15:00")]
        )
        self.assertEqual(
            self.watch(at(MONDAY, "18:00")), []
        )  # 9 awake hours since the report, 3 since the update
        tuesday = MONDAY + timedelta(days=1)
        self.morning_run(tuesday)
        sent = self.watch(at(tuesday, "17:00"))
        self.assertEqual(len(sent), 1)
        self.assertIn("since Mon 15:00) with no report since its last hand-over", sent[0])

    def test_a_reported_task_whose_open_pr_is_approved_is_left_to_the_merge(self) -> None:
        self.delegate("task:2026-01-05/5", at(MONDAY, "08:30"), reports=[at(MONDAY, "09:00")])
        self.produce("task:2026-01-05/5", ["approved", "merged", "merged"])
        self.assertEqual(self.watch(at(MONDAY, "18:00")), [])

    def test_a_long_running_task_with_recent_updates_and_an_approved_pr_is_not_stuck(self) -> None:
        """The case that alerted falsely: delegated for days, last report two days ago, updates
        since, and its one open PR approved and being prepared for merge."""
        wednesday = MONDAY + timedelta(days=2)
        self.morning_run(wednesday)
        self.delegate(
            "task:2026-01-05/6",
            at(MONDAY, "08:30") - timedelta(days=7),
            reports=[at(MONDAY, "17:29")],
            updates=[at(wednesday, "08:15"), at(wednesday, "08:56")],
        )
        self.produce("task:2026-01-05/6", ["approved", "merged", "merged", "merged", "merged"])
        self.assertEqual(self.watch(at(wednesday, "11:00")), [])
        self.produce(
            "task:2026-01-05/6", ["addressing", "merged"]
        )  # not waiting on others: the updates still count
        self.assertEqual(self.watch(at(wednesday, "11:00")), [])

    def test_a_task_with_no_movement_since_its_report_still_alerts(self) -> None:
        self.delegate(
            "task:2026-01-05/7",
            at(MONDAY, "08:30"),
            reports=[at(MONDAY, "09:00")],
            updates=[at(MONDAY, "08:45")],
        )
        self.produce("task:2026-01-05/7", ["changes-requested"])
        sent = self.watch(at(MONDAY, "18:00"))
        self.assertTrue(
            any(
                "task:2026-01-05/7 has reported at Mon 09:00 and nothing has moved it on since" in m
                for m in sent
            )
        )

    def test_nights_and_weekends_do_not_count(self) -> None:
        self.delegate("review:o/r#2", at(FRIDAY, "17:00"))
        monday = MONDAY + timedelta(days=7)
        self.morning_run(monday)
        self.assertEqual(self.watch(at(monday, "12:00")), [])  # 3 + 4 awake hours

    def test_off_when_zero(self) -> None:
        self.config = replace(self.config, delegated_hours=0)
        self.delegate("review:o/r#3", at(MONDAY, "08:00") - timedelta(days=3))
        self.assertEqual(self.watch(at(MONDAY, "19:00")), [])


class LongShellTest(WatchdogCase):
    """A background command keeping a Claude window in "shell": its turn is over, the command runs."""

    NEW_PID = 103

    def setUp(self) -> None:
        super().setUp()
        self.morning_run(MONDAY)
        self.morning_run(TUESDAY)

    def status(self, pid: int, status: str, since: datetime) -> None:
        self.records[pid] = {"status": status, "statusUpdatedAt": ms(since)}

    def test_four_awake_hours_in_shell_warn_once(self) -> None:
        self.status(REPO_PID, "shell", at(MONDAY, "09:00"))
        self.assertEqual(self.watch(at(MONDAY, "12:55")), [])
        sent = self.watch(at(MONDAY, "13:00"))
        self.assertEqual(len(sent), 1)
        self.assertIn("A background command has kept alpha in shell for 4h", sent[0])
        self.assertIn("check it is still wanted", sent[0])
        self.assertEqual(self.runs(at(MONDAY, "13:05"), 3), [])

    def test_turns_in_between_do_not_end_the_episode(self) -> None:
        """A check-in or an event makes the orchestrator busy for a turn; the command still runs."""
        self.status(ORCH_PID, "shell", at(MONDAY, "09:00"))
        self.watch(at(MONDAY, "09:05"))
        self.status(ORCH_PID, "busy", at(MONDAY, "10:00"))
        self.watch(at(MONDAY, "10:01"))
        self.status(ORCH_PID, "shell", at(MONDAY, "10:02"))
        sent = self.watch(at(MONDAY, "13:00"))
        self.assertEqual(len(sent), 1)
        self.assertIn("kept orch in shell for 4h (awake hours, since Mon 09:00)", sent[0])

    def test_the_warning_names_what_is_still_running(self) -> None:
        self.status(REPO_PID, "shell", at(MONDAY, "09:00"))
        self.records[REPO_PID]["sessionId"] = "alpha-session"
        stop = {
            "session_id": "alpha-session",
            "hook_event_name": "Stop",
            "background_tasks": [{"type": "shell", "status": "running", "command": "make watch"}],
        }
        add_signal(self.state_dir, "alpha", at(MONDAY, "09:00"), stop)
        sent = self.watch(at(MONDAY, "13:00"))
        self.assertIn("(shell `make watch`)", sent[0])

    def test_idle_ends_the_episode_and_the_next_one_warns_again(self) -> None:
        self.status(REPO_PID, "shell", at(MONDAY, "08:30"))
        self.assertEqual(len(self.watch(at(MONDAY, "12:30"))), 1)
        self.status(REPO_PID, "idle", at(MONDAY, "12:40"))
        self.watch(at(MONDAY, "12:45"))
        self.assertNotIn("alpha", self.state()["shells"])
        self.status(REPO_PID, "shell", at(MONDAY, "13:00"))
        self.assertEqual(self.watch(at(MONDAY, "16:55")), [])
        self.assertEqual(len(self.watch(at(MONDAY, "17:00"))), 1)

    def test_a_new_claude_in_the_window_starts_over(self) -> None:
        self.status(REPO_PID, "shell", at(MONDAY, "08:30"))
        self.watch(at(MONDAY, "09:00"))
        facts = replace(self.facts, windows=(WindowFact("orch", ORCH_PID), WindowFact("alpha", self.NEW_PID)))
        self.status(self.NEW_PID, "shell", at(MONDAY, "11:00"))
        self.assertEqual(self.watch(at(MONDAY, "13:00"), facts=facts), [])

    def test_nights_do_not_count(self) -> None:
        self.status(REPO_PID, "shell", at(MONDAY, "18:00"))
        self.assertEqual(self.watch(at(TUESDAY, "09:30")), [])  # 2 + 1.5 awake hours
        self.assertEqual(len(self.watch(at(TUESDAY, "10:00"))), 1)

    def test_a_dry_run_records_no_episode_and_zero_turns_it_off(self) -> None:
        self.status(REPO_PID, "shell", at(MONDAY, "08:30"))
        with redirect_stdout(io.StringIO()):
            watchdog.run_dry(self.inputs(at(MONDAY, "13:00")), CHECKS, sinks=(self.sink,))
        self.assertFalse((self.state_dir / watchdog.STATE_FILE).exists())
        self.assertIn("kept alpha in shell", self.dry_sent[0])
        self.config = replace(self.config, long_shell_hours=0)
        self.assertEqual(self.watch(at(MONDAY, "13:00")), [])


class RunTest(WatchdogCase):
    def setUp(self) -> None:
        super().setUp()
        self.morning_run(MONDAY)

    def verdict(self, severity: str, message: str) -> watchdog.Verdict:
        check = watchdog.Check("x", severity, "", watchdog.check_waiting)
        return watchdog.Verdict(check, watchdog.Finding(f"x:{message}", message), watchdog.SEND)

    def test_many_problems_are_one_message_most_severe_first(self) -> None:
        verdicts = [self.verdict("warn", f"w{n}") for n in range(5)] + [self.verdict("error", "e")]
        message = watchdog.compose([*verdicts, self.verdict("warn", "w5")])
        self.assertTrue(message.startswith("7 problems:\n- e\n- w0"))
        self.assertIn("- and 2 more: bosun watchdog --status", message)

    def test_a_dry_run_sends_nothing_and_writes_no_state(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "09:00"))
        with redirect_stdout(io.StringIO()) as out:
            watchdog.run_dry(self.inputs(at(MONDAY, "10:00")), CHECKS, sinks=(self.sink,))
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.dry_sent), 1)
        self.assertIn("waiting: alerts now", out.getvalue())
        self.assertEqual(sorted(p.name for p in self.state_dir.iterdir()), ["logs"])

    def test_a_dry_run_sees_what_the_real_run_will_send(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "09:00"))
        with redirect_stdout(io.StringIO()):
            watchdog.run_dry(self.inputs(at(MONDAY, "10:00")), CHECKS, sinks=(self.sink,))
        self.assertEqual(self.watch(at(MONDAY, "10:00")), self.dry_sent)

    def test_only_runs_the_named_checks_and_leaves_the_rest_alone(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "09:00"))
        self.assertEqual(len(self.watch(at(MONDAY, "10:00"), only="waiting")), 1)
        state = self.state()
        self.assertIsNone(state["last_run"])
        self.assertIsNone(state["logs"])
        self.assertEqual(self.watch(at(MONDAY, "10:05")), [])  # the full run shares the de-dup state

    def test_a_failed_delivery_is_not_retried(self) -> None:
        self.sink_ok = False
        self.waiting(ORCH_PID, at(MONDAY, "09:00"))
        with redirect_stdout(io.StringIO()):
            code = watchdog.run_once(self.inputs(at(MONDAY, "10:00")), CHECKS, True, sinks=(self.sink,))
        self.assertEqual(code, 1)
        self.assertEqual(self.watch(at(MONDAY, "10:05")), [])

    def test_status_lists_every_check(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "09:00"))
        status = watchdog.render_status(self.inputs(at(MONDAY, "10:00")), CHECKS)
        for check in CHECKS:
            self.assertIn(f" {check.name} ", status)
        self.assertIn("WARN  waiting", status)
        self.assertIn("[alerts now]", status)
        self.assertFalse((self.state_dir / watchdog.STATE_FILE).exists())

    def test_status_as_data_reads_only(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "09:00"))
        data = watchdog.status_data(self.inputs(at(MONDAY, "10:00")), CHECKS)
        self.assertEqual([c["name"] for c in data["checks"]], [c.name for c in CHECKS])
        waiting = next(c for c in data["checks"] if c["name"] == "waiting")
        self.assertEqual(waiting["severity"], "warn")
        self.assertEqual([f["decision"] for f in waiting["findings"]], ["alerts now"])
        self.assertTrue(data["in_window"])
        self.assertEqual((data["last_run"], data["last_alert"]), (None, None))
        self.assertFalse((self.state_dir / watchdog.STATE_FILE).exists())
        self.assertEqual((self.sent, self.dry_sent), ([], []))

    def test_unknown_checks_are_refused(self) -> None:
        with self.assertRaises(watchdog.ConfigError):
            watchdog.select_checks("waiting,nope")

    def test_old_alert_keys_are_pruned(self) -> None:
        self.waiting(ORCH_PID, at(MONDAY, "09:00"))
        self.watch(at(MONDAY, "10:00"))
        self.morning_run(MONDAY + timedelta(days=21))
        self.watch(at(MONDAY + timedelta(days=21), "10:00"))
        self.assertEqual(self.state()["alerted"], {})

    def test_a_corrupt_state_file_starts_afresh(self) -> None:
        (self.state_dir / watchdog.STATE_FILE).write_text("{")
        self.morning_run(MONDAY)
        self.assertEqual(self.watch(at(MONDAY, "10:00")), [])


class InputsTest(unittest.TestCase):
    def env(self, **overrides: str) -> dict[str, str]:
        values = {
            "STATE_DIR": "/s",
            "LOG_DIR": "/s/logs",
            "SESSION_NAME": "main",
            "ORCHESTRATOR": "orch",
            "DAYS": "MTWRF",
            "START_TIME": "08:00",
            "WAKE_DELAY_MINUTES": "1",
            "AWAKE_UNTIL": "20:00",
            "WATCHDOG_GRACE_MINUTES": "15",
            "ALERT_WAITING_MINUTES": "30",
            "CHECKIN_MINUTES": "60",
            "WATCHDOG_DELEGATED_HOURS": "8",
            "LEARN_ENABLED": "0",
            "LEARN_AT": "18:30",
            "LEARN_TIMEOUT_MINUTES": "30",
            **overrides,
        }
        return {f"BOSUN_{k}": v for k, v in values.items()}

    def test_reads_the_settings(self) -> None:
        config = Config.from_env(self.env())
        self.assertEqual((config.start, config.grace_minutes), (time(8, 0), 15))

    def test_bad_settings_are_reported(self) -> None:
        for bad in ({"START_TIME": "8am"}, {"WATCHDOG_GRACE_MINUTES": "soon"}):
            with self.subTest(bad=bad), self.assertRaises(watchdog.ConfigError):
                Config.from_env(self.env(**bad))
        env = self.env()
        del env["BOSUN_DAYS"]
        with self.assertRaises(watchdog.ConfigError):
            Config.from_env(env)

    def test_now_can_be_injected(self) -> None:
        self.assertEqual(watchdog.current_time("2026-01-05 10:00"), at(MONDAY, "10:00"))
        self.assertEqual(
            watchdog.current_time(str(int(at(MONDAY, "10:00").timestamp()))), at(MONDAY, "10:00")
        )
        with self.assertRaises(watchdog.ConfigError):
            watchdog.current_time("tomorrow")

    def test_facts_from_json(self) -> None:
        facts = Facts.from_json(
            {
                "session_exists": True,
                "windows": [{"name": "a", "agent_pid": 5}, {"name": "b", "agent_pid": None}],
                "morning_job": {"state": "", "last_exit_code": ""},
            }
        )
        self.assertEqual(facts.windows, (WindowFact("a", 5), WindowFact("b", None)))

    def test_main_reports_a_usage_error_for_unknown_checks(self) -> None:
        with redirect_stdout(io.StringIO()), unittest.mock.patch.dict(os.environ, self.env()):
            err = io.StringIO()
            with unittest.mock.patch("sys.stderr", err):
                self.assertEqual(watchdog.main(["--only", "nope"]), 2)
        self.assertIn("unknown check", err.getvalue())


class LoginExpiredTest(WatchdogCase):
    """An expired or revoked Claude Code login (fictional transcripts)."""

    def setUp(self) -> None:
        super().setUp()
        self.morning_run(MONDAY)
        self.morning_run(TUESDAY)
        patch = unittest.mock.patch.object(claude_state, "PROJECTS_DIR", self.state_dir / "projects")
        patch.start()
        self.addCleanup(patch.stop)
        for pid, session in ((ORCH_PID, "orch-session"), (REPO_PID, "alpha-session")):
            self.records[pid].update(cwd="/work/invoice-exporter", sessionId=session)
            self.reply(pid, at(MONDAY, "08:30"))

    def transcript(self, pid: int) -> Path:
        return claude_state.transcript_path("/work/invoice-exporter", self.records[pid]["sessionId"])

    def add(self, pid: int, entry: dict[str, Any]) -> None:
        path = self.transcript(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def reply(self, pid: int, moment: datetime) -> None:
        message = {"model": "claude-model", "content": [{"type": "text", "text": "Checked in."}]}
        self.add(pid, {"type": "assistant", "timestamp": iso(moment), "message": message})

    def login_error(self, pid: int, moment: datetime) -> None:
        message = {"model": "<synthetic>", "content": [{"type": "text", "text": "Login expired"}]}
        entry = {"type": "assistant", "timestamp": iso(moment), "isApiErrorMessage": True, "message": message}
        self.add(pid, {**entry, "error": "authentication_failed"})

    def test_healthy_windows_do_not_alert(self) -> None:
        self.assertEqual(self.runs(at(MONDAY, "10:00"), 3, only="login-expired"), [])

    def test_login_errors_alert_once_per_outage(self) -> None:
        self.login_error(ORCH_PID, at(MONDAY, "09:00"))
        sent = self.watch(at(MONDAY, "09:05"), only="login-expired")
        self.assertEqual(len(sent), 1)
        self.assertIn("Claude Code needs a login: every prompt in orch (since", sent[0])
        self.login_error(ORCH_PID, at(MONDAY, "10:00"))
        self.login_error(REPO_PID, at(MONDAY, "10:01"))
        self.assertEqual(self.runs(at(MONDAY, "10:05"), 6, only="login-expired"), [])

    def test_it_clears_when_the_login_works_again_and_the_next_outage_alerts(self) -> None:
        self.login_error(ORCH_PID, at(MONDAY, "09:00"))
        self.watch(at(MONDAY, "09:05"), only="login-expired")
        self.reply(ORCH_PID, at(MONDAY, "09:30"))
        self.assertEqual(self.watch(at(MONDAY, "09:35"), only="login-expired"), [])
        self.assertNotIn("login-expired:outage", self.state()["alerted"])
        self.login_error(ORCH_PID, at(MONDAY, "11:00"))
        self.assertEqual(len(self.watch(at(MONDAY, "11:05"), only="login-expired")), 1)

    def test_a_repo_window_alone_alerts_too(self) -> None:
        self.login_error(REPO_PID, at(MONDAY, "09:00"))
        sent = self.watch(at(MONDAY, "09:05"), only="login-expired")
        self.assertIn("alpha (since", sent[0])

    def test_logged_out_alerts_without_any_failed_prompt(self) -> None:
        self.logged_in = False
        sent = self.watch(at(MONDAY, "09:05"), only="login-expired")
        self.assertIn("claude auth status: not logged in", sent[0])

    def test_a_failed_login_check_alerts(self) -> None:
        probe = login.Probe(
            at(MONDAY, "08:01").astimezone(UTC), "auth", "structured", "authentication_failed"
        )
        login.State(probe=probe.to_json()).save(self.state_dir)
        sent = self.watch(at(MONDAY, "09:05"), only="login-expired")
        self.assertIn("the login check at", sent[0])

    def test_an_unreadable_auth_status_alone_does_not_alert(self) -> None:
        self.logged_in = None
        self.assertEqual(self.watch(at(MONDAY, "09:05"), only="login-expired"), [])

    def test_an_outage_overnight_alerts_when_the_window_opens(self) -> None:
        self.login_error(ORCH_PID, at(MONDAY, "21:00"))
        self.assertEqual(self.runs(at(MONDAY, "21:05"), 6, only="login-expired"), [])
        sent = self.watch(at(TUESDAY, "08:05"), only="login-expired")
        self.assertEqual(len(sent), 1)

    def test_status_shows_it(self) -> None:
        self.login_error(ORCH_PID, at(MONDAY, "09:00"))
        text = watchdog.render_status(
            self.inputs(at(MONDAY, "09:05")), watchdog.select_checks("login-expired")
        )
        self.assertIn("FAIL  login-expired", text)


if __name__ == "__main__":
    unittest.main()


class SpendLimitTest(WatchdogCase):
    """A usage limit (spend limit) stops a window or its subagents (fictional transcripts)."""

    def setUp(self) -> None:
        super().setUp()
        self.morning_run(MONDAY)
        patch = unittest.mock.patch.object(claude_state, "PROJECTS_DIR", self.state_dir / "projects")
        patch.start()
        self.addCleanup(patch.stop)
        for pid, session in ((ORCH_PID, "orch-session"), (REPO_PID, "alpha-session")):
            self.records[pid].update(cwd="/work/invoice-exporter", sessionId=session)
            self.add(self.transcript(pid), self.reply(at(MONDAY, "08:30")))
        self.probes: list[str] = []
        self.probe_outcome = "usage-limit"
        self.prompts: list[tuple[str, str]] = []
        self.send_ok = True

    def transcript(self, pid: int) -> Path:
        return claude_state.transcript_path("/work/invoice-exporter", self.records[pid]["sessionId"])

    def subagent(self, pid: int) -> Path:
        transcript = self.transcript(pid)
        return transcript.parent / transcript.stem / "subagents" / "agent-a1.jsonl"

    def add(self, path: Path, entry: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def reply(self, moment: datetime, **extra: Any) -> dict[str, Any]:
        message = {"model": "claude-model", "content": [{"type": "text", "text": "Done."}]}
        return {"type": "assistant", "timestamp": iso(moment), "message": message, **extra}

    def limit(self, moment: datetime, **extra: Any) -> dict[str, Any]:
        message = {"model": "<synthetic>", "content": [{"type": "text", "text": "Spend limit reached"}]}
        fields = {"isApiErrorMessage": True, "error": "rate_limit", "apiError": "usage_limit_reached"}
        return {"type": "assistant", "timestamp": iso(moment), "message": message, **fields, **extra}

    def hold(self, item_id: str, window: str) -> None:
        item = {"id": item_id, "kind": "task", "state": "delegated", "delegation": {"window": window}}
        path = self.state_dir / "ledger.json"
        data = json.loads(path.read_text()) if path.exists() else {"items": {}}
        data["items"][item_id] = item
        path.write_text(json.dumps(data))

    def probe(self, _claude_bin: str) -> claude_state.ProbeResult:
        self.probes.append(self.probe_outcome)
        outcome = claude_state.PROBE_OK if self.probe_outcome == "ok" else self.probe_outcome
        return claude_state.ProbeResult(outcome)

    def send(self, window: str, prompt: str) -> bool:
        self.prompts.append((window, prompt))
        return self.send_ok

    def inputs(self, now: datetime, facts: Facts | None = None) -> Inputs:
        return replace(super().inputs(now, facts), probe=self.probe, send=self.send)

    def test_a_stopped_window_alerts_once_with_the_items_it_holds(self) -> None:
        self.hold("task:2026-01-05/1", "alpha")
        self.add(self.transcript(REPO_PID), self.limit(at(MONDAY, "09:00")))
        sent = self.watch(at(MONDAY, "09:05"), only="spend-limit")
        self.assertEqual(len(sent), 1)
        self.assertIn("alpha: it stopped on a usage limit (spend limit) at Mon 09:00", sent[0])
        self.assertIn("it holds task:2026-01-05/1", sent[0])
        self.add(self.transcript(REPO_PID), self.limit(at(MONDAY, "09:20")))
        self.assertEqual(self.runs(at(MONDAY, "09:25"), 3, only="spend-limit"), [])

    def test_a_stop_only_the_hook_saw_alerts(self) -> None:
        failure = {
            "session_id": "alpha-session",
            "hook_event_name": "StopFailure",
            "error": "rate_limit",
            "last_assistant_message": "You've hit your individual spend limit",
        }
        add_signal(self.state_dir, "alpha", at(MONDAY, "09:00"), failure)
        sent = self.watch(at(MONDAY, "09:05"), only="spend-limit")
        self.assertIn("alpha: it stopped on a usage limit (spend limit) at Mon 09:00", sent[0])
        stop = {"session_id": "alpha-session", "hook_event_name": "Stop", "background_tasks": []}
        add_signal(self.state_dir, "alpha", at(MONDAY, "09:30"), stop)
        self.assertEqual(watchdog.check_spend_limit(self.context(at(MONDAY, "09:35"))).findings, [])

    def test_a_short_rate_limit_is_not_a_stop(self) -> None:
        self.add(self.transcript(REPO_PID), self.limit(at(MONDAY, "09:00"), apiError=None))
        self.assertEqual(self.watch(at(MONDAY, "09:05"), only="spend-limit"), [])

    def test_a_stopped_subagent_alerts_and_an_old_one_does_not(self) -> None:
        self.add(self.subagent(REPO_PID), {**self.limit(at(MONDAY, "09:00")), "isSidechain": True})
        sent = self.watch(at(MONDAY, "09:05"), only="spend-limit")
        self.assertIn("alpha: 1 of its subagent(s) stopped on a usage limit", sent[0])
        old = (at(MONDAY, "09:05") - timedelta(hours=watchdog.SUBAGENT_HOURS + 1)).timestamp()
        os.utime(self.subagent(REPO_PID), (old, old))
        self.assertEqual(watchdog.check_spend_limit(self.context(at(MONDAY, "09:10"))).findings, [])

    def context(self, now: datetime) -> watchdog.Context:
        return self.inputs(now).context(watchdog.State.load(self.state_dir))

    def test_the_window_is_asked_to_resume_once_the_probe_works(self) -> None:
        self.add(self.transcript(REPO_PID), self.limit(at(MONDAY, "09:00")))
        self.watch(at(MONDAY, "09:05"))
        self.assertEqual((self.probes, self.prompts), (["usage-limit"], []))
        self.watch(at(MONDAY, "09:10"))
        self.assertEqual(len(self.probes), 1)  # at most every LIMIT_PROBE_MINUTES
        self.probe_outcome = "ok"
        self.watch(at(MONDAY, "09:40"))
        self.assertEqual(self.prompts, [("alpha", watchdog.RESUME_PROMPT)])
        self.add(self.transcript(REPO_PID), self.limit(at(MONDAY, "09:00")))
        self.watch(at(MONDAY, "09:45"))
        self.assertEqual(len(self.prompts), 1)  # asked once: it has not replied yet
        self.add(self.transcript(REPO_PID), self.limit(at(MONDAY, "09:50")))  # it failed again
        self.probe_outcome = "usage-limit"
        self.watch(at(MONDAY, "10:15"))
        self.assertEqual((len(self.prompts), self.probes[-1]), (1, "usage-limit"))
        self.probe_outcome = "ok"
        self.watch(at(MONDAY, "10:45"))
        self.assertEqual(len(self.prompts), 2)  # a newer working probe, after the newer failure

    def test_a_normal_reply_ends_the_stop_and_resumes_it_without_a_probe(self) -> None:
        self.probe_outcome = "usage-limit"
        self.add(self.transcript(REPO_PID), self.limit(at(MONDAY, "09:00")))
        self.watch(at(MONDAY, "09:05"))
        self.add(self.transcript(REPO_PID), self.reply(at(MONDAY, "09:20")))
        self.watch(at(MONDAY, "09:25"))
        self.assertEqual(self.prompts, [("alpha", watchdog.RESUME_PROMPT)])
        self.assertEqual(self.probes, ["usage-limit"])
        self.assertEqual(self.state()["limits"], {})

    def test_a_failed_send_is_retried_and_a_restarted_claude_is_left_to_its_drain_prompt(self) -> None:
        self.add(self.transcript(REPO_PID), self.limit(at(MONDAY, "09:00")))
        self.watch(at(MONDAY, "09:05"))
        self.add(self.transcript(REPO_PID), self.reply(at(MONDAY, "09:20")))
        self.send_ok = False
        self.watch(at(MONDAY, "09:25"))
        self.assertIn("alpha", self.state()["limits"])
        restarted = Facts(**{**self.facts.__dict__, "windows": (WindowFact("alpha", 999),)})
        self.watch(at(MONDAY, "09:30"), facts=restarted)
        self.assertEqual(self.state()["limits"], {})
        self.assertEqual(len(self.prompts), 1)

    def test_dry_run_and_status_make_no_probe_and_send_nothing(self) -> None:
        self.add(self.transcript(REPO_PID), self.limit(at(MONDAY, "09:00")))
        self.watch(at(MONDAY, "09:05"))
        out = io.StringIO()
        with redirect_stdout(out):
            watchdog.run_dry(self.inputs(at(MONDAY, "09:40")), CHECKS, sinks=(self.sink,))
        self.assertIn("dry-run: spend-limit: alpha: still stopped; a run would probe", out.getvalue())
        status = watchdog.render_status(self.inputs(at(MONDAY, "09:40")), CHECKS)
        self.assertIn("spend-limit", status)
        self.assertEqual((len(self.probes), self.prompts), (1, []))


class OverheadChecksTest(WatchdogCase):
    """context-size (a conversation that stays long) and overhead-share (from the Impact snapshot)."""

    def setUp(self) -> None:
        super().setUp()
        self.morning_run(MONDAY)
        self.morning_run(TUESDAY)
        patch = unittest.mock.patch.object(claude_state, "PROJECTS_DIR", self.state_dir / "projects")
        patch.start()
        self.addCleanup(patch.stop)
        self.records[REPO_PID].update(cwd="/work/invoice-exporter", sessionId="alpha-session")
        self.path = claude_state.transcript_path("/work/invoice-exporter", "alpha-session")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def reply(self, moment: datetime, context: int) -> None:
        usage = {"input_tokens": 1000, "cache_read_input_tokens": context - 1000, "output_tokens": 50}
        entry = {"type": "assistant", "timestamp": iso(moment), "message": {"model": "m", "usage": usage}}
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def test_a_long_conversation_for_a_day_warns_once(self) -> None:
        self.reply(at(MONDAY, "08:00"), 120_000)
        self.reply(at(MONDAY, "08:30"), 320_000)
        self.reply(at(MONDAY, "17:00"), 350_000)
        self.assertEqual(self.watch(at(MONDAY, "18:00"), only="context-size"), [])  # not a day yet
        self.reply(at(TUESDAY, "09:00"), 380_000)
        sent = self.watch(at(TUESDAY, "09:05"), only="context-size")
        self.assertEqual(len(sent), 1)
        self.assertIn("the alpha window's conversation has re-read 380k tokens a turn for 25 h", sent[0])
        self.assertEqual(self.watch(at(TUESDAY, "09:10"), only="context-size"), [])

    def test_a_compacted_conversation_starts_over(self) -> None:
        self.reply(at(MONDAY, "08:30"), 320_000)
        self.reply(at(TUESDAY, "08:00"), 60_000)  # after /compact
        self.reply(at(TUESDAY, "09:00"), 310_000)
        self.assertEqual(self.watch(at(TUESDAY, "09:05"), only="context-size"), [])

    def snapshot(self, collected: datetime, work: int, overhead: int) -> None:
        def bucket(cache_read: int) -> dict[str, Any]:
            usage = {"claude-opus-5-5": {"input": 0, "output": 0, "cache_write": 0, "cache_read": cache_read}}
            return {"turns": 1, "usage": usage, "contexts": [100_000]}

        row = {
            "window": "orch",
            "day": (collected.date() - timedelta(days=1)).isoformat(),
            "triggers": {"work": bucket(work), "check-in": bucket(overhead)},
        }
        (self.state_dir / "impact").mkdir(exist_ok=True)
        data = {"collected_at": iso(collected), "activity": [row], "guardrails": [], "settings": {}}
        (self.state_dir / "impact" / "snapshot.json").write_text(json.dumps(data))

    def test_overhead_over_the_share_warns_once_a_week(self) -> None:
        self.assertEqual(self.watch(at(TUESDAY, "09:00"), only="overhead-share"), [])  # no snapshot
        self.snapshot(at(TUESDAY, "08:00"), work=1_000_000, overhead=3_000_000)
        sent = self.watch(at(TUESDAY, "09:05"), only="overhead-share")
        self.assertEqual(len(sent), 1)
        self.assertIn(
            "bosun's own turns (check-ins, events, notifications, resumes) cost 75% of the last 7 days",
            sent[0],
        )
        self.assertEqual(self.watch(at(TUESDAY, "09:10"), only="overhead-share"), [])

    def test_overhead_under_the_share_or_a_stale_snapshot_is_quiet(self) -> None:
        self.snapshot(at(TUESDAY, "08:00"), work=3_000_000, overhead=1_000_000)
        self.assertEqual(self.watch(at(TUESDAY, "09:05"), only="overhead-share"), [])
        self.snapshot(at(MONDAY, "08:00") - timedelta(days=5), work=1_000_000, overhead=3_000_000)
        result = watchdog.check_overhead_share(
            self.inputs(at(TUESDAY, "09:10")).context(watchdog.State.load(self.state_dir))
        )
        self.assertIn("over 3 days old", result.note)
