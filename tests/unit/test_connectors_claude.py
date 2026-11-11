"""lib/connectors_claude.py: a Claude Code window's list of connectors turned into its settings."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import claude_state  # noqa: E402
import connector_fakes  # noqa: E402
import connectors  # noqa: E402
import connectors_claude  # noqa: E402
from connector_fakes import fake_agent, printing  # noqa: E402
from connectors import parse  # noqa: E402
from connectors_claude import claude_entries  # noqa: E402


MAIL = "claude.ai Acme Mail"
MAIL_URL = "https://mail.example.com/mcp"
MCP_LIST = f"""Checking MCP server health…

{MAIL}: {MAIL_URL} - ✔ Connected
acme-local: /Users/me/.local/bin/acme-mcp  - ✔ Connected
"""
SYNCED = "plugin:acme-pdf:pdf"
SYNCED_URL_NAME = "plugin:acme-docs:docs"
ODD_NAME = 'claude.ai Acme "Mail" $(touch evaluated)'


class PureTest(unittest.TestCase):
    def test_empty_list_resolves_to_no_entries(self) -> None:
        for value in ("", " ", " , ,"):
            self.assertEqual(claude_entries(parse(value), {}, {}), connectors.Resolution((), ()))

    def test_all_has_no_allow_list(self) -> None:
        allowed = parse(" all ")
        self.assertEqual(claude_entries(allowed, {}, {}), connectors.Resolution(None, ()))
        self.assertFalse(connectors_claude.needs_lookup(allowed, {}))

    def test_plain_name_is_server_name(self) -> None:
        resolution = claude_entries(parse("acme-local, acme_docs2"), {}, {})
        self.assertEqual(resolution.entries, ({"serverName": "acme-local"}, {"serverName": "acme_docs2"}))
        self.assertEqual(resolution.missing, ())
        self.assertFalse(connectors_claude.needs_lookup(parse("acme-local"), {}))

    def test_connector_name_resolves_to_url(self) -> None:
        allowed = parse(MAIL)
        self.assertTrue(connectors_claude.needs_lookup(allowed, {}))
        self.assertEqual(claude_entries(allowed, {}, {MAIL: MAIL_URL}).entries, ({"serverUrl": MAIL_URL},))

    def test_url_pattern_passes_through(self) -> None:
        allowed = parse("https://mcp.example.com/*, http://localhost:9000/mcp")
        self.assertFalse(connectors_claude.needs_lookup(allowed, {}))
        self.assertEqual(
            claude_entries(allowed, {}, {}).entries,
            ({"serverUrl": "https://mcp.example.com/*"}, {"serverUrl": "http://localhost:9000/mcp"}),
        )

    def test_unfound_names_reported_missing(self) -> None:
        allowed = parse(f"claude.ai Acme Gone, acme-local, plugin:acme-gone:x, {MAIL}")
        resolution = claude_entries(allowed, {}, {MAIL: MAIL_URL})
        self.assertEqual(resolution.entries, ({"serverName": "acme-local"}, {"serverUrl": MAIL_URL}))
        self.assertEqual(resolution.missing, ("claude.ai Acme Gone", "plugin:acme-gone:x"))

    def test_a_plugin_server_without_url_or_command_is_missing(self) -> None:
        plugins = {"plugin:acme:odd": {"type": "stdio"}, "plugin:acme:bad": {"command": "x", "args": "y"}}
        resolution = claude_entries(parse("plugin:acme:odd, plugin:acme:bad"), plugins, {})
        self.assertEqual(resolution, connectors.Resolution((), ("plugin:acme:odd", "plugin:acme:bad")))

    def test_names_with_shell_characters_pass_as_data(self) -> None:
        allowed = parse(ODD_NAME)
        self.assertEqual(allowed.names, (ODD_NAME,))
        self.assertEqual(claude_entries(allowed, {}, {}).missing, (ODD_NAME,))
        self.assertEqual(
            claude_entries(allowed, {}, {ODD_NAME: MAIL_URL}).entries, ({"serverUrl": MAIL_URL},)
        )

    def test_a_plugin_server_only_the_listing_shows_resolves_to_what_it_shows(self) -> None:
        """A synced, --plugin-dir or managed plugin: not installed, yet `claude mcp list` shows it."""
        allowed = parse(f"{SYNCED}, {SYNCED_URL_NAME}")
        self.assertTrue(connectors_claude.needs_lookup(allowed, {}))
        targets = {SYNCED: "npx -y @acme/pdf-server --stdio", SYNCED_URL_NAME: MAIL_URL}
        self.assertEqual(
            claude_entries(allowed, {}, targets),
            connectors.Resolution(
                ({"serverCommand": ["npx", "-y", "@acme/pdf-server", "--stdio"]}, {"serverUrl": MAIL_URL}), ()
            ),
        )

    def test_an_installed_plugins_own_file_wins_over_what_the_listing_shows(self) -> None:
        plugins = {SYNCED: {"command": "acme-pdf", "args": ["--stdio"]}}
        resolution = claude_entries(parse(SYNCED), plugins, {SYNCED: "npx other --stdio"})
        self.assertEqual(resolution.entries, ({"serverCommand": ["acme-pdf", "--stdio"]},))

    def test_a_name_the_listing_does_not_show_is_still_missing(self) -> None:
        resolution = claude_entries(parse(SYNCED), {}, {"plugin:acme:other": "npx x"})
        self.assertEqual(resolution, connectors.Resolution((), (SYNCED,)))

    def test_widening_is_any_entry_not_exactly_among_the_window_s(self) -> None:
        """Claude Code adds every scope's allow list to a window's: an entry not JSON-equal to one of the
        window's own widens it, a narrower or a wider pattern too."""
        own = ({"serverName": "acme-local"}, {"serverUrl": "https://mcp.example.com/*"})
        user, project = Path("/home/me/.claude/settings.json"), Path("/repo/.claude/settings.json")
        scopes: list[tuple[Path, list[Any]]] = [
            (user, [{"serverName": "acme-local"}, {"serverUrl": "https://*"}]),
            (project, [{"serverUrl": "https://mcp.example.com/mcp"}, {"serverName": "acme-local", "x": 1}]),
        ]
        self.assertEqual(
            connectors_claude.widening(own, scopes),
            [
                (user, [{"serverUrl": "https://*"}]),
                (
                    project,
                    [{"serverUrl": "https://mcp.example.com/mcp"}, {"serverName": "acme-local", "x": 1}],
                ),
            ],
        )
        same = [(project, [{"serverUrl": "https://mcp.example.com/*"}, {"serverName": "acme-local"}])]
        self.assertEqual(connectors_claude.widening(own, same), [])
        self.assertEqual(
            connectors_claude.widening((), [(user, [{"serverName": "acme-local"}])]),
            [(user, [{"serverName": "acme-local"}])],
        )


