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
        self.assertEqual(ub.listed(self.state), [])

    def test_a_backup_cut_short_does_not_cost_a_good_one_and_is_pruned(self) -> None:
        good = [self.take(0), self.take(1)]
        cut = self.state / "backups" / "20261105-090500-aaa1111-bbb2222"
        (cut / "state").mkdir(parents=True)
        (cut / "state" / "ledger.json").write_text("copied\n")
        good += [self.take(600), self.take(601)]
        gone = ub.prune(self.state)
        self.assertEqual(sorted(gone), sorted([good[0], cut]))
        self.assertEqual(ub.listed(self.state), good[1:])

    def test_the_folder_is_named_by_utc_time_and_seven_character_commits(self) -> None:
        backup = ub.take(self.state, self.config, "a" * 40, "b" * 40, {}, NOW)
        self.assertEqual(backup.name, "20261105-090000-aaaaaaa-bbbbbbb")

    def test_restore_deletes_what_the_failed_step_made_under_the_prefixes(self) -> None:
        backup = self.take()
        (self.config / "knowledge" / "format.json").write_text("{}\n")
        (self.config / "knowledge" / "sub").mkdir()
        (self.config / "knowledge" / "sub" / "x").write_text("x\n")
        (self.state / "journal" / "new.jsonl").write_text("{}\n")
        (self.state / "journal" / "held.lock").write_text("")
        (self.state / "ledger.json").write_text("changed\n")
        done = ub.restore(backup, self.state, self.config, only=list(ub.FROZEN))
        self.assertEqual(
            done,
            [
                "config/knowledge/format.json",
                "config/knowledge/sub/x",
                "state/journal/new.jsonl",
                "state/ledger.json",
            ],
        )
        self.assertFalse((self.config / "knowledge" / "sub").exists())
        self.assertTrue((self.state / "journal" / "held.lock").exists())
        self.assertEqual((self.state / "journal" / "a.json").read_text(), "journal\n")

    def test_restore_deletes_a_folder_and_file_the_backup_never_had(self) -> None:
        shutil.rmtree(self.state / "journal")
        (self.state / "ledger.md").unlink()
        backup = self.take()
        (self.state / "journal").mkdir()
        (self.state / "journal" / "new.jsonl").write_text("{}\n")
        (self.state / "ledger.md").write_text("new\n")
        done = ub.restore(backup, self.state, self.config, only=["state/journal", "state/ledger.md"])
        self.assertEqual(done, ["state/journal/new.jsonl", "state/ledger.md"])
        self.assertFalse((self.state / "journal").exists())
        self.assertFalse((self.state / "ledger.md").exists())

    def test_a_trailing_slash_means_the_same_prefix(self) -> None:
        backup = self.take()
        self.assertEqual(ub.restore(backup, self.state, self.config, ["config/knowledge/"]), [])
        self.assertTrue((self.config / "knowledge" / "a.json").exists())
        (self.config / "knowledge" / "a.json").write_text("changed\n")
        self.assertEqual(
            ub.restore(backup, self.state, self.config, ["config/knowledge//"]), ["config/knowledge/a.json"]
        )

    def test_a_bad_prefix_is_refused_before_anything_is_written(self) -> None:
        backup = self.take()
        (self.state / "ledger.json").write_text("changed\n")
        bad_ones = (
            "state",
            "config/",
            "",
            "state/backups",
            "state/update/x",
            "config/../state",
            "config/./a",
            "other/x",
        )
        for bad in bad_ones:
            with self.assertRaises(ub.BackupError, msg=bad):
                ub.restore(backup, self.state, self.config, ["state/ledger.json", bad])
        self.assertEqual((self.state / "ledger.json").read_text(), "changed\n")

    def test_a_link_deeper_than_the_top_level_is_replaced_not_written_through(self) -> None:
        backup = self.take()
        outside = self.config.parent / "outside.txt"
        outside.write_text("mine\n")
        (self.config / "knowledge" / "a.json").unlink()
        (self.config / "knowledge" / "a.json").symlink_to(outside)
        ub.restore(backup, self.state, self.config, ["config/knowledge"])
        self.assertEqual(outside.read_text(), "mine\n")
        self.assertFalse((self.config / "knowledge" / "a.json").is_symlink())
        self.assertEqual((self.config / "knowledge" / "a.json").read_text(), "knowledge\n")

    def test_a_folder_that_became_a_file_is_put_back(self) -> None:
        (self.config / "knowledge" / "sub").write_text("file\n")
        backup = self.take()
        (self.config / "knowledge" / "sub").unlink()
        (self.config / "knowledge" / "sub").mkdir()
        (self.config / "knowledge" / "sub" / "x").write_text("x\n")
        done = ub.restore(backup, self.state, self.config, ["config/knowledge"])
        self.assertEqual(done, ["config/knowledge/sub", "config/knowledge/sub/x"])
        self.assertEqual((self.config / "knowledge" / "sub").read_text(), "file\n")

    def test_a_link_inside_is_recorded_and_restored_not_deleted(self) -> None:
        (self.config / "knowledge" / "current").symlink_to("a.json")
        backup = self.take()
        self.assertEqual(ub.read_manifest(backup).files["config/knowledge/current"], "link:a.json")
        (self.config / "knowledge" / "current").unlink()
        (self.config / "knowledge" / "current").symlink_to("elsewhere")
        self.assertEqual(
            ub.restore(backup, self.state, self.config, ["config/knowledge"]), ["config/knowledge/current"]
        )
        self.assertEqual((self.config / "knowledge" / "current").readlink(), Path("a.json"))
        self.assertEqual(ub.restore(backup, self.state, self.config, ["config/knowledge"]), [])

    def test_a_linked_knowledge_folder_backs_up_and_restores_its_files(self) -> None:
        real = self.config.parent / "dotfiles" / "knowledge"
        real.parent.mkdir()
        shutil.move(str(self.config / "knowledge"), real)
        (self.config / "knowledge").symlink_to(real)
        backup = self.take()
        self.assertFalse((backup / "config" / "knowledge").is_symlink())
        self.assertIn("config/knowledge/a.json", ub.read_manifest(backup).files)
        (real / "a.json").write_text("changed\n")
        (real / "new.json").write_text("new\n")
        done = ub.restore(backup, self.state, self.config, ["config/knowledge"])
        self.assertEqual(done, ["config/knowledge/a.json", "config/knowledge/new.json"])
        self.assertTrue((self.config / "knowledge").is_symlink())  # the link stays a link
        self.assertEqual((real / "a.json").read_text(), "knowledge\n")

    def test_a_linked_settings_file_is_written_through(self) -> None:
        real = self.config.parent / "dotfiles" / "bosun.env"
        real.parent.mkdir()
        shutil.move(str(self.config / "bosun.env"), real)
        (self.config / "bosun.env").symlink_to(real)
        backup = self.take()
        real.write_text("changed\n")
        self.assertEqual(
            ub.restore(backup, self.state, self.config, ["config/bosun.env"]), ["config/bosun.env"]
        )
        self.assertTrue((self.config / "bosun.env").is_symlink())
        self.assertEqual(real.read_text(), "bosun.env\n")

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
