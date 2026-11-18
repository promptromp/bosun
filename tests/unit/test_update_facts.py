"""lib/update_facts.py: the facts `bosun update` gathers for its plan, and what stops it there
(docs/design/update.md, step 1). Fictional data."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import knowledge_store
import migrations
import update_facts
import update_gate
import update_notes
import update_plan
import update_state
import versions
from test_update import FROM, TO, World


def opened(*names: str, unchanged: tuple[str, ...] = ()) -> list[update_plan.WindowFacts]:
    """Windows open and idle, each launched with what the newest version changes but <unchanged>."""
    return [
        update_plan.WindowFacts(
            name, "orchestrator" if name == "orchestrator" else "repo", "idle", name not in unchanged
        )
        for name in names
    ]


class PlanForTest(World):
    def setUp(self) -> None:
        super().setUp()
        self.windows: list[update_plan.WindowFacts] = []  # the windows open, as each test sets them

    def plan(self) -> update_plan.Plan:
        with (
            mock.patch.object(
                versions, "commit_of", side_effect=lambda root: FROM if root == self.old else TO
            ),
            mock.patch.object(update_facts, "window_facts", return_value=self.windows),
            mock.patch.object(update_facts, "jobs", return_value=([], [])),
            mock.patch.object(update_facts, "missing", return_value=[]),
            mock.patch.object(update_facts, "commits", return_value=["bbbbbbb the next version"]),
        ):
            return update_facts.plan_for(self.old, self.new, self.state, self.config)

    def test_nothing_stops_an_update_of_readable_stores(self) -> None:
        self.assertEqual(self.plan().blockers, ())

    def test_a_ledger_whose_format_cannot_be_read_is_a_blocker(self) -> None:
        (self.state / "ledger.json").write_text("{not json")
        [blocker] = self.plan().blockers
        self.assertIn("the ledger's format cannot be read", blocker)
        self.assertIn("ledger.json", blocker)

    def test_a_knowledge_base_whose_format_cannot_be_read_is_a_blocker(self) -> None:
        kb = knowledge_store.Paths(self.config / update_gate.KNOWLEDGE_DIR)
        kb.root.mkdir()
        kb.format.write_text('{"format": "two"}')
        self.assertTrue(any("the knowledge's format cannot be read" in b for b in self.plan().blockers))

    def test_a_link_a_backup_cannot_copy_is_a_blocker_naming_it(self) -> None:
        (self.config / "policy.md").symlink_to(self.config / "dotfiles" / "policy.md")
        [blocker] = self.plan().blockers
        self.assertIn(str(self.config / "policy.md"), blocker)
        self.assertIn("no backup can copy it", blocker)

    def test_windows_an_update_still_brings_along_never_block_the_next_one(self) -> None:
        """Decision 3: the next update takes them over; its plan lists each with what it is owed."""
        self.windows = opened("api", "orchestrator", "web")
        self.record(
            "agents",
            pending=("api", "orchestrator", "web"),
            windows={
                "api": ("repo", "wait until idle"),
                "orchestrator": ("orchestrator", "re-read"),
                "web": ("repo", "restart", "restarted"),
            },
        )
        plan = self.plan()
        self.assertEqual(plan.blockers, ())
        self.assertEqual(plan.owed, (("api", "restart"), ("orchestrator", "re-read"), ("web", "answer")))
        text = update_plan.render_plan(plan)
        self.assertIn("Still owed from the last update, which this one takes over:", text)
        self.assertIn("  web: its answer to the update", text)
        self.record("agents")
        self.assertEqual(self.plan().owed, ())

    def test_the_plan_lists_only_the_windows_it_takes_over_and_those_it_leaves_alone(self) -> None:
        """Item 4: by take_over's own decision over the new plan's treatments, so "still owed" never
        promises a restart the update will not make; a closed window is neither."""
        self.windows = opened("api", "orchestrator", "web", unchanged=("api", "orchestrator", "web"))
        self.record(
            "agents",
            pending=("api", "orchestrator", "web", "gone"),
            windows={
                "api": ("repo", "wait until idle"),  # owed a restart the newest version no longer needs
                "orchestrator": ("orchestrator", "re-read"),  # its re-read is still owed
                "web": ("repo", "restart", "restarted"),  # began: its answer is still owed
                "gone": ("repo", "restart"),
            },
        )
        plan = self.plan()
        self.assertEqual(plan.owed, (("orchestrator", "re-read"), ("web", "answer")))
        self.assertEqual(plan.owed_left, ("api",))
        text = update_plan.render_plan(plan)
        self.assertIn(
            "Also owed by the last update, and left alone, as nothing they were launched with differs from "
            "the newest version: api\n",
            text,
        )
        self.assertNotIn("api: a restart", text)
        self.windows = opened("api", "orchestrator", "web")
        self.assertEqual(self.plan().owed_left, ())

    def test_a_window_whose_launch_the_new_version_refuses_is_listed_only_as_refused(self) -> None:
        """Round 2, item 1: the last update asked it to take stock; it waits at a prompt; its launch on the
        new version would refuse. Its plan line says so, and Still owed promises it no restart."""
        why = "plugins: a.json does not parse, so bosun cannot tell which plugins to switch off"
        self.windows = [update_plan.WindowFacts("orch", "orchestrator", "waiting", True, refused=why)]
        self.record(
            "agents",
            pending=("orch",),
            windows={"orch": ("orchestrator", "take stock, then restart", "asked to take stock")},
        )
        plan = self.plan()
        self.assertEqual((plan.owed, plan.owed_left, plan.refused), ((), (), (("orch", why),)))
        text = update_plan.render_plan(plan)
        self.assertIn(f"  orch: left running, as it would not start on the new version: {why}; ", text)
        self.assertNotIn("Still owed", text)

    def test_a_window_answered_by_hand_is_owed_nothing(self) -> None:
        """No check-in loop took its answer yet: the plan counts it as answered, as bosun update does."""
        self.windows = opened("api", "web app")
        self.record("agents", pending=("api", "web app"), windows={"api": ("repo", "wait until idle")})
        update_notes.note(self.state, "web app", "status", "carried on by hand")
        self.assertEqual(self.plan().owed, (("api", "restart"),))

    def test_an_update_that_failed_before_taking_them_over_still_owes_them(self) -> None:
        """Its record carries the last one, whose windows the next update takes over in its place; and
        the plan an update makes again after its switch reads them from the record it carried."""
        self.windows = opened("api")
        last = self.record("agents", pending=("api",), windows={"api": ("repo", "restart")})
        for step in ("failed", "released"):
            self.record(step, carried=update_state.as_object(last))
            self.assertEqual(self.plan().owed, (("api", "restart"),), step)
        for step in ("agents", "done"):  # it took them over already: nothing more is owed from the last
            self.record(step, carried=update_state.as_object(last))
            self.assertEqual(self.plan().owed, (), step)

    def test_the_plan_says_whether_the_console_job_will_be_restarted(self) -> None:
        self.assertFalse(self.plan().console_job)
        with mock.patch.object(update_facts, "console_job", return_value=True):
            self.assertTrue(self.plan().console_job)

    def test_the_console_job_is_one_the_new_version_would_restart(self) -> None:
        """Asked of the new version's own shell (its common.sh decides: loaded, and wanted)."""
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(update_facts, "shell", return_value=done) as shell:
            self.assertTrue(update_facts.console_job(self.new))
        self.assertEqual(shell.call_args.args, (self.new, "console_restartable"))
        for code in (1, 127):
            done = subprocess.CompletedProcess([], code, "", "")
            with mock.patch.object(update_facts, "shell", return_value=done):
                self.assertFalse(update_facts.console_job(self.new))

    def test_the_ontology_key_is_read_as_the_new_version_reads_the_config(self) -> None:
        """Only BOSUN_KNOWLEDGE_ENABLED=1 is on; a shell that fails is off. The plan's treatment of the
        orchestrator goes by it (update_plan.reread_patterns)."""
        for code, out, on in ((0, "1", True), (0, "0", False), (0, "", False), (1, "1", False)):
            done = subprocess.CompletedProcess([], code, out, "")
            with mock.patch.object(update_facts, "shell", return_value=done) as shell:
                self.assertIs(update_facts.knowledge_on(self.new), on)
            self.assertEqual(shell.call_args.args[0], self.new)
        rules = {"orchestrator/knowledge.md": "k1"}
        with (
            mock.patch.object(
                update_facts, "prompts", side_effect=lambda r: {} if r == self.old else dict(rules)
            ),
            mock.patch.object(update_facts, "knowledge_on", return_value=True) as on,
        ):
            self.assertEqual(self.plan().prompts, ("orchestrator/knowledge.md",))
        on.assert_called_once_with(self.new)

    def test_the_plan_says_which_kept_values_it_writes_to_bosun_env(self) -> None:
        """A key the update adds with a kept value (migrations.CONFIG_ADDED), that bosun.env does not
        set: the plan shows it, so the person saying yes sees bosun.env change."""
        old_keys = frozenset({"BOSUN_DAYS"})
        new_keys = old_keys | {"BOSUN_CONNECTORS", "BOSUN_ORCHESTRATOR_CONNECTORS"}
        keys = mock.patch.object(
            update_facts, "config_keys", side_effect=lambda r: old_keys if r == self.old else new_keys
        )
        (self.config / "bosun.env").write_text("BOSUN_DAYS=MTWRF\n")
        with keys:
            self.assertEqual(self.plan().kept, (("BOSUN_ORCHESTRATOR_CONNECTORS", "all"),))
            self.assertEqual(self.plan().unwritten, ())
            (self.config / "bosun.env").write_text('BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat"\n')
            self.assertEqual((self.plan().kept, self.plan().unwritten), ((), ()))
            (self.config / "bosun.env").unlink()
            self.assertEqual(self.plan().kept, ())
            # No bosun.env to write it to: the plan says the key starts at its default.
            self.assertEqual(self.plan().unwritten, ("BOSUN_ORCHESTRATOR_CONNECTORS",))

    def test_versions_not_side_by_side_are_a_blocker(self) -> None:
        self.assertEqual(update_facts.install_problem(self.old, self.new), [])
        checkout = self.state.parent / "checkout"
        self.assertTrue(update_facts.install_problem(checkout, self.new))

    def test_a_checkout_at_the_install_path_is_refused_never_updated(self) -> None:
        """One an older get.sh made, from before versions side by side: it is installed again."""
        self.install.unlink()
        self.install.mkdir()
        for old_root in (self.install, self.old):
            found = update_facts.install_problem(old_root, self.new)
            self.assertEqual(len(found), 1, old_root)
            self.assertIn(
                f"bosun update moves an install with its versions side by side ({self.install}", found[0]
            )
        self.assertTrue(update_facts.install_problem(self.install, self.install))


