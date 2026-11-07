"""lib/migrations.py: the one registry that moves each store, and the config's keys, from an older
format to the code's (docs/design/update.md, "Migrations"). Fictional data."""

from __future__ import annotations

import fcntl
import json
import shutil
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import knowledge
import knowledge_store
import ledger
import migrations
import validation


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "migrations"
NOW = datetime(2026, 11, 6, 9, 30, 5, tzinfo=UTC)
RENAMES = (("OLD", "NEW"), ("GONE", ""))


def step(store: str, was: int, to: int) -> migrations.Migration:
    return migrations.Migration(store, was, to, lambda _ctx: 0, f"{store} {was} -> {to}")


def add_x(ctx: migrations.Context) -> int:
    """A test migration of the ledger from format 1 to 2: "x": 1 on each item. Run again, it changes
    nothing."""
    path = ctx.state_dir / "ledger.json"
    data = json.loads(path.read_text())
    changed = [item for item in data["items"].values() if item.get("x") != 1]
    for item in changed:
        item["x"] = 1
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    return len(changed)


ADD_X = migrations.Migration("ledger", 1, 2, add_x, "{n} items migrated")


def rebuild_from_journal(ctx: migrations.Context) -> int:
    """A test migration of a ledger that has a journal: ledger.json rebuilt from the journal (in the
    code's format) and stamped, as a write saves it, never edited in place."""
    journal = ledger.journal_at(ctx.state_dir / "journal" / ledger.LEDGER_STREAM)
    position = journal.position()
    made = ledger.rebuilt(journal, position)
    ledger.stamp(made, position)
    (ctx.state_dir / "ledger.json").write_text(json.dumps(made, indent=2, sort_keys=True))
    return len(made["items"])


REBUILD = migrations.Migration("ledger", 1, 2, rebuild_from_journal, "{n} items rebuilt")


def within_a_second[T](call: Callable[[], T]) -> T:
    """<call>'s result, which must come within a second (on a thread of its own, so that a call
    that waits for ever fails the test rather than hanging it)."""
    result: list[T] = []
    thread = threading.Thread(target=lambda: result.append(call()), daemon=True)
    thread.start()
    thread.join(1.0)
    if thread.is_alive() or not result:
        raise AssertionError("it did not return within a second")
    return result[0]


class MigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.ctx = migrations.Context(state_dir=base / "state", config_dir=base / "config")
        self.ctx.state_dir.mkdir()
        self.ctx.config_dir.mkdir()

    def ledger_file(self) -> Path:
        return self.ctx.state_dir / "ledger.json"

    def from_fixture(self) -> None:
        shutil.copy(FIXTURES / "ledger-1.json", self.ledger_file())

    def make_journaled_ledger(self) -> None:
        """A ledger with a journal, made by real writes."""
        ledger.configure(self.ctx.state_dir)
        for number in (12, 14):
            args = ledger.build_parser().parse_args(
                ["upsert", f"review:acme/exporter#{number}", "--kind", "review-request", "--title", "T"]
            )
            args.func(args)

    def make_knowledge(self) -> knowledge_store.Paths:
        paths = self.ctx.knowledge
        knowledge.configure(paths.root, git=False, state_dir=str(self.ctx.state_dir))
        args = knowledge.build_parser().parse_args(
            ["add-entity", "repo/invoice-exporter", "--name", "invoice exporter"]
        )
        args.func(args)
        return paths


