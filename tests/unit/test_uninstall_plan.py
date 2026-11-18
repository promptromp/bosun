"""lib/uninstall_plan.py: what goes and what stays, for every kind of file and answer."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import ClassVar


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_state
import file_tables as ft
import journal as jr
import log_files
import uninstall_plan as up
import update_backups as ub
from checkout_work import Checkout
from uninstall_plan import Choices, Facts, Job, Step


HOME = "/Users/me"
STATE = f"{HOME}/.local/state/bosun"
CONFIG = f"{HOME}/.config/bosun"
ROOT = f"{HOME}/.local/share/bosun"
VERSIONS = f"{HOME}/.local/share/bosun-versions"
LEGACY = f"{VERSIONS}/legacy-0123456789ab"
WAKE_LINE = "wakepoweron at 9:01AM every day"


def facts(**changes: object) -> Facts:
    base = Facts(
        home=HOME,
        checkout=Checkout(ROOT, managed=True),
        config_dir=CONFIG,
        state_dir=STATE,
        log_dir=f"{STATE}/logs",
        bosun=f"{ROOT}/bin/bosun",
        windows=("orchestrator", "api"),
        jobs=(Job("com.bosun.morning", f"{HOME}/Library/LaunchAgents/com.bosun.morning.plist", True),),
        repeating=(WAKE_LINE,),
        wake_at="9:01AM",
        link=f"{HOME}/.local/bin/bosun",
        trust=(("claude", f"{HOME}/Development/api"),),
        notes=(f"{HOME}/.claude/projects/x/memory/bosun-rule.md",),
        state_entries=("ledger.json", "ledger.md", "ledger.lock", "logs", "events", "learn", "mystery"),
        config_entries=(
            "bosun.env",
            "sessions.yml",
            "setup.sh",
            "knowledge",
            "forbidden-terms.txt",
            "mine.txt",
        ),
        ledger_items=3,
        knowledge_records=5,
        knowledge_files=3,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def side_by_side(**changes: object) -> Facts:
    """Versions side by side: the link at ROOT, the versions folder with two versions of bosun (each a
    worktree of its repository there) and the checkout from before versions, kept as legacy-*."""
    base = facts(
        checkout=Checkout(VERSIONS, managed=True),
        versions=(
            Checkout(f"{VERSIONS}/0123456789ab", managed=True),
            Checkout(f"{VERSIONS}/0f1e2d3c4b5a", managed=True),
        ),
        legacy=(Checkout(LEGACY, managed=True),),
        install_link=ROOT,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def targets(plan: up.Plan, kind: str = up.FILE) -> set[str]:
    return {step.target for step in plan.steps if step.kind == kind}


def stays(plan: up.Plan) -> set[str]:
    return {stay.path for stay in plan.stays}


class TheMenuBarApp(unittest.TestCase):
    APP = f"{HOME}/Applications/bosun.app"

    def test_the_app_is_a_step_with_what_to_run_by_hand(self) -> None:
        plan = up.build_plan(facts(app=self.APP), up.Choices())
        steps = [step for step in plan.steps if step.kind == up.APP]
        self.assertEqual([(step.target, step.by_hand) for step in steps], [(self.APP, f"rm -rf {self.APP}")])

    def test_it_comes_after_the_jobs_and_before_the_windows(self) -> None:
        kinds = [step.kind for step in up.build_plan(facts(app=self.APP), up.Choices()).steps]
        self.assertLess(kinds.index(up.JOB), kinds.index(up.APP))
        self.assertLess(kinds.index(up.APP), kinds.index(up.STOP))

    def test_it_reads_as_the_menu_bar_app_in_the_plan(self) -> None:
        plan = up.build_plan(facts(app=self.APP), up.Choices())
        text = up.render_plan(plan, facts(app=self.APP))
        self.assertIn("the menu bar app, ~/Applications/bosun.app", text)
        self.assertIn("asked to quit first", text)

    def test_leftovers_are_a_step_each_with_what_to_run_by_hand(self) -> None:
        left = f"{HOME}/Applications/.bosun-app-4242-abc"
        plan = up.build_plan(facts(app_leftovers=(left,)), up.Choices())
        (step,) = [step for step in plan.steps if step.kind == up.LEFTOVER]
        self.assertEqual((step.target, step.by_hand), (left, f"rm -rf {left}"))
        self.assertIn(
            "left, ~/Applications/.bosun-app-4242-abc", up.render_plan(plan, facts(app_leftovers=(left,)))
        )

    def test_another_app_of_that_name_is_no_step_but_a_stay_with_its_reason(self) -> None:
        other = (self.APP, "its bundle id is ai.other.app, not the publisher's ai.example.bosun")
        plan = up.build_plan(facts(app_other=other), up.Choices())
        self.assertEqual([step for step in plan.steps if step.kind == up.APP], [])
        (stay,) = [stay for stay in plan.stays if stay.path == self.APP]
        self.assertIn("it is not bosun's app", stay.why)
        self.assertIn("ai.other.app", stay.why)

    def test_no_app_no_step(self) -> None:
        plan = up.build_plan(facts(), up.Choices())
        self.assertEqual([step for step in plan.steps if step.kind == up.APP], [])

    def test_the_login_job_is_a_job_step_like_the_others(self) -> None:
        job = Job("com.bosun.morning.app", f"{HOME}/Library/LaunchAgents/com.bosun.morning.app.plist", True)
        plan = up.build_plan(facts(jobs=(job,)), up.Choices())
        (step,) = [step for step in plan.steps if step.kind == up.JOB]
        self.assertEqual(step.target, "com.bosun.morning.app")
        self.assertIn("launchctl bootout", step.by_hand)
        self.assertIn(f"rm -f {job.plist}", step.by_hand)


class FileTables(unittest.TestCase):
    def test_each_agents_state_files_are_rows(self) -> None:
        rows = ft.agent_state_rows()
        for agent in agent_state.AGENTS.values():
            for name in agent.PROFILE.state_files:
                self.assertIn((name, ft.WORKING), rows)
                self.assertIn((name, ft.WORKING), ft.STATE_FILES)
                if "/" not in name:
                    self.assertIn((f".{name}.*", ft.WORKING), rows)
                    self.assertIn((f".{name}.*", ft.WORKING), ft.STATE_FILES)

    def test_the_rows_for_codex_are_what_they_were(self) -> None:
        self.assertIn(("codex-sessions.json", ft.WORKING), ft.STATE_FILES)
        self.assertIn((".codex-sessions.json.*", ft.WORKING), ft.STATE_FILES)

    def test_the_login_jobs_marker_and_logs_go_with_bosun(self) -> None:
        self.assertEqual(ft.kind_of("login-reload", ft.STATE_FILES), ft.WORKING)
        for name in ("login.launchd.out", "login.launchd.err"):
            self.assertEqual(ft.kind_of(name, ft.LOG_FILES), ft.WORKING, name)

    def test_what_each_file_is(self) -> None:
        for name, kind in (
            ("ledger.json", ft.LEDGER),
            ("ledger.json.bak", ft.LEDGER),
            ("ledger.json.tmp", ft.WORKING),
            ("learn", ft.LEDGER),
            ("journal", ft.LEDGER),
            ("replica.json", ft.LEDGER),
            (jr.REPLICA_TEMP.format(writer="4242.1a2b3c4d"), ft.WORKING),  # replica.json cut short
            ("ledger.lock", ft.WORKING),
            ("logs", ft.WORKING),
            ("stopped-bosun", ft.WORKING),
            ("keep-awake-bosun.pid", ft.WORKING),
            ("keep-awake-bosun.json", ft.WORKING),  # when the hold ends, and its pid (bosun status --json)
            (".keep-awake-bosun.json.4242", ft.WORKING),  # that, cut short
            (".ledger.json.restoring", ft.WORKING),  # what an update's restore was writing, cut short
            (".replica.json.restoring", ft.WORKING),
            ("anything-else", ft.UNKNOWN),
        ):
            self.assertEqual(ft.kind_of(name, ft.STATE_FILES), kind, name)
        for name, kind in (
            ("bosun.env", ft.SETTINGS),
            ("bosun.env.pre-init-20261102-080000", ft.SETTINGS),
            ("bosun.env.pre-update-20261106-093005", ft.SETTINGS),
            ("bosun.env.pre-back-20261118-154200", ft.SETTINGS),  # what bosun update --back kept
            (".bosun.env.update.tmp", ft.WORKING),  # bosun update's bosun.env, cut short
            (
                jr.WRITING.format(name="bosun.env.pre-update-20261106-093005", writer="4242.1a2b3c4d"),
                ft.WORKING,
            ),
            ("tasks", ft.SETTINGS),
            (".bosun.env.restoring", ft.WORKING),  # what an update's restore was writing, cut short
            (".sessions.yml.restoring", ft.WORKING),
            ("knowledge", ft.KNOWLEDGE),
            ("forbidden-terms.txt", ft.OURS_TO_LEAVE),
            ("my-notes.txt", ft.UNKNOWN),
        ):
            self.assertEqual(ft.kind_of(name, ft.CONFIG_FILES), kind, name)

    def test_a_restores_temporary_file_is_working_in_both_tables(self) -> None:
        """lib/update_backups.py writes each file it puts back as .<name>.restoring beside it first."""
        for name, table in (("ledger.json", ft.STATE_FILES), ("bosun.env", ft.CONFIG_FILES)):
            temporary = ub.RESTORING.format(name=name)
            self.assertEqual(ft.kind_of(temporary, table), ft.WORKING, temporary)

    def test_no_table_lists_a_name_twice(self) -> None:
        for table in (ft.STATE_FILES, ft.CONFIG_FILES, ft.LOG_FILES):
            names = [pattern for pattern, _ in table]
            self.assertEqual(len(names), len(set(names)))


class Folders(unittest.TestCase):
    def test_never_home_or_what_holds_it(self) -> None:
        for path in (HOME, "/Users", "/", "", "relative/path"):
            self.assertFalse(up.safe_folder(path, facts()), path)

    def test_nor_one_found_to_be_home_under_another_spelling(self) -> None:
        self.assertFalse(up.safe_folder("/users/me", facts(unsafe=("/users/me",))))

    def test_a_folder_of_its_own(self) -> None:
        for path in (STATE, CONFIG, "/opt/bosun-state", f"{HOME}/Documents"):
            self.assertTrue(up.safe_folder(path, facts()), path)

    def test_a_folder_that_also_holds_other_files_is_left_whole(self) -> None:
        shared = facts(
            state_dir=f"{HOME}/work", log_dir=f"{HOME}/work/logs", state_entries=("logs", "notes.txt")
        )
        plan = up.build_plan(shared, Choices(keep_ledger=False))
        self.assertFalse([t for t in targets(plan) if t.startswith(f"{HOME}/work")])
        why = next(s.why for s in plan.stays if s.path == f"{HOME}/work")
        self.assertIn("notes.txt", why)

    def test_a_folder_named_for_bosun_loses_its_files_and_keeps_the_rest(self) -> None:
        plan = up.build_plan(facts(), Choices())
        self.assertIn(f"{STATE}/logs", targets(plan))
        self.assertIn(f"{STATE}/mystery", stays(plan))

    def test_a_folder_of_known_files_only_is_bosuns_whatever_its_name(self) -> None:
        own = facts(state_dir="/srv/state", log_dir="/srv/state/logs", state_entries=("logs", "events"))
        self.assertEqual(targets(up.build_plan(own, Choices())), {"/srv/state/logs", "/srv/state/events"})

    def test_a_log_folder_of_its_own_matches_bosun_s_names_only(self) -> None:
        for name in (
            "morning-2026-11-02.log",
            "watchdog.launchd.err",
            "hook-2026-11-02.log.1",
            "launch-2026-11-02.log",
            # every command's own day file (lib/common.sh's log)
            "install-2026-11-02.log",
            "watchdog-2026-11-02.log",
            "slack-watch-2026-11-02.log",
            "settings-2026-11-02.log",
            # what an agent writes there, by its profile (agy's window logs, and the dated ones rolled over)
            "agy-api.log",
            "agy-api-2026-11-02-1.log",
        ):
            self.assertEqual(ft.kind_of(name, ft.LOG_FILES), ft.WORKING, name)
            if name.endswith(".log") and not name.startswith("agy-"):
                self.assertIsNotNone(log_files.day_of(name), name)  # what the morning run prunes, too
        for name in (
            "Zoom.log",
            "vpn-client.log",
            "Install-2026-11-02.log",
            "report-2026-11.log",
            "backup-2026-11-01.log",  # someone else's day file in a shared folder
            "rsync-backup-2026-11-01.log",
        ):
            self.assertEqual(ft.kind_of(name, ft.LOG_FILES), ft.UNKNOWN, name)
            self.assertIsNone(log_files.day_of(name), name)  # nor does the morning run prune it

    def test_bosun_s_log_writers_are_its_commands_and_install_sh(self) -> None:
        """lib/common.sh's log names its file after the command bin/bosun runs (libexec/bosun-<command>)."""
        libexec = Path(__file__).resolve().parents[2] / "libexec"
        commands = {path.name.removeprefix("bosun-") for path in libexec.glob("bosun-*")}
        self.assertEqual(set(log_files.WRITERS), commands | {"install", "bosun"})

    def test_an_unsafe_folder_is_left_whole_and_named(self) -> None:
        plan = up.build_plan(facts(state_dir=HOME, log_dir=f"{HOME}/logs"), Choices(keep_ledger=False))
        self.assertFalse([t for t in targets(plan) if t.startswith(f"{HOME}/l")])
        self.assertIn(HOME, stays(plan))
        self.assertIsNotNone(next(s for s in plan.stays if "holds your home" in s.why))


