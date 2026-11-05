"""lib/update_state.py: the update's record, its hold, and the lock that says one runs."""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
import sys
import tempfile
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
            self.record(step="levitating"),
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
            for command in ("hold-active", "stuck"):
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
        self.assertIn("update-stuck", [c.name for c in watchdog.CHECKS])


if __name__ == "__main__":
    unittest.main()
