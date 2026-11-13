"""lib/app_install.py: `bosun app`'s verifying and placing of bosun.app, in a scratch home. Every tool it runs
(curl, ditto, codesign, spctl, osascript, open) is a fake behind its one runner, and subprocess is shut for
the whole file (tests/unit/app_fakes.py), so no test can reach a real tool, download anything or touch
~/Applications. What is decided without a tool is test_app_release.py."""

from __future__ import annotations

import contextlib
import io
import json
import os
import plistlib
import shutil
import signal
import sys
import tempfile
import unittest
import warnings
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import app_install
import app_release
import update_facts
from app_fakes import (
    BUNDLE_ID,
    EMPTY_TEAM,
    NOT_A_TEAM,
    PUBLISHER,
    SYMLINK_ATTR,
    TEAM,
    World,
    tree,
    write_bundle,
)
from app_install import Run, Setup
from app_release import AppError, Publisher


OU_LINE = 'certificate leaf[subject.OU] = "ABCDE12345"'  # pragma: allowlist secret gitleaks:allow


class VerifyTest(World):
    def setUp(self) -> None:
        super().setUp()
        self.bundle = self.home / "staged" / "bosun.app"
        write_bundle(self.bundle, "0.1.0")

    def verify(self, publisher: Publisher = PUBLISHER) -> str | None:
        return app_install.verify(self.bundle, publisher, self.tools)

    def test_the_publisher_s_notarized_app_passes_after_two_checks_and_the_bundle_id(self) -> None:
        self.assertIsNone(self.verify())
        requirement = app_release.requirement(BUNDLE_ID, TEAM)
        self.assertEqual(
            self.tools.calls,
            [
                ["codesign", "--verify", "--deep", "--strict", "-R", f"={requirement}", str(self.bundle)],
                ["spctl", "--assess", "--type", "execute", "-vv", str(self.bundle)],
            ],
        )

    def test_the_requirement_given_to_codesign_is_the_one_built_for_the_publisher(self) -> None:
        self.verify()
        self.assertEqual(
            self.tools.requirements, ["=" + app_release.requirement(BUNDLE_ID, TEAM)]
        )  # text introduced by =
        self.assertIn(OU_LINE, self.tools.requirements[0])

    def test_nothing_is_read_from_codesign_s_description(self) -> None:
        self.verify()  # the fake raises if codesign -dv is ever run
        self.assertFalse(any("-dv" in call for call in self.tools.calls))

    def test_a_forged_identifier_is_refused_by_the_requirement_s_exit_code(self) -> None:
        """A signer can put a line `TeamIdentifier=<the publisher's>` in the signing identifier (codesign
        takes a newline in it), ahead of the real one in what `codesign -dv` prints. The requirement is
        checked against the certificates: an ad hoc bundle has none, and codesign says exit 3."""
        self.tools.meets_requirement = False
        reason = self.verify()
        self.assertIn("not signed with the publisher's Developer ID", str(reason))
        self.assertIn(TEAM, str(reason))
        self.assertEqual(self.tools.names(), ["codesign"])  # and it goes no further

    def test_an_unsigned_app_is_refused(self) -> None:
        self.tools.verify_says = "code object is not signed at all"
        self.assertEqual(self.verify(), "it is not signed")

    def test_a_signature_that_does_not_verify_is_refused_saying_why(self) -> None:
        self.tools.verify_says = "a sealed resource is missing or invalid"
        reason = self.verify()
        self.assertIn("signature does not verify", str(reason))
        self.assertIn("a sealed resource is missing", str(reason))

    def test_a_requirement_codesign_cannot_read_fails_rather_than_passes(self) -> None:
        self.tools.verify_says = "invalid or corrupted code requirement(s)"
        self.assertIn("does not verify", str(self.verify()))

    def test_unsatisfied_means_the_exit_code_not_the_words(self) -> None:
        self.tools.codesign = lambda argv: Run(3, "", "something else entirely")  # type: ignore[method-assign]
        self.assertIn("Developer ID", str(self.verify()))

    def test_an_app_macos_does_not_accept_is_refused(self) -> None:
        self.tools.assess_says = "rejected"
        reason = self.verify()
        self.assertIn("not notarized", str(reason))
        self.assertIn("rejected", str(reason))

    def test_an_accepted_app_that_is_not_a_notarized_developer_id_is_refused(self) -> None:
        for source in (
            None,
            "source=Developer ID",
            "source=Notarized Developer ID extra",
            "source=Apple System",
        ):
            with self.subTest(source=source):
                self.tools.assess_source = source
                self.assertEqual(self.verify(), "Gatekeeper's assessment did not say Notarized Developer ID")

    def test_gatekeeper_switched_off_has_no_source_line_and_is_refused(self) -> None:
        self.tools.spctl = lambda argv: Run(0, "", "assessments disabled")  # type: ignore[method-assign]
        self.assertEqual(self.verify(), "Gatekeeper's assessment did not say Notarized Developer ID")

    def test_the_source_line_must_be_a_line_of_its_own(self) -> None:
        self.tools.spctl = lambda argv: Run(0, "", "x: accepted source=Notarized Developer ID")  # type: ignore[method-assign]
        self.assertIn("did not say", str(self.verify()))

    def test_the_source_line_is_found_on_either_stream(self) -> None:
        self.tools.spctl = lambda argv: Run(0, "x: accepted\nsource=Notarized Developer ID\n", "")  # type: ignore[method-assign]
        self.assertIsNone(self.verify())

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
        self.assertIn("not an app", str(app_install.verify(plain, PUBLISHER, self.tools)))
        self.assertEqual(self.tools.calls, [])

    def test_no_valid_team_id_never_passes_anything(self) -> None:
        for team in ("", "not set", "abcde12345", "ABCDE12345\n"):  # pragma: allowlist secret gitleaks:allow
            with self.subTest(team=team):
                self.assertIsNotNone(self.verify(Publisher(BUNDLE_ID, team, "x/y")))
        self.assertEqual(self.tools.calls, [])


