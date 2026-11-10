"""One at a time, for bosun's shell commands (lib/common.sh's one_at_a_time): runs a command holding a
lock, so that two of them never interleave (the morning job's `bosun start` and one you typed would both
build the same windows).

The lock is a flock on <lock file>, held on file descriptor LOCK_FD of the command it runs. Whatever that
command starts inherits it, so a nested command that asks (`bosun refresh` from `bosun start`) finds it
held and goes on, and the kernel drops it when they exit, killed or not: no lock is ever left behind. A
long-lived child must not keep it: tmux, whose server outlives the command, gets LOCK_FD closed
(tmux_cmd).

Usage: run_locked.py --held <lock file>
           exit 0 if this process holds the lock on LOCK_FD already, 1 if not
       run_locked.py <lock file> <wait seconds> <busy message> -- <command> [args...]
           runs the command holding the lock, waiting up to <wait seconds> for the one holding it; past
           that it prints "bosun: <busy message>" and exits BUSY
"""

from __future__ import annotations

import fcntl
import os
import sys
import time
from collections.abc import Sequence


LOCK_FD = 9
POLL_SECONDS = 0.2
BUSY = 75  # EX_TEMPFAIL: try again later
USAGE = 2


def held(path: str) -> bool:
    """Whether LOCK_FD is open on <path>: a parent took the lock, and this runs under it."""
    try:
        mine, there = os.fstat(LOCK_FD), os.stat(path)
    except OSError:
        return False
    return (mine.st_dev, mine.st_ino) == (there.st_dev, there.st_ino)


def acquire(path: str, wait: float) -> int | None:
    """A descriptor of <path> holding its lock, waiting up to <wait> seconds; None if it stayed taken."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(POLL_SECONDS)


def main(argv: Sequence[str]) -> int:
    if len(argv) == 2 and argv[0] == "--held":  # noqa: PLR2004 (the flag and its file)
        return 0 if held(argv[1]) else 1
    if len(argv) < 5 or argv[3] != "--":  # noqa: PLR2004 (three arguments, --, a command)
        print(__doc__, file=sys.stderr)
        return USAGE
    path, wait, message, _, *command = argv
    fd = acquire(path, float(wait))
    if fd is None:
        print(f"bosun: {message}", file=sys.stderr)
        return BUSY
    if fd != LOCK_FD:
        os.dup2(fd, LOCK_FD)  # inheritable, unlike the descriptor os.open made
        os.close(fd)
    os.execvp(command[0], command)  # noqa: S606 (bosun's own command, from one_at_a_time)
    return 1  # not reached: execvp replaces this process or raises


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
