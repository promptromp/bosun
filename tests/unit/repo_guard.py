"""What the unit tests must leave alone in the repository they run from (conftest.py checks it before
and after the run; test_repo_guard.py tests it).

Exactly three values of this checkout, read with git: its HEAD, the set of paths staged in its own
index, and core.bare. It does not look at the working tree or at untracked files. Git worktrees share
one repository (refs, branches, the stash), so the shared repository's HEAD is not on the list: it is
the main checkout's, and moves whenever a session in another worktree commits, merges or switches
branch, which failed a run that had touched nothing. tests/smoke.sh's `repo_state` is the same check
in shell."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def git_value(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, check=False, timeout=30)
    except FileNotFoundError:
        return ""
    return result.stdout.strip()


def repo_state(root: Path) -> tuple[str, str, str]:
    """<root>'s HEAD, a digest of what it has staged, and the repository's core.bare."""
    here = str(root)
    head = git_value("-C", here, "rev-parse", "HEAD")
    if not head:
        return "", "", ""
    staged = git_value("-C", here, "diff", "--cached", "--raw", "--no-abbrev")
    return (
        head,
        hashlib.sha256(staged.encode()).hexdigest()[:12],
        git_value("-C", here, "config", "--get", "core.bare"),
    )
