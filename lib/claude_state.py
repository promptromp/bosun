#!/usr/bin/env python3
"""Everything bosun reads or writes in Claude Code's own files, in one place.

None of these are public APIs. If a Claude Code update changes them, this is the file to fix
(and `bosun doctor` / `bosun refresh --dry-run` show whether it works):

- ~/.claude/projects/<cwd with non-alphanumerics as "-">/<session-uuid>.jsonl: transcripts; the
  latest "custom-title" entry is the session's name
- ~/.claude/sessions/<pid>.json: one record per running Claude (sessionId, name, status, version,
  statusUpdatedAt, startedAt)
- ~/.claude.json: projects[<dir>].hasTrustDialogAccepted, set when you accept the folder trust prompt
- transcript entries (read by `bosun learn`): user prompts (`origin`, `promptSource`), tool calls
  and results (`is_error`, `permissionDecision.source` "user_*" when you answered a prompt),
  `queue-operation` (a message typed or pasted while Claude was busy; once the turn picks it
  up, it is stored only as an `attachment` of type `queued_command`), PreToolUse hook results
  (`attachment` hook_success), `system` turn ends and notices; subagents in
  <session-uuid>/subagents/*.jsonl next to the transcript
- ~/.claude/projects/<cwd as above>/memory/: Claude Code's memory for that folder (MEMORY.md, the
  index, and one note file per memory)
- failed requests (read by lib/login.py): a synthetic `assistant` entry with isApiErrorMessage,
  `error` (the Agent SDK's assistant-message error: authentication_failed, rate_limit,
  server_error, ...), sometimes `apiErrorStatus` (HTTP status) and `apiError` (a finer code such as
  no_response); `claude -p --output-format stream-json` reports the same `error` on its assistant
  message and `is_error` / `api_error_status` on its result
- `claude auth status --json`: `loggedIn` and `authMethod`, from the stored credentials only (it
  makes no request, so a token the server revoked still shows as logged in)

Usage: claude_state.py resolve <workdir> <name>    the most recent session named exactly <name>
       claude_state.py list <workdir>              named sessions, newest first
       claude_state.py live <session-uuid>         pid of a running Claude holding the session
       claude_state.py registered <pid>...         the first <pid> that is a running Claude
       claude_state.py field <pid> <field>         a field of a running Claude's record
       claude_state.py describe <pid>              a running Claude's record and transcript (JSON)
       claude_state.py trusted <dir>               exit 0 if Claude Code trusts <dir>
       claude_state.py onboarded                   exit 0 if its first-run screens are done
       claude_state.py bypass-accepted             exit 0 if its bypass-mode warning was accepted
       claude_state.py logged-in                   exit 0 if `claude auth status` says so
       claude_state.py set-trust <dir> true|false  record (or remove) folder trust
       claude_state.py prompts <transcript> [<ISO time>]
                                                   the prompts typed into a session (JSON lines),
                                                   also those typed while it was busy
       claude_state.py api-error <transcript>      the error the session's latest reply reports
                                                   (JSON), exit 3 if that reply is a normal one
       claude_state.py context <pid>               tokens a running Claude's latest turn read (input,
                                                   cache reads and writes): its conversation's size

Exit codes: 0 found / done, 3 not found, 2 usage.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any


HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"
LIVE_SESSIONS_DIR = HOME / ".claude" / "sessions"
CONFIG_FILE = HOME / ".claude.json"
SETTINGS_FILE = HOME / ".claude" / "settings.json"
INSTALLED_PLUGINS = HOME / ".claude" / "plugins" / "installed_plugins.json"
SLACK_PLUGIN = "slack"
TITLE_MARKER = '"custom-title"'
SESSION_PREFIX = re.compile(r"^[0-9a-f]{8,36}$")  # a session id or its start, as references quote it
NOT_FOUND = 3
USAGE = 2
START_TIME_TOLERANCE_SECONDS = 2


# ---- transcripts -------------------------------------------------------------------------------


def project_dir(workdir: str) -> Path:
    resolved = str(Path(workdir).expanduser().resolve())
    return PROJECTS_DIR / "".join(c if c.isalnum() or c == "-" else "-" for c in resolved)


def latest_title(transcript: Path) -> str | None:
    title = None
    with transcript.open(errors="replace") as fh:
        for line in fh:
            if TITLE_MARKER in line:
                try:
                    title = json.loads(line).get("customTitle", title)
                except json.JSONDecodeError:
                    continue
    return title


def transcripts_by_prefix(prefix: str) -> list[Path]:
    """Transcripts whose session id starts with <prefix>, the short id a reference quotes."""
    if not SESSION_PREFIX.match(prefix):
        return []
    return sorted(PROJECTS_DIR.glob(f"*/{prefix}*.jsonl"))


def named_sessions(workdir: str) -> list[tuple[float, str, str]]:
    """(mtime, session id, title) for every named session in <workdir>, newest first."""
    sessions = []
    for transcript in project_dir(workdir).glob("*.jsonl"):
        try:
            title = latest_title(transcript)
            if title:
                sessions.append((transcript.stat().st_mtime, transcript.stem, title))
        except OSError:
            continue  # vanished, unreadable or not a file
    return sorted(sessions, reverse=True)


def resolve(workdir: str, name: str) -> str | None:
    return next((session_id for _, session_id, title in named_sessions(workdir) if title == name), None)


def transcript_path(workdir: str, session_id: str) -> Path:
    return project_dir(workdir) / f"{session_id}.jsonl"


@dataclass(frozen=True)
class Transcript:
    session_id: str
    title: str  # the latest custom title, "" for an unnamed session
    path: Path
    modified: float


def transcripts_since(workdir: str, since: float) -> list[Transcript]:
    """Every transcript in <workdir>'s project folder written to at or after <since> (epoch
    seconds), oldest first."""
    found = []
    for path in project_dir(workdir).glob("*.jsonl"):
        try:
            modified = path.stat().st_mtime
            if modified >= since:
                found.append(Transcript(path.stem, latest_title(path) or "", path, modified))
        except OSError:
            continue  # vanished, unreadable or not a file
    return sorted(found, key=lambda t: t.modified)


def subagent_transcripts(transcript: Path) -> list[Path]:
    return sorted((transcript.parent / transcript.stem / "subagents").glob("*.jsonl"))


# ---- running sessions --------------------------------------------------------------------------


def read_object(path: Path) -> dict[str, Any] | None:
    """The JSON object in <path>, or None if it is missing, unreadable or not an object."""
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


# ---- plugins -----------------------------------------------------------------------------------


def slack_plugin_mcp_config(installed: Path | None = None) -> dict[str, Any] | None:
    """The Slack plugin's MCP server as an --mcp-config document, or None if the plugin is not
    installed. The server keeps the name Claude Code gives a plugin's server
    (plugin:<plugin>:<server>), so a headless Claude with --strict-mcp-config finds the login it
    stored for it (the plugin's .mcp.json under the server's name alone has no login)."""
    plugins = (read_object(installed or INSTALLED_PLUGINS) or {}).get("plugins")
    for key, installs in (plugins or {}).items():
        if str(key).split("@")[0] != SLACK_PLUGIN or not isinstance(installs, list):
            continue
        for install in installs:
            server = plugin_server(install)
            if server:
                return {"mcpServers": {f"plugin:{SLACK_PLUGIN}:{server[0]}": server[1]}}
    return None


def plugin_server(install: object) -> tuple[str, Any] | None:
    """The one MCP server (name, config) in an installed plugin's .mcp.json, if it has exactly one."""
    if not isinstance(install, dict):
        return None
    servers = (read_object(Path(str(install.get("installPath", ""))) / ".mcp.json") or {}).get("mcpServers")
    if not isinstance(servers, dict) or len(servers) != 1:
        return None
    return next(iter(servers.items()))


