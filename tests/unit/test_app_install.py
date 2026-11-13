"""lib/app_install.py: `bosun app`'s verifying and placing of bosun.app, in a scratch home. Every tool it runs
(curl, ditto, codesign, spctl, osascript, open) is a fake behind its one runner, and subprocess is shut for
the whole file, so no test can reach a real tool, download anything or touch ~/Applications."""

from __future__ import annotations

import contextlib
import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import app_install
import update_lookup
from app_install import AppError, Publisher, Run, Setup


TEAM = "ABCDE12345"  # a made-up Team ID  # pragma: allowlist secret gitleaks:allow
BUNDLE_ID = "ai.example.bosun"
SLUG = "acme/bosun"
PUBLISHER = Publisher(BUNDLE_ID, TEAM, SLUG)
EMPTY_TEAM = "app/publisher.json has no team_id yet"
# Made-up addresses with a made-up password, which must never get into an address or a message.
AT_GITHUB = "https://user:secret@github.com/acme/bosun.git"  # pragma: allowlist secret gitleaks:allow
AT_GITLAB = (
    "https://user:hunter2@gitlab.example.com/acme/bosun.git"  # pragma: allowlist secret gitleaks:allow
)
AT_EXAMPLE = "https://user:hunter2@example.com/x"  # pragma: allowlist secret gitleaks:allow


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
        self.archive = ["bosun.app"]
        self.unzip_fails = False
        self.version = "0.2.0"  # what the release holds: app/VERSION's
        self.bundle_id = BUNDLE_ID
        self.verify_says: str | None = None  # codesign --verify's complaint (exit 1), or nothing
        self.team = TEAM  # what `codesign -dv` shows after TeamIdentifier=
        self.assess_says: str | None = None  # spctl's complaint (exit 3), or nothing
        self.running = False
        self.quits = True  # whether the running app ends when asked to
        self.open_fails = False
        self.on_open: Any = None
        self.on_assess: Any = None  # called as spctl runs: what is in ~/Applications then

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]

    def done(self, code: int = 0, out: str = "", err: str = "") -> Run:
        return Run(code, out, err)

    def curl(self, argv: Sequence[str]) -> Run:
        if self.download:
            return self.done(self.download, err="curl: (22) The requested URL returned error: 404")
        Path(argv[argv.index("-o") + 1]).write_text("zip")
        return self.done()

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
        if "--verify" in argv:
            if self.verify_says:
                return self.done(1, err=f"{argv[-1]}: {self.verify_says}")
            return self.done()
        lines = ["Identifier=" + BUNDLE_ID, "Authority=Developer ID Application: Acme (" + self.team + ")"]
        return self.done(0, err="\n".join([*lines, f"TeamIdentifier={self.team}", "Sealed Resources"]))

    def spctl(self, argv: Sequence[str]) -> Run:
        if self.on_assess:
            self.on_assess()
        if self.assess_says:
            return self.done(3, err=f"{argv[-1]}: {self.assess_says}")
        return self.done()

    def osascript(self, argv: Sequence[str]) -> Run:
        script = argv[-1]
        if script.endswith("is running"):
            return self.done(0, "true\n" if self.running else "false\n")
        if script.endswith("to quit"):
            self.running = self.running and not self.quits
        return self.done()

    def open(self, argv: Sequence[str]) -> Run:
        if self.on_open:
            self.on_open()
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
        publisher = {"bundle_id": BUNDLE_ID, "team_id": self.team_id, "releases": SLUG}
        (self.root / "app" / "publisher.json").write_text(json.dumps(publisher, indent=2) + "\n")
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

    def setup_for(self, releases: str = "") -> Setup:
        return Setup(root=self.root, home=self.home, releases=releases, run=self.tools, sleep=lambda _: None)

    def install_old(self, version: str = "0.1.0") -> None:
        self.apps.mkdir(exist_ok=True)
        write_bundle(self.app, version)
        (self.app / "Contents" / "old.txt").write_text("the old one")


