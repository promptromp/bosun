"""The smoke suite writes to no fixed path outside its own temp folder, so that suites of several checkouts
(git worktrees: parallel agents) can run side by side on one Mac. What this checks is narrow: no script the
suite runs names a `/tmp/<name>` (or `/var/tmp`, `/private/tmp`) path of its own. A fixed path of any other
kind (a port, a socket or launchd label, a file under the real home) is not seen here; the suite's HOME,
state, tmux sockets and stand-ins are its own by construction (tests/smoke.sh).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
# The scripts the suite runs or sources (the update section's among them).
SUITE_FILES = (
    "tests/smoke.sh",
    "tests/smoke_cache.sh",
    "tests/js_check.sh",
    "tests/sandbox/update_patch.sh",
    "tests/sandbox/acme_year.py",
    "tests/sandbox/agy-stand-in",
    "tests/sandbox/codex-stand-in",
)

# /tmp (or /var/tmp, /private/tmp) as the start of a path: not after a word character, `$`, `}` or `/`, so
# `$WORK/tmp/x` and `${TMPDIR:-/tmp}` are not it, and not before one, so `/tmpfoo` is not either.
FIXED = re.compile(r"(?<![A-Za-z0-9_$}/.~-])/(?:private/)?(?:var/)?tmp(?![A-Za-z0-9_-])")
# What names no shared file: the TMUX variable a check sets to look like a session inside tmux, and a string a
# check feeds the guard as a command.
ALLOWED = (
    re.compile(r'TMUX="/tmp/tmux-\d+/'),
    re.compile(r"send --new w /tmp task"),
)


def fixed_paths(text: str) -> list[str]:
    """The lines holding a /tmp path that none of ALLOWED explains."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        rest = line
        for allowed in ALLOWED:
            rest = allowed.sub("", rest)
        if FIXED.search(rest):
            found.append(f"{number}: {line.strip()}")
    return found


class SuiteState(unittest.TestCase):
    def test_no_script_names_a_tmp_path_of_its_own(self) -> None:
        for name in SUITE_FILES:
            with self.subTest(name):
                found = fixed_paths((ROOT / name).read_text())
                self.assertEqual(found, [], f"{name} names a path every run would share: use $WORK or mktemp")

    def test_it_finds_a_fixed_path_and_only_that(self) -> None:
        for line in ('lock="/tmp/bosun-smoke.lock"', "cp a /tmp/x", "d=/private/tmp/x", "touch /var/tmp/y"):
            self.assertEqual(fixed_paths(line), [f"1: {line}"], line)
        for line in (
            'd="${TMPDIR:-/tmp}/x.XXXXXX"',
            'f="$WORK/tmp/x"',
            "cd $HOME/tmp/x",
            "ls /tmpfoo",
            'env TMUX="/tmp/tmux-0/$SOCKET,1,0" bosun start',
        ):
            self.assertEqual(fixed_paths(line), [], line)


if __name__ == "__main__":
    unittest.main()
