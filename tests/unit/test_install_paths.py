"""Which path a process writes out for what outlives it (lib/install_paths.py)."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from install_paths import REPO, install_bin, install_path, is_version_folder, versions_dir


class InstallPathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.version = self.home / ".local" / "share" / "bosun-versions" / "0123456789ab"
        self.version.mkdir(parents=True)
        (self.version.parent / REPO).mkdir()  # the bare repository the versions are worktrees of

    def test_a_version_folder_writes_out_the_link(self) -> None:
        self.assertEqual(install_path(self.version), self.home / ".local" / "share" / "bosun")

    def test_the_checkout_kept_from_before_versions_writes_out_the_link(self) -> None:
        """A conversion put back leads the link to it again (lib/update_gate.py's _point_back)."""
        legacy = self.version.parent / "legacy-0123456789ab"
        legacy.mkdir()
        self.assertEqual(install_path(legacy), self.home / ".local" / "share" / "bosun")

    def test_a_checkout_writes_out_itself(self) -> None:
        self.assertEqual(install_path(self.home / "src" / "bosun"), self.home / "src" / "bosun")

    def test_versions_dir_sits_beside_the_link(self) -> None:
        self.assertEqual(versions_dir(Path("/h/.local/share/bosun")), Path("/h/.local/share/bosun-versions"))

    def test_only_a_folder_inside_versions_is_one(self) -> None:
        self.assertTrue(is_version_folder(self.version))
        self.assertFalse(is_version_folder(self.version.parent))
        self.assertFalse(is_version_folder(self.home / "src" / "bosun"))

    def test_the_command_is_the_links_bin(self) -> None:
        self.assertEqual(
            install_bin(self.version), self.home / ".local" / "share" / "bosun" / "bin" / "bosun"
        )

    def test_a_parent_named_only_versions_is_not_one(self) -> None:
        odd = self.home / "-versions" / "x"
        (odd.parent / REPO).mkdir(parents=True)
        self.assertFalse(is_version_folder(odd))
        self.assertEqual(install_path(odd), odd)

    def test_a_versions_folder_without_the_repository_holds_checkouts(self) -> None:
        """R22: a checkout of a pull request in ~/src/bosun-versions/pr-123 is a checkout, not a
        version of an install at ~/src/bosun."""
        checkout = self.home / "src" / "bosun-versions" / "pr-123"
        checkout.mkdir(parents=True)
        self.assertFalse(is_version_folder(checkout))
        self.assertEqual(install_path(checkout), checkout)
        self.assertEqual(install_bin(checkout), checkout / "bin" / "bosun")


if __name__ == "__main__":
    unittest.main()
