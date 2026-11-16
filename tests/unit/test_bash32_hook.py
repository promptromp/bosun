"""tools/bash32_hook.sh: every shell script parsed by macOS's own /bin/bash (3.2). Where /bin/bash is
not 3.2 (CI's Linux lint), it passes in one line."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "tools" / "bash32_hook.sh"
# The shape of what once passed shellcheck and bash 5 but not macOS's bash: an apostrophe and backquotes
# in a here-document inside $( ... ) inside double quotes.
UNPARSABLE = """#!/bin/bash
f() {
  say "$(
    cat <<TEXT
it's a \\`bosun stop\\` away
TEXT
  )"
}
"""


def bash_version() -> str:
    if not Path("/bin/bash").exists():
        return ""
    found = subprocess.run(
        ["/bin/bash", "-c", 'echo "${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"'],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return found.stdout.strip()


class Bash32HookTest(unittest.TestCase):
    def run_hook(self, text: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "script.sh"
            script.write_text(text)
            return subprocess.run(
                ["sh", str(HOOK), str(script)],
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )

    def test_a_script_bash_3_2_cannot_parse_fails(self) -> None:
        result = self.run_hook(UNPARSABLE)
        if bash_version() != "3.2":
            self.assertEqual(result.returncode, 0)
            self.assertIn("skipping the bash 3.2 parse", result.stdout)
            return
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unexpected", result.stderr)

    def test_a_script_it_parses_passes(self) -> None:
        result = self.run_hook('#!/bin/bash\nf() { echo "$(date)"; }\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
