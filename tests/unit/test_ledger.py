"""Unit tests for lib/ledger.py: ids and kinds, transitions, proposals, sources, runs, pruning."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import ledger


REVIEW_ID = "review:acme/api#1"
MYPR_ID = "mypr:acme/api#2"
MENTION_ID = "slack:C1/1"


def run(*argv: str) -> Any:
    """Run a ledger command; return its output (and exit code for commands that set one)."""
    args = ledger.build_parser().parse_args(list(argv))
    return args.func(args)


class LedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        ledger.configure(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def data(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(ledger.STORE.ledger_file.read_text())
        return value

    def item(self, item_id: str) -> dict[str, Any]:
        item: dict[str, Any] = self.data()["items"][item_id]
        return item


class UpsertTest(LedgerTestCase):
    def test_creates_in_initial_state(self) -> None:
        self.assertIn("created", run("upsert", REVIEW_ID, "--kind", "review-request", "--title", "T"))
        self.assertEqual(self.item(REVIEW_ID)["state"], "requested")

    def test_is_idempotent(self) -> None:
        run("upsert", REVIEW_ID, "--kind", "review-request", "--title", "T")
        self.assertIn("unchanged", run("upsert", REVIEW_ID, "--kind", "review-request", "--title", "T"))

    def test_updates_fields_without_changing_state(self) -> None:
        run("upsert", REVIEW_ID, "--kind", "review-request")
        run("upsert", REVIEW_ID, "--kind", "review-request", "--field", "requester=Marco")
        item = self.item(REVIEW_ID)
        self.assertEqual(item["fields"]["requester"], "Marco")
        self.assertEqual(item["state"], "requested")

    def test_rejects_an_id_of_another_kind(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "review-request ids look like"):
            run("upsert", MENTION_ID, "--kind", "review-request")

    def test_rejects_a_bare_prefix(self) -> None:
        with self.assertRaises(ledger.LedgerError):
            run("upsert", "slack:", "--kind", "mention")

    def test_rejects_a_kind_change(self) -> None:
        run("upsert", MENTION_ID, "--kind", "mention")
        with self.assertRaisesRegex(ledger.LedgerError, "is a mention"):
            run("upsert", MENTION_ID, "--kind", "review-request")

    def test_rejects_unknown_kinds_and_bad_fields(self) -> None:
        with self.assertRaises(ledger.LedgerError):
            run("upsert", MENTION_ID, "--kind", "nope")
        with self.assertRaisesRegex(ledger.LedgerError, "key=value"):
            run("upsert", MENTION_ID, "--kind", "mention", "--field", "novalue")

    def test_repo_mask_limits_repo_items_only(self) -> None:
        ledger.configure(self.tmp.name, "acme/*")
        with self.assertRaisesRegex(ledger.LedgerError, "outside BOSUN_REPO_MASK"):
            run("upsert", "review:other/x#1", "--kind", "review-request")
        run("upsert", REVIEW_ID, "--kind", "review-request")
        run("upsert", MENTION_ID, "--kind", "mention")
        self.assertEqual(set(self.data()["items"]), {REVIEW_ID, MENTION_ID})


class TransitionTest(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        run("upsert", REVIEW_ID, "--kind", "review-request")

    def test_valid_transition_is_recorded(self) -> None:
        self.assertIn("requested -> delegated", run("transition", REVIEW_ID, "delegated", "--note", "sent"))
        self.assertEqual(self.item(REVIEW_ID)["history"][-1]["to"], "delegated")

    def test_failed_delegation_can_go_back(self) -> None:
        run("transition", REVIEW_ID, "delegated", "--note", "sent")
        run("transition", REVIEW_ID, "requested", "--note", "delegate failed")
        self.assertEqual(self.item(REVIEW_ID)["state"], "requested")

    def test_invalid_transition_lists_the_allowed_ones(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "allowed: "):
            run("transition", REVIEW_ID, "rereview-requested", "--note", "x")

    def test_rereview_needs_evidence(self) -> None:
        run("transition", REVIEW_ID, "reviewed", "--note", "done")
        with self.assertRaisesRegex(ledger.LedgerError, "needs --evidence"):
            run("transition", REVIEW_ID, "rereview-requested", "--note", "again")
        run("transition", REVIEW_ID, "rereview-requested", "--note", "again", "--evidence", "re-requested")

    def test_tech_design_rereview_needs_evidence(self) -> None:
        run("upsert", "td:123", "--kind", "td-review")
        run("transition", "td:123", "commented", "--note", "posted")
        with self.assertRaises(ledger.LedgerError):
            run("transition", "td:123", "rereview-requested", "--note", "again")

    def test_dismiss_and_reopen(self) -> None:
        run("transition", REVIEW_ID, "dismissed", "--note", "not mine")
        run("transition", REVIEW_ID, "requested", "--note", "it is mine")
        self.assertEqual(self.item(REVIEW_ID)["state"], "requested")

    def test_same_state_is_a_no_op(self) -> None:
        self.assertIn("unchanged", run("transition", REVIEW_ID, "requested", "--note", "x"))

    def test_addressing_leads_back_to_awaiting_review(self) -> None:
        run("upsert", MYPR_ID, "--kind", "my-pr")
        run("transition", MYPR_ID, "changes-requested", "--note", "x")
        run("transition", MYPR_ID, "addressing", "--note", "x")
        run("transition", MYPR_ID, "awaiting-review", "--note", "pushed", "--field", "last_push_at=t")
        self.assertEqual(self.item(MYPR_ID)["fields"]["last_push_at"], "t")

    def test_unknown_item(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "no item"):
            run("transition", "slack:C9/9", "done", "--note", "x")


class ProposalTest(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        run("upsert", REVIEW_ID, "--kind", "review-request")

    def test_propose_and_resolve(self) -> None:
        run("propose", REVIEW_ID, "post the review")
        self.assertIn("PENDING: post the review", run("list", "--proposals"))
        self.assertIn("unchanged", run("propose", REVIEW_ID, "post the review"))
        run("resolve", REVIEW_ID, "done")
        self.assertIsNone(self.item(REVIEW_ID)["proposal"])
        self.assertEqual(self.item(REVIEW_ID)["history"][-1]["event"], "proposal-done")

    def test_a_new_proposal_replaces_the_old_one(self) -> None:
        run("propose", REVIEW_ID, "first")
        run("propose", REVIEW_ID, "second")
        events = [h["event"] for h in self.item(REVIEW_ID)["history"]]
        self.assertIn("proposal-replaced", events)
        self.assertEqual(self.item(REVIEW_ID)["proposal"]["action"], "second")

    def test_terminal_state_withdraws_the_proposal(self) -> None:
        run("propose", REVIEW_ID, "post the review")
        run("transition", REVIEW_ID, "closed", "--note", "merged")
        self.assertIsNone(self.item(REVIEW_ID)["proposal"])
        with self.assertRaisesRegex(ledger.LedgerError, "nothing to propose"):
            run("propose", REVIEW_ID, "again")

    def test_resolve_without_a_proposal(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "no pending proposal"):
            run("resolve", REVIEW_ID, "done")

    def test_ledger_view_shows_pending_proposals(self) -> None:
        run("propose", REVIEW_ID, "post the review")
        self.assertIn("post the review", ledger.STORE.view.read_text())


class SourceTest(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        run("upsert", REVIEW_ID, "--kind", "review-request")

    def test_record_and_seen(self) -> None:
        self.assertEqual(run("seen", "p1"), ("", 1))
        run("record-source", "p1", "--class", "pr-review", "--item", REVIEW_ID)
        output, code = run("seen", "p1")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["classification"], "pr-review")

    def test_class_must_match_the_item_kind(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "its messages are class pr-review"):
            run("record-source", "p1", "--class", "mention", "--item", REVIEW_ID)

    def test_only_fyi_stands_alone(self) -> None:
        run("record-source", "p1", "--class", "fyi")
        with self.assertRaisesRegex(ledger.LedgerError, "needs --item"):
            run("record-source", "p2", "--class", "mention")

    def test_unknown_item_and_class(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "upsert it first"):
            run("record-source", "p1", "--class", "pr-review", "--item", "review:acme/api#404")
        with (
            self.assertRaises(SystemExit),
            redirect_stdout(io.StringIO()),
            unittest.mock.patch("sys.stderr", io.StringIO()),
        ):
            run("record-source", "p1", "--class", "nope")

    def test_rerecording_keeps_the_first_seen_time(self) -> None:
        run("record-source", "p1", "--class", "fyi")
        first = self.data()["sources"]["p1"]["seen_at"]
        run("record-source", "p1", "--class", "pr-review", "--item", REVIEW_ID)
        self.assertEqual(self.data()["sources"]["p1"]["seen_at"], first)


class RunsTest(LedgerTestCase):
    def write_runs(self, **runs: str) -> None:
        data = ledger.load()
        data["runs"] = {name.replace("_", "-"): at for name, at in runs.items()}
        ledger.save(data)

    def test_first_check_in_falls_back_to_the_lookback(self) -> None:
        output = run("mark-run", "check-in")
        self.assertIn("previous check-in run: never", output)
        self.assertIn("(no earlier run: the last 24h)", output)

    def test_check_in_scans_from_the_later_run_minus_overlap(self) -> None:
        morning = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        check_in = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        self.write_runs(morning=morning.isoformat(), check_in=check_in.isoformat())
        with unittest.mock.patch.object(ledger, "now", return_value="2026-01-01T10:00:00+00:00"):
            output = run("mark-run", "check-in")
        expected = (check_in - ledger.CHECKIN_OVERLAP).isoformat(timespec="seconds")
        self.assertIn(f"scan from: {expected} (the last scan, with some overlap)", output)

    def test_morning_run_prints_its_scan_start(self) -> None:
        self.assertIn("scan from:", run("mark-run", "morning"))


class ScanStartTest(LedgerTestCase):
    """Where a chores run's Slack scan starts: the last scan, the lookback, the cap."""

    NOW = datetime(2026, 1, 8, 8, 0, tzinfo=UTC)

    def start(self, name: str, **hours_ago: float) -> tuple[datetime, str]:
        runs = {run.replace("_", "-"): self.ago(hours) for run, hours in hours_ago.items()}
        return ledger.scan_start(name, runs, self.NOW)

    def ago(self, hours: float) -> str:
        return (self.NOW - timedelta(hours=hours)).isoformat()

    def test_morning_after_a_recent_check_in_uses_the_lookback(self) -> None:
        start, why = self.start("morning", check_in=12, morning=24)
        self.assertEqual(start, self.NOW - timedelta(hours=24))
        self.assertIn("the last 24h", why)

    def test_morning_after_a_failed_morning_reaches_back_to_the_last_scan(self) -> None:
        start, why = self.start("morning", check_in=40, morning=50)
        self.assertEqual(start, self.NOW - timedelta(hours=40) - ledger.CHECKIN_OVERLAP)
        self.assertIn("the last scan", why)

    def test_a_long_absence_is_capped(self) -> None:
        start, why = self.start("morning", morning=24 * 10)
        self.assertEqual(start, self.NOW - timedelta(hours=96))
        self.assertIn("capped at 96h", why)

    def test_the_cap_follows_the_settings(self) -> None:
        ledger.configure_scan(12, 48)
        self.assertEqual(self.start("morning")[0], self.NOW - timedelta(hours=12))
        self.assertEqual(self.start("morning", morning=72)[0], self.NOW - timedelta(hours=48))

    def test_check_in_scans_from_the_last_run_even_if_older_than_the_lookback(self) -> None:
        start, _ = self.start("check-in", morning=30)
        self.assertEqual(start, self.NOW - timedelta(hours=30) - ledger.CHECKIN_OVERLAP)

    def test_check_in_after_days_away_is_capped_too(self) -> None:
        self.assertEqual(self.start("check-in", check_in=200)[0], self.NOW - timedelta(hours=96))

    def test_first_morning_uses_the_lookback(self) -> None:
        start, why = self.start("morning")
        self.assertEqual(start, self.NOW - timedelta(hours=24))
        self.assertIn("no earlier run", why)


