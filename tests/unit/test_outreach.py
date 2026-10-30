"""Unit tests for outreach in lib/ledger.py: what we sent to other people and wait on. Ids and
parents, the state machine with its proposal, message and reply rules, the guard that keeps a
parent open while it waits, listings, and matching Slack replies to the conversation we asked in."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import ledger


PARENT = "mypr:acme/exporter#102"
MENTION = "slack:C0000000001/1700000001.000001"
FIRST = f"out:{PARENT}/1"
SLACK = "https://acme.slack.com/archives/C0000000001/p"
THREAD = "?thread_ts=1700000090.000090&cid=C0000000001"
SENT_REF = f"{SLACK}1700000100000100{THREAD}"
REPLY_REF = f"{SLACK}1700000200000200{THREAD}"


def run(*argv: str) -> Any:
    args = ledger.build_parser().parse_args(list(argv))
    return args.func(args)


def ask(*extra: str, parent: str = PARENT, subject: str = "Keep the template unchanged?") -> str:
    output: str = run("reach-out", parent, "--to", "Dana (product)", "--subject", subject, *extra)
    return output


class OutreachTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ledger.configure(self.tmp.name)
        run("upsert", PARENT, "--kind", "my-pr", "--title", "VAT breakdown")
        run("upsert", MENTION, "--kind", "mention")

    def item(self, item_id: str) -> dict[str, Any]:
        item: dict[str, Any] = json.loads(ledger.STORE.ledger_file.read_text())["items"][item_id]
        return item

    def events(self, item_id: str) -> list[str]:
        return [entry["event"] for entry in self.item(item_id)["history"]]

    def sent(self, outreach_id: str = FIRST, ref: str = SENT_REF) -> str:
        output: str = run("sent", outreach_id, "--ref", ref)
        return output


class ReachOutTest(OutreachTestCase):
    def test_creates_the_first_outreach_of_a_parent_in_draft_and_prints_its_id_first(self) -> None:
        output = ask("--option", "a) keep", "--option", "b) re-export", "--purpose", "decision")
        self.assertEqual(output.splitlines()[0], FIRST)
        item = self.item(FIRST)
        self.assertEqual(
            (item["kind"], item["state"], item["title"]),
            ("outreach", "draft", "Keep the template unchanged?"),
        )
        self.assertEqual(
            {k: item["fields"][k] for k in ("parent", "channel", "purpose", "to", "options", "messages")},
            {
                "parent": PARENT,
                "channel": "slack",
                "purpose": "decision",
                "to": "Dana (product)",
                "options": ["a) keep", "b) re-export"],
                "messages": [],
            },
        )
        self.assertIn("outreach", self.events(PARENT))

    def test_numbers_count_up_per_parent_and_a_repeat_changes_nothing(self) -> None:
        ask()
        self.assertTrue(ask(subject="Quantity rules?").startswith(f"out:{PARENT}/2\n"))
        self.assertTrue(ask(parent=MENTION).startswith(f"out:{MENTION}/1\n"))
        repeat = ask()
        self.assertTrue(repeat.startswith(f"{FIRST}\nunchanged"))
        self.assertNotIn(f"out:{PARENT}/3", json.loads(ledger.STORE.ledger_file.read_text())["items"])

    def test_without_a_parent_the_id_is_scoped_by_day(self) -> None:
        output = run("ask", "--to", "platform team", "--question", "Can the field be renamed?")
        self.assertEqual(output.splitlines()[0], f"out:{date.today().isoformat()}/1")
        item = self.item(f"out:{date.today().isoformat()}/1")
        self.assertNotIn("parent", item["fields"])
        self.assertNotIn("hold the work", output)

    def test_channel_purpose_and_details(self) -> None:
        with mock.patch("sys.stdin", io.StringIO("The CSV has two header rows.\n")):
            ask(
                "--channel",
                "email",
                "--purpose",
                "clarification",
                "--details",
                "-",
                "--expect-by",
                "2026-10-20",
            )
        fields = self.item(FIRST)["fields"]
        self.assertEqual(
            (fields["channel"], fields["purpose"], fields["details"], fields["expect_by"]),
            ("email", "clarification", "The CSV has two header rows.", "2026-10-20"),
        )

    def test_rejects_unknown_channels_and_purposes(self) -> None:
        for extra in (["--channel", "fax"], ["--purpose", "gossip"]):
            with (
                self.subTest(extra=extra),
                mock.patch("sys.stderr", io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                ask(*extra)

    def test_rejects_bad_values(self) -> None:
        for extra, subject, message in [
            ([], "two\nlines", "--subject must be one non-empty line"),
            (["--option", ""], "ok", "--option must be one non-empty line"),
            (["--expect-by", "Friday"], "ok", "--expect-by takes a date"),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(ledger.LedgerError, message):
                ask(*extra, subject=subject)

    def test_the_parent_must_be_an_open_work_item(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "no item 'slack:C9/9'"):
            ask(parent="slack:C9/9")
        ask()
        with self.assertRaisesRegex(ledger.LedgerError, f"is itself outreach.*\\({PARENT}\\)"):
            ask(parent=FIRST)
        run("transition", MENTION, "done", "--note", "answered")
        with self.assertRaisesRegex(ledger.LedgerError, "is done; outreach belongs to open work items"):
            ask(parent=MENTION)

    def test_upsert_does_not_create_outreach(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "create outreach with: bosun ledger reach-out"):
            run("upsert", "out:x/1", "--kind", "outreach")


class ProposalTest(OutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        ask()

    def test_proposing_the_message_moves_a_draft_to_proposed(self) -> None:
        output = run("propose", FIRST, "Slack thread reply: 'Dana, ...'")
        self.assertIn("draft -> proposed", output)
        self.assertEqual(self.item(FIRST)["state"], "proposed")

    def test_the_message_can_come_from_stdin(self) -> None:
        message = "Slack DM to Dana: Hi Dana, it's about the shared password..."
        with mock.patch("sys.stdin", io.StringIO(f"{message}\n")):
            run("propose", FIRST, "-")
        self.assertEqual(self.item(FIRST)["proposal"]["action"], message)
        with (
            mock.patch("sys.stdin", io.StringIO(" \n")),
            self.assertRaisesRegex(ledger.LedgerError, "read nothing from stdin"),
        ):
            run("propose", FIRST, "-")

    def test_rejecting_or_withdrawing_it_goes_back_to_draft(self) -> None:
        for outcome in ("rejected", "withdrawn"):
            with self.subTest(outcome=outcome):
                run("propose", FIRST, f"message {outcome}")
                self.assertIn("proposed -> draft", run("resolve", FIRST, outcome))
                self.assertEqual(self.item(FIRST)["state"], "draft")

    def test_my_edits_change_the_channel_and_replace_the_proposal(self) -> None:
        run("propose", FIRST, "Slack DM to Dana: ...")
        run("upsert", FIRST, "--field", "channel=jira", "--field", "to=Dana (PM)")
        run("propose", FIRST, "Jira comment on the ticket: ...")
        item = self.item(FIRST)
        self.assertEqual((item["fields"]["channel"], item["fields"]["to"]), ("jira", "Dana (PM)"))
        self.assertEqual(item["proposal"]["action"], "Jira comment on the ticket: ...")
        self.assertIn("proposal-replaced", self.events(FIRST))
        for field in ("channel=pigeon", "purpose=gossip"):
            with self.subTest(field=field), self.assertRaisesRegex(ledger.LedgerError, "unknown outreach"):
                run("upsert", FIRST, "--field", field)

    def test_done_is_recorded_with_sent_not_resolve(self) -> None:
        run("propose", FIRST, "message")
        with self.assertRaisesRegex(ledger.LedgerError, "record it with bosun ledger sent"):
            run("resolve", FIRST, "done")

    def test_proposed_needs_a_pending_proposal(self) -> None:
        with self.assertRaisesRegex(
            ledger.LedgerError, "awaits my approval; record it with bosun ledger propose"
        ):
            run("transition", FIRST, "proposed", "--note", "x")


class SentTest(OutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        ask()
        run("propose", FIRST, "message")

    def test_the_first_message_resolves_the_proposal_and_sends(self) -> None:
        self.assertIn("proposed -> sent", self.sent())
        item = self.item(FIRST)
        self.assertIsNone(item["proposal"])
        self.assertEqual([m["ref"] for m in item["fields"]["messages"]], [SENT_REF])
        self.assertEqual(item["fields"]["messages"][0]["channel"], "slack")
        self.assertIn("proposal-done", self.events(FIRST))

    def test_later_messages_are_follow_ups_and_a_repeat_changes_nothing(self) -> None:
        self.sent()
        run("propose", FIRST, "nudge")
        self.assertEqual(
            self.sent(ref=f"{SLACK}1700000300000300{THREAD}"),
            f"ok: follow-up 1 recorded on {FIRST}; at 2023-11-14T22:18:20+00:00 (slack ref)",
        )
        self.assertIn("unchanged", self.sent())
        item = self.item(FIRST)
        self.assertEqual(
            (item["state"], len(item["fields"]["messages"]), item["proposal"]), ("sent", 2, None)
        )
        self.assertIn("nudged 1x", ledger.outreach_status(item))

    def test_a_message_on_another_channel(self) -> None:
        run("sent", FIRST, "--ref", "https://acme.example.com/issue/7#comment-1", "--channel", "jira")
        self.assertEqual(self.item(FIRST)["fields"]["messages"][0]["channel"], "jira")

    def test_sent_needs_a_message_ref(self) -> None:
        with self.assertRaisesRegex(
            ledger.LedgerError, "sent needs --field messages=.*use: bosun ledger sent"
        ):
            run("transition", FIRST, "sent", "--note", "x")

    def test_no_messages_once_replied(self) -> None:
        self.sent()
        run("replied", FIRST, "--evidence", REPLY_REF, "--summary", "b")
        with self.assertRaisesRegex(ledger.LedgerError, "is replied; only outreach waiting"):
            self.sent(ref="x")


class RepliedTest(OutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        ask()

    def test_a_reply_needs_evidence(self) -> None:
        self.sent()
        with self.assertRaisesRegex(ledger.LedgerError, "needs --evidence: a reference to the reply"):
            run("transition", FIRST, "replied", "--note", "x", "--field", "answer=b")

    def test_replied_records_the_answer_and_points_to_me_without_a_window(self) -> None:
        self.sent()
        output = run("replied", FIRST, "--evidence", REPLY_REF, "--summary", "option b: re-export")
        self.assertIn("sent -> replied", output)
        self.assertIn(f"{PARENT} is not with a repo session; tell me the reply", output)
        fields = self.item(FIRST)["fields"]
        self.assertEqual((fields["answer"], fields["reply_ref"]), ("option b: re-export", REPLY_REF))

    def test_points_to_the_window_holding_the_parent(self) -> None:
        run("record-delegation", PARENT, "--window", "exporter-tax", "--task", "fix-my-pr")
        self.sent()
        output = run("replied", FIRST, "--evidence", "user: Marco said b", "--summary", "b")
        self.assertIn(
            f"window exporter-tax holds {PARENT}; hand the reply over with bosun delegate --update '{FIRST}'",
            output,
        )

    def test_without_a_parent_it_goes_to_me(self) -> None:
        run("ask", "--to", "platform team", "--subject", "rename?")
        standalone = f"out:{date.today().isoformat()}/1"
        output = run("replied", standalone, "--evidence", "user: yes", "--summary", "yes")
        self.assertIn("draft -> replied", output)
        self.assertIn("belongs to no work item; tell me the reply", output)

    def test_a_reply_before_sending_withdraws_the_proposal(self) -> None:
        run("propose", FIRST, "message")
        run("replied", FIRST, "--evidence", "user: Dana told me", "--summary", "keep it")
        self.assertEqual(self.item(FIRST)["state"], "replied")
        self.assertIsNone(self.item(FIRST)["proposal"])
        self.assertIn("proposal-withdrawn", self.events(FIRST))

    def test_an_id_without_its_prefix_points_to_the_right_one(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, f"outreach ids start with out: \\('{FIRST}'\\)"):
            run("replied", f"{PARENT}/1", "--evidence", "user: b", "--summary", "b")

    def test_a_second_reply_updates_the_answer(self) -> None:
        run("replied", FIRST, "--evidence", "user: a", "--summary", "a")
        self.assertIn("updated", run("replied", FIRST, "--evidence", REPLY_REF, "--summary", "b after all"))
        self.assertEqual(self.item(FIRST)["fields"]["answer"], "b after all")

    def test_no_reply_and_a_late_reply(self) -> None:
        self.sent()
        run("transition", FIRST, "no-reply", "--note", "two nudges, no answer")
        self.assertFalse(ledger.is_open(self.item(FIRST)))
        run("replied", FIRST, "--evidence", REPLY_REF, "--summary", "late: b")
        self.assertEqual(self.item(FIRST)["state"], "replied")

    def test_dropped_is_the_way_out_not_dismissed(self) -> None:
        self.assertIn("draft -> dropped", run("transition", FIRST, "dropped", "--note", "not needed"))
        ask(subject="other")
        with self.assertRaisesRegex(ledger.LedgerError, "not allowed for outreach"):
            run("transition", f"out:{PARENT}/2", "dismissed", "--note", "x")


class ParentGuardTest(OutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        ask(parent=MENTION)
        self.outreach = f"out:{MENTION}/1"

    def test_a_parent_waiting_on_outreach_is_not_finished(self) -> None:
        with self.assertRaisesRegex(
            ledger.LedgerError, f"is waiting on: {self.outreach} \\(draft, slack to Dana"
        ):
            run("transition", MENTION, "done", "--note", "answered")
        with self.assertRaisesRegex(ledger.LedgerError, "add --force-open-outreach"):
            run("report", MENTION, "--summary", "done", "--state", "dismissed")
        self.assertEqual(self.item(MENTION)["state"], "new")

    def test_states_that_do_not_finish_it_are_fine(self) -> None:
        self.assertIn("new -> surfaced", run("transition", MENTION, "surfaced", "--note", "x"))

    def test_forcing_it_names_the_open_outreach(self) -> None:
        run("transition", MENTION, "done", "--note", "PR opened", "--force-open-outreach")
        self.assertEqual(self.item(MENTION)["state"], "done")
        self.assertIn(
            f"finished while waiting on: {self.outreach}", self.item(MENTION)["history"][-1]["note"]
        )
        self.assertTrue(ledger.is_open(self.item(self.outreach)))

    def test_a_pr_that_merged_or_closed_finishes_and_names_the_open_outreach(self) -> None:
        for kind, item_id, state in (
            ("my-pr", "mypr:acme/exporter#201", "merged"),
            ("my-pr", "mypr:acme/exporter#202", "closed"),
            ("review-request", "review:acme/exporter#203", "closed"),
        ):
            with self.subTest(item=item_id):
                run("upsert", item_id, "--kind", kind)
                ask(parent=item_id)
                self.assertIn(f"-> {state}", run("transition", item_id, state, "--note", "on GitHub"))
                self.assertIn(
                    f"finished while waiting on: out:{item_id}/1", self.item(item_id)["history"][-1]["note"]
                )
                self.assertTrue(ledger.is_open(self.item(f"out:{item_id}/1")))
        run("upsert", "mypr:acme/exporter#204", "--kind", "my-pr")
        ask(parent="mypr:acme/exporter#204")
        with self.assertRaisesRegex(ledger.LedgerError, "add --force-open-outreach"):
            run("transition", "mypr:acme/exporter#204", "dismissed", "--note", "x")

    def test_a_merged_or_closed_pr_closes_its_review_requests(self) -> None:
        # Before 2026-10-26 they stayed "sent" for good once the PR merged.
        for state in ("merged", "closed"):
            with self.subTest(state=state):
                pr = f"mypr:acme/exporter#{210 + ('merged', 'closed').index(state)}"
                run("upsert", pr, "--kind", "my-pr")
                ask("--purpose", "review", parent=pr, subject="Review please")
                run("sent", f"out:{pr}/1", "--ref", "https://acme.example.com/archives/C0/p1")
                ask("--purpose", "review", parent=pr, subject="Second look")
                ask("--purpose", "question", parent=pr, subject="Ship it to staging first?")
                outcome = run("transition", pr, state, "--note", "on GitHub")
                self.assertIn(f"out:{pr}/1 sent -> dropped", outcome)
                self.assertIn(f"out:{pr}/2 draft -> dropped", outcome)
                self.assertEqual(self.item(f"out:{pr}/1")["state"], "dropped")
                self.assertIn(f"{pr} is {state}", self.item(f"out:{pr}/1")["history"][-1]["note"])
                self.assertTrue(ledger.is_open(self.item(f"out:{pr}/3")))  # a question may still matter

    def test_a_report_that_merges_the_pr_closes_its_review_requests_too(self) -> None:
        pr = "mypr:acme/exporter#215"
        run("upsert", pr, "--kind", "my-pr")
        ask("--purpose", "review", parent=pr, subject="Review please")
        run("sent", f"out:{pr}/1", "--ref", "https://acme.example.com/archives/C0/p2")
        run("report", pr, "--summary", "merged after review", "--state", "merged")
        self.assertEqual(self.item(f"out:{pr}/1")["state"], "dropped")

    def test_applied_dropped_or_given_up_outreach_no_longer_blocks(self) -> None:
        for state in ("applied", "dropped", "no-reply"):
            with self.subTest(state=state):
                run("upsert", "slack:C2/2", "--kind", "mention")
                ask(parent="slack:C2/2", subject=state)
                outreach = f"out:slack:C2/2/{('applied', 'dropped', 'no-reply').index(state) + 1}"
                if state != "dropped":
                    self.sent(outreach, ref=f"ref-{state}")
                if state == "applied":
                    run("replied", outreach, "--evidence", "user: ok", "--summary", "ok")
                run("transition", outreach, state, "--note", "x")
                self.assertIn("-> done", run("transition", "slack:C2/2", "done", "--note", "x"))
                run("transition", "slack:C2/2", "dismissed", "--note", "x")
                run("transition", "slack:C2/2", "new", "--note", "reopen for the next case")


class ListingTest(OutreachTestCase):
    def test_parents_show_what_they_wait_on(self) -> None:
        ask()
        run("propose", FIRST, "message")
        self.sent()
        rows = run("list", "--open").splitlines()
        parent_row = next(row for row in rows if row.startswith(PARENT))
        self.assertRegex(parent_row, rf"\| waiting on {FIRST} \(slack to Dana \(product\), \d+[mhd]\)$")
        outreach_row = next(row for row in rows if row.startswith(FIRST))
        self.assertIn(
            "[outreach: sent] - Keep the template unchanged? (slack to Dana (product), ", outreach_row
        )
        self.assertIn(f"waiting on {FIRST}", ledger.STORE.view.read_text())

    def test_outreach_command(self) -> None:
        ask("--option", "a", "--option", "b")
        run("ask", "--channel", "email", "--to", "vendor contact", "--subject", "Invoice format?")
        self.sent()
        listing = run("outreach", "--open")
        self.assertIn(f"{FIRST}  [sent] question: slack to Dana (product)", listing)
        self.assertIn("  options:  a | b", listing)
        self.assertIn(f"  sent:     {SENT_REF} (slack,", listing)
        self.assertRegex(listing, r"\n  waiting:  \d+ business day\(s\) since the last message")
        self.assertIn("draft, email to vendor contact; no parent item", listing)
        self.assertEqual(run("outreach", "--parent", MENTION), "(no outreach)")

    def test_kinds_describe_outreach(self) -> None:
        kinds = run("kinds")
        self.assertIn("outreach: Something sent to someone outside this machine", kinds)
        self.assertIn("  sent -> applied, dropped, no-reply, replied", kinds)
        self.assertIn("A one-way message (purpose fyi) waits on nobody", kinds)
        self.assertIn("  -> replied (from draft, no-reply, proposed, sent) requires --evidence", kinds)


class SlackReplyMatchTest(OutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        ask()
        self.sent()

    def seen(self, permalink: str) -> tuple[str, int]:
        result: tuple[str, int] = run("seen", permalink)
        return result

    def test_a_message_in_the_thread_we_asked_in_names_the_outreach(self) -> None:
        output, code = self.seen(REPLY_REF)
        self.assertEqual(code, 1)
        self.assertIn(f"in the conversation of {FIRST}", output)
        self.assertIn("--class outreach --item", output)

    def test_other_threads_and_processed_messages_get_no_hint(self) -> None:
        self.assertEqual(self.seen(f"{SLACK}1700000200000200?thread_ts=1700000555.000555"), ("", 1))
        self.assertEqual(self.seen("https://acme.slack.com/archives/C0000000009/p1700000200000200"), ("", 1))
        run("record-source", REPLY_REF, "--class", "outreach", "--item", FIRST)
        self.assertEqual(self.seen(REPLY_REF)[1], 0)

    def test_in_a_dm_any_later_message_counts(self) -> None:
        dm = "https://acme.slack.com/archives/D0000000004/p"
        ask(subject="DM question")
        self.sent(f"out:{PARENT}/2", ref=f"{dm}1700000100000100")
        self.assertIn(f"out:{PARENT}/2", self.seen(f"{dm}1700000400000400")[0])
        self.assertEqual(self.seen(f"{dm}1700000000000000"), ("", 1))

    def test_answered_outreach_is_still_matched_but_finished_is_not(self) -> None:
        run("replied", FIRST, "--evidence", REPLY_REF, "--summary", "b")
        self.assertIn(FIRST, self.seen(f"{SLACK}1700000250000250{THREAD}")[0])
        run("transition", FIRST, "applied", "--note", "told the user")
        self.assertEqual(self.seen(f"{SLACK}1700000250000250{THREAD}"), ("", 1))


class SharedThreadTest(OutreachTestCase):
    """Two open items with messages in one thread (a decision, then an addition to it): a reply
    there is not credited to whichever comes first; `seen` names both, the latest message first."""

    def setUp(self) -> None:
        super().setUp()
        ask(subject="Decision 6: keep the template unchanged?")
        ask(subject="Addition to decision 6: the footer too?")
        self.second = f"out:{PARENT}/2"
        self.sent(self.second, ref=f"{SLACK}1700000150000150{THREAD}")
        self.sent()

    def test_seen_lists_every_open_item_in_the_thread_latest_message_first(self) -> None:
        output, code = run("seen", REPLY_REF)
        lines = output.splitlines()
        self.assertEqual(code, 1)
        self.assertIn("in a conversation shared by 2 open outreach items (latest message first)", lines[0])
        self.assertIn("pick the one it answers; ask me when unclear", lines[0])
        self.assertTrue(lines[1].startswith(f"  {self.second} [sent] question to Dana (product): Addition"))
        self.assertIn("(last message 2023-11-14T22:15:50+00:00)", lines[1])
        self.assertTrue(lines[2].startswith(f"  {FIRST} [sent] question to Dana (product): Decision 6"))
        self.assertIn("one call per item it answers", lines[3])

    def test_finished_items_drop_out_and_one_left_keeps_the_single_format(self) -> None:
        run("transition", self.second, "dropped", "--note", f"merged into {FIRST}")
        output, _ = run("seen", REPLY_REF)
        self.assertTrue(output.startswith(f"not processed yet; it is in the conversation of {FIRST} ("))

    def test_a_later_message_on_the_older_item_puts_it_first(self) -> None:
        self.sent(ref=f"{SLACK}1700000180000180{THREAD}")
        self.assertTrue(run("seen", REPLY_REF)[0].splitlines()[1].startswith(f"  {FIRST} "))


class OneWayTest(OutreachTestCase):
    """Purpose fyi: a one-way message needs no reply, so once sent it is done."""

    def test_reach_out_says_no_reply_is_needed(self) -> None:
        output = ask("--purpose", "fyi", subject="One more commit landed on the PR")
        self.assertIn("it needs no reply: once sent, it is done", output)
        self.assertNotIn("hold the work", output)

    def test_sent_moves_it_on_to_applied_and_it_waits_on_nothing(self) -> None:
        ask("--purpose", "fyi")
        run("propose", FIRST, "Slack thread reply: one more commit landed")
        output = self.sent()
        self.assertIn(f"ok: {FIRST} proposed -> sent -> applied (fyi: one-way, no reply expected)", output)
        self.assertTrue(output.endswith("(slack ref)"))
        item = self.item(FIRST)
        self.assertEqual(
            (item["state"], len(item["fields"]["messages"]), item["proposal"]), ("applied", 1, None)
        )
        self.assertEqual(run("outreach", "--open"), "(no outreach)")
        self.assertNotIn("waiting on", run("list", "--open"))
        self.assertIn("-> done", run("transition", MENTION, "done", "--note", "no outreach on it"))

    def test_only_one_way_messages_skip_the_reply(self) -> None:
        ask("--purpose", "info-ack")
        self.sent()
        with self.assertRaisesRegex(ledger.LedgerError, "sent -> applied is only for one-way messages"):
            run("transition", FIRST, "applied", "--note", "x")

    def test_an_item_retagged_fyi_after_sending_is_closed_by_hand(self) -> None:
        ask("--purpose", "info-ack")
        self.sent()
        output = run("upsert", FIRST, "--field", "purpose=fyi")
        self.assertIn(
            f"is one-way (fyi) and already sent; close it: bosun ledger transition '{FIRST}'", output
        )
        self.assertIn(
            "sent -> applied", run("transition", FIRST, "applied", "--note", "one-way message sent")
        )


class CombinedMessageTest(OutreachTestCase):
    """One message (e.g. a single nudge) covering several items is recorded on each."""

    def setUp(self) -> None:
        super().setUp()
        for subject in ("Decision 1?", "Decision 2?", "Decision 3?"):
            ask(subject=subject)
        self.ids = [f"out:{PARENT}/{n}" for n in (1, 2, 3)]
        self.sent(self.ids[0], ref="https://acme.example.com/old-1")
        self.sent(self.ids[1], ref="https://acme.example.com/old-2")
        run("propose", self.ids[0], "one nudge for decisions 1-3")

    def test_one_ref_on_several_items_resets_each_wait_clock(self) -> None:
        nudge = f"{SLACK}1700000300000300{THREAD}"
        lines = run("sent", *self.ids, "--ref", nudge).splitlines()
        self.assertEqual(
            lines,
            [
                f"ok: follow-up 1 recorded on {self.ids[0]}; at 2023-11-14T22:18:20+00:00 (slack ref)",
                f"ok: follow-up 1 recorded on {self.ids[1]}; at 2023-11-14T22:18:20+00:00 (slack ref)",
                f"ok: {self.ids[2]} draft -> sent; at 2023-11-14T22:18:20+00:00 (slack ref)",
            ],
        )
        for outreach_id in self.ids:
            item = self.item(outreach_id)
            self.assertEqual(item["fields"]["messages"][-1]["ref"], nudge)
            self.assertIn(f"one message for {', '.join(self.ids)}", item["history"][-1]["note"])
        self.assertIsNone(self.item(self.ids[0])["proposal"])

    def test_all_or_none_and_a_repeated_id_counts_once(self) -> None:
        run("replied", self.ids[1], "--evidence", "user: b", "--summary", "b")
        with self.assertRaisesRegex(ledger.LedgerError, f"{self.ids[1]} is replied"):
            run("sent", *self.ids, "--ref", "https://acme.example.com/nudge")
        self.assertEqual(len(self.item(self.ids[0])["fields"]["messages"]), 1)
        output = run("sent", self.ids[0], self.ids[0], "--ref", "https://acme.example.com/nudge")
        self.assertEqual(output.count("\n"), 0)
        self.assertEqual(len(self.item(self.ids[0])["fields"]["messages"]), 2)


class OverlapTest(OutreachTestCase):
    """A new draft next to open outreach of the same parent to the same person: warned, not refused."""

    def test_warns_and_names_the_existing_conversation(self) -> None:
        ask("--purpose", "decision")
        self.sent()
        output = run("reach-out", PARENT, "--to", "dana", "--purpose", "info-ack", "--subject", "Footer too?")
        self.assertTrue(output.startswith(f"out:{PARENT}/2\ncreated:"))
        self.assertIn(
            f"warning: {PARENT} already has open outreach to the same person: {FIRST} (decision, sent: "
            "Keep the template unchanged?)",
            output,
        )
        self.assertIn(f"bosun ledger fold 'out:{PARENT}/2' --into '<existing id>'", output)
        self.assertIn(
            f"bosun ledger transition 'out:{PARENT}/2' dropped --note 'duplicate of <existing id>'",
            output,
        )
        self.assertEqual(self.item(f"out:{PARENT}/2")["state"], "draft")

    def test_no_warning_for_others_one_way_finished_items_or_an_exact_repeat(self) -> None:
        ask()
        self.assertTrue(ask().startswith(f"{FIRST}\nunchanged"))
        self.assertNotIn("warning", ask("--purpose", "fyi", subject="One more commit landed"))
        self.assertNotIn("warning", ask(subject="other question", parent=MENTION))
        self.assertNotIn("warning", run("reach-out", PARENT, "--to", "Marco", "--subject", "Rows?"))
        self.assertNotIn("warning", run("ask", "--to", "Dana (product)", "--subject", "no parent"))
        run("transition", FIRST, "dropped", "--note", "not needed")
        self.assertNotIn("warning", ask(subject="new question"))

    def test_same_person_is_loose_about_case_roles_and_surnames(self) -> None:
        for one, other, same in [
            ("Dana (product)", "dana", True),
            ("Marco", "Marco Rossi", True),
            ("Marco Rossi", "marco rossi (PM)", True),
            ("Marco Rossi", "Marco Bianchi", False),
            ("Dana", "Priya", False),
            ("(team)", "Dana", False),
        ]:
            with self.subTest(one=one, other=other):
                self.assertEqual(ledger.same_person(one, other), same)


class RecordEditTest(OutreachTestCase):
    """Keeping the record in line with the message actually sent."""

    def setUp(self) -> None:
        super().setUp()
        ask("--option", "a) keep", "--option", "b) re-export")

    def test_subject_and_title_stay_one(self) -> None:
        run("upsert", FIRST, "--field", "subject=Keep the template unchanged (without the key question)?")
        item = self.item(FIRST)
        self.assertEqual(item["title"], item["fields"]["subject"])
        run("upsert", FIRST, "--title", "Keep it unchanged?")
        item = self.item(FIRST)
        self.assertEqual(
            (item["title"], item["fields"]["subject"]), ("Keep it unchanged?", "Keep it unchanged?")
        )
        self.assertIn("  subject:  Keep it unchanged?", run("outreach"))

    def test_options_are_one_line_split_on_the_bar(self) -> None:
        run("upsert", FIRST, "--field", "options=a) keep | c) drop the footer")
        self.assertEqual(self.item(FIRST)["fields"]["options"], ["a) keep", "c) drop the footer"])
        self.assertIn("  options:  a) keep | c) drop the footer", run("outreach"))
        run("upsert", FIRST, "--field", "options=")
        self.assertNotIn("options:", run("outreach"))

    def test_bad_values_are_refused(self) -> None:
        for field in ("subject=", "to=", "subject=two\nlines"):
            with self.subTest(field=field), self.assertRaisesRegex(ledger.LedgerError, "one non-empty line"):
                run("upsert", FIRST, "--field", field)
        self.assertEqual(self.item(FIRST)["fields"]["subject"], "Keep the template unchanged?")


SENT_AT = "2023-11-14T22:15:00+00:00"  # SENT_REF's own ts
REPLY_AT = "2023-11-14T22:16:40+00:00"  # REPLY_REF's
EMAIL_REF = "<msg-1@example.com>"
UTC_PLUS_1 = "Etc/GMT-1"  # POSIX sign: one hour east of UTC


@contextlib.contextmanager
def local_zone(zone: str) -> Iterator[None]:
    try:
        with mock.patch.dict(os.environ, {"TZ": zone}):
            time.tzset()
            yield
    finally:
        time.tzset()


class MessageTimeTest(OutreachTestCase):
    """When a message went out or a reply came, if earlier than when it was recorded (backfill)."""

    def setUp(self) -> None:
        super().setUp()
        ask()
        run("propose", FIRST, "message")

    def last_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = self.item(FIRST)["history"][-1]
        return entry

    def test_a_slack_ref_gives_the_time_it_was_posted_and_the_history_says_so(self) -> None:
        self.assertIn(f"proposed -> sent; at {SENT_AT} (slack ref)", self.sent())
        self.assertEqual(self.item(FIRST)["fields"]["messages"][0]["at"], SENT_AT)
        entry = self.last_entry()
        self.assertEqual((entry["happened_at"], entry["time_from"]), (SENT_AT, "slack ref"))
        self.assertGreater(ledger.parse_time(entry["at"]), ledger.parse_time(SENT_AT))

    def test_at_wins_and_is_stored_in_utc(self) -> None:
        output = run("sent", FIRST, "--ref", SENT_REF, "--at", "2026-10-15T12:20:59+01:00")
        self.assertIn("; at 2026-10-15T11:20:59+00:00 (--at)", output)
        self.assertEqual(self.item(FIRST)["fields"]["messages"][0]["at"], "2026-10-15T11:20:59+00:00")
        self.assertEqual(self.last_entry()["time_from"], "--at")

    def test_given_times_iso_with_offset_or_local(self) -> None:
        self.assertEqual(ledger.parse_given_time("2026-10-15T11:20:59Z"), "2026-10-15T11:20:59+00:00")
        with local_zone(UTC_PLUS_1):
            self.assertEqual(ledger.parse_given_time("2026-10-15 12:20"), "2026-10-15T11:20:00+00:00")
            self.assertEqual(ledger.parse_given_time("2026-10-15T12:20:05"), "2026-10-15T11:20:05+00:00")

    def test_garbage_and_the_future_are_refused_and_change_nothing(self) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat(timespec="minutes")
        for value, error in [
            ("yesterday", "--at takes an ISO 8601 time"),
            ("2026-10-15", "--at takes an ISO 8601 time"),
            ("2026-10-15 25:00", "--at takes an ISO 8601 time"),
            (future, "is in the future"),
        ]:
            with self.subTest(value=value), self.assertRaisesRegex(ledger.LedgerError, error):
                run("sent", FIRST, "--ref", SENT_REF, "--at", value)
        self.assertEqual(
            (self.item(FIRST)["state"], self.item(FIRST)["fields"]["messages"]), ("proposed", [])
        )

    def test_without_a_slack_ref_or_at_it_is_now(self) -> None:
        before = datetime.now(UTC).replace(microsecond=0)
        output = run("sent", FIRST, "--ref", EMAIL_REF, "--channel", "email")
        self.assertNotIn("; at ", output)
        self.assertGreaterEqual(ledger.parse_time(self.item(FIRST)["fields"]["messages"][0]["at"]), before)
        self.assertNotIn("happened_at", self.last_entry())

    def test_at_corrects_a_message_recorded_late(self) -> None:
        run("sent", FIRST, "--ref", EMAIL_REF, "--channel", "email")
        recorded = self.item(FIRST)["fields"]["messages"][0]["at"]
        self.assertTrue(run("sent", FIRST, "--ref", EMAIL_REF).startswith("unchanged"))
        output = run("sent", FIRST, "--ref", EMAIL_REF, "--at", "2026-10-15T12:20+01:00")
        self.assertEqual(
            output, f"ok: {FIRST}: {EMAIL_REF} now at 2026-10-15T11:20:00+00:00 (was {recorded})"
        )
        entry = self.last_entry()
        self.assertEqual(
            (entry["event"], entry["ref"], entry["happened_at"], entry["note"]),
            ("message-time-corrected", EMAIL_REF, "2026-10-15T11:20:00+00:00", f"was {recorded}"),
        )
        self.assertIn("unchanged", run("sent", FIRST, "--ref", EMAIL_REF, "--at", "2026-10-15T11:20Z"))
        self.assertEqual(len(self.item(FIRST)["fields"]["messages"]), 1)

    def test_a_message_time_can_be_corrected_once_replied(self) -> None:
        self.sent()
        run("replied", FIRST, "--evidence", REPLY_REF, "--summary", "b")
        self.assertIn("now at 2023-11-14T22:14:00+00:00", self.sent_at("2023-11-14T22:14Z"))

    def sent_at(self, moment: str) -> str:
        output: str = run("sent", FIRST, "--ref", SENT_REF, "--at", moment)
        return output

    def test_the_wait_counts_from_the_latest_message_time(self) -> None:
        recent = (datetime.now(UTC) - timedelta(hours=3)).isoformat(timespec="seconds")
        run("sent", FIRST, "--ref", EMAIL_REF, "--channel", "email", "--at", recent)
        run("sent", FIRST, "--ref", "<msg-0@example.com>", "--at", "2026-10-01T09:00Z")  # recorded later
        self.assertEqual(ledger.last_message_at(self.item(FIRST)["fields"]["messages"]), recent)
        self.assertIn("slack to Dana (product), 3h, nudged 1x", ledger.outreach_status(self.item(FIRST)))

    def test_reply_time_from_its_slack_ref_or_at(self) -> None:
        self.sent()
        self.assertIn(
            f"; at {REPLY_AT} (slack ref)", run("replied", FIRST, "--evidence", REPLY_REF, "--summary", "b")
        )
        self.assertEqual(self.item(FIRST)["fields"]["replied_at"], REPLY_AT)
        run(
            "replied",
            FIRST,
            "--evidence",
            "user: Dana told me on Monday",
            "--summary",
            "b",
            "--at",
            "2026-10-12 10:00Z",
        )
        entry = self.last_entry()
        self.assertEqual((entry["event"], entry["time_from"]), ("reply-updated", "--at"))
        self.assertEqual(self.item(FIRST)["fields"]["replied_at"], "2026-10-12T10:00:00+00:00")

    def test_a_slack_ts_ahead_of_this_clock_is_now(self) -> None:
        ahead = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp() * 1_000_000)
        moment = ledger.slack_time(f"{SLACK}{ahead}")
        self.assertIsNotNone(moment)
        self.assertLessEqual(ledger.parse_time(str(moment)), datetime.now(UTC))


class TimeTest(unittest.TestCase):
    def test_business_days_skip_weekends(self) -> None:
        friday = "2026-10-16T17:00:00+00:00"
        self.assertEqual(ledger.business_days_since(friday, date(2026, 10, 16)), 0)
        self.assertEqual(ledger.business_days_since(friday, date(2026, 10, 18)), 0)
        self.assertEqual(ledger.business_days_since(friday, date(2026, 10, 19)), 1)
        self.assertEqual(ledger.business_days_since(friday, date(2026, 10, 23)), 5)

    def test_age(self) -> None:
        moment = datetime.now(UTC)
        for delta, expected in [
            (timedelta(minutes=5), "5m"),
            (timedelta(hours=3), "3h"),
            (timedelta(days=2), "2d"),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(ledger.age((moment - delta).isoformat()), expected)

    def test_status_mentions_the_expected_date(self) -> None:
        item = {
            "state": "sent",
            "fields": {
                "channel": "email",
                "to": "vendor",
                "expect_by": "2026-10-20",
                "messages": [{"ref": "m1", "channel": "email", "at": datetime.now(UTC).isoformat()}],
            },
        }
        self.assertRegex(ledger.outreach_status(item), r"^email to vendor, \d+m, expected by 2026-10-20$")


if __name__ == "__main__":
    unittest.main()
