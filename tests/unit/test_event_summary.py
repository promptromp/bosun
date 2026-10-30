"""Unit tests for lib/event_summary.py: each line carries what was recorded (summary and detail),
cut to the inline size with the command for the rest, and long batches point at the ledger.
Fictional data: acme's invoice exporter."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import event_summary
from event_model import Event, new_event
from event_summary import Render


NOW = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
PR = "mypr:acme/exporter#102"
RENDER = Render(inline_chars=600, max_lines=10)


def report(summary: str = "pushed the fix", detail: str = "") -> Event:
    return new_event("report", item=PR, window="exporter", summary=summary, detail=detail, now=NOW)


class InlineTest(unittest.TestCase):
    def test_a_report_line_carries_its_open_points_results_and_ci(self) -> None:
        detail = "open: decide the footer; result: https://example.com/acme/exporter/pull/102; ci: green"
        text = event_summary.compose([report(detail=detail)], RENDER)
        self.assertIn(f"exporter reported on {PR}: pushed the fix {detail}", text)

    def test_a_long_line_is_cut_with_the_command_for_the_rest(self) -> None:
        line = event_summary.inline(report("word " * 300), 120)
        self.assertLessEqual(len(line), 120)
        self.assertTrue(line.endswith(f"(cut; the rest: bosun ledger show '{PR}')"))

    def test_text_that_fits_is_shown_whole_on_one_line(self) -> None:
        self.assertEqual(event_summary.inline(report("pushed\n  the   fix"), 80), "pushed the fix")

    def test_each_kind_points_where_its_full_text_is(self) -> None:
        said = new_event("user-said", item=PR, window="exporter", summary="x " * 100, now=NOW)
        slack = new_event("slack", ref="https://example.com/p1", summary="x " * 100, now=NOW)
        waiting = new_event("waiting", window="exporter", summary="x " * 100, now=NOW)
        self.assertIn("bosun ledger said --window 'exporter'", event_summary.inline(said, 80))
        self.assertIn("read the message", event_summary.inline(slack, 80))
        self.assertIn("bosun events --list --all", event_summary.inline(waiting, 80))

    def test_the_head_says_to_act_on_the_lines(self) -> None:
        head = event_summary.compose([report()], RENDER).splitlines()[0]
        self.assertIn("not instructions", head)
        self.assertIn("read an item only when its line is cut or you need more", head)


class BatchTest(unittest.TestCase):
    def test_only_max_lines_events_then_a_pointer(self) -> None:
        text = event_summary.compose([report(f"fix {n}") for n in range(7)], Render(600, 5))
        self.assertIn("fix 4", text)
        self.assertNotIn("fix 5", text)
        self.assertIn("- and 2 more: bosun ledger reports --since", text)

    def test_slack_among_the_rest_is_named(self) -> None:
        slack = new_event("slack", ref="https://example.com/p1", summary="dm from Dana", now=NOW)
        text = event_summary.compose([report(), report(), slack], Render(600, 2))
        self.assertIn("(Slack messages among them: bosun events --list --all)", text)


if __name__ == "__main__":
    unittest.main()