class TestPath(MigrationTest):
    def test_every_store_has_a_path_to_its_format(self) -> None:
        for store, target in migrations.FORMATS.items():
            with self.subTest(store=store):
                at = 1
                for each in migrations.path(store, 1, target):
                    self.assertEqual((each.store, each.from_version), (store, at))
                    at = each.to_version
                self.assertEqual(at, target)

    def test_a_gap_names_the_missing_step(self) -> None:
        registry = [step("ledger", 1, 2), step("ledger", 3, 4)]
        with self.assertRaisesRegex(migrations.MigrationError, "2 -> 3"):
            migrations.path("ledger", 1, 4, registry)
        self.assertEqual(
            [(s.from_version, s.to_version) for s in migrations.path("ledger", 3, 4, registry)], [(3, 4)]
        )

    def test_going_back_is_refused(self) -> None:
        with self.assertRaisesRegex(migrations.MigrationError, "going back is not supported"):
            migrations.path("ledger", 3, 2)

    def test_a_refusal_is_a_value_error(self) -> None:
        """What bosun update's plan catches."""
        self.assertTrue(issubclass(migrations.MigrationError, ValueError))

    def test_a_store_not_made_yet_needs_no_migration(self) -> None:
        """Format 0 is no store yet: it is made in the code's format."""
        self.assertEqual(migrations.path("ledger", 0, 4, [step("ledger", 1, 2)]), [])

    def test_a_registry_that_does_not_go_forward_is_refused(self) -> None:
        for registry in ([step("ledger", 1, 1)], [step("ledger", 1, 3)], [step("ledger", 1, 2)] * 2):
            with self.subTest(registry=registry), self.assertRaises(migrations.MigrationError):
                migrations.path("ledger", 1, 2, registry)

    def test_the_registry_holds_together(self) -> None:
        """Every migration is of a store this bosun has, goes forward to its format at most, and has a
        summary that takes {n}; FORMATS and the stores read and recorded are the same."""
        migrations.check_registry()
        for each in migrations.REGISTRY:
            with self.subTest(migration=each):
                self.assertIn(each.store, migrations.FORMATS)
                self.assertLess(each.from_version, each.to_version)
                self.assertLessEqual(each.to_version, migrations.FORMATS[each.store])
                each.summary.format(n=1)

    def test_a_registry_that_does_not_hold_together_is_refused(self) -> None:
        def made(store: str, was: int, to: int, summary: str = "{n} moved") -> migrations.Migration:
            return migrations.Migration(store, was, to, lambda _ctx: 0, summary)

        with mock.patch.dict(migrations.FORMATS, {"ledger": 2}):
            for bad, why in (
                (made("tickets", 1, 2), "not a store"),
                (made("ledger", 2, 3), "at most"),
                (made("ledger", 0, 1), "forward"),
                (made("ledger", 2, 2), "forward"),
                (made("ledger", 1, 2, "{count} moved"), "summary"),
                (made("ledger", 1, 2, "{n} moved {"), "summary"),
                (made("ledger", 1, 2, "{0} moved"), "summary"),
                (made("ledger", 1, 2, "{n[0]} moved"), "summary"),
                (made("ledger", 1, 2, "{n.x} moved"), "summary"),
            ):
                with self.subTest(why=why), self.assertRaisesRegex(migrations.MigrationError, why):
                    migrations.check_registry([bad])
        with (
            mock.patch.dict(migrations.FORMATS, {"tickets": 1}),
            self.assertRaisesRegex(migrations.MigrationError, "FORMATS"),
        ):
            migrations.check_registry()

    def test_the_tables_are_read_when_called(self) -> None:
        with (
            mock.patch.object(migrations, "REGISTRY", (step("ledger", 1, 2),)),
            mock.patch.dict(migrations.FORMATS, {"ledger": 2}),
        ):
            self.assertEqual(
                [(s.from_version, s.to_version) for s in migrations.path("ledger", 1, 2)], [(1, 2)]
            )
        self.from_fixture()
        with (
            mock.patch.object(migrations, "REGISTRY", (ADD_X,)),
            mock.patch.dict(migrations.FORMATS, {"ledger": 2}),
        ):
            self.assertEqual(
                migrations.run(self.ctx), [migrations.Applied("ledger", 1, 2, "2 items migrated")]
            )


