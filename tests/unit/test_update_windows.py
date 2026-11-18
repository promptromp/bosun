"""lib/update_windows.py: the windows brought along for real: the turn's commands (bosun commands of
the new version), and the update's wait with its Ctrl-C (docs/design/update.md,
"Bringing the agents along"). The decisions are tests/unit/test_update_agents.py. Fictional windows."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shutil
import signal
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
import install_paths
import update_facts
import update_notes
import update_plan
import update_state
import update_windows
from test_update_agents import FROM, TO, bringing
from update_agents import Started
from update_plan import Treatment
from update_state import UpdateState


class Folder(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.tmp = tmp
        self.state_dir = tmp / "state"


class CommandsTest(Folder):
    """The real actions: bosun commands of the new version, each in a session of its own."""

    def setUp(self) -> None:
        super().setUp()
        self.said: list[str] = []
        self.commands = update_windows.Commands(self.tmp / "bbbbbbb22222", self.state_dir, self.said.append)

    def ran(self, code: int, stderr: str = "") -> contextlib.AbstractContextManager[mock.MagicMock]:
        done = subprocess.CompletedProcess([], code, "", stderr)
        return mock.patch.object(update_facts, "bosun", return_value=done)

    def test_the_refresh_exit_status_says_what_the_restart_did(self) -> None:
        for code, started in ((0, Started.YES), (3, Started.LEFT_RUNNING), (4, Started.NEXT_CONVERSATION)):
            with self.ran(code) as bosun:
                self.assertEqual(self.commands.restart("api", "bosun was updated"), started)
            args, kwargs = bosun.call_args
            self.assertEqual(args[1:4], ("refresh", "--update-restart", args[3]))
            self.assertEqual((args[4], kwargs["detached"]), ("api", True))
            self.assertFalse(Path(args[3]).exists())  # the prompt file is gone once refresh read it
        self.assertEqual(self.said, [])

    def test_any_window_name_can_be_restarted(self) -> None:
        """Minor 5: the prompt's file is named for no window, so a name with a slash, or one longer than
        a file name may be, still gets its restart."""
        for window in ("acme/api", "acme-" + "x" * 295):
            with self.ran(0) as bosun:
                self.assertEqual(self.commands.restart(window, "bosun was updated"), Started.YES)
            self.assertEqual(bosun.call_args.args[4], window)
            self.assertEqual(Path(bosun.call_args.args[3]).parent, self.state_dir / update_state.UPDATE_DIR)

    def test_a_launch_that_failed_did_not_start_and_says_why(self) -> None:
        """refresh exits 5 when the window's launch failed: final, its reason the last line on stderr."""
        stderr = "2026-11-07 14:02:00 [refresh] api: did not start: x\n'api' does not start: no plugin\n"
        with self.ran(5, stderr):
            self.assertEqual(self.commands.restart("api", "bosun was updated"), Started.DID_NOT_START)
        self.assertEqual(self.commands.why_not_started("api"), "'api' does not start: no plugin")
        self.assertEqual(self.commands.why_not_started("web"), "")
        self.assertEqual(self.said, [])  # the turn records it, and says that

    def test_a_failure_is_said_once(self) -> None:
        for _ in range(2):
            with self.ran(1, "refresh: the window is split into several panes\n"):
                self.assertEqual(self.commands.relaunch("api", "bosun was updated"), Started.FAILED)
        self.assertEqual(
            self.said, ["  api: not restarted yet: refresh: the window is split into several panes"]
        )

    def test_a_prompt_goes_through_bosun_send_on_stdin(self) -> None:
        with self.ran(0) as bosun:
            self.assertTrue(self.commands.send("orchestrator", "bosun was updated"))
        args, kwargs = bosun.call_args
        self.assertEqual(args[1:], ("send", "--no-item", "orchestrator", "-"))
        self.assertEqual((kwargs["stdin"], kwargs["detached"]), ("bosun was updated", True))
        with self.ran(1, "window 'orchestrator' is waiting (a permission prompt)"):
            self.assertFalse(self.commands.send("orchestrator", "bosun was updated"))

    def test_the_windows_are_listed_once_a_turn(self) -> None:
        listed = [
            {"name": "orch", "kind": "orchestrator", "window": True, "conversation": "orch-2026-11-07",
             "agent": {"status": "idle", "name": "orch-2026-11-06"}},
            {"name": "api", "kind": "session", "window": True, "conversation": "api",
             "agent": {"status": "busy", "name": "api"}},
        ]  # fmt: skip
        with mock.patch.object(update_facts, "open_windows", return_value=listed) as listing:
            self.assertEqual(self.commands.statuses(), {"orch": "idle", "api": "busy"})
            self.assertTrue(self.commands.on_another_conversation("orch"))
            self.assertFalse(self.commands.on_another_conversation("api"))
        listing.assert_called_once()

    def test_what_the_last_turn_left_running_is_the_stop_hooks(self) -> None:
        signals = self.state_dir / "signals" / "web.jsonl"
        signals.parent.mkdir(parents=True)
        stop = {
            "at": "2026-11-07T14:01:00",
            "event": "Stop",
            "running": [{"type": "shell", "what": "sleep 900"}],
        }
        signals.write_text(json.dumps(stop) + "\n")
        self.assertEqual(self.commands.left_running("web"), "shell `sleep 900`")
        self.assertEqual(self.commands.left_running("api"), "")


