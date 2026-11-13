"""lib/init_plan.py: what `bosun init` recommends and what it would change, over fictional facts."""

from __future__ import annotations

import contextlib
import io
import posixpath
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import codex_state
import init_plan
from init_plan import Answers, AnswersError, DayUse, DirState, Facts, Repo, RepoUse


ROOT = Path(__file__).resolve().parents[2]
HOME = "/Users/me"
TODAY = "2026-11-02"  # a Monday


def facts(**changes: Any) -> Facts:
    base = Facts(home=HOME, today=TODAY, claude_logged_in=True, claude_plan="max", gh_logged_in=True)
    return replace(base, **changes)


def weekdays(first: int, last: int) -> tuple[DayUse, ...]:
    """Four weeks of Monday to Friday."""
    days = [date(2026, 10, day) for day in range(5, 31)]
    return tuple(DayUse(d.isoformat(), first, last) for d in days if d.weekday() < 5)


def use(name: str, prompts: int, owner: str = "acme") -> RepoUse:
    return RepoUse(f"{HOME}/Development/{name}", prompts, owner)


class ScheduleTest(unittest.TestCase):
    def test_a_day_runs_from_its_first_to_its_last_prompt(self) -> None:
        times = [datetime(2026, 10, 5, 9, 41), datetime(2026, 10, 5, 12, 0), datetime(2026, 10, 5, 18, 5)]
        self.assertEqual(init_plan.working_days(times), (DayUse("2026-10-05", 9 * 60 + 41, 18 * 60 + 5),))

    def test_work_past_midnight_belongs_to_the_day_it_started(self) -> None:
        times = [datetime(2026, 10, 5, 22, 0), datetime(2026, 10, 6, 1, 30), datetime(2026, 10, 6, 9, 0)]
        self.assertEqual(
            init_plan.working_days(times),
            (DayUse("2026-10-05", 22 * 60, 25 * 60 + 30), DayUse("2026-10-06", 9 * 60, 9 * 60)),
        )

    def test_hours_are_ready_before_you_start_and_awake_after_you_stop(self) -> None:
        self.assertEqual(init_plan.recommended_hours(weekdays(9 * 60 + 41, 21 * 60 + 27)), ("09:00", "21:30"))

    def test_an_exact_half_hour_stays_where_it_is(self) -> None:
        self.assertEqual(init_plan.recommended_hours(weekdays(9 * 60, 18 * 60)), ("08:30", "18:00"))

    def test_hours_stay_in_a_sensible_range(self) -> None:
        self.assertEqual(init_plan.recommended_hours(weekdays(2 * 60, 3 * 60))[0], "05:00")
        self.assertEqual(init_plan.recommended_hours(weekdays(14 * 60, 15 * 60))[0], "11:00")
        self.assertEqual(
            init_plan.recommended_hours(weekdays(9 * 60, 9 * 60 + 30))[1], "12:30"
        )  # at least 4 hours

    def test_a_night_owl_is_awake_past_midnight(self) -> None:
        self.assertEqual(init_plan.recommended_hours(weekdays(10 * 60, 25 * 60)), ("09:30", "01:00"))

    def test_weekdays_you_worked_on_most_of(self) -> None:
        self.assertEqual(init_plan.active_weekdays(weekdays(540, 1080), date(2026, 10, 31)), "MTWRF")

    def test_weekdays_count_from_your_first_day(self) -> None:
        recent = (
            DayUse("2026-10-26", 540, 600),
            DayUse("2026-10-27", 540, 600),
            DayUse("2026-10-28", 540, 600),
        )
        self.assertEqual(init_plan.active_weekdays(recent, date(2026, 10, 28)), "MTW")

    def test_little_history_keeps_the_default_schedule(self) -> None:
        few = (DayUse("2026-10-26", 60, 120),)
        self.assertEqual(init_plan.recommended_schedule(facts(days=few)), ("MTWRF", "08:00", "20:00"))

    def test_your_config_beats_your_history(self) -> None:
        env = {"BOSUN_DAYS": "MWF", "BOSUN_START_TIME": "07:00", "BOSUN_AWAKE_UNTIL": "19:00"}
        schedule = init_plan.recommended_schedule(facts(days=weekdays(600, 1200), env=env))
        self.assertEqual(schedule, ("MWF", "07:00", "19:00"))


