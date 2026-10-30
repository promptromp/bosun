#!/usr/bin/env python3
"""Claude Code's own signals, recorded by hooks: what a window is waiting on, what is still running
when a turn ends, and why a turn failed (design: docs/design/signals.md).

Usage: signals.py record --window <window>    (a hook's JSON on stdin; never fails)
       signals.py show --window <window> [--last N]

With BOSUN_HOOK_SIGNALS=1, `bosun launch` installs `bosun hook signal --window <w>` as an
async hook on Notification, PermissionRequest, Stop, StopFailure, SessionStart and SessionEnd. Each
event becomes one compact line in $BOSUN_STATE_DIR/signals/<window>.jsonl: the time, the event,
its kind (notification type, tool, error type, start source or end reason), the session id and a
short redacted detail. The file is trimmed to its newest half once it passes
BOSUN_SIGNALS_MAX_KB.

Readers (the watchdog, events) only add to what Claude's session records and transcripts already
tell them: what a waiting window was asked, which background work kept a window in shell, and a
usage-limit stop the transcript does not show. A signal from another session of the window (an
earlier Claude) never counts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact


SIGNALS_DIR = "signals"
DEFAULT_MAX_KB = 256
DETAIL_CHARS = 200
# A permission request's hook can land a moment before the session record says "waiting".
WAITING_SLACK = timedelta(seconds=30)
TASK_CHARS = 80
MAX_TASKS = 5

NOTIFICATION = "Notification"
PERMISSION_REQUEST = "PermissionRequest"
STOP = "Stop"
STOP_FAILURE = "StopFailure"
SESSION_START = "SessionStart"
SESSION_END = "SessionEnd"
EVENTS = (NOTIFICATION, PERMISSION_REQUEST, STOP, STOP_FAILURE, SESSION_START, SESSION_END)

# The notifications that mean the window waits for an answer (not idle_prompt: that is a finished
# turn nobody has looked at, which is not waiting on anything).
ASKING = frozenset({"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"})
# StopFailure's error types a usage limit (spend limit) arrives as; the text tells it from a short
# rate limit, which Claude Code retries on its own.
LIMIT_ERRORS = frozenset({"rate_limit", "billing_error"})
USAGE_LIMIT_TEXT = re.compile(r"spend limit|usage limit|usage-credits|usage credits", re.IGNORECASE)
# The tool input field that says what a permission prompt asks, by preference.
ASK_FIELDS = ("command", "file_path", "url", "pattern", "description", "prompt")


def text(value: object, limit: int = DETAIL_CHARS) -> str:
    """<value> as one redacted line of at most <limit> characters."""
    line = " ".join(str(value or "").split())
    line = redact(line)
    return line if len(line) <= limit else line[: limit - 3] + "..."


def asked(tool_input: object) -> str:
    """What a permission request asks to run or touch, from its tool input."""
    if not isinstance(tool_input, Mapping):
        return ""
    return next((text(tool_input[f]) for f in ASK_FIELDS if tool_input.get(f)), "")


def running_tasks(tasks: object) -> list[dict[str, str]]:
    """The background tasks a Stop event lists as still running: type and a short description."""
    if not isinstance(tasks, list):
        return []
    running = [t for t in tasks if isinstance(t, Mapping) and t.get("status") == "running"]
    return [{"type": str(t.get("type") or "?"), "what": task_what(t)} for t in running[:MAX_TASKS]]


def task_what(task: Mapping[str, Any]) -> str:
    return text(task.get("command") or task.get("description"), TASK_CHARS)


# Per event: its kind (notification type, tool, error type, start source or end reason) and detail.
KIND_AND_DETAIL: dict[str, Callable[[Mapping[str, Any]], tuple[str, str]]] = {
    NOTIFICATION: lambda p: (str(p.get("notification_type") or ""), text(p.get("message"))),
    PERMISSION_REQUEST: lambda p: (str(p.get("tool_name") or ""), asked(p.get("tool_input"))),
    STOP_FAILURE: lambda p: (str(p.get("error") or "unknown"), text(p.get("last_assistant_message"))),
    SESSION_START: lambda p: (str(p.get("source") or ""), ""),
    SESSION_END: lambda p: (str(p.get("reason") or ""), ""),
}


def kind_and_detail(payload: Mapping[str, Any]) -> tuple[str, str]:
    describe_event = KIND_AND_DETAIL.get(str(payload.get("hook_event_name")))
    return describe_event(payload) if describe_event else ("", "")


def signal_from(payload: Mapping[str, Any], now: datetime) -> dict[str, Any] | None:
    """The line to record for a hook payload, or None for an event this module does not keep."""
    event = payload.get("hook_event_name")
    if event not in EVENTS:
        return None
    kind, detail = kind_and_detail(payload)
    line: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "event": event, "kind": kind}
    line["session"] = str(payload.get("session_id") or "")
    if payload.get("agent_id"):
        line["agent"] = str(payload["agent_id"])
    if detail:
        line["detail"] = detail
    if event == STOP:
        line["running"] = running_tasks(payload.get("background_tasks"))
    return line


# ---- the per-window file ----------------------------------------------------------------------


def signals_path(state_dir: Path, window: str) -> Path:
    return state_dir / SIGNALS_DIR / f"{window.replace('/', '_')}.jsonl"


def trim(path: Path, max_bytes: int) -> None:
    """Keep the newest half of <path> once it passes <max_bytes>."""
    if path.stat().st_size <= max_bytes:
        return
    lines = path.read_text().splitlines(keepends=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text("".join(lines[len(lines) // 2 :]))
    temporary.replace(path)


def append(state_dir: Path, window: str, line: Mapping[str, Any], max_bytes: int) -> Path:
    path = signals_path(state_dir, window)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    trim(path, max_bytes)
    return path


def lines_last_first(path: Path) -> Iterator[dict[str, Any]]:
    try:
        raw = path.read_text().splitlines()
    except OSError:
        return
    for row in reversed(raw):
        try:
            entry = json.loads(row)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            yield entry


# ---- reading --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Signals:
    """The signals of one window's current Claude session."""

    entries: tuple[dict[str, Any], ...]  # newest first

    @classmethod
    def load(cls, state_dir: Path, window: str, session: str | None) -> Signals:
        if not session:
            return cls(())
        path = signals_path(state_dir, window)
        return cls(tuple(e for e in lines_last_first(path) if e.get("session") == session))

    def latest(self, *events: str) -> dict[str, Any] | None:
        return next((e for e in self.entries if e.get("event") in events), None)

    def asking(self, since: datetime | None = None) -> str:
        """What the window was last asked, if that came at or after <since>: a permission request's
        tool and what it runs, else the question's notification text; "" when unknown. The
        permission_prompt notification that follows a request only says "needs your permission", so
        the request itself is preferred."""
        notification = ""
        for entry in self.entries:
            if since is not None and parse_at(entry) < since:
                break
            if entry["event"] == PERMISSION_REQUEST:
                return f"{entry.get('kind')}: {entry.get('detail', '')}".strip(": ")
            if entry["event"] == NOTIFICATION and entry.get("kind") in ASKING and not notification:
                notification = str(entry.get("detail") or entry.get("kind"))
            elif entry["event"] in (STOP, STOP_FAILURE):
                break  # a finished turn: anything older was answered
        return notification

    def running(self) -> list[dict[str, str]]:
        """The background work the last finished turn left running."""
        stop = self.latest(STOP)
        return list(stop.get("running") or []) if stop else []

    def usage_limit_stop(self) -> datetime | None:
        """When the session's turn last ended on a usage limit, if no turn has finished since."""
        for entry in self.entries:
            if entry["event"] == STOP:
                return None
            if entry["event"] == STOP_FAILURE and is_usage_limit(entry):
                return parse_at(entry)
        return None


