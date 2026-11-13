"""`bosun quit`: stop bosun cleanly, with your say over busy agents, the morning start and the keep-awake
(design: docs/design/menu-bar.md, "Quit"). Run by libexec/bosun-quit, which parses the command line and the
settings, and passes them as the options below.

The plan is pure over the windows (`quit_plan`); the run goes through `Ports`, the steps that touch the
world, which the tests replace. The order of a quit is fixed:

1. wait, with --let-finish, until no window is busy (a window waiting at a prompt is never waited for: it
   needs you and is stopped with the rest; a window at `shell` has only background work and counts as done).
   The wait polls every POLL_SECONDS up to the minutes, checks once with 0 and changes nothing when it runs
   out (exit 3, the windows still busy printed, so the caller can ask again). Quit never sends keys to a
   window. --stop-now does not wait.
2. write quit.json first (lib/quit_state.py), so the watchdog's next run already skips and never restarts
   what is being stopped,
3. take the console's launchd marker away and tell its job to stop,
4. `bosun stop`, with --keep-awake unless the Mac may sleep now. If it fails quit.json stays (bosun is meant
   to be stopped) and the quit fails with the reason.

It never touches the ledger, the knowledge base or the config. Exit codes: 0 done, 1 refused (an update
runs) or failed, 2 usage (libexec/bosun-quit), 3 the wait ran out. Each --json output is one object with
`contract` 1; a run's has `result` (stopped, busy, refused or failed).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO


sys.path.insert(0, str(Path(__file__).resolve().parent))
import console_live
import console_token
import menu_status_live
import quit_state
import update_state


CONTRACT = 1
POLL_SECONDS = 5
WAITING, BUSY = "waiting", "busy"
UPDATING = "an update is running: quit when it has finished (it brings the windows along)"
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
    write_quit: Callable[[bool], None]  # morning_paused
    stop_console: Callable[[], str]  # CONSOLE_STOPPED, CONSOLE_NONE or CONSOLE_STILL
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


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def plan_text(plan: dict[str, Any]) -> str:
    lines = [
        f"{plural(len(plan['windows']), 'window')} open"
        + (": " + ", ".join(plan["windows"]) if plan["windows"] else "")
    ]
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


def wait_until_nothing_is_busy(options: Options, ports: Ports) -> dict[str, Any]:
    """The plan as it is when no window is busy, or when the minutes ran out (its `busy` is not empty), or
    when an update began (`refused`)."""
    deadline = ports.clock() + options.wait_minutes * 60
    while True:
        _windows, plan = look(ports, options.mode)
        if plan["refused"] or not plan["busy"] or ports.clock() >= deadline:
            return plan
        ports.sleep(min(POLL_SECONDS, deadline - ports.clock()))


def stop(options: Options, ports: Ports, plan: dict[str, Any]) -> dict[str, Any]:
    """The steps, in order. A step that fails ends the quit there, with its reason; quit.json, written first,
    stays (bosun is meant to be stopped)."""
    result = dict(plan, result="stopped", morning_paused=options.pause_morning, error=None)
    try:
        ports.write_quit(options.pause_morning)
        result["console"] = ports.stop_console()
        ports.stop_bosun(not options.sleep)
    except QuitError as exc:
        result.update(result="failed", error=str(exc))
        return result
    if options.sleep:
        result["keep_awake"] = "released" if plan["keep_awake_until"] else None
    else:
        until = plan["keep_awake_until"]
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
    elif result["console"] == CONSOLE_STILL:
        lines.append("A console is still running (not the job's: stop it where you started it)")
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
        print(
            f"Still busy after {plural(options.wait_minutes, 'minute')}: " + ", ".join(result["busy"]),
            file=out,
        )
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
            plan = wait_until_nothing_is_busy(options, ports)
        else:
            plan = look(ports, options.mode)[1]
    except QuitError as exc:
        return finish(
            options, {"contract": CONTRACT, "result": "failed", "refused": None, "error": str(exc)}, out, err
        )
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


def read_windows() -> list[Window]:
    try:
        return windows_from_sessions(
            menu_status_live.read_list(console_live.run_command, "sessions", "--json")
        )
    except console_live.LiveError as exc:
        raise QuitError(f"the windows could not be read: {exc}") from exc


def keep_awake_clock(world: World) -> str | None:
    until = menu_status_live.keep_awake_until(world.state_dir, world.session)
    return f"{until:%H:%M}" if until is not None else None


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
    so launchd does not start it again. A console started by hand is not the job and is not hunted for: if
    one still runs afterwards, that is said."""
    world.console_marker.unlink(missing_ok=True)
    was_running = console_token.running(world.state_dir)
    target = f"gui/{os.getuid()}/{world.console_label}"
    if launchctl("print", target):  # a missing job (BOSUN_CONSOLE_ALWAYS=0) is no failure
        launchctl("kill", "TERM", target)
        deadline = time.monotonic() + CONSOLE_WAIT_SECONDS
        while console_token.running(world.state_dir) and time.monotonic() < deadline:
            time.sleep(CONSOLE_POLL_SECONDS)
    if console_token.running(world.state_dir):
        return CONSOLE_STILL
    return CONSOLE_STOPPED if was_running else CONSOLE_NONE


def write_quit(world: World, paused: bool) -> None:
    try:
        quit_state.write(world.state_dir, paused)
    except OSError as exc:
        raise QuitError(f"could not write {quit_state.QUIT_FILE}: {exc}") from exc


def stop_bosun(keep_awake: bool) -> None:
    """`bosun stop` (--keep-awake: the Mac's keep-awake stays); its last line is the reason if it fails."""
    argv = [str(console_live.BOSUN), "stop", *(["--keep-awake"] if keep_awake else [])]
    try:
        result = subprocess.run(  # our own argv, no shell
            argv,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=STOP_TIMEOUT_SECONDS,
            check=False,
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
        write_quit=lambda paused: write_quit(world, paused),
        stop_console=lambda: stop_console(world),
        stop_bosun=stop_bosun,
        sleep=time.sleep,
        clock=time.monotonic,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="quit.py", allow_abbrev=False)
    parser.add_argument("--mode", choices=("plan", "finish", "now"), required=True)
    parser.add_argument("--wait-minutes", type=int, default=0)
    parser.add_argument("--pause-morning", action="store_true")
    parser.add_argument("--sleep", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--console-label", required=True)
    parser.add_argument("--console-marker", type=Path, required=True)
    args = parser.parse_args(argv)
    options = Options(args.mode, args.wait_minutes, args.pause_morning, args.sleep, args.json)
    world = World(args.state_dir, args.session, args.console_label, args.console_marker)
    return run(options, live_ports(world), sys.stdout, sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