class TestRun(MigrationTest):
    def test_a_migration_runs_once(self) -> None:
        self.from_fixture()
        with mock.patch.dict(migrations.FORMATS, {"ledger": 2}):
            first = migrations.run(self.ctx, [ADD_X])
            migrated = self.ledger_file().read_text()
            second = migrations.run(self.ctx, [ADD_X])
        self.assertEqual(first, [migrations.Applied("ledger", 1, 2, "2 items migrated")])
        self.assertEqual(second, [])
        self.assertEqual(self.ledger_file().read_text(), migrated)
        self.assertEqual(ledger.store_format(self.ctx.state_dir), 2)
        items = json.loads(migrated)["items"].values()
        self.assertEqual([item["x"] for item in items], [1, 1])

    def test_a_migration_leaves_the_store_valid(self) -> None:
        self.from_fixture()
        with mock.patch.dict(migrations.FORMATS, {"ledger": 2}):
            migrations.run(self.ctx, [ADD_X])
        found = ledger.validate_unlocked(self.ctx.state_dir)
        # the fixture is a ledger from before journals: a note, which `ledger validate` passes
        self.assertEqual([f["detail"] for f in found], [validation.NO_JOURNAL_YET])
        self.assertEqual(validation.report(found, False, "")[1], 0)

    def test_the_version_is_recorded_after_each_migration(self) -> None:
        """A migration that fails leaves the store at the last one that ran, which the next run
        goes on from."""
        self.from_fixture()

        def fails(_ctx: migrations.Context) -> int:
            raise OSError("disk full")

        broken = migrations.Migration("ledger", 2, 3, fails, "{n} items moved")
        with mock.patch.dict(migrations.FORMATS, {"ledger": 3}):
            with self.assertRaisesRegex(OSError, "disk full"):
                migrations.run(self.ctx, [ADD_X, broken])
            self.assertEqual(ledger.store_format(self.ctx.state_dir), 2)
            again = migrations.run(
                self.ctx, [ADD_X, migrations.Migration("ledger", 2, 3, lambda _c: 0, "{n} moved")]
            )
        self.assertEqual(again, [migrations.Applied("ledger", 2, 3, "0 moved")])
        self.assertEqual(ledger.store_format(self.ctx.state_dir), 3)

    def test_a_gap_changes_nothing(self) -> None:
        """Every store's steps are known before any runs."""
        self.from_fixture()
        before = self.ledger_file().read_text()
        with mock.patch.dict(migrations.FORMATS, {"ledger": 2, "knowledge": 2}):
            self.make_knowledge()
            with self.assertRaisesRegex(migrations.MigrationError, "knowledge.*1 -> 2"):
                migrations.run(self.ctx, [ADD_X])
        self.assertEqual(self.ledger_file().read_text(), before)

    def test_the_knowledge_base_records_its_format(self) -> None:
        paths = self.make_knowledge()
        counted = migrations.Migration(
            "knowledge",
            1,
            2,
            lambda ctx: len(knowledge_store.read_jsonl(ctx.knowledge.entities)),
            "{n} entities",
        )
        self.assertEqual(migrations.store_formats(self.ctx), {"ledger": 0, "knowledge": 1})
        with mock.patch.dict(migrations.FORMATS, {"knowledge": 2}):
            applied = migrations.run(self.ctx, [counted])
        self.assertEqual(applied, [migrations.Applied("knowledge", 1, 2, "1 entities")])
        self.assertEqual(knowledge_store.store_format(paths), 2)
        self.assertEqual(knowledge.validate_unlocked(paths), [])

    def test_nothing_to_migrate_changes_nothing(self) -> None:
        self.from_fixture()
        before = self.ledger_file().read_text()
        self.assertEqual(migrations.run(self.ctx), [])
        self.assertEqual(self.ledger_file().read_text(), before)
        self.assertFalse(self.ctx.knowledge.root.exists())

    def test_a_registry_that_does_not_hold_together_changes_nothing(self) -> None:
        self.from_fixture()
        before = self.ledger_file().read_text()
        bad = migrations.Migration("ledger", 1, 2, add_x, "{count} items migrated")
        with mock.patch.dict(migrations.FORMATS, {"ledger": 2}):
            with self.assertRaisesRegex(migrations.MigrationError, "summary"):
                migrations.run(self.ctx, [bad])
            self.assertEqual(self.ledger_file().read_text(), before)
            # applied anyway, its line is made before its store moves
            with self.assertRaises(KeyError):
                migrations._apply(self.ctx, bad)
        self.assertEqual(ledger.store_format(self.ctx.state_dir), 1)

    def test_a_store_with_a_journal_is_rebuilt_from_it_and_stays_valid(self) -> None:
        self.make_journaled_ledger()
        with (
            mock.patch.object(ledger, "LEDGER_FORMAT", 2),
            mock.patch.dict(migrations.FORMATS, {"ledger": 2}),
        ):
            applied = migrations.run(self.ctx, [REBUILD])
            again = migrations.run(self.ctx, [REBUILD])
        self.assertEqual((applied, again), ([migrations.Applied("ledger", 1, 2, "2 items rebuilt")], []))
        self.assertEqual(ledger.store_format(self.ctx.state_dir), 2)
        self.assertEqual(ledger.validate_unlocked(self.ctx.state_dir), [])

    def test_a_store_with_a_journal_edited_in_place_is_changed_outside_it(self) -> None:
        """Why a migration of a store with a journal rebuilds its files: the toy ADD_X, which edits
        ledger.json in place, leaves a change validate reports (the next write would adopt it)."""
        self.make_journaled_ledger()
        with mock.patch.dict(migrations.FORMATS, {"ledger": 2}):
            migrations.run(self.ctx, [ADD_X])
        self.assertEqual([f["kind"] for f in ledger.validate_unlocked(self.ctx.state_dir)], ["edited"])


