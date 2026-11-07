"""lib/update_state.py: the update's record, its hold, and the lock that says one runs."""

from __future__ import annotations

import dataclasses
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import update_state
import watchdog


LIB = Path(__file__).resolve().parents[2] / "lib"


def state(step: str = "holding", hold_until: int = 1000) -> update_state.UpdateState:
    return update_state.UpdateState(
        id="u1",
        from_root="/Users/me/bosun-old",
        to_root="/Users/me/bosun-new",
        from_commit="aaa",
        to_commit="bbb",
        step=step,
        started_at="2026-11-05T09:00:00",
        hold_until=hold_until,
    )


class HoldTest(unittest.TestCase):
    def test_a_hold_lasts_until_it_expires(self) -> None:
        self.assertTrue(update_state.hold_active(state(), 999))
        self.assertFalse(update_state.hold_active(state(), 1000))

    def test_no_hold_once_released(self) -> None:
        self.assertFalse(update_state.hold_active(state("released"), 0))

    def test_no_state_no_hold(self) -> None:
        self.assertFalse(update_state.hold_active(None, 0))

    def test_only_the_steps_from_holding_to_released_hold(self) -> None:
        """R24: planned and rehearsed never hold, whatever hold_until says."""
        self.assertEqual(
            update_state.HOLD_STEPS, ("holding", "marked", "backed-up", "migrated", "switched", "checked")
        )
        for step in update_state.STEPS:
            self.assertEqual(
                update_state.hold_active(state(step, hold_until=10**12), 0),
                step in update_state.HOLD_STEPS,
                step,
            )

    def test_a_newer_bosuns_step_holds_within_its_hour(self) -> None:
        """A step this code does not know: a newer bosun wrote the record, and it is taken for a hold."""
        self.assertTrue(update_state.hold_active(state("verifying", hold_until=1000), 999))
        self.assertFalse(update_state.hold_active(state("verifying", hold_until=1000), 1000))
        self.assertFalse(update_state.known(state("verifying")))
        self.assertTrue(update_state.known(state("migrated")))

    def test_the_marker_is_the_freeze_steps(self) -> None:
        for step in update_state.STEPS:
            self.assertEqual(update_state.in_freeze(state(step)), step in update_state.FREEZE_STEPS, step)
        self.assertFalse(update_state.in_freeze(None))


class RecordTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_the_record_is_written_whole(self) -> None:
        full = update_state.advanced(state(), "backed-up", backup="/b", pending=("api",), freeze_ms=5)
        update_state.write(self.dir, full)
        (self.dir / "update.json.tmp").write_text('{"id": "cut sho')
        self.assertEqual(update_state.read(self.dir), full)
        self.assertEqual(full.step, "backed-up")

    def test_nothing_recorded(self) -> None:
        self.assertIsNone(update_state.read(self.dir))

    def test_an_unreadable_record_is_none(self) -> None:
        (self.dir / "update.json").write_text("{nope")
        self.assertIsNone(update_state.read(self.dir))

    def test_an_unknown_step_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            update_state.advanced(state(), "sideways")

    def newer(self, **over: Any) -> None:
        """A record as a newer bosun writes it: this version's fields, and <over>."""
        (self.dir / "update.json").write_text(json.dumps(dict(dataclasses.asdict(state()), **over)))

    def test_a_newer_bosuns_fields_are_left_out(self) -> None:
        """R21: a field this code does not know is ignored, never a reason to read no record."""
        self.newer(verified_by="doctor", rollout={"waves": [1, 2]}, step="migrated")
        self.assertEqual(update_state.read(self.dir), state("migrated"))

    def test_an_earlier_bosuns_record_is_read_with_the_fields_added_since_at_their_defaults(self) -> None:
        """A record written before `windows` and `reread` existed: every field added since is optional."""
        earlier = {
            k: v for k, v in dataclasses.asdict(state("agents")).items() if k not in ("windows", "reread")
        }
        (self.dir / "update.json").write_text(json.dumps(earlier))
        found = update_state.load(self.dir)
        assert found is not None
        self.assertEqual((found.windows, found.reread), ({}, ()))

    def test_a_record_written_before_the_summary_s_parts_reads_with_them_empty(self) -> None:
        """A record of an update cut short by a bosun that kept no summary parts, carried on by this one."""
        added = ("plan", "migrated", "config_changed", "reloaded")
        earlier = {k: v for k, v in dataclasses.asdict(state("released")).items() if k not in added}
        (self.dir / "update.json").write_text(json.dumps(earlier))
        found = update_state.load(self.dir)
        assert found is not None
        self.assertEqual((found.plan, found.migrated, found.config_changed, found.reloaded), ({}, (), (), ()))

    def test_the_summary_s_parts_are_read_back(self) -> None:
        full = update_state.advanced(
            state(),
            "done",
            plan={"commits": ["c1 acme: retry the export"], "windows": [["api", "restart"]]},
            migrated=("ledger 1 -> 2: 412 items migrated",),
            config_changed=("bosun.env: BOSUN_OLD is now BOSUN_NEW (before: bosun.env.pre-update-x)",),
            reloaded=("com.bosun.morning.watchdog",),
        )
        update_state.write(self.dir, full)
        self.assertEqual(update_state.read(self.dir), full)

    def test_a_plan_that_is_no_object_is_no_record(self) -> None:
        for plan in (["c1"], "c1", 1):
            (self.dir / "update.json").write_text(json.dumps({**dataclasses.asdict(state()), "plan": plan}))
            with self.assertRaises(update_state.RecordUnreadable, msg=str(plan)):
                update_state.load(self.dir)

    def test_what_was_done_to_each_window_is_read_back(self) -> None:
        full = update_state.advanced(
            state(),
            "agents",
            pending=("api",),
            windows={"api": ("repo", "restart", "restarted")},
            reread=("/p",),
        )
        update_state.write(self.dir, full)
        self.assertEqual(update_state.read(self.dir), full)

    def test_a_windows_field_of_the_wrong_shape_is_no_record(self) -> None:
        for windows in (["api"], {"api": "restart"}, {"api": [1]}):
            (self.dir / "update.json").write_text(
                json.dumps({**dataclasses.asdict(state()), "windows": windows})
            )
            with self.assertRaises(update_state.RecordUnreadable, msg=str(windows)):
                update_state.load(self.dir)

    def test_a_newer_bosuns_step_is_read(self) -> None:
        self.newer(step="verifying")
        found = update_state.read(self.dir)
        assert found is not None
        self.assertEqual(found.step, "verifying")
        self.assertFalse(update_state.known(found))

    def test_a_record_there_but_unusable_is_unreadable_to_load(self) -> None:
        """load says why a record that is there is none (read says only None), naming the file."""
        for text in ("{nope", "[]", json.dumps(dict(dataclasses.asdict(state()), hold_until="soon"))):
            (self.dir / "update.json").write_text(text)
            with self.assertRaises(update_state.RecordUnreadable, msg=text) as caught:
                update_state.load(self.dir)
            self.assertIn(str(self.dir / "update.json"), str(caught.exception))
            self.assertIsNone(update_state.read(self.dir), text)

    def test_load_of_nothing_is_none(self) -> None:
        self.assertIsNone(update_state.load(self.dir))

    def test_windows_are_pending_only_while_an_update_brings_them_along(self) -> None:
        self.assertFalse(update_state.windows_pending(self.dir))
        for step, pending, expected in (
            ("agents", ("api",), True),
            ("agents", (), False),
            ("done", ("api",), False),
        ):
            update_state.write(self.dir, update_state.advanced(state(), step, pending=pending))
            self.assertEqual(update_state.windows_pending(self.dir), expected, (step, pending))


class LockTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_one_update_at_a_time(self) -> None:
        update_state.write(self.dir, state())
        self.assertFalse(update_state.running(self.dir))
        with update_state.one_update(self.dir):
            self.assertTrue(update_state.running(self.dir))
            with self.assertRaises(update_state.UpdateRunning) as raised, update_state.one_update(self.dir):
                pass
            self.assertEqual(raised.exception.from_commit, "aaa")
            self.assertEqual(raised.exception.to_commit, "bbb")
            self.assertEqual(raised.exception.started_at, "2026-11-05T09:00:00")
        self.assertFalse(update_state.running(self.dir))

    def test_a_dead_update_is_not_running(self) -> None:
        code = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import update_state\n"
            "with update_state.one_update(Path(sys.argv[2])):\n"
            "    print('held', flush=True); time.sleep(60)\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(LIB), str(self.dir)], stdout=subprocess.PIPE, text=True
        )
        try:
            assert child.stdout is not None
            self.assertEqual(child.stdout.readline().strip(), "held")
            self.assertTrue(update_state.running(self.dir))
            os.kill(child.pid, signal.SIGKILL)
            child.wait()
        finally:
            child.kill()
            if child.stdout:
                child.stdout.close()
        self.assertFalse(update_state.running(self.dir))


class StuckTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_a_dead_update_leaves_a_stuck_hold(self) -> None:
        update_state.write(self.dir, state("holding", hold_until=10**12))
        self.assertIn("left its hold", update_state.stuck(self.dir, 500))

    def test_a_freeze_with_nothing_running_is_stuck(self) -> None:
        update_state.write(self.dir, state("migrated", hold_until=10**12))
        self.assertIn("migrated", update_state.stuck(self.dir, 500))

    def test_a_running_update_is_not_stuck_within_its_hour(self) -> None:
        update_state.write(self.dir, state("holding", hold_until=1000))
        with update_state.one_update(self.dir):
            self.assertEqual(update_state.stuck(self.dir, 500), "")
            self.assertIn("outlived its hour", update_state.stuck(self.dir, 2000))

    def test_a_finished_update_is_not_stuck(self) -> None:
        for step in ("done", "restored", "failed", "released"):
            update_state.write(self.dir, state(step, hold_until=1000))
            self.assertEqual(update_state.stuck(self.dir, 2000), "", step)

    def test_an_update_stopped_before_its_hold_is_not_stuck(self) -> None:
        """R24: planned and rehearsed changed nothing and held nothing."""
        for step in ("planned", "rehearsed"):
            update_state.write(self.dir, state(step, hold_until=10**12))
            self.assertEqual(update_state.stuck(self.dir, 500), "", step)

    def newer(self, **over: Any) -> None:
        (self.dir / "update.json").write_text(json.dumps(dict(dataclasses.asdict(state()), **over)))

    def test_a_newer_bosuns_record_with_nothing_running_is_stuck(self) -> None:
        self.newer(step="verifying", hold_until=10**12, verified_by="doctor")
        why = update_state.stuck(self.dir, 500)
        self.assertIn("verifying", why)
        self.assertIn("newer bosun", why)
        self.newer(step="migrated", hold_until=10**12, verified_by="doctor")
        self.assertIn("migrated", update_state.stuck(self.dir, 500))

    def test_a_newer_bosuns_step_with_the_lock_held_is_a_hold(self) -> None:
        self.newer(step="verifying", hold_until=1000, verified_by="doctor")
        with update_state.one_update(self.dir):
            self.assertTrue(update_state.holding(self.dir, 500))
            self.assertEqual(update_state.stuck(self.dir, 500), "")
            self.assertIn("outlived its hour", update_state.stuck(self.dir, 2000))
        self.assertFalse(update_state.holding(self.dir, 500))

    def cli(self, command: str) -> subprocess.CompletedProcess[str]:
        argv = [sys.executable, str(LIB / "update_state.py"), command, "--state-dir", str(self.dir)]
        return subprocess.run(argv, capture_output=True, text=True, check=False)

    def test_a_hold_with_no_lock_is_not_active(self) -> None:
        update_state.write(self.dir, state("holding", hold_until=10**12))
        self.assertEqual(self.cli("hold-active").returncode, 1)
        out = self.cli("stuck")
        self.assertEqual(out.returncode, 0)
        self.assertTrue(out.stdout.strip())

    def test_a_hold_with_the_lock_held_is_active(self) -> None:
        update_state.write(self.dir, state("holding", hold_until=10**12))
        code = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import update_state\n"
            "with update_state.one_update(Path(sys.argv[2])):\n"
            "    print('held', flush=True); time.sleep(60)\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(LIB), str(self.dir)], stdout=subprocess.PIPE, text=True
        )
        try:
            assert child.stdout is not None
            self.assertEqual(child.stdout.readline().strip(), "held")
            self.assertEqual(self.cli("hold-active").returncode, 0)
            self.assertEqual(self.cli("stuck").returncode, 1)
        finally:
            child.kill()
            child.wait()
            if child.stdout:
                child.stdout.close()


class MalformedRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def record(self, **over: Any) -> str:
        data: dict[str, Any] = dict(dataclasses.asdict(state()), **over)
        return json.dumps(data)

    def bad(self) -> list[str]:
        return [
            "[]",
            "null",
            '"text"',
            self.record(hold_until="soon"),
            self.record(hold_until=True),
            self.record(pending="api"),
            self.record(id=7),
            json.dumps({"id": "u1"}),
        ]

    def test_none_of_them_is_a_record_or_a_crash(self) -> None:
        for text in self.bad():
            (self.dir / "update.json").write_text(text)
            self.assertIsNone(update_state.read(self.dir), text)
            self.assertFalse(update_state.hold_active(update_state.read(self.dir), 0))
            self.assertEqual(update_state.stuck(self.dir, 0), "", text)
            for command in ("hold-active", "stuck", "windows-pending"):
                argv = [sys.executable, str(LIB / "update_state.py"), command, "--state-dir", str(self.dir)]
                done = subprocess.run(argv, capture_output=True, text=True, check=False)
                self.assertEqual(done.returncode, 1, (text, command, done.stderr))
                self.assertEqual(done.stderr, "", text)


class WatchdogCheckTest(unittest.TestCase):
    def test_the_check_reports_a_stuck_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            update_state.write(Path(tmp), state("holding", hold_until=1))
            ctx: Any = SimpleNamespace(
                now=datetime(2026, 11, 5, 9, 0), config=SimpleNamespace(state_dir=Path(tmp))
            )
            result = watchdog.check_update_stuck(ctx)
            self.assertEqual(len(result.findings), 1)
            self.assertTrue(result.findings[0].key.startswith("update-stuck"))
            update_state.write(Path(tmp), state("migrated", hold_until=10**12))
            self.assertEqual(len(watchdog.check_update_stuck(ctx).findings), 1)
            update_state.write(Path(tmp), state("done"))
            self.assertEqual(watchdog.check_update_stuck(ctx).findings, [])
            newer = dict(dataclasses.asdict(state("verifying", hold_until=1)), verified_by="doctor")
            (Path(tmp) / "update.json").write_text(json.dumps(newer))
            [finding] = watchdog.check_update_stuck(ctx).findings
            self.assertIn("verifying", finding.message)
        self.assertIn("update-stuck", [c.name for c in watchdog.CHECKS])


class ProbeTest(unittest.TestCase):
    """A writer's running() takes update.lock shared for an instant: an update waits that out."""

    def test_one_update_waits_out_a_writers_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            held, release = threading.Event(), threading.Event()

            def probe() -> None:
                fd = os.open(folder / update_state.LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
                fcntl.flock(fd, fcntl.LOCK_SH)
                held.set()
                release.wait(0.1)
                os.close(fd)

            thread = threading.Thread(target=probe)
            thread.start()
            held.wait(5)
            try:
                with update_state.one_update(folder, wait=0):
                    taken = True
            finally:
                thread.join()
            self.assertTrue(taken)


if __name__ == "__main__":
    unittest.main()