class InstallTest(World):
    def install(self, **kwargs: Any) -> str:
        return app_install.install(self.setup_for(), **kwargs)

    def test_it_downloads_unpacks_verifies_and_places_the_app(self) -> None:
        message = self.install()
        self.assertEqual(app_release.bundle_version(self.app), "0.2.0")
        self.assertIn("installed", message)
        curl = self.tools.calls[0]
        self.assertEqual(
            curl[-1], "https://github.com/acme/bosun/releases/download/app-v0.2.0/bosun-app-0.2.0.zip"
        )
        self.assertEqual(self.tools.names(), ["curl", "ditto", "codesign", "spctl", "osascript"])
        self.assertEqual(self.tools.calls[1][1:3], ["-x", "-k"])

    def test_curl_reads_no_curlrc_and_takes_https_only_also_after_a_redirect(self) -> None:
        self.install()
        curl = self.tools.calls[0]
        self.assertEqual(curl[:3], ["curl", "-q", "-fsSL"])  # -q first, or it is not the first argument
        for flag in ("--proto", "--proto-redir"):
            self.assertEqual(curl[curl.index(flag) + 1], "=https")
        self.assertEqual(curl[curl.index("-w") + 1], "%{http_code}")

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
        self.write_publisher("")
        with self.assertRaises(AppError) as caught:
            self.install()
        self.assertEqual(str(caught.exception), EMPTY_TEAM)
        self.assertEqual(self.tools.calls, [])
        self.assertFalse(self.apps.exists())

    def test_a_malformed_team_id_refuses_before_anything_is_downloaded(self) -> None:
        for team in ("not set", "ABCDE12345\nTeamIdentifier=X", 'ABCDE12345" or true'):
            with self.subTest(team=team):
                self.write_publisher(team)
                with self.assertRaises(AppError) as caught:
                    self.install()
                self.assertEqual(str(caught.exception), NOT_A_TEAM)
                self.assertEqual(self.tools.calls, [])

    def test_a_refused_app_leaves_applications_exactly_as_it_was(self) -> None:
        self.install_old()
        before = tree(self.home)
        for knob, value, normal in (
            ("meets_requirement", False, True),
            ("assess_says", "rejected", None),
            ("assess_source", None, "source=Notarized Developer ID"),
            ("verify_says", "x", None),
        ):
            with self.subTest(knob=knob):
                setattr(self.tools, knob, value)
                with self.assertRaises(AppError):
                    self.install()
                self.assertEqual(tree(self.home), before)
                setattr(self.tools, knob, normal)

    def test_a_refusal_with_no_applications_folder_makes_none(self) -> None:
        self.tools.meets_requirement = False
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

    def test_an_entry_that_would_unpack_outside_or_link_elsewhere_is_refused_before_ditto_runs(self) -> None:
        link = zipfile.ZipInfo("bosun.app/Contents/link")
        link.external_attr = SYMLINK_ATTR
        for entry, says in (
            (zipfile.ZipInfo("/etc/escape"), "absolute path"),
            (zipfile.ZipInfo("../escape"), "climbs out"),
            (zipfile.ZipInfo("bosun.app/../../escape"), "climbs out"),
            (link, "symbolic link"),
        ):
            with self.subTest(entry=entry.filename):
                self.tools.zip_extra = [entry]
                with self.assertRaisesRegex(AppError, says):
                    self.install()
                self.assertNotIn("ditto", self.tools.names())
                self.assertFalse(self.apps.exists())
                self.tools.calls.clear()

    def test_a_local_header_that_names_another_path_than_the_directory_is_refused_before_ditto(self) -> None:
        """ditto may unpack by the local headers, which the central directory need not agree with: every entry
        is read back, which compares the two names."""
        name = "bosun.app/Contents/Resources/notes.txt"
        evil = "../../Library/LaunchAgents/x.plist"
        evil += "x" * (len(name) - len(evil))
        original = self.tools.curl

        def curl(argv: Sequence[str]) -> Run:
            done = original(argv)
            target = Path(argv[argv.index("-o") + 1])
            with zipfile.ZipFile(target, "a") as bundle:
                bundle.writestr(name, "x")
            data = target.read_bytes()
            target.write_bytes(data.replace(name.encode(), evil.encode(), 1))  # the local header comes first
            return done

        self.tools.curl = curl  # type: ignore[method-assign]
        with self.assertRaisesRegex(AppError, "not a zip archive"):
            self.install()
        self.assertNotIn("ditto", self.tools.names())
        self.assertFalse(self.apps.exists())

    def test_an_entry_named_twice_is_refused_before_ditto(self) -> None:
        self.tools.zip_extra = [zipfile.ZipInfo("bosun.app/Contents/Info.plist")]
        with (
            warnings.catch_warnings(),  # zipfile warns as the fake writes the name twice
            self.assertRaisesRegex(AppError, "names an entry twice: bosun.app/Contents/Info.plist"),
        ):
            warnings.simplefilter("ignore", UserWarning)
            self.install()
        self.assertNotIn("ditto", self.tools.names())

    def test_a_link_in_what_was_unpacked_is_refused_before_codesign(self) -> None:
        """The entries listed no link, so one in the unpacked tree came from what the directory did not
        show."""
        original = self.tools.ditto
        for target in ("../../outside", "MacOS/bosun"):
            with self.subTest(target=target):
                self.tools.calls.clear()

                def ditto(argv: Sequence[str], to: str = target) -> Run:
                    done = original(argv)
                    (Path(argv[-1]) / "bosun.app" / "Contents" / "link").symlink_to(to)
                    return done

                self.tools.ditto = ditto  # type: ignore[method-assign]
                with self.assertRaisesRegex(
                    AppError, "symbolic link its entries did not list: bosun.app/Contents/link"
                ):
                    self.install()
                self.assertNotIn("codesign", self.tools.names())
                self.assertFalse(self.apps.exists())

    def test_a_missing_release_says_so_in_a_line_with_the_status(self) -> None:
        self.tools.download = 22
        with self.assertRaises(AppError) as caught:
            self.install()
        self.assertIn("no release of bosun.app 0.2.0 at acme/bosun", str(caught.exception))
        self.assertIn("HTTP 404", str(caught.exception))
        self.assertNotIn("\n", str(caught.exception))
        self.assertFalse(self.apps.exists())

    def test_another_http_error_says_which(self) -> None:
        self.tools.download = 22
        for status in ("403", "500", "429"):
            with self.subTest(status=status):
                self.tools.http_status = status
                with self.assertRaises(AppError) as caught:
                    self.install()
                self.assertIn(f"HTTP {status}", str(caught.exception))
                self.assertNotIn("is not there", str(caught.exception))

    def test_another_download_failure_says_what_curl_said(self) -> None:
        self.tools.download = 6
        with self.assertRaisesRegex(AppError, "could not download"):
            self.install()

    def test_a_download_that_is_not_a_zip_is_refused(self) -> None:
        self.tools.not_a_zip = True
        with self.assertRaisesRegex(AppError, "not a zip"):
            self.install()
        self.assertFalse(self.apps.exists())

    def test_a_zip_ditto_cannot_read_is_refused(self) -> None:
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

    def test_when_the_old_app_cannot_be_put_back_the_folder_that_holds_it_is_kept_and_named(self) -> None:
        self.install_old()
        real = os.rename

        def rename(source: Any, target: Any) -> None:
            if Path(str(target)) == self.app:
                raise OSError("disk trouble")
            real(source, target)

        with mock.patch("os.rename", rename), self.assertRaises(AppError) as caught:
            self.install()
        kept = [path for path in self.apps.iterdir() if path.name.startswith(".bosun-app-")]
        self.assertEqual(len(kept), 1)
        self.assertTrue((kept[0] / "previous" / "bosun.app" / "Contents" / "old.txt").exists())
        self.assertIn(str(kept[0] / "previous" / "bosun.app"), str(caught.exception))

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
        self.tools.meets_requirement = False
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

    def test_automation_denied_says_so_and_how_to_allow_it(self) -> None:
        self.install_old()
        before = tree(self.home)
        self.tools.running = True
        self.tools.quit_says = "denied"
        with self.assertRaises(AppError) as caught:
            self.install()
        self.assertIn("Automation", str(caught.exception))
        self.assertIn("System Settings", str(caught.exception))
        self.assertIn("quit bosun.app by hand", str(caught.exception))
        self.assertEqual(tree(self.home), before)

    def test_another_failure_to_ask_it_to_quit_is_one_line(self) -> None:
        self.install_old()
        self.tools.running = True
        self.tools.quit_says = "other"
        with self.assertRaisesRegex(AppError, "could not ask bosun.app to quit"):
            self.install()

    def test_an_answer_osascript_cannot_give_does_not_replace_a_possibly_running_app(self) -> None:
        self.install_old()
        before = tree(self.home)
        self.tools.running = True
        self.tools.running_says = "unknown"
        with self.assertRaisesRegex(AppError, "could not tell whether bosun.app is running"):
            self.install()
        self.assertEqual(tree(self.home), before)

    def test_an_answer_it_cannot_give_after_the_quit_does_not_replace_it_either(self) -> None:
        self.install_old()
        before = tree(self.home)
        self.tools.running = True
        self.tools.quits = False
        self.tools.unknown_after_quit = True
        with self.assertRaisesRegex(AppError, "could not tell whether bosun.app is running"):
            self.install()
        self.assertEqual(tree(self.home), before)

    def test_an_app_launch_services_does_not_know_is_not_running(self) -> None:
        self.tools.running_says = "unregistered"
        self.install()
        self.assertEqual(app_release.bundle_version(self.app), "0.2.0")
        self.assertNotIn("open", self.tools.names())

    def test_when_the_rename_in_fails_after_a_quit_the_old_app_is_opened_again(self) -> None:
        self.install_old()
        self.tools.running = True
        real = os.rename

        def rename(source: Any, target: Any) -> None:
            if Path(str(target)) == self.app and "previous" not in str(source):
                raise OSError("disk trouble")
            real(source, target)

        with mock.patch("os.rename", rename), self.assertRaises(AppError):
            self.install()
        self.assertEqual(self.tools.calls[-1], ["open", str(self.app)])
        self.assertTrue((self.app / "Contents" / "old.txt").exists())

    def test_an_open_that_fails_is_said_after_the_app_is_in(self) -> None:
        self.install_old()
        self.tools.running = True
        self.tools.open_fails = True
        with self.assertRaisesRegex(AppError, "installed, but could not be opened"):
            self.install()
        self.assertEqual(app_release.bundle_version(self.app), "0.2.0")

    def test_no_tool_is_found_by_a_pattern_match(self) -> None:
        self.install_old()
        self.tools.running = True
        self.install()
        for call in self.tools.calls:
            self.assertNotIn(call[0], ("pkill", "killall", "pgrep", "ps", "kill"))


