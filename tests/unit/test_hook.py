"""Unit tests for the repo sessions' UserPromptSubmit hook (lib/hook.py) and the watchdog's
`events` check (lib/watchdog.py). Fictional data: acme's invoice exporter."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import codex_state
import events
import hook
import ledger
import watchdog
from watchdog import Config, Facts, Inputs, State


PR = "mypr:acme/exporter#102"
WINDOW = "exporter"


class HookTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_dir = Path(tmp.name)
        self.env = {
            "BOSUN_STATE_DIR": tmp.name,
            "BOSUN_LOG_DIR": str(self.state_dir / "logs"),
            "BOSUN_EVENTS_ENABLED": "1",
            "BOSUN_HOOK_SYNC": "1",
            "BOSUN_DRAIN_PROMPT": "Bosun resumed this session.",
        }
        patcher = mock.patch.dict("os.environ", self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        ledger.configure(tmp.name)
        ledger.cmd_upsert(ledger.build_parser().parse_args(["upsert", PR, "--kind", "my-pr", "--title", "T"]))
        args = ["record-delegation", PR, "--window", WINDOW, "--task", "fix-my-pr"]
        ledger.cmd_record_delegation(ledger.build_parser().parse_args(args))

    def run_hook(self, stdin: str, *argv: str) -> int:
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            return hook.main(list(argv or ("user-prompt", "--window", WINDOW)), self.env)

    def log(self) -> str:
        return "".join(path.read_text() for path in (self.state_dir / "logs").glob("hook-*.log"))

    def said(self) -> list[dict[str, Any]]:
        value: list[dict[str, Any]] = json.loads(ledger.STORE.ledger_file.read_text()).get("said", [])
        return value

    def payload(self, prompt: str) -> str:
        return json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s-1", "prompt": prompt})

    def codex_turn(
        self, prompts: str | list[str], env: dict[str, str] | None = None, thread: str = "t-1"
    ) -> int:
        session_meta = {"type": "session_meta", "payload": {"id": "t-1", "source": "cli"}}
        rollouts = self.state_dir / "codex" / "2026" / "10" / "30"
        rollouts.mkdir(parents=True, exist_ok=True)
        (rollouts / "rollout-2026-10-30T00-00-00-t-1.jsonl").write_text(json.dumps(session_meta) + "\n")
        patcher = mock.patch.object(codex_state, "SESSIONS_DIR", self.state_dir / "codex")
        patcher.start()
        self.addCleanup(patcher.stop)
        inputs = [prompts] if isinstance(prompts, str) else prompts
        payload = {"thread-id": thread, "cwd": "/repo", "input-messages": inputs}
        argv = ["codex-turn-end", "--window", WINDOW, "--conversation", f"{WINDOW}-2", json.dumps(payload)]
        return hook.main(argv, env or self.env)

    def test_a_codex_turn_says_which_session_a_window_s_name_stands_for(self) -> None:
        self.assertEqual(self.codex_turn("go with option b"), 0)
        names = json.loads((self.state_dir / "codex-sessions.json").read_text())
        self.assertEqual(names[f"{WINDOW}-2"]["id"], "t-1")

    def test_what_i_said_in_a_codex_window_is_recorded_like_in_any_other(self) -> None:
        self.codex_turn("go with option b")
        [entry] = self.said()
        self.assertEqual((entry["items"], entry["window"], entry["session"]), ([PR], WINDOW, "t-1"))

    def test_codex_s_hidden_helpers_are_not_the_window_s_session_or_what_i_said(self) -> None:
        self.codex_turn("go with option b")
        self.codex_turn("Write a short title for this conversation", thread="a-helper")
        names = json.loads((self.state_dir / "codex-sessions.json").read_text())
        self.assertEqual(names[f"{WINDOW}-2"]["id"], "t-1")
        self.assertEqual(len(self.said()), 1)
        self.assertIn("one of Codex's helpers", self.log())

    def test_a_prompt_codex_lists_again_is_not_recorded_again(self) -> None:
        self.codex_turn("go with option b")
        self.codex_turn(["go with option b", "and keep the tests"])
        self.assertEqual([e["text"] for e in self.said()], ["go with option b", "and keep the tests"])

    def test_a_codex_turn_without_events_only_remembers_the_name(self) -> None:
        self.codex_turn("go with option b", {**self.env, "BOSUN_EVENTS_ENABLED": "0"})
        self.assertEqual(self.said(), [])
        self.assertTrue((self.state_dir / "codex-sessions.json").exists())

    def test_a_prompt_i_type_is_recorded_and_queued(self) -> None:
        self.assertEqual(self.run_hook(self.payload("go with option b")), 0)
        [entry] = self.said()
        self.assertEqual((entry["items"], entry["window"], entry["session"]), ([PR], WINDOW, "s-1"))
        self.assertIn(f"user-said in {WINDOW} recorded on {PR}", self.log())
        [event] = events.read_queue(events.events_dir(self.state_dir))
        self.assertEqual((event.type, event.origin, event.item), ("user-said", WINDOW, PR))

    def test_bosuns_own_prompts_are_skipped(self) -> None:
        events.note_pasted(self.state_dir, WINDOW, "Please look at the footer")
        for prompt in (
            "Please look at the footer",
            "<pasted_content id='1'>Please look at the footer</pasted_content id='1'>",
            "Bosun resumed this session.",
            "[Cross-session delivery notice] Your message was held",
        ):
            self.run_hook(self.payload(prompt))
        self.assertEqual(self.said(), [])
        self.assertEqual(self.log().count("skipped in exporter: not typed by me"), 4)

    def test_it_never_fails_the_prompt(self) -> None:
        self.assertEqual(self.run_hook("{not json"), 0)
        self.assertIn("ERROR: unreadable hook input", self.log())
        with mock.patch.object(ledger, "add_user_said", side_effect=RuntimeError("locked out")):
            self.assertEqual(self.run_hook(self.payload("go with b")), 0)
        self.assertIn(
            "ERROR: could not record a prompt typed in exporter: RuntimeError('locked out')", self.log()
        )
        self.assertEqual(self.run_hook(json.dumps({"hook_event_name": "Stop"})), 0)
        self.assertIn("ignored: a Stop hook", self.log())


NOON = datetime(2026, 1, 5, 12, 0)  # a Monday


class WatchdogEventsCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_dir = Path(tmp.name)
        self.config = Config(
            state_dir=self.state_dir,
            log_dir=self.state_dir / "logs",
            session="main",
            orchestrator="orch",
            days="MTWRF",
            start=time(8, 0),
            wake_delay_minutes=1,
            awake_until=time(20, 0),
            grace_minutes=15,
            waiting_minutes=30,
            checkin_minutes=60,
            delegated_hours=8,
            events_enabled=True,
            events_interval_seconds=30,
        )
        self.facts = Facts(session_exists=True, checkin_loop=True)

    def check(self) -> watchdog.Result:
        inputs = Inputs(self.config, self.facts, NOON, lambda _pid: None, lambda _pid: True)
        return watchdog.check_events(inputs.context(State()))

    def save(self, **state: Any) -> None:
        directory = events.events_dir(self.state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        events.State(**state).save(directory)

    def utc(self, moment: datetime) -> str:
        return moment.astimezone().astimezone(UTC).isoformat(timespec="seconds")

    def test_off_unless_enabled(self) -> None:
        self.config = Config(**{**self.config.__dict__, "events_enabled": False})
        self.assertEqual(self.check().note, "off (BOSUN_EVENTS_ENABLED=0)")

    def test_a_dispatcher_that_stopped_is_stalled(self) -> None:
        self.save(last_tick=self.utc(NOON - timedelta(minutes=2)))
        self.assertEqual(self.check().findings, [])
        self.save(last_tick=self.utc(NOON - timedelta(minutes=12)))
        [finding] = self.check().findings
        self.assertTrue(finding.key.startswith("events:stalled:") and finding.confirm)
        self.assertIn("bosun refresh checkin", finding.message)
        self.facts = Facts(session_exists=True, checkin_loop=False)
        self.assertEqual(self.check().findings, [])  # checkin-missing covers that

    def test_a_failed_delivery_alerts_once_a_day(self) -> None:
        self.save(last_tick=self.utc(NOON), last_error={"at": self.utc(NOON), "error": "window is waiting"})
        [finding] = self.check().findings
        self.assertEqual(finding.key, "events:failed:2026-01-05")
        self.assertIn("window is waiting", finding.message)


if __name__ == "__main__":
    unittest.main()
