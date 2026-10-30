"""Unit tests for lib/impact_learning.py: the Impact view's "What learning has produced" panel."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import impact_learning


def report(*findings: tuple[str, str]) -> str:
    lines = ["# Learning report", "", "## mishap", ""]
    for key, title in findings:
        lines += [f"- **{title}** (medium; `{key}`; new)", "  What happened."]
    return "\n".join(lines) + "\n"


class RunsTest(unittest.TestCase):
    def test_totals_split_live_and_dry_runs(self) -> None:
        runs = [
            {"started": "2026-10-20T09:00:00+00:00", "dry_run": True, "findings": 3, "cost": 1.5},
            {"started": "2026-10-27T09:00:00+00:00", "dry_run": False, "findings": None, "cost": None},
            {"started": "2026-10-24T09:00:00+00:00", "dry_run": False, "findings": 2, "cost": 0.25},
        ]
        totals = impact_learning.run_totals(runs)
        self.assertEqual((totals["runs"], totals["dry_runs"], totals["findings"]), (2, 1, 5))
        self.assertEqual(totals["cost"], 1.75)
        self.assertEqual(totals["last"], "2026-10-27T09:00:00+00:00")

    def test_no_runs(self) -> None:
        self.assertEqual(impact_learning.run_totals([])["last"], None)


class RecurringTest(unittest.TestCase):
    def test_a_finding_in_two_runs_came_back(self) -> None:
        reports = [
            ("2026-10-27", report(("slow-handover", "Slow hand-over"), ("lost-paste", "Paste lost"))),
            ("2026-10-20", report(("slow-handover", "Slow hand-over"))),
        ]
        found = impact_learning.recurring(reports)
        self.assertEqual([f["key"] for f in found], ["slow-handover"])
        self.assertEqual(
            (found[0]["runs"], found[0]["first"], found[0]["last"]), (2, "2026-10-20", "2026-10-27")
        )
        self.assertEqual(found[0]["title"], "Slow hand-over")

    def test_one_run_has_nothing_recurring(self) -> None:
        self.assertEqual(impact_learning.recurring([("2026-10-20", report(("a", "A")))]), [])

    def test_lines_that_are_not_findings_are_ignored(self) -> None:
        self.assertEqual(impact_learning.report_findings("- **Bold** text, no key\n"), {})


class KnowledgeTest(unittest.TestCase):
    def test_growth_by_week_with_a_running_total(self) -> None:
        kb = {
            "facts": [
                {"recorded_at": "2026-10-21T10:00:00+00:00", "status": "proposed"},
                {"recorded_at": "2026-10-22T10:00:00+00:00", "status": "confirmed"},
                {"recorded_at": "2026-10-28T10:00:00+00:00", "status": "confirmed"},
                {"recorded_at": None, "status": "confirmed"},
            ],
            "entities": 5,
        }
        growth = impact_learning.kb_growth(kb)
        self.assertEqual((growth["facts"], growth["entities"]), (4, 5))
        self.assertEqual(growth["by_status"], {"proposed": 1, "confirmed": 3})
        self.assertEqual(
            growth["weeks"],
            [{"week": "2026-10-19", "added": 2, "total": 2}, {"week": "2026-10-26", "added": 1, "total": 3}],
        )

    def test_none_and_an_error(self) -> None:
        self.assertEqual(impact_learning.kb_growth(None), {"exists": False, "error": None})
        self.assertEqual(impact_learning.kb_growth({"error": "unreadable"})["error"], "unreadable")


class ModelTest(unittest.TestCase):
    def test_the_panel(self) -> None:
        model = impact_learning.learning_model([], [], [{"state": "applied"}, {"state": "rejected"}], None)
        self.assertEqual(model["proposals"], {"applied": 1, "rejected": 1})
        self.assertEqual(model["recurring"], [])
        self.assertFalse(model["knowledge"]["exists"])


if __name__ == "__main__":
    unittest.main()