class Wake(unittest.TestCase):
    def test_bosuns_alone_is_cancelled(self) -> None:
        plan = up.build_plan(facts(), Choices())
        self.assertEqual(plan.steps[0], Step(up.WAKE, WAKE_LINE, by_hand=up.CANCEL_BY_HAND))

    def test_any_other_is_left_and_named(self) -> None:
        for repeating in (
            ("wakepoweron at 8:00AM every day",),  # another time
            ("shutdown at 9:01AM every day",),  # not a wake
            (WAKE_LINE, "shutdown at 11:00PM every day"),  # cancelling would take the other with it
        ):
            plan = up.build_plan(facts(repeating=repeating), Choices())
            self.assertFalse(targets(plan, up.WAKE), repeating)
            self.assertTrue(set(repeating) <= stays(plan), repeating)

    def test_no_wake_nothing_to_say(self) -> None:
        plan = up.build_plan(facts(repeating=()), Choices())
        self.assertFalse(targets(plan, up.WAKE))
        self.assertFalse([s for s in plan.stays if "power event" in s.why])

    def test_not_knowing_the_time_it_is_left(self) -> None:
        self.assertFalse(up.wake_is_bosuns(facts(wake_at="")))

    def test_the_time_to_the_minute(self) -> None:
        def bosuns(event: str) -> bool:
            return up.wake_is_bosuns(facts(repeating=(event,), wake_at="1:01AM", days="MTWRFSU"))

        self.assertTrue(bosuns("wakepoweron at 1:01AM every day"))
        self.assertFalse(bosuns("wakepoweron at 11:01AM every day"))
        self.assertFalse(bosuns("wakepoweron at 1:01PM every day"))

    def test_the_days_where_pmset_has_a_word_for_them(self) -> None:
        weekends = facts(repeating=("wakepoweron at 9:01AM weekends only",), days="MTWRF")
        self.assertFalse(up.wake_is_bosuns(weekends))
        weekdays = facts(repeating=("wakepoweron at 9:01AM weekdays only",), days="MTWRF")
        self.assertTrue(up.wake_is_bosuns(weekdays))
        some_days = facts(repeating=("wakepoweron at 9:01AM some days",), days="MWF")
        self.assertTrue(up.wake_is_bosuns(some_days))


