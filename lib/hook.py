#!/usr/bin/env python3
"""Claude Code hooks for repo sessions (design: docs/design/events.md), and what Codex runs when a turn
ends (design: docs/design/harnesses.md).

Usage: hook.py user-prompt --window <window>    (the hook's JSON on stdin)
       hook.py codex-turn-end --window <window> --conversation <name> <json>

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
update was cut short) is run again by the child, in the foreground, through the install link.
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
import codex_state
import events
import ledger
import update_gate


USER_PROMPT_EVENT = "UserPromptSubmit"
SYNC = "BOSUN_HOOK_SYNC"
STAMP = "%Y-%m-%d %H:%M:%S"


def log(env: Mapping[str, str], message: str) -> None:
    """One line in the hook log (stdout when there is no log dir)."""
    line = f"{datetime.now():{STAMP}} [hook] {message}\n"
    log_dir = env.get("BOSUN_LOG_DIR")
    if not log_dir:
        sys.stdout.write(line)
        return
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    with (Path(log_dir) / f"hook-{datetime.now():%Y-%m-%d}.log").open("a") as handle:
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


def codex_turn_end(window: str, conversation: str, payload: Mapping[str, Any], env: Mapping[str, str]) -> str:
    """Remember which session <conversation> is, and with events on what was said in this turn."""
    session_id = str(payload.get("thread-id") or "")
    if not codex_state.is_terminal_session(session_id):
        return f"ignored: {session_id or 'no id'} is one of Codex's helpers, not {conversation}"
    prompts = [str(text) for text in payload.get("input-messages") or []]
    new = codex_state.new_prompts(conversation, session_id, prompts)
    said: list[str] = []
    with counted(conversation, session_id, str(payload.get("cwd") or ""), len(prompts)):
        if env.get("BOSUN_EVENTS_ENABLED") == "1":
            said = [record_prompt(window, {"prompt": text, "session_id": session_id}, env) for text in new]
    return "; ".join([f"{conversation} is {session_id}", *said])


@contextlib.contextmanager
def counted(conversation: str, session_id: str, workdir: str, prompts: int) -> Iterator[None]:
    """Remembers <conversation> as <session_id> with <prompts> seen once the block ends, whatever
    happened in it, but for a write the update gate refused: the run again on the installed version
    then takes the same prompts as new."""
    refused = False
    try:
        yield
    except update_gate.WriteRefused:
        refused = True
        raise
    finally:
        if not refused:
            codex_state.remember(conversation, session_id, workdir, prompts)


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
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    os.environ[SYNC] = "1"
    return False


def main(argv: list[str], env: Mapping[str, str]) -> int:
    parser = argparse.ArgumentParser(prog="bosun hook", description=__doc__)
    parser.add_argument("event", choices=("user-prompt", "codex-turn-end"))
    parser.add_argument("--window", required=True)
    parser.add_argument("--conversation", default="")
    parser.add_argument("payload", nargs="?")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.payload or sys.stdin.read() or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        log(env, f"ERROR: unreadable hook input in {args.window}: {exc}")
        return 0
    if detach(env):
        return 0
    try:
        if args.event == "codex-turn-end":
            log(env, codex_turn_end(args.window, args.conversation or args.window, payload, env))
        else:
            log(env, record_prompt(args.window, payload, env))
    except update_gate.StoreOlder as exc:
        log(env, f"ERROR: could not record a prompt typed in {args.window}: {exc}")
    except update_gate.WriteRefused as exc:
        log(env, f"not recorded in {args.window} on this version: {exc}; running it again")
        raise  # update_gate.guarded runs it again through the install link
    except Exception as exc:  # noqa: BLE001  (a hook never fails the prompt; the log has it)
        log(env, f"ERROR: could not record a prompt typed in {args.window}: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(update_gate.guarded("hook", lambda argv: main(argv, os.environ), sys.argv[1:]))