class OwnershipTest(World):
    """Nothing is replaced or removed that cannot be said to be the publisher's."""

    def foreign(self, bundle_id: str = "ai.other.app") -> None:
        self.apps.mkdir()
        write_bundle(self.app, "9.9", bundle_id)

    def test_install_leaves_another_app_of_that_name_and_says_so(self) -> None:
        self.foreign()
        before = tree(self.home)
        with self.assertRaises(AppError) as caught:
            app_install.install(self.setup_for())
        self.assertIn("ai.other.app", str(caught.exception))
        self.assertIn("left as it is", str(caught.exception))
        self.assertEqual(tree(self.home), before)
        self.assertEqual(self.tools.calls, [])  # not even downloaded

    def test_install_does_not_replace_what_it_cannot_read(self) -> None:
        self.apps.mkdir()
        self.app.mkdir()
        with self.assertRaises(AppError):
            app_install.install(self.setup_for())
        self.assertTrue(self.app.is_dir())

    def test_delete_leaves_another_app_and_says_so(self) -> None:
        self.foreign()
        with self.assertRaises(AppError) as caught:
            app_install.delete_app(self.setup_for())
        self.assertIn("ai.other.app", str(caught.exception))
        self.assertTrue(self.app.exists())

    def test_update_does_not_replace_another_app_either(self) -> None:
        self.foreign()
        with self.assertRaises(AppError):
            app_install.update(self.setup_for())
        self.assertEqual(app_release.bundle_version(self.app), "9.9")


