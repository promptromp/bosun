"""What repo windows are doing with their items: what I typed in a window (user-said), which item a
window is on (its active item), and whether it is working on it now. Design:
docs/design/events.md.

Pure functions over the ledger's items and its `said` list; lib/ledger.py stores and prints, and
passes in a window's live Claude status.

- A window holds the open items delegated to it (`delegation.window`).
- Its active item is the held item with the latest activity: a hand-over or update, a move to it,
  something I said there, a `ledger working` mark or a report. `ledger idle` takes an item out.
- An item is working while it is its window's active item and the window's Claude is mid-turn
  (busy, a background shell, or a prompt waiting for me), or while its latest activity is a
  `ledger working` mark. A turn that ended leaves the window free.
- What I type in a window is recorded on the item(s) it is about: the held items it names by id,
  else the active item (the other held items are listed as also held, so a reader of any of them
  still finds it).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from typing import Any

from similar import words


Item = dict[str, Any]
StatusOf = Callable[[str], "str | None"]

# The history events that make an item its window's active item, and the one that ends that.
ACTIVITY_EVENTS = ("delegated", "update", "moved", "user-said", "working", "report", "idle")
WORKING_MARK = "working"
IDLE_MARK = "idle"
# Claude statuses of a window that is mid-turn (Claude Code's session record).
MID_TURN = ("busy", "shell", "waiting")
# What I said, as kept: verbatim up to this many characters; history notes and listings show less.
MAX_SAID_LENGTH = 2000
EXCERPT_LENGTH = 200
# Prompts bosun itself types into a window (delegate, send, check-in, refresh), and messages
# Claude Code injects that are not mine (peer sessions, background tasks). Never "user-said".
BOSUN_PREFIXES = (
    "Ledger id",
    "Ledger item",
    "Update to ledger item",
    "Run this task in a subagent",
    "[bosun",
)
# Verified on Claude Code 2.1.303: peer messages and delivery notices also fire UserPromptSubmit.
NOT_TYPED_PREFIXES = (
    "<cross-session-message",
    "[Cross-session",
    "Another Claude session sent a message",
    "<task-notification",
    "<local-command",
    "<command-",
)
SLASH = "/"
PASTED = re.compile(r"</?pasted_content[^>]*>")


def excerpt(text: str, limit: int = EXCERPT_LENGTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def unwrap(text: str) -> str:
    """A prompt as typed: Claude Code wraps a long paste in <pasted_content> tags."""
    return PASTED.sub("", text).strip()


def prompt_hash(text: str) -> str:
    """How a prompt bosun pasted is recognised when the window's hook sees it."""
    return hashlib.sha256(" ".join(unwrap(text).split()).encode()).hexdigest()[:16]


def is_mine(text: str, pasted: Iterable[str] = (), drain_prompt: str = "") -> bool:
    """Whether a prompt is something I typed (or sent from my phone): not a prompt bosun
    pasted (by hash, or by its known openings), not a slash command, not injected by Claude Code."""
    stripped = unwrap(text)
    if not stripped or stripped.startswith((SLASH, *NOT_TYPED_PREFIXES, *BOSUN_PREFIXES)):
        return False
    if drain_prompt and " ".join(stripped.split()) == " ".join(drain_prompt.split()):
        return False
    return prompt_hash(stripped) not in set(pasted)


def held(items: Iterable[Item], window: str, is_open: Callable[[Item], bool]) -> list[Item]:
    """The open items delegated to <window>."""
    return [i for i in items if (i.get("delegation") or {}).get("window") == window and is_open(i)]


def last_activity(item: Item) -> dict[str, Any] | None:
    return next((h for h in reversed(item.get("history") or []) if h.get("event") in ACTIVITY_EVENTS), None)


def active_item(held_items: list[Item]) -> Item | None:
    """The held item with the latest activity, unless that was `ledger idle`."""
    latest: tuple[str, Item] | None = None
    for item in held_items:
        entry = last_activity(item)
        if entry and entry["event"] != IDLE_MARK and (latest is None or entry["at"] >= latest[0]):
            latest = (entry["at"], item)
    return latest[1] if latest else None


def attribute(text: str, held_items: list[Item]) -> tuple[list[str], list[str]]:
    """(items the prompt is about, other items the window holds): the held items it names by id,
    else the one whose title shares the most words with it (if one does more than the others),
    else the window's active item, else all of them."""
    ids = [i["id"] for i in held_items]
    named = [item_id for item_id in ids if re.search(rf"(?<![\w:/]){re.escape(item_id)}(?![\w/#])", text)]
    if not named:
        best = by_title(text, held_items) or active_item(held_items)
        named = [best["id"]] if best else ids
    return named, [item_id for item_id in ids if item_id not in named]


def by_title(text: str, held_items: list[Item]) -> Item | None:
    """The held item whose title shares the most content words with <text>, if exactly one does."""
    said = words(text)
    scored = sorted(((len(said & words(i.get("title") or "")), i) for i in held_items), key=lambda p: -p[0])
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][1]


def said_items(entry: dict[str, Any]) -> list[str]:
    """Every item a user-said entry concerns: the ones it is recorded on, then the also-held."""
    return [*entry.get("items", []), *entry.get("also_held", [])]


def is_working(item: Item, held_items: list[Item], status_of: StatusOf) -> bool:
    """Whether <item>'s window is working on it now (see the module docstring)."""
    window = (item.get("delegation") or {}).get("window")
    active = active_item(held_items)
    if not window or active is None or active["id"] != item["id"]:
        return False
    entry = last_activity(item)
    return bool(entry and entry["event"] == WORKING_MARK) or status_of(window) in MID_TURN
