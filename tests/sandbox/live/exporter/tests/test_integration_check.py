import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "integration_check.py"


def run(home: str, env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--env", env],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": home},
        stdin=subprocess.DEVNULL,
        check=False,
    )


class IntegrationCheckTest(unittest.TestCase):
    def test_it_records_the_environment_it_ran_against(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            result = run(home, "staging")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(home) / ".sandbox" / "integration-staging").exists())

    def test_an_unknown_environment_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(run(home, "production").returncode, 2)
            self.assertFalse((Path(home) / ".sandbox").exists())


if __name__ == "__main__":
    unittest.main()
