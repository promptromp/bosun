"""lib/update_back.py: `bosun update --back` over a ledger made by real writes and the backup an update took
of it (docs/design/update.md, "Going back, and trying a branch"): what refuses it, a back that keeps what was
written since, one that restores a store, a failed one put back, and the wrapper's check of a target against
the data. The end to end is the smoke suite's update-back sections. Fictional data."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import ledger
import migrations
import update
import update_back
import update_back_plan
import update_backups
import update_facts
import update_lookup
import update_state
import versions
from test_update import FROM, TO, World
from update_state import UpdateState


NOW = datetime(2026, 11, 18, 14, 2, 7, tzinfo=UTC)


def source(
    folder: Path, *, ledger_format: int = 1, rules: int = 1, kg_format: int = 1, kg_rules: int = 1
) -> None:
    """The lines of a version's source the back reads its formats and rules versions from."""
    lib = folder / "lib"
    lib.mkdir(exist_ok=True)
    (lib / "ledger.py").write_text(f"LEDGER_FORMAT = {ledger_format}\nRULES_VERSION = {rules}\n")
    (lib / "knowledge_store.py").write_text(f"KNOWLEDGE_FORMAT = {kg_format}\n")
    (lib / "knowledge.py").write_text(f"KG_RULES_VERSION = {kg_rules}\n")


class BackWorld(World):
    """World's install after an update from the old version to the new one: the link at the new one, its
    record done, and the backup it took of the two items written before; then one more item written."""

    def setUp(self) -> None:
        super().setUp()
        source(self.old)
        source(self.new)
        self.update_backup = update_backups.take(
            self.state, self.config, FROM, TO, {"ledger": 1, "knowledge": 0}, NOW
        )
        versions.point(self.install, self.new)
        self.over = self.record("done", backup=str(self.update_backup))
        self.ledger("upsert", "review:acme/exporter#21", "--kind", "review-request", "--title", "since")
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        commits = {self.old.resolve(): FROM, self.new.resolve(): TO}

        def commit_of(root: Path) -> str:
            if Path(root).resolve() not in commits:
                raise RuntimeError(f"cannot read the commit of {root}: not a git repository")
            return commits[Path(root).resolve()]

        stack.enter_context(mock.patch.object(versions, "commit_of", side_effect=commit_of))
        for module in (update, update_back):
            stack.enter_context(mock.patch.object(module, "ROOT", self.new))
        for name, value in (
            ("window_facts", []),
            ("jobs", ([], [])),
            ("missing", []),
            ("commits", []),
            ("dropped_jobs", []),
            ("open_windows", []),
        ):
            stack.enter_context(mock.patch.object(update_facts, name, return_value=value))
        stack.enter_context(mock.patch.object(update, "_doctor", return_value=[]))
        stack.enter_context(mock.patch.object(update, "_reload_jobs", return_value=([], [])))
        stack.enter_context(mock.patch.object(update, "_prune", return_value=[]))
        stack.enter_context(mock.patch.dict(os.environ, {"BOSUN_UPDATE_CHANNEL": "main"}))

    def items(self) -> set[str]:
        return set(ledger.load_at(self.state)["items"])

    def where(self) -> update_facts.Folders:
        return update_facts.folders()

    @contextlib.contextmanager
    def windows_answer(self) -> Iterator[mock.MagicMock]:
        """The windows brought along at once: the record done."""

        def done(state: UpdateState) -> UpdateState:
            ended = update_state.advanced(state, "done")
            update_state.write(self.state, ended)
            return ended

        with mock.patch.object(update, "_windows_brought_along", side_effect=done) as brought:
            yield brought

    def back(self) -> UpdateState:
        with self.windows_answer():
            return update_back.carried_out(update_back.facts(self.where()), self.where())


