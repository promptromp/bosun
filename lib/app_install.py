#!/usr/bin/env python3
"""`bosun app`: install, update and remove the menu bar app, bosun.app (docs/design/menu-bar.md).

It accepts only the publisher's notarized build. The app comes from a GitHub release of this repository (or
the mirror BOSUN_APP_RELEASES names), is unpacked in a folder of its own and is placed in ~/Applications only
once `codesign` and `spctl` have vouched for it, its Team ID is the one in app/publisher.json and its bundle
id is that file's. A refused or failed install leaves ~/Applications as it was.

Every external tool (curl, ditto, codesign, spctl, osascript, open) is run through one runner, `Runner`,
which a test replaces. The login job (Open at Login) is launchd's and the shell's (`libexec/bosun-app`;
lib/common.sh renders its plist like the others); here is only whether its plist is there, for `status`.

    app_install.py install [--build]     download and place the release app/VERSION names; --build: the app
                                         that app/build.sh makes, without the team check (developers)
    app_install.py update                install app/VERSION when the installed version differs
    app_install.py status [--json] [--login-plist PATH]
    app_install.py quit                  ask the running app, by its bundle id, to quit (remove's first step)
    app_install.py delete                remove ~/Applications/bosun.app (remove's last step)

Exit status: 0 done, 1 refused or failed (one line saying why), 2 usage.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

import install_paths
import update_check
import update_lookup


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = 1  # the shape of `status --json`; the menu bar app reads it
BUNDLE = "bosun.app"
TOOL_SECONDS = 900  # the most any one tool may take: the download is the slow one
CURL = ("curl", "-fsSL", "--connect-timeout", "30", "--max-time", "600")
HTTP_ERROR = 22  # curl -f: the server answered with an error, such as 404
QUIT_POLLS = 40  # an app asked to quit is looked at this often, QUIT_POLL_SECONDS apart
QUIT_POLL_SECONDS = 0.5
SCRATCH_PREFIX = ".bosun-app-"  # hidden, in ~/Applications itself: a rename into place never crosses a volume
SHOWN = 5  # of the extra names in an archive that a refusal lists
TEAM_ID = re.compile(r"^[A-Z0-9]{10}$")
BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")  # goes into AppleScript's text: nothing else may
VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*$")
GITHUB = re.compile(
    r"^(?:(?:https?|ssh|git)://)?(?:[^/@\s]+@)?github\.com[/:]([A-Za-z0-9][A-Za-z0-9-]*)/"
    r"([A-Za-z0-9_][A-Za-z0-9_.-]*?)(?:\.git)?/?$"
)
NO_TEAM = "app/publisher.json has no team_id yet"
NO_BUILD = "part 2 builds the app"


class AppError(Exception):
    """Something refused or failed, in the one line it is shown as."""


@dataclass(frozen=True)
class Run:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def last_line(self) -> str:
        """The last line the tool said, on either stream: what a refusal quotes."""
        lines = [line.strip() for line in f"{self.stdout}\n{self.stderr}".splitlines() if line.strip()]
        return lines[-1] if lines else f"exit status {self.returncode}"


Runner = Callable[[Sequence[str]], Run]


def run_tool(argv: Sequence[str]) -> Run:
    """The real runner: <argv> run with no shell, no input and a time limit, its output kept."""
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=TOOL_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return Run(127, "", f"{argv[0]}: command not found")
    except subprocess.TimeoutExpired:
        return Run(124, "", f"{argv[0]} did not finish in {TOOL_SECONDS} s")
    return Run(done.returncode, done.stdout, done.stderr)


def pause(seconds: float) -> None:
    time.sleep(seconds)


@dataclass(frozen=True)
class Setup:
    """What a command goes by: the code's folder (app/VERSION, app/publisher.json), the home folder,
    BOSUN_APP_RELEASES, the runner, and how to wait."""

    root: Path
    home: Path
    releases: str = ""
    run: Runner = run_tool
    sleep: Callable[[float], None] = pause

    @property
    def apps(self) -> Path:
        return self.home / "Applications"

    @property
    def app(self) -> Path:
        return self.apps / BUNDLE


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
    """The publisher's Team ID, which an install checks the app against: refused while it is not filled in."""
    if not publisher.team_id:
        raise AppError(NO_TEAM)
    if not TEAM_ID.match(publisher.team_id):
        raise AppError("app/publisher.json: team_id is not a Team ID (ten capital letters and digits)")
    return publisher.team_id


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


