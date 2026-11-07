"""Bringing the windows along, for real (docs/design/update.md, "Bringing the agents along"): the
decisions of lib/update_agents.py carried out by bosun's own commands, the notes the windows answer
with, and the update's wait.

- The notes are files in the state folder, update/notes/<window>.<kind> (`bosun update note`).
- Commands are the turn's Actions: `bosun send` and `bosun refresh --update-restart` of the new
  version, each in a session of its own, so a Ctrl-C at the terminal never stops one half way (a
  restart that stopped an agent and never started it again), and the facts a turn asks for, from one
  listing of the session's windows (`bosun sessions --json`) and the Stop hook's signals.
- turn() is one turn under update.lock; bring_along() is the update's own wait, a turn every
  POLL_SECONDS, after which Ctrl-C (noted, never raised mid-restart) leaves the windows still waited
  for to the check-in loop (`bosun update advance`, each tick).
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
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import install_paths
import update_agents
import update_facts
import update_plan
import update_state
from update_agents import STATUS, Started
from update_plan import Plan
from update_state import UpdateState


NOTES = Path(update_state.UPDATE_DIR) / "notes"
PLAIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
POLL_SECONDS = 5
CTRL_C_POLL_SECONDS = 0.2
LEFT_RUNNING_STATUS = 3  # bosun refresh --update-restart's status when it left the agent running
NEXT_CONVERSATION_STATUS = 4  # ... and when it started the window's next conversation


class NoteRefused(ValueError):
    """A note for a kind there is none of, or under a window name that is no plain name."""


# ---- the notes ---------------------------------------------------------------------------------


def note_path(state_dir: Path, window: str, kind: str) -> Path:
    if kind not in update_agents.KINDS:
        raise NoteRefused(
            f"there is no {kind!r} note: a note is the window's {' or '.join(update_agents.KINDS)}"
        )
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


def clear_notes(state_dir: Path) -> None:
    """The notes an earlier update left: none of them answers this one."""
    shutil.rmtree(state_dir / NOTES, ignore_errors=True)


class Files:
    """update_agents.Notes as the note files in <state_dir>."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

    def read(self, window: str, kind: str) -> str | None:
        return read_note(self.state_dir, window, kind)

    def save(self, window: str, kind: str, text: str) -> None:
        note(self.state_dir, window, kind, text)

    def where(self, window: str, kind: str) -> str:
        return str(note_path(self.state_dir, window, kind))


# ---- the turn's actions ------------------------------------------------------------------------


class Commands:
    """update_agents.Actions as bosun commands of the version in <root>, saying (<say>) why one failed,
    once per window and reason. The windows are listed once, at the first question about them."""

    def __init__(self, root: Path, state_dir: Path, say: Callable[[str], None]) -> None:
        self.root = root
        self.state_dir = state_dir
        self.say = say
        self.notes = Files(state_dir)
        self.said: dict[str, str] = {}
        self.listed: list[dict[str, Any]] | None = None

    def _windows(self) -> list[dict[str, Any]]:
        if self.listed is None:
            self.listed = update_facts.open_windows(self.root)
        return self.listed

    def statuses(self) -> dict[str, str]:
        return {str(w.get("name")): update_facts.status_of(w) for w in self._windows()}

    def on_another_conversation(self, window: str) -> bool:
        return any(
            w.get("name") == window and update_facts.on_another_conversation(w) for w in self._windows()
        )

    def left_running(self, window: str) -> str:
        return update_facts.left_running(self.state_dir, window)

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
            said = read_note(state_dir, window, STATUS) if what == update_agents.ANSWERED else None
            lines.append(f"  {window}: {what}" + (f": {said.splitlines()[0]}" if said else ""))
    return lines


def turn(state_dir: Path, say: Callable[[str], None]) -> list[str]:
    """One turn at the windows the update in <state_dir> still waits for, by bosun's own commands (the
    caller holds update.lock), each thing done said; the windows still waited for."""
    before = update_state.read(state_dir)
    if before is None:
        return []
    commands = Commands(Path(before.to_root), state_dir, say)
    waiting = update_agents.advance(state_dir, commands.statuses, commands)
    after = update_state.read(state_dir)
    for line in _news(before, after, state_dir) if after else []:
        say(line)
    return waiting


# ---- the update's wait -------------------------------------------------------------------------


class _CtrlC:
    """Ctrl-C while the update waits on the windows: noted, never raised, so it never cuts a restart
    short; the wait ends at once, and no turn follows."""

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


def _rereads(plan: Plan, root: Path) -> tuple[str, ...]:
    """The changed files the orchestrator re-reads, through the install link."""
    install = install_paths.install_path(root)
    return tuple(
        str(install / path)
        for path in plan.prompts
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in update_plan.REREAD)
    )


def who_finishes(root: Path, windows: Sequence[str]) -> str:
    """Who brings along <windows> once the update no longer waits: the check-in loop, if its window
    is open in the session, else `bosun update advance` run by hand."""
    named = ", ".join(windows)
    if update_facts.loop_runs(root):
        return (
            f"the check-in loop brings along {named}, each once its turn is over (bosun update advance "
            "takes a turn by hand)"
        )
    return f"no check-in loop runs here: bosun update advance, run by hand, takes a turn at {named}"


def _waited(state_dir: Path, waiting: list[str], say: Callable[[str], None]) -> list[str]:
    """Turns every POLL_SECONDS until no window is waited for or Ctrl-C comes, saying which windows it
    waits for whenever that changes; the windows still waited for."""
    ctrl_c, said = _CtrlC(), list[str]()
    with ctrl_c.noted():
        while waiting and not ctrl_c.asked:
            waiting = turn(state_dir, say)
            if waiting and waiting != said:
                say(f"Waiting for {', '.join(waiting)} (Ctrl-C stops waiting)...")
                said = waiting
            if waiting:
                ctrl_c.wait(POLL_SECONDS)
    return waiting


def bring_along(state: UpdateState, plan: Plan, say: Callable[[str], None] = print) -> UpdateState:
    """After the release: `agents` recorded with each window the plan gives something to do pending
    (with the kind the plan lists for it; notes an earlier update left cleared first), then a turn
    every POLL_SECONDS until each answered (`done`). Ctrl-C stops the wait and leaves the windows still
    waited for, at `agents`, to the check-in loop or `bosun update advance` (who_finishes)."""
    state_dir = update_facts.folders().state_dir
    root = Path(state.to_root)
    windows = update_agents.to_bring_along(plan.windows, dict(plan.kinds))
    clear_notes(state_dir)
    state = update_state.advanced(
        state, "agents", pending=tuple(windows), windows=windows, reread=_rereads(plan, root)
    )
    update_state.write(state_dir, state)
    waiting = _waited(state_dir, list(state.pending), say)
    state = update_state.read(state_dir) or state
    if waiting:
        say(f"Stopped waiting: {who_finishes(root, waiting)}.")
        return state
    if state.step != "done":
        state = update_state.advanced(state, "done", pending=())
        update_state.write(state_dir, state)
    return state
