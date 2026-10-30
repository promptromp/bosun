"""Unit tests for lib/console_kg_sources.py: memory notes named by their Library entry (only when
the Library lists them), sessions named from their transcript's title, and nothing else touched."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import claude_state
import console_files
import console_kg_sources as sources
import console_knowledge as ck
from console_library import Entry


SESSION = "abcd" * 8


def model(*refs: tuple[str, str]) -> dict[str, Any]:
    """A review-shaped model with one fact citing <refs>, labelled as console_knowledge does."""
    cited = [ck.source_view({"kind": kind, "ref": ref}, frozenset()) for kind, ref in refs]
    return {"groups": [{"facts": [{"id": "f1", "sources": cited}]}]}


class SourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.memory = self.tmp / "memory"
        self.memory.mkdir()
        (self.memory / "deploys.md").write_text(
            '---\nname: deploys\ndescription: "Every release deploys to staging first"\n---\n\nbody\n'
        )
        projects = self.tmp / "projects" / "-Users-me-acme"
        projects.mkdir(parents=True)
        title = {"type": "custom-title", "customTitle": "exporter"}
        (projects / f"{SESSION}.jsonl").write_text(
            json.dumps({"type": "user"}) + "\n" + json.dumps(title) + "\n"
        )
        patcher = mock.patch.object(claude_state, "PROJECTS_DIR", self.tmp / "projects")
        patcher.start()
        self.addCleanup(patcher.stop)
        sources.TITLES.clear()
        sources.NOTES.clear()

    def catalog(self, listed: bool) -> list[Entry]:
        env = {"BOSUN_CONSOLE_MEMORY_DIRS": str(self.memory)} if listed else {}
        return console_files.catalog(console_files.Settings.from_env(env))

    def test_a_listed_memory_note_gets_its_summary_and_library_link(self) -> None:
        shown = sources.label_sources(model(("memory", "deploys.md")), self.catalog(listed=True))
        [source] = list(sources.sources_in(shown))
        self.assertEqual(source["label"], "deploys")
        self.assertEqual(source["detail"], "Every release deploys to staging first")
        self.assertIn(source["library"], {e.key for e in self.catalog(listed=True)})

    def test_an_unlisted_memory_note_keeps_its_file_name(self) -> None:
        shown = sources.label_sources(model(("memory", "deploys.md")), self.catalog(listed=False))
        [source] = list(sources.sources_in(shown))
        self.assertNotIn("library", source)
        self.assertNotIn("detail", source)
        self.assertEqual(source["label"], "deploys")

    def test_a_session_is_named_by_its_transcript_title(self) -> None:
        shown = sources.label_sources(
            model(
                ("transcript", f"{SESSION[:8]} 2026-10-18T18:56"), ("transcript", "dcbadcba 2026-10-18T18:56")
            ),
            [],
        )
        named, unknown = sources.sources_in(shown)
        self.assertEqual(named["label"], "exporter session, Oct 18 18:56")
        self.assertEqual(unknown["label"], "session dcbadcba, Oct 18 18:56")

    def test_a_reference_that_is_not_a_session_id_reads_nothing(self) -> None:
        for ref in ("../../etc", "*", ""):
            with self.subTest(ref=ref):
                self.assertEqual(claude_state.transcripts_by_prefix(ref), [])

    def test_other_sources_are_left_alone(self) -> None:
        before = model(("slack", "#acme-prs"), ("ledger", "task:2026-10-01/1"))
        after = sources.label_sources(json.loads(json.dumps(before)), self.catalog(listed=True))
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
