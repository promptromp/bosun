"""What each window gets once an update has switched, decided turn by turn (docs/design/update.md,
"Bringing the agents along"). The decisions, over the update's record: no tmux, process or note
file is touched here. lib/update_windows.py carries them out (its Commands are the Actions here, the
note files, the update's wait and Ctrl-C).

After its release, `bosun update` records step `agents` with each window the plan gave something to
do pending: every window but those left alone and the checkin window, whose loop the release
restarted. A window stays pending until it answers with its status (`bosun update note --window <w>
--status`). advance() takes a turn at each pending window: it decides again, from the window's
status now, what the window gets, and does it once:

- re-read: the orchestrator, when only its prompts changed, gets the update prompt (re-read them,
  then check in) once its turn is over. One still on another conversation than its next launch takes
  up (yesterday's) is restarted into that one instead, as a new day does;
- restart: a window something it was launched with changed for, once idle, restarts on the new
  version with the update prompt;
- take stock first: one where only background work runs (shell) is asked to list that work, which a
  restart ends (`--stock`); once the list is there, or nothing runs any more (idle), it restarts with
  the list in the update prompt, so it starts that work again;
- wait while it is busy, and never touch one waiting for me (a permission prompt, a question);
- relaunch one whose agent exited.

A restart left running (busy again, someone typing) is tried again at the next turn, as is a prompt
that could not be delivered. A window restarted into its next conversation (an orchestrator still on
yesterday's, a repo window with a fresh one pending) starts that conversation's own prompt and never
sees the update prompt, so the turn notes its status itself. What was done to each window is
recorded in update.json (`windows`) as it is done, so no later turn does it again. Once no window is
pending, the record says `done`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import update_gate
import update_plan
import update_state
from update_plan import Treatment, WindowFacts
from update_state import UpdateState


STOCK = "stock"  # what a window listed of its background work before its restart
STATUS = "status"  # its answer once it caught up and carried on
KINDS = (STOCK, STATUS)
TURN_OVER = ("idle", "shell")
GONE = "gone"  # a window no longer open in the session
NOTHING_RUNNING = "nothing running"  # the stock note of a window whose background work ended

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
    "the list with `bosun update note --window {window} --stock` (the list on stdin; if nothing runs any "
    'more, save "nothing running"). Then stop and wait; start nothing new.'
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
SEEN_LINE = (
    "Before the restart, bosun saw this background work still running; start again what still needs "
    "doing: {running}.\n"
)
SEEN_RUNNING = "(What bosun saw still running then: {running}.)"
NEXT_STATUS = "started its next conversation"


class Started(StrEnum):
    """What a restart or a relaunch did (`bosun refresh --update-restart`'s exit status)."""

    YES = "started"  # with the update prompt
    LEFT_RUNNING = "left running"  # busy again, waiting, someone typing (exit 3): try again
    NEXT_CONVERSATION = "next conversation"  # into its next conversation, with its own prompt (exit 4)
    FAILED = "failed"  # anything else: try again


class Notes(Protocol):
    """What the windows answer: a note per window and kind (stock or status)."""

    def read(self, window: str, kind: str) -> str | None:
        """<window>'s <kind> note, or None while there is none."""

    def save(self, window: str, kind: str, text: str) -> None:
        """<window>'s <kind> note: <text>."""

    def where(self, window: str, kind: str) -> str:
        """Where <window>'s <kind> note is kept, to name it."""


class Actions(Protocol):
    """What a turn does to a window, and what it asks before."""

    @property
    def notes(self) -> Notes:
        """Where the windows' notes are."""

    def send(self, window: str, text: str) -> bool:
        """Paste <text> into <window>'s agent; whether it was delivered."""

    def restart(self, window: str, prompt: str) -> Started:
        """Restart <window>'s agent on the new version with <prompt>, if its turn is over."""

    def relaunch(self, window: str, prompt: str) -> Started:
        """Start <window>'s agent again, which exited, with <prompt>."""

    def left_running(self, window: str) -> str:
        """What the last turn that ended in <window> left running, described; "" when nothing."""

    def on_another_conversation(self, window: str) -> bool:
        """Whether <window>'s agent runs another conversation than its next launch takes up."""


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
    re-read, the catch-up for its kind, and the background work to start again (<stock>, the
    prompt's own lines about it: stock_lines)."""
    return UPDATE_PROMPT.format(
        from_=_short(state.from_commit),
        to=_short(state.to_commit),
        how=how,
        reread=REREAD_LINE.format(files=", ".join(reread)) if reread else "",
        catch_up=CATCH_UP.get(kind, CATCH_UP["repo"]).format(window=window),
        stock=stock,
        window=window,
    )


def stock_lines(listed: str | None, running: str) -> str:
    """The update prompt's lines about background work to start again: the list <listed> before the
    restart (none, or "nothing running": none), and what bosun saw still running then (<running>)."""
    if listed is not None and listed.strip().rstrip(".").lower() != NOTHING_RUNNING:
        seen = f"\n{SEEN_RUNNING.format(running=running)}" if running else ""
        return STOCK_LINE.format(stock=listed + seen)
    return SEEN_LINE.format(running=running) if running else ""


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

    def note(self, kind: str) -> str | None:
        return self.act.notes.read(self.name, kind)

    def stock(self) -> str:
        """The update prompt's lines about the background work to start again: what the window listed,
        and what the Stop hook saw still running, once it was asked to take stock."""
        if ASKED_STOCK not in self.done:
            return ""
        return stock_lines(self.note(STOCK), self.act.left_running(self.name))


def _decide(win: _Window, status: str) -> Treatment:
    """What the window gets, from its status now. The orchestrator, only its prompts changed, re-reads
    once its turn is over, unless it is still on another conversation than its next launch takes up:
    that one starts as a new day does (a restart, which refresh starts as its next conversation).
    One whose launch changed: as the plan's table says, by status."""
    if win.planned == Treatment.REREAD.value and status != "exited":
        if status not in TURN_OVER:
            return Treatment.WAIT
        return Treatment.RESTART if win.act.on_another_conversation(win.name) else Treatment.REREAD
    return update_plan.treatment(WindowFacts(win.name, win.kind, status, launch_changed=True), reread=False)


def _started(win: _Window, started: Started, what: str) -> UpdateState:
    if started is Started.YES:
        return win.did(what)
    if started is Started.NEXT_CONVERSATION:
        win.act.notes.save(win.name, STATUS, NEXT_STATUS)
        return win.did(NEXT_CONVERSATION)
    return win.state  # left running, or it failed: the next turn decides again


def _reread(win: _Window) -> UpdateState:
    prompt = prompt_for(win.name, win.kind, win.state, HOW_KEPT, win.state.reread, "")
    return win.did(ASKED_REREAD) if win.act.send(win.name, prompt) else win.state


def _restart(win: _Window) -> UpdateState:
    """Restarted with the update prompt (idle: nothing runs, so a restart ends nothing, whether or not
    it saved its list), with the background work it listed or bosun saw, if it was asked for that."""
    prompt = prompt_for(win.name, win.kind, win.state, HOW_RESTARTED, (), win.stock())
    return _started(win, win.act.restart(win.name, prompt), RESTARTED)


def _take_stock(win: _Window) -> UpdateState:
    """Only background work runs: asked to list it, then restarted once the list is there."""
    if ASKED_STOCK not in win.done:
        asked = win.act.send(win.name, take_stock_prompt(win.name, win.state))
        return win.did(ASKED_STOCK) if asked else win.state
    return _restart(win) if win.note(STOCK) is not None else win.state


def _relaunch(win: _Window) -> UpdateState:
    prompt = prompt_for(win.name, win.kind, win.state, HOW_RELAUNCHED, (), win.stock())
    return _started(win, win.act.relaunch(win.name, prompt), RELAUNCHED)


_ACTIONS: dict[Treatment, Callable[[_Window], UpdateState]] = {
    Treatment.REREAD: _reread,
    Treatment.RESTART: _restart,
    Treatment.TAKE_STOCK: _take_stock,
    Treatment.RELAUNCH: _relaunch,
}


def _bring(win: _Window, status: str) -> UpdateState:
    """The window's turn: nothing once it was started or answered; closed when it is gone; else what
    its status now calls for (nothing while it is busy or waiting for me)."""
    if set(win.done) & {*STARTED, *FINISHED} or win.note(STATUS) is not None:
        return win.state
    if status == GONE:
        return win.did(CLOSED)
    action = _ACTIONS.get(_decide(win, status))
    return action(win) if action else win.state


def _settled(state_dir: Path, state: UpdateState, notes: Notes) -> UpdateState:
    """The windows that answered (or closed) no longer pending, each answer recorded; `done` once
    none is left. Written only when that changes anything."""
    for window in state.pending:
        if notes.read(window, STATUS) is not None and ANSWERED not in _entry(state, window)[2]:
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
    return list(_settled(state_dir, state, act.notes).pending)


# ---- what came of each window ------------------------------------------------------------------


def to_bring_along(
    windows: Sequence[tuple[str, Treatment]], kinds: Mapping[str, str]
) -> dict[str, tuple[str, ...]]:
    """Each window of the plan's that gets something to do, with its kind (the plan's) and the
    treatment the plan gave it. A window the plan names no kind for is a ValueError: never guessed."""
    unknown = [name for name, how in windows if how not in NOT_PENDING and name not in kinds]
    if unknown:
        raise ValueError(f"the plan names no kind for {', '.join(unknown)}")
    return {name: (kinds[name], how.value) for name, how in windows if how not in NOT_PENDING}


def outcomes(state: UpdateState, notes: Notes) -> list[tuple[str, str]]:
    """Each window brought along, and what came of it in a line: what was done, its answer, where the
    background work it listed is kept when it never got it back (it started its next conversation),
    or that it is still waited for."""
    found = []
    for window, entry in state.windows.items():
        done = [what for what in entry[2:] if what != ANSWERED]
        line = ", ".join(done) or "nothing yet"
        answer = notes.read(window, STATUS)
        if NEXT_CONVERSATION in done:
            if notes.read(window, STOCK) is not None:
                line += f" (the background work it listed: {notes.where(window, STOCK)})"
        elif answer and ANSWERED in entry:
            line += f"; {answer.splitlines()[0]}"
        if window in state.pending:
            line += "; still waited for"
        found.append((window, line))
    return found
