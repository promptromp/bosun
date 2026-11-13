"""lib/app_release.py: what `bosun app` decides without running a tool: the publisher, the release address,
the code requirement, which archives are refused and `status`. The tool-running side is test_app_install.py.
Each test runs in a World (tests/unit/app_fakes.py), which shuts subprocess, so none can reach a real tool."""

from __future__ import annotations

import json
import sys
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import app_install
import app_release
import update_lookup
from app_fakes import (
    BUNDLE_ID,
    EMPTY_TEAM,
    NOT_A_TEAM,
    PUBLISHER,
    SLUG,
    SYMLINK_ATTR,
    TEAM,
    World,
    write_bundle,
)
from app_release import AppError, Publisher


# Made-up addresses with a made-up password, which must never get into an address or a message.
AT_GITHUB = "https://user:secret@github.com/acme/bosun.git"  # pragma: allowlist secret gitleaks:allow
AT_GITLAB = (
    "https://user:hunter2@gitlab.example.com/acme/bosun.git"  # pragma: allowlist secret gitleaks:allow
)
AT_EXAMPLE = "https://user:hunter2@example.com/x"  # pragma: allowlist secret gitleaks:allow
# The requirement for ABCDE12345 and ai.example.bosun, written out: the Developer ID chain, the identifier,
# the team in the leaf certificate's organizational unit.
EXPECTED_REQUIREMENT = (
    'anchor apple generic and identifier "ai.example.bosun"'
    " and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */"
    " and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */"
    ' and certificate leaf[subject.OU] = "ABCDE12345"'  # pragma: allowlist secret gitleaks:allow
)


def entry(name: str, attr: int = 0) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = attr
    return info


class ReleaseLookupTest(World):
    def test_the_asset_url(self) -> None:
        self.assertEqual(
            app_release.release_asset_url("acme/bosun", "0.1.0"),
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
            "acme/bosun\n",
        ):
            with self.subTest(slug=slug), self.assertRaises(AppError):
                app_release.release_asset_url(slug, "0.1.0")
        for version in ("", "0.1.0/../x", "0.1 0", "0.1.0#x", "0.1.0\n"):
            with self.subTest(version=version), self.assertRaises(AppError):
                app_release.release_asset_url("acme/bosun", version)

    def test_github_addresses_in_every_form(self) -> None:
        for url in (
            "https://github.com/acme/bosun.git",
            "https://github.com/acme/bosun",
            "git@github.com:acme/bosun.git",
            "ssh://git@github.com/acme/bosun.git",
            "https://github.com/acme/bosun/",
        ):
            with self.subTest(url=url):
                self.assertEqual(app_release.github_slug(url), "acme/bosun")

    def test_credentials_in_an_address_are_never_in_the_slug(self) -> None:
        self.assertEqual(app_release.github_slug(AT_GITHUB), "acme/bosun")

    def test_another_host_is_not_github(self) -> None:
        for url in (
            "https://gitlab.com/acme/bosun.git",
            "https://github.com.evil.example/acme/bosun.git",
            "https://example.com/github.com/acme/bosun",
            "/Users/me/src/bosun",
            "",
        ):
            with self.subTest(url=url):
                self.assertIsNone(app_release.github_slug(url))

    def test_the_order_is_the_setting_then_the_publisher_then_the_origin(self) -> None:
        def origin() -> str:
            return "https://github.com/fork/bosun.git"

        self.assertEqual(app_release.releases_slug("mirror/bosun", PUBLISHER, origin), "mirror/bosun")
        self.assertEqual(app_release.releases_slug("", PUBLISHER, origin), SLUG)
        self.assertEqual(app_release.releases_slug("", Publisher(BUNDLE_ID, TEAM, ""), origin), "fork/bosun")

    def test_the_origin_is_only_read_when_nothing_else_names_the_releases(self) -> None:
        def origin() -> str:
            raise AssertionError("the origin was read")

        self.assertEqual(app_release.releases_slug("mirror/bosun", PUBLISHER, origin), "mirror/bosun")
        self.assertEqual(app_release.releases_slug("", PUBLISHER, origin), SLUG)

    def test_a_setting_that_is_a_github_address_is_taken_as_its_slug(self) -> None:
        slug = app_release.releases_slug("https://tok@github.com/mirror/bosun.git", PUBLISHER, lambda: "")
        self.assertEqual(slug, "mirror/bosun")

    def test_nothing_to_go_by_says_so_without_credentials(self) -> None:
        with self.assertRaises(AppError) as caught:
            app_release.releases_slug("", Publisher(BUNDLE_ID, TEAM, ""), lambda: AT_GITLAB)
        self.assertIn("BOSUN_APP_RELEASES", str(caught.exception))
        self.assertNotIn("hunter2", str(caught.exception))

    def test_a_setting_that_is_neither_is_refused_without_credentials(self) -> None:
        with self.assertRaises(AppError) as caught:
            app_release.releases_slug(AT_EXAMPLE, PUBLISHER, lambda: "")
        self.assertIn("BOSUN_APP_RELEASES", str(caught.exception))
        self.assertNotIn("hunter2", str(caught.exception))

    def test_the_origin_is_the_one_the_update_reads(self) -> None:
        with mock.patch.object(
            update_lookup, "origin_url", return_value="https://github.com/o/r.git"
        ) as read:
            found = app_install.install_origin(Path("/nowhere/checkout"))
        self.assertEqual(found, "https://github.com/o/r.git")
        read.assert_called_once()

    def test_an_origin_that_cannot_be_read_is_none_at_all(self) -> None:
        with mock.patch.object(update_lookup, "origin_url", side_effect=update_lookup.LookupFailed("no")):
            self.assertEqual(app_install.install_origin(Path("/nowhere/checkout")), "")