class ReleaseLookupTest(unittest.TestCase):
    def test_the_asset_url(self) -> None:
        self.assertEqual(
            app_install.release_asset_url("acme/bosun", "0.1.0"),
            "https://github.com/acme/bosun/releases/download/app-v0.1.0/bosun-app-0.1.0.zip",
        )

    def test_a_slug_or_version_that_could_change_the_address_is_refused(self) -> None:
        for slug in (
            "acme",
            "acme/bosun/extra",
            "acme/bo sun",
            "../x/y",
            "a/b?c=d",
            "https://evil.example/x",
        ):
            with self.subTest(slug=slug), self.assertRaises(AppError):
                app_install.release_asset_url(slug, "0.1.0")
        for version in ("", "0.1.0/../x", "0.1 0", "0.1.0#x"):
            with self.subTest(version=version), self.assertRaises(AppError):
                app_install.release_asset_url("acme/bosun", version)

    def test_github_addresses_in_every_form(self) -> None:
        for url in (
            "https://github.com/acme/bosun.git",
            "https://github.com/acme/bosun",
            "git@github.com:acme/bosun.git",
            "ssh://git@github.com/acme/bosun.git",
            "https://github.com/acme/bosun/",
        ):
            with self.subTest(url=url):
                self.assertEqual(app_install.github_slug(url), "acme/bosun")

    def test_credentials_in_an_address_are_never_in_the_slug(self) -> None:
        self.assertEqual(app_install.github_slug(AT_GITHUB), "acme/bosun")

    def test_another_host_is_not_github(self) -> None:
        for url in (
            "https://gitlab.com/acme/bosun.git",
            "https://github.com.evil.example/acme/bosun.git",
            "https://example.com/github.com/acme/bosun",
            "/Users/me/src/bosun",
            "",
        ):
            with self.subTest(url=url):
                self.assertIsNone(app_install.github_slug(url))

    def test_the_order_is_the_setting_then_the_publisher_then_the_origin(self) -> None:
        def origin() -> str:
            return "https://github.com/fork/bosun.git"

        self.assertEqual(app_install.releases_slug("mirror/bosun", PUBLISHER, origin), "mirror/bosun")
        self.assertEqual(app_install.releases_slug("", PUBLISHER, origin), SLUG)
        self.assertEqual(app_install.releases_slug("", Publisher(BUNDLE_ID, TEAM, ""), origin), "fork/bosun")

    def test_the_origin_is_only_read_when_nothing_else_names_the_releases(self) -> None:
        def origin() -> str:
            raise AssertionError("the origin was read")

        self.assertEqual(app_install.releases_slug("mirror/bosun", PUBLISHER, origin), "mirror/bosun")
        self.assertEqual(app_install.releases_slug("", PUBLISHER, origin), SLUG)

    def test_a_setting_that_is_a_github_address_is_taken_as_its_slug(self) -> None:
        slug = app_install.releases_slug("https://tok@github.com/mirror/bosun.git", PUBLISHER, lambda: "")
        self.assertEqual(slug, "mirror/bosun")

    def test_nothing_to_go_by_says_so_without_credentials(self) -> None:
        origin = AT_GITLAB
        with self.assertRaises(AppError) as caught:
            app_install.releases_slug("", Publisher(BUNDLE_ID, TEAM, ""), lambda: origin)
        self.assertIn("BOSUN_APP_RELEASES", str(caught.exception))
        self.assertNotIn("hunter2", str(caught.exception))

    def test_a_setting_that_is_neither_is_refused_without_credentials(self) -> None:
        with self.assertRaises(AppError) as caught:
            app_install.releases_slug(AT_EXAMPLE, PUBLISHER, lambda: "")
        self.assertIn("BOSUN_APP_RELEASES", str(caught.exception))
        self.assertNotIn("hunter2", str(caught.exception))

    def test_the_origin_is_the_one_the_update_reads(self) -> None:
        with mock.patch.object(
            update_lookup, "origin_url", return_value="https://github.com/o/r.git"
        ) as read:
            self.assertEqual(
                app_install.install_origin(Path("/nowhere/checkout")), "https://github.com/o/r.git"
            )
        read.assert_called_once()

    def test_an_origin_that_cannot_be_read_is_none_at_all(self) -> None:
        with mock.patch.object(update_lookup, "origin_url", side_effect=update_lookup.LookupFailed("no")):
            self.assertEqual(app_install.install_origin(Path("/nowhere/checkout")), "")


