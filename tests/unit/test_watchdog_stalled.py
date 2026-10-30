"""lib/watchdog.py's stalled-start check: a running Claude that never reached a session."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import claude_state
import watchdog


NOW = datetime(2026, 11, 2, 9, 0)


def context(*windows: watchdog.WindowFact) -> Any:
    return SimpleNamespace(now=NOW, facts=SimpleNamespace(windows=windows))


class StalledStartTest(unittest.TestCase):
    def check(self, minutes: float) -> watchdog.Result:
        started = NOW.timestamp() - minutes * 60
        with mock.patch.object(claude_state, "process_start", return_value=started):
            return watchdog.check_stalled_start(context(watchdog.WindowFact("api", None, 123)))

    def test_no_session_after_a_while_is_a_stall(self) -> None:
        findings = self.check(6).findings
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].key, "stalled-start:api:123")
        self.assertIn("api", findings[0].message)
        self.assertIn("bosun attach", findings[0].message)
        self.assertIn("Do not just press Return", findings[0].message)

    def test_a_normal_start_gets_time(self) -> None:
        self.assertEqual(self.check(1).findings, [])

    def test_a_window_with_a_session_or_no_claude_is_not_this_check_s_business(self) -> None:
        with_session = watchdog.WindowFact("api", 123)
        without_claude = watchdog.WindowFact("api", None)
        self.assertEqual(watchdog.check_stalled_start(context(with_session, without_claude)).findings, [])

    def test_a_process_that_has_gone_is_not_a_stall(self) -> None:
        with mock.patch.object(claude_state, "process_start", return_value=None):
            gone = watchdog.WindowFact("api", None, 123)
            self.assertEqual(watchdog.check_stalled_start(context(gone)).findings, [])

    def test_the_facts_carry_it(self) -> None:
        facts = watchdog.Facts.from_json(
            {"windows": [{"name": "api", "claude_pid": None, "starting_pid": 123}]}
        )
        self.assertEqual(facts.windows, (watchdog.WindowFact("api", None, 123),))

    def test_it_is_in_the_table(self) -> None:
        self.assertIn("stalled-start", [check.name for check in watchdog.CHECKS])


if __name__ == "__main__":
    unittest.main()
