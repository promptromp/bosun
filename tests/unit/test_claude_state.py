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
        self.assertEqual(self.main(WORKDIR, "alpha")[0], resolve.NOT_FOUND)

    def test_unreadable_entries_are_skipped(self) -> None:
        (self.project / "broken.jsonl").mkdir()
        self.transcript("good", "alpha")
        self.assertEqual(self.main(WORKDIR, "alpha"), (0, "good"))

    def test_exit_codes(self) -> None:
        self.assertEqual(self.main(WORKDIR, "missing")[0], resolve.NOT_FOUND)
        self.assertEqual(self.main(WORKDIR)[0], 2)
        self.assertEqual(self.main("--help")[0], 0)

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
        self.assertEqual(self.main("--live", "S")[0], resolve.NOT_FOUND)

    def test_a_reused_pid_is_stale(self) -> None:
        # This process started long after 1970, so the record cannot be its own.
        self.registry("stale", pid=os.getpid(), sessionId="S", startedAt=1000)
        self.assertEqual(self.main("--live", "S")[0], resolve.NOT_FOUND)

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
        self.assertEqual(resolve.main(["field", str(os.getpid()), "missing"]), resolve.NOT_FOUND)
        self.assertEqual(resolve.main(["registered", "1"]), resolve.NOT_FOUND)


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
