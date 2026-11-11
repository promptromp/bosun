#!/usr/bin/env python3
"""Everything bosun reads or writes in Google's Antigravity CLI (`agy`)'s own files and on its screen, as
lib/claude_state.py and lib/codex_state.py do for the other agents. Design: docs/design/harnesses.md.

Usage: agy_state.py logged-in                      exit 0 if agy has a login and its onboarding is done
       agy_state.py resolve <workdir> <name>       print the conversation bosun knows by <name> there
       agy_state.py live <conversation-id>         print the pid of a running agy holding it

Exit status 3: not found.

A running agy writes no record of itself, so `record` builds one in the shape of Claude Code's
~/.claude/sessions/<pid>.json, and everything that goes by a window's status works for all agents. What it
goes by, seen in agy 1.5.5 (the Mac VM run, `tests/fixtures/agy/screens/`) and not a public API:
  - `bosun launch` starts agy with `--log-file <logs>/agy-<conversation name>.log` and the environment
    BOSUN_CONVERSATION, TMUX and TMUX_PANE, which `ps eww` shows, so a process is bosun's window <name> in
    pane <pane> of its tmux server, whatever its folder.
  - agy has no status of its own to read. bosun's plugin hooks write a status file per window,
    <state>/agy/<name>.json: busy before each model call, idle or shell at the end of a turn, with the
    conversationId (the launch writes the version it started on). Esc cancels a turn and fires no hook, so
    while the file says busy the window's screen says what it is doing (`screen_status`).
  - agy keeps each conversation as <home>/conversations/<id>.db, and resumes one only by its id, so bosun
    keeps its own table of names, filled in by the hooks.
  - Its login is a Keychain item with service `gemini` (looked for without reading the secret) and an
    onboarding that is done when cache/onboarding.json says `"onboardingComplete": true`.
  - A launch gives agy its folder for the session, so bosun never writes agy's trust.
The screen:
  - The input box is the line starting with `>` between two lines of `─` (the rules, drawn grey,
    `ESC[90m`), and the lines under it down to the second rule. The empty box is the mark alone
    (`ESC[94m>ESC[39m`). Earlier messages in the history are drawn with the same mark under a rule too,
    so the last such line is the box, if a second rule closes it with the footer right under it. A dialog
    has a rule above its choices with a blank line under it, and the trust screen none: no box.
  - Under the box a footer line says what agy is doing: it starts with `? for shortcuts` when it is idle
    and `esc to cancel` when a turn runs. Text in the box blanks the hint. A background task adds `task(s)`
    and `/tasks` to the idle footer (`· 1 task(s) · /tasks`), and a message queued with a turn running
    changes it to `Press up to edit queued messages`. The model name, drawn faint, ends the line.
  - A dialog (running a command, say) shows "Run this command?", numbered choices with the mark on the
    selected one and `↑/↓ Navigate · ...`. Enter answers it. Its footer also says `esc to cancel`, so a
    dialog is looked for before the footer is read. The trust screen ("Do you trust the contents of this
    project?") and the sign-in screen ("You are currently not signed in") have the same `↑/↓ Navigate` line.
  - Pressing Esc in a turn leaves "Interrupted" and the idle footer, and fires no hook, so the screen is
    how a window that still reads busy is told from one that was cancelled (`screen_status`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple


sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_files import (
    NOT_FOUND,
    Command,
    LaunchSettings,
    Screen,
    emit,
    never_rejects,
    read_object,
    run_command,
    write_atomically,
)
from screen_text import NBSP, SGR, is_rule, visible


HARNESS = "agy"
HOME = Path.home()
AGY_HOME = HOME / ".gemini"
CLI_HOME = AGY_HOME / "antigravity-cli"
BIN_KEY = "BOSUN_AGY_BIN"  # the setting that names its command
NAMES_FILE = "agy-sessions.json"  # in bosun's state dir
STATUS_DIR = "agy"  # in bosun's state dir: a status file per window
KEYCHAIN_SERVICE = "gemini"  # the Keychain item agy's login is
IDLE, BUSY, SHELL, WAITING = "idle", "busy", "shell", "waiting"
# The log file's flag, a word of its own with its value one word after it, in agy's command line at launch.
LOG_FILE = re.compile(r"(?<!\S)--log-file[= ](\S+)")
LOG_PREFIX, LOG_SUFFIX = "agy-", ".log"  # around the conversation name in that file's name
FIRST_PROMPT = re.compile(r"(?<!\S)--prompt-interactive(?:=|\s)")  # where the launch's own flags end
# bosun's flags come before the first prompt, which is on the command line too and can say anything (text from
# Slack, say), so a flag read for the launch settings is the first of its kind.
MODEL_FLAG = re.compile(r"(?<!\S)--model(?:=|\s+)(\S+)")
EFFORT_FLAG = re.compile(r"(?<!\S)--effort(?:=|\s+)(\S+)")
PS_TIMEOUT_SECONDS = 10
CAPTURE_TIMEOUT_SECONDS = 5


AGY_MARK = ">"  # the input line's prompt mark
AGY_QUESTIONS = (
    "↑/↓ Navigate",  # a dialog, and the trust and sign-in screens
    "Do you trust the contents of this project?",
    "You are currently not signed in",
)
IDLE_HINT = "? for shortcuts"  # the footer's start when nothing runs
TASK_HINT = "task(s)"  # in the idle footer while a background task runs
BOX_INDENT = "  "  # what the lines of a box under its first line are indented by


# ---- the screen --------------------------------------------------------------------------------


def agy_box(lines: list[str]) -> list[str] | None:
    """The lines of agy's box: from the last line with the mark under a rule down to the next rule, which has
    a footer or a task line right under it. None if there is none: a dialog's choices also have the mark,
    and a rule above them, but its rule has a blank line under it (the trust screen has no rule at all)."""
    for start in range(len(lines) - 1, 0, -1):
        if SGR.sub("", lines[start]).lstrip().startswith(AGY_MARK) and is_rule(lines[start - 1]):
            end = next((i for i in range(start + 1, len(lines)) if is_rule(lines[i])), None)
            if end is None or end + 1 >= len(lines) or not lines[end + 1].strip():
                return None
            return lines[start:end]
    return None


def agy_typed_text(lines: list[str]) -> str | None:
    """The text in agy's box, if this screen has one."""
    box = agy_box(lines)
    if box is None:
        return None
    first, *rest = (visible(line).replace(NBSP, " ") for line in box)
    first = first.lstrip().removeprefix(AGY_MARK).removeprefix(" ")
    return "\n".join([first, *(text.removeprefix(BOX_INDENT) for text in rest)]).strip()


