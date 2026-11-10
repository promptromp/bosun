#!/usr/bin/env python3
"""Everything bosun reads or writes in OpenAI Codex's own files, as lib/claude_state.py does for
Claude Code. Design: docs/design/harnesses.md.

Usage: codex_state.py trusted <dir>                  exit 0 if Codex trusts <dir>
       codex_state.py set-trust <dir> <true|false>   record that in its config.toml
       codex_state.py logged-in                      exit 0 if `codex login status` says so
       codex_state.py resolve <workdir> <name>       print the session bosun knows by <name> there
       codex_state.py live <session-id>              print the pid of a running Codex holding it

Exit status 3: not found.

A running Codex writes no record of itself, so `record` builds one in the shape of Claude Code's
~/.claude/sessions/<pid>.json, and everything that goes by a window's status works for both. What it
goes by, seen in Codex 0.166.0 and not a public API:
  - bosun starts Codex with --no-daemon, so the process itself holds its session file (its "rollout",
    $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl) open. Without that flag a shared daemon
    holds it. The automatic reviewer has rollouts of its own, so the main one is the one whose
    session_meta says it came from the terminal.
  - The file only appears with the first prompt, so `bosun launch` always gives a new session one, and
    like Claude Code's screens, a Codex with no session file is at a screen or still starting.
  - A turn is `task_started` to `task_complete` or `turn_aborted`. With the default flags nothing asks
    a person; in a mode that asks, a turn waiting for you still reads as busy, because
    the file does not record it (lib/pane_input.py reads that from the screen).
  - What servers a window reaches: `codex mcp list --json` lists the MCP servers Codex would start in a
    folder (configured, a trusted project's and plugin ones; not the built-ins `codex_apps`, ChatGPT's
    connectors, and `codex_worktrees`, which stay on), each with its name and, for a stdio server, its
    environment in plain text, so `mcp_server_names` keeps the names only, each once. With
    `--disable plugins` it lists no plugin's server, so `mcp_servers` tells a plugin's servers from the
    configured ones by listing twice: nothing Codex prints names a server's plugin. A launch switches
    configured servers off with one `-c mcp_servers={<key>={enabled=false},...}`, which merges into the
    configuration, every plugin with `--disable plugins` (a plugin's server in that table stops Codex:
    `invalid transport`), and the connectors with `--disable apps` (lib/connectors.py): one table,
    because Codex splits a `-c` path at every dot, quotes included, so `mcp_servers."a.b".enabled=false`
    makes it exit at start. Seen in the Mac VM (`tests/sandbox.sh mac live codex`) for a user's, a
    project's, a dotted and a local plugin's server.
  - A start waits at an "Update available" screen once Codex has seen a newer version of itself, so the
    launch passes `-c check_for_update_on_startup=false`.
  - Codex has no way to name a session from outside, so bosun keeps its own table of names, filled in by
    the program Codex runs when a turn ends (`bosun hook codex-turn-end`). That program also runs for
    Codex's hidden helpers, each with an id of its own (is_terminal_session).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_files import (
    NOT_FOUND,
    Command,
    LaunchSettings,
    Profile,
    command_line,
    emit,
    read_object,
    reversed_lines,
    run_command,
    write_atomically,
)


HARNESS = "codex"
PROFILE = Profile(
    bin_key="BOSUN_CODEX_BIN",
    flags_key="BOSUN_CODEX_FLAGS",
    model_key="BOSUN_CODEX_MODEL",
    names_sessions=False,
    statuses=frozenset({"idle", "busy"}),
    remote_control=False,
    autocompact=False,
    subagents=False,
    state_files=("codex-sessions.json",),
    display_name="Codex",
    setup_preview=(
        (
            "One Codex session per repo, in its sandbox, with its reviewer deciding when something may go "
            "past it."
        ),
        (
            "The orchestrator stays on Claude Code. Costs and what you said in a window show less for Codex "
            "so far."
        ),
    ),
    setup_sessions="Codex, with its automatic reviewer",
    setup_note=(
        "Repo sessions run on Codex with its automatic reviewer (BOSUN_CODEX_FLAGS): a sandbox, and a safety "
        "check that decides when something may go past it, with no prompts. The orchestrator stays on "
        "Claude Code."
    ),
)
HOME = Path.home()
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
CONFIG_FILE = CODEX_HOME / "config.toml"
SESSIONS_DIR = CODEX_HOME / "sessions"
NAMES_FILE = "codex-sessions.json"  # in bosun's state dir
TERMINAL_SOURCE = "cli"  # session_meta.source of a session in the terminal: not `exec`, not a reviewer
TURN_STARTED = "task_started"
TURN_ENDED = ("task_complete", "turn_aborted")
TURN_MARKER = re.compile(rb'"(task_started|task_complete|turn_aborted)"')
MODEL_FLAG = re.compile(r"(?:^|\s)(?:--model|-m)\s+(\S+)")
EFFORT_SETTING = re.compile(r'model_reasoning_effort="([a-z]+)"')
BOSUN_LAUNCHED = '"codex-turn-end"'  # in bosun's notify= at launch
NAME_ARGUMENT = re.compile(r'"--conversation","([^"]+)"')  # in bosun's notify= at launch
LOGIN_TIMEOUT_SECONDS = 30
IDLE, BUSY = "idle", "busy"


def codex_bin() -> str:
    return os.environ.get("BOSUN_CODEX_BIN") or HARNESS


def held_rollouts(pids: Iterable[int | str]) -> dict[int, list[Path]]:
    """The session files each of <pids> holds open."""
    listed = ",".join(str(pid) for pid in pids)
    if not listed:
        return {}
    try:
        result = subprocess.run(["lsof", "-p", listed, "-Fpn"], check=False, capture_output=True, text=True)
    except OSError:
        return {}
    root = str(SESSIONS_DIR.resolve())
    held: dict[int, list[Path]] = {}
    pid = 0
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n" + root) and line.endswith(".jsonl"):
            held.setdefault(pid, []).append(Path(line[1:]))
    return held


def session_meta(rollout: Path) -> dict[str, Any]:
    """The first line of a session file, which says what the session is."""
    try:
        with rollout.open() as file:
            entry = json.loads(file.readline())
    except (OSError, ValueError):
        return {}
    payload = entry.get("payload") if isinstance(entry, dict) else None
    return payload if isinstance(payload, dict) and entry.get("type") == "session_meta" else {}


def main_rollout(rollouts: Iterable[Path]) -> Path | None:
    return next((path for path in rollouts if session_meta(path).get("source") == TERMINAL_SOURCE), None)


def epoch_ms(timestamp: object) -> int | None:
    try:
        return int(datetime.fromisoformat(str(timestamp)).timestamp() * 1000)
    except ValueError:
        return None


def turn_state(rollout: Path) -> tuple[str, int | None]:
    """Whether a turn is running in <rollout> (idle or busy), and since when, epoch milliseconds."""
    for line in reversed_lines(rollout):
        if TURN_MARKER.search(line) is None:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kind = (entry.get("payload") or {}).get("type") if isinstance(entry, dict) else None
        if kind == TURN_STARTED:
            return BUSY, epoch_ms(entry.get("timestamp"))
        if kind in TURN_ENDED:
            return IDLE, epoch_ms(entry.get("timestamp"))
    return IDLE, None


def build_record(pid: int | str, rollout: Path) -> dict[str, Any]:
    meta = session_meta(rollout)
    status, since = turn_state(rollout)
    name = NAME_ARGUMENT.search(command_line(pid))
    return {
        "pid": int(pid),
        "sessionId": meta.get("id"),
        "name": name.group(1) if name else None,
        "status": status,
        "statusUpdatedAt": since,
        "version": meta.get("cli_version"),
        "cwd": meta.get("cwd"),
        "transcript": str(rollout),
    }


def record(pid: int | str) -> dict[str, Any] | None:
    """A running Codex's session, in the shape of Claude Code's running-session record."""
    if not str(pid).isdigit():
        return None
    rollout = main_rollout(held_rollouts([pid]).get(int(pid), []))
    return build_record(pid, rollout) if rollout else None


def describe(pid: str) -> dict[str, Any] | None:
    """What `bosun sessions` shows of a running Codex: all of its record."""
    return record(pid)


def registered(pids: Iterable[str]) -> str | None:
    """The first of <pids> that is a Codex with a session."""
    numeric = [pid for pid in pids if pid.isdigit()]
    held = held_rollouts(numeric)
    return next((pid for pid in numeric if main_rollout(held.get(int(pid), []))), None)


def running() -> Iterator[dict[str, Any]]:
    """The records of every Codex running on this machine."""
    result = subprocess.run(
        ["ps", "-Ao", "pid=,command="], check=False, capture_output=True, text=True, errors="replace"
    )
    pids = [line.split(None, 1)[0] for line in result.stdout.splitlines() if HARNESS in line]
    for pid, rollouts in held_rollouts(pids).items():
        rollout = main_rollout(rollouts)
        if rollout:
            yield build_record(pid, rollout)


def live_pid(session_id: str) -> int | None:
    return next((int(entry["pid"]) for entry in running() if entry["sessionId"] == session_id), None)


def live_status(name: str) -> str | None:
    return next((str(entry["status"]) for entry in running() if entry["name"] == name), None)


def launch_settings(pid: str) -> LaunchSettings:
    """The model and effort a running Codex was launched with, read from its command line; and whether
    bosun launched it, which only bosun's notify= says."""
    command = command_line(pid)
    model = MODEL_FLAG.search(command)
    effort = EFFORT_SETTING.search(command)
    return LaunchSettings(
        model.group(1) if model else None, effort.group(1) if effort else None, BOSUN_LAUNCHED in command
    )


