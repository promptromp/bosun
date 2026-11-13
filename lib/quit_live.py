"""`bosun quit`'s world: the live Ports (the windows from `bosun sessions --json`, the keep-awake, the
`windows` lock, launchctl and the console job, quit.json, `bosun stop`) and the command libexec/bosun-quit
runs. The plan, the texts and the run over the ports are lib/quit.py (design: docs/design/menu-bar.md).

    quit_live.py --mode plan|finish|now --state-dir D --session S --console-label L --console-marker M
                 [--wait-minutes N] [--lock-wait SECONDS] [--pause-morning] [--sleep] [--json]
    quit_live.py [--json] --error TEXT      only say, as the run would, that the quit failed and why
"""

from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import console_live
import console_token
import keep_awake
import quit_state
import run_locked
import update_state
from quit import (
    CONSOLE_NONE,
    CONSOLE_STILL,
    CONSOLE_STOPPED,
    LOCK_BUSY,
    Options,
    Ports,
    QuitError,
    Window,
    failed_object,
    finish,
    run,
    windows_from_sessions,
)


CONSOLE_POLL_SECONDS = 0.5
CONSOLE_WAIT_SECONDS = 5
LAUNCHCTL_TIMEOUT_SECONDS = 20
STOP_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class World:
    """What the live ports need to know of this install, as libexec/bosun-quit passes it."""

    state_dir: Path
    session: str
    console_label: str
    console_marker: Path
    lock_wait: float  # seconds to wait for a start or refresh that holds the windows lock


def read_windows() -> list[Window]:
    try:
        listing = console_live.bosun_json(console_live.run_command, "sessions", "--json")
    except console_live.LiveError as exc:
        raise QuitError(f"the windows could not be read: {exc}") from exc
    if not isinstance(listing, list):
        raise QuitError("the windows could not be read: sessions --json gave no list")
    return windows_from_sessions(listing)


def keep_awake_clock(world: World) -> str | None:
    until = keep_awake.keep_awake_until(world.state_dir, world.session)
    return f"{until:%H:%M}" if until is not None else None


def lock_fd_open() -> bool:
    """Whether the windows lock's descriptor is open in this process (inside the lock)."""
    try:
        os.fstat(run_locked.LOCK_FD)
    except OSError:
        return False
    return True


@contextlib.contextmanager
def windows_lock(world: World) -> Iterator[None]:
    """Hold the `windows` lock (what `bosun start` takes: one_at_a_time) for the body, on run_locked.LOCK_FD
    so that what the body starts inherits it. Under one already (a parent holds it) nothing is taken."""
    path = str(world.state_dir / "windows.lock")
    if run_locked.held(path):
        yield
        return
    try:
        fd = run_locked.acquire(path, world.lock_wait)
    except OSError as exc:  # the state folder cannot be made or the lock file opened
        raise QuitError(f"could not take the windows lock {path}: {exc}; nothing was changed") from exc
    if fd is None:
        raise QuitError(LOCK_BUSY)
    if fd != run_locked.LOCK_FD:
        os.dup2(fd, run_locked.LOCK_FD)  # inheritable, unlike the descriptor os.open made
        os.close(fd)
    try:
        yield
    finally:
        os.close(run_locked.LOCK_FD)


def launchctl(*args: str) -> bool:
    """Whether `launchctl <args>` succeeded; a missing launchctl or a slow one is a no."""
    try:
        return (
            subprocess.run(  # our own argv, no shell
                ["launchctl", *args],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=LAUNCHCTL_TIMEOUT_SECONDS,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def stop_console(world: World) -> tuple[str, int | None]:
    """The console's job stopped, and said so. The marker goes first: it is the job's KeepAlive condition,
    so launchd does not start it again. Returns what came of the console (CONSOLE_STOPPED, CONSOLE_NONE, or
    CONSOLE_STILL with its port, if known: the job's may be slow to exit, or it was started by hand, which is
    not hunted for)."""
    try:
        world.console_marker.unlink(missing_ok=True)
    except OSError as exc:
        raise QuitError(
            f"could not remove {world.console_marker}, which keeps the console job up: {exc}"
        ) from exc
    was_running = console_token.running(world.state_dir)
    target = f"gui/{os.getuid()}/{world.console_label}"
    if launchctl("print", target):  # a missing job (BOSUN_CONSOLE_ALWAYS=0) is no failure
        launchctl("kill", "TERM", target)
        deadline = time.monotonic() + CONSOLE_WAIT_SECONDS
        while console_token.running(world.state_dir) and time.monotonic() < deadline:
            time.sleep(CONSOLE_POLL_SECONDS)
    if console_token.running(world.state_dir):
        return CONSOLE_STILL, console_token.served_port(world.state_dir)
    return (CONSOLE_STOPPED if was_running else CONSOLE_NONE), None


def write_quit(world: World, paused: bool) -> None:
    try:
        quit_state.write(world.state_dir, paused)
    except OSError as exc:
        raise QuitError(f"could not write {quit_state.QUIT_FILE}: {exc}") from exc


def stop_bosun(keep_awake_stays: bool) -> None:
    """`bosun stop` (--keep-awake: the Mac's keep-awake stays), holding the windows lock if this does; its
    last line is the reason if it fails."""
    argv = [str(console_live.BOSUN), "stop", *(["--keep-awake"] if keep_awake_stays else [])]
    try:
        result = subprocess.run(  # our own argv, no shell
            argv,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=STOP_TIMEOUT_SECONDS,
            check=False,
            pass_fds=(run_locked.LOCK_FD,) if lock_fd_open() else (),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QuitError(f"bosun stop did not finish: {exc}") from exc
    if result.returncode != 0:
        last = ((result.stderr or result.stdout).strip().splitlines() or ["no output"])[-1]
        raise QuitError(f"bosun stop failed: {last}")


def live_ports(world: World) -> Ports:
    return Ports(
        read_windows=read_windows,
        updating=lambda: update_state.running(world.state_dir),
        keep_awake_until=lambda: keep_awake_clock(world),
        lock=lambda: windows_lock(world),
        write_quit=lambda paused: write_quit(world, paused),
        stop_console=lambda: stop_console(world),
        stop_bosun=stop_bosun,
        sleep=time.sleep,
        clock=time.monotonic,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quit.py", allow_abbrev=False)
    parser.add_argument("--mode", choices=("plan", "finish", "now"))
    parser.add_argument("--wait-minutes", type=int, default=0)
    parser.add_argument("--pause-morning", action="store_true")
    parser.add_argument("--sleep", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--session")
    parser.add_argument("--console-label")
    parser.add_argument("--console-marker", type=Path)
    parser.add_argument("--lock-wait", type=float, default=0.0)
    parser.add_argument("--error", metavar="TEXT", help="only say that the quit failed, and why")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    options = Options(args.mode or "plan", args.wait_minutes, args.pause_morning, args.sleep, args.json)
    if args.error is not None:  # the wrapper's own refusal, in the shape its caller asked for
        return finish(options, failed_object(args.error), sys.stdout, sys.stderr)
    if None in (args.mode, args.state_dir, args.session, args.console_label, args.console_marker):
        parser.error("--mode, --state-dir, --session, --console-label and --console-marker are required")
    world = World(args.state_dir, args.session, args.console_label, args.console_marker, args.lock_wait)
    return run(options, live_ports(world), sys.stdout, sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
