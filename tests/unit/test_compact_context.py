"""Unit tests for the context a session gets back after a compaction (lib/compact_context.py, and
`ledger compact-context` picking its rows). Fictional data: acme's invoice exporter."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compact_context
import ledger
from test_ledger_activity import MENTION, PR, WINDOW, ActivityTestCase, run


class BlockTest(unittest.TestCase):
    def test_all_rows_fit(self) -> None:
        block = compact_context.Block("head", ["a", "b"], ["foot"], "more-cmd")
        self.assertEqual(block.render(), "head\na\nb\nfoot")

    def test_rows_that_do_not_fit_are_counted_with_the_command_for_the_rest(self) -> None:
        rows = [f"row {n} " + "x" * 50 for n in range(10)]
        text = compact_context.Block("head", rows, ["foot"], "more-cmd").render(max_chars=250)
        self.assertLessEqual(len(text), 250)
        self.assertTrue(text.startswith("head\nrow 0"))
        self.assertIn("more: more-cmd", text)
        self.assertTrue(text.endswith("\nfoot"))

    def test_heading_and_footer_stay_even_when_no_row_fits(self) -> None:
        text = compact_context.Block("head", ["x" * 100], ["foot"], "cmd").render(max_chars=20)
        self.assertEqual(text.splitlines()[0], "head")
        self.assertEqual(text.splitlines()[-1], "foot")
        self.assertIn("1 more", text)

    def test_window_block_without_items_says_so_and_still_says_how_to_report(self) -> None:
        text = compact_context.window_block("exporter", []).render()
        self.assertIn("No open ledger items are handed to exporter", text)
        self.assertIn("bosun ledger report", text)

    def test_orchestrator_block_counts_proposals(self) -> None:
        text = compact_context.orchestrator_block(["a: merge it"], ["exporter 2"]).render()
        self.assertIn("1 proposal(s) wait for my approval", text)
        self.assertIn("Open items per window: exporter 2.", text)


class LedgerCompactContextTest(ActivityTestCase):
    def test_a_window_gets_its_held_items_in_flight_first(self) -> None:
        run("report", MENTION, "--summary", "rounded per customer")
        lines = run("compact-context", "--window", WINDOW).splitlines()
        self.assertIn(f"handed to {WINDOW}", lines[0])
        self.assertTrue(lines[1].startswith(PR))  # awaiting a report
        self.assertTrue(lines[2].startswith(MENTION))  # reported
        self.assertIn("bosun ledger report", lines[-1])

    def test_another_window_holds_nothing(self) -> None:
        self.assertIn("No open ledger items are handed to other", run("compact-context", "--window", "other"))

    def test_the_orchestrator_gets_pending_proposals_and_counts_per_window(self) -> None:
        run("propose", PR, "--run", "gh pr merge https://github.com/acme/exporter/pull/102 --squash")
        text = run("compact-context", "--orchestrator")
        self.assertIn("1 proposal(s) wait for my approval", text)
        self.assertIn(f"{PR}: run: gh pr merge", text)
        self.assertIn(f"Open items per window: {WINDOW} 2.", text)

    def test_the_block_keeps_to_its_size_bound(self) -> None:
        self.assertLessEqual(len(run("compact-context", "--window", WINDOW, "--max-chars", "300")), 300)

    def test_long_rows_are_shortened(self) -> None:
        self.assertEqual(len(ledger.shorten("x" * 500)), ledger.COMPACT_ROW_CHARS)


if __name__ == "__main__":
    unittest.main()
