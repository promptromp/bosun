"""Unit tests for the repo sessions' UserPromptSubmit hook (lib/hook.py) and the watchdog's
`events` check (lib/watchdog.py). Fictional data: acme's invoice exporter."""

from __future__ import annotations

import dataclasses
import io
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_state
import claude_state
import codex_state
import events
import hook
import ledger
import update_gate
import watchdog
from agent_files import AgentHooks
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
        self,
        prompts: str | list[str],
        env: dict[str, str] | None = None,
        thread: str = "t-1",
        head: tuple[str, ...] = ("codex-turn-end",),
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
        argv = [*head, "--window", WINDOW, "--conversation", f"{WINDOW}-2", json.dumps(payload)]
        return hook.main(argv, env or self.env)

    def after_codex_turn(self, *head: str) -> tuple[dict[str, Any], list[tuple[Any, ...]]]:
        """What a Codex turn run through <head> leaves in this test's state: the names, and what was said."""
        self.codex_turn("go with option b", head=head)
        names = json.loads((self.state_dir / "codex-sessions.json").read_text())
        return names, [(e["items"], e["window"], e["session"], e["text"]) for e in self.said()]

    def test_older_event_names_come_from_the_profiles(self) -> None:
        self.assertEqual({"codex-turn-end": "codex"}, hook.legacy_events())

    def test_turn_end_with_harness_codex_does_what_codex_turn_end_did(self) -> None:
        old = self.after_codex_turn("codex-turn-end")
        other = type(self)(self._testMethodName)  # a state folder of its own, cleaned up with this test's
        other.setUp()
        self.addCleanup(other.doCleanups)
        new = other.after_codex_turn("turn-end", "--harness", "codex")
        self.assertEqual(old, new)
        self.assertEqual(new[0][f"{WINDOW}-2"]["id"], "t-1")
        self.assertEqual(len(new[1]), 1)

    def test_an_agent_that_reports_no_turns_is_refused(self) -> None:
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as raised, mock.patch.object(sys, "stderr", stderr):
            hook.main(["turn-end", "--harness", "claude", "--window", WINDOW, "{}"], self.env)
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("claude reports no turns", stderr.getvalue())

    def test_an_agent_bosun_does_not_know_is_refused_with_the_ones_it_does(self) -> None:
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as raised, mock.patch.object(sys, "stderr", stderr):
            hook.main(["turn-end", "--harness", "nope", "--window", WINDOW, "{}"], self.env)
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("no agent 'nope': bosun knows claude, codex", stderr.getvalue())

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

    def newer_ledger(self) -> None:
        data = json.loads(ledger.STORE.ledger_file.read_text())
        data["version"] = ledger.LEDGER_FORMAT + 1
        ledger.STORE.ledger_file.write_text(json.dumps(data))

    def test_a_write_the_gate_refuses_goes_to_the_rerun(self) -> None:
        """Not swallowed as an error: update_gate.guarded runs it again on the installed version."""
        self.newer_ledger()
        with self.assertRaises(update_gate.StoreNewer):
            self.run_hook(self.payload("go with option b"))
        self.assertIn("newer than this bosun", self.log())
        self.assertNotIn("ERROR", self.log())

    def test_an_older_ledger_is_an_error_in_the_log(self) -> None:
        with mock.patch.object(ledger, "LEDGER_FORMAT", ledger.LEDGER_FORMAT + 1):
            self.assertEqual(self.run_hook(self.payload("go with option b")), 0)
        self.assertIn("ERROR: could not record a prompt typed in exporter", self.log())
        self.assertIn("run bosun update to migrate it", self.log())

    def test_an_update_record_that_cannot_be_read_is_an_error_in_the_log(self) -> None:
        """Refused for good (never run again), so it is said as an error, not as running it again."""
        (self.state_dir / "update.json").write_text("{nope")
        self.assertEqual(self.run_hook(self.payload("go with option b")), 0)
        self.assertIn("ERROR: could not record a prompt typed in exporter", self.log())
        self.assertIn("update.json is not JSON", self.log())
        self.assertNotIn("running it again", self.log())

    def test_a_codex_turn_the_gate_refuses_leaves_its_prompts_new(self) -> None:
        self.newer_ledger()
        with self.assertRaises(update_gate.StoreNewer):
            self.codex_turn("go with option b")
        data = json.loads(ledger.STORE.ledger_file.read_text())
        data["version"] = ledger.LEDGER_FORMAT
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        self.codex_turn("go with option b")  # the re-run, on a version that can write
        self.assertEqual([e["text"] for e in self.said()], ["go with option b"])

    def test_a_detached_child_reruns_in_the_foreground_and_says_why_in_the_log(self) -> None:
        env = {**self.env, "BOSUN_HOOK_SYNC": "0"}
        opened = {os.devnull: 7, str(hook.log_file(env)): 8}
        with (
            mock.patch.dict("os.environ", {"BOSUN_HOOK_SYNC": "0"}),
            mock.patch("os.fork", return_value=0),
            mock.patch("os.setsid"),
            mock.patch("os.open", side_effect=lambda path, *_: opened[str(path)]),
            mock.patch("os.dup2") as dup2,
        ):
            self.assertFalse(hook.detach(env))
            self.assertEqual(os.environ["BOSUN_HOOK_SYNC"], "1")
        self.assertEqual([c.args for c in dup2.call_args_list], [(7, 0), (7, 1), (8, 2)])

    def test_a_write_the_gate_still_refuses_is_an_error_in_the_log(self) -> None:
        argv = ["user-prompt", "--window", WINDOW]
        with mock.patch.object(update_gate, "guarded", return_value=1):
            self.assertEqual(hook.run(argv, self.env), 1)
        self.assertIn(f"ERROR: bosun hook user-prompt --window {WINDOW} recorded nothing", self.log())
        with mock.patch.object(update_gate, "guarded", return_value=0):
            self.assertEqual(hook.run(argv, self.env), 0)
        self.assertEqual(self.log().count("ERROR"), 1)

    def test_the_error_line_never_carries_the_payload_whatever_the_arguments(self) -> None:
        said = "go with option c"
        payload = json.dumps({"thread-id": "t-1", "cwd": "/repo", "input-messages": [said]})
        forms = (
            (["codex-turn-end", "--window", WINDOW, payload], f"codex-turn-end --window {WINDOW} recorded"),
            (
                ["turn-end", "--harness", "codex", "--window", WINDOW, "--conversation", "n", payload],
                f"turn-end --harness codex --window {WINDOW} --conversation n recorded",
            ),
        )
        for argv, line in forms:
            with self.subTest(argv[0]), mock.patch.object(update_gate, "guarded", return_value=1):
                self.assertEqual(hook.run(argv, self.env), 1)
                self.assertIn(f"ERROR: bosun hook {line} nothing", self.log())
        self.assertNotIn(said, self.log())
        self.assertNotIn("t-1", self.log())

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