def agy_question(capture: str) -> bool:
    """Whether agy is asking something on this screen (a dialog, the trust or the sign-in screen)."""
    return any(question in capture for question in AGY_QUESTIONS)


def screen_status(capture: str) -> str | None:
    """What the screen says agy is doing: "waiting" at a dialog, "shell" and "idle" when the hint is over
    an empty box (with and without a background task), None for anything else (a turn, text typed, a
    queued message, no box)."""
    if agy_question(capture):
        return "waiting"
    lines = capture.splitlines()
    if agy_typed_text(lines) != "":
        return None
    footer = next((visible(line).strip() for line in reversed(lines) if line.strip()), "")
    if not footer.startswith(IDLE_HINT):
        return None
    return "shell" if TASK_HINT in footer else "idle"


SCREEN = Screen(agy_typed_text, agy_question, never_rejects)


# ---- the status files ---------------------------------------------------------------------------


def state_dir() -> Path:
    return Path(os.environ.get("BOSUN_STATE_DIR") or HOME / ".local" / "state" / "bosun")


def status_path(name: str) -> Path:
    return state_dir() / STATUS_DIR / f"{name}.json"


def read_status(name: str) -> dict[str, Any]:
    """What the hooks and the launch wrote about the window <name>: its status, statusUpdatedAt (epoch
    milliseconds), conversationId and version. Empty before the first of them."""
    return read_object(status_path(name)) or {}


def write_status(name: str, **fields: Any) -> None:
    """Merge <fields> into the status file of <name>. Two hooks can race here, and the one that loses only
    leaves a status that the next event corrects, so no lock."""
    write_atomically(status_path(name), json.dumps({**read_status(name), **fields}, indent=1))


def write_status_if_unchanged(name: str, since: Any, **fields: Any) -> dict[str, Any]:
    """Merge <fields> into the status file of <name> if its statusUpdatedAt is still <since>, the one its
    reader saw, deciding and merging on one read of the file; the file as it is afterwards is returned.
    Without a lock, a hook's write between that read and the replace is still lost: this narrows the window
    and does not close it, and the next hook event corrects what is lost."""
    current = read_status(name)
    if current.get("statusUpdatedAt") != since:
        return current
    current.update(fields)
    write_atomically(status_path(name), json.dumps(current, indent=1))
    return current


def now_ms() -> int:
    return int(time.time() * 1000)


# ---- the names bosun knows conversations by


def names_path() -> Path:
    return state_dir() / NAMES_FILE


