"""lib/update_agents.py: bringing the agents along once an update has switched (docs/design/update.md,
"Bringing the agents along"): what each window gets from its status now, each window acted on once,
the notes the windows answer with, the prompts, and the update's wait. A fake Actions records what
would be done, and a statuses mapping the test changes stands in for the windows. The end to end is
the smoke suite's "bosun update" section. Fictional windows."""

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
import update_agents
import update_facts
import update_plan
import update_state
from update_agents import Started
from update_plan import Treatment
from update_state import UpdateState


FROM = "aaaaaaa1111111111111111111111111111111111"
TO = "bbbbbbb2222222222222222222222222222222222"
POLICY = "/Users/me/.local/share/bosun/orchestrator/policy.md"


class FakeActions:
    """What advance asks for, in order. send delivers unless <refused>; restart and relaunch answer
    with <started> for the window (Started.YES unless a test says otherwise)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.refused: set[str] = set()
        self.started: dict[str, Started] = {}

    def send(self, window: str, text: str) -> bool:
        self.calls.append(("send", window, text))
        return window not in self.refused

    def restart(self, window: str, prompt: str) -> Started:
        self.calls.append(("restart", window, prompt))
        return self.started.get(window, Started.YES)

    def relaunch(self, window: str, prompt: str) -> Started:
        self.calls.append(("relaunch", window, prompt))
        return self.started.get(window, Started.YES)

    def made(self, kind: str) -> list[tuple[str, str]]:
        return [(window, text) for what, window, text in self.calls if what == kind]


def bringing(windows: dict[str, tuple[str, Treatment]], reread: tuple[str, ...] = ()) -> UpdateState:
    """A record at step agents with <windows> pending: each one's kind and the plan's treatment."""
    return UpdateState(
        id="u1",
        from_root="/Users/me/.local/share/bosun-versions/aaaaaaa11111",
        to_root="/Users/me/.local/share/bosun-versions/bbbbbbb22222",
        from_commit=FROM,
        to_commit=TO,
        step="agents",
        started_at="2026-11-07T14:00:00+00:00",
        hold_until=0,
        pending=tuple(windows),
        windows={name: (kind, how.value) for name, (kind, how) in windows.items()},
        reread=reread,
    )


class Agents(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.state_dir = tmp / "state"
        self.statuses: dict[str, str] = {}
        self.act = FakeActions()

    def bring(self, windows: dict[str, tuple[str, Treatment]], **statuses: str) -> None:
        update_state.write(self.state_dir, bringing(windows, reread=(POLICY,)))
        self.statuses = dict(statuses)

    def advance(self) -> list[str]:
        return update_agents.advance(self.state_dir, lambda: self.statuses, self.act)

    def record(self) -> UpdateState:
        state = update_state.read(self.state_dir)
        assert state is not None
        return state

    def answer(self, window: str, text: str = "on it again") -> None:
        update_agents.note(self.state_dir, window, "status", text)


class AdvanceTest(Agents):
    def test_an_idle_window_is_restarted_with_the_update_prompt(self) -> None:
        self.bring({"api": ("repo", Treatment.RESTART)}, api="idle")
        self.assertEqual(self.advance(), ["api"])
        [(window, prompt)] = self.act.made("restart")
        self.assertEqual(window, "api")
        self.assertIn("bosun was updated from aaaaaaa to bbbbbbb; this session was restarted", prompt)
        self.assertIn("bosun ledger in-flight api", prompt)
        self.assertIn("`bosun update note --window api --status`", prompt)
        self.assertEqual(self.act.made("send"), [])
        self.assertEqual(self.record().windows["api"], ("repo", "restart", "restarted"))

    def test_a_shell_window_takes_stock_first(self) -> None:
        self.bring({"web": ("repo", Treatment.TAKE_STOCK)}, web="shell")
        self.advance()
        [(window, asked)] = self.act.made("send")
        self.assertEqual(window, "web")
        self.assertIn("bosun will restart this session for an update (aaaaaaa -> bbbbbbb)", asked)
        self.assertIn("`bosun update note --window web --stock`", asked)
        self.assertEqual(self.act.made("restart"), [])
        self.advance()  # its list is not there yet: still only asked
        self.assertEqual(len(self.act.calls), 1)
        update_agents.note(self.state_dir, "web", "stock", "sleep 900: the CI wait; report CI when it ends")
        self.assertEqual(self.advance(), ["web"])
        [(window, prompt)] = self.act.made("restart")
        self.assertEqual(window, "web")
        self.assertIn("sleep 900: the CI wait; report CI when it ends", prompt)
        self.assertEqual(
            self.record().windows["web"],
            ("repo", "take stock, then restart", "asked to take stock", "restarted"),
        )

    def test_a_busy_window_waits(self) -> None:
        self.bring({"api": ("repo", Treatment.WAIT)}, api="busy")
        self.assertEqual(self.advance(), ["api"])
        self.assertEqual(self.act.calls, [])
        self.statuses["api"] = "idle"
        self.advance()
        self.assertEqual([w for w, _ in self.act.made("restart")], ["api"])

    def test_a_window_busy_again_is_not_restarted(self) -> None:
        """Review Focus 5: the plan said restart, but by now it is busy: it is never killed."""
        self.bring({"api": ("repo", Treatment.RESTART)}, api="busy")
        self.assertEqual(self.advance(), ["api"])
        self.assertEqual(self.act.calls, [])
        self.assertEqual(self.record().pending, ("api",))

    def test_a_waiting_window_is_never_touched(self) -> None:
        self.bring(
            {"api": ("repo", Treatment.ASK_FIRST), "orchestrator": ("orchestrator", Treatment.REREAD)},
            api="waiting",
            orchestrator="waiting",
        )
        for _ in range(3):
            self.assertEqual(self.advance(), ["api", "orchestrator"])
        self.assertEqual(self.act.calls, [])

    def test_the_orchestrator_rereads_without_a_restart(self) -> None:
        self.bring({"orchestrator": ("orchestrator", Treatment.REREAD)}, orchestrator="idle")
        self.advance()
        [(window, prompt)] = self.act.made("send")
        self.assertEqual(window, "orchestrator")
        self.assertIn("this session was kept running", prompt)
        self.assertIn(f"Re-read {POLICY}", prompt)
        self.assertIn("/bosun:chores check-in", prompt)
        self.assertEqual(len(self.act.calls), 1)

    def test_done_when_every_window_answered(self) -> None:
        self.bring(
            {"api": ("repo", Treatment.RESTART), "orchestrator": ("orchestrator", Treatment.REREAD)},
            api="idle",
            orchestrator="idle",
        )
        self.assertEqual(self.advance(), ["api", "orchestrator"])
        self.answer("api", "resumed: waiting on CI for PR 12")
        self.assertEqual(self.advance(), ["orchestrator"])
        self.answer("orchestrator")
        self.assertEqual(self.advance(), [])
        state = self.record()
        self.assertEqual((state.pending, state.step), ((), "done"))
        self.assertEqual(state.windows["api"][-1], "answered")

    def test_each_window_is_acted_on_once(self) -> None:
        self.bring(
            {"api": ("repo", Treatment.RESTART), "orchestrator": ("orchestrator", Treatment.REREAD)},
            api="idle",
            orchestrator="idle",
        )
        for _ in range(3):
            self.advance()
        self.assertEqual(
            [(what, window) for what, window, _ in self.act.calls],
            [("restart", "api"), ("send", "orchestrator")],
        )

    def test_a_window_left_running_is_tried_again(self) -> None:
        """refresh exits 3 (busy again, someone typing): nothing is recorded, the next turn decides
        again from its status then."""
        self.bring({"api": ("repo", Treatment.RESTART)}, api="idle")
        self.act.started["api"] = Started.LEFT_RUNNING
        self.advance()
        self.assertEqual(self.record().windows["api"], ("repo", "restart"))
        del self.act.started["api"]
        self.advance()
        self.assertEqual(len(self.act.made("restart")), 2)
        self.assertEqual(self.record().windows["api"], ("repo", "restart", "restarted"))

    def test_a_prompt_not_delivered_is_sent_again(self) -> None:
        self.bring({"orchestrator": ("orchestrator", Treatment.REREAD)}, orchestrator="idle")
        self.act.refused.add("orchestrator")
        self.advance()
        self.act.refused.clear()
        self.advance()
        self.advance()
        self.assertEqual(len(self.act.made("send")), 2)

    def test_a_window_restarted_into_its_next_conversation_counts_as_answered(self) -> None:
        """R38: it starts that conversation's own prompt, never the update prompt, so it never
        answers; the update notes its status itself, and its list of background work stays."""
        self.bring({"web": ("repo", Treatment.TAKE_STOCK)}, web="shell")
        self.advance()
        update_agents.note(self.state_dir, "web", "stock", "sleep 900: the CI wait")
        self.act.started["web"] = Started.NEXT_CONVERSATION
        self.assertEqual(self.advance(), [])
        self.assertEqual(
            update_agents.read_note(self.state_dir, "web", "status"), "started its next conversation"
        )
        state = self.record()
        self.assertEqual(state.step, "done")
        self.assertIn("started its next conversation", state.windows["web"])
        [(_, outcome)] = update_agents.outcomes(self.state_dir, state)
        self.assertIn(str(update_agents.note_path(self.state_dir, "web", "stock")), outcome)

    def test_a_window_whose_agent_exited_is_relaunched(self) -> None:
        self.bring({"api": ("repo", Treatment.RESTART), "orchestrator": ("orchestrator", Treatment.REREAD)})
        self.statuses = {"api": "exited", "orchestrator": "exited"}
        self.advance()
        self.assertEqual([w for w, _ in self.act.made("relaunch")], ["api", "orchestrator"])
        self.assertIn("started again on the new version", self.act.made("relaunch")[0][1])

    def test_a_closed_window_is_no_longer_waited_for(self) -> None:
        self.bring({"api": ("repo", Treatment.RESTART), "web": ("repo", Treatment.WAIT)}, web="busy")
        self.assertEqual(self.advance(), ["web"])
        self.assertEqual(self.act.calls, [])
        self.assertEqual(self.record().windows["api"][-1], "closed")

    def test_no_windows_listed_changes_nothing(self) -> None:
        """An empty listing (the session's list could not be read) is no reason to drop a window."""
        self.bring({"api": ("repo", Treatment.RESTART)})
        self.assertEqual(self.advance(), ["api"])
        self.assertEqual((self.act.calls, self.record().windows["api"]), ([], ("repo", "restart")))

    def test_a_record_past_agents_is_left_alone(self) -> None:
        update_state.write(self.state_dir, update_state.advanced(bringing({}), "done"))
        self.assertEqual(self.advance(), [])
        self.assertEqual(self.act.calls, [])

    def test_what_bosun_saw_running_goes_with_the_stock(self) -> None:
        """The Stop hook's list of what the take-stock turn left running is passed back too."""
        self.bring({"web": ("repo", Treatment.TAKE_STOCK)}, web="shell")
        self.advance()
        update_agents.note(self.state_dir, "web", "stock", "the CI wait")
        signals = self.state_dir / "signals" / "web.jsonl"
        signals.parent.mkdir(parents=True)
        stop = {
            "at": "2026-11-07T14:01:00",
            "event": "Stop",
            "running": [{"type": "shell", "what": "sleep 900"}],
        }
        signals.write_text(json.dumps(stop) + "\n")
        self.advance()
        [(_, prompt)] = self.act.made("restart")
        self.assertIn("shell `sleep 900`", prompt)


class CommandsTest(unittest.TestCase):
    """The real actions: bosun commands of the new version, each in a session of its own."""

    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.state_dir = tmp / "state"
        self.said: list[str] = []
        self.commands = update_agents.Commands(tmp / "bbbbbbb22222", self.state_dir, self.said.append)

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


class FactsTest(unittest.TestCase):
    def test_statuses_and_kinds_of_the_open_windows(self) -> None:
        listed = [
            {"name": "orchestrator", "kind": "orchestrator", "window": True, "agent": {"status": "idle"}},
            {"name": "api", "kind": "session", "window": True, "agent": {"status": "busy"}},
            {"name": "plain", "kind": "extra", "window": True, "agent": None},
        ]
        with mock.patch.object(update_facts, "open_windows", return_value=listed):
            self.assertEqual(
                update_facts.statuses(Path("/b")), {"orchestrator": "idle", "api": "busy", "plain": "exited"}
            )
            self.assertEqual(
                update_facts.window_kinds(Path("/b")),
                {"orchestrator": "orchestrator", "api": "repo", "plain": "repo"},
            )


class NoteTest(Agents):
    def test_a_note_is_kept_by_window_and_kind(self) -> None:
        update_agents.note(self.state_dir, "api", "stock", "sleep 900")
        update_agents.note(self.state_dir, "api", "status", "waiting on CI")
        notes = self.state_dir / "update" / "notes"
        self.assertEqual(sorted(p.name for p in notes.iterdir()), ["api.status", "api.stock"])
        self.assertEqual(update_agents.read_note(self.state_dir, "api", "stock"), "sleep 900")
        self.assertIsNone(update_agents.read_note(self.state_dir, "web", "status"))

    def test_an_unknown_kind_or_a_name_that_is_a_path_is_refused(self) -> None:
        for window, kind in (
            ("api", "mood"),
            ("../api", "status"),
            ("a/b", "status"),
            ("..", "stock"),
            ("", "stock"),
        ):
            with self.assertRaises(update_agents.NoteRefused, msg=(window, kind)):
                update_agents.note(self.state_dir, window, kind, "x")
        self.assertFalse((self.state_dir / "update").exists())


class PromptTest(unittest.TestCase):
    def test_a_repo_session_catches_up_on_its_items(self) -> None:
        prompt = update_agents.prompt_for("api", "repo", bringing({}), "restarted on the new version", (), "")
        self.assertIn("bosun ledger in-flight api", prompt)
        self.assertNotIn("check-in", prompt)
        self.assertNotIn("Re-read", prompt)

    def test_the_orchestrator_catches_up_with_a_check_in(self) -> None:
        prompt = update_agents.prompt_for("orch", "orchestrator", bringing({}), "kept running", (POLICY,), "")
        self.assertIn("/bosun:chores check-in", prompt)
        self.assertIn("events queued during the update", prompt)
        self.assertIn("`bosun update note --window orch --status`", prompt)

    def test_the_stock_is_passed_back(self) -> None:
        prompt = update_agents.prompt_for("api", "repo", bringing({}), "restarted", (), "sleep 900")
        self.assertIn("sleep 900", prompt)
        self.assertLess(prompt.index("sleep 900"), prompt.index("Then continue what you were doing"))


class BringAlongTest(unittest.TestCase):
    """The update's own wait: the record at agents first, a turn every 5 s, the windows waited for
    said when they change, done once none is left; Ctrl-C leaves them to the check-in loop."""

    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.state_dir = tmp / "state"
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            mock.patch.dict(
                os.environ, {"BOSUN_STATE_DIR": str(self.state_dir), "BOSUN_CONFIG_DIR": str(tmp)}
            )
        )
        stack.enter_context(
            mock.patch.object(
                update_facts,
                "window_kinds",
                return_value={
                    "orchestrator": "orchestrator",
                    "api": "repo",
                    "web": "repo",
                    "checkin": "checkin",
                },
            )
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
            prompts=("orchestrator/policy.md", "orchestrator/learn.md"),
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
        )

    @contextlib.contextmanager
    def turns(self, *waits: list[str]) -> Iterator[mock.MagicMock]:
        """Each turn waits for the next of <waits>, dropping the others from the record."""
        left = list(waits)

        def turn(state_dir: Path, say: Any) -> list[str]:
            state = update_state.read(state_dir)
            assert state is not None
            self.at_turns.append((state.step, state.pending))
            waiting = left.pop(0)
            update_state.write(state_dir, update_state.advanced(state, "agents", pending=tuple(waiting)))
            return waiting

        with (
            mock.patch.object(update_agents, "turn", side_effect=turn) as turned,
            mock.patch.object(time, "sleep") as slept,
        ):
            yield slept
            self.turned = turned

    def bring_along(self) -> UpdateState:
        state = update_state.read(self.state_dir)
        assert state is not None
        return update_agents.bring_along(state, self.plan(), say=self.said.append)

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

    def test_ctrl_c_leaves_the_windows_to_the_check_in_loop(self) -> None:
        before = signal.getsignal(signal.SIGINT)
        with self.turns(["api"], ["api"]) as slept:
            slept.side_effect = lambda _: os.kill(os.getpid(), signal.SIGINT)
            stopped = self.bring_along()
        self.assertEqual((stopped.step, stopped.pending), ("agents", ("api",)))
        self.assertEqual(self.turned.call_count, 1)
        self.assertTrue(any("check-in loop" in line for line in self.said))
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    def test_notes_an_earlier_update_left_are_not_this_ones(self) -> None:
        update_agents.note(self.state_dir, "api", "status", "from the update before")
        with self.turns([]):
            self.bring_along()
        self.assertIsNone(update_agents.read_note(self.state_dir, "api", "status"))


class StateFieldsTest(unittest.TestCase):
    def test_what_was_done_to_each_window_is_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = bringing({"api": ("repo", Treatment.RESTART)}, reread=(POLICY,))
            state = update_state.advanced(state, "agents", windows={"api": ("repo", "restart", "restarted")})
            update_state.write(Path(tmp), state)
            self.assertEqual(update_state.read(Path(tmp)), state)

    def test_a_windows_field_of_the_wrong_shape_is_no_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for windows in (["api"], {"api": "restart"}, {"api": [1]}):
                data = {**dataclasses.asdict(bringing({})), "windows": windows}
                (Path(tmp) / "update.json").write_text(json.dumps(data))
                with self.assertRaises(update_state.RecordUnreadable, msg=str(windows)):
                    update_state.load(Path(tmp))


if __name__ == "__main__":
    unittest.main()
