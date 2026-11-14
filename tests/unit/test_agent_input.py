"""The commands bosun runs for an answer never get the terminal as their input (terminal_input.py says
why: Claude Code dies on /dev/tty, and its login check read as logged out)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agy_state
import claude_state
import codex_state
import init_detect
from terminal_input import a_terminal_as_input, calls_without_input, stand_in


class EveryCallSaysItsInput(unittest.TestCase):
    """A new call in these modules says what its input is, so none inherits init's terminal unseen."""

    def test_the_agents_modules_and_init_s_detection(self) -> None:
        for module in (claude_state, codex_state, agy_state, init_detect):
            with self.subTest(module=module.__name__):
                self.assertEqual(calls_without_input(module), [], "give each one stdin=subprocess.DEVNULL")


class OnATerminal(unittest.TestCase):
    """Run as init runs them under get.sh, with a terminal as the input, against stand-ins that refuse one."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_the_stand_in_refuses_a_terminal(self) -> None:
        tool = stand_in(self.dir, "tool", "ok")
        with a_terminal_as_input():
            refused = subprocess.run([str(tool)], capture_output=True, text=True, check=False)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("kqueue", refused.stderr)

    def test_claude_s_login_check(self) -> None:
        claude = stand_in(self.dir, "claude", '{"loggedIn": true, "authMethod": "claude.ai"}')
        with a_terminal_as_input():
            self.assertIs(claude_state.auth_status(str(claude)).logged_in, True)

    def test_codex_s_login_check(self) -> None:
        codex = stand_in(self.dir, "codex", "Logged in using ChatGPT")
        with mock.patch.dict("os.environ", {codex_state.BIN_KEY: str(codex)}), a_terminal_as_input():
            self.assertTrue(codex_state.logged_in())

    def test_init_s_detection(self) -> None:
        gh = stand_in(self.dir, "gh", "Logged in to github.com")
        with a_terminal_as_input():
            self.assertIsNotNone(init_detect.output_of(str(gh), "auth", "status"))


if __name__ == "__main__":
    unittest.main()
