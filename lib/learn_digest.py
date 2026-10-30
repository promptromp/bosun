"""Learning, stage 1: a compact digest of what happened in a time window.

The digest is what `bosun learn` gives the analysing Claude instead of raw transcripts: per
session (the orchestrator's daily conversations and each repo window), the prompts and who sent
them, permission prompts and how long they waited, refused and failed tool calls, guard denials,
questions to you, bosun commands and the end of each turn; then the ledger's activity, the
bosun logs (repeated lines collapsed) and the watchdog's alerts; then the instructions and
memory in force, and what earlier runs already found. Secrets and email addresses are redacted.

A digest over its cap (BOSUN_LEARN_MAX_DIGEST_KB) is trimmed step by step (`TRIM_STEPS`):
routine ledger history first, then ordinary log lines, then shorter ledger notes and older ledger
history, and only then fewer events per session (the most telling and newest stay), since the
sessions tell the most; it says at the top what it left out.

Sessions are read through a harness adapter (`Harness`); Claude Code (`ClaudeCode`, via
lib/claude_state.py) is the only one so far. Only sessions that belong to an bosun window are
read: a transcript whose session id a window runs, or whose name is a window's (the orchestrator's
`<name>-YYYY-MM-DD` conversations included), in the folder of a window.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import claude_state
import learn_checks
import ledger
import redact as redact_module
from claude_state import Event, Transcript


SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
LINE_LIMIT = 300
HISTORY_NOTE_LIMIT = 200
TITLE_LIMIT = 120
GIT_TIMEOUT_SECONDS = 10
MIN_REPEAT = 3  # identical log lines (digits aside) in a row collapse from this many
BYTES_PER_KB = 1024
# Trimming to the cap: ledger history that only files or links an item, the log lines that tell
# something, and how far the events per session and the ledger's history shrink at most.
ROUTINE_HISTORY = frozenset(
    {"linked", "unlinked", "updated", "synced", "links-migrated", "message-time-corrected", "spawned"}
)
LOG_SIGNAL = re.compile(r"error|fail|skip|denied|refused|alert|timed? ?out|traceback|warn", re.IGNORECASE)
MIN_SESSION_EVENTS = 20
HISTORY_QUARTERS = 4  # older ledger history goes a quarter of the window at a time
SHORT_HISTORY_CHARS = 160  # a ledger history line, trimmed (else HISTORY_NOTE_LIMIT + LINE_LIMIT)
COMMAND_WORD = re.compile(r"(?:^|[\s;&|(])bosun\s+[a-z]")
# Prompts bosun itself types into a session (checkin, delegate, send, refresh).
BOSUN_PROMPTS = (
    "/bosun:chores",
    "Ledger id",
    "Ledger item",
    "Update to ledger item",
    "Run this task in a subagent",
)
DRAIN_PREFIX_CHARS = 40
# Lower sorts first when a session has more events than fit.
PRIORITY = {
    "permission": 0,
    "rejected": 0,
    "hook-deny": 0,
    "question": 1,
    "prompt": 1,
    "notice": 2,
    "tool-error": 3,
    "command": 4,
    "reply": 5,
}
LIVING_OBJECTS = (
    ("orchestrator/policy.md", "the orchestrator's policy (its system prompt)"),
    ("orchestrator/plugin/commands/chores.md", "the chores procedure"),
    ("orchestrator/tasks/_common.md", "rules every delegated task gets"),
)
SITE_OBJECTS = (
    ("policy.md", "site policy overlay, appended to the orchestrator's policy"),
    ("tasks/_common.md", "site rules added to every delegated task"),
    ("learn.md", "site additions to the learning prompt"),
)


# ---- redaction: lib/redact.py (also used for what I type in a repo window) ---------------------

redact = redact_module.redact


# ---- sessions through a harness ----------------------------------------------------------------


class Harness(Protocol):
    """How learn reads an agent harness's sessions. Claude Code is the first."""

    name: str

    def transcripts(self, workdir: str, since: datetime) -> list[Transcript]: ...

    def events(self, transcript: Path, since: datetime, until: datetime) -> tuple[list[Event], int]: ...

    def subagents(self, transcript: Path) -> list[Path]: ...

    def memory_dir(self, workdir: str) -> Path: ...


