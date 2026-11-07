"""lib/update_facts.py: the facts `bosun update` gathers for its plan, and what stops it there
(docs/design/update.md, step 1). Fictional data."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import knowledge_store
import update_facts
import update_gate
import update_plan
import versions
from test_update import FROM, TO, World


class BlockerTest(World):
    def plan(self) -> update_plan.Plan:
        with (
            mock.patch.object(
                versions, "commit_of", side_effect=lambda root: FROM if root == self.old else TO
            ),
            mock.patch.object(update_facts, "window_facts", return_value=[]),
            mock.patch.object(update_facts, "jobs", return_value=([], [])),
            mock.patch.object(update_facts, "missing", return_value=[]),
            mock.patch.object(update_facts, "commits", return_value=["bbbbbbb the next version"]),
        ):
            return update_facts.plan_for(self.old, self.new, self.state, self.config)

    def test_nothing_stops_an_update_of_readable_stores(self) -> None:
        self.assertEqual(self.plan().blockers, ())

    def test_a_ledger_whose_format_cannot_be_read_is_a_blocker(self) -> None:
        (self.state / "ledger.json").write_text("{not json")
        [blocker] = self.plan().blockers
        self.assertIn("the ledger's format cannot be read", blocker)
        self.assertIn("ledger.json", blocker)

    def test_a_knowledge_base_whose_format_cannot_be_read_is_a_blocker(self) -> None:
        kb = knowledge_store.Paths(self.config / update_gate.KNOWLEDGE_DIR)
        kb.root.mkdir()
        kb.format.write_text('{"format": "two"}')
        self.assertTrue(any("the knowledge's format cannot be read" in b for b in self.plan().blockers))

    def test_a_link_a_backup_cannot_copy_is_a_blocker_naming_it(self) -> None:
        (self.config / "policy.md").symlink_to(self.config / "dotfiles" / "policy.md")
        [blocker] = self.plan().blockers
        self.assertIn(str(self.config / "policy.md"), blocker)
        self.assertIn("no backup can copy it", blocker)

    def test_versions_not_side_by_side_are_a_blocker(self) -> None:
        self.assertEqual(update_facts.install_problem(self.old, self.new), [])
        checkout = self.state.parent / "checkout"
        self.assertTrue(update_facts.install_problem(checkout, self.new))


class FactsTest(World):
    def test_a_handed_item_of_a_type_the_site_replaces_is_not_in_flight(self) -> None:
        self.ledger("record-delegation", "review:acme/exporter#12", "--window", "api", "--task", "review")
        self.ledger(
            "record-delegation", "review:acme/exporter#14", "--window", "web", "--task", "acme-review"
        )
        self.assertEqual(
            update_facts.in_flight(self.state, self.config),
            [("review:acme/exporter#12", "review"), ("review:acme/exporter#14", "acme-review")],
        )
        (self.config / "tasks").mkdir()
        (self.config / "tasks" / "acme-review.md").write_text("Our own review rules.\n")
        self.assertEqual(
            update_facts.in_flight(self.state, self.config), [("review:acme/exporter#12", "review")]
        )

    def test_prompts_keys_and_rules_come_from_the_version_folder(self) -> None:
        (self.old / "orchestrator" / "tasks").mkdir(parents=True)
        (self.old / "orchestrator" / "policy.md").write_text("Be brief.\n")
        (self.old / "orchestrator" / "tasks" / "review.md").write_text("Review it.\n")
        (self.old / "orchestrator" / "notes.md").write_text("Not a prompt.\n")
        (self.old / "lib").mkdir()
        (self.old / "lib" / "common.sh").write_text(
            'set_defaults() {\n  BOSUN_DAYS="MTWRF"\n  local x=1\n}\n'
        )
        (self.old / "lib" / "ledger.py").write_text("RULES_VERSION = 3\n")
        self.assertEqual(
            sorted(update_facts.prompts(self.old)), ["orchestrator/policy.md", "orchestrator/tasks/review.md"]
        )
        self.assertEqual(update_facts.config_keys(self.old), frozenset({"BOSUN_DAYS"}))
        self.assertEqual(update_facts.rules(self.old), {"ledger": 3, "knowledge": 0})

    def test_a_window_launched_otherwise_than_the_new_version_would_is_changed(self) -> None:
        """The new version's `launch --fingerprint` against the window's record; no record, or a
        fingerprint refused, counts as changed."""
        root = self.new
        (root / "bin").mkdir()
        stub = root / "bin" / "bosun"
        stub.write_text(
            '#!/bin/sh\n[ "$3" = known ] && echo \'{"args": "a", "files": "f"}\' && exit 0\nexit 1\n'
        )
        stub.chmod(0o755)
        record = {"fingerprint": {"args": "a", "files": "f"}}
        with mock.patch("launch_record.recorded", return_value=record):
            self.assertFalse(update_facts.launch_changed(root, "known", self.state, "s"))
            self.assertTrue(update_facts.launch_changed(root, "refused", self.state, "s"))
        with mock.patch("launch_record.recorded", return_value={"fingerprint": {"args": "a", "files": "g"}}):
            self.assertTrue(update_facts.launch_changed(root, "known", self.state, "s"))
        with mock.patch("launch_record.recorded", return_value=None):
            self.assertTrue(update_facts.launch_changed(root, "known", self.state, "s"))


if __name__ == "__main__":
    import unittest

    unittest.main()
