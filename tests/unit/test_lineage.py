"""Unit tests for tasks and typed links (lib/lineage.py, through lib/ledger.py's commands): the
task state machine and its approval rule, spawn (provenance, duplicates, --blocks and the blocker
guard), link validation (unknown ids, self-links, cycles, tickets), inverse links, drop notes,
folding one item into another, the lineage tree and list hints, report hints for produced PRs,
and the one-shot migration of ad hoc provenance. Fictional data: acme's invoice exporter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import ledger
import lineage


PR = "mypr:acme/exporter#102"
NEW_PR = "mypr:acme/pdf-renderer#7"
OUT = f"out:{PR}/1"
MENTION = "slack:C0000000001/1700000001.000001"
REPLY = "https://acme.slack.com/archives/C0000000003/p1700000042000042?thread_ts=1700000041.000041&cid=C0000000003"
PR_URL = "https://github.com/acme/pdf-renderer/pull/7"
TODAY = date.today().isoformat()
TASK = f"task:{TODAY}/1"
TASK2 = f"task:{TODAY}/2"
TASK3 = f"task:{TODAY}/3"


def run(*argv: str) -> Any:
    args = ledger.build_parser().parse_args(list(argv))
    return args.func(args)


class LineageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ledger.configure(self.tmp.name)
        run("upsert", PR, "--kind", "my-pr", "--title", "VAT breakdown")
        run("upsert", MENTION, "--kind", "mention", "--title", "Priya: could you also export credit notes?")
        run("reach-out", PR, "--to", "Marco Rossi", "--subject", "Fix the PDF renderer too?")
        run("sent", OUT, "--ref", "https://acme.slack.com/archives/C0000000003/p1700000041000041")
        run("replied", OUT, "--evidence", REPLY, "--summary", "yes please fix the PDF renderer")

    def item(self, item_id: str) -> dict[str, Any]:
        item: dict[str, Any] = json.loads(ledger.STORE.ledger_file.read_text())["items"][item_id]
        return item

    def spawn(
        self, *extra: str, source: str = OUT, title: str = "Fix the rounding bug in the PDF renderer"
    ) -> str:
        output: str = run("spawn", source, "--title", title, "--ask", "apply the rounding fix there", *extra)
        return output

    def accept(self, task: str = TASK) -> str:
        output: str = run("transition", task, "accepted", "--note", "go", "--evidence", "user: yes, do it")
        return output


class TaskStatesTest(LineageTestCase):
    def test_spawn_records_a_proposed_task_with_its_provenance(self) -> None:
        output = self.spawn("--via", REPLY, "--requester", "Marco Rossi", "--repo", "acme/pdf-renderer")
        self.assertEqual(output.splitlines()[0], TASK)
        task = self.item(TASK)
        self.assertEqual((task["kind"], task["state"]), ("task", "proposed"))
        self.assertEqual(task["fields"]["requester"], "Marco Rossi")
        self.assertEqual(
            task["links"][0] | {"at": ""}, {"rel": "spawned_from", "id": OUT, "via": REPLY, "at": ""}
        )
        self.assertIn(f"{TASK}: Fix the rounding bug", self.item(OUT)["history"][-1]["note"])
        self.assertEqual(self.item(OUT)["history"][-1]["event"], "spawned")
        self.assertIn("propose", output)

    def test_only_my_word_accepts_a_task(self) -> None:
        self.spawn()
        with self.assertRaisesRegex(ledger.LedgerError, "needs --evidence"):
            run("transition", TASK, "accepted", "--note", "go")
        with self.assertRaisesRegex(ledger.LedgerError, "never accepts work"):
            run("transition", TASK, "accepted", "--note", "go", "--evidence", REPLY)
        self.assertIn("proposed -> accepted", self.accept())

    def test_accepting_carries_out_the_pending_proposal(self) -> None:
        self.spawn()
        run("propose", TASK, f"delegate new-work --id '{TASK}' --window exporter")
        self.accept()
        task = self.item(TASK)
        self.assertIsNone(task["proposal"])
        self.assertIn("proposal-done", [h["event"] for h in task["history"]])

    def test_declining_rejects_the_proposal(self) -> None:
        self.spawn()
        run("propose", TASK, "accept and delegate it")
        run("transition", TASK, "declined", "--note", "not now")
        self.assertIn("proposal-rejected", [h["event"] for h in self.item(TASK)["history"]])

    def test_record_delegation_moves_an_accepted_task_on_and_refuses_a_proposed_one(self) -> None:
        self.spawn()
        with self.assertRaisesRegex(ledger.LedgerError, "only an accepted task is handed over"):
            run("record-delegation", TASK, "--window", "exporter", "--task", "new-work")
        self.accept()
        output = run("record-delegation", TASK, "--window", "exporter", "--task", "new-work")
        self.assertIn("accepted -> delegated", output)
        self.assertTrue(ledger.awaiting_report(self.item(TASK)))
        run("report", TASK, "--summary", "draft PR open", "--result", PR_URL)
        self.assertFalse(ledger.awaiting_report(self.item(TASK)))

    def test_outreach_is_never_delegated_as_the_work(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "is outreach, not a work item"):
            run("record-delegation", OUT, "--window", "exporter", "--task", "new-work")

    def test_task_ids_have_a_date_and_number(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "task ids look like"):
            run("upsert", "task:fix-it", "--kind", "task")


class SpawnTest(LineageTestCase):
    def test_a_repeat_is_unchanged_and_a_similar_title_warns(self) -> None:
        self.spawn()
        repeat = self.spawn()
        self.assertTrue(repeat.startswith(f"{TASK}\nunchanged"))
        similar = self.spawn(title="Fix the rounding bug in the PDF renderer, now")
        self.assertIn(f"warning: similar open task(s) from the same source: {TASK}", similar)
        other_source = self.spawn(source=MENTION)
        self.assertNotIn("warning", other_source)

    def test_without_a_source_it_needs_where_it_came_from(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "needs --via"):
            run("spawn", "--title", "Rotate the export key", "--ask", "rotate it")
        output = run(
            "spawn", "--title", "Rotate the export key", "--ask", "rotate it", "--via", "user: do it"
        )
        self.assertIn("my instruction is the approval", output)
        self.assertEqual(self.item(TASK)["fields"]["via"], "user: do it")

    def test_blocks_keeps_the_source_open_until_the_task_is_finished(self) -> None:
        self.spawn("--blocks", source=MENTION)
        self.assertEqual(self.item(MENTION)["links"][0]["rel"], "blocked_by")
        self.assertIn(f"blocked by {TASK} (proposed)", run("list", "--open"))
        with self.assertRaisesRegex(ledger.LedgerError, f"is blocked by: {TASK}"):
            run("transition", MENTION, "done", "--note", "answered")
        forced = run("transition", MENTION, "done", "--note", "answered", "--force-blocked")
        self.assertIn("new -> done", forced)
        self.assertIn(f"finished while waiting on: {TASK}", self.item(MENTION)["history"][-1]["note"])

    def test_a_spawned_task_does_not_block_its_source_by_default(self) -> None:
        self.spawn(source=MENTION)
        self.assertIn("new -> done", run("transition", MENTION, "done", "--note", "carried by the task"))

    def test_a_finished_blocker_no_longer_blocks(self) -> None:
        self.spawn("--blocks", source=MENTION)
        run("transition", TASK, "declined", "--note", "no")
        self.assertIn("new -> done", run("transition", MENTION, "done", "--note", "declined"))

    def test_a_merged_pr_is_a_fact_even_while_blocked(self) -> None:
        self.spawn("--blocks", source=PR)
        run("transition", OUT, "applied", "--note", f"carried by {TASK}")
        self.assertIn("-> merged", run("transition", PR, "merged", "--note", "merged"))


class LinkTest(LineageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.spawn()
        run("upsert", NEW_PR, "--kind", "my-pr", "--title", "PDF renderer: rounding fix")

    def test_link_validates_relation_ids_and_self_links(self) -> None:
        with self.assertRaisesRegex(lineage.LineageError, "unknown relation 'produced_by'.*swap the ids"):
            run("link", NEW_PR, "produced_by", TASK)
        with self.assertRaisesRegex(lineage.LineageError, "no item 'mypr:acme/nope#1'"):
            run("link", TASK, "produces", "mypr:acme/nope#1")
        with self.assertRaisesRegex(lineage.LineageError, "cannot be linked to itself"):
            run("link", TASK, "relates_to", TASK)
        self.assertEqual(run("link", TASK, "produces", NEW_PR), f"ok: {TASK} produces {NEW_PR}")
        self.assertTrue(run("link", TASK, "produces", NEW_PR).startswith("unchanged"))

    def test_cycles_are_refused_across_spawned_from_and_produces(self) -> None:
        run("link", TASK, "produces", NEW_PR)
        for rel, a, b in (
            ("produces", NEW_PR, TASK),
            ("spawned_from", OUT, NEW_PR),
            ("produces", NEW_PR, PR),
        ):
            with self.subTest(rel=rel, a=a, b=b), self.assertRaisesRegex(lineage.LineageError, "cycle"):
                run("link", a, rel, b)
        self.assertIn("ok", run("link", NEW_PR, "relates_to", PR))

    def test_blocked_by_cycles_are_refused_too(self) -> None:
        run("link", PR, "blocked_by", NEW_PR)
        with self.assertRaisesRegex(lineage.LineageError, "cycle"):
            run("link", NEW_PR, "blocked_by", PR)

    def test_inverse_links_are_computed_on_read(self) -> None:
        run("link", TASK, "produces", NEW_PR, "--via", "report")
        shown = json.loads(run("show", NEW_PR))
        self.assertEqual(shown["derived"]["linked_from"][0]["rel"], "produced_by")
        self.assertNotIn("links", shown)
        parent = json.loads(run("show", PR))
        self.assertEqual(
            parent["derived"]["computed_links"][0],
            {"rel": "blocked_by", "id": OUT}
            | {
                "at": parent["derived"]["computed_links"][0]["at"],
                "computed": "outreach parent",
            },
        )

    def test_unlink_removes_a_stored_link_but_not_an_outreach_parent(self) -> None:
        run("link", TASK, "relates_to", NEW_PR)
        self.assertIn("no longer relates_to", run("unlink", TASK, "relates_to", NEW_PR))
        self.assertTrue(run("unlink", TASK, "relates_to", NEW_PR).startswith("unchanged"))
        with self.assertRaisesRegex(ledger.LedgerError, "its parent field"):
            run("unlink", PR, "blocked_by", OUT)

    def test_tracks_creates_the_ticket_and_takes_only_tickets(self) -> None:
        output = run("link", PR, "tracks", "jira:EXP-123")
        self.assertIn("created jira:EXP-123 [ticket: unsynced]", output)
        self.assertEqual(self.item("jira:EXP-123")["fields"]["project"], "EXP")
        with self.assertRaisesRegex(lineage.LineageError, "tracks takes a ticket id"):
            run("link", PR, "tracks", NEW_PR)
        with self.assertRaisesRegex(lineage.LineageError, "relates_to|link from the work item"):
            run("link", "jira:EXP-123", "produces", NEW_PR)

    def test_drop_notes_record_folded_into_or_duplicate_of(self) -> None:
        self.spawn(source=MENTION, title="Export credit notes")
        self.spawn(source=MENTION, title="Credit notes in the totals row")
        for task, note, rel in (
            (TASK2, f"merged into {TASK}", "folded_into"),
            (TASK3, f"folded into '{TASK}'.", "folded_into"),
        ):
            with self.subTest(note=note):
                self.assertIn(
                    f"linked {task} {rel} {TASK}", run("transition", task, "dropped", "--note", note)
                )
                self.assertEqual(self.item(task)["links"][-1]["rel"], rel)
        run("upsert", "slack:C0000000001/1700000002.000002", "--kind", "mention")
        output = run(
            "transition",
            "slack:C0000000001/1700000002.000002",
            "dismissed",
            "--note",
            f"duplicate of {MENTION}",
        )
        self.assertIn(f"duplicate_of {MENTION}", output)
        self.assertIn(
            "note: out:x/9 is not a ledger item, so no folded_into link was recorded",
            run("transition", OUT, "dropped", "--note", "merged into out:x/9"),
        )

    def test_folded_into_shares_the_cycle_family_of_duplicate_of(self) -> None:
        self.assertEqual(lineage.RELATIONS["folded_into"].inverse, "absorbs")
        run("link", TASK, "folded_into", NEW_PR)
        with self.assertRaisesRegex(lineage.LineageError, "cycle of duplicate links"):
            run("link", NEW_PR, "duplicate_of", TASK)
        with self.assertRaisesRegex(lineage.LineageError, "cycle of duplicate links"):
            run("link", NEW_PR, "folded_into", TASK)


class FoldTest(LineageTestCase):
    """ledger fold: TASK2 (credit notes) goes on as part of TASK, which is with window exporter."""

    def setUp(self) -> None:
        super().setUp()
        self.spawn()
        self.accept()
        run("record-delegation", TASK, "--window", "exporter", "--task", "new-work", "--ask", "fix it")
        self.spawn(source=MENTION, title="Export credit notes")

    def fold(self, *extra: str, item: str = TASK2, into: str = TASK) -> str:
        output: str = run("fold", item, "--into", into, *extra)
        return output

    def test_fold_links_withdraws_the_proposal_and_closes_the_item(self) -> None:
        run("propose", TASK2, f"accept it and bosun delegate new-work --id '{TASK2}'")
        output = self.fold("--note", "one PR for both")
        self.assertTrue(output.startswith(f"ok: {TASK2} folded_into {TASK}; {TASK2} proposed -> dropped"))
        self.assertIn(f"proposal withdrawn: accept it and bosun delegate new-work --id '{TASK2}'", output)
        task = self.item(TASK2)
        self.assertEqual(task["state"], "dropped")
        self.assertIsNone(task["proposal"])
        self.assertEqual(task["links"][-1] | {"at": ""}, {"rel": "folded_into", "id": TASK, "at": ""})
        events = {h["event"]: h for h in task["history"]}
        self.assertEqual(events["proposal-withdrawn"]["note"], f"folded into {TASK}: one PR for both")
        self.assertEqual(events["transition"]["note"], f"folded into {TASK}: one PR for both")
        self.assertEqual(self.item(TASK)["state"], "delegated")

    def test_a_target_with_a_window_gets_the_update_command_printed_not_run(self) -> None:
        output = self.fold()
        self.assertIn(
            f"next: {TASK} is with window exporter; carry the work there: bosun delegate --update "
            f"'{TASK}' --details 'Also carry {TASK2} here (folded into {TASK}): Export credit notes. "
            "Ask: apply the rounding fix there'",
            output,
        )
        self.assertEqual(self.item(TASK)["delegation"]["updates"], 0)
        run("upsert", NEW_PR, "--kind", "my-pr")
        self.spawn(source=MENTION, title="Credit notes in the totals row")
        self.assertNotIn("next:", self.fold(item=TASK3, into=NEW_PR))

    def test_a_folded_item_held_by_another_window_is_told_to_stop(self) -> None:
        self.accept(TASK2)
        run("record-delegation", TASK2, "--window", "suites", "--task", "new-work")
        output = self.fold()
        self.assertIn(f"{TASK2} delegated -> dropped", output)
        self.assertIn(f"note: {TASK2} was handed to window suites; if it still works on it", output)
        self.assertIn(f"bosun send --id '{TASK2}' suites", output)

    def test_fold_is_idempotent(self) -> None:
        self.fold()
        history = self.item(TASK2)["history"]
        self.assertEqual(self.fold(), f"unchanged: {TASK2} already folded into {TASK} (dropped)")
        self.assertEqual(self.item(TASK2)["history"], history)

    def test_fold_closes_each_kind_in_its_escape_state(self) -> None:
        run("reach-out", PR, "--to", "Marco Rossi", "--subject", "And the header row?")
        self.assertIn(f"out:{PR}/2 draft -> dropped", self.fold(item=f"out:{PR}/2", into=OUT))
        self.assertIn(f"{MENTION} new -> dismissed", self.fold(item=MENTION))

    def test_refusals(self) -> None:
        run("upsert", NEW_PR, "--kind", "my-pr")
        run("transition", NEW_PR, "merged", "--note", "merged")
        run("upsert", f"learn:{TODAY}/1", "--kind", "learning")
        run("link", PR, "tracks", "jira:EXP-123")
        for kwargs, error in (
            ({"into": TASK2}, "cannot be folded into itself"),
            ({"into": "task:2000-01-01/1"}, "no item 'task:2000-01-01/1'"),
            ({"item": f"learn:{TODAY}/1"}, "close it with bosun learn reject"),
            ({"item": "jira:EXP-123"}, "mirrors its tracker"),
            ({"into": "jira:EXP-123"}, "folded_into takes a work item"),
            (
                {"into": NEW_PR},
                f"{NEW_PR} is merged, so nothing carries {TASK2}'s work on.*--force-closed-target",
            ),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(Exception, error):
                self.fold(**kwargs)
        self.assertEqual(self.item(TASK2)["state"], "proposed")
        self.assertIn("proposed -> dropped", self.fold("--force-closed-target", into=NEW_PR))
        self.spawn(source=MENTION, title="Credit notes in the totals row")
        run("transition", TASK3, "declined", "--note", "no")
        with self.assertRaisesRegex(ledger.LedgerError, f"already declined.*link '{TASK3}' folded_into"):
            self.fold(item=TASK3)

    def test_open_outreach_and_blockers_refuse_it_as_transition_does(self) -> None:
        run("reach-out", TASK2, "--to", "Priya", "--subject", "Credit notes per customer?")
        with self.assertRaisesRegex(ledger.LedgerError, f"{TASK2} is waiting on: out:{TASK2}/1"):
            self.fold()
        output = self.fold("--force-open-outreach")
        self.assertIn("proposed -> dropped", output)
        self.assertIn(f"[finished while waiting on: out:{TASK2}/1]", self.item(TASK2)["history"][-1]["note"])

    def test_list_tree_and_show_name_both_ends(self) -> None:
        self.fold()
        rows = {row.split()[0]: row for row in run("list").splitlines()}
        self.assertIn(f"| folded into {TASK}", rows[TASK2])
        self.assertIn(f"| absorbs {TASK2}", rows[TASK])
        self.assertIn(f"folded into {TASK} [delegated]", run("tree", TASK2))
        self.assertIn(f"absorbs {TASK2} [dropped]", run("tree", TASK))
        linked_from = json.loads(run("show", TASK))["derived"]["linked_from"]
        self.assertIn(
            {"rel": "absorbs", "id": TASK2}, [{"rel": e["rel"], "id": e["id"]} for e in linked_from]
        )

    def test_prune_keeps_a_folded_item_while_its_target_is_open(self) -> None:
        self.fold()
        run("transition", MENTION, "done", "--note", "answered")
        # The mention it came from goes; the folded task stays with its open target.
        self.assertIn("pruned 1 item(s)", run("prune", "--days", "0"))
        self.assertEqual(self.item(TASK2)["state"], "dropped")
        run("transition", TASK, "done", "--note", "landed")
        # TASK stays with its open source (the outreach's PR); the folded task goes.
        self.assertIn("pruned 1 item(s)", run("prune", "--days", "0"))
        items = json.loads(ledger.STORE.ledger_file.read_text())["items"]
        self.assertNotIn(TASK2, items)
        self.assertIn(TASK, items)


class TreeAndListTest(LineageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.spawn("--via", REPLY)
        self.accept()
        run("record-delegation", TASK, "--window", "exporter", "--task", "new-work")
        run("report", TASK, "--summary", "draft PR open", "--result", PR_URL)

    def test_reports_name_the_commands_that_link_a_new_pr(self) -> None:
        reports = run("reports")
        self.assertIn(f"bosun ledger upsert '{NEW_PR}' --kind my-pr --url '{PR_URL}'", reports)
        self.assertIn(f"bosun ledger link '{TASK}' produces '{NEW_PR}'", reports)
        run("upsert", NEW_PR, "--kind", "my-pr", "--url", PR_URL)
        run("link", TASK, "produces", NEW_PR)
        self.assertNotIn("next:", run("reports"))

    def test_a_recorded_pr_is_linked_not_recreated(self) -> None:
        run("upsert", NEW_PR, "--kind", "my-pr", "--url", PR_URL)
        reports = run("reports")
        self.assertIn(f"next:    bosun ledger link '{TASK}' produces '{NEW_PR}'", reports)
        self.assertNotIn("ledger upsert", reports)

    def test_a_pr_spawned_from_the_item_needs_no_link(self) -> None:
        run("upsert", NEW_PR, "--kind", "my-pr", "--url", PR_URL)
        run("link", NEW_PR, "spawned_from", TASK)
        self.assertNotIn("next:", run("reports"))

    def test_a_finished_report_with_nothing_produced_names_the_close(self) -> None:
        run("report", TASK, "--summary", "investigated: no change needed")
        close = f"bosun ledger transition '{TASK}' done --note 'report "
        self.assertIn(close, run("reports"))
        self.assertIn(close, run("reports", "--since", "2000-01-01T00:00:00+00:00"))
        run("report", TASK, "--summary", "half done", "--open", "waiting on the product decision")
        self.assertNotIn("transition", run("reports"))

    def test_a_task_that_produced_a_pr_stays_open_until_it_merges(self) -> None:
        run("upsert", NEW_PR, "--kind", "my-pr", "--url", PR_URL)
        run("link", TASK, "produces", NEW_PR)
        run("report", TASK, "--summary", "follow-up pushed to the same PR")
        self.assertNotIn(f"transition '{TASK}' done", run("reports"))

    def test_tree_shows_the_chain_with_states(self) -> None:
        run("upsert", NEW_PR, "--kind", "my-pr", "--title", "PDF renderer: rounding fix")
        run("link", TASK, "produces", NEW_PR)
        tree = run("tree", NEW_PR).splitlines()
        self.assertEqual(tree[0], f"- {PR} [my-pr: awaiting-review] VAT breakdown")
        self.assertTrue(tree[1].startswith(f"  - {OUT} [outreach: replied]"))
        self.assertTrue(tree[2].startswith(f"    - {TASK} [task: delegated @exporter (reported)]"))
        self.assertTrue(tree[2].endswith("(spawned)"))
        self.assertEqual(
            tree[3], f"      * {NEW_PR} [my-pr: awaiting-review] PDF renderer: rounding fix (produces)"
        )

    def test_list_shows_short_lineage_hints(self) -> None:
        run("upsert", NEW_PR, "--kind", "my-pr")
        run("link", TASK, "produces", NEW_PR)
        rows = {row.split()[0]: row for row in run("list").splitlines()}
        self.assertIn(f"| from {OUT} | -> {NEW_PR}", rows[TASK])
        self.assertIn(f"| from {TASK}", rows[NEW_PR])

    def test_a_task_whose_pr_merged_is_proposed_done(self) -> None:
        run("upsert", NEW_PR, "--kind", "my-pr")
        run("link", TASK, "produces", NEW_PR)
        run("transition", NEW_PR, "merged", "--note", "merged")
        self.assertIn(f"all {TASK} produced has landed ({NEW_PR}): transition it to done", run("tree", TASK))


class AppliedHintTest(LineageTestCase):
    def test_closing_a_reply_without_a_task_says_to_record_the_work(self) -> None:
        run("propose", OUT, f"bosun delegate new-work --id '{OUT}' --window exporter")
        output = run("transition", OUT, "applied", "--note", "session opened a draft PR")
        self.assertIn("a delegate command was proposed for its reply", output)
        self.assertIn(f"bosun ledger spawn '{OUT}'", output)

    def test_no_hint_once_a_task_carries_it(self) -> None:
        self.spawn()
        self.assertNotIn("next:", run("transition", OUT, "applied", "--note", f"carried by {TASK}"))


class MigrateTest(LineageTestCase):
    def setUp(self) -> None:
        super().setUp()
        run("upsert", NEW_PR, "--kind", "my-pr", "--field", f"origin={MENTION}")
        run(
            "upsert",
            "mypr:acme/exporter#103",
            "--kind",
            "my-pr",
            "--field",
            f"origin=Priya's ask ({MENTION})",
        )
        run("upsert", "mypr:acme/exporter#104", "--kind", "my-pr", "--field", "origin=a call with Dana")
        run("reach-out", PR, "--to", "Marco", "--subject", "And the header row?")
        ledger.cmd_transition(
            ledger.build_parser().parse_args(
                ["transition", f"out:{PR}/2", "dropped", "--note", "merged into nothing yet"]
            )
        )
        # An older drop, from before drops recorded links: only the note names the item.
        data = json.loads(ledger.STORE.ledger_file.read_text())
        data["items"][f"out:{PR}/2"]["history"][-1]["note"] = f"merged into {OUT}"
        ledger.STORE.ledger_file.write_text(json.dumps(data))

    def test_dry_run_changes_nothing(self) -> None:
        before = ledger.STORE.ledger_file.read_text()
        output = run("migrate-links", "--dry-run")
        self.assertIn("dry run: nothing written", output)
        self.assertEqual(ledger.STORE.ledger_file.read_text(), before)

    def test_origins_and_merged_notes_become_links_once(self) -> None:
        output = run("migrate-links")
        self.assertIn(f"{NEW_PR}: origin -> spawned_from {MENTION}", output)
        self.assertIn(f"mypr:acme/exporter#103: origin -> spawned_from {MENTION}", output)
        self.assertIn(
            "left: mypr:acme/exporter#104 origin 'a call with Dana' names no single ledger item", output
        )
        self.assertIn(f"out:{PR}/2: 'merged into' note -> folded_into {OUT}", output)
        self.assertNotIn("origin", self.item(NEW_PR)["fields"])
        self.assertEqual(self.item("mypr:acme/exporter#103")["links"][0]["via"], f"Priya's ask ({MENTION})")
        again = run("migrate-links")
        self.assertNotIn(NEW_PR, again)
        self.assertNotIn("folded_into", again)
        self.assertTrue(ledger.STORE.backup.exists())

    def test_a_duplicate_of_recorded_from_a_merged_into_note_becomes_folded_into(self) -> None:
        self.spawn(source=MENTION, title="Export credit notes")
        self.spawn(source=MENTION, title="Credit notes in the totals row")
        self.spawn(source=MENTION, title="Credit notes in the footer")
        for task in (TASK2, TASK3):
            run("transition", task, "dropped", "--note", f"duplicate of {TASK}")
        # TASK2 was dropped 'merged into' before such notes meant folded_into; TASK3 is a duplicate.
        data = json.loads(ledger.STORE.ledger_file.read_text())
        transition = next(h for h in data["items"][TASK2]["history"] if h["event"] == "transition")
        transition["note"] = f"merged into {TASK}"
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        before = ledger.STORE.ledger_file.read_text()
        self.assertIn(
            f"{TASK2}: 'merged into' note: duplicate_of {TASK} -> folded_into {TASK}",
            run("migrate-links", "--dry-run"),
        )
        self.assertEqual(ledger.STORE.ledger_file.read_text(), before)
        output = run("migrate-links")
        self.assertIn(f"{TASK2}: 'merged into' note: duplicate_of {TASK} -> folded_into {TASK}", output)
        self.assertNotIn(TASK3, output)
        self.assertEqual(
            [(e["rel"], e["id"]) for e in self.item(TASK2)["links"]][-1:], [("folded_into", TASK)]
        )
        self.assertNotIn("duplicate_of", [e["rel"] for e in self.item(TASK2)["links"]])
        self.assertEqual(self.item(TASK3)["links"][-1]["rel"], "duplicate_of")
        self.assertEqual(self.item(TASK2)["history"][-1]["event"], "links-migrated")
        self.assertNotIn(TASK2, run("migrate-links"))


class PruneTest(LineageTestCase):
    def test_prune_keeps_finished_items_linked_to_open_work(self) -> None:
        self.spawn()
        run("transition", OUT, "applied", "--note", f"carried by {TASK}")
        self.assertIn("pruned 0 item(s)", run("prune", "--days", "0"))
        run("transition", TASK, "declined", "--note", "no")
        # The task goes; the outreach stays with its open parent (the PR).
        self.assertIn("pruned 1 item(s)", run("prune", "--days", "0"))
        self.assertEqual(self.item(OUT)["state"], "applied")


if __name__ == "__main__":
    unittest.main()
