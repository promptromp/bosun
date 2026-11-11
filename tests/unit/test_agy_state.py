"""lib/agy_state.py's screen readers, over captures of real Antigravity CLI (agy) 1.5.5 in
tests/fixtures/agy/screens/ (`.ansi` as `tmux capture-pane -e` gives them, `.txt` plain)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_files
import agy_state
from screen_text import SGR


SCREENS = Path(__file__).resolve().parents[1] / "fixtures" / "agy" / "screens"


def ansi(name: str) -> list[str]:
    """The lines of a capture with its escape sequences, as `typed` gets them."""
    return (SCREENS / f"{name}.ansi").read_text(encoding="utf-8").splitlines()


def plain(name: str) -> str:
    """A capture without escape sequences, as `question` gets it."""
    if (SCREENS / f"{name}.txt").exists():
        return (SCREENS / f"{name}.txt").read_text(encoding="utf-8")
    return SGR.sub("", "\n".join(ansi(name)))


class TypedTextTest(unittest.TestCase):
    def test_empty_box(self) -> None:
        self.assertEqual(agy_state.agy_typed_text(ansi("idle")), "")

    def test_text_in_the_box_over_two_lines(self) -> None:
        self.assertEqual(
            agy_state.agy_typed_text(ansi("typed")),
            "Remember the word PELICAN for later.\nReply with just the word noted.",
        )

    def test_the_last_box_is_the_one_not_a_message_in_the_history(self) -> None:
        self.assertEqual(agy_state.agy_typed_text(ansi("busy")), "")

    def test_a_footer_hint_is_not_typed_text(self) -> None:
        self.assertEqual(agy_state.agy_typed_text(ansi("task")), "")

    def test_no_box(self) -> None:
        self.assertIsNone(agy_state.agy_typed_text(["no box here", "> not between rules"]))

    def test_a_box_no_rule_closes_is_no_box(self) -> None:
        rule = "\x1b[90m" + "\u2500" * 40
        self.assertIsNone(agy_state.agy_typed_text([rule, "> old", "reply"]))
        self.assertIsNone(agy_state.agy_typed_text([rule, "> old", rule]))
        self.assertEqual(agy_state.agy_typed_text([rule, "> old", rule, "? for shortcuts"]), "old")

    def test_no_box_on_a_dialog_or_the_trust_and_sign_in_screens(self) -> None:
        for name in ("dialog", "trust", "sign-in"):
            with self.subTest(name):
                self.assertIsNone(agy_state.agy_typed_text(ansi(name)))

    def test_plain_capture_of_an_empty_box(self) -> None:
        self.assertEqual(agy_state.agy_typed_text(plain("cancelled").splitlines()), "")


class QuestionTest(unittest.TestCase):
    def test_dialog_trust_and_sign_in_are_questions(self) -> None:
        for name in ("dialog", "trust", "sign-in"):
            with self.subTest(name):
                self.assertTrue(agy_state.agy_question(plain(name)))

    def test_other_screens_are_not(self) -> None:
        for name in ("idle", "typed", "busy", "queued", "task", "cancelled"):
            with self.subTest(name):
                self.assertFalse(agy_state.agy_question(plain(name)))


class ScreenStatusTest(unittest.TestCase):
    def test_each_capture(self) -> None:
        expected = {
            "idle": "idle",
            "typed": None,
            "busy": None,
            "dialog": "waiting",
            "queued": None,
            "task": "shell",
            "cancelled": "idle",
        }
        for name, status in expected.items():
            with self.subTest(name):
                self.assertEqual(agy_state.screen_status(plain(name)), status)

    def test_a_capture_with_escapes_reads_the_same(self) -> None:
        self.assertEqual(agy_state.screen_status("\n".join(ansi("idle"))), "idle")
        self.assertIsNone(agy_state.screen_status("\n".join(ansi("typed"))))

    def test_no_box_is_no_status(self) -> None:
        self.assertIsNone(agy_state.screen_status("starting up\n"))
        self.assertIsNone(agy_state.screen_status(""))


class ScreenTest(unittest.TestCase):
    def test_the_readers_agy_hands_out(self) -> None:
        self.assertIs(agy_state.SCREEN.typed, agy_state.agy_typed_text)
        self.assertIs(agy_state.SCREEN.question, agy_state.agy_question)
        self.assertIs(agy_state.SCREEN.rejected, agent_files.never_rejects)


SOCKET = "/private/tmp/tmux-501/bosun"


def command_of(name: str, *flags: str, agy: str = "/opt/bin/agy", pane: str = "%3") -> str:
    """What `ps eww -o command=` prints for an agy window: its command line, then its environment."""
    environment = (
        f"BOSUN_WINDOW={name} BOSUN_CONVERSATION={name} TMUX={SOCKET},4242,0 TMUX_PANE={pane} HOME=/Users/me"
    )
    return f"{agy} --log-file /state/logs/agy-{name}.log {' '.join(flags)} {environment}"


class AdapterCase(unittest.TestCase):
    """A scratch conversations folder and state folder, with ps and tmux faked."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.cli_home = self.root / "antigravity-cli"
        self.state = self.root / "state"
        (self.cli_home / "conversations").mkdir(parents=True)
        self.processes: dict[int, str] = {}
        self.screens: dict[str, str | None] = {}
        self.captured: list[tuple[str, str]] = []
        self.clock = 1_700_000_000_000
        patches = (
            mock.patch.object(agy_state, "CLI_HOME", self.cli_home),
            mock.patch.object(agy_state, "HOME", self.root),
            mock.patch.object(agy_state, "process_table", self.process_table),
            mock.patch.object(agy_state, "process_environment", self.process_environment),
            mock.patch.object(agy_state, "capture_pane", self.capture_pane),
            mock.patch.object(agy_state, "now_ms", lambda: self.clock),
            mock.patch.dict(os.environ, {"BOSUN_STATE_DIR": str(self.state)}),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop(agy_state.BIN_KEY, None)

    def process_table(self) -> list[tuple[int, str]]:
        return [(pid, command.split(" BOSUN_WINDOW=")[0]) for pid, command in self.processes.items()]

    def process_environment(self, pid: int | str) -> str:
        return self.processes.get(int(pid), "")

    def capture_pane(self, socket: str, pane: str) -> str | None:
        self.captured.append((socket, pane))
        return self.screens.get(pane)

    def conversation(self, conversation_id: str) -> None:
        (self.cli_home / "conversations" / f"{conversation_id}.db").write_text("")

    def window(self, pid: int, name: str, status: str | None, *flags: str, pane: str | None = None) -> None:
        """A running agy window <name>, its status file saying <status> (None: no status file yet)."""
        self.processes[pid] = command_of(name, *flags, pane=pane or f"%{pid}")
        if status is not None:
            agy_state.write_status(
                name, status=status, statusUpdatedAt=1000, conversationId=f"id-{name}", version="1.5.5"
            )


class ResolveTest(AdapterCase):
    def test_resolve_needs_the_conversation_on_disk(self) -> None:
        workdir = str(self.root)
        agy_state.remember("repo-a", "abc123", workdir)
        self.assertIsNone(agy_state.resolve(workdir, "repo-a"))
        self.conversation("abc123")
        self.assertEqual(agy_state.resolve(workdir, "repo-a"), "abc123")
        self.assertIsNone(agy_state.resolve("/elsewhere", "repo-a"))
        self.assertIsNone(agy_state.resolve(workdir, "unknown"))
        self.assertEqual(agy_state.known_names()["repo-a"]["id"], "abc123")


class RecordTest(AdapterCase):
    def test_record_comes_from_the_status_file(self) -> None:
        self.window(11, "repo-a", "idle")
        agy_state.remember("repo-a", "id-repo-a", "/work/a")
        self.assertEqual(
            agy_state.record(11),
            {
                "pid": 11,
                "sessionId": "id-repo-a",
                "name": "repo-a",
                "status": "idle",
                "statusUpdatedAt": 1000,
                "version": "1.5.5",
                "cwd": os.path.realpath("/work/a"),
                "transcript": str(self.cli_home / "conversations" / "id-repo-a.db"),
            },
        )
        self.assertEqual(agy_state.describe("11"), agy_state.record(11))
        self.assertEqual(self.captured, [])  # an idle status is taken as it is

    def test_a_window_before_its_first_prompt_has_no_record(self) -> None:
        self.window(12, "repo-b", None)
        self.assertIsNone(agy_state.record(12))
        agy_state.write_status("repo-b", version="1.5.5")  # the launch's own write: no conversation yet
        self.assertIsNone(agy_state.record(12))
        self.assertIsNone(agy_state.record(999))  # not a process
        self.assertIsNone(agy_state.record("x"))
        self.assertIsNone(agy_state.registered(["12", "999", "x"]))

    def test_a_process_that_is_not_agy_has_no_marker(self) -> None:
        self.processes[13] = command_of("repo-c", agy="/usr/bin/vim")
        agy_state.write_status("repo-c", status="idle", conversationId="id")
        self.assertIsNone(agy_state.marker(13))
        self.assertIsNone(agy_state.record(13))

    def test_the_marker_names_the_conversation_and_the_pane(self) -> None:
        self.window(14, "repo-d", "idle", pane="%9")
        self.assertEqual(agy_state.marker(14), agy_state.Marker("repo-d", SOCKET, "%9"))
        self.processes[15] = "/opt/bin/agy --log-file /state/logs/other.log"
        self.assertIsNone(agy_state.marker(15))

    def test_two_windows_in_one_repo_keep_their_own_status(self) -> None:
        self.window(21, "repo-a", "idle")
        self.window(22, "repo-a-review", "busy")
        statuses = {entry["name"]: entry["status"] for entry in agy_state.running()}
        self.assertEqual(statuses, {"repo-a": "idle", "repo-a-review": "busy"})
        self.assertEqual(agy_state.live_status("repo-a-review"), "busy")
        self.assertEqual(agy_state.live_pid("id-repo-a"), 21)
        self.assertEqual(agy_state.live_pid("id-repo-a-review"), 22)
        self.assertIsNone(agy_state.live_pid("nobody"))
        self.assertEqual(agy_state.registered(["999", "22"]), "22")

    def test_running_skips_what_is_not_an_agy_window(self) -> None:
        self.window(31, "repo-a", "idle")
        self.processes[32] = "/usr/bin/vim notes.txt"
        self.processes[33] = "/opt/bin/agy --version"
        self.assertEqual([entry["pid"] for entry in agy_state.running()], [31])


class BusyWindowTest(AdapterCase):
    def test_a_busy_window_whose_screen_is_idle_is_idle(self) -> None:
        self.window(41, "repo-a", "busy")
        self.screens["%41"] = plain("cancelled")
        self.clock = 1_700_000_005_000
        entry = agy_state.record(41)
        assert entry is not None
        self.assertEqual((entry["status"], entry["statusUpdatedAt"]), ("idle", 1_700_000_005_000))
        self.assertEqual(self.captured, [(SOCKET, "%41")])
        stored = agy_state.read_status("repo-a")
        self.assertEqual((stored["status"], stored["statusUpdatedAt"]), ("idle", 1_700_000_005_000))
        self.assertEqual(stored["conversationId"], "id-repo-a")  # what hooks wrote stays
        self.assertEqual(stored["version"], "1.5.5")

    def test_a_background_task_on_the_screen_is_shell(self) -> None:
        self.window(42, "repo-a", "busy")
        self.screens["%42"] = plain("task")
        entry = agy_state.record(42)
        assert entry is not None
        self.assertEqual(entry["status"], "shell")
        self.assertEqual(agy_state.read_status("repo-a")["status"], "shell")

    def test_a_busy_window_at_a_dialog_is_waiting(self) -> None:
        self.window(43, "repo-a", "busy")
        self.screens["%43"] = plain("dialog")
        entry = agy_state.record(43)
        assert entry is not None
        self.assertEqual(entry["status"], "waiting")
        stored = agy_state.read_status("repo-a")
        # not written: the next look reads the screen again
        self.assertEqual((stored["status"], stored["statusUpdatedAt"]), ("busy", 1000))

    def test_a_busy_window_that_is_still_working_is_busy(self) -> None:
        self.window(44, "repo-a", "busy")
        self.screens["%44"] = plain("busy")
        entry = agy_state.record(44)
        assert entry is not None
        self.assertEqual(entry["status"], "busy")
        self.assertEqual(agy_state.read_status("repo-a")["statusUpdatedAt"], 1000)

    def test_a_busy_status_with_no_screen_stays_busy(self) -> None:
        self.window(45, "repo-a", "busy")  # the capture fails: no screen for %45
        entry = agy_state.record(45)
        assert entry is not None
        self.assertEqual(entry["status"], "busy")
        self.assertEqual(agy_state.read_status("repo-a")["status"], "busy")
        # no tmux in its environment
        self.processes[46] = "/opt/bin/agy --log-file /state/logs/agy-repo-b.log"
        agy_state.write_status("repo-b", status="busy", conversationId="id-b")
        entry = agy_state.record(46)
        assert entry is not None
        self.assertEqual(entry["status"], "busy")

    def test_only_a_busy_status_reads_the_screen(self) -> None:
        for pid, status in ((51, "idle"), (52, "shell"), (53, "waiting")):
            self.window(pid, f"repo-{pid}", status)
            self.screens[f"%{pid}"] = plain("dialog")
            entry = agy_state.record(pid)
            assert entry is not None
            self.assertEqual(entry["status"], status)
        self.assertEqual(self.captured, [])


class StatusFileTest(AdapterCase):
    def test_write_merges_into_what_is_there(self) -> None:
        agy_state.write_status("repo-a", version="1.5.5")
        agy_state.write_status("repo-a", status="busy", conversationId="x", statusUpdatedAt=5)
        agy_state.write_status("repo-a", status="idle")
        self.assertEqual(
            agy_state.read_status("repo-a"),
            {"version": "1.5.5", "status": "idle", "conversationId": "x", "statusUpdatedAt": 5},
        )
        path = self.state / agy_state.STATUS_DIR / "repo-a.json"
        self.assertEqual(json.loads(path.read_text())["status"], "idle")
        self.assertEqual(agy_state.read_status("nobody"), {})


class LaunchSettingsTest(AdapterCase):
    def test_launch_settings_read_the_command_line(self) -> None:
        self.window(61, "repo-a", "idle", "--model", "flash-3", "--effort", "high")
        self.assertEqual(agy_state.launch_settings("61"), agent_files.LaunchSettings("flash-3", "high", True))
        self.window(62, "repo-b", "idle", "--model=pro-3")
        self.assertEqual(agy_state.launch_settings("62"), agent_files.LaunchSettings("pro-3", None, True))
        self.processes[63] = "/opt/bin/agy --model pro-3"  # started by hand: no bosun marker
        self.assertEqual(agy_state.launch_settings("63"), agent_files.LaunchSettings("pro-3", None, False))
        self.assertEqual(agy_state.launch_settings("999"), agent_files.LaunchSettings(None, None, False))

    def test_the_command_is_the_one_the_setting_names(self) -> None:
        self.processes[64] = command_of("repo-a", agy="/opt/bin/my-agy")
        self.assertIsNone(agy_state.marker(64))
        with mock.patch.dict(os.environ, {agy_state.BIN_KEY: "/elsewhere/my-agy"}):
            self.assertEqual(agy_state.marker(64), agy_state.Marker("repo-a", SOCKET, "%3"))


class LoginTest(AdapterCase):
    def onboarding(self, text: str) -> None:
        (self.cli_home / "cache").mkdir(exist_ok=True)
        (self.cli_home / "cache" / "onboarding.json").write_text(text)

    def test_logged_in_needs_the_keychain_item_and_onboarding(self) -> None:
        self.onboarding('{"onboardingComplete": true}')
        with mock.patch.object(agy_state, "keychain_item", return_value=True):
            self.assertTrue(agy_state.logged_in())
        with mock.patch.object(agy_state, "keychain_item", return_value=False):
            self.assertFalse(agy_state.logged_in())
        for text in ('{"onboardingComplete": false}', "{}", "not json", "[]"):
            self.onboarding(text)
            with mock.patch.object(agy_state, "keychain_item", return_value=True):
                self.assertFalse(agy_state.logged_in(), text)
        (self.cli_home / "cache" / "onboarding.json").unlink()
        with mock.patch.object(agy_state, "keychain_item", return_value=True):
            self.assertFalse(agy_state.logged_in())

    def test_the_keychain_is_asked_without_reading_the_secret(self) -> None:
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertTrue(agy_state.keychain_item())
        command = run.call_args.args[0]
        self.assertEqual(command, ["security", "find-generic-password", "-s", "gemini"])
        self.assertFalse({"-w", "-g"} & set(command))
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 44)):
            self.assertFalse(agy_state.keychain_item())
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertFalse(agy_state.keychain_item())

    def test_ready_needs_the_command_and_the_login(self) -> None:
        with mock.patch.object(agy_state, "logged_in", return_value=True):
            with mock.patch("shutil.which", return_value="/opt/bin/agy"):
                self.assertTrue(agy_state.ready())
            with mock.patch("shutil.which", return_value=None):
                self.assertFalse(agy_state.ready())
        with (
            mock.patch.object(agy_state, "logged_in", return_value=False),
            mock.patch("shutil.which", return_value="/opt/bin/agy"),
        ):
            self.assertFalse(agy_state.ready())


