"""When pending events are due for delivery: lanes and their timing (design: docs/design/events.md).

Pure. Two lanes: the fast one (BOSUN_EVENTS_FAST_KINDS: by default Slack activity for me and
windows waiting at a prompt) and the routine one (reports, what I said, outreach drafts, tasks,
reviews). Each has a debounce (its newest event this old: a burst becomes one delivery) and a
longest wait (its oldest event this old: a steady trickle still goes). While I am away (nobody
typed in tmux for BOSUN_EVENTS_AWAY_MINUTES, or no client is attached), the routine lane uses
the longer away timing. A delivery takes every pending event, whichever lane made it due.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class Timed(Protocol):
    @property
    def at(self) -> str: ...  # ISO time

    @property
    def type(self) -> str: ...


@dataclass(frozen=True)
class Timing:
    debounce: float  # seconds the newest event must be old
    max_wait: float  # seconds the oldest event may wait at most


@dataclass(frozen=True)
class Lanes:
    routine: Timing
    fast: Timing
    away: Timing  # the routine lane's timing while I am away
    fast_kinds: frozenset[str]
    away_minutes: float  # 0: never away

    def is_away(self, client_idle_minutes: float | None) -> bool:
        """No keystroke in an attached tmux client for away_minutes, or no client attached."""
        if self.away_minutes <= 0:
            return False
        return client_idle_minutes is None or client_idle_minutes >= self.away_minutes

    def split(self, events: Sequence[Timed], away: bool) -> list[tuple[str, Timing, list[Timed]]]:
        """The lanes with events: name, timing, its events (oldest first)."""
        fast = [e for e in events if e.type in self.fast_kinds]
        routine = [e for e in events if e.type not in self.fast_kinds]
        lanes = [
            ("fast", self.fast, fast),
            ("away" if away else "routine", self.away if away else self.routine, routine),
        ]
        return [lane for lane in lanes if lane[2]]


def age(at: str, now: datetime) -> float:
    return now.timestamp() - datetime.fromisoformat(at).timestamp()


def lane_wait(name: str, timing: Timing, events: Sequence[Timed], now: datetime) -> str | None:
    """Why the lane's events are not due yet, or None if they are."""
    newest, oldest = age(events[-1].at, now), age(events[0].at, now)
    if newest >= timing.debounce or oldest >= timing.max_wait:
        return None
    return (
        f"{name} lane debouncing: the newest event is {newest:.0f}s old (due at {timing.debounce:.0f}s,"
        f" or once the oldest is {timing.max_wait:.0f}s old)"
    )


def due(
    lanes: Lanes, events: Sequence[Timed], client_idle_minutes: float | None, now: datetime
) -> str | None:
    """Why no lane is due yet, or None when one is (then every pending event goes)."""
    waits = []
    for name, timing, group in lanes.split(events, lanes.is_away(client_idle_minutes)):
        reason = lane_wait(name, timing, group, now)
        if reason is None:
            return None
        waits.append(reason)
    return "; ".join(waits)