class TurnTest(Folder):
    def test_no_window_s_name_stops_the_turn_for_the_windows_after_it(self) -> None:
        """Minor 5: a name longer than a file name may be, or with a slash, is restarted like any."""
        names = ("acme-" + "x" * 295, "acme/api", "api")
        record = bringing(dict.fromkeys(names, ("repo", Treatment.RESTART)))
        update_state.write(self.state_dir, record)
        listed = [{"name": n, "kind": "extra", "window": True, "agent": {"status": "idle"}} for n in names]
        done = subprocess.CompletedProcess([], 0, "", "")
        with (
            mock.patch.object(update_facts, "open_windows", return_value=listed),
            mock.patch.object(update_facts, "bosun", return_value=done) as bosun,
        ):
            waiting = update_windows.turn(self.state_dir, lambda line: None)
        self.assertEqual([call.args[4] for call in bosun.call_args_list], list(names))
        self.assertEqual(waiting, dict.fromkeys(names, "idle"))  # restarted, each now to answer
        after = update_state.read(self.state_dir)
        assert after is not None
        self.assertEqual([after.windows[n][-1] for n in names], ["restarted"] * 3)


class BringAlongTest(Folder):
    """The update's own wait: the record at agents first, a turn every 5 s, the windows waited for
    said when they change, done once none is left; Ctrl-C leaves them to the check-in loop, or to
    `bosun update advance` by hand when no loop runs."""

    def setUp(self) -> None:
        super().setUp()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            mock.patch.dict(
                os.environ, {"BOSUN_STATE_DIR": str(self.state_dir), "BOSUN_CONFIG_DIR": str(self.tmp)}
            )
        )
        self.loop = stack.enter_context(mock.patch.object(update_facts, "loop_runs", return_value=True))
        self.knowledge_on = stack.enter_context(
            mock.patch.object(update_facts, "knowledge_on", return_value=False)
        )
        self.said: list[str] = []
        self.at_turns: list[tuple[str, tuple[str, ...]]] = []
        update_state.write(self.state_dir, update_state.advanced(bringing({}), "released", pending=()))

    def plan(self) -> update_plan.Plan:
        return update_plan.Plan(
            from_commit=FROM,
            to_commit=TO,
            commits=(),
            migrations=(),
            rules=(),
            keys_added=(),
            renames=(),
            prompts=("orchestrator/knowledge.md", "orchestrator/policy.md", "orchestrator/learn.md"),
            windows=(
                ("orchestrator", Treatment.REREAD),
                ("api", Treatment.WAIT),
                ("web", Treatment.LEAVE),
                ("checkin", Treatment.RESTART_LOOP),
            ),
            jobs=(),
            console=False,
            handed=(),
            user_changes=(),
            blockers=(),
            kinds=(
                ("orchestrator", "orchestrator"),
                ("api", "repo"),
                ("web", "repo"),
                ("checkin", "checkin"),
            ),
        )

    @contextlib.contextmanager
    def turns(self, *waits: list[str] | dict[str, str]) -> Iterator[mock.MagicMock]:
        """Each turn waits for the next of <waits> (busy, unless it names each one's status), dropping
        the others from the record."""
        left = [wait if isinstance(wait, dict) else dict.fromkeys(wait, "busy") for wait in waits]

        def turn(state_dir: Path, say: Any) -> dict[str, str]:
            state = update_state.read(state_dir)
            assert state is not None
            self.at_turns.append((state.step, state.pending))
            waiting = left.pop(0)
            update_state.write(state_dir, update_state.advanced(state, "agents", pending=tuple(waiting)))
            return waiting

        with (
            mock.patch.object(update_windows, "turn", side_effect=turn) as turned,
            mock.patch.object(time, "sleep") as slept,
        ):
            yield slept
            self.turned = turned

    def bring_along(self) -> UpdateState:
        state = update_state.read(self.state_dir)
        assert state is not None
        return update_windows.bring_along(state, self.plan(), say=self.said.append)

    def test_the_wait_between_turns_is_poll_seconds_unless_the_tests_knob_says(self) -> None:
        steps = update_windows.CTRL_C_POLL_SECONDS  # the wait sleeps in steps of this, for its Ctrl-C
        with mock.patch.dict(os.environ), self.turns(["api"], []) as slept:
            os.environ.pop("BOSUN_UPDATE_POLL_SECONDS", None)
            self.bring_along()
        self.assertEqual(slept.call_count, round(update_windows.POLL_SECONDS / steps))
        update_state.write(self.state_dir, update_state.advanced(bringing({}), "released", pending=()))
        knob = {"BOSUN_UPDATE_POLL_SECONDS": "0.4"}
        with mock.patch.dict(os.environ, knob), self.turns(["api"], []) as slept:
            self.bring_along()
        self.assertEqual(slept.call_count, 2)

    def test_a_knob_below_one_step_still_waits_one_step_between_turns(self) -> None:
        knob = {"BOSUN_UPDATE_POLL_SECONDS": "0.05"}
        with mock.patch.dict(os.environ, knob), self.turns(["api"], []) as slept:
            self.bring_along()
        self.assertEqual(slept.call_args_list, [mock.call(update_windows.CTRL_C_POLL_SECONDS)])

    def test_a_knob_that_is_no_positive_number_waits_the_default(self) -> None:
        for value in ("soon", "0", "-1", "nan"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"BOSUN_UPDATE_POLL_SECONDS": value}):
                self.assertEqual(update_windows.poll_seconds(), update_windows.POLL_SECONDS)

    def test_the_windows_to_bring_along_are_pending_from_the_start(self) -> None:
        with self.turns(["orchestrator", "api"], ["api"], ["api"], []):
            done = self.bring_along()
        self.assertEqual(self.at_turns[0], ("agents", ("orchestrator", "api")))
        self.assertEqual(done.step, "done")
        self.assertEqual(
            done.windows,
            {"orchestrator": ("orchestrator", "re-read"), "api": ("repo", "wait until idle")},
        )
        install = install_paths.install_path(Path(done.to_root))
        self.assertEqual(done.reread, (str(install / "orchestrator" / "policy.md"),))
        waited = [line for line in self.said if line.startswith("Waiting for")]
        self.assertEqual(len(waited), 2)  # said when the list changed, not every turn
        self.assertIn("orchestrator, api", waited[0])

    def test_the_ontology_rules_are_reread_only_with_the_key_on(self) -> None:
        """As the plan's treatment (update_plan.reread_patterns): with the key off (above) only the policy;
        with it on, the rules too."""
        self.knowledge_on.return_value = True
        with self.turns([]):
            done = self.bring_along()
        install = install_paths.install_path(Path(done.to_root))
        rules, policy = (str(install / "orchestrator" / name) for name in ("knowledge.md", "policy.md"))
        self.assertEqual(done.reread, (rules, policy))
        self.knowledge_on.assert_called_with(Path(done.to_root))

    def test_ctrl_c_leaves_the_windows_to_the_check_in_loop(self) -> None:
        before = signal.getsignal(signal.SIGINT)
        with self.turns(["api"], ["api"]) as slept:
            slept.side_effect = lambda _: os.kill(os.getpid(), signal.SIGINT)
            stopped = self.bring_along()
        self.assertEqual((stopped.step, stopped.pending), ("agents", ("api",)))
        self.assertEqual(self.turned.call_count, 1)
        self.assertIn("Stopped waiting: the check-in loop brings along api", self.said[-1])
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    def test_without_a_check_in_loop_ctrl_c_points_to_bosun_update_advance(self) -> None:
        self.loop.return_value = False
        with self.turns(["api"]) as slept:
            slept.side_effect = lambda _: os.kill(os.getpid(), signal.SIGINT)
            self.bring_along()
        self.assertIn("no check-in loop runs here: bosun update advance, run by hand", self.said[-1])
        self.assertNotIn("the check-in loop brings", self.said[-1])

    def test_the_wait_ends_once_only_windows_waiting_for_you_are_left(self) -> None:
        """Decision 1: a window at a permission prompt or a question never ends its wait by itself, so
        once every window still waited for waits for me, the wait ends as Ctrl-C ends it, each named."""
        with self.turns({"orchestrator": "waiting", "api": "busy"}, {"orchestrator": "waiting"}):
            stopped = self.bring_along()
        self.assertEqual(self.turned.call_count, 2)  # a busy one among them kept the wait
        self.assertEqual((stopped.step, stopped.pending), ("agents", ("orchestrator",)))
        self.assertTrue(stopped.handed_over)  # the status no longer calls it running
        self.assertEqual(update_state.read(self.state_dir), stopped)
        self.assertEqual(
            self.said[-2:],
            [
                "Stopped waiting: every window still waited for waits for your answer.",
                (
                    "  orchestrator waits for your answer at its prompt; once you answer, the check-in loop "
                    "asks it to re-read its prompts for the update"
                ),
            ],
        )

    def test_without_a_check_in_loop_a_window_waiting_for_you_names_bosun_update_advance(self) -> None:
        self.loop.return_value = False
        with self.turns({"api": "waiting"}):
            self.bring_along()
        self.assertEqual(
            self.said[-1],
            "  api waits for your answer at its prompt; once you answer, bosun update advance, run by hand, "
            "restarts it for the update",
        )

    def test_a_ctrl_c_hands_the_windows_over_too(self) -> None:
        with self.turns(["api"]) as slept:
            slept.side_effect = lambda _: os.kill(os.getpid(), signal.SIGINT)
            stopped = self.bring_along()
        self.assertTrue(stopped.handed_over)

    def test_the_windows_the_last_update_still_waits_for_are_taken_over(self) -> None:
        """Decision 3: the last record (kept as `carried`) brings its windows along with this one's: one
        the plan restarts keeps the list it saved for the last one; the orchestrator re-reads both
        updates' files; their notes stay, the others' go."""
        last = bringing(
            {"api": ("repo", Treatment.TAKE_STOCK), "orchestrator": ("orchestrator", Treatment.REREAD)},
            reread=("/Users/me/.local/share/bosun/orchestrator/tasks/review.md",),
        )
        last = update_state.advanced(
            last, "agents", windows={**last.windows, "api": (*last.windows["api"], "asked to take stock")}
        )
        update_notes.note(self.state_dir, "api", "stock", "sleep 600: the wait for CI")
        update_notes.note(self.state_dir, "web", "stock", "from the update before")
        state = update_state.read(self.state_dir)
        assert state is not None
        update_state.write(self.state_dir, dataclasses.replace(state, carried=update_state.as_object(last)))
        with self.turns([]):
            done = self.bring_along()
        self.assertEqual(done.windows["api"], ("repo", "wait until idle", "asked to take stock"))
        install = install_paths.install_path(Path(done.to_root))
        self.assertEqual(
            done.reread,
            (
                str(install / "orchestrator" / "policy.md"),
                "/Users/me/.local/share/bosun/orchestrator/tasks/review.md",
            ),
        )
        self.assertEqual(update_notes.read_note(self.state_dir, "api", "stock"), "sleep 600: the wait for CI")
        self.assertIsNone(update_notes.read_note(self.state_dir, "web", "stock"))

    def test_notes_an_earlier_update_left_are_not_this_ones(self) -> None:
        update_notes.note(self.state_dir, "api", "status", "from the update before")
        with self.turns([]):
            self.bring_along()
        self.assertIsNone(update_notes.read_note(self.state_dir, "api", "status"))


if __name__ == "__main__":
    unittest.main()
