#!/usr/bin/env python3
"""What sits in an agent window's input box, read from a tmux pane capture (pure).

`bosun send` pastes a prompt and presses Enter. If you are typing in that window at the same
moment (a half-typed prompt, `/clear`), the paste joins your text and the Enter submits the mix:
once a hand-over pasted into a `/clear` became the unknown command `/clearLedger ...` and was lost.
So send reads the box first and pastes only into an empty one, and reads it again afterwards to
confirm the prompt went in.

Where each agent draws its box, and what it shows besides, is that agent's own reader (`Profile.screen`,
in lib/claude_state.py and lib/codex_state.py). This is the command that asks them: with `--harness` the
window's agent, without it every agent's reader. The capture of `typed` keeps its escape sequences
(`capture-pane -e`), since faint text is not typed text.

    pane_input.py typed [--harness <h>]      stdin: `tmux capture-pane -e -p`; prints the typed text.
                                             Exit 0: the box is empty; 3: it holds text; 4: no input box
                                             found (without --harness: the first agent's box found)
    pane_input.py rejected [--harness <h>]   stdin: a plain capture; prints the lines saying a pasted
                                             prompt ran as an unknown command
    pane_input.py question [--harness <h>]   stdin: a plain capture; exit 0 if the agent is asking
                                             something, 3 if not

Exit 2: a usage error, or an agent bosun does not know.
"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_files import Screen
from agent_state import AGENTS


EXIT_EMPTY, EXIT_TYPED, EXIT_NO_BOX, EXIT_USAGE = 0, 3, 4, 2
COMMANDS = ("typed", "rejected", "question")
USAGE = "usage: pane_input.py typed|rejected|question [--harness <agent>] < capture"


def screens(harness: str | None) -> list[Screen]:
    """The readers to ask: that agent's, or every agent's in AGENTS order. KeyError: no such agent."""
    if harness is None:
        return [agent.PROFILE.screen for agent in AGENTS.values()]
    return [AGENTS[harness].PROFILE.screen]


def typed_text(capture: str, harness: str | None = None) -> str | None:
    """The text typed in the input box of <capture> ("" when empty); None if no box is found."""
    lines = capture.splitlines()
    return next((text for screen in screens(harness) if (text := screen.typed(lines)) is not None), None)


def rejected_lines(capture: str, harness: str | None = None) -> list[str]:
    """The agents' replies that a pasted prompt ran as an unknown command."""
    return [line for screen in screens(harness) for line in screen.rejected(capture)]


def asking(capture: str, harness: str | None = None) -> bool:
    """Whether the agent is asking something on <capture> that Enter would answer."""
    return any(screen.question(capture) for screen in screens(harness))


def parse(argv: list[str]) -> tuple[str, str | None] | None:
    """The command and the agent asked about (None: all), or None if the arguments are not usage."""
    command, *rest = argv[1:] or [""]
    if command not in COMMANDS:
        return None
    if not rest:
        return command, None
    match rest:
        case ["--harness", harness]:
            return command, harness
    return None


def run(command: str, capture: str, harness: str | None) -> int:
    """Carry out <command> (one of COMMANDS) and give its exit status."""
    if command == "typed":
        text = typed_text(capture, harness)
        if text is None:
            return EXIT_NO_BOX
        print(text)
        return EXIT_TYPED if text else EXIT_EMPTY
    if command == "rejected":
        print("\n".join(rejected_lines(capture, harness)))
        return 0
    return 0 if asking(capture, harness) else EXIT_TYPED


def main(argv: list[str]) -> int:
    parsed = parse(argv)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE
    command, harness = parsed
    if harness is not None and harness not in AGENTS:
        print(f"no agent '{harness}': bosun knows {', '.join(AGENTS)}", file=sys.stderr)
        return EXIT_USAGE
    return run(command, sys.stdin.read(), harness)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
