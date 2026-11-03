"""Unit tests for the ledger's hand-over protocol (delegations, updates, reports) and its storage
robustness (backups, corrupt files, history trimming, concurrent writers), and the journal every
write goes through (genesis, a crash between the journal and the file, a torn line)."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import ledger
from change_log import Change, canonical
from journal import Journal, replica_id
from ledger_changes import EMPTY, diff, rebuild, state_hash, trim_history


LIB = Path(ledger.__file__).parent
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "console" / "acme-ledger.json"
MENTION_ID = "slack:C1/1.1"
REVIEW_ID = "review:acme/api#7"
MYPR_ID = "mypr:acme/api#8"


def run(*argv: str) -> Any:
    args = ledger.build_parser().parse_args(list(argv))
    return args.func(args)


def journal(state_dir: Path | None = None) -> Journal:
    """The ledger's journal in <state_dir> (default: the configured one)."""
    state = state_dir or ledger.STORE.state_dir
    return Journal(state / "journal" / "ledger", "ledger", replica_id(state), ledger.RULES_VERSION)


def saved(state_dir: Path | None = None) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(((state_dir or ledger.STORE.state_dir) / "ledger.json").read_text())
    return data


def without_journal(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key != "journal"}


def contiguous(changes: list[Change]) -> bool:
    """Whether each file's changes are numbered 1..n, with no gap and none twice."""
    by_file: dict[str, list[int]] = {}
    for change in changes:
        by_file.setdefault("local" if change.get("local") else change["replica"], []).append(change["seq"])
    return all(sorted(seqs) == list(range(1, len(seqs) + 1)) for seqs in by_file.values())


class JournalChecks(unittest.TestCase):
    def assert_the_journal_rebuilds_the_file(self, state_dir: Path | None = None) -> None:
        """ledger.json is the rebuild of the journal (as canonical bytes: every list in order),
        with no conflict, at the journal's position and with the digest of what it holds; each
        file numbered with no gap."""
        found = journal(state_dir)
        changes = found.changes()
        result = rebuild(changes, ledger.RULES)
        self.assertEqual(result.conflicts, [])
        self.assertTrue(contiguous(changes))
        data = saved(state_dir)
        self.assertEqual(ledger.saved_position(data), found.position())
        self.assertEqual(data["journal"]["digest"], state_hash(data))
        self.assertEqual(canonical(result.ledger).decode(), canonical(without_journal(data)).decode())


class ProtocolTestCase(JournalChecks):
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

    def notes(self, item_id: str) -> list[str]:
        return [entry.get("note", "") for entry in self.item(item_id)["history"]]


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
        self.assert_the_journal_rebuilds_the_file()  # the journal keeps all, the rebuild trims


