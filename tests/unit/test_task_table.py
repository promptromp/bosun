"""docs/orchestrator.md's table of delegated tasks is what `bosun delegate --list --markdown` prints from the
task files (orchestrator/tasks/), row for row: CLAUDE.md's "A task rule" pastes the new rows there."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import delegate


ROOT = Path(__file__).resolve().parents[2]


class TaskTableTest(unittest.TestCase):
    def test_the_table_under_delegated_tasks_is_the_task_files_row_for_row(self) -> None:
        doc = (ROOT / "docs" / "orchestrator.md").read_text()
        section = doc.split("\n## Delegated tasks", 1)[1].split("\n## ", 1)[0]
        table = [
            line for line in section.splitlines() if line.startswith("| ") and not line.startswith("| Task |")
        ]
        with mock.patch.dict(os.environ):
            os.environ.pop("BOSUN_CONFIG_DIR", None)  # the repo's task types, never a site's
            rows = delegate.list_types(delegate.load_task_types(), markdown=True).splitlines()
        self.assertTrue(rows)
        self.assertEqual(sorted(table), sorted(rows))


if __name__ == "__main__":
    unittest.main()
