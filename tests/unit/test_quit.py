"""lib/quit.py: what `bosun quit` plans and does (design: docs/design/menu-bar.md, "Quit"). The plan is pure
over the windows; the run goes through Ports, which these tests replace with recorded fakes (a clock that
only the sleeps move, the steps written down in the order they happen). Fictional data: acme's windows."""

from __future__ import annotations

import io
import json
import sys
import unittest
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
        self.steps: list[str] = []
        self.now = 1000.0
        self.slept: list[float] = []

    def read_windows(self) -> list[quit_cmd.Window]:
        state = self.states[min(self.reads, len(self.states) - 1)]
        self.reads += 1
        return state

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def write_quit(self, paused: bool) -> None:
        self.steps.append(f"quit.json paused={paused}")

    def stop_console(self) -> str:
        self.steps.append("console")
        return "stopped"

    def stop_bosun(self, keep_awake: bool) -> None:
        self.steps.append(f"stop keep_awake={keep_awake}")
        if self.stop_fails:
            raise quit_cmd.QuitError(self.stop_fails)

    def ports(self) -> quit_cmd.Ports:
        return quit_cmd.Ports(
            read_windows=self.read_windows,
            updating=lambda: self.updating,
            keep_awake_until=lambda: self.keep_awake,
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


if __name__ == "__main__":
    unittest.main()
