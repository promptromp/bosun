"""Unit tests for what repo windows do with their items (lib/activity.py through lib/ledger.py):
every report in `reports --since`, what I said in a window (`user-said`, `said`), which item a
window works on (`working`, `idle`, derived from its Claude's status), the duplicate warning of
`spawn`, and the events the ledger queues. Fictional data: acme's invoice exporter."""

from __future__ import annotations

import itertools
import json
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import activity
import event_model
import events
import ledger


PR = "mypr:acme/exporter#102"
MENTION = "slack:C0000000003/1700000065.000065"
OTHER = "slack:C0000000001/1700000032.000032"
WINDOW = "exporter"
THREAD = "https://acme.slack.com/archives/C0000000003/p1700000066000066?thread_ts=1700000065.000065&cid=C0000000003"
TASK = f"task:{date.today().isoformat()}/1"
TASK2 = f"task:{date.today().isoformat()}/2"


def run(*argv: str) -> Any:
    args = ledger.build_parser().parse_args(list(argv))
    return args.func(args)


class ActivityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_dir = Path(tmp.name)
        ledger.configure(tmp.name)
        self.status: dict[str, str] = {}
        patcher = mock.patch.object(ledger, "STATUS_OF", self.status.get)
        patcher.start()
        self.addCleanup(patcher.stop)
        # A clock that ticks a second per write, so the order of activity never rests on a tie.
        ticks = itertools.count()
        start = datetime.now(UTC) - timedelta(hours=1)
        clock = mock.patch.object(
            ledger, "now", lambda: (start + timedelta(seconds=next(ticks))).isoformat(timespec="seconds")
        )
        clock.start()
        self.addCleanup(clock.stop)
        run("upsert", PR, "--kind", "my-pr", "--title", "Exported invoices: show the VAT breakdown")
        run("upsert", MENTION, "--kind", "mention", "--title", "Marco: round currencies per customer")
        run("record-delegation", MENTION, "--window", WINDOW, "--task", "new-work", "--ask", "round them")
        run("record-delegation", PR, "--window", WINDOW, "--task", "fix-my-pr", "--ask", "fix the threads")

    def data(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(ledger.STORE.ledger_file.read_text())
        return value


class ReportsSinceTest(ActivityTestCase):
    def test_every_report_in_the_window_in_order_per_item(self) -> None:
        run("report", PR, "--summary", "marked ready at my request")
        run("report", MENTION, "--summary", "rounding drafted")
        run("report", PR, "--summary", "pushed the fixes", "--open", "decide the footer")
        lines = run("reports", "--since", "2000-01-01T00:00:00Z").splitlines()
        summaries = [line for line in lines if "summary:" in line]
        self.assertEqual(
            summaries,
            [
                "  summary: rounding drafted",
                "  summary: marked ready at my request",
                "  summary: pushed the fixes",
            ],
        )
        self.assertIn("  open:    decide the footer", lines)
        self.assertEqual(
            len([line for line in lines if line.startswith(f"{PR}  [my-pr: awaiting-review]")]), 2
        )

    def test_since_takes_a_clock_time_today(self) -> None:
        moment = ledger.parse_since("10:40")
        local = datetime.now().astimezone()
        self.assertEqual(
            (moment.date(), moment.hour, moment.minute, moment.utcoffset()),
            (local.date(), 10, 40, local.utcoffset()),
        )
        with self.assertRaisesRegex(ledger.LedgerError, "or HH:MM today"):
            run("reports", "--since", "25:00")

    def test_without_since_only_the_latest_report_of_open_items(self) -> None:
        run("report", PR, "--summary", "first")
        run("report", PR, "--summary", "second")
        output = run("reports")
        self.assertIn("summary: second", output)
        self.assertNotIn("summary: first", output)

    def test_what_i_said_is_listed_with_the_reports(self) -> None:
        ledger.add_user_said(WINDOW, "about the template: go with option b")
        output = run("reports", "--since", "2000-01-01T00:00:00Z")
        self.assertIn(f"{PR}  [my-pr: awaiting-review] user said ", output)
        self.assertIn('"about the template: go with option b"', output)
        self.assertIn(f"(the window also held {MENTION})", output)
        self.assertNotIn(f"{MENTION}  [mention", output)  # only under the item it is recorded on
        self.assertEqual(run("reports", "--since", "2999-01-01T00:00:00Z"), "(no reports)")


class UserSaidTest(ActivityTestCase):
    def test_recorded_on_the_item_it_names(self) -> None:
        entry = ledger.add_user_said(WINDOW, f"for {MENTION}: round per invoice")
        self.assertEqual((entry["items"], entry["also_held"]), ([MENTION], [PR]))
        self.assertEqual(self.data()["items"][MENTION]["history"][-1]["event"], "user-said")

    def test_else_on_the_item_whose_title_it_matches(self) -> None:
        entry = ledger.add_user_said(WINDOW, "the VAT breakdown PR: show it per tax rate")
        self.assertEqual(entry["items"], [PR])

    def test_else_on_the_active_item(self) -> None:
        self.assertEqual(ledger.add_user_said(WINDOW, "yes, do that")["items"], [PR])  # delegated last
        run("working", MENTION)
        self.assertEqual(ledger.add_user_said(WINDOW, "yes, do that")["items"], [MENTION])

    def test_a_window_holding_nothing_still_keeps_it(self) -> None:
        entry = ledger.add_user_said("exporter-tax", "drop the footer")
        self.assertEqual(entry["items"], [])
        self.assertIn("(no item held there)", run("said", "--window", "exporter-tax"))

    def test_secrets_are_redacted_and_the_text_capped(self) -> None:
        entry = ledger.add_user_said(
            WINDOW,
            "use token=abcdef123456 " + "x" * 5000,  # gitleaks:allow (a made-up value)
        )
        self.assertIn("token=<redacted>", entry["text"])
        self.assertEqual(len(entry["text"]), activity.MAX_SAID_LENGTH)

    def test_said_reads_by_item_window_and_time(self) -> None:
        ledger.add_user_said(WINDOW, "the template PR: keep every row\nand the bold header")
        self.assertIn("  and the bold header", run("said", PR))
        self.assertIn("(the window also held", run("said", MENTION))  # it held the item: still found
        self.assertEqual(run("said", OTHER), "(nothing I said in a repo window matches)")
        self.assertEqual(
            run("said", "--since", "2999-01-01T00:00:00Z"), "(nothing I said in a repo window matches)"
        )
        shown = json.loads(run("show", PR))
        self.assertEqual(shown["derived"]["user_said"][0]["window"], WINDOW)

    def test_prune_drops_old_entries(self) -> None:
        ledger.add_user_said(WINDOW, "old")
        data = self.data()
        data["said"][0]["at"] = "2000-01-01T00:00:00+00:00"
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        run("prune", "--days", "1")
        self.assertEqual(self.data()["said"], [])


class BosunPromptsTest(unittest.TestCase):
    def test_only_what_i_typed_counts(self) -> None:
        pasted = {activity.prompt_hash("Please look at the footer\n")}
        self.assertTrue(activity.is_mine("go with b"))
        for text in (
            "/compact",
            "Ledger item mypr:acme/exporter#102: more",
            "Update to ledger item x (the task)",
            "Run this task in a subagent: ...",
            "<cross-session-message from='peer'>do it</cross-session-message>",
            "<task-notification>done</task-notification>",
            "Please  look at the footer",
            "   ",
        ):
            self.assertFalse(activity.is_mine(text, pasted), text)
        self.assertFalse(activity.is_mine("Bosun resumed this session.", (), "Bosun resumed  this session."))


class WorkingTest(ActivityTestCase):
    def marker(self, item_id: str) -> str:
        listing: str = run("list")
        return next(line for line in listing.splitlines() if line.startswith(item_id))

    def test_working_while_the_window_is_mid_turn_on_its_active_item(self) -> None:
        self.assertFalse(json.loads(run("delegation", PR))["working"])
        self.status[WINDOW] = "busy"
        self.assertTrue(json.loads(run("delegation", PR))["working"])
        self.assertFalse(json.loads(run("delegation", MENTION))["working"])  # not its active item
        self.assertIn("(awaiting report, working)", self.marker(PR))

    def test_a_report_does_not_free_a_busy_window(self) -> None:
        run("report", PR, "--summary", "marked ready")
        self.status[WINDOW] = "busy"
        delegation = json.loads(run("delegation", PR))
        self.assertEqual((delegation["awaiting_report"], delegation["working"]), (False, True))
        self.assertIn("(reported, working)", self.marker(PR))
        self.status[WINDOW] = "idle"
        self.assertIn("@exporter (reported) -", self.marker(PR))

    def test_an_explicit_mark_holds_while_idle_until_idle_or_more_activity(self) -> None:
        self.assertIn("ok: exporter is working on", run("working", MENTION))
        self.assertTrue(json.loads(run("delegation", MENTION))["working"])
        run("idle", MENTION)
        self.assertFalse(json.loads(run("delegation", MENTION))["working"])
        self.status[WINDOW] = "busy"
        self.assertTrue(json.loads(run("delegation", PR))["working"])  # PR is active again

    def test_working_takes_an_item_no_window_holds(self) -> None:
        run("upsert", OTHER, "--kind", "mention", "--title", "Priya: backfill scripts")
        with self.assertRaisesRegex(ledger.LedgerError, "give the window"):
            run("working", OTHER)
        self.assertIn("now holds it", run("working", OTHER, "--window", "exporter-tax"))
        self.assertEqual(json.loads(run("delegation", OTHER))["task"], ledger.DIRECT_TASK)
        with self.assertRaisesRegex(ledger.LedgerError, "held by exporter-tax"):
            run("working", OTHER, "--window", WINDOW)

    def test_a_finished_item_is_not_worked_on(self) -> None:
        run("transition", MENTION, "done", "--note", "answered")
        with self.assertRaisesRegex(ledger.LedgerError, "a finished item is not worked on"):
            run("working", MENTION)


class SpawnDuplicateTest(ActivityTestCase):
    def test_a_task_from_the_same_source_or_thread_warns_naming_it(self) -> None:
        run("spawn", MENTION, "--title", "Round currencies per customer", "--ask", "add rounding rules")
        same_source = run(
            "spawn", MENTION, "--title", "Export credit notes as CSV", "--ask", "add credit notes"
        )
        self.assertIn(
            f"warning: a task already comes from the same source or thread: {TASK} (proposed", same_source
        )
        run("upsert", OTHER, "--kind", "mention", "--title", "Priya: unrelated")
        same_thread = run(
            "spawn", OTHER, "--title", "Rotate the export key", "--ask", "rotate it", "--via", THREAD
        )
        self.assertIn(TASK, same_thread)  # its via is in the thread the mention started
        self.assertIn(TASK2, same_thread)

    def test_a_recently_closed_one_counts_and_an_old_one_does_not(self) -> None:
        run("spawn", "--title", "Rotate the export key", "--ask", "rotate it", "--via", THREAD)
        run("transition", TASK, "dropped", "--note", "not needed")
        self.assertIn("warning", run("spawn", "--title", "Something else", "--ask", "x", "--via", THREAD))
        data = self.data()
        data["items"][TASK]["updated_at"] = "2000-01-01T00:00:00+00:00"
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        output = run("spawn", "--title", "A third thing", "--ask", "y", "--via", THREAD)
        self.assertIn(TASK2, output)
        self.assertNotIn(f"{TASK} (", output)

    def test_unrelated_sources_and_free_text_do_not_warn(self) -> None:
        run("spawn", "--title", "Rotate the export key", "--ask", "rotate it", "--via", "user: do it")
        self.assertNotIn("warning", run("spawn", "--title", "Other", "--ask", "x", "--via", "user: do it"))
        self.assertNotIn("warning", run("spawn", PR, "--title", "Fix the suites", "--ask", "y"))


class EventsQueuedTest(ActivityTestCase):
    def queue(self) -> list[event_model.Event]:
        return events.read_queue(events.events_dir(self.state_dir))

    def test_nothing_is_queued_while_events_are_off(self) -> None:
        run("report", PR, "--summary", "pushed")
        self.assertEqual(self.queue(), [])

    def test_reports_said_outreach_and_tasks_are_queued_with_their_origin(self) -> None:
        ledger.configure(self.state_dir, events_enabled=True, caller_window=WINDOW)
        run("report", PR, "--summary", "pushed", "--open", "decide the footer")
        ledger.add_user_said(WINDOW, "the template PR: go with b")
        run("reach-out", PR, "--to", "Dana (product)", "--subject", "Keep the footer?")
        run("spawn", PR, "--title", "Fix the suites", "--ask", "same fix there")
        queued = self.queue()
        self.assertEqual([e.type for e in queued], ["report", "user-said", "reach-out", "spawn"])
        self.assertEqual({e.origin for e in queued}, {WINDOW})
        self.assertEqual((queued[0].summary, queued[0].detail), ("pushed", "open: decide the footer"))
        self.assertEqual(queued[1].summary, "the template PR: go with b")
        self.assertEqual(queued[3].detail, "ask: same fix there")
        self.assertEqual((queued[0].item, queued[0].window), (PR, WINDOW))

    def test_a_queue_that_cannot_be_written_never_fails_the_command(self) -> None:
        ledger.configure(self.state_dir, events_enabled=True)
        with mock.patch.object(events, "append", side_effect=OSError("disk full")):
            self.assertIn("ok: report recorded", run("report", PR, "--summary", "pushed"))


if __name__ == "__main__":
    unittest.main()


class InFlightTest(ActivityTestCase):
    def test_hand_overs_awaiting_a_report_are_in_flight(self) -> None:
        self.assertEqual(run("in-flight", WINDOW).splitlines(), sorted([MENTION, PR]))
        self.assertEqual(run("in-flight", "other-window"), "")

    def test_a_reported_hand_over_is_not_in_flight_unless_the_window_works_on_it(self) -> None:
        run("report", MENTION, "--summary", "rounded per customer")
        run("report", PR, "--summary", "threads fixed")
        self.assertEqual(run("in-flight", WINDOW), "")
        items = self.data()["items"]
        with mock.patch.object(ledger, "is_working", lambda item, _: item["id"] == PR):
            self.assertEqual([i["id"] for i in ledger.in_flight(items, WINDOW)], [PR])
