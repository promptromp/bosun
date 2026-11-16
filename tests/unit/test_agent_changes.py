"""What bosun changed in the agents' own files, on record so that uninstall undoes exactly that."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_changes
import run_locked


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
        self.home = self.base / "home"
        # bosun's own folders, as a launch names them: none of them the real ones
        self.env = {
            "BOSUN_CONFIG_DIR": str(self.base / "config"),
            "BOSUN_LOG_DIR": str(self.base / "logs"),
            "BOSUN_INSTALL": str(self.base / "install"),
        }
        for patcher in (
            mock.patch.dict(os.environ, self.env),
            mock.patch.object(Path, "home", return_value=self.home),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

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
        self.assertEqual(status, 1)
        self.assertEqual(
            said.strip(), f"{self.folder} is there, but is not a folder, so the window starts without it"
        )
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

    def refused(self) -> dict[str, str]:
        """Each path no record may hold, and why."""
        development = self.base / "Development"
        return {
            "exporter.worktrees": "not a full path",
            f"{self.folder}/": "a trailing slash",
            f"{development}/x/../exporter.worktrees": "not written one way only",
            f"{development}/exporter": "not named <something>.worktrees",
            f"{development}/.worktrees": "named .worktrees alone",
            "/": "the root folder",
            str(self.state / "a.worktrees"): "in the state folder",
            str(self.base / "config" / "a.worktrees"): "in the config folder",
            str(self.base / "logs" / "a.worktrees"): "in the log folder",
            str(self.base / "install" / "a.worktrees"): "in the install",
            str(self.base / "install-versions" / "a.worktrees"): "in the versions folder",
            str(agent_changes.ROOT / "a.worktrees"): "in this bosun's own folder",
            str(self.base / "alias" / "a.worktrees"): "in the state folder, by another spelling",
        }

    def test_a_path_that_may_not_be_a_worktrees_folder_is_no_entry(self) -> None:
        self.state.mkdir()
        (self.base / "alias").symlink_to(self.state)
        refused = self.refused()
        record = {"folders": [*refused, str(self.folder)], "trust": [], "setups": []}
        (self.state / agent_changes.FILE).write_text(json.dumps(record))
        self.assertEqual(agent_changes.folders_made(self.state), (str(self.folder),))
        for path, why in refused.items():
            self.assertFalse(agent_changes.record_folder(self.state, path), why)
        self.assertEqual(agent_changes.folders_made(self.state), (str(self.folder),))

    def test_nor_the_home_folder_or_a_bosun_folder_itself(self) -> None:
        for name in ("home", "BOSUN_CONFIG_DIR"):
            folder = self.base / "me.worktrees"
            with (
                mock.patch.object(Path, "home", return_value=folder if name == "home" else self.home),
                mock.patch.dict(os.environ, {name: str(folder)} if name != "home" else {}),
            ):
                self.assertFalse(agent_changes.folder_allowed(str(folder), self.state), name)
                self.assertTrue(agent_changes.folder_allowed(str(self.folder), self.state), name)

    def test_make_folder_makes_none_of_them(self) -> None:
        for path in (str(self.state / "a.worktrees"), str(self.base / "config" / "a.worktrees")):
            Path(path).parent.mkdir(exist_ok=True)
            status, said = self.make("make-folder", str(self.state), path)
            self.assertEqual(status, 1, path)
            self.assertIn("is not a folder bosun makes for worktrees", said)
            self.assertFalse(os.path.exists(path), path)

    def test_a_record_held_too_long_is_given_up_with_a_line(self) -> None:
        held = run_locked.acquire(str(self.state / agent_changes.LOCK), 0)
        assert held is not None
        errors = io.StringIO()
        try:
            with mock.patch.object(agent_changes, "LOCK_WAIT_SECONDS", 0.1):
                with contextlib.redirect_stderr(errors):
                    self.assertFalse(agent_changes.record_folder(self.state, str(self.folder)))
                    agent_changes.record_trust(self.state, "claude", str(self.base))  # no traceback either
                status, said = self.make("make-folder", str(self.state), str(self.folder))
        finally:
            os.close(held)
        self.assertIn(f"so {self.folder} is not on record for bosun uninstall", errors.getvalue())
        self.assertEqual(len(errors.getvalue().splitlines()), 2)
        self.assertEqual(status, 1)
        self.assertTrue(self.folder.is_dir())  # the window gets its folder, only not on record
        self.assertEqual(len(said.splitlines()), 1)
        self.assertEqual(agent_changes.folders_made(self.state), ())
        self.assertEqual(agent_changes.trust_given(self.state), ())

    def test_keys_it_does_not_know_are_written_back_as_they_were(self) -> None:
        self.state.mkdir()
        newer = {"links": [["a", {"b": 1}]], "version": 3}
        (self.state / agent_changes.FILE).write_text(json.dumps({**newer, "setups": ["stub"]}))
        agent_changes.record_folder(self.state, str(self.folder))
        agent_changes.forget_setup(self.state, "stub")
        written = json.loads((self.state / agent_changes.FILE).read_text())
        self.assertEqual({key: written[key] for key in newer}, newer)
        self.assertEqual(written["folders"], [str(self.folder)])
        self.assertEqual(written["setups"], [])


if __name__ == "__main__":
    unittest.main()
