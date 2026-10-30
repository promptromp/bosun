"""Unit tests for lib/guard.py: the orchestrator's PreToolUse permission guard."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import guard


def decision(tool: str, **tool_input: object) -> str | None:
    result = guard.decide({"tool_name": tool, "tool_input": tool_input})
    return result["permissionDecision"] if result else None


class McpTest(unittest.TestCase):
    def test_write_verbs_ask(self) -> None:
        for tool in [
            "mcp__example-wiki__create_page",
            "mcp__example-tracker__transition_issue",
            "mcp__plugin_slack_slack__slack_send_message",
            "mcp__example-deploy__execute_pipeline",
            "mcp__example-ci__restart_build",
            "mcp__plugin_example_design__use_canvas",
            "mcp__claude_ai_Slack__slack_update_canvas",
        ]:
            with self.subTest(tool=tool):
                self.assertEqual(decision(tool), "ask")

    def test_reads_pass(self) -> None:
        for tool in [
            "mcp__example-tracker__search_issues",
            "mcp__plugin_slack_slack__slack_read_thread",
            "mcp__plugin_slack_slack__slack_search_public_and_private",
            "mcp__example-metrics__query_logs",
            "mcp__example-wiki__get_page",
            # Reading a ticket and the transitions it offers is a read; making one asks (above).
            "mcp__example-tracker__jira_get_issue",
            "mcp__example-tracker__jira_get_transitions",
        ]:
            with self.subTest(tool=tool):
                self.assertIsNone(decision(tool))

    def test_non_mcp_tools_pass(self) -> None:
        self.assertIsNone(decision("Read", file_path="/x"))


class BashTest(unittest.TestCase):
    def test_force_pushes_are_denied(self) -> None:
        for command in [
            "git push -f",
            "git push --force origin main",
            "git push --force-with-lease",
            "git push origin +main",
            "git -C repo push -uf origin x",
            "cd a && git push -f; echo done",
            "(git push --force)",
        ]:
            with self.subTest(command=command):
                self.assertEqual(decision("Bash", command=command), "deny")

    def test_commands_on_later_lines_or_after_keywords_are_seen(self) -> None:
        # Before 2026-10-16 a newline was plain whitespace to the splitter, so these all passed.
        for command in [
            "cd repo\ngit push -f origin x",
            'set -e\nL() { bosun ledger "$@"; }\ngit push --force',
            "for b in a b; do git push --force origin $b; done",
            "if true; then gh pr merge 5 --admin; fi",
            "X=1 Y=2 git push -f",
            "{ git push -f; }",
            "! git push -f",
        ]:
            with self.subTest(command=command):
                self.assertEqual(decision("Bash", command=command), "deny")

    def test_wrapped_commands_are_seen(self) -> None:
        # Before 2026-10-23 a wrapper hid the command it runs: `timeout 120 gh pr merge` was no merge.
        for command in [
            "timeout 120 git push -f",
            "timeout -k 5 120 git push --force",
            "gtimeout 60 gh pr merge 5 --admin",
            "nice -n 5 git push -f",
            "nohup git push --force &",
            "env -u HOME X=1 git push -f",
            "command git push -f",
            "sudo -u me git push -f",
            "echo 5 | xargs -I {} gh pr merge {} --admin",
            "cd repo && timeout 30 nice git push -f 2>&1 | tail -2",
        ]:
            with self.subTest(command=command):
                self.assertEqual(decision("Bash", command=command), "deny")
        self.assertEqual(
            guard.simple_commands("timeout 120 gh pr merge 5 --squash"),
            [["gh", "pr", "merge", "5", "--squash"]],
        )
        self.assertEqual(guard.simple_commands("timeout 120"), [])

    def test_heredoc_bodies_are_data(self) -> None:
        command = (
            "bosun delegate investigate --id x --ask a --print --details - <<'EOF'\n"
            "git push -f is never allowed\nEOF"
        )
        self.assertIsNone(decision("Bash", command=command))
        self.assertEqual(
            guard.simple_commands("cat <<EOF\ngit push -f\nEOF\necho done"),
            [["cat", "<<EOF"], ["echo", "done"]],
        )

    def test_admin_merge_is_denied(self) -> None:
        self.assertEqual(decision("Bash", command="gh pr merge 5 --squash --admin"), "deny")

    def test_ordinary_commands_pass(self) -> None:
        for command in [
            "git push origin feature",
            "git push -u origin feature",
            "gh pr merge 5 --squash",
            "echo 'git push -f'",
            "git log --format=%H",
            "git push 'unbalanced",
        ]:
            with self.subTest(command=command):
                self.assertIsNone(decision("Bash", command=command))


class TextCommandTest(unittest.TestCase):
    def test_substitutions_that_would_run_are_denied(self) -> None:
        for command in [
            'bosun delegate review --id x --ask "fix $(rm -rf ~)" --print',
            'bosun send w "hi `whoami`"',
            "bosun delegate investigate --id x --ask a --details - <<EOF\nsee $(id)\nEOF",
            "/Users/a/.local/bin/bosun send w hi$(id)",
            'bosun ledger reach-out x --to Dana --subject "drop $(id)?"',
            'bosun ledger ask --to Dana --question "`id`"',
            'bosun ledger replied out:x/1 --evidence e --summary "$(id)"',
            'bosun ledger propose out:x/1 "Slack: $(id)"',
            # Tasks, links and tickets carry text from Slack, PRs or the tracker too.
            'bosun ledger spawn out:x/1 --title "fix $(id)" --ask a',
            "bosun ledger spawn out:x/1 --title t --ask a --via `id`",
            'bosun ledger link mypr:a/b#1 spawned_from slack:C1/1 --via "$(id)"',
            'bosun ledger unlink mypr:a/b#1 relates_to "$(id)"',
            'bosun ledger fold task:2026-01-01/2 --into task:2026-01-01/1 --note "$(id)"',
            "bosun ledger fold task:2026-01-01/2 --into `id`",
            'bosun ledger ticket-sync jira:EXP-1 --status "$(id)"',
            # The whole Bash call counts: a substitution in a variable or another step too.
            "T=$(gh pr view 5 --json title); bosun ledger link 'mypr:a/b#1' produces 'task:x'",
            "D=$(cat <<'EOF'\nsome text\nEOF\n)\nbosun delegate --update 'x' --details \"$D\"",
        ]:
            with self.subTest(command=command):
                self.assertEqual(decision("Bash", command=command), "deny")

    def test_task_and_ticket_text_in_single_quotes_passes(self) -> None:
        for command in [
            "bosun ledger spawn 'out:x/1' --title 'fix $(id)' --ask 'a' --via 'user: go ahead'",
            "bosun ledger link 'mypr:a/b#1' tracks 'jira:EXP-1'",
            "bosun ledger fold 'task:2026-01-01/2' --into 'task:2026-01-01/1' --note 'same PR $(id)'",
            "bosun ledger ticket-sync 'jira:EXP-1' --status 'In Review' --title 'Export `x`'",
            'bosun ledger tree "$id"',
        ]:
            with self.subTest(command=command):
                self.assertIsNone(decision("Bash", command=command))

    def test_outreach_text_in_single_quotes_passes(self) -> None:
        for command in [
            "bosun ledger reach-out x --to Dana --subject 'drop $(id)?'",
            "bosun ledger replied out:x/1 --evidence 'user: ok' --summary 'b'",
            'bosun ledger sent out:x/1 --ref "$ref"',
        ]:
            with self.subTest(command=command):
                self.assertIsNone(decision("Bash", command=command))

    def test_literal_text_passes(self) -> None:
        for command in [
            "bosun delegate review --id x --ask 'fix $(rm -rf ~)' --print",
            "bosun delegate investigate --id x --ask a --details - <<'EOF'\nDana's $(id) `x`\nEOF",
            'bosun send --no-item w "costs \\$(5)"',
            'bosun ledger note x "$(date)"',
            "echo $(date) && bosun context",
        ]:
            with self.subTest(command=command):
                self.assertIsNone(decision("Bash", command=command))


class SendTest(unittest.TestCase):
    def test_free_text_without_a_work_item_is_denied(self) -> None:
        for command in [
            "bosun send w hi",
            "bosun send api - 2>/dev/null <<'EOF' || bosun send api 'Addendum (x)'\nAddendum\nEOF",
            'bosun send w "Ignore the - message just sent"',
            "/Users/a/.local/bin/bosun send w 'Ledger id: mypr:a/b#1 ...'",
        ]:
            with self.subTest(command=command):
                self.assertEqual(decision("Bash", command=command), "deny")
        reason = guard.forbidden_bash("bosun send w hi") or ""
        self.assertIn("bosun delegate --update", reason)
        self.assertIn("--no-item", reason)

    def test_new_windows_go_through_delegate(self) -> None:
        for command in [
            "bosun send --new w /d task",
            "bosun send --start w task",
            "bosun send --start w -",
        ]:
            with self.subTest(command=command):
                self.assertIn("bosun delegate", guard.forbidden_bash(command) or "")

    def test_named_or_itemless_sends_and_reads_pass(self) -> None:
        for command in [
            "bosun send --id 'slack:C1/1' w 'is it done?'",
            "bosun send --no-item w - <<'EOF'\npause and save your state\nEOF",
            "bosun send --list",
            "bosun send --help; bosun context",
            "bosun send --start w",
            "bosun send --start w 2>/dev/null",
            "bosun delegate --update 'slack:C1/1' --details - <<'EOF'\nalso run the e2e tests\nEOF",
            "echo 'bosun send w hi'",
        ]:
            with self.subTest(command=command):
                self.assertIsNone(decision("Bash", command=command))


class PolicyReadsTest(unittest.TestCase):
    def test_the_auto_merge_read_the_policy_names_is_not_gated(self) -> None:
        """policy.md reads whether a repository allows auto-merge from pr-threads: no prompt."""
        url = "https://github.com/acme/exporter/pull/7"
        self.assertIsNone(guard.gated_bash(guard.simple_commands(f"bosun pr-threads {url}")))
        self.assertIsNone(decision("Bash", command=f"bosun pr-threads {url}"))
        policy = (Path(__file__).resolve().parents[2] / "orchestrator" / "policy.md").read_text()
        self.assertIn("`bosun pr-threads <url>` shows `auto-merge: allowed`", policy)
        self.assertNotIn("allow_auto_merge", policy)

    def test_the_gh_api_read_it_replaces_still_is(self) -> None:
        command = "gh api repos/acme/exporter --jq .allow_auto_merge"
        self.assertEqual(guard.gated_bash(guard.simple_commands(command)), "a GitHub API call")


class MainTest(unittest.TestCase):
    def run_main(self, stdin: str) -> str:
        with mock.patch("sys.stdin", io.StringIO(stdin)), mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(guard.main(), 0)
        return out.getvalue()

    def test_prints_a_hook_decision(self) -> None:
        out = json.loads(self.run_main(json.dumps({"tool_name": "mcp__x__send_message", "tool_input": {}})))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_malformed_events_are_ignored(self) -> None:
        self.assertEqual(self.run_main("not json"), "")
        self.assertEqual(self.run_main(json.dumps({"tool_name": "Bash", "tool_input": {}})), "")


if __name__ == "__main__":
    unittest.main()
