"""What `bosun app` decides without running a tool (docs/design/menu-bar.md): who publishes the app, which
release and address it comes from, what the signature must say, which archives are refused, and what `status`
says. The
live side, which runs codesign, curl and the rest and moves files, is lib/app_install.py.

It reads only files in the code's own folder (app/publisher.json, app/VERSION) and an app's Info.plist.
"""

from __future__ import annotations

import json
import plistlib
import re
import sys
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

import update_check


CONTRACT = 1  # the shape of `status --json`; the menu bar app reads it
BUNDLE = "bosun.app"
APPLICATIONS = "Applications"  # in the home folder: where the app is installed
SHOWN = 5  # of the extra names in an archive that a refusal lists
SYMLINK = 0o120000  # the file type bits of a zip entry that is a symbolic link
FILE_TYPE = 0o170000
TEAM_ID = re.compile(r"^[A-Z0-9]{10}\Z")  # \Z, not $: which also matches before a final newline
BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*\Z")  # goes into AppleScript's text: nothing else may
VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]*\Z")
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
GITHUB = re.compile(
    r"^(?:(?:https?|ssh|git)://)?(?:[^/@\s]+@)?github\.com[/:]([A-Za-z0-9][A-Za-z0-9-]*)/"
    r"([A-Za-z0-9_][A-Za-z0-9_.-]*?)(?:\.git)?/?$"
)
NO_TEAM = "app/publisher.json has no team_id yet"
BAD_TEAM = "app/publisher.json's team_id is not a Team ID"
BAD_BUNDLE_ID = "app/publisher.json's bundle_id is not a bundle id"
NO_BUILD = "app/build.sh is missing"


class AppError(Exception):
    """Something refused or failed, in the one line it is shown as."""


# ---- who publishes it, and where ------------------------------------------------------------------------


@dataclass(frozen=True)
class Publisher:
    bundle_id: str
    team_id: str  # "" until the publisher's own is filled in
    releases: str  # owner/repo of the releases


def read_publisher(root: Path) -> Publisher:
    path = root / "app" / "publisher.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise AppError(f"app/publisher.json cannot be read: {error}") from error
    if not isinstance(data, dict):
        raise AppError("app/publisher.json must hold an object")
    fields = {key: data.get(key, "") for key in ("bundle_id", "team_id", "releases")}
    if not all(isinstance(value, str) for value in fields.values()):
        raise AppError("app/publisher.json must hold the strings bundle_id, team_id and releases")
    bundle_id, team_id, releases = (str(value) for value in fields.values())
    if not BUNDLE_ID.match(bundle_id):
        raise AppError("app/publisher.json has no bundle_id")
    return Publisher(bundle_id, team_id, releases)


def require_team(publisher: Publisher) -> str:
    """The publisher's Team ID, which an install checks the app against: refused while it is not filled in,
    and unless it is a Team ID, since it goes into the code requirement the signature must meet."""
    if not publisher.team_id:
        raise AppError(NO_TEAM)
    if not TEAM_ID.match(publisher.team_id):
        raise AppError(BAD_TEAM)
    return publisher.team_id


def requirement(bundle_id: str, team_id: str) -> str:
    """The code requirement that only the publisher's Developer ID build meets: signed by Apple's Developer ID
    chain (the intermediate and the leaf each carry their Developer ID extension), the code's identifier is
    <bundle_id>, and the leaf certificate's organizational unit, which is the Team ID, is <team_id>.
    `codesign` checks it against the certificates in the signature, never against what codesign prints."""
    if not TEAM_ID.match(team_id):
        raise AppError(BAD_TEAM)
    if not BUNDLE_ID.match(bundle_id):
        raise AppError(BAD_BUNDLE_ID)
    return (
        f'anchor apple generic and identifier "{bundle_id}"'
        " and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */"
        " and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */"
        f' and certificate leaf[subject.OU] = "{team_id}"'
    )


def wanted_version(root: Path) -> str:
    """The app version this checkout works with (app/VERSION)."""
    try:
        version = (root / "app" / "VERSION").read_text().strip()
    except OSError as error:
        raise AppError(f"app/VERSION cannot be read: {error}") from error
    if not VERSION.match(version):
        raise AppError(f"app/VERSION is not a version: {version!r}")
    return version


def github_slug(url: str) -> str | None:
    """owner/repo of a GitHub address, in any form git takes; None for any other host (credentials in it
    are dropped, never kept)."""
    found = GITHUB.match(url.strip())
    return f"{found[1]}/{found[2]}" if found else None


def _setting_slug(value: str, name: str) -> str:
    slug = value if SLUG.match(value) else github_slug(value)
    if slug is None:
        shown = update_check.strip_credentials(value)
        raise AppError(f"{name} is neither owner/repo nor a GitHub address: {shown}")
    return slug


def releases_slug(configured: str, publisher: Publisher, origin: Callable[[], str]) -> str:
    """owner/repo to look for releases in: BOSUN_APP_RELEASES (<configured>), else the publisher's, else
    the install's origin when that is on GitHub. <origin> is only called when nothing else names it."""
    if configured.strip():
        return _setting_slug(configured.strip(), "BOSUN_APP_RELEASES")
    if publisher.releases:
        return _setting_slug(publisher.releases, "app/publisher.json's releases")
    slug = github_slug(origin())
    if slug is None:
        raise AppError(
            "no address to look for releases at: set BOSUN_APP_RELEASES to <owner>/<repo>, "
            "as this install's origin is not on GitHub"
        )
    return slug


