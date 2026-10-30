#!/usr/bin/env python3
"""Is Claude Code still logged in, and does each window's session still get real replies?

A login can expire or be revoked at any time; a session then answers every prompt with an error
reply and nothing else, and a check-in loop that only pastes prompts never notices. The signals,
best first (everything Claude Code-specific is read through lib/claude_state.py):

1. The window's transcript: the structured error fields of its latest reply
   (authentication_failed, HTTP 401: the login). Exact and free, but only once a prompt failed.
2. `claude auth status --json`: free, and tells a missing login apart from a stored one, but it
   makes no request, so a token the server revoked still shows as logged in.
3. The login check request (claude_state.probe): one tiny real request, a fraction of a cent. The
   morning setup step (ensure_claude_login) makes one; the check-in loop makes one while a window
   or the last check reports a login error, at most every PROBE_EVERY_MINUTES, to learn whether
   the login works again (a /login is not written to any transcript).
4. Fallback: a loose reading of the reply's text (or, without a transcript, of the window's last
   lines), only where no structured field exists. Verdicts from it say "pattern fallback".

Usage: login.py window <name> <claude pid> [--after <epoch>] [--wait <seconds>] [--probe] [--pane -]
                         [--dry-run]
       login.py global [--probe]
       login.py status          (lines "<window>\\t<claude pid>" on stdin, as window_claude_pids)

`window` prints "<state> <change> <detail>": state ok, needs-login or unknown; change new (it has
just started needing a login), same, recovered or -. It records the windows that need a login,
and the last login check, in $BOSUN_STATE_DIR/login.json. --after/--wait: first wait up to
<seconds> for a reply newer than <epoch> (after a delivery). --probe: if the window needs a login,
or the last login check failed, make the login check request when one is due. --pane -: the
window's visible text on stdin, read only when there is no transcript. --dry-run: record nothing.
`global` prints the state of the login itself; --probe makes the login check request.
`status` prints both for `bosun status`, changing nothing.
Exit codes: 0 ok, 1 needs a login, 3 unknown, 2 usage. Settings: BOSUN_STATE_DIR,
BOSUN_CLAUDE_BIN.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
from claude_state import ApiError, AuthStatus, ProbeResult


OK = "ok"
NEEDS_LOGIN = "needs-login"
UNKNOWN = "unknown"
EXIT_CODES = {OK: 0, NEEDS_LOGIN: 1, UNKNOWN: 3}
NEW, SAME, RECOVERED, UNCHANGED = "new", "same", "recovered", "-"
STATE_FILE = "login.json"
LOCK_FILE = "login.lock"
PROBE_EVERY_MINUTES = 30
REPLY_POLL_SECONDS = 0.5
STATUS_NAME_WIDTH = 16


def now_utc() -> datetime:
    return datetime.now(UTC)


def shown(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%a %H:%M") if moment else "an unknown time"


def stamp(moment: datetime | None) -> str:
    return moment.isoformat(timespec="seconds") if moment else ""


def parse_stamp(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None


# ---- state -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    """The last login check request: when, and what came back."""

    at: datetime
    outcome: str  # ok, unknown, or an error category (auth, network, ...)
    source: str = ""
    code: str = ""

    @classmethod
    def of(cls, data: object) -> Probe | None:
        at = parse_stamp(data.get("at")) if isinstance(data, dict) else None
        if at is None or not isinstance(data, dict):
            return None
        return cls(at, str(data.get("outcome")), str(data.get("source") or ""), str(data.get("code") or ""))

    def to_json(self) -> dict[str, str]:
        return {"at": stamp(self.at), "outcome": self.outcome, "source": self.source, "code": self.code}


@dataclass
class State:
    probe: dict[str, str] | None = None
    gated: dict[str, str] = field(default_factory=dict)  # window -> since when it needs a login

    @classmethod
    def load(cls, state_dir: Path) -> State:
        try:
            data = json.loads((state_dir / STATE_FILE).read_text())
        except (OSError, json.JSONDecodeError):
            return cls()  # missing or damaged: nothing is known, and the next run rewrites it
        if not isinstance(data, dict):
            return cls()
        gated = data.get("gated")
        return cls(data.get("probe"), dict(gated) if isinstance(gated, dict) else {})

    def save(self, state_dir: Path) -> None:
        tmp = state_dir / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))
        tmp.replace(state_dir / STATE_FILE)

    @property
    def last_probe(self) -> Probe | None:
        return Probe.of(self.probe)

    def record_probe(self, result: ProbeResult, at: datetime) -> Probe:
        probe = Probe(at, result.outcome, result.source, result.code)
        self.probe = probe.to_json()
        return probe


@contextlib.contextmanager
def locked_state(state_dir: Path) -> Iterator[State]:
    """The state, read and written under a lock (the check-in loop and the morning run may both
    write it)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / LOCK_FILE).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = State.load(state_dir)
        yield state
        state.save(state_dir)