class RefusedTest(BackWorld):
    def refused(self) -> str:
        with self.assertRaises(update_facts.UpdateRefused) as caught:
            update_back.facts(self.where())
        return str(caught.exception)

    def test_with_no_update_finished_there_is_nothing_to_go_back_to(self) -> None:
        (self.state / update_state.STATE_FILE).unlink()
        self.assertEqual(self.refused(), update_back_plan.NOTHING_FINISHED)
        self.record("failed")
        self.assertEqual(self.refused(), update_back_plan.NOTHING_FINISHED)

    def test_a_second_back_is_refused(self) -> None:
        update_state.write(
            self.state,
            dataclasses.replace(
                self.over, kind=update_state.BACK, from_root=str(self.new), to_root=str(self.old)
            ),
        )
        self.assertIn("bosun already went back", self.refused())

    def test_an_install_that_leads_elsewhere_is_left_to_be_done_by_hand(self) -> None:
        versions.point(self.install, self.old)
        self.assertIn("going back is then by hand", self.refused())

    def test_a_checkout_from_before_versions_is_left_to_be_done_by_hand(self) -> None:
        legacy = versions.legacy_folder(self.new.parent, FROM)
        legacy.mkdir()
        update_state.write(self.state, dataclasses.replace(self.over, from_root=str(legacy)))
        self.assertIn("checkout from before versions", self.refused())

    def test_a_restore_without_the_backup_a_forget_deleted_is_blocked_in_the_plan(self) -> None:
        source(self.new, rules=2)  # the ledger's rules changed: it needs the backup
        update_backups.delete_all(self.state)
        found = update_back.facts(self.where())
        [blocker] = found.plan.blockers
        self.assertIn("the ledger needs the backup that update took (rules 2 -> 1)", blocker)

    def test_none_needed_the_back_goes_on_without_it(self) -> None:
        update_backups.delete_all(self.state)
        self.assertEqual(update_back.facts(self.where()).plan.blockers, ())


class UnseenTest(BackWorld):
    """A dry run whose older folder is gone adds none: it reads what it can of that version from bosun.git."""

    def setUp(self) -> None:
        super().setUp()
        source(self.new, rules=2)  # the ledger needs restoring: decided from the older version's source
        self.files = {
            path.relative_to(self.old).as_posix(): path.read_bytes()
            for path in self.old.rglob("*")
            if path.is_file()
        }
        shutil.rmtree(self.old)

    @contextlib.contextmanager
    def bosun_git(self, has: bool) -> Iterator[None]:
        """bosun.git holding the older commit (<has>), its files as the folder had them."""
        with (
            mock.patch.object(versions, "has_commit", return_value=has),
            mock.patch.object(versions, "show", side_effect=lambda _r, _c, path: self.files.get(path)),
            mock.patch.object(versions, "tree", return_value=sorted(self.files)),
        ):
            yield

    def test_it_reads_the_older_version_from_bosun_git_and_adds_no_folder(self) -> None:
        with self.bosun_git(True):
            found = update_back.facts(self.where(), dry_run=True)
        self.assertFalse(self.old.exists())
        self.assertFalse(found.older.present)
        [ledger_part] = found.back.restored()
        self.assertEqual(ledger_part.why(), "rules 2 -> 1")
        [note] = found.plan.unknown
        self.assertIn(
            f"{self.old} is not there: going back adds it again from bosun.git, at {FROM[:7]}", note
        )
        self.assertIn("Not known in this dry run:", update_back_plan.render(found.plan, found.back))

    def test_without_the_commit_either_it_says_what_it_cannot_tell(self) -> None:
        with self.bosun_git(False):
            found = update_back.facts(self.where(), dry_run=True)
        self.assertEqual((found.back.stores, found.plan.blockers), ((), ()))
        [note] = found.plan.unknown
        self.assertIn(f"nor {FROM[:7]} in bosun.git: going back fetches it from the origin first", note)
        self.assertFalse(self.old.exists())


class RefusedMoreTest(BackWorld):
    def test_a_link_moved_by_hand_to_a_folder_git_cannot_read_is_refused_not_a_traceback(self) -> None:
        elsewhere = self.state.parent / "elsewhere"
        elsewhere.mkdir()
        versions.point(self.install, elsewhere)
        with self.assertRaises(update_facts.UpdateRefused) as caught:
            update_back.facts(self.where())
        self.assertIn("going back is then by hand", str(caught.exception))

    def test_a_backup_of_another_update_is_said_once(self) -> None:
        manifest = json.loads((self.update_backup / update_backups.MANIFEST).read_text())
        manifest["to"] = "c" * 40
        (self.update_backup / update_backups.MANIFEST).write_text(json.dumps(manifest))
        self.assertEqual(update_back.facts(self.where()).plan.blockers, ())  # nothing needs it
        source(self.new, rules=2)
        [blocker] = update_back.facts(self.where()).plan.blockers
        self.assertIn("is of another update (aaaaaaa -> ccccccc)", blocker)