class KeptTest(connector_fakes.ScratchCase):
    def test_kept_file_corrupt_is_empty(self) -> None:
        self.assertEqual(connectors_claude.load_kept(self.state), {})
        self.state.mkdir()
        kept = self.state / "connectors.json"
        for text in ("{not json", "[1, 2]", json.dumps({MAIL: 3}), "\x00\xff"):
            kept.write_text(text)
            self.assertEqual(connectors_claude.load_kept(self.state), {}, text)
        connectors_claude.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        self.assertEqual(connectors_claude.load_kept(self.state), {MAIL: MAIL_URL})

    def test_a_save_replaces_only_the_names_asked_about(self) -> None:
        chat, gone = "claude.ai Acme Chat", "claude.ai Acme Gone"
        connectors_claude.save_kept(
            self.state,
            [MAIL, chat, gone],
            {MAIL: "https://old.example.com/mcp", chat: "https://c", gone: "https://g"},
        )
        connectors_claude.save_kept(self.state, [MAIL, gone], {MAIL: MAIL_URL})
        self.assertEqual(connectors_claude.load_kept(self.state), {MAIL: MAIL_URL, chat: "https://c"})
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["connectors.json"])

    def test_a_save_whose_rename_finds_its_temp_gone_never_crashes(self) -> None:
        with (
            mock.patch("os.replace", side_effect=FileNotFoundError(2, "No such file or directory")),
            mock.patch("sys.stderr", io.StringIO()) as err,
        ):
            connectors_claude.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        self.assertTrue(err.getvalue().startswith("connectors: could not keep the servers found"))
        self.assertEqual(connectors_claude.load_kept(self.state), {})

    def test_concurrent_saves_never_crash_and_leave_valid_json(self) -> None:
        """Launches starting at once (the morning start) each save what they found."""
        script = (
            "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1])\n"
            "import connectors_claude\n"
            "for i in range(40):\n"
            "    n = f'claude.ai {sys.argv[3]} {i}'\n"
            "    connectors_claude.save_kept(Path(sys.argv[2]), [n], {n: 'https://x'})"
        )
        writers = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(ROOT / "lib"), str(self.state), f"Acme{n}"],
                stdin=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            for n in range(4)
        ]
        for writer in writers:
            _, err = writer.communicate(timeout=60)
            self.assertEqual((writer.returncode, err), (0, ""))
        kept = json.loads((self.state / "connectors.json").read_text())
        self.assertTrue(kept and all(isinstance(url, str) for url in kept.values()))
        self.assertEqual([p.name for p in self.state.iterdir()], ["connectors.json"])