class VerificationIsExplicitTest(World):
    """Whether to verify is a parameter, never inferred from the team_id."""

    def place(self, team: str, *, development: bool) -> str:
        def produce(scratch: Path) -> Path:
            write_bundle(scratch / "made" / "bosun.app", "0.2.0")
            return scratch / "made" / "bosun.app"

        publisher = Publisher(BUNDLE_ID, team, "acme/bosun")
        return app_install.place_app(self.setup_for(), publisher, produce, development=development)

    def test_a_non_development_install_with_an_empty_or_invalid_team_id_refuses(self) -> None:
        for team in ("", "not set", "abcde12345", "ABCDE12345\n"):  # pragma: allowlist secret gitleaks:allow
            with self.subTest(team=team), self.assertRaisesRegex(AppError, "no Team ID to check"):
                self.place(team, development=False)
            self.assertFalse(self.apps.exists())
            self.assertEqual(self.tools.calls, [])

    def test_only_development_skips_the_checks_whatever_the_team_id(self) -> None:
        self.place("", development=True)
        self.place(TEAM, development=True)
        self.assertFalse({"codesign", "spctl"} & set(self.tools.names()))  # not verified
        self.assertEqual(app_release.bundle_version(self.app), "0.2.0")

    def test_a_valid_team_id_is_verified_when_not_development(self) -> None:
        self.place(TEAM, development=False)
        self.assertEqual(self.tools.names()[:2], ["codesign", "spctl"])


