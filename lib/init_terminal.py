"""The wizard's terminal: a key at a time, a screen at a time, and the terminal put back afterwards."""

from __future__ import annotations

import os
import re
import select
import sys
import termios
import tty
from typing import TextIO

from init_wizard import ENTER


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CLEAR = "\x1b[H\x1b[2J"
ESCAPE, CTRL_C, CTRL_D = b"\x1b", b"\x03", b"\x04"
ESCAPE_SEQUENCE_SECONDS = 0.05  # how long the rest of an arrow key's bytes may take


def wants_color(out: TextIO, environ: dict[str, str] | os._Environ[str] = os.environ) -> bool:
    """https://no-color.org, and nothing fancy where it cannot show."""
    return out.isatty() and not environ.get("NO_COLOR") and environ.get("TERM") != "dumb"


class TerminalUi:
    def __init__(self, in_fd: int = 0, out: TextIO | None = None) -> None:
        self.in_fd = in_fd
        self.out = out or sys.stdout
        self.color = wants_color(self.out)

    def write(self, text: str) -> None:
        self.out.write(text if self.color else ANSI.sub("", text))
        self.out.flush()

    def show(self, text: str) -> None:
        self.write((CLEAR if self.color else "\n") + text + "\n")

    def say(self, text: str) -> None:
        self.write(text + "\n")

    def read_byte(self) -> bytes:
        """One byte, in a mode that does not wait for Return; b"" if the terminal has gone away."""
        try:
            saved = termios.tcgetattr(self.in_fd)
            tty.setcbreak(self.in_fd, termios.TCSANOW)  # the default flushes what was typed ahead
            data = self.read_key_bytes()
            termios.tcsetattr(self.in_fd, termios.TCSANOW, saved)  # DRAIN can wait forever on macOS
        except (OSError, termios.error):
            return b""
        return data

    def read_key_bytes(self) -> bytes:
        data = os.read(self.in_fd, 1)
        if data == ESCAPE:  # an arrow key or the like: its other bytes are not an answer
            while select.select([self.in_fd], [], [], ESCAPE_SEQUENCE_SECONDS)[0]:
                os.read(self.in_fd, 8)
        return data

    def key(self) -> str:
        """One key, lowercase: ENTER for Return, "" for Ctrl-D or the end of the input, and an escape
        sequence as a key that means nothing."""
        data = self.read_byte()
        if data in (b"", CTRL_D):
            return ""
        if data in (b"\r", b"\n"):
            return ENTER
        if data == CTRL_C:  # cbreak leaves signals on; this is for terminals in another mode
            raise KeyboardInterrupt
        return data.decode(errors="replace").lower()

    def line(self, prompt: str) -> str:
        self.write(prompt)
        data = b""
        while not data.endswith(b"\n"):
            try:
                chunk = os.read(self.in_fd, 256)
            except OSError:
                break
            if not chunk:
                break
            data += chunk
        return data.decode(errors="replace").rstrip("\n")