def install_origin(root: Path) -> str:
    """Where the install came from: its origin remote, read as `bosun update` reads it (credentials
    taken off); nothing when it cannot be read."""
    repo = root.parent / install_paths.REPO if install_paths.is_version_folder(root) else root / ".git"
    try:
        return update_lookup.origin_url(repo)
    except update_lookup.LookupFailed:
        return ""


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


# ---- what the app is: its signature, team, notarization and identity -------------------------------------


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


def _quoted(app: Path, done: Run) -> str:
    return done.last_line.removeprefix(f"{app}: ")


def _signature_problem(app: Path, run: Runner) -> str | None:
    done = run(["codesign", "--verify", "--deep", "--strict", str(app)])
    if done.returncode == 0:
        return None
    if "not signed at all" in done.last_line:
        return "it is not signed"
    return f"its signature does not verify: {_quoted(app, done)}"


def _team_problem(app: Path, team_id: str, run: Runner) -> str | None:
    done = run(["codesign", "-dv", str(app)])  # it describes on stderr
    if done.returncode != 0:
        return f"its signature cannot be read: {_quoted(app, done)}"
    teams = [
        line.partition("=")[2].strip()
        for line in f"{done.stdout}\n{done.stderr}".splitlines()
        if line.startswith("TeamIdentifier=")
    ]
    found = teams[0] if teams else ""
    if found in ("", "not set"):
        return "it is signed with no Team ID (ad hoc), so it is not the publisher's"
    if found != team_id:
        return f"it is signed by another team ({found}), not the publisher's ({team_id})"
    return None


def _notarization_problem(app: Path, run: Runner) -> str | None:
    done = run(["spctl", "--assess", "--type", "execute", str(app)])
    if done.returncode == 0:
        return None
    return f"macOS does not accept it (not notarized?): {_quoted(app, done)}"


def _identity_problem(app: Path, bundle_id: str) -> str | None:
    info = read_info(app)
    if info is None:
        return "its Info.plist cannot be read"
    found = info.get("CFBundleIdentifier")
    return None if found == bundle_id else f"its bundle id is {found}, not the publisher's {bundle_id}"


def verify(app: Path, team_id: str, bundle_id: str, run: Runner) -> str | None:
    """Why the app at <app> is refused, or None when it is the publisher's notarized build: its signature
    verifies (`codesign --verify --deep --strict`), `codesign -dv` names TeamIdentifier=<team_id>, `spctl
    --assess --type execute` accepts it and its Info.plist's CFBundleIdentifier is <bundle_id>. It stops at
    the first refusal."""
    if not app.is_dir() or app.is_symlink():
        return f"{app.name} is not an app bundle"
    if not TEAM_ID.match(team_id):
        return "there is no Team ID to check it against"
    return (
        _signature_problem(app, run)
        or _team_problem(app, team_id, run)
        or _notarization_problem(app, run)
        or _identity_problem(app, bundle_id)
    )


# ---- getting it and placing it --------------------------------------------------------------------------


def archive_problem(folder: Path) -> str | None:
    """Why what was unpacked in <folder> is refused: it must be exactly one bosun.app (a real folder) and
    nothing else but macOS's __MACOSX."""
    names = sorted(name for name in os.listdir(folder) if name != "__MACOSX")
    if BUNDLE not in names:
        return f"the archive does not hold {BUNDLE}"
    extras = [name for name in names if name != BUNDLE]
    if extras:
        listed = ", ".join(extras[:SHOWN]) + (", ..." if len(extras) > SHOWN else "")
        return f"the archive must hold {BUNDLE} and nothing else, but also holds: {listed}"
    if not (folder / BUNDLE).is_dir() or (folder / BUNDLE).is_symlink():
        return f"{BUNDLE} in the archive is not a folder"
    return None


