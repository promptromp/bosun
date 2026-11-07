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
import subprocess
import sys
import tempfile
import threading
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

    def test_what_doctor_fails_or_warns_about_is_kept(self) -> None:
        said = "Tools\n  ok    tmux\n  warn  on battery\n  FAIL  login keychain locks on sleep\nall\n"
        with mock.patch.object(
            update_facts, "bosun", return_value=subprocess.CompletedProcess([], 1, said, "")
        ):
            self.assertEqual(
                update._doctor(self.new), ["warn: on battery", "FAIL: login keychain locks on sleep"]
            )


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
        """R6: the windows still waited for are the check-in loop's; resume does not run them again."""
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
        self.assertEqual(said.summary.backup_bytes, 2000)  # what the files hold, the summary not counted
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

    def test_advance_with_nothing_pending_does_nothing(self) -> None:
        for step in ("done", "released"):
            self.record(step)
            with mock.patch.object(update_windows, "turn") as turn:
                self.assertEqual(self.main("advance"), (0, "", ""))
            turn.assert_not_called()


class GoAheadTest(unittest.TestCase):
    def test_without_a_terminal_only_the_plan(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("")), contextlib.redirect_stdout(out):
            self.assertFalse(update._go_ahead(yes=False))
        self.assertIn("Nothing has changed", out.getvalue())
        self.assertTrue(update._go_ahead(yes=True))


if __name__ == "__main__":
    unittest.main()
