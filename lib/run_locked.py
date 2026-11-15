"""One at a time, for bosun's shell commands (lib/common.sh's lock_here, and one_at_a_time on it): a lock
held by the command's shell, so that two of them never interleave (the morning job's `bosun start` and one
you typed would both build the same windows; `bosun init` and `bosun settings set` would write bosun.env
over each other).

The lock is a flock on <lock file>, held on file descriptor LOCK_FD. The shell opens LOCK_FD on the lock file
itself and has --take lock it: the lock belongs to that open file, so it stays held by the shell once this
exits, until the shell closes LOCK_FD or exits. Whatever the shell starts inherits it, so a nested command
that asks (`bosun refresh` from `bosun start`) finds it held (--held) and goes on, and the kernel drops it
when they all exit, killed or not: no lock is ever left behind. A long-lived child must not keep it: tmux,
whose server outlives the command, gets LOCK_FD closed (tmux_cmd), and so does the keep-awake's caffeinate.

The third form runs a command holding the lock, for what is not a bosun shell (tools/swift_hook.sh, the
tests' holders).

Usage: run_locked.py --held <lock file>
           exit 0 if this process holds the lock on LOCK_FD already, 1 if not
       run_locked.py --take <lock file> <wait seconds>
           locks LOCK_FD, which the caller opened on <lock file>, waiting up to <wait seconds> for the one
           holding it: exit 0 when locked, BUSY past that (saying nothing: the caller says why), USAGE if
           LOCK_FD is not open on <lock file>
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


def lock(fd: int, wait: float) -> bool:
    """Whether <fd>'s file is locked, waiting up to <wait> seconds for the one holding it."""
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_SECONDS)


def acquire(path: str, wait: float) -> int | None:
    """A descriptor of <path> holding its lock, waiting up to <wait> seconds; None if it stayed taken."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    if lock(fd, wait):
        return fd
    os.close(fd)
    return None


def take(path: str, wait: float) -> int:
    """--take: LOCK_FD, which the caller opened on <path>, locked for the caller."""
    if not held(path):
        print(f"bosun: file descriptor {LOCK_FD} is not open on {path}", file=sys.stderr)
        return USAGE
    return 0 if lock(LOCK_FD, wait) else BUSY


def main(argv: Sequence[str]) -> int:
    if len(argv) == 2 and argv[0] == "--held":  # noqa: PLR2004 (the flag and its file)
        return 0 if held(argv[1]) else 1
    if len(argv) == 3 and argv[0] == "--take":  # noqa: PLR2004 (the flag, its file and the wait)
        return take(argv[1], float(argv[2]))
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
