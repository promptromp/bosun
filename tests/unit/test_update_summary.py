"""lib/update_summary.py: what `bosun update` says when it ends (docs/design/update.md, "The
summary"), built from the update's record and what its steps recorded, so it is the same whether the
update ran in one go or was carried on. Fictional data: acme's windows api and web, a home folder
/Users/me."""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import update_plan
import update_state
import update_summary
from migrations import Applied
from update_plan import Plan, Treatment
from update_state import UpdateState
from update_summary import Summary


DESIGN = Path(__file__).resolve().parents[2] / "docs" / "design" / "update.md"
HOME = "/Users/me"
FROM = "95545b0" + "1" * 33
TO = "c41b186" + "2" * 33
BACKUP = f"{HOME}/.local/state/bosun/backups/20261104-153207-95545b0-c41b186"
BOSUN_ENV = "bosun.env: BOSUN_OLD_KEY is now BOSUN_NEW_KEY (before: bosun.env.pre-update-20261104-153207)"
# What came of each window, as update_agents.outcomes says it (the design's example).
AGENTS = (
    ("orchestrator", "re-read policy.md; checked in (2 events from the update)"),
    ("api", "restarted when idle; resumed: waiting on CI for PR 12"),
    ("web", "busy; kept running (nothing it was started with changed)"),
)


def design_block() -> str:
    """The text block of the design note's "The summary" section, as the update prints it."""
    section = DESIGN.read_text().split("### The summary\n", 1)[1]
    start = section.index("```text\n") + len("```text\n")
    return section[start : section.index("```\n", start)]


def a_plan(**fields: Any) -> Plan:
    base: dict[str, Any] = {
        "from_commit": FROM,
        "to_commit": TO,
        "commits": (),
        "migrations": (),
        "rules": (),
        "keys_added": (),
        "renames": (),
        "prompts": (),
        "windows": (),
        "jobs": (),
        "console": False,
        "handed": (),
        "user_changes": (),
        "blockers": (),
        "formats": (("ledger", 1), ("knowledge", 1)),
    }
    return Plan(**{**base, **fields})


def a_record(**fields: Any) -> UpdateState:
    base: dict[str, Any] = {
        "id": "20261104-153000-95545b0-c41b186",
        "from_root": f"{HOME}/.local/share/bosun-versions/95545b011111",
        "to_root": f"{HOME}/.local/share/bosun-versions/c41b18622222",
        "from_commit": FROM,
        "to_commit": TO,
        "step": "done",
        "started_at": "2026-11-04T15:30:00+00:00",
        "hold_until": 0,
        "backup": BACKUP,
    }
    return UpdateState(**{**base, **fields})


# build's other parts, as a test leaves them unless it names one.
PARTS: dict[str, Any] = {
    "applied": (),
    "config": (),
    "jobs": (),
    "findings": (),
    "exists": True,
    "size": 3_100_000,
    "seconds": 161,
    "agents": (),
}


def built(state: UpdateState | None = None, plan: Plan | None = None, **parts: Any) -> Summary:
    unknown = set(parts) - set(PARTS)
    assert not unknown, unknown
    got = {**PARTS, **parts}
    return update_summary.build(
        state or a_record(),
        plan or a_plan(),
        got["applied"],
        got["config"],
        got["jobs"],
        got["findings"],
        got["exists"],
        got["size"],
        got["seconds"],
        agents=got["agents"],
    )


def rendered(summary: Summary) -> str:
    with mock.patch.dict(os.environ, {"HOME": HOME}):
        return update_summary.render(summary)


# The design's example, in its parts.
DESIGN_PLAN = a_plan(
    commits=tuple(f"{n:07x} acme: change {n}" for n in range(23)),
    rules=(("ledger", 1, 2),),
    keys_added=("BOSUN_EXPORT_RETRIES", "BOSUN_EXPORT_TIMEOUT", "BOSUN_INVOICE_DAYS"),
    prompts=(
        "orchestrator/policy.md",
        "orchestrator/tasks/review.md",
        "orchestrator/tasks/_common.md",
        "orchestrator/learn.md",
    ),
)
DESIGN_PARTS: dict[str, Any] = {
    "applied": [Applied("ledger", 1, 2, "412 items migrated")],
    "config": [BOSUN_ENV],
    "jobs": ["com.bosun.morning.watchdog"],
    "agents": AGENTS,
}