def fetch_app(setup: Setup, slug: str, version: str, scratch: Path) -> Path:
    """The release's app, downloaded and unpacked in <scratch>, and checked to be one bosun.app."""
    url = release_asset_url(slug, version)
    archive = scratch / "bosun-app.zip"
    done = setup.run([*CURL, "-o", str(archive), url])
    if done.returncode == HTTP_ERROR:
        raise AppError(f"no release of {BUNDLE} {version} at {slug}: {url} is not there")
    if done.returncode != 0:
        raise AppError(f"could not download {url}: {update_check.strip_credentials(done.last_line)}")
    unpacked = scratch / "unpacked"
    unpacked.mkdir()
    done = setup.run(["ditto", "-x", "-k", str(archive), str(unpacked)])
    if done.returncode != 0:
        raise AppError(f"the download is not a zip archive: {done.last_line}")
    problem = archive_problem(unpacked)
    if problem:
        raise AppError(problem)
    return unpacked / BUNDLE


def build_app(setup: Setup, scratch: Path) -> Path:
    """The app app/build.sh makes in a folder of <scratch>, which it is given."""
    built = scratch / "built"
    built.mkdir()
    done = setup.run([str(setup.root / "app" / "build.sh"), str(built)])
    if done.returncode != 0:
        raise AppError(f"app/build.sh failed: {done.last_line}")
    if not (built / BUNDLE).is_dir():
        raise AppError(f"app/build.sh did not leave {BUNDLE} in the folder it was given")
    return built / BUNDLE


def is_running(run: Runner, bundle_id: str) -> bool:
    """Whether the app is running, asked by its bundle id: never found by the name of a process."""
    done = run(["osascript", "-e", f'application id "{bundle_id}" is running'])
    return done.returncode == 0 and done.stdout.strip() == "true"


def quit_app(setup: Setup) -> bool:
    """Ask the running app to quit, by its bundle id, and wait until it has. Whether it was running."""
    bundle_id = read_publisher(setup.root).bundle_id
    if not is_running(setup.run, bundle_id):
        return False
    setup.run(["osascript", "-e", f'tell application id "{bundle_id}" to quit'])
    for _ in range(QUIT_POLLS):
        setup.sleep(QUIT_POLL_SECONDS)
        if not is_running(setup.run, bundle_id):
            return True
    raise AppError(f"{BUNDLE} is still running: quit it, then run this again")


def put_in_place(staged: Path, dest: Path, scratch: Path) -> None:
    """<staged> renamed to <dest>; the app there before is renamed into <scratch> first (which goes with it
    when the install ends), and put back if the rename in fails."""
    aside = scratch / "previous"
    had_old = os.path.lexists(dest)
    if had_old:
        os.rename(dest, aside)
    try:
        os.rename(staged, dest)
    except OSError as error:
        if had_old:
            os.rename(aside, dest)
        raise AppError(f"could not put {BUNDLE} in {dest.parent}: {error}") from error


def remove_if_empty(folder: Path) -> None:
    with contextlib.suppress(OSError):
        folder.rmdir()


def place_app(setup: Setup, produce: Callable[[Path], Path], team_id: str | None) -> str:
    """The app <produce> makes in a scratch folder, checked against <team_id> (None: not checked) and
    placed. The scratch folder is made in ~/Applications, so the rename in is on one volume, and goes
    whatever happens; a ~/Applications this made goes too, unless the install worked."""
    publisher = read_publisher(setup.root)
    created = not setup.apps.exists()
    setup.apps.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=setup.apps))
    reopen = placed = False
    try:
        staged = produce(scratch)
        reason = None if team_id is None else verify(staged, team_id, publisher.bundle_id, setup.run)
        if reason:
            raise AppError(f"refused {BUNDLE}: {reason}")
        reopen = quit_app(setup)
        put_in_place(staged, setup.app, scratch)
        placed = True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        if created and not placed:
            remove_if_empty(setup.apps)
    shown = " ".join(filter(None, [BUNDLE, bundle_version(setup.app)]))
    if reopen:
        opened = setup.run(["open", str(setup.app)])
        if opened.returncode != 0:
            raise AppError(f"{shown} is installed, but could not be opened: {opened.last_line}")
    return f"{shown} installed in ~/Applications"


