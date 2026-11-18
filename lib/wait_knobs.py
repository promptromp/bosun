"""bosun's own waits that the tests shorten, each through a variable of its own that is for tests (as
BOSUN_RELAUNCH_SETTLE_SECONDS and BOSUN_AGENT_SETTLE_SECONDS are in the shell): BOSUN_UPDATE_POLL_SECONDS
for the update's wait between turns (lib/update_windows.py) and BOSUN_QUIT_POLL_SECONDS for quit
--let-finish's poll (lib/quit_live.py, which hands it to the pure run). Read where the live side starts,
never in a pure module.

A value that is not a finite number above 0 (unset, empty, a word, 0, negative, nan, inf) is the wait's
default: these are test knobs, so a bad one waits as bosun always did rather than fail a quit or an update
half way.
"""

from __future__ import annotations

import math
import os


def seconds(name: str, default: float) -> float:
    """The environment's <name> as seconds, or <default> when it is not a finite number above 0."""
    try:
        value = float(os.environ.get(name) or default)
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default
