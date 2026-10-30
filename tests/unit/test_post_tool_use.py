"""Unit tests for what happens after an approved call (docs/design/approvals.md, section 4): the
guard's --post-tool-use hook settling the proposal the call used (success, failure, outreach, the
wrong call or item, idempotence, the flag off), where a proposal stands for me
(approvals.proposal_status, `ledger list`, ledger.md), and the one-time cleanup `ledger
resolve-carried-out`. Fictional data: acme's invoice exporter."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import approvals
import claude_state
import guard
import ledger
from test_approvals import (
    CHANNEL,
    OTHER_PR,
    PR,
    QUOTE,
    REVIEW_REQUEST,
    SEND,
    SENT_REF,
    TEXT,
    THREAD,
    TOUCH,
    ApprovalsTestCase,
    StandingTestCase,
    run,
    stamp,
    typed,
)


SESSION = "00000000-0000-0000-0000-000000000001"
OTHER_SESSION = "00000000-0000-0000-0000-000000000002"
COMMENT_TOOL = "mcp__acme-tracker__jira_add_comment"
COMMENT_ARGS = {"issue_key": "EXP-12", "body": "Merged and on staging."}
TICKET_ITEM = "jira:EXP-12"
OTHER_COMMAND = "touch /tmp/acme-other.txt"
BASH_OK = {"stdout": "", "stderr": "", "interrupted": False, "isImage": False, "noOutputExpected": True}
MCP_OK = [{"type": "text", "text": '{"success": true, "comment": {"id": "10001"}}'}]


class PostToolUseTestCase(ApprovalsTestCase):
    """Proposals approved in chat and used by the PermissionRequest hook, then the call's result."""

    def event(self, tool: str, **tool_input: Any) -> dict[str, Any]:
        return {**super().event(tool, **tool_input), "session_id": SESSION}

    def approve(self, item_id: str, *spec: str) -> None:
        run("propose", item_id, *spec)
        self.say(typed(QUOTE, datetime.now(UTC)))
        run("approve", item_id, "--quote", QUOTE)

    def use(self, item_id: str = PR, command: str = TOUCH) -> dict[str, Any]:
        """Approve <command> on <item_id> and let it through; the call's event."""
        self.approve(item_id, "--run", command)
        call = self.event("Bash", command=command, description="touch it")
        self.assertTrue(self.allowed(call))
        return call

    def post(self, call: dict[str, Any], hook: str = approvals.POST_TOOL_USE, **extra: Any) -> None:
        response: dict[str, Any] = {"tool_response": BASH_OK if call["tool_name"] == "Bash" else MCP_OK}
        if hook == approvals.POST_TOOL_USE_FAILURE:
            response = {"error": "Exit code 1", "is_interrupt": False}
        event = {**call, "hook_event_name": hook, "tool_use_id": "toolu_01", **response, **extra}
        self.assertIsNone(self.respond("--post-tool-use", event))

    def history(self, item_id: str = PR) -> list[str]:
        return [entry["event"] for entry in self.item(item_id)["history"]]

    def ledger_text(self) -> str:
        return ledger.STORE.ledger_file.read_text()