class RecommendTest(unittest.TestCase):
    def test_a_new_mac_gets_events_approvals_and_learning(self) -> None:
        answers = init_plan.recommend(facts())
        self.assertTrue(answers.events and answers.approvals and answers.learning)
        self.assertFalse(answers.slack_watch)

    def test_the_slack_watcher_only_with_the_slack_plugin(self) -> None:
        self.assertTrue(init_plan.recommend(facts(slack_plugin=True)).slack_watch)

    def test_your_config_beats_the_recommendation(self) -> None:
        env = {"BOSUN_LEARN_ENABLED": "0", "BOSUN_EVENTS_ENABLED": "0", "BOSUN_MODEL": "haiku"}
        answers = init_plan.recommend(facts(env=env, slack_plugin=True))
        self.assertFalse(answers.learning or answers.events)
        self.assertEqual(answers.model, "haiku")

    def test_first_run_ticks_the_most_used_repo_only(self) -> None:
        answers = init_plan.recommend(facts(repos=(use("web", 30), use("api", 400), use("toy", 3))))
        self.assertEqual(answers.repos, (Repo("api", "~/Development/api"),))

    def test_the_busy_ones_are_ranked_most_used_first_at_most_six(self) -> None:
        ranked = init_plan.recommended_uses((use("web", 30), use("api", 400), use("toy", 3)))
        self.assertEqual([posixpath.basename(u.dir) for u in ranked], ["api", "web"])
        many = tuple(use(f"r{n}", 100 - n) for n in range(9))
        self.assertEqual(len(init_plan.recommended_uses(many)), init_plan.MAX_REPOS)

    def test_without_history_the_newest_found_repo(self) -> None:
        found = tuple(use(name, 0) for name in ("new", "older", "old", "oldest"))
        self.assertEqual([r.name for r in init_plan.recommend(facts(repos=found)).repos], ["new"])
        self.assertEqual(len(init_plan.recommended_uses(found)), init_plan.MAX_UNUSED_REPOS)

    def test_nothing_found_ticks_nothing(self) -> None:
        self.assertEqual(init_plan.recommend(facts()).repos, ())

    def test_first_run_ticks_no_connectors(self) -> None:
        found = ("claude.ai Acme Mail", "plugin:acme-tracker:tracker", "acme-local")
        self.assertEqual(init_plan.recommend(facts(connectors=found)).orchestrator_connectors, "")

    def test_rerun_keeps_the_configs_connectors(self) -> None:
        for value in ("claude.ai Acme Mail, plugin:acme-tracker:tracker", "all", ""):
            with self.subTest(value=value):
                env = {"BOSUN_DAYS": "MTWRF", "BOSUN_ORCHESTRATOR_CONNECTORS": value}
                answers = init_plan.recommend(facts(env=env, connectors=("acme-local",)))
                self.assertEqual(answers.orchestrator_connectors, value)

    def test_rerun_on_a_config_from_before_the_key_keeps_every_connector(self) -> None:
        """bosun.env from before BOSUN_ORCHESTRATOR_CONNECTORS: that orchestrator had every connector, so
        the recommendation is `all`, what bosun update writes, and the plan says why. A first run (no
        bosun.env) still recommends none."""
        older = facts(
            env={"BOSUN_DAYS": "MTWRF"}, files={"bosun.env": "BOSUN_DAYS=MTWRF\n"}, connectors=("acme-local",)
        )
        rerun = init_plan.recommend(older)
        self.assertEqual(rerun.orchestrator_connectors, "all")
        notes = " ".join(init_plan.build_plan(older, rerun, DirState()).notes)
        self.assertIn("bosun.env does not set BOSUN_ORCHESTRATOR_CONNECTORS", notes)
        self.assertIn("keeps all, as bosun update does", notes)
        chosen = replace(rerun, orchestrator_connectors="acme-local")
        self.assertNotIn(
            "BOSUN_ORCHESTRATOR_CONNECTORS", " ".join(init_plan.build_plan(older, chosen, DirState()).notes)
        )
        first = facts(connectors=("acme-local",))
        self.assertEqual(init_plan.recommend(first).orchestrator_connectors, "")
        self.assertNotIn(
            "BOSUN_ORCHESTRATOR_CONNECTORS",
            " ".join(init_plan.build_plan(first, init_plan.recommend(first), DirState()).notes),
        )

    def test_window_names_are_usable_and_unique(self) -> None:
        taken: set[str] = set()
        self.assertEqual(init_plan.window_name("/a/my.repo name", taken), "my-repo-name")
        self.assertEqual(init_plan.window_name("/b/my.repo name", taken), "my-repo-name-2")
        self.assertEqual(init_plan.window_name("/...", taken), "repo")

    def test_a_repo_cannot_take_a_window_bosun_adds(self) -> None:
        answers = init_plan.recommend(facts(repos=(use("orchestrator", 50),)))
        self.assertEqual(answers.repos[0].name, "orchestrator-2")

    def test_the_orchestrator_runs_in_the_folder_the_repos_share(self) -> None:
        self.assertEqual(
            init_plan.recommend(facts(repos=(use("api", 50), use("web", 50)))).repos_dir, "~/Development"
        )

    def test_never_the_home_folder(self) -> None:
        spread = (RepoUse(f"{HOME}/work/api", 90), RepoUse(f"{HOME}/play/toy", 50))
        self.assertEqual(init_plan.recommend(facts(repos=spread)).repos_dir, "~/work")

    def test_the_mask_is_the_owners_of_the_repos(self) -> None:
        repos = (
            use("api", 90, "acme"),
            use("web", 50, "acme"),
            use("toy", 30, "my-org"),
            use("mine", 30, ""),
        )
        self.assertEqual(init_plan.recommend(facts(repos=repos)).repo_mask, "acme")  # the ticked repo's

    def test_with_nothing_ticked_the_scope_is_the_repos_you_use(self) -> None:
        repos = (use("api", 90, "acme"), RepoUse(f"{HOME}/work/toy", 30, "my-org"))
        the_facts = facts(repos=repos)
        recommended = init_plan.recommend(the_facts)
        self.assertEqual((recommended.repo_mask, recommended.repos_dir), ("acme", "~/Development"))
        unattended = init_plan.unattended(the_facts, recommended)
        self.assertEqual(unattended.repos, ())
        self.assertEqual((unattended.repo_mask, unattended.repos_dir), ("acme my-org", "~/Development"))

    def test_the_model_follows_the_plan(self) -> None:
        self.assertEqual(init_plan.recommend(facts(claude_plan="max")).model, "opus")
        self.assertEqual(init_plan.recommend(facts(claude_plan="pro")).model, "sonnet")
        self.assertEqual(init_plan.recommend(facts(claude_plan="")).model, "sonnet")

    def test_one_aws_profile_is_used_but_with_more_the_choice_is_yours(self) -> None:
        self.assertEqual(init_plan.recommend(facts(aws_sso_profiles=("dev",))).aws_sso_profiles, ("dev",))
        self.assertEqual(init_plan.recommend(facts(aws_sso_profiles=("dev", "prod"))).aws_sso_profiles, ())

    def test_the_menu_bar_app_is_recommended_on_a_first_run(self) -> None:
        self.assertTrue(init_plan.recommend(facts()).menu_app)

    def test_a_rerun_goes_by_the_config_so_an_install_without_the_app_stays_without(self) -> None:
        self.assertFalse(init_plan.recommend(facts(env={"BOSUN_DAYS": "MTWRF"})).menu_app)
        self.assertFalse(init_plan.recommend(facts(env={"BOSUN_MENU_APP": "0"})).menu_app)
        self.assertTrue(init_plan.recommend(facts(env={"BOSUN_MENU_APP": "1"})).menu_app)

    def test_the_app_is_not_offered_while_there_is_no_signed_build_to_install(self) -> None:
        """app/publisher.json has no valid team_id: no recommendation, whatever the config says."""
        gone = "app/publisher.json has no team_id yet"
        self.assertFalse(init_plan.recommend(facts(app_unavailable=gone)).menu_app)
        self.assertFalse(
            init_plan.recommend(facts(app_unavailable=gone, env={"BOSUN_MENU_APP": "1"})).menu_app
        )
        offered = init_plan.offered(facts(app_unavailable=gone), Answers(menu_app=True))
        self.assertFalse(offered.menu_app)
        self.assertEqual(init_plan.offered(facts(), Answers(menu_app=True)), Answers(menu_app=True))
        self.assertEqual(
            init_plan.offered(facts(app_unavailable=gone), Answers(menu_app=False)), Answers(menu_app=False)
        )

    def test_someone_elses_repeating_wake_is_not_replaced(self) -> None:
        self.assertFalse(init_plan.recommend(facts(repeating_wake="wake at 6:00AM")).wake)
        self.assertTrue(init_plan.recommend(facts()).wake)

    def test_a_rerun_may_move_its_own_wake(self) -> None:
        answers = init_plan.recommend(facts(repeating_wake="wake at 8:01AM", env={"BOSUN_DAYS": "MTWRF"}))
        self.assertTrue(answers.wake)

    def test_a_rerun_keeps_the_listed_windows_and_their_options(self) -> None:
        listed = ({"name": "api", "dir": f"{HOME}/x/api", "model": "haiku"},)
        answers = init_plan.recommend(
            facts(files={"sessions.yml": "..."}, sessions=listed, repos=(use("web", 90),))
        )
        self.assertEqual(answers.repos, (Repo("api", "~/x/api", {"model": "haiku"}),))


