"""The context a session gets back after Claude Code compacts its conversation (docs/setup.md: compaction).

A compaction keeps a summary of the conversation, which can drop which ledger items the window
holds. `bosun hook compact-context` (a SessionStart hook, matcher `compact`) adds a short block
back: for a repo window, the items it holds and how to report; for the orchestrator, what waits for
my approval and where the rest is. This module only shapes that block and keeps it within a size
bound; `bosun ledger compact-context` picks the rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


DEFAULT_MAX_CHARS = 1500
MORE_LINE = "- and {count} more: {command}"
REPORT_HINT = (
    "Report each finished item with `bosun ledger report <id> ...` (the task's rules say how); "
    "`bosun ledger show <id>` has an item's ask and history."
)
ORCHESTRATOR_HINT = (
    "Your policy is in the system prompt; `bosun ledger list --open` and "
    "`bosun ledger reports --since <time>` have the rest. My approvals from before the "
    "compaction still count: the session's transcript keeps them."
)


@dataclass(frozen=True)
class Block:
    """A heading, one line per row, and closing lines that are always kept."""

    heading: str
    rows: Sequence[str]
    footer: Sequence[str]
    more_command: str

    def render(self, max_chars: int = DEFAULT_MAX_CHARS) -> str:
        """The block, with as many rows as fit in <max_chars> and a line naming the rest."""
        fixed = [self.heading, *self.footer]
        budget = max_chars - sum(len(line) + 1 for line in fixed)
        kept = fit_rows(self.rows, budget, self.more_command)
        return "\n".join([self.heading, *kept, *self.footer])


def fit_rows(rows: Sequence[str], budget: int, more_command: str) -> list[str]:
    """The leading rows that fit in <budget> characters, plus a line counting the rest."""
    for count in range(len(rows), -1, -1):
        kept = list(rows[:count])
        if count < len(rows):
            kept.append(MORE_LINE.format(count=len(rows) - count, command=more_command))
        if sum(len(line) + 1 for line in kept) <= budget or count == 0:
            return kept
    return []


def window_block(window: str, rows: Sequence[str]) -> Block:
    """What a repo window holds, and how to report on it."""
    if rows:
        heading = f"Bosun: this conversation was compacted. Ledger items handed to {window}:"
    else:
        heading = f"Bosun: this conversation was compacted. No open ledger items are handed to {window}."
    footer = [REPORT_HINT]
    return Block(heading, rows, footer, f"bosun ledger list --open | grep -E '@{window}( |$)'")


def orchestrator_block(rows: Sequence[str], held_counts: Sequence[str]) -> Block:
    """What waits for my approval, and where the rest of the state lives."""
    heading = (
        f"Bosun: this conversation was compacted. {len(rows)} proposal(s) wait for my approval:"
        if rows
        else "Bosun: this conversation was compacted. No proposal waits for my approval."
    )
    footer = [
        "Open items per window: " + (", ".join(held_counts) if held_counts else "none") + ".",
        ORCHESTRATOR_HINT,
    ]
    return Block(heading, rows, footer, "bosun ledger list --open --proposals")
