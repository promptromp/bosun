"""Unit tests for lib/learn_checks.py: the deterministic ledger hints learning gives the analysis
(overlapping outreach, info-ack outreach waiting for a confirmation, sensitive subjects in a
channel, second hand-overs), on hand-built items, including the cases that must stay quiet."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import learn_checks
import similar


SINCE = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)
IN_WINDOW = "2026-01-06T09:00:00+00:00"
BEFORE = "2026-01-05T09:00:00+00:00"
PARENT = "mypr:acme/exporter#7"
THREAD = "https://acme.slack.com/archives/C0000000001/p1700000001000001"


def out(n: int, created: str = IN_WINDOW, state: str = "draft", **fields: Any) -> dict[str, Any]:
    return {
        "id": f"out:{PARENT}/{n}",
        "kind": "outreach",
        "state": state,
        "created_at": created,
        "fields": {"parent": PARENT, "purpose": "question", **fields},
    }


def handed(*events: tuple[str, str, str]) -> dict[str, Any]:
    """An item whose history has (event, window, at) entries."""
    return {
        "id": "review:acme/exporter#9",
        "kind": "review-request",
        "state": "delegated",
        "history": [{"event": e, "window": w, "at": at} for e, w, at in events],
    }


class OverlapTest(unittest.TestCase):
    def hints(self, *items: dict[str, Any]) -> list[str]:
        return learn_checks.overlap_hints(list(items), SINCE)

    def test_same_person_and_similar_wording_overlap(self) -> None:
        a = out(1, to="Dana (QA)", subject="Rotate the leaked export test key today")
        b = out(2, to="Dana", subject="Leaked export test key: rotate it")
        [hint] = self.hints(a, b)
        self.assertIn(f"out:{PARENT}/1 (draft) and out:{PARENT}/2 (draft)", hint)
        self.assertIn("shared recipient dana", hint)
        self.assertIn("similar subject", hint)

    def test_same_parent_and_person_without_similar_wording_is_quiet(self) -> None:
        a = out(1, to="Dana", subject="Which currency does the footer use?")
        b = out(2, to="Dana", subject="Can line notes hold markdown?")
        self.assertEqual(self.hints(a, b), [])

    def test_a_shared_slack_thread_counts_as_telling(self) -> None:
        a = out(1, state="sent", to="Marco", subject="Rounding rule", messages=[{"ref": THREAD}])
        b = out(2, to="Priya", subject="Totals row", details=f"follow-up in {THREAD}")
        [hint] = self.hints(a, b)
        self.assertIn("same Slack conversation 1700000001.000001", hint)

    def test_pairs_both_older_than_the_window_are_skipped(self) -> None:
        a = out(1, created=BEFORE, to="Dana", subject="Rotate the leaked export test key today")
        b = out(2, created=BEFORE, to="Dana", subject="Leaked export test key: rotate it")
        self.assertEqual(self.hints(a, b), [])
        self.assertEqual(len(self.hints(a, {**b, "created_at": IN_WINDOW})), 1)

    def test_finished_outreach_is_ignored(self) -> None:
        a = out(1, state="applied", to="Dana", subject="Rotate the leaked export test key today")
        b = out(2, to="Dana", subject="Leaked export test key: rotate it")
        self.assertEqual(self.hints(a, b), [])

    def test_short_subjects_are_never_similar(self) -> None:
        self.assertEqual(similar.containment({"key"}, {"key"}), 0.0)

    def test_recipients_split_lists_and_drop_notes(self) -> None:
        self.assertEqual(
            learn_checks.recipients("Dana (QA lead), Marco and Priya; cc Omar"),
            {"dana", "marco", "priya", "omar"},
        )


class WaitsWithoutReplyTest(unittest.TestCase):
    def test_info_ack_waiting_is_flagged(self) -> None:
        for state in ("draft", "proposed", "sent"):
            with self.subTest(state=state):
                hint = learn_checks.waits_without_reply(out(1, state=state, purpose="info-ack"))
                self.assertIn(f"is an info-ack in {state}", hint)
                self.assertIn("purpose fyi", hint)

    def test_replied_info_ack_and_other_purposes_are_quiet(self) -> None:
        self.assertEqual(learn_checks.waits_without_reply(out(1, state="replied", purpose="info-ack")), "")
        self.assertEqual(learn_checks.waits_without_reply(out(1, state="sent", purpose="fyi")), "")
        self.assertEqual(learn_checks.waits_without_reply(out(1, state="sent")), "")


class SensitiveInChannelTest(unittest.TestCase):
    def test_a_password_question_to_a_channel_is_flagged(self) -> None:
        item = out(1, to="#exporter-dev", subject="Can we drop the shared SFTP password?")
        self.assertIn(
            "security-sensitive subject (password) addressed to a channel",
            learn_checks.sensitive_in_channel(item),
        )

    def test_a_channel_id_counts_as_a_channel(self) -> None:
        item = out(1, to="the team", details="post in C0000000001", subject="Leaked token in the logs")
        self.assertIn("subject (Leaked) addressed to a channel", learn_checks.sensitive_in_channel(item))

    def test_a_direct_message_or_a_plain_subject_is_quiet(self) -> None:
        dm = out(1, to="Dana, DM", subject="Rotate the leaked key", details="#exporter-dev knows nothing")
        plain = out(2, to="#exporter-dev", subject="Which footnote style do we use?")
        nowhere = out(3, to="Dana", subject="Rotate the API key")
        for item in (dm, plain, nowhere):
            with self.subTest(item=item["id"]):
                self.assertEqual(learn_checks.sensitive_in_channel(item), "")


class SecondHandoverTest(unittest.TestCase):
    def test_a_second_window_before_a_report_is_flagged(self) -> None:
        item = handed(("delegated", "exporter-tax", BEFORE), ("delegated", "exporter-pdf", IN_WINDOW))
        [hint] = learn_checks.second_handovers(item, SINCE)
        self.assertIn("was delegated to exporter-pdf", hint)
        self.assertIn("hand-over to exporter-tax had no report", hint)

    def test_a_report_in_between_or_the_same_window_is_quiet(self) -> None:
        reported = handed(
            ("delegated", "exporter-tax", BEFORE),
            ("report", "", BEFORE),
            ("delegated", "exporter-pdf", IN_WINDOW),
        )
        again = handed(("delegated", "exporter-tax", BEFORE), ("delegated", "exporter-tax", IN_WINDOW))
        for item in (reported, again):
            with self.subTest(events=[h["event"] for h in item["history"]]):
                self.assertEqual(learn_checks.second_handovers(item, SINCE), [])

    def test_a_move_is_not_a_second_handover_but_sets_the_window(self) -> None:
        item = handed(
            ("delegated", "exporter-tax", BEFORE),
            ("moved", "exporter-pdf", IN_WINDOW),
            ("delegated", "exporter-web", IN_WINDOW),
        )
        [hint] = learn_checks.second_handovers(item, SINCE)
        self.assertIn("delegated to exporter-web", hint)
        self.assertIn("hand-over to exporter-pdf had no report", hint)

    def test_second_handovers_before_the_window_are_skipped(self) -> None:
        item = handed(("delegated", "exporter-tax", BEFORE), ("delegated", "exporter-pdf", BEFORE))
        self.assertEqual(learn_checks.second_handovers(item, SINCE), [])


def item(item_id: str, kind: str, state: str, **extra: Any) -> dict[str, Any]:
    base = {"id": item_id, "kind": kind, "state": state, "created_at": BEFORE, "updated_at": IN_WINDOW}
    return base | {"fields": {}, "history": []} | extra


NEW_PR = "mypr:acme/pdf-renderer#7"
NEW_PR_URL = "https://github.com/acme/pdf-renderer/pull/7"


class LineageTest(unittest.TestCase):
    def hints(self, *items: dict[str, Any]) -> list[str]:
        return learn_checks.lineage_hints(list(items), SINCE, now=SINCE)

    def test_a_reported_pr_without_an_item_or_a_link(self) -> None:
        report = {"at": IN_WINDOW, "results": [NEW_PR_URL], "summary": "draft PR"}
        task = item("task:2026-01-06/1", "task", "delegated", report=report)
        [missing] = self.hints(task)
        self.assertIn(f"reported PR {NEW_PR_URL}, which has no ledger item ({NEW_PR})", missing)
        [unlinked] = self.hints(task, item(NEW_PR, "my-pr", "awaiting-review"))
        self.assertIn(f"{NEW_PR} came from task:2026-01-06/1", unlinked)
        linked = task | {"links": [{"rel": "produces", "id": NEW_PR, "at": IN_WINDOW}]}
        self.assertEqual(self.hints(linked, item(NEW_PR, "my-pr", "awaiting-review")), [])

    def test_a_reply_closed_after_a_delegate_proposal_without_a_task(self) -> None:
        proposed = {
            "event": "proposed",
            "note": "bosun delegate new-work --id 'out:x/3' --window exporter",
        }
        closed = item(f"out:{PARENT}/3", "outreach", "applied", history=[proposed])
        [hint] = self.hints(closed)
        self.assertIn("no task was spawned from it", hint)
        task = item(
            "task:2026-01-06/1", "task", "accepted", links=[{"rel": "spawned_from", "id": closed["id"]}]
        )
        self.assertEqual(self.hints(closed, task), [])

    def test_tasks_left_proposed_for_days(self) -> None:
        old = item("task:2026-01-01/1", "task", "proposed", created_at="2026-01-01T09:00:00+00:00", title="x")
        fresh = item("task:2026-01-06/1", "task", "proposed", created_at=IN_WINDOW)
        hints = self.hints(old, fresh)
        self.assertEqual(len(hints), 1)
        self.assertIn("task:2026-01-01/1 has been proposed since", hints[0])

    def test_ticket_mismatches(self) -> None:
        pr = item(PARENT, "my-pr", "merged", links=[{"rel": "tracks", "id": "jira:EXP-1"}])
        ticket = item("jira:EXP-1", "ticket", "in-progress", fields={"external_status": "In Progress"})
        [hint] = self.hints(pr, ticket)
        self.assertIn("propose transitioning jira:EXP-1 to done", hint)


if __name__ == "__main__":
    unittest.main()