class Order(unittest.TestCase):
    def test_nothing_can_bring_back_what_was_stopped(self) -> None:
        kinds = [step.kind for step in up.build_plan(facts(), Choices()).steps]
        for earlier, later in ((up.WAKE, up.JOB), (up.JOB, up.STOP), (up.STOP, up.TRUST), (up.LINK, up.FILE)):
            self.assertLess(kinds.index(earlier), kinds.index(later), (earlier, later))

    def test_the_checkout_is_not_among_the_steps(self) -> None:
        plan = up.build_plan(facts(), Choices())
        self.assertFalse(targets(plan, up.CHECKOUT))
        self.assertEqual(plan.install, (Step(up.CHECKOUT, ROOT, by_hand=f"rm -rf {ROOT}"),))

    def test_no_windows_nothing_to_stop(self) -> None:
        self.assertFalse(targets(up.build_plan(facts(windows=()), Choices()), up.STOP))

    def test_a_keep_awake_alone_is_still_stopped(self) -> None:
        plan = up.build_plan(facts(windows=(), keep_awake=True), Choices())
        self.assertEqual([s.kind for s in plan.steps if s.kind == up.STOP], [up.STOP])


class Answers(unittest.TestCase):
    def test_keeping_both(self) -> None:
        plan = up.build_plan(facts(), Choices())
        self.assertEqual(targets(plan), {f"{STATE}/ledger.lock", f"{STATE}/logs", f"{STATE}/events"})
        self.assertTrue({CONFIG, f"{STATE}/ledger.json", f"{CONFIG}/knowledge"} <= stays(plan))

    def test_keeping_neither(self) -> None:
        plan = up.build_plan(facts(), Choices(keep_ledger=False, keep_knowledge=False))
        for name in ("ledger.json", "ledger.md", "learn"):
            self.assertIn(f"{STATE}/{name}", targets(plan))
        self.assertIn(f"{CONFIG}/knowledge", targets(plan))
        self.assertNotIn(f"{CONFIG}/bosun.env", targets(plan))
        self.assertIn(CONFIG, stays(plan))

    def test_keeping_one(self) -> None:
        plan = up.build_plan(facts(), Choices(keep_ledger=True, keep_knowledge=False))
        self.assertNotIn(f"{STATE}/ledger.json", targets(plan))
        self.assertIn(f"{CONFIG}/knowledge", targets(plan))

    def test_the_update_backups_go_when_either_store_goes(self) -> None:
        with_backups = facts(state_entries=("ledger.json", "backups", "logs"))
        for choices, goes in (
            (Choices(keep_ledger=True, keep_knowledge=True), False),
            (Choices(keep_ledger=True, keep_knowledge=False), True),
            (Choices(keep_ledger=False, keep_knowledge=True), True),
            (Choices(keep_ledger=False, keep_knowledge=False), True),
        ):
            plan = up.build_plan(with_backups, choices)
            self.assertEqual(f"{STATE}/backups" in targets(plan), goes, choices)

    def test_purge_takes_what_is_bosuns_and_no_more(self) -> None:
        plan = up.build_plan(facts(), Choices(keep_ledger=False, keep_knowledge=False, purge=True))
        for name in ("bosun.env", "sessions.yml", "setup.sh", "knowledge"):
            self.assertIn(f"{CONFIG}/{name}", targets(plan))
        for name in ("forbidden-terms.txt", "mine.txt"):
            self.assertNotIn(f"{CONFIG}/{name}", targets(plan))
            self.assertIn(f"{CONFIG}/{name}", stays(plan))
        self.assertNotIn(f"{STATE}/mystery", targets(plan))
        self.assertIn(f"{STATE}/mystery", stays(plan))
        self.assertNotIn(CONFIG, stays(plan))

    def test_records_with_nothing_in_them_just_go(self) -> None:
        empty = facts(
            ledger_items=0, knowledge_records=0, knowledge_files=0, state_entries=("ledger.json", "logs")
        )
        plan = up.build_plan(empty, Choices())
        self.assertIn(f"{STATE}/ledger.json", targets(plan))
        self.assertIn(f"{CONFIG}/knowledge", targets(plan))
        self.assertEqual(up.questions(empty, Choices()), ())

    def test_a_knowledge_folder_with_anything_in_it_is_asked_about(self) -> None:
        for records in (0, up.UNREADABLE):  # only a schema.json, or files it cannot read
            something = facts(knowledge_records=records, knowledge_files=1)
            self.assertIn(ft.KNOWLEDGE, [q.key for q in up.questions(something, Choices())], records)
            self.assertNotIn(f"{CONFIG}/knowledge", targets(up.build_plan(something, Choices())))

    def test_a_hand_made_copy_of_the_ledger_is_not_a_working_file(self) -> None:
        self.assertEqual(ft.kind_of("ledger.json.before-my-edit", ft.STATE_FILES), ft.UNKNOWN)

    def test_learning_history_alone_is_worth_asking_about(self) -> None:
        learned = facts(ledger_items=0, state_entries=("ledger.json", "learn"))
        self.assertEqual([q.key for q in up.questions(learned, Choices())], [ft.LEDGER, ft.KNOWLEDGE])

    def test_a_ledger_it_cannot_read_may_hold_anything(self) -> None:
        unreadable = facts(ledger_items=up.UNREADABLE)
        self.assertNotIn(f"{STATE}/ledger.json", targets(up.build_plan(unreadable, Choices())))
        self.assertIn(ft.LEDGER, [q.key for q in up.questions(unreadable, Choices())])

    def test_questions_only_for_what_flags_left_open(self) -> None:
        self.assertEqual([q.key for q in up.questions(facts(), Choices(keep_ledger=False))], [ft.KNOWLEDGE])
        self.assertEqual(up.questions(facts(), Choices(keep_ledger=False, keep_knowledge=False)), ())

    def test_one_is_not_plural(self) -> None:
        ledger, knowledge = up.questions(facts(ledger_items=1, knowledge_records=1), Choices())
        self.assertIn("(1 item)", ledger.text)
        self.assertIn("(1 record)", knowledge.text)

    def test_a_question_says_how_much_and_where(self) -> None:
        ledger, knowledge = up.questions(facts(), Choices())
        self.assertIn("3 items", ledger.text)
        self.assertIn("~/.local/state/bosun", ledger.text)
        self.assertIn("5 records", knowledge.text)
        self.assertIn("~/.config/bosun/knowledge", knowledge.text)