class AnswersTest(unittest.TestCase):
    def test_a_file_only_needs_what_it_changes(self) -> None:
        base = Answers(days="MTWRF", learning=True)
        changed = init_plan.answers_from_json({"days": "MWF"}, base)
        self.assertEqual((changed.days, changed.learning), ("MWF", True))

    def test_printed_answers_read_back_the_same(self) -> None:
        answers = Answers(
            repos=(Repo("api", "~/d/api", {"model": "haiku"}),), aws_sso_profiles=("dev",), repo_mask="acme"
        )
        self.assertEqual(init_plan.answers_from_json(init_plan.answers_to_json(answers), Answers()), answers)

    def test_a_repo_needs_only_its_folder(self) -> None:
        changed = init_plan.answers_from_json(
            {"repos": [{"dir": "~/d/my.api"}, {"dir": "~/e/my.api"}]}, Answers()
        )
        self.assertEqual([r.name for r in changed.repos], ["my-api", "my-api-2"])

    def test_bad_answers_are_refused(self) -> None:
        for data, text in (
            ([], "object"),
            ({"typo": 1}, "unknown answers: typo"),
            ({"learning": "yes"}, "learning"),
            ({"days": 5}, "days"),
            ({"repos": "api"}, "list"),
            ({"repos": [{"name": "api"}]}, "dir"),
            ({"repos": [{"dir": "~/a", "name": 3}]}, "name"),
            ({"aws_sso_profiles": "dev"}, "aws_sso_profiles"),
            ({"orchestrator_connectors": ["acme-local"]}, "orchestrator_connectors"),
        ):
            with self.subTest(data=data), self.assertRaisesRegex(AnswersError, text):
                init_plan.answers_from_json(data, Answers())

    def test_problems_are_named(self) -> None:
        for changes, text in (
            ({"days": ""}, "days"),
            ({"days": "MM"}, "days"),
            ({"days": "XF"}, "days"),
            ({"start": "8am"}, "start"),
            ({"until": "24:00"}, "until"),
            ({"start": "08:00", "until": "08:00"}, "around the clock"),
            ({"repos": (Repo("a b", "~/x"),)}, "letters"),
            ({"repos": (Repo("-a", "~/x"),)}, "letters"),
            ({"repos": (Repo("checkin", "~/x"),)}, "bosun adds"),
            ({"repos": (Repo("a", "~/x"), Repo("a", "~/y"))}, "more than once"),
            ({"slack_watch": True, "events": False}, "needs events"),
            ({"model": "opus; rm -rf"}, "model"),
            ({"model": ""}, "model"),
            ({"repo_mask": "a\nb"}, "control"),
            ({"repos": (Repo("a", "~/x\ny"),)}, "control"),
        ):
            with self.subTest(changes=changes):
                self.assertIn(text, " ".join(init_plan.validate(replace(Answers(), **changes))))

    def test_good_answers_have_no_problems(self) -> None:
        self.assertEqual(init_plan.validate(init_plan.recommend(facts(repos=(use("api", 50),)))), [])

    def test_bad_connectors_answer_is_a_problem(self) -> None:
        for value, text in (
            ("all, claude.ai Acme Mail", "orchestrator_connectors: `all` means every server"),
            ("acme-local\nBOSUN_X=1", "control character"),
        ):
            with self.subTest(value=value):
                self.assertIn(text, " ".join(init_plan.validate(Answers(orchestrator_connectors=value))))
        for value in ("", "all", "claude.ai Acme Mail, https://mcp.example.com/*"):
            with self.subTest(value=value):
                self.assertEqual(init_plan.validate(Answers(orchestrator_connectors=value)), [])


def shell_value_of(text: str, key: str) -> str:
    """What bash makes of <key> in this env file, the real test of the quoting."""
    script = f'HOME=/Users/me; {text}\nprintf %s "${key}"'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout


class EnvFileTest(unittest.TestCase):
    def test_a_new_file_says_only_the_answers(self) -> None:
        text = init_plan.render_env(Answers(days="MWF", start="07:30", repos_dir="~/work", learning=False))
        self.assertIn('BOSUN_DAYS="MWF"\n', text)
        self.assertIn('BOSUN_REPOS_DIR="$HOME/work"\n', text)
        self.assertIn('BOSUN_LEARN_ENABLED="0"\n', text)
        self.assertEqual(shell_value_of(text, "BOSUN_REPOS_DIR"), "/Users/me/work")

    def test_env_groups_write_the_orchestrators_connectors(self) -> None:
        answers = Answers(orchestrator_connectors="claude.ai Acme Mail, plugin:acme-tracker:tracker")
        settings = dict(setting for _, group in init_plan.env_groups(answers) for setting in group)
        self.assertEqual(
            settings["BOSUN_ORCHESTRATOR_CONNECTORS"], "claude.ai Acme Mail, plugin:acme-tracker:tracker"
        )
        text = init_plan.render_env(answers)
        self.assertEqual(
            shell_value_of(text, "BOSUN_ORCHESTRATOR_CONNECTORS"),
            "claude.ai Acme Mail, plugin:acme-tracker:tracker",
        )
        self.assertEqual(
            init_plan.parse_env(text)["BOSUN_ORCHESTRATOR_CONNECTORS"], answers.orchestrator_connectors
        )
        self.assertIn('BOSUN_ORCHESTRATOR_CONNECTORS=""\n', init_plan.render_env(Answers()))

    def test_every_setting_it_writes_is_a_known_one(self) -> None:
        example = (ROOT / "examples" / "generic" / "bosun.env").read_text()
        for _, settings in init_plan.env_groups(Answers()):
            for key, _ in settings:
                self.assertIn(f"\n{key}=", example)

    def test_the_learning_run_is_before_the_window_ends(self) -> None:
        self.assertEqual(init_plan.learn_at(Answers(start="08:00", until="20:00")), "18:30")
        self.assertEqual(init_plan.learn_at(Answers(start="10:00", until="01:00")), "23:30")
        self.assertEqual(init_plan.learn_at(Answers(start="10:00", until="11:00")), "10:00")

    def test_a_folder_cannot_run_a_command(self) -> None:
        text = init_plan.render_env(Answers(repos_dir='~/a"$(touch /tmp/x)`b`'))
        self.assertEqual(shell_value_of(text, "BOSUN_REPOS_DIR"), '/Users/me/a"$(touch /tmp/x)`b`')

    def test_parse_reads_what_it_wrote_and_what_people_write(self) -> None:
        text = (
            'BOSUN_A="1"  # a comment\nBOSUN_B=2\nexport BOSUN_C=\'three\'\nBOSUN_D=""\n'
            'BOSUN_E="$HOME/x"\n# BOSUN_F="commented out"\nOTHER=1\nBOSUN_G="unclosed\nBOSUN_A="last wins"\n'
        )
        self.assertEqual(
            init_plan.parse_env(text),
            {"BOSUN_A": "last wins", "BOSUN_B": "2", "BOSUN_C": "three", "BOSUN_D": "", "BOSUN_E": "$HOME/x"},
        )

    def test_a_rerun_changes_its_settings_and_keeps_yours(self) -> None:
        text = init_plan.render_env(Answers()) + '\n# mine\nBOSUN_ALERT_SLACK_USER="U000"\n'
        merged = init_plan.merge_env(text, Answers(days="MWF"))
        self.assertIn('BOSUN_DAYS="MWF"\n', merged)
        self.assertNotIn('BOSUN_DAYS="MTWRF"', merged)
        self.assertIn('# mine\nBOSUN_ALERT_SLACK_USER="U000"\n', merged)

    def test_a_rerun_with_the_same_answers_changes_nothing(self) -> None:
        text = init_plan.render_env(Answers())
        self.assertEqual(init_plan.merge_env(text, Answers()), text)

    def test_a_line_that_already_says_it_stays_as_written(self) -> None:
        text = init_plan.render_env(Answers()).replace('BOSUN_DAYS="MTWRF"', "BOSUN_DAYS=MTWRF # weekdays")
        self.assertIn("BOSUN_DAYS=MTWRF # weekdays\n", init_plan.merge_env(text, Answers()))

    def test_what_the_file_lacks_is_added_at_the_end(self) -> None:
        merged = init_plan.merge_env('BOSUN_DAYS="MTWRF"\n', Answers())
        self.assertTrue(merged.startswith('BOSUN_DAYS="MTWRF"\n'))
        self.assertIn('# Added by `bosun init`\nBOSUN_START_TIME="08:00"\n', merged)
        self.assertEqual(merged.count("BOSUN_DAYS="), 1)


def read_yaml(text: str) -> Any:
    script = "require 'yaml'; require 'json'; puts JSON.generate(YAML.safe_load(STDIN.read))"
    result = subprocess.run(["ruby", "-e", script], input=text, capture_output=True, text=True, check=True)
    return __import__("json").loads(result.stdout)


class OtherFilesTest(unittest.TestCase):
    def test_sessions_read_back_as_yaml(self) -> None:
        repos = (
            Repo("api", "~/d/api"),
            Repo("web", "/odd path: yes/web", {"model": "opus", "autocompact": 200000}),
        )
        self.assertEqual(
            read_yaml(init_plan.render_sessions(Answers(repos=repos))),
            {
                "sessions": [
                    {"name": "api", "dir": "~/d/api"},
                    {"name": "web", "dir": "/odd path: yes/web", "model": "opus", "autocompact": 200000},
                ]
            },
        )

    def test_a_name_yaml_would_misread_stays_text(self) -> None:
        names = ("yes", "null", "123", "true")
        repos = tuple(Repo(name, f"~/d/{name}") for name in names)
        listed = read_yaml(init_plan.render_sessions(Answers(repos=repos)))["sessions"]
        self.assertEqual([entry["name"] for entry in listed], list(names))

    def test_no_repos_is_still_a_valid_file(self) -> None:
        self.assertEqual(read_yaml(init_plan.render_sessions(Answers())), {"sessions": []})

    def test_setup_checks_the_login_and_the_chosen_profiles(self) -> None:
        text = init_plan.render_setup(Answers(aws_sso_profiles=("dev", "odd name")))
        self.assertIn("  ensure_claude_login\n  ensure_aws_sso dev\n  ensure_aws_sso 'odd name'\n}\n", text)
        subprocess.run(["bash", "-n"], input=text, text=True, check=True)


