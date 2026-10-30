"""Unit tests for lib/delegate.py: task files, rendering and kind checks."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import delegate


TASK = """---
title: Do a thing
kinds: mention
may: do it
never: post
followup: nothing
---
Do the thing.
"""


def args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "id": ["slack:C1/1"],
        "link": None,
        "requester": None,
        "ask": "the ask",
        "details": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class TaskFileTest(unittest.TestCase):
    def write(self, text: str) -> Path:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as tmp:
            tmp.write(text)
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    def test_parses_front_matter_and_body(self) -> None:
        task = delegate.parse_task_file(self.write(TASK))
        self.assertEqual(task["title"], "Do a thing")
        self.assertEqual(task["body"], "Do the thing.")

    def test_rejects_missing_front_matter_fields_and_body(self) -> None:
        with self.assertRaisesRegex(delegate.DelegateError, "missing front matter"):
            delegate.parse_task_file(self.write("no front matter"))
        with self.assertRaisesRegex(delegate.DelegateError, "missing followup"):
            delegate.parse_task_file(self.write(TASK.replace("followup: nothing\n", "")))
        with self.assertRaisesRegex(delegate.DelegateError, "missing body"):
            delegate.parse_task_file(self.write(TASK.replace("Do the thing.\n", "")))

    def test_the_repo_task_files_are_valid(self) -> None:
        types = delegate.load_task_types()
        self.assertEqual(set(types), {"review", "fix-my-pr", "new-work", "investigate"})
        self.assertEqual(len(delegate.list_types(types, markdown=True).splitlines()), len(types))


class SiteOverlayTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.site = Path(tmp.name)
        (self.site / "tasks").mkdir()
        patch = mock.patch.dict("os.environ", {"BOSUN_CONFIG_DIR": str(self.site)})
        patch.start()
        self.addCleanup(patch.stop)

    def test_site_types_are_added_and_replace_built_ins(self) -> None:
        (self.site / "tasks" / "deploy.md").write_text(TASK)
        (self.site / "tasks" / "review.md").write_text(TASK.replace("Do a thing", "Site review"))
        types = delegate.load_task_types()
        self.assertIn("deploy", types)
        self.assertEqual(types["review"]["title"], "Site review")
        self.assertIn("investigate", types)

    def test_site_common_rules_are_added_not_replacing(self) -> None:
        (self.site / "tasks" / "_common.md").write_text("Site rule: say hi.")
        rules = delegate.common_rules()
        self.assertTrue(rules.startswith(delegate.COMMON_FILE.read_text().strip()))
        self.assertTrue(rules.endswith("Site rule: say hi."))


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.types = delegate.load_task_types()

    def test_header_then_blocks_then_shared_rules(self) -> None:
        text = delegate.render(
            args(link="https://x", requester="Priya", details="my point"),
            [self.types["investigate"], self.types["new-work"]],
        )
        lines = text.splitlines()
        self.assertEqual(
            lines[:5],
            [
                "Ledger id: slack:C1/1",
                "Link: https://x",
                "Requester: Priya",
                "Ask: the ask",
                "Details: my point",
            ],
        )
        self.assertLess(text.index("Investigate"), text.index("New work"))
        self.assertTrue(text.rstrip().endswith(delegate.COMMON_FILE.read_text().strip()))

    def test_several_ids_and_optional_fields(self) -> None:
        text = delegate.render(args(id=["slack:C1/1", "slack:C1/2"]), [self.types["investigate"]])
        self.assertTrue(text.startswith("Ledger ids: slack:C1/1, slack:C1/2\nAsk: the ask"))


class HandoverIdsTest(unittest.TestCase):
    """handover_ids reads back the ids from every hand-over this module renders (cost per task)."""

    def setUp(self) -> None:
        self.types = delegate.load_task_types()

    def test_round_trips(self) -> None:
        task = [self.types["investigate"]]
        cases = {
            "one id": (delegate.render(args(), task), ["slack:C1/1"]),
            "several": (
                delegate.render(args(id=["slack:C1/1", "slack:C1/2"]), task),
                ["slack:C1/1", "slack:C1/2"],
            ),
            "in a subagent": (
                delegate.render(args(), task, preamble=delegate.subagent_preamble("sonnet", "high")),
                ["slack:C1/1"],
            ),
            "an update": (
                delegate.render_update(
                    "task:2026-03-03/1", {"task": "new-work", "at": "2026-03-03T09:00:00Z"}, "more"
                ),
                ["task:2026-03-03/1"],
            ),
        }
        for name, (text, expected) in cases.items():
            with self.subTest(name):
                self.assertTrue(text.startswith(delegate.HANDOVER_OPENINGS))
                self.assertEqual(delegate.handover_ids(text), expected)

    def test_other_prompts_name_no_ids(self) -> None:
        for text in ("please fix the footer", "see\nLedger id: task:2026-03-03/1", ""):
            with self.subTest(text):
                self.assertEqual(delegate.handover_ids(text), [])


class CheckIdsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.types = delegate.load_task_types()

    @staticmethod
    def item(kind: str, state: str = "new", item_id: str = "slack:C1/1") -> dict[str, str]:
        return {"id": item_id, "kind": kind, "state": state}

    def test_kind_must_match_the_type(self) -> None:
        with mock.patch.object(delegate, "ledger_item", return_value=self.item("mention")):
            with self.assertRaisesRegex(delegate.DelegateError, "take review-request items"):
                delegate.check_ids(["slack:C1/1"], [self.types["review"]])
            delegate.check_ids(["slack:C1/1"], [self.types["investigate"]])

    def test_outreach_is_never_the_work_item(self) -> None:
        outreach = self.item("outreach", "replied", "out:mypr:acme/exporter#102/3")
        with (
            mock.patch.object(delegate, "ledger_item", return_value=outreach),
            self.assertRaisesRegex(delegate.DelegateError, r"is outreach, not a work item.*ledger spawn"),
        ):
            delegate.check_ids([outreach["id"]], [self.types["new-work"]])

    def test_a_task_is_handed_over_only_once_accepted(self) -> None:
        for state, refused in (
            ("proposed", True),
            ("declined", True),
            ("accepted", False),
            ("delegated", False),
        ):
            task = self.item("task", state, "task:2026-10-16/1")
            with self.subTest(state=state), mock.patch.object(delegate, "ledger_item", return_value=task):
                if refused:
                    with self.assertRaisesRegex(delegate.DelegateError, "only an accepted task"):
                        delegate.check_ids([task["id"]], [self.types["new-work"]])
                else:
                    delegate.check_ids([task["id"]], [self.types["new-work"]])

    def test_missing_items_are_rejected(self) -> None:
        missing = mock.Mock(returncode=1, stdout="")
        with (
            mock.patch("delegate.subprocess.run", return_value=missing),
            self.assertRaisesRegex(delegate.DelegateError, "upsert it first"),
        ):
            delegate.check_ids(["slack:C1/404"], [self.types["investigate"]])


class ModelEffortTest(unittest.TestCase):
    def setUp(self) -> None:
        self.types = delegate.load_task_types()

    def test_type_defaults(self) -> None:
        self.assertEqual(
            delegate.task_model_effort([self.types["review"]], None, None), ("session", "medium")
        )
        self.assertEqual(
            delegate.task_model_effort([self.types["fix-my-pr"]], None, None), ("session", "session")
        )

    def test_combined_types_run_in_the_session_if_one_needs_it(self) -> None:
        both = [self.types["investigate"], self.types["new-work"]]
        self.assertEqual(delegate.task_model_effort(both, None, None), ("session", "session"))

    def test_overrides_win(self) -> None:
        self.assertEqual(
            delegate.task_model_effort([self.types["review"]], "sonnet", "high"), ("sonnet", "high")
        )

    def test_subagent_preamble(self) -> None:
        text = delegate.subagent_preamble("session", "medium")
        self.assertIn('one Agent tool call with effort "medium"', text)
        self.assertNotIn("model", text.split("passing")[0])
        self.assertIn('effort "high" and model "sonnet"', delegate.subagent_preamble("sonnet", "high"))

    def run_main(self, argv: list[str], harness: str = "claude") -> tuple[mock.MagicMock, str]:
        with (
            mock.patch.object(delegate, "check_ids"),
            mock.patch.object(delegate, "check_not_awaiting"),
            mock.patch.object(delegate, "window_harness", return_value=harness),
            mock.patch("delegate.subprocess.run", return_value=mock.Mock(returncode=0, stdout="")) as run,
            mock.patch("sys.stdout", io.StringIO()) as out,
        ):
            self.assertEqual(delegate.main(argv), 0)
        return run, out.getvalue()

    def test_a_window_on_another_agent_gets_the_task_itself(self) -> None:
        run, _ = self.run_main(
            ["review", "--id", "review:a/b#1", "--ask", "a", "--window", "w"], harness="codex"
        )
        self.assertTrue(run.call_args_list[0].args[0][3].startswith("Ledger id: review:a/b#1"))

    def test_which_agent_a_window_runs(self) -> None:
        sessions = json.dumps(
            [
                {"name": "running", "harness": "claude", "agent": {"harness": "codex"}},
                {"name": "configured", "harness": "codex", "agent": None},
            ]
        )
        done = subprocess.CompletedProcess([], 0, sessions, "")
        with mock.patch("subprocess.run", return_value=done):
            self.assertEqual(delegate.window_harness("running"), "codex")
            self.assertEqual(delegate.window_harness("configured"), "codex")
            self.assertEqual(delegate.window_harness("unknown"), "claude")
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertEqual(delegate.window_harness("running"), "claude")

    def test_a_running_window_gets_the_subagent_preamble(self) -> None:
        run, _ = self.run_main(["review", "--id", "review:a/b#1", "--ask", "a", "--window", "w"])
        sent = run.call_args_list[0].args[0]
        self.assertEqual(sent[:3], [delegate.BOSUN, "send", "w"])
        self.assertTrue(sent[3].startswith("Run this task in a subagent"))

    def test_session_tasks_go_straight_to_the_session(self) -> None:
        run, _ = self.run_main(["fix-my-pr", "--id", "mypr:a/b#1", "--ask", "a", "--window", "w"])
        self.assertTrue(run.call_args_list[0].args[0][3].startswith("Ledger id: mypr:a/b#1"))

    def test_a_new_window_starts_at_the_task_effort(self) -> None:
        run, _ = self.run_main(["review", "--id", "review:a/b#1", "--ask", "a", "--new", "w", "/d"])
        sent = run.call_args_list[0].args[0]
        self.assertEqual(sent[:5], [delegate.BOSUN, "send", "--new", "--effort", "medium"])
        self.assertTrue(sent[-1].startswith("Ledger id:"))

    def test_print_shows_what_a_running_window_would_get(self) -> None:
        _, out = self.run_main(
            ["review", "--id", "review:a/b#1", "--ask", "a", "--effort", "high", "--print"]
        )
        self.assertTrue(
            out.startswith('Run this task in a subagent: make one Agent tool call with effort "high"')
        )

    def test_the_subagent_preamble_says_what_a_subagent_cannot_do(self) -> None:
        preamble = delegate.subagent_preamble("sonnet", "high")
        self.assertIn("without worktree isolation", preamble)
        self.assertIn("cannot call ReadNotifications", preamble)
        self.assertIn(
            "returns the content of any findings or report file as text, for you to write", preamble
        )
        self.assertTrue(preamble.endswith("\n\n---"))

    def test_the_subagent_preamble_keeps_its_waits_in_the_foreground(self) -> None:
        # An idle subagent wakes the parent session each time; a foreground wait does not.
        preamble = delegate.subagent_preamble("sonnet", "high")
        self.assertIn("in the foreground", preamble)
        self.assertIn("timeout 540 bash -c 'until <check>; do sleep 60; done'", preamble)
        self.assertIn("never to end its turn while it waits", preamble)

    def test_the_shared_rules_tell_subagents_to_wait_in_the_foreground(self) -> None:
        rules = delegate.COMMON_FILE.read_text()
        self.assertIn("a subagent waits in the foreground", rules)
        self.assertIn("never ends its turn while waiting", rules)


DELEGATION = {
    "id": "slack:C1/1",
    "state": "new",
    "window": "exports",
    "task": "new-work",
    "at": "T0",
    "updates": 0,
}


def completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> mock.Mock:
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


class RecordDelegationTest(unittest.TestCase):
    def run_main(self, argv: list[str], record: mock.Mock) -> tuple[mock.Mock, str]:
        with (
            mock.patch.object(delegate, "check_ids"),
            mock.patch.object(delegate, "check_not_awaiting"),
            mock.patch.object(delegate, "window_harness", return_value="claude"),
            mock.patch.object(delegate, "send", return_value=0) as send,
            mock.patch.object(delegate, "ledger", record),
            mock.patch("sys.stderr", io.StringIO()) as err,
        ):
            self.assertEqual(delegate.main(argv), 0)
        return send, err.getvalue()

    def test_each_item_records_the_window(self) -> None:
        record = mock.Mock(return_value=completed())
        argv = [
            "investigate,new-work",
            "--id",
            "slack:C1/1",
            "--id",
            "slack:C1/2",
            "--ask",
            "a",
            "--window",
            "w",
        ]
        self.run_main(argv, record)
        self.assertEqual(
            [c.args for c in record.call_args_list],
            [
                ("record-delegation", item, "--window", "w", "--task", "investigate,new-work", "--ask", "a")
                for item in ("slack:C1/1", "slack:C1/2")
            ],
        )

    def test_new_and_start_windows_are_recorded_too(self) -> None:
        for target, window in ((["--new", "fresh", "/d"], "fresh"), (["--start", "idle"], "idle")):
            record = mock.Mock(return_value=completed())
            self.run_main(["investigate", "--id", "slack:C1/1", "--ask", "a", *target], record)
            with self.subTest(target=target):
                self.assertEqual(record.call_args.args[3], window)

    def test_a_ledger_failure_after_delivery_only_warns(self) -> None:
        record = mock.Mock(return_value=completed(1, stderr="ledger: locked"))
        _, err = self.run_main(["investigate", "--id", "slack:C1/1", "--ask", "a", "--window", "w"], record)
        self.assertIn("delivered, but the ledger did not record the window for slack:C1/1", err)

    def test_nothing_is_recorded_for_print_or_a_failed_send(self) -> None:
        record = mock.Mock(return_value=completed())
        with mock.patch("sys.stdout", io.StringIO()):
            self.run_main(["investigate", "--id", "slack:C1/1", "--ask", "a", "--print"], record)
        with (
            mock.patch.object(delegate, "check_ids"),
            mock.patch.object(delegate, "check_not_awaiting"),
            mock.patch.object(delegate, "send", return_value=1),
            mock.patch.object(delegate, "ledger", record),
        ):
            self.assertEqual(
                delegate.main(["investigate", "--id", "slack:C1/1", "--ask", "a", "--window", "w"]), 1
            )
        record.assert_not_called()


class UpdateTest(unittest.TestCase):
    def ledger_answers(self, delegation: mock.Mock) -> mock.Mock:
        """A fake `bosun ledger`: <delegation> for `delegation`, success for the rest."""
        return mock.Mock(
            side_effect=lambda *a: delegation if a[0] == "delegation" else completed(stdout="ok: update 1")
        )

    def update(
        self, argv: list[str], ledger: mock.Mock, sent: int = 0, stdin: str = ""
    ) -> tuple[mock.Mock, str]:
        with (
            mock.patch.object(delegate, "ledger", ledger),
            mock.patch.object(delegate, "send", return_value=sent) as send,
            mock.patch("sys.stdin", io.StringIO(stdin)),
            mock.patch("sys.stdout", io.StringIO()) as out,
        ):
            self.assertEqual(delegate.main(["--update", "slack:C1/1", *argv]), 0)
        return send, out.getvalue()

    def test_goes_to_the_recorded_window_framed_with_the_id(self) -> None:
        ledger = self.ledger_answers(completed(stdout=json.dumps(DELEGATION)))
        send, _ = self.update(["--details", "-"], ledger, stdin="match the ERP connector fields\n")
        window, text = send.call_args.args
        self.assertEqual(window, "exports")
        self.assertTrue(
            text.startswith(
                "Update to ledger item slack:C1/1 (the new-work task handed to this window at T0)"
            )
        )
        self.assertIn("\n\nmatch the ERP connector fields\n\n", text)
        self.assertIn("pass this update on to it", text)
        self.assertEqual(
            ledger.call_args.args, ("record-update", "slack:C1/1", "--text", "match the ERP connector fields")
        )

    def test_print_shows_the_update_without_sending_or_recording(self) -> None:
        ledger = self.ledger_answers(completed(stdout=json.dumps(DELEGATION)))
        send, out = self.update(["--details", "more", "--print"], ledger)
        send.assert_not_called()
        self.assertTrue(out.startswith("Update to ledger item slack:C1/1"))
        self.assertEqual(ledger.call_count, 1)

    def test_the_ledger_refusal_is_passed_on(self) -> None:
        refused = completed(1, stderr="ledger: slack:C1/1 is done; a finished item takes no updates\n")
        with self.assertRaisesRegex(delegate.DelegateError, "^slack:C1/1 is done; a finished item"):
            self.update(["--details", "x"], self.ledger_answers(refused))

    def test_an_undelivered_update_is_not_recorded(self) -> None:
        ledger = self.ledger_answers(completed(stdout=json.dumps(DELEGATION)))
        with self.assertRaisesRegex(delegate.DelegateError, "handed to window exports at T0"):
            self.update(["--details", "x"], ledger, sent=1)
        self.assertEqual([c.args[0] for c in ledger.call_args_list], ["delegation"])

    def test_takes_only_details(self) -> None:
        for argv, message in [
            (["--details", "x", "--again"], "takes only --details"),
            (["--details", "x", "--start", "w"], "takes only --details"),
            (["--details", "x", "--id", "slack:C1/2"], "takes only --details"),
            (["--details", "x", "--ask", "a"], "takes only --details"),
            ([], "needs the update text"),
        ]:
            with self.subTest(argv=argv), self.assertRaisesRegex(delegate.DelegateError, message):
                delegate.main(["--update", "slack:C1/1", *argv])

    def test_empty_stdin_is_refused(self) -> None:
        ledger = self.ledger_answers(completed(stdout=json.dumps(DELEGATION)))
        with self.assertRaisesRegex(delegate.DelegateError, "read nothing from stdin"):
            self.update(["--details", "-"], ledger, stdin="  \n")


REPLIED: dict[str, Any] = {
    "id": "out:slack:C1/1/1",
    "kind": "outreach",
    "state": "replied",
    "fields": {
        "parent": "slack:C1/1",
        "channel": "slack",
        "purpose": "decision",
        "to": "the ERP connector team",
        "subject": "new field or match their contract?",
        "options": ["a) new field", "b) match the contract"],
        "answer": "b: match the contract",
        "reply_ref": "https://acme.slack.com/archives/C0000000001/p1700000200000200",
    },
}


class ReplyHandoverTest(unittest.TestCase):
    """`delegate --update <out-id>` hands an outreach reply to the window holding its parent."""

    def ledger_answers(self, item: dict[str, Any], delegation: mock.Mock) -> mock.Mock:
        def answer(*arguments: str) -> mock.Mock:
            if arguments[0] == "show":
                return completed(stdout=json.dumps(item))
            if arguments[0] == "delegation":
                return delegation
            return completed(stdout=f"ok: {arguments[0]}")

        return mock.Mock(side_effect=answer)

    def update(self, argv: list[str], ledger: mock.Mock) -> tuple[int, mock.Mock, str]:
        with (
            mock.patch.object(delegate, "ledger", ledger),
            mock.patch.object(delegate, "send", return_value=0) as send,
            mock.patch("sys.stdout", io.StringIO()) as out,
        ):
            code = delegate.main(["--update", REPLIED["id"], *argv])
        return code, send, out.getvalue()

    def test_the_reply_goes_to_the_parents_window_and_the_outreach_is_applied(self) -> None:
        ledger = self.ledger_answers(REPLIED, completed(stdout=json.dumps(DELEGATION)))
        code, send, _ = self.update([], ledger)
        self.assertEqual(code, 0)
        window, text = send.call_args.args
        self.assertEqual(window, "exports")
        self.assertTrue(text.startswith("Update to ledger item slack:C1/1 (the new-work task"))
        self.assertIn("Reply to out:slack:C1/1/1 (decision by slack to the ERP connector team", text)
        self.assertIn("Options were: a) new field | b) match the contract", text)
        self.assertIn("Reply: b: match the contract (reference: https://", text)
        calls = [c.args for c in ledger.call_args_list]
        self.assertEqual(calls[2][:2], ("record-update", "slack:C1/1"))
        self.assertEqual(calls[3][:3], ("transition", "out:slack:C1/1/1", "applied"))
        self.assertIn("reply handed to window exports", calls[3][4])

    def test_details_are_added_and_print_only_shows_it(self) -> None:
        ledger = self.ledger_answers(REPLIED, completed(stdout=json.dumps(DELEGATION)))
        _, send, out = self.update(["--details", "start with the mapper", "--print"], ledger)
        send.assert_not_called()
        self.assertIn("Continue the work on slack:C1/1 that waited on it.\n\nstart with the mapper", out)
        self.assertEqual([c.args[0] for c in ledger.call_args_list], ["show", "delegation"])

    def test_refusals(self) -> None:
        not_delegated = completed(1, stderr="ledger: slack:C1/1 was never handed to a repo session\n")
        for item, delegation, argv, message in [
            ({**REPLIED, "state": "sent"}, None, [], "is sent; only outreach with a reply"),
            (
                {**REPLIED, "fields": {**REPLIED["fields"], "parent": None}},
                None,
                [],
                "belongs to no work item",
            ),
            (REPLIED, not_delegated, [], "no window holds slack:C1/1 \\(slack:C1/1 was never handed"),
            (REPLIED, None, ["--window", "w"], "goes to the window holding its parent"),
        ]:
            ledger = self.ledger_answers(item, delegation or completed(stdout=json.dumps(DELEGATION)))
            with self.subTest(message=message), self.assertRaisesRegex(delegate.DelegateError, message):
                self.update(argv, ledger)

    def test_an_undelivered_reply_changes_nothing(self) -> None:
        ledger = self.ledger_answers(REPLIED, completed(stdout=json.dumps(DELEGATION)))
        with (
            mock.patch.object(delegate, "ledger", ledger),
            mock.patch.object(delegate, "send", return_value=1),
            self.assertRaisesRegex(delegate.DelegateError, "stays replied"),
        ):
            delegate.main(["--update", REPLIED["id"]])
        self.assertEqual([c.args[0] for c in ledger.call_args_list], ["show", "delegation"])


class RedelegateTest(unittest.TestCase):
    """`delegate` refuses an item whose last hand-over awaits a report, unless --again."""

    ARGV = ("investigate", "--id", "slack:C1/1", "--ask", "a", "--window", "w")

    def main(self, argv: list[str], delegation: mock.Mock) -> tuple[int, mock.Mock, mock.Mock]:
        record = mock.Mock(side_effect=lambda *a: delegation if a[0] == "delegation" else completed())
        with (
            mock.patch.object(delegate, "check_ids"),
            mock.patch.object(delegate, "ledger", record),
            mock.patch.object(delegate, "send", return_value=0) as send,
            mock.patch("sys.stdout", io.StringIO()),
        ):
            return delegate.main(argv), send, record

    def test_refused_while_awaiting_a_report(self) -> None:
        awaiting = completed(stdout=json.dumps({**DELEGATION, "awaiting_report": True}))
        with self.assertRaises(delegate.DelegateError) as caught:
            self.main(list(self.ARGV), awaiting)
        message = str(caught.exception)
        for text in (
            "handed to window exports at T0 (new-work)",
            "--update 'slack:C1/1'",
            "--window <window>",
            "--again",
        ):
            self.assertIn(text, message)

    def test_again_hands_it_over(self) -> None:
        awaiting = completed(stdout=json.dumps({**DELEGATION, "awaiting_report": True}))
        code, send, record = self.main([*self.ARGV, "--again"], awaiting)
        self.assertEqual(code, 0)
        send.assert_called_once()
        self.assertNotIn("delegation", [c.args[0] for c in record.call_args_list])

    def test_allowed_after_a_report_or_without_a_delegation(self) -> None:
        reported = completed(stdout=json.dumps({**DELEGATION, "awaiting_report": False}))
        never = completed(1, stderr="ledger: slack:C1/1 was never handed to a repo session")
        for delegation in (reported, never):
            with self.subTest(delegation=delegation.stdout or delegation.stderr):
                code, send, _ = self.main(list(self.ARGV), delegation)
                self.assertEqual(code, 0)
                send.assert_called_once()

    def test_print_does_not_check(self) -> None:
        awaiting = completed(stdout=json.dumps({**DELEGATION, "awaiting_report": True}))
        code, send, _ = self.main([*self.ARGV[:-2], "--print"], awaiting)
        self.assertEqual(code, 0)
        send.assert_not_called()

    def test_the_task_header_is_recorded(self) -> None:
        argv = [*self.ARGV, "--link", "https://x/1", "--requester", "Dana", "--details", "focus"]
        _, _, record = self.main(argv, completed(1))
        self.assertEqual(
            record.call_args.args[-6:],
            ("--link", "https://x/1", "--requester", "Dana", "--details", "focus"),
        )


MOVABLE = {**DELEGATION, "task": "investigate", "ask": "why?", "link": "https://x/1", "awaiting_report": True}


class MoveTest(unittest.TestCase):
    """`delegate --update <id> --window <new>` moves the item."""

    def move(self, argv: list[str], sends: list[int]) -> tuple[mock.Mock, mock.Mock, str, str]:
        record = mock.Mock(
            side_effect=lambda *a: (
                completed(stdout=json.dumps(MOVABLE))
                if a[0] == "delegation"
                else completed(stdout="ok: moved")
            )
        )
        with (
            mock.patch.object(delegate, "ledger", record),
            mock.patch.object(delegate, "send", side_effect=sends) as send,
            mock.patch("sys.stdout", io.StringIO()) as out,
            mock.patch("sys.stderr", io.StringIO()) as err,
        ):
            self.assertEqual(
                delegate.main(["--update", "slack:C1/1", "--details", "the old window is stuck", *argv]), 0
            )
        return send, record, out.getvalue(), err.getvalue()

    def test_the_whole_task_goes_to_the_new_window_and_the_old_one_is_told(self) -> None:
        send, record, _, err = self.move(["--window", "fresh"], sends=[0, 0])
        (window, text), (flag, item, old, notice) = (c.args for c in send.call_args_list)
        self.assertEqual(window, "fresh")
        self.assertTrue(text.startswith("Run this task in a subagent"))  # investigate runs at medium effort
        self.assertIn("Ledger item slack:C1/1 moves to this window from window exports", text)
        self.assertIn("Update: the old window is stuck", text)
        self.assertIn("Ledger id: slack:C1/1\nLink: https://x/1\nAsk: why?", text)
        self.assertIn("Investigate (no code change)", text)
        self.assertEqual((flag, item, old), ("--id", "slack:C1/1", "exports"))
        self.assertIn("has moved to window fresh. Stop working on it here", notice)
        self.assertEqual(
            record.call_args.args,
            (
                "record-move",
                "slack:C1/1",
                "--window",
                "fresh",
                "--text",
                "the old window is stuck",
                "--notified",
            ),
        )
        self.assertEqual(err, "")

    def test_an_old_window_that_cannot_be_told_is_recorded_and_reported(self) -> None:
        _, record, _, err = self.move(["--window", "fresh"], sends=[0, 1])
        self.assertNotIn("--notified", record.call_args.args)
        self.assertIn("window exports was not told to stop", err)

    def test_nothing_changes_if_the_new_window_does_not_take_it(self) -> None:
        with self.assertRaisesRegex(delegate.DelegateError, "not delivered to fresh .*stays with exports"):
            self.move(["--window", "fresh"], sends=[1])

    def test_the_same_window_is_refused(self) -> None:
        with self.assertRaisesRegex(delegate.DelegateError, "already delegated to exports; drop --window"):
            self.move(["--window", "exports"], sends=[])


class MainTest(unittest.TestCase):
    def test_details_from_stdin(self) -> None:
        with (
            mock.patch.object(delegate, "check_ids"),
            mock.patch.object(delegate, "check_not_awaiting"),
            mock.patch("sys.stdin", io.StringIO("Dana's $(point)\n")),
            mock.patch("sys.stdout", io.StringIO()) as out,
        ):
            self.assertEqual(
                delegate.main(
                    ["investigate", "--id", "slack:C1/1", "--ask", "a", "--details", "-", "--print"]
                ),
                0,
            )
        self.assertIn("Details: Dana's $(point)", out.getvalue())

    def test_argument_errors(self) -> None:
        for argv, message in [
            (["nope", "--id", "x", "--ask", "a", "--print"], "unknown task type"),
            (["investigate", "--ask", "a", "--print"], "at least one --id"),
            (["investigate", "--id", "x", "--ask", "a\nb", "--print"], "one line"),
            (["investigate", "--id", "x", "--ask", "a" * 301, "--print"], "one line"),
            (["investigate", "--id", "x", "--ask", "a"], "say where"),
        ]:
            with self.subTest(argv=argv), self.assertRaisesRegex(delegate.DelegateError, message):
                delegate.main(argv)


if __name__ == "__main__":
    unittest.main()
