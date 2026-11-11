"""Each agent module declares what it is and can do, in a Profile."""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import io
import os
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
        for agent in AGENTS.values():
            module = importlib.import_module(agent.PROFILE.connectors)
            assert callable(module.main) and callable(module.doctor_line)

    def test_the_profiles_command(self) -> None:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            assert agent_state.main(["profiles"]) == 0
        assert captured.getvalue().splitlines() == [
            "claude\tBOSUN_CLAUDE_BIN\tBOSUN_CLAUDE_FLAGS\tBOSUN_MODEL\tyes\tyes\tyes\tyes\tidle,busy,shell,waiting\t-",
            "codex\tBOSUN_CODEX_BIN\tBOSUN_CODEX_FLAGS\tBOSUN_CODEX_MODEL\tno\tno\tno\tno\tidle,busy\t-",
        ]

    def test_profiles_print_update_args(self) -> None:
        for harness in ("claude", "codex"):
            assert AGENTS[harness].PROFILE.update_args == ()
            assert agent_state.profile_line(harness, AGENTS[harness].PROFILE).endswith("\t-")
        updating = dataclasses.replace(claude_state.PROFILE, update_args=("update", "--now"))
        assert agent_state.profile_line("stub", updating).endswith("\tidle,busy,shell,waiting\tupdate --now")

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


if __name__ == "__main__":
    unittest.main()


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

    def test_a_failed_setup_says_why(self) -> None:
        def refuse() -> None:
            raise SetupError("stub's plugin command is not installed")

        self.install(needed=True, apply=refuse)
        status, out, err = self.run_setup()
        assert status == 1
        assert out == ""
        assert err.strip() == "stub's plugin command is not installed"
        assert agent_changes.setups_made(self.state) == ()

    def test_an_agent_without_a_setup_has_nothing_to_do(self) -> None:
        assert self.run_setup("claude") == (0, "", "")
        assert agent_changes.setups_made(self.state) == ()

    def test_an_agent_bosun_does_not_know_is_a_usage_error(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            assert agent_state.main(["setup", "nobody"]) == USAGE
