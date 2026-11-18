"""lib/update_back_plan.py: what `bosun update --back` decides, pure (docs/design/update.md, "Going back, and
trying a branch"): which update it goes back over, what each part needs, what restoring loses, what stops it,
and its plan's and summary's text. Fictional data."""

from __future__ import annotations

import dataclasses
import json
import sys
import unittest
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import update_back_plan as ubp
import update_backups
import update_plan
import update_state
from update_back_plan import BackPart, ConfigPart, Since, StorePart
from update_state import UpdateState


OLDER = "aaaaaaa1111111111111111111111111111111111"
NEWER = "bbbbbbb2222222222222222222222222222222222"


def record(step: str, kind: str = "", **fields: Any) -> UpdateState:
    """An update from OLDER to NEWER (a back the other way round), at <step>."""
    ends = (NEWER, OLDER) if kind == update_state.BACK else (OLDER, NEWER)
    return UpdateState(
        id=fields.pop("id", "u1"),
        from_root=f"/Users/me/.local/share/bosun-versions/{ends[0][:12]}",
        to_root=f"/Users/me/.local/share/bosun-versions/{ends[1][:12]}",
        from_commit=ends[0],
        to_commit=ends[1],
        step=step,
        started_at=fields.pop("started_at", "2026-11-18T14:02:07+00:00"),
        hold_until=0,
        kind=kind,
        **fields,
    )


def ledger(**changes: Any) -> StorePart:
    part = StorePart("ledger", 2, 1, 1, 1, 1)
    return dataclasses.replace(part, **changes)


def knowledge(**changes: Any) -> StorePart:
    return dataclasses.replace(StorePart("knowledge", 1, 1, 1, 1, 1), **changes)


class GoneOverTest(unittest.TestCase):
    def test_a_finished_update_is_the_one_gone_back_over(self) -> None:
        for step in ("done", "agents", "checked", "released"):
            with self.subTest(step=step):
                last = record(step)
                self.assertEqual(ubp.gone_over(last), last)

    def test_one_that_ended_as_it_was_goes_back_over_the_one_before(self) -> None:
        finished = record("done", id="u1")
        for step in ("failed", "restored", "planned", "backed-up"):
            with self.subTest(step=step):
                failed = record(step, id="u2", previous=update_state.bare(finished))
                self.assertEqual(ubp.gone_over(failed), finished)

    def test_nothing_finished_is_nothing_to_go_back_over(self) -> None:
        for state in (None, record("failed"), record("restored")):
            with self.subTest(state=state):
                self.assertEqual(ubp.gone_over(state), ubp.NOTHING_FINISHED)

    def test_a_finished_back_is_never_gone_back_over(self) -> None:
        """One step back, never further: no swinging back and forth."""
        back = record("done", kind=update_state.BACK, started_at="2026-11-18T15:42:00+00:00")
        said = ubp.gone_over(back)
        self.assertEqual(
            said,
            "bosun already went back from bbbbbbb to aaaaaaa on 2026-11-18 15:42 UTC: one step back, never "
            "further; bosun update goes forward again",
        )
        self.assertEqual(ubp.gone_over(record("failed", previous=update_state.bare(back))), said)

    def test_a_back_that_failed_can_be_run_again(self) -> None:
        over = record("done")
        failed = record("restored", kind=update_state.BACK, previous=update_state.bare(over))
        self.assertEqual(ubp.gone_over(failed), over)


class StorePartTest(unittest.TestCase):
    def test_a_store_is_restored_where_its_format_or_rules_differ(self) -> None:
        self.assertTrue(ledger().restore)
        self.assertEqual(ledger().why(), "format 2 -> 1")
        self.assertTrue(knowledge(rules_now=2).restore)
        self.assertEqual(knowledge(rules_now=2).why(), "rules 2 -> 1")
        self.assertEqual(ledger(rules_now=3).why(), "format 2 -> 1; rules 3 -> 1")
        self.assertFalse(knowledge().restore)

    def test_a_store_none_of_them_has_or_the_older_one_reads_none_of_needs_nothing(self) -> None:
        self.assertFalse(ledger(format_now=0).restore)
        self.assertFalse(ledger(format_then=0).restore)

    def test_a_restore_without_its_backup_is_blocked(self) -> None:
        self.assertIn("a kg forget deletes every backup", ledger(backup_format=None).blocker())
        self.assertIn(
            "the ledger needs the backup that update took (format 2 -> 1)",
            ledger(backup_format=None).blocker(),
        )
        self.assertEqual(knowledge(backup_format=None).blocker(), "")  # kept: no backup needed

    def test_a_backup_of_another_format_than_the_older_version_reads_is_blocked(self) -> None:
        self.assertEqual(
            ledger(backup_format=3).blocker(),
            "that update's backup holds the ledger at format 3, not the 1 the older version reads",
        )
        self.assertEqual(ledger(backup_format=0).blocker(), "")  # no store then: restoring removes it

    def test_what_restoring_loses_is_said(self) -> None:
        self.assertEqual(ledger().loses(), "nothing written since: restoring loses nothing")
        items = tuple(f"a{n}" for n in range(10))
        said = ledger(written=Since(12, "2026-11-18 15:40 UTC", items)).loses()
        self.assertTrue(
            said.startswith("loses 12 changes written since (the last at 2026-11-18 15:40 UTC), to a0, a1"),
            said,
        )
        self.assertIn("a7 and 2 more, kept in the backup going back takes first", said)
        self.assertNotIn("to ", ledger(written=Since(1, "x")).loses().split("(")[0][6:])

    def test_its_lines_say_kept_or_restored(self) -> None:
        self.assertEqual(
            knowledge().plan_line(), "kept, with what was written since (format 1, rules 1 in both)"
        )
        self.assertTrue(
            ledger().plan_line().startswith("format 2 -> 1: restored from that update's backup; ")
        )
        self.assertEqual(
            ledger(written=Since(1, "t")).done_line(),
            "ledger: restored from the update's backup (format 2 -> 1); 1 change written since went with it, "
            "kept in the backup going back took",
        )


