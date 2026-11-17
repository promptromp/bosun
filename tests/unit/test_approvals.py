"""Unit tests for approvals (lib/approvals.py, the ledger's propose/approve/approvals, and the
guard's --approvals and --permission-request hooks): exact matching, quote verification against
transcripts, single use, standing classes, and that nothing changes with the flag off."""

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
import activity
import agent_state
import approvals
import claude_state
import guard
import ledger
import minted
import update_gate


PR = "mypr:acme/exporter#102"
PR_URL = "https://github.com/acme/exporter/pull/102"
OTHER_PR = "mypr:acme/exporter#103"
CHANNEL = "C0000000001"
OTHER_CHANNEL = "C0000000009"
DM = "D0000000001"
THREAD = "1700000090.000090"
REVIEWER = "U0000000002"
STRANGER = "U0000000003"
SEND = "mcp__plugin_slack_slack__slack_send_message"
TEXT = f"<@{REVIEWER}> could you take another look at {PR_URL}? Fixed the footer."
TOUCH = "touch /tmp/acme-approved.txt"
QUOTE = "1 and 3 yes"
SENT_REF = f"https://acme.slack.com/archives/{CHANNEL}/p1700000100000100"


def run(*argv: str) -> Any:
    """A ledger command; it remembers the outreach it made (minted.made)."""
    return minted.run(*argv)


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def typed(text: str, moment: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": stamp(moment),
        "origin": {"kind": "human"},
        "promptSource": "typed",
        "message": {"role": "user", "content": text},
        **extra,
    }


def tool_result(text: str, moment: datetime) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": stamp(moment),
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": text}],
        },
    }


