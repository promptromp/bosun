#!/usr/bin/env python3
"""Claude Code hooks for repo sessions (design: docs/design/events.md), and what an agent runs when a
turn ends (design: docs/design/harnesses.md).

Usage: hook.py user-prompt --window <window>    (the hook's JSON on stdin)
       hook.py turn-end --harness <agent> --window <window> --conversation <name> <json>
       hook.py codex-turn-end --window <window> --conversation <name> <json>
       hook.py agent --harness <agent> --event <event>    (the hook's JSON on stdin)

`codex-turn-end` is `turn-end --harness codex`, which a Codex window started before the generic name
still runs: its launch command outlives the bosun that wrote it. An agent whose profile has no
`turn_end` reports no turns, and is refused.

`agent` is an agent's own hooks (the plugin bosun installs into Antigravity): the JSON comes on stdin and
the agent's answer goes to stdout, in the foreground, since the agent waits for it. It never fails: the
agent takes a failing hook for a refusal, so an error is an ERROR line in the hook log, nothing on stdout
and exit 0.

Run it as `bosun hook user-prompt --window <window>`: `bosun launch` installs it as the
UserPromptSubmit hook of a repo window's Claude when BOSUN_EVENTS_ENABLED=1. It records each
prompt I type in the window (also from my phone) as a `user-said` ledger entry on the item(s) the
window holds (lib/activity.py), so the orchestrator never asks me again what I already decided
there. Prompts bosun pasted (`bosun send`), slash commands and messages Claude Code
injects (peer sessions, background tasks) are not mine and are skipped.

It never blocks or changes the prompt: it reads the JSON, forks, and the parent exits 0 at once;
the child does the work and logs what it did (errors as ERROR lines, which the watchdog reports)
to $BOSUN_LOG_DIR/hook-YYYY-MM-DD.log. BOSUN_HOOK_SYNC=1 works in the foreground (tests). A write
the update gate refuses (lib/update_gate.py: bosun was updated since this hook's code started, or an
update was cut short) is run again by the child, in the foreground, through the install link; one
that still cannot be made is an ERROR line in the hook log, after the gate's own line saying why,
and so is one the gate never runs again (an older store, an update record that cannot be read).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import activity
import events
import ledger
import update_gate
from agent_files import AgentHooks, TurnEnd, TurnReport
from agent_state import AGENTS


USER_PROMPT_EVENT = "UserPromptSubmit"
SYNC = "BOSUN_HOOK_SYNC"
STAMP = "%Y-%m-%d %H:%M:%S"


def log_file(env: Mapping[str, str]) -> Path | None:
    """Today's hook log (its folder made), or None when there is no log dir."""
    log_dir = env.get("BOSUN_LOG_DIR")
    if not log_dir:
        return None
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    return Path(log_dir) / f"hook-{datetime.now():%Y-%m-%d}.log"


def log(env: Mapping[str, str], message: str) -> None:
    """One line in the hook log (stdout when there is no log dir)."""
    line = f"{datetime.now():{STAMP}} [hook] {message}\n"
    path = log_file(env)
    if path is None:
        sys.stdout.write(line)
        return
    with path.open("a") as handle:
        handle.write(line)


def record_prompt(window: str, payload: Mapping[str, Any], env: Mapping[str, str]) -> str:
    """Record the prompt in <payload> as user-said in <window>, unless it is not mine."""
    if payload.get("hook_event_name", USER_PROMPT_EVENT) != USER_PROMPT_EVENT:
        return f"ignored: a {payload.get('hook_event_name')} hook"
    text = str(payload.get("prompt") or "")
    state_dir = Path(env["BOSUN_STATE_DIR"])
    pasted = events.pasted_hashes(state_dir, window, datetime.now(UTC))
    if not activity.is_mine(text, pasted, env.get("BOSUN_DRAIN_PROMPT", "")):
        return f"skipped in {window}: not typed by me ({activity.excerpt(text, 60)!r})"
    ledger.configure_from_environment(command="hook")
    ledger.STORE.caller_window = window
    entry = ledger.add_user_said(window, text, str(payload.get("session_id") or ""))
    on = ", ".join(entry["items"]) or "no item (the window holds none)"
    return f"user-said in {window} recorded on {on}"


def turn_end(
    turn: TurnEnd, window: str, conversation: str, payload: Mapping[str, Any], env: Mapping[str, str]
) -> str:
    """Remember which session <conversation> is, and with events on what was said in this turn."""
    report = turn.report(conversation, payload)
    if isinstance(report, str):
        return report
    said: list[str] = []
    with counted(turn, conversation, report):
        if env.get("BOSUN_EVENTS_ENABLED") == "1":
            said = [
                record_prompt(window, {"prompt": text, "session_id": report.session_id}, env)
                for text in report.new
            ]
    return "; ".join([f"{conversation} is {report.session_id}", *said])


@contextlib.contextmanager
def counted(turn: TurnEnd, conversation: str, report: TurnReport) -> Iterator[None]:
    """Remembers <conversation> as the report's session with its prompts seen once the block ends,
    whatever happened in it, but for a write the update gate refused: the run again on the installed
    version then takes the same prompts as new."""
    refused = False
    try:
        yield
    except update_gate.WriteRefused:
        refused = True
        raise
    finally:
        if not refused:
            turn.remember(conversation, report.session_id, report.workdir, report.prompts)


