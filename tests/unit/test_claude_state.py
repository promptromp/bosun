"""Unit tests for lib/claude_state.py (Claude Code's files) and the bosun-resolve command."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import agent_files  # noqa: E402
import claude_state as resolve  # noqa: E402  (most tests exercise the lookup)


loader = importlib.machinery.SourceFileLoader("bosun_resolve", str(ROOT / "libexec" / "bosun-resolve"))
sys.dont_write_bytecode = True  # no __pycache__ next to the command in libexec/
spec = importlib.util.spec_from_loader("bosun_resolve", loader)
assert spec is not None
resolve_command = importlib.util.module_from_spec(spec)
loader.exec_module(resolve_command)

WORKDIR = "/work/repo"


class ResolveTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        patches = [
            mock.patch.object(resolve, "PROJECTS_DIR", root / "projects"),
            mock.patch.object(resolve, "LIVE_SESSIONS_DIR", root / "sessions"),
            mock.patch.object(resolve, "CONFIG_FILE", root / "claude.json"),
        ]
        self.root = root
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        resolve.LIVE_SESSIONS_DIR.mkdir(parents=True)
        self.project = resolve.project_dir(WORKDIR)
        self.project.mkdir(parents=True)

    def transcript(self, session_id: str, *titles: str, age: float = 0) -> Path:
        path = self.project / f"{session_id}.jsonl"
        lines = [json.dumps({"type": "user"})]
        lines += [
            json.dumps({"type": "custom-title", "customTitle": t, "sessionId": session_id}) for t in titles
        ]
        path.write_text("\n".join(lines) + "\n")
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def registry(self, name: str, **entry: object) -> None:
        (resolve.LIVE_SESSIONS_DIR / f"{name}.json").write_text(json.dumps(entry))

    def main(self, *argv: str) -> tuple[int, str]:
        """Run the `bosun resolve` command line."""
        with mock.patch("sys.stdout", io.StringIO()) as out, mock.patch("sys.stderr", io.StringIO()):
            code = resolve_command.main(list(argv))
        return code, out.getvalue().strip()


class NamesTest(ResolveTestCase):
    def test_most_recent_exact_match_wins(self) -> None:
        self.transcript("old", "alpha", age=100)
        self.transcript("new", "alpha", age=10)
        self.transcript("other", "alpha-extended", age=1)
        self.assertEqual(self.main(WORKDIR, "alpha"), (0, "new"))

    def test_the_latest_title_counts_after_a_rename(self) -> None:
        self.transcript("renamed", "alpha", "beta", age=1)
        self.assertEqual(self.main(WORKDIR, "beta"), (0, "renamed"))
        self.assertEqual(self.main(WORKDIR, "alpha")[0], agent_files.NOT_FOUND)

    def test_unreadable_entries_are_skipped(self) -> None:
        (self.project / "broken.jsonl").mkdir()
        self.transcript("good", "alpha")
        self.assertEqual(self.main(WORKDIR, "alpha"), (0, "good"))

    def test_exit_codes(self) -> None:
        self.assertEqual(self.main(WORKDIR, "missing")[0], agent_files.NOT_FOUND)
        self.assertEqual(self.main(WORKDIR)[0], 2)
        code, out = self.main("--help")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Usage: bosun resolve <workdir> <name>"), out)

    def test_list(self) -> None:
        self.transcript("s1", "alpha")
        self.assertEqual(self.main(WORKDIR, "--list"), (0, "s1  alpha"))


class LiveTest(ResolveTestCase):
    def test_a_running_holder_is_found(self) -> None:
        self.registry("live", pid=os.getpid(), sessionId="S")
        self.assertEqual(self.main("--live", "S"), (0, str(os.getpid())))

    def test_dead_pids_and_malformed_entries_are_ignored(self) -> None:
        self.registry("dead", pid=2**22 + 12345, sessionId="S")
        self.registry("bad", sessionId="S")
        (resolve.LIVE_SESSIONS_DIR / "junk.json").write_text("{not json")
        self.assertEqual(self.main("--live", "S")[0], agent_files.NOT_FOUND)

    def test_a_reused_pid_is_stale(self) -> None:
        # This process started long after 1970, so the record cannot be its own.
        self.registry("stale", pid=os.getpid(), sessionId="S", startedAt=1000)
        self.assertEqual(self.main("--live", "S")[0], agent_files.NOT_FOUND)

    def test_a_record_written_after_the_process_started_is_live(self) -> None:
        self.registry("ok", pid=os.getpid(), sessionId="S", startedAt=int(time.time() * 1000))
        self.assertEqual(self.main("--live", "S"), (0, str(os.getpid())))


class RecordsTest(ResolveTestCase):
    def test_registered_and_field(self) -> None:
        self.registry(str(os.getpid()), pid=os.getpid(), status="idle", version="2.0")
        with mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(resolve.main(["registered", "1", str(os.getpid())]), 0)
            self.assertEqual(resolve.main(["field", str(os.getpid()), "status"]), 0)
        self.assertEqual(out.getvalue().split(), [str(os.getpid()), "idle"])
        self.assertEqual(resolve.main(["field", str(os.getpid()), "missing"]), agent_files.NOT_FOUND)
        self.assertEqual(resolve.main(["registered", "1"]), agent_files.NOT_FOUND)


class TrustTest(ResolveTestCase):
    def test_trust_keeps_the_rest_of_the_config(self) -> None:
        resolve.CONFIG_FILE.write_text(json.dumps({"theme": "dark", "projects": {"/other": {"x": 1}}}))
        resolve.set_trust(self.tmp_dir(), True)
        config = json.loads(resolve.CONFIG_FILE.read_text())
        self.assertEqual(config["theme"], "dark")
        self.assertEqual(config["projects"]["/other"], {"x": 1})
        self.assertTrue(resolve.trusted(self.tmp_dir()))
        self.assertEqual(resolve.CONFIG_FILE.stat().st_mode & 0o777, 0o600)

    def test_revoke(self) -> None:
        resolve.set_trust(self.tmp_dir(), True)
        resolve.set_trust(self.tmp_dir(), False)
        self.assertFalse(resolve.trusted(self.tmp_dir()))

    def test_a_symlinked_config_is_updated_in_place(self) -> None:
        real = self.root / "real-claude.json"
        real.write_text("{}")
        resolve.CONFIG_FILE.symlink_to(real)
        resolve.set_trust(self.tmp_dir(), True)
        self.assertTrue(resolve.CONFIG_FILE.is_symlink())
        self.assertIn("hasTrustDialogAccepted", real.read_text())

    def test_missing_or_corrupt_config_means_untrusted(self) -> None:
        self.assertFalse(resolve.trusted(self.tmp_dir()))
        resolve.CONFIG_FILE.write_text("{not json")
        self.assertFalse(resolve.trusted(self.tmp_dir()))

    def tmp_dir(self) -> str:
        path = self.root / "repo"
        path.mkdir(exist_ok=True)
        return str(path)


MCP_LIST = """Checking MCP server health…

