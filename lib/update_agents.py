"""What each window gets once an update has switched, decided turn by turn (docs/design/update.md,
"Bringing the agents along"). The decisions: the only file read or written here is the update's own
record (lib/update_state.py), which records each thing done as it is done; no tmux, process or note
file is touched. lib/update_windows.py carries them out (its Commands are the Actions here, the
update's wait and Ctrl-C), lib/update_notes.py keeps the notes.

After its release, `bosun update` records step `agents` with each window the plan gave something to
do pending: every window but those left alone and the checkin window, whose loop the release
restarted. A window stays pending until it answers with its status (`bosun update note --window <w>
--status`). advance() takes a turn at each pending window: it decides again, from the window's
status now, what the window gets, and does it once:

- re-read: the orchestrator, when only its prompts changed, gets the update prompt (re-read them,
  then check in) once its turn is over. One still on another conversation than its next launch takes
  up (yesterday's) is brought along as one whose launch changed instead (taking stock first while
  only background work runs), and its restart starts that conversation, as a new day does;
- restart: a window something it was launched with changed for, once idle, restarts on the new
  version with the update prompt;
- take stock first: one where only background work runs (shell) is asked to list that work, which a
  restart ends (`--stock`); once the list is there, or nothing runs any more (idle), it restarts with
  the list in the update prompt, so it starts that work again;
- wait while it is busy, and never touch one waiting for me (a permission prompt, a question);
- relaunch one whose agent exited.

A restart left running (busy again, someone typing) is tried again at the next turn, as is a prompt
that could not be delivered. A restart whose launch failed (refused, its agent exited at once, the window
left a plain shell) is final: the window is recorded as one that did not start, with the launch's reason,
and no longer waited for. A window restarted into its next conversation (an orchestrator still on
yesterday's, a repo window with a fresh one pending) starts that conversation's own prompt and never
sees the update prompt, so the turn notes its status itself. What was done to each window is
recorded in update.json (`windows`) as it is done, so no later turn does it again. Once no window is
pending, the record says `done`.

The windows the last update still waits for when the next one starts are that one's to bring along
(take_over): each with what the new plan decides for it, what it already gave kept (asked to take stock,
its list saved), and an orchestrator's re-read the union of both updates' files. The last update is then
finished (carried_into), each of those windows named as carried into the next.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
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
NEXT_CONVERSATION = "started its next conversation"  # also its status note: it never sees the prompt
ANSWERED = "answered"
CLOSED = "closed"
DID_NOT_START = "did not start"  # recorded with the launch's reason after it: "did not start: <why>"
CARRIED = "carried into the next update"  # what the last update's summary says of a window taken over
# ... of one it still waited for that the next update did not take over: the newest version gives it nothing
LEFT_BY_NEXT = "left alone by the next update: nothing it was launched with differs from the newest version"
# After any of these a window only has to answer.
STARTED = (ASKED_REREAD, RESTARTED, RELAUNCHED, NEXT_CONVERSATION)
FINISHED = (ANSWERED, CLOSED, CARRIED, LEFT_BY_NEXT)  # and DID_NOT_START with its reason: finished()
WAITING = "waiting"  # the status of a window at a permission prompt or a question, waiting for me
# Left as they are (one bosun did not start among them), and the checkin window, whose loop is the release's.
NOT_PENDING = (Treatment.LEAVE, Treatment.NOT_STARTED_BY_BOSUN, Treatment.RESTART_LOOP)
PROMPTS_DIR = "orchestrator"  # the folder of bosun's prompts, left out of their names

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
NOT_STARTED_HINT = "fix that, then bosun refresh {window}"
OWED_TOO = "(owed by the last update too)"


class Started(StrEnum):
    """What a restart or a relaunch did (`bosun refresh --update-restart`'s exit status)."""

    YES = "started"  # with the update prompt
    LEFT_RUNNING = "left running"  # busy again, waiting, someone typing (exit 3): try again
    NEXT_CONVERSATION = "next conversation"  # into its next conversation, with its own prompt (exit 4)
    DID_NOT_START = "did not start"  # its launch failed (exit 5): final, Actions.why_not_started says why
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

    def why_not_started(self, window: str) -> str:
        """Why the last restart or relaunch of <window> did not start it (Started.DID_NOT_START): the
        launch's own reason, in a line."""

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
    state = _with(state, window, what)
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


def _rereads(win: _Window, status: str) -> bool:
    """Whether the window only re-reads: the orchestrator whose prompts alone changed, its agent running
    the conversation its next launch takes up. One still on another (yesterday's) re-reading there would
    be wasted (its check-in is refused there, and the loop restarts it anyway)."""
    return (
        win.planned == Treatment.REREAD.value
        and status != "exited"
        and not win.act.on_another_conversation(win.name)
    )


def _decide(win: _Window, status: str) -> Treatment:
    """What the window gets, from its status now. The orchestrator that only re-reads (_rereads) does so
    once its turn is over. Any other, as the plan's table says for one whose launch changed, by status:
    an orchestrator on yesterday's conversation too, so it takes stock first while only background work
    runs, and refresh starts its restart as its next conversation does (a new day)."""
    if _rereads(win, status):
        return Treatment.REREAD if status in TURN_OVER else Treatment.WAIT
    return update_plan.treatment(WindowFacts(win.name, win.kind, status, launch_changed=True), reread=False)


def _not_started(done: Sequence[str]) -> bool:
    """Whether a window whose record says <done> did not start: its launch failed (DID_NOT_START)."""
    return any(what.startswith(f"{DID_NOT_START}:") for what in done)


def finished(done: Sequence[str]) -> bool:
    """Whether a window whose record says <done> no longer has to answer: it answered, closed, was
    carried into the next update, or did not start."""
    return bool(set(done) & set(FINISHED)) or _not_started(done)


def _started(win: _Window, started: Started, what: str) -> UpdateState:
    if started is Started.YES:
        return win.did(what)
    if started is Started.NEXT_CONVERSATION:
        win.act.notes.save(win.name, STATUS, NEXT_CONVERSATION)
        return win.did(NEXT_CONVERSATION)
    if started is Started.DID_NOT_START:
        why = " ".join(win.act.why_not_started(win.name).split()) or "its launch failed"
        return win.did(f"{DID_NOT_START}: {why}")
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
    if set(win.done) & set(STARTED) or finished(win.done) or win.note(STATUS) is not None:
        return win.state
    if status == GONE:
        return win.did(CLOSED)
    action = _ACTIONS.get(_decide(win, status))
    return action(win) if action else win.state


def _with(state: UpdateState, window: str, what: str) -> UpdateState:
    kind, planned, done = _entry(state, window)
    return update_state.advanced(
        state, state.step, windows={**state.windows, window: (kind, planned, *done, what)}
    )


def settled(state: UpdateState, notes: Notes) -> UpdateState:
    """<state> with the windows that answered (a status note, theirs or one written by hand) or closed
    no longer pending, each answer recorded, and `done` once none is left; <state> itself when nothing
    changes. It acts on no window: an update carried on uses it before it plans."""
    for window in state.pending:
        if notes.read(window, STATUS) is not None and ANSWERED not in _entry(state, window)[2]:
            state = _with(state, window, ANSWERED)
    pending = tuple(w for w in state.pending if not finished(_entry(state, w)[2]))
    if pending and pending == state.pending:
        return state
    return update_state.advanced(state, "done" if not pending else state.step, pending=pending)


def advance(
    state_dir: Path,
    statuses: Callable[[], Mapping[str, str]],
    act: Actions,
    give_way: Callable[[], bool] = lambda: False,
) -> list[str]:
    """A turn at each window the update in <state_dir> still waits for (the caller holds update.lock):
    each decided again from its status now (<statuses>: by window name, as `bosun sessions` lists
    them) and acted on (<act>) at most once per step; before each window after the first, it stops if
    <give_way> says so (an update waits for the lock: it takes the rest over). Windows that answered
    are no longer pending, and the record says `done` once none is. Returns the windows still waited
    for. A listing with no window in it (the session's windows could not be listed) changes nothing."""
    state = update_state.read(state_dir)
    if state is None or state.step != "agents":
        return []
    now = statuses()
    for at, window in enumerate(state.pending if now else ()):
        if at and give_way():
            break
        state = _bring(_Window(state_dir, state, window, act), now.get(window, GONE))
    after = settled(state, act.notes)
    if after != state:
        update_state.write(state_dir, after)
    return list(after.pending)


# ---- windows waiting for me, and the next update taking over ------------------------------------


class Owed(StrEnum):
    """What a window still waited for is owed once its turn is over (owed)."""

    RESTART = "restart"  # a restart (after taking stock, if it was asked to)
    REREAD = "re-read"  # the update prompt, to re-read its prompts
    ANSWER = "answer"  # nothing more: it was started, and only has to answer


def owed(state: UpdateState, window: str) -> Owed:
    """What <window>, which the update <state> records still waits for, is owed."""
    _kind, planned, done = _entry(state, window)
    if set(done) & set(STARTED):
        return Owed.ANSWER
    return Owed.REREAD if planned == Treatment.REREAD.value else Owed.RESTART


def only_waiting_for_me(statuses: Mapping[str, str]) -> bool:
    """Whether every window still waited for (<statuses>: each one's status as listed) waits for me, at
    a permission prompt or a question: none ends its wait by itself, so the update's own wait ends."""
    return bool(statuses) and all(status == WAITING for status in statuses.values())


def take_over(
    windows: Mapping[str, tuple[str, ...]],
    plan: Mapping[str, Treatment],
    reread: Sequence[str],
    last: UpdateState | None,
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...], tuple[str, ...]]:
    """The windows an update brings along (<windows>, to_bring_along's), with those the <last> update
    still waits for taken over, by the new plan (<plan>: each open window's treatment): one it gives
    something to do keeps what it already gave (asked to take stock, its list saved) unless the last
    update had started it already; one it leaves alone still gets what the last update began or owed
    it a re-read for (else nothing: the newest version gives it nothing). The files to re-read are
    <reread> and the last update's, while its re-read was not asked yet. Returns the windows, the
    files to re-read, and the windows whose notes are theirs to keep (what they gave the last update)."""
    taken = dict(windows)
    files, kept = list(reread), list[str]()
    if last is None:
        return taken, tuple(files), ()
    for window in last.pending:
        entry = _taken_entry(_entry(last, window), taken.get(window), window in plan)
        if entry is None:
            continue
        taken[window] = entry
        kept.append(window)
        if entry[1] == Treatment.REREAD.value and ASKED_REREAD not in entry[2:]:
            files += last.reread
    return taken, tuple(dict.fromkeys(files)), tuple(kept)


