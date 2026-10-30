"""Claude Code's hook signals: recording each event as one line, and reading them back."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import signals


NOON = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
SESSION = "alpha-session"


def payload(event: str, **fields: Any) -> dict[str, Any]:
    """A hook's input, shaped like Claude Code 2.1.312's (fictional values)."""
    return {"session_id": SESSION, "hook_event_name": event, "transcript_path": "/x.jsonl", **fields}


PERMISSION = payload(
    "PermissionRequest",
    tool_name="Bash",
    tool_input={"command": "make deploy-docs", "description": "Publish the docs"},
)
ASKING = payload(
    "Notification", notification_type="permission_prompt", message="Claude needs your permission"
)
IDLE = payload("Notification", notification_type="idle_prompt", message="Claude is waiting for your input")
STOP_RUNNING = payload(
    "Stop",
    background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running", "description": "Run the export suite"},
        {"id": "b2", "type": "shell", "status": "running", "command": "sleep 600"},
        {"id": "c3", "type": "shell", "status": "completed", "command": "ls"},
    ],
)
STOP_CLEAN = payload("Stop", background_tasks=[])
LIMIT = payload(
    "StopFailure",
    error="rate_limit",
    last_assistant_message="You've hit your individual spend limit · run /usage-credits to ask your admin",
)
THROTTLED = payload(
    "StopFailure", error="rate_limit", last_assistant_message="API Error: 429 too many requests"
)


class SignalFromTest(unittest.TestCase):
    def test_a_permission_request_keeps_the_tool_and_what_it_runs(self) -> None:
        line = signals.signal_from(PERMISSION, NOON)
        assert line is not None
        self.assertEqual(line["event"], "PermissionRequest")
        self.assertEqual((line["kind"], line["detail"]), ("Bash", "make deploy-docs"))
        self.assertEqual(line["session"], SESSION)
        self.assertNotIn("transcript_path", line)

    def test_a_stop_keeps_only_the_work_still_running(self) -> None:
        line = signals.signal_from(STOP_RUNNING, NOON)
        assert line is not None
        self.assertEqual(
            line["running"],
            [{"type": "subagent", "what": "Run the export suite"}, {"type": "shell", "what": "sleep 600"}],
        )

    def test_a_stop_failure_keeps_the_error_type_and_text(self) -> None:
        line = signals.signal_from(LIMIT, NOON)
        assert line is not None
        self.assertEqual(line["kind"], "rate_limit")
        self.assertIn("spend limit", line["detail"])

    def test_session_events_keep_their_source_or_reason(self) -> None:
        start = signals.signal_from(payload("SessionStart", source="compact"), NOON)
        end = signals.signal_from(payload("SessionEnd", reason="prompt_input_exit"), NOON)
        self.assertEqual((start or {}).get("kind"), "compact")
        self.assertEqual((end or {}).get("kind"), "prompt_input_exit")

    def test_details_are_redacted_and_bounded(self) -> None:
        long = payload("Notification", notification_type="x", message="mail me@example.com " + "y" * 500)
        line = signals.signal_from(long, NOON) or {}
        self.assertIn("<email>", line["detail"])
        self.assertLessEqual(len(line["detail"]), signals.DETAIL_CHARS)

    def test_other_events_are_not_kept(self) -> None:
        self.assertIsNone(signals.signal_from(payload("PreToolUse", tool_name="Bash"), NOON))


class FileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def add(self, event: dict[str, Any], moment: datetime = NOON, window: str = "alpha") -> None:
        line = signals.signal_from(event, moment)
        assert line is not None
        signals.append(self.state_dir, window, line, 1 << 20)

    def load(self, session: str = SESSION) -> signals.Signals:
        return signals.Signals.load(self.state_dir, "alpha", session)

    def test_asking_names_the_permission_prompt(self) -> None:
        self.add(PERMISSION)
        self.add(ASKING, NOON + timedelta(seconds=6))
        self.assertEqual(self.load().asking(), "Bash: make deploy-docs")

    def test_asking_ignores_an_older_prompt_and_idle_reminders(self) -> None:
        self.add(PERMISSION)
        self.add(IDLE, NOON + timedelta(hours=1))
        later = (NOON + timedelta(minutes=30)).astimezone().replace(tzinfo=None)
        self.assertEqual(self.load().asking(later), "")
        self.assertEqual(self.load().asking(), "Bash: make deploy-docs")

    def test_another_session_of_the_window_never_counts(self) -> None:
        self.add(PERMISSION)
        self.assertEqual(self.load("a-later-claude").asking(), "")
        self.assertEqual(signals.Signals.load(self.state_dir, "alpha", None).entries, ())

    def test_running_is_the_last_finished_turn(self) -> None:
        self.add(STOP_RUNNING)
        self.assertEqual(
            signals.describe(self.load().running()), "subagent `Run the export suite`; shell `sleep 600`"
        )
        self.add(STOP_CLEAN, NOON + timedelta(minutes=5))
        self.assertEqual(self.load().running(), [])

    def test_a_usage_limit_stop_lasts_until_a_turn_finishes(self) -> None:
        self.add(LIMIT)
        self.assertIsNotNone(self.load().usage_limit_stop())
        self.add(STOP_CLEAN, NOON + timedelta(minutes=5))
        self.assertIsNone(self.load().usage_limit_stop())

    def test_a_short_rate_limit_is_not_a_usage_limit(self) -> None:
        self.add(THROTTLED)
        self.assertIsNone(self.load().usage_limit_stop())

    def test_the_file_keeps_its_newest_half_past_the_limit(self) -> None:
        line = signals.signal_from(ASKING, NOON) or {}
        for n in range(40):
            signals.append(self.state_dir, "alpha", {**line, "n": n}, 2000)
        kept = [
            json.loads(row)["n"]
            for row in signals.signals_path(self.state_dir, "alpha").read_text().splitlines()
        ]
        self.assertLess(len(kept), 40)
        self.assertEqual(kept[-1], 39)


class RecordTest(unittest.TestCase):
    def test_bad_input_is_reported_and_never_raised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(io.StringIO()) as err:
            signals.record("alpha", "{not json", {"BOSUN_STATE_DIR": tmp})
            signals.record("alpha", json.dumps(PERMISSION), {"BOSUN_STATE_DIR": tmp})
            self.assertTrue(signals.signals_path(Path(tmp), "alpha").is_file())
        self.assertIn("ERROR signals", err.getvalue())

    def test_a_window_name_cannot_leave_the_signals_folder(self) -> None:
        path = signals.signals_path(Path("/state"), "../x/y")
        self.assertEqual(path.parent, Path("/state") / signals.SIGNALS_DIR)


if __name__ == "__main__":
    unittest.main()
