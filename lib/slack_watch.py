#!/usr/bin/env python3
"""Slack watcher: new Slack activity for me, pushed to the orchestrator as events (design:
docs/design/events.md, "Slack watcher").

Usage: slack_watch.py run [--scheduled] [--dry-run]    (run it as `bosun slack-watch`)
       slack_watch.py status

One run is one headless `claude -p` with a small model (BOSUN_SLACK_WATCH_MODEL) that may use
only Slack's read tools: no built-in tools, no other MCP server, no settings files, the write tools
removed and anything not allowed denied without a prompt. It lists the messages since the last
watch (with a few minutes of overlap) that @-mention or name me, DMs and group DMs to me, and
replies in threads I started or replied in, as strict JSON: permalink, channel, ts, thread_ts,
author, kind and a short excerpt. Slack text is untrusted, so the excerpt is all of it that is
kept: redacted, at most EXCERPT_LENGTH characters, labelled untrusted in the event.

Code, not the model, then checks every entry, drops messages the ledger has processed (`ledger
seen`, matched by channel and ts), messages in the conversation of open outreach (step 5 of the
chores handles those) and messages already queued, and queues one `slack` event per new message
for the dispatcher (lib/events.py). It never posts and never writes the ledger.

--scheduled (the check-in loop, every tick) runs only with BOSUN_SLACK_WATCH_ENABLED=1, inside
the awake window, BOSUN_SLACK_WATCH_MINUTES after the last run started; every run needs
BOSUN_EVENTS_ENABLED=1 (nothing else would deliver what it finds), no other run going, and
BOSUN_SLACK_WATCH_MAX_USD_PER_CALL left of BOSUN_SLACK_WATCH_MAX_USD_PER_DAY. --dry-run
calls Claude and prints what a run would queue; it queues nothing and writes no state. State:
$BOSUN_STATE_DIR/slack-watch/state.json. BOSUN_SLACK_WATCH_NOW (epoch seconds) pins the
clock for tests.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import awake
import claude_state
import event_model
import events
import ledger
import redact


PROMPT_FILE = Path(__file__).resolve().parent.parent / "orchestrator" / "slack-watch.md"
SUBDIR = "slack-watch"
STATE_FILE = "state.json"
STATE_LOCK = "state.lock"
RUN_LOCK = "run.lock"
MCP_FILE = "mcp.json"
ORIGIN = "slack-watch"
EVENT_TYPE = "slack"
# Each watch scans from where the last successful one started, minus this overlap (search indexing
# lags a little; `ledger seen` and the queued list make repeats harmless). The first scans two
# intervals back; never before the last chores run's scan, nor further back than
# BOSUN_SLACK_LOOKBACK_HOURS (the chores cover that).
OVERLAP = timedelta(minutes=5)
FIRST_SCAN_INTERVALS = 2
TIMEOUT_SECONDS = 300
MAX_MESSAGES = 50
EXCERPT_LENGTH = 120
AUTHOR_LENGTH = 40
ERROR_LENGTH = 300
ERROR_TEXT_LENGTH = 160  # a failed call's result text and stderr, in its error
SUCCESS_SUBTYPE = "success"
# A message's ts may be a little before the scan start (search granularity, clock skew).
TS_TOLERANCE = timedelta(minutes=1)
# The watchdog alerts after this many failed runs in a row.
MAX_FAILURES = 3
RETENTION_DAYS = 7
RUNS_KEPT = 20
NOT_DUE = "not due"
KINDS = ("mention", "dm", "thread-reply")
# The Slack MCP tools (by name, without the server's prefix) the call may use, and those it never
# sees. Anything else the server offers is denied by --permission-mode dontAsk.
READ_TOOLS = (
    "slack_search_public_and_private",
    "slack_search_public",
    "slack_read_thread",
    "slack_read_user_profile",
    "slack_read_channel",
)
DENIED_TOOLS = (
    "slack_send_message",
    "slack_send_message_draft",
    "slack_schedule_message",
    "slack_add_reaction",
    "slack_create_canvas",
    "slack_update_canvas",
    "slack_create_conversation",
)
# How --json-schema returns the JSON: the one tool besides Slack's that the call may see.
OUTPUT_TOOL = "StructuredOutput"
# A tool whose name has one of these words writes; if the call can see one, its result is refused.
WRITE_WORDS = frozenset(
    {
        "send", "schedule", "post", "create", "update", "delete", "add", "remove", "upload", "invite",
        "join", "leave", "archive", "set", "edit", "draft", "write", "kick", "rename", "pin", "unpin",
        "mark",
    }
)  # fmt: skip
PERMALINK = re.compile(
    r"^https://[a-z0-9-]+\.slack\.com/archives/(?P<channel>[A-Z0-9]+)/p(?P<digits>\d{16})"
    r"(?:\?[A-Za-z0-9_.=&%-]*)?$"
)
SLACK_TS = re.compile(r"^\d{10}\.\d{6}$")
TS_DECIMALS = 6
CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["messages", "truncated"],
    "properties": {
        "messages": {
            "type": "array",
            "maxItems": MAX_MESSAGES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["permalink", "channel", "ts", "thread_ts", "author", "kind", "excerpt"],
                "properties": {
                    "permalink": {"type": "string"},
                    "channel": {"type": "string"},
                    "ts": {"type": "string"},
                    "thread_ts": {"type": "string"},
                    "author": {"type": "string"},
                    "kind": {"enum": list(KINDS)},
                    "excerpt": {"type": "string", "maxLength": EXCERPT_LENGTH},
                },
            },
        },
        "truncated": {"type": "boolean"},
    },
}


class WatchError(Exception):
    """A failed watch; <cost> is what its call cost, if it got that far (it counts to the budget)."""

    def __init__(self, message: str, cost: float = 0.0) -> None:
        super().__init__(message)
        self.cost = cost


Clock = Callable[[], datetime]


# ---- settings and state --------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    state_dir: Path
    awake: awake.AwakeWindow
    enabled: bool = False
    events_enabled: bool = False
    minutes: float = 15
    model: str = "haiku"
    max_usd_per_call: float = 0.25
    max_usd_per_day: float = 1.5
    lookback_hours: float = 24
    mcp_config: str = ""  # a site's MCP config file with only its Slack server; "": the Slack plugin's
    # Authors (display names, lower case) whose messages are never pushed: bots that post status
    # updates. The check-in's Slack scan still reads them.
    quiet_authors: frozenset[str] = frozenset()
    claude_bin: str = "claude"

    @property
    def directory(self) -> Path:
        return self.state_dir / SUBDIR

    @property
    def interval(self) -> timedelta:
        return timedelta(minutes=self.minutes)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        def text(name: str, default: str = "") -> str:
            return env.get(f"BOSUN_{name}", default)

        def number(name: str, default: float) -> float:
            try:
                return float(text(name) or default)
            except ValueError as exc:
                raise WatchError(f"BOSUN_{name} must be a number, got {text(name)!r}") from exc

        if not text("STATE_DIR"):
            raise WatchError("BOSUN_STATE_DIR is not set; run this as `bosun slack-watch`")
        try:
            window = awake.AwakeWindow(
                text("DAYS", "MTWRF"),
                awake.parse_hhmm(text("START_TIME", "08:00")),
                awake.parse_hhmm(text("AWAKE_UNTIL", "20:00")),
            )
        except ValueError as exc:
            raise WatchError("BOSUN_START_TIME and BOSUN_AWAKE_UNTIL must be HH:MM") from exc
        return cls(
            state_dir=Path(text("STATE_DIR")),
            awake=window,
            enabled=text("SLACK_WATCH_ENABLED") == "1",
            events_enabled=text("EVENTS_ENABLED") == "1",
            minutes=number("SLACK_WATCH_MINUTES", 15),
            model=text("SLACK_WATCH_MODEL") or "haiku",
            max_usd_per_call=number("SLACK_WATCH_MAX_USD_PER_CALL", 0.25),
            max_usd_per_day=number("SLACK_WATCH_MAX_USD_PER_DAY", 1.5),
            lookback_hours=number("SLACK_LOOKBACK_HOURS", 24),
            mcp_config=text("SLACK_WATCH_MCP_CONFIG"),
            quiet_authors=author_set(text("SLACK_WATCH_QUIET_AUTHORS")),
            claude_bin=text("CLAUDE_BIN") or "claude",
        )


def author_set(names: str) -> frozenset[str]:
    """A comma-separated list of display names, compared case-insensitively."""
    return frozenset(n.strip().casefold() for n in names.split(",") if n.strip())


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def local_date(moment: datetime) -> date:
    return moment.astimezone().date()


@dataclass
class State:
    watermark: str | None = None  # where the last successful scan ended (when it started)
    last_check: str | None = None  # the scheduler's last look (the watchdog's stall check)
    last_started: str | None = None
    last_reason: str = ""  # why the scheduler last skipped (logged when it changes)
    failures: int = 0  # failed runs in a row
    failing_since: str | None = None
    last_error: dict[str, str] | None = None
    budget_spent_on: str | None = None  # the local day the daily budget ran out
    spend: dict[str, float] = field(default_factory=dict)  # local day -> USD
    queued: dict[str, str] = field(default_factory=dict)  # channel/ts -> when it was queued
    runs: list[dict[str, Any]] = field(default_factory=list)  # newest last

    @classmethod
    def load(cls, directory: Path) -> State:
        try:
            data = json.loads((directory / STATE_FILE).read_text())
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as exc:
            raise WatchError(f"unreadable {directory / STATE_FILE} ({exc}); move it aside") from exc
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))
        tmp.replace(directory / STATE_FILE)

    def spent_on(self, day: date) -> float:
        return self.spend.get(day.isoformat(), 0.0)

    def forget_old(self, now: datetime) -> None:
        cutoff = now - timedelta(days=RETENTION_DAYS)
        self.queued = {k: v for k, v in self.queued.items() if datetime.fromisoformat(v) >= cutoff}
        self.spend = {k: v for k, v in self.spend.items() if k >= local_date(cutoff).isoformat()}
        self.runs = self.runs[-RUNS_KEPT:]


@contextlib.contextmanager
def locked_state(directory: Path) -> Iterator[State]:
    """The state, read and written under a lock (a run and the scheduler may overlap)."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / STATE_LOCK).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = State.load(directory)
        yield state
        state.save(directory)


