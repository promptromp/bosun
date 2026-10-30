"""lib/init_terminal.py: keys and lines through a real pseudo-terminal."""

from __future__ import annotations

import io
import os
import pty
import sys
import termios
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import init_terminal
from init_terminal import TerminalUi
from init_wizard import ENTER, bold


class PtyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.master, self.slave = pty.openpty()
        self.addCleanup(os.close, self.slave)
        self.master_open = True
        self.addCleanup(self.close_master)
        self.out = io.StringIO()
        self.ui = TerminalUi(self.slave, self.out)

    def close_master(self) -> None:
        if self.master_open:
            os.close(self.master)
            self.master_open = False

    def test_one_key_without_return(self) -> None:
        os.write(self.master, b"y")
        self.assertEqual(self.ui.key(), "y")

    def test_keys_are_lowercased(self) -> None:
        os.write(self.master, b"Q")
        self.assertEqual(self.ui.key(), "q")

    def test_return_is_enter(self) -> None:
        os.write(self.master, b"\r")
        self.assertEqual(self.ui.key(), ENTER)

    def test_what_is_typed_ahead_is_not_lost(self) -> None:
        os.write(self.master, b"2\ry")
        self.assertEqual([self.ui.key(), self.ui.key(), self.ui.key()], ["2", ENTER, "y"])

    def test_an_arrow_key_is_one_key_that_means_nothing(self) -> None:
        os.write(self.master, b"\x1b[A")
        self.assertEqual(self.ui.key(), "\x1b")
        os.write(self.master, b"y")
        self.assertEqual(self.ui.key(), "y")

    def test_ctrl_d_ends_the_input(self) -> None:
        os.write(self.master, b"\x04")
        self.assertEqual(self.ui.key(), "")

    def test_a_terminal_that_went_away_ends_the_input(self) -> None:
        self.close_master()
        self.assertEqual(self.ui.key(), "")

    def test_the_terminal_is_put_back(self) -> None:
        line_mode = termios.ECHO | termios.ICANON  # all it changes; macOS adds PENDIN of its own
        before = termios.tcgetattr(self.slave)[3] & line_mode
        self.assertEqual(before, line_mode)
        os.write(self.master, b"y")
        self.ui.key()
        self.assertEqual(termios.tcgetattr(self.slave)[3] & line_mode, line_mode)

    def test_ctrl_c_is_an_interrupt(self) -> None:
        with (
            mock.patch.object(self.ui, "read_byte", return_value=b"\x03"),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.ui.key()

    def test_a_line_is_a_line(self) -> None:
        os.write(self.master, b"~/Development/api\n")
        self.assertEqual(self.ui.line("Folder: "), "~/Development/api")
        self.assertIn("Folder: ", self.out.getvalue())


class OutputTest(unittest.TestCase):
    def test_no_color_when_it_is_not_a_terminal(self) -> None:
        out = io.StringIO()
        ui = TerminalUi(0, out)
        ui.show(bold("hello"))
        ui.say(bold("there"))
        self.assertEqual(out.getvalue(), "\nhello\nthere\n")

    def test_color_is_only_where_it_can_show(self) -> None:
        tty_out = mock.Mock(isatty=lambda: True)
        self.assertTrue(init_terminal.wants_color(tty_out, {"TERM": "xterm"}))
        self.assertFalse(init_terminal.wants_color(tty_out, {"TERM": "xterm", "NO_COLOR": "1"}))
        self.assertFalse(init_terminal.wants_color(tty_out, {"TERM": "dumb"}))
        self.assertFalse(init_terminal.wants_color(mock.Mock(isatty=lambda: False), {"TERM": "xterm"}))

    def test_a_new_screen_clears_the_old_one_in_a_terminal(self) -> None:
        out = io.StringIO()
        ui = TerminalUi(0, out)
        ui.color = True
        ui.show(bold("hello"))
        self.assertTrue(out.getvalue().startswith(init_terminal.CLEAR))
        self.assertIn(bold("hello"), out.getvalue())


if __name__ == "__main__":
    unittest.main()
