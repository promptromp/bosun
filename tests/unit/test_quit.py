"""lib/quit.py: what `bosun quit` plans and does (design: docs/design/menu-bar.md, "Quit"). The plan is pure
over the windows; the run goes through Ports, which these tests replace with recorded fakes (a clock that
only the sleeps move, the steps written down in the order they happen). Fictional data: acme's windows."""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_live
import console_token
import quit as quit_cmd
import run_locked


def window(name: str, status: str | None = "idle") -> quit_cmd.Window:
    return quit_cmd.Window(name, status)


def listing(name: str, status: str | None, exists: bool = True) -> dict[str, Any]:
    """A window as `bosun sessions --json` lists it."""
    agent = {"pid": 4101, "status": status} if status else None
    return {"name": name, "kind": "session", "window": exists, "agent": agent}


class PlanTest(unittest.TestCase):
    def test_idle_busy_waiting_and_shell_windows_are_told_apart(self) -> None:
        plan = quit_cmd.quit_plan(
            [window("orch"), window("alpha", "busy"), window("beta", "waiting"), window("gamma", "shell")],
            None,
            False,
        )
        self.assertEqual(
            plan,
            {
                "contract": 1,
                "windows": ["orch", "alpha", "beta", "gamma"],
                "busy": ["alpha"],
                "waiting": ["beta"],
                "keep_awake_until": None,
                "refused": None,
            },
        )

    def test_a_window_with_no_agent_is_open_and_neither_busy_nor_waiting(self) -> None:
        plan = quit_cmd.quit_plan([window("alpha", None)], "08:30", False)
        self.assertEqual((plan["windows"], plan["busy"], plan["waiting"]), (["alpha"], [], []))
        self.assertEqual(plan["keep_awake_until"], "08:30")

    def test_an_update_refuses_the_quit_and_says_why(self) -> None:
        plan = quit_cmd.quit_plan([window("alpha", "busy")], None, True)
        self.assertIn("update", str(plan["refused"]))
        self.assertEqual(plan["busy"], ["alpha"])  # still said: what the person would be told about

    def test_no_update_is_no_refusal(self) -> None:
        self.assertIsNone(quit_cmd.quit_plan([], None, False)["refused"])

    def test_the_windows_are_the_open_ones_of_the_listing(self) -> None:
        found = quit_cmd.windows_from_sessions(
            [
                listing("orch", "idle"),
                listing("alpha", "busy"),
                listing("ghost", None, exists=False),
                listing("beta", None),
            ]
        )
        self.assertEqual(found, [window("orch"), window("alpha", "busy"), window("beta", None)])


class Fake:
    """The ports of one run: <states> are the windows each reading of the listing gives (the last one
    stays); every step is written to <steps> as it happens."""

    def __init__(
        self, *states: list[quit_cmd.Window], updating: bool = False, stop_fails: str | None = None
    ) -> None:
        self.states = list(states) or [[]]
        self.reads = 0
        self.updating = updating
        self.stop_fails = stop_fails
        self.keep_awake: str | None = None  # when the keep-awake ends
        self.console = "stopped"
        self.steps: list[str] = []
        self.now = 1000.0
        self.slept: list[float] = []
        self.lock_busy = False
        self.locked = False
        self.outside: list[str] = []  # steps that ran without the windows lock
        self.read_locked: list[bool] = []  # whether each reading of the windows held the lock

    def read_windows(self) -> list[quit_cmd.Window]:
        state = self.states[min(self.reads, len(self.states) - 1)]
        self.reads += 1
        self.read_locked.append(self.locked)
        return state

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        if self.lock_busy:
            raise quit_cmd.QuitError(quit_cmd.LOCK_BUSY)
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def step(self, text: str) -> None:
        self.steps.append(text)
        if not self.locked:
            self.outside.append(text)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def write_quit(self, paused: bool) -> None:
        self.step(f"quit.json paused={paused}")

    def stop_console(self) -> str:
        self.step("console")
        return self.console

    def stop_bosun(self, keep_awake: bool) -> None:
        self.step(f"stop keep_awake={keep_awake}")
        if self.stop_fails:
            raise quit_cmd.QuitError(self.stop_fails)

    def ports(self) -> quit_cmd.Ports:
        return quit_cmd.Ports(
            read_windows=self.read_windows,
            updating=lambda: self.updating,
            keep_awake_until=lambda: self.keep_awake,
            lock=self.lock,
            write_quit=self.write_quit,
            stop_console=self.stop_console,
            stop_bosun=self.stop_bosun,
            sleep=self.sleep,
            clock=lambda: self.now,
        )


