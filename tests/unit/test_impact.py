"""Unit tests for lib/impact.py: the Impact view's metrics over a fixture snapshot (acme)."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import impact


NOW = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)  # a Wednesday; the start was Monday 2026-03-02


def pr(number: int, created: str, merged: str, repo: str = "acme/exporter") -> dict[str, Any]:
    return {
        "repo": repo,
        "number": number,
        "url": f"https://github.com/{repo}/pull/{number}",
        "title": f"change {number}",
        "created": created,
        "merged": merged,
        "additions": 10,
        "deletions": 2,
    }


def snapshot(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": impact.SNAPSHOT_VERSION,
        "collected_at": "2026-03-18T06:00:00Z",
        "start": "2026-03-02",
        "baseline_from": "2026-02-02",
        "repos": ["acme/exporter", "acme/billing"],
        "settings": {"prompt_minutes": 2},
        # baseline (4 full weeks from Feb 2): 1, 2, 1, 0 PRs
        "prs": [
            pr(1, "2026-02-02T09:00:00Z", "2026-02-03T09:00:00Z"),
            pr(2, "2026-02-09T09:00:00Z", "2026-02-10T21:00:00Z"),
            pr(3, "2026-02-10T09:00:00Z", "2026-02-12T09:00:00Z", repo="acme/billing"),
            pr(4, "2026-02-16T09:00:00Z", "2026-02-16T19:00:00Z"),
            # since: week of Mar 2: 3; week of Mar 9: 5; current week (Mar 16): 1, partial
            *(pr(10 + i, "2026-03-02T08:00:00Z", "2026-03-04T08:00:00Z") for i in range(3)),
            *(pr(20 + i, "2026-03-09T08:00:00Z", "2026-03-10T08:00:00Z") for i in range(5)),
            pr(30, "2026-03-16T08:00:00Z", "2026-03-16T10:00:00Z"),
        ],
        "reviews": [
            {
                "repo": "acme/exporter",
                "number": 501,
                "url": "https://github.com/acme/exporter/pull/501",
                "title": "someone's change",
                "pr_created": "2026-03-03T08:00:00Z",
                # two on the same day count once
                "reviews": ["2026-03-03T10:00:00Z", "2026-03-03T11:00:00Z", "2026-03-10T09:00:00Z"],
            },
            {
                "repo": "acme/exporter",
                "number": 401,
                "url": "https://github.com/acme/exporter/pull/401",
                "pr_created": "2026-02-09T08:00:00Z",
                "reviews": ["2026-02-10T08:00:00Z"],
            },
        ],
        "activity": [
            {
                "window": "orchestrator",
                "day": "2026-03-03",
                "busy_seconds": 7200,
                "prompts": 6,
                "usage": {
                    "claude-opus-5-5": {"input": 1_000_000, "output": 100_000, "cache_read": 10_000_000}
                },
            },
            {"window": "exporter", "day": "2026-03-10", "busy_seconds": 18000, "prompts": 3, "usage": {}},
            {"window": "exporter", "day": "2026-02-10", "busy_seconds": 3600, "prompts": 10, "usage": {}},
        ],
    }
    base.update(extra)
    return base


def model(**extra: Any) -> dict[str, Any]:
    return impact.impact_model(snapshot(**extra), NOW, UTC)


class WeeksTest(unittest.TestCase):
    def test_baseline_and_since_weeks(self) -> None:
        weeks = model()["weeks"]
        self.assertEqual(weeks[0]["week"], "2026-02-02")
        self.assertEqual([w["phase"] for w in weeks].count("baseline"), 4)
        self.assertEqual(
            [w["week"] for w in weeks if w["phase"] == "since"], ["2026-03-02", "2026-03-09", "2026-03-16"]
        )
        self.assertTrue(weeks[-1]["partial"])
        self.assertEqual([w["prs"] for w in weeks], [1, 2, 1, 0, 3, 5, 1])

    def test_a_partial_first_baseline_week_is_left_out(self) -> None:
        self.assertEqual(model(baseline_from="2026-02-04")["weeks"][0]["week"], "2026-02-09")

    def test_default_baseline_is_13_weeks_before_the_start(self) -> None:
        found = model(baseline_from=None)
        self.assertEqual(found["baseline_from"], "2025-12-01")


class DeliveryTest(unittest.TestCase):
    def test_prs_per_week_compared_with_the_baseline_band(self) -> None:
        prs = model()["prs"]
        self.assertEqual(prs["baseline"], {"mean": 1.0, "p25": 0.8, "p75": 1.2, "weeks": 4})
        self.assertEqual(prs["since"], {"mean": 4.0, "weeks": 2, "partial_only": False})
        self.assertEqual(prs["change"], 3.0)

    def test_only_a_partial_week_since_the_start(self) -> None:
        found = impact.impact_model(snapshot(), datetime(2026, 3, 4, tzinfo=UTC), UTC)
        self.assertTrue(found["prs"]["since"]["partial_only"])
        self.assertTrue(found["early"])

    def test_time_to_merge_medians(self) -> None:
        hours = model()["prs"]["merge_hours"]
        self.assertEqual(hours["baseline"], {"median": 30.0, "n": 4})  # 24, 36, 48 and 10 h
        self.assertEqual(hours["since"], {"median": 24.0, "n": 9})

    def test_drill_down_links(self) -> None:
        week = next(w for w in model()["weeks"] if w["week"] == "2026-02-09")
        self.assertEqual(week["prs_by_repo"], {"acme/exporter": 1, "acme/billing": 1})
        self.assertEqual(week["pr_links"][0]["url"], "https://github.com/acme/exporter/pull/2")


class ReviewsTest(unittest.TestCase):
    def test_one_review_per_pr_and_day(self) -> None:
        weeks = {w["week"]: w["reviews"] for w in model()["weeks"]}
        self.assertEqual((weeks["2026-02-09"], weeks["2026-03-02"], weeks["2026-03-09"]), (1, 1, 1))

    def test_time_to_first_review(self) -> None:
        first = model()["reviews"]["first_review_hours"]
        self.assertEqual(first["baseline"], {"median": 24.0, "n": 1})
        self.assertEqual(first["since"], {"median": 2.0, "n": 1})


class ActivityTest(unittest.TestCase):
    def test_leverage_is_agent_hours_over_touch_hours_with_a_range(self) -> None:
        since = model()["since"]
        # 7 agent hours since the start, 9 prompts x 2 min = 0.3 h
        self.assertEqual(since["leverage"]["agent_hours"], 7.0)
        self.assertEqual(since["leverage"]["touch"], {"estimate": 0.3, "low": 0.1, "high": 0.6})
        self.assertEqual(since["leverage"]["estimate"], 23.3)
        self.assertEqual((since["leverage"]["low"], since["leverage"]["high"]), (11.7, 70.0))

    def test_leverage_before_and_since_counts_only_full_weeks_with_prompts(self) -> None:
        compare = model()["leverage"]
        before, since = compare["baseline"], compare["since"]
        self.assertEqual((before["weeks"], before["of_weeks"], before["first_week"]), (1, 4, "2026-02-09"))
        self.assertEqual(before["agent_hours"], 1.0)
        self.assertEqual(before["estimate"], 3.3)  # 1 h over 10 prompts at 2 minutes (0.3 h)
        self.assertEqual((before["agent_per_week"], before["touch_per_week"]), (1.0, 0.3))
        self.assertEqual((since["weeks"], since["of_weeks"]), (2, 2))  # the partial week is left out
        self.assertEqual(since["agent_hours"], 7.0)

    def test_no_transcripts_before_the_start_is_no_before_figure(self) -> None:
        activity = [r for r in snapshot()["activity"] if r["day"] >= "2026-03-02"]
        before = model(activity=activity)["leverage"]["baseline"]
        self.assertIsNone(before["estimate"])
        self.assertEqual((before["weeks"], before["first_week"], before["agent_per_week"]), (0, None, None))

    def test_each_week_has_its_leverage(self) -> None:
        weeks = {w["week"]: w for w in model()["weeks"]}
        self.assertEqual(weeks["2026-02-09"]["leverage"], {"estimate": 3.3, "low": 1.4, "high": 5.0})
        self.assertIsNone(weeks["2026-02-02"]["leverage"]["estimate"])

    def test_no_prompts_no_leverage(self) -> None:
        self.assertIsNone(impact.leverage(3600, 0, 2)["estimate"])

    def test_cost_at_list_prices(self) -> None:
        usage = {"input": 1_000_000, "output": 100_000, "cache_read": 10_000_000}
        self.assertAlmostEqual(impact.usage_cost("claude-opus-5-5", usage), 4 + 2 + 2)
        self.assertAlmostEqual(impact.usage_cost("claude-opus-4-8", usage), 5 + 2.5 + 5)
        self.assertAlmostEqual(impact.usage_cost("claude-sonnet-5-5", usage), 2 + 1 + 2)
        self.assertAlmostEqual(impact.usage_cost("claude-haiku-5-5", {"cache_write": 1_000_000}), 0.125)
        self.assertAlmostEqual(impact.usage_cost("claude-fable-5-1", {"output": 1_000_000}), 50)
        since = model()["since"]
        self.assertEqual((since["cost"], since["merged"], since["cost_per_pr"]), (8.0, 9, 0.89))

    def test_unknown_models_are_priced_high_and_named(self) -> None:
        rows = [{"usage": {"mystery-1": {"output": 1_000_000}, "<synthetic>": {"output": 0}}}]
        self.assertEqual(impact.unpriced_models(rows), ["mystery-1"])
        self.assertEqual(impact.row_cost(rows[0]), 25.0)

    def test_where_time_went_since_the_start(self) -> None:
        self.assertEqual(
            model()["windows"],
            [
                {"window": "exporter", "agent_hours": 5.0, "prompts": 3, "cost": 0.0},
                {"window": "orchestrator", "agent_hours": 2.0, "prompts": 6, "cost": 8.0},
            ],
        )


class ModelTest(unittest.TestCase):
    def test_no_snapshot(self) -> None:
        self.assertEqual(impact.impact_model(None, NOW, UTC), {"exists": False})

    def test_freshness_and_early(self) -> None:
        found = model()
        self.assertFalse(found["stale"])
        self.assertTrue(found["early"])  # 2 full weeks since the start
        self.assertTrue(model(collected_at="2026-03-10T00:00:00Z")["stale"])

    def test_bad_times_are_skipped(self) -> None:
        found = model(prs=[pr(1, "nonsense", "also nonsense")])
        self.assertEqual(sum(w["prs"] for w in found["weeks"]), 0)

    def test_percentile(self) -> None:
        self.assertIsNone(impact.percentile([], 0.5))
        self.assertEqual(impact.percentile([4.0], 0.25), 4.0)
        self.assertEqual(impact.percentile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
        self.assertEqual(impact.monday(date(2026, 3, 18)), date(2026, 3, 16))


if __name__ == "__main__":
    unittest.main()