# ---- the commands ---------------------------------------------------------------------------------------


def install(setup: Setup, *, build: bool = False) -> str:
    """Place the release app/VERSION names (checked against the publisher), or with <build> the app
    app/build.sh makes (not checked: it is ad hoc signed, for developing the app)."""
    publisher = read_publisher(setup.root)
    if build:
        if not (setup.root / "app" / "build.sh").is_file():
            raise AppError(NO_BUILD)
        return place_app(setup, lambda scratch: build_app(setup, scratch), None)
    team_id = require_team(publisher)
    version = wanted_version(setup.root)
    slug = releases_slug(setup.releases, publisher, lambda: install_origin(setup.root))
    return place_app(setup, lambda scratch: fetch_app(setup, slug, version, scratch), team_id)


def update(setup: Setup) -> str:
    """Install app/VERSION when it differs from the installed app; say so, and download nothing, when it is
    the same or there is no app (bringing one is `install`)."""
    require_team(read_publisher(setup.root))
    wanted = wanted_version(setup.root)
    installed = bundle_version(setup.app)
    if installed is None:
        return f"{BUNDLE} is not installed: run bosun app install"
    if installed == wanted:
        return f"{BUNDLE} {wanted} is up to date"
    return install(setup)


def status(setup: Setup, login_plist: Path | None) -> dict[str, Any]:
    """The app's state, for `bosun app status --json`: `login` is whether the login job's plist is there."""
    return {
        "contract": CONTRACT,
        "installed": bundle_version(setup.app),
        "wanted": wanted_version(setup.root),
        "path": str(setup.app),
        "login": login_plist is not None and login_plist.exists(),
    }


def describe(state: Mapping[str, Any]) -> str:
    """`status` in a line."""
    login = "on" if state["login"] else "off"
    have = (
        f"{BUNDLE} {state['installed']} is installed" if state["installed"] else f"{BUNDLE} is not installed"
    )
    return f"{have} (this bosun wants {state['wanted']}); Open at Login: {login}"


def delete_app(setup: Setup) -> str:
    if not os.path.lexists(setup.app):
        return f"{BUNDLE} is not installed in ~/Applications"
    if setup.app.is_symlink() or not setup.app.is_dir():
        setup.app.unlink()
    else:
        shutil.rmtree(setup.app)
    return f"removed ~/Applications/{BUNDLE}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun app", allow_abbrev=False, description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", allow_abbrev=False).add_argument("--build", action="store_true")
    commands.add_parser("update", allow_abbrev=False)
    show = commands.add_parser("status", allow_abbrev=False)
    show.add_argument("--json", action="store_true")
    show.add_argument("--login-plist", type=Path)
    commands.add_parser("quit", allow_abbrev=False)
    commands.add_parser("delete", allow_abbrev=False)
    return parser


def dispatch(args: argparse.Namespace, setup: Setup) -> str:
    if args.command == "install":
        return install(setup, build=args.build)
    if args.command == "update":
        return update(setup)
    if args.command == "status":
        state = status(setup, args.login_plist)
        return json.dumps(state) if args.json else describe(state)
    if args.command == "quit":
        return f"asked {BUNDLE} to quit" if quit_app(setup) else ""
    return delete_app(setup)


def main(
    argv: Sequence[str],
    *,
    root: Path = ROOT,
    home: Path | None = None,
    run: Runner = run_tool,
    environ: Mapping[str, str] = os.environ,
) -> int:
    try:
        args = build_parser().parse_args(list(argv))
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    setup = Setup(root, home or Path.home(), environ.get("BOSUN_APP_RELEASES", ""), run)
    try:
        message = dispatch(args, setup)
    except AppError as error:
        print(f"bosun app: {error}", file=sys.stderr)
        return 1
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
