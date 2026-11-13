"""What the tests of `bosun app` share (tests/unit/test_app_install.py, test_app_release.py): a scratch
home and checkout, and fakes for every tool lib/app_install.py runs (curl, ditto, codesign, spctl, osascript,
open). subprocess is shut for the whole of a World, so no test can reach a real tool, download
anything or touch ~/Applications."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import app_release
from app_install import Run, Setup


TEAM = "ABCDE12345"  # a made-up Team ID  # pragma: allowlist secret gitleaks:allow
BUNDLE_ID = "ai.example.bosun"
SLUG = "acme/bosun"
PUBLISHER = app_release.Publisher(BUNDLE_ID, TEAM, SLUG)
EMPTY_TEAM = "app/publisher.json has no team_id yet"
NOT_A_TEAM = "app/publisher.json's team_id is not a Team ID"
SYMLINK_ATTR = 0o120777 << 16  # the external attributes of a zip entry that is a symbolic link
UNSATISFIED = "test-requirement: code failed to satisfy specified code requirement(s)"


def write_bundle(app: Path, version: str, bundle_id: str = BUNDLE_ID) -> None:
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "bosun").write_text("binary")
    plist = {"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version}
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(plist))


def tree(folder: Path) -> dict[str, str]:
    """Every file and folder under <folder>, with the file's text: what 'left as it was' compares."""
    found = {}
    for path in sorted(folder.rglob("*")):
        name = str(path.relative_to(folder))
        found[name] = path.read_text() if path.is_file() and not path.is_symlink() else "<dir>"
    return found


