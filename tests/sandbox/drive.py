#!/usr/local/bin/python3
"""Run a command in a real terminal and answer it: drive.py <log> <expected> <keys> ... -- <command...>

Waits for each <expected> text on the screen, then types its <keys> (`<enter>` and `<ctrl-c>` for
those keys), and at the end returns the command's exit status. The wizard needs a real terminal to be
tested for real: one key at a time, /dev/tty behind `curl | sh`, and the terminal put back afterwards.
"""

import os
import pty
import re
import select
import sys
import time
from pathlib import Path


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TIMEOUT_SECONDS = 60
CHUNK = 65536


def read_some(fd: int, seconds: float) -> str | None:
    """What arrived in <seconds>; None once the command has gone."""
    if not select.select([fd], [], [], seconds)[0]:
        return ""
    try:
        data = os.read(fd, CHUNK)
    except OSError:
        return None
    return ANSI.sub("", data.decode(errors="replace")) if data else None


def wait_for(fd: int, text: str, seen: list[str]) -> bool:
    """Until <text> shows up after what earlier steps already consumed."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    tail = ""
    while time.monotonic() < deadline:
        chunk = read_some(fd, 0.2)
        if chunk is None:
            return False
        seen.append(chunk)
        tail += chunk
        if text in tail:
            return True
    return False


def main(argv: list[str]) -> int:
    split = argv.index("--")
    log, steps, command = argv[0], argv[1:split], argv[split + 1 :]
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm"
        os.execvp(command[0], command)  # noqa: S606 (the command under test)
    seen: list[str] = []
    status = 0
    for expected, keys in zip(steps[::2], steps[1::2], strict=True):
        if not wait_for(fd, expected, seen):
            print(f"never saw: {expected}")
            status = 1
            break
        os.write(fd, keys.replace("<enter>", "\r").replace("<ctrl-c>", "\x03").encode())
    while status == 0 and (chunk := read_some(fd, 1.0)) is not None:
        seen.append(chunk)
    Path(log).write_text("".join(seen))
    if status:
        print("".join(seen)[-1500:])
        os.kill(pid, 9)
    _, wait_status = os.waitpid(pid, 0)
    return status or os.waitstatus_to_exitcode(wait_status)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
