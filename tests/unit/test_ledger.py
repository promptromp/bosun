"""Unit tests for lib/ledger.py: ids and kinds, transitions, proposals, sources, runs, pruning."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import journal
import ledger
import update_gate
import update_state
import validation
from journal import replica_id


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


MINTED = "[0-9a-hjkmnp-tv-z]{6}"  # six Crockford base32 characters


class MintedIdTest(LedgerTestCase):
    """Task ids: the UTC day and six base32 characters hashed from the replica and the journal
    position, so two Macs never make the same one and a pruned one never comes back; old numeric
    ids stay valid."""

    def spawn(self, title: str = "Footer totals") -> str:
        output: str = run("spawn", "--title", title, "--ask", "add totals", "--via", "user: totals too")
        return output.splitlines()[0]

    def test_spawn_mints_a_hash_id(self) -> None:
        task_id = self.spawn()
        self.assertRegex(task_id, rf"^task:\d{{4}}-\d{{2}}-\d{{2}}/{MINTED}$")
        self.assertTrue(task_id.startswith(f"task:{datetime.now(UTC).date().isoformat()}/"))
        self.assertEqual(self.item(task_id)["kind"], "task")
        self.assertNotEqual(self.spawn("Header totals"), task_id)

    def test_old_task_ids_still_parse(self) -> None:
        self.assertIn("created", run("upsert", "task:2026-10-15/1", "--kind", "task", "--title", "Old"))
        self.assertIn("created", run("upsert", "task:2026-10-15/7k2m9q", "--kind", "task"))
        for bad in ("task:2026-10-15/0", "task:2026-10-15/7k2m9", "task:2026-10-15/7k2m9i", "task:x/1"):
            with self.subTest(bad), self.assertRaisesRegex(ledger.LedgerError, "task ids look like"):
                run("upsert", bad, "--kind", "task")
        for good in ("out:mypr:acme/api#2/3", "out:2026-10-15/1", f"out:{MYPR_ID}/7k2m9q"):
            self.assertRegex(good, ledger.OUTREACH_ID)
        self.assertIsNone(ledger.OUTREACH_ID.match("out:2026-10-15/7k2m9i"))

    def test_two_replicas_mint_different_ids(self) -> None:
        minted = []
        for replica in ("a" * 32, "b" * 32):
            with tempfile.TemporaryDirectory() as state:
                (Path(state) / "replica.json").write_text(json.dumps({"replica": replica}))
                ledger.configure(state)
                minted.append(self.spawn())
        self.assertNotEqual(minted[0], minted[1])

    def test_a_pruned_id_is_never_minted_again(self) -> None:
        first = self.spawn()
        run("transition", first, "declined", "--note", "not now")
        with ledger.locked_ledger(write=True) as data:
            data["items"][first]["updated_at"] = (datetime.now(UTC) - timedelta(days=40)).isoformat()
        self.assertIn("pruned 1 item(s)", run("prune", "--days", "30"))
        self.assertNotEqual(self.spawn(), first)

    def test_minting_needs_a_write(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "no ledger write in progress"):
            ledger.next_task_id({})
        with ledger.locked_ledger(write=False), self.assertRaisesRegex(ledger.LedgerError, "no ledger write"):
            ledger.next_outreach_id({}, MYPR_ID)

    def test_an_id_that_names_an_item_already_is_minted_again(self) -> None:
        today = datetime.now(UTC).date().isoformat()
        run("upsert", f"task:{today}/aaaaaa", "--kind", "task", "--title", "Earlier")
        with unittest.mock.patch.object(journal, "mint_suffix", side_effect=["aaaaaa", "bbbbbb"]) as minted:
            self.assertEqual(self.spawn(), f"task:{today}/bbbbbb")
        self.assertEqual([c.args[2] for c in minted.call_args_list], [0, 1])  # the same position, counted on
        self.assertEqual(self.item(f"task:{today}/aaaaaa")["title"], "Earlier")

    def test_compact_context_rows_come_oldest_first(self) -> None:
        made = []
        for number in (1, 2):
            item_id = f"mypr:acme/api#{number}"
            run("upsert", item_id, "--kind", "my-pr", "--title", f"PR {number}")
            run("propose", item_id, f"merge {number}")
            run("record-delegation", item_id, "--window", "w", "--task", "fix-my-pr")
            made.append(item_id)
        with ledger.locked_ledger(write=True) as data:  # the larger id is the older one
            data["items"][made[1]]["created_at"] = "2026-10-20T09:00:00+00:00"
            data["items"][made[0]]["created_at"] = "2026-10-20T10:00:00+00:00"
        items = self.data()["items"]
        self.assertEqual(
            [row.split(":", 2)[:2] for row in ledger.pending_approval_rows(items)],
            [["mypr", "acme/api#2"], ["mypr", "acme/api#1"]],
        )
        self.assertEqual([row.split()[0] for row in ledger.window_rows(items, "w")], [made[1], made[0]])

    def test_the_replica_comes_from_the_store_made_under_its_lock(self) -> None:
        self.assertFalse((ledger.STORE.state_dir / "replica.json").exists())
        made = ledger.replica()
        self.assertRegex(made, "^[0-9a-f]{32}$")
        self.assertEqual(ledger.replica(), made)
        run("upsert", REVIEW_ID, "--kind", "review-request")
        self.assertTrue((ledger.STORE.journal_dir / f"{made}.jsonl").is_file())


class ValidateTest(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        run("upsert", REVIEW_ID, "--kind", "review-request", "--title", "T")
        run("transition", REVIEW_ID, "delegated", "--note", "sent")

    def edit_in_place(self, change: Any) -> None:
        with ledger.locked_ledger(write=True) as data:
            change(data["items"])

    def test_validate_passes_a_clean_ledger(self) -> None:
        self.assertEqual(run("validate", "--journal"), ("ledger is valid", 0))
        self.assertEqual(run("validate"), ("ledger is valid", 0))

    def test_validate_finds_a_bad_state(self) -> None:
        data = self.data()
        data["items"][REVIEW_ID]["state"] = "nonsense"
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        output, code = run("validate")
        self.assertEqual(code, 1)
        self.assertIn(REVIEW_ID, output)
        self.assertIn("nonsense", output)
        found = json.loads(run("validate", "--json")[0])
        self.assertEqual([f["kind"] for f in found], ["state"])
        self.assertEqual(found[0]["item"], REVIEW_ID)

    def test_validate_finds_links_that_break_a_rule(self) -> None:
        run("upsert", MENTION_ID, "--kind", "mention")
        data = self.data()
        data["items"][MENTION_ID]["links"] = [
            {"rel": "part_of", "id": "ws:a"},
            {"rel": "part_of", "id": "ws:b"},
        ]
        data["items"][REVIEW_ID]["links"] = [{"rel": "relates_to", "id": "slack:C9/9"}]
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        found = json.loads(run("validate", "--json")[0])
        self.assertIn(("part_of", MENTION_ID), [(f["kind"], f["item"]) for f in found])
        self.assertIn(("link-target", REVIEW_ID), [(f["kind"], f["item"]) for f in found])

    def test_validate_counts_distinct_part_of_targets(self) -> None:
        run("upsert", MENTION_ID, "--kind", "mention")
        run("ws", "add", "ws-a", "--title", "A")
        ws = next(i for i in self.data()["items"] if i not in (REVIEW_ID, MENTION_ID))
        run("link", MENTION_ID, "part_of", ws)
        data = self.data()
        data["items"][MENTION_ID]["links"].append(dict(data["items"][MENTION_ID]["links"][0]))
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        self.assertEqual(run("validate"), ("ledger is valid", 0))

    def test_validate_journal_finds_an_in_place_edit(self) -> None:
        def edit(items: dict[str, Any]) -> None:
            items[REVIEW_ID]["history"][-1]["note"] = "edited after the fact"

        self.edit_in_place(edit)
        output, code = run("validate", "--journal")
        self.assertEqual(code, 1)
        self.assertIn(REVIEW_ID, output)
        self.assertIn("history", output)
        self.assertEqual(run("validate")[1], 0)

    def test_validate_journal_lists_conflicts(self) -> None:
        journal = ledger.open_journal(replica_id(ledger.STORE.state_dir))
        transaction = journal.begin("test")
        transaction.add("item.state", REVIEW_ID, {"from": "requested", "to": "closed"})
        transaction.commit()
        output, code = run("validate", "--journal")
        self.assertEqual(code, 1)
        self.assertIn("conflict", output)
        self.assertIn(REVIEW_ID, output)
        self.assertIn("ahead of ledger.json", output)

    def test_validate_journal_reports_a_damaged_journal(self) -> None:
        path = next(ledger.STORE.journal_dir.glob("*.jsonl"))
        path.write_text(path.read_text() + "{not json}\n{}\n")
        output, code = run("validate", "--journal")
        self.assertEqual(code, 1)
        self.assertIn("damaged", output)

    def test_validate_lists_each_conflict_of_one_change(self) -> None:
        other = json.loads(json.dumps(self.item(REVIEW_ID)))
        other["state"] = "closed"
        other["title"] = "Another title"
        other["links"] = [{"rel": "relates_to", "id": "slack:C9/9"}]
        journal = ledger.open_journal(replica_id(ledger.STORE.state_dir))
        transaction = journal.begin("test")
        transaction.add("item.create", REVIEW_ID, {"item": other})
        transaction.commit()
        found = json.loads(run("validate", "--journal", "--json")[0])
        conflicts = [f for f in found if f["kind"] == "conflict"]
        self.assertEqual([f["detail"].split(":")[0] for f in conflicts], ["state", "link"])
        self.assertEqual(len({f["detail"].rsplit("(change ", 1)[1] for f in conflicts}), 1)
        self.assertEqual({f["item"] for f in conflicts}, {REVIEW_ID})

    def test_validate_checks_every_part_of_link_when_there_are_several(self) -> None:
        data = self.data()
        data["items"][REVIEW_ID]["links"] = [
            {"rel": "part_of", "id": "ws:a"},
            {"rel": "part_of", "id": "ws:b"},
        ]
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        found = json.loads(run("validate", "--json")[0])
        self.assertEqual(sorted(f["kind"] for f in found), ["link-target", "link-target", "part_of"])

    def test_validate_journal_reports_a_journal_behind_the_file(self) -> None:
        data = self.data()
        stem = next(iter(ledger.saved_position(data)))
        data["journal"][stem] += 5
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        found = json.loads(run("validate", "--journal", "--json")[0])
        self.assertEqual([(f["kind"], f["path"]) for f in found], [("position", "journal")])
        self.assertIn("behind", found[0]["detail"])
        self.assertEqual(run("validate", "--journal")[1], 1)

    def test_validate_journal_notes_a_ledger_from_before_journals(self) -> None:
        data = self.data()
        del data["journal"]
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        shutil.rmtree(ledger.STORE.journal_dir)
        self.assertEqual(run("validate", "--journal"), ("no journal yet; the next write starts it", 0))
        output, code = run("validate", "--journal", "--json")
        self.assertEqual(code, 0)
        self.assertEqual([f["kind"] for f in json.loads(output)], ["note"])

    def test_validate_reports_a_link_that_is_not_one_as_damage(self) -> None:
        """A stored link without its relation or other end (written by hand): a finding of its
        own, and the other items' links are still checked (a cycle check walks them all)."""
        run("upsert", MENTION_ID, "--kind", "mention")
        data = self.data()
        data["items"][REVIEW_ID]["links"] = [{"rel": "relates_to"}, "nonsense"]
        data["items"][MENTION_ID]["links"] = [{"rel": "blocked_by", "id": REVIEW_ID, "at": "2026-11-01"}]
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        output, code = run("validate", "--json")
        self.assertEqual(code, 1)
        found = json.loads(output)
        self.assertEqual(
            [(f["kind"], f["item"], f["path"]) for f in found], [("link", REVIEW_ID, "links")] * 2
        )
        self.assertIn("not a link", found[0]["detail"])

    def test_validate_journal_says_a_hand_edit_is_not_in_the_journal_yet(self) -> None:
        data = self.data()
        data["items"][REVIEW_ID]["title"] = "edited by hand"
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        output, code = run("validate", "--journal")
        self.assertEqual(code, 1)
        self.assertIn("changed outside its journal", output)
        self.assertIn("the next write adopts it", output)
        with unittest.mock.patch("sys.stderr", io.StringIO()):
            run("note", REVIEW_ID, "next")
        self.assertEqual(run("validate", "--journal"), ("ledger is valid", 0))

    def test_validate_journal_json_names_the_item_and_path(self) -> None:
        def edit(items: dict[str, Any]) -> None:
            items[REVIEW_ID]["history"][-1]["note"] = "edited after the fact"

        self.edit_in_place(edit)
        found = json.loads(run("validate", "--journal", "--json")[0])
        self.assertEqual([(f["kind"], f["item"]) for f in found], [("differs", REVIEW_ID)])
        self.assertTrue(found[0]["path"].startswith("history"))

    def severities(self, *args: str) -> list[tuple[str, str]]:
        return [(f["kind"], f["severity"]) for f in json.loads(run("validate", *args, "--json")[0])]

    def test_validate_json_says_how_much_each_finding_matters(self) -> None:
        """An error refuses writes or puts data at risk at a rebuild (bosun doctor fails on it); a
        warning is a rule broken or a change the next write takes care of (doctor warns); a note
        is information. The text and the exit code stay as they were."""
        data = self.data()
        data["items"][REVIEW_ID]["links"] = [{"rel": "relates_to", "id": "slack:C9/9", "at": "2026-11-01"}]
        data["items"][REVIEW_ID]["state"] = "nonsense"
        ledger.STORE.ledger_file.write_text(json.dumps(data))  # by hand: adopted by the next write
        self.assertEqual(
            sorted(self.severities("--journal")),
            [("edited", "warning"), ("link-target", "warning"), ("state", "warning")],
        )
        self.assertEqual(run("validate", "--journal")[1], 1)
        path = next(ledger.STORE.journal_dir.glob("*.jsonl"))
        path.write_text(path.read_text() + "nonsense\n")
        self.assertIn(("journal", "error"), self.severities("--journal"))
        data["journal"] = {stem: seq + 5 for stem, seq in ledger.saved_position(data).items()}
        path.write_text(path.read_text().removesuffix("nonsense\n"))
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        self.assertIn(("position", "error"), self.severities("--journal"))

    def test_validate_json_carries_each_findings_line_as_the_text_says_it(self) -> None:
        """bosun doctor prints `line`, so the format is written once (finding_line)."""
        data = self.data()
        data["items"][REVIEW_ID]["links"] = [{"rel": "relates_to", "id": "slack:C9/9", "at": "2026-11-01"}]
        data["items"][REVIEW_ID]["state"] = "nonsense"
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        text, _ = run("validate", "--journal")
        found = json.loads(run("validate", "--journal", "--json")[0])
        self.assertEqual([f["line"] for f in found], text.splitlines())
        self.assertEqual(len(found), 3)
        del data["journal"]
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        shutil.rmtree(ledger.STORE.journal_dir)
        notes = [f for f in json.loads(run("validate", "--journal", "--json")[0]) if f["kind"] == "note"]
        self.assertEqual([f["line"] for f in notes], [validation.NO_JOURNAL_YET])

    def test_validate_json_marks_a_note_as_a_note(self) -> None:
        data = self.data()
        del data["journal"]
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        shutil.rmtree(ledger.STORE.journal_dir)
        self.assertEqual(self.severities("--journal"), [("note", "note")])


