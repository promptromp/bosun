#!/usr/bin/env python3
"""Is Claude Code still logged in, and does each window's session still get real replies?

A login can expire or be revoked at any time; a session then answers every prompt with an error
reply and nothing else, and a check-in loop that only pastes prompts never notices. The signals,
best first (everything Claude Code-specific is read through lib/claude_state.py):

1. The window's transcript: the structured error fields of its latest reply
   (authentication_failed, HTTP 401: the login). Exact and free, but only once a prompt failed.
2. `claude auth status --json`: free, and tells a missing login apart from a stored one, but it
   makes no request, so a token the server revoked still shows as logged in.
3. The login check request (probe, below: claude_state.probe with none of my plugins): one tiny
   real request, a fraction of a cent. The morning setup step (ensure_claude_login) makes one; the
   check-in loop makes one while a window or the last check reports a login error, at most every
   PROBE_EVERY_MINUTES, to learn whether the login works again (a /login is not written to any
   transcript).
4. Fallback: a loose reading of the reply's text (or, without a transcript, of the window's last
   lines), only where no structured field exists. Verdicts from it say "pattern fallback".

The login is this Mac's, not one window's: a login check that worked, or a reply the API gave in
any window, after every window's last login error clears them all (see Worked). `status`, the
watchdog and the console read every window; `window` its own and those --also names.

A window can also get nothing but "Credit balance too low" (billing_error: an API key whose
organization has no credit that covers Claude Code): no-credit. Its login works, so it is not
gated: as with a usage limit, the check-in loop keeps sending to it, and the watchdog's no-credit
check alerts. The login check request cannot tell when credit was added (`claude -p` works
without it), so only a normal reply ends it, in that window or (credit being the organization's)
in another one after every window's last credit error.

Usage: login.py window <name> <claude pid> [--after <epoch>] [--wait <seconds>] [--probe] [--pane -]
                         [--dry-run] [--also <name>=<claude pid>]...
       login.py global [--probe]
       login.py status          (lines "<window>\\t<claude pid>" on stdin, as window_agent_pids)

`window` prints "<state> <change> <detail>": state ok, needs-login, no-credit or unknown; change
new (it has just started needing a login), same, recovered (it no longer does) or -. It records
the windows that need a login, and the last login check, in $BOSUN_STATE_DIR/login.json.
--after/--wait: first wait up to <seconds> for a reply newer than <epoch> (after a delivery).
--probe: if the window needs a login, or the last login check failed, make the login check
request when one is due. --pane -: the window's visible text on stdin, read only when there is no
transcript. --dry-run: record nothing. --also: another Claude window, whose replies count too (the
check-in loop passes them while the window is held back, so it is let go on the same signs as the
watchdog's).
`global` prints the state of the login itself; --probe makes the login check request.
`status` prints both for `bosun status`, changing nothing.
Exit codes: 0 ok, 1 needs a login, 3 unknown, 4 no credit, 2 usage. Settings: BOSUN_STATE_DIR,
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
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import plugins_claude
from claude_state import ApiError, AuthStatus, ProbeResult
from connectors import ConnectorsError


OK = "ok"
NEEDS_LOGIN = "needs-login"
NO_CREDIT = "no-credit"  # every reply is "Credit balance too low"; never gated
UNKNOWN = "unknown"
EXIT_CODES = {OK: 0, NEEDS_LOGIN: 1, UNKNOWN: 3, NO_CREDIT: 4}
LOGIN_WORKS = frozenset({OK, NO_CREDIT})  # the window's replies show that its login works
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
    state: str  # ok, needs-login, no-credit or unknown
    detail: str
    since: datetime | None = None  # needs-login, no-credit: since when


def error_detail(error: ApiError) -> str:
    how = "pattern fallback, no structured fields" if error.source == claude_state.PATTERN else error.code
    return f"every reply since {shown(error.since)} is a login error ({how})"


def credit_detail(error: ApiError) -> str:
    return f"every reply since {shown(error.since)} is 'credit balance too low' ({error.code})"


# The login and the credit are this Mac's, not one window's: a sign that one works, later than the
# last error of its kind in every window read, clears those windows: for the login, a login check
# that worked or a reply the API gave in any window; for the credit, only such a reply (`claude -p`,
# the login check, works without credit). This assumes that every window bosun starts uses the same
# credentials (one ~/.claude, one apiKeyHelper, one environment); a repo's own .claude/settings.json
# can set `env` or `apiKeyHelper`, and then its replies say nothing of the others. A window cleared
# wrongly shows again with its next prompt, which fails the same way, as a new row (restarted).


@dataclass(frozen=True)
class Worked:
    """A sign that the login works, or that credit covers Claude Code: when, and what it was."""

    at: datetime
    how: str  # "a login check at Mon 09:10 worked", "alpha got a reply at Mon 09:12"


def probe_worked(probe: Probe | None) -> Worked | None:
    if probe is None or probe.outcome != claude_state.PROBE_OK:
        return None
    return Worked(probe.at, f"a login check at {shown(probe.at)} worked")


@dataclass(frozen=True)
class Reading:
    """What a window's transcript shows: its latest error (None: a normal reply, or none yet) and
    when the API last answered it."""

    path: Path
    error: ApiError | None
    answered: datetime | None


UNREADABLE = Verdict(UNKNOWN, "its transcript could not be read (gone, or no permission)")


def read_transcript(path: Path) -> Reading | None:
    """<path> read, or None if it cannot be (gone since it was found, or no permission)."""
    try:
        return Reading(path, claude_state.last_api_error(path), claude_state.last_answer_at(path))
    except OSError:
        return None


def latest_answer(answered: Mapping[str, datetime | None]) -> Worked | None:
    """The latest reply the API gave in any of these windows (name -> when it last answered)."""
    answers = [(at, name) for name, at in answered.items() if at]
    if not answers:
        return None
    at, name = max(answers, key=lambda answer: answer[0])
    return Worked(at, f"{name} got a reply at {shown(at)}")


def later_than_all(sign: Worked, category: str, errors: Iterable[ApiError | None]) -> bool:
    """Whether <sign> came after every error of <category> (an error with no time: never)."""
    times = [error.at for error in errors if error and error.category == category]
    return all(at is not None and sign.at > at for at in times)


def signs_of(probe: Probe | None, answer: Worked | None) -> dict[str, Worked | None]:
    """The latest sign, per category, that it works: the login, from a login check that worked or a
    reply; the credit, from a reply only."""
    return {claude_state.AUTH: latest(probe_worked(probe), answer), claude_state.CREDIT: answer}


def cleared_by(signs: Mapping[str, Worked | None], errors: Iterable[ApiError | None]) -> dict[str, Worked]:
    """Per category, the sign that clears the windows' errors of it, if any."""
    found = list(errors)
    return {kind: sign for kind, sign in signs.items() if sign and later_than_all(sign, kind, found)}