class ClaudeCase(connector_fakes.ScratchCase):
    """connectors_claude.py's command line, with the installed plugins read from a scratch file."""

    def setUp(self) -> None:
        super().setUp()
        self.installed = self.root / "installed_plugins.json"
        self.home_settings = self.root / "home" / ".claude" / "settings.json"
        for name, value in (("INSTALLED_PLUGINS", self.installed), ("SETTINGS_FILE", self.home_settings)):
            patch = mock.patch.object(claude_state, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def allow_in(self, settings: Path, *entries: object) -> None:
        """A settings file holding an allowedMcpServers of <entries>."""
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"permissions": {}, "allowedMcpServers": list(entries)}))

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        return connector_fakes.capture(connectors_claude.main, *argv)


class CommandTest(ClaudeCase):
    def claude(self, value: str, agent: Path, *extra: str) -> tuple[int, str, str]:
        common = ["--cwd", str(self.root), "--state-dir", str(self.state), "--agent-bin", str(agent)]
        return self.run_main("--list", value, *common, *extra)

    def settings(self, out: str) -> Any:
        return json.loads(out)["allowedMcpServers"]

    def test_empty_list_allows_nothing(self) -> None:
        code, out, err = self.claude("", self.root / "no-claude")
        self.assertEqual((code, self.settings(out), err), (0, [], ""))

    def test_all_has_no_allow_list(self) -> None:
        self.assertEqual(self.claude("all", self.root / "no-claude"), (0, "{}\n", ""))

    def test_an_unexpected_failure_allows_nothing(self) -> None:
        boom = mock.patch.object(claude_state, "mcp_server_targets", side_effect=RuntimeError("boom"))
        with boom:
            code, out, err = self.claude(f"{MAIL}, acme-local", self.root / "no-claude")
        self.assertEqual((code, out), (1, '{"allowedMcpServers":[]}\n'))
        self.assertEqual(len(err.splitlines()), 1)
        self.assertTrue(err.startswith("connectors: ") and "boom" in err)

    def test_a_state_folder_that_cannot_be_written_still_gives_the_settings(self) -> None:
        self.state.write_text("a file where the state folder should be")
        code, out, err = self.claude(f"{MAIL}, claude.ai Acme Gone", printing(self.root, MCP_LIST))
        self.assertEqual(code, 1)
        self.assertEqual(self.settings(out), [{"serverUrl": MAIL_URL}])
        self.assertIn("connectors: could not keep the servers found", err)
        self.assertIn("claude.ai Acme Gone not found", err)

    def test_all_with_names_is_refused(self) -> None:
        code, out, err = self.claude("all, acme-local", self.root / "no-claude")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("connectors:", err)

    def test_a_plugin_server_only_the_listing_shows_gets_its_command(self) -> None:
        listing = f"{SYNCED}: /Users/me/bin/acme-pdf --stdio  - ✔ Connected\n"
        self.installed.write_text(json.dumps({"plugins": {}}))
        code, out, err = self.claude(SYNCED, printing(self.root, listing))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.settings(out), [{"serverCommand": ["/Users/me/bin/acme-pdf", "--stdio"]}])
        self.assertEqual(connectors_claude.load_kept(self.state), {SYNCED: "/Users/me/bin/acme-pdf --stdio"})

    def test_a_kept_command_line_resolves_on_kept_only(self) -> None:
        connectors_claude.save_kept(self.state, [SYNCED], {SYNCED: "/Users/me/bin/acme-pdf --stdio"})
        never = fake_agent(self.root, "exit 1")
        code, out, _ = self.claude(SYNCED, never, "--kept-only")
        self.assertEqual(
            (code, self.settings(out)), (0, [{"serverCommand": ["/Users/me/bin/acme-pdf", "--stdio"]}])
        )

    def test_a_found_lookup_is_kept(self) -> None:
        agent = printing(self.root, MCP_LIST)
        code, out, err = self.claude(f"{MAIL}, acme-local", agent)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.settings(out), [{"serverUrl": MAIL_URL}, {"serverName": "acme-local"}])
        self.assertEqual(connectors_claude.load_kept(self.state), {MAIL: MAIL_URL})

    def test_a_kept_name_a_later_lookup_no_longer_shows_is_dropped(self) -> None:
        """So that `--kept-only` (the fingerprint) agrees with the launch that looked it up."""
        gone = "claude.ai Acme Gone"
        connectors_claude.save_kept(
            self.state,
            [gone, "claude.ai Acme Other"],
            {gone: "https://g", "claude.ai Acme Other": "https://o"},
        )
        code, out, _ = self.claude(f"{MAIL}, {gone}", printing(self.root, MCP_LIST))
        self.assertEqual((code, self.settings(out)), (1, [{"serverUrl": MAIL_URL}]))
        self.assertEqual(
            connectors_claude.load_kept(self.state), {MAIL: MAIL_URL, "claude.ai Acme Other": "https://o"}
        )
        kept_only = self.claude(f"{MAIL}, {gone}", printing(self.root, MCP_LIST), "--kept-only")
        self.assertEqual(kept_only[:2], (code, out))

    def test_missing_to_holds_the_names_left_out(self) -> None:
        missing = self.root / "missing.json"
        code, _, _ = self.claude(
            f"{MAIL}, {ODD_NAME}, acme-local", printing(self.root, MCP_LIST), "--missing-to", str(missing)
        )
        self.assertEqual((code, json.loads(missing.read_text())), (1, [ODD_NAME]))
        for value in ("acme-local", "all", ""):
            code, _, _ = self.claude(value, self.root / "no-claude", "--missing-to", str(missing))
            self.assertEqual((code, json.loads(missing.read_text())), (0, []), value)

    def test_missing_to_after_an_unexpected_failure_holds_every_name(self) -> None:
        missing = self.root / "missing.json"
        with mock.patch.object(claude_state, "plugin_mcp_servers", side_effect=RuntimeError("boom")):
            code, out, _ = self.claude(
                f"{MAIL}, acme-local", self.root / "no-claude", "--missing-to", str(missing)
            )
        self.assertEqual((code, out), (1, '{"allowedMcpServers":[]}\n'))
        self.assertEqual(json.loads(missing.read_text()), [MAIL, "acme-local"])

    def test_missing_to_that_cannot_be_written_still_gives_the_settings(self) -> None:
        code, out, err = self.claude(
            "acme-local", self.root / "no-claude", "--missing-to", str(self.root / "no" / "such")
        )
        self.assertEqual((code, self.settings(out)), (0, [{"serverName": "acme-local"}]))
        self.assertIn("connectors: could not write the names left out", err)

    def test_no_lookup_without_a_name_that_needs_one(self) -> None:
        agent = printing(self.root, MCP_LIST)
        code, out, _ = self.claude("acme-local, https://mcp.example.com/*", agent)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.settings(out)), 2)
        self.assertFalse((self.root / "calls").exists())

    def test_kept_only_never_looks_up(self) -> None:
        connectors_claude.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        agent = printing(self.root, MCP_LIST)
        code, out, err = self.claude(f"{MAIL}, claude.ai Acme Gone", agent, "--kept-only")
        self.assertEqual(code, 1)
        self.assertEqual(self.settings(out), [{"serverUrl": MAIL_URL}])
        self.assertEqual(err, "connectors: claude.ai Acme Gone not found, so this window starts without it\n")
        self.assertFalse((self.root / "calls").exists())

    def test_failed_lookup_uses_kept(self) -> None:
        connectors_claude.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        code, out, err = self.claude(MAIL, fake_agent(self.root, "exit 1"))
        self.assertEqual(code, 0)
        self.assertEqual(self.settings(out), [{"serverUrl": MAIL_URL}])
        self.assertIn("connectors: claude mcp list failed (", err)
        self.assertIn("); using the servers kept from the last launch", err)

    def test_lookup_timeout_uses_kept(self) -> None:
        connectors_claude.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        started = time.monotonic()
        with mock.patch.object(connectors, "LOOKUP_TIMEOUT_SECONDS", 0.5):
            code, out, err = self.claude(MAIL, fake_agent(self.root, "exec sleep 20"))
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(code, 0)
        self.assertEqual(self.settings(out), [{"serverUrl": MAIL_URL}])
        self.assertIn("claude mcp list failed", err)

    def test_failed_lookup_with_nothing_kept_reports_missing(self) -> None:
        code, out, err = self.claude(f"{MAIL}, acme-local", fake_agent(self.root, "exit 1"))
        self.assertEqual(code, 1)
        self.assertEqual(self.settings(out), [{"serverName": "acme-local"}])
        self.assertIn(f"connectors: {MAIL} not found, so this window starts without it\n", err)

    def test_a_listed_command_is_its_split_argv(self) -> None:
        agent = printing(self.root, MCP_LIST.replace("acme-local:", "acme local:"))
        code, out, err = self.claude("acme local", agent)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.settings(out), [{"serverCommand": ["/Users/me/.local/bin/acme-mcp"]}])
        self.assertEqual(
            connectors_claude.load_kept(self.state), {"acme local": "/Users/me/.local/bin/acme-mcp"}
        )

    def test_plugin_server_by_url_and_by_command(self) -> None:
        plugin = self.root / "plugins" / "acme-tools"
        plugin.mkdir(parents=True)
        servers = {
            "tracker": {"type": "http", "url": "https://tracker.example.com/mcp"},
            "pdf": {"command": "${CLAUDE_PLUGIN_ROOT}/bin/pdf", "args": ["--stdio"]},
        }
        (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
        self.installed.write_text(
            json.dumps({"plugins": {"acme-tools@market": [{"installPath": str(plugin)}]}})
        )
        code, out, _ = self.claude(
            "plugin:acme-tools:tracker, plugin:acme-tools:pdf", self.root / "no-claude"
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            self.settings(out),
            [
                {"serverUrl": "https://tracker.example.com/mcp"},
                {"serverCommand": [f"{plugin}/bin/pdf", "--stdio"]},
            ],
        )

    def test_a_plugin_server_whose_file_holds_another_variable_is_looked_up(self) -> None:
        """${CLAUDE_PLUGIN_ROOT} is the only variable bosun expands: a server whose command, arguments or
        URL hold another is resolved as `claude mcp list` shows it, as for a plugin not installed."""
        plugin = self.root / "plugins" / "acme-tools"
        plugin.mkdir(parents=True)
        servers = {"pdf": {"command": "${ACME_HOME}/bin/pdf", "args": ["--stdio"]}}
        (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
        self.installed.write_text(
            json.dumps({"plugins": {"acme-tools@market": [{"installPath": str(plugin)}]}})
        )
        listing = "plugin:acme-tools:pdf: /opt/acme/bin/pdf --stdio - ✔ Connected\n"
        code, out, err = self.claude("plugin:acme-tools:pdf", printing(self.root, listing))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.settings(out), [{"serverCommand": ["/opt/acme/bin/pdf", "--stdio"]}])
        code, out, err = self.claude("plugin:acme-tools:pdf", fake_agent(self.root, "exit 1"), "--kept-only")
        self.assertEqual(
            (code, self.settings(out)), (0, [{"serverCommand": ["/opt/acme/bin/pdf", "--stdio"]}])
        )

    def test_names_with_shell_characters_pass_as_data(self) -> None:
        """Through the real command line, as the launch runs it: the name is data, never evaluated."""
        agent = printing(self.root, MCP_LIST)
        environment = {**os.environ, "HOME": str(self.root)}
        result = subprocess.run(
            [
                *(sys.executable, str(ROOT / "lib" / "connectors_claude.py"), "--list", ODD_NAME),
                *("--cwd", str(self.root), "--state-dir", str(self.state), "--agent-bin", str(agent)),
            ],
            cwd=self.root,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"allowedMcpServers": []})
        self.assertIn(f"connectors: {ODD_NAME} not found", result.stderr)
        self.assertFalse((self.root / "evaluated").exists())

    def test_a_plain_name_a_lookup_does_not_show_is_said_and_stays_allowed(self) -> None:
        """serverName cannot widen, so a name `claude mcp list` does not show stays in; when a lookup runs
        anyway the launch says so, as it may be a typo."""
        code, out, err = self.claude(f"{MAIL}, acme-local, acme-typo", printing(self.root, MCP_LIST))
        self.assertEqual(code, 0)
        self.assertEqual(
            self.settings(out),
            [{"serverUrl": MAIL_URL}, {"serverName": "acme-local"}, {"serverName": "acme-typo"}],
        )
        self.assertEqual(
            err,
            "connectors: acme-typo is not among the servers claude mcp list shows here; it stays allowed by "
            "name\n",
        )
        connectors_claude.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        for extra in (("--kept-only",), ()):
            value = f"{MAIL}, acme-typo" if extra else "acme-typo"
            code, _, err = self.claude(value, fake_agent(self.root, "exit 1"), *extra)
            self.assertEqual((code, err), (0, ""), extra)


