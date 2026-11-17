"""Each agent module declares what it is and can do, in a Profile."""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_changes
import agent_state
import agy_state
import claude_state
import codex_state
import uninstall_plan
import update_backups
from agent_files import USAGE, AgentSetup, Profile, SetupError
from agent_state import AGENTS


class AgentProfileTest(unittest.TestCase):
    def test_each_agent_has_a_profile(self) -> None:
        for agent in AGENTS.values():
            self.assertIsInstance(agent.PROFILE, Profile)
            self.assertTrue(
                {"idle", "busy"} <= agent.PROFILE.statuses <= {"idle", "busy", "shell", "waiting"}
            )

    def test_the_profiles_say_what_each_agent_does(self) -> None:
        claude = claude_state.PROFILE
        assert (claude.bin_key, claude.flags_key, claude.model_key) == (
            "BOSUN_CLAUDE_BIN",
            "BOSUN_CLAUDE_FLAGS",
            "BOSUN_MODEL",
        )
        assert claude.names_sessions
        assert claude.statuses == {"idle", "busy", "shell", "waiting"}
        assert claude.remote_control and claude.autocompact and claude.subagents
        assert claude.state_files == ()
        codex = codex_state.PROFILE
        assert (codex.bin_key, codex.flags_key, codex.model_key) == (
            "BOSUN_CODEX_BIN",
            "BOSUN_CODEX_FLAGS",
            "BOSUN_CODEX_MODEL",
        )
        assert not codex.names_sessions
        assert codex.statuses == {"idle", "busy"}
        assert not (codex.remote_control or codex.autocompact or codex.subagents)
        assert codex.state_files == ("codex-sessions.json",)
        assert codex.setup_sessions == "Codex, with its automatic reviewer"
        assert codex.setup_note.startswith("Repo sessions run on Codex")

    def test_each_agent_says_how_to_show_it(self) -> None:
        self.assertEqual(claude_state.PROFILE.display_name, "Claude Code")
        self.assertEqual(codex_state.PROFILE.display_name, "Codex")
        self.assertEqual(agy_state.PROFILE.display_name, "Antigravity")
        for agent in AGENTS.values():
            self.assertTrue(agent.PROFILE.setup_preview)

    def test_every_state_file_is_in_the_tables(self) -> None:
        patterns = {pattern for pattern, _kind in uninstall_plan.STATE_FILES}
        for agent in AGENTS.values():
            for name in agent.PROFILE.state_files:
                assert name in patterns
                assert name in update_backups.IN_FLIGHT

    def test_each_agent_names_the_module_that_holds_its_windows_to_their_connectors(self) -> None:
        self.assertEqual(claude_state.PROFILE.connectors, "connectors_claude")
        self.assertEqual(codex_state.PROFILE.connectors, "connectors_codex")
        self.assertEqual(agy_state.PROFILE.connectors, "connectors_agy")
        for agent in AGENTS.values():
            module = importlib.import_module(agent.PROFILE.connectors)
            assert callable(module.main) and callable(module.doctor_line)

    def test_each_agent_names_the_module_that_says_what_plugins_its_windows_load(self) -> None:
        self.assertEqual(claude_state.PROFILE.plugins, "plugins_claude")
        self.assertEqual(codex_state.PROFILE.plugins, "connectors_codex")
        self.assertEqual(agy_state.PROFILE.plugins, "connectors_agy")
        for agent in AGENTS.values():
            assert callable(importlib.import_module(agent.PROFILE.plugins).plugins_doctor_line)

    def test_the_profiles_command(self) -> None:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            assert agent_state.main(["profiles"]) == 0
        assert captured.getvalue().splitlines() == [
            "claude\tBOSUN_CLAUDE_BIN\tBOSUN_CLAUDE_FLAGS\tBOSUN_MODEL\tyes\tyes\tyes\tyes\tidle,busy,shell,waiting\t-\tyes",
            "codex\tBOSUN_CODEX_BIN\tBOSUN_CODEX_FLAGS\tBOSUN_CODEX_MODEL\tno\tno\tno\tno\tidle,busy\t-\tno",
            "agy\tBOSUN_AGY_BIN\tBOSUN_AGY_FLAGS\tBOSUN_AGY_MODEL\tno\tyes\tno\tno\tidle,busy,shell,waiting\tupdate\tno",
        ]

    def test_profiles_print_update_args(self) -> None:
        verifies = {"claude": "yes", "codex": "no"}
        for harness in ("claude", "codex"):
            assert AGENTS[harness].PROFILE.update_args == ()
            line = agent_state.profile_line(harness, AGENTS[harness].PROFILE)
            assert line.endswith("\t-\t" + verifies[harness])
        assert AGENTS["agy"].PROFILE.update_args == ("update",)
        assert agent_state.profile_line("agy", AGENTS["agy"].PROFILE).endswith(
            "\tidle,busy,shell,waiting\tupdate\tno"
        )
        updating = dataclasses.replace(claude_state.PROFILE, update_args=("update", "--now"))
        assert agent_state.profile_line("stub", updating).endswith(
            "\tidle,busy,shell,waiting\tupdate --now\tyes"
        )

    def test_every_agent_but_the_orchestrators_has_its_setup_texts(self) -> None:
        for name, agent in AGENTS.items():
            if name != agent_state.ORCHESTRATOR_HARNESS:
                assert agent.PROFILE.setup_sessions.strip(), name
                assert agent.PROFILE.setup_note.strip(), name

    def test_the_display_names_agree_with_the_shell_profiles(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as home:
            for name, agent in AGENTS.items():
                out = subprocess.run(
                    ["sh", "-c", '. "$1"; harness_name', "_", str(root / "lib" / "harnesses" / f"{name}.sh")],
                    capture_output=True,
                    text=True,
                    check=True,
                    env={"PATH": os.environ["PATH"], "HOME": home},
                )
                assert out.stdout.strip() == agent.PROFILE.display_name, name

    def test_the_default_agent(self) -> None:
        assert agent_state.DEFAULT_HARNESS == claude_state.HARNESS

    def test_the_orchestrators_agent(self) -> None:
        assert agent_state.ORCHESTRATOR_HARNESS == claude_state.HARNESS


def fake_agent(sessions: dict[int, dict[str, object]], verifies: bool = True) -> types.SimpleNamespace:
    """An agent whose running sessions are <sessions> by pid; a record with "stale" set is a stale one."""
    return types.SimpleNamespace(
        PROFILE=dataclasses.replace(claude_state.PROFILE, verifies_quotes=verifies),
        record=lambda pid: sessions.get(int(pid)),
        is_stale=lambda entry: bool(entry.get("stale")),
        session_transcript=lambda entry: Path(str(entry["transcript"])) if entry.get("transcript") else None,
    )


class CallingSessionTest(unittest.TestCase):
    """calling_session: the agent session that ran a command, from its process's ancestors. Nothing here reads
    the real agents' records: the suite itself may run inside an agent's session."""

    def ancestors(self, parents: dict[int, int]) -> None:
        patcher = mock.patch.object(agent_state, "parent_pid", side_effect=parents.get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def agents(self, **agents: object) -> None:
        patcher = mock.patch.dict(agent_state.AGENTS, agents, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_nearest_ancestor_that_is_an_agent_with_a_session(self) -> None:
        self.ancestors({100: 200, 200: 300, 300: 400, 400: 1})
        self.agents(
            near=fake_agent({300: {"transcript": "/scratch/near.jsonl"}}),
            other=fake_agent({400: {"transcript": "/scratch/far.jsonl"}}, verifies=False),
        )
        session = agent_state.calling_session(100)
        assert session == agent_state.CallingSession("near", 300, Path("/scratch/near.jsonl"), True)
        assert agent_state.calling_session(400) == agent_state.CallingSession(
            "other", 400, Path("/scratch/far.jsonl"), False
        )

    def test_a_stale_record_is_no_session(self) -> None:
        self.ancestors({100: 200, 200: 300, 300: 1})
        self.agents(stub=fake_agent({200: {"stale": True}, 300: {}}))
        assert agent_state.calling_session(100) == agent_state.CallingSession("stub", 300, None, True)

    def test_no_agent_above_is_none(self) -> None:
        self.ancestors({100: 200, 200: 1})
        self.agents(stub=fake_agent({}))
        assert agent_state.calling_session(100) is None
        self.ancestors({100: 200})  # 200 has gone: ps finds no parent
        assert agent_state.calling_session(100) is None

    def test_it_looks_at_most_max_ancestors_up(self) -> None:
        looked: list[int] = []
        self.ancestors({pid: pid + 1 for pid in range(10, 10 + 2 * agent_state.MAX_ANCESTORS)})
        stub = fake_agent({})
        stub.record = lambda pid: looked.append(int(pid))
        self.agents(stub=stub)
        assert agent_state.calling_session(10) is None
        assert len(looked) == agent_state.MAX_ANCESTORS

    def test_it_starts_from_the_parent_of_this_process(self) -> None:
        self.ancestors({})
        self.agents(stub=fake_agent({4242: {}}))
        with mock.patch("os.getppid", return_value=4242):
            assert agent_state.calling_session() == agent_state.CallingSession("stub", 4242, None, True)

    def test_a_claude_code_session_is_checked_in_its_own_transcript(self) -> None:
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        sessions = home / ".claude" / "sessions"
        sessions.mkdir(parents=True)
        entry = {"pid": 4242, "sessionId": "0f1e2d3c", "cwd": str(home / "repo")}
        (sessions / "4242.json").write_text(json.dumps(entry))
        for name, value in (("LIVE_SESSIONS_DIR", sessions), ("PROJECTS_DIR", home / ".claude" / "projects")):
            patcher = mock.patch.object(claude_state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ancestors({100: 4242, 4242: 1})
        self.agents(claude=claude_state)
        expected = claude_state.transcript_path(str(home / "repo"), "0f1e2d3c")
        assert expected.name == "0f1e2d3c.jsonl" and expected.is_relative_to(home)
        assert agent_state.calling_session(100) == agent_state.CallingSession("claude", 4242, expected, True)
        assert claude_state.session_transcript({"pid": 4242}) is None
        # a record written partly (no pid) is no session: kg runs on as from a terminal
        (sessions / "4242.json").write_text(json.dumps({"sessionId": "0f1e2d3c", "cwd": str(home / "repo")}))
        assert agent_state.calling_session(100) is None
        assert claude_state.is_stale({"pid": "4242"}) and claude_state.is_stale({})

    def test_only_claude_code_checks_quotes_and_the_others_keep_no_stale_records(self) -> None:
        assert claude_state.PROFILE.verifies_quotes
        for agent in (codex_state, agy_state):
            assert not agent.PROFILE.verifies_quotes
            assert agent.session_transcript({"pid": 1, "sessionId": "x", "cwd": "/scratch"}) is None
            assert not agent.is_stale({"pid": 1})

    def test_the_parent_of_a_process_from_ps(self) -> None:
        assert agent_state.parent_pid(os.getpid()) == os.getppid()


class AgentSetupCommandTest(unittest.TestCase):
    """`agent_state.py setup <harness>`: an agent's one-time setup, recorded for uninstall to take back."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name)
        self.calls: list[str] = []
        patcher = mock.patch.dict(os.environ, {"BOSUN_STATE_DIR": str(self.state)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, *, needed: bool, apply: Callable[[], None] | None = None) -> None:
        def applying() -> None:
            self.calls.append("apply")
            if apply:
                apply()

        def needing() -> bool:
            self.calls.append("needed")
            return needed

        setup = AgentSetup("a stub's plugin", needing, applying, lambda: self.calls.append("undo"))
        stub = types.SimpleNamespace(PROFILE=dataclasses.replace(claude_state.PROFILE, setup=setup))
        patcher = mock.patch.dict(agent_state.AGENTS, {"stub": stub})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_setup(self, harness: str = "stub") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = agent_state.main(["setup", harness])
        return status, out.getvalue(), err.getvalue()

    def test_setup_applies_only_when_needed(self) -> None:
        self.install(needed=False)
        assert self.run_setup() == (0, "", "")
        assert self.calls == ["needed"]
        assert agent_changes.setups_made(self.state) == ()

    def test_a_needed_setup_applies_once_and_is_recorded(self) -> None:
        self.install(needed=True)
        assert self.run_setup() == (0, "", "")
        assert self.calls == ["needed", "apply"]
        assert agent_changes.setups_made(self.state) == ("stub",)

    def test_no_state_folder_means_no_setup(self) -> None:
        self.install(needed=True)
        with mock.patch.dict(os.environ, {"BOSUN_STATE_DIR": ""}):
            status, out, err = self.run_setup()
        assert (status, out) == (1, "")
        assert "BOSUN_STATE_DIR" in err
        assert self.calls == ["needed"]  # never applied unrecorded

    def test_a_failed_setup_says_why(self) -> None:
        def refuse() -> None:
            raise SetupError("stub's plugin command is not installed")

        self.install(needed=True, apply=refuse)
        status, out, err = self.run_setup()
        assert status == 1
        assert out == ""
        assert err.strip() == "stub's plugin command is not installed"
        assert agent_changes.setups_made(self.state) == ("stub",)  # on record before it was applied

    def test_an_error_that_is_not_a_setup_error_is_one_line(self) -> None:
        def crash() -> None:
            raise PermissionError("plugins folder is read-only")

        self.install(needed=True, apply=crash)
        status, out, err = self.run_setup()
        assert status == 1
        assert out == ""
        assert err == "PermissionError: plugins folder is read-only\n"
        assert agent_changes.setups_made(self.state) == ("stub",)

    def test_the_setup_is_on_record_while_it_is_applied(self) -> None:
        seen: list[tuple[str, ...]] = []
        self.install(needed=True, apply=lambda: seen.append(agent_changes.setups_made(self.state)))
        self.run_setup()
        assert seen == [("stub",)]

    def test_an_agent_without_a_setup_has_nothing_to_do(self) -> None:
        assert self.run_setup("claude") == (0, "", "")
        assert agent_changes.setups_made(self.state) == ()

    def test_an_agent_bosun_does_not_know_is_a_usage_error(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            assert agent_state.main(["setup", "nobody"]) == USAGE


if __name__ == "__main__":
    unittest.main()