def restarted(
    transcript: Path, error: ApiError | None, signs: Mapping[str, Worked | None]
) -> ApiError | None:
    """<error>, its row starting again after the latest sign of its kind when that came within the row:
    it worked then, so only the errors after it count (for `since`, and so the watchdog's key)."""
    sign = signs.get(error.category) if error else None
    if error is None or sign is None or not (error.since and error.at and error.since <= sign.at < error.at):
        return error
    try:
        return claude_state.last_api_error(transcript, after=sign.at)
    except OSError:
        return error  # gone or unreadable since it was read: its row as it was


def latest(*signs: Worked | None) -> Worked | None:
    found = [sign for sign in signs if sign]
    return max(found, key=lambda sign: sign.at) if found else None


def transcript_verdict(error: ApiError | None, cleared: Mapping[str, Worked] | None = None) -> Verdict:
    """A window's verdict from its latest error; <cleared>: what clears an error of each category."""
    if error is None:
        return Verdict(OK, "its latest reply is a normal one")
    sign = (cleared or {}).get(error.category)
    if error.category == claude_state.CREDIT:
        if sign:
            return Verdict(OK, f"{sign.how}, after its last 'credit balance too low'")
        return Verdict(NO_CREDIT, credit_detail(error), error.since)
    if error.category != claude_state.AUTH:
        return Verdict(OK, f"its latest reply is a {error.category} error ({error.code}), not a login error")
    if sign:
        return Verdict(OK, f"{sign.how}, after its last login error")
    return Verdict(NEEDS_LOGIN, error_detail(error), error.since)


def pane_verdict(pane: str) -> Verdict:
    """A window with no transcript: its last lines, if given (the pattern fallback)."""
    if pane and claude_state.pane_auth_error(pane):
        return Verdict(
            NEEDS_LOGIN, "no transcript; its last lines read like a login error (pattern fallback)"
        )
    return Verdict(UNKNOWN, "no transcript to read yet")


def window_verdicts(
    transcripts: Mapping[str, Path | None], probe: Probe | None, panes: Mapping[str, str] | None = None
) -> dict[str, Verdict]:
    """Whether the session in each window gets real replies, from its transcript, the last login check
    and what the other windows show (see Worked); a window without a transcript, from its last lines in
    <panes>, if given (the pattern fallback). A transcript that cannot be read counts for nothing, and
    its window's verdict is unknown."""
    readings = {name: read_transcript(path) for name, path in transcripts.items() if path is not None}
    read = {name: reading for name, reading in readings.items() if reading is not None}
    signs = signs_of(probe, latest_answer({name: reading.answered for name, reading in read.items()}))
    errors = {name: restarted(reading.path, reading.error, signs) for name, reading in read.items()}
    cleared = cleared_by(signs, errors.values())
    return {
        name: verdict_in(name, readings, errors, cleared, (panes or {}).get(name, "")) for name in transcripts
    }


