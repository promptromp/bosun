"""lib/connectors.py: what Claude Code's and Codex's connector modules share, and the doctor command that
asks the window's agent."""

from __future__ import annotations

import contextlib
import dataclasses
import sys
import types
import unittest
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import claude_state  # noqa: E402
import connector_fakes  # noqa: E402
import connectors  # noqa: E402
from connectors import Allowed, ConnectorsError, parse  # noqa: E402


MAIL = "claude.ai Acme Mail"


class PureTest(unittest.TestCase):
    def test_empty_list_allows_nothing(self) -> None:
        for value in ("", " ", " , ,"):
            self.assertEqual(parse(value), Allowed())

    def test_all_has_no_allow_list(self) -> None:
        self.assertTrue(parse(" all ").everything)

    def test_all_with_names_is_refused(self) -> None:
        with self.assertRaises(ConnectorsError):
            parse("all, acme-local")
        self.assertIsInstance(ConnectorsError("x"), ValueError)

    def test_names_are_trimmed_and_kept_in_order(self) -> None:
        self.assertEqual(parse(f" acme-local ,{MAIL},, acme-local").names, ("acme-local", MAIL))


class DoctorTest(connector_fakes.ScratchCase):
    """`connectors.py doctor` asks the module its window's agent names."""

    def doctor(self, harness: str) -> tuple[int, str, str]:
        return connector_fakes.capture(
            connectors.main,
            *("doctor", "--window", "alpha", "--kind", "session", "--harness", harness, "--list", "x"),
            *("--cwd", str(self.root), "--state-dir", str(self.state), "--agent-bin", "no-agent"),
        )

    def claude_names(
        self, doctor_line: Callable[[Any, Allowed], tuple[str, str]]
    ) -> AbstractContextManager[Any]:
        """Claude Code's profile naming a stand-in module whose `doctor_line` is <doctor_line>."""
        stub = types.SimpleNamespace(doctor_line=doctor_line)
        profile = dataclasses.replace(claude_state.PROFILE, connectors="stub_connectors")
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.dict(sys.modules, {"stub_connectors": stub}))
        stack.enter_context(mock.patch.object(claude_state, "PROFILE", profile))
        return stack

    def test_doctor_asks_the_windows_agent_module(self) -> None:
        with self.claude_names(lambda args, allowed: ("ok", "stub")):
            self.assertEqual(self.doctor("claude"), (0, "ok\tstub\n", ""))

    def test_doctor_hands_the_module_the_list_it_parsed(self) -> None:
        seen: list[tuple[str, Allowed]] = []

        def doctor_line(args: Any, allowed: Allowed) -> tuple[str, str]:
            seen.append((args.window, allowed))
            return "ok", "stub"

        with self.claude_names(doctor_line):
            self.doctor("claude")
        self.assertEqual(seen, [("alpha", Allowed(("x",)))])

    def test_doctor_refuses_an_agent_bosun_does_not_know(self) -> None:
        with self.assertRaises(SystemExit) as raised, mock.patch("sys.stderr"):
            self.doctor("antigravity")
        self.assertEqual(raised.exception.code, 2)

    def test_doctor_says_when_the_module_cannot_check(self) -> None:
        def broken(args: Any, allowed: Allowed) -> tuple[str, str]:
            raise RuntimeError("boom")

        with self.claude_names(broken):
            self.assertEqual(
                self.doctor("claude"),
                (0, "warn\talpha: could not check its connectors (RuntimeError: boom)\n", ""),
            )


if __name__ == "__main__":
    unittest.main()
