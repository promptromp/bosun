"""lib/run_locked.py: one at a time, for bosun's shell commands; a nested command goes on, a second one
waits then gives up, and a holder that dies leaves no lock behind."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import run_locked


RUN_LOCKED = str(Path(__file__).resolve().parents[2] / "lib" / "run_locked.py")


def taken(path: Path) -> bool:
    """Whether another process holds <path>'s lock now."""
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


def wait_until(condition: object, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():  # type: ignore[operator]
            return True
        time.sleep(0.05)
    return False


class RunLockedTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.lock = Path(folder.name) / "windows.lock"

    def run_locked(self, wait: str, *command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, RUN_LOCKED, str(self.lock), wait, "busy setting up", "--", *command],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def hold(self, seconds: int) -> subprocess.Popen[bytes]:
        holder = subprocess.Popen(
            [sys.executable, RUN_LOCKED, str(self.lock), "0", "busy", "--", "sleep", str(seconds)]
        )
        self.addCleanup(holder.kill)
        self.assertTrue(wait_until(lambda: taken(self.lock)), "the holder never took the lock")
        return holder

    def test_not_held_without_its_descriptor(self) -> None:
        self.lock.touch()
        held = subprocess.run([sys.executable, RUN_LOCKED, "--held", str(self.lock)], check=False)
        self.assertEqual(held.returncode, 1)

    def test_the_command_holds_it_and_a_command_it_starts_goes_on(self) -> None:
        nested = f"{sys.executable} {RUN_LOCKED} --held {self.lock} && echo nested-goes-on"
        result = self.run_locked("0", "bash", "-c", nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "nested-goes-on")

    def test_a_second_one_waits_then_gives_up_saying_why(self) -> None:
        self.hold(5)
        started = time.monotonic()
        result = self.run_locked("1", "true")
        self.assertEqual(result.returncode, run_locked.BUSY)
        self.assertEqual(result.stderr.strip(), "bosun: busy setting up")
        self.assertGreaterEqual(time.monotonic() - started, 1.0)

    def test_it_goes_on_once_the_holder_is_done(self) -> None:
        holder = self.hold(1)
        result = self.run_locked("10", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(holder.poll())

    def test_a_holder_killed_leaves_no_lock(self) -> None:
        holder = self.hold(30)
        os.kill(holder.pid, signal.SIGKILL)
        holder.wait(timeout=10)
        result = self.run_locked("0", "true")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_it_makes_the_lock_file_s_folder(self) -> None:
        self.lock = self.lock.parent / "not-yet" / "windows.lock"
        result = self.run_locked("0", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.lock.exists())

    def test_bad_usage(self) -> None:
        self.assertEqual(run_locked.main([str(self.lock), "1", "message", "true"]), run_locked.USAGE)


if __name__ == "__main__":
    unittest.main()
