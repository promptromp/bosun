"""lib/update_plan.py: what an update would do, and what each window gets. Fictional data."""

from __future__ import annotations

import sys
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import update_plan as up
from update_plan import Treatment as T


KINDS = ("orchestrator", "repo", "checkin")
STATUSES = ("idle", "shell", "busy", "waiting", "exited", "unknown")
POLICY = "orchestrator/policy.md"
REVIEW = "orchestrator/tasks/review.md"
BASE_PROMPTS = {
    POLICY: "a1",
    REVIEW: "b1",
    "orchestrator/tasks/fix.md": "c1",
    "orchestrator/learn.md": "d1",
}


def facts(
    commit: str = "aaa",
    formats: Mapping[str, int] | None = None,
    rules: Mapping[str, int] | None = None,
    keys: Sequence[str] = ("A",),
    prompts: Mapping[str, str] | None = None,
) -> up.VersionFacts:
    return up.VersionFacts(
        commit,
        {"ledger": 1} if formats is None else formats,
        {"ledger": 1} if rules is None else rules,
        frozenset(keys),
        BASE_PROMPTS if prompts is None else prompts,
    )


def no_steps(store: str, found: int, target: int) -> list[tuple[int, int]]:
    return []


def plan(old: up.VersionFacts | None = None, new: up.VersionFacts | None = None, **kw: Any) -> up.Plan:
    args: dict[str, Any] = {
        "commits": [],
        "windows": [],
        "jobs": [],
        "renames": [],
        "user_changes": [],
        "in_flight": [],
        "missing": [],
        "deletion_cut_short": False,
        "console": False,
        "steps": no_steps,
    }
    args.update(kw)
    return up.make_plan(old or facts(), new or facts("bbb"), **args)


def expected(kind: str, status: str, changed: bool, reread: bool) -> T:
    if kind == "checkin":
        return T.RESTART_LOOP
    if not changed:
        return T.REREAD if reread and kind == "orchestrator" else T.LEAVE
    return {
        "idle": T.RESTART,
        "shell": T.TAKE_STOCK,
        "busy": T.WAIT,
        "waiting": T.ASK_FIRST,
        "unknown": T.ASK_FIRST,
        "exited": T.RELAUNCH,
    }[status]


class TestTreatment(unittest.TestCase):
    def test_every_pairing_has_one_treatment(self) -> None:
        n = 0
        for kind in KINDS:
            for status in STATUSES:
                for changed in (False, True):
                    for reread in (False, True):
                        with self.subTest(kind=kind, status=status, changed=changed, reread=reread):
                            window = up.WindowFacts("w", kind, status, changed)
                            self.assertEqual(
                                up.treatment(window, reread), expected(kind, status, changed, reread)
                            )
                        n += 1
        self.assertEqual(n, 72)

    def test_only_the_orchestrator_rereads(self) -> None:
        window = up.WindowFacts("acme-api", "repo", "idle", False)
        self.assertEqual(up.treatment(window, True), T.LEAVE)


class TestMakePlan(unittest.TestCase):
    def test_prompts_changed_are_named_and_counted(self) -> None:
        new = facts("bbb", prompts={**BASE_PROMPTS, POLICY: "a2", REVIEW: "b2"})
        self.assertEqual(plan(new=new).prompts, (POLICY, REVIEW))

    def test_a_changed_prompt_makes_the_orchestrator_reread(self) -> None:
        new = facts("bbb", prompts={**BASE_PROMPTS, POLICY: "a2"})
        windows = [up.WindowFacts("orchestrator", "orchestrator", "idle", False)]
        self.assertEqual(plan(new=new, windows=windows).windows, (("orchestrator", T.REREAD),))
        self.assertEqual(plan(new=new, windows=windows).kinds, (("orchestrator", "orchestrator"),))

    def test_migrations_between_the_formats(self) -> None:
        got = plan(new=facts("bbb", formats={"ledger": 2}), steps=lambda s, a, b: [(1, 2)])
        self.assertEqual(got.migrations, (("ledger", 1, 2),))

    def test_rules_changes_are_listed(self) -> None:
        got = plan(new=facts("bbb", rules={"ledger": 3}))
        self.assertEqual(got.rules, (("ledger", 1, 3),))

    def test_handed_items_of_a_changed_task_type(self) -> None:
        new = facts("bbb", prompts={**BASE_PROMPTS, REVIEW: "b2"})
        got = plan(new=new, in_flight=[("task:1", "review"), ("task:2", "fix")])
        self.assertEqual(got.handed, ("task:1",))

    def test_a_newer_store_blocks(self) -> None:
        got = plan(old=facts(formats={"ledger": 3}), new=facts("bbb", formats={"ledger": 2}))
        self.assertEqual(len(got.blockers), 1)
        self.assertIn("going back is not supported", got.blockers[0])

    def test_no_path_blocks_with_the_message(self) -> None:
        def steps(store: str, found: int, target: int) -> list[tuple[int, int]]:
            raise ValueError("no migration from ledger 1 to 2")

        got = plan(new=facts("bbb", formats={"ledger": 2}), steps=steps)
        self.assertEqual(got.blockers, ("no migration from ledger 1 to 2",))

    def test_missing_things_block(self) -> None:
        self.assertEqual(plan(missing=["tmux is missing"]).blockers, ("tmux is missing",))

    def test_a_deletion_cut_short_blocks(self) -> None:
        got = plan(deletion_cut_short=True)
        self.assertEqual(got.blockers, (up.CUT_SHORT,))

    def test_new_keys_are_listed(self) -> None:
        got = plan(old=facts(keys=("A", "B")), new=facts("bbb", keys=("A", "B", "Z", "C")))
        self.assertEqual(got.keys_added, ("C", "Z"))

    def test_nothing_changed_gives_an_empty_plan(self) -> None:
        got = plan(new=facts("bbb"))
        self.assertEqual((got.migrations, got.rules, got.prompts, got.blockers), ((), (), (), ()))


class TestRender(unittest.TestCase):
    def test_the_render_lists_every_part(self) -> None:
        new = facts(
            "bbb",
            formats={"ledger": 2},
            rules={"ledger": 2},
            keys=("A", "NEW_KEY"),
            prompts={**BASE_PROMPTS, POLICY: "a2"},
        )
        got = plan(
            new=new,
            commits=["c1 first", "c2 second"],
            windows=[up.WindowFacts("acme-api", "repo", "busy", True)],
            jobs=["com.acme.bosun.watchdog"],
            renames=[("OLD", "NEW"), ("GONE", "")],
            user_changes=[("sessions.yml", "rename the key")],
            in_flight=[],
            console=True,
            steps=lambda s, a, b: [(1, 2)],
        )
        text = up.render_plan(got)
        for part in (
            "Commits (2)",
            "ledger: format 1 -> 2",
            "ledger: version 1 -> 2",
            "NEW_KEY",
            "OLD is now NEW",
            "GONE is removed",
            "Prompts changed (1)",
            POLICY,
            "acme-api: wait until idle",
            "com.acme.bosun.watchdog",
            "Console",
            "sessions.yml: rename the key",
        ):
            self.assertIn(part, text)
        self.assertTrue(text.rstrip().endswith("Nothing changes until you say yes."))

    def test_a_blocked_plan_does_not_promise(self) -> None:
        text = up.render_plan(plan(deletion_cut_short=True))
        self.assertIn(up.CUT_SHORT, text)
        self.assertNotIn("Nothing changes until you say yes.", text)


if __name__ == "__main__":
    unittest.main()