class TestConfig(MigrationTest):
    def test_config_renames_keep_the_rest(self) -> None:
        self.assertEqual(
            migrations.migrate_config("# note\nOLD=1\nKEEP=2\n", RENAMES),
            ("# note\nNEW=1\nKEEP=2\n", ["OLD is now NEW"]),
        )
        self.assertEqual(
            migrations.migrate_config("# note\nOLD=1\nGONE=3\nKEEP=2\n", RENAMES),
            ("# note\nNEW=1\nKEEP=2\n", ["OLD is now NEW", "GONE removed: bosun no longer reads it"]),
        )

    def test_only_an_assignment_is_renamed(self) -> None:
        given = '# OLD=1 was here\nexport OLD="a b"  # mine\nKEEP_OLD=1\nOLDER=2\necho OLD=3\nKEEP=$OLD\n'
        text, said = migrations.migrate_config(given, RENAMES)
        self.assertEqual(
            text, '# OLD=1 was here\nexport NEW="a b"  # mine\nKEEP_OLD=1\nOLDER=2\necho OLD=3\nKEEP=$OLD\n'
        )
        self.assertEqual(said, ["OLD is now NEW"])

    def test_a_key_renamed_twice_gets_its_current_name(self) -> None:
        """A bosun.env from two versions back: A was renamed B, then B was renamed C."""
        chain = (("A", "B"), ("B", "C"))
        self.assertEqual(migrations.config_names(chain), {"A": "C", "B": "C"})
        self.assertEqual(migrations.migrate_config("A=1\nK=2\n", chain), ("C=1\nK=2\n", ["A is now C"]))
        self.assertEqual(migrations.migrate_config("B=1\n", chain), ("C=1\n", ["B is now C"]))

    def test_a_chain_that_ends_in_a_removal_removes_the_key(self) -> None:
        chain = (("A", "B"), ("B", ""))
        self.assertEqual(migrations.config_names(chain), {"A": "", "B": ""})
        self.assertEqual(
            migrations.migrate_config("A=1\nK=2\n", chain), ("K=2\n", ["A removed: bosun no longer reads it"])
        )

    def test_renames_that_go_round_are_refused(self) -> None:
        for table in ((("A", "B"), ("B", "A")), (("A", "A"),), (("A", "B"), ("B", "C"), ("C", "B"))):
            with self.subTest(table=table), self.assertRaisesRegex(migrations.MigrationError, "go round"):
                migrations.migrate_config("A=1\n", table)

    def test_an_old_key_in_two_rows_is_refused(self) -> None:
        with self.assertRaisesRegex(migrations.MigrationError, "renamed twice"):
            migrations.config_names((("A", "B"), ("A", "C")))
        for table in ((("", "B"),), (("A", "B C"),), (("A=", "B"),)):
            with self.subTest(table=table), self.assertRaisesRegex(migrations.MigrationError, "name a key"):
                migrations.config_names(table)

    def test_the_config_renames_hold_together(self) -> None:
        migrations.config_names()
        self.assertEqual(
            set(migrations.config_names(migrations.CONFIG_RENAMES)), {o for o, _ in migrations.CONFIG_RENAMES}
        )

    def test_a_name_the_file_sets_already_is_not_set_twice(self) -> None:
        for given in ("OLD=1\nNEW=2\n", "NEW=2\nOLD=1\n"):
            with self.subTest(given=given):
                self.assertEqual(
                    migrations.migrate_config(given, RENAMES),
                    ("NEW=2\n", ["OLD dropped: NEW is already set"]),
                )

    def test_of_two_old_keys_the_one_renamed_fewer_times_is_kept(self) -> None:
        """B is what the bosun before this one read; A, an older name of it, was already ignored."""
        chain = (("A", "B"), ("B", "C"))
        self.assertEqual(
            migrations.migrate_config("A=1\nB=2\n", chain),
            ("C=2\n", ["A dropped: C is already set", "B is now C"]),
        )

    def test_of_two_old_keys_renamed_as_often_the_first_in_the_file_is_kept(self) -> None:
        both = (("A", "C"), ("B", "C"))
        self.assertEqual(
            migrations.migrate_config("A=1\nB=2\n", both),
            ("C=1\n", ["A is now C", "B dropped: C is already set"]),
        )
        self.assertEqual(
            migrations.migrate_config("B=2\nA=1\n", both),
            ("C=2\n", ["B is now C", "A dropped: C is already set"]),
        )

    def test_the_tables_are_read_when_called(self) -> None:
        with mock.patch.object(migrations, "CONFIG_RENAMES", RENAMES):
            self.assertEqual(migrations.migrate_config("OLD=1\n"), ("NEW=1\n", ["OLD is now NEW"]))

    def test_the_config_copy_is_kept_only_when_it_changed(self) -> None:
        env = self.ctx.config_dir / "bosun.env"
        env.write_text("# note\nKEEP=2\n")
        env.chmod(0o600)
        with mock.patch.object(migrations, "CONFIG_RENAMES", RENAMES):
            self.assertEqual(migrations.write_config(self.ctx.config_dir, NOW), [])
            self.assertEqual(sorted(p.name for p in self.ctx.config_dir.iterdir()), ["bosun.env"])
            env.write_text("# note\nOLD=1\nKEEP=2\n")
            said = migrations.write_config(self.ctx.config_dir, NOW)
        self.assertEqual(len(said), 1)
        kept = migrations.config_copy(self.ctx.config_dir, NOW)
        self.assertEqual(kept.name, "bosun.env.pre-update-20261106-093005")
        self.assertEqual(sorted(p.name for p in self.ctx.config_dir.iterdir()), ["bosun.env", kept.name])
        self.assertEqual(kept.read_text(), "# note\nOLD=1\nKEEP=2\n")
        self.assertEqual(env.read_text(), "# note\nNEW=1\nKEEP=2\n")
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertEqual(kept.stat().st_mode & 0o777, 0o600)

    def test_no_config_file_is_nothing_to_change(self) -> None:
        with mock.patch.object(migrations, "CONFIG_RENAMES", RENAMES):
            self.assertEqual(migrations.write_config(self.ctx.config_dir, NOW), [])
        self.assertEqual(list(self.ctx.config_dir.iterdir()), [])

    def test_a_linked_config_stays_a_link(self) -> None:
        """A bosun.env kept with my dotfiles: its target changes, the link stays, and the copy is kept
        in the config folder."""
        dotfiles = Path(self.tmp.name) / "dotfiles"
        dotfiles.mkdir()
        (dotfiles / "bosun.env").write_text("OLD=1\n")
        env = self.ctx.config_dir / "bosun.env"
        env.symlink_to(dotfiles / "bosun.env")
        with mock.patch.object(migrations, "CONFIG_RENAMES", RENAMES):
            self.assertEqual(migrations.write_config(self.ctx.config_dir, NOW), ["OLD is now NEW"])
        self.assertTrue(env.is_symlink())
        self.assertEqual((dotfiles / "bosun.env").read_text(), "NEW=1\n")
        kept = migrations.config_copy(self.ctx.config_dir, NOW)
        self.assertFalse(kept.is_symlink())
        self.assertEqual(kept.read_text(), "OLD=1\n")
        self.assertEqual(sorted(p.name for p in self.ctx.config_dir.iterdir()), ["bosun.env", kept.name])
        self.assertEqual([p.name for p in dotfiles.iterdir()], ["bosun.env"])