def ping(event: str, payload: Any, env: Any) -> str:
    return json.dumps({"seen": payload["n"]})


def broken(event: str, payload: Any, env: Any) -> str:
    raise RuntimeError("the answer broke")


class AgentHookTest(unittest.TestCase):
    """`bosun hook agent --harness <agent> --event <event>`: an agent's own hooks, answered on stdout."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.logs = Path(tmp.name) / "logs"
        self.env = {"BOSUN_LOG_DIR": str(self.logs)}
        self.install(ping)

    def install(self, answer: Any) -> None:
        profile = dataclasses.replace(claude_state.PROFILE, hooks=AgentHooks(("Ping",), answer))
        stub = types.SimpleNamespace(PROFILE=profile)
        patcher = mock.patch.dict(agent_state.AGENTS, {"stub": stub})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_hook(self, stdin: str, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), mock.patch.object(sys, "stdout", out):
            return hook.agent_hook(list(argv), self.env), out.getvalue()

    def log(self) -> str:
        return "".join(path.read_text() for path in self.logs.glob("hook-*.log"))

    def refused(self, *argv: str) -> str:
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as raised, mock.patch.object(sys, "stderr", stderr):
            self.run_hook("{}", *argv)
        self.assertEqual(raised.exception.code, 2)
        return stderr.getvalue()

    def test_an_agent_hook_prints_its_answer(self) -> None:
        self.assertEqual(
            (0, '{"seen": 1}'), self.run_hook('{"n": 1}', "--harness", "stub", "--event", "Ping")
        )

    def test_an_empty_answer_prints_nothing_at_all(self) -> None:
        self.install(lambda event, payload, env: "")
        self.assertEqual((0, ""), self.run_hook("{}", "--harness", "stub", "--event", "Ping"))

    def test_a_failing_agent_hook_prints_nothing(self) -> None:
        self.install(broken)
        self.assertEqual((0, ""), self.run_hook('{"n": 1}', "--harness", "stub", "--event", "Ping"))
        for part in ("ERROR", "stub", "Ping", "RuntimeError", "the answer broke"):
            self.assertIn(part, self.log())

    def test_bad_json_prints_nothing(self) -> None:
        self.assertEqual((0, ""), self.run_hook("{not json", "--harness", "stub", "--event", "Ping"))
        self.assertIn("ERROR", self.log())
        self.assertNotIn("not json", self.log())

    def test_the_log_never_carries_the_payload(self) -> None:
        self.install(broken)
        self.run_hook('{"n": 1, "prompt": "my secret prompt"}', "--harness", "stub", "--event", "Ping")
        self.assertNotIn("secret", self.log())

    def test_an_agent_with_no_hooks_is_refused(self) -> None:
        self.assertIn(
            "claude has no hooks of its own", self.refused("--harness", "claude", "--event", "Ping")
        )

    def test_an_unknown_event_is_refused(self) -> None:
        self.assertIn("stub has no hook Pong", self.refused("--harness", "stub", "--event", "Pong"))

    def test_an_agent_bosun_does_not_know_is_refused_with_the_ones_it_does(self) -> None:
        self.assertIn(
            "no agent 'nope': bosun knows claude, codex, stub",
            self.refused("--harness", "nope", "--event", "Ping"),
        )


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
