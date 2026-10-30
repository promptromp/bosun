"""Unit tests for the console's data (lib/console_data.py): reading the ledger as a snapshot
(missing, invalid, valid), the stamp that changes when lib/ledger.py replaces the file, the cache
that re-reads only then, and the /api/version model. Fictional data: acme's invoice exporter
(tests/fixtures/console/acme-ledger.json)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_data
import ledger


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "console" / "acme-ledger.json"
FIXTURE_ITEMS = 19


class SnapshotTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / console_data.LEDGER_FILE

    def test_the_file_name_is_the_ledgers(self) -> None:
        self.assertEqual(ledger.Store(state_dir=self.path.parent).ledger_file, self.path)

    def test_a_missing_ledger_is_empty_not_an_error(self) -> None:
        snapshot = console_data.read_snapshot(self.path)
        self.assertFalse(snapshot.exists)
        self.assertEqual((snapshot.stamp, snapshot.error, snapshot.ledger), ("none", "", {}))

    def test_the_fixture_reads_whole(self) -> None:
        self.path.write_text(FIXTURE.read_text())
        snapshot = console_data.read_snapshot(self.path)
        self.assertTrue(snapshot.exists)
        self.assertEqual(snapshot.error, "")
        self.assertEqual(len(snapshot.ledger["items"]), FIXTURE_ITEMS)

    def test_invalid_json_is_an_error_without_content(self) -> None:
        self.path.write_text('{"items": {"mypr:acme/exporter#102": ')
        snapshot = console_data.read_snapshot(self.path)
        self.assertTrue(snapshot.exists)
        self.assertIn("not a valid ledger", snapshot.error)
        self.assertNotIn("acme", snapshot.error)
        self.assertEqual(snapshot.ledger, {})

    def test_a_wrong_shape_is_an_error(self) -> None:
        for text in ("[]", '{"items": []}', '{"runs": "x"}'):
            with self.subTest(text=text):
                self.path.write_text(text)
                self.assertIn("unexpected structure", console_data.read_snapshot(self.path).error)

    def test_the_stamp_changes_when_the_ledger_replaces_the_file(self) -> None:
        ledger.write_atomically(self.path, FIXTURE.read_text())
        first = console_data.read_snapshot(self.path).stamp
        self.assertEqual(console_data.read_snapshot(self.path).stamp, first)
        ledger.write_atomically(self.path, FIXTURE.read_text())  # same text, new file
        self.assertNotEqual(console_data.read_snapshot(self.path).stamp, first)


class CacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / console_data.LEDGER_FILE
        self.cache = console_data.SnapshotCache(self.path)

    def test_rereads_only_after_a_change(self) -> None:
        ledger.write_atomically(self.path, FIXTURE.read_text())
        first = self.cache.get()
        self.assertIs(self.cache.get(), first)
        data = json.loads(FIXTURE.read_text())
        data["items"].pop("mypr:acme/exporter#103")
        ledger.write_atomically(self.path, json.dumps(data))
        second = self.cache.get()
        self.assertIsNot(second, first)
        self.assertEqual(len(second.ledger["items"]), FIXTURE_ITEMS - 1)

    def test_follows_the_file_appearing_and_going(self) -> None:
        self.assertFalse(self.cache.get().exists)
        ledger.write_atomically(self.path, FIXTURE.read_text())
        self.assertTrue(self.cache.get().exists)
        self.path.unlink()
        self.assertFalse(self.cache.get().exists)


class VersionModelTestCase(unittest.TestCase):
    def test_counts_items_and_reports_the_schema_version(self) -> None:
        snapshot = console_data.Snapshot("1-2-3", 1_700_000_000.0, json.loads(FIXTURE.read_text()))
        self.assertEqual(
            console_data.version_model(snapshot),
            {
                "stamp": "1-2-3",
                "ledger": {
                    "exists": True,
                    "mtime": "2023-11-14T22:13:20Z",
                    "version": 1,
                    "items": FIXTURE_ITEMS,
                    "error": None,
                },
            },
        )

    def test_no_ledger(self) -> None:
        model = console_data.version_model(console_data.Snapshot())
        self.assertEqual(model["stamp"], "none")
        self.assertEqual(
            model["ledger"], {"exists": False, "mtime": None, "version": None, "items": 0, "error": None}
        )

    def test_is_json(self) -> None:
        json.dumps(console_data.version_model(console_data.Snapshot(error="bad")))


if __name__ == "__main__":
    unittest.main()
