#!/usr/bin/env python3
"""The agents bosun can run, and the one place that asks them, for what does not care which one it is.
Each is a module with the same functions (Agent, below), so another agent is one more line in AGENTS.
Design: docs/design/harnesses.md.

Usage: agent_state.py registered <pid>...               the first of them that is an agent with a session
       agent_state.py field <pid> <key>                 a field of its running-session record
       agent_state.py describe <pid>                    what `bosun sessions` shows of it (JSON)
       agent_state.py live <session-id>                 the pid of a running agent holding that session
       agent_state.py context <pid>                     how large its conversation is
       agent_state.py launch <pid>                      model<TAB>effort<TAB>bosun it was launched with
       agent_state.py resolve <harness> <dir> <name>    the session that agent knows by <name> in <dir>
       agent_state.py trusted <harness> <dir>           exit 0 if that agent trusts <dir>
       agent_state.py set-trust <harness> <dir> <true|false>

A record has the fields of Claude Code's running-session record, and `harness`, the agent's name.
Exit status 0 found or done, 3 not found, 2 usage.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import codex_state
from agent_files import NOT_FOUND, USAGE, Command, LaunchSettings, emit, run_command


class Agent(Protocol):
    """What bosun asks every agent. Each agent's own files are its module's business."""

    HARNESS: str

    def record(self, pid: int | str) -> dict[str, Any] | None: ...
    def registered(self, pids: Iterable[str]) -> str | None: ...
    def describe(self, pid: str) -> dict[str, Any] | None: ...
    def live_pid(self, session_id: str) -> int | None: ...
    def live_status(self, name: str) -> str | None: ...
    def launch_settings(self, pid: str) -> LaunchSettings: ...
    def conversation_size(self, pid: str) -> int | None: ...
    def resolve(self, workdir: str, name: str) -> str | None: ...
    def trusted(self, directory: str) -> bool: ...
    def set_trust(self, directory: str, trust: bool) -> None: ...
    def prompt_history(self, since: datetime) -> Iterator[tuple[str, datetime]]: ...
    def ready(self) -> bool: ...


AGENTS: dict[str, Agent] = {claude_state.HARNESS: claude_state, codex_state.HARNESS: codex_state}


def record(pid: int | str) -> dict[str, Any] | None:
    """The session of a running agent, whichever it is."""
    for harness, agent in AGENTS.items():
        entry = agent.record(pid)
        if entry is not None:
            return {**entry, "harness": harness}
    return None


def live_status(name: str) -> str | None:
    """The status (idle, busy, shell, waiting) of the running agent whose session is named <name>."""
    return next((status for agent in AGENTS.values() if (status := agent.live_status(name))), None)


def registered(pids: list[str]) -> str | None:
    return next((pid for agent in AGENTS.values() if (pid := agent.registered(pids))), None)


def live_pid(session_id: str) -> int | None:
    return next((pid for agent in AGENTS.values() if (pid := agent.live_pid(session_id))), None)


def describe(pid: str) -> dict[str, Any] | None:
    for harness, agent in AGENTS.items():
        described = agent.describe(pid)
        if described is not None:
            return {**described, "harness": harness}
    return None


def conversation_size(pid: str) -> int | None:
    entry = record(pid)
    return AGENTS[entry["harness"]].conversation_size(pid) if entry else None


def launch_settings(pid: str) -> LaunchSettings | None:
    entry = record(pid)
    return AGENTS[entry["harness"]].launch_settings(pid) if entry else None


def cmd_launch(args: list[str]) -> int:
    """model<TAB>effort<TAB>bosun, "-" for what was not set at launch and yes if bosun launched it."""
    settings = launch_settings(args[0])
    if settings is None:
        return emit(None)
    return emit(f"{settings.model or '-'}\t{settings.effort or '-'}\t{'yes' if settings.by_bosun else 'no'}")


def cmd_field(args: list[str]) -> int:
    return emit((record(args[0]) or {}).get(args[1]))


def cmd_describe(args: list[str]) -> int:
    described = describe(args[0])
    return emit(json.dumps(described) if described else None)


def cmd_resolve(harness: str, args: list[str]) -> int:
    return emit(AGENTS[harness].resolve(args[0], args[1]))


def cmd_trusted(harness: str, args: list[str]) -> int:
    return 0 if AGENTS[harness].trusted(args[0]) else NOT_FOUND


def cmd_set_trust(harness: str, args: list[str]) -> int:
    if args[1] not in ("true", "false"):
        print(__doc__.strip(), file=sys.stderr)
        return USAGE
    AGENTS[harness].set_trust(args[0], args[1] == "true")
    return 0


COMMANDS: dict[str, Command] = {
    "registered": (None, lambda args: emit(registered(args))),
    "field": (2, cmd_field),
    "describe": (1, cmd_describe),
    "live": (1, lambda args: emit(live_pid(args[0]))),
    "context": (1, lambda args: emit(conversation_size(args[0]))),
    "launch": (1, cmd_launch),
}
PER_AGENT = {
    "resolve": (2, cmd_resolve),
    "trusted": (1, cmd_trusted),
    "set-trust": (2, cmd_set_trust),
}


def main(argv: list[str]) -> int:
    per_agent = PER_AGENT.get(argv[0]) if argv else None
    if per_agent and len(argv) == per_agent[0] + 2 and argv[1] in AGENTS:
        return per_agent[1](argv[1], argv[2:])
    return run_command(__doc__, COMMANDS, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
