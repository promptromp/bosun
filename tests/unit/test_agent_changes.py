"""What bosun changed in the agents' own files, on record so that uninstall undoes exactly that."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import threading
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


class FolderRecordTest(unittest.TestCase):
    """The worktrees folders bosun made beside a window's repository (`make-folder`, run by bosun launch)."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.state = self.base / "state"
        self.folder = self.base / "Development" / "exporter.worktrees"
        self.folder.parent.mkdir()

    def make(self, *argv: str) -> tuple[int, str]:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            status = agent_changes.main(list(argv))
        return status, errors.getvalue()

    def test_a_folder_it_makes_goes_on_record(self) -> None:
        self.assertEqual(self.make("make-folder", str(self.state), str(self.folder)), (0, ""))
        self.assertTrue(self.folder.is_dir())
        self.assertEqual(agent_changes.folders_made(self.state), (str(self.folder),))

    def test_one_that_was_there_already_is_not_bosuns(self) -> None:
        self.folder.mkdir()
        self.assertEqual(self.make("make-folder", str(self.state), str(self.folder)), (0, ""))
        self.assertEqual(agent_changes.folders_made(self.state), ())

    def test_making_it_again_records_it_once(self) -> None:
        for _ in range(2):
            self.make("make-folder", str(self.state), str(self.folder))
        self.assertEqual(agent_changes.folders_made(self.state), (str(self.folder),))

    def test_a_file_in_its_place_is_one_line_and_not_recorded(self) -> None:
        self.folder.write_text("mine\n")
        status, said = self.make("make-folder", str(self.state), str(self.folder))
        self.assertEqual((status, said.strip()), (1, f"{self.folder} is there, but is not a folder"))
        self.assertEqual(self.folder.read_text(), "mine\n")
        self.assertEqual(agent_changes.folders_made(self.state), ())

    def test_only_the_folder_itself_is_made_never_its_parents(self) -> None:
        deeper = self.base / "gone" / "exporter.worktrees"
        status, said = self.make("make-folder", str(self.state), str(deeper))
        self.assertEqual(status, 1)
        self.assertIn(f"could not make {deeper}", said)
        self.assertFalse((self.base / "gone").exists())
        self.assertEqual(agent_changes.folders_made(self.state), ())

    def test_made_but_not_recorded_says_so(self) -> None:
        self.state.write_text("a file where the state folder goes\n")
        status, said = self.make("make-folder", str(self.state), str(self.folder))
        self.assertEqual(status, 1)
        self.assertIn(f"made {self.folder}, but could not put it on record", said)
        self.assertTrue(self.folder.is_dir())

    def test_usage(self) -> None:
        for argv in (
            (),
            ("make-folder", str(self.state)),
            ("make-folder", str(self.state), "relative.worktrees"),
        ):
            self.assertEqual(self.make(*argv)[0], agent_changes.USAGE, argv)
        self.assertFalse(Path("relative.worktrees").exists())

    def test_folders_are_forgotten(self) -> None:
        agent_changes.record_folder(self.state, str(self.folder))
        agent_changes.forget_folder(self.state, str(self.folder))
        agent_changes.forget_folder(self.state, str(self.folder))  # nothing to forget
        self.assertEqual(agent_changes.folders_made(self.state), ())

    def test_an_entry_that_is_not_well_formed_is_ignored(self) -> None:
        (self.state).mkdir()
        record = (
            '{"folders": ["/Users/me/Development/exporter.worktrees", "exporter.worktrees", 3, null, ["/x"]]}'
        )
        (self.state / agent_changes.FILE).write_text(record)
        self.assertEqual(
            agent_changes.folders_made(self.state), ("/Users/me/Development/exporter.worktrees",)
        )
        for record in ('{"folders": "/Users/me/x.worktrees"}', '{"folders": {}}', "[1]", "not json"):
            (self.state / agent_changes.FILE).write_text(record)
            self.assertEqual(agent_changes.folders_made(self.state), (), record)

    def test_a_folder_record_does_not_disturb_the_rest(self) -> None:
        agent_changes.record_trust(self.state, "claude", str(self.base))
        agent_changes.record_setup(self.state, "stub")
        agent_changes.record_folder(self.state, str(self.folder))
        found = agent_changes.read(self.state)
        self.assertEqual(found[agent_changes.TRUST], [["claude", str(self.base)]])
        self.assertEqual(found[agent_changes.SETUPS], ["stub"])

    def test_windows_starting_together_each_keep_their_folder(self) -> None:
        folders = [str(self.base / f"repo-{number}.worktrees") for number in range(16)]
        threads = [
            threading.Thread(target=agent_changes.record_folder, args=(self.state, folder))
            for folder in folders
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(agent_changes.folders_made(self.state)), sorted(folders))
        self.assertEqual(sorted(os.listdir(self.state)), sorted([agent_changes.FILE, agent_changes.LOCK]))


if __name__ == "__main__":
    unittest.main()