def release_asset_url(releases: str, version: str) -> str:
    """Where release app-v<version> of <releases> (owner/repo) keeps its zip."""
    if not SLUG.match(releases):
        raise AppError(f"the releases address is not owner/repo: {update_check.strip_credentials(releases)}")
    if not VERSION.match(version):
        raise AppError(f"the app version is not a version: {version!r}")
    base = f"https://github.com/{releases}/releases/download/app-v{version}"
    return f"{base}/bosun-app-{version}.zip"


# ---- what a download may hold ---------------------------------------------------------------------------


def entry_problem(entry: zipfile.ZipInfo) -> str | None:
    """Why one entry of an archive is refused: an absolute path, a `..` component (either would unpack
    outside the folder it is given) or a symbolic link (one could point anywhere)."""
    name = entry.filename
    parts = name.replace("\\", "/").split("/")
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return f"the archive has an entry with an absolute path: {name}"
    if ".." in parts:
        return f"the archive has an entry that climbs out of its folder: {name}"
    if (entry.external_attr >> 16) & FILE_TYPE == SYMLINK:
        return f"the archive has a symbolic link: {name}"
    return None


def entries_problem(entries: Iterable[zipfile.ZipInfo]) -> str | None:
    """Why an archive is refused before anything is unpacked, from its entries; the first one found."""
    return next((problem for entry in entries if (problem := entry_problem(entry))), None)


def archive_problem(folder: Path) -> str | None:
    """Why what was unpacked in <folder> is refused: it must be exactly one bosun.app (a real folder) and
    nothing else but macOS's __MACOSX."""
    names = sorted(path.name for path in folder.iterdir() if path.name != "__MACOSX")
    if BUNDLE not in names:
        return f"the archive does not hold {BUNDLE}"
    extras = [name for name in names if name != BUNDLE]
    if extras:
        listed = ", ".join(extras[:SHOWN]) + (", ..." if len(extras) > SHOWN else "")
        return f"the archive must hold {BUNDLE} and nothing else, but also holds: {listed}"
    if not (folder / BUNDLE).is_dir() or (folder / BUNDLE).is_symlink():
        return f"{BUNDLE} in the archive is not a folder"
    return None


def tree_problem(folder: Path) -> str | None:
    """Why what was unpacked in <folder> is refused after all: a symbolic link anywhere in it, or a path that
    resolves outside it. The entries were refused for a link before unpacking, so a link here is one the
    unpacker made from what the central directory did not show (a local header, say)."""
    root = folder.resolve()
    for path in sorted(folder.rglob("*")):
        shown = path.relative_to(folder).as_posix()
        if path.is_symlink():
            return f"the unpacked archive holds a symbolic link its entries did not list: {shown}"
        if not path.resolve().is_relative_to(root):
            return f"the unpacked archive holds a path outside its folder: {shown}"
    return None


# ---- the app on disk ------------------------------------------------------------------------------------


def read_info(app: Path) -> dict[str, Any] | None:
    try:
        data = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    except (OSError, ValueError, ExpatError):
        return None
    return data if isinstance(data, dict) else None


def bundle_version(app: Path) -> str | None:
    """CFBundleShortVersionString of the bundle at <app>; None when there is none or it cannot be read."""
    info = read_info(app)
    version = info.get("CFBundleShortVersionString") if info else None
    return version if isinstance(version, str) and version else None


def not_ours(app: Path, bundle_id: str) -> str | None:
    """Why the bundle at <app> cannot be said to be the publisher's (<bundle_id>), or None when it is: it is
    not a bundle, its Info.plist cannot be read, or its CFBundleIdentifier is another."""
    if not app.is_dir() or app.is_symlink():
        return f"{app.name} is not an app bundle"
    info = read_info(app)
    if info is None:
        return "its Info.plist cannot be read"
    found = info.get("CFBundleIdentifier")
    return None if found == bundle_id else f"its bundle id is {found}, not the publisher's {bundle_id}"


def app_path(home: Path) -> Path:
    """Where the app is, or would be installed, for the home folder <home>."""
    return home / APPLICATIONS / BUNDLE


def status(root: Path, app: Path, login_plist: Path | None) -> dict[str, Any]:
    """The app's state, for `bosun app status --json`: `login` is whether the login job's plist is there."""
    return {
        "contract": CONTRACT,
        "installed": bundle_version(app),
        "wanted": wanted_version(root),
        "path": str(app),
        "login": login_plist is not None and login_plist.exists(),
    }


def describe(state: Mapping[str, Any]) -> str:
    """`status` in a line."""
    login = "on" if state["login"] else "off"
    have = (
        f"{BUNDLE} {state['installed']} is installed" if state["installed"] else f"{BUNDLE} is not installed"
    )
    return f"{have} (this bosun wants {state['wanted']}); Open at Login: {login}"


# ---- the command line, for the release workflow ---------------------------------------------------------

USAGE = "Usage: app_release.py requirement"
ROOT = Path(__file__).resolve().parents[1]


def main(argv: Sequence[str], root: Path = ROOT) -> int:
    """`requirement`: print the code requirement `bosun app install` checks a build against, for
    app/publisher.json's bundle id and Team ID. The release workflow gives it to `codesign --verify -R`, so a
    release the install would refuse is never published and the requirement is written in one place. Exit 0,
    1 (one line on stderr: no or a wrong Team ID, an unreadable file) or 2 (usage)."""
    if list(argv) != ["requirement"]:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        publisher = read_publisher(root)
        print(requirement(publisher.bundle_id, require_team(publisher)))
    except AppError as error:
        print(f"app_release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