class TestValidateUnlocked(MigrationTest):
    def test_validate_unlocked_runs_under_a_held_lock(self) -> None:
        """The freeze holds both stores' exclusive locks while it checks them. flock is per open
        file, so a check that took a lock of its own would wait for its own process for ever."""
        self.from_fixture()
        paths = self.make_knowledge()
        with ledger.lock_at(self.ctx.state_dir, fcntl.LOCK_EX), knowledge_store.locked(paths, write=True):
            found = within_a_second(
                lambda: (ledger.validate_unlocked(self.ctx.state_dir), knowledge.validate_unlocked(paths))
            )
        self.assertEqual(found, ([validation.finding("note", validation.NO_JOURNAL_YET, path="journal")], []))

    def test_validate_unlocked_reads_the_folder_it_is_given(self) -> None:
        """Not the configured ledger: the rehearsal checks its copy in the same process."""
        ledger.configure(Path(self.tmp.name) / "elsewhere")
        (self.ctx.state_dir / "ledger.json").write_text(
            json.dumps({"version": 1, "items": {"review:acme/exporter#1": {"kind": "nope", "state": "x"}}})
        )
        found = ledger.validate_unlocked(self.ctx.state_dir)
        self.assertIn("state", [f["kind"] for f in found])
        self.assertEqual(ledger.validate_unlocked(Path(self.tmp.name) / "elsewhere"), [])
        self.assertFalse((Path(self.tmp.name) / "elsewhere").exists())  # it writes nothing


