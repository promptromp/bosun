"""lib/launch_record.py: what each window was launched with, and the fingerprint an update compares."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import launch_record


LIB = Path(__file__).resolve().parents[2] / "lib"


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

    def test_the_files_are_read_through_the_path_the_arguments_name(self) -> None:
        link = Path(self.tmp.name) / "share" / "bosun"
        link.parent.mkdir()
        link.symlink_to(self.install)
        args = ["--plugin-dir", str(link / "orchestrator" / "plugin")]
        before = launch_record.fingerprint(args, link)
        self.chores.write_text("changed\n")
        self.assertNotEqual(before["files"], launch_record.fingerprint(args, link)["files"])


class RecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"

    def test_a_record_round_trips(self) -> None:
        fp = {"args": "a" * 64, "files": "b" * 64}
        launch_record.record(self.state, "alpha", "0123abcd", "claude", fp)
        self.assertEqual(
            launch_record.recorded(self.state, "alpha"),
            {"window": "alpha", "commit": "0123abcd", "harness": "claude", "fingerprint": fp},
        )
        self.assertTrue((self.state / launch_record.LAUNCHES_DIR / "alpha.json").is_file())

    def test_the_latest_launch_replaces_the_record(self) -> None:
        launch_record.record(self.state, "alpha", "old", "claude", {"args": "1", "files": "2"})
        launch_record.record(self.state, "alpha", "new", "codex", {"args": "3", "files": "4"})
        self.assertEqual(
            launch_record.recorded(self.state, "alpha"),
            {
                "window": "alpha",
                "commit": "new",
                "harness": "codex",
                "fingerprint": {"args": "3", "files": "4"},
            },
        )
        self.assertEqual(
            [p.name for p in (self.state / launch_record.LAUNCHES_DIR).iterdir()], ["alpha.json"]
        )

    def test_a_window_never_launched_has_none(self) -> None:
        self.assertIsNone(launch_record.recorded(self.state, "alpha"))

    def test_an_unreadable_record_is_none(self) -> None:
        folder = self.state / launch_record.LAUNCHES_DIR
        folder.mkdir(parents=True)
        (folder / "alpha.json").write_text("{cut sh")
        self.assertIsNone(launch_record.recorded(self.state, "alpha"))


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
                "record", "--state-dir", str(state), "--install", str(install),
                "--window", "orch", "--commit", "0123abcd", "--harness", "claude", "--", *agent_args,
            )  # fmt: skip
            self.assertEqual(done.returncode, 0, done.stderr)
            record = launch_record.recorded(state, "orch")
            assert record is not None
            self.assertEqual(record["fingerprint"], json.loads(printed.stdout))
            self.assertEqual(record["fingerprint"], launch_record.fingerprint(agent_args, install))


if __name__ == "__main__":
    unittest.main()
