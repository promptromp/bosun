"""pytest setup for the unit tests (the commit hook runs them with pytest): no inherited GIT_*
variables, and a loud failure if the repository the tests run from changes during the run.

A commit hook exports GIT_DIR and GIT_INDEX_FILE for the repository being committed; a test that
runs git in a scratch folder would otherwise commit to, or re-initialise, that repository.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

for _name in [name for name in os.environ if name.startswith("GIT_")]:
    del os.environ[_name]


def git_value(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, check=False, timeout=30)
    except FileNotFoundError:
        return ""
    return result.stdout.strip()


def repo_state() -> tuple[str, str, str]:
    """This checkout's HEAD, the shared repository's HEAD and its core.bare."""
    common = git_value("-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return "", "", ""
    return (
        git_value("-C", str(ROOT), "rev-parse", "HEAD"),
        git_value(f"--git-dir={common}", "rev-parse", "HEAD"),
        git_value(f"--git-dir={common}", "config", "--get", "core.bare"),
    )


BEFORE = repo_state()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    after = repo_state()
    if after != BEFORE:
        print(
            f"\n!!! THE REAL REPOSITORY CHANGED DURING THE UNIT TESTS !!!\nbefore: {BEFORE}\nafter:  {after}"
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
