"""Everything bosun reads or writes in Google's Antigravity CLI (`agy`)'s own files and on its screen, as
lib/claude_state.py and lib/codex_state.py do for the other agents. Design: docs/design/harnesses.md.

What it goes by, seen in agy 1.5.5 (the Mac VM run, `tests/fixtures/agy/screens/`) and not a public API.
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

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_files import Screen, never_rejects
from screen_text import NBSP, SGR, is_rule, visible


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
