"""lib/connectors_codex.py: a Codex window's list of connectors turned into its launch arguments."""

from __future__ import annotations

import json
import sys
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import codex_state  # noqa: E402
import connector_fakes  # noqa: E402
import connectors  # noqa: E402
import connectors_codex  # noqa: E402
from connector_fakes import fake_agent, printing  # noqa: E402
from connectors import parse  # noqa: E402
from connectors_codex import codex_args  # noqa: E402


MAIL = "claude.ai Acme Mail"


def servers(*configured: str, plugins: tuple[str, ...] = ()) -> codex_state.McpServers:
    return codex_state.McpServers(configured, plugins)


def codex_listing(folder: Path, every: list[str], configured: list[str]) -> Path:
    """A `codex` whose `mcp list --json` shows <every> server, and <configured> with --disable plugins."""
    for name, names in (("every", every), ("configured", configured)):
        (folder / f"{name}.json").write_text(json.dumps([{"name": n, "env": {"TOKEN": "x"}} for n in names]))
    return fake_agent(
        folder,
        f'case "$*" in\n  "mcp list --json") cat "{folder}/every.json" ;;\n'
        f'  "--disable plugins mcp list --json") cat "{folder}/configured.json" ;;\n  *) exit 9 ;;\nesac',
    )


class PureTest(unittest.TestCase):
    def test_codex_disables_each_unlisted_server_in_one_table(self) -> None:
        self.assertEqual(
            codex_args(parse("acme-docs"), servers("acme-docs", "acme-tracker", "acme-wiki")),
            [
                "-c",
                "mcp_servers={acme-tracker={enabled=false},acme-wiki={enabled=false}}",
                "--disable",
                "apps",
            ],
        )

    def test_codex_with_nothing_to_switch_off_only_turns_off_apps(self) -> None:
        self.assertEqual(codex_args(parse(""), servers()), ["--disable", "apps"])
        self.assertEqual(codex_args(parse("acme-docs"), servers("acme-docs")), ["--disable", "apps"])

    def test_codex_quotes_odd_names_as_toml_basic_strings(self) -> None:
        names = ["acme.mail", "acme docs", 'acme"q', "acme\\b", "acme\x7f", "acme\nline", "acmé"]
        args = codex_args(parse(""), servers(*names))
        self.assertEqual(args[0], "-c")
        self.assertEqual(args[2:], ["--disable", "apps"])
        self.assertEqual(
            args[1],
            'mcp_servers={"acme.mail"={enabled=false},"acme docs"={enabled=false},'
            '"acme\\"q"={enabled=false},"acme\\\\b"={enabled=false},"acme\\u007F"={enabled=false},'
            '"acme\\u000Aline"={enabled=false},"acmé"={enabled=false}}',
        )
        # What Codex's TOML parser reads it as: each server by its own name, switched off.
        self.assertEqual(tomllib.loads(args[1])["mcp_servers"], {name: {"enabled": False} for name in names})
        self.assertNotIn("\n", args[1])  # one argument a line, for lib/launch/codex.sh
        self.assertEqual(connectors_codex.toml_key("acme_docs-2"), "acme_docs-2")

    def test_codex_all_keeps_apps(self) -> None:
        self.assertEqual(codex_args(parse("all"), servers("acme-docs", "acme.mail", plugins=("kit",))), [])

    def test_codex_a_named_plugin_server_keeps_plugins_on(self) -> None:
        self.assertEqual(
            codex_args(parse("acme-kit"), servers("acme-docs", plugins=("acme-kit",))),
            ["-c", "mcp_servers={acme-docs={enabled=false}}", "--disable", "apps"],
        )

    def test_codex_a_plugin_server_not_named_switches_every_plugin_off(self) -> None:
        """Never in the table: a plugin's server is no `mcp_servers` table, and naming one stops Codex."""
        self.assertEqual(
            codex_args(parse("acme-docs"), servers("acme-docs", plugins=("acme-kit",))),
            ["--disable", "plugins", "--disable", "apps"],
        )
        self.assertEqual(
            codex_args(parse(""), servers("acme-docs", plugins=("acme-kit",))),
            ["-c", "mcp_servers={acme-docs={enabled=false}}", "--disable", "plugins", "--disable", "apps"],
        )

    def test_codex_a_mix_of_plugin_servers_named_and_not(self) -> None:
        listing = servers("acme-docs", "acme.mail", plugins=("acme-kit", "acme-pad"))
        plan = connectors_codex.codex_plan(parse("acme-docs, acme-kit"), listing)
        self.assertEqual(plan, connectors_codex.CodexPlan(("acme.mail",), ("acme-pad",), ("acme-kit",)))
        self.assertEqual(
            codex_args(parse("acme-docs, acme-kit"), listing),
            ["-c", 'mcp_servers={"acme.mail"={enabled=false}}', "--disable", "plugins", "--disable", "apps"],
        )
        self.assertEqual(
            codex_args(parse("acme-kit, acme-pad"), listing),
            [
                "-c",
                'mcp_servers={acme-docs={enabled=false},"acme.mail"={enabled=false}}',
                "--disable",
                "apps",
            ],
        )


