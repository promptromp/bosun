"""Unit tests for the watchdog's `learn` check: the daily learning run is missing, failed, crashed
or stalled (lib/watchdog.py, reading $BOSUN_STATE_DIR/learn/state.json)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import watchdog
from watchdog import Config, Facts, Inputs, State


MONDAY = date(2026, 1, 5)
PID = 4242


def at(hhmm: str, day: date = MONDAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm))


def utc(moment: datetime) -> str:
    return moment.astimezone().astimezone(UTC).isoformat(timespec="seconds")


class LearnCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_dir = Path(tmp.name)
        self.config = Config(
            state_dir=self.state_dir,
            log_dir=self.state_dir / "logs",
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
            learn_enabled=True,
            learn_at=time(18, 0),
            learn_timeout_minutes=30,
        )
        self.alive = True

    def learn_state(self, **state: Any) -> None:
        folder = self.state_dir / "learn"
        folder.mkdir(exist_ok=True)
        (folder / "state.json").write_text(json.dumps({"runs": [], "running": None, **state}))

    def check(self, now: datetime) -> watchdog.Result:
        inputs = Inputs(self.config, Facts(), now, lambda _pid: None, lambda _pid: self.alive)
        return watchdog.check_learn(inputs.context(State()))

    def keys(self, now: datetime) -> list[str]:
        return [f.key for f in self.check(now).findings]

    def test_off_unless_enabled(self) -> None:
        self.config = Config(**{**self.config.__dict__, "learn_enabled": False})
        self.assertEqual(self.check(at("19:30")).note, "off (BOSUN_LEARN_ENABLED=0)")

    def test_missing_after_the_grace_period_and_confirmed(self) -> None:
        self.learn_state()
        self.assertEqual(self.keys(at("18:30")), [])
        [finding] = self.check(at("19:05")).findings
        self.assertEqual(finding.key, "learn:missing:2026-01-05")
        self.assertTrue(finding.confirm)
        self.assertEqual(self.keys(at("19:05", MONDAY + timedelta(days=5))), [])  # Saturday

    def test_a_run_today_is_not_missing(self) -> None:
        self.learn_state(runs=[{"started": utc(at("18:01")), "outcome": "ok"}])
        self.assertEqual(self.keys(at("19:05")), [])

    def test_a_failed_run_alerts_once_per_run(self) -> None:
        self.learn_state(
            runs=[{"started": utc(at("18:01")), "outcome": "failed", "error": "claude exited 1"}]
        )
        [finding] = self.check(at("18:10")).findings
        self.assertEqual(finding.key, "learn:failed:2026-01-05T18:01")
        self.assertIn("claude exited 1", finding.message)

    def test_running_too_long_is_stalled(self) -> None:
        self.learn_state(running={"pid": PID, "started": utc(at("18:01"))})
        self.assertEqual(self.check(at("18:20")).note, "running since 18:01")
        self.assertEqual(self.keys(at("18:40")), ["learn:stalled:2026-01-05T18:01"])

    def test_a_run_that_died_without_an_outcome_crashed(self) -> None:
        self.learn_state(running={"pid": PID, "started": utc(at("18:01"))})
        self.alive = False
        self.assertEqual(self.keys(at("18:05")), ["learn:crashed:2026-01-05T18:01"])

    def test_an_unreadable_state_file_alerts(self) -> None:
        (self.state_dir / "learn").mkdir()
        (self.state_dir / "learn" / "state.json").write_text("{")
        self.assertEqual(self.keys(at("12:00")), ["learn:unreadable:2026-01-05"])

    def test_learn_log_errors_are_left_to_this_check(self) -> None:
        self.assertFalse(watchdog.is_alerting_error("2026-01-05 18:02:00 [learn] ERROR: claude exited 1"))


if __name__ == "__main__":
    unittest.main()