ROOT = Path(__file__).resolve().parents[2]
LOGIN = "BOSUN_START_AT_LOGIN"


class JobsTest(World):
    """The jobs the plan lists are the ones the release reloads: the dry run reads bosun.env as the update
    leaves it, so an older install's (which keeps BOSUN_START_AT_LOGIN=0, CONFIG_ADDED) gets no login job."""

    def setUp(self) -> None:
        super().setUp()
        home = self.state.parent / "home"
        tools = home / ".local" / "bin"
        tools.mkdir(parents=True)
        for tool, status in (("plutil", 0), ("launchctl", 1)):  # every plist valid; no job loaded
            (tools / tool).write_text(f"#!/bin/sh\nexit {status}\n")
            (tools / tool).chmod(0o755)
        self.enterContext(
            mock.patch.dict(os.environ, {"HOME": str(home), "BOSUN_LOG_DIR": str(self.state / "logs")})
        )
        os.environ.pop(LOGIN, None)
        (self.config / "bosun.env").write_text('BOSUN_DAYS="MTWRF"\nBOSUN_START_TIME="08:00"\n')  # no key
        self.login = update_facts.shell(ROOT, "login_label").stdout.strip()

    def test_an_older_install_s_plan_does_not_list_the_login_job(self) -> None:
        kept = migrations.keys_to_keep(self.config, {LOGIN})
        self.assertEqual(kept, {LOGIN: "0"})
        reloads, blockers = update_facts.jobs(ROOT, kept)
        self.assertEqual(blockers, [])
        self.assertTrue(reloads, "the dry run listed no job at all")
        self.assertNotIn(self.login, reloads)

    def test_without_a_kept_value_the_new_default_lists_it(self) -> None:
        reloads, _ = update_facts.jobs(ROOT)
        self.assertIn(self.login, reloads)

    def test_the_plan_runs_the_dry_run_with_what_the_update_writes(self) -> None:
        asked: list[object] = []

        def jobs(_root: Path, kept: object = None) -> tuple[list[str], list[str]]:
            asked.append(kept)
            return [], []

        def keys(root: Path) -> frozenset[str]:
            return frozenset({LOGIN}) if root == self.new else frozenset()

        with (
            mock.patch.object(
                versions, "commit_of", side_effect=lambda root: FROM if root == self.old else TO
            ),
            mock.patch.object(update_facts, "config_keys", side_effect=keys),
            mock.patch.object(update_facts, "window_facts", return_value=[]),
            mock.patch.object(update_facts, "jobs", side_effect=jobs),
            mock.patch.object(update_facts, "missing", return_value=[]),
            mock.patch.object(update_facts, "commits", return_value=[]),
        ):
            plan = update_facts.plan_for(self.old, self.new, self.state, self.config)
        self.assertEqual(asked, [{LOGIN: "0"}])
        self.assertEqual(plan.kept, ((LOGIN, "0"),))


