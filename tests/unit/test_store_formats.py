"""The format version each store records, and the digests that pin it (the ledger's ledger.json,
the knowledge base's format.json): a change to what a store holds on disk cannot land without a
version bump and a migration (docs/design/update.md, "Migrations")."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import knowledge
import knowledge_store as store
import ledger
import update_gate


# The digest of each store's format (ledger.format_digest, knowledge_store.format_digest; digests, not
# secrets), by ledger.LEDGER_FORMAT and knowledge_store.KNOWLEDGE_FORMAT.
FORMAT_DIGESTS = {
    "ledger": {
        1: "74070ac131b5f1de8246337cf27a6c7a164dcf0d32a20e82b534b65da45df656",  # pragma: allowlist secret
    },
    "knowledge": {
        1: "75e67460be801fd70bbf88628c8457022ff7469e21aab11e1463393b7d200b4b",  # pragma: allowlist secret
    },
}


class TestPinnedFormats(unittest.TestCase):
    def test_the_ledger_format_is_pinned_to_its_version(self) -> None:
        """ledger.json's fields, maps and the change envelope are what is on disk. A change to
        them fails here: bump ledger.LEDGER_FORMAT, add a migration in lib/migrations.py with a
        fixture of the old format, and record the new digest under the new version (CLAUDE.md,
        "A data format change")."""
        self.assertEqual(FORMAT_DIGESTS["ledger"].get(ledger.LEDGER_FORMAT), ledger.format_digest())

    def test_the_knowledge_format_is_pinned_to_its_version(self) -> None:
        """The knowledge base's files, the ops of its journal and the change envelope are what is
        on disk. A change to them fails here: bump knowledge_store.KNOWLEDGE_FORMAT, add a
        migration in lib/migrations.py with a fixture of the old format, and record the new
        digest under the new version (CLAUDE.md, "A data format change")."""
        self.assertEqual(FORMAT_DIGESTS["knowledge"].get(store.KNOWLEDGE_FORMAT), store.format_digest())


class TestKnowledgeFormat(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "knowledge"
        self.state = Path(self.tmp.name) / "state"
        knowledge.configure(self.root, git=True, state_dir=str(self.state))

    def run_kg(self, *argv: str) -> None:
        args = knowledge.build_parser().parse_args(list(argv))
        args.func(args)

    def test_a_new_knowledge_base_records_its_format(self) -> None:
        self.assertEqual(store.store_format(store.Paths(self.root)), 0)
        self.run_kg("add-entity", "repo/invoice-exporter", "--name", "invoice exporter")
        paths = store.Paths(self.root)
        self.assertEqual(json.loads(paths.format.read_text()), {"format": 1})
        tracked = subprocess.run(
            ["git", "-C", str(self.root), "ls-files"], capture_output=True, text=True, check=True
        ).stdout.split()
        self.assertIn("format.json", tracked)

    def test_a_new_knowledge_base_records_the_current_format(self) -> None:
        with mock.patch.object(store, "KNOWLEDGE_FORMAT", 2):
            self.run_kg("add-entity", "repo/invoice-exporter", "--name", "invoice exporter")
        self.assertEqual(store.store_format(store.Paths(self.root)), 2)

    def test_an_ordinary_write_does_not_stamp_a_knowledge_base_without_a_format(self) -> None:
        """Only a migration changes a store's version: an ordinary write keeps it, and code newer
        than the store writes nothing to it (update_gate: run bosun update to migrate it)."""
        self.run_kg("add-entity", "repo/invoice-exporter", "--name", "invoice exporter")
        paths = store.Paths(self.root)
        paths.format.unlink()
        self.run_kg("add-entity", "gate/smoke-tests", "--name", "Smoke tests")
        self.assertFalse(paths.format.exists())
        with mock.patch.object(store, "KNOWLEDGE_FORMAT", 2), self.assertRaises(update_gate.StoreOlder):
            self.run_kg("add-entity", "step/merge", "--name", "Merge to main")
        self.assertFalse(paths.format.exists())
        self.assertEqual(store.store_format(paths), 1)

    def test_a_knowledge_base_from_before_formats_is_format_1(self) -> None:
        self.run_kg("add-entity", "repo/invoice-exporter", "--name", "invoice exporter")
        paths = store.Paths(self.root)
        paths.format.unlink()
        self.assertEqual(store.store_format(paths), 1)
        store.write_format(paths, 2)
        self.assertEqual(store.store_format(paths), 2)
        self.assertEqual(store.store_format(store.Paths(Path(self.tmp.name) / "none")), 0)

    def test_a_format_file_that_says_no_positive_whole_number_is_refused(self) -> None:
        paths = store.Paths(self.root)
        paths.root.mkdir(parents=True)
        for text in (
            "[]",
            "null",
            "{}",
            '{"format": true}',
            '{"format": "2"}',
            '{"format": 1.9}',
            '{"format": 0}',
            '{"format": -1}',
            '{"format": null}',
            "{nope",
            "\udcff",
        ):
            with self.subTest(text=text):
                paths.format.write_bytes(text.encode("utf-8", "surrogateescape"))
                with self.assertRaisesRegex(store.StoreError, "format.json"):
                    store.store_format(paths)
        paths.format.write_text('{"format": 2.0}')  # a whole number written by hand
        self.assertEqual(store.store_format(paths), 2)
        paths.format.unlink()
        paths.format.mkdir()
        with self.assertRaisesRegex(store.StoreError, "format.json"):
            store.store_format(paths)

    def test_a_write_into_a_malformed_format_is_refused_with_a_message(self) -> None:
        self.run_kg("add-entity", "repo/invoice-exporter", "--name", "invoice exporter")
        paths = store.Paths(self.root)
        paths.format.write_text('{"format": "2"}')
        with self.assertRaisesRegex(store.StoreError, "positive whole number"):
            self.run_kg("add-entity", "gate/smoke-tests", "--name", "Smoke tests")


class TestLedgerFormat(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ledger.configure(self.tmp.name)

    def upsert(self, item_id: str) -> None:
        args = ledger.build_parser().parse_args(
            ["upsert", item_id, "--kind", "review-request", "--title", "T"]
        )
        args.func(args)

    def raw_version(self) -> object:
        return json.loads((Path(self.tmp.name) / "ledger.json").read_text())["version"]

    def test_a_new_ledger_records_the_current_format(self) -> None:
        self.assertEqual(ledger.store_format(Path(self.tmp.name)), 0)
        with mock.patch.object(ledger, "LEDGER_FORMAT", 2):
            self.upsert("review:acme/exporter#1")
        self.assertEqual(self.raw_version(), 2)

    def test_an_ordinary_write_keeps_the_version_it_loaded(self) -> None:
        """Only a migration changes a store's version: an ordinary write keeps it, and code newer
        than the store writes nothing to it (update_gate: run bosun update to migrate it)."""
        (Path(self.tmp.name) / "ledger.json").write_text(json.dumps({"version": 1, "items": {}}))
        self.upsert("review:acme/exporter#1")
        self.assertEqual(self.raw_version(), 1)
        with mock.patch.object(ledger, "LEDGER_FORMAT", 2), self.assertRaises(update_gate.StoreOlder):
            self.upsert("review:acme/exporter#2")
        self.assertEqual(self.raw_version(), 1)
        self.assertEqual(ledger.store_format(Path(self.tmp.name)), 1)

    def test_a_ledger_that_says_no_positive_whole_number_is_refused(self) -> None:
        path = Path(self.tmp.name) / "ledger.json"
        for text in (
            "[]",
            "null",
            '"text"',
            '{"version": true}',
            '{"version": "2"}',
            '{"version": 1.9}',
            '{"version": 0}',
            '{"version": -1}',
            '{"version": null}',
            "{nope",
            "\udcff",
        ):
            with self.subTest(text=text):
                path.write_bytes(text.encode("utf-8", "surrogateescape"))
                with self.assertRaisesRegex(ledger.LedgerError, "ledger.json"):
                    ledger.store_format(Path(self.tmp.name))
        path.write_text('{"version": 1.0}')  # a whole number written by hand
        self.assertEqual(ledger.store_format(Path(self.tmp.name)), 1)
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(ledger.LedgerError, "ledger.json"):
            ledger.store_format(Path(self.tmp.name))

    def test_a_migration_records_the_format_and_keeps_the_stamp_true(self) -> None:
        """write_format, what a migration records (only a migration changes a store's version):
        ledger.json's version, and its stamp made again where a write saved it, so that the ledger
        still passes its check. A file changed outside the journal stays so, for the next write to
        adopt."""
        state = Path(self.tmp.name)
        self.upsert("review:acme/exporter#1")
        ledger.write_format(state, 2)
        self.assertEqual(self.raw_version(), 2)
        self.assertEqual(ledger.validate_unlocked(state), [])
        data = json.loads((state / "ledger.json").read_text())
        data["items"]["review:acme/exporter#1"]["title"] = "Edited by hand"
        (state / "ledger.json").write_text(json.dumps(data))
        ledger.write_format(state, 3)
        self.assertEqual(self.raw_version(), 3)
        self.assertEqual([f["kind"] for f in ledger.validate_unlocked(state)], ["edited"])

    def test_a_ledger_rebuilt_from_its_journal_records_the_current_format(self) -> None:
        """A migration never rewrites the journal, and its reader reads what an older format recorded
        as the current one, so a ledger.json made from its rebuild is in this code's format:
        not the empty ledger's 1, which a ledger at format 2 would then read as older, and refuse
        every write."""
        state = Path(self.tmp.name)
        self.upsert("review:acme/exporter#1")
        with mock.patch.object(ledger, "LEDGER_FORMAT", 2):
            ledger.write_format(state, 2)  # as a migration to format 2 leaves it
            data = json.loads((state / "ledger.json").read_text())
            data["journal"] = {}  # behind its journal (a write cut short): read and written as its rebuild
            (state / "ledger.json").write_text(json.dumps(data))
            self.assertEqual(ledger.for_reading()["version"], 2)
            self.upsert("review:acme/exporter#2")
        self.assertEqual(self.raw_version(), 2)

    def test_a_ledger_without_a_version_is_format_1(self) -> None:
        (Path(self.tmp.name) / "ledger.json").write_text(json.dumps({"items": {}}))
        self.assertEqual(ledger.store_format(Path(self.tmp.name)), 1)
        (Path(self.tmp.name) / "ledger.json").write_text("{}")  # nothing in it: no ledger yet
        self.assertEqual(ledger.store_format(Path(self.tmp.name)), 0)

    def test_a_write_into_a_malformed_version_is_refused_with_a_message(self) -> None:
        (Path(self.tmp.name) / "ledger.json").write_text(json.dumps({"version": "2", "items": {}}))
        with self.assertRaisesRegex(ledger.LedgerError, "positive whole number"):
            self.upsert("review:acme/exporter#1")


if __name__ == "__main__":
    unittest.main()
