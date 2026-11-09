"""lib/update.py: what stops an update at its plan, the rehearsal, the freeze and its failure path,
the release and an update cut short carried on (docs/design/update.md, "The steps" and "Cut short").
The end to end is the smoke suite's "bosun update" section. Fictional data."""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import install_paths
import knowledge_store
import ledger
import migrations
import update
import update_backups
import update_facts
import update_gate
import update_notes
import update_plan
import update_state
import update_summary
import update_windows
import versions
from update_state import UpdateState


FROM = "aaaaaaa1111111111111111111111111111111111"
TO = "bbbbbbb2222222222222222222222222222222222"


def a_plan(renames: tuple[tuple[str, str], ...] = ()) -> update_plan.Plan:
    return update_plan.Plan(
        from_commit=FROM,
        to_commit=TO,
        commits=(),
        migrations=(),
        rules=(),
        keys_added=(),
        renames=renames,
        prompts=(),
        windows=(),
        jobs=(),
        console=False,
        handed=(),
        user_changes=(),
        blockers=(),
    )


class World(unittest.TestCase):
    """A state and config folder with a ledger made by real writes, and an install: two version
    folders beside bosun.git, and the install link at the old one."""

    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.state = tmp / "state"
        self.config = tmp / "config"
        self.config.mkdir()
        share = tmp / "share"
        self.install = share / "bosun"
        self.old = share / "bosun-versions" / FROM[:12]
        self.new = share / "bosun-versions" / TO[:12]
        for folder in (self.old, self.new, share / "bosun-versions" / install_paths.REPO):
            folder.mkdir(parents=True)
        versions.point(self.install, self.old)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            mock.patch.dict(
                os.environ, {"BOSUN_STATE_DIR": str(self.state), "BOSUN_CONFIG_DIR": str(self.config)}
            )
        )
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        ledger.configure(self.state)
        for number in (12, 14):
            self.ledger(
                "upsert", f"review:acme/exporter#{number}", "--kind", "review-request", "--title", "T"
            )

    def ledger(self, *argv: str) -> None:
        args = ledger.build_parser().parse_args(list(argv))
        args.func(args)

    def record(self, step: str, **fields: Any) -> UpdateState:
        state = UpdateState(
            id="u1",
            from_root=str(self.old),
            to_root=str(self.new),
            from_commit=FROM,
            to_commit=TO,
            step=step,
            started_at="2026-11-07T12:00:00+00:00",
            hold_until=0,
            **fields,
        )
        update_state.write(self.state, state)
        return state

    def backup(self, size: int = 2000) -> Path:
        """A backup folder as update_backups.take leaves one: its files, and the manifest."""
        folder = self.state / update_backups.BACKUPS_DIR / f"20261107-120000-{FROM[:7]}-{TO[:7]}"
        (folder / "state").mkdir(parents=True)
        (folder / "state" / "ledger.json").write_bytes(b"x" * (size - 2))
        (folder / update_backups.MANIFEST).write_text("{}")
        return folder

    def link(self) -> Path:
        current = versions.current(self.install)
        assert current is not None
        return current

    @contextlib.contextmanager
    def steps_seen(self, seen: list[tuple[str, str, tuple[str, ...]]]) -> Iterator[None]:
        """Each record written in the block, in <seen>: its step, backup and what it restores."""
        real = update_state.write

        def write(state_dir: Path, state: UpdateState) -> None:
            seen.append((state.step, state.backup, state.restorable))
            real(state_dir, state)

        with mock.patch.object(update_state, "write", side_effect=write):
            yield


class FreezeTest(World):
    def spy(
        self, seen: list[tuple[str, str, tuple[str, ...]]], at: list[str], real: Callable[..., Any]
    ) -> Any:
        """<real>, noting in <at> the last step recorded when it is called."""

        def call(*args: Any, **kwargs: Any) -> Any:
            at.append(seen[-1][0])
            return real(*args, **kwargs)

        return call

    def test_each_step_is_recorded_before_it_starts_and_the_backup_once_it_is_taken(self) -> None:
        taken: list[str] = []
        migrated: list[str] = []
        switched: list[str] = []
        seen: list[tuple[str, str, tuple[str, ...]]] = []
        with (
            self.steps_seen(seen),
            mock.patch.object(update_backups, "take", side_effect=self.spy(seen, taken, update_backups.take)),
            mock.patch.object(migrations, "run", side_effect=self.spy(seen, migrated, migrations.run)),
            mock.patch.object(versions, "point", side_effect=self.spy(seen, switched, versions.point)),
        ):
            done = update.freeze(self.record("holding"), a_plan(), self.state, self.config)
        steps = [step for step, _, _ in seen]
        self.assertEqual(
            steps[1:], ["marked", "backed-up", "backed-up", "migrated", "switched", "checked", "checked"]
        )
        self.assertEqual((taken, migrated, switched), (["backed-up"], ["migrated"], ["switched"]))
        self.assertEqual(seen[2][1:], ("", ()))  # backed-up is recorded before the backup is taken
        self.assertTrue(seen[3][1])  # then with it, and what it restores
        self.assertEqual(seen[3][2], update_backups.FROZEN)
        past = update_state.STEPS.index("backed-up")
        for step, backup, restorable in seen:
            if update_state.STEPS.index(step) > past:
                self.assertTrue(backup and restorable, f"{step} recorded without its backup")
        self.assertEqual(done.step, "checked")
        self.assertEqual(self.link().resolve(), self.new.resolve())
        self.assertGreaterEqual(done.freeze_ms, 0)
        self.assertEqual(update_state.read(self.state), done)

    def test_what_the_migrations_did_is_recorded_with_bosun_env_s_copy_named(self) -> None:
        """For the summary, also of an update carried on by a later run: each migration's line, and
        bosun.env's change with the copy kept of it, which has the backup's time in its name."""
        (self.config / "bosun.env").write_text("BOSUN_OLD_KEY=7\n")
        ran = [migrations.Applied("ledger", 1, 2, "2 items migrated")]
        with (
            mock.patch.object(migrations, "run", return_value=ran),
            mock.patch.object(migrations, "CONFIG_RENAMES", (("BOSUN_OLD_KEY", "BOSUN_NEW_KEY"),)),
        ):
            done = update.freeze(
                self.record("holding"),
                a_plan(renames=(("BOSUN_OLD_KEY", "BOSUN_NEW_KEY"),)),
                self.state,
                self.config,
            )
        self.assertEqual(done.step, "checked")
        self.assertEqual(done.migrated, ("ledger 1 -> 2: 2 items migrated",))
        stamp = Path(done.backup).name[: len("20261107-120000")]
        copy = f"bosun.env.pre-update-{stamp}"
        self.assertEqual(
            done.config_changed, (f"bosun.env: BOSUN_OLD_KEY is now BOSUN_NEW_KEY (before: {copy})",)
        )
        self.assertEqual((self.config / copy).read_text(), "BOSUN_OLD_KEY=7\n")

    def test_freeze_ms_counts_from_the_locks_taken_not_the_wait_for_them(self) -> None:
        """A writer holding the ledger's lock when the freeze starts delays it; writes were not
        paused by the update meanwhile, so that wait is not in freeze_ms."""
        held, done = threading.Event(), threading.Event()

        def writer() -> None:
            with ledger.lock_at(self.state, fcntl.LOCK_EX):
                held.set()
                done.wait(1.0)

        thread = threading.Thread(target=writer)
        thread.start()
        held.wait(5)
        try:
            state = update.freeze(self.record("holding"), a_plan(), self.state, self.config)
        finally:
            thread.join()
        self.assertEqual(state.step, "checked")
        self.assertLess(state.freeze_ms, 800)

    def test_a_plan_that_renames_config_keys_restores_bosun_env_too(self) -> None:
        self.assertEqual(
            update.restorable(a_plan(renames=(("BOSUN_OLD", "BOSUN_NEW"),)))[-1], "config/bosun.env"
        )

    def test_a_plan_that_adds_a_kept_key_writes_and_restores_bosun_env(self) -> None:
        """One condition for both: the update may change bosun.env (renames, or a key it adds with the
        value that keeps what the install did), so it writes it and a failed freeze puts it back."""
        kept = dataclasses.replace(a_plan(), keys_added=("BOSUN_ORCHESTRATOR_CONNECTORS",))
        fresh = dataclasses.replace(a_plan(), keys_added=("BOSUN_CONNECTORS",))
        self.assertTrue(update.changes_config(kept))
        self.assertTrue(update.changes_config(a_plan(renames=(("BOSUN_OLD", "BOSUN_NEW"),))))
        self.assertFalse(update.changes_config(fresh))
        self.assertEqual(update.restorable(kept)[-1], "config/bosun.env")
        self.assertNotIn("config/bosun.env", update.restorable(fresh))

    def test_a_failed_freeze_puts_bosun_env_back_when_only_a_kept_key_was_written(self) -> None:
        env = self.config / "bosun.env"
        env.write_text("BOSUN_DAYS=MTWRF\n")
        plan = dataclasses.replace(a_plan(), keys_added=("BOSUN_ORCHESTRATOR_CONNECTORS",))
        written: list[str] = []

        def failing(state_dir: Path, config_dir: Path) -> list[str]:
            written.append(env.read_text())
            return ["the ledger: journal: damaged"]

        with mock.patch.object(update, "check_stores", side_effect=failing):
            done = update.freeze(self.record("holding"), plan, self.state, self.config)
        self.assertEqual(done.step, "failed")
        self.assertIn('BOSUN_ORCHESTRATOR_CONNECTORS="all"', written[0])
        self.assertEqual(env.read_text(), "BOSUN_DAYS=MTWRF\n")

    def test_a_failed_migration_is_put_back_under_the_locks_it_holds(self) -> None:
        before = (self.state / "ledger.json").read_bytes()

        def half_done(ctx: migrations.Context) -> list[migrations.Applied]:
            (ctx.state_dir / "ledger.json").write_text('{"version": 2, "items": {}}')
            raise migrations.MigrationError("the ledger's migration failed")

        put_back = mock.Mock(side_effect=update_gate.put_back_held)
        with (
            mock.patch.object(migrations, "run", side_effect=half_done),
            mock.patch.object(update_gate, "put_back_held", put_back),
            mock.patch.object(
                update_gate, "recover", side_effect=AssertionError("recover takes the locks again")
            ),
        ):
            done = update.freeze(self.record("holding"), a_plan(), self.state, self.config)
        put_back.assert_called_once()
        self.assertEqual(put_back.call_args.args[2].step, "migrated")
        self.assertEqual((self.state / "ledger.json").read_bytes(), before)
        self.assertEqual(self.link().resolve(), self.old.resolve())
        self.assertEqual((done.step, done.error), ("failed", "the ledger's migration failed"))
        self.assertEqual(update_state.read(self.state), done)

    def test_a_failed_check_after_the_switch_points_the_link_back(self) -> None:
        with mock.patch.object(update, "check_stores", return_value=["the ledger: journal: damaged"]):
            done = update.freeze(self.record("holding"), a_plan(), self.state, self.config)
        self.assertEqual(done.step, "failed")
        self.assertIn("the ledger: journal: damaged", done.error)
        self.assertEqual(self.link().resolve(), self.old.resolve())

    def test_a_put_back_that_fails_keeps_the_freeze_recorded(self) -> None:
        """Writes stay refused (the record still in the freeze) until bosun update puts it back."""
        with (
            mock.patch.object(migrations, "run", side_effect=migrations.MigrationError("first")),
            mock.patch.object(update_gate, "put_back_held", side_effect=update_backups.BackupError("second")),
        ):
            done = update.freeze(self.record("holding"), a_plan(), self.state, self.config)
        self.assertEqual(done.step, "migrated")
        self.assertIn("first", done.error)
        self.assertIn("second", done.error)
        recorded = update_state.read(self.state)
        assert recorded is not None
        self.assertTrue(update_state.in_freeze(recorded))

    def test_the_freeze_holds_both_stores_write_locks(self) -> None:
        kb = knowledge_store.Paths(self.config / update_gate.KNOWLEDGE_DIR)
        kb.root.mkdir()
        held: list[bool] = []

        def busy(ctx: migrations.Context) -> list[migrations.Applied]:
            for path in (self.state / ledger.LOCK_FILE, kb.lock):
                with path.open("a") as other:
                    try:
                        fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except OSError:
                        held.append(True)
                    else:
                        held.append(False)
            return []

        with mock.patch.object(migrations, "run", side_effect=busy):
            update.freeze(self.record("holding"), a_plan(), self.state, self.config)
        self.assertEqual(held, [True, True])