class PublisherTest(World):
    def test_the_publisher_file_is_read(self) -> None:
        self.assertEqual(app_install.read_publisher(self.root), PUBLISHER)

    def test_a_missing_or_broken_file_is_refused_in_a_line(self) -> None:
        (self.root / "app" / "publisher.json").write_text("{")
        with self.assertRaisesRegex(AppError, "publisher.json"):
            app_install.read_publisher(self.root)
        (self.root / "app" / "publisher.json").unlink()
        with self.assertRaisesRegex(AppError, "publisher.json"):
            app_install.read_publisher(self.root)

    def test_a_file_without_a_bundle_id_is_refused(self) -> None:
        (self.root / "app" / "publisher.json").write_text(json.dumps({"team_id": TEAM, "releases": SLUG}))
        with self.assertRaisesRegex(AppError, "bundle_id"):
            app_install.read_publisher(self.root)

    def test_the_wanted_version(self) -> None:
        self.assertEqual(app_install.wanted_version(self.root), "0.2.0")

    def test_no_version_file_is_refused(self) -> None:
        (self.root / "app" / "VERSION").unlink()
        with self.assertRaisesRegex(AppError, "VERSION"):
            app_install.wanted_version(self.root)


class VerifyTest(World):
    def setUp(self) -> None:
        super().setUp()
        self.bundle = self.home / "staged" / "bosun.app"
        write_bundle(self.bundle, "0.1.0")

    def verify(self) -> str | None:
        return app_install.verify(self.bundle, TEAM, BUNDLE_ID, self.tools)

    def test_the_publisher_s_notarized_app_passes_after_the_four_checks_in_order(self) -> None:
        self.assertIsNone(self.verify())
        self.assertEqual(
            self.tools.calls,
            [
                ["codesign", "--verify", "--deep", "--strict", str(self.bundle)],
                ["codesign", "-dv", str(self.bundle)],
                ["spctl", "--assess", "--type", "execute", str(self.bundle)],
            ],
        )

    def test_an_unsigned_app_is_refused(self) -> None:
        self.tools.verify_says = "code object is not signed at all"
        self.assertEqual(self.verify(), "it is not signed")

    def test_a_signature_that_does_not_verify_is_refused_saying_why(self) -> None:
        self.tools.verify_says = "a sealed resource is missing or invalid"
        reason = self.verify()
        self.assertIsNotNone(reason)
        self.assertIn("signature does not verify", str(reason))
        self.assertIn("a sealed resource is missing", str(reason))

    def test_another_team_is_refused_and_named(self) -> None:
        self.tools.team = "ZZZZZ99999"
        reason = self.verify()
        self.assertIn("another team", str(reason))
        self.assertIn("ZZZZZ99999", str(reason))
        self.assertNotIn("spctl", self.tools.names())  # it stops at the first refusal

    def test_an_ad_hoc_signature_has_no_team_and_is_refused(self) -> None:
        self.tools.team = "not set"
        self.assertIn("no Team ID", str(self.verify()))

    def test_an_app_macos_does_not_accept_is_refused(self) -> None:
        self.tools.assess_says = "rejected"
        reason = self.verify()
        self.assertIn("not notarized", str(reason))
        self.assertIn("rejected", str(reason))

    def test_another_bundle_id_is_refused_and_named(self) -> None:
        (self.bundle / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": "ai.other.app", "CFBundleShortVersionString": "1"})
        )
        reason = self.verify()
        self.assertIn("ai.other.app", str(reason))
        self.assertIn(BUNDLE_ID, str(reason))

    def test_a_bundle_with_no_readable_info_plist_is_refused(self) -> None:
        (self.bundle / "Contents" / "Info.plist").write_text("not a plist")
        self.assertIn("Info.plist", str(self.verify()))

    def test_something_that_is_not_a_folder_is_refused_before_any_tool(self) -> None:
        plain = self.home / "bosun.app"
        plain.write_text("x")
        self.assertIn("not an app", str(app_install.verify(plain, TEAM, BUNDLE_ID, self.tools)))
        self.assertEqual(self.tools.calls, [])

    def test_an_empty_team_id_never_passes_an_ad_hoc_app(self) -> None:
        self.tools.team = ""
        self.assertIsNotNone(app_install.verify(self.bundle, "", BUNDLE_ID, self.tools))


