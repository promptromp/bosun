"""Every helper a bosun shell script calls exists. A misspelled or removed function is a silent failure in
shell: `helper "$dir" && return 0` reads "command not found" as a plain no, and carries on down the other
path.
shellcheck does not look for this. Helpers here have an underscore in their name, and outside tools do not,
so that is what this goes by."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFINITION = re.compile(r"^\s*(?:function\s+)?([a-z][a-z0-9_]*)\s*\(\)\s*\{", re.MULTILINE)
COMMAND = re.compile(
    r"(?:^\s*|&&\s*|\|\|\s*|;\s*|\$\(\s*|\b(?:then|do|else|if|elif|while|until)\s+|!\s+)"
    r"([a-z][a-z0-9]*_[a-z0-9_]*)(?=\s|$|\)|;)",
    re.MULTILINE,
)
QUOTED = re.compile(r"'[^']*'")
SITE_DEFINED = {"setup_steps"}  # each site's own setup.sh


def shell_files() -> list[Path]:
    folders = ("libexec/*", "lib/*.sh", "lib/launch/*.sh", "lib/harnesses/*.sh", "lib/platforms/*.sh")
    candidates = [path for folder in folders for path in ROOT.glob(folder)]
    return [
        path
        for path in candidates
        if path.read_text().startswith(("#!/bin/bash", "#!/bin/sh", "# shellcheck"))
    ]


def without_comments_and_quotes(text: str) -> str:
    """jq and awk programs are in single quotes, and what a comment says is not a call."""
    lines = (line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return QUOTED.sub("''", "\n".join(lines))


class ShellFunctionsTest(unittest.TestCase):
    def test_every_helper_called_is_defined(self) -> None:
        files = shell_files()
        defined = {name for path in files for name in DEFINITION.findall(path.read_text())}
        missing = {
            f"{path.relative_to(ROOT)}: {name}"
            for path in files
            for name in COMMAND.findall(without_comments_and_quotes(path.read_text()))
            if name not in defined | SITE_DEFINED
        }
        self.assertEqual(sorted(missing), [])

    def test_it_would_notice_one(self) -> None:
        self.assertEqual(COMMAND.findall('  a_removed_helper "$1" && return 0\n'), ["a_removed_helper"])
        self.assertEqual(COMMAND.findall('x="$(another_helper "$1")"\n'), ["another_helper"])
        self.assertEqual(COMMAND.findall("some_var=1\n"), [])


if __name__ == "__main__":
    unittest.main()