def _taken_entry(
    last: tuple[str, str, tuple[str, ...]], new: tuple[str, ...] | None, still_open: bool
) -> tuple[str, ...] | None:
    """What a window the last update still waits for (<last>: its kind, its treatment then and what was
    done) is to the next update, which gives it <new> (None: leaves it alone), keeping what it gave the
    last one; None when it is not taken over (closed, started afresh, or given nothing by either)."""
    kind, planned, done = last
    if not still_open or (new is not None and set(done) & set(STARTED)):
        return None
    if new is not None:
        return (*new, *done)
    return (kind, planned, *done) if done or planned == Treatment.REREAD.value else None


def carried_into(last: UpdateState, taken: Collection[str], open_windows: Collection[str]) -> UpdateState:
    """The <last> update (settled) finished as the next one takes over the windows it still waited for:
    `done`, each of those the next took over (<taken>, take_over's) named as carried into it, one no
    longer open (not in <open_windows>, the next plan's) as closed, and any other as left alone by the
    next update (the newest version gives it nothing)."""
    for window in last.pending:
        what = CARRIED if window in taken else CLOSED if window not in open_windows else LEFT_BY_NEXT
        last = _with(last, window, what)
    return update_state.advanced(last, "done", pending=())


def _carried(state: UpdateState) -> frozenset[str]:
    """The windows <state>'s update took over from the last one (`taken_over`)."""
    return frozenset(state.taken_over)


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


