"""lib/uninstall_plan.py: what goes and what stays, for every kind of file and answer."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import journal as jr
import uninstall_plan as up
from uninstall_plan import Checkout, Choices, Facts, Job, Step


HOME = "/Users/me"
STATE = f"{HOME}/.local/state/bosun"
CONFIG = f"{HOME}/.config/bosun"
ROOT = f"{HOME}/.local/share/bosun"
WAKE_LINE = "wakepoweron at 9:01AM every day"


def facts(**changes: object) -> Facts:
    base = Facts(
        home=HOME,
        checkout=Checkout(ROOT, managed=True),
        config_dir=CONFIG,
        state_dir=STATE,
        log_dir=f"{STATE}/logs",
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


def targets(plan: up.Plan, kind: str = up.FILE) -> set[str]:
    return {step.target for step in plan.steps if step.kind == kind}


def stays(plan: up.Plan) -> set[str]:
    return {stay.path for stay in plan.stays}


class FileTables(unittest.TestCase):
    def test_what_each_file_is(self) -> None:
        for name, kind in (
            ("ledger.json", up.LEDGER),
            ("ledger.json.bak", up.LEDGER),
            ("ledger.json.tmp", up.WORKING),
            ("learn", up.LEDGER),
            ("journal", up.LEDGER),
            ("replica.json", up.LEDGER),
            (jr.REPLICA_TEMP.format(writer="4242.1a2b3c4d"), up.WORKING),  # replica.json cut short
            ("ledger.lock", up.WORKING),
            ("logs", up.WORKING),
            ("stopped-bosun", up.WORKING),
            ("keep-awake-bosun.pid", up.WORKING),
            ("anything-else", up.UNKNOWN),
        ):
            self.assertEqual(up.kind_of(name, up.STATE_FILES), kind, name)
        for name, kind in (
            ("bosun.env", up.SETTINGS),
            ("bosun.env.pre-init-20261102-080000", up.SETTINGS),
            ("bosun.env.pre-update-20261106-093005", up.SETTINGS),
            (".bosun.env.update.tmp", up.WORKING),  # bosun update's bosun.env, cut short
            (
                jr.WRITING.format(name="bosun.env.pre-update-20261106-093005", writer="4242.1a2b3c4d"),
                up.WORKING,
            ),
            ("tasks", up.SETTINGS),
            ("knowledge", up.KNOWLEDGE),
            ("forbidden-terms.txt", up.OURS_TO_LEAVE),
            ("my-notes.txt", up.UNKNOWN),
        ):
            self.assertEqual(up.kind_of(name, up.CONFIG_FILES), kind, name)

    def test_no_table_lists_a_name_twice(self) -> None:
        for table in (up.STATE_FILES, up.CONFIG_FILES, up.LOG_FILES):
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
        for name in ("morning-2026-11-02.log", "watchdog.launchd.err", "hook-2026-11-02.log.1"):
            self.assertEqual(up.kind_of(name, up.LOG_FILES), up.WORKING, name)
        for name in ("Zoom.log", "vpn-client.log"):
            self.assertEqual(up.kind_of(name, up.LOG_FILES), up.UNKNOWN, name)

    def test_an_unsafe_folder_is_left_whole_and_named(self) -> None:
        plan = up.build_plan(facts(state_dir=HOME, log_dir=f"{HOME}/logs"), Choices(keep_ledger=False))
        self.assertFalse([t for t in targets(plan) if t.startswith(f"{HOME}/l")])
        self.assertIn(HOME, stays(plan))
        self.assertIsNotNone(next(s for s in plan.stays if "holds your home" in s.why))


class Wake(unittest.TestCase):
    def test_bosuns_alone_is_cancelled(self) -> None:
        plan = up.build_plan(facts(), Choices())
        self.assertEqual(plan.steps[0], Step(up.WAKE, WAKE_LINE, by_hand=up.CANCEL_WAKE))

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
        self.assertEqual(plan.checkout, Step(up.CHECKOUT, ROOT, by_hand=f"rm -rf {ROOT}"))

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
            self.assertIn(up.KNOWLEDGE, [q.key for q in up.questions(something, Choices())], records)
            self.assertNotIn(f"{CONFIG}/knowledge", targets(up.build_plan(something, Choices())))

    def test_a_hand_made_copy_of_the_ledger_is_not_a_working_file(self) -> None:
        self.assertEqual(up.kind_of("ledger.json.before-my-edit", up.STATE_FILES), up.UNKNOWN)

    def test_learning_history_alone_is_worth_asking_about(self) -> None:
        learned = facts(ledger_items=0, state_entries=("ledger.json", "learn"))
        self.assertEqual([q.key for q in up.questions(learned, Choices())], [up.LEDGER, up.KNOWLEDGE])

    def test_a_ledger_it_cannot_read_may_hold_anything(self) -> None:
        unreadable = facts(ledger_items=up.UNREADABLE)
        self.assertNotIn(f"{STATE}/ledger.json", targets(up.build_plan(unreadable, Choices())))
        self.assertIn(up.LEDGER, [q.key for q in up.questions(unreadable, Choices())])

    def test_questions_only_for_what_flags_left_open(self) -> None:
        self.assertEqual([q.key for q in up.questions(facts(), Choices(keep_ledger=False))], [up.KNOWLEDGE])
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


class CheckoutRules(unittest.TestCase):
    def test_only_what_the_installer_made_with_no_work_in_it(self) -> None:
        for checkout, why in (
            (Checkout(ROOT, managed=False), "not made by the installer"),
            (Checkout(ROOT, managed=True, work="uncommitted changes"), "uncommitted changes"),
            (Checkout(ROOT, managed=True, work="commits not pushed"), "commits not pushed"),
        ):
            plan = up.build_plan(facts(checkout=checkout), Choices())
            self.assertIsNone(plan.checkout)
            self.assertIn(why, next(s.why for s in plan.stays if s.path == ROOT))

    def test_never_a_checkout_at_home(self) -> None:
        self.assertIsNone(up.build_plan(facts(checkout=Checkout(HOME, managed=True)), Choices()).checkout)


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
        left = (Step(up.WAKE, WAKE_LINE, by_hand=up.CANCEL_WAKE),)
        text = up.render_done(up.build_plan(facts(), Choices()), left, facts(), inside_checkout=False)
        self.assertNotIn("bosun is uninstalled", text)
        self.assertIn(up.CANCEL_WAKE, text)
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


if __name__ == "__main__":
    unittest.main()