class FakeTools:
    """curl, ditto, codesign, spctl, osascript and open as far as `bosun app` asks them, and what it asked.
    Each knob is one way the real tool answers; ditto lays out the archive as `archive` lists it."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.download = 0  # curl's exit status: 22 is an HTTP error, as -f makes of a 404
        self.http_status = "404"  # what -w prints after an HTTP error
        self.not_a_zip = False
        self.zip_extra: list[zipfile.ZipInfo] = []  # entries the zip holds besides the app's
        self.archive = ["bosun.app"]  # what ditto unpacks
        self.unzip_fails = False
        self.version = "0.2.0"  # what the release holds: app/VERSION's
        self.bundle_id = BUNDLE_ID
        self.verify_says: str | None = None  # codesign --verify's complaint (exit 1), or nothing
        self.meets_requirement = True  # False: codesign's real failure, exit 3
        self.requirements: list[str] = []  # what -R was given
        self.assess_says: str | None = None  # spctl's complaint (exit 3), or nothing
        self.assess_source: str | None = "source=Notarized Developer ID"  # the line spctl -vv prints
        self.running = False
        self.running_says: str | None = None  # osascript's answer to "is running": "unknown", "unregistered"
        self.quits = True  # whether the running app ends when asked to
        self.quit_says: str | None = None  # osascript's answer to "quit": "denied" or "other"
        self.unknown_after_quit = False  # the answer to the poll that follows a quit is an error
        self.quit_asked = False
        self.open_fails = False
        self.on_assess: Any = None  # called as spctl runs: what is in ~/Applications then
        self.on_download: Any = None

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]

    def done(self, code: int = 0, out: str = "", err: str = "") -> Run:
        return Run(code, out, err)

    def curl(self, argv: Sequence[str]) -> Run:
        if self.on_download:
            self.on_download()
        if self.download:
            return self.done(
                self.download, self.http_status, "curl: (22) The requested URL returned an error"
            )
        target = Path(argv[argv.index("-o") + 1])
        if self.not_a_zip:
            target.write_text("not a zip")
            return self.done(0, "200")
        with zipfile.ZipFile(target, "w") as bundle:
            bundle.writestr("bosun.app/Contents/Info.plist", "x")
            for entry in self.zip_extra:
                bundle.writestr(entry, "x")
        return self.done(0, "200")

    def ditto(self, argv: Sequence[str]) -> Run:
        if self.unzip_fails:
            return self.done(1, err="ditto: Couldn't read PKZip archive")
        into = Path(argv[-1])
        for name in self.archive:
            if name == "bosun.app":
                write_bundle(into / name, self.version, self.bundle_id)
            elif name.endswith("/"):
                (into / name).mkdir(parents=True)
            else:
                (into / name).write_text("x")
        return self.done()

    def codesign(self, argv: Sequence[str]) -> Run:
        if "-dv" in argv:
            raise AssertionError("codesign's description was asked for: nothing may be read from it")
        self.requirements.append(argv[argv.index("-R") + 1])
        if self.verify_says:
            return self.done(1, err=f"{argv[-1]}: {self.verify_says}")
        if not self.meets_requirement:
            return self.done(3, err=UNSATISFIED)
        return self.done()

    def spctl(self, argv: Sequence[str]) -> Run:
        if self.on_assess:
            self.on_assess()
        if self.assess_says:
            return self.done(3, err=f"{argv[-1]}: {self.assess_says}")
        lines = [f"{argv[-1]}: accepted", *([self.assess_source] if self.assess_source else [])]
        return self.done(0, err="\n".join(lines))

    def osascript(self, argv: Sequence[str]) -> Run:
        script = argv[-1]
        if script.endswith("is running"):
            return self.is_running()
        if self.quit_says == "denied":
            return self.done(1, err="execution error: Not authorized to send Apple events to bosun. (-1743)")
        if self.quit_says:
            return self.done(1, err="execution error: something else (-600)")
        self.quit_asked = True
        self.running = self.running and not self.quits
        return self.done()

    def is_running(self) -> Run:
        if self.running_says == "unregistered":
            return self.done(1, err='syntax error: Cannot get application id "x". (-1728)')
        if self.running_says == "unknown" or (self.unknown_after_quit and self.quit_asked):
            return self.done(1, err="execution error: osascript is not allowed assistive access (-25211)")
        return self.done(0, "true\n" if self.running else "false\n")

    def open(self, argv: Sequence[str]) -> Run:
        if self.open_fails:
            return self.done(1, err="The file does not exist")
        self.running = True
        return self.done()

    def __call__(self, argv: Sequence[str]) -> Run:
        self.calls.append(list(argv))
        handler = getattr(self, argv[0], None)
        if argv[0] not in ("curl", "ditto", "codesign", "spctl", "osascript", "open") or handler is None:
            raise AssertionError(f"a test reached a tool the fake does not know: {argv}")
        result: Run = handler(argv)
        return result


class World(unittest.TestCase):
    """A scratch home, a scratch checkout holding app/VERSION and app/publisher.json, and the fake tools."""

    team_id = TEAM

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.home, self.root = base / "home", base / "checkout"
        (self.root / "app").mkdir(parents=True)
        self.home.mkdir()
        (self.root / "app" / "VERSION").write_text("0.2.0\n")
        self.write_publisher(self.team_id)
        self.tools = FakeTools()
        self.apps = self.home / "Applications"
        self.app = self.apps / "bosun.app"
        # No test may run a real tool: subprocess is shut, and every tool goes through the fake.
        for name in ("run", "Popen", "check_output", "call", "check_call"):
            patcher = mock.patch.object(subprocess, name, side_effect=AssertionError("a test ran a tool"))
            patcher.start()
            self.addCleanup(patcher.stop)
        sleeper = mock.patch("time.sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def write_publisher(self, team_id: str) -> None:
        publisher = {"bundle_id": BUNDLE_ID, "team_id": team_id, "releases": SLUG}
        (self.root / "app" / "publisher.json").write_text(json.dumps(publisher, indent=2) + "\n")

    def setup_for(self, releases: str = "") -> Setup:
        return Setup(root=self.root, home=self.home, releases=releases, run=self.tools, sleep=lambda _: None)

    def install_old(self, version: str = "0.1.0") -> None:
        self.apps.mkdir(exist_ok=True)
        write_bundle(self.app, version)
        (self.app / "Contents" / "old.txt").write_text("the old one")
