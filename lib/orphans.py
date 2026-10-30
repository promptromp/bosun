#!/usr/bin/env python3
"""Shells a tmux pane leaves behind, found and stopped by their command line, never by a pattern.

Usage: orphans.py helpers <pid> [--wrapper <prefix>]... [--kill]
       orphans.py launch-shells --wrapper <prefix>... [--kill]
       (either with --ps <file>: read that `ps -Ao pid=,ppid=,tty=,command=` listing; for tests)

A pane runs its command in an interactive login shell (`<shell> -lic '<bosun> launch <window>
; exec <shell> -l'`, see build_launch_command), so the user's startup files run there. Some fork
helpers off (a prompt theme's git-status daemon, for one) that detach at once, so launchd adopts
them (ppid 1), and that exit only once their shell has shown its first prompt. The launch shell
never shows one: it runs Claude, then replaces itself with the fallback shell. And a fallback shell
that `refresh` replaces seconds after Claude exited is gone before its first prompt. Each such shell
leaves a helper behind for good: a fork of it, so with its exact command line, on its terminal.

- `helpers <pid>`: the helpers shell <pid> started: processes on its terminal with its exact
  command line, other than itself, adopted by launchd (ppid 1) or still under the subshell that
  forks them off (or under the shell itself). With --wrapper: only if <pid> is a
  launch wrapper (its command line starts with a prefix), a shell that never shows a prompt; the
  helpers of a shell you use (at its prompt) are left alone. `launch` and `checkin --loop` stop
  their own shell's helpers this way, `refresh` and `stop` those of a pane they replace or close.
- `launch-shells`: launch wrappers' helpers that outlived their pane (what `bosun orphans`
  lists): ppid 1, a command line starting with a prefix, no children, not a tmux process (the tmux
  server's own command line can hold the same text: `new-session ... -- <shell> -lic ...`).

--kill sends SIGTERM to each, after checking once more that the pid still has the same parent,
terminal and command line. Prints one "<pid>\t<command>" line per process found.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path


LAUNCHD_PID = 1
NO_TTY = ("??", "?", "-", "")
PS_FIELDS = "pid=,ppid=,tty=,command="
PS_COLUMNS = 4  # pid, ppid, tty, command (which may be empty)
TMUX = "tmux"
USAGE = 2


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    tty: str
    command: str


def parse_ps(text: str) -> list[Proc]:
    """Rows of `ps -o pid=,ppid=,tty=,command=`; unreadable rows are skipped."""
    procs = []
    for line in text.splitlines():
        fields = line.split(None, PS_COLUMNS - 1)
        if len(fields) < PS_COLUMNS - 1 or not (fields[0].isdigit() and fields[1].isdigit()):
            continue
        command = fields[PS_COLUMNS - 1] if len(fields) == PS_COLUMNS else ""
        procs.append(Proc(int(fields[0]), int(fields[1]), fields[2], command.rstrip()))
    return procs


def read_ps(*args: str) -> str:
    done = subprocess.run(["ps", *args, "-o", PS_FIELDS], capture_output=True, text=True, check=False)
    return done.stdout


def is_wrapper(command: str, prefixes: Iterable[str]) -> bool:
    return any(command.startswith(prefix) for prefix in prefixes)


def is_tmux(command: str) -> bool:
    words = command.split(None, 1)
    return bool(words) and Path(words[0]).name == TMUX


def shell_of(procs: Sequence[Proc], pid: int, wrappers: Sequence[str]) -> Proc | None:
    """Shell <pid>, if it has a terminal and (with <wrappers>) is a launch wrapper."""
    shell = next((p for p in procs if p.pid == pid), None)
    if shell is None or shell.tty in NO_TTY:
        return None
    return shell if not wrappers or is_wrapper(shell.command, wrappers) else None


def helpers(procs: Sequence[Proc], pid: int, wrappers: Sequence[str] = ()) -> list[Proc]:
    """The detached helpers shell <pid> started (see the module docstring)."""
    shell = shell_of(procs, pid, wrappers)
    if shell is None:
        return []
    forks = {p.pid for p in procs if p.pid != pid and p.tty == shell.tty and p.command == shell.command}
    # Adopted by launchd already, or still under the subshell that forked it off (or the shell).
    parents = {LAUNCHD_PID, pid, *forks}
    return [p for p in procs if p.pid in forks and p.ppid in parents]


def launch_shells(procs: Sequence[Proc], wrappers: Sequence[str]) -> list[Proc]:
    """Launch wrappers' helpers that outlived their pane (see the module docstring)."""
    parents = {p.ppid for p in procs}
    return [
        p
        for p in procs
        if p.ppid == LAUNCHD_PID
        and wrappers
        and is_wrapper(p.command, wrappers)
        and p.pid not in parents
        and not is_tmux(p.command)
    ]


def unchanged(proc: Proc) -> bool:
    """Whether <proc>'s pid still runs the same process: same parent, terminal and command line."""
    now = parse_ps(read_ps("-p", str(proc.pid)))
    return bool(now) and now[0] == proc


def stop(found: Sequence[Proc], check: Callable[[Proc], bool] = unchanged) -> list[Proc]:
    """SIGTERM each process that is still the one found; returns those it signalled."""
    stopped = []
    for proc in found:
        if not check(proc):
            continue
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        stopped.append(proc)
    return stopped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orphans.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("helpers", "launch-shells"):
        command = sub.add_parser(name)
        if name == "helpers":
            command.add_argument("pid", type=int)
        command.add_argument("--wrapper", action="append", default=[])
        command.add_argument("--kill", action="store_true")
        command.add_argument("--ps", type=Path, help="a ps listing to read instead (tests)")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "launch-shells" and not args.wrapper:
        print("orphans.py: launch-shells needs at least one --wrapper prefix", file=sys.stderr)
        return USAGE
    procs = parse_ps(args.ps.read_text() if args.ps else read_ps("-A"))
    if args.command == "helpers":
        found = helpers(procs, args.pid, args.wrapper)
    else:
        found = launch_shells(procs, args.wrapper)
    if args.kill:
        found = stop(found)
    for proc in found:
        print(f"{proc.pid}\t{proc.command}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
