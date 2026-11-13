"""`bosun quit`: stop bosun cleanly, with your say over busy agents, the morning start and the keep-awake
(design: docs/design/menu-bar.md, "Quit"). This file is the plan, the texts and the run over `Ports`;
lib/quit_live.py is the world the ports touch, and the command that libexec/bosun-quit runs.

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
3. look once more under the lock (the wait, or a start that held it, may have changed things): an update
   that began refuses the quit, and with --let-finish a window busy again lets the lock go and goes back to
   the wait, within the same deadline (exit 3 when it passes); all with nothing written. The windows the quit
   reports stopping are those of this look,
4. write quit.json (lib/quit_state.py), so the watchdog's next run already skips and never restarts what is
   being stopped,
5. take the console's launchd marker away and tell its job to stop,
6. `bosun stop`, with --keep-awake unless the Mac may sleep now. If a step after 4 fails, quit.json stays
   (bosun is meant to be stopped; `bosun status --json` then says the quit did not finish) and the quit fails
   with the reason and how to finish it.

It never touches the ledger, the knowledge base or the config. Exit codes: 0 done, 1 refused (an update
runs) or failed, 2 usage (libexec/bosun-quit), 3 the wait ran out. Each --json output is one object with
`contract` 1 and the plan's keys; a run's has `result` (stopped, busy, refused or failed) and `error`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, TextIO


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
CONSOLE_STOPPED, CONSOLE_NONE, CONSOLE_STILL = "stopped", "not running", "still running"


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
    lock_wait_seconds: int  # BOSUN_WINDOWS_LOCK_WAIT_SECONDS: how long the steps wait for a start's lock


@dataclass(frozen=True)
class Ports:
    read_windows: Callable[[], list[Window]]
    updating: Callable[[], bool]
    keep_awake_until: Callable[[], str | None]  # HH:MM
    lock: Callable[[], AbstractContextManager[None]]  # the windows lock; QuitError if it stays taken
    write_quit: Callable[[bool], None]  # morning_paused
    stop_console: Callable[[], tuple[str, int | None]]  # CONSOLE_STOPPED, CONSOLE_NONE or CONSOLE_STILL, and
    # the port of one still running
    stop_bosun: Callable[[bool], None]  # keep_awake; raises QuitError with the reason
    sleep: Callable[[float], None]
    clock: Callable[[], float]


# ---- the plan --------------------------------------------------------------------------------------


def windows_from_sessions(listing: Sequence[dict[str, Any]]) -> list[Window]:
    """The open windows of what `bosun sessions --json` lists, each with its agent's status."""
    return [Window(w["name"], (w.get("agent") or {}).get("status")) for w in listing if w.get("window")]


def quit_plan(
    windows: Sequence[Window],
    keep_awake_until: str | None,
    updating: bool,
    wait_minutes: int,
    lock_wait_seconds: int,
) -> dict[str, Any]:
    """What a quit would meet: the open windows, which are busy and which wait for you, when the Mac's
    keep-awake ends, and why a quit is refused (an update runs); and how long a quit may wait, which the
    menu bar app times it by: <wait_minutes> of --let-finish (BOSUN_QUIT_WAIT_MINUTES unless asked
    otherwise) and <lock_wait_seconds> for the windows lock (BOSUN_WINDOWS_LOCK_WAIT_SECONDS)."""
    return {
        "contract": CONTRACT,
        "windows": [w.name for w in windows],
        "busy": [w.name for w in windows if w.status == BUSY],
        "waiting": [w.name for w in windows if w.status == WAITING],
        "keep_awake_until": keep_awake_until,
        "refused": UPDATING if updating else None,
        "wait_minutes": wait_minutes,
        "lock_wait_seconds": lock_wait_seconds,
    }


def failed_object(error: str, options: Options) -> dict[str, Any]:
    """The object of a quit that could not even look: the plan's keys, empty, and why."""
    plan = quit_plan([], None, False, options.wait_minutes, options.lock_wait_seconds)
    return dict(plan, result="failed", error=error)


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