class InstallTest(World):
    def install(self, **kwargs: Any) -> str:
        return app_install.install(self.setup_for(), **kwargs)

    def test_it_downloads_unpacks_verifies_and_places_the_app(self) -> None:
        message = self.install()
        self.assertEqual(app_install.bundle_version(self.app), "0.2.0")
        self.assertIn("installed", message)
        curl = self.tools.calls[0]
        self.assertEqual(curl[:2], ["curl", "-fsSL"])
        self.assertEqual(
            curl[-1], "https://github.com/acme/bosun/releases/download/app-v0.2.0/bosun-app-0.2.0.zip"
        )
        self.assertEqual(self.tools.names(), ["curl", "ditto", "codesign", "codesign", "spctl", "osascript"])
        self.assertEqual(self.tools.calls[1][1:3], ["-x", "-k"])

    def test_it_leaves_nothing_but_the_app_in_applications(self) -> None:
        self.install()
        self.assertEqual([path.name for path in self.apps.iterdir()], ["bosun.app"])

    def test_the_app_is_fetched_from_the_release_of_the_setting_when_there_is_one(self) -> None:
        app_install.install(Setup(self.root, self.home, "mirror/bosun", self.tools, lambda _: None))
        self.assertIn("github.com/mirror/bosun/releases", self.tools.calls[0][-1])

    def test_an_old_app_is_replaced_and_gone(self) -> None:
        self.install_old()
        self.install()
        self.assertFalse((self.app / "Contents" / "old.txt").exists())
        self.assertEqual([path.name for path in self.apps.iterdir()], ["bosun.app"])

    def test_the_old_app_stays_until_the_new_one_is_verified(self) -> None:
        self.install_old()
        seen: list[bool] = []
        self.tools.on_assess = lambda: seen.append((self.app / "Contents" / "old.txt").exists())
        self.install()
        self.assertEqual(seen, [True])

    def test_an_empty_team_id_refuses_before_anything_is_downloaded(self) -> None:
        self.team_id = ""
        (self.root / "app" / "publisher.json").write_text(
            json.dumps({"bundle_id": BUNDLE_ID, "team_id": "", "releases": SLUG})
        )
        with self.assertRaises(AppError) as caught:
            self.install()
        self.assertEqual(str(caught.exception), EMPTY_TEAM)
        self.assertEqual(self.tools.calls, [])
        self.assertFalse(self.apps.exists())

    def test_a_refused_app_leaves_applications_exactly_as_it_was(self) -> None:
        self.install_old()
        before = tree(self.home)
        for knob, value in (("team", "ZZZZZ99999"), ("assess_says", "rejected"), ("verify_says", "x")):
            with self.subTest(knob=knob):
                setattr(self.tools, knob, value)
                with self.assertRaises(AppError):
                    self.install()
                self.assertEqual(tree(self.home), before)
                setattr(self.tools, knob, None if knob != "team" else TEAM)

    def test_a_refusal_with_no_applications_folder_makes_none(self) -> None:
        self.tools.team = "ZZZZZ99999"
        with self.assertRaises(AppError):
            self.install()
        self.assertFalse(self.apps.exists())

    def test_a_wrong_bundle_id_is_refused_and_leaves_it_all(self) -> None:
        self.install_old()
        before = tree(self.home)
        self.tools.bundle_id = "ai.other.app"
        with self.assertRaisesRegex(AppError, "ai.other.app"):
            self.install()
        self.assertEqual(tree(self.home), before)

    def test_a_zip_with_two_apps_is_refused_and_leaves_it_all(self) -> None:
        self.install_old()
        before = tree(self.home)
        self.tools.archive = ["bosun.app", "other.app"]
        with self.assertRaisesRegex(AppError, "other.app"):
            self.install()
        self.assertEqual(tree(self.home), before)
        self.assertNotIn("codesign", self.tools.names())

    def test_an_archive_with_anything_beside_the_app_is_refused(self) -> None:
        for extra in ("README.txt", ".DS_Store", "payload/"):
            with self.subTest(extra=extra):
                self.tools.archive = ["bosun.app", extra]
                with self.assertRaises(AppError):
                    self.install()
                self.assertFalse(self.apps.exists())

    def test_macos_resource_fork_folder_is_allowed(self) -> None:
        self.tools.archive = ["bosun.app", "__MACOSX/"]
        self.install()
        self.assertTrue(self.app.is_dir())
        self.assertEqual([path.name for path in self.apps.iterdir()], ["bosun.app"])

    def test_an_archive_with_no_app_is_refused(self) -> None:
        self.tools.archive = ["__MACOSX/"]
        with self.assertRaisesRegex(AppError, "bosun.app"):
            self.install()

    def test_an_app_that_is_a_link_is_refused(self) -> None:
        def ditto(argv: Sequence[str]) -> Run:
            into = Path(argv[-1])
            (into / "elsewhere").mkdir()
            (into / "bosun.app").symlink_to(into / "elsewhere")
            return Run(0, "", "")

        self.tools.ditto = ditto  # type: ignore[method-assign]
        with self.assertRaisesRegex(AppError, "bosun.app"):
            self.install()
        self.assertFalse(self.apps.exists())

    def test_a_missing_release_says_so_in_a_line(self) -> None:
        self.tools.download = 22
        with self.assertRaises(AppError) as caught:
            self.install()
        self.assertIn("no release of bosun.app 0.2.0", str(caught.exception))
        self.assertIn("acme/bosun", str(caught.exception))
        self.assertNotIn("\n", str(caught.exception))
        self.assertFalse(self.apps.exists())

    def test_another_download_failure_says_what_curl_said(self) -> None:
        self.tools.download = 6
        with self.assertRaisesRegex(AppError, "could not download"):
            self.install()

    def test_a_download_that_is_not_a_zip_is_refused(self) -> None:
        self.tools.unzip_fails = True
        with self.assertRaisesRegex(AppError, "not a zip"):
            self.install()
        self.assertFalse(self.apps.exists())

    def test_a_failed_rename_in_puts_the_old_app_back(self) -> None:
        self.install_old()
        before = tree(self.home)
        real = os.rename
        calls: list[tuple[str, str]] = []

        def rename(source: Any, target: Any) -> None:
            calls.append((str(source), str(target)))
            if Path(str(target)) == self.app and len(calls) == 2:
                raise OSError("disk trouble")
            real(source, target)

        with mock.patch("os.rename", rename), self.assertRaisesRegex(AppError, "disk trouble"):
            self.install()
        self.assertEqual(tree(self.home), before)

    def test_a_running_app_is_asked_to_quit_after_the_checks_and_opened_again(self) -> None:
        self.install_old()
        self.tools.running = True
        self.install()
        names = self.tools.names()
        self.assertLess(names.index("spctl"), names.index("osascript", names.index("spctl")))
        script = " ".join(" ".join(call) for call in self.tools.calls if call[0] == "osascript")
        self.assertIn(f'tell application id "{BUNDLE_ID}" to quit', script)
        self.assertEqual(self.tools.calls[-1], ["open", str(self.app)])

    def test_an_app_that_was_not_running_is_not_opened(self) -> None:
        self.install_old()
        self.install()
        self.assertNotIn("open", self.tools.names())
        self.assertFalse(any("to quit" in " ".join(call) for call in self.tools.calls))

    def test_a_refusal_leaves_a_running_app_running(self) -> None:
        self.install_old()
        self.tools.running = True
        self.tools.team = "ZZZZZ99999"
        with self.assertRaises(AppError):
            self.install()
        self.assertTrue(self.tools.running)
        self.assertFalse(any("to quit" in " ".join(call) for call in self.tools.calls))

    def test_an_app_that_will_not_quit_stops_the_install_and_leaves_it_all(self) -> None:
        self.install_old()
        before = tree(self.home)
        self.tools.running = True
        self.tools.quits = False
        with self.assertRaisesRegex(AppError, "still running"):
            self.install()
        self.assertEqual(tree(self.home), before)

    def test_an_open_that_fails_is_said_after_the_app_is_in(self) -> None:
        self.install_old()
        self.tools.running = True
        self.tools.open_fails = True
        with self.assertRaisesRegex(AppError, "installed, but could not be opened"):
            self.install()
        self.assertEqual(app_install.bundle_version(self.app), "0.2.0")

    def test_no_tool_is_found_by_a_pattern_match(self) -> None:
        self.install_old()
        self.tools.running = True
        self.install()
        for call in self.tools.calls:
            self.assertNotIn(call[0], ("pkill", "killall", "pgrep", "ps", "kill"))