def record(pid: int | str) -> dict[str, Any] | None:
    return read_object(LIVE_SESSIONS_DIR / f"{pid}.json")


def process_start(pid: int) -> float | None:
    """Start time of <pid> as a Unix timestamp, from ps."""
    result = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], check=False, capture_output=True, text=True
    )
    try:
        return time.mktime(time.strptime(result.stdout.strip(), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def is_stale(entry: dict[str, Any]) -> bool:
    """A record whose pid now belongs to a process started after the record was written: Claude
    exited without cleaning up and the pid was reused."""
    started_at = entry.get("startedAt")
    if not isinstance(started_at, (int, float)):
        return False
    began = process_start(entry["pid"])
    return began is not None and began > started_at / 1000 + START_TIME_TOLERANCE_SECONDS


def running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False  # gone, or another user's process
    return True


def live_records(key: str, value: str) -> Iterator[dict[str, Any]]:
    """The records of running Claudes whose <key> (sessionId, name) is <value>."""
    for registry in LIVE_SESSIONS_DIR.glob("*.json"):
        entry = read_object(registry)
        if entry is None or entry.get(key) != value or not isinstance(entry.get("pid"), int):
            continue
        if running(entry["pid"]) and not is_stale(entry):
            yield entry


def live_pid(session_id: str) -> int | None:
    entry = next(live_records("sessionId", session_id), None)
    return int(entry["pid"]) if entry else None


def live_status(name: str) -> str | None:
    """The status (idle, busy, shell, waiting) of the running Claude whose session is named
    <name>: a repo window's session carries the window's name."""
    entry = next(live_records("name", name), None)
    return str(entry.get("status") or "") or None if entry else None


# ---- folder trust ------------------------------------------------------------------------------


def trusted(directory: str) -> bool:
    try:
        config = json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return (
        config.get("projects", {}).get(os.path.realpath(directory), {}).get("hasTrustDialogAccepted") is True
    )


def onboarded() -> bool:
    """Whether someone went through the first-run screens (the theme, the security notes). Until then
    every interactive Claude stops at them, and no setting skips them."""
    return (read_object(CONFIG_FILE) or {}).get("hasCompletedOnboarding") is True


def bypass_accepted() -> bool:
    """Whether someone accepted the warning that --dangerously-skip-permissions shows once per machine:
    its cursor starts on "No, exit", so an unattended window never gets past it."""
    return (read_object(SETTINGS_FILE) or {}).get("skipDangerousModePermissionPrompt") is True


def set_trust(directory: str, trust: bool) -> None:
    """Rewrite ~/.claude.json with the entry set or removed: read, update, write a temp file and
    rename it, so a reader never sees a partial file. Claude Code re-reads the file before its own
    writes, so the entry survives running sessions."""
    target = Path(os.path.realpath(CONFIG_FILE))
    try:
        config = json.loads(target.read_text())
    except FileNotFoundError:
        config = {}
    projects = config.setdefault("projects", {})
    key = os.path.realpath(directory)
    if trust:
        projects.setdefault(key, {})["hasTrustDialogAccepted"] = True
    elif key in projects:
        projects[key].pop("hasTrustDialogAccepted", None)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".claude.json.bosun-")
    with os.fdopen(fd, "w") as fh:
        json.dump(config, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


# ---- what happened in a transcript (read by `bosun learn`) ---------------------------------

USER_REJECTED = "The user doesn't want to proceed with this tool use"
USER_SAID = "the user said:"
TASK_NOTIFICATION = "<task-notification>"
ASK_USER_TOOL = "AskUserQuestion"
TEXT_LIMIT = 600
SUMMARY_LIMIT = 200
PASTED = re.compile(r"</?pasted_content[^>]*>")
COMMAND_TAGS = re.compile(r"<command-(?:name|args)>(.*?)</command-(?:name|args)>", re.DOTALL)
COMMAND_NOISE = re.compile(r"<command-message>.*?</command-message>", re.DOTALL)


@dataclass(frozen=True)
class Event:
    """One thing that happened in a session, in terms that do not depend on Claude Code:

    prompt      a message to the session (origin: typed; queued: it arrived while the session was
                busy and waited for the turn to end; mid-turn: it arrived while busy and was added to
                the running turn; undelivered: it arrived while busy and was never delivered)
    tool-error  a tool call that failed
    rejected    a tool call you refused at its permission prompt (text: what you said, if anything)
    permission  a permission prompt you answered (waited: seconds from the call to the answer,
                including the run time of the tool)
    question    a question the session asked you (waited: until you answered)
    hook-deny   a PreToolUse hook refused a tool call (text: its reason)
    command     a shell command (tool Bash), with its first line
    reply       the session's last text at the end of a turn
    notice      context compaction, Remote Control disconnects and other warnings
    """

    at: datetime
    kind: str
    text: str = ""
    tool: str = ""
    waited: float | None = None
    origin: str = ""
    failed: bool = False


def short(text: str, limit: int = SUMMARY_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def entry_time(entry: dict[str, Any]) -> datetime | None:
    stamp = entry.get("timestamp")
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


def block_text(content: object) -> str:
    """The text of a message or tool result: a string, or the text blocks of a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return ""


def prompt_text(text: str) -> str:
    """A prompt as you would read it: slash commands as typed, pasted text unwrapped."""
    commands = COMMAND_TAGS.findall(text)
    if commands:
        return " ".join(c.strip() for c in commands if c.strip())
    return PASTED.sub("", COMMAND_NOISE.sub("", text)).strip()


QUEUED_COMMAND = "queued_command"
HUMAN_ORIGINS = (None, "human")
PROMPT_MODE = "prompt"


def typed_prompt(entry: dict[str, Any]) -> tuple[str, str] | None:
    """(text, origin) of a prompt someone typed or pasted, from a transcript entry, else None.

    Two shapes: a `user` entry (not a tool result, not meta, from a human; origin: its
    promptSource, else "typed"), and a prompt typed while the session was busy, which Claude Code
    stores only as an `attachment` of type `queued_command` (commandMode "prompt", origin human or
    none) when the turn picks it up (origin "queued"). Background-task notifications and other
    injected messages are not prompts."""
    if entry.get("type") == "attachment":
        return queued_prompt(entry.get("attachment") or {})
    if entry.get("type") != "user" or entry.get("isMeta") or has_tool_result(entry):
        return None
    if entry.get("isCompactSummary"):  # the summary after /compact, written by Claude, not typed
        return None
    return user_prompt(entry)


def user_prompt(entry: dict[str, Any]) -> tuple[str, str] | None:
    text = prompt_text(block_text((entry.get("message") or {}).get("content")))
    human = (entry.get("origin") or {}).get("kind") in HUMAN_ORIGINS
    if not text or text.startswith(TASK_NOTIFICATION) or not human:
        return None
    return text, str(entry.get("promptSource") or "typed")


def queued_prompt(attachment: dict[str, Any]) -> tuple[str, str] | None:
    if attachment.get("type") != QUEUED_COMMAND or attachment.get("commandMode") != PROMPT_MODE:
        return None
    text = prompt_text(block_text(attachment.get("prompt")))
    human = (attachment.get("origin") or {}).get("kind") in HUMAN_ORIGINS
    return (text, "queued") if text and human else None


def has_tool_result(entry: dict[str, Any]) -> bool:
    content = (entry.get("message") or {}).get("content")
    blocks = content if isinstance(content, list) else []
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)


@dataclass(frozen=True)
class Prompt:
    at: datetime
    text: str
    origin: str  # typed (or Claude Code's promptSource), queued


def user_prompts(transcript: Path, since: datetime | None = None) -> list[Prompt]:
    """Every prompt typed into a session (also while it was busy), oldest first: what the user
    said there. Use this, not a filter on `user` entries, which misses queued prompts."""
    found = []
    with transcript.open(errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            at = entry_time(entry) if isinstance(entry, dict) else None
            prompt = typed_prompt(entry) if at is not None else None
            if prompt and at is not None and (since is None or at >= since):
                found.append(Prompt(at, *prompt))
    return sorted(found, key=lambda p: p.at)


PROMPT_LINE = re.compile(r'"type":\s*"user"|"queued_command"')  # skips the rest of a long transcript unparsed
SCRIPTED_ENTRYPOINT = "sdk"  # `claude -p` and the Agent SDK: a script's prompts, not someone at work


def prompt_entries(transcript: Path) -> Iterator[dict[str, Any]]:
    """The entries of a transcript that can be prompts someone typed, parsed."""
    with transcript.open(errors="replace") as fh:
        for line in fh:
            if not PROMPT_LINE.search(line):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and not str(entry.get("entrypoint")).startswith(SCRIPTED_ENTRYPOINT):
                yield entry


def transcript_prompt_uses(transcript: Path, since: datetime) -> Iterator[tuple[str, datetime]]:
    cwd = ""
    for entry in prompt_entries(transcript):
        cwd = str(entry.get("cwd") or cwd)
        at = entry_time(entry)
        if at is not None and at >= since and cwd and typed_prompt(entry):
            yield cwd, at


def prompt_history(since: datetime) -> Iterator[tuple[str, datetime]]:
    """Where and when someone typed a prompt since <since>, across all projects, never what: what
    `bosun init` recommends repos and a schedule from."""
    for transcript in PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            if transcript.stat().st_mtime >= since.timestamp():
                yield from transcript_prompt_uses(transcript, since)
        except OSError:
            continue  # vanished or unreadable


@dataclass(frozen=True)
class Activity:
    """One entry of a transcript as the Impact view counts it: when, what kind (`assistant`, a
    `tool-result`, a `prompt` someone typed, or a message `injected` into the session that starts a
    turn without being typed: a background-task notification, another session's message), and for
    an assistant message its id, model and token usage (input, output, cache_write, cache_read, and
    cache_write_1h: the part of cache_write written with the 1-hour lifetime)."""

    at: datetime
    kind: str
    message_id: str = ""
    model: str = ""
    usage: tuple[tuple[str, int], ...] = ()
    text: str = ""
    scheduled: bool = False  # an injected prompt Claude Code itself sent on a schedule (cron, wake-up)


ACTIVITY_ASSISTANT, ACTIVITY_TOOL_RESULT, ACTIVITY_PROMPT = "assistant", "tool-result", "prompt"
ACTIVITY_INJECTED = "injected"
INJECTED_TEXT_LIMIT = 200  # enough to tell what kind of message it is
USAGE_FIELDS = (
    ("input", "input_tokens"),
    ("output", "output_tokens"),
    ("cache_write", "cache_creation_input_tokens"),
    ("cache_read", "cache_read_input_tokens"),
)
# The 1-hour share of the cache writes, from usage.cache_creation (absent in older transcripts: then
# every write counts as a 5-minute one).
CACHE_WRITE_1H = ("cache_write_1h", "cache_creation", "ephemeral_1h_input_tokens")


def assistant_activity(entry: dict[str, Any], at: datetime) -> Activity:
    message = entry.get("message") or {}
    usage = message.get("usage") or {}
    name_1h, group, key_1h = CACHE_WRITE_1H
    write_1h = (name_1h, int((usage.get(group) or {}).get(key_1h) or 0))
    counts = (*((name, int(usage.get(key) or 0)) for name, key in USAGE_FIELDS), write_1h)
    return Activity(
        at, ACTIVITY_ASSISTANT, str(message.get("id") or ""), str(message.get("model") or ""), counts
    )


def entry_activity(entry: dict[str, Any]) -> Activity | None:
    at = entry_time(entry)
    if at is None:
        return None
    if entry.get("type") == "assistant":
        return assistant_activity(entry, at)
    if entry.get("type") == "user" and has_tool_result(entry):
        return Activity(at, ACTIVITY_TOOL_RESULT)
    prompt = typed_prompt(entry)
    if prompt:
        return Activity(at, ACTIVITY_PROMPT, text=prompt[0])
    injected = injected_text(entry)
    if not injected:
        return None
    return Activity(at, ACTIVITY_INJECTED, text=injected, scheduled=bool(entry.get("isMeta")))


SYSTEM_SOURCE = "system"


def injected_text(entry: dict[str, Any]) -> str:
    """The opening of a user entry nobody typed that starts a turn (a background-task notification,
    a message from another session, a prompt Claude Code sends on a schedule: a meta entry whose
    promptSource is "system"), else "". Other meta entries (a slash command's expanded text) and
    the summary after /compact are not turns of their own."""
    if entry.get("type") != "user" or entry.get("isCompactSummary"):
        return ""
    if entry.get("isMeta") and entry.get("promptSource") != SYSTEM_SOURCE:
        return ""
    text = block_text((entry.get("message") or {}).get("content")).strip()
    return text[:INJECTED_TEXT_LIMIT]


def transcript_activity(transcript: Path) -> list[Activity]:
    """Every assistant message, tool result, typed prompt and injected message of a transcript,
    oldest first: what lib/impact_collect.py turns into busy time, prompts, tokens and what
    triggered each turn."""
    found = []
    with transcript.open(errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            activity = entry_activity(entry) if isinstance(entry, dict) else None
            if activity is not None:
                found.append(activity)
    return sorted(found, key=lambda a: a.at)


QUEUE_DELIVERED = "remove"
MID_TURN_ORIGIN = "mid-turn"


def mid_turn_prompt(entry: dict[str, Any]) -> str | None:
    """A prompt sent while the session was busy and delivered into the running turn: Claude Code
    keeps it only as a `queue-operation` "remove" entry (no prompt entry of its own follows)."""
    if entry.get("type") != "queue-operation" or entry.get("operation") != QUEUE_DELIVERED:
        return None
    text = prompt_text(str(entry.get("content") or ""))
    return text if text and not text.startswith(TASK_NOTIFICATION) else None


def delivered_prompts(transcript: Path) -> list[Prompt]:
    """Every prompt the session received from its user, oldest first: user_prompts, plus those
    delivered into a running turn (origin mid-turn). Tool results, meta entries, the summary after
    /compact and background-task notifications are not prompts; text other sessions or bosun
    put there still is, so callers filter it (lib/approvals.py)."""
    found = user_prompts(transcript)
    with transcript.open(errors="replace") as fh:
        for line in fh:
            if '"queue-operation"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            at = entry_time(entry) if isinstance(entry, dict) else None
            text = mid_turn_prompt(entry) if at is not None else None
            if text and at is not None:
                found.append(Prompt(at, text, MID_TURN_ORIGIN))
    return sorted(found, key=lambda p: p.at)


def tool_summary(name: str, tool_input: object) -> str:
    """A tool call in one short line: a shell command's first line, else its input as JSON."""
    if isinstance(tool_input, dict) and name == "Bash":
        return short(str(tool_input.get("command", "")).strip().splitlines()[0] if tool_input else "")
    if isinstance(tool_input, dict) and name == ASK_USER_TOOL:
        questions = tool_input.get("questions") or []
        return short(" / ".join(str(q.get("question", "")) for q in questions if isinstance(q, dict)))
    return short(json.dumps(tool_input, sort_keys=True))


@dataclass
class ToolCall:
    at: datetime
    name: str
    summary: str


@dataclass
class TranscriptReader:
    """Turns transcript entries into Events between <since> and <until> (aware datetimes)."""

    since: datetime
    until: datetime
    events: list[Event] = field(default_factory=list)
    calls: dict[str, ToolCall] = field(default_factory=dict)
    queued: dict[str, datetime] = field(default_factory=dict)
    denied: set[str] = field(default_factory=set)  # tool calls a hook refused (their result says so again)
    last_text: tuple[datetime, str] | None = None
    tool_calls: int = 0

    def inside(self, moment: datetime | None) -> bool:
        return moment is not None and self.since <= moment <= self.until

    def emit(self, event: Event) -> None:
        if self.inside(event.at):
            self.events.append(event)

    def feed(self, entry: dict[str, Any]) -> None:
        handler = {
            "assistant": self.on_assistant,
            "user": self.on_user,
            "queue-operation": self.on_queue,
            "attachment": self.on_attachment,
            "system": self.on_system,
        }.get(str(entry.get("type")))
        at = entry_time(entry)
        if handler and at is not None:
            handler(entry, at)

    def on_assistant(self, entry: dict[str, Any], at: datetime) -> None:
        for block in (entry.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text", "")).strip():
                self.last_text = (at, str(block["text"]))
            elif block.get("type") == "tool_use":
                self.on_tool_use(block, at)

    def on_tool_use(self, block: dict[str, Any], at: datetime) -> None:
        name = str(block.get("name", ""))
        self.calls[str(block.get("id"))] = ToolCall(at, name, tool_summary(name, block.get("input")))
        if self.inside(at):
            self.tool_calls += 1

    def on_user(self, entry: dict[str, Any], at: datetime) -> None:
        content = (entry.get("message") or {}).get("content")
        results = (
            [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            if isinstance(content, list)
            else []
        )
        for result in results:
            self.on_result(entry, result, at)
        self.on_prompt(entry, at)

    def on_prompt(self, entry: dict[str, Any], at: datetime) -> None:
        prompt = typed_prompt(entry)
        if prompt is None:
            return
        text, origin = prompt
        if origin == "queued":
            # Picked up from the queue when its turn began: not lost after all.
            self.queued = {k: v for k, v in self.queued.items() if prompt_text(k) != text}
        self.emit(Event(at, "prompt", short(text, TEXT_LIMIT), origin=origin))

    def on_result(self, entry: dict[str, Any], result: dict[str, Any], at: datetime) -> None:
        call = self.calls.get(str(result.get("tool_use_id")), ToolCall(at, "?", ""))
        if str(result.get("tool_use_id")) in self.denied:
            return
        text = block_text(result.get("content"))
        waited = (at - call.at).total_seconds()
        decided_by = str((entry.get("permissionDecision") or {}).get("source", ""))
        if text.startswith(USER_REJECTED):
            said = text.split(USER_SAID, 1)[1] if USER_SAID in text else ""
            self.emit(Event(at, "rejected", short(said, TEXT_LIMIT), f"{call.name}: {call.summary}", waited))
        elif call.name == ASK_USER_TOOL:
            self.emit(Event(at, "question", short(text, TEXT_LIMIT), call.summary, waited))
        elif decided_by.startswith("user"):
            self.emit(
                Event(
                    at,
                    "permission",
                    "",
                    f"{call.name}: {call.summary}",
                    waited,
                    failed=bool(result.get("is_error")),
                )
            )
        elif result.get("is_error"):
            self.emit(Event(at, "tool-error", short(text), f"{call.name}: {call.summary}"))
        if call.name == "Bash" and not text.startswith(USER_REJECTED):
            self.emit(Event(call.at, "command", call.summary, "Bash", failed=bool(result.get("is_error"))))

    def on_queue(self, entry: dict[str, Any], at: datetime) -> None:
        operation, content = entry.get("operation"), str(entry.get("content") or "")
        if operation == "enqueue" and not content.startswith(TASK_NOTIFICATION):
            self.queued[content] = at
        elif operation == "remove" and content in self.queued:
            # Delivered into the running turn: it never becomes a prompt entry of its own.
            self.emit(
                Event(
                    self.queued.pop(content),
                    "prompt",
                    short(prompt_text(content), TEXT_LIMIT),
                    origin="mid-turn",
                )
            )
        elif operation == "dequeue":
            self.queued.pop(next(iter(self.queued), ""), None)

    def on_attachment(self, entry: dict[str, Any], at: datetime) -> None:
        attachment = entry.get("attachment") or {}
        if attachment.get("type") == QUEUED_COMMAND:
            self.on_prompt(entry, at)
            return
        if attachment.get("type") != "hook_success" or attachment.get("hookEvent") != "PreToolUse":
            return
        try:
            output = json.loads(str(attachment.get("stdout") or "{}")).get("hookSpecificOutput") or {}
        except (json.JSONDecodeError, AttributeError):
            return
        if output.get("permissionDecision") == "deny":
            call_id = str(attachment.get("toolUseID", ""))
            self.denied.add(call_id)
            name = str(attachment.get("hookName", "")).removeprefix("PreToolUse:")
            call = self.calls.get(call_id)
            tool = f"{name}: {call.summary}" if call else name
            self.emit(Event(at, "hook-deny", short(str(output.get("permissionDecisionReason", ""))), tool))

    def on_system(self, entry: dict[str, Any], at: datetime) -> None:
        subtype = entry.get("subtype")
        if subtype == "turn_duration" and self.last_text:
            reply_at, text = self.last_text
            self.emit(Event(reply_at, "reply", short(text, TEXT_LIMIT)))
            self.last_text = None
        elif subtype == "compact_boundary":
            self.emit(Event(at, "notice", "context compacted"))
        elif entry.get("level") in ("warning", "error"):
            self.emit(Event(at, "notice", short(str(entry.get("content", "")))))

    def finish(self) -> list[Event]:
        for content, at in self.queued.items():
            self.emit(Event(at, "prompt", short(prompt_text(content), TEXT_LIMIT), origin="undelivered"))
        self.queued.clear()
        return sorted(self.events, key=lambda e: e.at)


@dataclass(frozen=True)
class CallResult:
    """A tool call in a transcript and how it ended: <failed> is the tool result's is_error, None
    when the transcript has no result for it."""

    tool_use_id: str
    at: datetime
    name: str
    tool_input: dict[str, Any]
    failed: bool | None
    error: str = ""


ToolFilter = Callable[[str, dict[str, Any]], bool]


def content_blocks(entry: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict) and block.get("type") == kind]


def call_results(transcript: Path, since: datetime, until: datetime, wanted: ToolFilter) -> list[CallResult]:
    """The tool calls <wanted> picks (by name and input) made between <since> and <until> in
    <transcript>, oldest first, each with its result's is_error (a call's result entry may be
    repeated without a timestamp; the first one counts)."""
    calls: list[CallResult] = []
    results: dict[str, tuple[bool, str]] = {}
    for entry in tool_entries(transcript):
        at = entry_time(entry)
        if at is not None and since <= at <= until:
            calls += [call for call in tool_uses(entry, at) if wanted(call.name, call.tool_input)]
        for block in content_blocks(entry, "tool_result"):
            text = short(block_text(block.get("content")))
            results.setdefault(str(block.get("tool_use_id")), (bool(block.get("is_error")), text))
    found = []
    for call in calls:
        failed, text = results.get(call.tool_use_id, (None, ""))
        found.append(replace(call, failed=failed, error=text if failed else ""))
    return sorted(found, key=lambda call: call.at)


def tool_entries(transcript: Path) -> Iterator[dict[str, Any]]:
    """The transcript's entries that may hold a tool call or result."""
    with transcript.open(errors="replace") as fh:
        for line in fh:
            if '"tool_use"' not in line and '"tool_result"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                yield entry


def tool_uses(entry: dict[str, Any], at: datetime) -> list[CallResult]:
    """The tool calls in an assistant entry, without results yet."""
    return [
        CallResult(
            str(block.get("id")),
            at,
            str(block.get("name", "")),
            block["input"] if isinstance(block.get("input"), dict) else {},
            None,
        )
        for block in content_blocks(entry, "tool_use")
    ]


def read_events(transcript: Path, since: datetime, until: datetime) -> tuple[list[Event], int]:
    """The events of <transcript> between <since> and <until>, and how many tool calls it made."""
    reader = TranscriptReader(since, until)
    with transcript.open(errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                reader.feed(entry)
    return reader.finish(), reader.tool_calls


# ---- failed requests and the login (read by lib/login.py) -------------------------------------
#
# The text of an error reply ("Login expired · Please run /login") is for people and changes
# between versions; the category comes from the structured fields. Only an entry flagged as an
# error that carries none of them falls back to a loose reading of its text.

AUTH = "auth"
# An individual spend limit or a plan's usage limit: every request fails until it is raised or
# resets ("You've hit your individual spend limit", error rate_limit, apiError usage_limit_reached,
# HTTP 429 on 2.1.30x). Not a short rate limit, so it is its own category.
USAGE_LIMIT = "usage-limit"
RATE_LIMIT = "rate-limit"
OVERLOADED = "overloaded"
NETWORK = "network"
OTHER = "other"
STRUCTURED = "structured"
PATTERN = "pattern"
SERVER_ERROR = "server_error"
REPLY_MARKER = b'"assistant"'


@dataclass(frozen=True)
class ErrorRule:
    """A category, and the values of `error`, `apiError` and the HTTP status that mean it."""

    category: str
    errors: frozenset[str] = frozenset()
    api_errors: frozenset[str] = frozenset()
    statuses: frozenset[int] = frozenset()

    def matches(self, code: str | None, api_error: str | None, status: int | None) -> bool:
        return code in self.errors or api_error in self.api_errors or status in self.statuses


ERROR_RULES = (
    ErrorRule(
        AUTH,
        frozenset({"authentication_failed", "oauth_org_not_allowed"}),
        frozenset({"org_disabled_credential", "api_key_auth_disabled", "provider_credentials"}),
        frozenset({401}),
    ),
    ErrorRule(USAGE_LIMIT, api_errors=frozenset({"usage_limit_reached"})),
    ErrorRule(RATE_LIMIT, frozenset({"rate_limit"}), statuses=frozenset({429})),
    ErrorRule(OVERLOADED, frozenset({"overloaded"}), frozenset({"overloaded"}), frozenset({529})),
    ErrorRule(NETWORK, api_errors=frozenset({"no_response"})),
)

# The fallback: a short message that reads like a missing, expired or refused login. Loose on
# purpose (phrases, not one version's wording), so it is used only where no structured field exists.
AUTH_ERROR_TEXT = re.compile(
    r"""
    please\s+(?:run\s+)?/?log\s?in\b
    | \bnot\s+logged\s+in\b
    | \blog(?:\s?in|ged\s+in)\s+(?:has\s+|is\s+)?(?:expired|required|failed|invalid)
    | \b(?:oauth|access|auth|refresh|api)\s+(?:token|key)\s+(?:has\s+(?:been\s+)?|was\s+|is\s+)?
      (?:revoked|expired|invalid)
    | \binvalid\s+(?:api\s+key|credentials|(?:oauth|access|bearer)\s+token)
    | \bauthentication\s+(?:failed|error|required)
    | \b401\b.{0,40}\bunauthori[sz]ed | \bunauthori[sz]ed\b.{0,40}\b401\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
FALLBACK_MAX_CHARS = 300
PANE_LINES = 12
CODE_FENCE = "```"
UI_GLYPHS = re.compile(r"^[\W_]+")


def looks_like_auth_error(text: str) -> bool:
    """Whether <text> reads like a login error: short, no code, and a login-failure phrase."""
    text = " ".join(text.split())
    return (
        0 < len(text) <= FALLBACK_MAX_CHARS and CODE_FENCE not in text and bool(AUTH_ERROR_TEXT.search(text))
    )


def pane_auth_error(pane: str) -> bool:
    """Whether one of the last lines shown in a window reads like a login error (the fallback when
    there is no transcript to read)."""
    lines = [UI_GLYPHS.sub("", line).strip() for line in pane.splitlines()]
    return any(looks_like_auth_error(line) for line in [line for line in lines if line][-PANE_LINES:])


def text_field(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def status_field(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def error_category(code: object, api_error: object, status: object) -> str | None:
    """The category of a failed request from its structured fields, or None if it has none."""
    code, api_error, status = text_field(code), text_field(api_error), status_field(status)
    if code is None and api_error is None and status is None:
        return None
    rule = next((r for r in ERROR_RULES if r.matches(code, api_error, status)), None)
    if rule:
        return rule.category
    # A server error with no HTTP status: the connection dropped, or the Mac slept mid-response.
    return NETWORK if code == SERVER_ERROR and status is None else OTHER


@dataclass(frozen=True)
class ApiError:
    category: str  # auth, usage-limit, rate-limit, overloaded, network or other
    source: str  # structured (from the fields) or pattern (the fallback over the text)
    code: str  # the structured fields, e.g. "authentication_failed/401", for logs; never the text
    at: datetime | None = None  # the latest such reply
    since: datetime | None = None  # the first of the replies in a row with this category


def error_code(*values: object) -> str:
    return "/".join(str(v) for v in values if v not in (None, "")) or "unclassified"


def is_reply(entry: dict[str, Any]) -> bool:
    """An assistant entry of the main conversation (not a subagent's)."""
    return entry.get("type") == "assistant" and not entry.get("isSidechain")


def reply_error(entry: dict[str, Any], status: object = None) -> ApiError | None:
    """The error a reply reports, or None for a normal reply. <status>: the HTTP status, when it
    comes from elsewhere (a headless run's result)."""
    if entry.get("isApiErrorMessage") is not True and text_field(entry.get("error")) is None:
        return None
    code, api_error = entry.get("error"), entry.get("apiError")
    status = entry.get("apiErrorStatus", status)
    category = error_category(code, api_error, status)
    if category:
        return ApiError(category, STRUCTURED, error_code(code, api_error, status))
    text = block_text((entry.get("message") or {}).get("content"))
    return ApiError(AUTH if looks_like_auth_error(text) else OTHER, PATTERN, "unclassified")


TAIL_CHUNK_BYTES = 1 << 16


def reversed_lines(path: Path) -> Iterator[bytes]:
    """The lines of <path>, last first, read from the end in chunks (transcripts grow large)."""
    with path.open("rb") as fh:
        position = fh.seek(0, os.SEEK_END)
        rest = b""
        while position > 0:
            size = min(TAIL_CHUNK_BYTES, position)
            position -= size
            fh.seek(position)
            lines = (fh.read(size) + rest).split(b"\n")
            rest = lines.pop(0)
            yield from reversed(lines)
        yield rest


def replies_last_first(transcript: Path, sidechain: bool = False) -> Iterator[dict[str, Any]]:
    """The session's replies, newest first; <sidechain>: a subagent's transcript, whose every entry
    is a sidechain one."""
    for line in reversed_lines(transcript):
        if REPLY_MARKER not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and (is_reply(entry) or (sidechain and entry.get("type") == "assistant")):
            yield entry


def last_api_error(transcript: Path, sidechain: bool = False) -> ApiError | None:
    """The error the session's latest reply reports, or None if that reply is a normal one (or there
    is none yet). `since` is the first of the error replies of that category in a row, so it stays
    put while every new prompt fails the same way; any normal reply ends the row. <sidechain>: a
    subagent's transcript."""
    latest: ApiError | None = None
    for entry in replies_last_first(transcript, sidechain):
        error = reply_error(entry)
        if error is None or (latest is not None and error.category != latest.category):
            break
        at = entry_time(entry)
        latest = replace(error, at=at, since=at) if latest is None else replace(latest, since=at)
    return latest


CONTEXT_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
CONTEXT_SCAN_REPLIES = 5000  # how far back a context run is followed


@dataclass(frozen=True)
class ContextRun:
    """A conversation that has re-read at least a threshold of tokens a call: how much its latest
    reply re-read, and when the run of replies over the threshold began (as far back as read)."""

    tokens: int
    since: datetime


def reply_context(entry: dict[str, Any]) -> int:
    """What a reply's call re-read: its input, cache reads and cache writes."""
    usage = (entry.get("message") or {}).get("usage") or {}
    return sum(int(usage.get(key) or 0) for key in CONTEXT_FIELDS)


def context_run(transcript: Path, threshold: int) -> ContextRun | None:
    """The run of the session's latest replies that each re-read <threshold> tokens or more, if its
    latest reply did; a smaller reply (after /compact, say) ends the run."""
    latest: int | None = None
    since: datetime | None = None
    for scanned, entry in enumerate(replies_last_first(transcript)):
        tokens = reply_context(entry)
        if not tokens:  # an error reply or a notice: no call
            continue
        if tokens < threshold or scanned >= CONTEXT_SCAN_REPLIES:
            break
        latest = tokens if latest is None else latest
        since = entry_time(entry) or since
    return ContextRun(latest, since) if latest is not None and since is not None else None


def last_reply_at(transcript: Path) -> datetime | None:
    """When the session last replied (normally or with an error)."""
    return next((entry_time(entry) for entry in replies_last_first(transcript)), None)


@dataclass(frozen=True)
class AuthStatus:
    logged_in: bool | None  # None: no answer that could be read
    method: str = ""
    plan: str = ""  # subscriptionType: pro, max, team, enterprise; empty for an API key


AUTH_STATUS_TIMEOUT_SECONDS = 30


def parse_auth_status(text: str) -> AuthStatus:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return AuthStatus(None)
    if not isinstance(data, dict) or not isinstance(data.get("loggedIn"), bool):
        return AuthStatus(None)
    return AuthStatus(
        data["loggedIn"], str(data.get("authMethod") or ""), str(data.get("subscriptionType") or "")
    )


def auth_status(claude_bin: str) -> AuthStatus:
    """`claude auth status --json`. Free and quick, but it only sees whether credentials are
    stored: it exits 1 with loggedIn false once they are gone, and still says loggedIn true for a
    token the server has revoked."""
    try:
        result = subprocess.run(
            [claude_bin, "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=AUTH_STATUS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return AuthStatus(None)
    return parse_auth_status(result.stdout)


# The login check request: the smallest real request a headless Claude makes (a small model, no
# tools, settings, MCP servers or transcript), about $0.002. It runs in an empty folder.
PROBE_MODEL = "haiku"
PROBE_BUDGET_USD = "0.05"
PROBE_TIMEOUT_SECONDS = 120
PROBE_PROMPT = "Reply with the single word OK."
PROBE_OK = "ok"
PROBE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    outcome: str  # ok, unknown (no structured answer: e.g. it could not start), or an error category
    source: str = STRUCTURED
    code: str = ""
    cost_usd: float = 0.0


def probe_command(claude_bin: str) -> list[str]:
    return [
        claude_bin,
        "-p",
        "--model",
        PROBE_MODEL,
        "--max-budget-usd",
        PROBE_BUDGET_USD,
        "--no-session-persistence",
        "--system-prompt",
        PROBE_PROMPT,
        "--tools",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources",
        "",
        "--output-format",
        "stream-json",
        "--verbose",
        "ping",
    ]


def read_stream(lines: Iterable[str]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """The assistant messages and the (last) result of a headless run's stream-json output."""
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    results = [e for e in entries if e.get("type") == "result"]
    return [e for e in entries if e.get("type") == "assistant"], (results[-1] if results else None)


def parse_probe(lines: Iterable[str]) -> ProbeResult:
    """A headless run's stream-json output: its error reply, else its result. Output without either
    (it failed before any request, for example while loading settings) is unknown: such failures can
    mention logging in for reasons that have nothing to do with the login."""
    replies, result = read_stream(lines)
    if result is None:
        return ProbeResult(PROBE_UNKNOWN, code="no result")
    cost = float(result.get("total_cost_usd") or 0)
    status = result.get("api_error_status")
    error = next((e for e in (reply_error(r, status) for r in reversed(replies)) if e), None)
    if error is None and result.get("is_error"):
        error = result_error(result, status)
    if error:
        return ProbeResult(error.category, error.source, error.code, cost)
    return ProbeResult(PROBE_OK, cost_usd=cost)


def result_error(result: dict[str, Any], status: object) -> ApiError:
    """A failed headless run without an error reply: its HTTP status, else its result text."""
    category = error_category(None, None, status)
    if category:
        return ApiError(category, STRUCTURED, error_code(status))
    text = str(result.get("result") or "")
    return ApiError(AUTH if looks_like_auth_error(text) else OTHER, PATTERN, "unclassified")


def probe(claude_bin: str) -> ProbeResult:
    """Make the login check request (it costs a fraction of a cent; see PROBE_*)."""
    with tempfile.TemporaryDirectory(prefix="bosun-login-") as scratch:
        try:
            result = subprocess.run(
                probe_command(claude_bin),
                cwd=scratch,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ProbeResult(PROBE_UNKNOWN, code=type(exc).__name__)
    return parse_probe(result.stdout.splitlines())


# ---- memory ------------------------------------------------------------------------------------

MEMORY_INDEX = "MEMORY.md"


def memory_dir(workdir: str) -> Path:
    return project_dir(workdir) / "memory"


def add_memory_note(workdir: str, slug: str, description: str, body: str) -> Path:
    """Write a memory note for Claude Code sessions in <workdir> and list it in MEMORY.md. Returns
    the note's path. An existing note of that name is left alone (the caller picks a new name)."""
    directory = memory_dir(workdir)
    directory.mkdir(parents=True, exist_ok=True)
    note = directory / f"{slug}.md"
    if note.exists():
        raise FileExistsError(note)
    front = json.dumps(description)
    note.write_text(
        f"---\nname: {slug}\ndescription: {front}\nmetadata:\n  node_type: memory\n  type: feedback\n---\n\n"
        f"{body.strip()}\n"
    )
    index = directory / MEMORY_INDEX
    existing = index.read_text() if index.exists() else "# Memory Index\n"
    index.write_text(existing.rstrip("\n") + f"\n- [{description}]({note.name})\n")
    return note


# ---- command line ------------------------------------------------------------------------------


def emit(value: object) -> int:
    """Print <value> and exit 0, or exit NOT_FOUND if there is none."""
    if value is None:
        return NOT_FOUND
    print(value)
    return 0


def cmd_resolve(args: list[str]) -> int:
    session_id = resolve(args[0], args[1])
    if session_id is None:
        print(f"no session named {args[1]!r} under {project_dir(args[0])}", file=sys.stderr)
    return emit(session_id)


def cmd_list(args: list[str]) -> int:
    for _, session_id, title in named_sessions(args[0]):
        print(f"{session_id}  {title}")
    return 0


def cmd_registered(args: list[str]) -> int:
    return emit(next((pid for pid in args if (LIVE_SESSIONS_DIR / f"{pid}.json").is_file()), None))


def describe(pid: str) -> dict[str, Any] | None:
    """What `bosun sessions` shows of a running Claude: its record's fields (statusUpdatedAt: when
    its status last changed, epoch milliseconds) and transcript."""
    entry = record(pid)
    if entry is None:
        return None
    keys = ("pid", "sessionId", "name", "status", "statusUpdatedAt", "version", "cwd")
    described = {key: entry.get(key) for key in keys}
    path = transcript_of(entry)
    described["transcript"] = str(path) if path else None
    return described


def transcript_of(entry: dict[str, Any]) -> Path | None:
    """The transcript of a running Claude, from its record (cwd and sessionId), if it exists."""
    cwd, session_id = entry.get("cwd"), entry.get("sessionId")
    path = transcript_path(cwd, session_id) if isinstance(cwd, str) and isinstance(session_id, str) else None
    return path if path and path.is_file() else None


CONTEXT_TAIL_BYTES = 4 * 1024 * 1024  # the latest usage is near the end; transcripts grow to GBs


def context_tokens(transcript: Path) -> int | None:
    """The size of a conversation as its latest assistant turn read it: input plus cache reads
    and writes (a synthetic error reply carries no usage and is skipped)."""
    try:
        with transcript.open("rb") as handle:
            handle.seek(max(0, transcript.stat().st_size - CONTEXT_TAIL_BYTES))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        usage = message_usage(line)
        if usage:
            keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            return sum(int(usage.get(key) or 0) for key in keys)
    return None


def message_usage(line: str) -> dict[str, Any] | None:
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    message = entry.get("message") if isinstance(entry, dict) and entry.get("type") == "assistant" else None
    usage = message.get("usage") if isinstance(message, dict) else None
    return usage if isinstance(usage, dict) and any(usage.values()) else None


def cmd_context(args: list[str]) -> int:
    entry = record(args[0])
    path = transcript_of(entry) if entry else None
    return emit(context_tokens(path) if path else None)


def cmd_describe(args: list[str]) -> int:
    described = describe(args[0])
    return emit(json.dumps(described) if described else None)


def cmd_logged_in(_args: list[str]) -> int:
    return 0 if auth_status(os.environ.get("BOSUN_CLAUDE_BIN") or "claude").logged_in is True else NOT_FOUND


def cmd_set_trust(args: list[str]) -> int:
    if args[1] not in ("true", "false"):
        print(__doc__.strip(), file=sys.stderr)
        return USAGE
    set_trust(args[0], args[1] == "true")
    return 0


def cmd_prompts(args: list[str]) -> int:
    """The prompts typed into a transcript (also while it was busy), as JSON lines."""
    since = datetime.fromisoformat(args[1]) if len(args) > 1 else None
    for prompt in user_prompts(Path(args[0]), since):
        print(json.dumps({"at": prompt.at.isoformat(), "origin": prompt.origin, "text": prompt.text}))
    return 0


def cmd_api_error(args: list[str]) -> int:
    """The error the latest reply in a transcript reports, as JSON; exit 3 for a normal reply."""
    error = last_api_error(Path(args[0]))
    if error is None:
        return NOT_FOUND
    times = {"at": error.at, "since": error.since}
    stamps = {key: moment.isoformat() if moment else None for key, moment in times.items()}
    print(json.dumps({"category": error.category, "source": error.source, "code": error.code, **stamps}))
    return 0


# subcommand -> (number of arguments, or None for any; handler)
COMMANDS: dict[str, tuple[int | None, Callable[[list[str]], int]]] = {
    "resolve": (2, cmd_resolve),
    "list": (1, cmd_list),
    "live": (1, lambda args: emit(live_pid(args[0]))),
    "registered": (None, cmd_registered),
    "field": (2, lambda args: emit((record(args[0]) or {}).get(args[1]))),
    "describe": (1, cmd_describe),
    "trusted": (1, lambda args: 0 if trusted(args[0]) else NOT_FOUND),
    "onboarded": (0, lambda _args: 0 if onboarded() else NOT_FOUND),
    "bypass-accepted": (0, lambda _args: 0 if bypass_accepted() else NOT_FOUND),
    "logged-in": (0, cmd_logged_in),
    "set-trust": (2, cmd_set_trust),
    "prompts": (None, cmd_prompts),
    "api-error": (1, cmd_api_error),
    "context": (1, cmd_context),
}


def main(argv: list[str]) -> int:
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    command = COMMANDS.get(argv[0]) if argv else None
    if command is None or (command[0] is not None and len(argv) - 1 != command[0]):
        print(__doc__.strip(), file=sys.stderr)
        return USAGE
    return command[1](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