claude.ai Acme Mail: https://mail.example.com/mcp - ✔ Connected
plugin:acme-tracker:tracker: https://tracker.example.com/mcp (HTTP) - ! Needs authentication
plugin:pdf-tools:pdf: npx -y @acme/pdf-server --stdio - ✔ Connected
acme-local: /Users/me/.local/bin/acme-mcp  - ✔ Connected
claude.ai Acme Metrics: https://metrics.example.com/mcp - ✘ Failed to connect — HTTP 502: bad - try later
"""


def fake_claude(folder: Path, body: str) -> Path:
    path = folder / "fake-claude"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


class McpServersTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_mcp_list_text_parsed(self) -> None:
        (self.root / "listing.txt").write_text(MCP_LIST)
        claude = fake_claude(
            self.root,
            f'[ "$*" = "mcp list" ] || exit 9\npwd -P > "{self.root}/cwd"\n'
            f'[ "$ENABLE_CLAUDEAI_MCP_SERVERS" = true ] || exit 8\ncat "{self.root}/listing.txt"',
        )
        workdir = self.root / "repo"
        workdir.mkdir()
        self.assertEqual(
            resolve.mcp_server_targets(str(claude), workdir),
            {
                "claude.ai Acme Mail": "https://mail.example.com/mcp",
                "plugin:acme-tracker:tracker": "https://tracker.example.com/mcp",
                "plugin:pdf-tools:pdf": "npx -y @acme/pdf-server --stdio",
                "acme-local": "/Users/me/.local/bin/acme-mcp",
                "claude.ai Acme Metrics": "https://metrics.example.com/mcp",
            },
        )
        self.assertEqual((self.root / "cwd").read_text().strip(), str(workdir))

    def test_mcp_list_failure_raises(self) -> None:
        cases = {
            "exit 1": "exited 1",
            "exec sleep 20": "timed out",
            "echo 'Checking MCP server health…'; echo": "no server",
            "echo 'No MCP servers configured.'": "no server",
        }
        for body, why in cases.items():
            # Short only for the one that hangs: on a busy machine a script needs longer than 0.5s to start.
            timeout = 0.5 if "sleep" in body else 30.0
            with self.subTest(body=body), self.assertRaisesRegex(resolve.McpListError, why):
                resolve.mcp_server_targets(str(fake_claude(self.root, body)), self.root, timeout=timeout)
        with self.assertRaisesRegex(resolve.McpListError, "could not run"):
            resolve.mcp_server_targets(str(self.root / "no-claude"), self.root)

    def test_plugin_servers_expand_the_plugin_root(self) -> None:
        tracker = self.root / "plugins" / "acme-tracker"
        pdf = self.root / "plugins" / "pdf-tools"
        for folder, servers in (
            (tracker, {"tracker": {"type": "http", "url": "${CLAUDE_PLUGIN_ROOT}/../t"}}),
            (
                pdf,
                {
                    "pdf": {
                        "command": "${CLAUDE_PLUGIN_ROOT}/bin/pdf",
                        "args": ["--root", "${CLAUDE_PLUGIN_ROOT}/d"],
                    },
                    "preview": {"type": "http", "url": "https://pdf.example.com/mcp"},
                },
            ),
        ):
            folder.mkdir(parents=True)
            (folder / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
        installed = self.root / "installed_plugins.json"
        plugins = {
            "acme-tracker@market": [{"installPath": str(tracker)}],
            "pdf-tools@market": [{"installPath": str(pdf)}, "not an install"],
            "empty@market": [{"installPath": str(self.root / "nothing")}],
            "odd@market": "not a list",
        }
        installed.write_text(json.dumps({"plugins": plugins}))
        self.assertEqual(
            resolve.plugin_mcp_servers(installed),
            {
                "plugin:acme-tracker:tracker": {"type": "http", "url": f"{tracker}/../t"},
                "plugin:pdf-tools:pdf": {"command": f"{pdf}/bin/pdf", "args": ["--root", f"{pdf}/d"]},
                "plugin:pdf-tools:preview": {"type": "http", "url": "https://pdf.example.com/mcp"},
            },
        )
        self.assertEqual(resolve.plugin_mcp_servers(self.root / "missing.json"), {})
        self.assertIsNone(resolve.slack_plugin_mcp_config(installed))

    def test_an_install_without_an_absolute_path_is_skipped(self) -> None:
        """Path("") / ".mcp.json" would be the window folder's own .mcp.json, read as a plugin's."""
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"own": {"command": "repo-server"}}}))
        installed = self.root / "installed_plugins.json"
        installs = {"blank@market": [{"installPath": ""}], "relative@market": [{"installPath": "."}]}
        installed.write_text(json.dumps({"plugins": {**installs, "none@market": [{}]}}))
        cwd = Path.cwd()
        os.chdir(repo)
        self.addCleanup(os.chdir, cwd)
        self.assertEqual(resolve.plugin_mcp_servers(installed), {})

    def test_a_variable_left_after_the_plugin_root_is_unexpanded(self) -> None:
        """Claude Code expands other ${VARIABLES} from its own environment, which bosun cannot know."""
        self.assertFalse(resolve.unexpanded({"command": "/opt/pdf", "args": ["--stdio"], "url": None}))
        self.assertFalse(resolve.unexpanded({"command": "x", "args": "not a list"}))
        for config in (
            {"command": "${ACME_HOME}/bin/pdf"},
            {"command": "pdf", "args": ["--token", "${ACME_TOKEN}"]},
            {"type": "http", "url": "https://${ACME_HOST}/mcp"},
        ):
            with self.subTest(config=config):
                self.assertTrue(resolve.unexpanded(config))

    def test_a_listing_that_times_out_leaves_no_server_it_started_behind(self) -> None:
        """`claude mcp list` starts each stdio server to check it; on a timeout its whole process group
        goes, not only the claude process."""
        pid_file = self.root / "server.pid"
        claude = fake_claude(self.root, f'sleep 60 &\necho $! > "{pid_file}"\nexec sleep 60')
        with self.assertRaisesRegex(resolve.McpListError, "timed out"):
            resolve.mcp_server_targets(str(claude), self.root, timeout=1.0)
        server = int(pid_file.read_text())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and alive(server):
            time.sleep(0.1)
        self.assertFalse(alive(server))


def alive(pid: int) -> bool:
    """Whether <pid> runs (a zombie its new parent has not reaped yet counts as gone)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = os.popen(f"ps -o stat= -p {pid}").read().strip()  # noqa: S605 - a fixed command, a number
    return bool(state) and not state.startswith("Z")


if __name__ == "__main__":
    unittest.main()


class ContextTokensTest(unittest.TestCase):
    def test_the_latest_turn_with_usage_gives_the_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "s.jsonl"
            turn = {"input_tokens": 10, "cache_read_input_tokens": 200, "cache_creation_input_tokens": 5}
            lines = [
                {"type": "assistant", "message": {"usage": {"input_tokens": 1}}},
                {"type": "assistant", "message": {"usage": turn}},
                {"type": "assistant", "isApiErrorMessage": True, "message": {"usage": {}}},
                {"type": "user", "message": {"content": "hi"}},
            ]
            transcript.write_text("".join(json.dumps(line) + "\n" for line in lines) + "{broken\n")
            self.assertEqual(resolve.context_tokens(transcript), 215)
            self.assertIsNone(resolve.context_tokens(Path(tmp) / "gone.jsonl"))
