"""lib/connectors_agy.py: an agy window starts only if it reaches no server beyond its list."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))
import connectors  # noqa: E402
import connectors_agy  # noqa: E402
from connector_fakes import ScratchCase, capture, fake_agent, printing  # noqa: E402
from connectors import parse  # noqa: E402


SECRET = "hunter2-token"  # noqa: S105  # pragma: allowlist secret - made up; no message may hold it
FIXTURE = ROOT / "tests" / "fixtures" / "agy" / "mcp-list.txt"


def stdio(**extra: object) -> dict[str, object]:
    return {"command": "python3", "args": ["--token", SECRET], "env": {"TOKEN": SECRET}, **extra}


class AgyCase(ScratchCase):
    """A scratch home with agy's global config folder, and a repo in it with a folder below its root."""

    def setUp(self) -> None:
        super().setUp()
        self.config = self.root / "home" / ".gemini" / "config"
        self.config.mkdir(parents=True)
        patch = mock.patch.object(connectors_agy, "AGY_CONFIG", self.config)
        patch.start()
        self.addCleanup(patch.stop)
        self.repo = self.root / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.below = self.repo / "pkg" / "deep"
        self.below.mkdir(parents=True)
        self.agent = printing(self.root, "[]")

    def write_servers(self, folder: Path, servers: dict[str, object], name: str = "mcp_config.json") -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(json.dumps({"mcpServers": servers}))

    def plugins(self, listing: object) -> Path:
        return printing(self.root, json.dumps(listing))

    def run_main(
        self, value: str, agent: Path | None = None, cwd: Path | None = None
    ) -> tuple[int, str, str]:
        return capture(
            connectors_agy.main,
            f"--list={value}",
            *("--agent-bin", str(agent or self.agent), "--cwd", str(cwd or self.below)),
        )

    def doctor(self, value: str, agent: Path | None = None) -> tuple[str, str]:
        args = argparse.Namespace(
            window="ag", kind="session", agent_bin=str(agent or self.agent), cwd=self.below
        )
        return connectors_agy.doctor_line(args, parse(value))