class AgentsRecord(unittest.TestCase):
    def test_stays_while_what_it_lists_is_still_there(self) -> None:
        entries = ("agent-changes.json", "logs")
        self.assertNotIn(
            f"{STATE}/agent-changes.json", targets(up.build_plan(facts(state_entries=entries), Choices()))
        )
        done = facts(state_entries=entries, trust=(), notes=())
        self.assertIn(f"{STATE}/agent-changes.json", targets(up.build_plan(done, Choices())))


class AgentSetups(unittest.TestCase):
    SETUP = ("stub", "the bosun plugin installed in Stub")

    def test_a_recorded_setup_is_an_uninstall_step(self) -> None:
        plan = up.build_plan(facts(setups=(self.SETUP,)), Choices())
        self.assertIn(Step(up.SETUP, self.SETUP[1], "stub"), plan.steps)

    def test_a_setup_step_reads_with_its_description_and_agent(self) -> None:
        step = Step(up.SETUP, self.SETUP[1], "stub")
        self.assertEqual(up.describe(step, HOME), "the bosun plugin installed in Stub (stub)")

    def test_setups_come_with_the_trust_after_the_windows_stop(self) -> None:
        kinds = [step.kind for step in up.build_plan(facts(setups=(self.SETUP,)), Choices()).steps]
        self.assertLess(kinds.index(up.STOP), kinds.index(up.SETUP))
        self.assertLess(kinds.index(up.SETUP), kinds.index(up.LINK))

    def test_no_setup_no_step(self) -> None:
        self.assertNotIn(up.SETUP, [step.kind for step in up.build_plan(facts(), Choices()).steps])

    def test_the_record_stays_while_a_setup_is_still_there(self) -> None:
        entries = ("agent-changes.json", "agent-setup.lock", "logs")
        left = facts(state_entries=entries, trust=(), notes=(), setups=(self.SETUP,))
        planned = targets(up.build_plan(left, Choices()))
        self.assertNotIn(f"{STATE}/agent-changes.json", planned)
        self.assertIn(f"{STATE}/agent-setup.lock", planned)
        done = facts(state_entries=entries, trust=(), notes=(), setups=())
        self.assertIn(f"{STATE}/agent-changes.json", targets(up.build_plan(done, Choices())))

    def test_the_setup_lock_is_bosuns_own(self) -> None:
        self.assertIn(("agent-setup.lock", ft.WORKING), ft.STATE_FILES)