class TextTest(unittest.TestCase):
    def test_the_text_matches_the_design(self) -> None:
        self.assertEqual(rendered(built(plan=DESIGN_PLAN, **DESIGN_PARTS)), design_block())

    def test_nothing_changed_says_so(self) -> None:
        text = rendered(built())
        self.assertIn("\nData\n  ledger      format 1, unchanged\n  knowledge   format 1, unchanged\n", text)
        self.assertNotIn("Config", text)
        self.assertNotIn("Prompts changed", text)
        self.assertNotIn("Jobs reloaded", text)

    def test_a_backup_a_forget_deleted_is_said_so(self) -> None:
        summary = built(exists=False)
        self.assertTrue(summary.backup_gone)
        text = rendered(summary)
        self.assertIn(
            "\nBackup: deleted by a kg forget since; going back past it is no longer possible\n", text
        )
        self.assertNotIn("backups/", text)
        self.assertTrue(text.endswith("Going back: docs/setup.md#going-back\n"))

    def test_handed_items_and_my_files_are_named(self) -> None:
        record = a_record(user_changes=("sessions.yml: rename `model:` to `agent_model:`",))
        text = rendered(built(record, a_plan(handed=("task:1", "review:acme/api#12"))))
        self.assertIn(
            "Handed tasks keep their old text: bosun delegate --update task:1, "
            "bosun delegate --update 'review:acme/api#12'\n",
            text,
        )
        config = text.split("\nConfig\n", 1)[1].split("\nAgents\n", 1)[0]
        self.assertTrue(
            config.startswith("  sessions.yml  change by hand: rename `model:` to `agent_model:`\n"), text
        )

    def test_my_files_line_up_with_bosun_env(self) -> None:
        record = a_record(user_changes=("workstreams.json: add a `kind:` to each rule",))
        text = rendered(built(record, config=[BOSUN_ENV]))
        self.assertIn(
            "Config\n"
            "  bosun.env         BOSUN_OLD_KEY is now BOSUN_NEW_KEY "
            "(before: bosun.env.pre-update-20261104-153207)\n"
            "  workstreams.json  change by hand: add a `kind:` to each rule\n",
            text,
        )

    def test_windows_left_alone_kept_running_and_the_checkin_window_is_not_listed(self) -> None:
        plan = a_plan(
            windows=(
                ("orchestrator", Treatment.REREAD),
                ("checkin", Treatment.RESTART_LOOP),
                ("web", Treatment.LEAVE),
                ("api", Treatment.RESTART),
            )
        )
        said = (("api", "restarted; resumed: waiting on CI for PR 12"), ("orchestrator", "asked to re-read"))
        summary = built(plan=plan, agents=said)
        self.assertEqual(
            summary.agents,
            (
                ("orchestrator", "asked to re-read"),
                ("web", "kept running (nothing it was started with changed)"),
                ("api", "restarted; resumed: waiting on CI for PR 12"),
            ),
        )
        self.assertIn(
            "\nAgents\n  orchestrator  asked to re-read\n  web           kept running", rendered(summary)
        )

    def test_one_commit_and_one_key_are_said_so(self) -> None:
        text = rendered(built(plan=a_plan(commits=("aaaaaaa acme: one",), keys_added=("BOSUN_X",))))
        self.assertTrue(text.startswith("bosun updated: 95545b0 -> c41b186 (1 commit) in 2m 41s\n"), text)
        self.assertIn("\n  1 new key, at its default (bosun doctor lists it)\n", text)

    def test_each_migration_of_a_store_is_said_and_a_store_not_made_yet(self) -> None:
        applied = [Applied("ledger", 1, 2, "412 items migrated"), Applied("ledger", 2, 3, "9 links rebuilt")]
        summary = built(plan=a_plan(formats=(("ledger", 1), ("knowledge", 0))), applied=applied)
        self.assertEqual(
            summary.data,
            (("ledger", "format 1 -> 3 (412 items migrated; 9 links rebuilt)"), ("knowledge", "none yet")),
        )

    def test_a_rules_change_alone_is_a_version_bump(self) -> None:
        summary = built(plan=a_plan(rules=(("knowledge", 3, 4),)))
        self.assertEqual(dict(summary.data)["knowledge"], "format 1, unchanged; rules 3 -> 4")

    def test_what_was_found_after_the_switch_is_listed(self) -> None:
        text = rendered(built(findings=["warn: on battery; scheduled wakes and caffeinate -s need AC"]))
        self.assertIn(
            "\nFound after the switch\n  warn: on battery; scheduled wakes and caffeinate -s need AC\n"
            "Backup: ",
            text,
        )

    def test_the_jobs_are_named_short(self) -> None:
        text = rendered(built(jobs=["com.bosun.morning", "com.bosun.morning.learn"]))
        self.assertIn("\nJobs reloaded: morning, learn\n", text)

    def test_durations(self) -> None:
        for seconds, said in ((0, "0s"), (41, "41s"), (161, "2m 41s"), (3723, "1h 2m 3s")):
            self.assertTrue(
                rendered(built(seconds=seconds)).startswith(f"bosun updated: 95545b0 -> c41b186 in {said}\n")
            )

    def test_sizes(self) -> None:
        for size, said in (
            (512, "512 bytes"),
            (12_300, "12.3 KB"),
            (3_100_000, "3.1 MB"),
            (2_500_000_000, "2.5 GB"),
        ):
            self.assertIn(f"backups/20261104-153207-95545b0-c41b186 ({said})\n", rendered(built(size=size)))

    def test_a_backup_outside_the_home_folder_is_shown_whole(self) -> None:
        text = rendered(built(a_record(backup="/srv/acme/bosun/backups/20261104-153207-95545b0-c41b186")))
        self.assertIn("\nBackup: /srv/acme/bosun/backups/20261104-153207-95545b0-c41b186 (3.1 MB)\n", text)
        self.assertEqual(update_summary.tilde(f"{HOME}x/backups"), f"{HOME}x/backups")

    def test_the_json_carries_the_same(self) -> None:
        summary = built(plan=DESIGN_PLAN, **DESIGN_PARTS)
        with mock.patch.dict(os.environ, {"HOME": HOME}):
            said = json.loads(json.dumps(update_summary.as_json(summary)))
        self.assertEqual(set(said), {field.name for field in dataclasses.fields(Summary)})
        self.assertEqual(said["backup"], BACKUP)  # absolute, never ~
        self.assertEqual(
            said["data"],
            {
                "ledger": "format 1 -> 2 (412 items migrated); rules 1 -> 2",
                "knowledge": "format 1, unchanged",
            },
        )
        self.assertEqual(said["agents"], dict(AGENTS))
        self.assertEqual(
            said["config"], [BOSUN_ENV, "3 new keys, at their defaults (bosun doctor lists them)"]
        )
        self.assertEqual((said["from_commit"], said["commits"], said["seconds"]), (FROM, 23, 161))
        self.assertEqual((said["backup_bytes"], said["backup_gone"]), (3_100_000, False))
        self.assertEqual(said["jobs"], ["com.bosun.morning.watchdog"])