class JournalTest(ProtocolTestCase):
    """Every write is one transaction in the journal, which ledger.json is the rebuild of."""

    def test_the_journal_rebuilds_the_ledger_after_every_command(self) -> None:
        self.assert_the_journal_rebuilds_the_file()  # the setUp's three upserts
        for command in (
            ["record-delegation", MENTION_ID, "--window", "exports", "--task", "new-work", "--ask", "do it"],
            ["record-update", MENTION_ID, "--text", "match the fields"],
            ["report", MENTION_ID, "--summary", "draft PR up", "--result", "https://x/pr/8"],
            ["transition", MENTION_ID, "done", "--note", "answered"],
            ["link", REVIEW_ID, "relates_to", MYPR_ID],
            ["record-source", "https://slack/p1", "--class", "fyi"],
            ["mark-run", "check-in"],
            ["render"],
        ):
            run(*command)
            with self.subTest(command=command[0]):
                self.assert_the_journal_rebuilds_the_file()

    def test_each_write_is_one_transaction_named_by_its_command(self) -> None:
        before = len(journal().changes())
        argv = ["ledger.py", "record-delegation", MENTION_ID, "--window", "w", "--task", "investigate"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict(os.environ, {"BOSUN_STATE_DIR": self.tmp.name}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(ledger.main(), 0)
        made = journal().changes()[before:]
        self.assertGreater(len(made), 1)
        named = {(c["cmd"], c["tx"], c["rules"]) for c in made}
        self.assertEqual(named, {("ledger record-delegation", made[0]["tx"], ledger.RULES_VERSION)})
        self.assertEqual(ledger.RULES_VERSION, 1)

    def test_a_write_that_changes_nothing_adds_nothing(self) -> None:
        before = journal().changes()
        run("render")
        run("upsert", MENTION_ID, "--kind", "mention", "--title", "warnings format")  # unchanged
        self.assertEqual(journal().changes(), before)
        self.assert_the_journal_rebuilds_the_file()

    def test_a_crash_after_the_journal_is_repaired(self) -> None:
        """A transaction in the journal that never reached ledger.json (committed by hand, without
        saving): a reader sees it without writing; the next write has it once and saves it."""
        before = saved()
        after = copy.deepcopy(before)
        ledger.record(after["items"][MENTION_ID], "note", "in the journal only")
        transaction = journal().begin("test")
        for edit in diff(before, after):
            transaction.add(edit.op, edit.target, edit.body, local=edit.local)
        transaction.commit()
        file = ledger.STORE.ledger_file.read_text()
        self.assertIn("in the journal only", run("show", MENTION_ID))
        with ledger.locked_ledger(write=False) as data:
            # the rebuild made the items in the order the setUp did; a command sees them by id, as
            # ledger.json loads them (render, reach-out and others go by that order)
            self.assertEqual(list(data["items"]), sorted([MENTION_ID, REVIEW_ID, MYPR_ID]))
        self.assertEqual(ledger.STORE.ledger_file.read_text(), file)
        run("note", MENTION_ID, "next")
        self.assertEqual(self.notes(MENTION_ID)[-2:], ["in the journal only", "next"])
        self.assertEqual(self.notes(MENTION_ID).count("in the journal only"), 1)
        self.assertEqual(ledger.saved_position(saved()), journal().position())
        self.assertEqual(ledger.STORE.backup.read_text(), file)  # one save: the backup is the file before
        self.assert_the_journal_rebuilds_the_file()

    def test_a_write_whose_save_failed_is_kept_once(self) -> None:
        with mock.patch.object(ledger, "save", side_effect=OSError("disk full")), self.assertRaises(OSError):
            run("note", MENTION_ID, "in the journal only")
        self.assertNotIn("in the journal only", self.notes(MENTION_ID))
        run("note", MENTION_ID, "next")
        self.assertEqual(self.notes(MENTION_ID)[-2:], ["in the journal only", "next"])
        self.assert_the_journal_rebuilds_the_file()

    def test_a_command_that_fails_writes_nothing(self) -> None:
        before = journal().changes()
        with self.assertRaises(ledger.LedgerError):
            run("transition", MENTION_ID, "posted", "--note", "x")
        self.assertEqual(journal().changes(), before)

    def test_a_torn_journal_tail_is_set_aside(self) -> None:
        path = ledger.STORE.journal_dir / f"{replica_id(ledger.STORE.state_dir)}.jsonl"
        with path.open("ab") as handle:
            handle.write(b'{"v":1,"seq')
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            run("note", MENTION_ID, "after the cut")
        (aside,) = ledger.STORE.journal_dir.glob("*.torn-*")
        self.assertEqual(aside.read_bytes(), b'{"v":1,"seq')
        self.assertIn(aside.name, said.getvalue())
        self.assertEqual(self.notes(MENTION_ID)[-1], "after the cut")
        self.assert_the_journal_rebuilds_the_file()

    def test_a_damaged_journal_line_stops_a_write_naming_it(self) -> None:
        path = ledger.STORE.journal_dir / f"{replica_id(ledger.STORE.state_dir)}.jsonl"
        lines = path.read_bytes().splitlines(keepends=True)
        file = ledger.STORE.ledger_file.read_text()
        for text, number, behind in (
            (b"".join(lines) + b"nonsense\n", len(lines) + 1, False),  # a whole last line
            (lines[0] + b"nonsense\n" + b"".join(lines[1:]), 2, True),  # found when it rebuilds
        ):
            path.write_bytes(text)
            if behind:
                data = saved()
                data["journal"] = {}
                ledger.STORE.ledger_file.write_text(json.dumps(data))
            with (
                self.subTest(line=number),
                self.assertRaisesRegex(ledger.LedgerError, rf"{path.name}, line {number}"),
            ):
                run("note", MENTION_ID, "x")
        self.assertNotIn("x", self.notes(MENTION_ID))
        ledger.STORE.ledger_file.write_text(file)
        path.write_bytes(b"".join(lines))
        self.assertIn(MENTION_ID, run("list"))  # a reader is not stopped: the file is whole

    def test_a_reader_leaves_out_an_unfinished_transaction_silently(self) -> None:
        path = ledger.STORE.journal_dir / f"{replica_id(ledger.STORE.state_dir)}.jsonl"
        kept = path.read_bytes()
        run("record-delegation", MENTION_ID, "--window", "w", "--task", "investigate", "--ask", "why?")
        path.write_bytes(kept + b'{"v":1,"seq')  # cut short: its whole lines gone, a line torn
        ledger.STORE.ledger_file.write_text(ledger.STORE.backup.read_text())  # not saved either
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.assertNotIn("@w", run("list"))
        self.assertEqual(said.getvalue(), "")
        self.assertEqual(list(ledger.STORE.journal_dir.glob("*.torn-*")), [])  # readers never repair

    def test_a_write_cut_between_the_files_is_set_aside(self) -> None:
        """Interrupted after local.jsonl, before the replica's file (a signal between the two): the
        local lines are left out, then set aside; the file is as it was."""
        path = ledger.STORE.journal_dir / f"{replica_id(ledger.STORE.state_dir)}.jsonl"
        kept, file = path.read_bytes(), ledger.STORE.ledger_file.read_text()
        local = ledger.STORE.journal_dir / "local.jsonl"
        self.assertFalse(local.exists())
        before = saved()
        after = copy.deepcopy(before)
        ledger.record(after["items"][MENTION_ID], "user-said", "my own words")  # a local entry
        ledger.record(after["items"][MENTION_ID], "note", "and a private one")
        transaction = journal().begin("test")
        for edit in diff(before, after):
            transaction.add(edit.op, edit.target, edit.body, local=edit.local)
        transaction.commit()
        path.write_bytes(kept)  # the replica's file never got its lines
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.assertNotIn("my own words", run("show", MENTION_ID))
            self.assertEqual(said.getvalue(), "")
            run("note", MENTION_ID, "next")
        self.assertIn("unfinished", said.getvalue())
        (aside,) = ledger.STORE.journal_dir.glob("local.jsonl.torn-*")
        self.assertIn("my own words", aside.read_text())
        self.assertNotIn("my own words", ledger.STORE.ledger_file.read_text())
        self.assertEqual(ledger.STORE.backup.read_text(), file)
        self.assert_the_journal_rebuilds_the_file()

    def damaged_and_ahead(self) -> Path:
        """A line in the middle of the replica's file damaged, and a transaction ledger.json never
        got (so a reader must rebuild, and so read the whole file)."""
        run("note", MENTION_ID, "one")
        path = ledger.STORE.journal_dir / f"{replica_id(ledger.STORE.state_dir)}.jsonl"
        lines = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(lines[0] + b"nonsense\n" + b"".join(lines[1:]))
        data = saved()
        data["journal"] = {}
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        return path

    def test_a_reader_warns_once_about_a_damaged_journal_and_reads_the_file(self) -> None:
        path = self.damaged_and_ahead()
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.assertIn(MENTION_ID, run("list"))
        self.assertEqual(said.getvalue().count("\n"), 1)
        self.assertIn(f"{path.name}, line 2", said.getvalue())
        self.assertIn("reading ledger.json as it is", said.getvalue())
        lines = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(b"".join(lines[:1] + lines[2:]) + b"nonsense too\n")  # a whole last line
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.assertIn(MENTION_ID, run("list"))
        self.assertIn(f"{path.name}, line {len(lines)}", said.getvalue())

    def test_a_journal_that_lost_a_file_is_refused_and_nothing_is_written(self) -> None:
        run("record-delegation", MENTION_ID, "--window", "w", "--task", "t")
        hook_said = ledger.add_user_said("w", "my own words")  # a local line too
        self.assertEqual(hook_said["items"], [MENTION_ID])
        for name in (f"{replica_id(ledger.STORE.state_dir)}.jsonl", "local.jsonl"):
            with self.subTest(lost=name):
                lost = ledger.STORE.journal_dir / name
                kept = lost.read_bytes()
                lost.unlink()
                file, backup = ledger.STORE.ledger_file.read_text(), ledger.STORE.backup.read_text()
                refused = rf"behind ledger.json \({Path(name).stem}\.jsonl ends at seq 0"
                with self.assertRaisesRegex(ledger.LedgerError, refused):
                    run("note", MENTION_ID, "x")
                self.assertEqual(ledger.STORE.ledger_file.read_text(), file)
                self.assertEqual(ledger.STORE.backup.read_text(), backup)
                said = io.StringIO()
                with contextlib.redirect_stderr(said):
                    self.assertIn("my own words", run("said"))  # a reader gets the file, warned
                self.assertIn("is behind it", said.getvalue())
                lost.write_bytes(kept)
        self.assert_the_journal_rebuilds_the_file()

    def test_moving_a_journal_that_lost_a_file_aside_starts_it_again(self) -> None:
        (ledger.STORE.journal_dir / f"{replica_id(ledger.STORE.state_dir)}.jsonl").unlink()
        file = saved()
        ledger.STORE.journal_dir.rename(ledger.STORE.journal_dir.with_name("ledger.lost"))
        run("note", MENTION_ID, "after")
        self.assertEqual(saved()["items"][REVIEW_ID], file["items"][REVIEW_ID])
        self.assertEqual({c["cmd"] for c in journal().changes()}, {"genesis", ""})
        self.assert_the_journal_rebuilds_the_file()

    def test_a_write_whose_rebuild_has_conflicts_says_so_once(self) -> None:
        """A change the rebuild keeps but cannot apply: a write that rebuilds says how many, and
        where to see them, once on stderr; a reader's rebuild stays quiet."""
        transaction = journal().begin("test")
        transaction.add("item.state", MENTION_ID, {"from": "nonsense", "to": "done"})
        transaction.commit()
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            run("list")
            self.assertEqual(said.getvalue(), "")
            run("note", MENTION_ID, "next")
        self.assertEqual(said.getvalue().count("\n"), 1)
        self.assertIn("1 conflict", said.getvalue())
        self.assertIn("bosun ledger validate --journal", said.getvalue())

    def test_following_the_advice_for_a_damaged_journal_keeps_everything(self) -> None:
        """Moving the journal folder and replica.json aside together: the next write starts a new
        journal from ledger.json, as a new replica (so no id is minted twice), and loses nothing."""
        run("note", MENTION_ID, "kept")
        old = replica_id(ledger.STORE.state_dir)
        path = ledger.STORE.journal_dir / f"{old}.jsonl"
        path.write_bytes(path.read_bytes() + b"nonsense\n")  # a whole last line: every write reads it
        with self.assertRaises(ledger.LedgerError) as refused:
            run("note", MENTION_ID, "refused")
        advice = str(refused.exception)
        for named in (
            str(ledger.STORE.journal_dir),
            str(ledger.STORE.state_dir / "replica.json"),
            "together",
        ):
            self.assertIn(named, advice)
        file = saved()
        aside = Path(self.enterContext(tempfile.TemporaryDirectory()))
        ledger.STORE.journal_dir.rename(aside / "ledger")
        (ledger.STORE.state_dir / "replica.json").rename(aside / "replica.json")
        run("note", MENTION_ID, "after")
        self.assertNotEqual(replica_id(ledger.STORE.state_dir), old)
        self.assertEqual(saved()["items"][REVIEW_ID], file["items"][REVIEW_ID])
        self.assertEqual(self.notes(MENTION_ID)[-2:], ["kept", "after"])
        self.assertEqual(next(c["cmd"] for c in journal().changes()), "genesis")
        self.assert_the_journal_rebuilds_the_file()

    def test_a_gap_in_the_journal_names_the_missing_seq(self) -> None:
        """A line moved out of the middle (what the old advice said to do): validate reports it,
        and a write that has to rebuild refuses, naming the file and the seq."""
        path = ledger.STORE.journal_dir / f"{replica_id(ledger.STORE.state_dir)}.jsonl"
        lines = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(b"".join(lines[:1] + lines[2:]))
        output, code = run("validate", "--journal")
        self.assertEqual(code, 1)
        self.assertIn(f"{path.name}, line 2: seq 3 where seq 2 was due: seq 2 is missing", output)
        data = saved()
        data["journal"] = {}  # ledger.json behind the journal: the next write rebuilds
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        with self.assertRaisesRegex(ledger.LedgerError, "seq 2 is missing"):
            run("note", MENTION_ID, "x")

    def test_ledger_json_has_these_top_level_keys(self) -> None:
        """diff compares items, sources, runs and said, and the rebuild makes only those (and
        version); journal is the file's own. A new top-level key would be dropped by every rebuild:
        give it rows in ledger_changes before adding it here."""
        self.assertEqual(sorted(saved()), ["items", "journal", "runs", "said", "sources", "version"])
        self.assertEqual(sorted(ledger.load()), sorted(saved()))
        self.assertEqual(sorted(EMPTY), ["items", "runs", "said", "sources", "version"])

    def test_ledger_json_reaches_the_disk_itself_before_its_rename(self) -> None:
        flushed: list[int] = []
        with mock.patch.object(ledger, "flush_to_disk", side_effect=flushed.append):
            run("note", MENTION_ID, "x")
        self.assertEqual(len(flushed), 1)  # ledger.json; ledger.md is a view of it

    def test_the_transaction_is_there_only_while_writing(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "no ledger write"):
            ledger.current_transaction()
        with ledger.locked_ledger(write=False), self.assertRaises(ledger.LedgerError):
            ledger.current_transaction()
        with ledger.locked_ledger(write=True):
            first = ledger.current_transaction()
        with ledger.locked_ledger(write=True):
            self.assertIsNot(ledger.current_transaction(), first)
        with self.assertRaises(RuntimeError), ledger.locked_ledger(write=True):
            raise RuntimeError("the command failed")
        with self.assertRaises(ledger.LedgerError):
            ledger.current_transaction()


class OutsideEditTest(ProtocolTestCase):
    """ledger.json changed outside the journal: by hand, or by a bosun that does not journal (it
    writes every top-level key back, so the "journal" key stays as it was). The next write puts
    the change in the journal ("adopt"), so that no later rebuild loses it."""

    OLD_ITEM = "review:acme/api#9"

    def old_bosun_writes(self) -> None:
        """What an older bosun's save does: load the file, change it, write it back whole."""
        data = saved()
        data["items"][REVIEW_ID]["title"] = "API review (renamed by the old bosun)"
        made = {
            **copy.deepcopy(data["items"][REVIEW_ID]),
            "id": self.OLD_ITEM,
            "title": "made by the old bosun",
        }
        data["items"][self.OLD_ITEM] = made
        ledger.record(data["items"][MENTION_ID], "note", "noted by the old bosun")
        ledger.STORE.ledger_file.write_text(json.dumps(data, indent=2, sort_keys=True))

    def assert_the_old_bosuns_write_is_kept(self) -> None:
        items = saved()["items"]
        self.assertEqual(items[self.OLD_ITEM]["title"], "made by the old bosun")
        self.assertEqual(items[REVIEW_ID]["title"], "API review (renamed by the old bosun)")
        self.assertEqual(self.notes(MENTION_ID).count("noted by the old bosun"), 1)
        self.assertEqual(run("validate", "--journal"), ("ledger is valid", 0))
        self.assert_the_journal_rebuilds_the_file()

    def cut_short(self, note: str) -> str:
        """A write cut between the journal and the save (the power went); what it said."""
        said = io.StringIO()
        with (
            mock.patch.object(ledger, "save", side_effect=OSError("power cut")),
            self.assertRaises(OSError),
            contextlib.redirect_stderr(said),
        ):
            run("note", REVIEW_ID, note)
        return said.getvalue()

    def test_an_old_bosuns_write_survives_a_write_cut_short_after_it(self) -> None:
        self.old_bosun_writes()
        self.assertIn("adopted", self.cut_short("cut short"))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            run("note", REVIEW_ID, "the next write")
        self.assertEqual(said.getvalue(), "")  # adopted once, by the write that was cut short
        self.assertEqual(self.notes(REVIEW_ID)[-2:], ["cut short", "the next write"])
        self.assert_the_old_bosuns_write_is_kept()

    def test_an_old_bosuns_write_after_a_write_cut_short_is_kept_with_it(self) -> None:
        self.assertEqual(self.cut_short("cut short"), "")
        self.old_bosun_writes()  # from ledger.json as it was before the cut write
        with contextlib.redirect_stderr(io.StringIO()):
            run("note", REVIEW_ID, "the next write")
        self.assertEqual(self.notes(REVIEW_ID)[-2:], ["cut short", "the next write"])
        self.assert_the_old_bosuns_write_is_kept()

    def test_a_hand_edit_is_adopted_by_the_next_write(self) -> None:
        data = saved()
        data["items"][MENTION_ID]["title"] = "edited by hand"
        ledger.STORE.ledger_file.write_text(json.dumps(data, indent=2))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            run("note", REVIEW_ID, "next")
        self.assertEqual(said.getvalue().count("\n"), 1)
        self.assertIn("adopted", said.getvalue())
        adopted = [
            (c["op"], c["target"], c["body"]["path"]) for c in journal().changes() if c["cmd"] == "adopt"
        ]
        self.assertEqual(adopted, [("item.set", MENTION_ID, ["title"])])
        self.assertEqual(self.item(MENTION_ID)["title"], "edited by hand")
        self.assertEqual(run("validate", "--journal"), ("ledger is valid", 0))
        self.assert_the_journal_rebuilds_the_file()

    def test_an_unchanged_file_adopts_nothing(self) -> None:
        ledger.STORE.ledger_file.write_text(json.dumps(saved()))  # the same content, laid out otherwise
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            run("note", REVIEW_ID, "next")
        self.assertEqual(said.getvalue(), "")
        self.assertNotIn("adopt", {c["cmd"] for c in journal().changes()})
        self.assert_the_journal_rebuilds_the_file()

    def test_a_history_entry_removed_by_hand_comes_back_and_the_write_says_so(self) -> None:
        """What the journal cannot hold (history only grows): the next write goes on from the
        journal's rebuild, so the entry is back, says so once, and keeps the edited file as
        ledger.json.bak; ledger.json stays the journal's rebuild (re-review probe q1)."""
        run("note", REVIEW_ID, "a note I regret")
        run("note", REVIEW_ID, "kept")
        data = saved()
        history = data["items"][REVIEW_ID]["history"]
        data["items"][REVIEW_ID]["history"] = [h for h in history if h.get("note") != "a note I regret"]
        edited = json.dumps(data, indent=2)
        ledger.STORE.ledger_file.write_text(edited)
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            run("note", REVIEW_ID, "after")
        self.assertEqual(self.notes(REVIEW_ID)[-3:], ["a note I regret", "kept", "after"])
        self.assertEqual(said.getvalue().count("\n"), 1, said.getvalue())
        self.assertIn("history only grows", said.getvalue())
        self.assertIn(str(ledger.STORE.backup), said.getvalue())
        self.assertEqual(ledger.STORE.backup.read_text(), edited)
        self.assertEqual(run("validate", "--journal"), ("ledger is valid", 0))
        self.assert_the_journal_rebuilds_the_file()

    def test_a_float_written_by_hand_where_diff_does_not_look_stops_nothing(self) -> None:
        """A float version (re-review probe q1): the next write succeeds from the journal's
        rebuild, never says nothing was written after journaling its change, and validate passes."""
        data = saved()
        data["version"] = 1.0
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            run("note", REVIEW_ID, "after a float version")
        self.assertNotIn("nothing was written", said.getvalue())
        self.assertEqual(self.notes(REVIEW_ID)[-1], "after a float version")
        self.assertEqual(saved()["version"], 1)
        self.assertEqual(run("validate", "--journal"), ("ledger is valid", 0))
        self.assert_the_journal_rebuilds_the_file()

    def test_a_link_written_by_hand_that_is_not_one_is_left_out_with_a_note(self) -> None:
        """A link that is not an object with a rel and an id (re-review probe q2): the next write
        leaves it out, saying so once, instead of crashing or keeping a conflict; validate passes."""
        for bad in ("nonsense", {"rel": "relates_to"}):
            with self.subTest(link=bad):
                self.tearDown()
                self.setUp()
                run("link", MYPR_ID, "relates_to", REVIEW_ID)
                data = saved()
                kept = list(data["items"][MYPR_ID]["links"])
                data["items"][MYPR_ID]["links"].append(bad)
                ledger.STORE.ledger_file.write_text(json.dumps(data, indent=2))
                said = io.StringIO()
                with contextlib.redirect_stderr(said):
                    run("note", REVIEW_ID, "after")
                self.assertEqual(said.getvalue().count("\n"), 1, said.getvalue())
                self.assertIn(f"{MYPR_ID} links: {bad!r}", said.getvalue())
                self.assertIn("left out", said.getvalue())
                self.assertEqual(saved()["items"][MYPR_ID]["links"], kept)
                self.assertEqual(run("validate", "--journal"), ("ledger is valid", 0))
                self.assert_the_journal_rebuilds_the_file()

    def test_a_file_saved_before_digests_is_trusted_and_gets_one(self) -> None:
        data = saved()
        del data["journal"]["digest"]
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        run("note", REVIEW_ID, "next")
        self.assertNotIn("adopt", {c["cmd"] for c in journal().changes()})
        self.assert_the_journal_rebuilds_the_file()


class BoundaryTest(ProtocolTestCase):
    """What the journal cannot hold (a float, text that is not valid Unicode) is refused before
    anything is written, naming the item and the field."""

    def test_a_float_in_a_tool_call_is_refused_naming_the_field(self) -> None:
        before, file = journal().changes(), ledger.STORE.ledger_file.read_text()
        arguments = json.dumps({"issue": "ACME-1", "story_points": 2.5})
        named = re.escape(
            f"nothing was written: {MYPR_ID} proposal.spec.args.story_points holds a float (2.5)"
        )
        with self.assertRaisesRegex(ledger.LedgerError, named):
            run(
                "propose",
                MYPR_ID,
                "set the estimate",
                "--tool",
                "mcp__jira__edit_issue",
                "--args-json",
                arguments,
            )
        self.assertEqual(journal().changes(), before)
        self.assertEqual(ledger.STORE.ledger_file.read_text(), file)

    def test_text_that_is_not_valid_unicode_is_refused_not_a_traceback(self) -> None:
        argv = ["ledger.py", "note", MENTION_ID, "r\udce9sum\udce9"]  # argv's bytes were not UTF-8
        said = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict(os.environ, {"BOSUN_STATE_DIR": self.tmp.name}),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(said),
        ):
            self.assertEqual(ledger.main(), 1)
        self.assertIn(
            f"nothing was written: {MENTION_ID} history.note holds text that is not valid", said.getvalue()
        )
        self.assertNotIn("r\ufffdsum", ledger.STORE.ledger_file.read_text())


class GenesisTest(JournalChecks):
    """A ledger written before journals: its first write puts it in the journal whole (genesis),
    and the rebuild is the file again. Augmented beyond the fixture, so that it holds what an old
    ledger may: a numeric task id, a trimmed history, an approval with my words and the call's
    context, and what I said."""

    OLD_TASK = "task:2026-10-15/1"
    LONG = "slack:C0000000001/1700000001.000001"
    APPROVED = "mypr:acme/exporter#103"
    QUOTE = "yes, export it"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ledger.configure(self.tmp.name)
        self.data = self.old_ledger()
        ledger.STORE.ledger_file.write_text(json.dumps(self.data, indent=2, sort_keys=True))

    def old_ledger(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(FIXTURE.read_text())
        items = data["items"]
        old = copy.deepcopy(items["task:2026-10-20/veembc"])
        items[self.OLD_TASK] = {**old, "id": self.OLD_TASK, "created_at": "2026-10-15T09:00:00+00:00"}
        long = items[self.LONG]
        long["history"] += [
            {"at": f"2026-10-21T{n // 60:02d}:{n % 60:02d}:00+00:00", "event": "note", "note": f"n{n}"}
            for n in range(ledger.MAX_HISTORY + 40)
        ]
        trim_history(long)
        self.assertEqual(long["history"][1]["event"], "history-trimmed")
        items[self.APPROVED].update(proposal=self.approved_proposal())
        items[self.APPROVED]["history"] += [
            {"at": "2026-10-21T10:00:00+00:00", "event": "approved", "note": self.QUOTE, "action": "Run"},
            {"at": "2026-10-21T10:01:00+00:00", "event": "approval-used", "note": self.QUOTE, "tool": "Bash"},
        ]
        said = {"at": "2026-10-21T10:00:30+00:00", "window": "exporter", "text": "ship it", "items": []}
        data["said"] += [{**said, "also_held": []}]
        return data

    def approved_proposal(self) -> dict[str, Any]:
        return {
            "action": "Run make export",
            "proposed_at": "2026-10-21T09:59:00+00:00",
            "spec": {"type": "run", "command": "make export"},
            "approval": {
                "at": "2026-10-21T10:00:00+00:00",
                "quote": self.QUOTE,
                "hash": "0" * 64,
                "used_at": "2026-10-21T10:01:00+00:00",
                "call": "1" * 64,
                "session": "s-0002",
                "tool_use_id": "toolu_0001",
                "transcript": "/Users/me/.claude/projects/acme/s-0002.jsonl",
            },
        }

    def test_genesis_reproduces_an_existing_ledger(self) -> None:
        file = ledger.STORE.ledger_file.read_text()
        run("list")
        self.assertFalse(ledger.STORE.journal_dir.exists())  # a reader never writes
        self.assertEqual(ledger.STORE.ledger_file.read_text(), file)
        run("render")
        changes = journal().changes()
        self.assertEqual({(c["cmd"], c["tx"]) for c in changes}, {("genesis", changes[0]["tx"])})
        creates = [c["target"] for c in changes if c["op"] == "item.create"]
        self.assertEqual(creates, sorted(self.data["items"]))  # by id, not as the file had them
        self.assert_the_journal_rebuilds_the_file()
        self.assertEqual(canonical(without_journal(saved())), canonical(self.data))  # only "journal" is new
        private = (ledger.STORE.journal_dir / f"{replica_id(ledger.STORE.state_dir)}.jsonl").read_text()
        local = (ledger.STORE.journal_dir / "local.jsonl").read_text()
        for mine in (self.QUOTE, "s-0002", "toolu_0001", "ship it"):
            with self.subTest(mine=mine):
                self.assertNotIn(mine, private)
                self.assertIn(mine, local)

    def test_a_journal_position_the_file_brought_from_elsewhere_ends_at_genesis(self) -> None:
        """A copy (the chores dry run's scratch ledger, a restore) with no journal: genesis puts
        it in a new one and saves its new position at once, so a command that then fails leaves
        no stale position behind to refuse the next write."""
        self.data["journal"] = {"f" * 32: 57, "local": 3}
        ledger.STORE.ledger_file.write_text(json.dumps(self.data))
        with self.assertRaises(ledger.LedgerError):
            run("transition", self.OLD_TASK, "nonsense", "--note", "fails after genesis")
        self.assertEqual(ledger.saved_position(saved()), journal().position())
        run("note", self.OLD_TASK, "first write")
        self.assertEqual(saved()["items"][self.OLD_TASK]["history"][-1]["note"], "first write")
        self.assert_the_journal_rebuilds_the_file()

    def genesis_by_hand(self) -> Path:
        """A genesis committed but never saved, as a first write cut short leaves it; the
        replica's file."""
        ledger.commit_edits(journal().begin(ledger.GENESIS), diff(EMPTY, ledger.load()))
        return ledger.STORE.journal_dir / f"{replica_id(ledger.STORE.state_dir)}.jsonl"

    def assert_the_ledger_is_whole(self) -> None:
        self.assertEqual(canonical(without_journal(saved())), canonical(self.data))
        backup = json.loads(ledger.STORE.backup.read_text())
        self.assertEqual(canonical(without_journal(backup)), canonical(self.data))
        self.assert_the_journal_rebuilds_the_file()

    def test_a_genesis_cut_short_is_set_aside_and_made_again(self) -> None:
        """Power lost mid-way through the replica's file: its whole lines are fewer than the
        transaction says, so it is set aside (with its local lines) and genesis runs again; the
        items stay, in the file and its backup."""
        path = self.genesis_by_hand()
        path.write_bytes(path.read_bytes()[: len(path.read_bytes()) // 2])
        with contextlib.redirect_stderr(io.StringIO()):
            run("render")
        self.assertEqual(len(list(ledger.STORE.journal_dir.glob("*.torn-*"))), 2)
        self.assert_the_ledger_is_whole()

    def test_a_genesis_stopped_between_the_files_keeps_my_words(self) -> None:
        """A signal after local.jsonl and before the replica's file: what I said and the approval's
        quote are kept, in the file and in the new genesis."""
        self.genesis_by_hand().write_bytes(b"")
        with contextlib.redirect_stderr(io.StringIO()):
            run("render")
        self.assert_the_ledger_is_whole()
        self.assertIn(self.QUOTE, (ledger.STORE.journal_dir / "local.jsonl").read_text())

    def test_a_ledger_without_said_gains_an_empty_one(self) -> None:
        del self.data["said"]
        ledger.STORE.ledger_file.write_text(json.dumps(self.data))
        run("render")
        self.assertEqual(saved()["said"], [])
        self.assert_the_journal_rebuilds_the_file()

    def test_an_empty_ledger_needs_no_genesis(self) -> None:
        ledger.STORE.ledger_file.unlink()
        run("render")
        self.assertFalse(ledger.STORE.journal_dir.exists())
        self.assertEqual(ledger.saved_position(saved()), {})

    def test_a_float_or_broken_text_from_before_journals_is_converted_once_with_a_note(self) -> None:
        """What the old ledger took and the journal cannot hold: a float becomes its text, a lone
        surrogate U+FFFD, each with a note on stderr; the writes after it work."""
        task = self.data["items"][self.OLD_TASK]
        task["fields"]["estimate"] = 1.5  # written by hand
        task["title"] = "r\udce9sum\udce9 export"  # argv's bytes were not UTF-8
        ledger.STORE.ledger_file.write_text(json.dumps(self.data))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            run("note", self.OLD_TASK, "first write")
        notes = said.getvalue().splitlines()
        self.assertEqual(len(notes), 2, notes)
        for place in ("fields.estimate", "title"):
            self.assertTrue(any(f"{self.OLD_TASK} {place}:" in note for note in notes), (place, notes))
        task = saved()["items"][self.OLD_TASK]
        self.assertEqual((task["fields"]["estimate"], task["title"]), ("1.5", "r\ufffdsum\ufffd export"))
        run("note", self.OLD_TASK, "second write")
        self.assertEqual(saved()["items"][self.OLD_TASK]["history"][-1]["note"], "second write")
        self.assert_the_journal_rebuilds_the_file()


class ConcurrentWritersTest(JournalChecks):
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

    def test_concurrent_writers_keep_contiguous_sequences(self) -> None:
        """Twenty writers at once (as in the smoke suite): one transaction each, numbered 1..n in
        <replica>.jsonl with no gap, and the rebuild is the file."""
        self.run_wave([["upsert", MENTION_ID, "--kind", "mention"]])
        mixed = [
            ["upsert", "slack:C2/{n}", "--kind", "mention"],
            ["note", MENTION_ID, "note {n}"],
            ["report", MENTION_ID, "--summary", "report {n}"],
            ["record-delegation", MENTION_ID, "--window", "w{n}", "--task", "investigate"],
        ]
        self.run_wave([[word.format(n=n) for word in mixed[n % 4]] for n in range(20)])
        state = Path(self.state.name)
        changes = journal(state).changes()
        self.assertTrue(contiguous(changes))
        self.assertEqual(len({c["tx"] for c in changes}), 21)
        lines = (state / "journal" / "ledger" / f"{replica_id(state)}.jsonl").read_text().splitlines()
        seqs = [json.loads(line)["seq"] for line in lines]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        self.assert_the_journal_rebuilds_the_file(state)


if __name__ == "__main__":
    unittest.main()