class BuildTest(World):
    """`install --build`: app/build.sh --identity - --out <a folder>, and a development build installed."""

    def write_build(self) -> Path:
        build = self.root / "app" / "build.sh"
        build.write_text("#!/bin/sh\n")
        build.chmod(0o755)
        return build

    def install(
        self, build: Path, *, leaves: bool = True, status: int = 0, says: str = ""
    ) -> tuple[str, list[Sequence[str]]]:
        made: list[Sequence[str]] = []

        def run(argv: Sequence[str]) -> Run:
            if argv[0] == str(build):
                made.append(argv)
                if leaves:
                    write_bundle(Path(argv[argv.index("--out") + 1]) / "bosun.app", "0.2.0")
                return Run(status, "", says)
            return self.tools(argv)

        message = app_install.install(Setup(self.root, self.home, "", run, lambda _: None), build=True)
        return message, made

    def test_without_the_build_script_it_says_so_and_changes_nothing(self) -> None:
        with self.assertRaises(AppError) as caught:
            app_install.install(self.setup_for(), build=True)
        self.assertEqual(str(caught.exception), "app/build.sh is missing")
        self.assertEqual(self.tools.calls, [])
        self.assertFalse(self.apps.exists())

    def test_it_builds_ad_hoc_into_a_temporary_folder_it_is_given(self) -> None:
        build = self.write_build()
        _, made = self.install(build)
        self.assertEqual(len(made), 1)
        self.assertEqual(list(made[0][:4]), [str(build), "--identity", "-", "--out"])
        self.assertEqual(len(made[0]), 5)
        out = Path(made[0][4])
        self.assertTrue(out.is_relative_to(self.apps))  # beside the install's own scratch
        self.assertFalse(out.exists())  # and gone again

    def test_the_app_it_makes_is_placed_without_the_team_check_and_called_a_development_build(self) -> None:
        message, _ = self.install(self.write_build())
        self.assertEqual(app_release.bundle_version(self.app), "0.2.0")
        self.assertIn("installed in ~/Applications", message)
        self.assertIn("development build", message)
        self.assertFalse({"spctl", "codesign", "curl"} & set(self.tools.names()))  # ad hoc: macOS refuses it

    def test_a_build_is_not_held_up_by_an_empty_team_id(self) -> None:
        self.write_publisher("")
        message, _ = self.install(self.write_build())
        self.assertIn("development build", message)

    def test_a_build_that_fails_shows_the_last_lines_of_its_output_and_leaves_nothing_behind(self) -> None:
        says = "warning: x\nerror: no such module 'Pretend'\nerror: fatalError"
        with self.assertRaises(AppError) as caught:
            self.install(self.write_build(), leaves=False, status=1, says=says)
        message = str(caught.exception)
        self.assertTrue(message.startswith("app/build.sh failed"), message)
        self.assertIn("error: no such module 'Pretend'", message)
        self.assertIn("error: fatalError", message)
        self.assertFalse(self.apps.exists())

    def test_a_build_that_fails_without_a_word_says_so_in_one_line_and_to_run_it_again(self) -> None:
        for says in ("", "\n  \n"):
            with self.subTest(says=says), self.assertRaises(AppError) as caught:
                self.install(self.write_build(), leaves=False, status=1, says=says)
            message = str(caught.exception)
            self.assertEqual(len(message.splitlines()), 1, message)
            self.assertIn("app/build.sh exited 1 and said nothing", message)
            self.assertIn("run it again", message)
            self.assertIn("before the script starts", message)
            self.assertIn("docs/design/menu-bar.md", message)
            self.assertFalse(self.apps.exists())

    def test_the_exit_code_is_the_one_it_had(self) -> None:
        with self.assertRaises(AppError) as caught:
            self.install(self.write_build(), leaves=False, status=137, says="")
        self.assertIn("exited 137 and said nothing", str(caught.exception))

    def test_only_the_last_twenty_lines_of_a_long_output_are_shown(self) -> None:
        says = "\n".join(f"line {number}" for number in range(1, 31))
        with self.assertRaises(AppError) as caught:
            self.install(self.write_build(), leaves=False, status=1, says=says)
        lines = str(caught.exception).splitlines()
        self.assertEqual([line.strip() for line in lines[1:]], [f"line {n}" for n in range(11, 31)])

    def test_it_says_it_is_building_before_it_does(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.install(self.write_build())
        self.assertIn("building bosun.app", stderr.getvalue())
        self.assertIn("minutes", stderr.getvalue())

    def test_a_build_that_leaves_no_app_is_refused_and_leaves_nothing_behind(self) -> None:
        with self.assertRaisesRegex(AppError, "did not leave bosun.app"):
            self.install(self.write_build(), leaves=False)
        self.assertFalse(self.apps.exists())


class StaleTest(World):
    """What an install that was killed leaves: swept before the next, and the old app given back."""

    def leftover(self, name: str = ".bosun-app-4242-abc", *, with_old: bool = False) -> Path:
        self.apps.mkdir(exist_ok=True)
        folder = self.apps / name
        (folder / "unpacked").mkdir(parents=True)
        (folder / "unpacked" / "junk").write_text("a full unpacked app")
        if with_old:
            write_bundle(folder / "previous" / "bosun.app", "0.1.0")
            (folder / "previous" / "bosun.app" / "Contents" / "old.txt").write_text("the old one")
        return folder

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(app_install, "_alive", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_install_sweeps_the_leftovers_of_a_dead_one(self) -> None:
        folder = self.leftover()
        app_install.install(self.setup_for())
        self.assertFalse(folder.exists())
        self.assertEqual([path.name for path in self.apps.iterdir()], ["bosun.app"])

    def test_it_sweeps_even_when_the_install_is_then_refused(self) -> None:
        folder = self.leftover()
        self.tools.meets_requirement = False
        with self.assertRaises(AppError):
            app_install.install(self.setup_for())
        self.assertFalse(folder.exists())

    def test_the_old_app_is_given_back_when_none_is_there_and_a_leftover_holds_it(self) -> None:
        folder = self.leftover(with_old=True)
        self.tools.meets_requirement = False  # the install itself is refused: only the sweep matters here
        with self.assertRaises(AppError):
            app_install.install(self.setup_for())
        self.assertTrue((self.app / "Contents" / "old.txt").exists())
        self.assertEqual(app_release.bundle_version(self.app), "0.1.0")
        self.assertFalse(folder.exists())

    def test_an_old_app_in_a_leftover_is_not_put_over_one_that_is_there(self) -> None:
        self.install_old("0.3.0")
        folder = self.leftover(with_old=True)
        self.tools.meets_requirement = False
        with self.assertRaises(AppError):
            app_install.install(self.setup_for())
        self.assertEqual(app_release.bundle_version(self.app), "0.3.0")
        self.assertFalse(folder.exists())

    def test_remove_sweeps_first_and_removes_what_it_gave_back(self) -> None:
        folder = self.leftover(with_old=True)
        message = app_install.delete_app(self.setup_for())
        self.assertIn("removed", message)
        self.assertFalse(self.app.exists())
        self.assertFalse(folder.exists())

    def test_remove_sweeps_with_no_app_at_all(self) -> None:
        folder = self.leftover()
        self.assertIn("not installed", app_install.delete_app(self.setup_for()))
        self.assertFalse(folder.exists())

    def test_a_folder_of_a_live_install_is_left_alone(self) -> None:
        folder = self.leftover()
        with mock.patch.object(app_install, "_alive", return_value=True):
            app_install.install(self.setup_for())
        self.assertTrue(folder.exists())

    def test_a_folder_that_is_not_one_of_ours_is_left_alone(self) -> None:
        self.apps.mkdir()
        mine = self.apps / ".bosun-app-looks-like-a-file"
        mine.write_text("x")
        other = self.apps / ".bosun-other"
        other.mkdir()
        app_install.install(self.setup_for())
        self.assertTrue(mine.exists() and other.exists())

    def test_when_the_old_app_cannot_be_given_back_the_leftover_stays_and_the_install_stops(self) -> None:
        folder = self.leftover(with_old=True)
        real = os.rename

        def rename(source: Any, target: Any) -> None:
            if Path(str(target)) == self.app:
                raise OSError("disk trouble")
            real(source, target)

        with mock.patch("os.rename", rename), self.assertRaises(AppError) as caught:
            app_install.install(self.setup_for())
        self.assertTrue((folder / "previous" / "bosun.app").exists())
        self.assertIn(str(folder / "previous" / "bosun.app"), str(caught.exception))


class AliveTest(unittest.TestCase):
    def test_a_pid_is_alive_unless_there_is_no_such_process(self) -> None:
        with mock.patch("os.kill", side_effect=ProcessLookupError):
            self.assertFalse(app_install._alive(4242))
        with mock.patch("os.kill", side_effect=PermissionError):  # somebody else's, but there
            self.assertTrue(app_install._alive(4242))
        with mock.patch("os.kill", return_value=None):
            self.assertTrue(app_install._alive(4242))


class SignalTest(World):
    def test_sigterm_and_sighup_end_the_install_as_a_failure_that_cleans_up(self) -> None:
        for number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number.name):
                self.install_old()
                before = tree(self.home)
                self.tools.on_download = lambda number=number: signal.raise_signal(number)
                with self.assertRaises(AppError) as caught:
                    app_install.install(self.setup_for())
                self.assertIn(number.name, str(caught.exception))
                self.assertEqual(tree(self.home), before)
                shutil.rmtree(self.apps, ignore_errors=True)

    def test_the_handlers_there_before_are_put_back(self) -> None:
        before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
        app_install.install(self.setup_for())
        self.assertEqual((signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)), before)

    def test_and_after_a_failure_too(self) -> None:
        before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
        self.tools.meets_requirement = False
        with self.assertRaises(AppError):
            app_install.install(self.setup_for())
        self.assertEqual((signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)), before)

    def test_a_signal_between_the_two_renames_puts_the_old_app_back(self) -> None:
        self.install_old()
        before = tree(self.home)
        real = os.rename

        def rename(source: Any, target: Any) -> None:
            real(source, target)
            if Path(str(target)).name == "bosun.app" and "previous" in str(target):
                signal.raise_signal(signal.SIGTERM)  # the old app has just been moved aside

        with mock.patch("os.rename", rename), self.assertRaises(AppError):
            app_install.install(self.setup_for())
        self.assertEqual(tree(self.home), before)

    def test_ctrl_c_between_the_renames_never_loses_the_old_app(self) -> None:
        self.install_old()
        before = tree(self.home)
        real = os.rename

        def rename(source: Any, target: Any) -> None:
            real(source, target)
            if "previous" in str(target):
                raise KeyboardInterrupt  # the old app has just been moved aside

        with mock.patch("os.rename", rename), self.assertRaises(KeyboardInterrupt):
            app_install.install(self.setup_for())
        self.assertEqual(tree(self.home), before)

    def test_a_second_end_while_the_old_app_is_being_put_back_never_loses_it(self) -> None:
        self.install_old()
        before = tree(self.home)
        real_rename, real_restore = os.rename, app_install._restore
        restores: list[int] = []

        def rename(source: Any, target: Any) -> None:
            real_rename(source, target)
            if "previous" in str(target):
                signal.raise_signal(signal.SIGTERM)  # the first end

        def restore(aside: Path, dest: Path) -> None:
            restores.append(1)
            if len(restores) == 1:
                raise KeyboardInterrupt  # the second, while it puts the old app back
            real_restore(aside, dest)

        with (
            mock.patch("os.rename", rename),
            mock.patch.object(app_install, "_restore", restore),
            self.assertRaises(KeyboardInterrupt),
        ):
            app_install.install(self.setup_for())
        self.assertEqual(tree(self.home), before)

    def test_when_clean_up_cannot_give_the_old_app_back_it_keeps_the_folder_and_says_where(self) -> None:
        self.install_old()
        real = os.rename

        def rename(source: Any, target: Any) -> None:
            if Path(str(target)) == self.app:
                raise OSError("disk trouble")  # neither the rename in nor any put back works
            real(source, target)
            if "previous" in str(target):
                raise KeyboardInterrupt

        with mock.patch("os.rename", rename), self.assertRaises(AppError) as caught:
            app_install.install(self.setup_for())
        kept = [path for path in self.apps.iterdir() if path.name.startswith(".bosun-app-")]
        self.assertEqual(len(kept), 1)
        self.assertTrue((kept[0] / "previous" / "bosun.app" / "Contents" / "old.txt").exists())
        self.assertIn(str(kept[0] / "previous" / "bosun.app"), str(caught.exception))

    def test_a_signal_after_the_rename_in_says_what_is_actually_there(self) -> None:
        self.install_old()
        real = os.rename

        def rename(source: Any, target: Any) -> None:
            real(source, target)
            if Path(str(target)) == self.app and "unpacked" in str(source):
                signal.raise_signal(signal.SIGTERM)  # the new app has just been put in

        with mock.patch("os.rename", rename), self.assertRaises(AppError) as caught:
            app_install.install(self.setup_for())
        self.assertIn("SIGTERM", str(caught.exception))
        self.assertIn("bosun.app 0.2.0 is in ~/Applications", str(caught.exception))
        self.assertNotIn("put back", str(caught.exception))
        self.assertEqual(app_release.bundle_version(self.app), "0.2.0")
        self.assertEqual([path.name for path in self.apps.iterdir()], ["bosun.app"])

    def test_a_signal_that_was_ignored_stays_ignored(self) -> None:
        seen: list[Any] = []
        self.tools.on_download = lambda: seen.append(signal.getsignal(signal.SIGHUP))
        before = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        self.addCleanup(signal.signal, signal.SIGHUP, before)
        app_install.install(self.setup_for())
        self.assertEqual(seen, [signal.SIG_IGN])  # under nohup a hangup is not an end
        self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
        self.assertNotEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)

    def test_ctrl_c_on_the_command_line_is_one_line_and_exit_130(self) -> None:
        self.tools.on_download = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = app_install.main(["install"], root=self.root, home=self.home, run=self.tools, environ={})
        self.assertEqual((status, out.getvalue()), (130, ""))
        self.assertEqual(err.getvalue().strip(), "bosun app: interrupted")
        self.assertFalse(self.apps.exists())


