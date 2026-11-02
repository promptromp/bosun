"""lib/uninstall.py and what it reads: the whole command, in a scratch home, on a fake Mac. Any command the
fake does not know fails the test, so none reaches launchd, pmset or sudo for real."""

from __future__ import annotations

import contextlib
import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_changes
import agent_state
import claude_state
import codex_state
import init_detect
import uninstall
import uninstall_detect
import uninstall_macos
import uninstall_plan as up


LABEL = "com.bosun.morning"
LABELS = f"{LABEL} {LABEL}.watchdog {LABEL}.learn {LABEL}.waketest"
WAKE_LINE = "wakepoweron at 9:01AM every day"
real_run = subprocess.run


class FakeMac:
    """launchd, pmset, sudo and tmux, as far as this command asks them, and a log of what it asked."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.loaded: set[str] = set()
        self.repeating: list[str] = []
        self.windows: list[str] = []
        self.calls: list[str] = []
        self.stuck_jobs: set[str] = set()
        self.sudo_works = True

    def done(self, code: int = 0, out: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], code, out, "")

    def launchctl(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        label = command[2].rsplit("/", 1)[-1]
        if command[1] == "print":
            return self.done(0 if label in self.loaded else 113)
        self.calls.append(f"bootout {label}")
        if label not in self.stuck_jobs:
            self.loaded.discard(label)
        return self.done()

    def pmset(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if list(command[1:3]) == ["-g", "sched"]:
            lines = "".join(f"  {event}\n" for event in self.repeating)
            return self.done(out=f"Repeating power events:\n{lines}" if lines else "")
        raise AssertionError(f"pmset without sudo: {command}")

    def sudo(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(" ".join(command))
        if not self.sudo_works:
            return self.done(1)
        if list(command[-3:]) != ["pmset", "repeat", "cancel"]:
            raise AssertionError(f"unexpected sudo: {command}")
        self.repeating.clear()
        return self.done()

    def tmux(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if "list-windows" not in command:
            raise AssertionError(f"unexpected tmux: {command}")
        return self.done(0 if self.windows else 1, "".join(f"{name}\n" for name in self.windows))

    def stop(self) -> subprocess.CompletedProcess[str]:
        """What `bosun stop` does: ends the session, and leaves a file saying so."""
        self.calls.append("bosun stop")
        self.windows.clear()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "stopped-bosun").write_text("today")
        return self.done()

    def run(self, command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        tool = Path(command[0]).name
        if tool == "git":
            return real_run(command, **kwargs)
        if tool == "bosun":
            return self.stop()
        handlers = {"launchctl": self.launchctl, "pmset": self.pmset, "sudo": self.sudo, "tmux": self.tmux}
        if tool not in handlers:
            raise AssertionError(f"a test reached a real tool: {command}")
        return handlers[tool](command)


class World(unittest.TestCase):
    """A real install's files in a scratch home: the checkout get.sh makes, config, state, jobs, a wake."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve() / "home"
        self.home.mkdir()
        self.config = self.home / ".config" / "bosun"
        self.state = self.home / ".local" / "state" / "bosun"
        self.root = self.home / ".local" / "share" / "bosun"
        self.agents = self.home / "Library" / "LaunchAgents"
        self.mac = FakeMac(self.state)
        self.make_checkout()
        self.make_config()
        self.make_state()
        self.make_jobs()
        self.mac.repeating.append(WAKE_LINE)
        link = self.home / ".local" / "bin" / "bosun"
        link.parent.mkdir(parents=True)
        link.symlink_to(self.root / "bin" / "bosun")
        self.env = {
            "HOME": str(self.home),
            "BOSUN_CONFIG_DIR": str(self.config),
            "BOSUN_STATE_DIR": str(self.state),
            "BOSUN_LOG_DIR": str(self.state / "logs"),
            "BOSUN_SESSION_NAME": "bosun",
            "BOSUN_TMUX_SOCKET": "bosun",
            "BOSUN_START_TIME": "09:00",
            "BOSUN_WAKE_DELAY_MINUTES": "1",
            "BOSUN_JOB_LABELS": LABELS,
        }
        for patcher in (
            mock.patch.dict(os.environ, self.env),
            mock.patch.object(Path, "home", return_value=self.home),
            mock.patch.object(uninstall, "ROOT", self.root),
            mock.patch.object(subprocess, "run", self.mac.run),
            mock.patch.object(claude_state, "CONFIG_FILE", self.home / ".claude.json"),
            mock.patch.object(claude_state, "PROJECTS_DIR", self.home / ".claude" / "projects"),
            mock.patch.object(codex_state, "CONFIG_FILE", self.home / ".codex" / "config.toml"),
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(uninstall_macos, "UNLOAD_WAIT_SECONDS", 0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("TMUX", None)

    def make_checkout(self) -> None:
        origin = self.home.parent / "origin.git"
        source = self.home.parent / "source"
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.com"]
        real_run(["git", "init", "-q", "--bare", str(origin)], check=True)
        real_run(["git", "init", "-q", "-b", "main", str(source)], check=True)
        (source / "bin").mkdir()
        (source / "bin" / "bosun").write_text("#!/bin/sh\n")
        real_run(["git", "-C", str(source), "add", "."], check=True)
        real_run(["git", "-C", str(source), *identity, "commit", "-qm", "first"], check=True)
        real_run(["git", "-C", str(source), "push", "-q", str(origin), "main"], check=True)
        self.root.parent.mkdir(parents=True)
        real_run(["git", "clone", "-q", "-b", "main", str(origin), str(self.root)], check=True)
        real_run(["git", "-C", str(self.root), "config", "bosun.managed", "true"], check=True)

    def make_config(self) -> None:
        (self.config / "knowledge").mkdir(parents=True)
        for name, text in (
            ("bosun.env", 'BOSUN_DAYS="MTWRF"\n'),
            ("sessions.yml", "windows: []\n"),
            ("setup.sh", "setup_steps() { :; }\n"),
            ("forbidden-terms.txt", "secret-name\n"),
            ("knowledge/entities.jsonl", '{"id": "e1"}\n'),
            ("knowledge/facts.jsonl", '{"op": "add"}\n{"op": "add"}\n'),
        ):
            (self.config / name).write_text(text)

    def make_state(self) -> None:
        (self.state / "logs").mkdir(parents=True)
        (self.state / "events").mkdir()
        for name, text in (
            ("ledger.json", json.dumps({"items": {"a": {}, "b": {}}, "sources": {}, "runs": {}})),
            ("ledger.md", "# Ledger\n"),
            ("ledger.lock", ""),
            ("watchdog.json", "{}"),
            ("logs/morning-2026-11-02.log", "ran\n"),
            ("events/queue.jsonl", "{}\n"),
        ):
            (self.state / name).write_text(text)

    def make_jobs(self) -> None:
        self.agents.mkdir(parents=True)
        for label in (LABEL, f"{LABEL}.watchdog", f"{LABEL}.learn"):
            self.write_plist(label, f'exec "{self.root}/bin/bosun" run')
            self.mac.loaded.add(label)

    def write_plist(self, label: str, command: str) -> Path:
        path = self.agents / f"{label}.plist"
        job = {"Label": label, "ProgramArguments": ["/bin/bash", "-lc", command]}
        path.write_bytes(plistlib.dumps(job))
        return path

    def tree(self) -> dict[str, bytes | str]:
        """Everything under the home folder, to compare before and after."""
        found: dict[str, bytes | str] = {}
        for path in sorted(self.home.rglob("*")):
            if ".git" in path.parts:
                continue
            if path.is_symlink():
                found[str(path)] = f"link to {os.readlink(path)}"
            elif path.is_file():
                found[str(path)] = path.read_bytes()
            else:
                found[str(path)] = "folder"
        return found

    def run_it(self, *args: str, answers: Sequence[object] = ()) -> tuple[int, str]:
        output = io.StringIO()
        typed = mock.patch("builtins.input", side_effect=list(answers))
        with typed, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = uninstall.main(list(args))
        return status, output.getvalue()


class NothingChanges(World):
    def assert_untouched(self, before: dict[str, bytes | str]) -> None:
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.mac.calls, [])
        self.assertEqual(self.mac.repeating, [WAKE_LINE])

    def test_enter_at_are_you_sure_means_no(self) -> None:
        before = self.tree()
        status, output = self.run_it(answers=[""])
        self.assertEqual(status, 0)
        self.assertIn("Nothing was changed", output)
        self.assert_untouched(before)

    def test_no_means_no(self) -> None:
        before = self.tree()
        self.assertEqual(self.run_it(answers=["n"])[0], 0)
        self.assert_untouched(before)

    def test_anything_else_asks_again(self) -> None:
        before = self.tree()
        self.run_it(answers=["maybe", ""])
        self.assert_untouched(before)

    def test_leaving_at_any_question_changes_nothing(self) -> None:
        for leave in (KeyboardInterrupt(), EOFError()):
            for answers in ([leave], ["y", leave], ["y", "y", leave]):
                before = self.tree()
                status, output = self.run_it(answers=answers)
                self.assertEqual(status, 0)
                self.assertIn("Nothing was changed", output)
                self.assert_untouched(before)

    def test_a_dry_run(self) -> None:
        before = self.tree()
        status, output = self.run_it("--dry-run")
        self.assertEqual(status, 0)
        self.assertIn("It would ask: Keep your ledger", output)
        self.assertIn("dry run", output)
        self.assert_untouched(before)

    def test_no_terminal_without_yes_refuses(self) -> None:
        before = self.tree()
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            status, output = self.run_it()
        self.assertEqual(status, uninstall.EXIT_REFUSED)
        self.assertIn("--yes", output)
        self.assert_untouched(before)

    def test_inside_bosuns_tmux_refuses(self) -> None:
        before = self.tree()
        with mock.patch.dict(os.environ, {"TMUX": "/private/tmp/tmux-501/bosun,123,0"}):
            status, output = self.run_it("--yes")
        self.assertEqual(status, uninstall.EXIT_REFUSED)
        self.assertIn("another terminal", output)
        self.assert_untouched(before)

    def test_another_tmux_is_fine(self) -> None:
        with mock.patch.dict(os.environ, {"TMUX": "/private/tmp/tmux-501/default,123,0"}):
            self.assertEqual(self.run_it("--dry-run")[0], 0)
            self.assertFalse(uninstall.inside_bosun(os.environ))

    def test_sharing_the_default_server_any_tmux_there_counts(self) -> None:
        env = {"TMUX": "/private/tmp/tmux-501/default,123,0", "BOSUN_TMUX_SOCKET": ""}
        self.assertTrue(uninstall.inside_bosun(env))

    def test_a_link_to_another_checkout_refuses(self) -> None:
        other = self.home / "other" / "bin" / "bosun"
        other.parent.mkdir(parents=True)
        other.write_text("#!/bin/sh\n")
        link = self.home / ".local" / "bin" / "bosun"
        link.unlink()
        link.symlink_to(other)
        before = self.tree()
        status, output = self.run_it("--yes")
        self.assertEqual(status, uninstall.EXIT_REFUSED)
        self.assertIn(str(other), output)
        self.assert_untouched(before)


class Uninstalls(World):
    def assert_bosun_is_gone(self) -> None:
        self.assertEqual(self.mac.loaded, set())
        self.assertEqual(list(self.agents.glob("*.plist")), [])
        self.assertEqual(self.mac.repeating, [])
        self.assertFalse((self.home / ".local" / "bin" / "bosun").is_symlink())
        self.assertFalse(self.root.exists())

    def test_keeping_your_records(self) -> None:
        kept = {
            path: self.config.joinpath(path).read_bytes()
            for path in (
                "bosun.env",
                "sessions.yml",
                "setup.sh",
                "knowledge/facts.jsonl",
                "forbidden-terms.txt",
            )
        }
        ledger = (self.state / "ledger.json").read_bytes()
        status, output = self.run_it(answers=["y", "", ""])
        self.assertEqual(status, 0)
        self.assert_bosun_is_gone()
        for path, content in kept.items():
            self.assertEqual(self.config.joinpath(path).read_bytes(), content, path)
        self.assertEqual((self.state / "ledger.json").read_bytes(), ledger)
        self.assertEqual(sorted(e.name for e in self.state.iterdir()), ["ledger.json", "ledger.md"])
        self.assertIn("bosun is uninstalled", output)
        self.assertIn(str(self.config).replace(str(self.home), "~"), output)
        self.assertIn(up.INSTALL_LINE, output)

    def test_keeping_nothing_of_your_records(self) -> None:
        status, _ = self.run_it(answers=["yes", "n", "no"])
        self.assertEqual(status, 0)
        self.assert_bosun_is_gone()
        self.assertFalse(self.state.exists())
        self.assertFalse((self.config / "knowledge").exists())
        self.assertTrue((self.config / "bosun.env").exists())

    def test_yes_asks_nothing_and_keeps_your_records(self) -> None:
        status, _ = self.run_it("--yes")
        self.assertEqual(status, 0)
        self.assert_bosun_is_gone()
        self.assertTrue((self.state / "ledger.json").exists())
        self.assertTrue((self.config / "knowledge" / "facts.jsonl").exists())

    def test_flags_decide_without_a_question(self) -> None:
        status, _ = self.run_it("--delete-ledger", answers=["y", ""])
        self.assertEqual(status, 0)
        self.assertFalse((self.state / "ledger.json").exists())
        self.assertTrue((self.config / "knowledge").exists())

    def test_purge_leaves_only_what_is_not_bosuns(self) -> None:
        (self.config / "mine.txt").write_text("mine\n")
        (self.state / "mystery").write_text("not bosun's\n")
        status, output = self.run_it("--yes", "--purge")
        self.assertEqual(status, 0)
        self.assertEqual(sorted(e.name for e in self.config.iterdir()), ["forbidden-terms.txt", "mine.txt"])
        self.assertEqual([e.name for e in self.state.iterdir()], ["mystery"])
        self.assertIn("mystery", output)
        self.assertIn("forbidden-terms.txt", output)

    def test_purge_with_nothing_else_there_removes_the_folders(self) -> None:
        (self.config / "forbidden-terms.txt").unlink()
        self.run_it("--yes", "--purge")
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state.exists())

    def test_nothing_starts_again_while_it_works(self) -> None:
        self.mac.windows = ["orchestrator", "api"]
        self.run_it("--yes")
        calls = self.mac.calls
        self.assertIn("pmset repeat cancel", calls[0])
        self.assertLess(calls.index(f"bootout {LABEL}.watchdog"), calls.index("bosun stop"))

    def test_what_stopping_writes_goes_too(self) -> None:
        self.mac.windows = ["api"]
        self.run_it("--yes")
        self.assertFalse((self.state / "stopped-bosun").exists())

    def test_a_second_run_finds_nothing(self) -> None:
        self.run_it("--yes")
        status, output = self.run_it("--yes")
        self.assertEqual(status, 0)
        self.assertIn("Nothing of bosun is left", output)
        self.assertIn("your settings", output)

    def test_a_shell_inside_the_checkout_is_told(self) -> None:
        with mock.patch.dict(os.environ, {"PWD": str(self.root)}):
            _, output = self.run_it("--yes")
        self.assertIn("cd somewhere else", output)

    def test_your_own_shell_files_are_untouched(self) -> None:
        for name in (".zshrc", ".tmux.conf"):
            (self.home / name).write_text("mine\n")
        self.run_it("--yes")
        for name in (".zshrc", ".tmux.conf"):
            self.assertEqual((self.home / name).read_text(), "mine\n")