class ApprovalsTestCase(unittest.TestCase):
    """A scratch ledger with my PR, and a transcript file for the session. `ledger approve` runs from
    no agent's session (a terminal) unless a test says otherwise: the suite itself may run inside one,
    which must not count."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.approving_in(None)
        self.state = Path(self.tmp.name) / "state"
        ledger.configure(self.state)
        minted.forget()
        self.transcript = Path(self.tmp.name) / "session.jsonl"
        self.transcript.write_text("")
        run("upsert", PR, "--kind", "my-pr", "--title", "VAT breakdown", "--url", PR_URL)
        self.env = {
            "BOSUN_STATE_DIR": str(self.state),
            "BOSUN_ORCHESTRATOR": "orchestrator",
            "BOSUN_APPROVALS_ENABLED": "1",
            # touch stands in for an outward command (the headless check does the same)
            "BOSUN_APPROVALS_GATED_COMMANDS": "touch, acme-deploy run",
        }

    def approving_in(self, session: agent_state.CallingSession | None) -> None:
        """Run `ledger approve` from <session>, as the agent session that ran it; None: from a terminal."""
        patcher = mock.patch.object(agent_state, "calling_session", return_value=session)
        patcher.start()
        self.addCleanup(patcher.stop)

    @property
    def review_request_id(self) -> str:
        """The first outreach this test made for PR (a review request, in the standing tests)."""
        return minted.made(PR)

    def say(self, *entries: dict[str, Any]) -> None:
        with self.transcript.open("a") as fh:
            for entry in entries:
                fh.write(json.dumps(entry) + "\n")

    def item(self, item_id: str) -> dict[str, Any]:
        item: dict[str, Any] = json.loads(ledger.STORE.ledger_file.read_text())["items"][item_id]
        return item

    def propose_touch(self, item_id: str = PR, command: str = TOUCH) -> None:
        run("propose", item_id, "--run", command)

    def event(self, tool: str, **tool_input: Any) -> dict[str, Any]:
        return {"tool_name": tool, "tool_input": tool_input, "transcript_path": str(self.transcript)}

    def respond(self, mode: str, event: dict[str, Any], **env: str) -> dict[str, Any] | None:
        result = guard.respond(mode, event, {**self.env, **env})
        ledger.configure(self.state)  # the guard configured it the same way; keep tests explicit
        return result

    def allowed(self, event: dict[str, Any], **env: str) -> bool:
        return self.respond("--permission-request", event, **env) is not None

    def denial(self, event: dict[str, Any], **env: str) -> str | None:
        result = self.respond("--approvals", event, **env)
        return str(result["permissionDecisionReason"]) if result else None


class MatchingTest(unittest.TestCase):
    def test_slack_text_is_whitespace_normalized_but_otherwise_exact(self) -> None:
        spec = approvals.slack_spec(CHANNEL, "Hi  there,\nPR is up", THREAD)
        call = {"channel_id": CHANNEL, "thread_ts": THREAD, "message": "Hi there, PR is up\n"}
        self.assertTrue(approvals.call_matches(spec, SEND, call))
        self.assertFalse(approvals.call_matches(spec, SEND, {**call, "message": "hi there, PR is up"}))
        self.assertFalse(approvals.call_matches(spec, SEND, {**call, "message": "Hi there, PR is up!"}))

    def test_slack_channel_thread_and_broadcast_must_match(self) -> None:
        spec = approvals.slack_spec(CHANNEL, "ok", THREAD)
        call = {"channel_id": CHANNEL, "thread_ts": THREAD, "message": "ok"}
        self.assertFalse(approvals.call_matches(spec, SEND, {**call, "channel_id": OTHER_CHANNEL}))
        self.assertFalse(approvals.call_matches(spec, SEND, {**call, "thread_ts": ""}))
        self.assertFalse(approvals.call_matches(spec, SEND, {**call, "reply_broadcast": True}))
        top_level = approvals.slack_spec(CHANNEL, "ok")
        self.assertFalse(approvals.call_matches(top_level, SEND, call))
        self.assertTrue(approvals.call_matches(top_level, SEND, {"channel_id": CHANNEL, "message": "ok"}))
        # the same tool under another server name
        self.assertTrue(approvals.call_matches(spec, "mcp__claude_ai_Slack__slack_send_message", call))

    def test_commands_match_exactly(self) -> None:
        spec = approvals.run_spec(f"  {TOUCH} ")
        self.assertTrue(approvals.call_matches(spec, "Bash", {"command": TOUCH}))
        self.assertFalse(approvals.call_matches(spec, "Bash", {"command": f"{TOUCH} && rm -rf /tmp/x"}))
        self.assertFalse(approvals.call_matches(spec, "Bash", {"command": TOUCH.replace("touch", "touch  ")}))

    def test_mcp_tool_and_arguments_match_exactly(self) -> None:
        spec = approvals.mcp_spec("jira_add_comment", {"issue_key": "EXP-1", "comment": "done"})
        tool = "mcp__acme-tracker__jira_add_comment"
        self.assertTrue(approvals.call_matches(spec, tool, {"comment": "done", "issue_key": "EXP-1"}))
        self.assertFalse(approvals.call_matches(spec, tool, {"issue_key": "EXP-1", "comment": "done!"}))
        self.assertFalse(approvals.call_matches(spec, "mcp__acme-tracker__jira_edit_comment", spec["args"]))

    def test_the_hash_follows_the_compared_spec(self) -> None:
        one = approvals.slack_spec(CHANNEL, "a  b")
        self.assertEqual(approvals.spec_hash(one), approvals.spec_hash(approvals.slack_spec(CHANNEL, "a b")))
        self.assertNotEqual(
            approvals.spec_hash(one), approvals.spec_hash(approvals.slack_spec(CHANNEL, "a c"))
        )

    def test_terminal_states_agree_with_the_ledger(self) -> None:
        self.assertEqual(
            {kind: set(states) for kind, states in approvals.TERMINAL_STATES.items()},
            {kind: set(spec["terminal"]) for kind, spec in ledger.KINDS.items()},
        )


# The review's round-2 quotes (scratchpad rv2/q2.py) with what each gets. Those that count and should not, as
# docs/design/approvals.md lists: a request that holds "go ahead", someone's words without quotation marks, a
# yes with a time on it, a homoglyph negation (a Greek omicron), a bare number.
ROUND_TWO_QUOTES: tuple[tuple[str, str, bool], ...] = (
    ("sure, but not yet", "sure, but not yet", False),
    ("sure", "sure, but not yet", False),
    ("ok \u2014 actually no", "ok \u2014 actually no", False),
    ("ok", "ok \u2014 actually no", False),
    ("yes?", "yes?", False),
    ("approve 2", "approve 2", True),  # round 3: a verb that picks a bare choice
    ("1", "1", True),
    ("all", "all", True),
    ("go ahead and tell me what's pending", "go ahead and tell me what's pending", True),
    ("go ahead", "go ahead and tell me what's pending", True),
    ("send me the link", "send me the link", False),
    ("can you send me the link", "can you send me the link?", False),
    ("how should we proceed", "how should we proceed?", False),
    ("who needs to approve this", "who needs to approve this?", False),
    ("is the build fine", "is the build fine?", False),
    ("the build is fine", "the build is fine", False),
    ('Dana wrote "ok, ship it"', 'Dana wrote "ok, ship it" on the PR, what do you think?', False),
    ('"ok, ship it"', 'Dana wrote "ok, ship it" on the PR, what do you think?', False),
    ("ok, ship it", "Dana wrote: ok, ship it. Look at the thread", True),
    ("don\u2019t send it", "don\u2019t send it", False),
    ("don\xb4t send it", "don\xb4t send it", False),
    ("don`t send it", "don`t send it", False),
    ("dont send it", "dont send it", False),
    ("isnt fine", "the build isnt fine", False),
    ("the build isnt fine", "the build isnt fine", False),
    ("ok nevermind", "ok nevermind", False),
    ("okay, scratch that", "okay, scratch that", False),
    ("fine, forget it", "fine, forget it", False),
    ("ok abort", "ok abort", False),
    ("yes, after lunch", "yes, after lunch", True),
    ("send it? No way.", "send it? No way.", False),
    ("send it?", "send it? No way.", False),
    ("should we send it", "Should we send it? No.", False),
    ("yes!!!!!", "yes!!!!! no wait", False),
    ("yes", "yes. no wait", False),
    ("Yes, send it.", "No. Yes, send it.", False),
    ("send it", "No.\nsend it", False),
    ("ok send it", "no\nok send it", False),
    ("ok send it", "No!\nok send it", False),
    ("look into mypr:acme/exporter#102", "look into mypr:acme/exporter#102 and summarize the review", False),
    ("mypr:acme/exporter#102", "mypr:acme/exporter#102", False),
    ("why is mypr:acme/exporter#102 open", "why is mypr:acme/exporter#102 open", False),
    ("NO", "NO", False),
    ("No.", "No.", False),
    ("n", "n", False),
    ("ok send it", "ok send it \uff4e\uff4f\uff54", False),
    ("ok send it", "ok send it n\u200bot", False),
    ("ok send it", "ok send it n\u03bf", True),
    ("ok send it", "ok send it non-stop", False),
    ("ok send it", "ok send it - no-go", False),
    ("send it", "e.g. send it", False),
    ("it. ok send it", "don't do it. ok send it", False),
    ("ok send it", "don't do it. ok send it", False),
    ("ok send it", "don't do it; ok send it", False),
    ("ok send it", "ok send it, not the other one", False),
    ("approved", "approved... not", False),
    ("yes", "yes..no", False),
    ("yes", "yes.no", False),
    ("yes.", "yes.no", False),
    ("1, 3", "1, 3", True),
    ("both", "both", True),
    ("all of them", "all of them", True),
    ("1 and 3", "1 and 3?", False),
    ("2b", "2b", True),
    ("100", "100", True),
    ("1+1", "1+1", True),
    ("3 & 4", "3 & 4", True),
    ("all?", "all?", False),
    ("ok send it", "ok send it, holding pattern", True),
    ("ok send it", "ok send it, I can't wait", False),
    ("yesterday's deploy", "yesterday's deploy", False),
    ("okay", "okay", True),
    ("ok", "ok", True),
    ("k", "k", False),
    ("yes sir", "yes sir", True),
)

# The review's round-3 quotes (scratchpad rv3/q.py, q2.py), (quote, prompt, counts). Those that count and
# should not are docs/design/approvals.md's list: a yes with a limit ("ok for all except 2", "yes, but only
# 1"), with a time or a condition, someone's words passed on, a quoted no (`yes "no"`), a homoglyph.
ROUND_THREE_QUOTES: tuple[tuple[str, str, bool], ...] = (
    ("yes", "yes", True),
    ("ok", "ok", True),
    ("1 and 3", "1 and 3", True),
    ("all", "all", True),
    ("go ahead with 1 and 3", "go ahead with 1 and 3", True),
    ("yes please", "yes please", True),
    ("ok, send it", "ok, send it", True),
    ("Yes!", "Yes!", True),
    ("yes", "Yes!", True),
    ("sure, do it", "sure, do it", True),
    ("approved", "approved", True),
    ("lgtm", "lgtm", True),
    ("LGTM", "LGTM", True),
    ("1 and 3 yes", "1 and 3 yes", True),
    ("Ok.", "Ok.", True),
    ("yes", "yes.", True),
    ("ok \U0001f44d", "ok \U0001f44d", True),
    ("yes, 1 and 3", "yes, 1 and 3", True),
    ("1 and 3 yes", "1 and 3 yes, 2 later", False),
    ("1 and 3, not 2", "1 and 3, not 2", False),
    ("approve 1 and 3", "approve 1 and 3", True),
    ("send 1 and 3", "send 1 and 3", True),
    ("do 1 and 3", "do 1 and 3", True),
    ("1 and 3 please", "1 and 3 please", True),
    ("go with 1 and 3", "go with 1 and 3", True),
    ("merge it", "merge it", False),
    ("yes, merge it", "yes, merge it", True),
    ("1, 3", "1, 3", True),
    ("both", "both", True),
    ("all of them", "all of them", True),
    ("2", "2", True),
    ("yes to all", "yes to all", True),
    ("yep", "yep", True),
    ("okay go ahead", "okay go ahead", True),
    ("Yes, go ahead. Thanks!", "Yes, go ahead. Thanks!", True),
    ("Yes, go ahead", "Yes, go ahead. Thanks!", True),
    ("yes, no rush", "yes, no rush", False),
    ("send it now", "send it now", True),
    ("ship it", "ship it", True),
    ("y", "y", False),
    ("approve", "approve", False),
    ("confirmed", "confirmed", True),
    ("fine", "fine", False),
    ("yes for mypr:acme/x#12", "yes for mypr:acme/x#12", True),
    ("all good, send them", "all good, send them", True),
    ("ok do 1 and 3", "ok do 1 and 3", True),
    ("1+3", "1+3", True),
    ("1 & 3", "1 & 3", True),
    ("1 and 3.", "1 and 3.", True),
    ("ok!!", "ok!!", True),
    ("yes - go ahead", "yes - go ahead", True),
    ("Yes \u2014 go ahead", "Yes \u2014 go ahead", True),
    ("ok, send it", "ok, send it. I'll check later", False),
    ("Sure thing", "Sure thing", True),
    ("yes, but only 1", "yes, but only 1", True),
    ("ok, it's fine, send it", "ok, it's fine, send it", True),
    ("ok for all except 2", "ok for all except 2", True),
    ("go", "go", False),
    ("do it", "do it", True),
    ("go ahead and tell me what's pending", "go ahead and tell me what's pending", True),
    ("Dana wrote: ok, ship it", "Dana wrote: ok, ship it", True),
    ("ok, ship it", "Dana wrote: ok, ship it", True),
    ("yes, after lunch", "yes, after lunch", True),
    ("yes, n\u03bf", "yes, n\u03bf", True),
    ("ok, but you mustnt merge", "ok, but you mustnt merge", False),
    ("ok, you neednt merge", "ok, you neednt merge", False),
    ("yes", "yes, dont", False),
    ("yes", "yes \u201cno\u201d", True),
    ("yes", "yes \u2018no\u2019", True),
    ("yes", "yes 'no'", True),
    ("yes", "yes \xabno\xbb", False),
    ("yes", "yes \u201eno\u201c", False),
    ("yes", "yes\u200b", True),
    ("yes", "n\u200bo, yes", False),
    ("yes", "yes \uff1f", False),
    ("yes", "yes \xbf", False),
    ("ok", "ok. nope.", False),
    ("Yes for 2", "Yes for 2, NO for 3", False),
    ("yes, merge it once ci is green", "yes, merge it once ci is green", True),
    ("yes, after the standup", "yes, after the standup", True),
)


class QuoteRuleTest(unittest.TestCase):
    """What counts as my approval of a proposal (approvals.approval_quote_in, QUOTE_RULE): in a prompt with no
    negation and no question anywhere (quoted text left out), all of it holding an approving phrase or a bare
    choice, or at least MIN_QUOTE_CHARS of its whole words holding an approving phrase."""

    def counts(self, quote: str, *prompts: str) -> bool:
        return approvals.approval_quote_in(quote, prompts)

    def test_round_two_and_three_quotes(self) -> None:
        for quote, prompt, wanted in (*ROUND_TWO_QUOTES, *ROUND_THREE_QUOTES):
            with self.subTest(quote=quote, prompt=prompt):
                self.assertEqual(self.counts(quote, prompt), wanted)

    def test_a_whole_prompt_counts_when_it_says_yes(self) -> None:
        for quote, prompt in [
            ("yes", "yes"),
            ("send it", "Send it!"),
            ("Yes!", "yes"),
            ("1 and 3", "  1 and\n3 "),
            ("1,3", "1,3"),
            ("all", "all"),
            ("all of them", "all of them!"),
            ("Yeah, go ahead", "yeah, go ahead"),
            ("lgtm", "LGTM"),
            (f"yes, {PR}", f"yes, {PR}"),
            (f"ok for {PR}", f"ok for {PR}"),
            ("\uff59\uff45\uff53", "yes"),  # fullwidth letters are letters (NFKC)
        ]:
            with self.subTest(quote=quote):
                self.assertTrue(self.counts(quote, prompt))

    def test_a_prompt_that_says_no_or_asks_approves_nothing(self) -> None:
        for prompt in [
            "what is on my plate today?",
            "no",
            "hold on",
            "go with option 2",  # not a bare choice
            "approve",  # a bare verb
            "merge 1 and 3",  # a merge asks again
            "the build is fine",
            PR,  # the id alone
            f"approve {PR}",
            "y",
            "\U0001f44d",  # an emoji: later (M9)
            "",
        ]:
            with self.subTest(prompt=prompt):
                self.assertFalse(self.counts(prompt, prompt))

    def test_a_negation_anywhere_in_the_prompt_voids_it(self) -> None:
        for quote, prompt in [
            ("yes, send it.", "Don't wait for CI. Yes, send it."),  # another sentence
            ("ok send it", "ok send it, not the other one"),
            ("ok send it", "no\nok send it"),
            ("yes, send it", "Yes, send it\u2026 actually don\u2019t"),
            ("ok send it", "ok send it, isnt that right"),
            ("go ahead and send it", "go ahead and send it, nevermind the second"),
            ("ok send it", "ok send it ok send it n\u200bot"),  # a zero-width space inside the not
        ]:
            with self.subTest(quote=quote, prompt=prompt):
                self.assertFalse(self.counts(quote, prompt))

    def test_quoted_text_is_someone_elses(self) -> None:
        self.assertFalse(self.counts("ok, ship it", 'Dana wrote "ok, ship it" on the PR'))
        self.assertFalse(self.counts("ok, ship it", "Dana wrote \u201cok, ship it\u201d on the PR"))
        self.assertFalse(self.counts("lgtm", "she said 'lgtm' to me"))
        self.assertTrue(self.counts("yes, send it", 'yes, send it to Dana: she asked for "the summary"'))
        self.assertTrue(self.counts("ok, it's fine to send it", "ok, it's fine to send it"))  # apostrophes

    def test_a_part_needs_whole_words_the_length_and_an_approving_phrase(self) -> None:
        prompt = "1 and 3 YES, 2 skip; also check why the exporter's CI is red today"
        self.assertTrue(self.counts("1 and 3 yes", prompt))
        self.assertTrue(self.counts("go ahead with 1 and 3", "sure, go ahead with 1 and 3 and tell me after"))
        for quote in [
            "e",
            "today",
            "the exporter's CI is red",
            "es, 2 skip",
            "1 and 3 ye",
            "yes",  # an approving phrase, too short as a part
            "1 and 3",  # a bare choice, but not all of the prompt
        ]:
            with self.subTest(quote=quote):
                self.assertFalse(self.counts(quote, prompt))
        self.assertFalse(self.counts("yes and look", "yes", "and look"))  # across two prompts
        self.assertFalse(self.counts(f"{PR} go", f"for {PR} go, the rest later"))  # the id says no yes

    def test_an_approving_word_inside_another_word_is_none(self) -> None:
        prompt = "yesterday's okayish draft is in the doc"
        self.assertFalse(self.counts("yesterday's okayish", prompt))
        self.assertFalse(self.counts("okayish draft", prompt))

    def test_a_statement_needs_only_whole_words(self) -> None:
        """approvals.words_in, which an ontology quote of my words uses: no approving phrase, no length, and
        quoted text kept (a statement may quote a name)."""
        prompt = 'Yes: smoke tests gate every release, the job is "nightly-export".'
        self.assertTrue(approvals.words_in("smoke  tests gate", [prompt]))
        self.assertTrue(approvals.words_in("yes", ["other", prompt]))
        self.assertTrue(approvals.words_in('the job is "nightly-export"', [prompt]))
        for quote in ("moke tests", "every relea", "e", ""):
            with self.subTest(quote=quote):
                self.assertFalse(approvals.words_in(quote, [prompt]))

    def test_text_bosun_pasted_is_compared_as_typed(self) -> None:
        self.assertTrue(self.counts("yes", "<pasted_content>yes</pasted_content>"))
        self.assertFalse(self.counts("...", "..."))


class GatedTest(unittest.TestCase):
    def gated(self, tool: str, **tool_input: Any) -> bool:
        return guard.outward_call({"tool_name": tool, "tool_input": tool_input}) is not None

    def test_outward_calls_are_gated(self) -> None:
        for tool in [
            SEND,
            "mcp__claude_ai_Slack__slack_schedule_message",
            "mcp__plugin_slack_slack__slack_add_reaction",
            "mcp__plugin_slack_slack__slack_create_canvas",
            "mcp__acme-tracker__jira_transition_issue",
            "mcp__acme-tracker__confluence_add_comment",
            "mcp__acme-ci__restart_build",
            "mcp__acme-ci__promote_build",
        ]:
            with self.subTest(tool=tool):
                self.assertTrue(self.gated(tool))
        for command in [
            "gh pr merge https://github.com/acme/exporter/pull/1 --squash",
            "gh pr ready 1",
            "gh pr update-branch https://github.com/acme/exporter/pull/1",
            "gh api repos/acme/exporter/pulls/1",
            "cd /tmp && git push origin main",
            "gh pr view 1 && gh pr comment 1 --body hi",
            "timeout 120 gh pr merge https://github.com/acme/exporter/pull/1 --squash 2>&1 | tail -2",
            "env GH_DEBUG=1 nohup git push origin main",
            # git's own options before the subcommand, a full path, a substitution, a shell wrapper
            "git -C /Users/me/Development/exporter push origin HEAD:main",
            "git -c core.sshCommand=ssh --git-dir=/x/.git push origin main",
            "/usr/bin/git push origin main",
            "echo `gh pr merge 1 --squash`",
            'echo "$(gh pr comment 1 --body hi)"',
            "bash -c 'gh pr merge 1 --squash'",
            'eval "git push origin main"',
            # the other outward gh commands
            "gh pr create --fill",
            "gh issue comment 3 --body hi",
            "gh issue create --title t --body b",
            "gh issue edit 3 --add-label bug",
            "gh issue close 3",
            "gh release create v1.2.0",
            "gh workflow run ci.yml",
            "gh run rerun 12345",
        ]:
            with self.subTest(command=command):
                self.assertTrue(self.gated("Bash", command=command))

    def test_reads_are_not_gated(self) -> None:
        for tool in [
            "mcp__plugin_slack_slack__slack_read_thread",
            "mcp__acme-tracker__jira_get_issue",
            "mcp__acme-deploy__get_build",
        ]:
            with self.subTest(tool=tool):
                self.assertFalse(self.gated(tool))
        for command in [
            "gh pr view 1 --json state",
            "bosun ledger list",
            "echo 'gh pr merge 1'",
            "gh issue view 3",
            "gh issue list --state open",
            "gh run view 12345 --log-failed",
            "gh workflow list",
            "gh release view v1.2.0",
            "git -C /Users/me/Development/exporter log --oneline -3",
            "bash -c 'git status'",
        ]:
            with self.subTest(command=command):
                self.assertFalse(self.gated("Bash", command=command))


class ProposeTest(ApprovalsTestCase):
    def test_propose_records_the_spec_and_a_readable_action(self) -> None:
        with mock.patch("sys.stdin", io.StringIO("Could you  review?\n")):
            run("propose", PR, "--send", "slack", "--channel", CHANNEL, "--thread", THREAD, "--text", "-")
        proposal = self.item(PR)["proposal"]
        self.assertEqual(proposal["spec"], approvals.slack_spec(CHANNEL, "Could you  review?", THREAD))
        self.assertIn(CHANNEL, proposal["action"])

    def test_free_text_proposals_still_work(self) -> None:
        run("propose", PR, "merge it")
        self.assertEqual(self.item(PR)["proposal"]["action"], "merge it")
        self.assertNotIn("spec", self.item(PR)["proposal"])

    def test_bad_specs_are_refused(self) -> None:
        for argv, error in [
            (["--run", "x", "--tool", "y", "--args-json", "{}"], "one action spec"),
            (["--send", "slack", "--channel", CHANNEL], "--text"),
            (["--tool", "jira_add_comment"], "--args-json"),
            (["--tool", "jira_add_comment", "--args-json", "[1]"], "JSON object"),
            (["--channel", CHANNEL], "go with --send"),
            ([], "say what is proposed"),
        ]:
            with self.subTest(argv=argv), self.assertRaisesRegex(ledger.LedgerError, error):
                run("propose", PR, *argv)

    def test_approve_needs_a_spec_and_a_quote(self) -> None:
        run("propose", PR, "merge it")
        with self.assertRaisesRegex(ledger.LedgerError, "no pending proposal with an action spec"):
            run("approve", PR, "--quote", "yes")
        self.propose_touch()
        with self.assertRaisesRegex(ledger.LedgerError, "--quote"):
            run("approve", PR, "--quote", "  ")
        run("approve", PR, "--quote", " 1 and\n3 yes ")
        approval = self.item(PR)["proposal"]["approval"]
        self.assertEqual(
            (approval["quote"], approval["hash"]), (QUOTE, approvals.spec_hash(approvals.run_spec(TOUCH)))
        )

    def test_a_new_proposal_drops_the_old_approval(self) -> None:
        self.propose_touch()
        run("approve", PR, "--quote", QUOTE)
        self.propose_touch(command=TOUCH + "2")
        self.assertNotIn("approval", self.item(PR)["proposal"])

    def test_fyi_outreach_may_follow_a_merge_but_questions_may_not(self) -> None:
        run("transition", PR, "merged", "--note", "merged")
        run("reach-out", PR, "--to", "the team", "--subject", "merged and on staging", "--purpose", "fyi")
        with self.assertRaisesRegex(ledger.LedgerError, "outreach belongs to open work items"):
            run("reach-out", PR, "--to", "Dana", "--subject", "a question")


class ChatApprovalTest(ApprovalsTestCase):
    def approve_touch(self, *said: dict[str, Any]) -> None:
        """Propose, then say <said> restamped after the proposal, as a real reply comes: the
        proposal's time is kept to the second, so a prompt stamped before it can come out older."""
        self.propose_touch()
        moment = stamp(self.now())
        self.say(*({**entry, "timestamp": moment} for entry in said))
        run("approve", PR, "--quote", QUOTE)

    def now(self) -> datetime:
        return datetime.now(UTC)

    def test_an_approved_quoted_call_is_allowed_once(self) -> None:
        self.approve_touch(typed("1 and 3 YES, 2 skip", self.now()))
        call = self.event("Bash", command=TOUCH)
        self.assertIsNone(self.denial(call))
        self.assertTrue(self.allowed(call))
        self.assertIn("used_at", self.item(PR)["proposal"]["approval"])
        self.assertEqual(self.item(PR)["history"][-1]["event"], ledger.APPROVAL_USED)
        self.assertFalse(self.allowed(call))
        self.assertIn("had its one approval used", self.denial(call) or "")
        with self.assertRaisesRegex(ledger.LedgerError, "already used"):
            run("approve", PR, "--quote", QUOTE)

    def test_a_write_the_update_gate_refuses_is_not_no_decision(self) -> None:
        """The guard swallows errors as no decision, but not the gate's refusal: update_gate.guarded
        runs the hook again on the installed version, which prints the decision."""
        self.approve_touch(typed(QUOTE, self.now()))
        data = json.loads(ledger.STORE.ledger_file.read_text())
        data["version"] = ledger.LEDGER_FORMAT + 1
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        event = json.dumps({**self.event("Bash", command=TOUCH), "hook_event_name": "PermissionRequest"})
        out = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(event)),
            contextlib.redirect_stdout(out),
            self.assertRaises(update_gate.StoreNewer),
        ):
            guard.main(["--permission-request"], self.env)
        self.assertEqual(out.getvalue(), "")
        self.assertNotIn("used_at", self.item(PR)["proposal"]["approval"])

    def test_a_different_call_is_not_allowed(self) -> None:
        self.approve_touch(typed(QUOTE, self.now()))
        other = self.event("Bash", command=f"{TOUCH}; git push origin main")
        self.assertFalse(self.allowed(other))
        self.assertIn("holds the approved command", self.denial(other) or "")
        unrelated = self.event("Bash", command="git push origin main")
        self.assertFalse(self.allowed(unrelated))
        self.assertIn("no pending proposal has exactly this action", self.denial(unrelated) or "")

    def test_an_approved_command_with_more_around_it_is_denied_with_the_exact_call(self) -> None:
        self.approve_touch(typed(QUOTE, self.now()))
        for command in [
            f"timeout 120 {TOUCH}",
            f"{TOUCH} 2>&1 | tail -2",
            f"cd /tmp && {TOUCH}; ls /tmp",
        ]:
            with self.subTest(command=command):
                call = self.event("Bash", command=command)
                self.assertFalse(self.allowed(call))
                reason = self.denial(call) or ""
                self.assertIn(f"holds the approved command of {PR} with more around it", reason)
                self.assertIn(f"Run exactly `{TOUCH}` as a Bash call of its own", reason)
        self.assertNotIn("used_at", self.item(PR)["proposal"]["approval"])
        self.assertTrue(self.allowed(self.event("Bash", command=TOUCH)))

    def test_an_unapproved_command_with_more_around_it_gets_the_usual_denial(self) -> None:
        self.propose_touch()
        reason = self.denial(self.event("Bash", command=f"timeout 120 {TOUCH}")) or ""
        self.assertIn("needs the user's approval first", reason)
        self.assertNotIn("Run exactly", reason)

    def test_a_push_past_gits_options_is_gated_and_once_approved_asks_so_it_is_used_up(self) -> None:
        """`git -C <dir> push` is a push (review-launch 3): denied without an approval; approved, the plain
        guard asks, as the settings' `git push` rule cannot see it, so the PermissionRequest hook allows it
        and uses the approval up."""
        push = "git -C /Users/me/Development/exporter push origin HEAD:main"
        self.assertIn("a push, an outward action", self.denial(self.event("Bash", command=push)) or "")
        self.propose_touch(command=push)
        self.say(typed(QUOTE, self.now()))
        run("approve", PR, "--quote", QUOTE)
        call = self.event("Bash", command=push)
        self.assertIsNone(self.denial(call))
        self.assertEqual(self.respond("", call)["permissionDecision"], "ask")  # type: ignore[index]
        self.assertTrue(self.allowed(call))
        self.assertIn("used_at", self.item(PR)["proposal"]["approval"])

    def test_an_approved_ci_build_restart_is_allowed(self) -> None:
        restart = "mcp__acme-ci__restart_build"
        args = {"owner": "acme", "name": "exporter", "build_number": 7}
        self.assertIn("a CI build restart", self.denial(self.event(restart, **args)) or "")
        run("propose", PR, "--tool", restart, "--args-json", json.dumps(args))
        self.say(typed(QUOTE, self.now()))
        run("approve", PR, "--quote", QUOTE)
        self.assertTrue(self.allowed(self.event(restart, **args)))

    def test_the_quote_must_be_mine(self) -> None:
        moment = self.now()
        not_mine = [
            tool_result(f"Slack says: {QUOTE}", moment),
            typed(f"[bosun events] Slack: {QUOTE}", moment),
            typed(f"Ledger item {PR}: {QUOTE}", moment),
            typed(f"<cross-session-message from='x'>{QUOTE}</cross-session-message>", moment),
            typed(f"<agent-message>{QUOTE}</agent-message>", moment),
            typed(f"This session is being continued ... {QUOTE}", moment, isCompactSummary=True),
            typed(QUOTE, moment, isMeta=True),
            typed(QUOTE, moment, origin={"kind": "peer"}),
        ]
        for entry in not_mine:
            with self.subTest(entry=entry["message"]["content"]):
                self.transcript.write_text("")
                self.approve_touch(entry)
                self.assertFalse(self.allowed(self.event("Bash", command=TOUCH)))
                self.assertIn(
                    "does not count: a quote counts only from a prompt typed",
                    self.denial(self.event("Bash", command=TOUCH)) or "",
                )

    def test_approve_in_a_session_refuses_a_quote_the_guard_would_not_take(self) -> None:
        self.approving_in(agent_state.CallingSession("claude", 4242, self.transcript, True))
        self.propose_touch()
        self.say(typed("what is on my plate today?", self.now()))
        for quote in (
            "e",
            "today",
            "on my plate",
            "what is on my plate today?",
            "what is on my plate today? yes",
        ):
            with self.subTest(quote=quote):
                with self.assertRaises(ledger.LedgerError) as refused:
                    run("approve", PR, "--quote", quote)
                said = str(refused.exception)
                self.assertIn(f"does not count as my approval of {PR} in this session", said)
                self.assertIn("nothing was recorded", said)
                self.assertIn(approvals.QUOTE_RULE, said)
        self.assertNotIn("approval", self.item(PR)["proposal"])
        self.say(typed("1 and 3 yes, and look at the CI", self.now()))
        run("approve", PR, "--quote", QUOTE)
        self.assertTrue(self.allowed(self.event("Bash", command=TOUCH)))

    def test_a_reply_that_says_no_yes_approves_nothing(self) -> None:
        """review round 1 (test_forge): a whole unrelated prompt, a "no", a negated "send". Refused by
        approve in the session, and by the guard when approve was run where it could not check."""
        for typed_text, quote in [
            ("what is on my plate today?", "what is on my plate today?"),
            ("no", "no"),
            ("no, don't send it yet, I want to reword it", "don't send it"),
        ]:
            with self.subTest(quote=quote):
                self.transcript.write_text("")
                self.approving_in(agent_state.CallingSession("claude", 4242, self.transcript, True))
                self.propose_touch()
                self.say(typed(typed_text, self.now()))
                with self.assertRaisesRegex(ledger.LedgerError, "does not count as my approval"):
                    run("approve", PR, "--quote", quote)
                self.approving_in(None)
                run("approve", PR, "--quote", quote)
                self.assertFalse(self.allowed(self.event("Bash", command=TOUCH)))

    def test_approve_refuses_where_it_cannot_check_and_records_from_a_terminal(self) -> None:
        """review round 1: a Codex or agy window, or a session whose transcript is not there, recorded the
        quote unchecked and said to make the call, which the guard then denied. They are refused, saying
        why; from a terminal (the base case) approve records, and the guard checks at the call."""
        self.propose_touch()
        self.approving_in(agent_state.CallingSession("codex", 4242, None, False))
        with self.assertRaisesRegex(ledger.LedgerError, "bosun cannot check my words in Codex"):
            run("approve", PR, "--quote", QUOTE)
        gone = self.transcript.with_name("gone.jsonl")
        for transcript in (None, gone):
            with self.subTest(transcript=transcript):
                self.approving_in(agent_state.CallingSession("claude", 4242, transcript, True))
                with self.assertRaisesRegex(ledger.LedgerError, "transcript is not there"):
                    run("approve", PR, "--quote", QUOTE)
        self.assertNotIn("approval", self.item(PR)["proposal"])
        self.approving_in(None)
        run("approve", PR, "--quote", "e")
        self.assertFalse(self.allowed(self.event("Bash", command=TOUCH)))
        self.assertIn("does not count", self.denial(self.event("Bash", command=TOUCH)) or "")

    def test_what_bosun_pasted_is_not_mine(self) -> None:
        events_dir = self.state / "events"
        events_dir.mkdir(parents=True)
        line = {"at": stamp(self.now()), "window": "orchestrator", "hash": activity.prompt_hash(QUOTE)}
        (events_dir / "pasted.jsonl").write_text(json.dumps(line) + "\n")
        self.approve_touch(typed(QUOTE, self.now()))
        self.assertFalse(self.allowed(self.event("Bash", command=TOUCH)))

    def test_the_quote_must_come_after_the_proposal(self) -> None:
        self.say(typed(QUOTE, self.now() - timedelta(minutes=5)))
        self.approve_touch()
        self.assertFalse(self.allowed(self.event("Bash", command=TOUCH)))

    def test_prompts_typed_while_busy_count(self) -> None:
        delivered = {"type": "queue-operation", "operation": "remove", "content": QUOTE}
        queued = {
            "type": "attachment",
            "attachment": {"type": "queued_command", "commandMode": "prompt", "prompt": QUOTE},
        }
        for entry in (delivered, queued):
            with self.subTest(entry=entry["type"]):
                self.transcript.write_text("")
                self.approve_touch(entry)
                self.assertTrue(self.allowed(self.event("Bash", command=TOUCH)))

    def test_a_missing_transcript_allows_nothing(self) -> None:
        self.approve_touch(typed(QUOTE, self.now()))
        event = {**self.event("Bash", command=TOUCH), "transcript_path": str(self.transcript) + ".gone"}
        self.assertFalse(self.allowed(event))

    def test_an_unapproved_call_is_denied_with_how_to_propose_it(self) -> None:
        reason = self.denial(self.event(SEND, channel_id=CHANNEL, message="hi")) or ""
        self.assertIn("a Slack message, an outward action", reason)
        self.assertIn("bosun ledger approve", reason)
        self.assertIn("--send slack", reason)
        self.propose_touch()
        self.assertIn(
            "is proposed but not approved yet", self.denial(self.event("Bash", command=TOUCH)) or ""
        )

    def test_the_escape_switch_restores_the_prompt(self) -> None:
        self.assertIsNone(self.denial(self.event("Bash", command=TOUCH), BOSUN_APPROVALS_DENY_UNAPPROVED="0"))

    def test_flag_off_changes_nothing(self) -> None:
        self.approve_touch(typed(QUOTE, self.now()))
        call = self.event("Bash", command=TOUCH)
        before = ledger.STORE.ledger_file.read_text()
        self.assertIsNone(
            self.respond(
                "--approvals",
                self.event(SEND, channel_id=CHANNEL, message="x"),
                BOSUN_APPROVALS_ENABLED="0",
            )
        )
        self.assertIsNone(self.respond("--permission-request", call, BOSUN_APPROVALS_ENABLED="0"))
        self.assertEqual(ledger.STORE.ledger_file.read_text(), before)
        # the plain guard is the same as before, whatever the flag
        self.assertEqual(
            self.respond("", self.event(SEND, channel_id=CHANNEL, message="x"))["permissionDecision"],  # type: ignore[index]
            "ask",
        )

    def test_listing(self) -> None:
        self.approve_touch(typed(QUOTE, self.now()))
        self.assertTrue(self.allowed(self.event("Bash", command=TOUCH)))
        self.assertIn(f"{PR}  approval-used ({QUOTE}) Bash: run: {TOUCH}", run("approvals"))
        self.assertIn("no calls approved by the guard", run("approvals", "--auto"))