class ScratchTest(World):
    def test_a_scratch_folder_that_cannot_be_made_removes_the_applications_folder_this_made(self) -> None:
        with mock.patch("tempfile.mkdtemp", side_effect=OSError("no room")), self.assertRaises(OSError):
            app_install.install(self.setup_for())
        self.assertFalse(self.apps.exists())

    def test_one_that_was_there_stays(self) -> None:
        self.apps.mkdir()
        with mock.patch("tempfile.mkdtemp", side_effect=OSError("no room")), self.assertRaises(OSError):
            app_install.install(self.setup_for())
        self.assertTrue(self.apps.is_dir())

    def test_the_scratch_folder_carries_the_pid_of_its_install(self) -> None:
        names: list[str] = []
        self.tools.on_download = lambda: names.extend(path.name for path in self.apps.iterdir())
        app_install.install(self.setup_for())
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], rf"^\.bosun-app-{os.getpid()}-")


class CurlBudgetTest(unittest.TestCase):
    def test_curl_ends_a_slow_download_itself_before_the_update_stops_the_command(self) -> None:
        """`bosun app update` run by `bosun update` is killed at update_facts.COMMAND_SECONDS: curl's own
        --max-time must end first, with its own message."""
        budget = int(app_install.CURL[app_install.CURL.index("--max-time") + 1])
        self.assertLess(budget, update_facts.COMMAND_SECONDS)


