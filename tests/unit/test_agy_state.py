"""lib/agy_state.py's screen readers, over captures of real Antigravity CLI (agy) 1.5.5 in
tests/fixtures/agy/screens/ (`.ansi` as `tmux capture-pane -e` gives them, `.txt` plain)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_files
import agy_state
from screen_text import SGR


SCREENS = Path(__file__).resolve().parents[1] / "fixtures" / "agy" / "screens"


def ansi(name: str) -> list[str]:
    """The lines of a capture with its escape sequences, as `typed` gets them."""
    return (SCREENS / f"{name}.ansi").read_text(encoding="utf-8").splitlines()


def plain(name: str) -> str:
    """A capture without escape sequences, as `question` gets it."""
    if (SCREENS / f"{name}.txt").exists():
        return (SCREENS / f"{name}.txt").read_text(encoding="utf-8")
    return SGR.sub("", "\n".join(ansi(name)))


class TypedTextTest(unittest.TestCase):
    def test_empty_box(self) -> None:
        self.assertEqual(agy_state.agy_typed_text(ansi("idle")), "")

    def test_text_in_the_box_over_two_lines(self) -> None:
        self.assertEqual(
            agy_state.agy_typed_text(ansi("typed")),
            "Remember the word PELICAN for later.\nReply with just the word noted.",
        )

    def test_the_last_box_is_the_one_not_a_message_in_the_history(self) -> None:
        self.assertEqual(agy_state.agy_typed_text(ansi("busy")), "")

    def test_a_footer_hint_is_not_typed_text(self) -> None:
        self.assertEqual(agy_state.agy_typed_text(ansi("task")), "")

    def test_no_box(self) -> None:
        self.assertIsNone(agy_state.agy_typed_text(["no box here", "> not between rules"]))

    def test_a_box_no_rule_closes_is_no_box(self) -> None:
        rule = "\x1b[90m" + "\u2500" * 40
        self.assertIsNone(agy_state.agy_typed_text([rule, "> old", "reply"]))
        self.assertIsNone(agy_state.agy_typed_text([rule, "> old", rule]))
        self.assertEqual(agy_state.agy_typed_text([rule, "> old", rule, "? for shortcuts"]), "old")

    def test_no_box_on_a_dialog_or_the_trust_and_sign_in_screens(self) -> None:
        for name in ("dialog", "trust", "sign-in"):
            with self.subTest(name):
                self.assertIsNone(agy_state.agy_typed_text(ansi(name)))

    def test_plain_capture_of_an_empty_box(self) -> None:
        self.assertEqual(agy_state.agy_typed_text(plain("cancelled").splitlines()), "")


class QuestionTest(unittest.TestCase):
    def test_dialog_trust_and_sign_in_are_questions(self) -> None:
        for name in ("dialog", "trust", "sign-in"):
            with self.subTest(name):
                self.assertTrue(agy_state.agy_question(plain(name)))

    def test_other_screens_are_not(self) -> None:
        for name in ("idle", "typed", "busy", "queued", "task", "cancelled"):
            with self.subTest(name):
                self.assertFalse(agy_state.agy_question(plain(name)))


class ScreenStatusTest(unittest.TestCase):
    def test_each_capture(self) -> None:
        expected = {
            "idle": "idle",
            "typed": None,
            "busy": None,
            "dialog": "waiting",
            "queued": None,
            "task": "shell",
            "cancelled": "idle",
        }
        for name, status in expected.items():
            with self.subTest(name):
                self.assertEqual(agy_state.screen_status(plain(name)), status)

    def test_a_capture_with_escapes_reads_the_same(self) -> None:
        self.assertEqual(agy_state.screen_status("\n".join(ansi("idle"))), "idle")
        self.assertIsNone(agy_state.screen_status("\n".join(ansi("typed"))))

    def test_no_box_is_no_status(self) -> None:
        self.assertIsNone(agy_state.screen_status("starting up\n"))
        self.assertIsNone(agy_state.screen_status(""))


class ScreenTest(unittest.TestCase):
    def test_the_readers_agy_hands_out(self) -> None:
        self.assertIs(agy_state.SCREEN.typed, agy_state.agy_typed_text)
        self.assertIs(agy_state.SCREEN.question, agy_state.agy_question)
        self.assertIs(agy_state.SCREEN.rejected, agent_files.never_rejects)


if __name__ == "__main__":
    unittest.main()