class TrustAndHistoryTest(AdapterCase):
    def test_trust_is_never_written(self) -> None:
        self.assertTrue(agy_state.trusted("/work/a"))
        self.assertTrue(agy_state.trust_recorded("/work/a"))
        agy_state.set_trust("/work/a", True)
        agy_state.set_trust("/work/a", False)
        self.assertEqual(list(self.cli_home.rglob("*.json")), [])
        self.assertFalse(self.state.exists())

    def test_the_rest_of_the_protocol_has_nothing_to_say(self) -> None:
        self.window(71, "repo-a", "idle")
        self.assertIsNone(agy_state.conversation_size("71"))
        self.assertEqual(list(agy_state.prompt_history(datetime(2026, 1, 1))), [])


class CommandLineTest(AdapterCase):
    def run_main(self, *argv: str) -> tuple[int, str]:
        with mock.patch("builtins.print") as printed:
            code = agy_state.main(list(argv))
        return code, "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)

    def test_resolve_and_live_print_or_exit_3(self) -> None:
        workdir = str(self.root)
        agy_state.remember("repo-a", "abc", workdir)
        self.assertEqual(self.run_main("resolve", workdir, "repo-a"), (agent_files.NOT_FOUND, ""))
        self.conversation("abc")
        self.assertEqual(self.run_main("resolve", workdir, "repo-a"), (0, "abc"))
        self.window(81, "repo-a", "idle")
        agy_state.write_status("repo-a", conversationId="abc")
        self.assertEqual(self.run_main("live", "abc"), (0, "81"))
        self.assertEqual(self.run_main("live", "zzz"), (agent_files.NOT_FOUND, ""))

    def test_logged_in_exits_0_or_3(self) -> None:
        with mock.patch.object(agy_state, "logged_in", return_value=True):
            self.assertEqual(self.run_main("logged-in")[0], 0)
        with mock.patch.object(agy_state, "logged_in", return_value=False):
            self.assertEqual(self.run_main("logged-in")[0], agent_files.NOT_FOUND)

    def test_a_usage_error_is_exit_2(self) -> None:
        with mock.patch("sys.stderr"):
            self.assertEqual(agy_state.main(["resolve", "one"]), agent_files.USAGE)


if __name__ == "__main__":
    unittest.main()