def detach(env: Mapping[str, str]) -> bool:
    """Fork; True in the parent, which returns at once. The child leaves the hook's process group
    and its pipes, so Claude Code neither waits for it nor kills it; what it runs again
    (update_gate.guarded) stays in the foreground, as it is in the background already."""
    if env.get(SYNC) == "1":
        return False
    if os.fork() > 0:
        return True
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    path = log_file(env)
    said = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644) if path else devnull
    for fd, to in ((0, devnull), (1, devnull), (2, said)):  # what it says on stderr goes to the log
        os.dup2(to, fd)
    os.environ[SYNC] = "1"
    return False


def legacy_events() -> dict[str, str]:
    """The agent each older event name stands for (`turn-end --harness <agent>`), from the profiles."""
    return {
        agent.PROFILE.turn_end.legacy_event: name
        for name, agent in AGENTS.items()
        if agent.PROFILE.turn_end and agent.PROFILE.turn_end.legacy_event
    }


def known_agent(parser: argparse.ArgumentParser, harness: str) -> Any:
    """The module of the agent <harness>, or a usage error naming the ones bosun knows."""
    if harness not in AGENTS:
        parser.error(f"no agent '{harness}': bosun knows {', '.join(AGENTS)}")
    return AGENTS[harness]


def turn_end_of(parser: argparse.ArgumentParser, args: argparse.Namespace) -> TurnEnd | None:
    """The turn-end handling of the agent <args> names (an older event name stands for its agent), or None
    for a hook that is not about a turn. A usage error, before anything detaches, for an agent with none."""
    if args.event == "user-prompt":
        return None
    harness = legacy_events().get(args.event) or args.harness
    if not harness:
        parser.error("turn-end needs --harness <agent>")
    turn: TurnEnd | None = known_agent(parser, harness).PROFILE.turn_end
    if turn is None:
        parser.error(f"{harness} reports no turns")
    return turn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun hook", description=__doc__)
    parser.add_argument("event", choices=("user-prompt", "turn-end", *legacy_events()))
    parser.add_argument("--harness", default="")
    parser.add_argument("--window", required=True)
    parser.add_argument("--conversation", default="")
    parser.add_argument("payload", nargs="?")
    return parser


def described(argv: list[str]) -> str:
    """The event, agent, window and conversation <argv> names, for a log line: never its payload, which
    holds what was said in the window, wherever the arguments put it."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit:  # not a call main would have run
        return argv[0] if argv else ""
    options = (("--harness", args.harness), ("--window", args.window), ("--conversation", args.conversation))
    return " ".join([args.event, *(f"{flag} {value}" for flag, value in options if value)])


def agent_hooks_of(parser: argparse.ArgumentParser, args: argparse.Namespace) -> AgentHooks:
    """The hooks of the agent <args> names, or a usage error: that is a wrong command line, which no
    agent's run of its hook can cause."""
    hooks: AgentHooks | None = known_agent(parser, args.harness).PROFILE.hooks
    if hooks is None:
        parser.error(f"{args.harness} has no hooks of its own")
    if args.event not in hooks.events:
        parser.error(f"{args.harness} has no hook {args.event}")
    return hooks


def agent_hook(argv: list[str], env: Mapping[str, str]) -> int:
    """`agent --harness <agent> --event <event>`: print the agent's answer to the JSON on stdin. Once the
    agent and event are known nothing fails it: the agent takes a failing hook for a refusal of what it asked,
    so an error is an ERROR line in the hook log (never the payload, which holds what was said), no
    answer and exit 0."""
    parser = argparse.ArgumentParser(prog="bosun hook agent")
    parser.add_argument("--harness", required=True)
    parser.add_argument("--event", required=True)
    args = parser.parse_args(argv)
    hooks = agent_hooks_of(parser, args)
    try:
        answer = hooks.answer(args.event, json.loads(sys.stdin.read()), env)
        sys.stdout.write(answer)
        sys.stdout.flush()
    except Exception as exc:  # noqa: BLE001  (a hook that fails is a refusal to the agent; the log has it)
        with contextlib.suppress(Exception):
            line = f"ERROR: {args.harness} hook {args.event} failed: {exc!r}"
            if env.get("BOSUN_LOG_DIR"):
                log(env, line)
            else:  # stdout is the agent's answer
                sys.stderr.write(f"{line}\n")
    return 0


def main(argv: list[str], env: Mapping[str, str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    turn = turn_end_of(parser, args)
    try:
        payload = json.loads(args.payload or sys.stdin.read() or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        log(env, f"ERROR: unreadable hook input in {args.window}: {exc}")
        return 0
    if detach(env):
        return 0
    try:
        if turn is not None:
            log(env, turn_end(turn, args.window, args.conversation or args.window, payload, env))
        else:
            log(env, record_prompt(args.window, payload, env))
    except update_gate.WriteRefused as exc:
        if not exc.run_again:  # an older store, an update record that cannot be read
            log(env, f"ERROR: could not record a prompt typed in {args.window}: {exc}")
            return 0
        log(env, f"not recorded in {args.window} on this version: {exc}; running it again")
        raise  # update_gate.guarded runs it again through the install link
    except Exception as exc:  # noqa: BLE001  (a hook never fails the prompt; the log has it)
        log(env, f"ERROR: could not record a prompt typed in {args.window}: {exc!r}")
    return 0


def run(argv: list[str], env: Mapping[str, str]) -> int:
    """main, under update_gate.guarded: a write the gate refused is run again on the installed
    version, and one that still could not be made is an ERROR line in the hook log, for the
    watchdog (the gate's own line, on stderr, says why)."""
    if argv[:1] == ["agent"]:  # writes no store, and the agent waits for its stdin's answer
        return agent_hook(argv[1:], env)
    code = update_gate.guarded("hook", lambda args: main(args, env), argv)
    if code != 0:
        log(env, f"ERROR: bosun hook {described(argv)} recorded nothing: the update gate refused its write")
    return code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], os.environ))
