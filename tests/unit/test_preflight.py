"""lib/preflight.sh and its profiles: what it finds and what it tells you to do, on a made-up machine.

Each test builds a PATH holding only what that machine has, so nothing here depends on this one.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "lib" / "preflight.sh"
BASICS = ("awk", "basename", "cat", "dirname", "sed", "tr", "uname")  # what the script itself uses
LOGGED_IN = '{"loggedIn": true, "authMethod": "claude.ai"}'
LOGGED_OUT = '{"loggedIn": false}'


class PreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "home"
        self.home.mkdir()
        self.bin = Path(temp.name) / "bin"
        self.bin.mkdir()
        for name in BASICS:
            found = shutil.which(name)
            assert found
            (self.bin / name).symlink_to(found)
        (self.bin / "python3").symlink_to(sys.executable)
        self.tool("xcode-select", "echo /Library/Developer/CommandLineTools")
        self.tool("brew")
        for name in ("git", "tmux", "jq", "ruby", "curl", "gh"):
            self.tool(name)
        self.claude("2.1.308", LOGGED_IN)
        (self.home / ".claude.json").write_text('{"hasCompletedOnboarding": true}')

    def tool(self, name: str, body: str = "exit 0") -> None:
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def claude(self, version: str, auth: str) -> None:
        self.tool(
            "claude",
            f"""case "$*" in
  --version) echo "{version} (Claude Code)" ;;
  "auth status --json") echo '{auth}' ;;
esac""",
        )

    def remove(self, *names: str) -> None:
        for name in names:
            (self.bin / name).unlink()

    def run_it(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        environment = {"PATH": str(self.bin), "HOME": str(self.home), "BOSUN_PLATFORM": "macos", **env}
        return subprocess.run(
            ["/bin/sh", str(PREFLIGHT), *args], env=environment, capture_output=True, text=True, check=False
        )

    def test_all_there(self) -> None:
        result = self.run_it()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Everything bosun needs is here", result.stdout)
        self.assertIn("on macOS", result.stdout)

    def test_quiet_says_nothing_when_all_is_well(self) -> None:
        result = self.run_it("--quiet")
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_missing_tools_are_one_line_to_paste(self) -> None:
        self.remove("tmux", "jq")
        result = self.run_it()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing    tmux", result.stdout)
        self.assertIn("1. brew install tmux jq", result.stdout)

    def test_everything_is_listed_at_once_with_what_to_do(self) -> None:
        self.remove("tmux", "claude")
        self.tool("gh", "exit 1")
        result = self.run_it()
        for expected in ("brew install tmux", "gh auth login", "https://claude.com/claude-code"):
            self.assertIn(expected, result.stdout)

    def test_it_says_how_to_go_on_and_where_to_get_help(self) -> None:
        self.remove("tmux")
        result = self.run_it(BOSUN_RERUN="the line you ran")
        self.assertIn("Then run this again: the line you ran", result.stdout)
        self.assertIn("docs/setup.md#if-you-get-stuck", result.stdout)

    def test_no_package_manager_points_at_it(self) -> None:
        self.remove("brew")
        result = self.run_it()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing    Homebrew", result.stdout)
        self.assertIn("https://brew.sh", result.stdout)

    def test_without_apple_s_tools_it_stops_before_touching_a_stub(self) -> None:
        self.tool("xcode-select", "exit 2")
        self.tool("git", "echo 'the git stub ran' >&2; exit 1")
        result = self.run_it()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Apple's command line tools", result.stdout)
        self.assertIn("https://brew.sh", result.stdout)
        self.assertNotIn("stub", result.stdout + result.stderr)
        self.assertNotIn("tmux", result.stdout)

    def test_an_old_python_is_outdated(self) -> None:
        self.remove("python3")
        self.tool("python3", "echo 3.9")
        result = self.run_it()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Python 3.9, and bosun needs 3.13", result.stdout)
        self.assertIn("brew install python", result.stdout)
        self.assertIn("once Python is in place", result.stdout)

    def test_no_python_at_all(self) -> None:
        self.remove("python3")
        result = self.run_it()
        self.assertIn("missing    Python 3.13", result.stdout)
        self.assertIn("brew install python", result.stdout)

    def test_the_harness_is_missing(self) -> None:
        self.remove("claude")
        result = self.run_it()
        self.assertIn("missing    Claude Code", result.stdout)
        self.assertIn("https://claude.com/claude-code", result.stdout)

    def test_the_harness_is_too_old(self) -> None:
        self.claude("2.1.100", LOGGED_IN)
        result = self.run_it()
        self.assertEqual(result.returncode, 1)
        self.assertIn("version 2.1.100, and bosun needs", result.stdout)
        self.assertIn("claude update", result.stdout)

    def test_the_floor_compares_as_numbers(self) -> None:
        self.claude("2.1.1000", LOGGED_IN)
        self.assertEqual(self.run_it().returncode, 0)
        self.claude("10.0.0", LOGGED_IN)
        self.assertEqual(self.run_it().returncode, 0)

    def test_the_harness_is_logged_out(self) -> None:
        self.claude("2.1.308", LOGGED_OUT)
        result = self.run_it()
        self.assertEqual(result.returncode, 1)
        self.assertIn("not logged in", result.stdout)

    def test_the_first_run_screens_are_for_a_person(self) -> None:
        (self.home / ".claude.json").unlink()
        result = self.run_it()
        self.assertEqual(result.returncode, 1)
        self.assertIn("first-run screens not done", result.stdout)
        self.assertIn("run claude, then /exit", result.stdout)

    def test_quiet_still_says_what_is_wrong(self) -> None:
        self.remove("tmux")
        result = self.run_it("--quiet")
        self.assertEqual(result.returncode, 1)
        self.assertIn("brew install tmux", result.stdout)
        self.assertNotIn("ok ", result.stdout)

    def with_codex(self, status: str = "exit 0") -> None:
        self.tool(
            "codex",
            f"""case "$*" in
  --version) echo "codex-cli 0.166.0" ;;
  "login status") {status} ;;