class ScopesTest(ClaudeCase):
    """R21: an allowedMcpServers in another settings scope adds to a window's, so a launch with a list
    refuses one that holds an entry its own resolved entries do not."""

    def claude(self, value: str, *extra: str) -> tuple[int, str, str]:
        common = ["--cwd", str(self.root), "--state-dir", str(self.state), "--agent-bin", "no-claude"]
        return self.run_main("--list", value, *common, *extra)

    def scope_files(self) -> list[Path]:
        project = self.root / ".claude"
        return [self.home_settings, project / "settings.json", project / "settings.local.json"]

    def test_each_scope_is_read(self) -> None:
        for settings in self.scope_files():
            with self.subTest(settings=settings):
                self.allow_in(settings, {"serverUrl": "https://*"}, {"serverName": "acme-local"})
                self.assertEqual(
                    claude_state.settings_allow_lists(self.root),
                    [(settings, [{"serverUrl": "https://*"}, {"serverName": "acme-local"}])],
                )
                settings.unlink()

    def test_missing_unreadable_or_listless_files_hold_no_entries(self) -> None:
        user, project, local = self.scope_files()
        self.assertEqual(claude_state.settings_allow_lists(self.root), [])
        project.parent.mkdir(parents=True)
        user.parent.mkdir(parents=True)
        user.write_text("{not json")
        project.write_text(json.dumps({"allowedMcpServers": {"serverName": "x"}}))
        local.write_text(json.dumps({"allowedMcpServers": []}))
        self.assertEqual(claude_state.settings_allow_lists(self.root), [])
        self.assertIsNone(claude_state.settings_allow_list(project))
        self.assertEqual(claude_state.settings_allow_list(local), [])

    def test_a_folder_that_is_home_reads_its_settings_once(self) -> None:
        home = self.home_settings.parent.parent
        self.allow_in(self.home_settings, {"serverName": "x"})
        self.assertEqual(
            claude_state.settings_allow_lists(home), [(self.home_settings, [{"serverName": "x"}])]
        )

    def test_an_extra_entry_in_any_scope_refuses_the_launch(self) -> None:
        for settings in self.scope_files():
            with self.subTest(settings=settings):
                self.allow_in(settings, {"serverName": "acme-local"}, {"serverUrl": "https://*"})
                missing = self.root / "missing.json"
                code, out, err = self.claude("acme-local", "--missing-to", str(missing))
                self.assertEqual((code, out), (2, ""))
                self.assertEqual(
                    err,
                    f'connectors: {settings} allows [{{"serverUrl": "https://*"}}], beyond this window\'s '
                    "list, and Claude Code adds it to it: name it in the window's list, use all, or remove "
                    "it from that file\n",
                )
                self.assertFalse(missing.exists())
                settings.unlink()

    def test_an_empty_list_is_widened_too(self) -> None:
        user, project, _ = self.scope_files()
        self.allow_in(user, {"serverName": "acme-a"})
        self.allow_in(project, {"serverName": "acme-b"}, {"serverName": "acme-c"})
        code, out, err = self.claude("")
        self.assertEqual((code, out), (2, ""))
        self.assertIn(f'{user} allows [{{"serverName": "acme-a"}}]', err)
        self.assertIn(f'{project} allows [{{"serverName": "acme-b"}}, {{"serverName": "acme-c"}}]', err)
        self.assertIn("name them in the window's list, use all, or remove them from those files", err)

    def test_entries_the_list_names_start(self) -> None:
        _, project, local = self.scope_files()
        self.allow_in(project, {"serverUrl": "https://mcp.example.com/*"})
        self.allow_in(local, {"serverName": "acme-local"})
        code, out, err = self.claude("acme-local, https://mcp.example.com/*")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(json.loads(out)["allowedMcpServers"]), 2)

    def test_all_is_not_checked(self) -> None:
        self.allow_in(self.home_settings, {"serverUrl": "https://*"})
        self.assertEqual(self.claude("all"), (0, "{}\n", ""))

    def test_the_fallback_after_an_unexpected_failure_is_checked_too(self) -> None:
        self.allow_in(self.home_settings, {"serverUrl": "https://*"})
        with mock.patch.object(claude_state, "plugin_mcp_servers", side_effect=RuntimeError("boom")):
            code, out, err = self.claude("acme-local")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("beyond this window's list, and Claude Code adds it to it", err)