def known_names() -> dict[str, Any]:
    return read_object(names_path()) or {}


def remember(name: str, conversation_id: str, workdir: str) -> None:
    """A lost race here only loses a name until the next turn ends, so no lock."""
    names = known_names()
    names[name] = {"id": conversation_id, "cwd": os.path.realpath(workdir)}
    write_atomically(names_path(), json.dumps(names, indent=1))


def conversation_file(conversation_id: str) -> Path:
    return CLI_HOME / "conversations" / f"{conversation_id}.db"


def resolve(workdir: str, name: str) -> str | None:
    """The conversation bosun knows by <name> in <workdir>, if agy still has it on disk."""
    entry = known_names().get(name)
    if not isinstance(entry, dict) or entry.get("cwd") != os.path.realpath(workdir):
        return None
    conversation_id = str(entry.get("id") or "")
    return conversation_id if conversation_id and conversation_file(conversation_id).exists() else None


# ---- the processes


def agy_bin() -> str:
    return os.environ.get(BIN_KEY) or HARNESS


def is_agy_command(words: str) -> bool:
    """Whether the command line <words> starts agy's own command."""
    first = words.split(None, 1)[0] if words.strip() else ""
    return os.path.basename(first) == os.path.basename(agy_bin())


class Marker(NamedTuple):
    """What a bosun-launched agy's command line and environment say: its conversation name and its pane."""

    name: str
    socket: str | None  # the tmux server's socket
    pane: str | None


def last_value(key: str, words: str) -> str | None:
    """The value of the last `<key>=<value>` word in <words>. `ps eww` prints a process's environment after
    its arguments, and the arguments include the first prompt, which can hold such a word too, so only the
    last is the environment's."""
    values = re.findall(rf"(?<!\S){key}=(\S+)", words)
    return values[-1] if values else None


def parse_marker(words: str) -> Marker | None:
    """The marker in the output of `ps eww` for one process, None if it is not agy launched by bosun. The
    conversation name is the environment's BOSUN_CONVERSATION (the last one, see `last_value`), and counts
    only if the command line also has a `--log-file` for exactly that name before its first prompt, where the
    launch puts it. A prompt on the command line can say other names, sockets and panes, but neither what the
    environment says after it nor a flag before it."""
    name = last_value("BOSUN_CONVERSATION", words)
    if name is None or not is_agy_command(words):
        return None
    log_name = f"{LOG_PREFIX}{name}{LOG_SUFFIX}"
    flags = FIRST_PROMPT.split(words, maxsplit=1)[0]
    if not any(os.path.basename(path) == log_name for path in LOG_FILE.findall(flags)):
        return None
    socket = last_value("TMUX", words)
    return Marker(name, socket.split(",")[0] if socket else None, last_value("TMUX_PANE", words))