class PublisherTest(World):
    def test_the_publisher_file_is_read(self) -> None:
        self.assertEqual(app_release.read_publisher(self.root), PUBLISHER)

    def test_a_missing_or_broken_file_is_refused_in_a_line(self) -> None:
        (self.root / "app" / "publisher.json").write_text("{")
        with self.assertRaisesRegex(AppError, "publisher.json"):
            app_release.read_publisher(self.root)
        (self.root / "app" / "publisher.json").unlink()
        with self.assertRaisesRegex(AppError, "publisher.json"):
            app_release.read_publisher(self.root)

    def test_a_file_without_a_bundle_id_is_refused(self) -> None:
        (self.root / "app" / "publisher.json").write_text(json.dumps({"team_id": TEAM, "releases": SLUG}))
        with self.assertRaisesRegex(AppError, "bundle_id"):
            app_release.read_publisher(self.root)

    def test_the_wanted_version(self) -> None:
        self.assertEqual(app_release.wanted_version(self.root), "0.2.0")

    def test_no_version_file_is_refused(self) -> None:
        (self.root / "app" / "VERSION").unlink()
        with self.assertRaisesRegex(AppError, "VERSION"):
            app_release.wanted_version(self.root)

    def test_an_empty_team_id_has_its_own_message(self) -> None:
        with self.assertRaises(AppError) as caught:
            app_release.require_team(Publisher(BUNDLE_ID, "", SLUG))
        self.assertEqual(str(caught.exception), EMPTY_TEAM)

    def test_a_team_id_that_is_not_one_is_refused_in_one_line(self) -> None:
        for team in (
            "not set",
            "abcde12345",  # pragma: allowlist secret gitleaks:allow
            "ABCDE1234",  # pragma: allowlist secret gitleaks:allow
            "ABCDE123456",  # pragma: allowlist secret gitleaks:allow
            'ABCDE12345" or true',
            "ABCDE12345\n",
            " ",
        ):
            with self.subTest(team=team), self.assertRaises(AppError) as caught:
                app_release.require_team(Publisher(BUNDLE_ID, team, SLUG))
            self.assertEqual(str(caught.exception), NOT_A_TEAM)

    def test_the_team_id_of_the_file_is_what_goes_in_the_requirement(self) -> None:
        self.assertEqual(app_release.require_team(app_release.read_publisher(self.root)), TEAM)