class WorktreeFolders(unittest.TestCase):
    """The worktrees folders bosun made beside a window's repository: removed while empty, never otherwise."""

    EMPTY = f"{HOME}/Development/exporter.worktrees"
    FULL = f"{HOME}/Development/billing.worktrees"

    def plan(self, *folders: tuple[str, int], **changes: object) -> up.Plan:
        return up.build_plan(facts(worktree_folders=folders, **changes), Choices())

    def test_an_empty_one_is_removed_with_rmdir_only(self) -> None:
        plan = self.plan((self.EMPTY, 0))
        step = Step(up.WORKTREES, self.EMPTY, by_hand=f"rmdir {self.EMPTY}")
        self.assertIn(step, plan.steps)
        self.assertNotIn(self.EMPTY, stays(plan))

    def test_one_with_anything_in_it_stays_saying_why(self) -> None:
        for held in (1, up.UNREADABLE):
            plan = self.plan((self.FULL, held))
            self.assertEqual(targets(plan, up.WORKTREES), set(), held)
            (stay,) = [stay for stay in plan.stays if stay.path == self.FULL]
            self.assertIn("worktrees", stay.why)
            self.assertIn("unfinished work", stay.why)
            self.assertFalse(stay.record)

    def test_the_plan_names_both(self) -> None:
        both = facts(worktree_folders=((self.EMPTY, 0), (self.FULL, 2)))
        text = up.render_plan(up.build_plan(both, Choices()), both)
        removes, leaves = text.split("It leaves:")
        self.assertIn("the empty worktrees folder bosun made, ~/Development/exporter.worktrees", removes)
        self.assertIn("~/Development/billing.worktrees", leaves)
        self.assertNotIn("billing", removes)

    def test_it_goes_after_the_windows_stop(self) -> None:
        kinds = [step.kind for step in self.plan((self.EMPTY, 0)).steps]
        self.assertLess(kinds.index(up.STOP), kinds.index(up.WORKTREES))
        self.assertLess(kinds.index(up.WORKTREES), kinds.index(up.FILE))

    def test_the_record_stays_while_an_empty_one_is_there_to_remove(self) -> None:
        entries = ("agent-changes.json", "agent-changes.lock", "logs")
        bare = {"state_entries": entries, "trust": (), "notes": ()}
        planned = targets(self.plan((self.EMPTY, 0), **bare))
        self.assertNotIn(f"{STATE}/agent-changes.json", planned)
        self.assertIn(f"{STATE}/agent-changes.lock", planned)
        for left in ((self.FULL, 3),), ():
            self.assertIn(f"{STATE}/agent-changes.json", targets(self.plan(*left, **bare)), left)

    def test_none_no_step(self) -> None:
        self.assertNotIn(up.WORKTREES, [step.kind for step in self.plan().steps])

    def test_the_lock_is_bosuns_own(self) -> None:
        self.assertIn(("agent-changes.lock", ft.WORKING), ft.STATE_FILES)