class ServersTest(AgyCase):
    def test_no_servers_starts(self) -> None:
        (self.config / "mcp_config.json").write_text("")
        self.assertEqual(connectors_agy.servers(str(self.below), str(self.agent)), [])
        self.assertEqual(self.run_main(""), (0, "", ""))
        self.assertEqual(self.doctor("")[0], connectors.OK)

    def test_a_missing_global_file_and_an_empty_object_are_no_servers(self) -> None:
        self.assertEqual(connectors_agy.servers(str(self.below), str(self.agent)), [])
        self.write_servers(self.config, {})
        self.assertEqual(connectors_agy.servers(str(self.below), str(self.agent)), [])

    def test_a_disabled_server_is_not_counted(self) -> None:
        self.write_servers(
            self.config, {"acme-on": stdio(disabled=False), "acme-off": stdio(disabled=True), "acme-bare": {}}
        )
        self.assertEqual(connectors_agy.servers(str(self.below), str(self.agent)), ["acme-on", "acme-bare"])
        self.assertEqual(self.run_main("acme-on, acme-bare"), (0, "", ""))

    def test_a_workspace_server_counts(self) -> None:
        """Found from a folder below the repo's root, in each folder up to the root and no further."""
        self.write_servers(self.repo / ".agents", {"acme-repo": {"serverUrl": "https://mcp.example.com/x"}})
        self.write_servers(self.below.parent / ".agents", {"acme-pkg": stdio(), "acme-repo": stdio()})
        self.write_servers(self.root / ".agents", {"acme-outside": stdio()})
        found = connectors_agy.servers(str(self.below), str(self.agent))
        self.assertEqual(sorted(found), ["acme-pkg", "acme-repo"])
        code, out, err = self.run_main("acme-repo")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("acme-pkg", err)
        self.assertIn(str(self.below.parent / ".agents"), err)
        self.assertNotIn("acme-repo", err)

    def test_without_a_repo_only_the_windows_folder_counts(self) -> None:
        loose = self.root / "loose" / "inner"
        self.write_servers(loose / ".agents", {"acme-here": stdio()})
        self.write_servers(loose.parent / ".agents", {"acme-above": stdio()})
        self.assertEqual(connectors_agy.servers(str(loose), str(self.agent)), ["acme-here"])

    def test_a_plugins_server_counts_by_its_full_name(self) -> None:
        agent = self.plugins(
            [
                {"name": "acme-hooks", "enabled": True, "hooks": ["h"]},
                {"name": "acme-plug", "enabled": True, "mcpServers": ["tracker", "wiki"]},
                {"name": "acme-off", "enabled": False, "mcpServers": ["gone"]},
            ]
        )
        self.assertEqual(
            connectors_agy.servers(str(self.below), str(agent)), ["acme-plug_tracker", "acme-plug_wiki"]
        )
        code, _out, err = self.run_main("acme-plug_tracker", agent)
        self.assertEqual(code, 1)
        self.assertIn("acme-plug_wiki", err)
        self.assertIn("plugin acme-plug", err)
        self.assertEqual(self.run_main("acme-plug_tracker, acme-plug_wiki", agent), (0, "", ""))

    def test_a_server_found_twice_is_listed_once(self) -> None:
        self.write_servers(self.config, {"acme-docs": stdio()})
        self.write_servers(self.repo / ".agents", {"acme-docs": stdio()})
        self.assertEqual(connectors_agy.servers(str(self.below), str(self.agent)), ["acme-docs"])

    def test_the_real_listing_shape_reads(self) -> None:
        """The plugin listing agy 1.5.5 printed, from the fixture of its run."""
        text = FIXTURE.read_text()
        start = text.index('[\n  {\n    "name": "bosun-disc"')
        end = text.index("\n]\n", start) + 2
        agent = self.plugins(json.loads(text[start:end]))
        self.assertEqual(connectors_agy.servers(str(self.below), str(agent)), ["disc-mcp_disc-plug"])


