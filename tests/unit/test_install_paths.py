"""Which path a process writes out for what outlives it (lib/install_paths.py)."""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from install_paths import install_bin, install_path, is_version_folder, versions_dir


VERSION = Path("/h/.local/share/bosun-versions/0123456789ab")


def test_a_version_folder_writes_out_the_link() -> None:
    assert install_path(VERSION) == Path("/h/.local/share/bosun")


def test_a_checkout_writes_out_itself() -> None:
    assert install_path(Path("/h/src/bosun")) == Path("/h/src/bosun")


def test_versions_dir_sits_beside_the_link() -> None:
    assert versions_dir(Path("/h/.local/share/bosun")) == Path("/h/.local/share/bosun-versions")


def test_only_a_folder_inside_versions_is_one() -> None:
    assert is_version_folder(VERSION)
    assert not is_version_folder(Path("/h/.local/share/bosun-versions"))
    assert not is_version_folder(Path("/h/src/bosun"))


def test_the_command_is_the_links_bin() -> None:
    assert install_bin(VERSION) == Path("/h/.local/share/bosun/bin/bosun")
