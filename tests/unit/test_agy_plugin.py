"""bosun's plugin in Antigravity CLI (agy): its hooks.json, the answers of lib/agy_state.py's HOOKS to the
payloads of agy 1.5.5 in tests/fixtures/agy/hooks/, and SETUP, which installs and removes the plugin (with a
fake `agy` and a scratch ~/.gemini)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_files
import agy_state


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "agents" / "agy-plugin"
HOOKS = ROOT / "tests" / "fixtures" / "agy" / "hooks"
CONVERSATION = "9dc2bdd8-ac7e-40ae-9a75-298c3ac5c91a"
APPROVAL = '{"decision":"auto_approve","overwrite":{"BypassSandbox":true}}'


def payload(name: str, **changes: Any) -> dict[str, Any]:
    return {**json.loads((HOOKS / f"{name}.json").read_text()), **changes}


def command_payload(command: str) -> dict[str, Any]:
    """The PreToolUse payload of a run_command asking for <command>."""
    base = payload("pre-tool-use")
    base["toolCall"]["args"]["CommandLine"] = command
    return base


def handlers() -> list[tuple[str, dict[str, Any]]]:
    """Every handler of hooks.json, with its event."""
    found: list[tuple[str, dict[str, Any]]] = []
    for events in json.loads((PLUGIN / "hooks.json").read_text()).values():
        for event, entries in events.items():
            for entry in entries:
                found.extend((event, handler) for handler in entry.get("hooks", [entry]))
    return found


class PluginFilesTest(unittest.TestCase):
    def test_the_plugin_validates(self) -> None:
        manifest = json.loads((PLUGIN / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "bosun")
        self.assertEqual(manifest["version"], agy_state.PLUGIN_VERSION)
        hooks = json.loads((PLUGIN / "hooks.json").read_text())
        self.assertEqual(list(hooks), ["bosun"])
        self.assertEqual(set(hooks["bosun"]), set(agy_state.HOOKS.events))
        self.assertEqual(hooks["bosun"]["PreToolUse"][0]["matcher"], "run_command")
        for _event, handler in handlers():
            self.assertEqual(handler["type"], "command")
            self.assertEqual(handler["timeout"], 30)

    def test_every_hook_is_silent_outside_a_bosun_window(self) -> None:
        environment = {k: v for k, v in os.environ.items() if k != "BOSUN_HOOK_CMD"}
        self.assertEqual(len(handlers()), 4)
        for event, handler in handlers():
            result = subprocess.run(
                ["sh", "-c", handler["command"]],
                input=json.dumps(payload("stop")),
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            self.assertEqual((result.stdout, result.stderr, result.returncode), ("", "", 0), event)

    def test_every_hook_runs_bosun_hook_agent_in_a_bosun_window(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            fake = Path(scratch) / "bosun"
            fake.write_text('#!/bin/sh\necho "$@"\n')
            fake.chmod(0o755)
            for event, handler in handlers():
                result = subprocess.run(
                    ["sh", "-c", handler["command"]],
                    capture_output=True,
                    text=True,
                    env={**os.environ, "BOSUN_HOOK_CMD": str(fake)},
                    check=False,
                )
                self.assertEqual(result.stdout.strip(), f"hook agent --harness agy --event {event}")
                self.assertEqual(result.returncode, 0)


class HookCase(unittest.TestCase):
    """A scratch state folder, a log folder, and the environment of a bosun window."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.logs = self.root / "logs"
        self.env = {
            "BOSUN_WINDOW": "repo",
            "BOSUN_CONVERSATION": "repo-conv",
            "BOSUN_HOOK_CMD": "/opt/bosun/bin/bosun",
            "BOSUN_STATE_DIR": str(self.root / "state"),
            "BOSUN_LOG_DIR": str(self.logs),
        }
        self.recorded: list[tuple[list[str], dict[str, str]]] = []
        self.alerts: list[str] = []
        patches = (
            mock.patch.dict(os.environ, self.env),
            mock.patch.object(agy_state, "now_ms", lambda: 1234),
            mock.patch.object(agy_state, "run_hook", self.run_hook),
            mock.patch.object(agy_state, "start_alert", self.alerts.append),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_hook(self, argv: list[str], env: dict[str, str]) -> int:
        self.recorded.append((argv, dict(env)))
        return 0

    def answer(self, event: str, body: dict[str, Any], **env: str) -> str:
        return agy_state.HOOKS.answer(event, body, {**self.env, **env})

    def log_text(self) -> str:
        return "".join(path.read_text() for path in sorted(self.logs.glob("hook-*.log")))


class SessionStartTest(HookCase):
    def known(self) -> dict[str, Any]:
        return agy_state.known_names()

    def test_session_start_remembers_the_conversation(self) -> None:
        folder = str(self.root)
        self.assertEqual(self.answer("SessionStart", payload("session-start", cwd=folder)), "")
        self.assertEqual(self.known()["repo-conv"], {"id": CONVERSATION, "cwd": os.path.realpath(folder)})

    def test_the_first_workspace_stands_in_for_an_empty_cwd(self) -> None:
        folder = str(self.root)
        self.answer("SessionStart", payload("session-start", workspacePaths=[folder, "/elsewhere"]))
        self.assertEqual(self.known()["repo-conv"]["cwd"], os.path.realpath(folder))

    def test_no_folder_is_an_error_and_remembers_nothing(self) -> None:
        self.assertEqual(self.answer("SessionStart", payload("session-start")), "")
        self.assertEqual(self.known(), {})
        self.assertIn("ERROR", self.log_text())


class PreInvocationTest(HookCase):
    def test_pre_invocation_marks_busy(self) -> None:
        self.assertEqual(self.answer("PreInvocation", payload("pre-invocation")), "")
        status = agy_state.read_status("repo-conv")
        self.assertEqual(status["status"], "busy")
        self.assertEqual(status["statusUpdatedAt"], 1234)
        self.assertEqual(status["conversationId"], CONVERSATION)
        self.assertEqual(self.recorded, [])  # events are off: nothing said is recorded

    def test_pre_invocation_stops_a_resume_that_got_another_conversation(self) -> None:
        answer = self.answer(
            "PreInvocation", payload("pre-invocation-resumed"), BOSUN_RESUMED="0000-another-conversation"
        )
        message = json.loads(answer)
        self.assertEqual(set(message), {"injectSteps", "terminationBehavior"})
        self.assertEqual(message["terminationBehavior"], "terminate")
        (step,) = message["injectSteps"]
        self.assertEqual(list(step), ["errorMessage"])
        self.assertEqual(list(step["errorMessage"]), ["userMessage"])
        text = step["errorMessage"]["userMessage"]
        self.assertIn("resumed a different conversation", text)
        self.assertIn("stopped it before its model call", text)
        self.assertIn("ERROR", self.log_text())
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("repo", self.alerts[0])
        self.assertNotIn("busy", agy_state.read_status("repo-conv").get("status", ""))

    def test_pre_invocation_lets_its_own_resume_through(self) -> None:
        self.assertEqual(
            self.answer("PreInvocation", payload("pre-invocation-resumed"), BOSUN_RESUMED=CONVERSATION), ""
        )
        self.assertEqual(agy_state.read_status("repo-conv")["status"], "busy")
        self.assertEqual(self.alerts, [])

    def test_what_was_said_is_recorded_once_with_events_on(self) -> None:
        body = payload("pre-invocation")
        self.answer("PreInvocation", body, BOSUN_EVENTS_ENABLED="1")
        self.answer("PreInvocation", body, BOSUN_EVENTS_ENABLED="1")  # the turn's next model call
        self.assertEqual(len(self.recorded), 1)
        argv, _env = self.recorded[0]
        self.assertEqual(argv[:3], ["user-prompt", "--window", "repo"])
        self.assertEqual(json.loads(argv[3]), {"prompt": body["lastUserInput"], "session_id": CONVERSATION})
        self.answer(
            "PreInvocation", payload("pre-invocation", lastUserInput="more"), BOSUN_EVENTS_ENABLED="1"
        )
        self.assertEqual(len(self.recorded), 2)

    def test_an_empty_input_is_not_recorded(self) -> None:
        self.answer("PreInvocation", payload("pre-invocation", lastUserInput=""), BOSUN_EVENTS_ENABLED="1")
        self.assertEqual(self.recorded, [])


class StopTest(HookCase):
    def test_stop_is_idle_or_shell(self) -> None:
        self.assertEqual(self.answer("Stop", payload("stop")), "")
        self.assertEqual(agy_state.read_status("repo-conv")["status"], "idle")
        self.assertEqual(self.answer("Stop", payload("stop-not-fully-idle")), "")
        self.assertEqual(agy_state.read_status("repo-conv")["status"], "shell")


class PreToolUseTest(HookCase):
    def asked(self, command: str) -> str:
        return self.answer("PreToolUse", command_payload(command))

    def test_git_gh_and_bosun_are_approved(self) -> None:
        for command in ("git push origin feature", "cd repo && gh pr view 5", "bosun ledger list"):
            self.assertEqual(self.asked(command), APPROVAL, command)

    def test_the_approval_is_exactly_this_json(self) -> None:
        self.assertEqual(
            json.loads(self.asked("git status")),
            {"decision": "auto_approve", "overwrite": {"BypassSandbox": True}},
        )

    def test_other_commands_get_no_answer(self) -> None:
        self.assertEqual(self.answer("PreToolUse", payload("pre-tool-use")), "")  # uname -s
        for command in ("curl https://example.com", "git status; rm -rf x", "cd repo", ""):
            self.assertEqual(self.asked(command), "", command)

    def test_a_command_that_hides_another_gets_no_answer(self) -> None:
        for command in (
            "git log $(rm -rf x)",
            "git log `rm -rf x`",
            "git status | sh",
            "(git status; rm -rf x)",
            "git status && curl https://example.com",
            "sudo git status",
            "env git status",
            "PATH=/tmp/x git status",
            "FOO=1 git push",
            "git status; PATH=/tmp/x gh pr list",
            "if true; then git status; fi",
            "./git status",
        ):
            self.assertEqual(self.asked(command), "", command)

    def test_a_pipe_needs_every_program_to_be_one_of_the_three(self) -> None:
        self.assertEqual(self.asked("git log --oneline | head -3"), "")  # head is not one of the three
        self.assertEqual(self.asked("gh pr list | gh pr view 5"), APPROVAL)

    def test_only_a_run_command_is_looked_at(self) -> None:
        body = command_payload("git status")
        body["toolCall"]["name"] = "write_to_file"
        self.assertEqual(self.answer("PreToolUse", body), "")
        self.assertEqual(self.answer("PreToolUse", {"toolCall": "git status"}), "")
        self.assertEqual(self.answer("PreToolUse", {}), "")

    def test_an_unreadable_command_gets_no_answer(self) -> None:
        self.assertEqual(self.asked("git commit -m 'unterminated"), "")


class RunHookTest(unittest.TestCase):
    def test_in_the_foreground_with_hook_sync(self) -> None:
        import hook

        with mock.patch.object(hook, "run", return_value=0) as run:
            env = {hook.SYNC: "1", "BOSUN_WINDOW": "repo"}
            self.assertEqual(agy_state.run_hook(["user-prompt", "--window", "repo", "{}"], env), 0)
        run.assert_called_once_with(["user-prompt", "--window", "repo", "{}"], env)

    def test_the_parent_returns_at_once_and_the_child_records_without_the_agents_pipe(self) -> None:
        script = (
            "import os, sys, time\n"
            f"sys.path.insert(0, {str(ROOT / 'lib')!r})\n"
            "import agy_state, hook\n"
            "def fake(argv, env):\n"
            "    time.sleep(0.5)\n"
            "    try:\n"
            "        os.fstat(3)\n"
            "        open_3 = True\n"
            "    except OSError:\n"
            "        open_3 = False\n"
            "    open(sys.argv[1], 'w').write(' '.join(argv) + ' fd3=' + str(open_3))\n"
            "    return 0\n"
            "hook.run = fake\n"
            "assert os.open(os.devnull, os.O_RDONLY) == 3  # stands for the agent's pipe\n"
            "start = time.time()\n"
            "agy_state.run_hook(['x', 'y'], {})\n"
            "print('returned in', round(time.time() - start, 1))\n"
        )
        with tempfile.TemporaryDirectory() as scratch:
            marker = Path(scratch) / "done"
            result = subprocess.run(
                [sys.executable, "-c", script, str(marker)],
                capture_output=True,
                text=True,
                check=False,
                env={k: v for k, v in os.environ.items() if k != "BOSUN_HOOK_SYNC"},
                pass_fds=(),
                timeout=30,
            )
            self.assertEqual(
                (result.returncode, result.stdout.strip()), (0, "returned in 0.0"), result.stderr
            )
            for _ in range(100):
                if marker.exists():
                    break
                time.sleep(0.1)
            self.assertEqual(marker.read_text(), "x y fd3=False")


class SetupCase(unittest.TestCase):
    """A scratch ~/.gemini and a fake agy on BOSUN_AGY_BIN."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.home = self.root / ".gemini"
        self.config = self.home / "config"
        self.cli = self.home / "antigravity-cli"
        self.install = self.root / "install"
        shutil_copy_plugin(self.install / "agents" / "agy-plugin")
        self.calls = self.root / "calls.txt"
        self.bin = self.root / "bin" / "fake-agy"
        self.bin.parent.mkdir()
        self.write_fake()
        patches = (
            mock.patch.object(agy_state, "AGY_HOME", self.home),
            mock.patch.object(agy_state, "AGY_CONFIG", self.config),
            mock.patch.object(agy_state, "CLI_HOME", self.cli),
            mock.patch.dict(os.environ, {"BOSUN_AGY_BIN": str(self.bin), "BOSUN_INSTALL": str(self.install)}),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_fake(self, install_exit: int = 0, uninstall_exit: int = 0, say: str = "") -> None:
        """A fake agy that does what `plugin install <dir>` and `plugin uninstall <name>` do to ~/.gemini."""
        self.bin.write_text(
            f"""#!/bin/sh
echo "$@" >> '{self.calls}'
if [ "$1 $2" = "plugin install" ]; then
  [ -n '{say}' ] && printf '%s\\n' 'starting' '{say}' >&2
  [ {install_exit} -ne 0 ] && exit {install_exit}
  mkdir -p '{self.config}/plugins/bosun'
  cp "$3"/* '{self.config}/plugins/bosun/'
  [ -e '{self.config}/import_manifest.json' ] ||
    echo '{{"imports": {{"bosun": {{}}}}}}' > '{self.config}/import_manifest.json'
  exit 0
fi
if [ "$1 $2" = "plugin uninstall" ]; then
  [ -n '{say}' ] && echo '{say}' >&2
  [ {uninstall_exit} -ne 0 ] && exit {uninstall_exit}
  rm -rf '{self.config}/plugins/bosun'
  echo '{{"imports": null}}' > '{self.config}/import_manifest.json'
  mkdir -p '{self.cli}/plugin_data/bosun'
  exit 0
fi
exit 9
"""
        )
        self.bin.chmod(0o755)

    def installed_version(self) -> str | None:
        found = agent_files.read_object(self.config / "plugins" / "bosun" / "plugin.json")
        return found["version"] if found else None

    def commands(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []


def shutil_copy_plugin(target: Path) -> None:
    target.mkdir(parents=True)
    for source in PLUGIN.iterdir():
        (target / source.name).write_text(source.read_text())


class SetupTest(SetupCase):
    def test_needed_when_missing_unreadable_or_another_version(self) -> None:
        self.assertTrue(agy_state.SETUP.needed())
        plugin = self.config / "plugins" / "bosun"
        plugin.mkdir(parents=True)
        (plugin / "plugin.json").write_text("not json")
        self.assertTrue(agy_state.SETUP.needed())
        (plugin / "plugin.json").write_text(json.dumps({"name": "bosun", "version": "0.0.1"}))
        self.assertTrue(agy_state.SETUP.needed())
        (plugin / "plugin.json").write_text(
            json.dumps({"name": "bosun", "version": agy_state.PLUGIN_VERSION})
        )
        self.assertFalse(agy_state.SETUP.needed())

    def test_setup_installs_when_missing_or_older(self) -> None:
        agy_state.SETUP.apply()
        self.assertEqual(self.commands(), [f"plugin install {self.install}/agents/agy-plugin"])
        self.assertEqual(self.installed_version(), agy_state.PLUGIN_VERSION)
        self.assertFalse(agy_state.SETUP.needed())
        # an older version in place: removed first, then the new one installed
        (self.config / "plugins" / "bosun" / "plugin.json").write_text('{"name":"bosun","version":"0.0.1"}')
        self.calls.unlink()
        agy_state.SETUP.apply()
        self.assertEqual(
            self.commands(),
            ["plugin uninstall bosun", f"plugin install {self.install}/agents/agy-plugin"],
        )
        self.assertEqual(self.installed_version(), agy_state.PLUGIN_VERSION)

    def test_a_failed_install_says_the_last_line_only(self) -> None:
        self.write_fake(install_exit=1, say="could not read the plugin")
        with self.assertRaises(agent_files.SetupError) as raised:
            agy_state.SETUP.apply()
        self.assertIn("could not read the plugin", str(raised.exception))
        self.assertNotIn("starting", str(raised.exception))

    def test_agy_missing_is_said_plainly(self) -> None:
        os.environ["BOSUN_AGY_BIN"] = str(self.root / "no-such-agy")
        with self.assertRaises(agent_files.SetupError) as raised:
            agy_state.SETUP.apply()
        self.assertEqual(
            str(raised.exception),
            f"{self.root / 'no-such-agy'} is not installed or not on PATH (BOSUN_AGY_BIN)",
        )

    def test_no_install_folder_is_an_error(self) -> None:
        del os.environ["BOSUN_INSTALL"]
        with self.assertRaises(agent_files.SetupError) as raised:
            agy_state.SETUP.apply()
        self.assertIn("BOSUN_INSTALL", str(raised.exception))
        self.assertEqual(self.commands(), [])

    def test_an_install_that_leaves_no_plugin_is_an_error(self) -> None:
        self.bin.write_text("#!/bin/sh\nexit 0\n")
        with self.assertRaises(agent_files.SetupError):
            agy_state.SETUP.apply()


class UndoTest(SetupCase):
    def test_undo_leaves_nothing_of_bosun(self) -> None:
        agy_state.SETUP.apply()
        self.assertTrue((self.config / "import_manifest.json").exists())
        agy_state.SETUP.undo()
        self.assertIn("plugin uninstall bosun", self.commands())
        self.assertFalse((self.config / "plugins" / "bosun").exists())
        self.assertFalse((self.cli / "plugin_data" / "bosun").exists())
        self.assertFalse((self.config / "import_manifest.json").exists())

    def test_undo_keeps_a_record_that_holds_another_plugins_imports(self) -> None:
        agy_state.SETUP.apply()
        self.bin.write_text(
            self.bin.read_text().replace("echo '{\"imports\": null}'", 'echo \'{"imports": {"other": {}}}\'')
        )
        agy_state.SETUP.undo()
        self.assertTrue((self.config / "import_manifest.json").exists())

    def test_undo_with_nothing_to_undo_succeeds(self) -> None:
        self.write_fake(uninstall_exit=1, say="plugin bosun is not installed")
        agy_state.SETUP.undo()
        self.assertEqual(self.commands(), ["plugin uninstall bosun"])

    def test_undo_without_agy_removes_the_plugin_itself(self) -> None:
        agy_state.SETUP.apply()
        os.environ["BOSUN_AGY_BIN"] = str(self.root / "no-such-agy")
        agy_state.SETUP.undo()
        self.assertFalse((self.config / "plugins" / "bosun").exists())

    def test_a_failed_uninstall_of_an_installed_plugin_is_an_error(self) -> None:
        agy_state.SETUP.apply()
        self.write_fake(uninstall_exit=1, say="cannot remove")
        with self.assertRaises(agent_files.SetupError) as raised:
            agy_state.SETUP.undo()
        self.assertIn("cannot remove", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
