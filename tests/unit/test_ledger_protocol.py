"""Unit tests for the ledger's hand-over protocol (delegations, updates, reports) and its storage
robustness (backups, corrupt files, history trimming, concurrent writers)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import ledger


LIB = Path(ledger.__file__).parent
MENTION_ID = "slack:C1/1.1"
REVIEW_ID = "review:acme/api#7"
MYPR_ID = "mypr:acme/api#8"


def run(*argv: str) -> Any:
    args = ledger.build_parser().parse_args(list(argv))
    return args.func(args)


class ProtocolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        ledger.configure(self.tmp.name)
        run("upsert", MENTION_ID, "--kind", "mention", "--title", "warnings format")
        run("upsert", REVIEW_ID, "--kind", "review-request")
        run("upsert", MYPR_ID, "--kind", "my-pr")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def item(self, item_id: str) -> dict[str, Any]:
        item: dict[str, Any] = json.loads(ledger.STORE.ledger_file.read_text())["items"][item_id]
        return item

    def events(self, item_id: str) -> list[str]:
        return [entry["event"] for entry in self.item(item_id)["history"]]


class UpsertWithoutKindTest(ProtocolTestCase):
    def test_updates_fields_of_an_existing_item(self) -> None:
        self.assertIn("updated", run("upsert", MYPR_ID, "--field", "review_thread=t1"))
        self.assertEqual(self.item(MYPR_ID)["fields"]["review_thread"], "t1")

    def test_creating_needs_a_kind(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "give --kind to create it"):
            run("upsert", "slack:C9/9", "--field", "a=b")

    def test_a_wrong_kind_is_still_rejected(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "unknown kind 'review'"):
            run("upsert", REVIEW_ID, "--kind", "review", "--field", "a=b")


class DelegationTest(ProtocolTestCase):
    def test_records_the_window_and_task(self) -> None:
        run("record-delegation", MENTION_ID, "--window", "exports", "--task", "new-work", "--ask", "build it")
        delegation = json.loads(run("delegation", MENTION_ID))
        self.assertEqual(
            (delegation["window"], delegation["task"], delegation["updates"]), ("exports", "new-work", 0)
        )
        self.assertEqual(self.item(MENTION_ID)["history"][-1]["note"], "build it")

    def test_a_new_delegation_replaces_the_window(self) -> None:
        run("record-delegation", MENTION_ID, "--window", "a", "--task", "investigate")
        run("record-delegation", MENTION_ID, "--window", "b", "--task", "new-work")
        self.assertEqual(json.loads(run("delegation", MENTION_ID))["window"], "b")

    def test_updates_are_counted_and_kept(self) -> None:
        run("record-delegation", MENTION_ID, "--window", "exports", "--task", "new-work")
        run("record-update", MENTION_ID, "--text", "match the ERP connector fields")
        self.assertIn("update 2", run("record-update", MENTION_ID, "--text", "test end to end"))
        delegation = self.item(MENTION_ID)["delegation"]
        self.assertEqual(delegation["updates"], 2)
        self.assertIn("last_update_at", delegation)
        updates = [h for h in self.item(MENTION_ID)["history"] if h["event"] == "update"]
        self.assertEqual([h["note"] for h in updates], ["match the ERP connector fields", "test end to end"])
        self.assertEqual({h["window"] for h in updates}, {"exports"})

    def test_refuses_an_item_never_delegated(self) -> None:
        for command in (["delegation", MENTION_ID], ["record-update", MENTION_ID, "--text", "x"]):
            with self.subTest(command=command), self.assertRaisesRegex(ledger.LedgerError, "never handed"):
                run(*command)

    def test_refuses_a_finished_item(self) -> None:
        run("record-delegation", MENTION_ID, "--window", "exports", "--task", "investigate")
        run("transition", MENTION_ID, "done", "--note", "answered")
        with self.assertRaisesRegex(ledger.LedgerError, "is done; a finished item takes no updates"):
            run("record-update", MENTION_ID, "--text", "x")

    def test_list_shows_where_open_work_is(self) -> None:
        run("record-delegation", MENTION_ID, "--window", "exports", "--task", "new-work")
        self.assertIn(f"{MENTION_ID}  [mention: new] @exports (awaiting report)", run("list"))
        run("report", MENTION_ID, "--summary", "draft PR up")
        self.assertIn("@exports (reported)", run("list"))
        run("record-update", MENTION_ID, "--text", "one more thing")
        self.assertIn("@exports (awaiting report)", run("list"))
        run("transition", MENTION_ID, "done", "--note", "x")
        self.assertNotIn("@exports", run("list"))


class AwaitingReportTest(ProtocolTestCase):
    def awaiting(self, item_id: str) -> bool:
        return bool(json.loads(run("delegation", item_id))["awaiting_report"])

    def test_a_hand_over_awaits_until_a_report(self) -> None:
        run("record-delegation", MENTION_ID, "--window", "a", "--task", "investigate")
        self.assertTrue(self.awaiting(MENTION_ID))
        run("report", MENTION_ID, "--summary", "answered")
        self.assertFalse(self.awaiting(MENTION_ID))
        run("record-update", MENTION_ID, "--text", "one more question")
        self.assertTrue(self.awaiting(MENTION_ID))

    def test_new_comments_while_addressing_do_not_end_the_wait(self) -> None:
        run("transition", MYPR_ID, "changes-requested", "--note", "comments")
        run("transition", MYPR_ID, "addressing", "--note", "to api")
        run("record-delegation", MYPR_ID, "--window", "api", "--task", "fix-my-pr")
        run("transition", MYPR_ID, "changes-requested", "--note", "more comments")
        run("transition", MYPR_ID, "addressing", "--note", "still with api")
        self.assertTrue(self.awaiting(MYPR_ID))  # so they go to api with --update
        run("transition", MYPR_ID, "awaiting-review", "--note", "pushed; review re-requested")
        self.assertFalse(self.awaiting(MYPR_ID))  # the next round is a new hand-over

    def test_reaching_a_done_state_also_ends_the_wait(self) -> None:
        run("transition", REVIEW_ID, "delegated", "--note", "to api")
        run("record-delegation", REVIEW_ID, "--window", "api", "--task", "review")
        # the orchestrator saw the review on GitHub (old task text: no report)
        run("transition", REVIEW_ID, "reviewed", "--note", "reviewed on GitHub")
        self.assertFalse(self.awaiting(REVIEW_ID))
        self.assertIn(f"{REVIEW_ID}  [review-request: reviewed] @api -", run("list") + " -")

    def test_the_re_review_path(self) -> None:
        run("transition", REVIEW_ID, "delegated", "--note", "to api")
        run("record-delegation", REVIEW_ID, "--window", "api", "--task", "review")
        run("report", REVIEW_ID, "--state", "reviewed", "--summary", "COMMENT")
        run(
            "transition",
            REVIEW_ID,
            "rereview-requested",
            "--note",
            "again",
            "--evidence",
            "re-requested on GitHub",
        )
        self.assertFalse(self.awaiting(REVIEW_ID))  # so `delegate review` may hand it over again
        run("transition", REVIEW_ID, "delegated", "--note", "re-review to api")
        run("record-delegation", REVIEW_ID, "--window", "api", "--task", "review")
        self.assertTrue(self.awaiting(REVIEW_ID))

    def test_the_task_header_is_kept_for_a_move(self) -> None:
        run(
            "record-delegation", MENTION_ID, "--window", "a", "--task", "investigate", "--ask", "why?",
            "--link", "https://x/1", "--requester", "Dana", "--details", "focus on the totals",
        )  # fmt: skip
        delegation = json.loads(run("delegation", MENTION_ID))
        self.assertEqual(
            [delegation[k] for k in ("ask", "link", "requester", "details")],
            ["why?", "https://x/1", "Dana", "focus on the totals"],
        )


class MoveTest(ProtocolTestCase):
    def setUp(self) -> None:
        super().setUp()
        run("record-delegation", MENTION_ID, "--window", "old", "--task", "new-work", "--ask", "build it")
        run("record-update", MENTION_ID, "--text", "first update")

    def test_moves_the_window_and_records_from_and_to(self) -> None:
        out = run("record-move", MENTION_ID, "--window", "new", "--text", "old window is stuck", "--notified")
        self.assertEqual(out, f"ok: {MENTION_ID} moved from old to new (the old window was told to stop)")
        delegation = self.item(MENTION_ID)["delegation"]
        self.assertEqual(
            (delegation["window"], delegation["moved_from"], delegation["updates"]), ("new", "old", 0)
        )
        self.assertNotIn("last_update_at", delegation)
        self.assertEqual(delegation["ask"], "build it")
        moved = self.item(MENTION_ID)["history"][-1]
        self.assertEqual(
            (moved["event"], moved["from"], moved["to"], moved["note"], moved["old_window_told"]),
            ("moved", "old", "new", "old window is stuck", "true"),
        )
        self.assertTrue(json.loads(run("delegation", MENTION_ID))["awaiting_report"])
        self.assertIn("@new (awaiting report)", run("list"))

    def test_records_when_the_old_window_was_not_told(self) -> None:
        self.assertIn("NOT told to stop", run("record-move", MENTION_ID, "--window", "new", "--text", "x"))
        self.assertEqual(self.item(MENTION_ID)["history"][-1]["old_window_told"], "false")

    def test_refusals(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "already delegated to old"):
            run("record-move", MENTION_ID, "--window", "old", "--text", "x")
        with self.assertRaisesRegex(ledger.LedgerError, "never handed"):
            run("record-move", MYPR_ID, "--window", "new", "--text", "x")
        run("transition", MENTION_ID, "done", "--note", "answered")
        with self.assertRaisesRegex(ledger.LedgerError, "is done; a finished item"):
            run("record-move", MENTION_ID, "--window", "new", "--text", "x")


class ReportTest(ProtocolTestCase):
    def test_records_summary_results_and_open_points(self) -> None:
        run("record-delegation", MYPR_ID, "--window", "api", "--task", "fix-my-pr")
        out = run(
            "report", MYPR_ID, "--summary", "fixed both threads", "--result", "https://x/pr/8",
            "--result", "abc123", "--open", "decide on the row limit",
        )  # fmt: skip
        self.assertEqual(out, f"ok: report recorded on {MYPR_ID}")
        report = self.item(MYPR_ID)["report"]
        self.assertEqual(report["results"], ["https://x/pr/8", "abc123"])
        self.assertEqual((report["open"], report["window"]), ("decide on the row limit", "api"))
        self.assertEqual(self.item(MYPR_ID)["state"], "awaiting-review")
        self.assertEqual(self.events(MYPR_ID)[-1], "report")

    def test_state_makes_a_validated_transition_with_fields(self) -> None:
        run("transition", REVIEW_ID, "delegated", "--note", "to api")
        out = run(
            "report", REVIEW_ID, "--state", "reviewed", "--summary", "APPROVE", "--result", "https://x/r/1",
            "--field", "reviewed_sha=abc", "--field", "reviewed_at=2026-10-16T05:39:00Z",
        )  # fmt: skip
        self.assertIn("delegated -> reviewed", out)
        item = self.item(REVIEW_ID)
        self.assertEqual((item["state"], item["fields"]["reviewed_sha"]), ("reviewed", "abc"))
        self.assertEqual(self.events(REVIEW_ID)[-2:], ["transition", "report"])

    def test_an_invalid_state_records_nothing(self) -> None:
        before = self.events(MENTION_ID)
        with self.assertRaisesRegex(ledger.LedgerError, "is not allowed"):
            run("report", MENTION_ID, "--state", "posted", "--summary", "x")
        self.assertEqual(self.events(MENTION_ID), before)
        self.assertNotIn("report", self.item(MENTION_ID))

    def test_skip_reports_close_the_item(self) -> None:
        run("transition", REVIEW_ID, "delegated", "--note", "to api")
        run(
            "report",
            REVIEW_ID,
            "--state",
            "dismissed",
            "--summary",
            "already approved by someone; no specific ask",
        )
        self.assertEqual(self.item(REVIEW_ID)["state"], "dismissed")

    def test_fields_must_be_single_lines(self) -> None:
        for argv in (
            ["--summary", "a\nb"],
            ["--summary", " "],
            ["--summary", "x" * (ledger.MAX_SUMMARY_LENGTH + 1)],
            ["--summary", "ok", "--result", "a\nb"],
            ["--summary", "ok", "--open", "a\nb"],
        ):
            with self.subTest(argv=argv), self.assertRaisesRegex(ledger.LedgerError, "one non-empty line"):
                run("report", MENTION_ID, *argv)

    def test_evidence_needs_a_state(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "--evidence goes with --state"):
            run("report", MENTION_ID, "--summary", "x", "--evidence", "y")

    def test_reports_lists_open_items_or_everything_since_a_time(self) -> None:
        run("report", MENTION_ID, "--summary", "the answer is yes", "--result", "notes/findings.md")
        run("transition", REVIEW_ID, "delegated", "--note", "to api")
        run("report", REVIEW_ID, "--state", "closed", "--summary", "merged before review")
        open_reports = run("reports")
        self.assertIn("summary: the answer is yes", open_reports)
        self.assertIn("result:  notes/findings.md", open_reports)
        self.assertNotIn(REVIEW_ID, open_reports)
        self.assertIn(
            f"{REVIEW_ID}  [review-request: closed]", run("reports", "--since", "2000-01-01T00:00:00Z")
        )
        self.assertIn(REVIEW_ID, run("reports", "--since", "2000-01-01T00:00:00"))  # naive means UTC
        self.assertEqual(run("reports", "--since", "2999-01-01T00:00:00+00:00"), "(no reports)")
        with self.assertRaisesRegex(ledger.LedgerError, "--since expects an ISO time"):
            run("reports", "--since", "yesterday")

    def test_diff_and_view_show_hand_overs(self) -> None:
        before = Path(self.tmp.name, "before.json")
        before.write_text(ledger.STORE.ledger_file.read_text())
        run("record-delegation", MENTION_ID, "--window", "exports", "--task", "new-work")
        run("report", MENTION_ID, "--summary", "draft PR up")
        diff = run("diff", str(before))
        self.assertIn(f"> {MENTION_ID} delegated to exports (0 update(s))", diff)
        self.assertIn(f"* {MENTION_ID} reported: draft PR up", diff)
        view = ledger.STORE.view.read_text()
        self.assertIn("@exports (reported)", view)
        self.assertIn("report: draft PR up", view)


class StorageTest(ProtocolTestCase):
    def test_each_write_keeps_the_previous_version(self) -> None:
        previous = ledger.STORE.ledger_file.read_text()
        run("note", MENTION_ID, "x")
        self.assertEqual(ledger.STORE.backup.read_text(), previous)
        self.assertFalse(list(Path(self.tmp.name).glob("*.tmp")))

    def test_a_corrupt_file_is_refused_with_the_backup_named(self) -> None:
        run("note", MENTION_ID, "x")
        ledger.STORE.ledger_file.write_text('{"items": ')
        with self.assertRaisesRegex(ledger.LedgerError, "restore it from .*ledger.json.bak"):
            run("list")

    def test_a_file_of_the_wrong_shape_is_refused(self) -> None:
        for text in ("[]", "null", '{"items": []}'):
            ledger.STORE.ledger_file.write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(ledger.LedgerError, "is not a ledger"):
                run("list")

    def test_a_missing_file_with_a_backup_is_not_a_fresh_start(self) -> None:
        run("note", MENTION_ID, "x")
        ledger.STORE.ledger_file.unlink()
        with self.assertRaisesRegex(ledger.LedgerError, "is missing but .*bak exists"):
            run("list")

    def test_history_is_trimmed_to_the_newest_entries(self) -> None:
        for number in range(ledger.MAX_HISTORY + 50):
            run("note", MENTION_ID, f"note {number}")
        history = self.item(MENTION_ID)["history"]
        self.assertEqual(len(history), ledger.MAX_HISTORY)
        self.assertEqual(history[0]["event"], "created")
        self.assertEqual(history[1]["event"], "history-trimmed")
        self.assertEqual(history[-1]["note"], f"note {ledger.MAX_HISTORY + 49}")
        # created + the 250 notes, of which MAX_HISTORY - 2 are kept
        self.assertEqual(history[1]["count"], ledger.MAX_HISTORY + 50 - (ledger.MAX_HISTORY - 2))


class ConcurrentWritersTest(unittest.TestCase):
    """Real processes against one state dir: the lock and the atomic rename lose nothing."""

    WRITERS = 40

    def setUp(self) -> None:
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        self.env = {**os.environ, "BOSUN_STATE_DIR": self.state.name}

    def cli(self, *argv: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen([sys.executable, str(LIB / "ledger.py"), *argv], env=self.env)

    def run_wave(self, commands: list[list[str]]) -> None:
        """Start every command at once, then wait for all of them."""
        writers = [self.cli(*command) for command in commands]
        self.assertEqual([writer.wait() for writer in writers], [0] * len(writers))

    def second_wave(self, number: int) -> list[list[str]]:
        """A transition or a delegation on item <number>, and a report on the shared item."""
        item_id = f"slack:C2/{number}"
        own = (
            ["transition", item_id, "surfaced", "--note", "s"]
            if number % 2
            else ["record-delegation", item_id, "--window", f"w{number}", "--task", "investigate"]
        )
        return [own, ["report", MENTION_ID, "--summary", f"report {number}"]]

    def test_mixed_concurrent_writes_are_all_kept(self) -> None:
        self.run_wave([["upsert", MENTION_ID, "--kind", "mention"]])
        self.run_wave(
            [
                command
                for number in range(self.WRITERS)
                for command in (
                    ["upsert", f"slack:C2/{number}", "--kind", "mention", "--field", f"n={number}"],
                    ["note", MENTION_ID, f"note {number}"],
                )
            ]
        )
        self.run_wave([command for number in range(self.WRITERS) for command in self.second_wave(number)])
        items = json.loads(Path(self.state.name, "ledger.json").read_text())["items"]
        self.assertEqual(len(items), self.WRITERS + 1)
        events = [h["event"] for h in items[MENTION_ID]["history"]]
        self.assertEqual((events.count("note"), events.count("report")), (self.WRITERS, self.WRITERS))
        for number in range(self.WRITERS):
            item = items[f"slack:C2/{number}"]
            self.assertEqual(item["fields"]["n"], str(number))
            got = item["state"] if number % 2 else item["delegation"]["window"]
            self.assertEqual(got, "surfaced" if number % 2 else f"w{number}")


if __name__ == "__main__":
    unittest.main()
