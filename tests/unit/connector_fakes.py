"""What the connectors' tests share: an executable standing in for an agent, and a scratch folder."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import connectors


def fake_agent(folder: Path, body: str) -> Path:
    """An executable standing in for `claude` or `codex`: a shell script with <body>."""
    path = folder / "fake-agent"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def printing(folder: Path, text: str) -> Path:
    """An agent that notes each call in <folder>/calls and prints <text>."""
    (folder / "listing.txt").write_text(text)
    return fake_agent(folder, f'echo called >> "{folder}/calls"\ncat "{folder}/listing.txt"')


def capture(main: Callable[[list[str]], int], *argv: str) -> tuple[int, str, str]:
    """What <main> returns and prints to stdout and stderr when run with <argv>."""
    with mock.patch("sys.stdout", io.StringIO()) as out, mock.patch("sys.stderr", io.StringIO()) as err:
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ScratchCase(unittest.TestCase):
    """A scratch folder (root) with a state folder in it (state, not yet made)."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state = self.root / "state"


class DoctorMixin(unittest.TestCase):
    """`connectors.py doctor` run for a window, in a case that has a scratch folder."""

    root: Path
    state: Path

    def doctor(
        self,
        window: str,
        value: str,
        agent: Path,
        kind: str = "session",
        harness: str = "claude",
        settings_file: Path | None = None,
    ) -> str:
        site = ("--settings-file", str(settings_file)) if settings_file else ()
        code, out, err = capture(
            connectors.main,
            *("doctor", "--window", window, "--kind", kind, "--harness", harness, "--list", value),
            *("--cwd", str(self.root), "--state-dir", str(self.state), "--agent-bin", str(agent), *site),
        )
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(out.splitlines()), 1, out)
        return out.rstrip("\n")