class ClaudeCode:
    name = "Claude Code"

    def transcripts(self, workdir: str, since: datetime) -> list[Transcript]:
        return claude_state.transcripts_since(workdir, since.timestamp())

    def events(self, transcript: Path, since: datetime, until: datetime) -> tuple[list[Event], int]:
        return claude_state.read_events(transcript, since, until)

    def subagents(self, transcript: Path) -> list[Path]:
        return claude_state.subagent_transcripts(transcript)

    def memory_dir(self, workdir: str) -> Path:
        return claude_state.memory_dir(workdir)


@dataclass(frozen=True)
class Window:
    """A window from `bosun sessions --json`."""

    name: str
    kind: str
    dir: str
    session_id: str | None = None
    status: str = ""

    @classmethod
    def from_json(cls, data: list[dict[str, Any]]) -> list[Window]:
        windows = []
        for entry in data:
            claude = entry.get("claude") or {}
            windows.append(
                cls(
                    name=str(entry["name"]),
                    kind=str(entry.get("kind", "")),
                    dir=str(entry.get("dir", "")),
                    session_id=claude.get("sessionId"),
                    status=str(claude.get("status") or ("no-claude" if entry.get("window") else "no-window")),
                )
            )
        return windows


@dataclass(frozen=True)
class Settings:
    since: datetime  # aware
    until: datetime  # aware
    state_dir: Path
    log_dir: Path
    config_dir: Path
    root: Path  # this checkout
    orchestrator: str = "orchestrator"
    drain_prompt: str = ""
    max_events: int = 250
    exclude: frozenset[str] = frozenset()
    known: tuple[dict[str, Any], ...] = ()  # earlier findings: key, category, title, status, seen
    max_chars: int = 0  # the digest's cap (BOSUN_LEARN_MAX_DIGEST_KB); 0: none


@dataclass
class Session:
    window: Window
    transcript: Transcript
    events: list[Event]
    tool_calls: int
    dropped: int = 0


@dataclass
class Digest:
    text: str
    sessions: list[Session] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # transcripts not read, and why


def owning_window(transcript: Transcript, windows: list[Window], orchestrator: str) -> Window | None:
    """The window a transcript belongs to: the one running it, else the one named like it."""
    for window in windows:
        if window.session_id == transcript.session_id:
            return window
    for window in windows:
        if transcript.title == window.name:
            return window
    daily = re.fullmatch(rf"{re.escape(orchestrator)}-\d{{4}}-\d{{2}}-\d{{2}}", transcript.title)
    if orchestrator and daily:
        return next((w for w in windows if w.name == orchestrator), None)
    return None


def is_bosun_prompt(text: str, drain_prompt: str) -> bool:
    drain = drain_prompt[:DRAIN_PREFIX_CHARS]
    return text.startswith(BOSUN_PROMPTS) or bool(drain and text.startswith(drain))


def kept(event: Event) -> bool:
    """Shell commands only when they call bosun; everything else the reader produced."""
    return event.kind != "command" or bool(COMMAND_WORD.search(event.text))


def cap(events: list[Event], limit: int) -> tuple[list[Event], int]:
    """At most <limit> events, the most telling first (see PRIORITY), back in time order."""
    if len(events) <= limit:
        return events, 0
    ranked = sorted(events, key=lambda e: (PRIORITY.get(e.kind, len(PRIORITY)), e.at))[:limit]
    return sorted(ranked, key=lambda e: e.at), len(events) - limit


def read_session(harness: Harness, settings: Settings, window: Window, transcript: Transcript) -> Session:
    events, calls = harness.events(transcript.path, settings.since, settings.until)
    for sub in harness.subagents(transcript.path):
        sub_events, sub_calls = harness.events(sub, settings.since, settings.until)
        calls += sub_calls
        events += [
            Event(e.at, e.kind, e.text, e.tool, e.waited, "subagent", e.failed)
            for e in sub_events
            if e.kind in ("tool-error", "rejected", "permission", "hook-deny", "question")
        ]
    chosen, dropped = cap(sorted((e for e in events if kept(e)), key=lambda e: e.at), settings.max_events)
    return Session(window, transcript, chosen, calls, dropped)


