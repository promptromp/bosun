"""Bringing the agents along once an update has switched (docs/design/update.md, "Bringing the agents
along").

After its release, `bosun update` (lib/update.py) records step `agents` with each window the plan
gave something to do pending: every window but those left alone and the checkin window, whose loop
the release restarted. A window stays pending until it answers with its status (`bosun update note
--window <w> --status`, the text on stdin). advance() takes a turn at each pending window: it decides
again, from the window's status now, what the window gets, and does it once:

- re-read: the orchestrator, when only its prompts changed, gets the update prompt (re-read them,
  then check in) once its turn is over, through `bosun send`;
- restart: a window something it was launched with changed for, once idle, restarts on the new
  version with the update prompt (`bosun refresh --update-restart`);
- take stock first: one where only background work runs (shell) is asked to list that work, which a
  restart ends (`--stock`); once the list is there and its turn is over, it restarts with the list
  in the update prompt, so it starts that work again;
- wait while it is busy, and never touch one waiting for me (a permission prompt, a question);
- relaunch one whose agent exited, with the update prompt.

A restart that refresh leaves running (busy again, someone typing) is tried again at the next turn,
as is a prompt `send` could not deliver. A window restarted into its next conversation (an
orchestrator still on yesterday's, a repo window with a fresh one pending) starts that
conversation's own prompt and never sees the update prompt, so the turn notes its status itself.
What was done to each window is recorded in update.json (`windows`) as it is done, and a later turn
never does it again: the update's own wait (bring_along, a turn every POLL_SECONDS) and, after a
Ctrl-C, the check-in loop's (`bosun update advance`), which take update.lock in turn. Once no window
is pending, the record says `done`.

The windows' notes are files in the state folder: update/notes/<window>.<kind>.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import re
import shutil
import signal
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import install_paths
import update_facts
import update_gate
import update_plan
import update_state
from update_plan import Plan, Treatment, WindowFacts
from update_state import UpdateState


NOTES = Path(update_state.UPDATE_DIR) / "notes"
STOCK = "stock"  # what a window listed of its background work before its restart
STATUS = "status"  # its answer once it caught up and carried on
KINDS = (STOCK, STATUS)
PLAIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
POLL_SECONDS = 5
CTRL_C_POLL_SECONDS = 0.2
TURN_OVER = ("idle", "shell")
GONE = "gone"  # a window no longer open in the session
LEFT_RUNNING_STATUS = 3  # bosun refresh --update-restart's status when it left the agent running
NEXT_CONVERSATION_STATUS = 4  # ... and when it restarted the agent into its next conversation

# What was done to a window, as update.json records it after its kind and the plan's treatment.
ASKED_STOCK = "asked to take stock"
ASKED_REREAD = "asked to re-read"
RESTARTED = "restarted"
RELAUNCHED = "relaunched"
NEXT_CONVERSATION = "started its next conversation"
ANSWERED = "answered"
CLOSED = "closed"
# After any of these a window only has to answer.
STARTED = (ASKED_REREAD, RESTARTED, RELAUNCHED, NEXT_CONVERSATION)
FINISHED = (ANSWERED, CLOSED)
NOT_PENDING = (Treatment.LEAVE, Treatment.RESTART_LOOP)  # the checkin window's loop is the release's

TAKE_STOCK_PROMPT = (
    "bosun will restart this session for an update ({from_} -> {to}), which ends your background "
    "work. List each background task still running and what you would do when it finishes, and save "
    "the list with `bosun update note --window {window} --stock` (the list on stdin). Then stop and "
    "wait; start nothing new."
)
UPDATE_PROMPT = (
    "bosun was updated from {from_} to {to}; this session was {how}. {reread}Check for anything "
    "missed just before: {catch_up}. {stock}Then continue what you were doing, and send a one-line "
    "status with `bosun update note --window {window} --status` (on stdin)."
)
HOW_RESTARTED = "restarted on the new version"
HOW_KEPT = "kept running"
HOW_RELAUNCHED = "started again on the new version, as its agent had exited"
CATCH_UP = {
    "orchestrator": (
        "run a check-in (`/bosun:chores check-in`), which covers the events queued during the update "
        "and Slack since your last scan"
    ),
    "repo": (
        "the items this window holds in the ledger (`bosun ledger in-flight {window}`) and any replies "
        "to their outreach (`bosun ledger outreach --parent '<id>'`)"
    ),
}
REREAD_LINE = "Re-read {files}, which this update changed. "
STOCK_LINE = (
    "Before the restart you listed this background work; start again what still needs doing:\n{stock}\n"
)
SEEN_RUNNING = "(What bosun saw still running then: {running}.)"
NEXT_STATUS = "started its next conversation"


class NoteRefused(ValueError):
    """A note for a kind there is none of, or under a window name that is no plain name."""


class Started(StrEnum):
    """What a restart or a relaunch did (`bosun refresh --update-restart`'s exit status)."""

    YES = "started"  # with the update prompt
    LEFT_RUNNING = "left running"  # busy again, waiting, someone typing (exit 3): try again
    NEXT_CONVERSATION = "next conversation"  # into its next conversation, with its own prompt (exit 4)
    FAILED = "failed"  # anything else: try again


class Actions(Protocol):
    def send(self, window: str, text: str) -> bool:
        """Paste <text> into <window>'s agent; whether it was delivered."""

    def restart(self, window: str, prompt: str) -> Started:
        """Restart <window>'s agent on the new version with <prompt>, if its turn is over."""

    def relaunch(self, window: str, prompt: str) -> Started:
        """Start <window>'s agent again, which exited, with <prompt>."""


# ---- notes -----------------------------------------------------------------------------------


def note_path(state_dir: Path, window: str, kind: str) -> Path:
    if kind not in KINDS:
        raise NoteRefused(f"there is no {kind!r} note: a note is the window's {' or '.join(KINDS)}")
    if not PLAIN_NAME.match(window) or ".." in window:
        raise NoteRefused(f"{window!r} is not a plain window name")
    return state_dir / NOTES / f"{window}.{kind}"


def note(state_dir: Path, window: str, kind: str, text: str) -> None:
    """<window>'s <kind> note: <text>, written whole (update/notes/<window>.<kind>)."""
    path = note_path(state_dir, window, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as out:
        out.write(text.strip() + "\n")
    Path(tmp).replace(path)


def read_note(state_dir: Path, window: str, kind: str) -> str | None:
    try:
        return note_path(state_dir, window, kind).read_text().strip()
    except FileNotFoundError:
        return None


def _clear_notes(state_dir: Path) -> None:
    """The notes an earlier update left: none of them answers this one."""
    shutil.rmtree(state_dir / NOTES, ignore_errors=True)


# ---- prompts ---------------------------------------------------------------------------------


def _short(commit: str) -> str:
    return commit[: update_gate.SHORT]


def take_stock_prompt(window: str, state: UpdateState) -> str:
    return TAKE_STOCK_PROMPT.format(
        from_=_short(state.from_commit), to=_short(state.to_commit), window=window
    )


def prompt_for(
    window: str, kind: str, state: UpdateState, how: str, reread: Sequence[str], stock: str
) -> str:
    """The update prompt for <window> (of <kind>: orchestrator or repo), which was <how>: the files to
    re-read, the catch-up for its kind, and the background work it listed (<stock>) to start again."""
    return UPDATE_PROMPT.format(
        from_=_short(state.from_commit),
        to=_short(state.to_commit),
        how=how,
        reread=REREAD_LINE.format(files=", ".join(reread)) if reread else "",
        catch_up=CATCH_UP.get(kind, CATCH_UP["repo"]).format(window=window),
        stock=STOCK_LINE.format(stock=stock) if stock else "",
        window=window,
    )


def _stock_of(state_dir: Path, window: str) -> str:
    """The background work <window> listed before its restart, with what bosun saw still running then."""
    listed = read_note(state_dir, window, STOCK)
    if listed is None:
        return ""
    running = update_facts.left_running(state_dir, window)
    return f"{listed}\n{SEEN_RUNNING.format(running=running)}" if running else listed


# ---- a turn ------------------------------------------------------------------------------------


def _entry(state: UpdateState, window: str) -> tuple[str, str, tuple[str, ...]]:
    """What the record says of <window>: its kind, the plan's treatment, and what was done to it."""
    entry = state.windows.get(window, ())
    kind = entry[0] if entry else "repo"
    planned = entry[1] if len(entry) > 1 else Treatment.RESTART.value
    return kind, planned, tuple(entry[2:])


def _did(state_dir: Path, state: UpdateState, window: str, what: str) -> UpdateState:
    """<what> recorded as done to <window>, at once, so no later turn does it again."""
    kind, planned, done = _entry(state, window)
    state = update_state.advanced(
        state, state.step, windows={**state.windows, window: (kind, planned, *done, what)}
    )
    update_state.write(state_dir, state)
    return state


@dataclass(frozen=True)
class _Window:
    """One pending window at one turn: what the record says of it, and how to act on it."""

    state_dir: Path
    state: UpdateState
    name: str
    act: Actions

    @property
    def kind(self) -> str:
        return _entry(self.state, self.name)[0]

    @property
    def planned(self) -> str:
        return _entry(self.state, self.name)[1]

    @property
    def done(self) -> tuple[str, ...]:
        return _entry(self.state, self.name)[2]

    def did(self, what: str) -> UpdateState:
        return _did(self.state_dir, self.state, self.name, what)

    def stock(self) -> str | None:
        return read_note(self.state_dir, self.name, STOCK)


def _decide(win: _Window, status: str) -> Treatment:
    """What the window gets, from its status now: re-read (the orchestrator, only its prompts changed)
    once its turn is over, or, as the plan's table says for one whose launch changed, by status."""
    if win.planned == Treatment.REREAD.value and status != "exited":
        return Treatment.REREAD if status in TURN_OVER else Treatment.WAIT
    return update_plan.treatment(WindowFacts(win.name, win.kind, status, launch_changed=True), reread=False)


def _started(win: _Window, started: Started, what: str) -> UpdateState:
    if started is Started.YES:
        return win.did(what)
    if started is Started.NEXT_CONVERSATION:
        note(win.state_dir, win.name, STATUS, NEXT_STATUS)
        return win.did(NEXT_CONVERSATION)
    return win.state  # left running, or it failed: the next turn decides again


def _reread(win: _Window) -> UpdateState:
    prompt = prompt_for(win.name, win.kind, win.state, HOW_KEPT, win.state.reread, "")
    return win.did(ASKED_REREAD) if win.act.send(win.name, prompt) else win.state


def _restart(win: _Window) -> UpdateState:
    """Restarted with the update prompt; one asked to take stock only once its list is there."""
    if ASKED_STOCK in win.done and win.stock() is None:
        return win.state
    prompt = prompt_for(win.name, win.kind, win.state, HOW_RESTARTED, (), _stock_of(win.state_dir, win.name))
    return _started(win, win.act.restart(win.name, prompt), RESTARTED)


def _take_stock(win: _Window) -> UpdateState:
    if ASKED_STOCK in win.done:
        return _restart(win)
    asked = win.act.send(win.name, take_stock_prompt(win.name, win.state))
    return win.did(ASKED_STOCK) if asked else win.state


def _relaunch(win: _Window) -> UpdateState:
    prompt = prompt_for(win.name, win.kind, win.state, HOW_RELAUNCHED, (), _stock_of(win.state_dir, win.name))
    return _started(win, win.act.relaunch(win.name, prompt), RELAUNCHED)


_ACTIONS: dict[Treatment, Callable[[_Window], UpdateState]] = {
    Treatment.REREAD: _reread,
    Treatment.RESTART: _restart,
    Treatment.TAKE_STOCK: _take_stock,
    Treatment.RELAUNCH: _relaunch,
}


def _answered(state_dir: Path, window: str) -> bool:
    return read_note(state_dir, window, STATUS) is not None


def _bring(win: _Window, status: str) -> UpdateState:
    """The window's turn: nothing once it was started or answered; closed when it is gone; else what
    its status now calls for (nothing while it is busy or waiting for me)."""
    if set(win.done) & {*STARTED, *FINISHED} or _answered(win.state_dir, win.name):
        return win.state
    if status == GONE:
        return win.did(CLOSED)
    action = _ACTIONS.get(_decide(win, status))
    return action(win) if action else win.state


def _settled(state_dir: Path, state: UpdateState) -> UpdateState:
    """The windows that answered (or closed) no longer pending, each answer recorded; `done` once
    none is left."""
    for window in state.pending:
        if _answered(state_dir, window) and ANSWERED not in _entry(state, window)[2]:
            state = _did(state_dir, state, window, ANSWERED)
    pending = tuple(w for w in state.pending if not set(_entry(state, w)[2]) & set(FINISHED))
    if pending == state.pending:
        return state
    state = update_state.advanced(state, state.step if pending else "done", pending=pending)
    update_state.write(state_dir, state)
    return state


def advance(state_dir: Path, statuses: Callable[[], Mapping[str, str]], act: Actions) -> list[str]:
    """A turn at each window the update in <state_dir> still waits for (the caller holds update.lock):
    each decided again from its status now (<statuses>: by window name, as `bosun sessions` lists
    them) and acted on (<act>) at most once per step. Windows that answered are no longer pending,
    and the record says `done` once none is. Returns the windows still waited for. A listing with no
    window in it (the session's windows could not be listed) changes nothing."""
    state = update_state.read(state_dir)
    if state is None or state.step != "agents":
        return []
    now = statuses()
    if now:
        for window in state.pending:
            state = _bring(_Window(state_dir, state, window, act), now.get(window, GONE))
    return list(_settled(state_dir, state).pending)


# ---- the real actions, and the update's wait -----------------------------------------------------


class Commands:
    """Actions as bosun commands of the version in <root>, each in a session of its own (a Ctrl-C
    never stops one half way), saying (<say>) why one failed, once per window and reason."""

    def __init__(self, root: Path, state_dir: Path, say: Callable[[str], None]) -> None:
        self.root = root
        self.state_dir = state_dir
        self.say = say
        self.said: dict[str, str] = {}

    def _failed(self, window: str, what: str, stderr: str) -> None:
        why = f"{what}: {(stderr.strip().splitlines() or ['it failed'])[-1]}"
        if self.said.get(window) != why:
            self.said[window] = why
            self.say(f"  {window}: {why}")

    def send(self, window: str, text: str) -> bool:
        done = update_facts.bosun(self.root, "send", "--no-item", window, "-", stdin=text, detached=True)
        if done.returncode != 0:
            self._failed(window, "not sent yet", done.stderr)
        return done.returncode == 0

    def restart(self, window: str, prompt: str) -> Started:
        folder = self.state_dir / update_state.UPDATE_DIR
        folder.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(dir=folder, prefix=f"prompt-{window}.")
        with os.fdopen(fd, "w") as out:
            out.write(prompt)
        try:
            done = update_facts.bosun(self.root, "refresh", "--update-restart", path, window, detached=True)
        finally:
            Path(path).unlink(missing_ok=True)
        if done.returncode == 0:
            return Started.YES
        if done.returncode == LEFT_RUNNING_STATUS:
            return Started.LEFT_RUNNING
        if done.returncode == NEXT_CONVERSATION_STATUS:
            return Started.NEXT_CONVERSATION
        self._failed(window, "not restarted yet", done.stderr)
        return Started.FAILED

    def relaunch(self, window: str, prompt: str) -> Started:
        return self.restart(window, prompt)  # refresh --update-restart starts an agent that exited


def _news(before: UpdateState, after: UpdateState, state_dir: Path) -> list[str]:
    """A line for each thing done to a window between the two records."""
    lines = []
    for window, entry in after.windows.items():
        for what in entry[2 + len(before.windows.get(window, ())[2:]) :]:
            said = read_note(state_dir, window, STATUS) if what == ANSWERED else None
            lines.append(f"  {window}: {what}" + (f": {said.splitlines()[0]}" if said else ""))
    return lines


def turn(state_dir: Path, say: Callable[[str], None]) -> list[str]:
    """One turn at the windows the update in <state_dir> still waits for, by bosun's own commands (the
    caller holds update.lock), each thing done said; the windows still waited for."""
    before = update_state.read(state_dir)
    if before is None:
        return []
    root = Path(before.to_root)
    waiting = advance(state_dir, lambda: update_facts.statuses(root), Commands(root, state_dir, say))
    after = update_state.read(state_dir)
    for line in _news(before, after, state_dir) if after else []:
        say(line)
    return waiting


class _CtrlC:
    """Ctrl-C while the update waits on the windows: noted, never raised, so it never cuts a restart
    short; the wait ends at the next turn."""

    def __init__(self) -> None:
        self.asked = False

    def _on_signal(self, *_: object) -> None:
        self.asked = True

    @contextlib.contextmanager
    def noted(self) -> Iterator[None]:
        before = signal.signal(signal.SIGINT, self._on_signal)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, before)

    def wait(self, seconds: float) -> None:
        """<seconds>, or less if Ctrl-C comes."""
        for _ in range(round(seconds / CTRL_C_POLL_SECONDS)):
            if self.asked:
                return
            time.sleep(CTRL_C_POLL_SECONDS)


def _to_bring_along(plan: Plan, kinds: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """Each window the plan gives something to do, with its kind and the plan's treatment."""
    return {
        name: (kinds.get(name, "repo"), how.value) for name, how in plan.windows if how not in NOT_PENDING
    }


def _rereads(plan: Plan, root: Path) -> tuple[str, ...]:
    """The changed files the orchestrator re-reads, through the install link."""
    install = install_paths.install_path(root)
    return tuple(
        str(install / path)
        for path in plan.prompts
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in update_plan.REREAD)
    )


def bring_along(state: UpdateState, plan: Plan, say: Callable[[str], None] = print) -> UpdateState:
    """After the release: `agents` recorded with each window the plan gives something to do pending
    (notes an earlier update left cleared first), then a turn every POLL_SECONDS until each answered
    (`done`), saying which windows it waits for whenever that changes. Ctrl-C stops the wait and
    leaves the windows still pending to the check-in loop (`bosun update advance`), at `agents`."""
    state_dir = update_facts.folders().state_dir
    root = Path(state.to_root)
    windows = _to_bring_along(plan, update_facts.window_kinds(root))
    _clear_notes(state_dir)
    state = update_state.advanced(
        state, "agents", pending=tuple(windows), windows=windows, reread=_rereads(plan, root)
    )
    update_state.write(state_dir, state)
    ctrl_c, waiting = _CtrlC(), list(state.pending)
    said: list[str] = []
    with ctrl_c.noted():
        while waiting and not ctrl_c.asked:
            waiting = turn(state_dir, say)
            if waiting and waiting != said:
                say(f"Waiting for {', '.join(waiting)} (Ctrl-C leaves them to the check-in loop)...")
                said = waiting
            if waiting:
                ctrl_c.wait(POLL_SECONDS)
    state = update_state.read(state_dir) or state
    if waiting:
        say(
            f"Stopped waiting: the check-in loop brings along {', '.join(waiting)}, each once its turn is "
            "over (bosun update advance takes a turn by hand)."
        )
        return state
    if state.step != "done":
        state = update_state.advanced(state, "done", pending=())
        update_state.write(state_dir, state)
    return state


# ---- what came of each window ------------------------------------------------------------------


def outcomes(state_dir: Path, state: UpdateState) -> list[tuple[str, str]]:
    """Each window brought along, and what came of it in a line: what was done, its answer, a list of
    background work it never got back (it started its next conversation), or that it is still
    waited for."""
    found = []
    for window, entry in state.windows.items():
        done = [what for what in entry[2:] if what != ANSWERED]
        line = ", ".join(done) or "nothing yet"
        answer = read_note(state_dir, window, STATUS)
        if NEXT_CONVERSATION in done and read_note(state_dir, window, STOCK) is not None:
            line += f" (the background work it listed: {note_path(state_dir, window, STOCK)})"
        elif answer and ANSWERED in entry:
            line += f"; {answer.splitlines()[0]}"
        if window in state.pending:
            line += "; still waited for"
        found.append((window, line))
    return found
