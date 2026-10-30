"""The console's Library view (/api/library, /api/library/<key>): the editorial content that steers
bosun, read-only. Design: docs/design/console.md (section 5).

Pure: which files the Library lists comes from lib/console_files.py (the only I/O); this module
decides what each one is (kind, group, badge), and how its text is shown:
- Markdown: its YAML front matter (`key: value` lines between `---` markers) as a table, the body
  rendered by the page's safe Markdown renderer.
- Config (bosun.env, sessions.yml, the orchestrator settings): masked, line by line, where a
  key's name says it holds a secret (TOKEN, SECRET, KEY, PASSWORD, ...) and wherever a webhook
  URL appears, then lib/redact.py over the whole text.
- Anything from outside the repo (site files, memory notes) goes through lib/redact.py too.
- Task types carry an "effective" badge: lib/delegate.py's own resolution (`task_files`: a site file
  named like a repo type replaces it; `_common.md` files are added, never replaced) decides which
  file `bosun delegate` uses.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import delegate
import redact


REPO = "repo"
SITE = "site"
MEMORY = "memory"
MARKDOWN = "markdown"
CONFIG_KINDS = ("env", "yaml", "json")
KINDS_BY_SUFFIX = {".md": MARKDOWN, ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".env": "env"}
KEY_LENGTH = 16  # hex digits of a file's opaque key
KEY_FORMAT = re.compile(rf"^[0-9a-f]{{{KEY_LENGTH}}}$")
MASK = "<masked>"
WEBHOOK_MASK = "<webhook URL>"
SECRET_NAME = re.compile(r"TOKEN|SECRET|KEY|PASSWORD|PASSWD|CREDENTIAL|COOKIE|PRIVATE", re.IGNORECASE)
# NAME=value, export NAME=value, NAME: value, "NAME": "value" (with the value's own quoting kept out)
ASSIGNMENT = re.compile(
    r"""^(?P<head>\s*(?:-\s+)?(?:export\s+)?["']?(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)["']?\s*[=:]\s*)(?P<value>.*?)(?P<tail>,?\s*)$"""
)
WEBHOOK = re.compile(r"https?://[^\s\"'<>]*(?:hooks?\.|webhook)[^\s\"'<>]*", re.IGNORECASE)
EMPTY_VALUES = ("", '""', "''", "{", "[", "|", ">")
FRONT_MATTER_FENCE = "---"
COMMON_RULES = delegate.COMMON_NAME


def file_key(root: str, path: Path) -> str:
    """A file's opaque key: a hash of where it is, never the path itself."""
    return hashlib.sha256(f"{root}\0{path}".encode()).hexdigest()[:KEY_LENGTH]


def valid_key(key: str) -> bool:
    return bool(KEY_FORMAT.match(key))


def kind_of(name: str) -> str:
    if name == "bosun.env":
        return "env"
    return KINDS_BY_SUFFIX.get(Path(name).suffix.lower(), "text")


# ---- masking -----------------------------------------------------------------------------------


def mask_line(line: str) -> str:
    """The line with a secret-named key's value masked, and any webhook URL in it."""
    match = ASSIGNMENT.match(line)
    value = match["value"].strip() if match else ""
    if match and SECRET_NAME.search(match["name"]) and value not in EMPTY_VALUES:
        quote = value[0] if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'" else ""
        line = f"{match['head']}{quote}{MASK}{quote}{match['tail']}"
    return WEBHOOK.sub(WEBHOOK_MASK, line)


def mask_config(text: str) -> str:
    """A config file as shown: lib/redact.py, then secret-named keys and webhook URLs masked."""
    return "\n".join(mask_line(line) for line in redact.redact(text).split("\n"))


def shown_text(text: str, kind: str, root: str) -> tuple[str, bool]:
    """The text the page gets, and whether anything was masked or redacted."""
    if kind in CONFIG_KINDS and root != REPO:
        shown = mask_config(text)
    elif root != REPO:
        shown = redact.redact(text)
    else:
        shown = text
    return shown, shown != text


# ---- front matter ------------------------------------------------------------------------------


def front_matter(text: str) -> tuple[list[list[str]], str]:
    """The `key: value` lines of a leading `---` block (as lib/delegate.py reads task files), and the
    body after it; no block: nothing, and the whole text."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != FRONT_MATTER_FENCE:
        return [], text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == FRONT_MATTER_FENCE)
    except StopIteration:
        return [], text
    fields = []
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            fields.append([key.strip(), value.strip()])
    return fields, "\n".join(lines[end + 1 :]).lstrip("\n")


# ---- the catalog -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One file the Library may show. <path> stays on the server: the page only gets the key."""

    key: str
    root: str  # repo, site, or memory
    group: str
    name: str  # as shown: relative to its root
    path: Path
    badge: str = ""

    @property
    def kind(self) -> str:
        return kind_of(self.path.name)


def task_badges(repo_dir: Path, site_dir: Path | None, files: Iterable[Path]) -> dict[Path, str]:
    """For each task file: "effective" if `bosun delegate` uses it, "replaced by site" if a site
    file of the same name wins, "added to every task" for the shared rules (_common.md)."""
    winners = set(delegate.task_files((repo_dir, site_dir)).values())
    badges = {}
    for path in files:
        if path.name == COMMON_RULES:
            badges[path] = "added to every task"
        elif path in winners:
            badges[path] = "effective"
        else:
            badges[path] = "replaced by site" if path.parent == repo_dir else "not used"
    return badges


def entry_summary(entry: Entry, text: str | None) -> dict[str, Any]:
    """An entry in /api/library: what it is, and its front-matter title, if any."""
    fields = dict(front_matter(text)[0]) if text is not None and entry.kind == MARKDOWN else {}
    return {
        "key": entry.key,
        "root": entry.root,
        "name": entry.name,
        "kind": entry.kind,
        "badge": entry.badge,
        "title": fields.get("title") or fields.get("name") or "",
        "summary": fields.get("description") or fields.get("may") or "",
    }


def library_model(entries: list[Entry], texts: Mapping[str, str | None], memory_on: bool) -> dict[str, Any]:
    """/api/library: the entries by group, in the order listed."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        groups.setdefault(entry.group, []).append(entry_summary(entry, texts.get(entry.key)))
    return {
        "groups": [{"name": name, "entries": found} for name, found in groups.items()],
        "memory": memory_on,
    }


def file_model(entry: Entry, text: str | None, error: str = "") -> dict[str, Any]:
    """/api/library/<key>: one file, masked where it must be; markdown split from its front matter."""
    base = {
        "key": entry.key,
        "root": entry.root,
        "group": entry.group,
        "name": entry.name,
        "kind": entry.kind,
    }
    if text is None:
        return {
            **base,
            "badge": entry.badge,
            "front_matter": [],
            "body": "",
            "redacted": False,
            "error": error,
        }
    shown, redacted = shown_text(text, entry.kind, entry.root)
    fields, body = front_matter(shown) if entry.kind == MARKDOWN else ([], shown)
    return {
        **base,
        "badge": entry.badge,
        "front_matter": fields,
        "body": body,
        "redacted": redacted,
        "error": None,
    }