class BuildTest(World):
    def test_without_part_two_it_says_so_and_changes_nothing(self) -> None:
        with self.assertRaises(AppError) as caught:
            app_install.install(self.setup_for(), build=True)
        self.assertEqual(str(caught.exception), "part 2 builds the app")
        self.assertEqual(self.tools.calls, [])

    def test_a_build_is_not_held_up_by_an_empty_team_id(self) -> None:
        (self.root / "app" / "publisher.json").write_text(
            json.dumps({"bundle_id": BUNDLE_ID, "team_id": "", "releases": SLUG})
        )
        with self.assertRaises(AppError) as caught:
            app_install.install(self.setup_for(), build=True)
        self.assertNotEqual(str(caught.exception), EMPTY_TEAM)

    def test_with_a_build_script_the_app_it_makes_is_placed_without_the_team_check(self) -> None:
        build = self.root / "app" / "build.sh"
        build.write_text("#!/bin/sh\n")
        build.chmod(0o755)
        made: list[Sequence[str]] = []

        def run(argv: Sequence[str]) -> Run:
            if argv[0] == str(build):
                made.append(argv)
                write_bundle(Path(argv[1]) / "bosun.app", "0.2.0")
                return Run(0, "", "")
            return self.tools(argv)

        message = app_install.install(Setup(self.root, self.home, "", run, lambda _: None), build=True)
        self.assertEqual(len(made), 1)
        self.assertEqual(app_install.bundle_version(self.app), "0.2.0")
        self.assertIn("installed", message)
        self.assertNotIn("spctl", self.tools.names())  # ad hoc signed: macOS would not accept it
        self.assertNotIn("curl", self.tools.names())