def run(
    fake: Fake,
    mode: str,
    minutes: int = 10,
    pause: bool = False,
    sleep: bool = False,
    as_json: bool = False,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    options = quit_cmd.Options(mode, minutes, pause, sleep, as_json)
    status = quit_cmd.run(options, fake.ports(), out, err)
    return status, out.getvalue(), err.getvalue()


class RunTest(unittest.TestCase):
    def test_quit_refuses_while_an_update_runs(self) -> None:
        fake = Fake([window("alpha")], updating=True)
        status, out, _err = run(fake, "now", as_json=True)
        self.assertEqual(status, 1)
        data = json.loads(out)
        self.assertEqual((data["contract"], data["result"]), (1, "refused"))
        self.assertIn("update", data["refused"])
        self.assertEqual(fake.steps, [])  # nothing written, nothing stopped

    def test_the_refusal_is_one_line_in_text(self) -> None:
        status, out, err = run(Fake([], updating=True), "finish")
        self.assertEqual(status, 1)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("update", err)
        self.assertEqual(out, "")

    def test_an_update_that_starts_during_the_wait_refuses_it_too(self) -> None:
        fake = Fake([window("alpha", "busy")])
        reads = fake.read_windows

        def flip() -> list[quit_cmd.Window]:
            fake.updating = fake.reads >= 1  # the update starts after the first reading
            return reads()

        fake.read_windows = flip  # type: ignore[method-assign]
        status, _out, _err = run(fake, "finish")
        self.assertEqual(status, 1)
        self.assertEqual(fake.steps, [])

    def test_stop_now_writes_the_quit_first_then_the_console_then_stops_keeping_the_mac_awake(self) -> None:
        fake = Fake([window("alpha", "busy")])
        status, out, _err = run(fake, "now", pause=True)
        self.assertEqual(status, 0)
        self.assertEqual(fake.steps, ["quit.json paused=True", "console", "stop keep_awake=True"])
        self.assertIn("alpha", out)

    def test_sleep_releases_the_keep_awake_and_without_pause_the_morning_stays_on(self) -> None:
        fake = Fake([window("alpha")])
        status, _out, _err = run(fake, "now", sleep=True)
        self.assertEqual(status, 0)
        self.assertEqual(fake.steps, ["quit.json paused=False", "console", "stop keep_awake=False"])

    def test_the_json_says_what_was_stopped_and_what_was_kept(self) -> None:
        fake = Fake([window("alpha", "busy"), window("beta", "waiting")])
        fake.keep_awake = "08:30"
        _status, out, _err = run(fake, "now", pause=True, as_json=True)
        data = json.loads(out)
        self.assertEqual(data["contract"], 1)
        self.assertEqual(data["result"], "stopped")
        self.assertEqual((data["windows"], data["waiting"]), (["alpha", "beta"], ["beta"]))
        self.assertEqual((data["morning_paused"], data["console"]), (True, "stopped"))
        self.assertEqual(data["keep_awake"], "kept until 08:30")

    def test_with_sleep_the_keep_awake_is_released(self) -> None:
        fake = Fake([window("alpha")])
        fake.keep_awake = "08:30"
        _status, out, _err = run(fake, "now", sleep=True, as_json=True)
        self.assertEqual(json.loads(out)["keep_awake"], "released")

    def test_a_mac_with_no_keep_awake_says_none(self) -> None:
        _status, out, _err = run(Fake([window("alpha")]), "now", as_json=True)
        self.assertIsNone(json.loads(out)["keep_awake"])

    def test_let_finish_stops_when_nothing_is_busy_without_waiting(self) -> None:
        fake = Fake([window("alpha"), window("gamma", "shell"), window("beta", "waiting")])
        status, out, _err = run(fake, "finish")
        self.assertEqual(status, 0)
        self.assertEqual(fake.slept, [])  # waiting windows do not hold the wait, a shell is done
        self.assertEqual(fake.steps[-1], "stop keep_awake=True")
        self.assertIn("beta", out)  # said: it waits for you and was stopped with the rest

    def test_let_finish_polls_every_five_seconds_until_nothing_is_busy(self) -> None:
        fake = Fake([window("alpha", "busy")], [window("alpha", "busy")], [window("alpha")])
        status, _out, _err = run(fake, "finish", minutes=10)
        self.assertEqual(status, 0)
        self.assertEqual(fake.slept, [5, 5])
        self.assertEqual(fake.steps[0], "quit.json paused=False")  # written once it is done, not before

    def test_the_wait_running_out_exits_3_naming_the_busy_windows_and_changes_nothing(self) -> None:
        fake = Fake([window("alpha", "busy"), window("beta")])
        status, out, _err = run(fake, "finish", minutes=1)
        self.assertEqual(status, 3)
        self.assertEqual(fake.steps, [])
        self.assertEqual(sum(fake.slept), 60)
        self.assertIn("alpha", out)
        self.assertNotIn("beta", out)

    def test_the_wait_running_out_is_an_object_that_says_so_in_json(self) -> None:
        status, out, _err = run(Fake([window("alpha", "busy")]), "finish", minutes=0, as_json=True)
        data = json.loads(out)
        self.assertEqual(status, 3)
        self.assertEqual((data["contract"], data["result"], data["busy"]), (1, "busy", ["alpha"]))

    def test_zero_minutes_checks_once(self) -> None:
        fake = Fake([window("alpha", "busy")])
        status, _out, _err = run(fake, "finish", minutes=0)
        self.assertEqual((status, fake.reads, fake.slept), (3, 1, []))

    def test_when_bosun_stop_fails_the_quit_stays_and_the_reason_is_said(self) -> None:
        fake = Fake([window("alpha")], stop_fails="tmux could not kill the session")
        status, out, _err = run(fake, "now", as_json=True)
        data = json.loads(out)
        self.assertEqual(status, 1)
        self.assertEqual((data["contract"], data["result"]), (1, "failed"))
        self.assertIn("tmux could not kill", data["error"])
        self.assertEqual(fake.steps[0], "quit.json paused=False")  # written first, and left

    def test_the_plan_prints_the_object_and_changes_nothing(self) -> None:
        fake = Fake([window("alpha", "busy")])
        fake.keep_awake = "09:00"
        status, out, _err = run(fake, "plan", as_json=True)
        data = json.loads(out)
        self.assertEqual(status, 0)
        self.assertEqual(
            sorted(data), ["busy", "contract", "keep_awake_until", "refused", "waiting", "windows"]
        )
        self.assertEqual(data["keep_awake_until"], "09:00")
        self.assertEqual(fake.steps, [])

    def test_the_plan_in_text_names_the_windows_that_are_busy_or_waiting(self) -> None:
        _status, out, _err = run(Fake([window("alpha", "busy"), window("beta", "waiting")]), "plan")
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_the_plan_with_an_update_running_says_so_and_still_exits_0(self) -> None:
        status, out, _err = run(Fake([window("alpha")], updating=True), "plan", as_json=True)
        self.assertEqual(status, 0)  # the app shows the reason; only a quit is refused
        self.assertIn("update", json.loads(out)["refused"])

    def test_stop_now_with_the_windows_unreadable_still_stops(self) -> None:
        fake = Fake()

        def broken() -> list[quit_cmd.Window]:
            raise quit_cmd.QuitError("sessions --json exited 1")

        fake.read_windows = broken  # type: ignore[method-assign]
        status, out, _err = run(fake, "now", as_json=True)
        self.assertEqual(status, 0)
        self.assertEqual(fake.steps[-1], "stop keep_awake=True")
        self.assertIn("could not be read", json.loads(out)["note"])

    def test_let_finish_with_the_windows_unreadable_fails_instead_of_guessing(self) -> None:
        fake = Fake()

        def broken() -> list[quit_cmd.Window]:
            raise quit_cmd.QuitError("sessions --json exited 1")

        fake.read_windows = broken  # type: ignore[method-assign]
        status, _out, err = run(fake, "finish")
        self.assertEqual(status, 1)
        self.assertIn("sessions --json exited 1", err)
        self.assertEqual(fake.steps, [])

    def test_the_quit_steps_run_under_the_windows_lock_and_the_wait_does_not(self) -> None:
        fake = Fake([window("alpha", "busy")], [window("alpha")])
        status, _out, _err = run(fake, "finish")
        self.assertEqual(status, 0)
        self.assertEqual(fake.outside, [])  # quit.json, the console and bosun stop all held it
        self.assertEqual(fake.read_locked, [False, False])  # the wait held none

    def test_a_start_that_holds_the_lock_makes_the_quit_fail_before_changing_anything(self) -> None:
        fake = Fake([window("alpha")])
        fake.lock_busy = True
        status, out, _err = run(fake, "now", as_json=True)
        data = json.loads(out)
        self.assertEqual((status, data["result"]), (1, "failed"))
        self.assertIn("setting up the windows", data["error"])
        self.assertNotIn("quit record stays", data["error"])  # nothing was written
        self.assertEqual(fake.steps, [])

    def test_the_lock_busy_message_is_one_line_in_text(self) -> None:
        fake = Fake([window("alpha")])
        fake.lock_busy = True
        status, out, err = run(fake, "now")
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_a_failed_stop_says_the_record_stays_and_both_ways_to_finish(self) -> None:
        fake = Fake([window("alpha")], stop_fails="bosun stop failed: tmux could not kill")
        for as_json in (False, True):
            _status, out, err = run(fake, "now", as_json=as_json)
            text = json.loads(out)["error"] if as_json else err
            self.assertIn("tmux could not kill", text)
            self.assertIn("quit record stays", text)
            self.assertIn("bosun quit --stop-now", text)
            self.assertIn("bosun start", text)

    def test_a_console_step_that_fails_after_the_record_says_the_same(self) -> None:
        fake = Fake([window("alpha")])

        def broken() -> str:
            raise quit_cmd.QuitError("could not remove the marker")

        fake.stop_console = broken  # type: ignore[method-assign]
        status, out, _err = run(fake, "now", as_json=True)
        data = json.loads(out)
        self.assertEqual((status, data["result"]), (1, "failed"))
        self.assertIn("could not remove the marker", data["error"])
        self.assertIn("bosun quit --stop-now", data["error"])
        self.assertEqual(fake.steps, ["quit.json paused=False"])  # bosun stop never ran

    def test_a_record_that_cannot_be_written_is_a_plain_failure_with_nothing_stopped(self) -> None:
        fake = Fake([window("alpha")])

        def broken(_paused: bool) -> None:
            raise quit_cmd.QuitError("could not write quit.json")

        fake.write_quit = broken  # type: ignore[method-assign,assignment]
        _status, out, _err = run(fake, "now", as_json=True)
        data = json.loads(out)
        self.assertEqual(data["result"], "failed")
        self.assertNotIn("quit record stays", data["error"])
        self.assertEqual(fake.steps, [])

    def test_a_console_still_running_is_said_neutrally_with_its_port(self) -> None:
        fake = Fake([window("alpha")])
        fake.console = "still running on port 8765"
        _status, out, _err = run(fake, "now")
        self.assertIn("still running on port 8765", out)
        self.assertNotIn("not the job", out)

    def test_the_wait_says_once_on_stderr_who_it_waits_for_and_how_long(self) -> None:
        fake = Fake([window("alpha", "busy"), window("beta", "busy")], [window("alpha")])
        _status, _out, err = run(fake, "finish", minutes=7)
        lines = err.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("2 busy windows", lines[0])
        self.assertIn("alpha, beta", lines[0])
        self.assertIn("7 minutes", lines[0])

    def test_no_wait_line_when_nothing_is_busy(self) -> None:
        _status, _out, err = run(Fake([window("alpha")]), "finish")
        self.assertEqual(err, "")

    def test_the_plan_failing_is_an_object_with_the_plans_keys_empty_and_the_error(self) -> None:
        fake = Fake()

        def broken() -> list[quit_cmd.Window]:
            raise quit_cmd.QuitError("the windows could not be read")

        fake.read_windows = broken  # type: ignore[method-assign]
        status, out, _err = run(fake, "plan", as_json=True)
        data = json.loads(out)
        self.assertEqual(status, 1)
        self.assertEqual(
            (data["contract"], data["windows"], data["busy"], data["waiting"], data["keep_awake_until"]),
            (1, [], [], [], None),
        )
        self.assertEqual((data["result"], data["refused"]), ("failed", None))
        self.assertIn("could not be read", data["error"])

    def test_the_wrappers_own_refusal_is_the_same_object_and_exit_1(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            status = quit_cmd.main(["--json", "--error", "BOSUN_QUIT_WAIT_MINUTES must be a number"])
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
        self.world = quit_cmd.World(self.state, "acme", "com.acme.console", self.marker, 0.2)

    def stop(self, running: list[bool], launchctl: Any) -> str:
        with (
            mock.patch.object(quit_cmd, "launchctl", launchctl),
            mock.patch.object(console_token, "running", side_effect=running + [running[-1]] * 20),
            mock.patch.object(time, "sleep"),
            mock.patch.object(time, "monotonic", side_effect=itertools.count(0.0, 1.0)),
        ):
            return quit_cmd.stop_console(self.world)

    def test_launchctl_missing_is_a_no_not_a_crash(self) -> None:
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("launchctl")):
            self.assertFalse(quit_cmd.launchctl("print", "gui/501/com.acme.console"))

    def test_launchctl_that_times_out_is_a_no(self) -> None:
        timeout = subprocess.TimeoutExpired("launchctl", 20)
        with mock.patch.object(subprocess, "run", side_effect=timeout):
            self.assertFalse(quit_cmd.launchctl("kill", "TERM", "x"))

    def test_a_job_that_is_not_loaded_is_not_told_anything_and_is_no_failure(self) -> None:
        calls: list[tuple[str, ...]] = []

        def launchctl(*args: str) -> bool:
            calls.append(args)
            return False

        self.assertEqual(self.stop([False], launchctl), quit_cmd.CONSOLE_NONE)
        self.assertFalse(self.marker.exists())  # the marker goes whatever launchctl says
        self.assertEqual([c[0] for c in calls], ["print"])

    def test_a_loaded_job_is_told_to_stop_and_the_console_is_gone(self) -> None:
        calls: list[tuple[str, ...]] = []

        def launchctl(*args: str) -> bool:
            calls.append(args)
            return True

        self.assertEqual(self.stop([True, True, False], launchctl), quit_cmd.CONSOLE_STOPPED)
        self.assertEqual(calls[1][:2], ("kill", "TERM"))
        self.assertTrue(calls[1][2].endswith("/com.acme.console"))

    def test_print_ok_but_kill_failing_is_no_failure_and_a_console_still_running_says_so(self) -> None:
        launchctl = mock.Mock(side_effect=[True, False])  # print ok, kill fails
        with mock.patch.object(console_token, "served_port", return_value=8765):
            said = self.stop([True], launchctl)
        self.assertEqual(said, "still running on port 8765")

    def test_a_marker_that_cannot_be_removed_is_a_quit_error_not_a_traceback(self) -> None:
        with (
            mock.patch.object(Path, "unlink", side_effect=PermissionError("denied")),
            self.assertRaisesRegex(quit_cmd.QuitError, "could not remove .*wanted"),
        ):
            quit_cmd.stop_console(self.world)

    def hold(self) -> subprocess.Popen[str]:
        """A process holding the windows lock until it is told to stop."""
        code = (
            "import os, sys, time\n"
            f"sys.path.insert(0, {str(Path(quit_cmd.__file__).parent)!r})\n"
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
            self.assertRaisesRegex(quit_cmd.QuitError, "setting up the windows"),
            quit_cmd.windows_lock(self.world),
        ):
            self.fail("the lock was taken while another process held it")

    def test_what_the_lock_starts_finds_it_held_and_it_is_free_afterwards(self) -> None:
        check = self.state / "bosun"
        held = f"{Path(run_locked.__file__)} --held {self.state / 'windows.lock'}"
        check.write_text(f"#!/bin/sh\nexec {sys.executable} -I {held}\n")
        check.chmod(0o755)
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(Path(quit_cmd.__file__).parent)!r})\n"
            "import quit as q\n"
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
            self.assertRaisesRegex(quit_cmd.QuitError, "bosun stop failed: tmux could not kill the session"),
        ):
            quit_cmd.stop_bosun(True)


if __name__ == "__main__":
    unittest.main()
