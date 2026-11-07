"""lib/update_agents.py: what each window gets once an update has switched (docs/design/update.md,
"Bringing the agents along"): decided from its status now, each window acted on once, the prompts,
and what came of each. A fake Actions records what would be done and keeps the notes in memory, and
a statuses mapping the test changes stands in for the windows; only the record is a file. The I/O
is tests/unit/test_update_windows.py, the end to end the smoke suite's "bosun update" section.
Fictional windows."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import update_agents
import update_state
from update_agents import Started
from update_plan import Treatment
from update_state import UpdateState


FROM = "aaaaaaa1111111111111111111111111111111111"
TO = "bbbbbbb2222222222222222222222222222222222"
POLICY = "/Users/me/.local/share/bosun/orchestrator/policy.md"


class MemoryNotes:
    """The windows' notes, in memory."""

    def __init__(self) -> None:
        self.kept: dict[tuple[str, str], str] = {}

    def read(self, window: str, kind: str) -> str | None:
        return self.kept.get((window, kind))

    def save(self, window: str, kind: str, text: str) -> None:
        self.kept[(window, kind)] = text

    def where(self, window: str, kind: str) -> str:
        return f"/Users/me/.local/state/bosun/update/notes/{window}.{kind}"


class FakeActions:
    """What advance asks for, in order. send delivers unless <refused>; restart and relaunch answer
    with <started> for the window (Started.YES unless a test says otherwise); <running> is what the
    Stop hook saw each window's last turn leave running; <elsewhere>, the windows on another
    conversation than their next launch takes up."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.refused: set[str] = set()
        self.started: dict[str, Started] = {}
        self.running: dict[str, str] = {}
        self.elsewhere: set[str] = set()
        self.notes = MemoryNotes()

    def send(self, window: str, text: str) -> bool:
        self.calls.append(("send", window, text))
        return window not in self.refused

    def restart(self, window: str, prompt: str) -> Started:
        self.calls.append(("restart", window, prompt))
        return self.started.get(window, Started.YES)

    def relaunch(self, window: str, prompt: str) -> Started:
        self.calls.append(("relaunch", window, prompt))
        return self.started.get(window, Started.YES)

    def left_running(self, window: str) -> str:
        return self.running.get(window, "")

    def on_another_conversation(self, window: str) -> bool:
        return window in self.elsewhere

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
        self.act.notes.save(window, "status", text)

    def stock(self, window: str, text: str) -> None:
        self.act.notes.save(window, "stock", text)


class AdvanceTest(Agents):
    def test_an_idle_window_is_restarted_with_the_update_prompt(self) -> None:
        self.bring({"api": ("repo", Treatment.RESTART)}, api="idle")
        self.assertEqual(self.advance(), ["api"])
        [(window, prompt)] = self.act.made("restart")
        self.assertEqual(window, "api")
        self.assertIn("bosun was updated from aaaaaaa to bbbbbbb; this session was restarted", prompt)
        self.assertIn("bosun ledger in-flight api", prompt)
        self.assertIn("`bosun update note --window api --status`", prompt)
        self.assertNotIn("background work", prompt)
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
        self.advance()  # its list is not there yet, and its background work runs: still only asked
        self.assertEqual(len(self.act.calls), 1)
        self.stock("web", "sleep 900: the CI wait; report CI when it ends")
        self.assertEqual(self.advance(), ["web"])
        [(window, prompt)] = self.act.made("restart")
        self.assertEqual(window, "web")
        self.assertIn("Before the restart you listed this background work", prompt)
        self.assertIn("sleep 900: the CI wait; report CI when it ends", prompt)
        self.assertEqual(
            self.record().windows["web"],
            ("repo", "take stock, then restart", "asked to take stock", "restarted"),
        )

    def test_an_idle_window_asked_to_take_stock_is_restarted_without_its_list(self) -> None:
        """Its background work ended before it read the prompt: nothing runs, so a restart ends nothing
        and it is not waited for; what the Stop hook saw running goes with the restart."""
        self.bring({"web": ("repo", Treatment.TAKE_STOCK)}, web="shell")
        self.advance()
        self.statuses["web"] = "idle"
        self.act.running["web"] = "shell `sleep 900`"
        self.advance()
        [(_, prompt)] = self.act.made("restart")
        self.assertIn("bosun saw this background work still running", prompt)
        self.assertIn("shell `sleep 900`", prompt)
        self.advance()
        self.assertEqual(len(self.act.calls), 2)  # asked once, restarted once

    def test_nothing_running_is_no_work_to_start_again(self) -> None:
        self.bring({"web": ("repo", Treatment.TAKE_STOCK)}, web="shell")
        self.advance()
        self.stock("web", "Nothing running.")
        self.advance()
        [(_, prompt)] = self.act.made("restart")
        self.assertNotIn("background work", prompt)
        self.assertNotIn("Nothing running", prompt)

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

    def test_an_orchestrator_on_yesterdays_conversation_starts_todays_instead(self) -> None:
        """A re-read into yesterday's conversation is wasted (its check-in is refused there, and the
        next tick restarts it anyway): refresh restarts it as a new day does, which counts as answered."""
        self.bring({"orchestrator": ("orchestrator", Treatment.REREAD)}, orchestrator="idle")
        self.act.elsewhere.add("orchestrator")
        self.act.started["orchestrator"] = Started.NEXT_CONVERSATION
        self.assertEqual(self.advance(), [])
        self.assertEqual([what for what, _, _ in self.act.calls], ["restart"])
        self.assertIn("started its next conversation", self.record().windows["orchestrator"])

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
        answers; the update notes its status itself, and names where its list of background work is
        kept. Without a list, its outcome says so once."""
        self.bring(
            {"web": ("repo", Treatment.TAKE_STOCK), "api": ("repo", Treatment.RESTART)},
            web="shell",
            api="idle",
        )
        self.act.started["api"] = Started.NEXT_CONVERSATION
        self.advance()
        self.stock("web", "sleep 900: the CI wait")
        self.act.started["web"] = Started.NEXT_CONVERSATION
        self.assertEqual(self.advance(), [])
        self.assertEqual(self.act.notes.read("web", "status"), "started its next conversation")
        state = self.record()
        self.assertEqual(state.step, "done")
        self.assertIn("started its next conversation", state.windows["web"])
        outcome = dict(update_agents.outcomes(state, self.act.notes))
        self.assertIn(self.act.notes.where("web", "stock"), outcome["web"])
        self.assertEqual(outcome["api"], "started its next conversation")

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
        self.stock("web", "the CI wait")
        self.act.running["web"] = "shell `sleep 900`"
        self.advance()
        [(_, prompt)] = self.act.made("restart")
        self.assertIn("the CI wait\n(What bosun saw still running then: shell `sleep 900`.)", prompt)