class CheckoutRules(unittest.TestCase):
    def test_only_what_the_installer_made_with_no_work_in_it(self) -> None:
        for checkout, why in (
            (Checkout(ROOT, managed=False), "not made by the installer"),
            (Checkout(ROOT, managed=True, work="uncommitted changes"), "uncommitted changes"),
            (Checkout(ROOT, managed=True, work="commits not pushed"), "commits not pushed"),
        ):
            plan = up.build_plan(facts(checkout=checkout), Choices())
            self.assertEqual(plan.install, ())
            self.assertIn(why, next(s.why for s in plan.stays if s.path == ROOT))

    def test_never_a_checkout_at_home(self) -> None:
        self.assertEqual(up.build_plan(facts(checkout=Checkout(HOME, managed=True)), Choices()).install, ())


class TheNewLayout(unittest.TestCase):
    def test_the_new_layout_goes_whole(self) -> None:
        """The checkout from before versions, the versions folder and the link: each checked as the
        checkout is, and all of it last, after every other step."""
        plan = up.build_plan(side_by_side(), Choices())
        self.assertEqual(
            plan.install,
            (
                Step(up.CHECKOUT, LEGACY, by_hand=f"rm -rf {LEGACY}"),
                Step(up.CHECKOUT, VERSIONS, by_hand=f"rm -rf {VERSIONS}"),
                Step(up.LINK, ROOT, by_hand=f"rm {ROOT}"),
            ),
        )
        self.assertFalse({LEGACY, VERSIONS, ROOT} & {step.target for step in plan.steps})
        self.assertFalse({LEGACY, VERSIONS, ROOT} & stays(plan))
        text = up.render_plan(plan, side_by_side())
        for line in ("~/.local/share/bosun-versions/legacy-0123456789ab", "~/.local/share/bosun-versions\n"):
            self.assertIn(line, text)
        self.assertTrue(text.index("~/.local/share/bosun-versions\n") < text.index("~/.local/share/bosun\n"))

    def test_work_in_a_version_folder_keeps_it(self) -> None:
        """Whole: the versions folder, the link that leads into it, and the checkout from before versions
        in it, with the version that holds the work named."""
        busy = f"{VERSIONS}/0f1e2d3c4b5a"
        layout = side_by_side(
            versions=(
                Checkout(f"{VERSIONS}/0123456789ab", managed=True),
                Checkout(busy, managed=True, work="uncommitted changes"),
            )
        )
        plan = up.build_plan(layout, Choices())
        self.assertEqual(plan.install, ())
        self.assertIn("uncommitted changes", next(s.why for s in plan.stays if s.path == busy))
        self.assertTrue({VERSIONS, ROOT} <= stays(plan))
        self.assertIn(busy.rsplit("/", 1)[1], next(s.why for s in plan.stays if s.path == VERSIONS))

    def test_a_checkout_from_before_versions_with_work_keeps_the_folder_it_is_in(self) -> None:
        for legacy, why in (
            (Checkout(LEGACY, managed=True, work="files git ignores (.env)"), ".env"),
            (Checkout(LEGACY, managed=False), "not made by the installer"),
        ):
            plan = up.build_plan(side_by_side(legacy=(legacy,)), Choices())
            self.assertEqual(plan.install, ())
            self.assertIn(why, next(s.why for s in plan.stays if s.path == LEGACY))
            self.assertTrue({VERSIONS, ROOT} <= stays(plan))

    def test_a_repository_the_installer_did_not_make_is_yours(self) -> None:
        plan = up.build_plan(side_by_side(checkout=Checkout(VERSIONS, managed=False)), Choices())
        self.assertEqual(plan.install, ())
        self.assertIn("not made by the installer", next(s.why for s in plan.stays if s.path == VERSIONS))

    def test_what_to_run_names_the_link(self) -> None:
        layout = side_by_side()
        stop = next(step for step in up.build_plan(layout, Choices()).steps if step.kind == up.STOP)
        self.assertEqual(stop.by_hand, f"{ROOT}/bin/bosun stop")
        left = (Step(up.WAKE, WAKE_LINE, by_hand=up.CANCEL_BY_HAND),)
        text = up.render_done(up.build_plan(layout, Choices()), left, layout, inside_checkout=False)
        self.assertIn("~/.local/share/bosun/bin/bosun uninstall", text)

    def test_a_link_a_switch_left_behind_goes_with_the_links(self) -> None:
        leftover = f"{HOME}/.local/share/.bosun.link-1a2b3c4d"
        plan = up.build_plan(side_by_side(leftover_links=(leftover,)), Choices())
        self.assertIn(leftover, targets(plan, up.LINK))


