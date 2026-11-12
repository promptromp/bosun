"""lib/console_token.py: the console's access token, kept across restarts."""

from __future__ import annotations

import argparse
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


class RunningTest(unittest.TestCase):
    def test_a_console_is_running_while_it_serves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            self.assertFalse(console_token.running(state))
            with console_token.serving(state), console_token.serving(state):  # two consoles
                self.assertTrue(console_token.running(state))
            self.assertFalse(console_token.running(state))
            self.assertEqual(mode(state / console_token.TOKEN_DIR), console_token.DIR_MODE)

    def test_the_port_is_kept_while_it_serves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            self.assertIsNone(console_token.served_port(state))
            with console_token.serving(state, 8123):
                self.assertEqual(console_token.served_port(state), 8123)
            with console_token.serving(state, 9):  # a shorter one replaces it whole
                self.assertEqual(console_token.served_port(state), 9)

    def test_a_console_that_kept_no_port_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            with console_token.serving(state):
                self.assertIsNone(console_token.served_port(state))
            (state / console_token.TOKEN_DIR / console_token.RUNNING_LOCK).write_text("not a port\n")
            self.assertIsNone(console_token.served_port(state))
            (state / console_token.TOKEN_DIR / console_token.RUNNING_LOCK).write_text("99999\n")
            self.assertIsNone(console_token.served_port(state))


class ServedUrlTest(unittest.TestCase):
    """console_server.served_url and serving_url: the address of a console that runs, for `--url` and a
    hand run."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.token = token_for(self.state, persist=True)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("BOSUN_TRIGGER", None)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def args(self, *argv: str) -> argparse.Namespace:
        return console_server.build_parser().parse_args(list(argv))

    def test_not_running_is_an_error(self) -> None:
        with self.assertRaisesRegex(console_server.ConsoleError, "the console is not running"):
            console_server.served_url(self.state, 8765)

    def test_the_address_is_the_recorded_port_and_the_stored_token(self) -> None:
        with console_token.serving(self.state, 8123):
            self.assertEqual(
                console_server.served_url(self.state, 8765), f"http://127.0.0.1:8123/?t={self.token}"
            )

    def test_a_console_from_before_it_recorded_its_port_has_the_configured_one(self) -> None:
        with console_token.serving(self.state):
            self.assertEqual(
                console_server.served_url(self.state, 8765), f"http://127.0.0.1:8765/?t={self.token}"
            )
            with self.assertRaisesRegex(console_server.ConsoleError, "address is not kept"):
                console_server.served_url(self.state, 0)

    def test_no_stored_token_means_no_address(self) -> None:
        console_token.token_path(self.state).unlink()
        with (
            console_token.serving(self.state, 8123),
            self.assertRaisesRegex(console_server.ConsoleError, "not kept"),
        ):
            console_server.served_url(self.state, 8765)

    def test_a_hand_run_takes_the_running_console(self) -> None:
        args = self.args()
        self.assertIsNone(console_server.serving_url(self.state, args))
        with console_token.serving(self.state, 8123):
            self.assertEqual(
                console_server.serving_url(self.state, args), f"http://127.0.0.1:8123/?t={self.token}"
            )

    def test_the_job_always_starts(self) -> None:
        with (
            console_token.serving(self.state, 8123),
            mock.patch.dict(os.environ, {"BOSUN_TRIGGER": "launchd"}),
        ):
            self.assertIsNone(console_server.serving_url(self.state, self.args()))

    def test_a_new_token_cannot_reach_a_running_console(self) -> None:
        with (
            console_token.serving(self.state, 8123),
            self.assertRaisesRegex(console_server.ConsoleError, "already running"),
        ):
            console_server.serving_url(self.state, self.args("--new-token"))

    def test_a_running_console_with_no_address_leaves_a_hand_run_to_start_one(self) -> None:
        with console_token.serving(self.state):
            self.assertIsNone(console_server.serving_url(self.state, self.args("--port", "0")))

    def test_a_hand_run_with_another_address_is_refused_even_while_one_runs(self) -> None:
        with (
            console_token.serving(self.state, 8123),
            mock.patch.dict(os.environ, {"BOSUN_STATE_DIR": str(self.state)}),
            mock.patch("sys.stderr"),
        ):
            self.assertEqual(console_server.main(["--host", "0.0.0.0"]), 1)  # noqa: S104 - the address it must refuse

    def test_url_prints_the_address(self) -> None:
        with (
            console_token.serving(self.state, 8123),
            mock.patch.dict(os.environ, {"BOSUN_STATE_DIR": str(self.state)}),
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(console_server.main(["--url"]), 0)
        printed.assert_called_once_with(f"http://127.0.0.1:8123/?t={self.token}")

    def test_url_with_none_running_exits_1(self) -> None:
        with (
            mock.patch.dict(os.environ, {"BOSUN_STATE_DIR": str(self.state)}),
            mock.patch("sys.stderr") as err,
        ):
            self.assertEqual(console_server.main(["--url"]), 1)
        self.assertIn("the console is not running", "".join(c.args[0] for c in err.write.call_args_list))

    def test_parser_takes_url(self) -> None:
        self.assertTrue(console_server.build_parser().parse_args(["--url"]).url)


if __name__ == "__main__":
    unittest.main()