# ---- verdicts ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    state: str  # ok, needs-login or unknown
    detail: str
    since: datetime | None = None  # needs-login: since when


def error_detail(error: ApiError) -> str:
    how = "pattern fallback, no structured fields" if error.source == claude_state.PATTERN else error.code
    return f"every reply since {shown(error.since)} is a login error ({how})"


def transcript_verdict(error: ApiError | None, probe: Probe | None) -> Verdict:
    if error is None:
        return Verdict(OK, "its latest reply is a normal one")
    if error.category != claude_state.AUTH:
        return Verdict(OK, f"its latest reply is a {error.category} error ({error.code}), not a login error")
    if probe and probe.outcome == claude_state.PROBE_OK and error.at and probe.at > error.at:
        return Verdict(OK, f"a login check at {shown(probe.at)} worked, after its last login error")
    return Verdict(NEEDS_LOGIN, error_detail(error), error.since)


def window_verdict(transcript: Path | None, probe: Probe | None, pane: str = "") -> Verdict:
    """Whether the session in a window gets real replies, from its transcript; without one, from the
    window's last lines (the pattern fallback)."""
    if transcript is not None:
        return transcript_verdict(claude_state.last_api_error(transcript), probe)
    if pane and claude_state.pane_auth_error(pane):
        return Verdict(
            NEEDS_LOGIN, "no transcript; its last lines read like a login error (pattern fallback)"
        )
    return Verdict(UNKNOWN, "no transcript to read yet")


def login_failure(status: AuthStatus, probe: Probe | None) -> Verdict | None:
    """No stored login, or the last login check got a login error."""
    if status.logged_in is False:
        return Verdict(NEEDS_LOGIN, "claude auth status: not logged in")
    if probe and probe.outcome == claude_state.AUTH:
        detail = f"the login check at {shown(probe.at)} got a login error ({probe.code})"
        return Verdict(NEEDS_LOGIN, detail, probe.at)
    return None


def global_verdict(status: AuthStatus, probe: Probe | None, fresh: bool = False) -> Verdict:
    """Whether the login itself works: `claude auth status`, and the last login check. <fresh>: that
    check was just made for this answer, so anything but ok or a login error is unknown."""
    failure = login_failure(status, probe)
    if failure:
        return failure
    if fresh and probe and probe.outcome != claude_state.PROBE_OK:
        return Verdict(
            UNKNOWN, f"the login check could not tell: {probe.outcome} ({probe.code or 'no detail'})"
        )
    if status.logged_in is None:
        return Verdict(UNKNOWN, "claude auth status gave no readable answer")
    checked = f"last login check {shown(probe.at)}: {probe.outcome}" if probe else "no login check made yet"
    return Verdict(OK, f"logged in ({status.method or 'method unknown'}); {checked}")


def update_gate(state: State, window: str, verdict: Verdict) -> str:
    """Record whether <window> needs a login; how that changed."""
    before = state.gated.get(window)
    if verdict.state == NEEDS_LOGIN:
        state.gated[window] = stamp(verdict.since) or "?"
        return SAME if before == state.gated[window] else NEW
    if verdict.state == OK and before is not None:
        del state.gated[window]
        return RECOVERED
    return UNCHANGED


def probe_due(probe: Probe | None, now: datetime) -> bool:
    return probe is None or now - probe.at >= timedelta(minutes=PROBE_EVERY_MINUTES)


# ---- what the commands do ----------------------------------------------------------------------


@dataclass(frozen=True)
class Env:
    state_dir: Path
    claude_bin: str
    now: Callable[[], datetime] = now_utc
    auth_status: Callable[[str], AuthStatus] = claude_state.auth_status
    probe: Callable[[str], ProbeResult] = claude_state.probe
    record: Callable[[int | str], dict[str, Any] | None] = claude_state.record
    sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_environ(cls) -> Env:
        state_dir = os.environ.get("BOSUN_STATE_DIR")
        if not state_dir:
            raise SystemExit("login.py: BOSUN_STATE_DIR is not set; run it through bosun")
        return cls(Path(state_dir), os.environ.get("BOSUN_CLAUDE_BIN") or "claude")


