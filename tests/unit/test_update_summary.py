"""lib/update_summary.py: what `bosun update` says when it ends (docs/design/update.md, "The
summary"), built from the update's record and what its steps recorded, so it is the same whether the
update ran in one go or was carried on. Fictional data: acme's windows api and web, a home folder
/Users/me."""

from __future__ import annotations

import dataclasses
import fnmatch
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import migrations
import update_agents
import update_plan
import update_state
import update_summary
from migrations import Applied
from update_plan import Plan, Treatment, VersionFacts, WindowFacts
from update_state import UpdateState
from update_summary import Summary


DESIGN = Path(__file__).resolve().parents[2] / "docs" / "design" / "update.md"
HOME = "/Users/me"
FROM = "95545b0" + "1" * 33
TO = "c41b186" + "2" * 33
BACKUP = f"{HOME}/.local/state/bosun/backups/20261104-153207-95545b0-c41b186"
INSTALL = f"{HOME}/.local/share/bosun"
LABEL = "com.bosun.morning"  # BOSUN_LAUNCHD_LABEL's default
BOSUN_ENV = "bosun.env: BOSUN_OLD_KEY is now BOSUN_NEW_KEY (before: bosun.env.pre-update-20261104-153207)"
AGENTS = (
    ("orchestrator", "asked to re-read policy.md; checked in"),
    ("api", "restarted; resumed: waiting on CI for PR 12"),
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


class Notes:
    """The windows' notes (update_agents.Notes), from a table."""

    def __init__(self, kept: dict[tuple[str, str], str]) -> None:
        self.kept = dict(kept)

    def read(self, window: str, kind: str) -> str | None:
        return self.kept.get((window, kind))

    def save(self, window: str, kind: str, text: str) -> None:
        self.kept[(window, kind)] = text

    def where(self, window: str, kind: str) -> str:
        return f"{HOME}/.local/state/bosun/update/notes/{window}.{kind}"


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


def rendered(summary: Summary, label: str = LABEL) -> str:
    """The text, with the home folder and the launchd label passed in: render reads no environment
    ($HOME here is another folder)."""
    with mock.patch.dict(os.environ, {"HOME": "/Users/someone-else"}):
        return update_summary.render(summary, home=HOME, launchd_label=label)


# ---- the design's example, from its real parts -------------------------------------------------

PROMPTS = (
    "orchestrator/learn.md",
    "orchestrator/plugin/commands/chores.md",
    "orchestrator/policy.md",
    "orchestrator/tasks/_common.md",
    "orchestrator/tasks/fix.md",
    "orchestrator/tasks/review.md",
)
CHANGED = (
    "orchestrator/policy.md",
    "orchestrator/tasks/review.md",
    "orchestrator/tasks/_common.md",
    "orchestrator/learn.md",
)


def design_plan() -> Plan:
    """The plan as make_plan makes it for the design's example: four prompts changed, the ledger's
    format and rules bumped, three new keys, a key renamed, and three windows (the orchestrator, which
    only re-reads; api, whose launch changed, idle; web, whose launch did not change, busy)."""
    keys = frozenset({"BOSUN_DAYS"})
    old = VersionFacts(FROM, {"ledger": 1, "knowledge": 1}, {"ledger": 1}, keys, dict.fromkeys(PROMPTS, "a"))
    new = VersionFacts(
        TO,
        {"ledger": 2, "knowledge": 1},
        {"ledger": 2},
        keys | {"BOSUN_EXPORT_RETRIES", "BOSUN_EXPORT_TIMEOUT", "BOSUN_INVOICE_DAYS"},
        {**dict.fromkeys(PROMPTS, "a"), **dict.fromkeys(CHANGED, "b")},
    )
    return update_plan.make_plan(
        old,
        new,
        commits=[f"{n:07x} acme: change {n}" for n in range(23)],
        windows=[
            WindowFacts("orchestrator", "orchestrator", "idle", False),
            WindowFacts("api", "repo", "idle", True),
            WindowFacts("web", "repo", "busy", False),
        ],
        jobs=["com.bosun.morning.watchdog"],
        renames=[("BOSUN_OLD_KEY", "BOSUN_NEW_KEY")],
        user_changes=[],
        in_flight=[],
        missing=[],
        deletion_cut_short=False,
        console=False,
        steps=lambda store, found, target: [(1, 2)],
    )


def design_record(plan: Plan) -> UpdateState:
    """The record as the update's steps leave it once each window answered: the plan, the migration
    that ran, bosun.env's change, the job reloaded, and each window brought along (update_agents'
    vocabulary), the orchestrator asked to re-read the changed files it reads."""
    windows = update_agents.to_bring_along(plan.windows, dict(plan.kinds))
    done = {"orchestrator": ("asked to re-read", "answered"), "api": ("restarted", "answered")}
    reread = [p for p in plan.prompts if any(fnmatch.fnmatchcase(p, x) for x in update_plan.REREAD)]
    return a_record(
        plan=update_plan.as_record(plan),
        migrated=(Applied("ledger", 1, 2, "412 items migrated").as_record(),),
        config_changed=(BOSUN_ENV,),
        reloaded=("com.bosun.morning.watchdog",),
        windows={name: (*entry, *done[name]) for name, entry in windows.items()},
        reread=tuple(f"{INSTALL}/{path}" for path in reread),
    )


DESIGN_NOTES = Notes(
    {
        ("orchestrator", "status"): "checked in (2 events from the update)",
        ("api", "status"): "resumed: waiting on CI for PR 12",
    }
)


def design_summary() -> Summary:
    record = design_record(design_plan())
    return update_summary.of_record(
        record, update_agents.outcomes(record, DESIGN_NOTES), True, 3_100_000, 161
    )


class TextTest(unittest.TestCase):
    def test_the_text_matches_the_design(self) -> None:
        """The design's example is what the code prints: the plan from make_plan (its prompts
        sorted), each window's line from update_agents.outcomes over the record and the notes."""
        self.assertEqual(rendered(design_summary()), design_block())

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

    def test_render_reads_no_environment(self) -> None:
        summary = built()
        with mock.patch.dict(os.environ, {"HOME": HOME}):
            self.assertIn(f"Backup: {BACKUP} (", update_summary.render(summary))  # no home passed: whole
        self.assertIn("Backup: ~/.local/state/bosun/backups/", rendered(summary))
        self.assertEqual(
            update_summary.tilde(BACKUP, HOME), "~/.local/state/bosun/backups/20261104-153207-95545b0-c41b186"
        )
        self.assertEqual(update_summary.tilde(f"{HOME}x/backups", HOME), f"{HOME}x/backups")
        self.assertEqual(update_summary.tilde(BACKUP, ""), BACKUP)

    def test_windows_left_alone_kept_running_and_the_checkin_window_is_not_listed(self) -> None:
        plan = a_plan(
            windows=(
                ("orchestrator", Treatment.REREAD),
                ("checkin", Treatment.RESTART_LOOP),
                ("web", Treatment.LEAVE),
                ("api", Treatment.RESTART),
                ("mine", Treatment.NOT_STARTED_BY_BOSUN),
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
                ("mine", "left as it is (bosun did not start it)"),
            ),
        )
        self.assertIn(
            "\nAgents\n  orchestrator  asked to re-read\n  web           kept running", rendered(summary)
        )

    def test_one_commit_and_one_key_are_said_so(self) -> None:
        text = rendered(built(plan=a_plan(commits=("aaaaaaa acme: one",), keys_added=("BOSUN_X",))))
        self.assertTrue(text.startswith("bosun updated: 95545b0 -> c41b186 (1 commit) in 2m 41s\n"), text)
        self.assertIn("\n  1 new key, at its default (bosun doctor lists it)\n", text)

    def test_summary_shows_added_key_notes(self) -> None:
        """Each added key with a note gets its line, under Config: a kept key's once the update wrote
        its value (plan.kept, beside bosun.env's line), one whose default applies always; the count
        is of the other keys only."""
        written = (
            'bosun.env: BOSUN_ORCHESTRATOR_CONNECTORS="all" added (before: bosun.env.pre-update-20261104)'
        )
        plan = a_plan(
            keys_added=("BOSUN_CONNECTORS", "BOSUN_ORCHESTRATOR_CONNECTORS", "BOSUN_X"),
            kept=(("BOSUN_ORCHESTRATOR_CONNECTORS", "all"),),
        )
        notes = {entry.key: entry.note for entry in migrations.CONFIG_ADDED}
        summary = built(plan=plan, config=[written])
        self.assertEqual(
            summary.config,
            (
                written,
                f"BOSUN_ORCHESTRATOR_CONNECTORS: {notes['BOSUN_ORCHESTRATOR_CONNECTORS']}",
                f"BOSUN_CONNECTORS: {notes['BOSUN_CONNECTORS']}",
                "1 new key, at its default (bosun doctor lists it)",
            ),
        )
        text = rendered(summary)
        self.assertIn(f"\n  BOSUN_CONNECTORS               {notes['BOSUN_CONNECTORS']}\n", text)
        self.assertIn(
            '\n  bosun.env                      BOSUN_ORCHESTRATOR_CONNECTORS="all" added (before: ', text
        )
        unwritten = built(plan=dataclasses.replace(plan, kept=())).config
        self.assertEqual(
            unwritten,
            (
                f"BOSUN_CONNECTORS: {notes['BOSUN_CONNECTORS']}",
                "2 new keys, at their defaults (bosun doctor lists them)",
            ),
        )

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
        text = rendered(built(findings=["FAIL: gh is not logged in: run 'gh auth login'"]))
        self.assertIn(
            "\nFound after the switch\n  FAIL: gh is not logged in: run 'gh auth login'\nBackup: ", text
        )

    def test_the_jobs_are_named_after_the_configured_label(self) -> None:
        jobs = ["com.acme.bosun", "com.acme.bosun.watchdog", "com.acme.bosun.learn", "org.example.other"]
        text = rendered(built(jobs=jobs), label="com.acme.bosun")
        self.assertIn("\nJobs reloaded: morning, watchdog, learn, org.example.other\n", text)
        self.assertIn("\nJobs reloaded: com.acme.bosun, ", rendered(built(jobs=jobs), label=""))

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

    def test_the_json_carries_the_same(self) -> None:
        summary = design_summary()
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
        self.assertEqual(
            said["agents"],
            {
                "orchestrator": "asked to re-read policy.md, tasks/_common.md, tasks/review.md; "
                "checked in (2 events from the update)",
                "api": "restarted; resumed: waiting on CI for PR 12",
                "web": "kept running (nothing it was started with changed)",
            },
        )
        self.assertEqual(
            said["config"], [BOSUN_ENV, "3 new keys, at their defaults (bosun doctor lists them)"]
        )
        self.assertEqual(said["prompts"], sorted(CHANGED))
        self.assertEqual((said["from_commit"], said["commits"], said["seconds"]), (FROM, 23, 161))
        self.assertEqual((said["backup_size"], said["backup_gone"]), (3_100_000, False))
        self.assertEqual(said["jobs"], ["com.bosun.morning.watchdog"])  # the label, whole


class FoundTest(unittest.TestCase):
    """What the update found after the switch, for the summary: what bosun doctor fails at that
    it did not before the update (each FAIL with its detail), how many already failed, and how many
    warnings; and the release's own findings."""

    BEFORE = ("FAIL: gh is not logged in: run 'gh auth login'",)

    def found(self, findings: tuple[str, ...], before: tuple[str, ...] = BEFORE) -> tuple[str, ...]:
        record = a_record(findings=findings, doctor_before=before)
        return update_summary.of_record(record, (), True, 3_100_000, 161).findings

    def test_a_fail_that_was_not_there_before_is_listed_with_the_release_s_own(self) -> None:
        found = self.found(
            (
                "launchd: bootstrap of com.bosun.morning.watchdog failed",
                "FAIL: the ledger's journal refuses writes: journal: a position that does not match",
            )
        )
        self.assertEqual(
            found,
            (
                "launchd: bootstrap of com.bosun.morning.watchdog failed",
                "FAIL: the ledger's journal refuses writes: journal: a position that does not match",
            ),
        )

    def test_what_already_failed_before_the_update_is_counted_not_listed(self) -> None:
        found = self.found(
            ("FAIL: gh is not logged in: run 'gh auth login'", "FAIL: no repeating pmset wake")
        )
        self.assertEqual(found, ("FAIL: no repeating pmset wake", "1 already failing before the update"))

    def test_warnings_are_counted(self) -> None:
        self.assertEqual(self.found(("warn: on battery",)), ("1 warning: run bosun doctor",))
        found = self.found(
            ("warn: on battery", "warn: lid is closed", "FAIL: gh is not logged in: run 'gh auth login'")
        )
        self.assertEqual(found, ("1 already failing before the update", "2 warnings: run bosun doctor"))

    def test_nothing_found_is_no_section(self) -> None:
        self.assertEqual(self.found(()), ())
        self.assertNotIn(
            "Found after the switch", rendered(update_summary.of_record(a_record(), (), True, 1, 1))
        )

    def test_a_record_without_the_doctor_run_before_lists_every_fail(self) -> None:
        self.assertEqual(
            self.found(("FAIL: gh is not logged in: run 'gh auth login'",), before=()),
            ("FAIL: gh is not logged in: run 'gh auth login'",),
        )


class DoctorTest(unittest.TestCase):
    """bosun doctor's output read for the summary: each FAIL and warn with its indented detail joined
    onto it, a log line's time and tag left out, so two runs compare by what they say."""

    SAID = """What bosun needs
  FAIL  something is missing or not done yet:
    tmux    not found
    jq      not found
  ok    tools are there
Config (/Users/me/.config/bosun)
  FAIL  2026-11-07 08:12:39 [doctor] ERROR: sessions manifest not found: /Users/me/.config/bosun/sessions.yml
  warn  proposals awaiting approval:
          slack:C0000000000/1 [mention]
  ok    the ledger and its journal: ledger is valid
          not a detail of the warn: an ok came between
Ledger (/Users/me/.local/state/bosun)
  FAIL  the ledger's journal refuses writes (every finding: bosun ledger validate --journal):
          journal: a position that does not match

all checks passed
"""

    def test_each_fail_and_warn_with_its_detail(self) -> None:
        self.assertEqual(
            update_summary.doctor_found(self.SAID),
            [
                "FAIL: something is missing or not done yet: tmux not found; jq not found",
                "FAIL: ERROR: sessions manifest not found: /Users/me/.config/bosun/sessions.yml",
                "warn: proposals awaiting approval: slack:C0000000000/1 [mention]",
                (
                    "FAIL: the ledger's journal refuses writes (every finding: bosun ledger validate "
                    "--journal): journal: a position that does not match"
                ),
            ],
        )


class RecordTest(unittest.TestCase):
    """The summary of an update cut short and carried on is the one it would have said itself: what
    each step learnt is in its record (update.json), read back by of_record."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_a_resumed_record_builds_the_same_summary(self) -> None:
        plan = design_plan()
        record = dataclasses.replace(
            design_record(plan),
            findings=("FAIL: gh is not logged in", "warn: on battery"),
            doctor_before=("FAIL: gh is not logged in",),
        )
        update_state.write(self.dir, record)
        read = update_state.read(self.dir)
        assert read is not None
        agents = update_agents.outcomes(read, DESIGN_NOTES)
        resumed = update_summary.of_record(read, agents, True, 3_100_000, 161)
        in_one_go = built(
            record,
            plan,
            applied=[Applied("ledger", 1, 2, "412 items migrated")],
            config=[BOSUN_ENV],
            jobs=["com.bosun.morning.watchdog"],
            findings=["1 already failing before the update", "1 warning: run bosun doctor"],
            agents=update_agents.outcomes(record, DESIGN_NOTES),
        )
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


class LeftToMeTest(unittest.TestCase):
    """What the last update left to me, which bosun doctor lists: the changes to make in my own files
    (never applied), and the config keys it added that bosun.env does not set."""

    def record(self, step: str = "done", keys: tuple[str, ...] = ()) -> UpdateState:
        return a_record(
            step=step,
            user_changes=("sessions.yml: rename model: to agent_model:",),
            plan={"keys_added": list(keys)},
        )

    def test_my_file_changes_and_the_new_keys_bosun_env_does_not_set(self) -> None:
        record = self.record(keys=("BOSUN_EXPORT_RETRIES", "BOSUN_EXPORT_TIMEOUT", "not a key; rm -rf"))
        self.assertEqual(
            update_summary.left_to_me(record, {"BOSUN_EXPORT_TIMEOUT"}),
            [("change", "sessions.yml: rename model: to agent_model:"), ("key", "BOSUN_EXPORT_RETRIES")],
        )

    def test_nothing_without_a_record_or_before_the_update_brought_the_windows_along(self) -> None:
        """Before then the update says them itself (doctor in its release would say them twice), and an
        update put back or failed left nothing to change."""
        self.assertEqual(update_summary.left_to_me(None, set()), [])
        for step in ("planned", "migrated", "checked", "released", "failed", "restored"):
            self.assertEqual(update_summary.left_to_me(self.record(step, ("BOSUN_X",)), set()), [], step)
        self.assertEqual(len(update_summary.left_to_me(self.record("agents", ("BOSUN_X",)), set())), 2)


if __name__ == "__main__":
    unittest.main()
