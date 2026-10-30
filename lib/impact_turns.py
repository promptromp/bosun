"""A transcript's turns, as the Impact view counts them (design: docs/design/impact.md).

Pure over lib/claude_state.py's `Activity` entries. A turn is what one prompt or injected message
set off: every assistant call up to the next one. Claude Code writes an assistant message once per
content block, so each call is counted once, by its message id. Shared by the Overhead panel (what
started each turn, `lib/impact_collect.py`'s `summarize`) and cost per task (each turn's tokens and
the ledger ids a hand-over names, `lib/impact_attribution.py`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import claude_state
import impact


TURN_STARTS = (claude_state.ACTIVITY_PROMPT, claude_state.ACTIVITY_INJECTED)
UNKNOWN_MODEL = "unknown"
Classify = Callable[[str, bool], str]


@dataclass
class Turn:
    """The turn under way: the class of what started it, when and with what text, and whether its
    first call is still to come."""

    cls: str
    at: datetime | None = None
    text: str = ""
    first: bool = True


def new_call(entry: claude_state.Activity, seen: set[str]) -> bool:
    """An assistant message with usage not counted yet; remembers it."""
    if entry.kind != claude_state.ACTIVITY_ASSISTANT or not entry.usage or entry.message_id in seen:
        return False
    if entry.message_id:
        seen.add(entry.message_id)
    return True


def uses_tokens(entry: claude_state.Activity) -> bool:
    """Claude Code's <synthetic> notices carry usage with every count zero."""
    return any(count for _, count in entry.usage)


def turn_calls(
    entries: Iterable[claude_state.Activity], classify: Classify, first_class: str
) -> Iterator[tuple[Turn, claude_state.Activity]]:
    """Each new call with the turn it belongs to; calls before any prompt (a subagent's, whose
    prompt lives in its parent) belong to a turn of <first_class>."""
    seen: set[str] = set()
    turn = Turn(first_class)
    for entry in entries:
        if entry.kind in TURN_STARTS:
            turn = Turn(classify(entry.text, entry.scheduled), entry.at, entry.text)
        elif new_call(entry, seen):
            yield turn, entry


def call_usage(entry: claude_state.Activity) -> dict[str, dict[str, int]]:
    """One call's tokens, by model."""
    return {entry.model or UNKNOWN_MODEL: dict(entry.usage)}


def turn_records(
    entries: Iterable[claude_state.Activity],
    classify: Classify,
    first_class: str,
    handover_ids: Callable[[str], list[str]],
) -> list[dict[str, Any]]:
    """Per turn with tokens: its start (the first call's time for a turn without a prompt), the
    ledger ids its prompt hands over, and its tokens by model. What the collector caches per
    transcript for cost per task."""
    records: list[dict[str, Any]] = []
    current: Turn | None = None
    for turn, entry in turn_calls(entries, classify, first_class):
        if not uses_tokens(entry):
            continue
        if turn is not current:
            current = turn
            start = turn.at or entry.at
            records.append({"at": start.isoformat(), "ids": handover_ids(turn.text), "usage": {}})
        impact.merge_usage(records[-1]["usage"], call_usage(entry))
    return records