def check_login(env: Env, state: State) -> Probe | None:
    """Make the login check request, unless `claude auth status` already says there is no login."""
    if env.auth_status(env.claude_bin).logged_in is False:
        return None
    return state.record_probe(env.probe(env.claude_bin), env.now())


def window_transcript(env: Env, pid: str) -> Path | None:
    entry = env.record(pid)
    return claude_state.transcript_of(entry) if entry else None


def wait_for_reply(env: Env, transcript: Path, after: float, seconds: float) -> None:
    """Wait up to <seconds> for a reply written after <after> (epoch seconds)."""
    deadline = time.monotonic() + seconds
    while True:
        latest = claude_state.last_reply_at(transcript)
        if (latest and latest.timestamp() > after) or time.monotonic() >= deadline:
            return
        env.sleep(REPLY_POLL_SECONDS)


def check_window(env: Env, args: argparse.Namespace, pane: str) -> tuple[Verdict, str]:
    transcript = window_transcript(env, args.pid)
    if transcript is not None and args.after is not None:
        wait_for_reply(env, transcript, args.after, args.wait)
    if args.dry_run:
        state = State.load(env.state_dir)
        verdict = window_verdict(transcript, state.last_probe, pane)
        return verdict, update_gate(state, args.name, verdict)  # not saved
    with locked_state(env.state_dir) as state:
        verdict = window_verdict(transcript, state.last_probe, pane)
        last = state.last_probe
        failing = verdict.state == NEEDS_LOGIN or (last is not None and last.outcome == claude_state.AUTH)
        if args.probe and failing and probe_due(last, env.now()):
            verdict = window_verdict(transcript, check_login(env, state) or last, pane)
        return verdict, update_gate(state, args.name, verdict)


def check_global(env: Env, probe_now: bool) -> Verdict:
    if not probe_now:
        return global_verdict(env.auth_status(env.claude_bin), State.load(env.state_dir).last_probe)
    with locked_state(env.state_dir) as state:
        status = env.auth_status(env.claude_bin)
        probe = state.last_probe
        if status.logged_in is not False:
            probe = state.record_probe(env.probe(env.claude_bin), env.now())
        return global_verdict(status, probe, fresh=True)


def render_status(env: Env, windows: list[tuple[str, str]]) -> str:
    """The login and each Claude window, for `bosun status`. Reads only."""
    state = State.load(env.state_dir)
    overall = global_verdict(env.auth_status(env.claude_bin), state.last_probe)
    width = max([STATUS_NAME_WIDTH, *(len(name) for name, _ in windows)])
    lines = [f"{'login':<{width}} {overall.state:<12} {overall.detail}"]
    for name, pid in windows:
        verdict = window_verdict(window_transcript(env, pid), state.last_probe)
        lines.append(f"{name:<{width}} {verdict.state:<12} {verdict.detail}")
    return "\n".join(lines)


# ---- command line ------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="login.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    window = commands.add_parser("window")
    window.add_argument("name")
    window.add_argument("pid")
    window.add_argument("--after", type=float)
    window.add_argument("--wait", type=float, default=0)
    window.add_argument("--probe", action="store_true")
    window.add_argument("--pane", choices=["-"])
    window.add_argument("--dry-run", action="store_true")
    overall = commands.add_parser("global")
    overall.add_argument("--probe", action="store_true")
    commands.add_parser("status")
    return parser


def read_windows(text: str) -> list[tuple[str, str]]:
    pairs = (line.split("\t", 1) for line in text.splitlines() if "\t" in line)
    return [(name, pid.strip()) for name, pid in pairs if pid.strip()]


def run(args: argparse.Namespace, env: Env) -> int:
    if args.command == "status":
        print(render_status(env, read_windows(sys.stdin.read())))
        return 0
    if args.command == "global":
        verdict = check_global(env, args.probe)
        print(verdict.detail)
        return EXIT_CODES[verdict.state]
    verdict, change = check_window(env, args, sys.stdin.read() if args.pane else "")
    print(f"{verdict.state} {change} {verdict.detail}")
    return EXIT_CODES[verdict.state]


def main(argv: list[str]) -> int:
    return run(build_parser().parse_args(argv), Env.from_environ())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
