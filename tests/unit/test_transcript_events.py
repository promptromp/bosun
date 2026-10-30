"""Unit tests for what lib/claude_state.py reads from a transcript for `bosun learn` (events),
its transcript and memory helpers, and `describe` (used by `bosun sessions`)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import claude_state  # noqa: E402


T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
SINCE, UNTIL = T0 - timedelta(hours=1), T0 + timedelta(days=1)


def at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def prompt(minutes: float, text: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": at(minutes),
        "origin": {"kind": "human"},
        "message": {"content": text},
        **extra,
    }


def call(minutes: float, call_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    block = {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}
    return {"type": "assistant", "timestamp": at(minutes), "message": {"content": [block]}}


def result(
    minutes: float, call_id: str, text: str, error: bool = False, source: str = "config"
) -> dict[str, Any]:
    block = {"type": "tool_result", "tool_use_id": call_id, "content": text, "is_error": error}
    return {
        "type": "user",
        "timestamp": at(minutes),
        "permissionDecision": {"decision": "accept", "source": source},
        "message": {"content": [block]},
    }


def queue(minutes: float, operation: str, content: str | None = None) -> dict[str, Any]:
    return {"type": "queue-operation", "timestamp": at(minutes), "operation": operation, "content": content}


def queued_command(
    minutes: float, text: str, mode: str = "prompt", origin: dict[str, str] | None = None
) -> dict[str, Any]:
    """A prompt typed while the session was busy, as Claude Code stores it once picked up."""
    attachment = {
        "type": "queued_command",
        "prompt": text,
        "commandMode": mode,
        "origin": origin or {"kind": "human"},
    }
    return {"type": "attachment", "timestamp": at(minutes), "attachment": attachment}


class EventsTest(unittest.TestCase):
    def events(self, *entries: dict[str, Any]) -> list[claude_state.Event]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in entries) + "\nnot json\n")
            events, _calls = claude_state.read_events(path, SINCE, UNTIL)
        return events

    def kinds(self, *entries: dict[str, Any]) -> list[tuple[str, str]]:
        return [(e.kind, e.origin) for e in self.events(*entries)]

    def test_prompts_and_slash_commands(self) -> None:
        events = self.events(
            prompt(0, "<command-name>/bosun:chores</command-name><command-args>check-in</command-args>"),
            prompt(1, "<task-notification>done</task-notification>"),
            prompt(2, "meta", isMeta=True),
            prompt(3, "<pasted_content id='x'>Ledger id: a</pasted_content>", promptSource="queued"),
        )
        self.assertEqual(
            [(e.text, e.origin) for e in events],
            [("/bosun:chores check-in", "typed"), ("Ledger id: a", "queued")],
        )

    def test_messages_that_arrive_while_busy(self) -> None:
        events = self.kinds(
            queue(0, "enqueue", "first"),
            queue(0.1, "remove", "first"),
            queue(1, "enqueue", "second"),
            queue(1.1, "dequeue"),
            prompt(1.1, "second", promptSource="queued"),
            queue(2, "enqueue", "third"),
        )
        self.assertEqual(events, [("prompt", "mid-turn"), ("prompt", "queued"), ("prompt", "undelivered")])

    def test_a_prompt_typed_while_busy_is_stored_as_a_queued_command(self) -> None:
        # Claude Code 2.1.30x: enqueue, dequeue, then only an attachment carries the text.
        events = self.events(
            queue(0, "enqueue", "keep the customer footer"),
            queue(0.5, "dequeue"),
            queued_command(0.5, "keep the customer footer"),
            queued_command(0.6, "<task-notification>done</task-notification>", mode="task-notification"),
            queued_command(0.7, "from a peer", origin={"kind": "peer"}),
        )
        self.assertEqual([(e.text, e.origin) for e in events], [("keep the customer footer", "queued")])

    def test_user_prompts_include_queued_ones_and_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            entries = [
                prompt(0, "go with option b"),
                result(1, "c1", "ok"),
                prompt(2, "<task-notification>x</task-notification>"),
                queued_command(3, "drop the footer row"),
                prompt(4, "an injected one", origin={"kind": "task-notification"}),
                {"type": "attachment", "timestamp": at(5), "attachment": {"type": "date"}},
            ]
            path.write_text("\n".join(json.dumps(e) for e in entries) + "\n{broken\n")
            prompts = claude_state.user_prompts(path)
            later = claude_state.user_prompts(path, T0 + timedelta(minutes=1))
        self.assertEqual(
            [(p.text, p.origin) for p in prompts],
            [("go with option b", "typed"), ("drop the footer row", "queued")],
        )
        self.assertEqual([p.text for p in later], ["drop the footer row"])

    def test_permission_prompt_rejection_question_and_error(self) -> None:
        events = self.events(
            call(0, "a", "Bash", {"command": "gh api repos/acme/exporter\nsecond line"}),
            result(600, "a", "{}", source="user_temporary"),
            call(601, "b", "Bash", {"command": "rm -rf build"}),
            result(
                602,
                "b",
                f"{claude_state.USER_REJECTED}. To tell you how to proceed, the user said: keep build",
                error=True,
            ),
            call(603, "c", "AskUserQuestion", {"questions": [{"question": "Which one?"}]}),
            result(605, "c", "The user answered: the second"),
            call(606, "d", "Read", {"file_path": "/x"}),
            result(606, "d", "missing", error=True),
        )
        summary = [(e.kind, e.tool, e.text, e.waited) for e in events if e.kind != "command"]
        self.assertEqual(
            summary,
            [
                ("permission", "Bash: gh api repos/acme/exporter", "", 36000.0),
                ("rejected", "Bash: rm -rf build", "keep build", 60.0),
                ("question", "Which one?", "The user answered: the second", 120.0),
                ("tool-error", 'Read: {"file_path": "/x"}', "missing", None),
            ],
        )

    def test_shell_commands_are_events_too(self) -> None:
        events = self.events(call(0, "a", "Bash", {"command": "bosun ledger list"}), result(0.1, "a", "ok"))
        self.assertEqual([(e.kind, e.text) for e in events], [("command", "bosun ledger list")])

    def test_turn_end_reply_and_notices(self) -> None:
        events = self.events(
            {
                "type": "assistant",
                "timestamp": at(0),
                "message": {"content": [{"type": "text", "text": "Done."}]},
            },
            {"type": "system", "subtype": "turn_duration", "timestamp": at(0.1)},
            {"type": "system", "subtype": "compact_boundary", "timestamp": at(1)},
            {
                "type": "system",
                "subtype": "informational",
                "level": "warning",
                "timestamp": at(2),
                "content": "Remote Control disconnected",
            },
        )
        self.assertEqual(
            [(e.kind, e.text) for e in events],
            [("reply", "Done."), ("notice", "context compacted"), ("notice", "Remote Control disconnected")],
        )

    def test_only_the_window(self) -> None:
        self.assertEqual(self.events(prompt(-120, "too early"), prompt(60 * 30, "too late")), [])


class FilesTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in (
            ("PROJECTS_DIR", self.root / "projects"),
            ("LIVE_SESSIONS_DIR", self.root / "sessions"),
        ):
            patch = mock.patch.object(claude_state, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_transcripts_since_and_subagents(self) -> None:
        project = claude_state.project_dir("/work/repo")
        (project / "s1" / "subagents").mkdir(parents=True)
        (project / "s1.jsonl").write_text(json.dumps({"type": "custom-title", "customTitle": "repo"}) + "\n")
        (project / "s1" / "subagents" / "agent-a.jsonl").write_text("")
        found = claude_state.transcripts_since("/work/repo", 0)
        self.assertEqual([(t.session_id, t.title) for t in found], [("s1", "repo")])
        self.assertEqual(
            claude_state.subagent_transcripts(found[0].path), [project / "s1" / "subagents" / "agent-a.jsonl"]
        )
        self.assertEqual(claude_state.transcripts_since("/work/repo", 4e9), [])

    def test_describe_a_running_claude(self) -> None:
        claude_state.LIVE_SESSIONS_DIR.mkdir()
        record = {
            "pid": 7,
            "sessionId": "s1",
            "name": "repo",
            "status": "idle",
            "statusUpdatedAt": 1700000000000,
            "version": "2",
            "cwd": "/work/repo",
        }
        (claude_state.LIVE_SESSIONS_DIR / "7.json").write_text(json.dumps(record))
        self.assertIsNone(claude_state.describe("7")["transcript"])  # type: ignore[index]
        path = claude_state.transcript_path("/work/repo", "s1")
        path.parent.mkdir(parents=True)
        path.write_text("")
        self.assertEqual(claude_state.describe("7"), {**record, "transcript": str(path)})
        self.assertIsNone(claude_state.describe("8"))

    def test_memory_notes(self) -> None:
        note = claude_state.add_memory_note(
            "/work/repo", "open-normal-prs", "Open normal PRs", "Never open drafts."
        )
        self.assertIn('description: "Open normal PRs"', note.read_text())
        self.assertTrue(note.read_text().endswith("Never open drafts.\n"))
        index = (claude_state.memory_dir("/work/repo") / "MEMORY.md").read_text()
        self.assertIn("- [Open normal PRs](open-normal-prs.md)", index)
        with self.assertRaises(FileExistsError):
            claude_state.add_memory_note("/work/repo", "open-normal-prs", "again", "x")


if __name__ == "__main__":
    unittest.main()
