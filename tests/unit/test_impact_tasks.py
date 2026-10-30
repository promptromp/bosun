"""Unit tests for lib/impact_tasks.py: the Cost per task panel over a snapshot's `tasks`."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import impact_tasks as it


OPUS = "claude-opus-5-5"  # $20 per million output tokens


def usage(output: int, model: str = OPUS) -> dict[str, dict[str, int]]:
    return {model: {"output": output}}


def row(item_id: str, output: int, sub: int = 0, **extra: Any) -> dict[str, Any]:
    return {"id": item_id, "kind": "task", "task": "new-work", "outcome": "done", "title": item_id,
            "turns": 2, "windows": ["exporter"], "usage": usage(output),
            "subagent_usage": usage(sub) if sub else {}, **extra}  # fmt: skip


def snapshot(
    items: list[dict[str, Any]], rest: list[dict[str, Any]] | None = None, **settings: Any
) -> dict[str, Any]:
    tasks = {"since": "2026-03-01T00:00:00+00:00", "items": items, "unattributed": rest or [],
             "rules": {"handover": usage(1_000_000)}}  # fmt: skip
    return {"tasks": tasks, "settings": {"attribution_hours": 8, **settings}}


class ModelTest(unittest.TestCase):
    def test_cost_shares_and_groups(self) -> None:
        model = it.from_snapshot(
            snapshot(
                [
                    row("task:a", 1_000_000, sub=1_000_000),  # $20 + $20 in subagents
                    row("task:b", 500_000, kind="my-pr", task="fix-my-pr", outcome="merged"),  # $10
                    row("task:c", 250_000, task="", outcome="open"),  # $5, never handed over
                ],
                [{"window": "exporter", "turns": 3, "usage": usage(250_000), "subagent_usage": {}}],  # $5
            )
        )
        self.assertTrue(model["exists"])
        self.assertEqual(
            (model["total"], model["attributed"], model["attributed_share"]), (60.0, 55.0, 0.917)
        )
        self.assertEqual(model["subagent_share"], 0.364)  # 20 of 55
        self.assertEqual([g["key"] for g in model["by_kind"]], ["task", "my-pr"])
        self.assertEqual(model["by_kind"][0]["cost"], 45.0)
        self.assertEqual({g["key"] for g in model["by_task"]}, {"new-work", "fix-my-pr", it.NOT_HANDED_OVER})
        self.assertEqual([g["key"] for g in model["by_outcome"]], ["done", "merged", "open"])
        self.assertEqual(model["rules"]["handover"], 20.0)
        self.assertEqual(model["unattributed"], [{"window": "exporter", "turns": 3, "cost": 5.0}])
        self.assertEqual([t["id"] for t in model["top"]], ["task:a", "task:b", "task:c"])
        self.assertEqual((model["top"][0]["cost"], model["top"][0]["subagent_cost"]), (40.0, 20.0))

    def test_the_top_list_is_configurable(self) -> None:
        items = [row(f"task:{n}", 1000 * n) for n in range(1, 6)]
        self.assertEqual(len(it.from_snapshot(snapshot(items, tasks_top=2))["top"]), 2)
        self.assertEqual(len(it.from_snapshot(snapshot(items))["top"]), 5)  # default 15

    def test_an_unknown_model_is_priced_by_the_fallback_and_named(self) -> None:
        model = it.from_snapshot(snapshot([row("task:a", 1000, usage={"some-new-model": {"output": 1000}})]))
        self.assertEqual(model["unpriced"], ["some-new-model"])
        self.assertIn("unpriced models (priced as opus): some-new-model", it.summary(model))

    def test_no_data_yet(self) -> None:
        empties: tuple[dict[str, Any] | None, ...] = (None, {}, {"tasks": {}}, snapshot([]))
        for empty in empties:
            with self.subTest(snapshot=empty):
                model = it.from_snapshot(empty)
                self.assertEqual(model, {"exists": False})
                self.assertEqual(it.summary(model), "cost per task: no turns yet")

    def test_only_unattributed_turns(self) -> None:
        model = it.from_snapshot(snapshot([], [{"window": "solo", "turns": 1, "usage": usage(50_000)}]))
        self.assertEqual((model["attributed_share"], model["subagent_share"], model["top"]), (0.0, None, []))
        self.assertEqual(it.summary(model), "cost per task: 0 items, 0% of $1.00 attributed")


if __name__ == "__main__":
    unittest.main()