class UpdateTest(World):
    def update(self) -> str:
        return app_install.update(self.setup_for())

    def test_a_different_version_is_installed(self) -> None:
        self.install_old("0.1.0")
        self.update()
        self.assertEqual(app_release.bundle_version(self.app), self.tools.version)
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

    def test_no_app_is_not_installed_before_any_team_check(self) -> None:
        """An install with no app of the publisher's asks nothing of the publisher: `bosun update` runs this
        on every install, app or not, and a Team ID not filled in yet is no failure of it."""
        for team in ("", "not set"):
            with self.subTest(team=team):
                self.write_publisher(team)
                self.assertIn("not installed", self.update())
                self.assertEqual(self.tools.calls, [])

    def test_the_same_version_is_up_to_date_with_no_team_id_too(self) -> None:
        self.write_publisher("")
        self.install_old("0.2.0")
        self.assertIn("up to date", self.update())
        self.assertEqual(self.tools.calls, [])

    def test_an_empty_team_id_refuses_an_update_too(self) -> None:
        self.write_publisher("")
        self.install_old("0.1.0")
        with self.assertRaises(AppError) as caught:
            self.update()
        self.assertEqual(str(caught.exception), EMPTY_TEAM)
        self.assertEqual(self.tools.calls, [])


class RemoveTest(World):
    def test_a_running_app_is_asked_to_quit_and_its_folder_removed(self) -> None:
        self.install_old()
        self.tools.running = True
        self.assertTrue(app_install.quit_app(self.setup_for(), BUNDLE_ID))
        self.assertFalse(self.tools.running)
        self.assertIn("removed", app_install.delete_app(self.setup_for()))
        self.assertFalse(self.app.exists())

    def test_an_app_that_is_not_running_is_not_asked_to_quit(self) -> None:
        self.install_old()
        self.assertFalse(app_install.quit_app(self.setup_for(), BUNDLE_ID))
        self.assertFalse(any("to quit" in " ".join(call) for call in self.tools.calls))

    def test_nothing_installed_is_said_not_failed(self) -> None:
        self.assertIn("not installed", app_install.delete_app(self.setup_for()))

    def test_an_app_that_will_not_quit_is_not_removed(self) -> None:
        self.install_old()
        self.tools.running = True
        self.tools.quits = False
        with self.assertRaisesRegex(AppError, "still running"):
            app_install.quit_app(self.setup_for(), BUNDLE_ID)
        self.assertTrue(self.app.exists())

    def test_quit_takes_the_bundle_id_it_is_handed(self) -> None:
        (self.root / "app" / "publisher.json").unlink()  # it is not read again
        self.assertFalse(app_install.quit_app(self.setup_for(), "ai.handed.over"))
        self.assertIn("ai.handed.over", " ".join(self.tools.calls[0]))

    def test_an_app_osascript_cannot_say_anything_of_is_not_taken_for_not_running(self) -> None:
        self.install_old()
        self.tools.running_says = "unknown"
        with self.assertRaisesRegex(AppError, "could not tell"):
            app_install.quit_app(self.setup_for(), BUNDLE_ID)


