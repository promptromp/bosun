"""What `bosun stop` leaves behind: the stop marker, `stopped-<session>` in the state folder.

`bosun stop` writes it (`stop_marker` in lib/common.sh: the time, as `date '+%F %T'` writes it) and `bosun
start` removes it. Unlike `bosun quit`'s record (lib/quit_state.py), which holds until bosun is started again,
the marker only counts on the day it was written: the watchdog does not expect the session for the rest of
that day, and a login that day does not start bosun (lib/login_start.py). Both read it here.

The shell writes other one-line markers in the same format (login_start.py's reload marker); `read_stamp`
reads any of them.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


STAMP = "%Y-%m-%d %H:%M:%S"  # the time in the marker, as `date '+%F %T'` writes it


def path(state_dir: Path, session: str) -> Path:
    return state_dir / f"stopped-{session}"


def read_stamp(marker: Path) -> datetime | None:
    """The local time in <marker> (one line, STAMP), or None when it is not there or does not hold one."""
    try:
        return datetime.strptime(marker.read_text().strip(), STAMP)
    except (OSError, ValueError):  # a file that is not text is a ValueError too (UnicodeDecodeError)
        return None


def read(state_dir: Path, session: str) -> datetime | None:
    """When <session> was last stopped with `bosun stop` (local time), or None when it was not, or the marker
    does not say when."""
    return read_stamp(path(state_dir, session))
