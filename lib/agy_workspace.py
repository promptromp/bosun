#!/usr/bin/env python3
"""Where agy looks for a workspace's own files: the folders from a window's folder up to its repo's root, and
what in them agy would run or start. Design: docs/design/antigravity.md.

Usage: agy_workspace.py hooks <dir>
           Prints each `.agents/hooks.json` that agy would read for a window in <dir>, one path per line, and
           exits 0 whether or not there are any. agy runs the hooks of such a file when it starts, outside
           its sandbox, so a window with one is not started (`check_agy_workspace`, lib/launch/agy.sh).

Exit codes: 0 done, 2 a command line that cannot be read.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


HOOKS_FILE = Path(".agents") / "hooks.json"
COMMAND_WORDS = 2  # `hooks <dir>`


def workspace_folders(workdir: Path) -> list[Path]:
    """<workdir> and each folder above it up to its repo's root, the folder holding `.git`; <workdir>
    alone when there is none."""
    folders = [workdir, *workdir.parents]
    for index, folder in enumerate(folders):
        if (folder / ".git").exists():
            return folders[: index + 1]
    return folders[:1]


def workspace_hooks(workdir: Path) -> list[Path]:
    """Each `.agents/hooks.json` in the workspace folders of <workdir>, nearest first. A link counts, even a
    dangling one: what is there is not bosun's to judge."""
    return [
        folder / HOOKS_FILE
        for folder in workspace_folders(workdir.resolve())
        if os.path.lexists(folder / HOOKS_FILE)
    ]


def main(argv: list[str]) -> int:
    if len(argv) != COMMAND_WORDS or argv[0] != "hooks":
        print((__doc__ or "").strip(), file=sys.stderr)
        return 2
    for path in workspace_hooks(Path(argv[1])):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