class Reading(unittest.TestCase):
    def test_the_plan_says_what_goes_stays_and_is_asked(self) -> None:
        text = up.render_plan(up.build_plan(facts(), Choices()), facts())
        for words in (
            "launchd job com.bosun.morning",
            "sudo pmset repeat cancel",
            "orchestrator, api",
            "~/.local/bin/bosun",
            "~/.local/share/bosun",
            "~/.config/bosun\n      your settings: bosun.env, sessions.yml, setup.sh",
            "It will ask whether to keep:",
            "not touched",
            "/bosun:chores",
            "any work in them stops",
        ):
            self.assertIn(words, text)

    def test_what_was_done_does_not_read_like_a_warning(self) -> None:
        for step in (Step(up.WAKE, WAKE_LINE), Step(up.STOP, "api")):
            self.assertNotIn("(", up.describe(step, HOME))

    def test_with_nothing_to_ask_your_records_just_stay(self) -> None:
        text = up.render_plan(up.build_plan(facts(), Choices()), facts(), asking=False)
        self.assertNotIn("It will ask", text)
        self.assertIn("your ledger", text)

    def test_nothing_to_do_says_so(self) -> None:
        bare = facts(
            jobs=(), repeating=(), windows=(), link="", trust=(), notes=(), state_entries=(),
            checkout=Checkout(ROOT, managed=False),
        )  # fmt: skip
        self.assertIn("Nothing of bosun is left", up.render_plan(up.build_plan(bare, Choices()), bare))

    def test_the_end_says_where_things_are_and_how_to_come_back(self) -> None:
        after = facts(
            jobs=(),
            repeating=(),
            windows=(),
            link="",
            trust=(),
            notes=(),
            state_entries=("ledger.json",),
            config_entries=("bosun.env", "sessions.yml"),
        )
        text = up.render_done(up.build_plan(after, Choices()), (), after, inside_checkout=False)
        for words in (
            "bosun is uninstalled",
            "~/.config/bosun",
            "your ledger",
            "rm -rf ~/.local/state/bosun ~/.config/bosun",
            up.INSTALL_LINE,
        ):
            self.assertIn(words, text)

    def test_what_could_not_be_removed_comes_with_what_to_run(self) -> None:
        left = (Step(up.WAKE, WAKE_LINE, by_hand=up.CANCEL_BY_HAND),)
        text = up.render_done(up.build_plan(facts(), Choices()), left, facts(), inside_checkout=False)
        self.assertNotIn("bosun is uninstalled", text)
        self.assertIn(up.CANCEL_WAKE, text)
        self.assertIn(
            f"{up.CANCEL_WAKE}  # it removes any other repeating event too", text
        )  # still a line to paste
        self.assertIn("~/.local/share/bosun/bin/bosun uninstall", text)

    def test_a_shell_left_in_a_removed_folder_is_told(self) -> None:
        text = up.render_done(up.build_plan(facts(), Choices()), (), facts(), inside_checkout=True)
        self.assertIn("cd somewhere else", text)

    def test_no_line_to_delete_a_folder_that_holds_more_than_bosun_s(self) -> None:
        shared = facts(state_dir=f"{HOME}/Documents", state_entries=("ledger.json", "taxes.pdf"))
        text = up.render_done(up.build_plan(shared, Choices()), (), shared, inside_checkout=False)
        self.assertNotIn("rm -rf ~/Documents", text)
        at_home = facts(state_dir=HOME, log_dir=f"{HOME}/logs", state_entries=("ledger.json",))
        text = up.render_done(up.build_plan(at_home, Choices()), (), at_home, inside_checkout=False)
        self.assertNotIn("rm -rf ~ ", text + " ")
        self.assertNotRegex(text, r"rm -rf ~$")

    def test_paths_are_quoted_for_the_shell(self) -> None:
        odd = f"{HOME}/Library/Application Support/bosun"
        spaced = facts(config_dir=odd, config_entries=("bosun.env",))
        text = up.render_done(up.build_plan(spaced, Choices()), (), spaced, inside_checkout=False)
        self.assertIn("rm -rf ~/'Library/Application Support/bosun'", text)
        step = next(
            s for s in up.build_plan(spaced, Choices(purge=True)).steps if s.target.endswith("bosun.env")
        )
        self.assertEqual(step.by_hand, f"rm -rf '{odd}/bosun.env'")

    def test_the_install_line_is_the_one_get_sh_gives(self) -> None:
        get_sh = Path(__file__).resolve().parents[2] / "get.sh"
        self.assertIn(up.INSTALL_LINE, get_sh.read_text())


