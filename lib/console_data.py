"""The console's data: snapshots of the ledger, /api/version, and what every view shares (the
query error the API answers 400 for, which values become links). Design: docs/design/console.md.

The ledger is read as a snapshot: lib/ledger.py replaces ledger.json atomically (a rename), so a
plain read always sees one whole version, and the console never takes the ledger's lock.
SnapshotCache re-reads the file only when it was replaced or changed. The views live next to it:
lib/console_ledger.py (the Ledger list, item page and "needs you"), lib/console_sessions.py (the
Sessions view and health, pure) over the facts lib/console_live.py collects.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


LEDGER_FILE = "ledger.json"  # lib/ledger.py's Store.ledger_file, under BOSUN_STATE_DIR
MISSING_STAMP = "none"


@dataclass(frozen=True)
class Snapshot:
    """One version of ledger.json. <stamp> changes whenever the file is replaced or rewritten (inode,
    mtime and size), so the page can poll it cheaply; <error> is set when the file is unreadable."""

    stamp: str = MISSING_STAMP
    mtime: float | None = None
    ledger: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def exists(self) -> bool:
        return self.mtime is not None


def file_stamp(stat: os.stat_result) -> str:
    return f"{stat.st_ino}-{stat.st_mtime_ns}-{stat.st_size}"


def parse_ledger(text: str) -> dict[str, Any]:
    """The parsed ledger; ValueError if it is not JSON or not shaped like a ledger."""
    data = json.loads(text)
    if not isinstance(data, dict) or not all(
        isinstance(data.get(key, {}), dict) for key in ("items", "sources", "runs")
    ):
        raise ValueError("not a ledger (unexpected structure)")
    return data


def read_snapshot(path: Path) -> Snapshot:
    """Read <path> once. The stamp comes from the open file, so it always matches the text read even
    if the ledger is replaced meanwhile. A missing file is an empty ledger, not an error."""
    try:
        with path.open() as handle:
            stat = os.fstat(handle.fileno())
            text = handle.read()
    except FileNotFoundError:
        return Snapshot()
    except OSError as exc:
        return Snapshot(error=f"cannot read {path.name}: {exc.strerror}")
    stamp = file_stamp(stat)
    try:
        return Snapshot(stamp, stat.st_mtime, parse_ledger(text))
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        return Snapshot(stamp, stat.st_mtime, error=f"{path.name} is not a valid ledger: {exc}")


class SnapshotCache:
    """The latest snapshot of one ledger file, re-read only when its stamp changes. Shared by the
    server's request threads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None

    def current_stamp(self) -> str:
        try:
            return file_stamp(self.path.stat())
        except FileNotFoundError:
            return MISSING_STAMP

    def get(self) -> Snapshot:
        with self._lock:
            if self._snapshot is None or self._snapshot.stamp != self.current_stamp():
                self._snapshot = read_snapshot(self.path)
            return self._snapshot


def iso_time(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def version_model(snapshot: Snapshot) -> dict[str, Any]:
    """/api/version: what the page polls to notice a change, and the ledger's size."""
    items = snapshot.ledger.get("items", {})
    return {
        "stamp": snapshot.stamp,
        "ledger": {
            "exists": snapshot.exists,
            "mtime": iso_time(snapshot.mtime),
            "version": snapshot.ledger.get("version"),
            "items": len(items),
            "error": snapshot.error or None,
        },
    }


# ---- shared by the views ------------------------------------------------------------------------

Item = dict[str, Any]
Params = Mapping[str, list[str]]
WEB_SCHEMES = ("http", "https")
CONTROL = re.compile(r"[\x00-\x20\x7f]")


class QueryError(ValueError):
    """A query parameter the API cannot use (the server answers 400 with the message)."""


def as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def web_url(value: Any) -> str | None:
    """<value> if it is an http(s) URL with a host and no spaces or control characters, else None:
    only these become links (a javascript: or data: URL in the ledger stays text)."""
    if not isinstance(value, str) or CONTROL.search(value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return None
    return value if parts.scheme.lower() in WEB_SCHEMES and host else None