class CommandTest(connector_fakes.ScratchCase):
    """connectors_codex.py's command line."""

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        return connector_fakes.capture(connectors_codex.main, *argv)

    def test_all_with_names_is_refused(self) -> None:
        self.assertEqual(self.run_main("--list", "all,x", "--agent-bin", "x")[0], 2)

    def test_codex_prints_its_arguments(self) -> None:
        agent = printing(
            self.root, json.dumps([{"name": "acme-docs"}, {"name": "acme.mail", "env": {"T": "x"}}])
        )
        code, out, err = self.run_main("--list", "acme-docs", "--agent-bin", str(agent))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out.splitlines(), ["-c", 'mcp_servers={"acme.mail"={enabled=false}}', "--disable", "apps"]
        )

    def test_codex_lists_with_plugins_and_without(self) -> None:
        agent = codex_listing(self.root, ["acme-docs", "acme-kit", "acme-kit"], ["acme-docs"])
        code, out, err = self.run_main("--list", "acme-docs", "--agent-bin", str(agent))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), ["--disable", "plugins", "--disable", "apps"])

    def test_codex_listing_that_fails_stops_the_window(self) -> None:
        code, out, err = self.run_main("--list", "", "--agent-bin", str(fake_agent(self.root, "exit 3")))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("connectors: codex mcp list failed (", err)

    def test_codex_all_needs_no_listing(self) -> None:
        self.assertEqual(self.run_main("--list", "all", "--agent-bin", str(self.root / "none")), (0, "", ""))

    def test_codex_says_which_names_it_has_no_server_by(self) -> None:
        agent = printing(self.root, json.dumps([{"name": "acme-docs"}, {"name": "acme.mail"}]))
        code, out, err = self.run_main(
            "--list",
            f"acme-docs, acme-gone, https://mcp.example.com/*, {MAIL}",
            "--agent-bin",
            str(agent),
        )
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[-2:], ["--disable", "apps"])
        meaningless = connectors_codex.MEANS_NOTHING
        self.assertEqual(
            err.splitlines(),
            [
                "connectors: acme-gone not found, so this window starts without it",
                f"connectors: https://mcp.example.com/* {meaningless}",
                f"connectors: {MAIL} {meaningless}",
            ],
        )


class DoctorTest(connector_fakes.DoctorMixin, connector_fakes.ScratchCase):
    """`connectors.py doctor` for a Codex window."""

    def test_doctor_says_when_every_plugin_goes(self) -> None:
        agent = codex_listing(self.root, ["acme-docs", "acme-kit", "acme-pad"], ["acme-docs"])
        self.assertEqual(
            self.doctor("cx", "acme-docs, acme-kit", agent, harness="codex"),
            "warn\tcx: reaches acme-docs (switches off every plugin (with acme-pad, acme-kit) and ChatGPT's "
            "connectors); every plugin is switched off for the plugin servers its list does not name, so it "
            "also starts without: acme-kit",
        )
        self.assertEqual(
            self.doctor("cx", "acme-kit, acme-pad", agent, harness="codex"),
            "ok\tcx: reaches acme-kit, acme-pad (switches off acme-docs and ChatGPT's connectors)",
        )

    def test_doctor_lines_for_a_codex_window(self) -> None:
        """From `codex mcp list --json`, names only (a stdio server's entry holds its environment); a URL
        or a claude.ai name means nothing to Codex."""
        agent = printing(
            self.root, json.dumps([{"name": "acme-docs"}, {"name": "acme.mail", "env": {"TOKEN": "x"}}])
        )
        line = self.doctor(
            "cx", f"acme-docs, https://mcp.example.com/*, {MAIL}, acme-gone", agent, harness="codex"
        )
        self.assertEqual(
            line,
            "warn\tcx: reaches acme-docs (switches off acme.mail and ChatGPT's connectors); not found, so it "
            f"starts without: acme-gone; mean nothing to Codex: https://mcp.example.com/*, {MAIL}",
        )
        self.assertEqual(
            self.doctor("cx", "", agent, harness="codex"),
            "ok\tcx: none (switches off acme-docs, acme.mail and ChatGPT's connectors)",
        )
        self.assertEqual(
            self.doctor("cx", "acme-docs", agent, harness="codex"),
            "ok\tcx: reaches acme-docs (switches off acme.mail and ChatGPT's connectors)",
        )
        self.assertEqual(
            self.doctor("cx", "all", self.root / "no-codex", harness="codex"),
            "ok\tcx: all (every MCP server and connector)",
        )
        self.assertEqual(
            self.doctor("cx", "acme-docs", fake_agent(self.root, "exit 3"), harness="codex"),
            "warn\tcx: does not start: codex mcp list failed (exited 3), so bosun cannot tell which servers "
            "to switch off",
        )

    def test_doctor_lists_codex_servers_in_the_window_s_folder(self) -> None:
        folder = self.root / "repo"
        folder.mkdir()
        agent = fake_agent(self.root, f'pwd > "{self.root}/where"; echo "[]"')
        code, _, _ = connector_fakes.capture(
            connectors.main,
            *("doctor", "--window", "cx", "--kind", "session", "--harness", "codex", "--list", ""),
            *("--cwd", str(folder), "--state-dir", str(self.state), "--agent-bin", str(agent)),
        )
        self.assertEqual(code, 0)
        self.assertEqual(Path((self.root / "where").read_text().strip()).resolve(), folder.resolve())


if __name__ == "__main__":
    unittest.main()
