"""pytest setup for the unit tests (the commit hook runs them with pytest): no inherited GIT_*
variables, the journal's flushes without F_FULLFSYNC (pytest_configure), and a loud failure if
this checkout's HEAD, staged files or core.bare change during the run (repo_guard.py).

A commit hook exports GIT_DIR and GIT_INDEX_FILE for the repository being committed; a test that
runs git in a scratch folder would otherwise commit to, or re-initialise, that repository.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from repo_guard import repo_state


ROOT = Path(__file__).resolve().parents[2]

for _name in [name for name in os.environ if name.startswith("GIT_")]:
    del os.environ[_name]


BEFORE = repo_state(ROOT)


def pytest_configure(config: pytest.Config) -> None:
    """The journal's writes in this process flush with fsync, not macOS's F_FULLFSYNC, which waits
    for the drive's own cache: run in parallel, every worker queued behind that one drive, and the
    suite took more than twice as long. What F_FULLFSYNC adds (surviving a power cut) no unit test
    can show; test_journal checks that a commit asks for it, and the commands the tests run as
    subprocesses keep it.

    And the check that each file a test reads or runs selects it in the commit hook's run
    (impacted_guard.py)."""
    import impacted_guard
    import journal

    journal.FULL_FSYNC = None
    config.pluginmanager.register(impacted_guard, "impacted_guard")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    after = repo_state(ROOT)
    if after != BEFORE:
        print(
            f"\n!!! THE REAL REPOSITORY CHANGED DURING THE UNIT TESTS !!!\nbefore: {BEFORE}\nafter:  {after}"
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