class UpdateGateTest(LedgerTestCase):
    """Every write checks the store first (lib/update_gate.py), before anything is journaled."""

    def setUp(self) -> None:
        super().setUp()
        run("upsert", REVIEW_ID, "--kind", "review-request", "--title", "T")

    def files(self) -> dict[str, bytes]:
        """ledger.json, its view and the journal, as they are on disk."""
        state = ledger.STORE.state_dir
        return {
            path.relative_to(state).as_posix(): path.read_bytes()
            for path in sorted(state.rglob("*"))
            if path.is_file() and not path.name.endswith(".lock") and path.name != update_state.STATE_FILE
        }

    def test_a_write_in_a_freeze_no_update_runs_is_cut_short(self) -> None:
        marked = update_state.UpdateState(
            id="u1",
            from_root="/Users/me/.local/share/bosun-versions/aaaaaaa11111",
            to_root="/Users/me/.local/share/bosun-versions/bbbbbbb22222",
            from_commit="aaaaaaa",
            to_commit="bbbbbbb",
            step="marked",
            started_at="2026-11-05T09:00:00+00:00",
            hold_until=10**12,
        )
        update_state.write(ledger.STORE.state_dir, marked)
        before = self.files()
        with self.assertRaises(update_gate.UpdateCutShort):
            run("upsert", MYPR_ID, "--kind", "my-pr", "--title", "T")
        self.assertEqual(self.files(), before)

    def test_a_write_into_a_newer_store_is_refused(self) -> None:
        data = self.data()
        data["version"] = ledger.LEDGER_FORMAT + 1
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        before = self.files()
        with self.assertRaises(update_gate.StoreNewer) as caught:
            run("upsert", MYPR_ID, "--kind", "my-pr", "--title", "T")
        self.assertEqual(caught.exception.found, ledger.LEDGER_FORMAT + 1)
        self.assertEqual(self.files(), before)

    def test_a_newer_store_in_another_shape_is_newer_not_malformed(self) -> None:
        """The format is read before the shape: a newer format may have another one."""
        ledger.STORE.ledger_file.write_text(json.dumps({"version": ledger.LEDGER_FORMAT + 1, "items": []}))
        before = self.files()
        with self.assertRaises(update_gate.StoreNewer):
            run("upsert", MYPR_ID, "--kind", "my-pr", "--title", "T")
        self.assertEqual(self.files(), before)

    def test_a_freeze_comes_before_reading_the_ledger(self) -> None:
        """A freeze cut short at migrated: what the ledger holds, reshaped or not a version at all,
        is not looked at, as the backup is the truth."""
        update_state.write(ledger.STORE.state_dir, self.record("migrated"))
        reshaped: tuple[Any, ...] = (
            {"version": ledger.LEDGER_FORMAT + 1, "items": []},
            {"version": "two", "items": {}},
            [],
        )
        for data in reshaped:
            with self.subTest(data=data):
                ledger.STORE.ledger_file.write_text(json.dumps(data))
                with self.assertRaises(update_gate.UpdateCutShort):
                    run("upsert", MYPR_ID, "--kind", "my-pr", "--title", "T")
        ledger.STORE.ledger_file.write_text("{not json")
        with self.assertRaises(update_gate.UpdateCutShort):
            run("upsert", MYPR_ID, "--kind", "my-pr", "--title", "T")

    def record(self, step: str) -> update_state.UpdateState:
        return update_state.UpdateState(
            id="u1",
            from_root="/Users/me/.local/share/bosun-versions/aaaaaaa11111",
            to_root="/Users/me/.local/share/bosun-versions/bbbbbbb22222",
            from_commit="aaaaaaa",
            to_commit="bbbbbbb",
            step=step,
            started_at="2026-11-05T09:00:00+00:00",
            hold_until=10**12,
        )

    def test_check_writable_writes_nothing(self) -> None:
        before = self.files()
        ledger.check_writable()
        self.assertEqual(self.files(), before)
        update_state.write(ledger.STORE.state_dir, self.record("switched"))
        with self.assertRaises(update_gate.UpdateCutShort):
            ledger.check_writable()

    def test_a_write_into_an_older_store_is_refused(self) -> None:
        before = self.files()
        with (
            unittest.mock.patch.object(ledger, "LEDGER_FORMAT", ledger.LEDGER_FORMAT + 1),
            self.assertRaisesRegex(update_gate.StoreOlder, "run bosun update to migrate it"),
        ):
            run("upsert", MYPR_ID, "--kind", "my-pr", "--title", "T")
        self.assertEqual(self.files(), before)

    def test_a_first_write_makes_its_store_in_this_format(self) -> None:
        with tempfile.TemporaryDirectory() as fresh:
            ledger.configure(fresh)
            with unittest.mock.patch.object(ledger, "LEDGER_FORMAT", ledger.LEDGER_FORMAT + 1):
                run("upsert", MYPR_ID, "--kind", "my-pr", "--title", "T")
            self.assertEqual(
                json.loads(ledger.STORE.ledger_file.read_text())["version"], ledger.LEDGER_FORMAT + 1
            )
            ledger.configure(self.tmp.name)


if __name__ == "__main__":
    unittest.main()
