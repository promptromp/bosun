"""libexec/bosun-watchdog's `# Usage:` header (its --help, which bin/bosun prints without running it)
describes every check of lib/watchdog.py's CHECKS (what --list prints), as CLAUDE.md's "A watchdog check"
asks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import watchdog


ROOT = Path(__file__).resolve().parents[2]


def usage_lines() -> list[str]:
    """The header as `usage_of` (lib/common.sh) prints it: the comment lines after the shebang, up to the
    code."""
    lines = []
    for line in (ROOT / "libexec" / "bosun-watchdog").read_text().splitlines()[1:]:
        if not line.startswith("#"):
            break
        lines.append(line.removeprefix("#").removeprefix(" "))
    return lines


class UsageTest(unittest.TestCase):
    def test_the_usage_describes_every_check_on_a_line_of_its_own(self) -> None:
        lines = usage_lines()
        self.assertTrue(lines[0].startswith("Usage: bosun watchdog"), lines[0])
        for check in watchdog.CHECKS:
            with self.subTest(check=check.name):
                self.assertTrue(
                    any(line.startswith(f"  {check.name} ") for line in lines),
                    f"no line '  {check.name} ...' in libexec/bosun-watchdog's usage",
                )


if __name__ == "__main__":
    unittest.main()
