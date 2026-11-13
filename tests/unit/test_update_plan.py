"""lib/update_plan.py: what an update would do, and what each window gets. Fictional data."""

from __future__ import annotations

import dataclasses
import json
import sys
import unittest
import unittest.mock
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import migrations
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

    def test_a_window_bosun_did_not_start_is_left_as_it_is_whatever_it_does(self) -> None:
        """A shell or a Claude started by hand in bosun's session, outside the window list and
        with no launch recorded: never relaunched or restarted, and the plan says why."""
        for status in STATUSES:
            for reread in (False, True):
                window = up.WindowFacts("mine", "repo", status, False, started_by_bosun=False)
                self.assertEqual(up.treatment(window, reread), T.NOT_STARTED_BY_BOSUN, (status, reread))
        windows = [up.WindowFacts("mine", "repo", "exited", False, started_by_bosun=False)]
        self.assertIn("  mine: not started by bosun: left as it is\n", up.render_plan(plan(windows=windows)))


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
        got = plan(new=new, in_flight=[("task:1", ("review",)), ("task:2", ("fix",))])
        self.assertEqual(got.handed, ("task:1",))

    def test_an_item_handed_several_types_is_handed_when_any_of_them_changed(self) -> None:
        """`bosun delegate review,fix` hands both types' text."""
        in_flight = [("task:1", ("review", "fix")), ("task:3", ("review",))]
        self.assertEqual(up._handed(in_flight, ("orchestrator/tasks/fix.md",)), ("task:1",))
        self.assertEqual(up._handed(in_flight, (REVIEW,)), ("task:1", "task:3"))

    def test_a_change_to_the_rules_every_task_gets_touches_every_item_handed_out(self) -> None:
        """`orchestrator/tasks/_common.md` is in every task, also one of the site's own types only
        (a site's _common.md adds to it, never replaces it)."""
        in_flight = [("task:1", ("review", "fix")), ("task:3", ("review",)), ("task:4", ())]
        self.assertEqual(up._handed(in_flight, (up.COMMON, REVIEW)), ("task:1", "task:3", "task:4"))
        self.assertEqual(up._handed(in_flight, (POLICY,)), ())

    def test_a_prompt_file_only_one_version_has_counts_as_changed(self) -> None:
        added = {**BASE_PROMPTS, "orchestrator/tasks/triage.md": "e1"}
        self.assertEqual(plan(new=facts("bbb", prompts=added)).prompts, ("orchestrator/tasks/triage.md",))
        removed = {path: digest for path, digest in BASE_PROMPTS.items() if path != REVIEW}
        got = plan(
            new=facts("bbb", prompts=removed), windows=[up.WindowFacts("o", "orchestrator", "idle", False)]
        )
        self.assertEqual(got.prompts, (REVIEW,))
        self.assertEqual(got.windows, (("o", T.REREAD),))

    def test_a_store_only_one_version_has(self) -> None:
        """One the new version adds is made in its format: no migration, no blocker. One only the old
        version had is the new version's no longer: nothing is done to it."""
        got = plan(old=facts(formats={"ledger": 1}), new=facts("bbb", formats={"ledger": 1, "outbox": 2}))
        self.assertEqual((got.migrations, got.blockers), ((), ()))
        self.assertEqual(got.formats, (("ledger", 1), ("outbox", 2)))
        got = plan(old=facts(formats={"ledger": 1, "outbox": 2}), new=facts("bbb", formats={"ledger": 1}))
        self.assertEqual((got.migrations, got.blockers, got.formats), ((), (), (("ledger", 1),)))

    def test_a_rules_stream_only_one_version_has(self) -> None:
        """One the new version adds goes from 0; one only the old version had is not listed."""
        got = plan(old=facts(rules={"ledger": 1}), new=facts("bbb", rules={"ledger": 1, "knowledge": 2}))
        self.assertEqual(got.rules, (("knowledge", 0, 2),))
        got = plan(old=facts(rules={"ledger": 1, "knowledge": 2}), new=facts("bbb", rules={"ledger": 1}))
        self.assertEqual(got.rules, ())

    def test_a_lower_rules_version_is_listed_and_blocks_nothing(self) -> None:
        """Changes recorded under a higher rules version are applied without being checked again
        (docs/design/sync.md), so a version with a lower one is listed; only a newer format blocks."""
        got = plan(old=facts(rules={"ledger": 3}), new=facts("bbb", rules={"ledger": 2}))
        self.assertEqual((got.rules, got.blockers), ((("ledger", 3, 2),), ()))

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

    def test_each_store_s_format_on_disk_is_kept_in_the_new_version_s_order(self) -> None:
        """For the summary's Data lines: a store not made yet is 0, one the new version adds is at its
        format (it is made in it)."""
        got = plan(
            old=facts(formats={"knowledge": 0, "ledger": 1}),
            new=facts("bbb", formats={"ledger": 2, "knowledge": 1, "outbox": 1}),
            steps=lambda s, a, b: [(1, 2)],
        )
        self.assertEqual(got.formats, (("ledger", 1), ("knowledge", 0), ("outbox", 1)))