@contextlib.contextmanager
def run_lock(directory: Path) -> Iterator[bool]:
    """Held for a whole run; yields False if another run holds it."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / RUN_LOCK).open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


# ---- when to run ---------------------------------------------------------------------------------


def schedule_gate(config: Config, state: State, now: datetime) -> str | None:
    """Why a scheduled run does not happen now (NOT_DUE: quietly), or None."""
    if not config.awake.contains(now.astimezone().replace(tzinfo=None)):
        return "outside the awake window"
    started = state.last_started
    if started and now - datetime.fromisoformat(started) < config.interval:
        return NOT_DUE
    return None


def budget_gate(config: Config, state: State, now: datetime) -> str | None:
    spent = state.spent_on(local_date(now))
    if spent + config.max_usd_per_call > config.max_usd_per_day:
        return (
            f"the daily budget is spent (${spent:.2f} of ${config.max_usd_per_day:.2f} today, "
            f"${config.max_usd_per_call:.2f} a call)"
        )
    return None


def run_gate(config: Config, state: State, now: datetime) -> str | None:
    """Why no run may happen now, scheduled or not."""
    if not config.events_enabled:
        return "events are off (BOSUN_EVENTS_ENABLED=0): nothing would deliver what it finds"
    return budget_gate(config, state, now)


def scan_start(config: Config, state: State, now: datetime, chores: datetime | None = None) -> datetime:
    """From the last successful scan, minus the overlap; the first scans two intervals back.
    Never earlier than the last chores scan (<chores>: they processed what came before) or
    BOSUN_SLACK_LOOKBACK_HOURS."""
    if state.watermark:
        start = datetime.fromisoformat(state.watermark) - OVERLAP
    else:
        start = now - FIRST_SCAN_INTERVALS * config.interval
    return max(start, chores or start, now - timedelta(hours=config.lookback_hours))


def chores_scan(book: ledger.Ledger) -> datetime | None:
    """Where the last chores run (morning or check-in) started scanning Slack, roughly: its start
    minus the check-in overlap."""
    runs = [ledger.parse_time(at) for name, at in book["runs"].items() if name in ledger.SCAN_RUNS]
    return max(runs) - ledger.CHECKIN_OVERLAP if runs else None


# ---- the Slack MCP server and the call ------------------------------------------------------------


def server_prefix(name: str) -> str:
    """The prefix of an MCP server's tool names, as Claude Code builds it."""
    return "mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", name) + "__"


