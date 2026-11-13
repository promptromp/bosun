"""`bosun quit`: stop bosun cleanly, with your say over busy agents, the morning start and the keep-awake
(design: docs/design/menu-bar.md, "Quit"). Run by libexec/bosun-quit, which parses the command line and the
settings, and passes them as the options below.

The plan is pure over the windows (`quit_plan`); the run goes through `Ports`, the steps that touch the
world, which the tests replace. The order of a quit is fixed:

1. wait, with --let-finish, until no window is busy (a window waiting at a prompt is never waited for: it
   needs you and is stopped with the rest; a window at `shell` has only background work and counts as done).
   The wait polls every POLL_SECONDS up to the minutes, checks once with 0 and changes nothing when it runs
   out (exit 3, the windows still busy printed, so the caller can ask again). Quit never sends keys to a
   window. --stop-now does not wait. The wait holds no lock.
2. take the `windows` lock that `bosun start` and `bosun refresh` hold (lib/run_locked.py), waiting for one
   that sets up the windows; the rest holds it, and so does what it starts (`bosun stop`), so a start cannot
   begin in between and undo the quit,
3. write quit.json (lib/quit_state.py), so the watchdog's next run already skips and never restarts what is
   being stopped,
4. take the console's launchd marker away and tell its job to stop,
5. `bosun stop`, with --keep-awake unless the Mac may sleep now. If a step after 3 fails, quit.json stays
   (bosun is meant to be stopped; `bosun status --json` then says the quit did not finish) and the quit fails
   with the reason and how to finish it.

It never touches the ledger, the knowledge base or the config. Exit codes: 0 done, 1 refused (an update
runs) or failed, 2 usage (libexec/bosun-quit), 3 the wait ran out. Each --json output is one object with
`contract` 1 and the plan's keys; a run's has `result` (stopped, busy, refused or failed) and `error`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO


sys.path.insert(0, str(Path(__file__).resolve().parent))
import console_live
import console_token
import keep_awake
import quit_state
import run_locked
import update_state


CONTRACT = 1
POLL_SECONDS = 5
WAITING, BUSY = "waiting", "busy"
UPDATING = "an update is running: quit when it has finished (it brings the windows along)"
LOCK_BUSY = (
    "another bosun start or refresh is setting up the windows, and did not finish in time: "
    "nothing was changed, try the quit again"
)
RECORD_STAYS = (
    "the quit record stays, so bosun stays stopped: run `bosun quit --stop-now` again to finish, "
    "or `bosun start` to start bosun again"
)
CONSOLE_POLL_SECONDS = 0.5
CONSOLE_WAIT_SECONDS = 5
CONSOLE_STOPPED, CONSOLE_NONE, CONSOLE_STILL = "stopped", "not running", "still running"
LAUNCHCTL_TIMEOUT_SECONDS = 20
STOP_TIMEOUT_SECONDS = 120


class QuitError(Exception):
    """Something that stops the quit, in one line a person can act on."""


@dataclass(frozen=True)
class Window:
    name: str
    status: str | None  # the agent's status (idle, busy, waiting, shell); None with no agent running


@dataclass(frozen=True)
class Options:
    mode: str  # plan, finish (wait for the busy windows, then quit) or now
    wait_minutes: int
    pause_morning: bool
    sleep: bool  # the Mac may sleep now: bosun stop releases the keep-awake too
    json: bool


@dataclass(frozen=True)
class Ports:
    read_windows: Callable[[], list[Window]]
    updating: Callable[[], bool]
    keep_awake_until: Callable[[], str | None]  # HH:MM
    lock: Callable[[], AbstractContextManager[None]]  # the windows lock; QuitError if it stays taken
    write_quit: Callable[[bool], None]  # morning_paused
    stop_console: Callable[[], str]  # CONSOLE_STOPPED, CONSOLE_NONE or CONSOLE_STILL (+ " on port N")
    stop_bosun: Callable[[bool], None]  # keep_awake; raises QuitError with the reason
    sleep: Callable[[float], None]
    clock: Callable[[], float]


# ---- the plan --------------------------------------------------------------------------------------


def windows_from_sessions(listing: Sequence[dict[str, Any]]) -> list[Window]:
    """The open windows of what `bosun sessions --json` lists, each with its agent's status."""
    return [Window(w["name"], (w.get("agent") or {}).get("status")) for w in listing if w.get("window")]


def quit_plan(windows: Sequence[Window], keep_awake_until: str | None, updating: bool) -> dict[str, Any]:
    """What a quit would meet: the open windows, which are busy and which wait for you, when the Mac's
    keep-awake ends, and why a quit is refused (an update runs)."""
    return {
        "contract": CONTRACT,
        "windows": [w.name for w in windows],
        "busy": [w.name for w in windows if w.status == BUSY],
        "waiting": [w.name for w in windows if w.status == WAITING],
        "keep_awake_until": keep_awake_until,
        "refused": UPDATING if updating else None,
    }


def failed_object(error: str) -> dict[str, Any]:
    """The object of a quit that could not even look: the plan's keys, empty, and why."""
    return dict(quit_plan([], None, False), result="failed", error=error)


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def plan_text(plan: dict[str, Any]) -> str:
    names = ", ".join(plan["windows"])
    lines = [f"{plural(len(plan['windows']), 'window')} open" + (f": {names}" if names else "")]
    if plan["busy"]:
        lines.append("busy: " + ", ".join(plan["busy"]))
    if plan["waiting"]:
        lines.append("waiting for you: " + ", ".join(plan["waiting"]))
    if plan["keep_awake_until"]:
        lines.append(f"the Mac is kept awake until {plan['keep_awake_until']}")
    if plan["refused"]:
        lines.append(f"a quit is refused: {plan['refused']}")
    return "\n".join(lines)


