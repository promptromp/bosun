"""lib/quit.py: what `bosun quit` plans and does (design: docs/design/menu-bar.md, "Quit"). The plan is pure
over the windows; the run goes through Ports, which these tests replace with recorded fakes (a clock that
only the sleeps move, the steps written down in the order they happen). Fictional data: acme's windows."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import quit as quit_cmd


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
            10,
            180,
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
                "wait_minutes": 10,
                "lock_wait_seconds": 180,
            },
        )

    def test_a_window_with_no_agent_is_open_and_neither_busy_nor_waiting(self) -> None:
        plan = quit_cmd.quit_plan([window("alpha", None)], "08:30", False, 10, 180)
        self.assertEqual((plan["windows"], plan["busy"], plan["waiting"]), (["alpha"], [], []))
        self.assertEqual(plan["keep_awake_until"], "08:30")

    def test_an_update_refuses_the_quit_and_says_why(self) -> None:
        plan = quit_cmd.quit_plan([window("alpha", "busy")], None, True, 10, 180)
        self.assertIn("update", str(plan["refused"]))
        self.assertEqual(plan["busy"], ["alpha"])  # still said: what the person would be told about

    def test_no_update_is_no_refusal(self) -> None:
        self.assertIsNone(quit_cmd.quit_plan([], None, False, 10, 180)["refused"])

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
        self.console: tuple[str, int | None] = ("stopped", None)
        self.update_when_locked = False  # an update that begins while the lock is held
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

    def stop_console(self) -> tuple[str, int | None]:
        self.step("console")
        return self.console

    def stop_bosun(self, keep_awake: bool) -> None:
        self.step(f"stop keep_awake={keep_awake}")
        if self.stop_fails:
            raise quit_cmd.QuitError(self.stop_fails)

    def ports(self) -> quit_cmd.Ports:
        return quit_cmd.Ports(
            read_windows=self.read_windows,
            updating=lambda: self.updating or (self.update_when_locked and self.locked),
            keep_awake_until=lambda: self.keep_awake,
            lock=self.lock,
            write_quit=self.write_quit,
            stop_console=self.stop_console,
            stop_bosun=self.stop_bosun,
            sleep=self.sleep,
            clock=lambda: self.now,
        )


def run(  # noqa: PLR0913, PLR0917 - a test helper: each option of a run is a keyword
    fake: Fake,
    mode: str,
    minutes: int = 10,
    pause: bool = False,
    sleep: bool = False,
    as_json: bool = False,
    lock_wait: int = 180,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    options = quit_cmd.Options(mode, minutes, pause, sleep, as_json, lock_wait)
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
        self.assertEqual(
            (data["morning_paused"], data["console"], data["console_port"]), (True, "stopped", None)
        )
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
            sorted(data),
            [
                "busy",
                "contract",
                "keep_awake_until",
                "lock_wait_seconds",
                "refused",
                "wait_minutes",
                "waiting",
                "windows",
            ],
        )
        self.assertEqual(data["keep_awake_until"], "09:00")
        self.assertEqual(fake.steps, [])

    def test_the_plan_says_how_long_a_quit_may_wait_so_the_app_can_time_it(self) -> None:
        """The app passes --let-finish=<N> and gives the quit N minutes plus the windows lock wait plus a
        margin: the minutes of the run (BOSUN_QUIT_WAIT_MINUTES, which the command passes as them) and the
        lock's seconds (BOSUN_WINDOWS_LOCK_WAIT_SECONDS), ints, also in the object of a failure."""
        for minutes, lock in ((10, 180), (0, 0), (25, 30)):
            _status, out, _err = run(
                Fake([window("alpha")]), "plan", minutes=minutes, as_json=True, lock_wait=lock
            )
            data = json.loads(out)
            self.assertEqual((data["wait_minutes"], data["lock_wait_seconds"]), (minutes, lock))

        def broken() -> list[quit_cmd.Window]:
            raise quit_cmd.QuitError("the windows could not be read")

        fake = Fake([])
        fake.read_windows = broken  # type: ignore[method-assign]
        _status, out, _err = run(fake, "plan", minutes=7, as_json=True, lock_wait=45)
        data = json.loads(out)
        self.assertEqual((data["wait_minutes"], data["lock_wait_seconds"]), (7, 45))

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
        self.assertEqual(fake.read_locked, [False, False, True])  # the wait held none; the last look did

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

        def broken() -> tuple[str, int | None]:
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
        fake.console = ("still running", 8765)
        _status, out, _err = run(fake, "now")
        self.assertIn("still running on port 8765", out)
        self.assertNotIn("not the job", out)

    def test_the_json_console_field_is_one_of_a_fixed_set_and_the_port_is_its_own_key(self) -> None:
        fake = Fake([window("alpha")])
        fake.console = ("still running", 8765)
        _status, out, _err = run(fake, "now", as_json=True)
        data = json.loads(out)
        self.assertEqual((data["console"], data["console_port"]), ("still running", 8765))
        fake.console = ("still running", None)
        _status, out, _err = run(fake, "now", as_json=True)
        self.assertEqual(json.loads(out)["console_port"], None)

    def test_an_update_that_began_while_the_lock_was_taken_refuses_the_quit_with_nothing_written(
        self,
    ) -> None:
        for mode in ("now", "finish"):
            fake = Fake([window("alpha")])
            fake.update_when_locked = True
            status, out, _err = run(fake, mode, as_json=True)
            data = json.loads(out)
            self.assertEqual((status, data["result"]), (1, "refused"))
            self.assertIn("update", data["refused"])
            self.assertEqual(fake.steps, [])

    def test_a_window_busy_again_under_the_lock_lets_it_go_and_goes_back_to_waiting(self) -> None:
        # reads: the wait (idle), under the lock (busy again), the wait again (idle), under the lock (idle)
        fake = Fake([window("alpha")], [window("alpha", "busy")], [window("alpha")], [window("alpha")])
        status, _out, _err = run(fake, "finish")
        self.assertEqual(status, 0)
        self.assertEqual(fake.read_locked, [False, True, False, True])
        self.assertEqual(fake.outside, [])
        self.assertEqual(fake.steps, ["quit.json paused=False", "console", "stop keep_awake=True"])

    def test_busy_again_under_the_lock_waits_on_with_the_same_deadline_and_says_so_when_it_passes(
        self,
    ) -> None:
        # idle at the first look, busy ever after: no time left at all (0 minutes) is exit 3, nothing changed
        fake = Fake([window("alpha")], [window("alpha", "busy")])
        status, out, _err = run(fake, "finish", minutes=0, as_json=True)
        data = json.loads(out)
        self.assertEqual((status, data["result"], data["busy"]), (3, "busy", ["alpha"]))
        self.assertEqual(fake.steps, [])
        self.assertEqual(fake.outside, [])

    def test_the_wait_after_busy_again_ends_at_the_original_deadline(self) -> None:
        # one minute: idle, then busy under the lock, then busy for the rest of it
        fake = Fake([window("alpha")], [window("alpha", "busy")])
        status, _out, _err = run(fake, "finish", minutes=1)
        self.assertEqual(status, 3)
        self.assertEqual(
            sum(fake.slept), 60
        )  # the whole minute, counted from the start, not from the re-look
        self.assertEqual(fake.steps, [])

    def test_the_windows_reported_stopped_are_those_of_the_look_under_the_lock(self) -> None:
        fake = Fake([window("alpha"), window("beta", "waiting")], [window("alpha")])
        _status, out, _err = run(fake, "now", as_json=True)
        data = json.loads(out)
        self.assertEqual((data["windows"], data["waiting"]), (["alpha"], []))

    def test_stop_now_under_the_lock_looks_again_too(self) -> None:
        fake = Fake([window("alpha", "busy")])
        _status, _out, _err = run(fake, "now")
        self.assertEqual(fake.read_locked, [False, True])  # before the lock, and under it

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


if __name__ == "__main__":
    unittest.main()
