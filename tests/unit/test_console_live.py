"""Unit tests for the console's live facts (lib/console_live.py): the settings it is given, one
collection with fake commands over a scratch state directory (sessions, the watchdog's facts and
checks, events, the Slack watcher, the login), what a failing reader leaves, that nothing is
written and Claude is never started, and the cache. Fictional data: acme's invoice exporter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import claude_state
import console_live
import events
import login
import slack_watch
import watchdog
from console_live import Collector, LiveCache, LiveError, Settings


def window(name: str, kind: str, pid: int | None = None, status: str = "idle") -> dict[str, Any]:
    """A window as `bosun sessions --json` lists it."""
    running = {"pid": pid, "status": status, "statusUpdatedAt": 1700000000000, "transcript": None}
    return {
        "name": name,
        "kind": kind,
        "dir": f"/Users/me/repos/{name}",
        "model": "-",
        "effort": "-",
        "window": True,
        "agent": running if pid else None,
    }


WINDOWS = [
    window("orch", "orchestrator", 4101),
    window("exporter", "session", 4102, "waiting"),
    window("checkin", "checkin"),
]
WATCHDOG_FACTS = {
    "session_exists": True,
    "windows": [{"name": "orch", "agent_pid": 4101}, {"name": "exporter", "agent_pid": 4102}],
    "checkin_window": True,
    "checkin_loop": True,
    "morning_job": {"state": "not running", "last_exit_code": "0"},
}


def environment(state_dir: Path) -> dict[str, str]:
    """What `bosun console` passes: the watchdog's settings and the console's own."""
    return {
        "BOSUN_STATE_DIR": str(state_dir),
        "BOSUN_LOG_DIR": str(state_dir / "logs"),
        "BOSUN_SESSION_NAME": "acme-bosun",
        "BOSUN_ORCHESTRATOR": "orch",
        "BOSUN_DAYS": "MTWRF",
        "BOSUN_START_TIME": "08:00",
        "BOSUN_WAKE_DELAY_MINUTES": "1",
        "BOSUN_AWAKE_UNTIL": "20:00",
        "BOSUN_ALERT_WAITING_MINUTES": "30",
        "BOSUN_CHECKIN_MINUTES": "60",
        "BOSUN_WATCHDOG_GRACE_MINUTES": "15",
        "BOSUN_WATCHDOG_DELEGATED_HOURS": "8",
        "BOSUN_LEARN_ENABLED": "0",
        "BOSUN_LEARN_AT": "18:30",
        "BOSUN_LEARN_TIMEOUT_MINUTES": "30",
        "BOSUN_EVENTS_ENABLED": "1",
        "BOSUN_EVENTS_INTERVAL_SECONDS": "30",
        "BOSUN_SLACK_WATCH_ENABLED": "1",
        "BOSUN_SLACK_WATCH_MINUTES": "15",
        "BOSUN_CLAUDE_BIN": "/nonexistent/claude",
        "BOSUN_TMUX_SOCKET": "bosun",
        "BOSUN_EVENTS_MAX_PER_DAY": "12",
        "BOSUN_SLACK_WATCH_MAX_USD_PER_DAY": "2.5",
    }


class FakeCommands:
    """`bosun sessions --json` and `bosun watchdog --facts`, recording what was run."""

    def __init__(self, outputs: dict[str, str]) -> None:
        self.outputs = outputs
        self.ran: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.ran.append(argv)
        key = " ".join(argv[1:])
        if key not in self.outputs:
            raise LiveError(f"{key} exited 1: boom")
        return self.outputs[key]


def good_commands() -> FakeCommands:
    return FakeCommands(
        {"sessions --json": json.dumps(WINDOWS), "watchdog --facts": json.dumps(WATCHDOG_FACTS)}
    )


def tree(directory: Path) -> dict[str, float]:
    return {str(p.relative_to(directory)): p.stat().st_mtime for p in directory.rglob("*")}


class SettingsTestCase(unittest.TestCase):
    def test_from_the_environment(self) -> None:
        settings = Settings.from_env(environment(Path("/state")))
        self.assertEqual((settings.session, settings.tmux_socket), ("acme-bosun", "bosun"))
        self.assertEqual((settings.events_max_per_day, settings.slack_max_usd_per_day), (12, 2.5))
        self.assertTrue(settings.events_enabled and settings.slack_watch_enabled)
        self.assertFalse(settings.learn_enabled)

    def test_defaults_and_bad_numbers(self) -> None:
        settings = Settings.from_env({"BOSUN_STATE_DIR": "/state", "BOSUN_EVENTS_MAX_PER_DAY": "many"})
        self.assertEqual(settings.log_dir, Path("/state/logs"))
        self.assertEqual(settings.events_max_per_day, console_live.DEFAULT_EVENTS_MAX_PER_DAY)
        self.assertEqual(settings.slack_max_usd_per_day, console_live.DEFAULT_SLACK_MAX_USD_PER_DAY)


class CollectTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        (self.state / "logs").mkdir()
        self.settings = Settings.from_env(environment(self.state))
        # Claude is never started: `claude auth status` would fail the test if it ran.
        never = mock.patch.object(claude_state, "auth_status", side_effect=AssertionError("claude started"))
        never.start()
        self.addCleanup(never.stop)
        records = mock.patch.object(claude_state, "LIVE_SESSIONS_DIR", self.state / "claude-sessions")
        records.start()
        self.addCleanup(records.stop)

    def write_state(self) -> None:
        queue_dir = events.events_dir(self.state)
        queue_dir.mkdir()
        now = datetime.now(UTC).isoformat(timespec="seconds")
        for number in (1, 2):
            event = {
                "id": f"{number}-1",
                "at": now,
                "type": "report",
                "item": "task:2026-10-20/1",
                "summary": "s",
            }
            with (queue_dir / events.QUEUE_FILE).open("a") as queue:
                queue.write(json.dumps(event) + "\n")
        delivered = {
            "delivered": {"1-1": f"send {now}"},
            "deliveries": [{"at": now, "via": "send", "events": ["1-1"]}],
        }
        (queue_dir / events.STATE_FILE).write_text(json.dumps(delivered))
        slack_dir = self.state / slack_watch.SUBDIR
        slack_dir.mkdir()
        (slack_dir / slack_watch.STATE_FILE).write_text(json.dumps({"failures": 1, "last_started": now}))
        probe = {"at": now, "outcome": "ok", "source": "structured", "code": ""}
        (self.state / login.STATE_FILE).write_text(json.dumps({"probe": probe, "gated": {}}))

    def test_one_collection(self) -> None:
        self.write_state()
        commands = good_commands()
        facts = Collector(self.settings, commands).collect()
        self.assertEqual(
            [argv[1:] for argv in commands.ran], [["sessions", "--json"], ["watchdog", "--facts"]]
        )
        self.assertEqual([w["name"] for w in facts.windows or []], ["orch", "exporter", "checkin"])
        self.assertEqual(facts.errors, {})
        self.assertEqual(facts.events_pending, 1)
        self.assertEqual(facts.events.deliveries_on(datetime.now().date()), 1)  # type: ignore[union-attr]
        self.assertEqual(facts.slack.failures, 1)  # type: ignore[union-attr]
        self.assertEqual(facts.login_state.last_probe.outcome, "ok")  # type: ignore[union-attr]
        self.assertIsNone(facts.morning)

    def test_the_watchdog_checks_run_read_only_without_claude(self) -> None:
        before = tree(self.state)
        facts = Collector(self.settings, good_commands()).collect()
        checks = {c["name"]: c for c in (facts.checks or {}).get("checks", [])}
        self.assertEqual(list(checks), [c.name for c in watchdog.CHECKS])
        self.assertEqual(checks[watchdog.LOGIN_CHECK]["note"], console_live.LOGIN_CHECK_NOTE)
        self.assertEqual(tree(self.state), before)  # no de-dup state, no lock file, nothing

    def test_a_failing_reader_leaves_its_part_empty(self) -> None:
        facts = Collector(self.settings, FakeCommands({})).collect()
        self.assertIsNone(facts.windows)
        self.assertIsNone(facts.checks)
        self.assertIn("sessions --json exited 1", facts.errors["sessions"])
        self.assertIn("watchdog --facts exited 1", facts.errors["watchdog"])

    def test_output_that_is_not_json(self) -> None:
        facts = Collector(self.settings, FakeCommands({"sessions --json": "oops"})).collect()
        self.assertIn("gave no JSON", facts.errors["sessions"])

    def test_the_watchdog_without_its_settings(self) -> None:
        settings = Settings.from_env({"BOSUN_STATE_DIR": str(self.state)})
        facts = Collector(settings, good_commands()).collect()
        self.assertIsNone(facts.checks)
        self.assertIn("BOSUN_LOG_DIR is not set", facts.errors["watchdog"])

    def test_unreadable_state_files(self) -> None:
        events.events_dir(self.state).mkdir()
        (events.events_dir(self.state) / events.STATE_FILE).write_text("{")
        facts = Collector(self.settings, good_commands()).collect()
        self.assertIsNone(facts.events)
        self.assertIn("unreadable", facts.errors["events"])

    def test_a_window_login_from_its_transcript(self) -> None:
        transcript = self.state / "orch.jsonl"
        transcript.write_text("")
        windows: list[dict[str, Any]] = [
            {**WINDOWS[0], "agent": {**WINDOWS[0]["agent"], "transcript": str(transcript)}}
        ]
        facts = Collector(self.settings, FakeCommands({"sessions --json": json.dumps(windows)})).collect()
        self.assertEqual(list(facts.window_logins), ["orch"])
        self.assertEqual(facts.window_logins["orch"].state, login.OK)


class RunCommandTestCase(unittest.TestCase):
    def test_output_and_failures(self) -> None:
        self.assertEqual(console_live.run_command(["/bin/echo", "hi"]), "hi\n")
        with self.assertRaisesRegex(LiveError, "exited 1"):
            console_live.run_command(["/bin/sh", "-c", "echo nope >&2; exit 1"])
        with self.assertRaises(LiveError):
            console_live.run_command(["/nonexistent/bosun", "sessions"])


class CacheTestCase(unittest.TestCase):
    def test_collects_again_only_once_stale(self) -> None:
        clock = [100.0]
        made: list[int] = []

        def collect() -> Any:
            made.append(len(made))
            return len(made)

        cache = LiveCache(collect, ttl_seconds=10, clock=lambda: clock[0])
        self.assertEqual(cache.get(), 1)
        clock[0] += 9
        self.assertEqual(cache.get(), 1)
        clock[0] += 1
        self.assertEqual(cache.get(), 2)
        self.assertEqual(len(made), 2)


if __name__ == "__main__":
    unittest.main()
