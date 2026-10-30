"""Unit tests for lib/impact_attribution.py: which ledger item each turn's tokens go to."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import impact_attribution as ia
import ledger


WINDOW = "exporter"
SINCE = datetime(2026, 3, 1, tzinfo=UTC)
HORIZON = timedelta(hours=8)
TERMINAL = {"done", "dropped", "merged", "closed"}


def event(at: str, name: str, **extra: Any) -> dict[str, Any]:
    return {"at": at, "event": name, **extra}


def item(item_id: str, *history: dict[str, Any], state: str = "delegated", **extra: Any) -> dict[str, Any]:
    return {"id": item_id, "kind": "task", "state": state, "title": f"title of {item_id}",
            "history": list(history), **extra}  # fmt: skip


def is_open(found: Any) -> bool:
    return found.get("state") not in TERMINAL


def record(at: str, *ids: str, output: int = 1_000_000) -> dict[str, Any]:
    return {"at": at, "ids": list(ids), "usage": {"claude-opus-5-5": {"output": output}}}


def timeline(*found: dict[str, Any]) -> ia.Timeline:
    return ia.Timeline.of({i["id"]: i for i in found}, is_open, HORIZON)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


FOOTER = item(
    "task:2026-03-02/1",
    event("2026-03-02T09:00:00+00:00", "delegated", window=WINDOW, task="new-work"),
    event("2026-03-02T11:00:00+00:00", "report", window=WINDOW),
    delegation={"window": WINDOW, "task": "new-work"},
)
HEADER = item(
    "task:2026-03-02/2",
    event("2026-03-02T14:00:00+00:00", "report", window=WINDOW),
    delegation={"window": WINDOW, "task": "investigate"},
)


class RuleTest(unittest.TestCase):
    def test_a_handover_names_its_item(self) -> None:
        found = timeline(FOOTER, HEADER).item_at(
            WINDOW, at("2026-03-02T13:00:00+00:00"), ["task:2026-03-02/1"]
        )
        self.assertEqual(found, ("task:2026-03-02/1", ia.HANDOVER))

    def test_an_unknown_handover_id_falls_through(self) -> None:
        found = timeline(FOOTER).item_at(WINDOW, at("2026-03-02T09:30:00+00:00"), ["task:gone/1"])
        self.assertEqual(found, ("task:2026-03-02/1", ia.REPORT))

    def test_work_before_a_report_goes_to_the_reported_item(self) -> None:
        # asked for directly in the window, no hand-over: the report says what it was
        found = timeline(FOOTER, HEADER).item_at(WINDOW, at("2026-03-02T12:00:00+00:00"), [])
        self.assertEqual(found, ("task:2026-03-02/2", ia.REPORT))

    def test_the_active_item_after_its_last_event(self) -> None:
        found = timeline(FOOTER).item_at(WINDOW, at("2026-03-02T15:00:00+00:00"), [])
        self.assertEqual(found, ("task:2026-03-02/1", ia.ACTIVE))

    def test_nothing_past_the_horizon(self) -> None:
        self.assertIsNone(timeline(FOOTER).item_at(WINDOW, at("2026-03-02T20:00:00+00:00"), []))
        self.assertIsNone(timeline(FOOTER).item_at(WINDOW, at("2026-03-01T20:00:00+00:00"), []))

    def test_idle_ends_the_active_item(self) -> None:
        idle = item("task:2026-03-02/3", event("2026-03-02T12:00:00+00:00", "idle", window=WINDOW))
        self.assertIsNone(timeline(FOOTER, idle).item_at(WINDOW, at("2026-03-02T13:00:00+00:00"), []))

    def test_a_closed_item_is_no_longer_active(self) -> None:
        closed = item(
            "task:2026-03-02/1",
            *FOOTER["history"],
            event("2026-03-02T12:00:00+00:00", "transition", to="done"),
            state="done",
        )
        line = timeline(closed)
        self.assertEqual(
            line.item_at(WINDOW, at("2026-03-02T11:30:00+00:00"), []), ("task:2026-03-02/1", ia.ACTIVE)
        )
        self.assertIsNone(line.item_at(WINDOW, at("2026-03-02T12:30:00+00:00"), []))

    def test_another_window_or_an_empty_ledger_attributes_nothing(self) -> None:
        self.assertIsNone(timeline(FOOTER).item_at("other", at("2026-03-02T10:00:00+00:00"), []))
        self.assertIsNone(timeline().item_at(WINDOW, at("2026-03-02T10:00:00+00:00"), ["task:2026-03-02/1"]))


class TallyTest(unittest.TestCase):
    def tasks(
        self, *sessions: ia.Session, items: tuple[dict[str, Any], ...] = (FOOTER, HEADER)
    ) -> dict[str, Any]:
        return ia.snapshot_tasks(sessions, {i["id"]: i for i in items}, is_open, SINCE, HORIZON)

    def test_subagents_roll_up_to_their_parent_turn(self) -> None:
        session = ia.Session(
            WINDOW,
            [record("2026-03-02T09:00:00+00:00", "task:2026-03-02/1"), record("2026-03-02T23:00:00+00:00")],
            [[record("2026-03-02T09:10:00+00:00", output=2_000_000)], [record("2026-03-02T23:30:00+00:00")]],
        )
        found = self.tasks(session)
        (row,) = found["items"]
        self.assertEqual(row["id"], "task:2026-03-02/1")
        self.assertEqual((row["turns"], row["usage"]["claude-opus-5-5"]["output"]), (1, 1_000_000))
        self.assertEqual(row["subagent_usage"]["claude-opus-5-5"]["output"], 2_000_000)
        self.assertEqual((row["outcome"], row["task"], row["windows"]), ("open", "new-work", [WINDOW]))
        # the evening turn and its subagent are past the horizon: unattributed, kept per window
        (rest,) = found["unattributed"]
        self.assertEqual((rest["window"], rest["turns"]), (WINDOW, 1))
        self.assertEqual(rest["subagent_usage"]["claude-opus-5-5"]["output"], 1_000_000)
        self.assertEqual(set(found["rules"]), {ia.HANDOVER})

    def test_a_subagent_before_any_parent_turn_uses_its_own_start(self) -> None:
        session = ia.Session(WINDOW, [], [[record("2026-03-02T10:00:00+00:00")]])
        (row,) = self.tasks(session)["items"]
        self.assertEqual((row["id"], row["turns"]), ("task:2026-03-02/1", 0))

    def test_turns_before_the_start_are_left_out(self) -> None:
        found = self.tasks(ia.Session(WINDOW, [record("2026-02-27T09:00:00+00:00", "task:2026-03-02/1")]))
        self.assertEqual((found["items"], found["unattributed"]), ([], []))

    def test_no_ledger_at_all(self) -> None:
        found = self.tasks(ia.Session(WINDOW, [record("2026-03-02T09:00:00+00:00")]), items=())
        self.assertEqual(found["items"], [])
        self.assertEqual(found["unattributed"][0]["window"], WINDOW)


class OutcomeTest(unittest.TestCase):
    def test_outcomes(self) -> None:
        cases = [
            ({"kind": "task", "state": "delegated"}, ia.OPEN),
            ({"kind": "my-pr", "state": "merged"}, ia.MERGED),
            ({"kind": "task", "state": "done"}, ia.DONE),
            ({"kind": "task", "state": "dropped"}, ia.DROPPED),
            ({"kind": "my-pr", "state": "closed"}, ia.DROPPED),
            ({"kind": "review-request", "state": "closed"}, ia.DONE),
        ]
        for found, expected in cases:
            with self.subTest(found=found):
                self.assertEqual(ia.outcome(found, is_open), expected)

    def test_the_outcome_states_are_the_ledgers_own(self) -> None:
        terminal = {(kind, state) for kind, spec in ledger.KINDS.items() for state in spec["terminal"]}
        self.assertLessEqual(ia.DROPPED_STATES | {ia.MERGED}, {state for _, state in terminal})
        self.assertLessEqual(ia.DROPPED_BY_KIND, terminal)

    def test_the_pr_an_item_is_or_produced(self) -> None:
        pr = "https://github.com/acme/exporter/pull/7"
        items = {"mypr:acme/exporter#7": {"url": pr}}
        produced = {"links": [{"rel": "produces", "id": "mypr:acme/exporter#7"}]}
        self.assertEqual(ia.pr_link({"url": pr}, {}), pr)
        self.assertEqual(ia.pr_link(produced, items), pr)
        self.assertIsNone(ia.pr_link({"url": "https://example.com/thread"}, items))


if __name__ == "__main__":
    unittest.main()
