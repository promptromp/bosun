"""Unit tests for lib/update_backups.py (docs/design/update.md, "Backups"). Fictional data."""

from __future__ import annotations

import fnmatch
import shutil
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import update_backups as ub
from uninstall_plan import CONFIG_FILES, STATE_FILES


NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


def lay_out(folder: Path, table: tuple[tuple[str, str], ...]) -> None:
    """One file or folder per name of <table>, a pattern with its star filled in."""
    folder.mkdir(parents=True, exist_ok=True)
    for pattern, _kind in table:
        name = pattern.replace("*", "x")
        assert fnmatch.fnmatchcase(name, pattern)
        if name in (
            "journal",
            "events",
            "knowledge",
            "learn",
            "tasks",
            "update",
            "backups",
            "logs",
            "dry-runs",
        ):
            (folder / name).mkdir(exist_ok=True)
            (folder / name / "a.json").write_text(f"{name}\n")
        elif name != ".DS_Store":
            (folder / name).write_text(f"{name}\n")


class BackupsTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.state = tmp / "state"
        self.config = tmp / "config"
        lay_out(self.state, STATE_FILES)
        lay_out(self.config, CONFIG_FILES)

    def take(self, seconds: int = 0) -> Path:
        return ub.take(
            self.state, self.config, "aaa1111", "bbb2222", {"ledger": 3}, NOW + timedelta(seconds=seconds)
        )

    def test_the_set_is_records_settings_and_work_in_flight(self) -> None:
        names = {name for name, _ in ub.backup_set(self.state, self.config)}
        for wanted in (
            "state/ledger.json",
            "state/journal",
            "state/events",
            "config/knowledge",
            "config/bosun.env",
        ):
            self.assertIn(wanted, names)
        for left_out in ("state/logs", "state/backups", "state/update", "state/dry-runs"):
            self.assertNotIn(left_out, names)

    def test_a_backup_lists_every_file_with_its_digest(self) -> None:
        manifest = ub.read_manifest(self.take())
        self.assertEqual(dict(manifest.formats), {"ledger": 3})
        self.assertEqual((manifest.from_commit, manifest.to_commit), ("aaa1111", "bbb2222"))
        self.assertEqual(manifest.files["state/ledger.json"], ub.digest(self.state / "ledger.json"))
        self.assertIn("state/events/a.json", manifest.files)
        self.assertIn("config/knowledge/a.json", manifest.files)

    def test_restore_puts_back_only_what_changed_under_its_prefixes(self) -> None:
        backup = self.take()
        (self.state / "ledger.json").write_text("changed\n")
        (self.state / "events" / "a.json").write_text("changed\n")
        done = ub.restore(backup, self.state, self.config, only=["state/ledger.json"])
        self.assertEqual(done, ["state/ledger.json"])
        self.assertEqual((self.state / "ledger.json").read_text(), "ledger.json\n")
        self.assertEqual((self.state / "events" / "a.json").read_text(), "changed\n")

    def test_restore_refuses_a_backup_file_that_differs_from_its_manifest(self) -> None:
        backup = self.take()
        (backup / "state" / "ledger.json").write_text("tampered\n")
        (self.state / "ledger.json").write_text("changed\n")
        with self.assertRaises(ub.BackupError):
            ub.restore(backup, self.state, self.config, only=["state"])

    def test_a_folder_without_a_manifest_is_no_backup(self) -> None:
        (self.state / "backups" / "cut-short").mkdir(parents=True)
        with self.assertRaises(ub.BackupError):
            ub.read_manifest(self.state / "backups" / "cut-short")

    def test_three_are_kept(self) -> None:
        backups = [self.take(seconds) for seconds in range(4)]
        self.assertEqual(ub.prune(self.state), [backups[0]])
        self.assertEqual(ub.listed(self.state), backups[1:])
        self.assertFalse(backups[0].exists())

    def test_a_backup_does_not_hold_the_earlier_ones(self) -> None:
        first = self.take()
        second = self.take(1)
        self.assertFalse((second / "state" / "backups").exists())
        self.assertTrue(first.exists())

    def test_delete_all_takes_the_rehearsal_too(self) -> None:
        self.take()
        self.take(1)
        rehearsal = self.state / "update" / "rehearsal"
        rehearsal.mkdir(parents=True)
        (rehearsal / "x").write_text("x\n")
        self.assertEqual(ub.delete_all(self.state), 2)
        self.assertEqual(ub.listed(self.state), [])
        self.assertFalse((self.state / "backups").exists())
        self.assertFalse(rehearsal.exists())
        self.assertEqual(ub.delete_all(self.state), 0)


if __name__ == "__main__":
    unittest.main()
