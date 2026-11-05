"""The two paths of an installed bosun (pure; docs/design/update.md, "Two paths, kept apart").

A process runs the code of its own folder. Anything that outlives it (a hook command in a
session's settings, a launchd job, the `~/.local/bin/bosun` link) names the install link instead,
which a later version switches to point at itself. With the versions kept side by side the
link is `<install>` and each version is a folder in `<install>-versions`. In a checkout there is
no such folder, and both paths are the checkout. lib/common.sh derives `BOSUN_INSTALL` by the
same rule.
"""

from __future__ import annotations

from pathlib import Path


VERSIONS_SUFFIX = "-versions"


def is_version_folder(root: Path) -> bool:
    """True for a folder directly inside `<name>-versions`."""
    parent = root.parent.name
    return len(parent) > len(VERSIONS_SUFFIX) and parent.endswith(VERSIONS_SUFFIX)


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
