"""lib/console_token.py: the console's access token, kept across restarts."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_server
import console_token
from console_token import console_token as token_for


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class ConsoleTokenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.path = console_token.token_path(self.state)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reused_across_starts(self) -> None:
        first = token_for(self.state, persist=True)
        self.assertEqual(token_for(self.state, persist=True), first)

    def test_stored_private(self) -> None:
        token_for(self.state, persist=True)
        self.assertEqual(mode(self.path), console_token.FILE_MODE)
        self.assertEqual(mode(self.path.parent), console_token.DIR_MODE)

    def test_rotate_replaces_and_keeps_the_new_one(self) -> None:
        first = token_for(self.state, persist=True)
        second = token_for(self.state, persist=True, rotate=True)
        self.assertNotEqual(second, first)
        self.assertEqual(token_for(self.state, persist=True), second)

    def test_readable_by_others_is_replaced_not_reused(self) -> None:
        first = token_for(self.state, persist=True)
        os.chmod(self.path, 0o644)
        second = token_for(self.state, persist=True)
        self.assertNotEqual(second, first)
        self.assertEqual(mode(self.path), console_token.FILE_MODE)

    def test_loose_folder_is_tightened(self) -> None:
        token_for(self.state, persist=True)
        os.chmod(self.path.parent, 0o755)  # noqa: S103 - the loose folder under test
        token_for(self.state, persist=True, rotate=True)
        self.assertEqual(mode(self.path.parent), console_token.DIR_MODE)

    def test_corrupt_file_is_replaced(self) -> None:
        console_token.write_token(self.path, "short")
        token = token_for(self.state, persist=True)
        self.assertRegex(token, console_token.TOKEN_PATTERN)
        self.assertEqual(self.path.read_text().strip(), token)

    def test_unreadable_bytes_are_replaced(self) -> None:
        console_token.private_dir(self.path.parent)
        self.path.write_bytes(b"\xff\xfe")
        os.chmod(self.path, console_token.FILE_MODE)
        self.assertRegex(token_for(self.state, persist=True), console_token.TOKEN_PATTERN)

    def test_persistence_off_is_new_each_start_and_writes_nothing(self) -> None:
        first = token_for(self.state, persist=False)
        self.assertNotEqual(token_for(self.state, persist=False), first)
        self.assertFalse(self.path.exists())

    def test_persistence_off_leaves_a_stored_token_alone(self) -> None:
        stored = token_for(self.state, persist=True)
        token_for(self.state, persist=False)
        self.assertEqual(self.path.read_text().strip(), stored)

    def test_no_temporary_file_left(self) -> None:
        token_for(self.state, persist=True)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [console_token.TOKEN_FILE])

    def test_persist_setting(self) -> None:
        self.assertTrue(console_token.persist_setting(None))
        self.assertTrue(console_token.persist_setting("1"))
        self.assertFalse(console_token.persist_setting("0"))
        self.assertFalse(console_token.persist_setting(" 0 "))


class ConsoleStartTest(unittest.TestCase):
    """console_server.new_app: what a start of `bosun console` uses."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        env = {"BOSUN_CONFIG_DIR": str(self.state / "config")}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_two_starts_share_a_token(self) -> None:
        first = console_server.new_app(self.state).token
        self.assertEqual(console_server.new_app(self.state).token, first)

    def test_new_token_rotates(self) -> None:
        first = console_server.new_app(self.state).token
        self.assertNotEqual(console_server.new_app(self.state, rotate=True).token, first)

    def test_setting_off_mints_per_start(self) -> None:
        with mock.patch.dict(os.environ, {console_server.PERSIST_SETTING: "0"}):
            first = console_server.new_app(self.state).token
            self.assertNotEqual(console_server.new_app(self.state).token, first)

    def test_parser_takes_new_token(self) -> None:
        self.assertTrue(console_server.build_parser().parse_args(["--new-token"]).new_token)


if __name__ == "__main__":
    unittest.main()
