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


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import knowledge
import knowledge_store as store
import ledger


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

    def test_a_knowledge_base_from_before_formats_is_format_1(self) -> None:
        self.run_kg("add-entity", "repo/invoice-exporter", "--name", "invoice exporter")
        paths = store.Paths(self.root)
        paths.format.unlink()
        self.assertEqual(store.store_format(paths), 1)
        store.write_format(paths, 2)
        self.assertEqual(store.store_format(paths), 2)
        self.assertEqual(store.store_format(store.Paths(Path(self.tmp.name) / "none")), 0)


class TestLedgerFormat(unittest.TestCase):
    def test_the_ledger_writes_its_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger.configure(tmp)
            self.assertEqual(ledger.store_format(Path(tmp)), 0)
            args = ledger.build_parser().parse_args(
                ["upsert", "review:acme/exporter#1", "--kind", "review-request", "--title", "T"]
            )
            args.func(args)
            self.assertEqual(ledger.store_format(Path(tmp)), ledger.LEDGER_FORMAT)


if __name__ == "__main__":
    unittest.main()