class DoctorTest(connector_fakes.DoctorMixin, ClaudeCase):
    """`connectors.py doctor` for a Claude Code window: bosun doctor's line, its level and a tab first."""

    def test_doctor_lines(self) -> None:
        connectors_claude.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        never = fake_agent(self.root, f'echo called >> "{self.root}/calls"; exit 1')  # claude: never run
        self.assertEqual(self.doctor("alpha", "", never), "ok\talpha: none (no MCP server or connector)")
        self.assertEqual(
            self.doctor("alpha", f"acme-local, {MAIL}, https://mcp.example.com/*", never),
            f"ok\talpha: reaches {MAIL}; allowed by name: acme-local; allowed by pattern: "
            "https://mcp.example.com/*",
        )
        self.assertEqual(
            self.doctor("alpha", "acme-local, acme-typo", never),
            "ok\talpha: allowed by name: acme-local, acme-typo",
        )
        self.assertEqual(
            self.doctor("alpha", f"acme-local, {MAIL}, claude.ai Acme Gone", never),
            "warn\talpha: reaches claude.ai Acme Mail; allowed by name: acme-local; not among the servers "
            "earlier starts looked up: claude.ai Acme Gone (bosun doctor looks nothing up: the window's next "
            "start does)",
        )
        self.assertEqual(
            self.doctor("alpha", "all", never), "ok\talpha: all (every MCP server and connector)"
        )
        self.assertEqual(
            self.doctor("orchestrator", "all", never, kind="orchestrator"),
            "warn\torchestrator: all (every MCP server and connector): name the ones it needs in "
            "BOSUN_ORCHESTRATOR_CONNECTORS and drop all",
        )
        self.assertFalse((self.root / "calls").exists())
        self.assertEqual(
            self.doctor("alpha", "all, acme-local", never),
            "warn\talpha: does not start: `all` means every server, so it stands alone, but the list also "
            "names acme-local",
        )

    def test_doctor_says_a_listed_only_plugin_server_is_reached_once_kept(self) -> None:
        never = fake_agent(self.root, "exit 1")
        self.assertEqual(
            self.doctor("alpha", SYNCED, never),
            f"warn\talpha: reaches nothing; not among the servers earlier starts looked up: {SYNCED} "
            "(bosun doctor looks nothing up: the window's next start does)",
        )
        connectors_claude.save_kept(self.state, [SYNCED], {SYNCED: "/Users/me/bin/acme-pdf --stdio"})
        self.assertEqual(self.doctor("alpha", SYNCED, never), f"ok\talpha: reaches {SYNCED}")

    def test_doctor_names_a_plugin_server_not_found(self) -> None:
        self.installed.write_text(json.dumps({"plugins": {}}))
        self.assertEqual(
            self.doctor("alpha", "acme-local, plugin:acme-tools:gone", self.root / "no-claude"),
            "warn\talpha: allowed by name: acme-local; not among the servers earlier starts looked up: "
            "plugin:acme-tools:gone (bosun doctor looks nothing up: the window's next start does)",
        )
        plugin = self.root / "plugins" / "acme-tools"
        plugin.mkdir(parents=True)
        (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {"broken": {"type": "http"}}}))
        self.installed.write_text(
            json.dumps({"plugins": {"acme-tools@market": [{"installPath": str(plugin)}]}})
        )
        self.assertEqual(
            self.doctor("alpha", "plugin:acme-tools:broken", self.root / "no-claude"),
            "warn\talpha: reaches nothing; not found, so it starts without: plugin:acme-tools:broken",
        )

    def test_doctor_says_a_window_another_scope_widens_does_not_start(self) -> None:
        never = self.root / "no-claude"
        project = self.root / ".claude" / "settings.json"
        self.allow_in(project, {"serverName": "acme-local"}, {"serverUrl": "https://*"})
        self.assertEqual(
            self.doctor("alpha", "acme-local", never),
            f'warn\talpha: allowed by name: acme-local; does not start: {project} allows [{{"serverUrl": '
            '"https://*"}], beyond this window\'s list, and Claude Code adds it to it: name it in the '
            "window's list, use all, or remove it from that file",
        )
        self.assertEqual(
            self.doctor("alpha", "", never).split("; ")[0], "warn\talpha: none (no MCP server or connector)"
        )
        self.assertEqual(
            self.doctor("alpha", "all", never), "ok\talpha: all (every MCP server and connector)"
        )
        self.assertEqual(
            self.doctor("alpha", "acme-local, https://*", never),
            "ok\talpha: allowed by name: acme-local; allowed by pattern: https://*",
        )

    def test_doctor_says_what_the_orchestrator_s_settings_file_allow_list_does(self) -> None:
        """It is merged into the window's own --settings: bosun's list replaces it, and `all` leaves it."""
        never = self.root / "no-claude"
        site = self.root / "site-settings.json"
        self.allow_in(site, {"serverName": "acme-site"})
        line = self.doctor("orch", "acme-local", never, kind="orchestrator", settings_file=site)
        self.assertEqual(
            line,
            f"ok\torch: allowed by name: acme-local; its settings file ({site}) holds an allowedMcpServers, "
            "which this list replaces",
        )
        line = self.doctor("orch", "all", never, kind="orchestrator", settings_file=site)
        self.assertEqual(
            line,
            f"warn\torch: all, so the allowedMcpServers of its settings file ({site}) applies: "
            '[{"serverName": "acme-site"}]: name the ones it needs in BOSUN_ORCHESTRATOR_CONNECTORS and drop '
            "all",
        )
        site.write_text(json.dumps({"permissions": {}}))
        line = self.doctor("orch", "acme-local", never, kind="orchestrator", settings_file=site)
        self.assertEqual(line, "ok\torch: allowed by name: acme-local")


if __name__ == "__main__":
    unittest.main()