class WhatIsNotBosuns(World):
    def test_a_wake_at_another_time_stays(self) -> None:
        self.mac.repeating[:] = ["wakepoweron at 7:00AM every day"]
        status, output = self.run_it("--yes")
        self.assertEqual(status, 0)
        self.assertEqual(self.mac.repeating, ["wakepoweron at 7:00AM every day"])
        self.assertFalse([call for call in self.mac.calls if "pmset" in call])
        self.assertIn("7:00AM", output)
        self.assertIn(up.CANCEL_WAKE, output)

    def test_a_wake_cancelling_would_take_another_event_with_it_stays(self) -> None:
        self.mac.repeating.append("shutdown at 11:00PM every day")
        self.run_it("--yes")
        self.assertEqual(len(self.mac.repeating), 2)

    def test_a_stranger_s_job_stays(self) -> None:
        theirs = self.write_plist("com.example.backup", "/usr/local/bin/backup")
        self.mac.loaded.add("com.example.backup")
        self.run_it("--yes")
        self.assertTrue(theirs.exists())
        self.assertIn("com.example.backup", self.mac.loaded)

    def test_your_own_job_that_runs_bosun_stays(self) -> None:
        yours = self.write_plist("com.me.daily-report", f"{self.root}/bin/bosun status | mail me")
        self.mac.loaded.add("com.me.daily-report")
        self.run_it("--yes")
        self.assertTrue(yours.exists())
        self.assertIn("com.me.daily-report", self.mac.loaded)

    def test_a_job_running_this_checkout_goes_though_the_label_changed(self) -> None:
        renamed = self.write_plist("com.mine.morning", f'exec "{self.root}/bin/bosun" morning')
        self.mac.loaded.add("com.mine.morning")
        self.run_it("--yes")
        self.assertFalse(renamed.exists())
        self.assertNotIn("com.mine.morning", self.mac.loaded)

    def test_a_loaded_job_with_no_plist_goes(self) -> None:
        self.mac.loaded.add(f"{LABEL}.waketest")
        self.run_it("--yes")
        self.assertNotIn(f"{LABEL}.waketest", self.mac.loaded)

    def test_a_plist_that_is_not_one_is_skipped(self) -> None:
        for name, text in (
            ("not-one.plist", "not a plist"),
            ("cut-short.plist", '<?xml version="1.0"?><plist version="1.0"><dict><key>Lab'),
            ("not-a-dict.plist", plistlib.dumps(["a", "list"]).decode()),
            ("odd.plist", plistlib.dumps({"Label": "x", "ProgramArguments": 3}).decode()),
        ):
            (self.agents / name).write_text(text)
        self.assertEqual(self.run_it("--yes")[0], 0)
        self.assertEqual(
            sorted(path.name for path in self.agents.glob("*.plist")),
            ["cut-short.plist", "not-a-dict.plist", "not-one.plist", "odd.plist"],
        )

    def test_finder_s_file_does_not_keep_a_folder_alive(self) -> None:
        (self.state / ".DS_Store").write_text("")
        self.run_it("--yes", "--delete-ledger")
        self.assertFalse(self.state.exists())

    def test_a_file_it_does_not_know_stays_and_is_named(self) -> None:
        (self.state / "mystery").write_text("x")
        status, output = self.run_it("--yes", "--delete-ledger")
        self.assertEqual(status, 0)
        self.assertTrue((self.state / "mystery").exists())
        self.assertIn("mystery", output)

    def test_a_link_goes_and_what_it_points_at_stays(self) -> None:
        elsewhere = self.home / "dotfiles" / "knowledge"
        elsewhere.mkdir(parents=True)
        (elsewhere / "facts.jsonl").write_text('{"op": "add"}\n')
        target = self.config / "knowledge"
        for child in target.iterdir():
            child.unlink()
        target.rmdir()
        target.symlink_to(elsewhere)
        self.run_it("--yes", "--delete-knowledge")
        self.assertFalse(target.is_symlink())
        self.assertTrue((elsewhere / "facts.jsonl").exists())

    def test_a_state_folder_set_to_home_is_not_touched(self) -> None:
        (self.home / "logs").mkdir()
        (self.home / "logs" / "mine.log").write_text("mine\n")
        with mock.patch.dict(
            os.environ, {"BOSUN_STATE_DIR": str(self.home), "BOSUN_LOG_DIR": str(self.home / "logs")}
        ):
            _, output = self.run_it("--yes", "--purge")
        self.assertTrue((self.home / "logs" / "mine.log").exists())
        self.assertIn("holds your home folder", output)

    def test_a_log_folder_of_its_own_loses_bosun_s_logs(self) -> None:
        logs = self.home / "elsewhere"
        logs.mkdir()
        (logs / "morning-2026-11-02.log").write_text("ran\n")
        (logs / "watchdog.launchd.err").write_text("")
        with mock.patch.dict(os.environ, {"BOSUN_LOG_DIR": str(logs)}):
            self.run_it("--yes")
        self.assertFalse(logs.exists())

    def test_a_log_folder_shared_with_others_is_left_whole(self) -> None:
        logs = self.home / "Library" / "Logs"
        logs.mkdir(parents=True)
        for name in ("morning-2026-11-02.log", "Zoom.log"):
            (logs / name).write_text("ran\n")
        with mock.patch.dict(os.environ, {"BOSUN_LOG_DIR": str(logs)}):
            _, output = self.run_it("--yes")
        self.assertEqual(
            sorted(entry.name for entry in logs.iterdir()), ["Zoom.log", "morning-2026-11-02.log"]
        )
        self.assertIn("Zoom.log", output)

    def test_a_state_folder_set_to_home_under_another_spelling_is_not_touched(self) -> None:
        spelled = str(self.home).swapcase()
        if not os.path.exists(spelled):
            self.skipTest("a file system that tells letter case apart")
        (self.home / "logs").mkdir()
        (self.home / "logs" / "mine.log").write_text("mine\n")
        with mock.patch.dict(os.environ, {"BOSUN_STATE_DIR": spelled, "BOSUN_LOG_DIR": f"{spelled}/logs"}):
            _, output = self.run_it("--yes", "--purge")
        self.assertTrue((self.home / "logs" / "mine.log").exists())
        self.assertNotIn("rm -rf ~", output.replace("rm -rf ~/.config", ""))