def find_sessions(
    harness: Harness, settings: Settings, windows: list[Window]
) -> tuple[list[Session], list[str]]:
    sessions, skipped = [], []
    folders = sorted({w.dir for w in windows if w.dir and w.kind != "checkin"})
    for folder in folders:
        for transcript in harness.transcripts(folder, settings.since):
            window = owning_window(transcript, windows, settings.orchestrator)
            if transcript.session_id in settings.exclude:
                skipped.append(f"{transcript.session_id[:8]} (excluded)")
            elif window is None:
                skipped.append(
                    f"{transcript.session_id[:8]} {transcript.title or '(unnamed)'} (not an bosun window)"
                )
            else:
                session = read_session(harness, settings, window, transcript)
                if session.events:
                    sessions.append(session)
    return sessions, skipped


# ---- rendering ---------------------------------------------------------------------------------


def duration(seconds: float | None) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds >= SECONDS_PER_HOUR:
        return f"{seconds // SECONDS_PER_HOUR}h{seconds % SECONDS_PER_HOUR // SECONDS_PER_MINUTE:02d}m"
    if seconds >= SECONDS_PER_MINUTE:
        return f"{seconds // SECONDS_PER_MINUTE}m"
    return f"{seconds}s"


def local(moment: datetime) -> datetime:
    return moment.astimezone().replace(tzinfo=None)