class RequirementTest(World):
    def test_the_requirement_is_built_exactly(self) -> None:
        self.assertEqual(app_release.requirement(BUNDLE_ID, TEAM), EXPECTED_REQUIREMENT)

    def test_a_team_id_or_bundle_id_that_could_change_it_never_goes_in(self) -> None:
        for team, bundle in (
            ('ABCDE12345" or anchor apple or identifier "x', BUNDLE_ID),
            ("", BUNDLE_ID),
            (TEAM, 'ai.example.bosun" or true or identifier "x'),
            (TEAM, ""),
        ):
            with self.subTest(team=team, bundle=bundle), self.assertRaises(AppError):
                app_release.requirement(bundle, team)


class ArchiveEntriesTest(World):
    def test_a_plain_app_passes(self) -> None:
        entries = [entry("bosun.app/"), entry("bosun.app/Contents/Info.plist"), entry("__MACOSX/._bosun.app")]
        self.assertIsNone(app_release.entries_problem(entries))

    def test_an_absolute_path_is_refused(self) -> None:
        for name in ("/etc/passwd", "\\windows\\x", "C:/x"):
            with self.subTest(name=name):
                self.assertIn("absolute path", str(app_release.entries_problem([entry(name)])))

    def test_a_dot_dot_component_is_refused(self) -> None:
        for name in ("../x", "bosun.app/../../x", "bosun.app/Contents/..", "a\\..\\b"):
            with self.subTest(name=name):
                self.assertIn("climbs out", str(app_release.entries_problem([entry(name)])))

    def test_a_symbolic_link_is_refused(self) -> None:
        problem = app_release.entries_problem([entry("bosun.app/link", SYMLINK_ATTR)])
        self.assertIn("symbolic link", str(problem))

    def test_a_regular_file_with_other_attributes_is_not_a_link(self) -> None:
        regular = 0o100755 << 16
        self.assertIsNone(app_release.entries_problem([entry("bosun.app/run", regular)]))

    def test_the_first_bad_entry_is_the_one_named(self) -> None:
        problem = app_release.entries_problem([entry("ok"), entry("../first"), entry("/second")])
        self.assertIn("../first", str(problem))

    def test_a_dot_dot_inside_a_name_is_fine(self) -> None:
        self.assertIsNone(app_release.entries_problem([entry("bosun.app/a..b/c")]))


class NotOursTest(World):
    def test_the_publishers_bundle_is_ours(self) -> None:
        write_bundle(self.app, "0.1.0")
        self.assertIsNone(app_release.not_ours(self.app, BUNDLE_ID))

    def test_another_bundle_id_is_not(self) -> None:
        write_bundle(self.app, "0.1.0", "ai.other.app")
        self.assertIn("ai.other.app", str(app_release.not_ours(self.app, BUNDLE_ID)))

    def test_what_cannot_be_read_is_not(self) -> None:
        self.app.mkdir(parents=True)
        self.assertIn("Info.plist", str(app_release.not_ours(self.app, BUNDLE_ID)))

    def test_a_file_or_a_link_is_not_a_bundle(self) -> None:
        self.apps.mkdir()
        self.app.write_text("x")
        self.assertIn("not an app", str(app_release.not_ours(self.app, BUNDLE_ID)))
        self.app.unlink()
        self.app.symlink_to(self.home)
        self.assertIn("not an app", str(app_release.not_ours(self.app, BUNDLE_ID)))


class StatusTest(World):
    def status(self, login: Path | None = None) -> dict[str, Any]:
        return app_release.status(self.root, self.app, login)

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

    def test_the_words(self) -> None:
        self.install_old("0.1.0")
        text = app_release.describe(self.status())
        self.assertIn("bosun.app 0.1.0 is installed", text)
        self.assertIn("wants 0.2.0", text)


if __name__ == "__main__":
    unittest.main()
