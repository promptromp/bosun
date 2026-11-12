"""bosun's plugin in Antigravity CLI (agy): its hooks.json, the answers of lib/agy_state.py's HOOKS to the
payloads of agy 1.5.5 in tests/fixtures/agy/hooks/, and SETUP, which installs and removes the plugin (with a
fake `agy` and a scratch ~/.gemini)."""

from __future__ import annotations

import contextlib
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
        self.assertEqual(len(handlers()), 4)
        with tempfile.TemporaryDirectory() as scratch:
            not_executable = Path(scratch) / "not-executable"
            not_executable.write_text("#!/bin/sh\necho hi\n")
            variants: dict[str, str | None] = {
                "unset": None,
                "empty": "",
                "missing file": str(Path(scratch) / "no-such-bosun"),
                "not executable": str(not_executable),
                "a folder": scratch,
            }
            for name, value in variants.items():
                environment = {k: v for k, v in os.environ.items() if k != "BOSUN_HOOK_CMD"}
                if value is not None:
                    environment["BOSUN_HOOK_CMD"] = value
                for event, handler in handlers():
                    result = subprocess.run(
                        ["sh", "-c", handler["command"]],
                        input=json.dumps(payload("stop")),
                        capture_output=True,
                        text=True,
                        env=environment,
                        check=False,
                    )
                    self.assertEqual(
                        (result.stdout, result.stderr, result.returncode), ("", "", 0), f"{event}, {name}"
                    )

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
        self.alerts: list[tuple[str, dict[str, str]]] = []
        patches = (
            mock.patch.dict(os.environ, self.env),
            mock.patch.object(agy_state, "now_ms", lambda: 1234),
            mock.patch.object(agy_state, "run_hook", self.run_hook),
            mock.patch.object(agy_state, "start_alert", self.start_alert),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_hook(self, argv: list[str], env: dict[str, str]) -> int:
        self.recorded.append((argv, dict(env)))
        return 0

    def start_alert(self, message: str, env: dict[str, str]) -> None:
        self.alerts.append((message, dict(env)))

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

    def test_the_folder_the_launch_recorded_stands_in_when_the_payload_has_none(self) -> None:
        folder = str(self.root)
        agy_state.launched("repo-conv", "1.5.5", "%1", "/run/tmux/sock", folder)
        self.assertEqual(self.answer("SessionStart", payload("session-start")), "")  # cwd "", no workspaces
        self.assertEqual(self.known()["repo-conv"], {"id": CONVERSATION, "cwd": os.path.realpath(folder)})
        self.assertNotIn("ERROR", self.log_text())

    def test_the_payload_wins_over_the_folder_the_launch_recorded(self) -> None:
        agy_state.launched("repo-conv", "1.5.5", "", "", "/recorded")
        folder = str(self.root)
        self.answer("SessionStart", payload("session-start", cwd=folder))
        self.assertEqual(self.known()["repo-conv"]["cwd"], os.path.realpath(folder))
        self.answer("SessionStart", payload("session-start", workspacePaths=["", folder]))
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
        self.assertIn("repo", self.alerts[0][0])
        self.assertEqual(self.alerts[0][1]["BOSUN_HOOK_CMD"], "/opt/bosun/bin/bosun")  # the env it got
        self.assertNotIn("busy", agy_state.read_status("repo-conv").get("status", ""))

    def test_the_resume_guard_still_stops_the_turn_when_its_log_and_alert_fail(self) -> None:
        def broken(*_args: Any) -> None:
            raise OSError("cannot start")

        for failing in (("start_alert",), ("hook_error",), ("start_alert", "hook_error")):
            with contextlib.ExitStack() as stack:
                for name in failing:
                    stack.enter_context(mock.patch.object(agy_state, name, broken))
                answer = self.answer(
                    "PreInvocation", payload("pre-invocation-resumed"), BOSUN_RESUMED="other"
                )
            self.assertEqual(json.loads(answer)["terminationBehavior"], "terminate", failing)

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
    """What passes agy's sandbox: allow-lists of git, gh and bosun subcommands, in the workspace the payload
    names (a scratch folder here, so that a path the agent wrote can be planted in it)."""

    def setUp(self) -> None:
        super().setUp()
        self.work = self.root / "r1"
        self.work.mkdir()
        self.remotes = {"origin"}  # the remotes of the repository's config, never a real one's
        self.config_reads: list[tuple[str, str]] = []
        patcher = mock.patch.object(agy_state, "configured_remote", self.configured_remote)
        patcher.start()
        self.addCleanup(patcher.stop)

    def configured_remote(self, name: str, cwd: str) -> bool:
        self.config_reads.append((name, cwd))
        return name in self.remotes

    def body(self, command: str) -> dict[str, Any]:
        return {**command_payload(command), "cwd": str(self.work), "workspacePaths": [str(self.work)]}

    def asked(self, command: str) -> str:
        return self.answer("PreToolUse", self.body(command))

    def assert_approved(self, *commands: str) -> None:
        for command in commands:
            self.assertEqual(self.asked(command), APPROVAL, command)

    def assert_no_answer(self, *commands: str) -> None:
        for command in commands:
            self.assertEqual(self.asked(command), "", command)

    def test_git_gh_and_bosun_are_approved(self) -> None:
        self.assert_approved(
            "git push origin feature",
            "git push",
            "git pull --rebase",
            "git fetch origin",
            "git commit -m 'fix #12: a; b'",
            "git commit -c HEAD",
            "git switch -c new",
            "git log -c",
            "git add src/a.py",
            "git status",
            "gh pr create --title 'Fix #12' --body 'a && b'",
            "gh pr view 5",
            "gh api graphql -f query=q",
            "gh run view 9",
            "bosun ledger list",
            "bosun send w hi",
            "git status && git push origin feature",
            'git commit -m "fix: it\'s done"',
            "git fetch origin && git rebase origin/main",
            "git status || git log",
            "git log --oneline -5; gh pr list",
            "git commit -m 'a\nb'",
            "git push -u origin feature",
            "git push --force-with-lease origin HEAD:feature",
            "git cherry-pick -x abc123",
            "git add 'src/*.py'",
        )

    def test_the_approval_is_exactly_this_json(self) -> None:
        self.assertEqual(
            json.loads(self.asked("git status")),
            {"decision": "auto_approve", "overwrite": {"BypassSandbox": True}},
        )

    def test_every_subcommand_of_the_tables_is_approved_on_its_own(self) -> None:
        for sub in agy_state.GIT:
            self.assert_approved(f"git {sub}")
        for sub, seconds in agy_state.GH.items():
            self.assert_approved(*(f"gh {sub} {second}" for second in (seconds.seconds or ("x",))))
        for sub in agy_state.BOSUN:
            self.assert_approved(f"bosun {sub}")

    def test_other_commands_get_no_answer(self) -> None:
        self.assertEqual(self.answer("PreToolUse", payload("pre-tool-use")), "")  # uname -s
        self.assert_no_answer("curl https://example.com", "git status; rm -rf x", "cd repo", "")

    def test_a_working_directory_in_the_args_gets_no_answer(self) -> None:
        for key in ("Cwd", "WorkingDirectory", "cwd"):
            body = self.body("git status")
            body["toolCall"]["args"][key] = str(self.work / "elsewhere")
            self.assertEqual(self.answer("PreToolUse", body), "", key)
        body = self.body("git status")
        body["toolCall"]["args"]["BypassSandbox"] = True
        self.assertEqual(self.answer("PreToolUse", body), APPROVAL)

    def test_a_cwd_outside_its_workspaces_gets_no_answer(self) -> None:
        for cwd in (str(self.root), str(self.work / "sub"), "", "r1"):
            body = {**self.body("git status"), "cwd": cwd}
            self.assertEqual(self.answer("PreToolUse", body), "", cwd)
        self.assertEqual(self.answer("PreToolUse", {**self.body("git status"), "workspacePaths": "/"}), "")

    def test_without_workspaces_the_cwd_must_still_be_a_full_path(self) -> None:
        for cwd, expected in ((str(self.work), APPROVAL), ("", ""), ("r1", ""), (None, "")):
            body = {**self.body("git status"), "workspacePaths": [], "cwd": cwd}
            self.assertEqual(self.answer("PreToolUse", body), expected, cwd)

    def test_the_reviewers_lines_get_no_answer(self) -> None:
        (self.work / "evil").mkdir()
        self.assert_no_answer(
            "git * zz",
            "git ?? x zz",
            "git [-]c x zz",
            "git log ~/x",
            "git log HEAD~1",
            "git fetch -u./x origin",
            "git fetch -qu./x origin",
            "git pull -u./x origin",
            "git rebase -x'cmd' main",
            "git rebase -ix'sh x' main",
            "git difftool -x x",
            "git filter-branch --tree-filter x HEAD",
            "git grep -O./x pattern",
            "git send-email x.patch",
            "git instaweb",
            "git daemon .",
            "git init --template=t",
            "git clone src dst",
            "git push ./evil HEAD",
            "git push evil HEAD",
            "git push file:///x HEAD",
            "git push host:path HEAD",
            "git push ext::sh HEAD",
            "git remote add evil ./x",
            "git submodule update",
            "git -C x status",
            "git --git-dir=x log",
            "cd x && git status",
            "git log --output=x",
            "git log --out x",
            "git bundle create x",
            "git archive --output=x HEAD",
            "git config x y",
            "git -c a=b status",
            "git merge -s ours x",
            "git merge -Xs x",
            "git rebase -i main",
            "gh ext install .",
            "gh extensions list",
            "gh config set browser x",
            "gh codespace ssh",
            "gh repo clone a/b -- --template=t",
            "gh api -F x=@/etc/passwd",
            "gh pr create --body-file /x",
            "gh gist create x",
            "bosun trust --any x",
            "bosun launch foo",
            "bosun send --new w x hi",
            "bosun uninstall",
            "bosun alert x",
            "bosun ledger approve x --quote e",
        )

    def test_a_push_or_fetch_reaches_only_a_remote_by_its_name(self) -> None:
        (self.work / "evil.git").mkdir()
        (self.work / "bundle").write_text("x")
        self.assert_no_answer(
            "git push evil HEAD",  # git tries evil.git too
            "git fetch bundle",
            "git push -o x ./evil HEAD",  # -o takes the next word: ./evil is the repository
            "git fetch --depth 1 /x",
            "git fetch --multiple origin ./x",
            "git fetch -j 2 /x",
            "git push origin -- x",
            "git push --repo=x",
            "git push =git HEAD",
            "git pull --rebase=interactive origin",
            "git pull -rinteractive origin",
            "git remote show ./x",
            "git remote get-url /x",
            "git remote set-url origin ./x",
            "git remote -v show",
        )
        self.assert_approved(
            "git fetch --depth=1 origin",
            "git pull --rebase=merges origin main",
            "git push --no-verify origin HEAD:refs/heads/x",
            "git remote",
            "git remote -v",
            "git remote show origin",
            "git remote get-url origin",
        )

    def test_the_repository_must_be_a_remote_in_its_own_config(self) -> None:
        (self.work / "upstream").mkdir()
        self.remotes = {"origin", "upstream"}
        self.assert_no_answer(
            "git push upstream HEAD",  # a folder too: refused even though configured
            "git push fork HEAD",  # neither a folder nor a remote: a folder made later would be pushed to
            "git fetch fork",
            "git pull fork main",
            "git remote show fork",
            "git remote get-url fork",
        )
        self.config_reads.clear()
        self.assert_approved("git push origin feature", "git push")
        self.assertEqual(self.config_reads, [("origin", str(self.work))])

    def test_bosun_ledger_never_approves_a_proposal(self) -> None:
        self.assert_no_answer(
            "bosun ledger approve x --quote e",
            "bosun ledger approve",
            "bosun ledger -- approve x --quote e",
            "bosun ledger list --",
        )
        self.assert_approved("bosun ledger list", "bosun ledger show x", "bosun ledger note x approve")

    def test_global_options_before_the_subcommand_get_no_answer(self) -> None:
        self.assert_no_answer(
            "git --exec-path=/x log",
            "git --work-tree=x status",
            "git --namespace=x push",
            "git -p log",
            "gh -R a/b pr view 5",
            "bosun --help",
            "git",
            "gh",
            "bosun",
        )

    def test_what_runs_a_program_or_reads_a_file_into_what_is_sent_gets_no_answer(self) -> None:
        self.assert_no_answer(
            "git diff --ext-diff",
            "git log --exec=x",
            "git commit -F /x",
            "git commit --file=/x",
            "git commit -t /x",
            "git tag -F /x v1",
            "git merge -F /x topic",
            "git checkout --recurse-submodules main",
            "git rebase --strategy=x main",
            "git rebase --interactive main",
            "git cherry-pick -s abc",  # --signoff here, but -s is a strategy in merge and rebase
            "git status --upload-pack=x",
            "gh pr create --recover /x",
            "gh issue create -T x",
            "gh api https://example.com/x",
            "gh api --input /x repos/a/b/issues",
            "gh api --hostname example.com /x",
            "gh pr comment 5 --body=@/x",
            "gh pr checkout 5 --recurse-submodules",
            "gh pr comment 5 -- x",
            "gh pr frob 5",
            "gh repo clone a/b",
            "bosun delegate review --id i-1 --ask x --start w",
            "bosun send --start w",
        )

    def test_a_command_that_hides_another_gets_no_answer(self) -> None:
        self.assert_no_answer(
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
            "/usr/bin/git status",
            "git commit -m 'unterminated",
            'git commit -m "unterminated',
        )

    def test_a_hash_inside_a_word_gets_no_answer(self) -> None:
        # the shell starts a comment only at a word's start, shlex anywhere: its view must not decide
        self.assert_no_answer(
            "git log a#; touch PWNED",
            "git log a#b; touch PWNED",
            "git log --grep=#; touch PWNED",
            "git log HEAD~1#x && touch PWNED",
            "git log x#\ntouch PWNED",
            "git log x# > PWNED",
            "git log x# | sh",
            "git log x# 2>&1; touch PWNED",
            "git status #; touch PWNED",
            "git log ;#; touch PWNED",
            "git log &#; touch PWNED",
            "git log x#$(touch PWNED)",
        )

    def test_quoting_the_lexer_and_the_shell_read_differently_gets_no_answer(self) -> None:
        self.assert_no_answer(
            "git log $'\\'' ; touch PWNED ; # '",
            "git log $'\\'' && touch PWNED && git log '",
            'git log "a\\"" ; touch PWNED',
            "git log 'a\\' ; touch PWNED ; git log '",
            "git log \\; touch PWNED",
            "git log $HOME",
            'git log "$HOME"',
            'git log "`id`"',
            'git log "a!b"',
            "git log {a,b}",
            "git log HEAD@{1}",
            # a quoted operator or nothing in quotes: both lexers split there, the shell does not, so this
            # would be checked as two commands and run as `git merge x ; git log -sevil` (a strategy)
            "git merge x ';' git log -sevil",
            "git status '&&' git log",
            "git push '' HEAD",
            'git commit -m ""',
        )

    def test_other_separators_and_control_characters_get_no_answer(self) -> None:
        self.assert_no_answer(
            "git status\ncurl x",
            "git status\rcurl x",
            "git status\x0bcurl x",
            "git status\x0ccurl x",
            "git status\x00; curl x",
            "git log\N{LINE SEPARATOR}curl x",
            "git log\u0085curl x",
            "git status & curl x",
            "git status &",
            "git status|curl x",
            "git status |& sh",
            "git status ||| sh",
            "git status &&& curl x",
            "git status ;; curl x",
            "git status > out",
            "git status 2>&1",
            "git log <(curl x)",
            "git log <<EOF\nfoo\nEOF",
            "{ git log; }",
            "! git log",
            "time git log",
            "git log \\\ncurl x",
        )

    def test_a_pipe_gets_no_answer(self) -> None:
        self.assert_no_answer("git log --oneline | head -3", "gh pr list | gh pr view 5")

    def test_the_routes_from_git_gh_and_bosun_to_any_program_get_no_answer(self) -> None:
        self.assert_no_answer(
            "git -c core.sshCommand='sh x' push",
            "git -c alias.x='!sh' x",
            "git push origin feature -c x",
            "git --config-env=core.x=Y push",
            "git fetch --upload-pack='sh x' origin",
            "git fetch --upl=x origin",
            "git ls-remote --upload-pack=x origin",
            "git push --receive-pack='sh x' origin",
            "git push --exec=x origin",
            "git push --e=x origin",
            "git fetch -u 'sh x' origin",
            "git pull -u x origin",
            "git clone ext::'sh -c x' dir",
            "git submodule foreach 'sh x'",
            "git rebase -x 'sh x' main",
            "git rebase --exec 'sh x' main",
            "git rebase --exe 'sh x' main",
            "git bisect run sh x",
            "gh extension install a/b",
            "gh alias set --shell x 'curl y'",
            "gh auth login",
            "gh secret set X",
            "bosun update",
            "bosun init",
        )

    def test_only_a_run_command_is_looked_at(self) -> None:
        body = self.body("git status")
        body["toolCall"]["name"] = "write_to_file"
        self.assertEqual(self.answer("PreToolUse", body), "")
        self.assertEqual(self.answer("PreToolUse", {"toolCall": "git status"}), "")
        self.assertEqual(self.answer("PreToolUse", {}), "")


class ConfiguredRemoteTest(unittest.TestCase):
    """configured_remote against a scratch repository, with no global or system git config."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        isolated = {"HOME": str(self.root), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        patcher = mock.patch.dict(os.environ, isolated)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for argv in (["init", "-q"], ["remote", "add", "origin", "https://example.com/x.git"]):
            subprocess.run(["git", "-C", str(self.repo), *argv], check=True, stdin=subprocess.DEVNULL)

    def test_a_remote_of_the_config_and_nothing_else(self) -> None:
        self.assertTrue(agy_state.configured_remote("origin", str(self.repo)))
        self.assertFalse(agy_state.configured_remote("evil", str(self.repo)))
        (self.repo / "evil").mkdir()
        self.assertFalse(agy_state.configured_remote("evil", str(self.repo)))
        self.assertFalse(agy_state.configured_remote("origin", str(self.root)))  # not a repository

    def test_a_config_that_cannot_be_read_is_no_remote(self) -> None:
        for error in (OSError("no git"), subprocess.TimeoutExpired("git", 5)):
            with mock.patch("subprocess.run", side_effect=error):
                self.assertFalse(agy_state.configured_remote("origin", str(self.repo)))


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

    def test_undo_keeps_a_record_that_holds_more_than_its_empty_imports(self) -> None:
        agy_state.SETUP.apply()
        self.bin.write_text(
            self.bin.read_text().replace(
                "echo '{\"imports\": null}'", 'echo \'{"imports": null, "other": 1}\''
            )
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