class UpdateTest(World):
    def update(self) -> str:
        return app_install.update(self.setup_for())

    def test_a_different_version_is_installed(self) -> None:
        self.install_old("0.1.0")
        self.update()
        self.assertEqual(app_install.bundle_version(self.app), self.tools.version)
        self.assertIn("curl", self.tools.names())

    def test_the_same_version_says_so_and_downloads_nothing(self) -> None:
        self.install_old("0.2.0")
        message = self.update()
        self.assertIn("up to date", message)
        self.assertEqual(self.tools.calls, [])

    def test_no_app_says_so_and_installs_nothing(self) -> None:
        message = self.update()
        self.assertIn("not installed", message)
        self.assertIn("bosun app install", message)
        self.assertEqual(self.tools.calls, [])
        self.assertFalse(self.apps.exists())

    def test_an_empty_team_id_refuses_an_update_too(self) -> None:
        (self.root / "app" / "publisher.json").write_text(
            json.dumps({"bundle_id": BUNDLE_ID, "team_id": "", "releases": SLUG})
        )
        self.install_old("0.1.0")
        with self.assertRaises(AppError) as caught:
            self.update()
        self.assertEqual(str(caught.exception), EMPTY_TEAM)
        self.assertEqual(self.tools.calls, [])


class StatusTest(World):
    def status(self, login: Path | None = None) -> dict[str, Any]:
        return app_install.status(self.setup_for(), login)

    def test_not_installed(self) -> None:
        self.assertEqual(
            self.status(),
            {"contract": 1, "installed": None, "wanted": "0.2.0", "path": str(self.app), "login": False},
        )

    def test_installed_names_the_bundles_version(self) -> None:
        self.install_old("0.1.0")
        self.assertEqual(self.status()["installed"], "0.1.0")

    def test_a_bundle_with_no_readable_version_is_not_installed(self) -> None:
        self.install_old()
        (self.app / "Contents" / "Info.plist").write_text("broken")
        self.assertIsNone(self.status()["installed"])

    def test_login_is_whether_the_job_s_plist_is_there(self) -> None:
        plist = self.home / "job.plist"
        self.assertFalse(self.status(plist)["login"])
        plist.write_text("x")
        self.assertTrue(self.status(plist)["login"])


