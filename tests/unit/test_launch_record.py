"""lib/launch_record.py: what each window was launched with, and the fingerprint an update compares."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import launch_record


ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "lib"


def hooks(*commands: str) -> str:
    """A --settings value whose Stop hooks run <commands>."""
    entries = [{"hooks": [{"type": "command", "command": command}]} for command in commands]
    return json.dumps({"effortLevel": "medium", "hooks": {"Stop": entries}})


class FingerprintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install = Path(self.tmp.name) / "bosun"
        self.plugin = self.install / "orchestrator" / "plugin"
        (self.plugin / "commands").mkdir(parents=True)
        (self.plugin / ".claude-plugin").mkdir()
        (self.plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "bosun"}\n')
        self.chores = self.plugin / "commands" / "chores.md"
        self.chores.write_text("# Chores\n\nCheck in.\n")

    def orchestrator_args(self, policy: str = "Be brief.", settings: str | None = None) -> list[str]:
        bosun = f"{self.install}/bin/bosun"
        return [
            "--model",
            "opus",
            "--settings",
            settings if settings is not None else hooks(f"{bosun} hook signal --window orch"),
            "--plugin-dir",
            str(self.plugin),
            "--append-system-prompt",
            policy,
            "--name",
            "orch-2026-11-06",
            "--remote-control",
            "orch",
        ]

    def fingerprint(self, args: list[str]) -> dict[str, str]:
        return launch_record.fingerprint(args, self.install)

    def test_the_policy_text_is_not_a_launch_input(self) -> None:
        brief = self.fingerprint(self.orchestrator_args(policy="Be brief."))
        thorough = self.fingerprint(self.orchestrator_args(policy="Be thorough, and say why."))
        self.assertEqual(brief, thorough)

    def test_a_changed_plugin_file_is(self) -> None:
        before = self.fingerprint(self.orchestrator_args())
        self.chores.write_text("# Chores\n\nCheck in, then report.\n")
        after = self.fingerprint(self.orchestrator_args())
        self.assertNotEqual(before["files"], after["files"])
        self.assertEqual(before["args"], after["args"])

    def test_a_new_plugin_file_is(self) -> None:
        before = self.fingerprint(self.orchestrator_args())
        (self.plugin / "commands" / "review.md").write_text("# Review\n")
        self.assertNotEqual(before["files"], self.fingerprint(self.orchestrator_args())["files"])

    def test_a_changed_hook_command_is(self) -> None:
        bosun = f"{self.install}/bin/bosun"
        one = hooks(f"{bosun} hook signal --window orch")
        two = hooks(f"{bosun} hook signal --window orch", f"{bosun} guard --post-tool-use")
        before = self.fingerprint(self.orchestrator_args(settings=one))
        after = self.fingerprint(self.orchestrator_args(settings=two))
        self.assertNotEqual(before["args"], after["args"])
        self.assertEqual(before["files"], after["files"])

    def test_a_settings_file_in_the_install_is_read(self) -> None:
        settings = self.install / "orchestrator" / "settings.json"
        settings.write_text('{"permissions": {"ask": []}}\n')
        args = ["--settings", str(settings), "--name", "orch"]
        before = self.fingerprint(args)
        settings.write_text('{"permissions": {"ask": ["Bash(gh pr merge:*)"]}}\n')
        self.assertNotEqual(before["files"], self.fingerprint(args)["files"])

    def test_a_file_outside_the_install_is_not_read(self) -> None:
        elsewhere = Path(self.tmp.name) / "site-settings.json"
        elsewhere.write_text("{}\n")
        args = ["--settings", str(elsewhere), "--name", "orch"]
        before = self.fingerprint(args)
        elsewhere.write_text('{"model": "sonnet"}\n')
        self.assertEqual(before, self.fingerprint(args))

    def test_an_install_path_that_is_not_there_has_no_files_yet(self) -> None:
        args = ["--plugin-dir", str(self.install / "orchestrator" / "extra-plugin")]
        before = self.fingerprint(args)
        self.assertEqual(before, self.fingerprint(args))
        (self.install / "orchestrator" / "extra-plugin").mkdir()
        (self.install / "orchestrator" / "extra-plugin" / "review.md").write_text("# Review\n")
        self.assertNotEqual(before["files"], self.fingerprint(args)["files"])

    def test_the_files_are_read_through_the_path_the_arguments_name(self) -> None:
        link = Path(self.tmp.name) / "share" / "bosun"
        link.parent.mkdir()
        link.symlink_to(self.install)
        args = ["--plugin-dir", str(link / "orchestrator" / "plugin")]
        before = launch_record.fingerprint(args, link)
        self.chores.write_text("changed\n")
        self.assertNotEqual(before["files"], launch_record.fingerprint(args, link)["files"])


def a_launch(commit: str = "0123abcd", harness: str = "claude", **options: str) -> launch_record.Launch:
    return launch_record.Launch(
        commit=commit,
        harness=harness,
        fingerprint={"args": "a" * 64, "files": "b" * 64},
        dir="/Users/me/src/invoice-exporter",
        model="opus",
        effort="medium",
        **options,
    )


class RecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"

    def test_a_record_round_trips(self) -> None:
        launch_record.record(self.state, "acme", "alpha", a_launch(model_override="sonnet"))
        self.assertEqual(
            launch_record.recorded(self.state, "acme", "alpha"),
            {
                "session": "acme",
                "window": "alpha",
                "commit": "0123abcd",
                "harness": "claude",
                "fingerprint": {"args": "a" * 64, "files": "b" * 64},
                "dir": "/Users/me/src/invoice-exporter",
                "model": "opus",
                "effort": "medium",
                "model_override": "sonnet",
                "effort_override": "",
            },
        )

    def test_the_latest_launch_replaces_the_record(self) -> None:
        launch_record.record(self.state, "acme", "alpha", a_launch(commit="old"))
        launch_record.record(self.state, "acme", "alpha", a_launch(commit="new", harness="codex"))
        record = launch_record.recorded(self.state, "acme", "alpha")
        assert record is not None
        self.assertEqual((record["commit"], record["harness"]), ("new", "codex"))
        folder = self.state / launch_record.LAUNCHES_DIR / "acme"
        self.assertEqual([p.name for p in folder.iterdir()], ["alpha.json"])

    def test_another_session_s_window_of_the_same_name_is_a_record_of_its_own(self) -> None:
        launch_record.record(self.state, "acme", "alpha", a_launch(commit="real"))
        launch_record.record(self.state, "acme-waketest", "alpha", a_launch(commit="wake-test"))
        real = launch_record.recorded(self.state, "acme", "alpha")
        assert real is not None
        self.assertEqual(real["commit"], "real")

    def test_a_window_never_launched_has_none(self) -> None:
        self.assertIsNone(launch_record.recorded(self.state, "acme", "alpha"))

    def test_an_unreadable_record_is_none(self) -> None:
        folder = self.state / launch_record.LAUNCHES_DIR / "acme"
        folder.mkdir(parents=True)
        (folder / "alpha.json").write_text("{cut sh")
        self.assertIsNone(launch_record.recorded(self.state, "acme", "alpha"))


class CliTest(unittest.TestCase):
    def run_cli(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(LIB / "launch_record.py"), *argv],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_record_writes_the_fingerprint_that_fingerprint_prints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "bosun"
            (install / "orchestrator" / "plugin").mkdir(parents=True)
            (install / "orchestrator" / "plugin" / "chores.md").write_text("# Chores\n")
            agent_args = [
                "--plugin-dir",
                f"{install}/orchestrator/plugin",
                "--append-system-prompt",
                "-x",
                "--name",
                "orch",
            ]
            printed = self.run_cli("fingerprint", "--install", str(install), "--", *agent_args)
            self.assertEqual(printed.returncode, 0, printed.stderr)
            state = Path(tmp) / "state"
            done = self.run_cli(
                "record", "--state-dir", str(state), "--session", "acme", "--window", "orch",
                "--commit", "0123abcd", "--harness", "claude", "--install", str(install),
                "--dir", tmp, "--model=opus", "--effort=-", "--model-override=-", "--effort-override=",
                "--", *agent_args,
            )  # fmt: skip
            self.assertEqual(done.returncode, 0, done.stderr)
            record = launch_record.recorded(state, "acme", "orch")
            assert record is not None
            self.assertEqual(record["fingerprint"], json.loads(printed.stdout))
            self.assertEqual(record["fingerprint"], launch_record.fingerprint(agent_args, install))
            self.assertEqual(
                (record["dir"], record["model_override"], record["effort_override"]), (tmp, "-", "")
            )
            self.assertEqual((record["model"], record["effort"]), ("opus", "-"))
            shown = self.run_cli("show", "--state-dir", str(state), "--session", "acme", "--window", "orch")
            self.assertEqual(json.loads(shown.stdout), record)
            missing = self.run_cli("show", "--state-dir", str(state), "--session", "acme", "--window", "web")
            self.assertEqual((missing.returncode, missing.stdout), (1, ""))


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("ruby", "jq", "git")), "bosun needs ruby, jq and git"
)
class LaunchCommandTest(unittest.TestCase):
    """`bin/bosun launch` in a scratch world, in a dry run: what it records, and --fingerprint."""

    def setUp(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.tmp = tmp
        self.repo = tmp / "repos" / "invoice-exporter"
        self.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (tmp / "config").mkdir()
        (tmp / "config" / "sessions.yml").write_text(
            f"sessions:\n  - name: api\n    dir: {self.repo}\n"
            f"  - name: web\n    dir: {self.repo}\n    harness: codex\n"
        )
        self.state = tmp / "state"
        # A Codex that lists no MCP servers, which is what a launch asks it, and does no more.
        self.codex = tmp / "codex"
        self.codex.write_text("#!/bin/sh\n[ \"$1 $2 $3\" = 'mcp list --json' ] && echo '[]'\n")
        self.codex.chmod(0o755)
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp / "home"),
            "BOSUN_CONFIG_DIR": str(tmp / "config"),
            "BOSUN_STATE_DIR": str(self.state),
            "BOSUN_LOG_DIR": str(self.state / "logs"),
            "BOSUN_SESSION_NAME": "acme",
            "BOSUN_LAUNCH_DRY_RUN": "1",
            "BOSUN_ORCHESTRATOR": "",
            "BOSUN_EVENTS_ENABLED": "0",
            "BOSUN_CLAUDE_BIN": str(tmp / "no-claude"),
            "BOSUN_CODEX_BIN": str(self.codex),
            "CODEX_HOME": str(tmp / "codex-home"),
        }

    def bosun(self, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ROOT / "bin" / "bosun"), *args],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def configure(self, *entries: str) -> None:
        """sessions.yml with these entries, each its lines after `- name: `."""
        listed = "".join(f"  - name: {entry}\n" for entry in entries)
        (self.tmp / "config" / "sessions.yml").write_text(f"sessions:\n{listed}")

    def launched(self, window: str) -> dict[str, object]:
        record = launch_record.recorded(self.state, "acme", window)
        assert record is not None, f"no launch of {window} recorded"
        return record

    def test_the_conversation_name_is_not_a_launch_input(self) -> None:
        for window in ("api", "web"):
            with self.subTest(window=window):
                first = self.bosun("launch", window, cwd=self.repo)
                self.assertEqual(first.returncode, 0, first.stderr)
                before = self.launched(window)["fingerprint"]
                (self.state / "conversations").mkdir(exist_ok=True)
                (self.state / "conversations" / window).write_text(f"{window}-2026-11-06-1\n")
                again = self.bosun("launch", window, cwd=self.repo)
                self.assertEqual(again.returncode, 0, again.stderr)
                self.assertIn(f"{window}-2026-11-06-1", again.stdout)  # the launch itself goes by it
                self.assertEqual(self.launched(window)["fingerprint"], before)
                printed = self.bosun("launch", "--fingerprint", window, cwd=self.tmp)
                self.assertEqual(json.loads(printed.stdout), before, printed.stderr)

    def test_a_window_outside_the_list_fingerprints_from_its_record(self) -> None:
        done = self.bosun("launch", "--model", "sonnet", "--effort", "low", "scratch", cwd=self.repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        record = self.launched("scratch")
        self.assertEqual(
            (record["dir"], record["model_override"], record["effort_override"]),
            (str(self.repo), "sonnet", "low"),
        )
        printed = self.bosun("launch", "--fingerprint", "scratch", cwd=self.tmp)
        self.assertEqual(json.loads(printed.stdout), record["fingerprint"], printed.stderr)

    def test_a_listed_window_keeps_its_recorded_agent_when_the_config_changes(self) -> None:
        self.configure(f"api\n    dir: {self.repo}")
        done = self.bosun("launch", "api", cwd=self.repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        before = self.launched("api")
        self.assertEqual((before["harness"], before["model"], before["effort"]), ("claude", "opus", "medium"))
        self.configure(f"api\n    dir: {self.repo}\n    harness: codex\n    model: gpt-test")
        printed = self.bosun("launch", "--fingerprint", "api", cwd=self.tmp)
        self.assertEqual(json.loads(printed.stdout), before["fingerprint"], printed.stderr)
        plain = self.bosun(
            "launch", "api", cwd=self.repo
        )  # a launch not for an update: the agent configured now
        self.assertIn("codex-turn-end", plain.stdout)
        self.assertEqual(self.launched("api")["harness"], "codex")

    def test_a_codex_window_whose_servers_cannot_be_listed_does_not_start(self) -> None:
        self.env["BOSUN_CODEX_BIN"] = str(self.tmp / "no-codex")
        done = self.bosun("launch", "web", cwd=self.repo)
        self.assertEqual(done.returncode, 1)
        self.assertIn("codex mcp list failed", done.stderr)
        self.assertNotIn("dry-run: would run", done.stdout)

    def test_a_window_outside_the_list_keeps_its_recorded_agent(self) -> None:
        done = self.bosun("launch", "--model", "sonnet", "scratch", cwd=self.repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        before = self.launched("scratch")["fingerprint"]
        self.env["BOSUN_HARNESS"] = "codex"
        printed = self.bosun("launch", "--fingerprint", "scratch", cwd=self.tmp)
        self.assertEqual(json.loads(printed.stdout), before, printed.stderr)

    def test_launch_harness_starts_the_window_on_that_agent(self) -> None:
        self.env["BOSUN_HARNESS"] = "codex"
        done = self.bosun("launch", "--harness", "claude", "--model", "opus", "scratch", cwd=self.repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--remote-control scratch", done.stdout)
        self.assertEqual(self.launched("scratch")["harness"], "claude")
        unknown = self.bosun("launch", "--harness", "nope", "scratch", cwd=self.repo)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("no way to run a window on 'nope'", unknown.stderr)

    def test_a_window_with_neither_a_list_entry_nor_a_record_is_refused(self) -> None:
        refused = self.bosun("launch", "--fingerprint", "elsewhere", cwd=self.repo)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(refused.stdout, "")
        self.assertIn(
            "no window 'elsewhere' in the window list, and no launch of it recorded", refused.stderr
        )


class NewVersionTest(unittest.TestCase):
    """Before an update switches the link, the new version's fingerprint reads its own files."""

    def test_a_changed_plugin_file_in_the_new_version_shows_before_the_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old, new, link = base / "versions" / "old", base / "versions" / "new", base / "bosun"
            for version in (old, new):
                (version / "orchestrator" / "plugin" / "commands").mkdir(parents=True)
                (version / "orchestrator" / "plugin" / "commands" / "chores.md").write_text("# Chores\n")
            link.symlink_to(old)
            args = ["--plugin-dir", str(link / "orchestrator" / "plugin"), "--name", "orch"]
            recorded = launch_record.fingerprint(args, link)  # what the running window was launched with
            self.assertEqual(launch_record.fingerprint(args, link, new), recorded)
            (new / "orchestrator" / "plugin" / "commands" / "chores.md").write_text(
                "# Chores\n\nThen report.\n"
            )
            self.assertNotEqual(launch_record.fingerprint(args, link, new)["files"], recorded["files"])
            self.assertEqual(launch_record.fingerprint(args, link), recorded)  # through the link: the old one


if __name__ == "__main__":
    unittest.main()