class SettleTest(PostToolUseTestCase):
    def test_a_successful_call_closes_its_proposal(self) -> None:
        call = self.use()
        grant = self.item(PR)["proposal"]["approval"]
        self.assertEqual(grant["call"], approvals.call_hash(approvals.Call("Bash", call["tool_input"])))
        self.assertEqual((grant["session"], grant["transcript"]), (SESSION, str(self.transcript)))
        self.post(call)
        item = self.item(PR)
        self.assertIsNone(item["proposal"])
        last = item["history"][-1]
        self.assertEqual(
            (last["event"], last["note"]), ("proposal-done", "carried out by the approved call (Bash)")
        )
        self.assertEqual(last["tool_use_id"], "toolu_01")

    def test_it_is_idempotent(self) -> None:
        call = self.use()
        self.post(call)
        before = self.ledger_text()
        self.post(call)
        self.post(call, approvals.POST_TOOL_USE_FAILURE)
        self.assertEqual(self.ledger_text(), before)

    def test_a_failed_bash_call_leaves_it_open_and_needing_a_new_approval(self) -> None:
        call = self.use()
        self.post(call, approvals.POST_TOOL_USE_FAILURE)
        proposal = self.item(PR)["proposal"]
        self.assertEqual(proposal["approval"]["failed"]["reason"], "Exit code 1")
        last = self.item(PR)["history"][-1]
        self.assertEqual(last["event"], ledger.APPROVAL_FAILED)
        self.assertIn("Exit code 1; approvals are single-use: propose it again", last["note"])
        self.assertEqual(approvals.proposal_status(proposal), approvals.NEEDS_YOU)
        self.assertEqual(approvals.proposal_label(proposal), approvals.FAILED_LABEL)
        # settled: a later success of the same call (allowed at a prompt by hand) changes nothing
        before = self.ledger_text()
        self.post(call)
        self.assertEqual(self.ledger_text(), before)
        # and the way on is a new proposal and approval
        with self.assertRaisesRegex(ledger.LedgerError, "already used; propose it again"):
            run("approve", PR, "--quote", QUOTE)
        self.assertIn("ok: proposal pending", run("propose", PR, "--run", TOUCH))
        self.assertEqual(approvals.proposal_status(self.item(PR)["proposal"]), approvals.NEEDS_YOU)

    def test_failures_a_post_tool_use_response_reports(self) -> None:
        for response, reason in (
            ({**BASH_OK, "interrupted": True}, "the call was interrupted"),
            ({"is_error": True, "error": "boom"}, "boom"),
            ({"stdout": "", "exit_code": 2}, "exit code 2"),
        ):
            with self.subTest(response=response):
                call = self.use()
                self.post(call, tool_response=response)
                self.assertEqual(self.item(PR)["proposal"]["approval"]["failed"]["reason"], reason)
                run("resolve", PR, "withdrawn", "--note", "next case")

    def test_a_background_call_settles_nothing(self) -> None:
        call = self.use()
        before = self.ledger_text()
        self.post(call, tool_response={**BASH_OK, "backgroundTaskId": "b1"})
        self.assertEqual(self.ledger_text(), before)

    def test_mcp_success_and_is_error(self) -> None:
        run("upsert", TICKET_ITEM, "--kind", "ticket")
        self.approve(TICKET_ITEM, "--tool", COMMENT_TOOL, "--args-json", json.dumps(COMMENT_ARGS))
        call = self.event(COMMENT_TOOL, **COMMENT_ARGS)
        self.assertTrue(self.allowed(call))
        self.post(call)
        self.assertIsNone(self.item(TICKET_ITEM)["proposal"])
        self.assertIn("(mcp__acme-tracker__jira_add_comment)", self.item(TICKET_ITEM)["history"][-1]["note"])
        # an MCP result with isError arrives as PostToolUseFailure with its text
        self.approve(TICKET_ITEM, "--tool", COMMENT_TOOL, "--args-json", json.dumps(COMMENT_ARGS))
        self.assertTrue(self.allowed(call))
        self.post(call, approvals.POST_TOOL_USE_FAILURE, error="Error: comment not found")
        failed = self.item(TICKET_ITEM)["proposal"]["approval"]["failed"]
        self.assertEqual(failed["reason"], "Error: comment not found")

    def test_only_the_item_whose_approval_the_call_used(self) -> None:
        run("upsert", OTHER_PR, "--kind", "my-pr")
        call = self.use()
        self.approve(OTHER_PR, "--run", OTHER_COMMAND)  # approved, not used
        other_before = self.item(OTHER_PR)
        self.post(self.event("Bash", command=OTHER_COMMAND))  # approved but never let through
        self.post(self.event("Bash", command=f"{TOUCH} && echo x"))  # a different call
        self.assertIsNotNone(self.item(PR)["proposal"])
        self.post(call)
        self.assertIsNone(self.item(PR)["proposal"])
        self.assertEqual(self.item(OTHER_PR), other_before)

    def test_another_session_or_a_late_result_is_not_this_call(self) -> None:
        call = self.use()
        self.post({**call, "session_id": OTHER_SESSION})
        self.assertIsNotNone(self.item(PR)["proposal"])
        items = json.loads(self.ledger_text())["items"]
        used = datetime.fromisoformat(items[PR]["proposal"]["approval"]["used_at"])
        found = approvals.Call("Bash", call["tool_input"])
        context = approvals.CallContext(SESSION)
        late = used + approvals.CALL_WINDOW + timedelta(seconds=1)
        self.assertIsNotNone(approvals.carried_out_by(items, found, context, used))
        self.assertIsNone(approvals.carried_out_by(items, found, context, late))
        self.assertIsNone(approvals.carried_out_by(items, found, context, used - timedelta(minutes=1)))

    def test_without_session_or_tool_use_id_the_hash_and_time_decide(self) -> None:
        call = self.use()
        bare = {key: value for key, value in call.items() if key != "session_id"}
        self.post(bare, tool_use_id="")
        self.assertIsNone(self.item(PR)["proposal"])

    def test_a_recorded_tool_use_id_must_match(self) -> None:
        grant = {"call": "x", "used_at": "2026-10-21T10:00:00+00:00", "tool_use_id": "toolu_A"}
        call = approvals.Call("Bash", {"command": "y"})
        moment = datetime(2026, 10, 21, 10, 0, 5, tzinfo=UTC)
        with mock.patch.object(approvals, "call_hash", return_value="x"):
            self.assertFalse(
                approvals.grant_matches(grant, call, approvals.CallContext(tool_use_id="toolu_B"), moment)
            )
            self.assertTrue(
                approvals.grant_matches(grant, call, approvals.CallContext(tool_use_id="toolu_A"), moment)
            )
            self.assertTrue(approvals.grant_matches(grant, call, approvals.CallContext(), moment))

    def test_flag_off_and_ungated_calls_do_nothing(self) -> None:
        call = self.use()
        before = self.ledger_text()
        self.assertIsNone(
            self.respond(
                "--post-tool-use", {**call, "hook_event_name": "PostToolUse"}, BOSUN_APPROVALS_ENABLED="0"
            )
        )
        self.assertEqual(self.ledger_text(), before)
        ls = {**self.event("Bash", command="ls"), "hook_event_name": "PostToolUse", "tool_response": BASH_OK}
        self.assertEqual(guard.after_call(ls, guard.ApprovalSettings.from_env(self.env)), "")
        # another hook event in this mode is ignored
        self.assertEqual(
            guard.after_call(
                {**call, "hook_event_name": "PreToolUse"}, guard.ApprovalSettings.from_env(self.env)
            ),
            "",
        )
        self.assertEqual(self.ledger_text(), before)

    def test_the_hook_prints_nothing_and_exits_0(self) -> None:
        call = self.use()
        for stdin in (
            json.dumps({**call, "hook_event_name": "PostToolUse", "tool_response": BASH_OK}),
            "not json",
        ):
            out = io.StringIO()
            with mock.patch("sys.stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out):
                self.assertEqual(guard.main(["--post-tool-use"], self.env), 0)
            self.assertEqual(out.getvalue(), "")
        self.assertIsNone(self.item(PR)["proposal"])

    def test_a_learning_proposal_is_never_closed_here(self) -> None:
        run("upsert", "learn:2026-10-21/1", "--kind", "learning")
        call = self.use("learn:2026-10-21/1")
        self.post(call)
        self.assertIsNotNone(self.item("learn:2026-10-21/1")["proposal"])
        self.assertEqual(self.history("learn:2026-10-21/1")[-1], ledger.APPROVAL_CARRIED_OUT)


class OutreachTest(PostToolUseTestCase):
    def setUp(self) -> None:
        super().setUp()
        run("reach-out", PR, "--to", "Dana", "--subject", "re-review", "--purpose", "review")
        self.approve(
            REVIEW_REQUEST, "--send", "slack", "--channel", CHANNEL, "--thread", THREAD, "--text", TEXT
        )
        self.call = self.event(SEND, channel_id=CHANNEL, thread_ts=THREAD, message=TEXT)

    def test_success_marks_it_carried_out_and_sent_closes_it_once(self) -> None:
        self.assertTrue(self.allowed(self.call))
        self.post(self.call)
        item = self.item(REVIEW_REQUEST)
        self.assertEqual(item["state"], "proposed")
        self.assertIn("carried_out", item["proposal"]["approval"])
        self.assertEqual(item["history"][-1]["event"], ledger.APPROVAL_CARRIED_OUT)
        self.assertIn("ledger sent closes the proposal", item["history"][-1]["note"])
        self.assertEqual(approvals.proposal_status(item["proposal"]), approvals.USED)
        before = self.ledger_text()
        self.post(self.call)
        self.assertEqual(self.ledger_text(), before)
        run("sent", REVIEW_REQUEST, "--ref", SENT_REF)
        item = self.item(REVIEW_REQUEST)
        self.assertEqual((item["state"], item["proposal"]), ("sent", None))
        self.assertEqual(self.history(REVIEW_REQUEST).count("proposal-done"), 1)

    def test_a_failed_send_needs_me_again(self) -> None:
        self.assertTrue(self.allowed(self.call))
        self.post(self.call, approvals.POST_TOOL_USE_FAILURE, error="channel_not_found")
        item = self.item(REVIEW_REQUEST)
        self.assertEqual(approvals.proposal_status(item["proposal"]), approvals.NEEDS_YOU)
        self.assertEqual(item["history"][-1]["event"], ledger.APPROVAL_FAILED)


class StandingCallTest(StandingTestCase):
    def test_a_standing_send_is_settled_like_an_approved_one(self) -> None:
        run("transition", PR, "changes-requested", "--note", "comments")
        run("transition", PR, "addressing", "--note", "fixing")
        self.hand_over()
        self.report()
        call = {**self.review_request(), "session_id": SESSION}
        self.assertTrue(self.allowed(call))
        grant = self.item(REVIEW_REQUEST)["proposal"]["auto_approved"]
        self.assertEqual(approvals.proposal_status(self.item(REVIEW_REQUEST)["proposal"]), approvals.USED)
        self.respond("--post-tool-use", {**call, "hook_event_name": "PostToolUse", "tool_response": MCP_OK})
        self.assertIn("carried_out", self.item(REVIEW_REQUEST)["proposal"]["auto_approved"])
        self.assertEqual(grant["class"], "pr-review-traffic")


class ProposalStatusTest(unittest.TestCase):
    SPEC = approvals.run_spec(TOUCH)

    def proposal(self, approved: bool = False, **approval: Any) -> dict[str, Any]:
        base: dict[str, Any] = {"action": "x", "proposed_at": "2026-10-21T10:00:00+00:00", "spec": self.SPEC}
        if approved or approval:
            base["approval"] = {
                "at": "2026-10-21T10:01:00+00:00",
                "quote": QUOTE,
                "hash": approvals.spec_hash(self.SPEC),
                **approval,
            }
        return base

    def test_every_state(self) -> None:
        used = "2026-10-21T10:02:00+00:00"
        cases = [
            (None, None, ""),
            ({"action": "free text", "proposed_at": used}, approvals.NEEDS_YOU, "awaiting your approval"),
            (self.proposal(), approvals.NEEDS_YOU, "awaiting your approval"),
            (self.proposal(hash="earlier"), approvals.NEEDS_YOU, "awaiting your approval"),
            (self.proposal(approved=True), approvals.APPROVED_UNUSED, "approved, not yet made"),
            (self.proposal(used_at=used), approvals.USED, "carried out, closing"),
            (self.proposal(used_at=used, carried_out={"at": used}), approvals.USED, "carried out, closing"),
            (
                self.proposal(used_at=used, failed={"at": used, "reason": "x"}),
                approvals.NEEDS_YOU,
                approvals.FAILED_LABEL,
            ),
            (
                {**self.proposal(), "auto_approved": {"at": used, "class": "pr-housekeeping"}},
                approvals.USED,
                "carried out, closing",
            ),
        ]
        for proposal, status, label in cases:
            with self.subTest(proposal=proposal):
                self.assertEqual(approvals.proposal_status(proposal), status)
                self.assertEqual(approvals.proposal_label(proposal), label)

    def test_outcomes(self) -> None:
        self.assertEqual(
            approvals.call_outcome({"hook_event_name": "PostToolUse", "tool_response": BASH_OK}),
            approvals.Outcome(True),
        )
        self.assertEqual(
            approvals.call_outcome({"hook_event_name": "PostToolUse", "tool_response": MCP_OK}),
            approvals.Outcome(True),
        )
        failure = approvals.call_outcome({"hook_event_name": "PostToolUseFailure", "error": "x " * 200})
        self.assertFalse(failure.ok)
        self.assertLessEqual(len(failure.reason), approvals.MAX_REASON_LENGTH)
        self.assertEqual(
            approvals.call_outcome({"hook_event_name": "PostToolUseFailure"}).reason, "no reason given"
        )

    def test_the_call_hash_ignores_a_bash_description(self) -> None:
        one = approvals.Call("Bash", {"command": TOUCH, "description": "a"})
        other = approvals.Call("Bash", {"command": TOUCH, "description": "b"})
        self.assertEqual(approvals.call_hash(one), approvals.call_hash(other))
        self.assertEqual(approvals.call_hash(one), approvals.spec_hash(approvals.run_spec(TOUCH)))


class ListingTest(PostToolUseTestCase):
    def test_list_and_the_readable_view_say_where_each_proposal_stands(self) -> None:
        run("upsert", OTHER_PR, "--kind", "my-pr")
        self.use()
        run("propose", OTHER_PR, "--run", OTHER_COMMAND)
        listing = run("list", "--proposals")
        self.assertIn(f"| PENDING (carried out, closing): run: {TOUCH}", listing)
        self.assertIn(f"| PENDING: run: {OTHER_COMMAND}", listing)
        view = ledger.render(json.loads(self.ledger_text()))
        awaiting, approved = view.split("## Approved", 1)
        self.assertIn(OTHER_PR, awaiting.split("## Awaiting your approval", 1)[1])
        self.assertIn(f"**{PR}**: run: {TOUCH} (carried out, closing)", approved)

    def test_a_failed_one_is_awaiting_again(self) -> None:
        self.post(self.use(), approvals.POST_TOOL_USE_FAILURE)
        self.assertIn(f"| PENDING ({approvals.FAILED_LABEL}): run: {TOUCH}", run("list", "--proposals"))
        view = ledger.render(json.loads(self.ledger_text()))
        self.assertNotIn("## Approved", view)
        self.assertIn(f"({approvals.FAILED_LABEL})", view)


def tool_use(call_id: str, name: str, tool_input: dict[str, Any], moment: datetime) -> dict[str, Any]:
    content = [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]
    return {
        "type": "assistant",
        "timestamp": stamp(moment),
        "message": {"role": "assistant", "content": content},
    }


def tool_result(call_id: str, moment: datetime | None, is_error: bool = False) -> dict[str, Any]:
    block = {"type": "tool_result", "tool_use_id": call_id, "content": "Exit code 1" if is_error else "ok"}
    entry = {"type": "user", "message": {"role": "user", "content": [{**block, "is_error": is_error}]}}
    return {**entry, "timestamp": stamp(moment)} if moment else entry


class CleanupTest(PostToolUseTestCase):
    """Approvals used before the PostToolUse hook: settled from the transcript, by hand otherwise."""

    def setUp(self) -> None:
        super().setUp()
        run("upsert", OTHER_PR, "--kind", "my-pr")
        run("upsert", TICKET_ITEM, "--kind", "ticket")
        self.use()
        self.use(OTHER_PR, OTHER_COMMAND)
        self.approve(TICKET_ITEM, "--tool", COMMENT_TOOL, "--args-json", json.dumps(COMMENT_ARGS))
        self.assertTrue(self.allowed(self.event(COMMENT_TOOL, **COMMENT_ARGS)))
        now = datetime.now(UTC)
        # what the transcript shows: TOUCH succeeded (its result repeated without a time), the other
        # command failed, the comment has no result (the session ended)
        self.say(
            tool_use("toolu_1", "Bash", {"command": TOUCH, "description": "x"}, now),
            tool_result("toolu_1", now),
            tool_result("toolu_1", None),
            tool_use("toolu_2", "Bash", {"command": OTHER_COMMAND}, now),
            tool_result("toolu_2", now, is_error=True),
            tool_use("toolu_3", COMMENT_TOOL, COMMENT_ARGS, now),
            tool_use("toolu_4", "Bash", {"command": TOUCH}, now - timedelta(hours=2)),
            tool_result("toolu_4", now - timedelta(hours=2), is_error=True),
        )

    def test_dry_run_says_what_it_would_do_and_changes_nothing(self) -> None:
        before = self.ledger_text()
        output = run("resolve-carried-out", "--dry-run")
        self.assertIn(f"{PR}: the approved call (Bash) succeeded", output)
        self.assertIn(f"{OTHER_PR}: the approved call (Bash) failed", output)
        self.assertIn(f"{TICKET_ITEM}: approval used at", output)
        self.assertIn(f"resolve it by hand: bosun ledger resolve '{TICKET_ITEM}'", output)
        self.assertIn("(2 would settle, 1 left for you to resolve)", output)
        self.assertEqual(self.ledger_text(), before)

    def test_it_settles_by_the_transcript_and_leaves_the_rest(self) -> None:
        output = run("resolve-carried-out")
        self.assertIn("(2 settled, 1 left for you to resolve)", output)
        self.assertIsNone(self.item(PR)["proposal"])
        self.assertEqual(self.item(PR)["history"][-1]["tool_use_id"], "toolu_1")
        self.assertEqual(self.history(OTHER_PR)[-1], ledger.APPROVAL_FAILED)
        self.assertEqual(self.item(OTHER_PR)["proposal"]["approval"]["failed"]["reason"], "Exit code 1")
        self.assertNotIn("carried_out", self.item(TICKET_ITEM)["proposal"]["approval"])
        # done once: run again, only the one without evidence is left
        self.assertIn("(0 settled, 1 left for you to resolve)", run("resolve-carried-out"))

    def test_legacy_approvals_read_given_or_orchestrator_transcripts(self) -> None:
        data = json.loads(self.ledger_text())
        for item in data["items"].values():
            for key in ("transcript", "session", "call"):
                (item.get("proposal") or {}).get("approval", {}).pop(key, None)
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        self.assertIn("(0 would settle, 3 left", run("resolve-carried-out", "--dry-run"))
        self.assertIn(
            "(2 would settle", run("resolve-carried-out", "--dry-run", "--transcript", str(self.transcript))
        )
        with tempfile.TemporaryDirectory() as projects:
            workdir = Path(projects) / "orchestrator-dir"
            folder = Path(projects) / "".join(
                c if c.isalnum() or c == "-" else "-" for c in str(workdir.resolve())
            )
            folder.mkdir()
            (folder / "session.jsonl").write_text(self.transcript.read_text())
            ledger.configure_tickets("", str(workdir))
            with mock.patch.object(claude_state, "PROJECTS_DIR", Path(projects)):
                self.assertIn("(2 would settle", run("resolve-carried-out", "--dry-run"))

    def test_nothing_to_do(self) -> None:
        for item_id in (PR, OTHER_PR, TICKET_ITEM):
            run("resolve", item_id, "withdrawn", "--note", "x")
        self.assertEqual(
            run("resolve-carried-out", "--dry-run"),
            "no pending proposal has a used approval waiting for its result",
        )


if __name__ == "__main__":
    unittest.main()
