"""What bosun changed in the agents' own files, on record so that uninstall undoes exactly that."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_changes


class SetupRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name)

    def test_setups_are_recorded_and_forgotten(self) -> None:
        self.assertEqual(agent_changes.read(self.state)[agent_changes.SETUPS], [])
        agent_changes.record_setup(self.state, "stub")
        agent_changes.record_setup(self.state, "stub")  # once is enough
        agent_changes.record_setup(self.state, "other")
        self.assertEqual(agent_changes.read(self.state)[agent_changes.SETUPS], ["stub", "other"])
        self.assertEqual(agent_changes.setups_made(self.state), ("stub", "other"))
        agent_changes.forget_setup(self.state, "stub")
        agent_changes.forget_setup(self.state, "stub")  # nothing to forget
        self.assertEqual(agent_changes.setups_made(self.state), ("other",))

    def test_a_setup_record_does_not_disturb_trust_or_notes(self) -> None:
        agent_changes.record_trust(self.state, "claude", str(self.state))
        agent_changes.record_note(self.state, self.state / "note.md")
        agent_changes.record_setup(self.state, "stub")
        found = agent_changes.read(self.state)
        self.assertEqual(found[agent_changes.TRUST], [["claude", str(self.state.resolve())]])
        self.assertEqual(found[agent_changes.NOTES], [str(self.state / "note.md")])

    def test_a_setup_entry_that_is_not_well_formed_is_ignored(self) -> None:
        (self.state / agent_changes.FILE).write_text('{"setups": ["ok", 3, null, ["x"]]}')
        self.assertEqual(agent_changes.setups_made(self.state), ("ok",))
        (self.state / agent_changes.FILE).write_text('{"setups": "stub"}')
        self.assertEqual(agent_changes.setups_made(self.state), ())


if __name__ == "__main__":
    unittest.main()
