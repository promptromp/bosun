"""lib/init_wizard.py: the wizard's questions, answered by a list of keys, over fictional facts."""

from __future__ import annotations

import re
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import init_plan
import init_wizard
from init_plan import Answers, DayUse, DirState, Facts, Repo, RepoUse
from init_wizard import ENTER, Machine


HOME = "/Users/me"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
TEN_DAYS = tuple(DayUse(f"2026-10-{day:02d}", 9 * 60 + 10, 18 * 60 + 40) for day in range(5, 10))
CONFIG_DIR = f"{HOME}/.config/bosun"


class FakeUi:
    """Keys and lines from lists, and the end of the input when they run out."""

    def __init__(self, keys: str | list[str] = "", lines: tuple[str, ...] = ()) -> None:
        self.keys = list(keys)
        self.lines = list(lines)
        self.screens: list[str] = []
        self.said: list[str] = []
        self.asked: list[str] = []

    def show(self, text: str) -> None:
        self.screens.append(ANSI.sub("", text))

    def say(self, text: str) -> None:
        self.said.append(ANSI.sub("", text))

    def key(self) -> str:
        return self.keys.pop(0) if self.keys else ""

    def line(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self.lines.pop(0) if self.lines else ""


def facts(**changes: Any) -> Facts:
    base = Facts(
        home=HOME,
        today="2026-11-02",
        claude_logged_in=True,
        claude_plan="max",
        gh_logged_in=True,
        repos=(
            RepoUse(f"{HOME}/d/api", 400, "acme"),
            RepoUse(f"{HOME}/d/web", 50, "acme"),
            RepoUse(f"{HOME}/d/toy", 3, "me"),
        ),
    )
    return replace(base, **changes)


def typed_folder(typed: str) -> RepoUse | None:
    return RepoUse(f"{HOME}/{typed}", 0, "typed-owner") if typed.startswith("real") else None


def machine(the_facts: Facts, missing: tuple[str, ...] = ()) -> Machine:
    def plan(answers: Answers) -> init_plan.Plan:
        return init_plan.build_plan(the_facts, answers, DirState(missing=missing), force=True)

    return Machine(plan, typed_folder)


def run(
    keys: str | list[str], the_facts: Facts | None = None, **kwargs: Any
) -> tuple[Answers | None, FakeUi]:
    the_facts = the_facts or facts()
    missing = kwargs.pop("missing", ())
    ui = FakeUi(keys, **kwargs)
    return init_wizard.run(
        ui, the_facts, init_plan.recommend(the_facts), machine(the_facts, missing), CONFIG_DIR
    ), ui


ALL_RECOMMENDED = [ENTER] * 6 + ["y"]


class FlowTest(unittest.TestCase):
    def test_return_all_the_way_takes_what_is_recommended(self) -> None:
        the_facts = facts()
        answers, ui = run(ALL_RECOMMENDED, the_facts)
        self.assertEqual(answers, init_plan.recommend(the_facts))
        self.assertIn("Nothing has changed yet", ui.screens[-1])

    def test_each_question_gets_a_screen_and_says_which(self) -> None:
        _, ui = run(ALL_RECOMMENDED)
        self.assertEqual(len(ui.screens), 7)
        self.assertIn("step 1 of 6", ui.screens[0])
        self.assertIn("step 6 of 6", ui.screens[5])

    def test_q_anywhere_changes_nothing(self) -> None:
        for at in range(7):
            answers, ui = run([ENTER] * at + ["q"])
            self.assertIsNone(answers)
            self.assertEqual(len(ui.screens), at + 1)

    def test_the_end_of_the_input_is_like_q(self) -> None:
        self.assertIsNone(run([ENTER])[0])

    def test_r_starts_again_and_forgets_the_answers(self) -> None:
        answers, ui = run(["2", ENTER, "r", *ALL_RECOMMENDED])
        assert answers is not None
        self.assertEqual([r.name for r in answers.repos], ["api", "web"])
        self.assertEqual(sum("step 1 of 6" in screen for screen in ui.screens), 3)

    def test_r_on_the_plan_starts_again(self) -> None:
        _, ui = run([*[ENTER] * 6, "r", "q"])
        self.assertIn("step 1 of 6", ui.screens[-1])

    def test_keys_that_mean_nothing_are_ignored(self) -> None:
        answers, ui = run(["z", "!", *ALL_RECOMMENDED])
        self.assertIsNotNone(answers)
        self.assertEqual(len(ui.screens), 7)

    def test_every_screen_offers_a_way_out(self) -> None:
        _, ui = run(ALL_RECOMMENDED)
        for screen in ui.screens:
            self.assertIn("(q) quit and change nothing", screen)
            self.assertIn("(r) restart", screen)

    def test_recommended_is_marked(self) -> None:
        _, ui = run(ALL_RECOMMENDED)
        for screen in ui.screens[1:6]:
            self.assertIn("(recommended)", screen)

    def test_lines_fit_a_normal_terminal(self) -> None:
        _, ui = run(ALL_RECOMMENDED, facts(days=TEN_DAYS, has_battery=True))
        for screen in ui.screens[1:]:
            for line in screen.splitlines():
                if not line.lstrip().startswith(
                    ("(enter)", "(y)", "(r)", "./install.sh", "sudo", "bosun trust")
                ):
                    self.assertLessEqual(len(line), 90, line)

    def test_a_blocked_plan_cannot_be_accepted(self) -> None:
        answers, ui = run([*[ENTER] * 6, "y", "y", "q"], missing=("~/gone",))
        self.assertIsNone(answers)
        self.assertIn("No such folder: ~/gone", ui.screens[-1])
        self.assertNotIn("(y) do it", ui.screens[-1])


class ReposTest(unittest.TestCase):
    def test_what_is_recommended_is_on_and_the_rest_listed(self) -> None:
        _, ui = run("q")
        self.assertIn("[x] (1)  ~/d/api", ui.screens[0])
        self.assertIn("400 prompts", ui.screens[0])
        self.assertIn("[x] (2)  ~/d/web", ui.screens[0])
        self.assertIn("[ ] (3)  ~/d/toy", ui.screens[0])

    def test_a_digit_switches_a_repo(self) -> None:
        answers, ui = run(["2", "3", *ALL_RECOMMENDED])
        assert answers is not None
        self.assertEqual([r.name for r in answers.repos], ["api", "toy"])
        self.assertIn("[ ] (2)", ui.screens[1])
        self.assertIn("[x] (3)", ui.screens[2])

    def test_a_digit_that_is_not_listed_means_nothing(self) -> None:
        answers, _ = run(["7", *ALL_RECOMMENDED])
        assert answers is not None
        self.assertEqual(len(answers.repos), 2)

    def test_the_mask_and_the_folder_follow_the_choice(self) -> None:
        answers, _ = run(["3", *ALL_RECOMMENDED])
        assert answers is not None
        self.assertEqual(answers.repo_mask, "acme me")
        self.assertEqual(answers.repos_dir, "~/d")

    def test_all_off_is_allowed(self) -> None:
        answers, _ = run(["1", "2", *ALL_RECOMMENDED])
        assert answers is not None
        self.assertEqual(answers.repos, ())

    def test_add_a_folder(self) -> None:
        answers, ui = run(["a", *ALL_RECOMMENDED], lines=("real/new",))
        assert answers is not None
        self.assertEqual(ui.asked, ["Folder: "])
        self.assertIn("new", [r.name for r in answers.repos])
        self.assertIn("typed-owner", answers.repo_mask)
        self.assertIn("[x] (4)  ~/real/new", ui.screens[1])

    def test_a_folder_that_is_not_one_is_not_added(self) -> None:
        answers, _ = run(["a", *ALL_RECOMMENDED], lines=("nope",))
        assert answers is not None
        self.assertEqual(len(answers.repos), 2)

    def test_at_most_nine_are_listed(self) -> None:
        many = tuple(RepoUse(f"{HOME}/d/r{n}", 100 - n) for n in range(15))
        _, ui = run("q", facts(repos=many))
        self.assertIn("(9)", ui.screens[0])
        self.assertNotIn("(10)", ui.screens[0])

    def test_nothing_found_says_what_to_do(self) -> None:
        _, ui = run("q", facts(repos=()))
        self.assertIn("None found", ui.screens[0])

    def test_a_rerun_keeps_a_windows_options_and_its_scope(self) -> None:
        listed = ({"name": "api", "dir": f"{HOME}/d/api", "model": "haiku"},)
        env = {"BOSUN_REPO_MASK": "my-org", "BOSUN_REPOS_DIR": "$HOME/elsewhere", "BOSUN_DAYS": "MTWRF"}
        the_facts = facts(files={"sessions.yml": "...", "bosun.env": "..."}, sessions=listed, env=env)
        answers, _ = run(ALL_RECOMMENDED, the_facts)
        assert answers is not None
        self.assertEqual(answers.repos, (Repo("api", "~/d/api", {"model": "haiku"}),))
        self.assertEqual((answers.repo_mask, answers.repos_dir), ("my-org", "~/elsewhere"))


class ScheduleTest(unittest.TestCase):
    def test_a_day_is_drawn_on_its_ruler(self) -> None:
        ruler, bar = init_wizard.day_bar("06:00", "18:00")
        self.assertEqual(len(bar), init_wizard.CHUNKS_PER_DAY)
        self.assertEqual(bar, "·" * 12 + "█" * 24 + "·" * 12)
        self.assertTrue(ruler.startswith("00"))

    def test_past_midnight_wraps_around(self) -> None:
        bar = init_wizard.day_bar("22:00", "02:00")[1]
        self.assertEqual(bar, "█" * 4 + "·" * 40 + "█" * 4)

    def test_your_history_is_quoted(self) -> None:
        _, ui = run([ENTER, "q"], facts(days=TEN_DAYS))
        self.assertIn("Your usual first prompt is 09:10", ui.screens[1])

    def test_the_options_do_not_repeat(self) -> None:
        options = init_wizard.schedule_options(Answers())
        self.assertEqual([o.label for o in options], ["Mon-Fri, 08:00 to 20:00", "Something else"])

    def test_the_same_hours_on_weekdays_and_the_default_are_offered(self) -> None:
        options = init_wizard.schedule_options(Answers(days="MTWRFSU", start="09:00", until="20:30"))
        self.assertEqual(
            [o.label for o in options],
            [
                "Mon-Sun, 09:00 to 20:30",
                "Mon-Fri, 09:00 to 20:30",
                "Mon-Fri, 08:00 to 20:00",
                "Something else",
            ],
        )
        self.assertTrue(options[0].recommended)

    def test_picking_one(self) -> None:
        answers, _ = run([ENTER, "1", ENTER, ENTER, ENTER, ENTER, "y"])
        assert answers is not None
        self.assertEqual((answers.days, answers.start, answers.until), ("MTWRF", "08:00", "20:00"))

    def test_typing_your_own(self) -> None:
        answers, _ = run([ENTER, "2", ENTER, ENTER, ENTER, ENTER, "y"], lines=("MWF", "07:15", "16:45"))
        assert answers is not None
        self.assertEqual((answers.days, answers.start, answers.until), ("MWF", "07:15", "16:45"))

    def test_return_keeps_each_part(self) -> None:
        answers, _ = run([ENTER, "2", ENTER, ENTER, ENTER, ENTER, "y"], lines=("", "07:15", ""))
        assert answers is not None
        self.assertEqual((answers.days, answers.start, answers.until), ("MTWRF", "07:15", "20:00"))

    def test_what_makes_no_sense_is_asked_again(self) -> None:
        answers, ui = run(
            [ENTER, "2", ENTER, ENTER, ENTER, ENTER, "y"], lines=("XX", "25:00", "", "MWF", "07:00", "16:00")
        )
        assert answers is not None
        self.assertEqual(answers.days, "MWF")
        self.assertTrue(any("must use each of" in screen and "HH:MM" in screen for screen in ui.screens))


class OtherQuestionsTest(unittest.TestCase):
    def test_without_the_slack_plugin_the_watcher_is_not_offered(self) -> None:
        _, ui = run([ENTER, ENTER, "q"])
        self.assertIn("not offered", ui.screens[2])
        self.assertNotIn("with the Slack watcher", ui.screens[2])

    def test_with_it_the_watcher_is_recommended(self) -> None:
        answers, ui = run(ALL_RECOMMENDED, facts(slack_plugin=True))
        assert answers is not None
        self.assertTrue(answers.slack_watch)
        self.assertIn("Proactive, with the Slack watcher (recommended)", ui.screens[2])

    def test_proactive_without_the_watcher_can_be_picked(self) -> None:
        answers, _ = run([ENTER, ENTER, "2", ENTER, ENTER, ENTER, "y"], facts(slack_plugin=True))
        assert answers is not None
        self.assertTrue(answers.events and answers.learning)
        self.assertFalse(answers.slack_watch)

    def test_little_else_turns_it_all_off(self) -> None:
        answers, _ = run([ENTER, ENTER, "2", ENTER, ENTER, ENTER, "y"])
        assert answers is not None
        self.assertFalse(answers.events or answers.approvals or answers.learning or answers.slack_watch)

    def test_the_model_follows_the_plan_and_can_be_changed(self) -> None:
        _, ui = run([ENTER, ENTER, ENTER, "q"], facts(claude_plan="pro"))
        self.assertIn("sonnet (recommended)", ui.screens[3])
        answers, _ = run([ENTER, ENTER, ENTER, "2", ENTER, ENTER, "y"])
        assert answers is not None
        self.assertEqual(answers.model, "sonnet")

    def test_a_model_of_your_own_stays_on_offer(self) -> None:
        options = init_wizard.model_options(Answers(model="haiku"))
        self.assertEqual(options[-1].label, "haiku, as now")
        self.assertTrue(options[-1].recommended)

    def test_codex_is_only_offered_when_it_is_ready(self) -> None:
        _, ui = run(ALL_RECOMMENDED)
        self.assertFalse(any("Which agent" in screen for screen in ui.screens))

    def test_choosing_codex_asks_nothing_about_claude_s_permissions(self) -> None:
        answers, ui = run(
            [ENTER, ENTER, ENTER, "2", ENTER, ENTER, "y"], facts(ready_agents=("claude", "codex"))
        )
        assert answers is not None
        self.assertEqual(answers.harness, "codex")
        self.assertTrue(any("Which agent" in screen for screen in ui.screens))
        self.assertTrue(any("model should the orchestrator" in screen for screen in ui.screens))
        self.assertFalse(any("handle permissions" in screen for screen in ui.screens))
        self.assertIn("step 6 of 6", ui.screens[5])

    def test_staying_on_claude_code_asks_as_before(self) -> None:
        answers, ui = run(
            [ENTER, ENTER, ENTER, ENTER, ENTER, ENTER, ENTER, "y"], facts(ready_agents=("claude", "codex"))
        )
        assert answers is not None
        self.assertEqual(answers.harness, "claude")
        self.assertTrue(any("handle permissions" in screen for screen in ui.screens))
        self.assertIn("step 7 of 7", ui.screens[6])

    def test_auto_mode_is_recommended_and_says_what_it_does(self) -> None:
        answers, ui = run(ALL_RECOMMENDED)
        assert answers is not None
        self.assertEqual(answers.permissions, "auto")
        self.assertIn("Auto mode (recommended)", ui.screens[4])
        self.assertIn("blocks risky ones", " ".join(ui.screens[4].split()))
        self.assertIn("slightly more tokens", " ".join(ui.screens[4].split()))

    def test_skipping_permission_checks_is_a_choice_that_says_what_it_costs(self) -> None:
        answers, ui = run([ENTER, ENTER, ENTER, ENTER, "2", ENTER, "y"])
        assert answers is not None
        self.assertEqual(answers.permissions, "bypass")
        self.assertIn("no protection against prompt injection", " ".join(ui.screens[4].split()))
        self.assertIn("accept its warning once", " ".join(ui.screens[4].split()))

    def test_the_wake_says_what_it_runs_and_that_it_needs_your_password(self) -> None:
        _, ui = run([*[ENTER] * 5, "q"], facts(has_battery=True))
        self.assertIn("sudo pmset repeat wakeorpoweron MTWRF 08:01:00", ui.screens[5])
        self.assertIn("password once", ui.screens[5])
        self.assertIn("charger", ui.screens[5])

    def test_someone_elses_wake_is_kept_unless_you_say_otherwise(self) -> None:
        the_facts = facts(repeating_wake="wake at 6:00AM")
        answers, ui = run([*[ENTER] * 5, ENTER, "y"], the_facts)
        assert answers is not None
        self.assertFalse(answers.wake)
        self.assertIn("this replaces: wake at 6:00AM", ui.screens[5])
        answers, _ = run([*[ENTER] * 5, "y", "y"], the_facts)
        assert answers is not None
        self.assertTrue(answers.wake)

    def test_aws_is_only_asked_when_there_are_profiles(self) -> None:
        _, ui = run(ALL_RECOMMENDED)
        self.assertFalse(any("AWS" in screen for screen in ui.screens))
        answers, ui = run([*[ENTER] * 6, "2", ENTER, "y"], facts(aws_sso_profiles=("dev", "prod")))
        assert answers is not None
        self.assertEqual(answers.aws_sso_profiles, ("prod",))
        self.assertIn("step 7 of 7", ui.screens[6])


class BeforeAndAfterTest(unittest.TestCase):
    def test_starting_says_what_will_happen_and_keeps_the_screen(self) -> None:
        ui = FakeUi([ENTER])
        self.assertTrue(init_wizard.ask_start(ui))
        self.assertEqual(ui.screens, [])
        self.assertIn("chores", ui.said[0])
        self.assertIn("asks you before anything goes out", " ".join(ui.said[0].split()))

    def test_not_starting_is_fine(self) -> None:
        self.assertFalse(init_wizard.ask_start(FakeUi("n")))
        self.assertFalse(init_wizard.ask_start(FakeUi("q")))
        self.assertFalse(init_wizard.ask_start(FakeUi("")))


if __name__ == "__main__":
    unittest.main()