def is_usage_limit(entry: Mapping[str, Any]) -> bool:
    return entry.get("kind") in LIMIT_ERRORS and bool(USAGE_LIMIT_TEXT.search(str(entry.get("detail") or "")))


def parse_at(entry: Mapping[str, Any]) -> datetime:
    moment = datetime.fromisoformat(str(entry["at"]))
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def describe(tasks: list[dict[str, str]]) -> str:
    """'shell `sleep 100`; subagent `Run the test suite`'."""
    return "; ".join(f"{t.get('type')} `{t.get('what')}`" for t in tasks)


# ---- the command line -------------------------------------------------------------------------


def max_bytes(env: Mapping[str, str]) -> int:
    try:
        return int(float(env.get("BOSUN_SIGNALS_MAX_KB") or DEFAULT_MAX_KB) * 1024)
    except ValueError:
        return DEFAULT_MAX_KB * 1024


def record(window: str, raw: str, env: Mapping[str, str]) -> None:
    """Record one hook payload; any problem is reported on stderr, never raised."""
    try:
        payload = json.loads(raw)
        line = signal_from(payload, datetime.now(UTC)) if isinstance(payload, dict) else None
        if line:
            append(Path(env["BOSUN_STATE_DIR"]), window, line, max_bytes(env))
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR signals: could not record a {window} hook event: {exc}", file=sys.stderr)


def show(window: str, last: int, env: Mapping[str, str]) -> None:
    entries = list(lines_last_first(signals_path(Path(env["BOSUN_STATE_DIR"]), window)))[:last]
    for entry in reversed(entries):
        print(json.dumps(entry))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="signals.py")
    parser.add_argument("command", choices=("record", "show"))
    parser.add_argument("--window", required=True)
    parser.add_argument("--last", type=int, default=20)
    args = parser.parse_args(argv)
    if args.command == "record":
        record(args.window, sys.stdin.read(), os.environ)
    else:
        show(args.window, args.last, os.environ)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
