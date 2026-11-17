"""lib/update_facts.py: the facts `bosun update` gathers for its plan, and what stops it there
(docs/design/update.md, step 1). Fictional data."""

from __future__ import annotations

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


class PlanForTest(World):
    def plan(self) -> update_plan.Plan:
        with (
            mock.patch.object(
                versions, "commit_of", side_effect=lambda root: FROM if root == self.old else TO
            ),
            mock.patch.object(update_facts, "window_facts", return_value=[]),
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

    def test_a_window_answered_by_hand_is_owed_nothing(self) -> None:
        """No check-in loop took its answer yet: the plan counts it as answered, as bosun update does."""
        self.record("agents", pending=("api", "web app"), windows={"api": ("repo", "wait until idle")})
        update_notes.note(self.state, "web app", "status", "carried on by hand")
        self.assertEqual(self.plan().owed, (("api", "restart"),))

    def test_an_update_that_failed_before_taking_them_over_still_owes_them(self) -> None:
        """Its record carries the last one, whose windows the next update takes over in its place; and
        the plan an update makes again after its switch reads them from the record it carried."""
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

    def test_the_checkout_at_the_install_path_is_what_the_first_update_converts(self) -> None:
        self.install.unlink()
        self.install.mkdir()
        self.assertEqual(update_facts.install_problem(self.install, self.new), [])
        self.assertTrue(update_facts.install_problem(self.install, self.install))


class ConversionFactsTest(World):
    def ran(self, out: str, code: int = 0) -> Any:
        return mock.patch.object(
            update_facts, "shell", return_value=subprocess.CompletedProcess([], code, out, "")
        )

    def test_the_quiet_moment_reads_each_window_but_the_checkin_one_and_who_typed(self) -> None:
        listed = [
            {"name": "orch", "kind": "orchestrator", "window": True, "agent": {"status": "idle"}},
            {"name": "checkin", "kind": "checkin", "window": True, "agent": None},
            {"name": "api", "kind": "session", "window": True, "agent": {"status": "busy"}},
        ]
        with mock.patch.object(update_facts, "open_windows", return_value=listed), self.ran("10 3"):
            quiet = update_facts.quiet_facts(self.new)
        self.assertEqual(quiet, update_facts.Quiet((("orch", "idle"), ("api", "busy")), 3, 10))
        with mock.patch.object(update_facts, "open_windows", return_value=[]), self.ran("0 "):
            self.assertEqual(update_facts.quiet_facts(self.new), update_facts.Quiet((), None, 0))
        with mock.patch.object(update_facts, "open_windows", return_value=[]), self.ran(""):
            self.assertEqual(update_facts.quiet_facts(self.new).minutes, update_facts.ACTIVE_MINUTES)

    def test_the_check_in_loop_s_pid(self) -> None:
        with self.ran("4242\n"):
            self.assertEqual(update_facts.checkin_loop_pid(self.new), 4242)
        with self.ran("", code=1):
            self.assertIsNone(update_facts.checkin_loop_pid(self.new))


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
        fingerprint refused, counts as changed."""
        root = self.new
        (root / "bin").mkdir()
        stub = root / "bin" / "bosun"
        stub.write_text(
            '#!/bin/sh\n[ "$3" = known ] && echo \'{"args": "a", "files": "f"}\' && exit 0\nexit 1\n'
        )
        stub.chmod(0o755)
        record = {"fingerprint": {"args": "a", "files": "f"}}
        with mock.patch("launch_record.recorded", return_value=record):
            self.assertFalse(update_facts.launch_changed(root, "known", self.state, "s"))
            self.assertTrue(update_facts.launch_changed(root, "refused", self.state, "s"))
        with mock.patch("launch_record.recorded", return_value={"fingerprint": {"args": "a", "files": "g"}}):
            self.assertTrue(update_facts.launch_changed(root, "known", self.state, "s"))
        with mock.patch("launch_record.recorded", return_value=None):
            self.assertTrue(update_facts.launch_changed(root, "known", self.state, "s"))

    def test_a_launch_is_compared_with_the_values_the_update_writes(self) -> None:
        """An older install's orchestrator, launched with every connector: the update writes
        BOSUN_ORCHESTRATOR_CONNECTORS="all", so its launch is unchanged and it is not restarted."""
        root = self.state.parent / "version"
        (root / "bin").mkdir(parents=True)
        stub = root / "bin" / "bosun"
        stub.write_text('#!/bin/sh\necho "{\\"args\\": \\"${BOSUN_ORCHESTRATOR_CONNECTORS:-none}\\"}"\n')
        stub.chmod(0o755)
        with mock.patch("launch_record.recorded", return_value={"fingerprint": {"args": "all"}}):
            self.assertTrue(update_facts.launch_changed(root, "orch", self.state, "s"))
            kept = {"BOSUN_ORCHESTRATOR_CONNECTORS": "all"}
            self.assertFalse(update_facts.launch_changed(root, "orch", self.state, "s", kept))


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
            mock.patch.object(update_facts, "launch_changed", return_value=True) as changed,
        ):
            facts = update_facts.window_facts(self.new)
        self.assertEqual(
            [(w.name, w.launch_changed, w.started_by_bosun) for w in facts],
            [("orch", True, True), ("api", True, True), ("plain", False, False), ("by-hand", False, False),
             ("sent", True, True)],
        )  # fmt: skip
        self.assertEqual([call.args[1] for call in changed.call_args_list], ["orch", "api", "sent"])
        with (
            mock.patch.object(update_facts, "open_windows", return_value=listed[:1]),
            mock.patch.object(update_facts, "launch_changed", return_value=False) as changed,
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
