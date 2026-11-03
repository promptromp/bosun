"""Unit tests for the ledger's hand-over protocol (delegations, updates, reports) and its storage
robustness (backups, corrupt files, history trimming, concurrent writers), and the journal every
write goes through (genesis, a crash between the journal and the file, a torn line)."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
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
from ledger_changes import diff, rebuild, trim_history


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
        with no conflict, at the journal's position; each file numbered with no gap."""
        found = journal(state_dir)
        changes = found.changes()
        result = rebuild(changes, ledger.RULES)
        self.assertEqual(result.conflicts, [])
        self.assertTrue(contiguous(changes))
        data = saved(state_dir)
        self.assertEqual(data["journal"], found.position())
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
        self.assertEqual(saved()["journal"], journal().position())
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

    def test_a_reader_does_not_rebuild_a_torn_journal(self) -> None:
        path = ledger.STORE.journal_dir / f"{replica_id(ledger.STORE.state_dir)}.jsonl"
        with path.open("ab") as handle:
            handle.write(b'{"v":1,"seq')
        self.assertIn(MENTION_ID, run("list"))
        self.assertEqual(list(ledger.STORE.journal_dir.glob("*.torn-*")), [])

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
        old = copy.deepcopy(items["task:2026-10-20/1"])
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

    def test_a_stale_journal_position_in_the_file_is_rebuilt_from_genesis(self) -> None:
        self.data["journal"] = {"f" * 32: 57, "local": 3}
        ledger.STORE.ledger_file.write_text(json.dumps(self.data))
        run("note", self.OLD_TASK, "first write")
        self.assertEqual(saved()["items"][self.OLD_TASK]["history"][-1]["note"], "first write")
        self.assert_the_journal_rebuilds_the_file()

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
        self.assertEqual(saved()["journal"], {})

    def test_a_ledger_the_journal_cannot_hold_is_refused_whole(self) -> None:
        self.data["items"][self.OLD_TASK]["fields"]["estimate"] = 1.5  # written by hand: a float
        ledger.STORE.ledger_file.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(ledger.LedgerError, "nothing was written: a change holds a float"):
            run("note", self.OLD_TASK, "x")
        self.assertEqual(journal().changes(), [])


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