def mcp_document(config: Config) -> dict[str, Any]:
    """The --mcp-config document: only the Slack server (a site's file, or the Slack plugin's)."""
    if config.mcp_config:
        try:
            document: dict[str, Any] = json.loads(Path(config.mcp_config).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise WatchError(f"cannot read BOSUN_SLACK_WATCH_MCP_CONFIG ({exc})") from exc
    else:
        found = claude_state.slack_plugin_mcp_config()
        if found is None:
            raise WatchError(
                "no Slack MCP server: install Claude Code's Slack plugin, or point "
                "BOSUN_SLACK_WATCH_MCP_CONFIG at an MCP config file with only your Slack server"
            )
        document = found
    servers = document.get("mcpServers")
    if not isinstance(servers, dict) or len(servers) != 1:
        raise WatchError("the Slack watcher's MCP config must hold exactly one server (Slack)")
    return document


def claude_command(config: Config, mcp_file: Path, prefix: str, prompt: str) -> list[str]:
    """One headless Claude that may use only Slack's read tools: no built-in tools, only the Slack
    MCP server, no settings files (so no allow rules or hooks of mine), the write tools removed,
    and anything else denied without a prompt."""
    return [
        config.claude_bin, "-p", "--model", config.model,
        "--setting-sources", "", "--strict-mcp-config", "--mcp-config", str(mcp_file), "--tools", "",
        "--allowedTools", *(prefix + tool for tool in READ_TOOLS),
        "--disallowedTools", *(prefix + tool for tool in DENIED_TOOLS),
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--no-session-persistence", "--output-format", "stream-json", "--verbose",
        "--max-budget-usd", f"{config.max_usd_per_call:g}", "--json-schema", json.dumps(SCHEMA),
        "--system-prompt", prompt,
    ]  # fmt: skip


@dataclass
class CallResult:
    tools: list[str]  # what the call could see (its init message)
    result: dict[str, Any]  # the final result message
    cost: float = 0.0
    calls: list[str] = field(default_factory=list)  # the tool calls it made, with their search filters
    error: str = ""  # an error reply's category and code (e.g. usage-limit rate_limit/...), if any
    returncode: int = 0
    stderr: str = ""


# What a dry run shows of each tool call: the search arguments, never message text.
SHOWN_ARGUMENTS = ("filters", "keywords", "after", "cursor")


def tool_calls(message: dict[str, Any]) -> list[str]:
    """The tool calls in an assistant message, as "<tool> <search arguments>"."""
    content = (message.get("message") or {}).get("content")
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    return [
        f"{str(block.get('name', '')).split('__')[-1]} {json.dumps(shown_arguments(block.get('input')))}"
        for block in blocks
        if block.get("type") == "tool_use"
    ]


def shown_arguments(arguments: object) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        return {}
    return {k: arguments[k] for k in SHOWN_ARGUMENTS if k in arguments}


def stream_messages(stdout: str) -> Iterator[dict[str, Any]]:
    """The JSON objects in stream-json output, one per line; anything else is skipped."""
    for line in stdout.splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            message = json.loads(line)
            if isinstance(message, dict):
                yield message


def read_stream(stdout: str) -> CallResult:
    """The init tools, the tool calls and the result from stream-json output. Nothing else is
    kept: the tool results in between hold Slack text."""
    tools: list[str] = []
    calls: list[str] = []
    replies: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    for message in stream_messages(stdout):
        kind = message.get("type")
        if kind == "system" and message.get("subtype") == "init":
            tools = [str(t) for t in message.get("tools") or []]
        elif kind == "assistant":
            calls += tool_calls(message)
            replies.append(message)
        elif kind == "result":
            result = message
    if result is None:
        raise WatchError("claude returned no result")
    cost = result.get("total_cost_usd")
    error = reply_error_code(replies, result.get("api_error_status"))
    return CallResult(tools, result, float(cost) if isinstance(cost, (int, float)) else 0.0, calls, error)


def reply_error_code(replies: list[dict[str, Any]], status: object) -> str:
    """The latest error reply's category and structured code ("usage-limit rate_limit/..."), or ""."""
    error = next((e for e in (claude_state.reply_error(r, status) for r in reversed(replies)) if e), None)
    return f"{error.category} {error.code}" if error else ""


def failure_detail(outcome: CallResult) -> str:
    """Why a call whose result is an error failed: its error reply's category and code, the
    result's subtype (unless "success", which a failed call can also report), its short text, and
    claude's exit status and stderr."""
    result = outcome.result
    parts = [outcome.error] if outcome.error else []
    subtype = str(result.get("subtype") or "")
    if subtype and subtype != SUCCESS_SUBTYPE:
        parts.append(subtype)
    text = clean(result.get("result") or "", ERROR_TEXT_LENGTH)
    if text:
        parts.append(f'"{text}"')
    if outcome.returncode:
        parts.append(f"exit {outcome.returncode}")
    if outcome.stderr.strip():
        parts.append(f"stderr: {clean(outcome.stderr[-ERROR_LENGTH:], ERROR_TEXT_LENGTH)}")
    return "; ".join(parts) or "the result gives no detail"


def tool_problem(tools: list[str], prefix: str) -> str | None:
    """Why the tools the call could see break the restriction, or None."""
    for tool in tools:
        words = set(tool.removeprefix(prefix).split("_"))
        if tool == OUTPUT_TOOL:
            continue
        if not tool.startswith(prefix):
            return f"the call could use {tool}, which is not a Slack tool"
        if words & WRITE_WORDS:
            return f"the call could see the write tool {tool}"
    return None


def call(config: Config, document: dict[str, Any], since: datetime, workdir: Path) -> CallResult:
    """Run the watch call in <workdir> (it holds the MCP config file, nothing else)."""
    mcp_file = workdir / MCP_FILE
    mcp_file.write_text(json.dumps(document))
    prefix = server_prefix(next(iter(document["mcpServers"])))
    message = (
        f"Scan start: {iso(since)} (Unix time {int(since.timestamp())}). "
        f"Now: {iso(datetime.now(UTC))}. List the new Slack activity for me since the scan start."
    )
    command = claude_command(config, mcp_file, prefix, prompt_text())
    try:
        done = subprocess.run(
            command,
            input=message,
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WatchError(f"the watch call took longer than {TIMEOUT_SECONDS}s") from exc
    if done.returncode != 0 and '"type":"result"' not in done.stdout.replace(" ", ""):
        raise WatchError(
            f"claude exited {done.returncode}: {(done.stderr or done.stdout).strip()[-ERROR_LENGTH:]}"
        )
    outcome = replace(read_stream(done.stdout), returncode=done.returncode, stderr=done.stderr or "")
    problem = tool_problem(outcome.tools, prefix)
    if problem:
        raise WatchError(f"tool restriction not in force, result ignored: {problem}", outcome.cost)
    return outcome


def prompt_text() -> str:
    text = PROMPT_FILE.read_text()
    return text.split("\n---\n", 1)[1].strip() if text.startswith("---\n") else text.strip()


# ---- checking what came back ------------------------------------------------------------------------


def clean(text: object, limit: int) -> str:
    """Untrusted text as one short, redacted line."""
    line = " ".join(CONTROL.sub(" ", redact.redact(str(text))).split())
    return line if len(line) <= limit else line[: limit - 3] + "..."


def checked_permalink(raw: dict[str, Any]) -> re.Match[str]:
    """The entry's permalink, which its channel, ts and thread_ts must agree with."""
    found = PERMALINK.match(str(raw.get("permalink", "")))
    if not found:
        raise ValueError("not a Slack permalink")
    ts, thread_ts = str(raw.get("ts", "")), str(raw.get("thread_ts") or "")
    if not SLACK_TS.match(ts) or ts.replace(".", "") != found["digits"]:
        raise ValueError("ts does not match the permalink")
    if raw.get("channel") != found["channel"]:
        raise ValueError("channel does not match the permalink")
    if thread_ts and not SLACK_TS.match(thread_ts):
        raise ValueError("thread_ts is not a Slack ts")
    return found


@dataclass(frozen=True)
class Message:
    permalink: str
    channel: str
    ts: str
    thread_ts: str
    author: str
    kind: str
    excerpt: str

    @property
    def key(self) -> str:
        return f"{self.channel}/{self.ts}"

    @property
    def posted(self) -> datetime:
        return datetime.fromtimestamp(float(self.ts), UTC)

    @classmethod
    def of(cls, raw: object, since: datetime) -> Message:
        """A checked entry; ValueError says what is wrong with it."""
        if not isinstance(raw, dict):
            raise ValueError("not an object")  # noqa: TRY004  # every rejection is a ValueError
        found = checked_permalink(raw)
        if raw.get("kind") not in KINDS:
            raise ValueError(f"unknown kind {raw.get('kind')!r}")
        message = cls(
            found[0], found["channel"], str(raw["ts"]), str(raw.get("thread_ts") or ""),
            clean(raw.get("author", ""), AUTHOR_LENGTH), str(raw["kind"]),
            clean(raw.get("excerpt", ""), EXCERPT_LENGTH),
        )  # fmt: skip
        if message.posted < since - TS_TOLERANCE:
            raise ValueError("posted before the scan start")
        return message

    def summary(self) -> str:
        """The event's summary: who, where and the untrusted excerpt, within the event's length."""
        head = f"{self.kind} from {self.author or 'someone'} in {self.channel}, untrusted excerpt: "
        room = max(0, event_model.SUMMARY_LENGTH - len(head) - 2)
        return f'{head}"{clean(self.excerpt, room) if len(self.excerpt) > room else self.excerpt}"'


def parse_messages(
    result: dict[str, Any], since: datetime, detail: str = ""
) -> tuple[list[Message], list[str]]:
    """The checked messages, and why entries were rejected. A result without the JSON is an error;
    <detail> says why a failed call failed (failure_detail)."""
    if result.get("is_error"):
        raise WatchError(f"the watch call failed: {detail or result.get('subtype', 'error')}")
    output = result.get("structured_output")
    if not isinstance(output, dict) or not isinstance(output.get("messages"), list):
        raise WatchError("the watch call returned no messages list")
    found: list[Message] = []
    rejected: list[str] = []
    for raw in output["messages"][:MAX_MESSAGES]:
        try:
            found.append(Message.of(raw, since))
        except ValueError as exc:
            rejected.append(str(exc))
    return found, rejected


@dataclass
class Triage:
    new: list[Message] = field(default_factory=list)
    seen: int = 0  # processed by the chores already (ledger seen)
    outreach: int = 0  # in an open outreach conversation: step 5's
    queued: int = 0  # queued by an earlier watch, or twice in this one
    quiet: int = 0  # from a quiet author: left to the check-in's scan


def triage(
    messages: list[Message],
    book: ledger.Ledger,
    queued: Mapping[str, str],
    quiet_authors: frozenset[str] = frozenset(),
) -> Triage:
    """Sort the messages: new ones become events; the rest are counted."""
    sorted_out = Triage()
    keys = set(queued)
    for message in messages:
        if message.key in keys:
            sorted_out.queued += 1
        elif ledger.processed_source(book, message.permalink) is not None:
            sorted_out.seen += 1
        elif ledger.outreach_for_message(book, message.permalink):
            sorted_out.outreach += 1
        elif message.author.casefold() in quiet_authors:
            sorted_out.quiet += 1
        else:
            sorted_out.new.append(message)
        keys.add(message.key)
    return sorted_out


def read_ledger(state_dir: Path) -> ledger.Ledger:
    """The ledger, read-only."""
    ledger.configure(state_dir)
    with ledger.locked_ledger(write=False) as book:
        return book


def enqueue(state_dir: Path, messages: list[Message], now: datetime) -> list[event_model.Event]:
    """One `slack` event per message (distinct ids: a microsecond apart)."""
    queued = []
    with events.locked(state_dir) as directory:
        for index, message in enumerate(messages):
            event = event_model.new_event(
                EVENT_TYPE,
                ref=message.permalink,
                origin=ORIGIN,
                summary=message.summary(),
                now=now + timedelta(microseconds=index),
            )
            events.write(directory, event)
            queued.append(event)
    return queued


# ---- a run --------------------------------------------------------------------------------------------


@dataclass
class Report:
    since: datetime
    found: list[Message] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    sorted_out: Triage = field(default_factory=Triage)
    cost: float = 0.0
    denials: int = 0
    tools: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    truncated: bool = False

    def line(self) -> str:
        t = self.sorted_out
        kinds = ", ".join(f"{sum(m.kind == k for m in self.found)} {k}" for k in KINDS)
        parts = [
            f"scanned from {self.since.astimezone():%a %H:%M}: {len(self.found)} message(s) ({kinds})",
            f"{len(t.new)} new",
            f"{t.seen} already processed",
            f"{t.outreach} in outreach conversations",
            f"{t.queued} already queued",
        ]
        if t.quiet:
            parts.append(f"{t.quiet} from quiet authors (left to the check-in)")
        if self.rejected:
            parts.append(f"{len(self.rejected)} rejected ({'; '.join(sorted(set(self.rejected)))})")
        if self.denials:
            parts.append(f"{self.denials} tool call(s) denied")
        if self.truncated:
            parts.append("more than it listed (the next check-in's scan covers the rest)")
        return ", ".join(parts) + f" (${self.cost:.3f})"


def watch(config: Config, since: datetime, workdir: Path) -> Report:
    """Call Claude and sort what it found; queues nothing."""
    outcome = call(config, mcp_document(config), since, workdir)
    report = Report(since, cost=outcome.cost, tools=outcome.tools, calls=outcome.calls)
    report.denials = len(outcome.result.get("permission_denials") or [])
    try:
        report.found, report.rejected = parse_messages(outcome.result, since, failure_detail(outcome))
    except WatchError as exc:
        raise WatchError(str(exc), outcome.cost) from exc
    report.truncated = bool((outcome.result.get("structured_output") or {}).get("truncated"))
    return report


def record_success(config: Config, report: Report, started: datetime, now: datetime) -> str:
    queued = enqueue(config.state_dir, report.sorted_out.new, now)
    with locked_state(config.directory) as state:
        state.watermark = iso(started)
        state.failures, state.failing_since, state.last_error = 0, None, None
        add_spend(state, report.cost, now)
        state.queued.update({m.key: iso(now) for m in report.sorted_out.new})
        state.runs.append(
            {"started": iso(started), "ended": iso(now), "outcome": "ok", "found": len(report.found),
             "queued": len(queued), "rejected": len(report.rejected), "cost": round(report.cost, 4)}
        )  # fmt: skip
        state.forget_old(now)
    return report.line()


def record_failure(config: Config, error: str, cost: float, started: datetime, now: datetime) -> str:
    with locked_state(config.directory) as state:
        state.failures += 1
        state.failing_since = state.failing_since or iso(started)
        state.last_error = {"at": iso(now), "error": error[:ERROR_LENGTH]}
        add_spend(state, cost, now)
        state.runs.append({"started": iso(started), "ended": iso(now), "outcome": "failed", "error": error})
        state.forget_old(now)
        failures, watermark = state.failures, state.watermark
    line = f"ERROR: the Slack watch failed ({failures} in a row): {error}"
    return line + covered_note(watermark) if failures >= MAX_FAILURES else line


def covered_note(watermark: str | None) -> str:
    """Nothing is missed while watches fail: the watermark stays, and the check-ins scan Slack."""
    start = f"from {watermark}" if watermark else "two intervals back"
    return (
        f". Nothing is missed: the next successful watch scans {start} (at most "
        "BOSUN_SLACK_LOOKBACK_HOURS back), and every check-in scans Slack itself"
    )


def add_spend(state: State, cost: float, now: datetime) -> None:
    day = local_date(now).isoformat()
    state.spend[day] = round(state.spend.get(day, 0.0) + cost, 4)


def begin(config: Config, now: datetime, scheduled: bool) -> tuple[str | None, datetime]:
    """Check the gates and mark the run as started; (why not, or None) and the scan start. The
    scheduler logs a reason to skip only when it changes, and never "not due"."""
    with locked_state(config.directory) as state:
        reason = gate_and_note(config, state, now, scheduled)
        if reason is None:
            state.last_started = iso(now)
            return None, scan_start(config, state, now, chores_scan(read_ledger(config.state_dir)))
        quiet = reason == NOT_DUE or (scheduled and reason == state.last_reason)
        if scheduled:
            state.last_reason = reason
        return ("" if quiet else f"skip: {reason}"), now


def gate_and_note(config: Config, state: State, now: datetime, scheduled: bool) -> str | None:
    """Why no run now (None: run), noting the scheduler's look and a spent budget for the watchdog."""
    if scheduled:
        state.last_check = iso(now)
    reason = (schedule_gate(config, state, now) if scheduled else None) or run_gate(config, state, now)
    if reason is not None and reason == budget_gate(config, state, now):
        state.budget_spent_on = local_date(now).isoformat()
    if reason is None:
        state.last_reason = ""
    return reason


def run(config: Config, now: datetime, scheduled: bool, clock: Clock) -> str:
    """One watch, under the run lock: gates, the call, the triage, the events and the state."""
    if scheduled and not config.enabled:
        return ""
    with run_lock(config.directory) as acquired:
        if not acquired:
            return "" if scheduled else "skip: another Slack watch is running"
        reason, since = begin(config, now, scheduled)
        if reason is not None:
            return reason
        try:
            with tempfile.TemporaryDirectory(prefix="slack-watch-") as workdir:
                report = watch(config, since, Path(workdir))
            report.sorted_out = triage(
                report.found,
                read_ledger(config.state_dir),
                State.load(config.directory).queued,
                config.quiet_authors,
            )
        except (WatchError, OSError, ledger.LedgerError) as exc:
            return record_failure(config, str(exc), getattr(exc, "cost", 0.0), now, clock())
        return record_success(config, report, now, clock())


def dry_run(config: Config, now: datetime) -> str:
    """Call Claude and show what a run would queue; queues nothing, writes no state."""
    state = State.load(config.directory)
    off = None if config.enabled else "off (BOSUN_SLACK_WATCH_ENABLED=0)"
    gate = off or schedule_gate(config, state, now) or run_gate(config, state, now)
    book = read_ledger(config.state_dir)
    since = scan_start(config, state, now, chores_scan(book))
    with tempfile.TemporaryDirectory(prefix="slack-watch-dry-") as workdir:
        report = watch(config, since, Path(workdir))
    report.sorted_out = triage(report.found, book, state.queued, config.quiet_authors)
    lines = [
        f"dry-run: a scheduled run now: {'would skip: ' + gate if gate else 'would run'}",
        f"tools the call could see: {', '.join(report.tools) or '(none)'}",
        *(f"tool call: {c}" for c in report.calls),
        report.line(),
        *(f"would queue: {m.kind} {m.permalink} {m.summary()}" for m in report.sorted_out.new),
    ]
    return "\n".join(lines)


def render_status(config: Config, now: datetime) -> str:
    state = State.load(config.directory)
    last = state.runs[-1] if state.runs else None
    last_text = "never"
    if last:
        detail = (
            last.get("error")
            or f"{last.get('found')} found, {last.get('queued')} queued, ${last.get('cost')}"
        )
        last_text = f"{last['started']} {last['outcome']} ({detail})"
    error = f"{state.last_error['at']}: {state.last_error['error']}" if state.last_error else "none"
    enabled = "yes" if config.enabled else "no"
    events_on = "on" if config.events_enabled else "off"
    spent = state.spent_on(local_date(now))
    next_start = scan_start(config, state, now, chores_scan(read_ledger(config.state_dir)))
    return "\n".join(
        [
            (
                f"enabled: {enabled} (events {events_on}), every {config.minutes:g} min in the awake "
                f"window, model {config.model}"
            ),
            (
                f"budget: ${config.max_usd_per_call:.2f} a call, ${config.max_usd_per_day:.2f} a day; "
                f"spent today ${spent:.2f}"
            ),
            f"last run: {last_text}",
            f"next scan from: {iso(next_start)}",
            f"failures in a row: {state.failures}; last error: {error}",
            f"scheduler's last look: {state.last_check or 'never'}",
        ]
    )


def current_time(env: Mapping[str, str]) -> datetime:
    """Now (UTC); BOSUN_SLACK_WATCH_NOW (epoch seconds) for tests."""
    override = env.get("BOSUN_SLACK_WATCH_NOW")
    return datetime.fromtimestamp(float(override), UTC) if override else datetime.now(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun slack-watch", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--scheduled", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.from_env(os.environ)
        now = current_time(os.environ)
        if args.command == "status":
            output = render_status(config, now)
        elif args.dry_run:
            output = dry_run(config, now)
        else:
            output = run(config, now, args.scheduled, lambda: current_time(os.environ))
    except (WatchError, ledger.LedgerError, event_model.EventsError) as exc:
        print(f"slack-watch: {exc}", file=sys.stderr)
        return 1
    if output:
        print(output)
    return 1 if output.startswith("ERROR:") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