class MaintenanceTest(LedgerTestCase):
    def test_prune_drops_old_finished_items_but_keeps_linked_sources(self) -> None:
        run("upsert", REVIEW_ID, "--kind", "review-request")
        run("upsert", MENTION_ID, "--kind", "mention")
        run("record-source", "p-open", "--class", "mention", "--item", MENTION_ID)
        run("record-source", "p-fyi", "--class", "fyi")
        run("transition", REVIEW_ID, "closed", "--note", "merged")
        data = ledger.load()
        old = (datetime.now(UTC) - timedelta(days=40)).isoformat(timespec="seconds")
        data["items"][REVIEW_ID]["updated_at"] = old
        for source in data["sources"].values():
            source["seen_at"] = old
        ledger.save(data)
        self.assertIn("pruned 1 item(s) and 1 source(s)", run("prune", "--days", "30"))
        self.assertEqual(set(self.data()["items"]), {MENTION_ID})
        self.assertEqual(set(self.data()["sources"]), {"p-open"})

    def test_corrupt_ledger_gives_a_clear_error(self) -> None:
        ledger.STORE.state_dir.mkdir(parents=True, exist_ok=True)
        ledger.STORE.ledger_file.write_text("{not json")
        with self.assertRaisesRegex(ledger.LedgerError, "not valid JSON"):
            run("list")

    def test_kinds_show_id_formats_and_classes(self) -> None:
        output = run("kinds")
        for spec in ledger.KINDS.values():
            self.assertIn(spec["id_format"], output)
            self.assertIn(f"--class {spec['source_class']}", output)


if __name__ == "__main__":
    unittest.main()