class TheCheckout(World):
    identity = ("-c", "user.name=test", "-c", "user.email=test@example.com")

    def git(self, *args: str) -> None:
        real_run(["git", "-C", str(self.root), *self.identity, *args], check=True, capture_output=True)

    def assert_kept(self, why: str) -> None:
        status, output = self.run_it("--yes")
        self.assertEqual(status, 0)
        self.assertTrue((self.root / "bin" / "bosun").exists())
        self.assertIn(why, output)

    def test_stashed_changes_stay(self) -> None:
        (self.root / "bin" / "bosun").write_text("#!/bin/sh\n# mine\n")
        self.git("stash", "-q")
        self.assert_kept("stashed changes")

    def test_commits_on_a_detached_head_stay(self) -> None:
        self.git("checkout", "-q", "--detach")
        self.git("commit", "-q", "--allow-empty", "-m", "mine")
        self.assert_kept("commits not pushed")

    def test_files_git_ignores_stay(self) -> None:
        (self.root / ".gitignore").write_text(".env\n__pycache__/\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore")
        self.git("push", "-q", "origin", "main")
        (self.root / ".env").write_text("TOKEN=x\n")
        self.assert_kept(".env")

    def test_caches_from_running_it_are_not_work(self) -> None:
        (self.root / ".gitignore").write_text("__pycache__/\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore")
        self.git("push", "-q", "origin", "main")
        (self.root / "lib" / "__pycache__").mkdir(parents=True)
        (self.root / "lib" / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"")
        self.assertEqual(self.run_it("--yes")[0], 0)
        self.assertFalse(self.root.exists())

    def test_another_worktree_stays(self) -> None:
        self.git("worktree", "add", "-q", str(self.home.parent / "elsewhere"), "-b", "mine")
        self.assert_kept("commits not pushed" if False else "worktree")

    def test_git_that_cannot_answer_counts_as_work(self) -> None:
        (self.root / ".git" / "index").write_bytes(b"not an index")
        self.assert_kept("could not be checked")

    def test_a_state_folder_inside_it_keeps_it(self) -> None:
        (self.root / ".gitignore").write_text("state/\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore")
        self.git("push", "-q", "origin", "main")
        inside = self.root / "state"
        inside.mkdir()
        (inside / "ledger.json").write_text(json.dumps({"items": {"a": {}}}))
        with mock.patch.dict(
            os.environ, {"BOSUN_STATE_DIR": str(inside), "BOSUN_LOG_DIR": str(inside / "logs")}
        ):
            self.assert_kept("state")
        self.assertTrue((inside / "ledger.json").exists())

    def test_one_you_made_stays(self) -> None:
        real_run(["git", "-C", str(self.root), "config", "--unset", "bosun.managed"], check=True)
        status, output = self.run_it("--yes")
        self.assertEqual(status, 0)
        self.assertTrue(self.root.exists())
        self.assertIn("not made by the installer", output)

    def test_uncommitted_changes_stay(self) -> None:
        (self.root / "bin" / "bosun").write_text("#!/bin/sh\n# edited\n")
        _, output = self.run_it("--yes")
        self.assertTrue(self.root.exists())
        self.assertIn("uncommitted changes", output)

    def test_commits_not_pushed_stay(self) -> None:
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.com"]
        real_run(
            ["git", "-C", str(self.root), *identity, "commit", "-q", "--allow-empty", "-m", "mine"],
            check=True,
        )
        _, output = self.run_it("--yes")
        self.assertTrue(self.root.exists())
        self.assertIn("commits not pushed", output)