class RehearseTest(World):
    def test_a_rehearsal_that_passes_finds_nothing_and_leaves_no_copy(self) -> None:
        self.assertEqual(update.rehearse(self.state, self.config, a_plan()), [])
        self.assertFalse((self.state / update_backups.REHEARSAL).exists())

    def test_a_failing_migration_is_a_finding_and_changes_nothing(self) -> None:
        before = (self.state / "ledger.json").read_bytes()
        seen: list[Path] = []

        def fails(ctx: migrations.Context) -> list[migrations.Applied]:
            seen.append(ctx.state_dir)
            (ctx.state_dir / "ledger.json").write_text("{}")
            raise migrations.MigrationError("no way from format 1")

        with mock.patch.object(migrations, "run", side_effect=fails):
            found = update.rehearse(self.state, self.config, a_plan())
        self.assertEqual(found, ["MigrationError: no way from format 1"])
        self.assertEqual(seen, [self.state / update_backups.REHEARSAL / "state"])
        self.assertEqual((self.state / "ledger.json").read_bytes(), before)
        self.assertFalse((self.state / update_backups.REHEARSAL).exists())

    def test_a_migration_that_breaks_its_store_is_a_finding(self) -> None:
        def damages(ctx: migrations.Context) -> list[migrations.Applied]:
            journal = next((ctx.state_dir / "journal" / ledger.LEDGER_STREAM).glob("*.jsonl"))
            journal.write_text("nonsense\n")
            return []

        with mock.patch.object(migrations, "run", side_effect=damages):
            found = update.rehearse(self.state, self.config, a_plan())
        self.assertTrue(found)
        self.assertTrue(all(line.startswith("the ledger: ") for line in found), found)


