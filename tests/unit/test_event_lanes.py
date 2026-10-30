"""Unit tests for lib/event_lanes.py: which lane an event takes, each lane's debounce and longest
wait, and the away timing while nobody types in tmux."""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import event_lanes
from event_lanes import Lanes, Timing


NOW = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
LANES = Lanes(
    routine=Timing(600, 1200),
    fast=Timing(45, 300),
    away=Timing(1200, 3600),
    fast_kinds=frozenset({"slack", "waiting"}),
    away_minutes=60,
)


@dataclass(frozen=True)
class Ev:
    at: str
    type: str


def ev(seconds_ago: float, kind: str = "report") -> Ev:
    return Ev((NOW - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds"), kind)


def due(*events: Ev, idle: float | None = 0, lanes: Lanes = LANES) -> str | None:
    return event_lanes.due(lanes, list(events), idle, NOW)


class LaneSelectionTest(unittest.TestCase):
    def test_fast_kinds_go_in_the_fast_lane_and_the_rest_in_the_routine_one(self) -> None:
        lanes = LANES.split([ev(9, "report"), ev(8, "slack"), ev(7, "waiting"), ev(6, "spawn")], away=False)
        self.assertEqual(
            [(name, [e.type for e in group]) for name, _timing, group in lanes],
            [("fast", ["slack", "waiting"]), ("routine", ["report", "spawn"])],
        )

    def test_a_lane_without_events_is_left_out(self) -> None:
        self.assertEqual([name for name, _t, _g in LANES.split([ev(5)], away=False)], ["routine"])

    def test_with_no_fast_kinds_everything_is_routine(self) -> None:
        lanes = Lanes(LANES.routine, LANES.fast, LANES.away, frozenset(), 0)
        self.assertEqual([name for name, _t, _g in lanes.split([ev(5, "slack")], away=False)], ["routine"])


class TimingTest(unittest.TestCase):
    def test_the_routine_lane_waits_for_its_own_debounce(self) -> None:
        self.assertIn("routine lane debouncing: the newest event is 100s old", due(ev(100)) or "")
        self.assertIsNone(due(ev(600)))

    def test_a_steady_trickle_goes_after_the_longest_wait(self) -> None:
        self.assertIsNone(due(ev(1200), ev(10)))
        self.assertIsNotNone(due(ev(1100), ev(10)))

    def test_a_fast_event_goes_soon_and_takes_the_routine_ones_along(self) -> None:
        self.assertIsNotNone(due(ev(100), ev(10, "slack")))
        self.assertIsNone(due(ev(100), ev(50, "slack")))

    def test_the_reason_names_every_lane_still_waiting(self) -> None:
        reason = due(ev(100), ev(10, "waiting")) or ""
        self.assertIn("fast lane debouncing", reason)
        self.assertIn("routine lane debouncing", reason)


class AwayTest(unittest.TestCase):
    def test_away_after_the_set_minutes_or_with_no_client_attached(self) -> None:
        self.assertFalse(LANES.is_away(59))
        self.assertTrue(LANES.is_away(60))
        self.assertTrue(LANES.is_away(None))

    def test_never_away_when_the_setting_is_zero(self) -> None:
        never = Lanes(LANES.routine, LANES.fast, LANES.away, LANES.fast_kinds, 0)
        self.assertFalse(never.is_away(None))
        self.assertIsNone(due(ev(700), idle=None, lanes=never))

    def test_away_the_routine_lane_waits_longer(self) -> None:
        self.assertIsNone(due(ev(700), idle=10))
        self.assertIn("away lane debouncing", due(ev(700), idle=90) or "")
        self.assertIsNone(due(ev(1300), idle=90))
        self.assertIsNone(due(ev(3600), ev(10), idle=None))

    def test_away_the_fast_lane_keeps_its_timing(self) -> None:
        self.assertIsNone(due(ev(100), ev(50, "slack"), idle=None))


if __name__ == "__main__":
    unittest.main()