class ConfigCopyTest(BackWorld):
    """bosun.env's changes taken back from the copy the update kept beside it, once a kg forget deleted the
    backup."""

    def test_the_copy_the_update_kept_beside_bosun_env_serves(self) -> None:
        patch = mock.patch.object(migrations, "CONFIG_ADDED", ConfigTest.ADDED)
        patch.start()
        self.addCleanup(patch.stop)
        before = "# mine\nKEEP=2\n"
        (self.config / "bosun.env.pre-update-20261107-120000").write_text(before)
        (self.config / "bosun.env").write_text(migrations.written_config(before, ("BOSUN_KEEPS",))[0])
        changed = ('bosun.env: BOSUN_KEEPS="all" added (before: bosun.env.pre-update-20261107-120000)',)
        self.record(
            "done",
            backup=str(self.update_backup),
            config_changed=changed,
            plan={"keys_added": ["BOSUN_KEEPS"]},
        )
        update_backups.delete_all(self.state)
        found = update_back.facts(self.where())
        self.assertEqual((found.plan.blockers, found.back.config.said), ((), ('BOSUN_KEEPS="all" removed',)))
        self.assertEqual(found.before_env, before)


class AddAgainTest(unittest.TestCase):
    """The older version's folder added again from bosun.git, its commit fetched from the origin if
    needed."""

    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.origin = tmp / "origin"
        self.git("init", "-q", "-b", "main", str(self.origin))
        self.first = self.commit("first")
        self.versions = tmp / "share" / "bosun-versions"
        self.repo = self.versions / "bosun.git"
        self.git("clone", "-q", "--bare", str(self.origin), str(self.repo))
        self.folder = self.versions / self.first[:12]
        self.git(
            "--git-dir", str(self.repo), "worktree", "add", "-q", "--detach", str(self.folder), self.first
        )
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def git(self, *args: str) -> str:
        done = versions.git(
            "-c", "user.name=acme", "-c", "user.email=dev@example.com", "-c", "commit.gpgsign=false", *args
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def commit(self, subject: str) -> str:
        self.git("-C", str(self.origin), "commit", "-q", "--allow-empty", "-m", subject)
        return self.git("-C", str(self.origin), "rev-parse", "HEAD")

    def test_a_folder_removed_comes_back_from_bosun_git(self) -> None:
        self.git("--git-dir", str(self.repo), "worktree", "remove", str(self.folder))
        update_back._add_again(self.folder, self.first)
        self.assertEqual(versions.commit_of(self.folder), self.first)

    def test_a_commit_bosun_git_lacks_is_fetched_from_the_origin_first(self) -> None:
        later = self.commit("later")
        self.assertFalse(versions.has_commit(self.repo, later))
        folder = self.versions / later[:12]
        update_back._add_again(folder, later)
        self.assertEqual(versions.commit_of(folder), later)

    def test_its_fetch_never_asks_a_terminal_and_gives_up_on_a_slow_one(self) -> None:
        later = self.commit("later")
        real = versions.git
        calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

        def git(*args: str, **kw: Any) -> Any:
            calls.append((args, kw))
            return real(*args, **kw)

        with mock.patch.object(versions, "git", side_effect=git):
            update_back._add_again(self.versions / later[:12], later)
        [(args, kw)] = [call for call in calls if "fetch" in call[0]]
        self.assertEqual(kw["env"], {"GIT_TERMINAL_PROMPT": "0"})
        self.assertEqual(kw["timeout"], update_back.FETCH_SECONDS)
        self.assertIn("http.lowSpeedLimit=1000", args)
        self.assertIn("http.lowSpeedTime=30", args)

    def test_a_commit_the_origin_does_not_have_either_is_refused(self) -> None:
        with self.assertRaises(update_facts.UpdateRefused) as caught:
            update_back._add_again(self.versions / ("e" * 12), "e" * 40)
        self.assertIn("could not fetch " + "e" * 40 + " from the origin", str(caught.exception))
        self.assertFalse((self.versions / ("e" * 12)).exists())


class KeepTest(BackWorld):
    """The two versions read the stores alike: everything written since is kept, and nothing is frozen."""

    def test_the_plan_keeps_each_store_with_what_was_written_since(self) -> None:
        found = update_back.facts(self.where())
        self.assertFalse(found.back.restores)
        self.assertEqual((found.plan.from_commit, found.plan.to_commit), (TO, FROM))
        self.assertEqual((found.plan.migrations, found.plan.keys_added, found.plan.blockers), ((), (), ()))
        text = update_back_plan.render(found.plan, found.back)
        self.assertIn("ledger: kept, with what was written since (format 1, rules 1 in both)", text)

    def test_it_switches_back_and_keeps_what_was_written_since(self) -> None:
        seen: list[str] = []
        real = update_state.write

        def write(state_dir: Path, state: UpdateState) -> None:
            seen.append(state.step)
            real(state_dir, state)

        with mock.patch.object(update_state, "write", side_effect=write):
            done = self.back()
        self.assertEqual(seen[:4], ["planned", "rehearsed", "holding", "checked"])  # no freeze
        self.assertEqual((done.step, done.kind, done.backup), ("done", update_state.BACK, ""))
        self.assertEqual(update_state.from_object(done.previous), dataclasses.replace(self.over, previous={}))
        self.assertEqual(self.link().resolve(), self.old.resolve())
        self.assertIn("review:acme/exporter#21", self.items())

    def test_its_summary_is_saved_beside_the_backup_of_the_update_it_went_back_over(self) -> None:
        done = self.back()
        said = update.Said()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            update._say_summary(self.state, done, said)
        text = out.getvalue()
        self.assertTrue(text.startswith("bosun went back: bbbbbbb -> aaaaaaa in "), text)
        self.assertIn("over the update of 2026-11-07 12:00 UTC", text.splitlines()[0])
        self.assertIn("ledger      kept, with what was written since", text)
        self.assertTrue(text.rstrip().endswith("Going forward again: bosun update"), text)
        self.assertIn("bosun went back", (self.update_backup / update_back_plan.BACK_SUMMARY).read_text())


class PendingTest(BackWorld):
    """A back that leaves windows to the check-in loop saves its summary then, marked, with this code: an
    older version's loop, which finishes it, knows no back."""

    def test_its_summary_is_saved_at_agents_marked_with_the_windows_still_waited_for(self) -> None:
        def handed(state: UpdateState) -> UpdateState:
            ended = update_state.advanced(state, "agents", pending=("acme-api",))
            update_state.write(self.state, ended)
            return ended

        with mock.patch.object(update, "_windows_brought_along", side_effect=handed):
            ended = update_back.carried_out(update_back.facts(self.where()), self.where())
        with contextlib.redirect_stdout(io.StringIO()):
            update._say_summary(self.state, ended, update.Said())
        saved = (self.update_backup / update_back_plan.BACK_SUMMARY).read_text().splitlines()
        self.assertTrue(saved[1].startswith("bosun went back: "), saved)
        self.assertEqual(
            saved[2], "(saved while it still brought along acme-api: the check-in loop finishes them)"
        )

    def test_an_update_at_agents_saves_none_yet(self) -> None:
        state = self.record("agents", backup=str(self.update_backup), pending=("acme-api",))
        with contextlib.redirect_stdout(io.StringIO()):
            update._say_summary(self.state, state, update.Said())
        self.assertFalse((self.update_backup / update_backups.SUMMARY).exists())


class RestoreTest(BackWorld):
    """The ledger's rules differ: it is restored from the update's backup, under the freeze."""

    def setUp(self) -> None:
        super().setUp()
        source(self.new, rules=2)

    def test_the_plan_says_what_restoring_loses(self) -> None:
        found = update_back.facts(self.where())
        [ledger_part] = found.back.restored()
        self.assertEqual(ledger_part.written.changes, 1)
        self.assertIn("review:acme/exporter#21", ledger_part.written.items)
        text = update_back_plan.render(found.plan, found.back)
        self.assertIn(
            "ledger: rules 2 -> 1: restored from that update's backup; loses 1 change written since", text
        )

    def test_it_restores_the_store_and_keeps_what_it_drops_in_a_backup_of_its_own(self) -> None:
        done = self.back()
        self.assertEqual((done.step, done.kind), ("done", update_state.BACK))
        self.assertEqual(done.restorable, update_backups.STORE_PARTS["ledger"])
        self.assertEqual(len(done.restored), 1)
        self.assertNotIn("review:acme/exporter#21", self.items())
        own = Path(done.backup)
        self.assertNotEqual(own, self.update_backup)
        self.assertIn("review:acme/exporter#21", ledger.load_at(own / "state")["items"])
        self.assertEqual(self.link().resolve(), self.old.resolve())
        self.assertEqual(update_backups.read_manifest(own).to_commit, FROM)

    def test_a_failure_while_writes_are_paused_puts_everything_back(self) -> None:
        real, calls = update_backups.restore, []

        def restore(*args: Any) -> list[str]:
            """The back's restore done, then a failure; the put back's restore as it is."""
            calls.append(args)
            done = real(*args)
            if len(calls) == 1:
                raise OSError("disk full")
            return done

        with mock.patch.object(update_backups, "restore", side_effect=restore), self.windows_answer():
            ended = update_back.carried_out(update_back.facts(self.where()), self.where())
        self.assertEqual(ended.step, "failed")
        self.assertIn("disk full", ended.error)
        self.assertIn("review:acme/exporter#21", self.items())
        self.assertEqual(self.link().resolve(), self.new.resolve())

    def test_a_backup_found_damaged_fails_the_rehearsal_and_changes_nothing(self) -> None:
        (self.update_backup / "state" / "ledger.json").write_text("{}")
        with self.windows_answer():
            ended = update_back.carried_out(update_back.facts(self.where()), self.where())
        self.assertEqual(ended.step, "failed")
        self.assertIn("the rehearsal failed", ended.error)
        self.assertIn("review:acme/exporter#21", self.items())
        self.assertEqual(self.link().resolve(), self.new.resolve())


class ConfigTest(BackWorld):
    """bosun.env, which the update wrote, has its changes taken back; nothing else is restored."""

    ADDED = (migrations.AddedKey("BOSUN_KEEPS", "all", "keeps what it did"),)

    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.object(migrations, "CONFIG_ADDED", self.ADDED)
        patch.start()
        self.addCleanup(patch.stop)
        before = "# mine\nKEEP=2\n"
        (self.update_backup / "config").mkdir()
        (self.update_backup / "config" / "bosun.env").write_text(before)
        (self.config / "bosun.env").write_text(migrations.written_config(before, ("BOSUN_KEEPS",))[0])
        plan = {"keys_added": ["BOSUN_KEEPS"]}
        self.record(
            "done",
            backup=str(self.update_backup),
            config_changed=('bosun.env: BOSUN_KEEPS="all" added',),
            plan=plan,
        )

    def test_it_takes_them_back_and_keeps_the_file_first(self) -> None:
        found = update_back.facts(self.where())
        self.assertEqual(found.back.config.said, ('BOSUN_KEEPS="all" removed',))
        self.assertEqual(found.back.restorable(), (update_back_plan.CONFIG,))
        done = self.back()
        self.assertEqual((self.config / "bosun.env").read_text(), "# mine\nKEEP=2\n")
        [line] = done.config_changed
        self.assertTrue(
            line.startswith('bosun.env: BOSUN_KEEPS="all" removed (before: bosun.env.pre-back-'), line
        )
        self.assertIn("review:acme/exporter#21", self.items())  # the stores were kept


class CommandTest(BackWorld):
    def main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            mock.patch("sys.stdin", io.StringIO()),
        ):
            code = update_back.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_dry_run_shows_the_plan_and_changes_nothing(self) -> None:
        code, out, _ = self.main("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Go back bbbbbbb -> aaaaaaa", out)
        self.assertIn("A dry run: nothing has changed.", out)
        self.assertEqual(self.link().resolve(), self.new.resolve())

    def test_json_prints_the_update_s_object_with_the_back_s_part(self) -> None:
        code, out, _ = self.main("--dry-run", "--json")
        printed = json.loads(out)
        self.assertEqual((code, printed["contract"], printed["error"]), (0, 1, None))
        self.assertEqual(printed["plan"]["back"]["over"], "u1")

    def test_without_a_terminal_it_stops_at_the_plan(self) -> None:
        code, out, _ = self.main()
        self.assertEqual(code, 0)
        self.assertIn("bosun update --back --yes goes back without asking", out)
        self.assertEqual(self.link().resolve(), self.new.resolve())

    def test_yes_goes_back_and_skips_the_version_it_left(self) -> None:
        with self.windows_answer():
            code, out, err = self.main("--yes")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(update_lookup.skipped_version(self.state), TO[:12])
        self.assertIn(f"bosun update --check no longer offers {TO[:12]}", out)
        self.assertIn("bosun went back", out)

    def test_a_refusal_is_said_in_one_line(self) -> None:
        (self.state / update_state.STATE_FILE).unlink()
        code, _, err = self.main("--yes")
        self.assertEqual((code, err), (1, f"bosun update: {update_back_plan.NOTHING_FINISHED}\n"))

    def test_the_target_check_refuses_a_version_older_than_the_data(self) -> None:
        source(self.old, rules=0, ledger_format=1)
        self.assertEqual(self.main("target-check", str(self.old), "main")[0], 0)  # its source says no rules
        source(self.new, rules=2)  # the installed version's rules, under which the data is
        source(self.old, rules=1)
        code, _, err = self.main("target-check", str(self.old), "try-x")
        self.assertEqual(code, 1)
        self.assertIn("try-x (aaaaaaa) reads the ledger under rules 1, and the data is under rules 2", err)
        self.assertIn("bosun update --back", err)


if __name__ == "__main__":
    unittest.main()