class TestRecord(unittest.TestCase):
    """The plan as the update's record keeps it (`plan` in update.json), for the summary of an update
    carried on by a later run, or finished by the check-in loop."""

    def full(self) -> up.Plan:
        return plan(
            new=facts(
                "bbb",
                formats={"ledger": 2},
                rules={"ledger": 2},
                keys=("A", "B"),
                prompts={**BASE_PROMPTS, POLICY: "a2"},
            ),
            commits=["c1 first"],
            windows=[
                up.WindowFacts("acme-api", "repo", "busy", True),
                up.WindowFacts("o", "orchestrator", "idle", False),
            ],
            jobs=["com.acme.bosun.watchdog"],
            renames=[("OLD", "NEW")],
            user_changes=[("sessions.yml", "rename the key")],
            in_flight=[("task:1", "review")],
            steps=lambda s, a, b: [(1, 2)],
        )

    def test_a_plan_reads_back_as_it_was(self) -> None:
        full = dataclasses.replace(
            self.full(), kept=(("BOSUN_ORCHESTRATOR_CONNECTORS", "all"),), unwritten=("BOSUN_X",)
        )
        kept = json.loads(json.dumps(up.as_record(full)))
        self.assertEqual(up.as_record(full), kept)  # JSON's types only, so a record read back is equal
        self.assertEqual(up.from_record(kept), full)

    def test_a_record_from_before_the_console_job_reads_as_none(self) -> None:
        older = {key: value for key, value in up.as_record(self.full()).items() if key != "console_job"}
        self.assertFalse(up.from_record(older).console_job)
        job = dataclasses.replace(self.full(), console_job=True)
        self.assertTrue(up.from_record(json.loads(json.dumps(up.as_record(job)))).console_job)

    def test_a_record_with_no_plan_reads_as_one_with_nothing_in_it(self) -> None:
        empty = up.from_record({})
        self.assertEqual((empty.commits, empty.formats, empty.windows, empty.console), ((), (), (), False))

    def test_a_record_from_before_kept_keys_reads_as_none_written(self) -> None:
        older = {
            key: value for key, value in up.as_record(self.full()).items() if key not in ("kept", "unwritten")
        }
        self.assertEqual(up.from_record(older), self.full())
        self.assertEqual((up.from_record(older).kept, up.from_record(older).unwritten), ((), ()))

    def test_a_part_of_another_shape_is_left_empty_the_rest_read(self) -> None:
        """A newer bosun's shape of one part, or one this code cannot read, costs only that part."""
        kept = {
            **up.as_record(self.full()),
            "prompts": "orchestrator/policy.md",
            "windows": [["o", "sideways"]],
        }
        got = up.from_record(kept)
        self.assertEqual((got.prompts, got.windows), ((), ()))
        self.assertEqual(got.keys_added, ("B",))
        self.assertEqual(got.migrations, (("ledger", 1, 2),))


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

    def test_a_console_job_is_restarted_by_the_update_and_a_console_by_hand_is_yours_to_restart(self) -> None:
        """The console line: bosun's job (loaded, and wanted) is restarted after the switch, so it serves
        the new version; a console started by hand is not, and the line says to restart it."""
        job = up.render_plan(plan(console=True, console_job=True))
        self.assertIn("Console:\n  bosun's background job: restarted after the update", job)
        self.assertNotIn("restart it after the update", job)
        by_hand = up.render_plan(plan(console=True))
        self.assertIn("running: restart it after the update", by_hand)
        self.assertNotIn("Console:", up.render_plan(plan()))
        self.assertTrue(plan(console_job=True).console_job)  # a job to restart, though none runs now
        self.assertIn("Console:", up.render_plan(plan(console_job=True)))

    def test_a_kept_value_and_the_added_keys_notes_are_shown(self) -> None:
        """So the person saying yes sees that bosun.env changes, and what each added key means. A kept
        key's note only where its value is written (plan.kept)."""
        added = (
            migrations.AddedKey("BOSUN_KEEPS", "all", "keeps what it did", "had all, now none"),
            migrations.AddedKey("BOSUN_FRESH", None, "starts at its default"),
        )
        got = dataclasses.replace(
            plan(new=facts("bbb", keys=("A", "BOSUN_KEEPS", "BOSUN_FRESH", "BOSUN_OTHER"))),
            kept=(("BOSUN_KEEPS", "all"),),
        )
        with unittest.mock.patch.object(migrations, "CONFIG_ADDED", added):
            text = up.render_plan(got)
            unwritten = up.render_plan(dataclasses.replace(got, kept=()))
            no_env = up.render_plan(dataclasses.replace(got, kept=(), unwritten=("BOSUN_KEEPS",)))
        self.assertIn(
            "New config keys:\n  BOSUN_FRESH: starts at its default\n  BOSUN_KEEPS: keeps what it did\n"
            '  BOSUN_OTHER\n\nWritten to bosun.env, to keep what this install did:\n  BOSUN_KEEPS="all"\n',
            text,
        )
        self.assertIn("\n  BOSUN_KEEPS\n", unwritten)
        self.assertNotIn("Written to bosun.env", unwritten)
        self.assertIn("\n  BOSUN_KEEPS: had all, now none\n", no_env)  # no bosun.env to write it to
        self.assertNotIn("Written to bosun.env", no_env)

    def test_a_blocked_plan_does_not_promise(self) -> None:
        text = up.render_plan(plan(deletion_cut_short=True))
        self.assertIn(up.CUT_SHORT, text)
        self.assertNotIn("Nothing changes until you say yes.", text)


if __name__ == "__main__":
    unittest.main()
