"""lib/init_detect.py and lib/init.py: what `bosun init` finds and does, in a scratch home."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import claude_state
import init
import init_detect
import init_plan
import init_terminal
import init_wizard


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


class FakeTerminal:
    """Keys from a list (an exception in it is raised), and the end of the input after them."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def show(self, text: str) -> None:
        pass

    def say(self, text: str) -> None:
        pass

    def key(self) -> str:
        key = self.keys.pop(0) if self.keys else ""
        if not isinstance(key, str):
            raise key
        return key

    def line(self, prompt: str) -> str:
        return ""


class WorldTest(unittest.TestCase):
    """A scratch home, with Claude Code's folders pointing into it."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve() / "home"
        self.home.mkdir()
        self.config = self.home / ".config" / "bosun"
        for patcher in (
            mock.patch.object(claude_state, "PROJECTS_DIR", self.home / ".claude" / "projects"),
            mock.patch.object(claude_state, "CONFIG_FILE", self.home / ".claude.json"),
            mock.patch.object(claude_state, "INSTALLED_PLUGINS", self.home / "plugins.json"),
            mock.patch.object(claude_state, "SETTINGS_FILE", self.home / ".claude" / "settings.json"),
            mock.patch.object(Path, "home", return_value=self.home),
            # This scratch home is itself in a temporary folder.
            mock.patch.object(init_detect, "TEMPORARY_FOLDERS", (str(self.home / "scratch"),)),
            mock.patch.dict(os.environ, {"BOSUN_CONFIG_DIR": str(self.config), "HOME": str(self.home)}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        # Never the real tools: only git and ruby, which only read here.
        real_output = init_detect.output_of

        def output_of(*command: str) -> str | None:
            return None if command[0] in ("gh", "pmset", "sudo") else real_output(*command)

        for tool in (
            mock.patch.object(init_detect, "output_of", output_of),
            mock.patch.object(shutil, "which", return_value="/usr/bin/a-tool"),
            mock.patch.object(
                claude_state, "auth_status", return_value=claude_state.AuthStatus(True, "x", "max")
            ),
        ):
            tool.start()
            self.addCleanup(tool.stop)

    def repo(self, relative: str, remote: str = "") -> Path:
        path = self.home / relative
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        if remote:
            subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
        return path

    def prompts(self, cwd: Path, count: int, *, session: str = "s1", days_ago: int = 1, **extra: Any) -> None:
        folder = claude_state.PROJECTS_DIR / str(cwd).replace("/", "-")
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{session}.jsonl").open("a") as fh:
            for number in range(count):
                entry = {
                    "type": "user",
                    "cwd": str(cwd),
                    "timestamp": (NOW - timedelta(days=days_ago, minutes=number)).isoformat(),
                    "message": {"content": "do the thing"},
                    **extra,
                }
                fh.write(json.dumps(entry) + "\n")


class HistoryTest(WorldTest):
    def test_where_and_when_someone_typed_never_what(self) -> None:
        api = self.repo("Development/api")
        self.prompts(api, 2)
        found = list(claude_state.prompt_history(NOW - timedelta(days=30)))
        self.assertEqual([cwd for cwd, _ in found], [str(api), str(api)])
        self.assertTrue(all(isinstance(at, datetime) for _, at in found))

    def test_only_typed_prompts_count(self) -> None:
        api = self.repo("Development/api")
        self.prompts(api, 1)
        folder = claude_state.PROJECTS_DIR / str(api).replace("/", "-")
        stamp = (NOW - timedelta(days=1)).isoformat()
        with (folder / "s1.jsonl").open("a") as fh:
            for entry in (
                {"type": "assistant", "cwd": str(api), "timestamp": stamp, "message": {"content": "ok"}},
                {
                    "type": "user",
                    "cwd": str(api),
                    "timestamp": stamp,
                    "isMeta": True,
                    "message": {"content": "meta"},
                },
                {
                    "type": "user",
                    "cwd": str(api),
                    "timestamp": stamp,
                    "message": {"content": [{"type": "tool_result", "content": "x"}]},
                },
            ):
                fh.write(json.dumps(entry) + "\n")
            fh.write("not json\n")
        self.assertEqual(len(list(claude_state.prompt_history(NOW - timedelta(days=30)))), 1)

    def test_a_script_s_prompts_are_not_someone_at_work(self) -> None:
        self.prompts(self.repo("Development/api"), 3, entrypoint="sdk-cli")
        self.assertEqual(list(claude_state.prompt_history(NOW - timedelta(days=30))), [])

    def test_older_prompts_are_left_out(self) -> None:
        self.prompts(self.repo("Development/api"), 3, days_ago=60)
        self.assertEqual(list(claude_state.prompt_history(NOW - timedelta(days=30))), [])

    def test_the_plan_comes_from_auth_status(self) -> None:
        status = claude_state.parse_auth_status(
            '{"loggedIn": true, "authMethod": "x", "subscriptionType": "max"}'
        )
        self.assertEqual((status.logged_in, status.plan), (True, "max"))
        self.assertEqual(claude_state.parse_auth_status('{"loggedIn": false}').plan, "")


class DetectPartsTest(WorldTest):
    def test_a_subfolder_counts_for_its_repo(self) -> None:
        api = self.repo("Development/api")
        (api / "src" / "deep").mkdir(parents=True)
        self.assertEqual(init_detect.repo_root(api / "src" / "deep"), api)

    def test_a_worktree_counts_for_its_main_checkout(self) -> None:
        api = self.repo("Development/api")
        identity = ("-c", "user.name=a", "-c", "user.email=a@example.com")
        subprocess.run(
            ["git", "-C", str(api), *identity, "commit", "-q", "--allow-empty", "-m", "x"], check=True
        )
        tree = self.home / "trees" / "feature"
        subprocess.run(["git", "-C", str(api), "worktree", "add", "-q", str(tree)], check=True)
        self.assertEqual(init_detect.repo_root(tree), api)

    def test_folders_that_are_no_repo_scratch_or_home_are_left_out(self) -> None:
        plain = self.home / "notes"
        plain.mkdir()
        self.assertIsNone(init_detect.repo_root(plain))
        self.assertFalse(init_detect.usable_repo(self.home, self.home))
        scratch = self.home / "scratch" / "x"
        scratch.mkdir(parents=True)
        self.assertFalse(init_detect.usable_repo(scratch, self.home))
        self.assertFalse(init_detect.usable_repo(None, self.home))

    def test_prompts_add_up_per_repo_most_first(self) -> None:
        api, web = self.repo("Development/api"), self.repo("Development/web")
        (api / "src").mkdir()
        uses = [str(web)] + [str(api)] * 2 + [str(api / "src")] * 2 + [str(self.home / "gone")]
        self.assertEqual(init_detect.repos_from_history(uses, self.home), [(api, 4), (web, 1)])

    def test_without_history_it_looks_in_the_usual_folders_newest_first(self) -> None:
        older, newer = self.repo("Development/older"), self.repo("src/newer")
        self.repo("elsewhere/hidden")
        os.utime(older / ".git", (1000, 1000))
        (self.home / "Development" / "plain").mkdir()
        self.assertEqual(init_detect.repos_in_usual_folders(self.home), [newer, older])

    def test_the_github_owner_of_a_repo(self) -> None:
        for remote, owner in (
            ("git@github.com:acme/api.git", "acme"),
            ("https://github.com/my-org/web.git", "my-org"),
            ("git@gitlab.example.com:acme/api.git", ""),
        ):
            with self.subTest(remote=remote):
                self.assertEqual(init_detect.github_owner(self.repo(f"r/{owner or 'none'}", remote)), owner)
        self.assertEqual(init_detect.github_owner(self.repo("r/no-remote")), "")

    def test_only_aws_profiles_with_sso(self) -> None:
        config = self.home / "aws-config"
        config.write_text(
            "[default]\nregion = x\n[profile dev]\nsso_session = a\n[profile keys]\nregion = y\n"
            "[profile old]\nsso_start_url = https://example.com\n[sso-session a]\nsso_region = z\n"
        )
        self.assertEqual(init_detect.aws_sso_profiles(config), ("dev", "old"))
        self.assertEqual(init_detect.aws_sso_profiles(self.home / "none"), ())

    def test_only_a_repeating_wake_counts(self) -> None:
        one_off = "Scheduled power events:\n [0] wake at 11/02/2026 09:00:00 by 'x'\n"
        repeating = (
            "Repeating power events:\n  wakepoweron at 8:01AM weekdays only\nScheduled power events:\n x\n"
        )
        self.assertEqual(init_detect.repeating_wake(one_off), "")
        self.assertEqual(init_detect.repeating_wake(repeating), "wakepoweron at 8:01AM weekdays only")
        self.assertEqual(init_detect.repeating_wake("Repeating power events:\n  shutdown at 8PM\n"), "")

    def test_a_command_that_is_missing_or_fails_is_none(self) -> None:
        self.assertIsNone(init_detect.output_of("no-such-command-here"))
        self.assertIsNone(init_detect.output_of("false"))
        self.assertEqual(init_detect.output_of("echo", "hi"), "hi\n")

    def test_the_listed_windows_are_read_with_ruby_as_the_rest_of_bosun_does(self) -> None:
        path = self.home / "sessions.yml"
        path.write_text("# a comment\nsessions:\n  - name: api\n    dir: ~/d/api\n    model: haiku\n")
        self.assertEqual(
            init_detect.sessions_listed(path), ({"name": "api", "dir": "~/d/api", "model": "haiku"},)
        )
        self.assertEqual(init_detect.sessions_listed(self.home / "none.yml"), ())


class DetectTest(WorldTest):
    def test_it_finds_repos_and_days_and_reads_only(self) -> None:
        api = self.repo("Development/api", "git@github.com:acme/api.git")
        self.prompts(api, 30, days_ago=1)
        self.prompts(api, 1, session="s2", days_ago=2)
        before = {str(p) for p in self.home.rglob("*")}
        facts = init_detect.detect(self.config, NOW)
        self.assertEqual({str(p) for p in self.home.rglob("*")} - before, set())
        self.assertEqual(facts.repos, (init_plan.RepoUse(str(api), 31, "acme"),))
        self.assertEqual(len(facts.days), 2)
        self.assertEqual(facts.files, {})

    def test_a_rerun_sees_the_config(self) -> None:
        self.config.mkdir(parents=True)
        (self.config / "bosun.env").write_text('BOSUN_DAYS="MWF"\n')
        (self.config / "sessions.yml").write_text("sessions:\n  - name: api\n    dir: ~/d/api\n")
        facts = init_detect.detect(self.config, NOW)
        self.assertEqual(facts.env, {"BOSUN_DAYS": "MWF"})
        self.assertEqual(facts.sessions, ({"name": "api", "dir": "~/d/api"},))
        self.assertEqual(set(facts.files), {"bosun.env", "sessions.yml"})


class CommandTest(WorldTest):
    def setUp(self) -> None:
        super().setUp()
        self.api = self.repo("Development/api", "git@github.com:acme/api.git")
        self.prompts(self.api, 30)
        self.calls: list[list[str]] = []
        real_run = subprocess.run

        def run(command: Any, *args: Any, **kwargs: Any) -> Any:
            """What bosun would run is recorded. Only git and ruby really run, on this scratch home:
            a test must never reach a real login or anything else that changes a machine."""
            name = Path(str(command[0])).name
            if name in ("install.sh", "bosun"):
                self.calls.append([name, *command[1:]])
                return subprocess.CompletedProcess(command, self.install_status)
            if name in ("git", "ruby"):
                return real_run(command, *args, **kwargs)
            raise AssertionError(f"a unit test tried to run {command}")

        self.install_status = 0
        patcher = mock.patch.object(subprocess, "run", run)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("can_ask_for_sudo", True),):
            sudo = mock.patch.object(init, name, return_value=value)
            sudo.start()
            self.addCleanup(sudo.stop)

    def bosun_init(self, *argv: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            mock.patch.object(sys, "stdin", io.StringIO(stdin)),
        ):
            status = init.main(list(argv))
        return status, out.getvalue(), err.getvalue()

    def answers_file(self, **answers: Any) -> str:
        path = self.home / f"answers-{len(list(self.home.glob('answers-*')))}.json"
        path.write_text(json.dumps(answers))
        return str(path)

    def backups(self) -> list[str]:
        return sorted(p.name for p in self.config.glob("*.pre-init-*"))

    def test_on_its_own_it_only_prints_the_plan(self) -> None:
        status, out, _ = self.bosun_init()
        self.assertEqual(status, 0)
        self.assertIn("Nothing has changed yet", out)
        self.assertIn("--recommended", out)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.calls, [])

    def test_a_dry_run_changes_nothing_either(self) -> None:
        status, out, _ = self.bosun_init("--recommended", "--dry-run")
        self.assertEqual(status, 0)
        self.assertIn("create", out)
        self.assertFalse(self.config.exists())

    def test_detect_and_print_answers_are_json(self) -> None:
        self.assertEqual(json.loads(self.bosun_init("--detect")[1])["repos"][0]["dir"], str(self.api))
        answers = json.loads(self.bosun_init("--print-answers")[1])
        self.assertEqual(answers["repos"], [{"name": "api", "dir": "~/Development/api"}])
        self.assertFalse(self.config.exists())

    def test_detect_never_prints_what_the_config_says(self) -> None:
        self.config.mkdir(parents=True)
        (self.config / "bosun.env").write_text('BOSUN_ALERT_URL="https://hooks.example.com/made-up"\n')
        out = self.bosun_init("--detect")[1]
        self.assertNotIn("made-up", out)
        printed = json.loads(out)
        self.assertEqual(sorted(printed["env"]), ["BOSUN_ALERT_URL"])
        self.assertEqual(sorted(printed["files"]), ["bosun.env"])

    def test_recommended_writes_the_config_then_trusts_and_installs(self) -> None:
        status, out, _ = self.bosun_init("--recommended")
        self.assertEqual(status, 0)
        self.assertEqual(self.calls[0][:3], ["bosun", "trust", "--any"])
        self.assertIn(str(self.api), self.calls[0])
        self.assertEqual(self.calls[1], ["install.sh", "--with-wake"])
        self.assertIn('BOSUN_REPO_MASK="acme"', (self.config / "bosun.env").read_text())
        self.assertIn("name: api", (self.config / "sessions.yml").read_text())
        self.assertIn("ensure_claude_login", (self.config / "setup.sh").read_text())
        self.assertIn("bosun is set up", out)

    def test_the_files_are_readable_by_you_and_no_temp_file_is_left(self) -> None:
        self.bosun_init("--recommended")
        self.assertEqual(stat.S_IMODE((self.config / "bosun.env").stat().st_mode), 0o644)
        self.assertEqual(
            sorted(p.name for p in self.config.iterdir()), ["bosun.env", "sessions.yml", "setup.sh"]
        )

    def test_what_claude_state_says_about_its_screens(self) -> None:
        self.assertFalse(claude_state.onboarded())
        (self.home / ".claude.json").write_text('{"hasCompletedOnboarding": true}')
        self.assertTrue(claude_state.onboarded())
        self.assertFalse(claude_state.bypass_accepted())

    def test_a_rerun_writes_nothing_and_backs_up_nothing(self) -> None:
        self.bosun_init("--recommended")
        before = {p.name: p.read_text() for p in self.config.iterdir()}
        status, out, _ = self.bosun_init("--recommended")
        self.assertEqual(status, 0)
        self.assertEqual({p.name: p.read_text() for p in self.config.iterdir()}, before)
        self.assertEqual(self.backups(), [])
        self.assertNotIn("create", out)

    def test_answers_change_the_env_file_keep_your_lines_and_back_it_up(self) -> None:
        self.bosun_init("--recommended")
        with (self.config / "bosun.env").open("a") as fh:
            fh.write('BOSUN_ALERT_SLACK_USER="U000"\n')
        status, _, _ = self.bosun_init("--answers", self.answers_file(days="MWF"))
        self.assertEqual(status, 0)
        text = (self.config / "bosun.env").read_text()
        self.assertIn('BOSUN_DAYS="MWF"', text)
        self.assertIn('BOSUN_ALERT_SLACK_USER="U000"', text)
        self.assertEqual(len(self.backups()), 1)
        self.assertIn('BOSUN_DAYS="M', (self.config / self.backups()[0]).read_text())

    def test_other_windows_are_refused_without_force_and_nothing_changes(self) -> None:
        self.bosun_init("--recommended")
        web = self.repo("Development/web")
        self.calls.clear()
        before = (self.config / "sessions.yml").read_text()
        status, out, err = self.bosun_init("--answers", self.answers_file(repos=[{"dir": str(web)}]))
        self.assertEqual(status, init.EXIT_BLOCKED)
        self.assertIn("--force", out)
        self.assertIn("Nothing was changed", err)
        self.assertEqual((self.config / "sessions.yml").read_text(), before)
        self.assertEqual(self.calls, [])

    def test_force_replaces_them_with_a_backup(self) -> None:
        self.bosun_init("--recommended")
        web = self.repo("Development/web")
        status, _, _ = self.bosun_init("--force", "--answers", self.answers_file(repos=[{"dir": str(web)}]))
        self.assertEqual(status, 0)
        self.assertIn("name: web", (self.config / "sessions.yml").read_text())
        self.assertTrue(any(name.startswith("sessions.yml.pre-init-") for name in self.backups()))

    def test_answers_from_stdin(self) -> None:
        status, _, _ = self.bosun_init("--answers", "-", stdin='{"days": "MWF"}')
        self.assertEqual(status, 0)
        self.assertIn('BOSUN_DAYS="MWF"', (self.config / "bosun.env").read_text())

    def test_a_folder_that_does_not_exist_blocks_before_anything_is_written(self) -> None:
        status, out, _ = self.bosun_init("--answers", self.answers_file(repos=[{"dir": "~/gone"}]))
        self.assertEqual(status, init.EXIT_BLOCKED)
        self.assertIn("No such folder: ~/gone", out)
        self.assertFalse(self.config.exists())

    def test_bad_answers_are_a_usage_error(self) -> None:
        for argv, text in (
            (["--answers", str(self.home / "none.json")], "cannot read"),
            (["--answers", "-"], "not JSON"),
            (["--answers", self.answers_file(typo=1)], "unknown answers"),
            (["--answers", self.answers_file(days="XX")], "days"),
        ):
            with self.subTest(argv=argv):
                status, _, err = self.bosun_init(*argv)
                self.assertEqual(status, init.EXIT_USAGE)
                self.assertIn(text, err)
        self.assertFalse(self.config.exists())

    def test_a_failed_doctor_is_reported(self) -> None:
        self.install_status = 1
        status, _, err = self.bosun_init("--recommended")
        self.assertEqual(status, init.EXIT_BLOCKED)
        self.assertIn("bosun doctor", err)

    def test_trusted_folders_are_not_trusted_again(self) -> None:
        with mock.patch.object(claude_state, "trusted", return_value=True):
            self.bosun_init("--recommended")
        self.assertEqual([call[0] for call in self.calls], ["install.sh"])

    def test_without_a_terminal_or_sudo_it_says_what_to_run(self) -> None:
        with mock.patch.object(init, "can_ask_for_sudo", return_value=False):
            _, out, _ = self.bosun_init("--recommended")
        self.assertIn("run this once yourself: sudo pmset repeat", out)
        self.assertEqual(self.calls[-1], ["install.sh"])

    def test_a_terminal_gets_the_wizard_and_anything_explicit_does_not(self) -> None:
        # Parsed first: argparse looks at the real stdout, for colour, since Python 3.14.
        plain = init.parse_args([])
        explicit = [
            init.parse_args(argv)
            for argv in (["--recommended"], ["--answers", "x"], ["--dry-run"], ["--print-answers"])
        ]
        terminal = mock.Mock(isatty=lambda: True)
        with mock.patch.object(sys, "stdin", terminal), mock.patch.object(sys, "stdout", terminal):
            self.assertTrue(init.wants_wizard(plain))
            for args in explicit:
                self.assertFalse(init.wants_wizard(args))

    def test_no_terminal_no_wizard(self) -> None:
        self.assertFalse(init.wants_wizard(init.parse_args([])))

    def test_a_typed_folder_counts_for_its_repo(self) -> None:
        (self.api / "src").mkdir()
        use = init.typed_repo(str(self.api / "src"))
        assert use is not None
        self.assertEqual((use.dir, use.owner), (str(self.api), "acme"))
        self.assertIsNone(init.typed_repo(str(self.home / "gone")))

    def test_it_says_how_to_reach_bosun_only_if_your_path_cannot(self) -> None:
        with mock.patch.dict(os.environ, {"BOSUN_USER_PATH": "/usr/bin"}):
            self.assertIn("not on your PATH", init.path_note())
        with mock.patch.dict(os.environ, {"BOSUN_USER_PATH": f"/usr/bin:{self.home}/.local/bin"}):
            self.assertEqual(init.path_note(), "")

    def wizard(self, keys: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            mock.patch.object(init_terminal, "TerminalUi", return_value=FakeTerminal(keys)),
        ):
            status = init.run_wizard(init_detect.detect(self.config), self.config)
        return status, out.getvalue()

    def test_the_wizard_writes_nothing_until_you_accept(self) -> None:
        status, out = self.wizard([init_wizard.ENTER, "q"])
        self.assertEqual(status, 0)
        self.assertIn("Nothing was changed", out)
        self.assertFalse(self.config.exists())

    def test_the_wizard_carries_out_what_you_accepted_then_offers_to_start(self) -> None:
        status, _ = self.wizard([*[init_wizard.ENTER] * 6, "y", "y"])
        self.assertEqual(status, 0)
        self.assertIn("name: api", (self.config / "sessions.yml").read_text())
        self.assertEqual(self.calls[-1], ["bosun", "start"])

    def test_the_wizard_replaces_other_windows_once_you_saw_it_would(self) -> None:
        self.bosun_init("--recommended")
        self.repo("Development/web")
        self.prompts(self.home / "Development" / "web", 30, session="s9")
        status, _ = self.wizard(["1", "2", *[init_wizard.ENTER] * 6, "y", "n"])
        self.assertEqual(status, 0)
        self.assertIn("name: web", (self.config / "sessions.yml").read_text())
        self.assertTrue(any(name.startswith("sessions.yml.pre-init-") for name in self.backups()))

    def test_ctrl_c_in_the_wizard_changes_nothing(self) -> None:
        status, out = self.wizard([KeyboardInterrupt])  # type: ignore[list-item]
        self.assertEqual(status, 0)
        self.assertIn("Nothing was changed", out)
        self.assertFalse(self.config.exists())

    def test_no_wake_asked_for_means_no_wake_flag(self) -> None:
        self.bosun_init("--answers", self.answers_file(wake=False))
        self.assertEqual(self.calls[-1], ["install.sh"])


if __name__ == "__main__":
    unittest.main()