class RefusalTest(AgyCase):
    def test_a_global_server_not_listed_refuses(self) -> None:
        self.write_servers(self.config, {"acme-docs": stdio(), "acme-mail": stdio()})
        code, out, err = self.run_main("acme-docs")
        self.assertEqual((code, out), (1, ""))
        lines = err.splitlines()
        self.assertEqual(len(lines), 1, err)
        self.assertIn("acme-mail", lines[0])
        self.assertIn("global", lines[0])
        self.assertNotIn(SECRET, err)
        self.assertEqual(self.run_main("")[0], 1)
        self.assertEqual(len(self.run_main("")[2].splitlines()), 2)

    def test_all_allows_anything(self) -> None:
        self.write_servers(self.config, {"acme-docs": stdio()})
        self.assertEqual(self.run_main("all", self.root / "no-agy"), (0, "", ""))
        self.assertEqual(
            self.doctor("all", self.root / "no-agy"), (connectors.OK, "ag: " + connectors.EVERYTHING_LINE)
        )

    def test_a_name_agy_has_no_server_by_is_said_without_refusing(self) -> None:
        self.write_servers(self.config, {"acme-docs": stdio()})
        code, out, err = self.run_main("acme-docs, acme-gone, https://mcp.example.com/*")
        self.assertEqual((code, out), (0, ""))
        self.assertIn("acme-gone not found", err)
        self.assertIn("https://mcp.example.com/*", err)

    def test_a_listing_that_fails_refuses(self) -> None:
        for agent in (
            fake_agent(self.root, "exit 3"),
            printing(self.root, "not json"),
            printing(self.root, '{"a": 1}'),
            self.root / "no-agy",
        ):
            code, out, err = self.run_main("acme-docs", agent)
            self.assertEqual((code, out), (1, ""), err)
            self.assertIn("agy plugin list", err)

    def test_a_listing_that_times_out_refuses(self) -> None:
        agent = fake_agent(self.root, "exec sleep 5")
        with mock.patch.object(connectors, "LOOKUP_TIMEOUT_SECONDS", 0.2):
            code, _out, err = self.run_main("acme-docs", agent)
        self.assertEqual(code, 1)
        self.assertIn("timed out", err)

    def test_a_config_that_does_not_parse_refuses_by_file_name_only(self) -> None:
        (self.config / "mcp_config.json").write_text(f'{{"mcpServers": {{"a": "{SECRET}"')
        code, _out, err = self.run_main("acme-docs")
        self.assertEqual(code, 1)
        self.assertIn(str(self.config / "mcp_config.json"), err)
        self.assertNotIn(SECRET, err)
        (self.config / "mcp_config.json").write_text(json.dumps({"mcpServers": [SECRET]}))
        code, _out, err = self.run_main("acme-docs")
        self.assertEqual(code, 1)
        self.assertNotIn(SECRET, err)
        self.assertEqual(self.run_main("all")[0], 0)

    def test_a_list_that_cannot_be_read_is_exit_2(self) -> None:
        code, _out, err = self.run_main("all, acme-docs")
        self.assertEqual(code, 2)
        self.assertIn("stands alone", err)

    def test_nothing_is_written(self) -> None:
        self.write_servers(self.config, {"acme-docs": stdio()})
        before = sorted(p for p in self.root.rglob("*") if p.is_file())
        stamps = [p.stat().st_mtime_ns for p in before]
        self.run_main("")
        self.doctor("")
        after = sorted(p for p in self.root.rglob("*") if p.is_file())
        self.assertEqual(before, [p for p in after if p.name != "calls"])
        self.assertEqual(stamps, [p.stat().st_mtime_ns for p in before])


class DoctorTest(AgyCase):
    def test_doctor_names_what_blocks(self) -> None:
        self.write_servers(self.config, {"acme-docs": stdio(), "acme-mail": stdio()})
        self.write_servers(self.repo / ".agents", {"acme-repo": stdio()})
        level, line = self.doctor("acme-docs")
        self.assertEqual(level, connectors.WARN)
        self.assertEqual(
            line.split(": ", 1)[0] + ": " + line.split(": ", 2)[1].split(" (")[0],
            "ag: does not start",
        )
        self.assertIn("acme-mail", line)
        self.assertIn("acme-repo", line)
        self.assertNotIn(SECRET, line)

    def test_doctor_is_ok_when_it_would_start(self) -> None:
        self.write_servers(self.config, {"acme-docs": stdio()})
        self.assertEqual(
            self.doctor("acme-docs, acme-gone"),
            (
                connectors.WARN,
                (
                    "ag: reaches acme-docs (agy starts nothing else here); "
                    "not found, so it starts without: acme-gone"
                ),
            ),
        )
        self.assertEqual(
            self.doctor("acme-docs"), (connectors.OK, "ag: reaches acme-docs (agy starts nothing else here)")
        )
        (self.config / "mcp_config.json").unlink()
        self.assertEqual(self.doctor(""), (connectors.OK, "ag: none (agy starts no server here)"))

    def test_doctor_says_when_the_listing_fails(self) -> None:
        level, line = self.doctor("acme-docs", fake_agent(self.root, "exit 3"))
        self.assertEqual(level, connectors.WARN)
        self.assertTrue(line.startswith("ag: does not start: "), line)
        self.assertIn("agy plugin list", line)


class SharedCodeTest(unittest.TestCase):
    def test_no_agent_name_in_the_module(self) -> None:
        text = (ROOT / "lib" / "connectors_agy.py").read_text()
        self.assertNotIn('"claude"', text)
        self.assertNotIn('"codex"', text)


if __name__ == "__main__":
    unittest.main()
