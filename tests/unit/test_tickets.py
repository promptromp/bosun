"""Unit tests for external tickets: ids per tracker (lib/tickets.py), the status mapping with the
site's overrides, `ledger ticket-sync` (a read-only mirror: history only on change, synced_at
stamped), refusing bosun transitions, mismatch hints and staleness, and `ledger tickets`.
Fictional data: acme's invoice exporter, Jira project EXP."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import ledger
import tickets


PR = "mypr:acme/exporter#102"
TICKET = "jira:EXP-123"
URL = "https://jira.example.com/browse/EXP-123"


def run(*argv: str) -> Any:
    args = ledger.build_parser().parse_args(list(argv))
    return args.func(args)


class IdsAndMappingTest(unittest.TestCase):
    def test_ids_per_tracker(self) -> None:
        jira = tickets.parse_id("jira:EXP-123")
        self.assertEqual((jira.system, jira.key, jira.project), ("jira", "EXP-123", "EXP"))
        issue = tickets.parse_id("ghissue:acme/exporter#7")
        self.assertEqual(issue.project, "acme/exporter")
        for bad, message in (
            ("jira:exp-1", "look like jira:EXP-123"),
            ("linear:ENG-1", "not a ticket id"),
            ("EXP-123", "not a ticket id"),
        ):
            with self.subTest(bad=bad), self.assertRaisesRegex(tickets.TicketError, message):
                tickets.parse_id(bad)

    def test_built_in_names_then_the_trackers_own_category(self) -> None:
        jira = tickets.parse_id(TICKET)
        for status, hint, expected in (
            ("In Progress", None, "in-progress"),
            ("Code Review", None, "in-review"),
            ("Won't Do", "Done", "cancelled"),
            ("Ready to ship", "Done", "done"),
            ("Ready to ship", "In Progress", "in-progress"),
            ("Ready to ship", "in-review", "in-review"),
        ):
            with self.subTest(status=status, hint=hint):
                self.assertEqual(tickets.categorize(jira, status, hint, {}), expected)
        with self.assertRaisesRegex(tickets.TicketError, "maps to no category.*BOSUN_TICKET_STATUS_MAP"):
            tickets.categorize(jira, "On Hold", None, {})

    def test_site_overrides_win_per_project_then_per_system(self) -> None:
        overrides = tickets.parse_overrides(
            "jira:On Hold=todo; jira/EXP:On Hold=cancelled\njira:In Review=done"
        )
        self.assertEqual(
            tickets.categorize(tickets.parse_id(TICKET), "on hold", None, overrides), "cancelled"
        )
        self.assertEqual(
            tickets.categorize(tickets.parse_id("jira:OPS-1"), "On Hold", None, overrides), "todo"
        )
        self.assertEqual(
            tickets.categorize(tickets.parse_id("jira:OPS-1"), "In Review", None, overrides), "done"
        )
        for bad in ("jira:On Hold", "jira:On Hold=later", "wiki:On Hold=todo"):
            with self.subTest(bad=bad), self.assertRaisesRegex(tickets.TicketError, "cannot read"):
                tickets.parse_overrides(bad)

    def test_github_issue_states(self) -> None:
        issue = tickets.parse_id("ghissue:acme/exporter#7")
        self.assertEqual(tickets.categorize(issue, "OPEN", None, {}), "todo")
        self.assertEqual(tickets.categorize(issue, "not_planned", None, {}), "cancelled")


class TicketTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ledger.configure(self.tmp.name, status_map="jira/EXP:Ready for Review=in-review", stale_hours=24)
        run("upsert", PR, "--kind", "my-pr", "--title", "VAT breakdown")
        run("link", PR, "tracks", TICKET)

    def item(self, item_id: str) -> dict[str, Any]:
        item: dict[str, Any] = json.loads(ledger.STORE.ledger_file.read_text())["items"][item_id]
        return item

    def sync(self, status: str, *extra: str) -> str:
        output: str = run("ticket-sync", TICKET, "--status", status, *extra)
        return output


class TicketSyncTest(TicketTestCase):
    def test_sync_records_what_the_tracker_shows(self) -> None:
        output = self.sync("To Do", "--title", "VAT breakdown on exports", "--url", URL, "--assignee", "me")
        self.assertTrue(output.startswith(f"ok: {TICKET} unsynced -> todo ('To Do')"))
        item = self.item(TICKET)
        self.assertEqual(
            (item["state"], item["title"], item["url"]), ("todo", "VAT breakdown on exports", URL)
        )
        self.assertEqual(item["fields"]["status_category"], "todo")
        self.assertEqual(item["fields"]["assignee"], "me")

    def test_history_only_on_change_but_synced_at_always(self) -> None:
        self.sync("To Do")
        events = len(self.item(TICKET)["history"])
        first = self.item(TICKET)["fields"]["synced_at"]
        self.assertIn("unchanged", self.sync("To Do"))
        self.assertEqual(len(self.item(TICKET)["history"]), events)
        self.assertGreaterEqual(self.item(TICKET)["fields"]["synced_at"], first)
        self.sync("Ready for Review")
        self.assertEqual(self.item(TICKET)["state"], "in-review")
        self.assertEqual(self.item(TICKET)["history"][-1]["event"], "synced")

    def test_a_new_ticket_is_created_by_sync(self) -> None:
        output = run("ticket-sync", "jira:EXP-9", "--status", "Done")
        self.assertIn("unsynced -> done", output)

    def test_tickets_are_never_transitioned_by_bosun(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "mirrors its tracker.*ticket-sync"):
            run("transition", TICKET, "done", "--note", "merged")

    def test_github_issues_follow_the_repo_mask(self) -> None:
        ledger.configure(self.tmp.name, mask="acme/*")
        with self.assertRaisesRegex(ledger.LedgerError, "outside BOSUN_REPO_MASK"):
            run("ticket-sync", "ghissue:personal/x#1", "--status", "OPEN")


class MismatchTest(TicketTestCase):
    def test_a_pr_in_review_while_the_ticket_is_todo(self) -> None:
        output = self.sync("To Do")
        self.assertIn(
            f"{PR} is awaiting-review but {TICKET} is todo ('To Do'): propose moving it to in-review", output
        )

    def test_a_merged_pr_while_the_ticket_is_open(self) -> None:
        self.sync("In Progress")
        run("transition", PR, "merged", "--note", "merged")
        self.assertIn(f"propose transitioning {TICKET} to done", run("tickets", "--mismatch"))

    def test_a_closed_ticket_while_the_pr_is_open(self) -> None:
        self.sync("Won't Do")
        self.assertIn(f"propose closing {PR}", run("tree", PR))

    def test_work_a_tracked_task_produced_counts(self) -> None:
        run("spawn", PR, "--title", "Fix the PDF renderer", "--ask", "fix them")
        task = run("list", "--kind", "task").split()[0]
        run("link", task, "tracks", "jira:EXP-200")
        run("upsert", "mypr:acme/pdf-renderer#7", "--kind", "my-pr")
        run("link", task, "produces", "mypr:acme/pdf-renderer#7")
        run("ticket-sync", "jira:EXP-200", "--status", "To Do")
        self.assertIn("mypr:acme/pdf-renderer#7 [my-pr: awaiting-review]", run("tickets"))

    def test_quiet_when_in_step_and_stale_when_old(self) -> None:
        self.sync("In Review")
        self.assertEqual(run("tickets", "--mismatch"), "(no tickets)")
        data = json.loads(ledger.STORE.ledger_file.read_text())
        old = (datetime.now(UTC) - timedelta(hours=30)).isoformat(timespec="seconds")
        data["items"][TICKET]["fields"]["synced_at"] = old
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        self.assertIn(f"{TICKET} was last synced 30h ago", run("tickets", "--mismatch"))

    def test_never_synced(self) -> None:
        self.assertIn(f"{TICKET} was never synced", run("tickets"))
        self.assertIn("read:  the site's Jira read tool", run("tickets"))

    def test_finished_tickets_of_finished_work_are_listed_only_with_all(self) -> None:
        self.sync("Done")
        run("transition", PR, "merged", "--note", "merged")
        self.assertEqual(run("tickets"), "(no tickets)")
        self.assertIn(TICKET, run("tickets", "--all"))


class OwnedProjectsTest(TicketTestCase):
    """BOSUN_TICKET_OWNED_PROJECTS: another team's ticket is never ours to move."""

    OTHERS = "jira:OPS-7"

    def setUp(self) -> None:
        super().setUp()
        run("link", PR, "tracks", self.OTHERS)
        self.sync("To Do")
        run("ticket-sync", self.OTHERS, "--status", "Needs Scoping", "--category", "todo")

    def test_parsing_and_matching(self) -> None:
        self.assertEqual(tickets.parse_owned(" EXP, acme/Exporter  ops "), {"exp", "acme/exporter", "ops"})
        self.assertEqual(tickets.parse_owned(""), frozenset())
        owned = tickets.parse_owned("EXP acme/exporter")
        self.assertTrue(tickets.owned(TICKET, owned))
        self.assertTrue(tickets.owned("ghissue:acme/exporter#7", owned))
        self.assertFalse(tickets.owned(self.OTHERS, owned))
        self.assertFalse(tickets.owned("ghissue:acme/other#7", owned))
        self.assertTrue(tickets.owned(self.OTHERS, frozenset()))  # none configured: all ours
        self.assertTrue(tickets.owned("not-a-ticket", owned))

    def test_default_empty_proposes_for_every_ticket(self) -> None:
        output = run("tickets", "--mismatch")
        self.assertIn(f"hint:  {PR} is awaiting-review but {self.OTHERS} is todo", output)
        self.assertNotIn("fyi:", output)

    def test_another_teams_mismatch_is_fyi_only(self) -> None:
        ledger.configure_tickets("EXP")
        output = run("tickets", "--mismatch")
        self.assertIn(f"hint:  {PR} is awaiting-review but {TICKET} is todo", output)
        self.assertNotIn(f"hint:  {PR} is awaiting-review but {self.OTHERS}", output)
        self.assertIn(f"fyi:   {tickets.NOT_OURS}{PR} is awaiting-review but {self.OTHERS} is todo", output)
        synced = run("ticket-sync", self.OTHERS, "--status", "Needs Scoping", "--category", "todo")
        self.assertIn(f"\nfyi: {tickets.NOT_OURS}", synced)
        self.assertNotIn("\nhint:", synced)
        self.assertIn(tickets.NOT_OURS, " ".join(json.loads(run("show", PR))["derived"]["hints"]))

    def test_re_reading_it_stays_a_chore(self) -> None:
        ledger.configure_tickets("EXP")
        data = json.loads(ledger.STORE.ledger_file.read_text())
        old = (datetime.now(UTC) - timedelta(hours=30)).isoformat(timespec="seconds")
        data["items"][self.OTHERS]["fields"]["synced_at"] = old
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        self.assertIn(f"hint:  {self.OTHERS} was last synced 30h ago", run("tickets", "--mismatch"))


if __name__ == "__main__":
    unittest.main()