class StillRunning(unittest.TestCase):
    """What ran in the windows bosun stops, each with the window it came from, and what of it still runs
    after: what was there at the stop, and what it starts apart from the windows (a stopped agent's async
    hook, which writes a signal after the windows are gone), never a helper it starts in its window's own
    session as it ends."""

    # (pid, ppid, session): pane 100 (window api) ran an agent (110) that started a hook (120), which
    # started its python (130), and a background command in a session of its own (140); pane 300 (window
    # web) runs a shell (310); 200 is another tool's, in a session of its own; 1 is launchd.
    TABLE = (
        (1, 0, 1),
        (100, 1, 100),
        (110, 100, 100),
        (120, 110, 100),
        (130, 120, 100),
        (140, 110, 140),
        (200, 1, 200),
        (300, 1, 300),
        (310, 300, 300),
    )
    PANES: ClassVar[dict[int, str]] = {100: "api", 300: "web"}
    SESSIONS: ClassVar[dict[int, str]] = {100: "api", 300: "web"}

    def test_what_ran_in_the_windows_their_sessions_and_all_the_panes_started(self) -> None:
        self.assertEqual(
            up.ran_in_windows(self.TABLE, self.SESSIONS, self.PANES),
            {100: "api", 110: "api", 120: "api", 130: "api", 140: "api", 300: "web", 310: "web"},
        )

    def test_a_process_in_a_panes_session_counts_without_its_pane(self) -> None:
        table = ((1, 0, 1), (110, 1, 100))
        self.assertEqual(up.ran_in_windows(table, self.SESSIONS, self.PANES), {110: "api"})

    def test_what_was_there_at_the_stop_and_still_runs(self) -> None:
        watched = up.ran_in_windows(self.TABLE, self.SESSIONS, self.PANES)
        table = ((1, 0, 1), (110, 1, 100), (140, 110, 140), (200, 1, 200))
        self.assertEqual(up.still_running(table, self.SESSIONS, watched), {110: "api", 140: "api"})

    def test_a_hook_an_agent_starts_apart_as_it_ends_counts_with_what_it_starts(self) -> None:
        # The agent (110) starts a hook in a session of its own (150, as Claude Code starts its async
        # hooks), which starts its python (151): both of the agent's window.
        table = ((1, 0, 1), (110, 1, 100), (150, 110, 150), (151, 150, 150))
        self.assertEqual(
            up.still_running(table, self.SESSIONS, {100: "api", 110: "api"}),
            {110: "api", 150: "api", 151: "api"},
        )

    def test_a_hook_left_by_an_agent_that_exited_still_counts(self) -> None:
        # The agent is gone, and its hook, reparented to launchd, was seen before: still waited for, and
        # so is what it starts.
        table = ((1, 0, 1), (150, 1, 150), (151, 150, 150), (200, 1, 200))
        self.assertEqual(
            up.still_running(table, self.SESSIONS, {110: "api", 150: "api"}), {150: "api", 151: "api"}
        )

    def test_a_helper_an_agent_starts_in_its_windows_session_as_it_ends_does_not(self) -> None:
        # Claude Code's `caffeinate -i -t 300`, started as it ends, outlives it by five minutes.
        table = ((1, 0, 1), (110, 1, 100), (160, 110, 100))
        self.assertEqual(up.still_running(table, self.SESSIONS, {110: "api"}), {110: "api"})
        self.assertEqual(up.still_running(((1, 0, 1), (160, 1, 100)), self.SESSIONS, {110: "api"}), {})

    def test_nothing_once_they_are_gone(self) -> None:
        table = ((1, 0, 1), (200, 1, 200))
        self.assertEqual(up.still_running(table, self.SESSIONS, {110: "api", 120: "api"}), {})

    def test_a_session_unknown_counts_for_nothing_it_did_not_start(self) -> None:
        table = ((1, 0, 1), (300, 1, None))
        self.assertEqual(up.ran_in_windows(table, {100: "api"}, {100: "api"}), {})
        self.assertEqual(up.still_running(table, {100: "api"}, {110: "api"}), {})

    def test_what_still_runs_is_said_with_its_window(self) -> None:
        self.assertEqual(
            up.still_running_note("exporter", 16856, "sleep 300"),
            "still running, started in the exporter window: sleep 300 (pid 16856); bosun did not stop it.",
        )

    def test_a_long_command_is_cut_short_in_the_note(self) -> None:
        note = up.still_running_note("orchestrator", 8211, "claude --settings " + "x" * 300)
        self.assertIn("claude --settings xxx", note)
        self.assertLess(len(note), 200)


if __name__ == "__main__":
    unittest.main()