def conversation_size(pid: str) -> int | None:
    """How large the conversation in the running Codex <pid> was at its last reply."""
    entry = record(pid)
    if entry is None:
        return None
    for line in reversed_lines(Path(entry["transcript"])):
        if b'"token_count"' not in line:
            continue
        try:
            usage = json.loads(line)["payload"]["info"]["last_token_usage"]
        except (ValueError, KeyError, TypeError):
            continue
        return int(usage["total_tokens"]) if isinstance(usage, dict) and "total_tokens" in usage else None
    return None


# --- folder trust: `[projects."<dir>"]` in config.toml, which Codex itself writes in this form


def trust_header(directory: str) -> str:
    return f"[projects.{json.dumps(os.path.realpath(directory))}]"


def trust_recorded(directory: str) -> bool | None:
    """Whether config.toml marks <directory> as trusted; None if the file is there but cannot be read."""
    if not CONFIG_FILE.exists():
        return False
    try:
        projects = tomllib.loads(CONFIG_FILE.read_text()).get("projects", {})
    except (OSError, ValueError):
        return None
    if not isinstance(projects, dict):
        return None
    entry = projects.get(os.path.realpath(directory), {})
    return isinstance(entry, dict) and entry.get("trust_level") == "trusted"


def trusted(directory: str) -> bool:
    return trust_recorded(directory) is True