def _prompt_name(path: str) -> str:
    """A prompt file the orchestrator re-reads, as bosun names it (orchestrator/tasks/review.md:
    tasks/review.md), from its path through the install link."""
    return path.rsplit(f"/{PROMPTS_DIR}/", 1)[-1]


def _what_came(window: str, entry: Sequence[str], notes: Notes, asked: str) -> str:
    """What came of <window>, whose record is <entry>: what was done (<asked> for its re-read), then
    its answer, or where the background work it listed is kept when it started its next conversation."""
    done = [asked if what == ASKED_REREAD else what for what in entry[2:] if what != ANSWERED]
    line = ", ".join(done) or "nothing yet"
    if NEXT_CONVERSATION in done:
        listed = notes.read(window, STOCK) is not None
        return line + (f" (the background work it listed: {notes.where(window, STOCK)})" if listed else "")
    if _not_started(done):
        return f"{line}; {NOT_STARTED_HINT.format(window=window)}"
    answer = notes.read(window, STATUS)
    return line + (f"; {answer.splitlines()[0]}" if answer and ANSWERED in entry else "")


def outcomes(state: UpdateState, notes: Notes) -> list[tuple[str, str]]:
    """Each window brought along, and what came of it in a line: what was done (the files it was asked
    to re-read named), its answer, where the background work it listed is kept when it never got it
    back (it started its next conversation), or that it is still waited for."""
    asked = f"{ASKED_REREAD} {', '.join(map(_prompt_name, state.reread))}" if state.reread else ASKED_REREAD
    carried = _carried(state)
    return [
        (
            window,
            _what_came(window, entry, notes, asked)
            + ("; still waited for" if window in state.pending else "")
            + (f" {OWED_TOO}" if window in carried else ""),
        )
        for window, entry in state.windows.items()
    ]
