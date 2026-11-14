"""tools/swift_hook.sh: what it hands SwiftPM. Run with a stand-in `swift` that records its environment, and a
stand-in `uname` that says Darwin, so this runs on any machine and builds nothing."""

from __future__ import annotations

import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "tools" / "swift_hook.sh"
GIT_VARIABLES = {  # what git sets for a hook (and a worktree's own), pointing at this repository
    "GIT_INDEX_FILE": "/somewhere/.git/index.lock",
    "GIT_DIR": "/somewhere/.git",
    "GIT_WORK_TREE": "/somewhere",
    "GIT_OBJECT_DIRECTORY": "/somewhere/.git/objects",
    "GIT_PREFIX": "app/",
}


class SwiftHookTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "home"
        self.home.mkdir()
        self.bin = Path(temp.name) / "bin"
        self.bin.mkdir()
        self.seen = Path(temp.name) / "seen"
        self.tool("uname", "echo Darwin")
        self.tool("swift", f'env | grep "^GIT_" >>"{self.seen}"; echo "swift $*" >>"{self.seen}"; exit 0')
        (self.bin / "python3").symlink_to(sys.executable)
        for name in ("sh", "env", "grep", "cat", "mkdir", "mktemp", "rm", "dirname"):
            found = next(Path(d) / name for d in ("/bin", "/usr/bin") if (Path(d) / name).exists())
            (self.bin / name).symlink_to(found)

    def tool(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def test_swiftpm_never_gets_git_s_variables_for_this_repository(self) -> None:
        """A checkout of a dependency took the hook's GIT_INDEX_FILE and used this repository's index."""
        env = {"PATH": str(self.bin), "HOME": str(self.home), **GIT_VARIABLES}
        result = subprocess.run(
            ["sh", str(HOOK), "test"], env=env, capture_output=True, text=True, check=False, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        seen = self.seen.read_text()
        self.assertIn("swift package resolve", seen)
        self.assertIn("swift test -Xswiftc -warnings-as-errors", seen)
        self.assertNotIn("GIT_", seen.replace("swift ", ""), seen)


if __name__ == "__main__":
    unittest.main()
