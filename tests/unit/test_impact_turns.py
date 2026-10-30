"""Unit tests for lib/impact_turns.py: a transcript's turns and the records cost per task caches."""

from __future__ import annotations

import sys
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import claude_state
import delegate
import impact_turns


MODEL = "claude-opus-5-5"


def at(minute: int) -> datetime:
    return datetime(2026, 3, 3, 9, minute, tzinfo=UTC)


def call(minute: int, message_id: str, output: int = 10) -> claude_state.Activity:
    usage = (("input", 1), ("output", output), ("cache_write", 0), ("cache_read", 0))
    return claude_state.Activity(at(minute), claude_state.ACTIVITY_ASSISTANT, message_id, MODEL, usage)


def started(minute: int, text: str) -> claude_state.Activity:
    return claude_state.Activity(at(minute), claude_state.ACTIVITY_PROMPT, text=text)


def classify(text: str, scheduled: bool) -> str:
    return "work"


class TurnRecordsTest(unittest.TestCase):
    def test_one_record_per_turn_with_its_handover_ids(self) -> None:
        entries = [
            started(0, "Ledger id: task:2026-03-03/1\nAsk: fix it"),
            call(1, "m1"),
            call(1, "m1"),  # a second content block of the same message
            call(2, "m2"),
            started(5, "thanks"),
            call(6, "m3"),
            started(7, "a turn without a call"),
        ]
        records = impact_turns.turn_records(entries, classify, "work", delegate.handover_ids)
        self.assertEqual([r["ids"] for r in records], [["task:2026-03-03/1"], []])
        self.assertEqual([r["at"] for r in records], [at(0).isoformat(), at(5).isoformat()])
        self.assertEqual(records[0]["usage"][MODEL]["output"], 20)

    def test_a_subagent_is_one_record_from_its_first_call(self) -> None:
        entries = [call(3, "a1"), call(4, "a2", output=0), call(8, "a3")]
        (record,) = impact_turns.turn_records(entries, classify, "subagent", delegate.handover_ids)
        self.assertEqual((record["at"], record["ids"]), (at(3).isoformat(), []))
        self.assertEqual(record["usage"][MODEL]["output"], 20)

    def test_calls_without_tokens_make_no_record(self) -> None:
        synthetic = claude_state.Activity(
            at(1), claude_state.ACTIVITY_ASSISTANT, "s1", "<synthetic>", (("output", 0),)
        )
        self.assertEqual(
            impact_turns.turn_records([started(0, "hi"), synthetic], classify, "work", delegate.handover_ids),
            [],
        )


if __name__ == "__main__":
    unittest.main()


class UsageFieldsTest(unittest.TestCase):
    def entry(self, usage: Mapping[str, object]) -> dict[str, object]:
        return {"type": "assistant", "message": {"id": "m1", "model": MODEL, "usage": usage}}

    def test_one_hour_cache_writes_are_counted_apart(self) -> None:
        usage = {
            "input_tokens": 2,
            "output_tokens": 30,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 50,
            "cache_creation": {"ephemeral_5m_input_tokens": 600, "ephemeral_1h_input_tokens": 400},
        }
        counts = dict(claude_state.assistant_activity(self.entry(usage), at(1)).usage)
        self.assertEqual(counts["cache_write"], 1000)
        self.assertEqual(counts["cache_write_1h"], 400)

    def test_without_the_breakdown_every_write_is_five_minute(self) -> None:
        usage = {"input_tokens": 2, "output_tokens": 30, "cache_creation_input_tokens": 1000}
        counts = dict(claude_state.assistant_activity(self.entry(usage), at(1)).usage)
        self.assertEqual((counts["cache_write"], counts["cache_write_1h"]), (1000, 0))
