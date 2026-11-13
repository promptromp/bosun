"""lib/quit_live.py: the world `bosun quit` touches (launchctl and the console job, the windows lock, bosun
stop, the command's own refusals). The plan and the run over the ports are tests/unit/test_quit.py. Nothing
here reaches a real launchd, a real bosun or this Mac's state: subprocesses are stand-in scripts in a temp
folder, and the lock tests run the process that takes it as a child."""

from __future__ import annotations

import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_live
import console_token
import quit as quit_core
import quit_live
import run_locked


class CommandTest(unittest.TestCase):
    def test_the_wrappers_own_refusal_is_the_same_object_and_exit_1(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            status = quit_live.main(["--json", "--error", "BOSUN_QUIT_WAIT_MINUTES must be a number"])
        data = json.loads(out.getvalue())
        self.assertEqual((status, data["result"], data["windows"]), (1, "failed", []))
        self.assertIn("BOSUN_QUIT_WAIT_MINUTES", data["error"])


class LiveTest(unittest.TestCase):
    """The helpers that touch the world: launchctl, the console job, the windows lock, bosun stop."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.marker = self.state / "console" / "wanted"
        self.marker.parent.mkdir()
        self.marker.touch()
        self.world = quit_live.World(self.state, "acme", "com.acme.console", self.marker, 0.2)

    def stop(self, running: list[bool], launchctl: Any) -> tuple[str, int | None]:
        with (
            mock.patch.object(quit_live, "launchctl", launchctl),
            mock.patch.object(console_token, "running", side_effect=running + [running[-1]] * 20),
            mock.patch.object(time, "sleep"),
            mock.patch.object(time, "monotonic", side_effect=itertools.count(0.0, 1.0)),
        ):
            return quit_live.stop_console(self.world)

    def test_launchctl_missing_is_a_no_not_a_crash(self) -> None:
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("launchctl")):
            self.assertFalse(quit_live.launchctl("print", "gui/501/com.acme.console"))

    def test_launchctl_that_times_out_is_a_no(self) -> None:
        timeout = subprocess.TimeoutExpired("launchctl", 20)
        with mock.patch.object(subprocess, "run", side_effect=timeout):
            self.assertFalse(quit_live.launchctl("kill", "TERM", "x"))

    def test_a_job_that_is_not_loaded_is_not_told_anything_and_is_no_failure(self) -> None:
        calls: list[tuple[str, ...]] = []

        def launchctl(*args: str) -> bool:
            calls.append(args)
            return False

        self.assertEqual(self.stop([False], launchctl), (quit_core.CONSOLE_NONE, None))
        self.assertFalse(self.marker.exists())  # the marker goes whatever launchctl says
        self.assertEqual([c[0] for c in calls], ["print"])

    def test_a_loaded_job_is_told_to_stop_and_the_console_is_gone(self) -> None:
        calls: list[tuple[str, ...]] = []

        def launchctl(*args: str) -> bool:
            calls.append(args)
            return True

        self.assertEqual(self.stop([True, True, False], launchctl), (quit_core.CONSOLE_STOPPED, None))
        self.assertEqual(calls[1][:2], ("kill", "TERM"))
        self.assertTrue(calls[1][2].endswith("/com.acme.console"))

    def test_print_ok_but_kill_failing_is_no_failure_and_a_console_still_running_says_so(self) -> None:
        launchctl = mock.Mock(side_effect=[True, False])  # print ok, kill fails
        with mock.patch.object(console_token, "served_port", return_value=8765):
            said = self.stop([True], launchctl)
        self.assertEqual(said, ("still running", 8765))

    def test_a_marker_that_cannot_be_removed_is_a_quit_error_not_a_traceback(self) -> None:
        with (
            mock.patch.object(Path, "unlink", side_effect=PermissionError("denied")),
            self.assertRaisesRegex(quit_core.QuitError, "could not remove .*wanted"),
        ):
            quit_live.stop_console(self.world)

    def hold(self) -> subprocess.Popen[str]:
        """A process holding the windows lock until it is told to stop."""
        code = (
            "import os, sys, time\n"
            f"sys.path.insert(0, {str(Path(quit_live.__file__).parent)!r})\n"
            "import run_locked\n"
            f"fd = run_locked.acquire({str(self.state / 'windows.lock')!r}, 5)\n"
            "print('held', flush=True)\n"
            "sys.stdin.readline()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert proc.stdout is not None
        self.assertEqual(proc.stdout.readline().strip(), "held")
        return proc

    def release(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdin is not None
        proc.stdin.write("\n")
        proc.stdin.close()
        proc.wait(timeout=10)
        if proc.stdout:
            proc.stdout.close()

    def test_the_windows_lock_waits_for_a_start_and_gives_up_with_the_busy_message(self) -> None:
        proc = self.hold()
        self.addCleanup(self.release, proc)
        with (
            self.assertRaisesRegex(quit_core.QuitError, "setting up the windows"),
            quit_live.windows_lock(self.world),
        ):
            self.fail("the lock was taken while another process held it")

    def test_what_the_lock_starts_finds_it_held_and_it_is_free_afterwards(self) -> None:
        check = self.state / "bosun"
        held = f"{Path(run_locked.__file__)} --held {self.state / 'windows.lock'}"
        check.write_text(f"#!/bin/sh\nexec {sys.executable} -I {held}\n")
        check.chmod(0o755)
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(Path(quit_live.__file__).parent)!r})\n"
            "import quit_live as q\n"
            "from pathlib import Path\n"
            "import console_live\n"
            f"console_live.BOSUN = Path({str(check)!r})\n"
            f"world = q.World(Path({str(self.state)!r}), 's', 'l', Path('m'), 1)\n"
            "with q.windows_lock(world):\n"
            "    q.stop_bosun(True)\n"  # raises QuitError (exit 1) unless the child saw the lock held
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, check=False
        )
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "ok"), result.stderr)
        fd = run_locked.acquire(str(self.state / "windows.lock"), 0)
        self.assertIsNotNone(fd)  # free once the quit's steps are done
        if fd is not None:
            os.close(fd)

    def test_bosun_stop_failing_is_the_last_line_of_what_it_said(self) -> None:
        fake = self.state / "bosun"
        fake.write_text("#!/bin/sh\necho first >&2\necho tmux could not kill the session >&2\nexit 1\n")
        fake.chmod(0o755)
        with (
            mock.patch.object(console_live, "BOSUN", fake),
            self.assertRaisesRegex(quit_core.QuitError, "bosun stop failed: tmux could not kill the session"),
        ):
            quit_live.stop_bosun(True)

    def test_a_lock_that_cannot_be_opened_is_a_quit_error_not_a_traceback(self) -> None:
        blocked = self.state / "afile"
        blocked.write_text("not a folder")
        world = quit_live.World(blocked / "state", "acme", "com.acme.console", self.marker, 0.2)
        with (
            self.assertRaisesRegex(
                quit_core.QuitError, "could not take the windows lock .*nothing was changed"
            ),
            quit_live.windows_lock(world),
        ):
            self.fail("the lock was taken in a folder that cannot exist")

    def test_a_console_still_running_with_no_recorded_port_has_none(self) -> None:
        launchctl = mock.Mock(side_effect=[True, False])
        with mock.patch.object(console_token, "served_port", return_value=None):
            self.assertEqual(self.stop([True], launchctl), ("still running", None))


if __name__ == "__main__":
    unittest.main()