esac""",
        )

    def test_codex_is_only_asked_about_when_it_is_in_use(self) -> None:
        result = self.run_it()
        self.assertNotIn("Codex", result.stdout)

    def test_codex_in_use_is_checked_with_claude_code(self) -> None:
        self.with_codex()
        result = self.run_it(BOSUN_HARNESSES="claude codex")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Claude Code", result.stdout)
        self.assertIn("Codex", result.stdout)
        self.assertIn("version 0.166.0", result.stdout)

    def test_codex_missing_says_where_to_get_it(self) -> None:
        result = self.run_it(BOSUN_HARNESSES="claude codex")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing    Codex", result.stdout)
        self.assertIn("https://developers.openai.com/codex/cli", result.stdout)

    def test_codex_logged_out_says_how_to_log_in(self) -> None:
        self.with_codex("exit 1")
        result = self.run_it(BOSUN_HARNESSES="claude codex")
        self.assertEqual(result.returncode, 1)
        self.assertIn("codex login", result.stdout)

    def test_codex_only_needs_no_claude_code(self) -> None:
        self.remove("claude")
        self.with_codex()
        result = self.run_it(BOSUN_HARNESSES="codex")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("Claude Code", result.stdout)

    def test_each_agent_is_checked_once(self) -> None:
        result = self.run_it(BOSUN_HARNESSES="claude claude")
        self.assertEqual(result.stdout.count("Claude Code\n"), 1)

    def test_an_os_without_a_profile_says_so(self) -> None:
        result = self.run_it(BOSUN_PLATFORM="plan9")
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not run on plan9 yet", result.stdout)
        self.assertIn("ROADMAP", result.stdout)

    def test_a_harness_without_a_profile_says_which_there_are(self) -> None:
        result = self.run_it(BOSUN_HARNESSES="nosuch")
        self.assertEqual(result.returncode, 2)
        self.assertIn("'nosuch'", result.stdout)
        self.assertIn("claude", result.stdout)

    def test_it_changes_nothing(self) -> None:
        before = sorted(str(p) for p in self.home.rglob("*"))
        self.run_it()
        self.assertEqual(sorted(str(p) for p in self.home.rglob("*")), before)

    def test_every_profile_has_the_whole_interface(self) -> None:
        interfaces = {
            ROOT / "lib" / "platforms": (
                "platform_name",
                "platform_base_ready",
                "platform_base_name",
                "platform_base_help",
                "platform_manager_name",
                "platform_manager_ready",
                "platform_manager_help",
                "platform_install_command",
                "platform_package",
            ),
            ROOT / "lib" / "harnesses": (
                "harness_name",
                "harness_command",
                "harness_install_help",
                "harness_upgrade_help",
                "harness_min_version",
                "harness_version",
                "harness_checks",
            ),
        }
        for folder, names in interfaces.items():
            profiles = list(folder.glob("*.sh"))
            self.assertTrue(profiles)
            for profile in profiles:
                text = profile.read_text()
                for name in names:
                    with self.subTest(profile=profile.name, name=name):
                        self.assertIn(f"{name}()", text)

    def test_it_is_plain_sh(self) -> None:
        for path in [
            PREFLIGHT,
            *(ROOT / "lib" / "platforms").glob("*.sh"),
            *(ROOT / "lib" / "harnesses").glob("*.sh"),
        ]:
            subprocess.run(["/bin/sh", "-n", str(path)], check=True, env=os.environ)


if __name__ == "__main__":
    unittest.main()