# ---- the run ---------------------------------------------------------------------------------------


def look(ports: Ports, mode: str) -> tuple[list[Window] | None, dict[str, Any]]:
    """The windows as they are now and the plan from them. Windows that cannot be read stop every mode but
    --stop-now, which is your say (the plan then lists none, and says it could not be read)."""
    try:
        windows: list[Window] | None = ports.read_windows()
    except QuitError:
        if mode == "now":
            windows = None
        else:
            raise
    plan = quit_plan(windows or [], ports.keep_awake_until(), ports.updating())
    if windows is None:
        plan["note"] = "the windows could not be read, so none is listed"
    return windows, plan


def wait_until_nothing_is_busy(options: Options, ports: Ports, err: TextIO) -> dict[str, Any]:
    """The plan as it is when no window is busy, or when the minutes ran out (its `busy` is not empty), or
    when an update began (`refused`). Says on <err> once, when it begins to wait, who it waits for."""
    deadline = ports.clock() + options.wait_minutes * 60
    said = False
    while True:
        _windows, plan = look(ports, options.mode)
        if plan["refused"] or not plan["busy"] or ports.clock() >= deadline:
            return plan
        if not said:
            said = True
            who = ", ".join(plan["busy"])
            minutes = plural(options.wait_minutes, "minute")
            print(
                f"Waiting for {plural(len(plan['busy']), 'busy window')} ({who}), up to {minutes}", file=err
            )
        ports.sleep(min(POLL_SECONDS, deadline - ports.clock()))


def stop_steps(options: Options, ports: Ports, result: dict[str, Any]) -> None:
    """What follows the quit record: the console, then bosun stop. A failure says how to finish."""
    try:
        result["console"] = ports.stop_console()
        ports.stop_bosun(not options.sleep)
    except QuitError as exc:
        raise QuitError(f"{exc}; {RECORD_STAYS}") from exc


def stop(options: Options, ports: Ports, plan: dict[str, Any]) -> dict[str, Any]:
    """The steps, in order, under the windows lock. A step that fails ends the quit there, with its reason;
    quit.json, once written, stays (bosun is meant to be stopped)."""
    result = dict(plan, result="stopped", morning_paused=options.pause_morning, error=None)
    try:
        with ports.lock():
            ports.write_quit(options.pause_morning)
            stop_steps(options, ports, result)
    except QuitError as exc:
        result.update(result="failed", error=str(exc))
        return result
    until = plan["keep_awake_until"]
    if options.sleep:
        result["keep_awake"] = "released" if until else None
    else:
        result["keep_awake"] = f"kept until {until}" if until else None
    return result


def stopped_text(result: dict[str, Any]) -> str:
    lines = ["bosun is stopped."]
    if result["windows"]:
        lines.append("Stopped the windows: " + ", ".join(result["windows"]))
    if result["waiting"]:
        lines.append("They were waiting for you: " + ", ".join(result["waiting"]))
    if result["morning_paused"]:
        lines.append("The morning start is paused until you run: bosun start")
    else:
        lines.append("The morning start stays on (the next one starts bosun again, or run: bosun start)")
    if result["keep_awake"]:
        lines.append(f"The Mac: keep-awake {result['keep_awake']}")
    if result["console"] == CONSOLE_STOPPED:
        lines.append("The console's job is stopped; it comes back with: bosun start")
    elif result["console"].startswith(CONSOLE_STILL):
        lines.append(
            f"A console is {result['console']}: the job's may still be exiting; if it stays, or it is one "
            "you started by hand, stop it where you started it"
        )
    if result.get("note"):
        lines.append(result["note"])
    return "\n".join(lines)


def finish(options: Options, result: dict[str, Any], out: TextIO, err: TextIO) -> int:
    """Print <result> (an object, or text) and say how the run went: 0 stopped, 3 busy, 1 otherwise."""
    status = {"stopped": 0, "busy": 3}.get(result.get("result", ""), 1)
    if options.json:
        print(json.dumps(result), file=out)
    elif result["result"] == "stopped":
        print(stopped_text(result), file=out)
    elif result["result"] == "busy":
        waited = plural(options.wait_minutes, "minute")
        print(f"Still busy after {waited}: " + ", ".join(result["busy"]), file=out)
        print("Nothing was stopped.", file=out)
    else:
        print(result["refused"] or result["error"], file=err)
    return status


def run(options: Options, ports: Ports, out: TextIO, err: TextIO) -> int:
    """The quit as <options> say. Returns the exit status."""
    try:
        if options.mode == "plan":
            _windows, plan = look(ports, options.mode)
            print(json.dumps(plan) if options.json else plan_text(plan), file=out)
            return 0
        if options.mode == "finish":
            plan = wait_until_nothing_is_busy(options, ports, err)
        else:
            plan = look(ports, options.mode)[1]
    except QuitError as exc:
        return finish(options, failed_object(str(exc)), out, err)
    if plan["refused"]:
        return finish(options, dict(plan, result="refused"), out, err)
    if plan["busy"] and options.mode == "finish":
        return finish(options, dict(plan, result="busy"), out, err)
    return finish(options, stop(options, ports, plan), out, err)


# ---- the world -------------------------------------------------------------------------------------


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
    fd = run_locked.acquire(path, world.lock_wait)
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


def stop_console(world: World) -> str:
    """The console's job stopped, and said so. The marker goes first: it is the job's KeepAlive condition,
    so launchd does not start it again. A console that still runs afterwards is said, with its port: the
    job's may be slow to exit, or it was started by hand, which is not hunted for."""
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
        port = console_token.served_port(world.state_dir)
        return f"{CONSOLE_STILL} on port {port}" if port else CONSOLE_STILL
    return CONSOLE_STOPPED if was_running else CONSOLE_NONE


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
