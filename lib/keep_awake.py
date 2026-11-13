"""When the Mac's keep-awake ends: the record the morning writes next to its caffeinate's pid file
(`keep-awake-<session>.json`, `{"until": <ISO time with its zone>, "pid": <caffeinate's pid>}`, removed by
`bosun stop`), read by `bosun status --json` and `bosun quit`. Read-only; a record that is garbled, or whose
caffeinate is gone, is no keep-awake."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


PS_TIMEOUT_SECONDS = 5

Alive = Callable[[int], bool]


def is_caffeinate(pid: int) -> bool:
    """Whether <pid> is a running caffeinate: the morning's keep-awake (a pid can be reused later)."""
    try:
        result = subprocess.run(  # our own argv, no shell
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "caffeinate" in result.stdout


def keep_awake_until(state_dir: Path, session: str, alive: Alive = is_caffeinate) -> datetime | None:
    """When the Mac's keep-awake ends, as local naive time: the morning's keep-awake-<session>.json, while
    its caffeinate still runs. None when there is none, it is garbled, or the process is gone."""
    try:
        data = json.loads((state_dir / f"keep-awake-{session}.json").read_text())
        until, pid = datetime.fromisoformat(data["until"]), data["pid"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not alive(pid):
        return None
    return until.astimezone().replace(tzinfo=None)
