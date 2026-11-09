"""lib/connectors.py: a window's list of connectors turned into what Claude Code and Codex take."""

from __future__ import annotations

import io
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


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import claude_state  # noqa: E402
import connectors  # noqa: E402
from connectors import Allowed, ConnectorsError, claude_entries, codex_args, parse  # noqa: E402


MAIL = "claude.ai Acme Mail"
MAIL_URL = "https://mail.example.com/mcp"
MCP_LIST = f"""Checking MCP server health…

{MAIL}: {MAIL_URL} - ✔ Connected
acme-local: /Users/me/.local/bin/acme-mcp  - ✔ Connected
"""
ODD_NAME = 'claude.ai Acme "Mail" $(touch evaluated)'


def fake_agent(folder: Path, body: str) -> Path:
    """An executable standing in for `claude` or `codex`: a shell script with <body>."""
    path = folder / "fake-agent"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def printing(folder: Path, text: str) -> Path:
    (folder / "listing.txt").write_text(text)
    return fake_agent(folder, f'echo called >> "{folder}/calls"\ncat "{folder}/listing.txt"')


class PureTest(unittest.TestCase):
    def test_empty_list_allows_nothing(self) -> None:
        for value in ("", " ", " , ,"):
            allowed = parse(value)
            self.assertEqual(allowed, Allowed())
            self.assertEqual(claude_entries(allowed, {}, {}), connectors.Resolution((), ()))

    def test_all_has_no_allow_list(self) -> None:
        allowed = parse(" all ")
        self.assertTrue(allowed.everything)
        self.assertEqual(claude_entries(allowed, {}, {}), connectors.Resolution(None, ()))
        self.assertFalse(connectors.needs_lookup(allowed, {}))

    def test_all_with_names_is_refused(self) -> None:
        with self.assertRaises(ConnectorsError):
            parse("all, acme-local")
        self.assertIsInstance(ConnectorsError("x"), ValueError)

    def test_names_are_trimmed_and_kept_in_order(self) -> None:
        self.assertEqual(parse(f" acme-local ,{MAIL},, acme-local").names, ("acme-local", MAIL))

    def test_plain_name_is_server_name(self) -> None:
        resolution = claude_entries(parse("acme-local, acme_docs2"), {}, {})
        self.assertEqual(resolution.entries, ({"serverName": "acme-local"}, {"serverName": "acme_docs2"}))
        self.assertEqual(resolution.missing, ())
        self.assertFalse(connectors.needs_lookup(parse("acme-local"), {}))

    def test_connector_name_resolves_to_url(self) -> None:
        allowed = parse(MAIL)
        self.assertTrue(connectors.needs_lookup(allowed, {}))
        self.assertEqual(claude_entries(allowed, {}, {MAIL: MAIL_URL}).entries, ({"serverUrl": MAIL_URL},))

    def test_url_pattern_passes_through(self) -> None:
        allowed = parse("https://mcp.example.com/*, http://localhost:9000/mcp")
        self.assertFalse(connectors.needs_lookup(allowed, {}))
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

    def test_codex_disables_each_unlisted_server(self) -> None:
        self.assertEqual(
            codex_args(parse("acme-docs"), ["acme-docs", "acme-tracker"]),
            ["-c", "mcp_servers.acme-tracker.enabled=false", "--disable", "apps"],
        )
        self.assertEqual(codex_args(parse(""), []), ["--disable", "apps"])

    def test_codex_quotes_odd_names(self) -> None:
        self.assertEqual(
            codex_args(parse(""), ["acme.mail", "acme docs", 'acme"q']),
            [
                "-c",
                'mcp_servers."acme.mail".enabled=false',
                "-c",
                'mcp_servers."acme docs".enabled=false',
                "-c",
                'mcp_servers."acme\\"q".enabled=false',
                "--disable",
                "apps",
            ],
        )
        self.assertEqual(connectors.toml_key("acme_docs-2"), "acme_docs-2")

    def test_codex_all_keeps_apps(self) -> None:
        self.assertEqual(codex_args(parse("all"), ["acme-docs", "acme.mail"]), [])


class KeptTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name) / "state"

    def test_kept_file_corrupt_is_empty(self) -> None:
        self.assertEqual(connectors.load_kept(self.state), {})
        self.state.mkdir()
        kept = self.state / "connectors.json"
        for text in ("{not json", "[1, 2]", json.dumps({MAIL: 3}), "\x00\xff"):
            kept.write_text(text)
            self.assertEqual(connectors.load_kept(self.state), {}, text)
        connectors.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        self.assertEqual(connectors.load_kept(self.state), {MAIL: MAIL_URL})

    def test_a_save_replaces_only_the_names_asked_about(self) -> None:
        chat, gone = "claude.ai Acme Chat", "claude.ai Acme Gone"
        connectors.save_kept(
            self.state,
            [MAIL, chat, gone],
            {MAIL: "https://old.example.com/mcp", chat: "https://c", gone: "https://g"},
        )
        connectors.save_kept(self.state, [MAIL, gone], {MAIL: MAIL_URL})
        self.assertEqual(connectors.load_kept(self.state), {MAIL: MAIL_URL, chat: "https://c"})
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["connectors.json"])

    def test_a_save_whose_rename_finds_its_temp_gone_never_crashes(self) -> None:
        with (
            mock.patch("os.replace", side_effect=FileNotFoundError(2, "No such file or directory")),
            mock.patch("sys.stderr", io.StringIO()) as err,
        ):
            connectors.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        self.assertTrue(err.getvalue().startswith("connectors: could not keep the addresses found"))
        self.assertEqual(connectors.load_kept(self.state), {})

    def test_concurrent_saves_never_crash_and_leave_valid_json(self) -> None:
        """Launches starting at once (the morning start) each save what they found."""
        script = (
            "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import connectors\n"
            "for i in range(40):\n"
            "    n = f'claude.ai {sys.argv[3]} {i}'\n"
            "    connectors.save_kept(Path(sys.argv[2]), [n], {n: 'https://x'})"
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


class CommandTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state = self.root / "state"
        self.installed = self.root / "installed_plugins.json"
        patch = mock.patch.object(claude_state, "INSTALLED_PLUGINS", self.installed)
        patch.start()
        self.addCleanup(patch.stop)

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        with mock.patch("sys.stdout", io.StringIO()) as out, mock.patch("sys.stderr", io.StringIO()) as err:
            code = connectors.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def claude(self, value: str, agent: Path, *extra: str) -> tuple[int, str, str]:
        common = ["--cwd", str(self.root), "--state-dir", str(self.state), "--agent-bin", str(agent)]
        return self.run_main("claude", "--list", value, *common, *extra)

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
        self.assertIn("connectors: could not keep the addresses found", err)
        self.assertIn("claude.ai Acme Gone not found", err)

    def test_all_with_names_is_refused(self) -> None:
        code, out, err = self.claude("all, acme-local", self.root / "no-claude")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("connectors:", err)
        self.assertEqual(self.run_main("codex", "--list", "all,x", "--agent-bin", "x")[0], 2)

    def test_a_found_lookup_is_kept(self) -> None:
        agent = printing(self.root, MCP_LIST)
        code, out, err = self.claude(f"{MAIL}, acme-local", agent)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.settings(out), [{"serverUrl": MAIL_URL}, {"serverName": "acme-local"}])
        self.assertEqual(connectors.load_kept(self.state), {MAIL: MAIL_URL})

    def test_a_kept_name_a_later_lookup_no_longer_shows_is_dropped(self) -> None:
        """So that `--kept-only` (the fingerprint) agrees with the launch that looked it up."""
        gone = "claude.ai Acme Gone"
        connectors.save_kept(
            self.state,
            [gone, "claude.ai Acme Other"],
            {gone: "https://g", "claude.ai Acme Other": "https://o"},
        )
        code, out, _ = self.claude(f"{MAIL}, {gone}", printing(self.root, MCP_LIST))
        self.assertEqual((code, self.settings(out)), (1, [{"serverUrl": MAIL_URL}]))
        self.assertEqual(
            connectors.load_kept(self.state), {MAIL: MAIL_URL, "claude.ai Acme Other": "https://o"}
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
        connectors.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        agent = printing(self.root, MCP_LIST)
        code, out, err = self.claude(f"{MAIL}, claude.ai Acme Gone", agent, "--kept-only")
        self.assertEqual(code, 1)
        self.assertEqual(self.settings(out), [{"serverUrl": MAIL_URL}])
        self.assertEqual(err, "connectors: claude.ai Acme Gone not found, so this window starts without it\n")
        self.assertFalse((self.root / "calls").exists())

    def test_failed_lookup_uses_kept(self) -> None:
        connectors.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
        code, out, err = self.claude(MAIL, fake_agent(self.root, "exit 1"))
        self.assertEqual(code, 0)
        self.assertEqual(self.settings(out), [{"serverUrl": MAIL_URL}])
        self.assertIn("connectors: claude mcp list failed (", err)
        self.assertIn("); using the addresses kept from the last launch", err)

    def test_lookup_timeout_uses_kept(self) -> None:
        connectors.save_kept(self.state, [MAIL], {MAIL: MAIL_URL})
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

    def test_a_listed_command_is_no_url(self) -> None:
        agent = printing(self.root, MCP_LIST.replace("acme-local:", "acme local:"))
        code, out, err = self.claude("acme local", agent)
        self.assertEqual((code, self.settings(out)), (1, []))
        self.assertIn("acme local not found", err)
        self.assertEqual(connectors.load_kept(self.state), {})

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

    def test_names_with_shell_characters_pass_as_data(self) -> None:
        """Through the real command line, as the launch runs it: the name is data, never evaluated."""
        agent = printing(self.root, MCP_LIST)
        environment = {**os.environ, "HOME": str(self.root)}
        result = subprocess.run(
            [
                *(sys.executable, str(ROOT / "lib" / "connectors.py"), "claude", "--list", ODD_NAME),
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

    def test_codex_prints_its_arguments(self) -> None:
        agent = printing(
            self.root, json.dumps([{"name": "acme-docs"}, {"name": "acme.mail", "env": {"T": "x"}}])
        )
        code, out, err = self.run_main("codex", "--list", "acme-docs", "--agent-bin", str(agent))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out.splitlines(), ["-c", 'mcp_servers."acme.mail".enabled=false', "--disable", "apps"]
        )

    def test_codex_listing_that_fails_stops_the_window(self) -> None:
        code, out, err = self.run_main(
            "codex", "--list", "", "--agent-bin", str(fake_agent(self.root, "exit 3"))
        )
        self.assertEqual((code, out), (1, ""))
        self.assertIn("connectors: codex mcp list failed (", err)

    def test_codex_all_needs_no_listing(self) -> None:
        self.assertEqual(
            self.run_main("codex", "--list", "all", "--agent-bin", str(self.root / "none")), (0, "", "")
        )


if __name__ == "__main__":
    unittest.main()