class PlanWindowsTest(unittest.TestCase):
    def test_the_windows_to_bring_along_take_their_kind_from_the_plan(self) -> None:
        windows = (("orch", Treatment.REREAD), ("api", Treatment.WAIT), ("web", Treatment.LEAVE))
        kinds = {"orch": "orchestrator", "api": "repo", "web": "repo"}
        self.assertEqual(
            update_agents.to_bring_along(windows, kinds),
            {"orch": ("orchestrator", "re-read"), "api": ("repo", "wait until idle")},
        )

    def test_a_kind_the_plan_does_not_name_is_never_guessed(self) -> None:
        with self.assertRaisesRegex(ValueError, "names no kind for orch"):
            update_agents.to_bring_along((("orch", Treatment.REREAD),), {})


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
        stock = update_agents.stock_lines("sleep 900", "")
        prompt = update_agents.prompt_for("api", "repo", bringing({}), "restarted", (), stock)
        self.assertIn("sleep 900", prompt)
        self.assertLess(prompt.index("sleep 900"), prompt.index("Then continue what you were doing"))

    def test_the_take_stock_prompt_says_what_to_save_when_nothing_runs(self) -> None:
        prompt = update_agents.take_stock_prompt("web", bringing({}))
        self.assertIn('if nothing runs any more, save "nothing running"', prompt)


if __name__ == "__main__":
    unittest.main()
