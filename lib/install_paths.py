"""The two paths of an installed bosun (docs/design/update.md, "Two paths, kept apart").

A process runs the code of its own folder. Anything that outlives it (a hook command in a
session's settings, a launchd job, the `~/.local/bin/bosun` link) names the install link instead,
which a later version switches to point at itself. With the versions kept side by side the
link is `<install>` and each version is a folder in `<install>-versions`, beside the bare
repository they are worktrees of (REPO). Anywhere else, a checkout included (one in a folder that
only happens to be named `<x>-versions`), both paths are the checkout. lib/common.sh derives
`BOSUN_INSTALL` by the same rule.
"""

from __future__ import annotations

from pathlib import Path


VERSIONS_SUFFIX = "-versions"
REPO = "bosun.git"  # the bare repository in the versions folder (lib/versions.py)


def is_version_folder(root: Path) -> bool:
    """True for a folder directly inside `<name>-versions` that holds REPO."""
    parent = root.parent
    named = len(parent.name) > len(VERSIONS_SUFFIX) and parent.name.endswith(VERSIONS_SUFFIX)
    return named and (parent / REPO).is_dir()


def install_path(root: Path) -> Path:
    """The link to write out for the code in `root`: its own folder unless it is a version."""
    if is_version_folder(root):
        return root.parent.with_name(root.parent.name.removesuffix(VERSIONS_SUFFIX))
    return root


def install_bin(root: Path) -> Path:
    """The command to write out for the code in `root`."""
    return install_path(root) / "bin" / "bosun"


def versions_dir(install: Path) -> Path:
    """The folder that holds the versions of the install at `install`."""
    return install.with_name(install.name + VERSIONS_SUFFIX)