class RemoveTest(World):
    def test_a_running_app_is_asked_to_quit_and_its_folder_removed(self) -> None:
        self.install_old()
        self.tools.running = True
        self.assertTrue(app_install.quit_app(self.setup_for()))
        self.assertFalse(self.tools.running)
        self.assertIn("removed", app_install.delete_app(self.setup_for()))
        self.assertFalse(self.app.exists())

    def test_an_app_that_is_not_running_is_not_asked_to_quit(self) -> None:
        self.install_old()
        self.assertFalse(app_install.quit_app(self.setup_for()))
        self.assertFalse(any("to quit" in " ".join(call) for call in self.tools.calls))

    def test_nothing_installed_is_said_not_failed(self) -> None:
        self.assertIn("not installed", app_install.delete_app(self.setup_for()))

    def test_an_app_that_will_not_quit_is_not_removed(self) -> None:
        self.install_old()
        self.tools.running = True
        self.tools.quits = False
        with self.assertRaisesRegex(AppError, "still running"):
            app_install.quit_app(self.setup_for())
        self.assertTrue(self.app.exists())


class CommandTest(World):
    def main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = app_install.main(
                list(argv), root=self.root, home=self.home, run=self.tools, environ={"BOSUN_APP_RELEASES": ""}
            )
        return status, out.getvalue(), err.getvalue()

    def test_status_json_is_one_object(self) -> None:
        status, out, _ = self.main("status", "--json")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out)["contract"], 1)
        self.assertEqual(out.count("\n"), 1)

    def test_status_says_it_in_words_too(self) -> None:
        self.install_old("0.1.0")
        status, out, _ = self.main("status")
        self.assertEqual(status, 0)
        self.assertIn("0.1.0", out)
        self.assertIn("0.2.0", out)

    def test_a_refusal_is_one_line_on_stderr_and_exit_1(self) -> None:
        self.tools.team = "ZZZZZ99999"
        status, out, err = self.main("install")
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("another team", err)

    def test_an_install_says_it_is_done_and_exits_0(self) -> None:
        status, out, _ = self.main("install")
        self.assertEqual(status, 0)
        self.assertIn("installed", out)

    def test_the_setting_reaches_the_lookup(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            app_install.main(
                ["install"], root=self.root, home=self.home, run=self.tools,
                environ={"BOSUN_APP_RELEASES": "mirror/bosun"},
            )  # fmt: skip
        self.assertIn("github.com/mirror/bosun/releases", self.tools.calls[0][-1])

    def test_usage_errors_exit_2(self) -> None:
        for argv in ((), ("bogus",), ("install", "--bogus"), ("status", "--bogus"), ("update", "now")):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                status, _, _ = self.main(*argv)
                self.assertEqual(status, 2)
        self.assertEqual(self.tools.calls, [])

    def test_quit_and_delete_are_the_steps_of_remove(self) -> None:
        self.install_old()
        self.tools.running = True
        self.assertEqual(self.main("quit")[0], 0)
        self.assertEqual(self.main("delete")[0], 0)
        self.assertFalse(self.app.exists())


if __name__ == "__main__":
    unittest.main()