def run_ps(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["ps", *args],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=PS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout


def process_environment(pid: int | str) -> str:
    """A process's command line followed by its environment, as `ps eww` prints them."""
    return run_ps(["eww", "-o", "command=", "-p", str(pid)]).strip()


def process_table() -> list[tuple[int, str]]:
    """Every process, as its pid and command line."""
    table = []
    for line in run_ps(["-axww", "-o", "pid=,command="]).splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit():
            table.append((int(pid), command.strip()))
    return table


def marker(pid: int | str) -> Marker | None:
    return parse_marker(process_environment(pid))


# ---- the screen of a window that reads busy


def capture_pane(socket: str, pane: str) -> str | None:
    """The screen of tmux pane <pane>, with its escape sequences; None if it could not be read."""
    try:
        result = subprocess.run(
            ["tmux", "-S", socket, "capture-pane", "-e", "-p", "-t", pane],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=CAPTURE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def screen_of(found: Marker) -> str | None:
    return capture_pane(found.socket, found.pane) if found.socket and found.pane else None


def settle(found: Marker, status: dict[str, Any]) -> tuple[str, Any]:
    """The status and its time of a window, as the file says them unless it says busy: a turn that Esc
    cancelled fires no hook, so then the screen is asked. Idle and shell are written to the file, with
    the time they were seen, and what the file says afterwards is the answer: if a hook wrote meanwhile
    (`write_status_if_unchanged`), that is its fresh status, not the screen's. Waiting is not written, so
    the next look reads the screen again. Whatever the screen cannot tell (no pane, a failed capture, a turn
    on it) stays busy."""
    shown, since = str(status.get("status") or ""), status.get("statusUpdatedAt")
    if shown != BUSY:
        return shown, since
    capture = screen_of(found)
    seen = screen_status(capture) if capture else None
    if seen == WAITING:
        return WAITING, since
    if seen not in (IDLE, SHELL):
        return BUSY, since
    written = write_status_if_unchanged(found.name, since, status=seen, statusUpdatedAt=now_ms())
    return str(written.get("status") or ""), written.get("statusUpdatedAt")


# ---- the records


def build_record(pid: int, found: Marker, status: dict[str, Any]) -> dict[str, Any]:
    conversation_id = str(status["conversationId"])
    shown, since = settle(found, status)
    entry = known_names().get(found.name)
    named_here = isinstance(entry, dict) and entry.get("id") == conversation_id
    return {
        "pid": pid,
        "sessionId": conversation_id,
        "name": found.name,
        "status": shown,
        "statusUpdatedAt": since,
        "version": status.get("version"),
        "cwd": entry.get("cwd") if named_here and isinstance(entry, dict) else None,
        "transcript": str(conversation_file(conversation_id)),
    }


def record(pid: int | str) -> dict[str, Any] | None:
    """A running agy's conversation, in the shape of Claude Code's running-session record. None until its
    window has had its first prompt (no conversation in its status file yet), as for a Claude Code at a
    screen."""
    if not str(pid).isdigit():
        return None
    found = marker(pid)
    status = read_status(found.name) if found else {}
    return build_record(int(pid), found, status) if found and status.get("conversationId") else None


def describe(pid: str) -> dict[str, Any] | None:
    """What `bosun sessions` shows of a running agy: all of its record."""
    return record(pid)


def registered(pids: Iterable[str]) -> str | None:
    """The first of <pids> that is an agy with a conversation."""
    return next((pid for pid in pids if record(pid) is not None), None)


def running() -> Iterator[dict[str, Any]]:
    """The records of every agy window running on this machine."""
    for pid, command in process_table():
        if is_agy_command(command) and "--log-file" in command:
            entry = record(pid)
            if entry is not None:
                yield entry


def live_pid(conversation_id: str) -> int | None:
    return next((int(entry["pid"]) for entry in running() if entry["sessionId"] == conversation_id), None)


def live_status(name: str) -> str | None:
    return next((str(entry["status"]) for entry in running() if entry["name"] == name), None)


def launch_settings(pid: str) -> LaunchSettings:
    """The model and effort a running agy was launched with, read from its command line; and whether bosun
    launched it, which only its log file and environment say."""
    words = process_environment(pid)
    model, effort = MODEL_FLAG.search(words), EFFORT_FLAG.search(words)
    return LaunchSettings(
        model.group(1) if model else None,
        effort.group(1) if effort else None,
        parse_marker(words) is not None,
    )


def conversation_size(_pid: str) -> int | None:
    """None: agy's conversation file (a database) does not say how large the conversation is."""
    return None


# ---- folder trust: each launch passes the folder to agy for its session, so there is nothing to record


def trusted(_directory: str) -> bool:
    """Always: a launch gives agy its folder, and nothing of agy's is written."""
    return True


def trust_recorded(_directory: str) -> bool | None:
    """Always: see `trusted`."""
    return True


def set_trust(_directory: str, _trust: bool) -> None:
    """Nothing: bosun never writes agy's trust, it passes the folder to each session."""


def prompt_history(_since: datetime) -> Iterator[tuple[str, datetime]]:
    """Nothing: where agy keeps what was typed is undocumented, so `bosun init` has no history to read."""
    return iter(())


# ---- the login


def keychain_item() -> bool:
    """Whether the Keychain holds agy's login item. Asked without -w or -g, which would read the secret."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            check=False,
            capture_output=True,
            timeout=CAPTURE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def onboarded() -> bool:
    done = read_object(CLI_HOME / "cache" / "onboarding.json")
    return done is not None and done.get("onboardingComplete") is True


def logged_in() -> bool:
    """Whether agy has its login and has been through its onboarding. Whether the login still works is
    only found out when it is used."""
    return keychain_item() and onboarded()


def ready() -> bool:
    """Whether agy is installed and logged in."""
    return shutil.which(agy_bin()) is not None and logged_in()


def cmd_logged_in(_args: list[str]) -> int:
    return 0 if logged_in() else NOT_FOUND


COMMANDS: dict[str, Command] = {
    "logged-in": (0, cmd_logged_in),
    "resolve": (2, lambda args: emit(resolve(args[0], args[1]))),
    "live": (1, lambda args: emit(live_pid(args[0]))),
}


def main(argv: list[str]) -> int:
    return run_command(__doc__, COMMANDS, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