class SinceTest(unittest.TestCase):
    def test_the_changes_past_the_backup_s_position_are_counted_file_by_file(self) -> None:
        changes = [
            {"replica": "r1", "seq": 3, "hlc": "1763474400000.0000", "target": "a1"},
            {"replica": "r1", "seq": 4, "hlc": "1763474460000.0000", "target": "a2"},
            {"replica": "r1", "seq": 5, "hlc": "1763478000000.0000", "target": "a2"},
            {"local": True, "replica": "r1", "seq": 2, "hlc": "1763474460000.0000", "target": "a2"},
            {"local": True, "replica": "r1", "seq": 3, "hlc": "1763474520000.0000", "target": "a9"},
            {"replica": "r2", "seq": 1, "hlc": "1763474520000.0000", "target": "b1"},
        ]
        found = ubp.since(changes, {"r1": 3, "local": 2}, items=True)
        self.assertEqual(found.changes, 4)
        self.assertEqual(found.items, ("a2", "a9", "b1"))
        self.assertEqual(found.last, "2025-11-18 15:00 UTC")
        self.assertEqual(ubp.since(changes, {"r1": 3, "local": 2}, items=False).items, ())

    def test_nothing_past_it_is_nothing(self) -> None:
        self.assertEqual(ubp.since([{"replica": "r1", "seq": 1}], {"r1": 1}, items=True), Since())