class TestApplied(unittest.TestCase):
    def test_a_migration_that_ran_reads_back_from_the_update_s_record(self) -> None:
        applied = migrations.Applied("ledger", 1, 2, "412 items migrated: 3 kept: as they were")
        self.assertEqual(applied.as_record(), "ledger 1 -> 2: 412 items migrated: 3 kept: as they were")
        self.assertEqual(migrations.Applied.from_record(applied.as_record()), applied)

    def test_a_line_of_another_shape_is_none(self) -> None:
        for text in ("", "ledger: 412 items migrated", "ledger one -> 2: x"):
            self.assertIsNone(migrations.Applied.from_record(text), text)


class TestAssignedKeys(unittest.TestCase):
    def test_the_keys_bosun_env_sets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            self.assertEqual(migrations.assigned_keys(config), set())
            (config / "bosun.env").write_text("A=1\n  export B=2\n#C=3\nD 4\n")
            self.assertEqual(migrations.assigned_keys(config), {"A", "B"})


class TestUserFileChanges(unittest.TestCase):
    def test_a_change_to_my_own_files_carries_the_version_it_came_in(self) -> None:
        """Its entries carry no version yet, so the plan and bosun doctor would list every entry at every
        update, forever after: the first entry needs that version first."""
        unversioned = [entry for entry in migrations.USER_FILE_CHANGES if len(entry) < 3]
        self.assertEqual(
            unversioned,
            [],
            "USER_FILE_CHANGES gained an entry, but its entries carry no version: give each entry the "
            "version it came in, and have the plan (update_facts.plan_for) and bosun doctor "
            "(update_summary.left_to_me) list only those after the version updated from",
        )


if __name__ == "__main__":
    unittest.main()