class RecordTest(unittest.TestCase):
    """The summary of an update cut short and carried on is the one it would have said itself: what
    each step learnt is in its record (update.json), read back by of_record."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_a_resumed_record_builds_the_same_summary(self) -> None:
        record = a_record(
            plan=update_plan.as_record(DESIGN_PLAN),
            migrated=tuple(applied.as_record() for applied in DESIGN_PARTS["applied"]),
            config_changed=(BOSUN_ENV,),
            reloaded=tuple(DESIGN_PARTS["jobs"]),
            findings=("warn: on battery",),
        )
        update_state.write(self.dir, record)
        read = update_state.read(self.dir)
        assert read is not None
        resumed = update_summary.of_record(read, AGENTS, True, 3_100_000, 161)
        in_one_go = built(record, DESIGN_PLAN, findings=["warn: on battery"], **DESIGN_PARTS)
        self.assertEqual(resumed, in_one_go)

    def test_a_record_from_before_its_parts_were_kept_says_less_never_something_false(self) -> None:
        """An update cut short by a bosun that kept no plan in its record, carried on by this one."""
        summary = update_summary.of_record(a_record(), (), True, 3_100_000, 161)
        text = rendered(summary)
        self.assertEqual(
            text,
            "bosun updated: 95545b0 -> c41b186 in 2m 41s\n"
            "Backup: ~/.local/state/bosun/backups/20261104-153207-95545b0-c41b186 (3.1 MB)\n"
            "Going back: docs/setup.md#going-back\n",
        )

    def test_an_update_that_took_no_backup_names_none(self) -> None:
        summary = built(a_record(backup=""), exists=False)
        self.assertFalse(summary.backup_gone)
        self.assertNotIn("Backup:", rendered(summary))


if __name__ == "__main__":
    unittest.main()
