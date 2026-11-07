"""lib/update_facts.py: the facts `bosun update` gathers for its plan, and what stops it there
(docs/design/update.md, step 1). Fictional data."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import knowledge_store
import update_facts
import update_gate
import update_notes
import update_plan
import versions
from test_update import FROM, TO, World


class BlockerTest(World):
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

    def test_a_window_answered_by_hand_does_not_block(self) -> None:
        """No check-in loop took its answer yet: the plan counts it as answered, as bosun update does."""
        self.record("agents", pending=("api", "web app"), windows={"api": ("repo", "wait until idle")})
        update_notes.note(self.state, "web app", "status", "carried on by hand")
        [blocker] = self.plan().blockers
        self.assertIn("still brings along api:", blocker)
        update_notes.note(self.state, "api", "status", "carried on by hand")
        self.assertEqual(self.plan().blockers, ())

    def test_windows_an_update_still_brings_along_block_the_next_one(self) -> None:
        """A new update would bring along its own windows and clear the notes of these."""
        self.record("agents", pending=("api", "web"), windows={"api": ("repo", "wait until idle")})
        [blocker] = self.plan().blockers
        self.assertIn("still brings along api, web", blocker)
        self.assertIn("bosun update note --window <w> --status", blocker)
        self.assertIn("bosun update advance", blocker)
        self.record("agents")
        self.assertEqual(self.plan().blockers, ())

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
