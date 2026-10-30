"""lib/codex_state.py and lib/agent_state.py, over session files in the shape real Codex 0.166.0 writes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_files
import agent_state
import claude_state
import codex_state


SESSION_ID = "01a18dc6-0f72-7510-976b-71a3b7ade182"
REVIEWER_ID = "01a18dca-a921-7832-a186-29add4831a6a"
STARTED = "2026-10-30T17:05:30.180Z"
ENDED = "2026-10-30T17:05:35.328Z"


def event(kind: str, at: str = ENDED, **fields: Any) -> dict[str, Any]:
    return {"timestamp": at, "type": "event_msg", "payload": {"type": kind, **fields}}


def meta(session_id: str = SESSION_ID, source: object = "cli", cwd: str = "/work/api") -> dict[str, Any]:
    payload = {
        "id": session_id,
        "cwd": cwd,
        "cli_version": "0.166.0",
        "originator": "codex-tui",
        "source": source,
    }
    return {"timestamp": STARTED, "type": "session_meta", "payload": payload}


class CodexTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state = self.root / "state"
        self.sessions = self.root / "sessions"
        self.config = self.root / "config.toml"
        for name, value in (("SESSIONS_DIR", self.sessions), ("CONFIG_FILE", self.config)):
            patch = mock.patch.object(codex_state, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        environment = mock.patch.dict(os.environ, {"BOSUN_STATE_DIR": str(self.state)})
        environment.start()
        self.addCleanup(environment.stop)

    def rollout(self, *entries: dict[str, Any], session_id: str = SESSION_ID) -> Path:
        folder = self.sessions / "2026" / "10" / "30"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"rollout-2026-10-30T17-05-30-{session_id}.jsonl"
        path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
        return path

    def lsof_says(self, *lines: str) -> Any:
        result = subprocess.CompletedProcess([], 0, "\n".join(lines) + "\n", "")
        return mock.patch("subprocess.run", return_value=result)

    def test_a_turn_in_progress_is_busy(self) -> None:
        path = self.rollout(meta(), event("task_started", STARTED), {"type": "response_item", "payload": {}})
        status, since = codex_state.turn_state(path)
        self.assertEqual(status, "busy")
        self.assertEqual(since, codex_state.epoch_ms(STARTED))

    def test_a_finished_or_interrupted_turn_is_idle(self) -> None:
        for ending in ("task_complete", "turn_aborted"):
            with self.subTest(ending=ending):
                path = self.rollout(meta(), event("task_started", STARTED), event(ending))
                self.assertEqual(codex_state.turn_state(path), ("idle", codex_state.epoch_ms(ENDED)))

    def test_a_prompt_quoting_a_turn_event_does_not_count(self) -> None:
        quoted = {
            "timestamp": ENDED,
            "type": "response_item",
            "payload": {"type": "message", "text": '"task_started"'},
        }
        path = self.rollout(meta(), event("task_started", STARTED), event("task_complete"), quoted)
        self.assertEqual(codex_state.turn_state(path)[0], "idle")

    def test_the_reviewer_s_session_is_not_the_window_s(self) -> None:
        guardian = {"subagent": {"other": "guardian"}}
        reviewer = self.rollout(meta(REVIEWER_ID, guardian), session_id=REVIEWER_ID)
        window = self.rollout(meta())
        self.assertEqual(codex_state.main_rollout([reviewer, window]), window)
        self.assertIsNone(codex_state.main_rollout([reviewer]))

    def test_only_a_session_in_the_terminal_counts(self) -> None:
        self.rollout(meta())
        self.rollout(meta(REVIEWER_ID, {"subagent": {"other": "guardian"}}), session_id=REVIEWER_ID)
        self.assertTrue(codex_state.is_terminal_session(SESSION_ID))
        self.assertFalse(codex_state.is_terminal_session(REVIEWER_ID))
        self.assertFalse(codex_state.is_terminal_session("a-helper-with-no-file"))

    def test_it_finds_what_a_process_holds_from_lsof(self) -> None:
        path = self.rollout(meta())
        with self.lsof_says("p41", "fcwd", "n/work/api", "f12", f"n{path}", "p42", "n/tmp/other.jsonl"):
            self.assertEqual(codex_state.held_rollouts([41, 42]), {41: [path]})

    def test_a_record_has_the_fields_a_claude_s_has(self) -> None:
        path = self.rollout(meta(), event("task_started", STARTED), event("task_complete"))
        notify = '-c notify=["/bin/bosun","hook","codex-turn-end","--window","api","--conversation","api-2"]'
        with (
            mock.patch.object(codex_state, "held_rollouts", return_value={41: [path]}),
            mock.patch.object(codex_state, "command_line", return_value=f"codex {notify}"),
        ):
            record = codex_state.record(41)
        assert record is not None
        self.assertEqual(record["sessionId"], SESSION_ID)
        self.assertEqual(record["name"], "api-2")
        self.assertEqual(record["status"], "idle")
        self.assertEqual(record["statusUpdatedAt"], codex_state.epoch_ms(ENDED))
        self.assertEqual(record["version"], "0.166.0")
        self.assertEqual(record["cwd"], "/work/api")

    def test_no_session_file_means_no_record(self) -> None:
        with mock.patch.object(codex_state, "held_rollouts", return_value={}):
            self.assertIsNone(codex_state.record(41))
        self.assertIsNone(codex_state.record("not-a-pid"))

    def test_registered_is_the_first_process_with_a_session(self) -> None:
        path = self.rollout(meta())
        with mock.patch.object(codex_state, "held_rollouts", return_value={42: [path]}):
            self.assertEqual(codex_state.registered(["41", "42", "43"]), "42")
        with mock.patch.object(codex_state, "held_rollouts", return_value={}):
            self.assertIsNone(codex_state.registered(["41"]))

    def test_how_it_was_launched(self) -> None:
        notify = 'notify=["/bin/bosun","hook","codex-turn-end","--window","api"]'
        line = f"""codex --no-daemon -c {notify} --model gpt-test -c model_reasoning_effort="high" resume x"""
        with mock.patch.object(codex_state, "command_line", return_value=line):
            self.assertEqual(
                codex_state.launch_settings("1"), agent_files.LaunchSettings("gpt-test", "high", True)
            )
        with mock.patch.object(codex_state, "command_line", return_value="codex"):
            self.assertEqual(codex_state.launch_settings("1"), agent_files.LaunchSettings(None, None, False))

    def test_how_large_the_conversation_is(self) -> None:
        path = self.rollout(meta(), event("token_count", info={"last_token_usage": {"total_tokens": 12651}}))
        with mock.patch.object(codex_state, "record", return_value={"transcript": str(path)}):
            self.assertEqual(codex_state.conversation_size("41"), 12651)

    def test_trust_round_trip_leaves_the_rest_of_the_file_alone(self) -> None:
        original = (
            '[tui]\nscreen_reader_detection_done = true\n\n[projects."/other"]\ntrust_level = "trusted"\n'
        )
        self.config.write_text(original)
        os.chmod(self.config, 0o640)
        folder = self.root / "repo"
        folder.mkdir()
        self.assertFalse(codex_state.trusted(str(folder)))
        codex_state.set_trust(str(folder), True)
        self.assertTrue(codex_state.trusted(str(folder)))
        self.assertTrue(codex_state.trusted("/other"))
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        codex_state.set_trust(str(folder), True)
        self.assertEqual(self.config.read_text().count(str(folder)), 1)
        codex_state.set_trust(str(folder), False)
        self.assertFalse(codex_state.trusted(str(folder)))
        self.assertEqual(self.config.read_text().strip(), original.strip())

    def test_trust_starts_a_config_that_does_not_exist(self) -> None:
        folder = self.root / "repo"
        folder.mkdir()
        codex_state.set_trust(str(folder), True)
        self.assertTrue(codex_state.trusted(str(folder)))

    def test_a_config_it_cannot_read_trusts_nothing(self) -> None:
        self.config.write_text("this is = not [toml")
        self.assertFalse(codex_state.trusted("/other"))

    def test_a_name_resolves_to_its_session_in_that_folder_only(self) -> None:
        self.rollout(meta())
        folder = self.root / "repo"
        folder.mkdir()
        codex_state.remember("api", SESSION_ID, str(folder), 1)
        self.assertEqual(codex_state.resolve(str(folder), "api"), SESSION_ID)
        self.assertIsNone(codex_state.resolve(str(self.root), "api"))
        self.assertIsNone(codex_state.resolve(str(folder), "other"))

    def test_each_report_lists_the_whole_session_so_only_the_rest_is_new(self) -> None:
        folder = str(self.root)
        new = codex_state.new_prompts
        self.assertEqual(new("api", "t", ["one"]), ["one"])
        codex_state.remember("api", "t", folder, 1)
        self.assertEqual(new("api", "t", ["one", "two"]), ["two"])
        self.assertEqual(new("api", "t", ["one", "two", "three"]), ["two", "three"])

    def test_the_same_words_twice_are_two_prompts(self) -> None:
        codex_state.remember("api", "t", str(self.root), 1)
        self.assertEqual(codex_state.new_prompts("api", "t", ["yes", "yes"]), ["yes"])

    def test_without_a_count_only_the_last_prompt_can_be_new(self) -> None:
        prompts = ["one", "two", "three"]
        self.assertEqual(codex_state.new_prompts("api", "t", prompts), ["three"])
        codex_state.remember("api", "another-session", str(self.root), 2)
        self.assertEqual(codex_state.new_prompts("api", "t", prompts), ["three"])
        self.assertEqual(codex_state.new_prompts("api", "t", []), [])

    def test_a_name_whose_session_file_has_gone_is_not_resolved(self) -> None:
        folder = self.root / "repo"
        folder.mkdir()
        codex_state.remember("api", SESSION_ID, str(folder), 1)
        self.assertIsNone(codex_state.resolve(str(folder), "api"))

    def test_history_counts_terminal_turns_and_never_what_was_said(self) -> None:
        since = datetime(2026, 10, 1, tzinfo=UTC)
        self.rollout(
            meta(), event("task_started", STARTED), event("task_complete"), event("task_started", STARTED)
        )
        guardian = {"subagent": {"other": "guardian"}}
        self.rollout(meta(REVIEWER_ID, guardian), event("task_started", STARTED), session_id=REVIEWER_ID)
        uses = list(codex_state.prompt_history(since))
        self.assertEqual([cwd for cwd, _ in uses], ["/work/api", "/work/api"])

    def test_history_leaves_out_what_came_before(self) -> None:
        self.rollout(meta(), event("task_started", "2026-01-01T00:00:00.000Z"))
        self.assertEqual(list(codex_state.prompt_history(datetime(2026, 10, 1, tzinfo=UTC))), [])

    def test_login_is_what_codex_says(self) -> None:
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertTrue(codex_state.logged_in())
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertFalse(codex_state.logged_in())
        with mock.patch.dict(os.environ, {"BOSUN_CODEX_BIN": str(self.root / "no-such-codex")}):
            self.assertFalse(codex_state.logged_in())
            self.assertFalse(codex_state.ready())

    def test_the_command_line(self) -> None:
        self.assertEqual(codex_state.main(["no-such-command"]), agent_files.USAGE)
        self.assertEqual(codex_state.main(["trusted", str(self.root)]), agent_files.NOT_FOUND)
        self.assertEqual(codex_state.main(["set-trust", str(self.root), "true"]), 0)
        self.assertEqual(codex_state.main(["trusted", str(self.root)]), 0)


class AgentStateTest(unittest.TestCase):
    def test_a_record_says_which_agent_it_is(self) -> None:
        with (
            mock.patch.object(claude_state, "record", return_value={"status": "idle"}),
            mock.patch.object(codex_state, "record") as codex,
        ):
            self.assertEqual(agent_state.record(1), {"status": "idle", "harness": "claude"})
        codex.assert_not_called()
        with (
            mock.patch.object(claude_state, "record", return_value=None),
            mock.patch.object(codex_state, "record", return_value={"status": "busy"}),
        ):
            self.assertEqual(agent_state.record(1), {"status": "busy", "harness": "codex"})

    def test_no_agent_means_no_record(self) -> None:
        with (
            mock.patch.object(claude_state, "record", return_value=None),
            mock.patch.object(codex_state, "record", return_value=None),
        ):
            self.assertIsNone(agent_state.record(1))
            self.assertIsNone(agent_state.conversation_size("1"))

    def test_field_and_describe_go_by_the_process(self) -> None:
        codex = {"status": "busy"}
        with (
            mock.patch.object(claude_state, "record", return_value=None),
            mock.patch.object(claude_state, "describe", return_value=None),
            mock.patch.object(codex_state, "record", return_value=codex),
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(agent_state.main(["field", "1", "status"]), 0)
            printed.assert_called_with("busy")
            self.assertEqual(agent_state.main(["field", "1", "harness"]), 0)
            printed.assert_called_with("codex")
            self.assertEqual(agent_state.main(["describe", "1"]), 0)
            printed.assert_called_with(json.dumps({"status": "busy", "harness": "codex"}))

    def test_the_size_of_a_conversation_is_asked_of_its_own_agent(self) -> None:
        with (
            mock.patch.object(claude_state, "record", return_value=None),
            mock.patch.object(codex_state, "record", return_value={"status": "idle"}),
            mock.patch.object(codex_state, "conversation_size", return_value=7),
        ):
            self.assertEqual(agent_state.conversation_size("1"), 7)

    def test_how_a_process_was_launched_is_asked_of_its_own_agent(self) -> None:
        settings = agent_files.LaunchSettings("gpt-test", None, True)
        with (
            mock.patch.object(claude_state, "record", return_value=None),
            mock.patch.object(codex_state, "record", return_value={"status": "idle"}),
            mock.patch.object(codex_state, "launch_settings", return_value=settings),
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(agent_state.main(["launch", "1"]), 0)
            printed.assert_called_with("gpt-test\t-\tyes")
        with (
            mock.patch.object(claude_state, "record", return_value=None),
            mock.patch.object(codex_state, "record", return_value=None),
        ):
            self.assertEqual(agent_state.main(["launch", "1"]), agent_files.NOT_FOUND)

    def test_claude_code_s_launch_is_read_from_its_own_flags(self) -> None:
        settings_json = '{"effortLevel":"high"}'
        line = f"claude --permission-mode auto --model opus --settings {settings_json} --remote-control api"
        with mock.patch.object(claude_state, "command_line", return_value=line):
            self.assertEqual(
                claude_state.launch_settings("1"), agent_files.LaunchSettings("opus", "high", True)
            )
        with mock.patch.object(claude_state, "command_line", return_value="claude"):
            self.assertEqual(claude_state.launch_settings("1"), agent_files.LaunchSettings(None, None, False))

    def test_registered_asks_each_agent(self) -> None:
        with (
            mock.patch.object(claude_state, "registered", return_value=None),
            mock.patch.object(codex_state, "registered", return_value="42"),
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(agent_state.main(["registered", "41", "42"]), 0)
            printed.assert_called_with("42")

    def test_what_is_per_agent_goes_to_the_agent_named(self) -> None:
        for name, module in (("claude", claude_state), ("codex", codex_state)):
            with self.subTest(agent=name), mock.patch.object(module, "trusted", return_value=True) as trusted:
                self.assertEqual(agent_state.main(["trusted", name, "/repo"]), 0)
                trusted.assert_called_with("/repo")

    def test_an_unknown_agent_is_a_usage_error(self) -> None:
        self.assertEqual(agent_state.main(["trusted", "nosuch", "/repo"]), agent_files.USAGE)

    def test_set_trust_only_takes_true_or_false(self) -> None:
        self.assertEqual(agent_state.main(["set-trust", "codex", "/repo", "maybe"]), agent_files.USAGE)

    def test_every_agent_in_the_table_is_named_for_its_key(self) -> None:
        for name, agent in agent_state.AGENTS.items():
            self.assertEqual(agent.HARNESS, name)


if __name__ == "__main__":
    unittest.main()