class ReleaseTest(World):
    def test_the_check_in_loop_restarts_before_the_hold_is_lifted(self) -> None:
        calls: list[tuple[tuple[str, ...], str]] = []

        def bosun(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
            recorded = update_state.read(self.state)
            calls.append((args, recorded.step if recorded else ""))
            return subprocess.CompletedProcess(args, 0, "", "")

        with (
            mock.patch.object(update, "_reload_jobs", return_value=([], [])),
            mock.patch.object(
                update_facts, "open_windows", return_value=[{"kind": "checkin", "window": True}]
            ),
            mock.patch.object(update_facts, "bosun", side_effect=bosun),
            mock.patch.object(update, "_doctor", return_value=["warn: on battery"]),
            mock.patch.object(update, "_prune"),
        ):
            done = update.release(self.record("checked"))
        self.assertEqual(calls, [(("refresh", "--checkin-loop"), "checked")])
        self.assertEqual((done.step, done.findings), ("released", ("warn: on battery",)))

    def test_the_doctor_run_after_the_switch_is_said_before_it_starts(self) -> None:
        """Minor 6: it can take a while (600 s at the most), so it is never silent."""
        out = io.StringIO()
        said_before: list[str] = []

        def doctor(root: Path) -> list[str]:
            said_before.append(out.getvalue())
            return []

        with (
            contextlib.redirect_stdout(out),
            mock.patch.object(update, "_reload_jobs", return_value=([], [])),
            mock.patch.object(update_facts, "open_windows", return_value=[]),
            mock.patch.object(update, "_doctor", side_effect=doctor),
            mock.patch.object(update, "_prune", return_value=[]),
        ):
            update.release(self.record("checked"))
        [before] = said_before
        self.assertTrue(before.endswith("Asking bosun doctor what fails after the switch...\n"), before)
        self.assertEqual(update_facts.COMMAND_SECONDS, 600)

    def test_what_fails_after_the_switch_is_a_finding_not_a_rollback(self) -> None:
        with (
            mock.patch.object(
                update_facts, "shell", return_value=subprocess.CompletedProcess([], 1, "", "bootstrap failed")
            ),
            mock.patch.object(update_facts, "open_windows", return_value=[]),
            mock.patch.object(update, "_doctor", return_value=[]),
            mock.patch.object(update, "_prune"),
        ):
            done = update.release(self.record("checked"))
        self.assertEqual((done.step, done.findings), ("released", ("launchd: bootstrap failed",)))
        self.assertEqual(self.link().resolve(), self.old.resolve())  # nothing pointed anywhere

    def test_the_jobs_reloaded_are_recorded_with_those_of_a_release_before(self) -> None:
        """A release carried on (cut short after the reload) keeps the jobs the first one reloaded."""
        with (
            mock.patch.object(
                update_facts,
                "shell",
                return_value=subprocess.CompletedProcess([], 0, "com.bosun.morning.watchdog\n", ""),
            ),
            mock.patch.object(update_facts, "open_windows", return_value=[]),
            mock.patch.object(update, "_doctor", return_value=[]),
            mock.patch.object(update, "_prune"),
        ):
            done = update.release(self.record("checked", reloaded=("com.bosun.morning",)))
        self.assertEqual(done.reloaded, ("com.bosun.morning", "com.bosun.morning.watchdog"))
        self.assertEqual(update_state.read(self.state), done)

    def test_a_checkout_from_before_versions_with_work_in_it_is_kept_and_said(self) -> None:
        """The release's prune checks it as bosun uninstall checks a checkout, and the
        summary says once what it kept."""
        origin = self.new.parent.parent / "origin.git"
        versions.git("init", "-q", "--bare", str(origin))
        identity = ("-c", "user.name=acme", "-c", "user.email=dev@example.com", "-c", "commit.gpgsign=false")
        made = []
        for commit in ("a" * 40, "b" * 40):
            folder = versions.legacy_folder(self.new.parent, commit)
            versions.git("clone", "-q", str(origin), str(folder))
            versions.git("-C", str(folder), *identity, "commit", "-q", "--allow-empty", "-m", "a release")
            versions.git("-C", str(folder), "push", "-q", "origin", "HEAD:main")
            versions.git("-C", str(folder), "fetch", "-q", "origin")
            versions.git("-C", str(folder), "config", "bosun.managed", "true")
            made.append(folder)
        mine, clean = made
        (mine / "notes.txt").write_text("mine\n")
        with (
            mock.patch.object(update, "_reload_jobs", return_value=([], [])),
            mock.patch.object(update_facts, "open_windows", return_value=[]),
            mock.patch.object(update, "_doctor", return_value=[]),
            mock.patch.object(update_backups, "prune", return_value=[]),
        ):
            done = update.release(self.record("checked"))
        self.assertTrue(mine.is_dir())
        self.assertFalse(clean.exists())
        said = f"kept {mine}, the checkout from before versions: it has uncommitted changes; delete it"
        self.assertEqual(done.findings, (f"{said} once you have what you need",))

    def test_a_folder_that_is_not_a_version_is_said_as_such(self) -> None:
        """Round 2: a plain folder in the versions folder is kept, and said for what it is, once."""
        plain = self.new.parent / "notes"
        plain.mkdir()
        (plain / "mine.txt").write_text("mine\n")
        with (
            mock.patch.object(update, "_reload_jobs", return_value=([], [])),
            mock.patch.object(update_facts, "open_windows", return_value=[]),
            mock.patch.object(update, "_doctor", return_value=[]),
            mock.patch.object(versions, "prune", return_value=[]),
        ):
            done = update.release(self.record("checked"))
        self.assertEqual(
            done.findings, (f"kept {plain}, a folder that is not a version: bosun did not put it there",)
        )

    def test_an_older_version_with_work_in_it_is_kept_and_said(self) -> None:
        """As a kept checkout from before versions is."""
        (self.old / ".git").write_text(
            f"gitdir: {self.old.parent / 'bosun.git' / 'worktrees' / self.old.name}\n"
        )
        why = "it has uncommitted changes; delete it once you have what you need"
        with (
            mock.patch.object(update, "_reload_jobs", return_value=([], [])),
            mock.patch.object(update_facts, "open_windows", return_value=[]),
            mock.patch.object(update, "_doctor", return_value=[]),
            mock.patch.object(versions, "prune", return_value=[]),
            mock.patch.object(versions, "held_back", return_value=[(self.old, why)]),
        ):
            done = update.release(self.record("checked"))
        self.assertEqual(done.findings, (f"kept {self.old}, an older version: {why}",))

    def test_the_release_sweeps_the_links_a_switch_cut_short_left_before_it_started(self) -> None:
        """Minor 12: older than the update's start, as its record has it; a newer one stays."""
        stale = self.install.with_name(".bosun.link-0000aaaa")
        newer = self.install.with_name(".bosun.link-1111bbbb")
        for link, moment in ((stale, "2026-11-07T11:59:00+00:00"), (newer, "2026-11-07T12:01:00+00:00")):
            link.symlink_to(self.new)
            seconds = datetime.fromisoformat(moment).timestamp()
            os.utime(link, (seconds, seconds), follow_symlinks=False)
        with (
            mock.patch.object(update, "_reload_jobs", return_value=([], [])),
            mock.patch.object(update_facts, "open_windows", return_value=[]),
            mock.patch.object(update, "_doctor", return_value=[]),
        ):
            update.release(self.record("checked"))  # started at 12:00
        self.assertFalse(os.path.lexists(stale))
        self.assertTrue(newer.is_symlink())

    def test_what_doctor_fails_or_warns_about_is_kept(self) -> None:
        said = (
            "Tools\n  ok    tmux\n  warn  on battery\n  FAIL  something is missing or not done yet:\n"
            "    tmux   not found\nall\n"
        )
        with mock.patch.object(
            update_facts, "bosun", return_value=subprocess.CompletedProcess([], 1, said, "")
        ):
            self.assertEqual(
                update._doctor(self.new),
                ["warn: on battery", "FAIL: something is missing or not done yet: tmux not found"],
            )


class ThroughTest(World):
    def test_a_window_opened_after_the_go_ahead_s_plan_is_brought_along(self) -> None:
        """R68: once released, the windows are brought along by a plan made again from the two versions,
        so one opened between the plan the go-ahead was given on and the switch (the orchestrator
        delegating during the prompt or the hold) is brought along by the table too, as an update cut
        short has its windows brought along (_brought_along)."""
        approved = a_plan()
        again = dataclasses.replace(
            a_plan(), windows=(("acme-api", update_plan.Treatment.RESTART),), kinds=(("acme-api", "repo"),)
        )
        given: list[update_plan.Plan] = []

        def bring_along(state: UpdateState, plan: update_plan.Plan, say: Any) -> UpdateState:
            given.append(plan)
            return update_state.advanced(state, "done")

        def checked(state: UpdateState, *_args: Any) -> UpdateState:
            return update_state.advanced(state, "checked")

        with (
            mock.patch.object(update, "freeze", side_effect=checked),
            mock.patch.object(update, "release", side_effect=lambda s: update_state.advanced(s, "released")),
            mock.patch.object(update_facts, "plan_for", return_value=again) as plan_for,
            mock.patch.object(update_windows, "bring_along", side_effect=bring_along),
        ):
            done = update._through(self.record("rehearsed"), approved, update_facts.folders())
        plan_for.assert_called_once_with(self.old, self.new, self.state, self.config)
        self.assertEqual(given, [again])
        self.assertEqual(done.step, "done")


class ResumeTest(World):
    def test_nothing_recorded_or_finished_is_nothing_to_carry_on(self) -> None:
        self.assertIsNone(update.resume(self.state))
        self.record("done")
        self.assertIsNone(update.resume(self.state))

    def test_a_running_update_is_not_carried_on(self) -> None:
        self.record("migrated")
        with update_state.one_update(self.state):
            self.assertIsNone(update.resume(self.state))

    def test_one_cut_short_before_the_freeze_is_ended(self) -> None:
        self.record("holding")
        done = update.resume(self.state)
        assert done is not None
        self.assertEqual(done.step, "failed")
        self.assertIn("cut short at step holding", done.error)
        self.assertFalse(update_state.holding(self.state, 0))

    def test_one_cut_short_inside_the_freeze_is_put_back(self) -> None:
        self.record("migrated")
        with mock.patch.object(update_gate, "recover", return_value=True) as recover:
            update.resume(self.state)
        recover.assert_called_once_with(self.state, self.config)

    @contextlib.contextmanager
    def brought_along(self) -> Iterator[mock.MagicMock]:
        """bring_along, recording done, over a plan made again from the two versions."""

        def bring_along(state: UpdateState, plan: update_plan.Plan, say: Any) -> UpdateState:
            done = update_state.advanced(state, "done")
            update_state.write(self.state, done)
            return done

        with (
            mock.patch.object(update_facts, "plan_for", return_value=a_plan()) as plan_for,
            mock.patch.object(update_windows, "bring_along", side_effect=bring_along) as bring,
        ):
            yield bring
        plan_for.assert_called_once_with(self.old, self.new, self.state, self.config)

    def test_one_past_its_checks_rolls_forward(self) -> None:
        self.record("checked")

        def released(state: UpdateState) -> UpdateState:
            return update_state.advanced(state, "released")

        with (
            mock.patch.object(update, "release", side_effect=released) as release,
            self.brought_along() as bring,
        ):
            done = update.resume(self.state)
        release.assert_called_once()
        bring.assert_called_once()
        assert done is not None
        self.assertEqual(done.step, "done")
        self.assertEqual(update_state.read(self.state), done)

    def test_one_released_brings_the_agents_along(self) -> None:
        self.record("released")
        with mock.patch.object(update, "release") as release, self.brought_along() as bring:
            done = update.resume(self.state)
        release.assert_not_called()
        bring.assert_called_once()
        assert done is not None
        self.assertEqual(done.step, "done")

    def test_one_cut_short_while_bringing_the_windows_along_is_left_to_the_loop(self) -> None:
        """The windows still waited for are the check-in loop's; resume does not run them again."""
        self.record("agents", pending=("api",), windows={"api": ("repo", "restart", "restarted")})
        with mock.patch.object(update_windows, "bring_along") as bring:
            left = update.resume(self.state)
        bring.assert_not_called()
        assert left is not None
        self.assertEqual((left.step, left.pending), ("agents", ("api",)))
        with mock.patch.object(update_facts, "loop_runs", return_value=True):
            line = update._resumed_line(left)
        self.assertIn("still brings windows along: the check-in loop brings along api", line)
        self.assertNotIn("carried on", line)

    def test_one_at_agents_with_nothing_left_is_finished(self) -> None:
        self.record("agents")
        done = update.resume(self.state)
        assert done is not None
        self.assertEqual(done.step, "done")

    def test_a_window_answered_by_hand_no_longer_holds_the_next_update(self) -> None:
        """With no check-in loop, a status note written by hand ends the window's wait: an update carried
        on settles it before it plans (acting on no window), and finishes the record."""
        self.record("agents", pending=("web app",), windows={"web app": ("repo", "wait until idle")})
        update_notes.note(self.state, "web app", "status", "carried on by hand")
        with mock.patch.object(update_windows, "turn") as turn:
            done = update.resume(self.state)
        turn.assert_not_called()
        assert done is not None
        self.assertEqual((done.step, done.pending, done.windows["web app"][-1]), ("done", (), "answered"))
        self.assertEqual(update_state.read(self.state), done)

    def test_a_newer_bosuns_step_is_refused(self) -> None:
        state = self.record("checked")
        data = {**json.loads((self.state / update_state.STATE_FILE).read_text()), "step": "re-checked"}
        (self.state / update_state.STATE_FILE).write_text(json.dumps(data))
        with self.assertRaisesRegex(update_facts.UpdateRefused, "a newer bosun wrote"):
            update.resume(self.state)
        self.assertEqual(state.step, "checked")


class RunTest(World):
    """`bosun update` as the wrapper hands over to it, with the install link already at this version
    (the new one): what it says on each path, and --json's one object."""

    def setUp(self) -> None:
        super().setUp()
        versions.point(self.install, self.new)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(update, "ROOT", self.new))
        stack.enter_context(
            mock.patch.object(
                versions, "commit_of", side_effect=lambda root: FROM if root == self.old else TO
            )
        )
        self.addCleanup(setattr, update.Output, "to_stderr", False)

    def main(self, *flags: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = update.main(["--from", str(self.new), *flags])
        return code, out.getvalue(), err.getvalue()

    def test_nothing_new_and_nothing_recorded_is_up_to_date(self) -> None:
        self.assertEqual(self.main("--yes"), (0, "bosun is up to date (bbbbbbb).\n", ""))

    def test_a_dry_run_after_a_cut_at_switched_says_so_and_is_not_up_to_date(self) -> None:
        self.record("switched")
        code, out, _ = self.main("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("an update stopped at step switched", out)
        self.assertNotIn("up to date", out)
        recorded = update_state.read(self.state)
        assert recorded is not None
        self.assertEqual(recorded.step, "switched")  # a dry run changes nothing

    def test_an_update_running_is_refused_whatever_its_step(self) -> None:
        """A second update while the first is in its release (the link already switched) is refused,
        naming when the first started, never told it is up to date."""
        self.record("released")
        with update_state.one_update(self.state):
            code, out, err = self.main("--yes")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("an update is running (started 2026-11-07T12:00:00+00:00", err)

    @contextlib.contextmanager
    def resumed_wait(self, ends: UpdateState) -> Iterator[mock.MagicMock]:
        """An update cut short after its release, carried on: its wait ends at <ends>."""
        self.record("released")
        with (
            mock.patch.object(update_facts, "plan_for", return_value=a_plan()) as plan_for,
            mock.patch.object(update_windows, "bring_along", return_value=ends),
            mock.patch.object(update, "_update") as new_update,
        ):
            yield new_update
        plan_for.assert_called_once()  # the plan made again for the windows, and no other

    def test_ctrl_c_in_a_resumed_wait_ends_the_command(self) -> None:
        stopped = update_state.advanced(self.record("released"), "agents", pending=("api",))
        with self.resumed_wait(stopped) as new_update:
            code, _, _ = self.main("--yes")
        self.assertEqual(code, 0)
        new_update.assert_not_called()

    def test_a_resumed_wait_says_what_came_of_each_window(self) -> None:
        windows = {"api": ("repo", "restart", "restarted", "answered")}
        done = update_state.advanced(self.record("released"), "done", windows=windows)
        update_notes.note(self.state, "api", "status", "resumed: waiting on CI for PR 12")
        with self.resumed_wait(done):
            code, out, _ = self.main("--yes")
        self.assertEqual(code, 0)
        self.assertIn("carried on to the end", out)
        self.assertRegex(
            out, re.compile(r"^Agents\n  api +restarted; resumed: waiting on CI for PR 12$", re.MULTILINE)
        )
        self.assertIn("Going back: docs/setup.md#going-back\nbosun is up to date (bbbbbbb).\n", out)

    def test_json_carries_the_summary_of_an_update_that_ended(self) -> None:
        windows = {"api": ("repo", "restart", "restarted", "answered")}
        done = update_state.advanced(self.record("released"), "done", windows=windows)
        update_notes.note(self.state, "api", "status", "resumed: waiting on CI for PR 12")
        with self.resumed_wait(done):
            code, out, err = self.main("--yes", "--json")
        said = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(said["summary"]["agents"], {"api": "restarted; resumed: waiting on CI for PR 12"})
        self.assertEqual(said["summary"]["to_commit"], TO)
        self.assertIn("bosun updated:", err)  # the text goes to stderr

    def test_json_keeps_plan_and_update_on_every_path(self) -> None:
        self.record("done")
        code, out, _ = self.main("--yes", "--json")
        self.assertEqual(code, 0)
        said = json.loads(out)
        self.assertEqual((said["plan"], said["update"]["step"], said["summary"]), (None, "done", None))
        self.record("released")
        with update_state.one_update(self.state):
            code, out, err = self.main("--yes", "--json")
        said = json.loads(out)
        self.assertEqual((code, said["plan"], said["update"]["step"]), (1, None, "released"))
        self.assertIn("an update is running", err)


class StartTest(World):
    def test_the_start_records_the_plan_and_what_doctor_failed_at_before(self) -> None:
        """For the summary of an update carried on later: the plan said yes to, and the FAIL lines of
        a bosun doctor run while planning, before the hold."""
        plan = dataclasses.replace(
            a_plan(), keys_added=("BOSUN_EXPORT_RETRIES",), prompts=("orchestrator/policy.md",)
        )
        before = ("FAIL: gh is not logged in: run 'gh auth login'",)
        update_state.write(self.state, update._started(plan, self.old, self.new, before))
        recorded = update_state.read(self.state)
        assert recorded is not None
        self.assertEqual(update_plan.from_record(recorded.plan), plan)
        self.assertEqual((recorded.step, recorded.doctor_before), ("planned", before))

    def test_doctor_runs_while_planning_before_anything_is_held_and_its_fails_are_kept(self) -> None:
        """Outside the hold and the freeze, and recorded, so an update carried on compares the same."""
        seen: list[str | None] = []

        def fails(root: Path) -> tuple[str, ...]:
            recorded = update_state.read(self.state)
            seen.append(recorded.step if recorded else None)
            return ("FAIL: gh is not logged in: run 'gh auth login'",)

        where = update_facts.Folders(self.state, self.config, "update-test")
        with (
            mock.patch.object(update, "_doctor_fails", side_effect=fails),
            mock.patch.object(update, "rehearse", return_value=["the rehearsal stops it here"]),
        ):
            state = update._carried_out(a_plan(), self.old, self.new, where)
        self.assertEqual(seen, [None])  # before the update's record is written, so long before its hold
        self.assertEqual(state.step, "failed")
        self.assertEqual(state.doctor_before, ("FAIL: gh is not logged in: run 'gh auth login'",))

    def test_doctor_while_planning_keeps_its_fails_only(self) -> None:
        said = "Tools\n  ok    tmux\n  warn  on battery\n  FAIL  gh is not logged in: run 'gh auth login'\n"
        with mock.patch.object(
            update_facts, "bosun", return_value=subprocess.CompletedProcess([], 1, said, "")
        ):
            self.assertEqual(
                update._doctor_fails(self.new), ("FAIL: gh is not logged in: run 'gh auth login'",)
            )


class SummaryTest(World):
    """What the update says when it ends: the summary, built from its record, and saved as summary.md
    in the backup folder once the update is done."""

    def ended(self, step: str = "done", **fields: Any) -> UpdateState:
        started = (datetime.now(UTC) - timedelta(seconds=161)).isoformat(timespec="seconds")
        plan = update_plan.as_record(dataclasses.replace(a_plan(), formats=(("ledger", 1), ("knowledge", 1))))
        return dataclasses.replace(
            self.record(step, **fields),
            started_at=started,
            plan=plan,
            migrated=("ledger 1 -> 2: 2 items migrated",),
        )

    def said(self, state: UpdateState) -> tuple[str, update.Said]:
        said, out = update.Said(), io.StringIO()
        with contextlib.redirect_stdout(out):
            update._say_summary(self.state, state, said)
        return out.getvalue(), said

    def test_an_update_that_ended_says_its_summary_and_saves_it_next_to_the_backup(self) -> None:
        backup = self.backup(size=2000)
        out, said = self.said(self.ended(backup=str(backup)))
        assert said.summary is not None
        self.assertEqual(out, update_summary.render(said.summary))
        self.assertIn("  ledger      format 1 -> 2 (2 items migrated)\n", out)
        self.assertIn(f"Backup: {backup} (2.0 KB)", out)
        self.assertIn(said.summary.seconds, (161, 162))
        self.assertEqual(said.summary.backup_size, 2000)  # what the files hold, the summary not counted
        self.assertEqual((backup / "summary.md").read_text(), f"```text\n{out}```\n")

    def test_one_whose_wait_was_stopped_is_said_but_saved_only_once_done(self) -> None:
        backup = self.backup()
        state = self.ended(
            "agents", backup=str(backup), pending=("api",), windows={"api": ("repo", "restart")}
        )
        out, said = self.said(state)
        self.assertRegex(out, re.compile(r"^  api +nothing yet; still waited for$", re.MULTILINE))
        self.assertIsNotNone(said.summary)
        self.assertFalse((backup / "summary.md").exists())

    def test_a_backup_a_forget_deleted_is_said_so_and_never_made_again(self) -> None:
        gone = self.state / update_backups.BACKUPS_DIR / "20261107-120000-aaaaaaa-bbbbbbb"
        out, _ = self.said(self.ended(backup=str(gone)))
        self.assertIn("Backup: deleted by a kg forget since; going back past it is no longer possible", out)
        self.assertFalse(gone.exists())

    def test_a_summary_that_cannot_be_saved_says_so_once(self) -> None:
        """A backup folder that is there but cannot be written (its permissions, a full disk)."""
        backup = self.backup()
        state = self.ended(backup=str(backup))
        with mock.patch.object(Path, "write_text", side_effect=PermissionError(13, "Permission denied")):
            out, said = self.said(state)
        self.assertIsNotNone(said.summary)
        self.assertIn(
            f"could not save the summary as {backup / 'summary.md'}: [Errno 13] Permission denied", out
        )
        self.assertFalse((backup / "summary.md").exists())

    def test_a_summary_is_saved_as_utf_8_whatever_the_locale(self) -> None:
        backup = self.backup()
        state = self.ended(
            backup=str(backup), user_changes=("sessions.yml: rename \u201cmodel\u201d \udcff",)
        )
        self.said(state)
        self.assertIn("\u201cmodel\u201d", (backup / "summary.md").read_text(encoding="utf-8"))

    def test_a_failed_update_names_the_backup_it_took(self) -> None:
        """So that it is there in case I need it."""
        backup = self.backup()
        for step in ("failed", "migrated"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                update._failed(self.record(step, backup=str(backup), error="the ledger's check failed"))
            self.assertIn(f"The backup it took: {backup}.", out.getvalue(), step)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            update._failed(self.record("failed", error="the rehearsal failed"))
        self.assertNotIn("backup", out.getvalue())

    def failed_says(self, state: UpdateState) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(update, "ROOT", self.new):
            update._failed(state)
        return out.getvalue()

    def test_a_failure_left_in_the_freeze_says_what_carries_it_on(self) -> None:
        """Minor 7: bosun update where the installed bosun has it; the install line where it is a
        checkout from before versions (a conversion cut before its swap, or put back to legacy-*)."""
        self.assertIn("run bosun update again", self.failed_says(self.record("migrated", error="x")))
        self.install.unlink()
        self.install.mkdir()  # a conversion that failed before its swap: the checkout is installed
        cut = dataclasses.replace(self.record("migrated", error="x"), from_root=str(self.install))
        said = self.failed_says(cut)
        self.assertIn("Writes stay paused until it is put back: run the install line again.", said)
        self.install.rmdir()
        legacy = versions.legacy_folder(self.new.parent, FROM)
        legacy.mkdir()
        versions.point(self.install, legacy)  # put back after its swap
        said = self.failed_says(
            dataclasses.replace(self.record("switched", error="x"), from_root=str(legacy))
        )
        self.assertIn("run the install line again", said)

    def test_ctrl_c_says_what_carries_the_update_on(self) -> None:
        """Minor 7: as a failure does, from where the install leads now."""
        args = ["--from", str(self.old), "--yes"]
        for installed, again in ((self.old, "bosun update"), (self.install, "the install line")):
            if installed == self.install:
                self.install.unlink()
                self.install.mkdir()
                args = ["--from", str(self.install), "--yes"]
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                mock.patch.object(update, "ROOT", self.new),
                mock.patch.object(update, "run", side_effect=KeyboardInterrupt),
            ):
                self.assertEqual(update.main(args), 1)
            self.assertIn(f"stopped; run {again} again: it carries on, or puts back", err.getvalue())


class AgentsCommandTest(World):
    """What the agents and the check-in loop run while an update brings the windows along: `bosun
    update note` and `bosun update advance`."""

    def main(self, *argv: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(stdin)),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = update.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_window_whose_name_holds_a_space_can_answer(self) -> None:
        code, _, _ = self.main("note", "--window", "web app", "--status", stdin="carried on")
        self.assertEqual((code, update_notes.read_note(self.state, "web app", "status")), (0, "carried on"))

    def test_a_note_is_read_from_stdin(self) -> None:
        code, out, _ = self.main("note", "--window", "api", "--status", stdin="resumed: waiting on CI\n")
        self.assertEqual(code, 0)
        self.assertIn("api", out)
        self.assertEqual(update_notes.read_note(self.state, "api", "status"), "resumed: waiting on CI")
        code, _, _ = self.main("note", "--window", "api", "--stock", stdin="sleep 900: the CI wait")
        self.assertEqual(
            (code, update_notes.read_note(self.state, "api", "stock")), (0, "sleep 900: the CI wait")
        )

    def test_a_note_refuses_an_unknown_kind_no_window_and_nothing_to_say(self) -> None:
        for argv in (
            ("note", "--window", "api", "--mood"),
            ("note", "--window", "api"),
            ("note", "--status"),
        ):
            with (
                self.assertRaises(SystemExit, msg=str(argv)) as caught,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.main(*argv, stdin="x")
            self.assertEqual(caught.exception.code, 2)
        code, _, err = self.main("note", "--window", "", "--status", stdin="x")
        self.assertEqual(code, 2)
        self.assertIn("needs the window's name", err)
        code, _, err = self.main("note", "--window", "api", "--status", stdin=" \n")
        self.assertEqual(code, 1)
        self.assertIn("empty", err)
        self.assertFalse((self.state / "update" / "notes").exists())

    def test_advance_skips_quietly_while_the_update_itself_runs(self) -> None:
        self.record("agents", pending=("api",), windows={"api": ("repo", "restart")})
        with update_state.one_update(self.state), mock.patch.object(update_windows, "turn") as turn:
            self.assertEqual(self.main("advance"), (0, "", ""))
        turn.assert_not_called()

    def test_advance_takes_a_turn_while_windows_are_pending(self) -> None:
        self.record("agents", pending=("api",), windows={"api": ("repo", "restart")})
        with mock.patch.object(update_windows, "turn", return_value=["api"]) as turn:
            self.assertEqual(self.main("advance")[0], 0)
        turn.assert_called_once()
        self.assertEqual(turn.call_args.args[0], self.state)

    def test_advance_that_finishes_the_update_saves_its_summary(self) -> None:
        """The check-in loop's turn that takes the last answer ends the update: its summary is saved."""
        backup = self.backup()
        self.record("agents", backup=str(backup), pending=("api",), windows={"api": ("repo", "restart")})

        def last_answer(state_dir: Path, say: Any) -> list[str]:
            state = update_state.read(state_dir)
            assert state is not None
            update_state.write(state_dir, update_state.advanced(state, "done", pending=()))
            return []

        with mock.patch.object(update_windows, "turn", side_effect=last_answer):
            code, out, _ = self.main("advance")
        self.assertEqual(code, 0)
        self.assertIn("bosun updated:", (backup / "summary.md").read_text())
        self.assertIn(f"bosun update: done; its summary is in {backup / 'summary.md'}", out)

    def test_after_update_says_what_the_last_update_left_me_for_bosun_doctor(self) -> None:
        """A line each, its kind and a tab first, from the record and bosun.env (left_to_me)."""
        self.record("done", user_changes=("sessions.yml: rename model:\nto agent_model:",))
        state = update_state.read(self.state)
        assert state is not None
        plan = {"keys_added": ["BOSUN_EXPORT_RETRIES", "BOSUN_EXPORT_TIMEOUT"]}
        update_state.write(self.state, dataclasses.replace(state, plan=plan))
        (self.config / "bosun.env").write_text("export BOSUN_EXPORT_TIMEOUT=30\n")
        argv = ("after-update", "--state-dir", str(self.state), "--config-dir", str(self.config))
        self.assertEqual(
            self.main(*argv),
            (0, "change\tsessions.yml: rename model: to agent_model:\nkey\tBOSUN_EXPORT_RETRIES\n", ""),
        )
        (self.state / update_state.STATE_FILE).unlink()
        self.assertEqual(self.main(*argv), (0, "", ""))

    def test_after_update_gives_a_new_key_s_note_where_it_holds(self) -> None:
        """A third column: the note of a key at its default. A kept key bosun.env does not set (no
        bosun.env to write it to) is at its default too, so its note ("keeps every connector") would be
        false: none."""
        self.record("done")
        state = update_state.read(self.state)
        assert state is not None
        plan = {"keys_added": ["BOSUN_CONNECTORS", "BOSUN_ORCHESTRATOR_CONNECTORS"]}
        update_state.write(self.state, dataclasses.replace(state, plan=plan))
        note = next(entry.note for entry in migrations.CONFIG_ADDED if entry.key == "BOSUN_CONNECTORS")
        argv = ("after-update", "--state-dir", str(self.state), "--config-dir", str(self.config))
        self.assertEqual(
            self.main(*argv), (0, f"key\tBOSUN_CONNECTORS\t{note}\nkey\tBOSUN_ORCHESTRATOR_CONNECTORS\n", "")
        )

    def test_advance_with_nothing_pending_does_nothing(self) -> None:
        for step in ("done", "released"):
            self.record(step)
            with mock.patch.object(update_windows, "turn") as turn:
                self.assertEqual(self.main("advance"), (0, "", ""))
            turn.assert_not_called()


OWN_LABELS = ("com.bosun.morning.watchdog", "com.bosun.morning.learn")  # the jobs a conversion stops


class FakeShell:
    """update_facts.shell for a conversion: bosun's own jobs, two of them loaded, and what each script
    the update ran was, in order."""

    def __init__(self) -> None:
        self.ran: list[tuple[str, tuple[str, ...]]] = []
        self.stop_fails = False

    def __call__(self, root: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        self.ran.append((script, args))
        labels = "".join(f"{label}\n" for label in OWN_LABELS)
        out = {"loaded_own_jobs": labels, "stop_own_jobs": labels, "reload_changed_jobs": labels}.get(
            script, ""
        )
        code = 1 if script == "stop_own_jobs" and self.stop_fails else 0
        return subprocess.CompletedProcess([script], code, out, "could not unload it" if code else "")

    def scripts(self) -> list[str]:
        return [script for script, _ in self.ran]


class ConvertTest(World):
    """The first update, from a checkout of bosun at the install path (docs/design/update.md, "The first
    update converts, at a quiet moment"): the stores real, the swap real, bosun's commands faked."""

    def setUp(self) -> None:
        super().setUp()
        self.install.unlink()
        self.install.mkdir()
        (self.install / "bin").mkdir()
        (self.install / "bin" / "bosun").write_text("#!/bin/sh\n# the release before versions\n")
        identity = ["-c", "user.name=acme", "-c", "user.email=dev@example.com", "-c", "commit.gpgsign=false"]
        origin = self.install.parent / "origin.git"
        versions.git("init", "-q", "--bare", str(origin))
        for argv in (
            ["init", "-q", "-b", "main"],
            ["add", "-A"],
            [*identity, "commit", "-qm", "a release"],
            ["remote", "add", "origin", str(origin)],
            ["push", "-q", "origin", "main"],
            ["fetch", "-q", "origin"],
        ):
            versions.git("-C", str(self.install), *argv)
        self.commit = versions.commit_of(self.install)
        self.legacy = versions.legacy_folder(self.new.parent, self.commit)
        self.shell = FakeShell()
        self.bosun_ran: list[tuple[str, ...]] = []
        self.loop: int | None = None
        self.quiet = [update_facts.Quiet((("orch", "idle"), ("api", "exited")), None, 10)]
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for name, fake in (
            ("plan_for", lambda *args: dataclasses.replace(a_plan(), from_commit=self.commit)),
            ("shell", self.shell),
            ("bosun", self.bosun),
            ("open_windows", lambda root: [{"kind": "checkin", "name": "checkin", "window": True}]),
            ("quiet_facts", lambda root: self.quiet.pop(0) if len(self.quiet) > 1 else self.quiet[0]),
            ("checkin_loop_pid", lambda root: self.loop if self.loop_runs() else None),
        ):
            stack.enter_context(mock.patch.object(update_facts, name, side_effect=fake))
        stack.enter_context(mock.patch.object(update, "_doctor", return_value=[]))
        stack.enter_context(mock.patch.object(update, "QUIET_POLL_SECONDS", 0))
        stack.enter_context(mock.patch.object(update, "ROOT", self.new))
        stack.enter_context(mock.patch.object(update_windows, "bring_along", side_effect=self.brought_along))
        self.addCleanup(setattr, update.Output, "to_stderr", False)

    def brought_along(self, state: UpdateState, plan: update_plan.Plan, say: Any) -> UpdateState:
        return update._step(self.state, state, "done")

    def on_the_real_stores(self, fake: Callable[..., Any], real: Callable[..., Any]) -> Callable[..., Any]:
        """<fake> where the freeze calls it, on the real state folder; <real> on the rehearsal's copy."""

        def call(first: Any, *args: Any) -> Any:
            where = first.state_dir if isinstance(first, migrations.Context) else first
            return (fake if where == self.state else real)(first, *args)

        return call

    def bosun(self, root: Path, *args: str, **_: Any) -> subprocess.CompletedProcess[str]:
        self.bosun_ran.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def a_loop(self) -> int:
        """A stand-in for the check-in loop: a process to stop, whose parent is not this one (as the
        loop's is its pane's shell, which reaps it once it stops)."""
        started = subprocess.run(
            ["sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"], capture_output=True, text=True, check=True
        )
        self.loop = int(started.stdout)
        self.addCleanup(self.kill_loop)
        return self.loop

    def kill_loop(self) -> None:
        if self.loop:
            with contextlib.suppress(ProcessLookupError):
                os.kill(self.loop, signal.SIGKILL)

    def loop_runs(self) -> bool:
        if not self.loop:
            return False
        try:
            os.kill(self.loop, 0)
        except ProcessLookupError:
            return False
        return True

    def mark(self) -> Path:
        return self.state / update.OWN_WORK

    def assert_own_work_back(self, loop: bool = False) -> None:
        """What the conversion stopped runs again as it was, once: the jobs it stopped loaded from
        their plists on disk and no other (no render, and so never the morning job), the check-in loop
        restarted when it stopped it; and its mark gone."""
        starts = [args for script, args in self.shell.ran if script == 'start_jobs "$@"']
        self.assertEqual(starts, [OWN_LABELS])
        self.assertNotIn("reload_changed_jobs", self.shell.scripts())
        self.assertEqual(("refresh", "--checkin-loop") in self.bosun_ran, loop)
        self.assertFalse(self.mark().exists())

    def cut_short(self, step: str) -> None:
        """What a crash at <step> leaves of a conversion that stopped bosun's own work: the record, as an
        older bosun's write gate rewrites it (only the fields it knows), and the mark of what was stopped."""
        record = {
            "id": "c1",
            "from_root": str(self.install),
            "to_root": str(self.new),
            "from_commit": self.commit,
            "to_commit": TO,
            "step": step,
            "started_at": "2026-11-07T12:00:00+00:00",
            "hold_until": 0,
        }
        (self.state / update_state.STATE_FILE).write_text(json.dumps(record))
        self.mark().parent.mkdir(parents=True, exist_ok=True)
        self.mark().write_text(json.dumps({"jobs": list(OWN_LABELS), "loop": True}))

    def convert(self, *flags: str) -> tuple[int, str]:
        args = update.build_parser().parse_args(["--from", str(self.install), "--yes", *flags])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = update.convert(self.install, self.new, args)
        return code, out.getvalue()

    def test_a_checkout_with_anything_of_yours_in_it_is_refused_with_nothing_changed(self) -> None:
        (self.install / "notes.txt").write_text("mine\n")
        with self.assertRaises(update_facts.UpdateRefused) as refused:
            self.convert()
        self.assertIn(f"{self.install} has uncommitted changes", str(refused.exception))
        self.assertIn("run get.sh again", str(refused.exception))
        self.assertTrue(self.install.is_dir() and not self.install.is_symlink())
        self.assertIsNone(update_state.read(self.state))
        self.assertEqual(self.shell.ran, [])

    def test_it_waits_for_a_quiet_moment_saying_what_it_waits_for_once_each(self) -> None:
        busy = update_facts.Quiet((("orch", "idle"), ("api", "busy"), ("web", "shell")), None, 10)
        typed = update_facts.Quiet((("orch", "idle"), ("api", "idle")), 3, 10)
        self.quiet = [busy, busy, typed, update_facts.Quiet((("api", "idle"),), 10, 10)]
        code, out = self.convert()
        self.assertEqual(code, 0, out)
        waits = [line for line in out.splitlines() if line.startswith("Waiting for a quiet moment")]
        self.assertEqual(
            waits,
            [
                "Waiting for a quiet moment to convert: api (busy); web (shell)",
                (
                    "Waiting for a quiet moment to convert: someone typed in the session 3 min ago"
                    " (quiet after 10)"
                ),
            ],
        )

    def test_ctrl_c_while_it_waits_changes_nothing(self) -> None:
        self.a_loop()
        self.quiet = [update_facts.Quiet((("api", "busy"),), None, 10)]
        with mock.patch.object(time, "sleep", side_effect=KeyboardInterrupt):
            code, out = self.convert()
        self.assertEqual(code, update.BLOCKED)
        self.assertIn("Nothing has changed: run get.sh again at a quieter moment", out)
        self.assertTrue(self.install.is_dir() and not self.install.is_symlink())
        self.assertIsNone(update_state.read(self.state))
        self.assertEqual(self.shell.ran, [])
        self.assertTrue(self.loop_runs())  # the check-in loop still runs

    def test_its_own_work_stops_before_the_backup_and_the_checkout_is_swapped_for_the_link(self) -> None:
        self.a_loop()
        before_backup: list[tuple[list[str], bool]] = []
        real_take = update_backups.take

        def take(*args: Any) -> Path:
            before_backup.append((self.shell.scripts(), self.loop_runs()))
            return real_take(*args)

        with mock.patch.object(update_backups, "take", side_effect=take):
            code, out = self.convert()
        self.assertEqual(code, 0, out)
        self.assertEqual(before_backup, [(["loaded_own_jobs", "stop_own_jobs"], False)])
        self.assertEqual(versions.current(self.install), self.new)
        self.assertEqual(
            (self.legacy / "bin" / "bosun").read_text(), "#!/bin/sh\n# the release before versions\n"
        )
        recorded = update_state.read(self.state)
        assert recorded is not None
        self.assertEqual((recorded.step, recorded.from_root), ("done", str(self.legacy)))
        self.assertIn("reload_changed_jobs", self.shell.scripts())  # the release, on the link
        self.assertIn(("refresh", "--checkin-loop"), self.bosun_ran)
        self.assertNotIn('start_jobs "$@"', self.shell.scripts())
        self.assertFalse(self.mark().exists())
        self.assertIn(f"The checkout is kept as {self.legacy}", out)
        self.assertIn("bosun updated: ", out)

    def test_a_window_busy_again_by_the_stop_is_waited_for_before_anything_is_frozen(self) -> None:
        """Important 1: the doctor run and the rehearsal take a while after the first wait, and the old
        loop could prompt a window meanwhile; once bosun's own work is stopped it waits again."""
        quiet = update_facts.Quiet((("api", "idle"),), None, 10)
        busy = update_facts.Quiet((("api", "busy"),), None, 10)
        self.quiet = [quiet, busy, busy, quiet]
        left_at_backup: list[int] = []
        real_take = update_backups.take

        def take(*args: Any) -> Path:
            left_at_backup.append(len(self.quiet))
            return real_take(*args)

        with mock.patch.object(update_backups, "take", side_effect=take):
            code, out = self.convert()
        self.assertEqual(code, 0, out)
        self.assertEqual(left_at_backup, [1])  # every reading taken, the last one quiet, before the freeze
        self.assertEqual(out.count("Waiting for a quiet moment to convert: api (busy)"), 1)
        self.assertLess(out.index("stopped the launchd job"), out.index("Waiting for a quiet moment"))

    def test_ctrl_c_in_the_wait_after_the_stop_starts_its_own_work_again(self) -> None:
        self.quiet = [update_facts.Quiet((), None, 10), update_facts.Quiet((("api", "busy"),), None, 10)]
        with mock.patch.object(time, "sleep", side_effect=KeyboardInterrupt):
            code, out = self.convert()
        self.assertEqual(code, 1, out)
        recorded = update_state.read(self.state)
        assert recorded is not None
        self.assertEqual(recorded.step, "failed")
        self.assertIn("quiet moment", recorded.error)
        self.assertTrue(self.install.is_dir() and not self.install.is_symlink())
        self.assert_own_work_back()

    def test_a_ctrl_c_while_its_own_work_stops_starts_it_again(self) -> None:
        """Minor 1: part of it may be stopped by then (here the jobs, as the loop was about to stop); the
        loop, which never stopped, is left running, not restarted mid-tick (final review, Minor 4)."""
        self.a_loop()
        with (
            mock.patch.object(update, "_stop_loop", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.convert()
        self.assert_own_work_back(loop=False)
        self.assertTrue(self.loop_runs())

    def test_the_mark_names_what_runs_before_anything_stops(self) -> None:
        """Written before the first stop, so a crash at any point of it is covered."""
        self.a_loop()
        seen: list[Any] = []
        real = self.shell.__call__

        def shell(root: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
            if script == "stop_own_jobs":
                seen.append(json.loads(self.mark().read_text()))
            return real(root, script, *args)

        with mock.patch.object(update_facts, "shell", side_effect=shell):
            self.assertEqual(self.convert()[0], 0)
        self.assertEqual(seen, [{"jobs": list(OWN_LABELS), "loop": True}])
        self.assertFalse(self.mark().exists())  # the release brought it all back

    def test_the_next_update_starts_again_what_a_conversion_cut_short_left_stopped(self) -> None:
        """The mark says what was stopped, whatever wrote the record since: a crash while stopping
        (rehearsed), in the hold, in the freeze, or after an older version's write put the freeze back
        (restored, its record rewritten with only the fields it knows). Once, whatever runs next."""
        for step in ("rehearsed", "holding", "marked", "restored"):
            with self.subTest(step=step):
                self.shell.ran.clear()
                self.bosun_ran.clear()
                self.cut_short(step)
                with contextlib.redirect_stdout(io.StringIO()):
                    update.resume(self.state)
                    update.resume(self.state)
                self.assert_own_work_back(loop=True)
                recorded = update_state.read(self.state)
                assert recorded is not None
                self.assertIn(recorded.step, ("failed", "restored"))

    def test_a_mark_with_no_record_is_acted_on(self) -> None:
        """Minor 3: the record moved aside (as an unreadable one is), or deleted: what the mark names
        still runs again, once."""
        self.cut_short("holding")
        (self.state / update_state.STATE_FILE).unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(update.resume(self.state))
            update.resume(self.state)
        self.assert_own_work_back(loop=True)

    def test_a_check_in_loop_that_runs_is_not_restarted(self) -> None:
        """Minor 4: one started since (bosun start, the morning job), or one never stopped (a Ctrl-C
        before the stop, stop_own_jobs failing first): refresh --checkin-loop would stop it mid-tick."""
        self.cut_short("holding")
        self.a_loop()
        with contextlib.redirect_stdout(io.StringIO()):
            update.resume(self.state)
        self.assert_own_work_back(loop=False)
        self.assertTrue(self.loop_runs())

    def test_an_update_that_stopped_nothing_starts_nothing(self) -> None:
        self.cut_short("holding")
        self.mark().unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            update.resume(self.state)
        self.assertEqual((self.shell.ran, self.bosun_ran), ([], []))

    def test_the_loop_a_put_back_restarts_runs_the_old_code(self) -> None:
        """`refresh --checkin-loop` of the new version respawns the pane with the install path's bosun,
        which a put back leads to the old checkout again (kept as legacy-* after the swap)."""
        shutil.copytree(Path(__file__).resolve().parents[2] / "lib", self.new / "lib")
        shutil.move(self.install, self.legacy)
        versions.point(self.install, self.legacy)  # as a put back after the swap leaves it
        script = (
            'source "$1/lib/common.sh"; build_launch_command checkin "" checkin; '
            'printf "%s\\n" "${launch_cmd[@]}"'
        )
        env = {"HOME": str(self.state.parent), "PATH": os.environ["PATH"], "SHELL": "/bin/sh"}
        env["BOSUN_TMUX_SOCKET"] = f"bosun-unit-{os.getpid()}"  # a server that is not there
        done = subprocess.run(
            ["bash", "-c", script, "_", str(self.new)], env=env, capture_output=True, text=True, check=False
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"{self.install}/bin/bosun checkin --loop", done.stdout)
        self.assertEqual(
            os.path.realpath(self.install / "bin" / "bosun"), os.path.realpath(self.legacy / "bin" / "bosun")
        )

    def test_a_window_busy_past_the_bound_brings_its_own_work_back_and_waits_again(self) -> None:
        """After the stop it waits QUIET_AFTER_STOP_SECONDS at the most; then bosun's own work runs
        again while it waits for a quiet moment, and it plans again, as after the first wait."""
        counts = {"stops": 0, "starts": 0, "readings": 0}
        real = self.shell.__call__

        def shell(root: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
            counts["stops"] += script == "stop_own_jobs"
            counts["starts"] += script == 'start_jobs "$@"'
            return real(root, script, *args)

        def quiet(root: Path) -> update_facts.Quiet:
            counts["readings"] += 1
            busy = counts["stops"] == 1 and (counts["starts"] == 0 or counts["readings"] % 3)
            return update_facts.Quiet((("api", "busy" if busy else "idle"),), None, 10)

        # Three plans, one after each wait; the fourth, made again once released, brings the windows along.
        plans = [
            dataclasses.replace(a_plan(), from_commit=self.commit, commits=(f"plan {n}",)) for n in range(4)
        ]
        frozen_at: list[int] = []
        real_take = update_backups.take

        def take(*args: Any) -> Path:
            frozen_at.append(counts["stops"])
            return real_take(*args)

        with (
            mock.patch.object(update_facts, "shell", side_effect=shell),
            mock.patch.object(update_facts, "quiet_facts", side_effect=quiet),
            mock.patch.object(update_facts, "plan_for", side_effect=plans),
            mock.patch.object(update_backups, "take", side_effect=take),
            mock.patch.object(update, "QUIET_AFTER_STOP_SECONDS", 0.05),
        ):
            code, out = self.convert()
        self.assertEqual(code, 0, out)
        self.assertIn("starting it again, and waiting for a quiet moment with everything running", out)
        self.assertEqual(counts["starts"], 1)  # brought back once, at the bound
        self.assertEqual(frozen_at, [2])  # nothing frozen before the second stop
        recorded = update_state.read(self.state)
        assert recorded is not None
        self.assertEqual(recorded.plan, update_plan.as_record(plans[2]))  # the plan after the last wait

    def test_the_checkout_lands_where_the_record_says(self) -> None:
        """Minor 4: one derivation of the legacy folder, so a HEAD that moved since the plan cannot leave
        the record naming a folder that is not there."""
        named = versions.legacy_folder(self.new.parent, "c" * 40)
        plan = dataclasses.replace(a_plan(), from_commit="c" * 40)
        with mock.patch.object(update_facts, "plan_for", return_value=plan):
            code, out = self.convert()
        self.assertEqual(code, 0, out)
        recorded = update_state.read(self.state)
        assert recorded is not None
        self.assertEqual(recorded.from_root, str(named))
        self.assertEqual((named / "bin" / "bosun").read_text(), "#!/bin/sh\n# the release before versions\n")

    def test_a_failure_before_the_switch_starts_its_own_work_again_as_it_was(self) -> None:
        self.a_loop()

        def fails(ctx: migrations.Context) -> list[migrations.Applied]:
            raise migrations.MigrationError("no way from 1")

        with mock.patch.object(migrations, "run", side_effect=self.on_the_real_stores(fails, migrations.run)):
            code, out = self.convert()
        self.assertEqual(code, 1)
        self.assertIn("no way from 1", out)
        self.assertTrue(self.install.is_dir() and not self.install.is_symlink())
        self.assertFalse(os.path.lexists(self.legacy))
        self.assert_own_work_back(loop=True)

    def test_a_job_that_will_not_stop_stops_it_before_anything_changed(self) -> None:
        self.shell.stop_fails = True
        code, out = self.convert()
        self.assertEqual(code, 1)
        self.assertIn("launchd: could not unload it", out)
        recorded = update_state.read(self.state)
        assert recorded is not None
        self.assertEqual(recorded.step, "failed")
        self.assertFalse(recorded.backup)
        self.assert_own_work_back()
        self.assertTrue(self.install.is_dir() and not self.install.is_symlink())

    def test_a_conversion_failing_after_the_switch_leads_the_install_to_the_old_code_again(self) -> None:
        """The checkout is legacy-* by then, and the record names it from the switch on."""
        ledger_before = (self.state / "ledger.json").read_bytes()

        def damaged(state_dir: Path, config_dir: Path) -> list[str]:
            (state_dir / "ledger.json").write_text('{"version": 2, "items": {}}')
            return ["the ledger: journal: damaged"]

        checks = self.on_the_real_stores(damaged, update.check_stores)
        with mock.patch.object(update, "check_stores", side_effect=checks):
            code, out = self.convert()
        self.assertEqual(code, 1, out)
        self.assertEqual(versions.current(self.install), self.legacy)
        self.assertEqual(
            (self.install / "bin" / "bosun").read_text(), "#!/bin/sh\n# the release before versions\n"
        )
        self.assertEqual((self.state / "ledger.json").read_bytes(), ledger_before)
        recorded = update_state.read(self.state)
        assert recorded is not None
        self.assertEqual((recorded.step, recorded.from_root), ("failed", str(self.legacy)))
        self.assert_own_work_back()

    def test_a_dry_run_says_what_converting_adds_and_changes_nothing(self) -> None:
        code, out = self.convert("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn(f"This first update converts {self.install}, a checkout from before versions", out)
        self.assertIn(f"keeping the checkout as {self.legacy}", out)
        self.assertTrue(self.install.is_dir() and not self.install.is_symlink())
        self.assertEqual(self.shell.ran, [])
        self.assertIsNone(update_state.read(self.state))

    def test_bosun_update_from_a_checkout_or_its_legacy_folder_converts(self) -> None:
        """The dispatch, after an update cut short is carried on: from the checkout, or from the legacy
        folder a conversion put back leads the link to."""
        checkout = self.install.resolve()
        with mock.patch.object(update, "convert", return_value=0) as converting:
            update.main(["--from", str(self.install), "--yes"])
            shutil.move(self.install, self.legacy)  # as a conversion put back leaves it
            versions.point(self.install, self.legacy)
            update.main(["--from", str(self.legacy), "--dry-run"])
        self.assertEqual(
            [call.args[:2] for call in converting.call_args_list],
            [(checkout, self.new), (self.legacy.resolve(), self.new)],
        )


class StartJobsTest(unittest.TestCase):
    """A put back loads the jobs a conversion stopped from their plists as they are on disk
    (lib/common.sh's start_jobs), against a stand-in launchctl in a scratch home: the shell refuses to
    go on if any other launchctl would answer."""

    def test_only_the_named_jobs_that_are_not_loaded_from_their_plists_as_they_are(self) -> None:
        home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        agents, tools = home / "Library" / "LaunchAgents", home / ".local" / "bin"
        agents.mkdir(parents=True)
        tools.mkdir(parents=True)
        (home / "loaded").write_text("com.acme.morning.learn\n")  # a stop cut short left it loaded
        launchctl = tools / "launchctl"
        launchctl.write_text(
            '#!/bin/sh\necho "$*" >>"$HOME/launchctl.log"\n'
            '[ "$1" != print ] || grep -qx "${2##*/}" "$HOME/loaded"\n'
        )
        launchctl.chmod(0o755)
        plists = {
            label: agents / f"{label}.plist"
            for label in ("com.acme.morning", "com.acme.morning.watchdog", "com.acme.morning.learn")
        }
        for label, path in plists.items():
            path.write_text(f"<plist>{label}, as install.sh rendered it</plist>\n")
        before = {label: path.read_bytes() for label, path in plists.items()}
        script = (
            'source "$1/lib/common.sh"; '
            '[ "$(command -v launchctl)" = "$HOME/.local/bin/launchctl" ] || exit 99; '
            "start_jobs com.acme.morning.watchdog com.acme.morning.learn"
        )
        root = Path(__file__).resolve().parents[2]
        done = subprocess.run(
            ["bash", "-c", script, "_", str(root)],
            env={"HOME": str(home), "PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        uid = os.getuid()
        self.assertEqual(
            (home / "launchctl.log").read_text().splitlines(),
            [
                f"print gui/{uid}/com.acme.morning.watchdog",
                f"bootstrap gui/{uid} {plists['com.acme.morning.watchdog']}",
                f"print gui/{uid}/com.acme.morning.learn",
            ],
        )
        self.assertEqual({label: path.read_bytes() for label, path in plists.items()}, before)


class QuietTest(unittest.TestCase):
    def test_idle_or_no_agent_is_quiet_and_anything_else_is_waited_for(self) -> None:
        statuses = (
            ("a", "idle"),
            ("b", "exited"),
            ("c", "busy"),
            ("d", "shell"),
            ("e", "waiting"),
            ("f", "unknown"),
        )
        self.assertEqual(
            update.waiting_for(update_facts.Quiet(statuses, None, 10)),
            ["c (busy)", "d (shell)", "e (waiting)", "f (unknown)"],
        )

    def test_typing_counts_within_the_active_minutes(self) -> None:
        for typed, waits in ((None, False), (0, True), (9, True), (10, False), (25, False)):
            self.assertEqual(bool(update.waiting_for(update_facts.Quiet((), typed, 10))), waits, typed)
        self.assertEqual(update.waiting_for(update_facts.Quiet((), 0, 0)), [])


class UsageTest(unittest.TestCase):
    def test_the_usage_says_the_update_ends_with_its_summary_and_json_carries_it(self) -> None:
        """`bosun update --help` (libexec/bosun-update's header) and docs/commands.md say the same."""
        root = Path(__file__).resolve().parents[2]
        header = " ".join(
            line.lstrip("#").strip()
            for line in (root / "libexec" / "bosun-update").read_text().splitlines()
            if line.startswith("#")
        )
        self.assertIn("ends with a summary of what it did", header)
        self.assertIn("it is saved as summary.md in the backup's folder", header)
        self.assertIn("and the summary (null unless an update ended in this run)", header)
        row = next(
            line
            for line in (root / "docs" / "commands.md").read_text().splitlines()
            if "`bosun update [" in line
        )
        for said in (
            "It ends with a short summary",
            "`summary.md`",
            "and the summary (`null` unless an update ended",
        ):
            self.assertIn(said, row)


class GoAheadTest(unittest.TestCase):
    def test_without_a_terminal_only_the_plan(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("")), contextlib.redirect_stdout(out):
            self.assertFalse(update._go_ahead(yes=False))
        self.assertIn("Nothing has changed", out.getvalue())
        self.assertTrue(update._go_ahead(yes=True))


if __name__ == "__main__":
    unittest.main()
