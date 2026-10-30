#!/usr/bin/env python3
"""What sits in a Claude window's input box, read from a tmux pane capture (pure).

`bosun send` pastes a prompt and presses Enter. If you are typing in that window at the same
moment (a half-typed prompt, `/clear`), the paste joins your text and the Enter submits the mix:
once a hand-over pasted into a `/clear` became the unknown command `/clearLedger ...` and was lost.
So send reads the box first and pastes only into an empty one, and reads it again afterwards to
confirm the prompt went in.

The box is the line starting with the prompt mark (U+276F) right under a horizontal rule, plus any
continuation lines down to the next rule. Text Claude Code draws faint (a placeholder suggestion)
is not typed text, so the capture must keep its escape sequences (`capture-pane -e`): dim or grey
runs are dropped.

    pane_input.py typed       stdin: `tmux capture-pane -e -p`; prints the typed text.
                              Exit 0: the box is empty; 3: it holds text; 4: no input box found
    pane_input.py rejected    stdin: a plain capture; prints Claude's "Unknown command" lines
"""

from __future__ import annotations

import re
import sys


PROMPT_MARK = "\u276f"  # the input line's prompt mark
RULE_CHAR = "─"
MIN_RULE_LENGTH = 10
SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[A-Za-ln-z]|\x1b\][^\x07]*\x07")
NBSP = "\u00a0"
UNKNOWN_COMMAND = "Unknown command"
EXIT_EMPTY, EXIT_TYPED, EXIT_NO_BOX = 0, 3, 4

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


def box_lines(lines: list[str]) -> list[str] | None:
    """The input box's lines, from the prompt line down to the next rule; None if there is none."""
    for start in range(len(lines) - 1, 0, -1):
        if SGR.sub("", lines[start]).lstrip().startswith(PROMPT_MARK) and is_rule(lines[start - 1]):
            end = next((i for i in range(start + 1, len(lines)) if is_rule(lines[i])), len(lines))
            return lines[start:end]
    return None


def typed_text(capture: str) -> str | None:
    """The text typed in the input box of <capture> ("" when empty); None if no box is found."""
    box = box_lines(capture.splitlines())
    if box is None:
        return None
    first = visible(box[0]).replace(NBSP, " ").lstrip()
    first = first.removeprefix(PROMPT_MARK)
    rest = [visible(line).replace(NBSP, " ") for line in box[1:]]
    return "\n".join([first, *rest]).strip()


def rejected_lines(capture: str) -> list[str]:
    """Claude's replies that a pasted prompt ran as an unknown slash command."""
    return [line.strip() for line in capture.splitlines() if UNKNOWN_COMMAND in line]


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    capture = sys.stdin.read()
    if command == "typed":
        text = typed_text(capture)
        if text is None:
            return EXIT_NO_BOX
        print(text)
        return EXIT_TYPED if text else EXIT_EMPTY
    if command == "rejected":
        print("\n".join(rejected_lines(capture)))
        return 0
    print("usage: pane_input.py typed|rejected < capture", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
