"""Unit tests for lib/impact_overhead.py: classifying turn starts, the Overhead panel and its
guardrails."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import activity
import impact_overhead as io


DRAIN = "Bosun resumed or restarted this session (for example after sleep)."
TODAY = date(2026, 3, 18)  # a Wednesday


def bucket(turns: int, cache_read: int, context: int) -> dict[str, Any]:
    usage = {"claude-opus-5-5": {"input": 0, "output": 0, "cache_write": 0, "cache_read": cache_read}}
    return {"turns": turns, "usage": usage, "contexts": [context] * turns}


def row(day: date, window: str = "orchestrator", **triggers: dict[str, Any]) -> dict[str, Any]:
    return {
        "window": window,
        "day": day.isoformat(),
        "triggers": {k.replace("_", "-"): v for k, v in triggers.items()},
    }


class ClassifyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = io.Classifier((DRAIN,), ("Nightly review",))

    def test_each_kind_of_turn_start(self) -> None:
        cases = {
            "please fix the footer": io.WORK,
            "/bosun:chores check-in": io.CHECK_IN,
            "/bosun:chores morning": io.CHECK_IN,
            "[bosun events] 3 new from the repo sessions": io.EVENTS,
            "<task-notification><task-id>b1</task-id>": io.NOTIFICATION,
            "<cross-session-message from='x'>hi</cross-session-message>": io.CROSS_SESSION,
            DRAIN: io.RESUME,
            "Nightly review (read-only): run the digest": io.SCHEDULED,
            "Update to ledger item task:2026-03-18/1: more": io.HANDOVER,
            "Run this task in a subagent: make one Agent tool call": io.HANDOVER,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.classifier.classify(text), expected)

    def test_a_prompt_claude_code_sent_on_a_schedule_is_overhead(self) -> None:
        self.assertEqual(
            self.classifier.classify("Scheduled check-in on the open PRs", scheduled=True), io.SCHEDULED
        )
        self.assertEqual(
            self.classifier.classify("Another Claude session sent a message", scheduled=True),
            io.CROSS_SESSION,
        )

    def test_bosuns_own_prompts_are_never_my_work(self) -> None:
        for prefix in activity.BOSUN_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertNotEqual(self.classifier.classify(f"{prefix} something"), io.WORK)

    def test_a_resume_prompt_is_known_by_its_opening_whatever_the_spacing(self) -> None:
        self.assertEqual(
            self.classifier.classify("Bosun  resumed or restarted\nthis session (for example after sleep)."),
            io.RESUME,
        )

    def test_the_key_follows_the_classification(self) -> None:
        self.assertNotEqual(self.classifier.key(), io.Classifier((DRAIN,), ()).key())
        self.assertEqual(io.scheduled_prefixes(" a | b ||"), ("a", "b"))

    def test_add_call_counts_the_turn_once(self) -> None:
        triggers: dict[str, Any] = {}
        usage = [("input", 10), ("output", 5), ("cache_write", 0), ("cache_read", 90)]
        io.add_call(triggers, io.CHECK_IN, "claude-opus-5-5", usage, True)
        io.add_call(triggers, io.CHECK_IN, "claude-opus-5-5", usage, False)
        self.assertEqual(triggers[io.CHECK_IN]["turns"], 1)
        self.assertEqual(triggers[io.CHECK_IN]["contexts"], [100])
        self.assertEqual(triggers[io.CHECK_IN]["usage"]["claude-opus-5-5"]["output"], 10)


class ModelTest(unittest.TestCase):
    def rows(self) -> list[dict[str, Any]]:
        found = []
        for offset in range(21):
            day = TODAY - timedelta(days=offset)
            fixed = day >= TODAY - timedelta(days=3)
            found.append(
                row(
                    day,
                    work=bucket(10, 1_000_000, 300_000),
                    check_in=bucket(2 if fixed else 10, 500_000 if fixed else 2_500_000, 200_000),
                )
            )
        found.append(row(TODAY, window="exporter", notification=bucket(4, 1_000_000, 400_000)))
        return found

    def test_no_turn_data_no_panel(self) -> None:
        self.assertEqual(io.overhead_model([{"day": "2026-03-18"}], [], TODAY, None), {"exists": False})

    def test_the_last_seven_days_against_the_seven_before(self) -> None:
        model = io.overhead_model(self.rows(), [], TODAY, None)
        last, previous = model["last"], model["previous"]
        # Opus 5.5 cache reads cost $0.20 per million
        self.assertEqual(previous["cost"], {"work": 1.4, "check-in": 3.5})
        self.assertEqual(previous["share"], round(3.5 / 4.9, 3))
        self.assertEqual(previous["per_turn"], {"check-in": 0.05})
        self.assertEqual(last["turns"]["check-in"], 4 * 2 + 3 * 10)
        self.assertEqual(last["turns"]["notification"], 4)
        self.assertGreater(previous["share"], last["share"])
        self.assertEqual(last["contexts"]["n"], 70 + 38 + 4)

    def test_weeks_and_windows(self) -> None:
        model = io.overhead_model(self.rows(), [], TODAY, None)
        self.assertEqual(
            [w["week"] for w in model["weeks"]], ["2026-02-23", "2026-03-02", "2026-03-09", "2026-03-16"]
        )
        self.assertTrue(model["weeks"][-1]["partial"])
        self.assertEqual(model["windows"][0]["window"], "exporter")  # the largest latest p90 first
        self.assertEqual(model["windows"][0]["weeks"][-1]["p90_k"], 400)
        self.assertEqual({c["key"] for c in model["classes"] if c["overhead"]}, set(io.OVERHEAD))

    def test_before_and_after_a_fix_count_only_the_days_that_passed(self) -> None:
        fixed_at = TODAY - timedelta(days=3)
        model = io.overhead_model(self.rows(), [], TODAY, fixed_at)
        before, after = model["fix"]["before"], model["fix"]["after"]
        self.assertEqual((before["days"], after["days"]), (7, 4))
        self.assertEqual(before["per_turn"]["check-in"], 0.05)
        self.assertEqual(after["per_turn"]["check-in"], round(4 * 0.1 / 8, 2))
        self.assertLess(after["overhead_per_day"], before["overhead_per_day"])

    def test_guardrails_by_week_and_around_the_fix(self) -> None:
        rows = [
            {
                "day": (TODAY - timedelta(days=offset)).isoformat(),
                "tasks_reported": 2,
                "handover_to_report_hours": [1.0, 3.0],
                "events_pending_end": offset % 3,
                "checkins_skipped_nothing_new": 1 if offset < 4 else 0,
            }
            for offset in range(10)
        ]
        model = io.guardrails_model(rows, TODAY, TODAY - timedelta(days=3))
        week = model["weeks"][-1]
        self.assertEqual(
            (week["days"], week["tasks_reported"], week["handover_to_report_hours_median"]), (3, 6, 2.0)
        )
        self.assertEqual(week["events_pending_end_max"], 2)
        self.assertEqual((model["fix"]["before"]["days"], model["fix"]["after"]["days"]), (6, 4))
        self.assertEqual(model["fix"]["after"]["checkins_skipped_nothing_new"], 4)
        self.assertEqual(io.guardrails_model([], TODAY, None), {"exists": False})

    def test_from_a_snapshot(self) -> None:
        snapshot = {
            "activity": self.rows(),
            "guardrails": [],
            "settings": {"overhead_fixed_at": "2026-03-15"},
        }
        model = io.from_snapshot(snapshot, datetime(2026, 3, 18, 12, tzinfo=UTC), UTC)
        self.assertEqual(model["fixed_at"], "2026-03-15")
        self.assertFalse(model["guardrails"]["exists"])
        self.assertEqual(io.from_snapshot(None, datetime.now(UTC), UTC), {"exists": False})


if __name__ == "__main__":
    unittest.main()