def clip(text: str, limit: int = LINE_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def describe(event: Event, drain_prompt: str) -> str:
    failed = " (failed)" if event.failed else ""
    sub = "subagent " if event.origin == "subagent" else ""
    if event.kind == "prompt":
        who = "bosun" if is_bosun_prompt(event.text, drain_prompt) else "you"
        how = {
            "queued": ", while busy",
            "mid-turn": ", into the running turn",
            "undelivered": ", NEVER DELIVERED",
        }
        return f"{who}{how.get(event.origin, '')}: {event.text}"
    texts = {
        "permission": f"{sub}PERMISSION PROMPT answered after {duration(event.waited)}{failed}: {event.tool}",
        "rejected": f"{sub}REJECTED by you after {duration(event.waited)}: {event.tool}"
        + (f" -- you said: {event.text}" if event.text else ""),
        "question": f"{sub}QUESTION to you, answered after {duration(event.waited)}: "
        f"{event.tool} -> {event.text}",
        "hook-deny": f"{sub}GUARD DENIED {event.tool} -- {event.text}",
        "command": f"ran{failed}: {event.text}",
        "tool-error": f"{sub}error: {event.tool} -> {event.text}",
        "reply": f"reply: {event.text}",
        "notice": f"notice: {event.text}",
    }
    return texts.get(event.kind, f"{event.kind}: {event.text}")


def wait_text(session: Session) -> str:
    waits = [e.waited or 0 for e in session.events if e.kind in ("permission", "question")]
    return f" (longest wait {duration(max(waits))})" if waits else ""


def session_stats(session: Session, drain_prompt: str) -> str:
    kinds = Counter(e.kind for e in session.events)
    automated = sum(1 for e in session.events if e.kind == "prompt" and is_bosun_prompt(e.text, drain_prompt))
    dropped = f"; {session.dropped} less telling events left out" if session.dropped else ""
    return (
        f"{kinds['prompt']} prompts ({kinds['prompt'] - automated} from you, {automated} from bosun), "
        f"{session.tool_calls} tool calls, {kinds['tool-error']} errors, {kinds['permission']} permission "
        f"prompts{wait_text(session)}, {kinds['rejected']} rejected, {kinds['hook-deny']} guard denials, "
        f"{kinds['question']} questions to you{dropped}"
    )


def day_lines(stamps: Iterable[tuple[datetime, str]]) -> list[str]:
    """Lines "HH:MM text" under a header for each new day."""
    lines, day = [], None
    for moment, text in stamps:
        if moment.date() != day:
            day = moment.date()
            lines.append(f"{moment:%a %Y-%m-%d}")
        lines.append(f"  {moment:%H:%M} {text}")
    return lines


def render_session(session: Session, drain_prompt: str) -> list[str]:
    window, transcript = session.window, session.transcript
    title = transcript.title or "unnamed"
    header = (
        f"### {window.name} ({window.kind}; conversation {title}, session {transcript.session_id[:8]}; "
        f"now {window.status or '?'})"
    )
    events = ((local(e.at), clip(describe(e, drain_prompt))) for e in session.events)
    return [header, f"stats: {session_stats(session, drain_prompt)}", *day_lines(events), ""]


# ---- the ledger --------------------------------------------------------------------------------

HISTORY_SKIP = {"source"}


def in_window(stamp: object, settings: Settings) -> bool:
    if not isinstance(stamp, str):
        return False
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    return settings.since <= moment <= settings.until


def history_text(entry: dict[str, Any], limit: int = HISTORY_NOTE_LIMIT + LINE_LIMIT) -> str:
    move = f" {entry['from']}->{entry['to']}" if entry.get("from") else ""
    extra = {k: v for k, v in entry.items() if k not in ("at", "event", "note", "from", "to") and v}
    details = f" {json.dumps(extra, sort_keys=True)}" if extra else ""
    note = f": {entry['note']}" if entry.get("note") else ""
    return clip(f"{entry['event']}{move}{note}{details}", limit)


def item_header(item: dict[str, Any]) -> str:
    delegation = item.get("delegation") or {}
    where = f" @{delegation['window']}" if delegation.get("window") else ""
    pending = " PROPOSAL PENDING" if item.get("proposal") else ""
    title = clip(item.get("title") or "", TITLE_LIMIT)
    return f"- {item['id']} [{item['kind']}] now {item['state']}{where}{pending}: {title}"


def trimmed_history(item: dict[str, Any], settings: Settings, trim: Trim) -> list[dict[str, Any]]:
    """The item's history entries inside the window, less what <trim> leaves out."""
    skip = HISTORY_SKIP | (ROUTINE_HISTORY if trim.routine_ledger else frozenset())
    window = replace(settings, since=max(settings.since, trim.history_since or settings.since))
    return [
        h for h in item.get("history") or [] if h.get("event") not in skip and in_window(h.get("at"), window)
    ]


def item_activity(item: dict[str, Any], settings: Settings, trim: Trim) -> list[str]:
    """The item's header and its history inside the window (less with <trim>), or nothing."""
    recent = trimmed_history(item, settings, trim)
    if not recent:
        return []
    limit = SHORT_HISTORY_CHARS if trim.short_history else HISTORY_NOTE_LIMIT + LINE_LIMIT
    stamps = ((local(datetime.fromisoformat(h["at"])), history_text(h, limit)) for h in recent)
    return [item_header(item), *(f"  {line}" for line in day_lines(stamps))]


def render_ledger(settings: Settings, trim: Trim) -> list[str]:
    ledger.configure(settings.state_dir)
    try:
        items = list(ledger.load()["items"].values())
    except ledger.LedgerError as exc:
        return ["## Ledger", f"(unreadable: {exc})", ""]
    lines = ["## Ledger: items with activity in the window", ""]
    for item in sorted(items, key=lambda i: str(i.get("created_at"))):
        lines += item_activity(item, settings, trim)
    open_items = [i for i in items if ledger.is_open(i)]
    hints = learn_checks.ledger_hints(items, settings.since)
    return [
        *lines,
        "",
        f"Open items now: {len(open_items)}",
        *(item_header(i) for i in open_items),
        "",
        "## Ledger checks (deterministic hints from code; verify them against the history above)",
        "",
        *(f"- {hint}" for hint in hints or ["none"]),
        "",
    ]


# ---- logs and the watchdog ---------------------------------------------------------------------

LOG_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ")
DIGITS = re.compile(r"\d+")


def collapse(lines: list[str]) -> list[str]:
    """Runs of MIN_REPEAT+ lines that differ only in their digits become first, count and last."""
    out: list[str] = []
    run: list[str] = []
    for line in [*lines, ""]:
        if run and DIGITS.sub("#", line) == DIGITS.sub("#", run[0]):
            run.append(line)
            continue
        if len(run) >= MIN_REPEAT:
            out += [run[0], f"  ... the same {len(run) - 2} more time(s) ...", run[-1]]
        else:
            out += run
        run = [line] if line else []
    return out


def log_lines(path: Path, settings: Settings) -> list[str]:
    """The stamped lines of <path> inside the window (other lines are command output)."""
    since, until = local(settings.since), local(settings.until)
    chosen = []
    for line in path.read_text(errors="replace").splitlines():
        match = LOG_STAMP.match(line)
        if match and since <= datetime.fromisoformat(match[1]) <= until:
            chosen.append(clip(line))
    return chosen


def render_logs(settings: Settings, trim: Trim) -> list[str]:
    lines = ["## Bosun logs (repeated lines collapsed)", ""]
    for path in sorted(settings.log_dir.glob("*.log")):
        if path.name.startswith("learn-"):
            continue  # its own log
        chosen = collapse(log_lines(path, settings))
        if trim.signal_logs:
            chosen = [line for line in chosen if LOG_SIGNAL.search(line)]
        if chosen:
            lines += [f"### {path.name}", *chosen, ""]
    return lines


def render_watchdog(settings: Settings) -> list[str]:
    try:
        state = json.loads((settings.state_dir / "watchdog.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    since, until = local(settings.since), local(settings.until)
    alerted = [
        f"- {at}: {key}"
        for key, at in sorted((state.get("alerted") or {}).items(), key=lambda kv: str(kv[1]))
        if since <= datetime.fromisoformat(at) <= until
    ]
    return ["## Watchdog alerts in the window", "", *(alerted or ["- none"]), ""]


def render_changes(settings: Settings) -> list[str]:
    """Commits to this checkout in the window, so the analysis knows what was already fixed."""
    command = ["git", "-C", str(settings.root), "log", f"--since={settings.since.isoformat()}"]
    command += [
        f"--until={settings.until.isoformat()}",
        "--date=format-local:%a %Y-%m-%d %H:%M",
        "--pretty=%ad %s",
    ]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    lines = done.stdout.splitlines() if done.returncode == 0 else []
    title = "## Changes to bosun itself in the window (commits; a problem fixed here needs no proposal)"
    return [title, "", *(f"- {clip(line)}" for line in lines or ["none"]), ""]


# ---- what is in force --------------------------------------------------------------------------


def file_block(path: Path, label: str) -> list[str]:
    if not path.is_file():
        return []
    return [f"### {label}: {path}", "````markdown", path.read_text().strip(), "````", ""]


def render_objects(settings: Settings, windows: list[Window], harness: Harness) -> list[str]:
    lines = [
        "## Instructions and memory in force (reference, not activity)",
        "",
        "Windows (bosun sessions): "
        + "; ".join(f"{w.name} ({w.kind}, {w.dir}, {w.status})" for w in windows),
        "",
    ]
    for relative, label in LIVING_OBJECTS:
        lines += file_block(settings.root / relative, label)
    task_types = sorted(p.stem for p in (settings.root / "orchestrator" / "tasks").glob("[!_]*.md"))
    lines += [f"Task types (orchestrator/tasks/): {', '.join(task_types)}", ""]
    for relative, label in SITE_OBJECTS:
        lines += file_block(settings.config_dir / relative, label)
    orchestrator = next((w for w in windows if w.kind == "orchestrator"), None)
    if orchestrator:
        index = harness.memory_dir(orchestrator.dir) / claude_state.MEMORY_INDEX
        lines += file_block(index, f"{harness.name} memory index of the orchestrator's folder")
    return lines


def render_known(settings: Settings) -> list[str]:
    lines = ["## Findings of earlier runs (reuse their key when you see the same thing again)", ""]
    lines += [
        f"- {k['key']} [{k.get('category', '?')}] {k.get('status', '?')}, seen {k.get('times_seen', 1)}x: "
        f"{k.get('title', '')}"
        for k in settings.known
    ] or ["- none"]
    return [*lines, ""]


@dataclass(frozen=True)
class Trim:
    """How much a digest leaves out to fit its cap (the defaults: nothing)."""

    routine_ledger: bool = False  # ledger history that only files or links items (ROUTINE_HISTORY)
    signal_logs: bool = False  # only log lines with errors, failures, skips, denials or alerts
    short_history: bool = False  # ledger history lines cut at SHORT_HISTORY_CHARS
    session_events: int = 0  # at most this many events per session; 0: as collected
    history_since: datetime | None = None  # ledger history from here on only

    def notes(self) -> list[str]:
        """What this trim leaves out, one line each."""
        notes = []
        if self.routine_ledger:
            notes.append(f"ledger: routine history events left out ({', '.join(sorted(ROUTINE_HISTORY))})")
        if self.signal_logs:
            notes.append("logs: only lines with errors, failures, skips, denials, timeouts or alerts")
        if self.short_history:
            notes.append(f"ledger: history lines cut at {SHORT_HISTORY_CHARS} characters")
        if self.session_events:
            notes.append(
                f"sessions: at most {self.session_events} events each (the most telling, newest first)"
            )
        if self.history_since:
            notes.append(f"ledger: history before {local(self.history_since):%a %Y-%m-%d %H:%M} left out")
        return notes


def trim_steps(settings: Settings) -> Iterator[Trim]:
    """Ever tighter trims, from none to the tightest: the ledger and the logs give way before the
    sessions do."""
    yield Trim()
    yield Trim(routine_ledger=True)
    yield (quiet := Trim(routine_ledger=True, signal_logs=True))
    yield (short := replace(quiet, short_history=True))
    span = settings.until - settings.since
    since = None
    for quarter in range(1, HISTORY_QUARTERS):
        since = settings.since + span * quarter / HISTORY_QUARTERS
        yield replace(short, history_since=since)
    limit = settings.max_events // 2
    while limit > MIN_SESSION_EVENTS:
        yield replace(short, history_since=since, session_events=limit)
        limit //= 2
    yield replace(short, history_since=since, session_events=MIN_SESSION_EVENTS)


def recap(session: Session, limit: int) -> Session:
    """<session> with at most <limit> events: the most telling (PRIORITY), then the newest."""
    if not limit or len(session.events) <= limit:
        return session
    ranked = sorted(session.events, key=lambda e: (PRIORITY.get(e.kind, len(PRIORITY)), -e.at.timestamp()))
    kept = sorted(ranked[:limit], key=lambda e: e.at)
    return replace(session, events=kept, dropped=session.dropped + len(session.events) - limit)


def trim_section(trim: Trim, settings: Settings, cut: bool = False) -> list[str]:
    notes = trim.notes() + (["cut at the cap: the end of the activity sections is missing"] if cut else [])
    if not notes:
        return []
    kb = settings.max_chars // BYTES_PER_KB
    return [f"## Trimmed to fit the digest cap ({kb} KB, BOSUN_LEARN_MAX_DIGEST_KB)", "",
            *(f"- {note}" for note in notes), ""]  # fmt: skip


@dataclass
class Parts:
    """A digest in three parts: the head, the activity (what trimming shrinks) and the reference."""

    head: list[str]
    sessions: list[Session]
    tail: list[str]

    def activity(self, settings: Settings, trim: Trim) -> list[str]:
        lines = ["## Sessions", ""]
        for session in sorted(self.sessions, key=lambda s: (s.window.name, s.events[0].at)):
            lines += render_session(recap(session, trim.session_events), settings.drain_prompt)
        return lines + render_ledger(settings, trim) + render_logs(settings, trim)

    def text(self, settings: Settings, trim: Trim, cut: bool = False) -> str:
        head = self.head + trim_section(trim, settings, cut)
        return redact("\n".join(head + self.activity(settings, trim) + self.tail) + "\n")


def fitted(parts: Parts, settings: Settings) -> str:
    """The digest under settings.max_chars: the first trim that fits, else the tightest, cut."""
    trim = Trim()
    for trim in trim_steps(settings):
        text = parts.text(settings, trim)
        if not settings.max_chars or len(text) <= settings.max_chars:
            return text
    head = redact("\n".join(parts.head + trim_section(trim, settings, cut=True)) + "\n")
    tail = redact("\n".join(parts.tail) + "\n")
    body = redact("\n".join(parts.activity(settings, trim)) + "\n")
    room = max(settings.max_chars - len(head) - len(tail), 0)
    return head + body[:room].rsplit("\n", 1)[0] + "\n" + tail


def collect(settings: Settings, windows: list[Window], harness: Harness | None = None) -> Digest:
    """The digest for <settings>' window, redacted, trimmed to settings.max_chars."""
    harness = harness or ClaudeCode()
    sessions, skipped = find_sessions(harness, settings, windows)
    since, until = local(settings.since), local(settings.until)
    head = [
        "# Bosun activity digest",
        "",
        f"Window: {since:%a %Y-%m-%d %H:%M} to {until:%a %Y-%m-%d %H:%M}, local time like every time below.",
        f"Sessions read through {harness.name}: {len(sessions)}. Not read: {', '.join(skipped) or 'none'}.",
        "",
    ]
    tail = render_watchdog(settings) + render_changes(settings)
    tail += render_known(settings) + render_objects(settings, windows, harness)
    return Digest(fitted(Parts(head, sessions, tail), settings), sessions, skipped)


def window_bounds(until: datetime, hours: float, last_until: datetime | None) -> tuple[datetime, datetime]:
    """From the end of the last run (no gap, no overlap), at most <hours> back."""
    earliest = until - timedelta(hours=hours)
    return (max(earliest, last_until) if last_until else earliest), until
