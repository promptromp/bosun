"""What reading an agent's screen takes, whichever agent (pure, a leaf module): shared by the readers in
lib/claude_state.py and lib/codex_state.py, which say where each agent's input box is.

A screen is a tmux pane capture. Text an agent draws faint (a placeholder suggestion) is not typed text,
so the capture must keep its escape sequences (`capture-pane -e`): dim or grey runs are dropped.
"""

from __future__ import annotations

import re


RULE_CHAR = "─"
MIN_RULE_LENGTH = 10
SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[A-Za-ln-z]|\x1b\][^\x07]*\x07")
NBSP = "\u00a0"

# SGR parameters (ECMA-48). A placeholder is drawn dim, or in a grey foreground: bright black, the
# 256-colour greys (and 8), or a true colour with red = green = blue.
RESET, DIM_ON, DIM_OFF, FG_DEFAULT, FG_BRIGHT_BLACK = 0, 2, 22, 39, 90
FG_EXTENDED, EXTENDED_256, EXTENDED_RGB = 38, 5, 2
GREY_256 = {8, *range(232, 256)}
FG_COLOURS = {*range(30, 38), *range(91, 98)}
SET_FAINT = {DIM_ON: True, FG_BRIGHT_BLACK: True, RESET: False, DIM_OFF: False, FG_DEFAULT: False}
RGB_PARTS = 3


def extended_faint(args: list[int]) -> tuple[bool | None, int]:
    """After 38: whether the colour is grey (None if unreadable), and how many parameters it used."""
    mode = args[0] if args else None
    if mode == EXTENDED_256:
        return (args[1] in GREY_256 if len(args) > 1 else None), 2
    if mode == EXTENDED_RGB:
        rgb = args[1 : 1 + RGB_PARTS]
        return (len(set(rgb)) == 1 if len(rgb) == RGB_PARTS else None), 1 + RGB_PARTS
    return None, 0


def faint_after(params: list[int], faint: bool) -> bool:
    """Whether text is faint after an SGR sequence with <params>."""
    i = 0
    while i < len(params):
        code = params[i]
        if code == FG_EXTENDED:
            grey, used = extended_faint(params[i + 1 :])
            faint = faint if grey is None else grey
            i += used
        elif code in SET_FAINT:
            faint = SET_FAINT[code]
        elif code in FG_COLOURS:
            faint = False
        i += 1
    return faint


def visible(line: str) -> str:
    """<line> without escape sequences and without the runs drawn faint."""
    line = OTHER_ESCAPES.sub("", line)
    kept, faint, pos = [], False, 0
    for match in SGR.finditer(line):
        if not faint:
            kept.append(line[pos : match.start()])
        params = [int(p) for p in match.group(1).split(";") if p] or [RESET]
        faint = faint_after(params, faint)
        pos = match.end()
    if not faint:
        kept.append(line[pos:])
    return "".join(kept)


def is_rule(line: str) -> bool:
    text = SGR.sub("", line).strip()
    return text.startswith(RULE_CHAR * MIN_RULE_LENGTH)


def box_lines(lines: list[str], mark: str) -> list[str] | None:
    """A box drawn under a horizontal rule: its lines, from the one starting with <mark> down to the next
    rule; None if there is none."""
    for start in range(len(lines) - 1, 0, -1):
        if SGR.sub("", lines[start]).lstrip().startswith(mark) and is_rule(lines[start - 1]):
            end = next((i for i in range(start + 1, len(lines)) if is_rule(lines[i])), len(lines))
            return lines[start:end]
    return None