class WhenItCannot(World):
    def test_a_job_that_will_not_go_is_reported_and_the_rest_done(self) -> None:
        self.mac.stuck_jobs.add(LABEL)
        status, output = self.run_it("--yes")
        self.assertEqual(status, uninstall.EXIT_LEFT)
        self.assertNotIn("bosun is uninstalled", output)
        self.assertIn(f"launchctl bootout gui/$(id -u)/{LABEL}", output)
        self.assertFalse((self.home / ".local" / "bin" / "bosun").is_symlink())
        self.assertFalse((self.state / "logs").exists())
        self.assertTrue(self.root.exists(), "the way to run it again stays")

    def test_running_it_again_finishes_the_job(self) -> None:
        self.mac.stuck_jobs.add(LABEL)
        self.run_it("--yes")
        self.mac.stuck_jobs.clear()
        status, output = self.run_it("--yes")
        self.assertEqual(status, 0)
        self.assertIn("bosun is uninstalled", output)
        self.assertFalse(self.root.exists())

    def test_no_sudo_says_what_to_run(self) -> None:
        self.mac.sudo_works = False
        status, output = self.run_it("--yes")
        self.assertEqual(status, uninstall.EXIT_LEFT)
        self.assertEqual(self.mac.repeating, [WAKE_LINE])
        self.assertIn(up.CANCEL_WAKE, output)
        self.assertEqual(self.mac.loaded, set())

    def test_without_a_terminal_sudo_does_not_wait_for_a_password(self) -> None:
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            self.run_it("--yes")
        self.assertIn("sudo -n pmset repeat cancel", self.mac.calls)

    def test_with_one_it_may_ask(self) -> None:
        self.run_it(answers=["y", "", ""])
        self.assertIn("sudo pmset repeat cancel", self.mac.calls)

    def test_a_file_that_will_not_go_is_reported(self) -> None:
        real_unlink = Path.unlink

        def refuse(path: Path, missing_ok: bool = False) -> None:
            if path.name == "ledger.lock":
                raise PermissionError("not allowed")
            real_unlink(path, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", refuse):
            status, output = self.run_it("--yes")
        self.assertEqual(status, uninstall.EXIT_LEFT)
        self.assertIn("ledger.lock", output)
        self.assertIn("not allowed", output)

    def test_ctrl_c_part_way_says_how_to_finish(self) -> None:
        with mock.patch.object(uninstall_macos, "cancel_wake", side_effect=KeyboardInterrupt):
            status, output = self.run_it("--yes")
        self.assertEqual(status, uninstall.EXIT_LEFT)
        self.assertIn("again finishes the job", output)


class AgentsFiles(World):
    def test_only_the_trust_bosun_gave_is_taken_back(self) -> None:
        mine = self.home / "Development" / "mine"
        given = self.home / "Development" / "given"
        for folder in (mine, given):
            folder.mkdir(parents=True)
        claude_state.set_trust(str(mine), True)  # you accepted the prompt yourself
        for folder in (mine, given):
            agent_state.main(["set-trust", "claude", str(folder), "true"])
        agent_state.main(["set-trust", "codex", str(given), "true"])
        self.assertEqual(
            sorted(agent_changes.trust_given(self.state)),
            [("claude", str(given)), ("codex", str(given))],
        )
        self.run_it("--yes")
        self.assertTrue(claude_state.trusted(str(mine)))
        self.assertFalse(claude_state.trusted(str(given)))
        self.assertFalse(codex_state.trusted(str(given)))

    def test_the_rest_of_an_agent_s_config_stays(self) -> None:
        folder = self.home / "Development" / "api"
        folder.mkdir(parents=True)
        (self.home / ".claude.json").write_text(
            json.dumps({"hasCompletedOnboarding": True, "numStartups": 9})
        )
        agent_state.main(["set-trust", "claude", str(folder), "true"])
        self.run_it("--yes")
        config = json.loads((self.home / ".claude.json").read_text())
        self.assertEqual(config, {"hasCompletedOnboarding": True, "numStartups": 9, "projects": {}})

    def test_what_the_agent_knows_of_the_folder_stays(self) -> None:
        folder = self.home / "Development" / "api"
        folder.mkdir(parents=True)
        key = str(folder)
        (self.home / ".claude.json").write_text(json.dumps({"projects": {key: {"lastCost": 1}}}))
        agent_state.main(["set-trust", "claude", key, "true"])
        self.run_it("--yes")
        self.assertEqual(
            json.loads((self.home / ".claude.json").read_text())["projects"][key], {"lastCost": 1}
        )

    def test_a_folder_since_deleted_is_no_trouble(self) -> None:
        folder = self.home / "Development" / "gone"
        folder.mkdir(parents=True)
        agent_state.main(["set-trust", "claude", str(folder), "true"])
        folder.rmdir()
        self.assertEqual(self.run_it("--yes")[0], 0)
        self.assertFalse(claude_state.trusted(str(folder)))

    def test_revoking_by_hand_forgets_it(self) -> None:
        folder = self.home / "Development" / "api"
        folder.mkdir(parents=True)
        agent_state.main(["set-trust", "claude", str(folder), "true"])
        agent_state.main(["set-trust", "claude", str(folder), "false"])
        self.assertEqual(agent_changes.trust_given(self.state), ())

    def test_trust_you_gave_after_bosun_revoked_stays(self) -> None:
        folder = self.home / "Development" / "api"
        folder.mkdir(parents=True)
        agent_state.main(["set-trust", "claude", str(folder), "true"])
        agent_state.main(["set-trust", "claude", str(folder), "false"])
        claude_state.set_trust(str(folder), True)
        self.run_it("--yes")
        self.assertTrue(claude_state.trusted(str(folder)))

    def test_nothing_is_recorded_without_a_state_folder(self) -> None:
        folder = self.home / "Development" / "api"
        folder.mkdir(parents=True)
        with mock.patch.dict(os.environ):
            del os.environ["BOSUN_STATE_DIR"]
            agent_state.main(["set-trust", "claude", str(folder), "true"])
        self.assertEqual(agent_changes.trust_given(self.state), ())

    def test_memory_notes_bosun_wrote_go_and_yours_stay(self) -> None:
        workdir = str(self.home / "Development" / "api")
        note = claude_state.add_memory_note(workdir, "bosun-rule", "A rule learned", "Do this.\n")
        agent_changes.record_note(self.state, note)
        own = claude_state.add_memory_note(
            workdir, "bosun-mine", "My own note", "Named like one, but mine.\n"
        )
        status, output = self.run_it("--yes")
        self.assertEqual(status, 0)
        self.assertFalse(note.exists())
        self.assertTrue(own.exists())
        index = (note.parent / claude_state.MEMORY_INDEX).read_text()
        self.assertNotIn("A rule learned", index)
        self.assertIn("My own note", index)
        self.assertIn("memory note", output)

    def test_a_note_already_gone_is_no_trouble(self) -> None:
        agent_changes.record_note(self.state, self.home / "no-such-note.md")
        self.assertEqual(self.run_it("--yes")[0], 0)

    def test_the_record_stays_until_what_it_lists_is_gone(self) -> None:
        folder = self.home / "Development" / "api"
        folder.mkdir(parents=True)
        agent_state.main(["set-trust", "claude", str(folder), "true"])
        with mock.patch.object(claude_state, "set_trust", side_effect=PermissionError("locked")):
            status, _ = self.run_it("--yes")
        self.assertEqual(status, uninstall.EXIT_LEFT)
        self.assertEqual(agent_changes.trust_given(self.state), (("claude", str(folder)),))
        self.assertEqual(self.run_it("--yes")[0], 0)
        self.assertFalse(claude_state.trusted(str(folder)))

    def test_an_agent_config_it_cannot_read_is_not_taken_for_untrusted(self) -> None:
        folder = self.home / "Development" / "api"
        folder.mkdir(parents=True)
        agent_state.main(["set-trust", "claude", str(folder), "true"])
        (self.home / ".claude.json").write_text("{ cut short")
        status, output = self.run_it("--yes")
        self.assertEqual(status, uninstall.EXIT_LEFT)
        self.assertNotIn("bosun is uninstalled", output)
        self.assertEqual(agent_changes.trust_given(self.state), (("claude", str(folder)),))

    def test_trust_that_will_not_go_is_not_forgotten(self) -> None:
        folder = self.home / "Development" / "api"
        folder.mkdir(parents=True)
        agent_state.main(["set-trust", "claude", str(folder), "true"])
        with mock.patch.object(claude_state, "set_trust"):  # does nothing
            status, output = self.run_it("--yes")
        self.assertEqual(status, uninstall.EXIT_LEFT)
        self.assertIn("still trusts it", output)
        self.assertEqual(agent_changes.trust_given(self.state), (("claude", str(folder)),))

    def test_a_record_of_any_shape_is_no_crash(self) -> None:
        for record in (
            '{"trust": null, "memory_notes": 5}',
            '{"trust": [["claude"], [1], "x", ["claude", 2]], "memory_notes": [null, 3]}',
            '{"trust": {}}',
            "[1, 2]",
            b"\xff\xfe not text",
        ):
            path = self.state / agent_changes.FILE
            path.write_bytes(record if isinstance(record, bytes) else record.encode())
            self.assertEqual(agent_changes.trust_given(self.state), (), record)
            self.assertEqual(agent_changes.notes(self.state), (), record)
        folder = self.home / "Development" / "api"
        folder.mkdir(parents=True)
        (self.state / agent_changes.FILE).write_text('{"trust": {}}')
        self.assertEqual(agent_state.main(["set-trust", "claude", str(folder), "true"]), 0)
        self.assertEqual(agent_changes.trust_given(self.state), (("claude", str(folder)),))
        self.assertEqual(self.run_it("--yes")[0], 0)

    def test_a_step_that_crashes_does_not_stop_the_others(self) -> None:
        workdir = str(self.home / "Development" / "api")
        note = claude_state.add_memory_note(workdir, "bosun-rule", "A rule", "Do this.\n")
        agent_changes.record_note(self.state, note)
        (note.parent / claude_state.MEMORY_INDEX).write_bytes(b"\xff\xfe not text")
        status, output = self.run_it("--yes")
        self.assertEqual(status, uninstall.EXIT_LEFT)
        self.assertFalse((self.state / "logs").exists())
        self.assertEqual(self.mac.loaded, set())
        self.assertIn("could not remove the memory note", output)

    def test_a_record_it_cannot_read_means_nothing_is_taken_back(self) -> None:
        (self.state / agent_changes.FILE).write_text("not json")
        self.assertEqual(agent_changes.trust_given(self.state), ())
        self.assertEqual(self.run_it("--yes")[0], 0)


class Detect(World):
    def facts(self) -> up.Facts:
        return uninstall_detect.detect(os.environ, self.root)

    def test_what_is_here(self) -> None:
        self.mac.windows = ["orchestrator", "my api"]
        found = self.facts()
        self.assertEqual(found.windows, ("orchestrator", "my api"))
        self.assertEqual(found.wake_at, "9:01AM")
        self.assertEqual(found.repeating, (WAKE_LINE,))
        self.assertEqual(found.ledger_items, 2)
        self.assertEqual(found.knowledge_records, 3)
        self.assertTrue(found.checkout.managed)
        self.assertEqual({job.label for job in found.jobs}, self.mac.loaded)

    def test_it_only_reads(self) -> None:
        before = self.tree()
        self.facts()
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.mac.calls, [])

    def test_a_ledger_it_cannot_read_counts_as_something(self) -> None:
        for content in (b"{ cut short", b"\xff\xfe not text"):
            (self.state / "ledger.json").write_bytes(content)
            self.assertEqual(self.facts().ledger_items, up.UNREADABLE)

    def test_the_backup_counts_when_it_holds_more(self) -> None:
        backup = self.state / "ledger.json.bak"
        backup.write_text(json.dumps({"items": {"a": {}, "b": {}, "c": {}}}))
        (self.state / "ledger.json").write_text(json.dumps({"items": {}}))
        self.assertEqual(self.facts().ledger_items, 3)
        self.run_it("--yes")
        self.assertTrue(backup.exists())

    def test_knowledge_it_cannot_read_counts_as_something(self) -> None:
        (self.config / "knowledge" / "facts.jsonl").write_bytes(b"\xff\xfe")
        self.assertEqual(self.facts().knowledge_records, up.UNREADABLE)
        self.run_it("--yes")
        self.assertTrue((self.config / "knowledge" / "facts.jsonl").exists())

    def test_a_knowledge_folder_with_only_a_schema_is_kept(self) -> None:
        for name in ("entities.jsonl", "facts.jsonl"):
            (self.config / "knowledge" / name).unlink()
        (self.config / "knowledge" / "schema.json").write_text("{}")
        self.run_it("--yes")
        self.assertTrue((self.config / "knowledge" / "schema.json").exists())

    def test_nothing_installed(self) -> None:
        self.mac.loaded.clear()
        self.mac.repeating.clear()
        for path in self.agents.glob("*.plist"):
            path.unlink()
        (self.home / ".local" / "bin" / "bosun").unlink()
        found = self.facts()
        self.assertEqual(found.jobs, ())
        self.assertEqual(found.link, "")

    def test_a_link_to_nothing_is_this_one_to_clean_up(self) -> None:
        link = self.home / ".local" / "bin" / "bosun"
        link.unlink()
        link.symlink_to(self.home / "no-such" / "bosun")
        self.assertEqual(self.facts().link, str(link))
        self.assertEqual(uninstall_detect.other_install(self.root), "")

    def test_an_old_tmuxinator_project_goes(self) -> None:
        project = self.home / ".config" / "tmuxinator" / "bosun.yml"
        project.parent.mkdir(parents=True)
        project.write_text("# bosun tmuxinator project\n")
        mine = project.with_name("mine.yml")
        mine.write_text("mine\n")
        self.assertEqual(self.facts().old_files, (str(project),))
        self.run_it("--yes")
        self.assertFalse(project.exists())
        self.assertTrue(mine.exists())

    def test_a_tmuxinator_project_of_that_name_that_is_yours_stays(self) -> None:
        project = self.home / ".config" / "tmuxinator" / "bosun.yml"
        project.parent.mkdir(parents=True)
        project.write_text("mine\n")
        self.assertEqual(self.facts().old_files, ())


class RepeatingEvents(unittest.TestCase):
    SCHEDULE = (
        "Repeating power events:\n  wakepoweron at 9:01AM every day\n  shutdown at 11:00PM weekdays only\n"
        "Scheduled power events:\n [0]  wake at 11/03/2026 09:00:00 by 'pmset'\n"
    )

    def test_all_of_them_and_no_one_off(self) -> None:
        self.assertEqual(
            init_detect.repeating_events(self.SCHEDULE),
            ("wakepoweron at 9:01AM every day", "shutdown at 11:00PM weekdays only"),
        )

    def test_the_wake_among_them(self) -> None:
        self.assertEqual(init_detect.repeating_wake(self.SCHEDULE), "wakepoweron at 9:01AM every day")

    def test_none(self) -> None:
        self.assertEqual(init_detect.repeating_events(""), ())
        self.assertEqual(init_detect.repeating_wake("Scheduled power events:\n [0]  wake at x\n"), "")


if __name__ == "__main__":
    unittest.main()
