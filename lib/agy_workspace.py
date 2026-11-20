#!/usr/bin/env python3
"""Where agy looks for a workspace's own files: the folders from a window's folder up to its repo's root, and
what in them agy would run or start. Design: docs/design/antigravity.md.

Usage: agy_workspace.py hooks <dir>
           Prints each hooks file agy would read for a window in <dir>, one path per line, and exits 0 whether
           or not there are any: `.agents/hooks.json`, the `hooks.json` of each plugin in `.agents/plugins/`
           (agy 1.5.13 runs them, seen 2026-11-20, though `agy plugin list` does not list them), and
           `.agents/plugins.json`, which registers plugins from elsewhere. agy runs such hooks outside its
           sandbox, so a window with one is not started (`check_agy_workspace`, lib/launch/agy.sh).

Exit codes: 0 done, 2 a command line that cannot be read.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


AGENTS_DIR = Path(".agents")
HOOKS_FILE = AGENTS_DIR / "hooks.json"
PLUGINS_DIR = AGENTS_DIR / "plugins"  # a plugin per folder, found and loaded by agy
PLUGINS_FILE = AGENTS_DIR / "plugins.json"  # registers plugins from other folders
COMMAND_WORDS = 2  # `hooks <dir>`


def workspace_folders(workdir: Path) -> list[Path]:
    """<workdir> and each folder above it up to its repo's root, the folder holding `.git`; <workdir>
    alone when there is none."""
    folders = [workdir, *workdir.parents]
    for index, folder in enumerate(folders):
        if (folder / ".git").exists():
            return folders[: index + 1]
    return folders[:1]


def workspace_plugins(folder: Path) -> list[Path]:
    """The plugin folders in <folder>'s `.agents/plugins/`, sorted."""
    plugins = folder / PLUGINS_DIR
    return sorted(path for path in plugins.iterdir() if path.is_dir()) if plugins.is_dir() else []


def folder_hooks(folder: Path) -> list[Path]:
    """What in <folder>'s `.agents/` would have agy run hooks: its hooks.json, each plugin's, and
    plugins.json. A link counts, even a dangling one: what is there is not bosun's to judge."""
    found = [folder / HOOKS_FILE, *(plugin / "hooks.json" for plugin in workspace_plugins(folder))]
    found.append(folder / PLUGINS_FILE)
    return [path for path in found if os.path.lexists(path)]


def workspace_hooks(workdir: Path) -> list[Path]:
    """What would have agy run hooks in the workspace folders of <workdir>, nearest folder first."""
    return [path for folder in workspace_folders(workdir.resolve()) for path in folder_hooks(folder)]


def main(argv: list[str]) -> int:
    if len(argv) != COMMAND_WORDS or argv[0] != "hooks":
        print((__doc__ or "").strip(), file=sys.stderr)
        return 2
    for path in workspace_hooks(Path(argv[1])):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