class PlanTest(unittest.TestCase):
    def plan(self, the_facts: Facts, answers: Answers, force: bool = False, **state: Any) -> init_plan.Plan:
        return init_plan.build_plan(the_facts, answers, DirState(**state), force)

    def actions(self, plan: init_plan.Plan) -> dict[str, str]:
        return {change.name: change.action for change in plan.files}

    def test_a_new_mac_creates_all_three_files(self) -> None:
        answers = init_plan.recommend(facts())
        self.assertEqual(
            self.actions(self.plan(facts(), answers)),
            {"bosun.env": "create", "sessions.yml": "create", "setup.sh": "create"},
        )

    def configured(self, answers: Answers) -> Facts:
        return facts(
            files={
                "bosun.env": init_plan.render_env(answers),
                "sessions.yml": init_plan.render_sessions(answers),
                "setup.sh": "setup_steps() { :; }\n",
            },
            env=init_plan.parse_env(init_plan.render_env(answers)),
            sessions=tuple({"name": r.name, "dir": r.dir} for r in answers.repos),
        )

    def test_a_rerun_with_the_same_answers_changes_nothing(self) -> None:
        answers = Answers(repos=(Repo("api", "~/d/api"),))
        the_facts = self.configured(answers)
        again = init_plan.recommend(the_facts)
        self.assertEqual(
            self.actions(self.plan(the_facts, again)),
            {"bosun.env": "same", "sessions.yml": "same", "setup.sh": "keep"},
        )

    def test_a_rerun_merges_the_env_file_without_force(self) -> None:
        answers = Answers(repos=(Repo("api", "~/d/api"),))
        the_facts = self.configured(answers)
        changed = self.plan(the_facts, replace(init_plan.recommend(the_facts), days="MWF"))
        self.assertEqual(self.actions(changed)["bosun.env"], "update")
        self.assertEqual(changed.blockers, ())

    def test_other_windows_need_force(self) -> None:
        answers = Answers(repos=(Repo("api", "~/d/api"),))
        the_facts = self.configured(answers)
        other = replace(init_plan.recommend(the_facts), repos=(Repo("web", "~/d/web"),))
        refused = self.plan(the_facts, other)
        self.assertEqual(self.actions(refused)["sessions.yml"], "refuse")
        self.assertIn("--force", " ".join(refused.blockers))
        forced = self.plan(the_facts, other, force=True)
        self.assertEqual(self.actions(forced)["sessions.yml"], "replace")
        self.assertEqual(forced.blockers, ())

    def test_a_sessions_file_with_no_windows_needs_no_force(self) -> None:
        the_facts = self.configured(Answers())
        wanted = replace(init_plan.recommend(the_facts), repos=(Repo("web", "~/d/web"),))
        plan = self.plan(the_facts, wanted)
        self.assertEqual(self.actions(plan)["sessions.yml"], "replace")
        self.assertEqual(plan.blockers, ())

    def test_unattended_first_run_takes_no_windows_or_connectors(self) -> None:
        the_facts = facts(repos=(use("api", 400),), connectors=("acme-local",))
        recommended = replace(init_plan.recommend(the_facts), orchestrator_connectors="acme-local")
        unattended = init_plan.unattended(the_facts, recommended)
        self.assertEqual((unattended.repos, unattended.orchestrator_connectors), ((), ""))
        self.assertEqual(
            replace(
                unattended,
                repos=recommended.repos,
                orchestrator_connectors="acme-local",
                menu_app=recommended.menu_app,
            ),
            recommended,
        )
        self.assertEqual(init_plan.unattended_note(the_facts, unattended), init_plan.UNATTENDED_NOTE)

    def test_unattended_first_run_skips_the_menu_bar_app_nobody_asked_for(self) -> None:
        the_facts = facts(repos=(use("api", 400),))
        recommended = init_plan.recommend(the_facts)
        self.assertTrue(recommended.menu_app)
        self.assertFalse(init_plan.unattended(the_facts, recommended).menu_app)
        rerun = self.configured(Answers(menu_app=True))
        self.assertTrue(init_plan.unattended(rerun, init_plan.recommend(rerun)).menu_app)

    def test_unattended_keeps_windows_a_sessions_file_already_lists(self) -> None:
        listed = ({"name": "api", "dir": f"{HOME}/d/api"},)
        the_facts = facts(files={"sessions.yml": "..."}, sessions=listed)
        unattended = init_plan.unattended(the_facts, init_plan.recommend(the_facts))
        self.assertEqual(unattended.repos, (Repo("api", "~/d/api"),))

    def test_the_unattended_note_names_the_windows_it_kept(self) -> None:
        listed = ({"name": "api", "dir": f"{HOME}/d/api"}, {"name": "web", "dir": f"{HOME}/d/web"})
        the_facts = facts(files={"sessions.yml": "..."}, sessions=listed)
        note = init_plan.unattended_note(
            the_facts, init_plan.unattended(the_facts, init_plan.recommend(the_facts))
        )
        self.assertNotIn("No repo windows", note)
        self.assertIn("No connectors were set up", note)
        self.assertIn("the windows sessions.yml lists stay: api, web", note)

    def test_a_sessions_file_that_cannot_be_read_needs_force(self) -> None:
        the_facts = facts(files={"sessions.yml": "sessions: [unclosed"}, sessions_unreadable=True)
        wanted = replace(init_plan.recommend(the_facts), repos=(Repo("web", "~/d/web"),))
        self.assertEqual(self.actions(self.plan(the_facts, wanted))["sessions.yml"], "refuse")
        self.assertEqual(self.actions(self.plan(the_facts, wanted, force=True))["sessions.yml"], "replace")

    def test_unattended_rerun_is_the_recommendation(self) -> None:
        the_facts = self.configured(Answers(repos=(Repo("api", "~/d/api"),), orchestrator_connectors="all"))
        recommended = init_plan.recommend(the_facts)
        self.assertEqual(init_plan.unattended(the_facts, recommended), recommended)
        self.assertEqual(recommended.orchestrator_connectors, "all")
        self.assertEqual(init_plan.unattended_note(the_facts, recommended), "")

    def test_setup_stays_yours_even_with_force(self) -> None:
        answers = Answers()
        the_facts = self.configured(answers)
        self.assertEqual(self.actions(self.plan(the_facts, answers))["setup.sh"], "keep")
        self.assertEqual(self.actions(self.plan(the_facts, answers, force=True))["setup.sh"], "keep")

    def test_a_profile_your_setup_lacks_is_yours_to_add(self) -> None:
        the_facts = self.configured(Answers())
        plan = self.plan(the_facts, Answers(aws_sso_profiles=("dev",)))
        self.assertIn("ensure_aws_sso dev", " ".join(plan.notes))

    def test_a_rerun_with_a_hand_written_config_changes_no_behaviour(self) -> None:
        text = 'BOSUN_DAYS="MWF"\nBOSUN_ORCHESTRATOR_MODEL="haiku"\nBOSUN_LEARN_AT="17:00"\n'
        the_facts = facts(
            files={"bosun.env": text},
            env=init_plan.parse_env(text),
            days=weekdays(600, 1200),
            repos=(use("api", 50),),
            slack_plugin=True,
            claude_plan="pro",
        )
        answers = init_plan.recommend(the_facts)
        self.assertEqual(
            (answers.days, answers.start, answers.until, answers.model), ("MWF", "08:00", "20:00", "opus")
        )
        self.assertFalse(answers.events or answers.approvals or answers.learning or answers.slack_watch)
        self.assertEqual((answers.repo_mask, answers.repos_dir), ("", "~/Development"))
        merged = init_plan.parse_env(self.plan(the_facts, answers).files[0].text)
        self.assertEqual(merged["BOSUN_ORCHESTRATOR_MODEL"], "haiku")
        self.assertEqual(merged["BOSUN_LEARN_AT"], "17:00")
        self.assertEqual(merged["BOSUN_EVENTS_ENABLED"], "0")

    def test_what_follows_from_an_answer_moves_when_it_changes(self) -> None:
        text = init_plan.render_env(Answers(model="opus")).replace(
            'BOSUN_ORCHESTRATOR_MODEL="opus"', 'BOSUN_ORCHESTRATOR_MODEL="haiku"'
        )
        the_facts = facts(files={"bosun.env": text}, env=init_plan.parse_env(text))
        same = self.plan(the_facts, init_plan.recommend(the_facts))
        self.assertEqual(self.actions(same)["bosun.env"], "same")
        changed = replace(init_plan.recommend(the_facts), model="sonnet", until="17:00")
        merged = init_plan.parse_env(self.plan(the_facts, changed).files[0].text)
        self.assertEqual(merged["BOSUN_ORCHESTRATOR_MODEL"], "sonnet")
        self.assertEqual(merged["BOSUN_LEARN_AT"], "15:30")

    def test_a_missing_tool_or_folder_blocks(self) -> None:
        plan = self.plan(facts(missing_tools=("tmux", "claude")), Answers(), missing=("~/gone",))
        text = " ".join(plan.blockers)
        self.assertIn("tmux, claude", text)
        self.assertIn("No such folder: ~/gone", text)

    def test_a_login_that_is_missing_is_worth_knowing_but_does_not_block(self) -> None:
        plan = self.plan(facts(claude_logged_in=False, gh_logged_in=False), Answers())
        self.assertEqual(plan.blockers, ())
        self.assertIn("claude auth login", " ".join(plan.notes))
        self.assertIn("gh auth login", " ".join(plan.notes))

    def test_only_untrusted_folders_get_trusted_and_only_if_answered(self) -> None:
        self.assertEqual(self.plan(facts(), Answers(), untrusted=("/x",)).trust, ("/x",))
        self.assertEqual(self.plan(facts(), Answers(trust=False), untrusted=("/x",)).trust, ())

    def test_a_wake_already_set_needs_no_sudo(self) -> None:
        env = {"BOSUN_DAYS": "MWF"}
        current = facts(env=env, repeating_wake="wakepoweron at 9:01AM MWF")
        answers = Answers(days="MWF", start="09:00")
        self.assertEqual(self.plan(current, answers).wake_command, "")
        self.assertIn("MTWRF", self.plan(current, replace(answers, days="MTWRF")).wake_command)
        self.assertIn("10:01:00", self.plan(current, replace(answers, start="10:00")).wake_command)

    def test_pmset_prints_a_twelve_hour_clock(self) -> None:
        for clock_time, label in (
            ("08:01", "8:01AM"),
            ("00:01", "0:01AM"),
            ("00:30", "0:30AM"),
            ("11:59", "11:59AM"),
            ("12:00", "12:00PM"),
            ("12:01", "12:01PM"),
            ("13:01", "1:01PM"),
            ("18:30", "6:30PM"),
            ("23:59", "11:59PM"),
        ):
            self.assertEqual(init_plan.pmset_label(clock_time), label)

    def test_doctor_asks_for_the_label_on_the_command_line(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(init_plan.main(["pmset-label", "00:01"]), 0)
        self.assertEqual(out.getvalue(), "0:01AM\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(init_plan.main(["pmset-label", "soon"]), 2)
            self.assertEqual(init_plan.main([]), 2)

    def test_outside_macos_it_says_so(self) -> None:
        self.assertIn("macOS", " ".join(self.plan(facts(has_launchd=False), Answers()).blockers))

    def test_the_wake_is_a_minute_after_the_job(self) -> None:
        plan = self.plan(facts(), Answers(days="MWF", start="09:00"))
        self.assertEqual(plan.wake_command, "sudo pmset repeat wakeorpoweron MWF 09:01:00")
        delayed = self.plan(facts(env={"BOSUN_WAKE_DELAY_MINUTES": "5"}), Answers(start="09:00"))
        self.assertIn(" 09:05:00", delayed.wake_command)
        self.assertEqual(self.plan(facts(), Answers(wake=False)).wake_command, "")

    def test_a_first_run_says_how_sessions_handle_permissions(self) -> None:
        self.assertIn("auto mode", " ".join(self.plan(facts(), Answers()).notes))

    def test_bypass_says_what_it_costs_and_that_its_warning_needs_accepting(self) -> None:
        notes = " ".join(self.plan(facts(), Answers(permissions="bypass")).notes)
        self.assertIn("no protection against prompt injection", notes)
        self.assertIn("accept that mode's warning once", notes)
        accepted = " ".join(self.plan(facts(bypass_accepted=True), Answers(permissions="bypass")).notes)
        self.assertNotIn("accept that mode's warning once", accepted)

    def test_the_answer_becomes_the_flags(self) -> None:
        self.assertIn('BOSUN_CLAUDE_FLAGS="--permission-mode auto --chrome"', init_plan.render_env(Answers()))
        bypass = init_plan.render_env(Answers(permissions="bypass"))
        self.assertIn('BOSUN_CLAUDE_FLAGS="--dangerously-skip-permissions --chrome"', bypass)

    def test_a_config_on_bypass_stays_on_bypass(self) -> None:
        env = {"BOSUN_CLAUDE_FLAGS": "--dangerously-skip-permissions --chrome"}
        self.assertEqual(init_plan.recommend(facts(env=env)).permissions, "bypass")
        self.assertEqual(init_plan.recommend(facts()).permissions, "auto")

    def test_flags_you_added_stay_until_you_change_the_answer(self) -> None:
        text = 'BOSUN_CLAUDE_FLAGS="--permission-mode auto --verbose"\n'
        the_facts = facts(files={"bosun.env": text}, env=init_plan.parse_env(text))
        same = self.plan(the_facts, init_plan.recommend(the_facts)).files[0].text
        self.assertEqual(init_plan.parse_env(same)["BOSUN_CLAUDE_FLAGS"], "--permission-mode auto --verbose")
        changed = (
            self.plan(the_facts, replace(init_plan.recommend(the_facts), permissions="bypass")).files[0].text
        )
        self.assertIn("--dangerously-skip-permissions", init_plan.parse_env(changed)["BOSUN_CLAUDE_FLAGS"])

    def test_codex_is_recommended_when_it_is_what_you_use_and_it_is_ready(self) -> None:
        def recommended(ready: tuple[str, ...], claude: int, codex: int) -> str:
            prompts = {"claude": claude, "codex": codex}
            return init_plan.recommend(facts(ready_agents=ready, agent_prompts=prompts)).harness

        both = ("claude", "codex")
        self.assertEqual(recommended(both, 2, 9), "codex")
        self.assertEqual(recommended(both, 2, 1), "claude")
        self.assertEqual(recommended(both, 2, 2), "claude")
        self.assertEqual(recommended(("claude",), 2, 9), "claude")
        self.assertEqual(init_plan.recommend(facts()).harness, "claude")

    def test_asking_for_an_agent_when_installing_wins(self) -> None:
        the_facts = facts(asked_harness="codex", env={"BOSUN_HARNESS": "claude"})
        self.assertEqual(init_plan.recommend(the_facts).harness, "codex")

    def test_your_config_says_which_agent_on_a_rerun(self) -> None:
        self.assertEqual(init_plan.recommend(facts(env={"BOSUN_HARNESS": "codex"})).harness, "codex")

    def test_the_answer_becomes_the_setting(self) -> None:
        self.assertIn('BOSUN_HARNESS="codex"', init_plan.render_env(Answers(harness="codex")))
        self.assertIn('BOSUN_HARNESS="claude"', init_plan.render_env(Answers()))

    def test_a_first_install_follows_the_releases(self) -> None:
        self.assertEqual(init_plan.recommend(facts()).update_channel, "releases")
        self.assertEqual(Answers().update_channel, "releases")
        self.assertIn('BOSUN_UPDATE_CHANNEL="releases"\n', init_plan.render_env(Answers()))

    def test_an_install_that_asked_for_a_branch_follows_it(self) -> None:
        """get.sh hands BOSUN_INIT_CHANNEL=main to init when BOSUN_BRANCH was given: the answer is taken
        from the environment, with no question."""
        answers = init_plan.recommend(facts(asked_channel="main"))
        self.assertEqual(answers.update_channel, "main")
        self.assertEqual(init_plan.validate(answers), [])
        self.assertIn('BOSUN_UPDATE_CHANNEL="main"\n', init_plan.render_env(answers))
        self.assertEqual(init_plan.unattended(facts(asked_channel="main"), answers).update_channel, "main")

    def test_a_rerun_keeps_the_channel_the_config_has(self) -> None:
        self.assertEqual(
            init_plan.recommend(facts(env={"BOSUN_UPDATE_CHANNEL": "main"})).update_channel, "main"
        )
        self.assertEqual(
            init_plan.recommend(facts(env={"BOSUN_UPDATE_CHANNEL": "releases"})).update_channel, "releases"
        )

    def test_what_the_config_has_wins_over_what_a_window_inherited(self) -> None:
        """BOSUN_INIT_CHANNEL=main may be inherited by a window get.sh started, long after: a re-run of init
        there must never flip a channel set by hand back; only a first run (no config) takes it."""
        inherited = facts(asked_channel="main", env={"BOSUN_UPDATE_CHANNEL": "releases"})
        self.assertEqual(init_plan.recommend(inherited).update_channel, "releases")
        flipped = facts(asked_channel="releases", env={"BOSUN_UPDATE_CHANNEL": "main"})
        self.assertEqual(init_plan.recommend(flipped).update_channel, "main")
        self.assertEqual(init_plan.recommend(facts(asked_channel="main")).update_channel, "main")  # first run

    def test_a_config_from_before_the_channel_goes_on_following_main(self) -> None:
        """What bosun update writes for it (migrations.CONFIG_ADDED): a re-run changes nothing unasked."""
        self.assertEqual(init_plan.recommend(facts(env={"BOSUN_DAYS": "MTWRF"})).update_channel, "main")

    def test_the_channel_is_one_of_the_two(self) -> None:
        self.assertIn("update_channel", " ".join(init_plan.validate(Answers(update_channel="nightly"))))
        changed = init_plan.answers_from_json({"update_channel": "main"}, Answers())
        self.assertEqual(changed.update_channel, "main")
        with self.assertRaisesRegex(AnswersError, "update_channel"):
            init_plan.answers_from_json({"update_channel": 3}, Answers())

    def test_a_rerun_with_the_same_channel_changes_nothing_in_the_file(self) -> None:
        text = init_plan.render_env(Answers(update_channel="main"))
        self.assertEqual(init_plan.merge_env(text, Answers(update_channel="main")), text)
        self.assertIn('BOSUN_UPDATE_CHANNEL="releases"', init_plan.merge_env(text, Answers()))

    def test_on_codex_the_plan_says_so(self) -> None:
        the_facts = facts()
        answers = Answers(harness="codex")
        plan = self.plan(the_facts, answers)
        text = init_plan.render_plan(plan, answers, the_facts, "/c")
        self.assertIn("Codex, with its automatic reviewer", text)
        self.assertIn("for the orchestrator", text)
        self.assertIn("stays on Claude Code", " ".join(" ".join(plan.notes).split()))
        self.assertNotIn("auto mode", " ".join(plan.notes))

    def test_any_agent_with_a_profile_is_an_answer(self) -> None:
        self.assertEqual(init_plan.validate(Answers(harness="codex")), [])
        self.assertIn(
            "harness: 'acme-agent' must be agy or claude or codex",
            init_plan.validate(Answers(harness="acme-agent")),
        )

    def test_an_agent_other_than_the_default_says_what_its_windows_run(self) -> None:
        profile = replace(codex_state.PROFILE, setup_sessions="Acme sessions", setup_note="Acme note.")
        the_facts = facts()
        answers = Answers(harness="codex")
        with mock.patch.object(codex_state, "PROFILE", profile):
            plan = self.plan(the_facts, answers)
            text = init_plan.render_plan(plan, answers, the_facts, "/c")
        self.assertIn("Acme sessions", text)
        self.assertIn("Acme note.", " ".join(plan.notes))

    def test_an_unknown_agent_is_refused(self) -> None:
        self.assertIn("harness", " ".join(init_plan.validate(Answers(harness="nosuch"))))

    def test_an_unknown_answer_is_refused(self) -> None:
        self.assertIn("permissions", " ".join(init_plan.validate(Answers(permissions="yolo"))))

    def test_a_kept_wake_says_how_to_replace_it(self) -> None:
        plan = self.plan(facts(repeating_wake="wake at 6:00AM"), Answers(wake=False))
        self.assertIn("wake at 6:00AM", " ".join(plan.notes))

    def test_the_reasons_quote_your_history(self) -> None:
        the_facts = facts(repos=(use("api", 50),), days=weekdays(9 * 60 + 41, 21 * 60 + 27))
        plan = self.plan(the_facts, init_plan.recommend(the_facts))
        self.assertIn("09:41", " ".join(plan.reasons))
        self.assertIn("20 active days", " ".join(plan.reasons))

    def test_the_printed_plan_says_nothing_has_changed_and_what_would(self) -> None:
        answers = Answers(repos=(Repo("api", "~/d/api"),), days="MTWRF", start="09:00", until="21:30")
        text = init_plan.render_plan(
            self.plan(facts(), answers, untrusted=("/Users/me/d/api",)),
            answers,
            facts(),
            "/Users/me/.config/bosun",
        )
        for expected in (
            "Nothing has changed yet",
            "Windows    api",
            "Connectors none, for the orchestrator",
            "Mon-Fri, 09:00 to 21:30",
            "events, approvals, learning on; Slack watcher off",
            "Files in ~/.config/bosun",
            "bosun trust --any ~/d/api",
            "./install.sh --with-wake",
            "sudo pmset repeat wakeorpoweron MTWRF 09:01:00",
        ):
            self.assertIn(expected, text)

    def test_the_menu_bar_app_turns_on_its_setting_and_the_consoles_background_job(self) -> None:
        on = init_plan.parse_env(init_plan.render_env(Answers(menu_app=True)))
        self.assertEqual((on["BOSUN_MENU_APP"], on["BOSUN_CONSOLE_ALWAYS"]), ("1", "1"))
        off = init_plan.parse_env(init_plan.render_env(Answers(menu_app=False)))
        self.assertEqual((off["BOSUN_MENU_APP"], off["BOSUN_CONSOLE_ALWAYS"]), ("0", "0"))

    def merged(self, env: dict[str, str], answers: Answers) -> dict[str, str]:
        """What a re-run on a bosun.env that sets <env> leaves it as, for <answers>."""
        text = "".join(f'{key}="{value}"\n' for key, value in env.items())
        the_facts = facts(files={"bosun.env": text}, env=env)
        keep = init_plan.settings_to_keep(the_facts, answers)
        return init_plan.parse_env(init_plan.merge_env(text, answers, keep))

    def test_the_console_job_follows_the_app_answer_when_the_answer_changes(self) -> None:
        # No to yes: both on. Yes to no: both off, so turning the app off turns the console job off.
        on = self.merged({"BOSUN_MENU_APP": "0", "BOSUN_CONSOLE_ALWAYS": "0"}, Answers(menu_app=True))
        self.assertEqual((on["BOSUN_MENU_APP"], on["BOSUN_CONSOLE_ALWAYS"]), ("1", "1"))
        off = self.merged({"BOSUN_MENU_APP": "1", "BOSUN_CONSOLE_ALWAYS": "1"}, Answers(menu_app=False))
        self.assertEqual((off["BOSUN_MENU_APP"], off["BOSUN_CONSOLE_ALWAYS"]), ("0", "0"))
        # A bosun.env from before the key: its app was off, so yes is a change.
        older = self.merged({"BOSUN_DAYS": "MTWRF"}, Answers(menu_app=True))
        self.assertEqual((older["BOSUN_MENU_APP"], older["BOSUN_CONSOLE_ALWAYS"]), ("1", "1"))

    def test_a_hand_set_console_setting_survives_a_rerun_that_changes_nothing(self) -> None:
        kept = self.merged({"BOSUN_MENU_APP": "1", "BOSUN_CONSOLE_ALWAYS": "0"}, Answers(menu_app=True))
        self.assertEqual((kept["BOSUN_MENU_APP"], kept["BOSUN_CONSOLE_ALWAYS"]), ("1", "0"))
        also = self.merged({"BOSUN_MENU_APP": "0", "BOSUN_CONSOLE_ALWAYS": "1"}, Answers(menu_app=False))
        self.assertEqual((also["BOSUN_MENU_APP"], also["BOSUN_CONSOLE_ALWAYS"]), ("0", "1"))

    def test_a_refused_install_puts_both_settings_back_to_off_and_leaves_the_rest(self) -> None:
        text = init_plan.render_env(Answers(menu_app=True)) + '\n# mine\nBOSUN_ALERT_SLACK_USER="U000"\n'
        back = init_plan.parse_env(init_plan.menu_app_off(text))
        self.assertEqual((back["BOSUN_MENU_APP"], back["BOSUN_CONSOLE_ALWAYS"]), ("0", "0"))
        self.assertEqual(back["BOSUN_ALERT_SLACK_USER"], "U000")
        self.assertEqual(back["BOSUN_DAYS"], "MTWRF")
        self.assertEqual(init_plan.menu_app_off(init_plan.menu_app_off(text)), init_plan.menu_app_off(text))
        self.assertIn(
            'BOSUN_MENU_APP="0"', init_plan.menu_app_off('BOSUN_DAYS="MWF"\n')
        )  # added when missing

    def test_the_plan_says_it_installs_the_app_and_turns_on_open_at_login(self) -> None:
        wanted = self.plan(facts(), Answers(menu_app=True))
        self.assertTrue(wanted.menu_app)
        text = init_plan.render_plan(wanted, Answers(menu_app=True), facts(), "/Users/me/.config/bosun")
        self.assertIn("bosun app install", text)
        self.assertIn("bosun app login on", text)
        self.assertEqual(wanted.app_verb, "install")
        self.assertNotIn("bosun app update", text)
        unwanted = self.plan(facts(), Answers(menu_app=False))
        self.assertFalse(unwanted.menu_app)
        self.assertNotIn(
            "bosun app",
            init_plan.render_plan(unwanted, Answers(menu_app=False), facts(), "/Users/me/.config/bosun"),
        )

    def test_the_plan_says_what_will_run_where_the_app_is_already_there(self) -> None:
        there = facts(app_present=True)
        plan = self.plan(there, Answers(menu_app=True))
        self.assertEqual(plan.app_verb, "update")
        text = init_plan.render_plan(plan, Answers(menu_app=True), there, "/Users/me/.config/bosun")
        self.assertIn("bosun app update", text)
        self.assertNotIn("bosun app install", text)
        self.assertIn("bosun app login on", text)

    def test_the_plan_says_why_the_app_is_not_offered_in_one_line(self) -> None:
        gone = facts(app_unavailable="app/publisher.json has no team_id yet")
        plan = self.plan(gone, Answers(menu_app=False))
        lines = [note for note in plan.notes if "menu bar app" in note]
        self.assertEqual(len(lines), 1)
        self.assertIn("app/publisher.json has no team_id yet", lines[0])
        self.assertIn("no signed build to install", lines[0])
        self.assertEqual(
            [n for n in self.plan(facts(), Answers(menu_app=False)).notes if "menu bar app" in n], []
        )

    def test_no_to_the_app_where_it_is_installed_says_it_stays_and_how_to_remove_it(self) -> None:
        plan = self.plan(facts(app_present=True), Answers(menu_app=False))
        [line] = [note for note in plan.notes if "bosun.app" in note]
        self.assertIn("stays", line)
        self.assertIn("bosun app remove", line)
        self.assertEqual(
            [n for n in self.plan(facts(), Answers(menu_app=False)).notes if "bosun app remove" in n], []
        )
        self.assertEqual(
            [
                n
                for n in self.plan(facts(app_present=True), Answers(menu_app=True)).notes
                if "bosun app remove" in n
            ],
            [],
        )

    def test_the_menu_app_answer_is_a_yes_or_no_in_an_answers_file(self) -> None:
        base = Answers()
        self.assertFalse(init_plan.answers_from_json({"menu_app": False}, base).menu_app)
        with self.assertRaises(AnswersError):
            init_plan.answers_from_json({"menu_app": "yes"}, base)
        self.assertIs(init_plan.answers_to_json(base)["menu_app"], True)

    def test_days_read_naturally(self) -> None:
        for letters, words in (
            ("MTWRF", "Mon-Fri"),
            ("MTWRFSU", "Mon-Sun"),
            ("MWF", "Mon, Wed, Fri"),
            ("SU", "Sat, Sun"),
        ):
            self.assertEqual(init_plan.describe_days(letters), words)


if __name__ == "__main__":
    unittest.main()
