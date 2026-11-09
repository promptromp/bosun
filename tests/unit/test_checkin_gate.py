"""Unit tests for lib/checkin_gate.py: a check-in goes out only when something is new since the
last one, or after the longest quiet stretch allowed."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import checkin_gate as gate


NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)  # a Tuesday
LAST = NOW - timedelta(hours=1)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def inputs(**changes: Any) -> gate.Inputs:
    quiet_run = {"started": iso(NOW - timedelta(minutes=10)), "found": 0}
    base: dict[str, Any] = {
        "now": NOW,
        "last": LAST,
        "items": {},
        "pending_events": 0,
        "slack": {"failures": 0, "runs": [quiet_run]},
        "last_alert": None,
        "max_quiet": timedelta(hours=4),
    }
    return gate.Inputs(**{**base, **changes})


def outreach(sent: datetime, **fields: Any) -> dict[str, Any]:
    return {
        "id": "out:slack:C0/1/1",
        "kind": "outreach",
        "state": "sent",
        "fields": {"messages": [{"at": iso(sent)}], **fields},
    }


class FindingsTest(unittest.TestCase):
    def test_nothing_new_means_no_check_in(self) -> None:
        self.assertEqual(gate.findings(inputs()), [])

    def test_no_recorded_check_in_or_a_long_quiet_stretch_sends_one(self) -> None:
        self.assertEqual(gate.findings(inputs(last=None)), ["no check-in recorded yet"])
        self.assertEqual(
            gate.findings(inputs(last=NOW - timedelta(hours=5)))[0], "5.0 h since the last check-in"
        )

    def test_undelivered_events_are_news(self) -> None:
        self.assertEqual(gate.findings(inputs(pending_events=2)), ["2 event(s) not yet delivered"])

    def test_slack_found_failing_stalled_or_off_is_news(self) -> None:
        found = {"failures": 0, "runs": [{"started": iso(NOW - timedelta(minutes=5)), "found": 3}]}
        self.assertEqual(gate.findings(inputs(slack=found)), ["3 Slack message(s) found since"])
        self.assertIn("failing", gate.findings(inputs(slack={"failures": 2, "runs": []}))[0])
        stale = {"failures": 0, "runs": [{"started": iso(LAST - timedelta(minutes=5)), "found": 9}]}
        self.assertIn("has not run since", gate.findings(inputs(slack=stale))[0])
        self.assertIn("is off", gate.findings(inputs(slack=None))[0])

    def test_a_report_since_the_last_check_in_is_news(self) -> None:
        items = {
            "task:2026-10-27/1": {
                "id": "task:2026-10-27/1",
                "report": {"at": iso(NOW - timedelta(minutes=3))},
            },
            "task:2026-10-26/1": {
                "id": "task:2026-10-26/1",
                "report": {"at": iso(LAST - timedelta(hours=1))},
            },
        }
        self.assertEqual(gate.findings(inputs(items=items)), ["report(s) on task:2026-10-27/1"])

    def test_outreach_is_news_once_its_wait_crosses_a_business_day(self) -> None:
        with mock.patch.dict(os.environ, {"TZ": "UTC"}):  # the gate counts in this Mac's zone
            time.tzset()
            self.addCleanup(time.tzset)
            self.check_crossing_in_the_local_zone()

    def check_crossing_in_the_local_zone(self) -> None:
        crossed = outreach(NOW - timedelta(days=1))
        overnight = NOW.replace(hour=0) - timedelta(minutes=30)
        first_check_of_the_day = inputs(items={"o": crossed}, last=overnight, max_quiet=timedelta(hours=24))
        self.assertIn("out:slack:C0/1/1", gate.findings(first_check_of_the_day)[0])
        self.assertEqual(gate.findings(inputs(items={"o": crossed})), [])  # already counted today

    def test_a_business_day_is_crossed_at_local_midnight_not_utc_midnight(self) -> None:
        east = timezone(timedelta(hours=2))
        sent = "2026-11-09T10:00:00Z"  # Monday
        before = datetime(2026, 11, 10, 21, 30, tzinfo=UTC)  # Tuesday 23:30 local
        now = datetime(2026, 11, 10, 22, 30, tzinfo=UTC)  # Wednesday 00:30 local, still Tuesday in UTC
        self.assertTrue(gate.crossed_a_business_day(sent, before, now, east))
        self.assertFalse(gate.crossed_a_business_day(sent, before, now, UTC))

    def test_a_passed_expect_by_is_news_once(self) -> None:
        due = outreach(NOW - timedelta(minutes=90), expect_by=iso(NOW - timedelta(minutes=20)))
        self.assertIn("out:slack:C0/1/1", gate.findings(inputs(items={"o": due}))[0])

    def test_a_new_watchdog_alert_is_news(self) -> None:
        self.assertEqual(
            gate.findings(inputs(last_alert=NOW - timedelta(minutes=1))), ["a new watchdog alert"]
        )
        self.assertEqual(gate.findings(inputs(last_alert=LAST - timedelta(minutes=1))), [])


class MainTest(unittest.TestCase):
    def test_reads_the_state_dir_and_prints_its_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "ledger.json").write_text(json.dumps({"items": {}, "runs": {}}))
            (state / "events").mkdir()
            (state / "events" / "queue.jsonl").write_text(json.dumps({"id": "e1"}) + "\n")
            (state / "events" / "state.json").write_text(json.dumps({"delivered": {"e1": "send now"}}))
            self.assertEqual(gate.pending_events(state), 0)
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                verdict = gate.main(["--state-dir", tmp, "--slack-watch", "0"])
            self.assertEqual((verdict, printed.getvalue()), (gate.NEWS, "news: no check-in recorded yet\n"))
