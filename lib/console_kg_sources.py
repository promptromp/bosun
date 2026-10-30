"""Readable fact sources for the Knowledge view: a memory note's title and a link to it in the
Library, and a session's name for a transcript reference. The only I/O behind sources
(lib/console_knowledge.py builds the plain labels). Design: docs/design/console.md, section 6.

- A memory note is read only through the Library's catalog (lib/console_files.py), so only when
  BOSUN_CONSOLE_MEMORY_DIRS lists its folder; otherwise its label stays its file name.
- A session's name comes from its transcript's title (lib/claude_state.py), found by the short
  session id the reference quotes; without one the label keeps the id.
- Both are cached by file and modification time: a transcript is read once until it changes.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import console_files
import console_library
from console_library import MEMORY, Entry


MEMORY_KIND = "memory"
TRANSCRIPT_KIND = "transcript"
Cache = dict[tuple[str, float], str]
TITLES: Cache = {}
NOTES: Cache = {}


def cached(cache: Cache, path: Path, read: Callable[[Path], str]) -> str:
    """<read>(path), kept until the file changes."""
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return ""
    if key not in cache:
        cache[key] = read(path)
    return cache[key]


def title_of(transcript: Path) -> str:
    try:
        return claude_state.latest_title(transcript) or ""
    except OSError:
        return ""


def summary_of(note: Path) -> str:
    """A memory note's front-matter description (its one-line summary), or ''."""
    text, _ = console_files.read(note)
    fields = dict(console_library.front_matter(text)[0]) if text else {}
    return str(fields.get("description") or "").strip().strip('"')


def session_name(session: str) -> str:
    """The name of the session a reference quotes by its short id, or ''."""
    for transcript in claude_state.transcripts_by_prefix(session)[:1]:
        return cached(TITLES, transcript, title_of)
    return ""


def memory_notes(catalog: list[Entry]) -> dict[str, Entry]:
    """The Library's memory notes by file name (a source names a note by its file name)."""
    return {entry.path.name: entry for entry in catalog if entry.root == MEMORY}


def label_memory(source: dict[str, Any], notes: dict[str, Entry]) -> None:
    entry = notes.get(str(source.get("ref", "")).rsplit("/", 1)[-1])
    if entry is None:
        return
    source["library"] = entry.key
    source["detail"] = cached(NOTES, entry.path, summary_of)


def label_transcript(source: dict[str, Any]) -> None:
    session, _, rest = str(source.get("ref", "")).partition(" ")
    name = session_name(session)
    if name:
        tail = str(source.get("label", "")).partition(",")[2]
        source["label"] = f"{name} session" + (f",{tail}" if rest and tail else "")


def sources_in(model: Any) -> Iterator[dict[str, Any]]:
    """Every source in a Knowledge model: the dicts in any "sources" list, however deep."""
    if isinstance(model, dict):
        for key, value in model.items():
            if key == "sources" and isinstance(value, list):
                yield from (s for s in value if isinstance(s, dict))
            else:
                yield from sources_in(value)
    elif isinstance(model, list):
        for value in model:
            yield from sources_in(value)


def label_sources(model: Any, catalog: list[Entry]) -> Any:
    """<model> with its memory and transcript sources named where they can be (in place)."""
    notes = memory_notes(catalog)
    for source in sources_in(model):
        if source.get("kind") == MEMORY_KIND:
            label_memory(source, notes)
        elif source.get("kind") == TRANSCRIPT_KIND:
            label_transcript(source)
    return model