def look(ports: Ports, options: Options) -> tuple[list[Window] | None, dict[str, Any]]:
    """The windows as they are now and the plan from them. Windows that cannot be read stop every mode but
    --stop-now, which is your say (the plan then lists none, and says it could not be read)."""
    try:
        windows: list[Window] | None = ports.read_windows()
    except QuitError:
        if options.mode == "now":
            windows = None
        else:
            raise
    plan = quit_plan(
        windows or [],
        ports.keep_awake_until(),
        ports.updating(),
        options.wait_minutes,
        options.lock_wait_seconds,
    )
    if windows is None:
        plan["note"] = "the windows could not be read, so none is listed"
    return windows, plan


def wait_until_nothing_is_busy(
    options: Options, ports: Ports, deadline: float, err: TextIO
) -> dict[str, Any]:
    """The plan as it is when no window is busy, or when <deadline> passed (its `busy` is not empty), or when
    an update began (`refused`). Says on <err>, when it begins to wait, who it waits for."""
    said = False
    while True:
        _windows, plan = look(ports, options)
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
        result["console"], result["console_port"] = ports.stop_console()
        ports.stop_bosun(not options.sleep)
    except QuitError as exc:
        raise QuitError(f"{exc}; {RECORD_STAYS}") from exc


def locked_steps(options: Options, ports: Ports) -> dict[str, Any]:
    """Under the windows lock: look once more (what the wait saw may be minutes old, and a start that held
    the lock may have changed the windows), then the steps, in order. An update that began refuses the quit,
    and with --let-finish a window busy again leaves it to wait on (`busy`), both with nothing written. A step
    that fails ends the quit there, with its reason; quit.json, once written, stays (bosun is meant to be
    stopped). The windows listed are those of this look."""
    _windows, plan = look(ports, options)
    if plan["refused"]:
        return dict(plan, result="refused", error=None)
    if plan["busy"] and options.mode == "finish":
        return dict(plan, result="busy", error=None)
    result = dict(plan, result="stopped", morning_paused=options.pause_morning, error=None)
    try:
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


def stop(options: Options, ports: Ports) -> dict[str, Any]:
    """The quit's last phase, holding the windows lock; a lock that stays taken, or cannot be opened, or a
    reading that fails under it, fails the quit with nothing written."""
    try:
        with ports.lock():
            return locked_steps(options, ports)
    except QuitError as exc:
        return failed_object(str(exc), options)


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
        port = f" on port {result['console_port']}" if result.get("console_port") else ""
        lines.append(
            f"A console is still running{port}: the job's may still be exiting; if it stays, or it is one "
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


def first_plan(options: Options, ports: Ports, deadline: float, err: TextIO) -> dict[str, Any]:
    """The plan the quit goes on from: with --let-finish after the wait for the busy windows."""
    if options.mode == "finish":
        return wait_until_nothing_is_busy(options, ports, deadline, err)
    return look(ports, options)[1]


def quit_it(options: Options, ports: Ports, err: TextIO) -> dict[str, Any]:
    """The result of a quit (not of a plan): refused, busy (the wait ran out), failed or stopped. Busy again
    under the lock, with time left, goes back to waiting, as the same deadline's."""
    deadline = ports.clock() + options.wait_minutes * 60
    while True:
        plan = first_plan(options, ports, deadline, err)
        if plan["refused"]:
            return dict(plan, result="refused", error=None)
        if plan["busy"] and options.mode == "finish":
            return dict(plan, result="busy", error=None)
        result = stop(options, ports)
        if result["result"] != "busy" or ports.clock() >= deadline:
            return result


def run(options: Options, ports: Ports, out: TextIO, err: TextIO) -> int:
    """The quit as <options> say. Returns the exit status."""
    try:
        if options.mode == "plan":
            _windows, plan = look(ports, options)
            print(json.dumps(plan) if options.json else plan_text(plan), file=out)
            return 0
        result = quit_it(options, ports, err)
    except QuitError as exc:
        result = failed_object(str(exc), options)
    return finish(options, result, out, err)
