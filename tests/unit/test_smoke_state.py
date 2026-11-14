"""The smoke suite keeps all its state in its own temp folder, so that suites of several checkouts (git
worktrees: parallel agents) run side by side on one Mac. A fixed path outside it (a /tmp/<name>) would be
shared by every run.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUITE_FILES = ("tests/smoke.sh", "tests/smoke_cache.sh")

# The /tmp literals that name no shared file: the fallbacks of $TMPDIR and $TMUX_TMPDIR (the run's mktemp and
# its own tmux socket live under them), the TMUX variable a check sets to look like a session inside tmux, and
# a string a check feeds the guard as a command.
FIXED = ("/tmp", "/var/tmp")  # noqa: S108 (what the test looks for in the suite, never a path it uses)
ALLOWED = (
    re.compile(r"\$\{TMPDIR:-/tmp\}"),
    re.compile(r"\$\{TMUX_TMPDIR:-/tmp\}"),
    re.compile(r'TMUX="/tmp/tmux-\d+/'),
    re.compile(r"send --new w /tmp task"),
)


def shared_paths(text: str) -> list[str]:
    """The lines holding a /tmp literal that none of ALLOWED explains."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        rest = line
        for allowed in ALLOWED:
            rest = allowed.sub("", rest)
        if any(path in rest for path in FIXED):
            found.append(f"{number}: {line.strip()}")
    return found


class SuiteState(unittest.TestCase):
    def test_no_file_outside_the_runs_own_temp_folder(self) -> None:
        for name in SUITE_FILES:
            with self.subTest(name):
                found = shared_paths((ROOT / name).read_text())
                self.assertEqual(found, [], f"{name} names a path every run would share: use $WORK or mktemp")

    def test_it_finds_a_fixed_path(self) -> None:
        self.assertEqual(shared_paths('lock="/tmp/bosun-smoke.lock"'), ['1: lock="/tmp/bosun-smoke.lock"'])
        self.assertEqual(shared_paths('d="${TMPDIR:-/tmp}/x.XXXXXX"'), [])
        self.assertEqual(shared_paths('env TMUX="/tmp/tmux-0/$SOCKET,1,0" bosun start'), [])


if __name__ == "__main__":
    unittest.main()