def verdict_in(
    name: str,
    readings: Mapping[str, Reading | None],
    errors: Mapping[str, ApiError | None],
    cleared: Mapping[str, Worked],
    pane: str,
) -> Verdict:
    """<name>'s verdict: from its transcript's latest error, unknown if it could not be read, from
    <pane> if it has none."""
    if name not in readings:
        return pane_verdict(pane)
    if name not in errors:
        return UNREADABLE
    return transcript_verdict(errors[name], cleared)


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
    """Record whether <window> needs a login; how that changed. A window with no credit is not
    gated (nor is one on a usage limit): its replies come from the API, so its login works."""
    before = state.gated.get(window)
    if verdict.state == NEEDS_LOGIN:
        state.gated[window] = stamp(verdict.since) or "?"
        return SAME if before == state.gated[window] else NEW
    if verdict.state in LOGIN_WORKS and before is not None:
        del state.gated[window]
        return RECOVERED
    return UNCHANGED


def probe_due(probe: Probe | None, now: datetime) -> bool:
    return probe is None or now - probe.at >= timedelta(minutes=PROBE_EVERY_MINUTES)


# ---- what the commands do ----------------------------------------------------------------------


def probe(claude_bin: str) -> ProbeResult:
    """The login check request (claude_state.probe), loading none of my plugins (a `false` for each
    installed or named by my settings, on or off there: plugins_claude.headless_settings), whose hooks could
    hold it up or answer in its place. An installed_plugins.json that does not parse makes no request: the
    check could not tell, saying why (unknown, as for a request that could not start)."""

    def none_of_mine(folder: Path) -> dict[str, Any]:
        return plugins_claude.headless_settings(folder, who="the login check", reads_settings=False)

    try:
        return claude_state.probe(claude_bin, none_of_mine)
    except ConnectorsError as exc:
        return ProbeResult(claude_state.PROBE_UNKNOWN, code=str(exc))


@dataclass(frozen=True)
class Env:
    state_dir: Path
    claude_bin: str
    now: Callable[[], datetime] = now_utc
    auth_status: Callable[[str], AuthStatus] = claude_state.auth_status
    probe: Callable[[str], ProbeResult] = probe
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
        try:
            latest = claude_state.last_reply_at(transcript)
        except OSError:
            return  # gone or unreadable: the verdict says so
        if (latest and latest.timestamp() > after) or time.monotonic() >= deadline:
            return
        env.sleep(REPLY_POLL_SECONDS)


def verdict_of(name: str, windows: Mapping[str, Path | None], probe: Probe | None, pane: str) -> Verdict:
    """<name>'s verdict among <windows> (name -> transcript); <pane>: its last lines."""
    return window_verdicts(windows, probe, {name: pane})[name]


def other_transcripts(env: Env, others: list[tuple[str, str]]) -> dict[str, Path | None]:
    """The transcripts of the windows --also names (name, pid)."""
    return {name: window_transcript(env, pid) for name, pid in others}


def check_window(env: Env, args: argparse.Namespace, pane: str) -> tuple[Verdict, str]:
    transcript = window_transcript(env, args.pid)
    if transcript is not None and args.after is not None:
        wait_for_reply(env, transcript, args.after, args.wait)
    windows = {**other_transcripts(env, args.also), args.name: transcript}
    if args.dry_run:
        state = State.load(env.state_dir)
        verdict = verdict_of(args.name, windows, state.last_probe, pane)
        return verdict, update_gate(state, args.name, verdict)  # not saved
    with locked_state(env.state_dir) as state:
        verdict = verdict_of(args.name, windows, state.last_probe, pane)
        last = state.last_probe
        failing = verdict.state == NEEDS_LOGIN or (last is not None and last.outcome == claude_state.AUTH)
        if args.probe and failing and probe_due(last, env.now()):
            verdict = verdict_of(args.name, windows, check_login(env, state) or last, pane)
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
    verdicts = window_verdicts({name: window_transcript(env, pid) for name, pid in windows}, state.last_probe)
    for name, verdict in verdicts.items():
        lines.append(f"{name:<{width}} {verdict.state:<12} {verdict.detail}")
    return "\n".join(lines)


# ---- command line ------------------------------------------------------------------------------


def other_window(value: str) -> tuple[str, str]:
    """--also's value: another window and the pid of the Claude in it."""
    name, _, pid = value.rpartition("=")
    if not name or not pid:
        raise argparse.ArgumentTypeError(f"expected NAME=PID, not {value!r}")
    return name, pid


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
    window.add_argument("--also", action="append", default=[], type=other_window, metavar="NAME=PID")
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
