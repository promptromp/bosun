"""Each agent module declares what it is and can do, in a Profile."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_state
import claude_state
import codex_state
import uninstall_plan
import update_backups
from agent_files import Profile
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

    def test_the_profiles_command(self) -> None:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            assert agent_state.main(["profiles"]) == 0
        assert captured.getvalue().splitlines() == [
            "claude\tBOSUN_CLAUDE_BIN\tBOSUN_CLAUDE_FLAGS\tBOSUN_MODEL\tyes\tyes\tyes\tyes\tidle,busy,shell,waiting",
            "codex\tBOSUN_CODEX_BIN\tBOSUN_CODEX_FLAGS\tBOSUN_CODEX_MODEL\tno\tno\tno\tno\tidle,busy",
        ]

    def test_the_default_agent(self) -> None:
        assert agent_state.DEFAULT_HARNESS == claude_state.HARNESS


if __name__ == "__main__":
    unittest.main()