class EveryUpdateFactsTest(World):
    """The facts every update's plan and release gather since `bosun update --back`: the jobs to unload,
    each version's store formats from its source, and the line a plan says going forward to a version a back
    left."""

    def kinds(self, listed: dict[Path, str | None]) -> tuple[Any, list[tuple[str, ...]]]:
        """update_facts.shell as each version's job_kinds answers (None: it fails), and what else it was
        asked (unload_jobs --dry-run, answering that each label it is given is installed)."""
        asked: list[tuple[str, ...]] = []

        def shell(root: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
            if script == update_facts.JOB_KINDS:
                said = listed[root]
                return subprocess.CompletedProcess([], 1 if said is None else 0, said or "", "")
            asked.append((script, *args))
            return subprocess.CompletedProcess([], 0, "\n".join(args) + "\n", "")

        return mock.patch.object(update_facts, "shell", side_effect=shell), asked

    NEW = "com.bosun.morning\tbosun\ncom.bosun.morning.app\tapp\n"
    OLD = (
        "com.bosun.morning\tbosun\ncom.bosun.morning.now\tbosun\ncom.bosun.morning.login\tbosun\n"
        "com.bosun.morning.app\tapp\n"
    )

    def test_the_jobs_the_new_version_lacks_are_those_it_unloads(self) -> None:
        patch, asked = self.kinds({self.old: self.OLD, self.new: self.NEW})
        with patch:
            self.assertEqual(
                update_facts.dropped_jobs(self.old, self.new),
                ["com.bosun.morning.now", "com.bosun.morning.login"],
            )
            self.assertEqual(update_facts.dropped_jobs(self.new, self.old), [])
        self.assertEqual(
            asked, [('unload_jobs --dry-run "$@"', "com.bosun.morning.now", "com.bosun.morning.login")]
        )

    def test_a_version_that_cannot_say_its_jobs_unloads_none(self) -> None:
        cases: list[dict[Path, str | None]] = [
            {self.old: None, self.new: None},
            {self.old: self.OLD, self.new: None},
            {self.old: None, self.new: self.NEW},
        ]
        for listed in cases:
            with self.subTest(listed=listed):
                patch, asked = self.kinds(listed)
                with patch:
                    self.assertEqual(update_facts.dropped_jobs(self.old, self.new), [])
                self.assertEqual(asked, [])

    def test_the_app_s_open_at_login_job_is_never_one_it_unloads(self) -> None:
        """It runs the app's own binary, and no reload makes it again: a target from before it keeps it."""
        before_the_app = "com.bosun.morning\tbosun\n"
        patch, asked = self.kinds({self.old: self.OLD, self.new: before_the_app})
        with patch:
            dropped = update_facts.dropped_jobs(self.old, self.new)
        self.assertNotIn("com.bosun.morning.app", dropped)
        self.assertNotIn("com.bosun.morning.app", asked[0])

    def test_job_kinds_read_from_each_version_s_own_shell(self) -> None:
        """This code's job_kinds, and an older version's job_labels, each a job that runs bosun."""
        repo = Path(update_facts.__file__).resolve().parents[1]
        with mock.patch.dict(os.environ, {"BOSUN_LAUNCHD_LABEL": "com.bosun.kinds-test"}):
            kinds = update_facts.job_kinds(repo)
        assert kinds is not None
        self.assertEqual(kinds["com.bosun.kinds-test.app"], "app")
        self.assertEqual(kinds["com.bosun.kinds-test.watchdog"], "bosun")
        older = self.old / "lib"
        older.mkdir()
        (older / "common.sh").write_text(
            'load_config() { :; }\njob_labels() { echo "com.a com.a.watchdog"; }\n'
        )
        self.assertEqual(update_facts.job_kinds(self.old), {"com.a": "bosun", "com.a.watchdog": "bosun"})
        # a released version: its job_labels lists the app's Open at Login job, it has app_label, no job_kinds
        (older / "common.sh").write_text(
            "load_config() { :; }\napp_label() { echo com.a.app; }\n"
            'job_labels() { echo "com.a com.a.watchdog $(app_label)"; }\n'
        )
        self.assertEqual(
            update_facts.job_kinds(self.old),
            {"com.a": "bosun", "com.a.watchdog": "bosun", "com.a.app": "app"},
        )

    def test_a_released_version_s_app_job_is_never_one_it_unloads(self) -> None:
        """Installed X, a release from before job_kinds, updating to Y, a branch from before the app's job:
        the app's Open at Login job, which X's job_labels lists, is kept."""
        for folder, labels in (
            (self.old, "com.a com.a.watchdog com.a.app"),
            (self.new, "com.a com.a.watchdog"),
        ):
            (folder / "lib").mkdir(exist_ok=True)
            app = "app_label() { echo com.a.app; }\n" if folder == self.old else ""
            (folder / "lib" / "common.sh").write_text(
                f'load_config() {{ :; }}\n{app}job_labels() {{ echo "{labels}"; }}\n'
            )
        self.assertEqual(update_facts.dropped_jobs(self.old, self.new), [])

    def test_a_commit_in_bosun_git_reads_as_its_folder_does(self) -> None:
        """A back's dry run whose older folder is gone reads it there, never checked out."""
        tree = self.state.parent / "tree"
        files = {
            "lib/ledger.py": "LEDGER_FORMAT = 2\nRULES_VERSION = 3\n",
            "lib/knowledge_store.py": "KNOWLEDGE_FORMAT = 1\n",
            "lib/knowledge.py": "KG_RULES_VERSION = 1\n",
            "lib/common.sh": "  BOSUN_ONE=1\n  BOSUN_TWO=2\n",
            "orchestrator/policy.md": "# policy\n",
            "orchestrator/tasks/review.md": "review\n",
            "orchestrator/tasks/deeper/not-a-task.md": "no\n",
        }
        for name, text in files.items():
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            (tree / name).write_text(text)
        identity = ("-c", "user.name=acme", "-c", "user.email=dev@example.com", "-c", "commit.gpgsign=false")
        versions.git("init", "-q", str(tree))
        versions.git("-C", str(tree), "add", "-A")
        versions.git("-C", str(tree), *identity, "commit", "-qm", "a version")
        committed = update_facts.Committed(tree / ".git", versions.commit_of(tree))
        folder = update_facts.Folder(tree)
        self.assertEqual(update_facts.formats_in(committed), {"ledger": 2, "knowledge": 1})
        self.assertEqual(update_facts.rules_in(committed), {"ledger": 3, "knowledge": 1})
        self.assertEqual(update_facts.keys_in(committed), frozenset({"BOSUN_ONE", "BOSUN_TWO"}))
        self.assertEqual(update_facts.prompts_in(committed), update_facts.prompts_in(folder))
        self.assertEqual(
            sorted(update_facts.prompts_in(committed)),
            ["orchestrator/policy.md", "orchestrator/tasks/review.md"],
        )
        self.assertIsNone(committed.read("no/such/file"))

    def test_a_version_s_formats_are_read_from_its_source(self) -> None:
        (self.old / "lib").mkdir()
        (self.old / "lib" / "ledger.py").write_text("LEDGER_FORMAT = 3\nRULES_VERSION = 2\n")
        self.assertEqual(update_facts.source_formats(self.old), {"ledger": 3, "knowledge": 0})
        repo = Path(update_facts.__file__).resolve().parents[1]
        self.assertEqual(update_facts.source_formats(repo), dict(migrations.FORMATS))

    def test_a_plan_to_the_version_a_back_left_says_so(self) -> None:
        back = dataclasses.replace(
            self.record("done"), kind=update_state.BACK, from_commit=TO, to_commit=FROM
        )
        update_state.write(self.state, back)
        self.assertEqual(update_facts.left_by_back(self.state, TO), "2026-11-07 12:00 UTC")
        self.assertEqual(update_facts.left_by_back(self.state, FROM), "")
        update_state.write(self.state, dataclasses.replace(back, step="failed"))  # put back: it left nothing
        self.assertEqual(update_facts.left_by_back(self.state, TO), "")
        update_state.write(self.state, dataclasses.replace(back, kind=""))  # an update, not a back
        self.assertEqual(update_facts.left_by_back(self.state, TO), "")


class FactsTest(World):
    def test_a_handed_item_s_type_the_site_replaces_is_left_out(self) -> None:
        """The session got the site's text of it, which an update never changes; the item stays, with
        no type, as the rules every task gets (orchestrator/tasks/_common.md) reached it too."""
        self.ledger("record-delegation", "review:acme/exporter#12", "--window", "api", "--task", "review")
        self.ledger(
            "record-delegation", "review:acme/exporter#14", "--window", "web", "--task", "acme-review"
        )
        self.assertEqual(
            update_facts.in_flight(self.state, self.config),
            [("review:acme/exporter#12", ("review",)), ("review:acme/exporter#14", ("acme-review",))],
        )
        (self.config / "tasks").mkdir()
        (self.config / "tasks" / "acme-review.md").write_text("Our own review rules.\n")
        self.assertEqual(
            update_facts.in_flight(self.state, self.config),
            [("review:acme/exporter#12", ("review",)), ("review:acme/exporter#14", ())],
        )

    def test_an_item_handed_several_types_is_in_flight_with_each_built_in_one(self) -> None:
        """`bosun delegate review,fix` records "review,fix"; only a type the site replaces is
        dropped, and the item stays, as the rules every task gets reached it."""
        self.ledger("record-delegation", "review:acme/exporter#12", "--window", "api", "--task", "review,fix")
        self.assertEqual(
            update_facts.in_flight(self.state, self.config), [("review:acme/exporter#12", ("review", "fix"))]
        )
        (self.config / "tasks").mkdir()
        (self.config / "tasks" / "fix.md").write_text("Our own fix rules.\n")
        self.assertEqual(
            update_facts.in_flight(self.state, self.config), [("review:acme/exporter#12", ("review",))]
        )

    def test_an_item_a_window_took_on_directly_carries_no_task_text(self) -> None:
        """Work I asked for in a window (`ledger working`) records the task "direct": no task text was
        ever handed with it, so it is not in flight, and a change to `_common.md` leaves it out too."""
        self.ledger("record-delegation", "review:acme/exporter#12", "--window", "api", "--task", "direct")
        self.ledger(
            "record-delegation", "review:acme/exporter#14", "--window", "web", "--task", "review,direct"
        )
        self.assertEqual(
            update_facts.in_flight(self.state, self.config), [("review:acme/exporter#14", ("review",))]
        )

    def test_prompts_keys_and_rules_come_from_the_version_folder(self) -> None:
        (self.old / "orchestrator" / "tasks").mkdir(parents=True)
        (self.old / "orchestrator" / "policy.md").write_text("Be brief.\n")
        (self.old / "orchestrator" / "tasks" / "review.md").write_text("Review it.\n")
        (self.old / "orchestrator" / "notes.md").write_text("Not a prompt.\n")
        (self.old / "lib").mkdir()
        (self.old / "lib" / "common.sh").write_text(
            'set_defaults() {\n  BOSUN_DAYS="MTWRF"\n  local x=1\n}\n'
        )
        (self.old / "lib" / "ledger.py").write_text("RULES_VERSION = 3\n")
        self.assertEqual(
            sorted(update_facts.prompts(self.old)), ["orchestrator/policy.md", "orchestrator/tasks/review.md"]
        )
        self.assertEqual(update_facts.config_keys(self.old), frozenset({"BOSUN_DAYS"}))
        self.assertEqual(update_facts.rules(self.old), {"ledger": 3, "knowledge": 0})

    def test_a_window_launched_otherwise_than_the_new_version_would_is_changed(self) -> None:
        """The new version's `launch --fingerprint` against the window's record; no record, or a
        fingerprint refused, counts as changed, and a refused one says why (its last stderr line)."""
        root = self.new
        (root / "bin").mkdir()
        stub = root / "bin" / "bosun"
        stub.write_text(
            '#!/bin/sh\n[ "$3" = known ] && echo \'{"args": "a", "files": "f"}\' && exit 0\n'
            '[ "$3" = silent ] && exit 4\n'
            '[ "$3" = crashed ] && echo "Traceback: KeyError" >&2 && exit 1\n'
            "echo \"ERROR: 'refused' does not start: its plugins were refused\" >&2\n"
            'echo "plugins: a.json does not parse" >&2\necho >&2\nexit 4\n'
        )
        stub.chmod(0o755)
        record = {"fingerprint": {"args": "a", "files": "f"}}
        with mock.patch("launch_record.recorded", return_value=record):
            self.assertEqual(update_facts.launch_compared(root, "known", self.state, "s"), (False, ""))
            self.assertEqual(
                update_facts.launch_compared(root, "refused", self.state, "s"),
                (True, "plugins: a.json does not parse"),
            )
            self.assertEqual(
                update_facts.launch_compared(root, "silent", self.state, "s"), (True, update_facts.REFUSED)
            )
            # item 4 of round 2: a crash, a usage error or a timeout is no refusal: changed, as before
            self.assertEqual(update_facts.launch_compared(root, "crashed", self.state, "s"), (True, ""))
        with mock.patch("launch_record.recorded", return_value={"fingerprint": {"args": "a", "files": "g"}}):
            self.assertEqual(update_facts.launch_compared(root, "known", self.state, "s"), (True, ""))
        with mock.patch("launch_record.recorded", return_value=None):
            self.assertEqual(update_facts.launch_compared(root, "known", self.state, "s"), (True, ""))
            self.assertEqual(update_facts.launch_compared(root, "refused", self.state, "s")[0], True)

    def test_a_launch_is_compared_with_the_values_the_update_writes(self) -> None:
        """An older install's orchestrator, launched with every connector: the update writes
        BOSUN_ORCHESTRATOR_CONNECTORS="all", so its launch is unchanged and it is not restarted."""
        root = self.state.parent / "version"
        (root / "bin").mkdir(parents=True)
        stub = root / "bin" / "bosun"
        stub.write_text('#!/bin/sh\necho "{\\"args\\": \\"${BOSUN_ORCHESTRATOR_CONNECTORS:-none}\\"}"\n')
        stub.chmod(0o755)
        with mock.patch("launch_record.recorded", return_value={"fingerprint": {"args": "all"}}):
            self.assertEqual(update_facts.launch_compared(root, "orch", self.state, "s"), (True, ""))
            kept = {"BOSUN_ORCHESTRATOR_CONNECTORS": "all"}
            self.assertEqual(update_facts.launch_compared(root, "orch", self.state, "s", kept), (False, ""))


class WindowFactsTest(World):
    """What a turn knows of the windows, from `bosun sessions --json`."""

    def listed(self) -> list[dict[str, Any]]:
        return [
            {"name": "orch", "kind": "orchestrator", "window": True, "conversation": "orch-2026-11-07",
             "agent": {"status": "idle", "name": "orch-2026-11-06"}},
            {"name": "api", "kind": "session", "window": True, "conversation": "api",
             "agent": {"status": "busy", "name": "api"}},
            {"name": "plain", "kind": "extra", "window": True, "conversation": "plain", "agent": None},
        ]  # fmt: skip

    def test_each_windows_status(self) -> None:
        self.assertEqual([update_facts.status_of(w) for w in self.listed()], ["idle", "busy", "exited"])

    def test_an_agent_on_another_conversation_than_its_next_launch_takes_up(self) -> None:
        orch, api, plain = self.listed()
        self.assertTrue(update_facts.on_another_conversation(orch))
        self.assertFalse(update_facts.on_another_conversation(api))
        self.assertFalse(update_facts.on_another_conversation(plain))
        self.assertFalse(update_facts.on_another_conversation({**api, "agent": {"status": "idle"}}))

    def test_a_window_outside_the_list_with_no_launch_recorded_was_not_started_by_bosun(self) -> None:
        """A shell (prefix-c) or a Claude started by hand in bosun's session is left as it is, never
        fingerprinted; one `bosun send --new` opened has a launch recorded, and is compared as bosun's."""
        listed = [
            *self.listed(),
            {"name": "by-hand", "kind": "extra", "window": True, "agent": {"status": "idle", "name": ""}},
            {"name": "sent", "kind": "extra", "window": True, "agent": {"status": "idle", "name": "sent"}},
        ]
        records: dict[str, dict[str, Any]] = {"api": {"fingerprint": {}}, "sent": {"fingerprint": {}}}
        with (
            mock.patch.object(update_facts, "open_windows", return_value=listed),
            mock.patch("launch_record.recorded", side_effect=lambda state, session, name: records.get(name)),
            mock.patch.object(update_facts, "launch_compared", return_value=(True, "")) as changed,
        ):
            facts = update_facts.window_facts(self.new)
        self.assertEqual(
            [(w.name, w.launch_changed, w.started_by_bosun) for w in facts],
            [("orch", True, True), ("api", True, True), ("plain", False, False), ("by-hand", False, False),
             ("sent", True, True)],
        )  # fmt: skip
        with (
            mock.patch.object(update_facts, "open_windows", return_value=listed[:1]),
            mock.patch.object(update_facts, "launch_compared", return_value=(True, "plugins: refused")),
        ):
            [orch] = update_facts.window_facts(self.new)
        self.assertEqual(orch.refused, "plugins: refused")
        self.assertEqual([call.args[1] for call in changed.call_args_list], ["orch", "api", "sent"])
        with (
            mock.patch.object(update_facts, "open_windows", return_value=listed[:1]),
            mock.patch.object(update_facts, "launch_compared", return_value=(False, "")) as changed,
        ):
            update_facts.window_facts(self.new, {"BOSUN_ORCHESTRATOR_CONNECTORS": "all"})
        self.assertEqual(changed.call_args.args[4], {"BOSUN_ORCHESTRATOR_CONNECTORS": "all"})

    def test_a_check_in_loop_runs_while_its_window_is_open(self) -> None:
        with mock.patch.object(update_facts, "open_windows", return_value=self.listed()):
            self.assertFalse(update_facts.loop_runs(self.new))
        with mock.patch.object(
            update_facts, "open_windows", return_value=[{"name": "checkin", "kind": "checkin"}]
        ):
            self.assertTrue(update_facts.loop_runs(self.new))


if __name__ == "__main__":
    import unittest

    unittest.main()
