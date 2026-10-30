"""Unit tests for lib/pane_input.py: reading a Claude window's input line from a pane capture."""

from __future__ import annotations

import io
import sys
import unittest
import unittest.mock
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import pane_input


RULE = "─" * 60
NAMED_RULE = "─" * 50 + " alpha-2026-01-05-1 ─"
MARK, NBSP = pane_input.PROMPT_MARK, pane_input.NBSP
DIM, RESET, GREY = "\x1b[2m", "\x1b[0m", "\x1b[38;5;244m"


def screen(*box: str, above: str = "⏺ Done.") -> str:
    return "\n".join([above, NAMED_RULE, *box, RULE, "  ~/repo · main", ""])


class TypedTextTest(unittest.TestCase):
    def test_an_empty_line_with_a_non_breaking_space(self) -> None:
        self.assertEqual(pane_input.typed_text(screen(f"{MARK}{NBSP} ")), "")

    def test_typed_text(self) -> None:
        self.assertEqual(pane_input.typed_text(screen(f"{MARK} /clear")), "/clear")

    def test_a_multi_line_draft(self) -> None:
        self.assertEqual(
            pane_input.typed_text(screen(f"{MARK} first line", "  second line")), "first line\n  second line"
        )

    def test_a_dim_placeholder_is_not_typed_text(self) -> None:
        self.assertEqual(pane_input.typed_text(screen(f'{MARK} {DIM}Try "fix the tests"{RESET}')), "")

    def test_a_grey_placeholder_is_not_typed_text(self) -> None:
        self.assertEqual(pane_input.typed_text(screen(f"{MARK} {GREY}Try something{RESET}")), "")

    def test_typed_text_after_a_coloured_prompt_mark(self) -> None:
        self.assertEqual(pane_input.typed_text(screen(f"\x1b[39m{MARK}\x1b[0m half typed")), "half typed")

    def test_a_prompt_mark_in_the_transcript_is_not_the_input_line(self) -> None:
        capture = "\n".join([f"{MARK} an earlier prompt", "⏺ reply", NAMED_RULE, f"{MARK} ", RULE])
        self.assertEqual(pane_input.typed_text(capture), "")

    def test_no_input_line_on_screen(self) -> None:
        self.assertIsNone(pane_input.typed_text("$ sleep 600\n"))
        self.assertIsNone(pane_input.typed_text(f"{MARK} a prompt line with no rule above\n"))


class RejectedTest(unittest.TestCase):
    def test_unknown_command_lines(self) -> None:
        capture = "⏺ ok\n  ⎿  Unknown command: /clearLedger\nmore\n"
        self.assertEqual(pane_input.rejected_lines(capture), ["⎿  Unknown command: /clearLedger"])
        self.assertEqual(pane_input.rejected_lines("⏺ all fine\n"), [])


CODEX = "\u203a"
# As real Codex 0.166.0 draws it (capture-pane -e), with its status lines under the box.
CODEX_STATUS = "\n  GPT-6-Luna default · ~/work/api\n  ? for shortcuts\n"


class CodexBoxTest(unittest.TestCase):
    def test_empty_shows_a_dim_placeholder(self) -> None:
        capture = f"\x1b[1m{CODEX}\x1b[0m \x1b[2mAsk Codex to do anything\x1b[0m\n{CODEX_STATUS}"
        self.assertEqual(pane_input.typed_text(capture), "")

    def test_typed_text(self) -> None:
        capture = f"\x1b[1m{CODEX}\x1b[0m half typed words\n{CODEX_STATUS}"
        self.assertEqual(pane_input.typed_text(capture), "half typed words")

    def test_a_paste_of_two_lines(self) -> None:
        capture = f"{CODEX} line one\n  line two\n{CODEX_STATUS}"
        self.assertEqual(pane_input.typed_text(capture), "line one\n  line two")

    def test_the_box_is_the_last_mark_not_an_earlier_prompt(self) -> None:
        box = f"\x1b[1m{CODEX}\x1b[0m \x1b[2mAsk Codex\x1b[0m"
        capture = f"{CODEX} an earlier prompt\n• OK\n\n{box}\n{CODEX_STATUS}"
        self.assertEqual(pane_input.typed_text(capture), "")


class CodexQuestionTest(unittest.TestCase):
    def test_approving_a_command(self) -> None:
        question = "  Would you like to run the following command?"
        capture = f"{question}\n{CODEX} 1. Yes, proceed (y)\n  Press enter to confirm or esc to cancel\n"
        self.assertTrue(pane_input.codex_question(capture))

    def test_trusting_a_folder(self) -> None:
        capture = (
            f"  Trust this folder?\n{CODEX} 1. Trust and continue\n  2. Quit\n\n  enter continue · esc quit\n"
        )
        self.assertTrue(pane_input.codex_question(capture))

    def test_a_normal_screen_is_not_one(self) -> None:
        self.assertFalse(
            pane_input.codex_question(f"• OK\n\n{CODEX} Ask Codex to do anything\n{CODEX_STATUS}")
        )

    def test_only_the_bottom_of_the_screen_counts(self) -> None:
        said = "• Codex said: Press enter to confirm\n" + "• more\n" * 8
        self.assertFalse(pane_input.codex_question(said))


class MainTest(unittest.TestCase):
    def run_main(self, command: str, capture: str) -> tuple[int, str]:
        out = io.StringIO()
        with unittest.mock.patch("sys.stdin", io.StringIO(capture)), unittest.mock.patch("sys.stdout", out):
            code = pane_input.main(["pane_input.py", command])
        return code, out.getvalue()

    def test_question_exit_codes(self) -> None:
        self.assertEqual(self.run_main("question", "  enter continue · esc quit\n")[0], 0)
        self.assertEqual(self.run_main("question", "• OK\n")[0], pane_input.EXIT_TYPED)

    def test_exit_codes(self) -> None:
        self.assertEqual(self.run_main("typed", screen(f"{MARK} "))[0], pane_input.EXIT_EMPTY)
        self.assertEqual(self.run_main("typed", screen(f"{MARK} hi")), (pane_input.EXIT_TYPED, "hi\n"))
        self.assertEqual(self.run_main("typed", "plain shell\n")[0], pane_input.EXIT_NO_BOX)
        self.assertEqual(self.run_main("rejected", "Unknown command: /x\n"), (0, "Unknown command: /x\n"))

    def test_usage(self) -> None:
        with unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(self.run_main("nope", "")[0], 2)


if __name__ == "__main__":
    unittest.main()
