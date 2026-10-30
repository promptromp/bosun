#!/usr/bin/env python3
"""Claude Code hooks for repo sessions (design: docs/design/events.md).

Usage: hook.py user-prompt --window <window>    (the hook's JSON on stdin)

Run it as `bosun hook user-prompt --window <window>`: `bosun launch` installs it as the
UserPromptSubmit hook of a repo window's Claude when BOSUN_EVENTS_ENABLED=1. It records each
prompt I type in the window (also from my phone) as a `user-said` ledger entry on the item(s) the
window holds (lib/activity.py), so the orchestrator never asks me again what I already decided
there. Prompts bosun pasted (`bosun send`), slash commands and messages Claude Code
injects (peer sessions, background tasks) are not mine and are skipped.

It never blocks or changes the prompt: it reads the JSON, forks, and the parent exits 0 at once;
the child does the work and logs what it did (errors as ERROR lines, which the watchdog reports)
to $BOSUN_LOG_DIR/hook-YYYY-MM-DD.log. BOSUN_HOOK_SYNC=1 works in the foreground (tests).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import activity
import events
import ledger


USER_PROMPT_EVENT = "UserPromptSubmit"
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
    ledger.configure_from_environment()
    ledger.STORE.caller_window = window
    entry = ledger.add_user_said(window, text, str(payload.get("session_id") or ""))
    on = ", ".join(entry["items"]) or "no item (the window holds none)"
    return f"user-said in {window} recorded on {on}"


def detach(env: Mapping[str, str]) -> bool:
    """Fork; True in the parent, which returns at once. The child leaves the hook's process group
    and its pipes, so Claude Code neither waits for it nor kills it."""
    if env.get("BOSUN_HOOK_SYNC") == "1":
        return False
    if os.fork() > 0:
        return True
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    return False


def main(argv: list[str], env: Mapping[str, str]) -> int:
    parser = argparse.ArgumentParser(prog="bosun hook", description=__doc__)
    parser.add_argument("event", choices=("user-prompt",))
    parser.add_argument("--window", required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        log(env, f"ERROR: unreadable hook input in {args.window}: {exc}")
        return 0
    if detach(env):
        return 0
    try:
        log(env, record_prompt(args.window, payload, env))
    except Exception as exc:  # noqa: BLE001  (a hook never fails the prompt; the log has it)
        log(env, f"ERROR: could not record a prompt typed in {args.window}: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