def without_section(text: str, header: str) -> str:
    """<text> without the TOML table <header>, whatever else it holds left as it was."""
    kept: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        if line.strip() == header:
            inside = True
        elif inside and line.lstrip().startswith("["):
            inside = False
        if not inside:
            kept.append(line)
    return "".join(kept)


def set_trust(directory: str, trust: bool) -> None:
    try:
        text = CONFIG_FILE.read_text()
    except OSError:
        text = ""
    header = trust_header(directory)
    text = without_section(text, header)
    if trust:
        separator = "\n" if text and not text.endswith("\n\n") else ""
        text += f'{separator}{header}\ntrust_level = "trusted"\n'
    write_atomically(CONFIG_FILE, text)


def logged_in() -> bool:
    """What Codex says. For an API key that only means there is one: it is checked when first used."""
    try:
        result = subprocess.run(
            [codex_bin(), "login", "status"], check=False, capture_output=True, timeout=LOGIN_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


class CodexListError(RuntimeError):
    """`codex mcp list --json` gave no answer to read."""


MCP_LIST_TIMEOUT_SECONDS = 30.0


def mcp_server_names(
    codex_bin: str,
    timeout: float = MCP_LIST_TIMEOUT_SECONDS,
    cwd: Path | None = None,
    *,
    plugins: bool = True,
) -> list[str]:
    """The name of each MCP server Codex would start in <cwd> (default: this folder; configured, a
    trusted project's or, unless <plugins> is False (`--disable plugins`), a plugin's; never its
    built-ins), each once. Nothing else of the listing is kept: a stdio server's entry holds its
    environment, secrets included."""
    switch = [] if plugins else ["--disable", "plugins"]
    try:
        result = subprocess.run(
            [codex_bin, *switch, "mcp", "list", "--json"],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexListError(f"timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise CodexListError(f"could not run {codex_bin}: {exc.strerror or exc}") from exc
    if result.returncode != 0:
        raise CodexListError(f"exited {result.returncode}")
    return list(dict.fromkeys(listed_names(result.stdout)))


@dataclass(frozen=True)
class McpServers:
    """The servers a Codex window would start, by how a launch switches one off: <configured>, an
    `mcp_servers` table each (in `config.toml`, a trusted project's or a profile's), by name in
    `-c mcp_servers={...}`; <plugins>, a plugin's each, only all at once (`--disable plugins`), since
    nothing Codex prints says which plugin a server comes from."""

    configured: tuple[str, ...] = ()
    plugins: tuple[str, ...] = ()


def mcp_servers(
    codex_bin: str, timeout: float = MCP_LIST_TIMEOUT_SECONDS, cwd: Path | None = None
) -> McpServers:
    """Codex's listing twice, with plugins and without: a server only in the first is a plugin's."""
    every = mcp_server_names(codex_bin, timeout, cwd)
    configured = mcp_server_names(codex_bin, timeout, cwd, plugins=False)
    return McpServers(tuple(configured), tuple(name for name in every if name not in configured))


def listed_names(text: str) -> list[str]:
    try:
        entries = json.loads(text)
    except ValueError:
        raise CodexListError("its output is not JSON") from None
    if not isinstance(entries, list):
        raise CodexListError("its output is not a list")
    names = [entry.get("name") if isinstance(entry, dict) else None for entry in entries]
    if not all(isinstance(name, str) and name for name in names):
        raise CodexListError("a server in it has no name")
    return [str(name) for name in names]


def ready() -> bool:
    """Whether Codex is installed and logged in."""
    return shutil.which(codex_bin()) is not None and logged_in()


# --- the names bosun knows sessions by


def names_path() -> Path:
    state_dir = os.environ.get("BOSUN_STATE_DIR") or HOME / ".local" / "state" / "bosun"
    return Path(state_dir) / NAMES_FILE


def known_names() -> dict[str, Any]:
    return read_object(names_path()) or {}


def new_prompts(name: str, session_id: str, prompts: list[str]) -> list[str]:
    """Which of <prompts> are new. Codex's report at the end of a turn lists every prompt of the session
    so far, so this counts what it has seen, per session. Not knowing the session yet (the table was lost,
    or it just started), only the last one can be taken for new."""
    entry = known_names().get(name)
    seen = entry.get("prompts") if isinstance(entry, dict) and entry.get("id") == session_id else None
    if not isinstance(seen, int) or seen > len(prompts):
        seen = max(len(prompts) - 1, 0)
    return prompts[seen:]


def remember(name: str, session_id: str, workdir: str, prompts: int) -> None:
    """A lost race here only loses a name until the next turn ends, so no lock."""
    names = known_names()
    names[name] = {"id": session_id, "cwd": os.path.realpath(workdir), "prompts": prompts}
    write_atomically(names_path(), json.dumps(names, indent=1))


def rollout_exists(session_id: str) -> bool:
    return any(SESSIONS_DIR.glob(f"*/*/*/rollout-*-{session_id}.jsonl"))


def is_terminal_session(session_id: str) -> bool:
    """Whether <session_id> is a session someone works in. Codex also reports the end of a turn for its
    helpers from the same process: the one that writes a session's title has no file at all, and the
    reviewer's says it is a reviewer."""
    return main_rollout(SESSIONS_DIR.glob(f"*/*/*/rollout-*-{session_id}.jsonl")) is not None


def resolve(workdir: str, name: str) -> str | None:
    entry = known_names().get(name)
    if not isinstance(entry, dict) or entry.get("cwd") != os.path.realpath(workdir):
        return None
    session_id = str(entry.get("id") or "")
    return session_id if session_id and rollout_exists(session_id) else None


# --- what `bosun init` recommends from


def rollout_turns(rollout: Path, since: datetime) -> Iterator[tuple[str, datetime]]:
    meta = session_meta(rollout)
    cwd = meta.get("cwd")
    if meta.get("source") != TERMINAL_SOURCE or not cwd:
        return
    with rollout.open(errors="replace") as file:
        for line in file:
            if f'"{TURN_STARTED}"' not in line:
                continue
            try:
                at = datetime.fromisoformat(json.loads(line)["timestamp"])
            except (ValueError, KeyError, TypeError):
                continue
            if at >= since:
                yield str(cwd), at


def prompt_history(since: datetime) -> Iterator[tuple[str, datetime]]:
    """Where and when someone gave Codex a prompt since <since>, never what: each turn in a terminal
    session starts with one, as claude_state.prompt_history does for Claude Code."""
    for rollout in SESSIONS_DIR.glob("*/*/*/rollout-*.jsonl"):
        try:
            if rollout.stat().st_mtime >= since.timestamp():
                yield from rollout_turns(rollout, since)
        except OSError:
            continue  # vanished or unreadable


def cmd_logged_in(_args: list[str]) -> int:
    return 0 if logged_in() else NOT_FOUND


def cmd_set_trust(args: list[str]) -> int:
    set_trust(args[0], args[1] == "true")
    return 0


COMMANDS: dict[str, Command] = {
    "trusted": (1, lambda args: 0 if trusted(args[0]) else NOT_FOUND),
    "set-trust": (2, cmd_set_trust),
    "logged-in": (0, cmd_logged_in),
    "resolve": (2, lambda args: emit(resolve(args[0], args[1]))),
    "live": (1, lambda args: emit(live_pid(args[0]))),
}


def main(argv: list[str]) -> int:
    return run_command(__doc__, COMMANDS, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