class CommandTest(World):
    def main(self, *argv: str, **env: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = app_install.main(
                list(argv),
                root=self.root,
                home=self.home,
                run=self.tools,
                environ={"BOSUN_APP_RELEASES": "", **env},
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
        self.tools.meets_requirement = False
        status, out, err = self.main("install")
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("Developer ID", err)
        self.assertNotIn("test-requirement", err)  # the fake's own wording is not shown

    def test_an_install_says_it_is_done_and_exits_0(self) -> None:
        status, out, _ = self.main("install")
        self.assertEqual(status, 0)
        self.assertIn("installed", out)

    def test_the_setting_reaches_the_lookup(self) -> None:
        self.main("install", BOSUN_APP_RELEASES="mirror/bosun")
        self.assertIn("github.com/mirror/bosun/releases", self.tools.calls[0][-1])

    def test_an_os_error_is_one_line_and_exit_1_not_a_traceback(self) -> None:
        with mock.patch("tempfile.mkdtemp", side_effect=PermissionError("Permission denied: root's folder")):
            status, out, err = self.main("install")
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("Permission denied", err)

    def test_an_os_error_while_removing_is_one_line_too(self) -> None:
        self.install_old()
        with mock.patch("shutil.rmtree", side_effect=PermissionError("Permission denied")):
            status, _, err = self.main("delete")
        self.assertEqual(status, 1)
        self.assertEqual(len(err.strip().splitlines()), 1)

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


class VerifyArchiveTest(World):
    """`verify-archive <zip>`: the release workflow's check of its final zip, which is what an install runs on
    a download (the same functions), without installing anything."""

    def make_zip(self, *extra: zipfile.ZipInfo) -> Path:
        archive = self.home / "bosun-app-0.2.0.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("bosun.app/Contents/Info.plist", "x")
            for entry in extra:
                bundle.writestr(entry, "x")
        return archive

    def check(self, archive: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = app_install.main(
                ["verify-archive", str(archive or self.make_zip())],
                root=self.root,
                home=self.home,
                run=self.tools,
                environ={},
            )
        return status, out.getvalue(), err.getvalue()

    def test_a_good_zip_passes_the_same_tools_in_the_same_order_as_an_install(self) -> None:
        status, out, err = self.check()
        self.assertEqual((status, err), (0, ""))
        self.assertIn("passes", out)
        self.assertEqual(self.tools.names(), ["ditto", "codesign", "spctl"])
        self.assertEqual(self.tools.calls[0][1:3], ["-x", "-k"])
        self.assertEqual(self.tools.requirements, ["=" + app_release.requirement(BUNDLE_ID, TEAM)])

    def test_it_installs_nothing_and_leaves_no_folder_behind(self) -> None:
        unpacked: list[Path] = []
        original = self.tools.ditto

        def ditto(argv: Sequence[str]) -> Run:
            unpacked.append(Path(argv[-1]))
            return original(argv)

        self.tools.ditto = ditto  # type: ignore[method-assign]
        self.check()
        self.assertFalse(self.apps.exists())
        self.assertTrue(unpacked)
        self.assertFalse(unpacked[0].parent.exists())

    def test_it_cleans_up_after_a_refusal_too(self) -> None:
        unpacked: list[Path] = []
        original = self.tools.ditto

        def ditto(argv: Sequence[str]) -> Run:
            unpacked.append(Path(argv[-1]))
            return original(argv)

        self.tools.ditto = ditto  # type: ignore[method-assign]
        self.tools.meets_requirement = False
        self.assertEqual(self.check()[0], 1)
        self.assertFalse(unpacked[0].parent.exists())

    def test_what_the_install_refuses_in_an_archive_it_refuses(self) -> None:
        link = zipfile.ZipInfo("bosun.app/Contents/link")
        link.external_attr = SYMLINK_ATTR
        for entry, says in (
            (zipfile.ZipInfo("/etc/escape"), "absolute path"),
            (zipfile.ZipInfo("bosun.app/../../escape"), "climbs out"),
            (link, "symbolic link"),
        ):
            with self.subTest(entry=entry.filename):
                self.tools.calls.clear()
                status, _, err = self.check(self.make_zip(entry))
                self.assertEqual(status, 1)
                self.assertIn(says, err)
                self.assertNotIn("ditto", self.tools.names())

    def test_an_archive_with_anything_beside_the_app_is_refused(self) -> None:
        self.tools.archive = ["bosun.app", "another.txt"]
        status, _, err = self.check()
        self.assertEqual(status, 1)
        self.assertIn("must hold bosun.app and nothing else", err)

    def test_an_archive_with_no_bosun_app_is_refused(self) -> None:
        self.tools.archive = ["other.txt"]
        status, _, err = self.check()
        self.assertEqual(status, 1)
        self.assertIn("does not hold bosun.app", err)

    def test_not_a_zip_and_a_zip_ditto_cannot_read_are_refused(self) -> None:
        plain = self.home / "x.zip"
        plain.write_text("not a zip")
        self.assertIn("not a zip archive", self.check(plain)[2])
        self.tools.unzip_fails = True
        self.assertIn("not a zip archive", self.check()[2])

    def test_a_bundle_id_that_is_not_the_publisher_s_is_refused(self) -> None:
        self.tools.bundle_id = "ai.example.other"
        status, _, err = self.check()
        self.assertEqual(status, 1)
        self.assertIn("ai.example.other", err)

    def test_a_signature_that_does_not_meet_the_requirement_is_refused(self) -> None:
        self.tools.meets_requirement = False
        status, _, err = self.check()
        self.assertEqual(status, 1)
        self.assertIn("publisher's Developer ID", err)
        self.assertNotIn("spctl", self.tools.names())

    def test_gatekeeper_must_say_notarized_on_a_line_of_its_own_and_its_words_are_shown(self) -> None:
        self.tools.assess_source = "source=Developer ID"
        status, _, err = self.check()
        self.assertEqual(status, 1)
        self.assertIn("did not say Notarized Developer ID", err)
        self.assertIn("spctl said: source=Developer ID", err)

    def test_spctl_s_exit_status_counts(self) -> None:
        self.tools.assess_says = "rejected"
        status, _, err = self.check()
        self.assertEqual(status, 1)
        self.assertIn("spctl said: ", err)
        self.assertIn("rejected", err)

    def test_without_a_team_id_it_stops_before_any_tool(self) -> None:
        self.write_publisher("")
        status, _, err = self.check()
        self.assertEqual(status, 1)
        self.assertIn("no team_id", err)
        self.assertEqual(self.tools.calls, [])

    def test_the_archive_is_an_argument(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            status = app_install.main(["verify-archive"], root=self.root, home=self.home, run=self.tools)
        self.assertEqual(status, 2)


class RunnerTest(unittest.TestCase):
    def test_the_default_runner_is_the_only_place_that_runs_a_tool(self) -> None:
        with tempfile.TemporaryDirectory() as folder, mock.patch("subprocess.run") as run:
            run.side_effect = FileNotFoundError
            done = app_install.run_tool(["nonexistent-tool", folder])
        self.assertEqual((done.returncode, "command not found" in done.stderr), (127, True))


if __name__ == "__main__":
    unittest.main()
