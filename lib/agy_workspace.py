#!/usr/bin/env python3
"""Where agy looks for a workspace's own files, and what in them would make an agy window unsafe to start.
Design: docs/design/antigravity.md.

Usage: agy_workspace.py hooks <dir>
           Prints each thing under an `.agents/` folder that keeps an agy window in <dir> from starting, one
           path per line, and exits 0 whether or not there are any. It looks in <dir> and in every folder
           above it up to and including the home folder (up to `/` for a folder outside it), by the path
           given and by its resolved path, and finds: an `.agents` that is a link, a `plugins` folder in it
           (whatever it holds: agy 1.5.13 runs the hooks of a plugin there, seen 2026-11-20, though `agy
           plugin list` does not list it), and any file in it, at any depth, named like `*hooks*.json`,
           `plugin.json`, `plugins.json` or `mcp_config.json`. Rules, skills and workflows there are left
           alone. It fails closed: an `.agents` too large to look through, or one that cannot be read, is
           printed too. agy runs a workspace's hooks outside its sandbox, so a window with any of these is
           not started (`check_agy_workspace`, lib/launch/agy.sh). The check runs when a window starts, not
           while it runs.

Exit codes: 0 done, 2 a command line that cannot be read.
"""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path


AGENTS = ".agents"
PLUGINS_DIR = Path(AGENTS) / "plugins"  # a plugin per folder, found and loaded by agy
PLUGINS_FILE = Path(AGENTS) / "plugins.json"  # registers plugins from other folders
REFUSED_NAMES = ("*hooks*.json", "plugin.json", "plugins.json", "mcp_config.json")  # case-folded globs
MAX_ENTRIES = 5000  # an `.agents` with more than this is not looked through, and refuses
COMMAND_WORDS = 2  # `hooks <dir>`


def workspace_folders(workdir: Path) -> list[Path]:
    """<workdir> and each folder above it up to its repo's root, the folder holding `.git`; <workdir>
    alone when there is none. (What the connectors check reads servers from.)"""
    folders = [workdir, *workdir.parents]
    for index, folder in enumerate(folders):
        if (folder / ".git").exists():
            return folders[: index + 1]
    return folders[:1]


def workspace_plugins(folder: Path) -> list[Path]:
    """The plugin folders in <folder>'s `.agents/plugins/`, sorted."""
    plugins = folder / PLUGINS_DIR
    return sorted(path for path in plugins.iterdir() if path.is_dir()) if plugins.is_dir() else []


def folders_up_to_home(workdir: Path, home: Path) -> list[Path]:
    """<workdir> and each folder above it, up to and including <home> when it is under it (else up to `/`),
    by the path as given and by its resolved path, nearest first, each once."""
    found: list[Path] = []
    for start in (workdir.resolve(), Path(os.path.abspath(workdir))):
        for root in (home.resolve(), Path(os.path.abspath(home))):
            if start == root or root in start.parents:
                chain = [start, *start.parents]
                chain = chain[: chain.index(root) + 1]
                break
        else:
            chain = [start, *start.parents]
        found.extend(folder for folder in chain if folder not in found)
    return found


def refused_name(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name.casefold(), pattern) for pattern in REFUSED_NAMES)


def agents_findings(agents: Path) -> list[Path]:
    """What in one `.agents` keeps a window from starting: the folder itself when it is a link or cannot be
    looked through, its `plugins` folder, and each file named as REFUSED_NAMES says, at any depth."""
    if agents.is_symlink() or not agents.is_dir():
        return [agents]
    found = [agents / "plugins"] if os.path.lexists(agents / "plugins") else []
    seen = 0
    for top, folders, files in os.walk(agents, onerror=lambda _error: found.append(agents)):
        seen += len(folders) + len(files)
        if seen > MAX_ENTRIES:
            return [*found, agents]
        found.extend(Path(top) / name for name in sorted(files) if refused_name(name))
        found.extend(Path(top) / name for name in sorted(folders) if refused_name(name))
    return list(dict.fromkeys(found))


def workspace_hooks(workdir: Path, home: Path | None = None) -> list[Path]:
    """Everything under an `.agents` from <workdir> up to the home folder that keeps an agy window from
    starting, nearest folder first."""
    found: list[Path] = []
    for folder in folders_up_to_home(workdir, home or Path.home()):
        if os.path.lexists(folder / AGENTS):
            found.extend(agents_findings(folder / AGENTS))
    return list(dict.fromkeys(found))


def main(argv: list[str]) -> int:
    if len(argv) != COMMAND_WORDS or argv[0] != "hooks":
        print((__doc__ or "").strip(), file=sys.stderr)
        return 2
    for path in workspace_hooks(Path(argv[1])):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