class StandingTestCase(ApprovalsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.env.update(
            BOSUN_STANDING_CLASSES="pr-review-traffic pr-housekeeping",
            BOSUN_STANDING_SLACK_CHANNELS=f"{CHANNEL} {DM}",
            BOSUN_STANDING_SLACK_MENTIONS=REVIEWER,
            BOSUN_STANDING_GH_REVIEWERS="dana-acme",
        )

    def review_request(
        self, text: str = TEXT, channel: str = CHANNEL, purpose: str = "review"
    ) -> dict[str, Any]:
        run("reach-out", PR, "--to", "Dana", "--subject", "re-review", "--purpose", purpose)
        run(
            "propose",
            self.review_request_id,
            "--send",
            "slack",
            "--channel",
            channel,
            "--thread",
            THREAD,
            "--text",
            text,
        )
        return self.event(SEND, channel_id=channel, thread_ts=THREAD, message=text)

    def hand_over_and_report(self, open_points: str = "", ci: str = "green") -> None:
        run("transition", PR, "changes-requested", "--note", "comments")
        run("transition", PR, "addressing", "--note", "fixing")
        self.hand_over()
        self.report(open_points, ci)

    def hand_over(self, item_id: str = PR) -> None:
        run("record-delegation", item_id, "--window", "exporter", "--task", "fix-my-pr")

    def report(self, open_points: str = "", ci: str = "green", item_id: str = PR) -> None:
        report = ["report", item_id, "--summary", "pushed the fix"]
        run(*report, *(["--open", open_points] if open_points else []), *(["--ci", ci] if ci else []))


class ReadyTest(StandingTestCase):
    """Ready for reviewers = the latest report on the PR's work, after its last hand-over, says CI
    is green and leaves nothing open."""

    def ready(self, item_id: str = PR) -> bool:
        items = json.loads(ledger.STORE.ledger_file.read_text())["items"]
        return approvals.pr_ready(items[item_id], items)

    def test_awaiting_review_alone_is_not_ready(self) -> None:
        self.assertFalse(self.ready())

    def test_a_green_report_with_nothing_open(self) -> None:
        self.report()
        self.assertTrue(self.ready())

    def test_not_ready(self) -> None:
        for name, (open_points, ci) in {
            "red": ("", "red"),
            "pending": ("", "pending"),
            "no CI status": ("", ""),
            "points left open": ("one thread", "green"),
        }.items():
            with self.subTest(name):
                self.report(open_points, ci)
                self.assertFalse(self.ready())

    def test_a_new_hand_over_waits_for_its_report(self) -> None:
        self.hand_over_and_report()
        self.assertTrue(self.ready())
        run("record-update", PR, "--text", "also rename the column")
        self.assertFalse(self.ready())
        self.report()
        self.assertTrue(self.ready())

    def test_the_report_of_the_task_that_produced_the_pr_counts(self) -> None:
        run("upsert", OTHER_PR, "--kind", "my-pr", "--title", "rounding")
        task = run(
            "spawn", "--title", "rounding", "--ask", "fix rounding", "--via", "user: fix it"
        ).splitlines()[0]
        run("link", task, "produces", OTHER_PR)
        run("transition", task, "accepted", "--note", "go", "--evidence", "user: fix it")
        self.hand_over(task)
        self.assertFalse(self.ready(OTHER_PR))
        self.report(item_id=task)
        self.assertTrue(self.ready(OTHER_PR))

    def test_reports_show_the_ci_status(self) -> None:
        self.report(ci="red")
        self.assertIn("ci:      red", run("reports", "--since", "2000-01-01T00:00:00+00:00"))
        self.assertEqual(self.item(PR)["report"]["ci"], "red")


class PrReviewTrafficTest(StandingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.report()  # ready: CI green, nothing open

    def test_a_review_request_for_my_ready_pr_is_allowed_and_listed(self) -> None:
        call = self.review_request()
        self.assertIsNone(self.denial(call))
        self.assertTrue(self.allowed(call))
        self.assertEqual(self.item(self.review_request_id)["history"][-1]["event"], ledger.AUTO_APPROVED)
        self.assertIn("pr-review-traffic", run("approvals", "--auto"))
        self.assertFalse(self.allowed(call))  # once
        run("sent", self.review_request_id, "--ref", SENT_REF)

    def test_a_re_review_after_a_reported_fix_is_allowed(self) -> None:
        self.hand_over_and_report()
        self.assertTrue(self.allowed(self.review_request()))

    def test_not_while_the_window_still_works_or_left_points_open(self) -> None:
        self.hand_over_and_report(open_points="one thread left")
        self.assertFalse(self.allowed(self.review_request()))
        self.assertIn("must say --ci green", self.denial(self.review_request()) or "")

    def test_not_with_ci_red(self) -> None:
        self.hand_over_and_report(ci="red")
        self.assertFalse(self.allowed(self.review_request()))

    def test_refusals(self) -> None:
        cases: dict[str, dict[str, str]] = {
            "wrong channel": {"channel": OTHER_CHANNEL},
            "a DM, even if configured": {"channel": DM},
            "extra mention": {"text": f"<@{STRANGER}> {TEXT}"},
            "group mention": {"text": f"<!here> {TEXT}"},
            "extra URL": {"text": f"{TEXT} https://example.com/x"},
            "another PR's URL": {"text": TEXT.replace("/102", "/103")},
            "a question is not review traffic": {"purpose": "question"},
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.setUp()
                self.assertFalse(self.allowed(self.review_request(**change)))

    def test_a_closed_pr_gets_nothing_but_a_merged_one_gets_its_fyi_note(self) -> None:
        run("transition", PR, "closed", "--note", "abandoned")
        with contextlib.suppress(ledger.LedgerError):
            self.review_request()
        self.assertFalse(self.allowed(self.event(SEND, channel_id=CHANNEL, thread_ts=THREAD, message=TEXT)))
        self.setUp()
        run("transition", PR, "merged", "--note", "merged")
        note = f"Merged and on staging: {PR_URL}"
        self.assertTrue(self.allowed(self.review_request(text=note, purpose="fyi")))

    def test_a_scheduled_message_is_never_standing(self) -> None:
        self.review_request()
        scheduled = self.event(
            "mcp__plugin_slack_slack__slack_schedule_message",
            channel_id=CHANNEL,
            thread_ts=THREAD,
            message=TEXT,
            post_at=1,
        )
        self.assertFalse(self.allowed(scheduled))

    def test_classes_are_off_until_configured(self) -> None:
        call = self.review_request()
        self.assertFalse(self.allowed(call, BOSUN_STANDING_CLASSES=""))


class PrHousekeepingTest(StandingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.report()  # ready: CI green, nothing open

    def bash(self, command: str) -> dict[str, Any]:
        return self.event("Bash", command=command)

    def test_edits_with_allowed_flags_on_my_ready_pr(self) -> None:
        for command in [
            f"gh pr edit {PR_URL} --add-reviewer dana-acme",
            f"gh pr edit {PR_URL} --title 'Keep the template' --body 'Fixes the footer.'",
            f"gh pr edit {PR_URL}/ --add-reviewer=Dana-Acme",
        ]:
            with self.subTest(command=command):
                self.assertIsNone(self.denial(self.bash(command)))
                self.assertTrue(self.allowed(self.bash(command)))
        self.assertEqual(self.item(PR)["history"][-1]["event"], ledger.AUTO_APPROVED)

    def test_refusals(self) -> None:
        for command in [
            f"gh pr edit {PR_URL} --add-reviewer mallory",
            f"gh pr edit {PR_URL} --add-label urgent",
            f"gh pr edit {PR_URL} --body-file notes.md",
            f"gh pr edit {PR_URL}",
            f"gh pr edit {PR_URL} --title x && gh pr merge {PR_URL}",
            f'gh pr edit {PR_URL} --title "$(cat x)"',
            f"gh pr edit {PR_URL} --title $TITLE",
            "gh pr edit 102 --title x",
            "gh pr edit https://github.com/acme/other/pull/102 --title x",
            f"gh pr edit {PR_URL.replace('102', '103')} --title x",
            f"gh pr merge {PR_URL} --squash",
            f"gh pr comment {PR_URL} --body hi",
            "gh api repos/acme/exporter/pulls/102",
        ]:
            with self.subTest(command=command):
                self.assertFalse(self.allowed(self.bash(command)))
                self.assertIsNotNone(self.denial(self.bash(command)))

    def test_housekeeping_leaves_another_pending_proposal_alone(self) -> None:
        self.propose_touch()
        self.assertTrue(self.allowed(self.bash(f"gh pr edit {PR_URL} --title x")))
        self.assertNotIn("auto_approved", self.item(PR)["proposal"])
        self.assertIn(f"run: gh pr edit {PR_URL} --title x", run("approvals", "--auto"))

    def test_ready_needs_a_ci_green_report_after_the_last_hand_over(self) -> None:
        command = f"gh pr ready {PR_URL}"
        self.assertTrue(self.allowed(self.bash(command)))
        self.hand_over()
        self.assertFalse(self.allowed(self.bash(command)))
        self.report(ci="red")
        self.assertFalse(self.allowed(self.bash(command)))
        self.assertIn("CI-green report", self.denial(self.bash(command)) or "")
        self.report()
        self.assertTrue(self.allowed(self.bash(command)))
        self.assertFalse(self.allowed(self.bash(f"{command} --undo")))


RE_REVIEW = "/review-pr"


class StandingCommentTest(StandingTestCase):
    """pr-housekeeping and BOSUN_STANDING_GH_COMMENTS: a listed comment body on my ready PR."""

    def setUp(self) -> None:
        super().setUp()
        self.env.update(BOSUN_STANDING_GH_COMMENTS=f"{RE_REVIEW} | /rerun ci")
        self.report()  # ready: CI green, nothing open

    def bash(self, command: str) -> dict[str, Any]:
        return self.event("Bash", command=command)

    def comment(self, body: str = RE_REVIEW, flag: str = "--body", url: str = PR_URL) -> dict[str, Any]:
        return self.bash(f"gh pr comment {url} {flag} '{body}'")

    def test_a_listed_body_goes_out_and_is_recorded(self) -> None:
        for event in (self.comment(), self.comment(flag="-b"), self.comment("/rerun ci")):
            with self.subTest(command=event["tool_input"]["command"]):
                self.assertIsNone(self.denial(event))
                self.assertTrue(self.allowed(event))
        last = self.item(PR)["history"][-1]
        self.assertEqual((last["event"], last["comment"]), (ledger.AUTO_APPROVED, "/rerun ci"))
        self.assertTrue(self.allowed(self.bash(f"gh pr comment {PR_URL} --body={RE_REVIEW}")))

    def test_anything_else_needs_an_approval(self) -> None:
        for command in [
            f"gh pr comment {PR_URL} --body '{RE_REVIEW} please'",  # a near miss
            f"gh pr comment {PR_URL} --body '/review'",
            f"gh pr comment {PR_URL} --body '{RE_REVIEW}' --edit-last",  # an extra flag
            f"gh pr comment {PR_URL} --body-file note.md",
            f"gh pr comment {PR_URL}",
            f"gh pr comment {PR_URL} --body '{RE_REVIEW}' && gh pr merge {PR_URL}",
            f"gh pr comment {PR_URL.replace('102', '103')} --body '{RE_REVIEW}'",  # not my PR in the ledger
            f"gh pr comment https://github.com/acme/other/pull/102 --body '{RE_REVIEW}'",
            f"gh pr review {PR_URL} --comment --body '{RE_REVIEW}'",
        ]:
            with self.subTest(command=command):
                self.assertFalse(self.allowed(self.bash(command)))
                self.assertIsNotNone(self.denial(self.bash(command)))

    def test_only_on_a_ready_pr(self) -> None:
        self.hand_over()
        self.assertFalse(self.allowed(self.comment()))
        self.assertIn("CI-green report", self.denial(self.comment()) or "")
        self.report(open_points="one thread left")
        self.assertFalse(self.allowed(self.comment()))
        self.report()
        self.assertTrue(self.allowed(self.comment()))

    def test_none_without_the_setting_or_the_class(self) -> None:
        self.assertFalse(self.allowed(self.comment(), BOSUN_STANDING_GH_COMMENTS=""))
        self.assertFalse(self.allowed(self.comment(), BOSUN_STANDING_CLASSES="pr-review-traffic"))

    def test_at_most_the_cap_per_body_and_pr(self) -> None:
        for _ in range(2):
            self.assertTrue(self.allowed(self.comment(), BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR="2"))
        self.assertTrue(self.allowed(self.comment("/rerun ci"), BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR="2"))
        denial = self.denial(self.comment(), BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR="2") or ""
        self.assertIn("2 such comments already went out", denial)
        self.assertFalse(self.allowed(self.comment(), BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR="2"))
        self.assertTrue(self.allowed(self.comment(), BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR="0"))  # no cap

    def test_the_default_cap(self) -> None:
        for _ in range(approvals.DEFAULT_COMMENTS_PER_PR):
            self.assertTrue(self.allowed(self.comment()))
        self.assertFalse(self.allowed(self.comment()))

    def test_settings_parsing(self) -> None:
        standing = approvals.Standing.from_settings("pr-housekeeping", "", "", "", " /a | | /b c ", "x")
        self.assertEqual(standing.gh_comments, frozenset({"/a", "/b c"}))
        self.assertEqual(standing.comments_per_pr, approvals.DEFAULT_COMMENTS_PER_PR)
        self.assertEqual(approvals.Standing.from_settings("", "", "", "", "", "0").comments_per_pr, 0)
        self.assertEqual(approvals.Standing.from_settings("", "", "", "").gh_comments, frozenset())


class TranscriptTest(unittest.TestCase):
    def test_delivered_prompts_include_mid_turn_ones_and_skip_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            moment = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
            entries = [
                typed("first", moment),
                typed("summary", moment, isCompactSummary=True),
                {
                    "type": "queue-operation",
                    "operation": "enqueue",
                    "timestamp": stamp(moment),
                    "content": "x",
                },
                {
                    "type": "queue-operation",
                    "operation": "remove",
                    "timestamp": stamp(moment),
                    "content": "while busy",
                },
                {
                    "type": "queue-operation",
                    "operation": "remove",
                    "timestamp": stamp(moment),
                    "content": "<task-notification>x",
                },
            ]
            path.write_text("".join(json.dumps(e) + "\n" for e in entries))
            prompts = claude_state.delivered_prompts(path)
        self.assertEqual(
            [(p.text, p.origin) for p in prompts], [("first", "typed"), ("while busy", "mid-turn")]
        )


if __name__ == "__main__":
    unittest.main()