class BackPartTest(unittest.TestCase):
    def back(self, **changes: Any) -> BackPart:
        part = BackPart("u1", "2026-11-18T14:02:07+00:00", "/b", (ledger(), knowledge()))
        return dataclasses.replace(part, **changes)

    def test_what_a_put_back_restores_is_the_parts_it_changes(self) -> None:
        self.assertEqual(self.back().restorable(), update_backups.STORE_PARTS["ledger"])
        both = self.back(config=ConfigPart(True, ("NEW is OLD again",)))
        self.assertEqual(both.restorable(), (*update_backups.STORE_PARTS["ledger"], ubp.CONFIG))
        self.assertTrue(both.restores)

    def test_with_nothing_to_restore_it_only_switches(self) -> None:
        kept = self.back(stores=(ledger(format_now=1), knowledge()), config=ConfigPart(True, (), ("left",)))
        self.assertFalse(kept.restores)
        self.assertEqual(kept.restorable(), ())

    def test_its_blockers(self) -> None:
        blocked = self.back(stores=(ledger(backup_format=None),), config=ConfigPart(True, backup_there=False))
        self.assertEqual(len(blocked.blockers()), 2)
        self.assertIn(ubp.CONFIG_GONE, blocked.blockers())

    def test_it_reads_back_from_the_record_and_one_of_another_shape_is_none(self) -> None:
        back = self.back(config=ConfigPart(True, ("a",), ("b",)), user_changes=("sessions.yml: x",))
        back = dataclasses.replace(back, stores=(ledger(written=Since(2, "t", ("a1",))), knowledge()))
        self.assertEqual(ubp.from_record(json.loads(json.dumps(back.as_record()))), back)
        for bad in (
            None,
            {},
            {"over": "u1"},
            {"over": "u1", "over_started": "t", "stores": [{"store": "x"}]},
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(ubp.from_record(bad))


def a_plan(**changes: Any) -> update_plan.Plan:
    plan = update_plan.Plan(
        from_commit=NEWER,
        to_commit=OLDER,
        commits=(),
        migrations=(),
        rules=(),
        keys_added=(),
        renames=(),
        prompts=("orchestrator/policy.md",),
        windows=(("api", update_plan.Treatment.RESTART),),
        jobs=(),
        console=False,
        handed=(),
        user_changes=(),
        blockers=(),
    )
    return dataclasses.replace(plan, **changes)


class TextTest(unittest.TestCase):
    def test_the_plan_says_what_it_goes_back_over_and_what_each_part_gets(self) -> None:
        back = BackPart(
            "u1",
            "2026-11-18T14:02:07+00:00",
            "/b",
            (ledger(written=Since(12, "2026-11-18 15:40 UTC", ("a12", "d3"))), knowledge()),
            ConfigPart(True, ("NEW is OLD again",), ("MINE was changed since the update: left as it is",)),
        )
        text = ubp.render(a_plan(unload=("com.bosun.morning.now",)), back)
        self.assertTrue(
            text.startswith("Go back bbbbbbb -> aaaaaaa, over the update of 2026-11-18 14:02 UTC\n")
        )
        self.assertIn("  ledger: format 2 -> 1: restored from that update's backup; loses 12 changes", text)
        self.assertIn("  knowledge: kept, with what was written since (format 1, rules 1 in both)", text)
        self.assertIn("  bosun.env: NEW is OLD again\n  bosun.env: MINE was changed since", text)
        self.assertIn("Windows:\n  api: restart\n", text)
        self.assertIn(
            "Jobs to unload (the version it goes to has none of them):\n  com.bosun.morning.now", text
        )
        self.assertTrue(text.endswith("Nothing changes until you say yes.\n"))
        blocked = ubp.render(a_plan(blockers=("x",)), back)
        self.assertTrue(blocked.endswith("Blocked:\n  x\n\nGoing back is blocked.\n"))

    def test_the_summary_says_what_each_store_restored_or_kept(self) -> None:
        back = BackPart("u1", "t", "/b", (ledger(), knowledge()))
        restored = ("ledger: restored from the update's backup (format 2 -> 1)",)
        self.assertEqual(
            ubp.summary_data(back, restored),
            (
                ("ledger", "restored from the update's backup (format 2 -> 1)"),
                ("knowledge", "kept, with what was written since"),
            ),
        )

    def test_a_time_is_said_in_utc_to_the_minute(self) -> None:
        self.assertEqual(ubp.when("2026-11-18T14:02:07+00:00"), "2026-11-18 14:02 UTC")
        self.assertEqual(ubp.when("2026-11-18T16:02:07+02:00"), "2026-11-18 14:02 UTC")
        self.assertEqual(ubp.when("not a time"), "not a time")


class SummaryPlaceTest(unittest.TestCase):
    def test_beside_its_own_backup_or_the_one_of_the_update_gone_back_over(self) -> None:
        self.assertEqual(ubp.summary_place(record("done", backup="/b/1")), (Path("/b/1"), "summary.md"))
        over = record("done", backup="/b/0")
        back = record("done", kind=update_state.BACK, previous=update_state.bare(over))
        self.assertEqual(ubp.summary_place(back), (Path("/b/0"), ubp.BACK_SUMMARY))
        self.assertIsNone(ubp.summary_place(record("done")))
        self.assertIsNone(ubp.summary_place(record("done", previous=update_state.bare(over))))  # an update


class OlderThanDataTest(unittest.TestCase):
    def check(self, theirs: dict[str, int], their_rules: dict[str, int]) -> list[str]:
        return ubp.older_than_data(
            "try-x (abc1234)",
            {"ledger": 2, "knowledge": 1},
            theirs,
            {"ledger": 2, "knowledge": 1},
            their_rules,
        )

    def test_a_target_at_the_data_s_formats_and_rules_reads_it(self) -> None:
        self.assertEqual(self.check({"ledger": 2, "knowledge": 1}, {"ledger": 2, "knowledge": 1}), [])
        self.assertEqual(self.check({"ledger": 3, "knowledge": 1}, {"ledger": 4, "knowledge": 1}), [])

    def test_an_older_format_is_refused_pointing_at_going_back(self) -> None:
        [said] = self.check({"ledger": 1, "knowledge": 1}, {"ledger": 2, "knowledge": 1})
        self.assertEqual(
            said,
            "try-x (abc1234) has the ledger at format 1, and the data is at format 2: going back past a "
            "migration or a rules change is bosun update --back's, to the version the last update came from",
        )

    def test_older_rules_are_refused_too(self) -> None:
        [said] = self.check({"ledger": 2, "knowledge": 1}, {"ledger": 1, "knowledge": 1})
        self.assertIn("reads the ledger under rules 1, and the data is under rules 2", said)

    def test_a_store_not_made_yet_or_a_source_that_says_no_format_is_not_judged(self) -> None:
        self.assertEqual(
            ubp.older_than_data("t", {"ledger": 0}, {"ledger": 0}, {"ledger": 2}, {"ledger": 1}), []
        )
        self.assertEqual(self.check({"ledger": 0, "knowledge": 0}, {"ledger": 0, "knowledge": 0}), [])


if __name__ == "__main__":
    unittest.main()
