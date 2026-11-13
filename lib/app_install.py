#!/usr/bin/env python3
"""`bosun app`: install, update and remove the menu bar app, bosun.app (docs/design/menu-bar.md).

It accepts only the publisher's notarized build. The app comes from a GitHub release of this repository (or
the mirror BOSUN_APP_RELEASES names), is unpacked in a folder of its own and is placed in ~/Applications only
once `codesign` has checked its signature against the publisher's Developer ID requirement (Team ID and bundle
id, from the certificates, never from what codesign prints), Gatekeeper says it is a Notarized Developer
ID and its bundle id is the publisher's. A refused or failed install leaves ~/Applications as it was.

Every external tool (curl, ditto, codesign, spctl, osascript, open) is run through one runner, `Runner`,
which a test replaces. What is decided without a tool (the publisher, the release address, the code
requirement, which archives are refused, `status`) is lib/app_release.py. The login job (Open at Login) is
launchd's and the shell's (`libexec/bosun-app`; lib/common.sh renders its plist like the others); here is only
whether its plist is there, for `status`.

    app_install.py install [--build]     download and place the release app/VERSION names; --build: the app
                                         that app/build.sh makes, without the team check (developers)
    app_install.py update                install app/VERSION when the installed version differs
    app_install.py status [--json] [--login-plist PATH]
    app_install.py verify-archive <zip>  run on a zip what an install runs on a download (the release workflow
                                         checks its final zip with it before it publishes): the archive's
                                         entries, ditto, one bosun.app, then `verify`; nothing is installed
    app_install.py quit                  ask the running app, by its bundle id, to quit (remove's first step)
    app_install.py delete                remove ~/Applications/bosun.app (remove's last step)

Exit status: 0 done, 1 refused or failed (one line saying why), 2 usage.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

import app_release
import install_paths
import update_check
import update_lookup
from app_release import BUNDLE, AppError, Publisher


ROOT = Path(__file__).resolve().parents[1]
TOOL_SECONDS = 900  # the most any one tool may take: the download is the slow one
# curl: no ~/.curlrc (-q, which must come first), https only, also after a redirect, a failed status an error
# (-f) whose number is printed (-w) for the message.
CURL = (
    "curl",
    "-q",
    "-fsSL",
    "--proto",
    "=https",
    "--proto-redir",
    "=https",
    "--connect-timeout",
    "30",
    "--max-time",
    # inside the 600 s `bosun update` gives a command (update_facts.COMMAND_SECONDS): curl ends it itself
    "540",
    "-w",
    "%{http_code}",
)
INTERRUPTED = 130  # the exit status of a command ended by Ctrl-C
HTTP_ERROR = 22  # curl -f: the server answered with an error, such as 404
NOT_FOUND = "404"
CODESIGN_UNSATISFIED = 3  # codesign --verify: the code does not meet the requirement given with -R
NO_SUCH_APP = "(-1728)"  # osascript: Launch Services knows no app of that bundle id, so it is not running
NOT_ALLOWED = "(-1743)"  # osascript: the user has not allowed bosun to control that app (Automation)
QUIT_POLLS = 40  # an app asked to quit is looked at this often, QUIT_POLL_SECONDS apart
QUIT_POLL_SECONDS = 0.5
BUILD_FAILURE_LINES = 20  # of the build's output that a failed build shows: the compiler's last words
# Hidden, in ~/Applications itself, so that a rename into place never crosses a volume; the pid of the install
# that made it is in the name, so that a later one can tell what a dead install left from a live one's.
SCRATCH_PREFIX = ".bosun-app-"
GATEKEEPER_REFUSALS = ("Gatekeeper's assessment", "macOS does not accept")  # what `verify` says of spctl
SPCTL_LINES = 10  # of what spctl said, which `verify-archive` shows when it refuses
SCRATCH_PID = re.compile(r"^\.bosun-app-(\d+)-")
NOTARIZED = "source=Notarized Developer ID"
ASK_AUTOMATION = (
    "macOS did not let bosun ask bosun.app to quit (Automation is not allowed): allow it in System Settings, "
    "Privacy & Security, Automation, or quit bosun.app by hand, then run this again"
)


class Interrupted(AppError):
    """The install was ended by SIGTERM or SIGHUP; what it had done is undone as for any failure."""


class Stuck(AppError):
    """The old app could not be put back: the folder that holds it is kept, and the message says where."""


@dataclass(frozen=True)
class Run:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def text(self) -> str:
        return f"{self.stdout}\n{self.stderr}"

    @property
    def last_line(self) -> str:
        """The last line the tool said, on either stream: what a refusal quotes."""
        lines = [line.strip() for line in self.text.splitlines() if line.strip()]
        return lines[-1] if lines else f"exit status {self.returncode}"

    def tail(self, count: int) -> list[str]:
        """The last <count> lines the tool said, on either stream (stderr last), without blank ones."""
        return [line.rstrip() for line in self.text.splitlines() if line.strip()][-count:]


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
        return self.home / app_release.APPLICATIONS

    @property
    def app(self) -> Path:
        return app_release.app_path(self.home)


def install_origin(root: Path) -> str:
    """Where the install came from: its origin remote, read as `bosun update` reads it (credentials
    taken off); nothing when it cannot be read."""
    repo = root.parent / install_paths.REPO if install_paths.is_version_folder(root) else root / ".git"
    try:
        return update_lookup.origin_url(repo)
    except update_lookup.LookupFailed:
        return ""


# ---- what the app is: its signature, notarization and identity ------------------------------------------


def _quoted(app: Path, done: Run) -> str:
    return done.last_line.removeprefix(f"{app}: ")


def _signature_problem(app: Path, publisher: Publisher, team_id: str, run: Runner) -> str | None:
    """One `codesign --verify --deep --strict -R` against the publisher's requirement: the signature is whole,
    and the certificates in it are the publisher's. Nothing is read from codesign's description of the app,
    which a signer can write into (an identifier may hold a newline and a line that looks like a Team ID)."""
    wanted = app_release.requirement(publisher.bundle_id, team_id)
    done = run(["codesign", "--verify", "--deep", "--strict", "-R", f"={wanted}", str(app)])
    if done.returncode == 0:
        return None
    if "not signed at all" in done.last_line:
        return "it is not signed"
    if done.returncode == CODESIGN_UNSATISFIED:
        who = f"team {team_id}, bundle id {publisher.bundle_id}"
        return f"it is not signed with the publisher's Developer ID ({who})"
    return f"its signature does not verify: {_quoted(app, done)}"


def _notarization_problem(app: Path, run: Runner) -> str | None:
    done = run(["spctl", "--assess", "--type", "execute", "-vv", str(app)])
    if done.returncode != 0:
        return f"macOS does not accept it (not notarized?): {_quoted(app, done)}"
    if NOTARIZED not in [line.strip() for line in done.text.splitlines()]:
        return "Gatekeeper's assessment did not say Notarized Developer ID"
    return None


def verify(app: Path, publisher: Publisher, run: Runner) -> str | None:
    """Why the app at <app> is refused, or None when it is the publisher's notarized build: `codesign
    --verify --deep --strict -R` against the publisher's Developer ID requirement passes, `spctl --assess
    --type execute -vv` exits 0 and says `source=Notarized Developer ID`, and its Info.plist's
    CFBundleIdentifier is the publisher's. It stops at the first refusal."""
    if not app.is_dir() or app.is_symlink():
        return f"{app.name} is not an app bundle"
    if not app_release.TEAM_ID.match(publisher.team_id):
        return "there is no Team ID to check it against"
    return (
        _signature_problem(app, publisher, publisher.team_id, run)
        or _notarization_problem(app, run)
        or app_release.not_ours(app, publisher.bundle_id)
    )


# ---- getting it ------------------------------------------------------------------------------------------


def download(setup: Setup, url: str, archive: Path, what: str) -> None:
    """<url> fetched to <archive>; a refusal says what the server answered."""
    done = setup.run([*CURL, "-o", str(archive), url])
    if done.returncode == 0:
        return
    status = done.stdout.strip()
    if done.returncode == HTTP_ERROR and status == NOT_FOUND:
        raise AppError(f"no release of {what}: {url} is not there (HTTP 404)")
    if done.returncode == HTTP_ERROR and status:
        raise AppError(f"could not download {url}: the server answered HTTP {status}")
    raise AppError(f"could not download {url}: {update_check.strip_credentials(done.last_line)}")


def one_line(error: BaseException) -> str:
    """What an exception says, in one line."""
    return " ".join(str(error).split()) or type(error).__name__


def check_archive(archive: Path) -> None:
    """The archive's entries, before anything is unpacked: refused for an absolute path, a `..` or a link
    (from the central directory), and unless every entry reads back as that directory lists it. Reading each
    one compares its local header's name with the directory's, which an unpacker may go by instead: a zip
    that names one path in the directory and another in the header is refused here, as is a name twice, an
    entry whose data does not inflate and an encrypted one (zipfile raises RuntimeError for it)."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            problem = app_release.entries_problem(entries)
            if problem:
                raise AppError(problem)
            damaged = bundle.testzip()
    except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError, zlib.error) as error:
        raise AppError(f"the download is not a zip archive: {one_line(error)}") from error
    except RuntimeError as error:  # an encrypted entry: password required
        raise AppError(f"an entry of the download cannot be read: {one_line(error)}") from error
    if damaged is not None:
        raise AppError(f"the download is not a zip archive: {damaged} does not read back")
    names = [entry.filename for entry in entries]
    twice = sorted({name for name in names if names.count(name) > 1})
    if twice:
        raise AppError(f"the archive names an entry twice: {twice[0]}")


def unpack_archive(run: Runner, archive: Path, scratch: Path) -> Path:
    """The bosun.app of <archive>, unpacked with `ditto -x -k` in <scratch> after its entries are checked,
    and what was unpacked checked again before anything else looks at it: exactly one bosun.app, and no
    symbolic link anywhere in it (the entries had none). What an install does with a download."""
    check_archive(archive)
    unpacked = scratch / "unpacked"
    unpacked.mkdir()
    done = run(["ditto", "-x", "-k", str(archive), str(unpacked)])
    if done.returncode != 0:
        raise AppError(f"the download is not a zip archive: {done.last_line}")
    problem = app_release.archive_problem(unpacked) or app_release.tree_problem(unpacked)
    if problem:
        raise AppError(problem)
    return unpacked / BUNDLE


def fetch_app(setup: Setup, slug: str, version: str, scratch: Path) -> Path:
    """The release's app, downloaded and unpacked in <scratch>, and checked to be one bosun.app."""
    url = app_release.release_asset_url(slug, version)
    archive = scratch / "bosun-app.zip"
    download(setup, url, archive, f"{BUNDLE} {version} at {slug}")
    return unpack_archive(setup.run, archive, scratch)


def build_app(setup: Setup, scratch: Path) -> Path:
    """The app app/build.sh makes in a folder of <scratch>, which it is given: `--identity -` (ad hoc: this is
    a build for developing the app) and `--out` that folder."""
    built = scratch / "built"
    built.mkdir()
    print(
        "building bosun.app with app/build.sh (the first build takes minutes)...", file=sys.stderr, flush=True
    )
    done = setup.run([str(setup.root / "app" / "build.sh"), "--identity", "-", "--out", str(built)])
    if done.returncode != 0:
        shown = "\n".join(f"  {line}" for line in done.tail(BUILD_FAILURE_LINES))
        if not shown:
            raise AppError(
                f"app/build.sh exited {done.returncode} and said nothing: run it again (the first run of a "
                "fresh copy can fail before the script starts; see docs/design/menu-bar.md, the Mac VM "
                "run of the app)"
            )
        raise AppError(f"app/build.sh failed, the last lines of its output:\n{shown}")
    if not (built / BUNDLE).is_dir():
        raise AppError(f"app/build.sh did not leave {BUNDLE} in the folder it was given")
    return built / BUNDLE


# ---- the running app -------------------------------------------------------------------------------------


def is_running(run: Runner, bundle_id: str) -> bool | None:
    """Whether the app is running, asked by its bundle id (never found by the name of a process): True,
    False, or None when osascript could not say. An app Launch Services does not know of is not running."""
    done = run(["osascript", "-e", f'application id "{bundle_id}" is running'])
    answer = done.stdout.strip()
    if done.returncode == 0 and answer in ("true", "false"):
        return answer == "true"
    return False if NO_SUCH_APP in done.text else None


def _running(setup: Setup, bundle_id: str) -> bool:
    """is_running, where an answer it could not give refuses: an app that may be running is not replaced."""
    running = is_running(setup.run, bundle_id)
    if running is None:
        raise AppError(
            f"could not tell whether {BUNDLE} is running, so it was not touched: it must be quit first"
        )
    return running


def quit_app(setup: Setup, bundle_id: str) -> bool:
    """Ask the running app to quit, by its bundle id, and wait until it has. Whether it was running."""
    if not _running(setup, bundle_id):
        return False
    done = setup.run(["osascript", "-e", f'tell application id "{bundle_id}" to quit'])
    if done.returncode != 0:
        if NOT_ALLOWED in done.text:
            raise AppError(ASK_AUTOMATION)
        raise AppError(f"could not ask {BUNDLE} to quit: {done.last_line}")
    for _ in range(QUIT_POLLS):
        setup.sleep(QUIT_POLL_SECONDS)
        if not _running(setup, bundle_id):
            return True
    raise AppError(f"{BUNDLE} is still running: quit it, then run this again")


# ---- placing it ------------------------------------------------------------------------------------------


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _restore(aside: Path, dest: Path) -> None:
    """The old app, at <aside>, put back at <dest>; Stuck, naming where it is, when that fails."""
    try:
        os.rename(aside, dest)
    except OSError as error:
        raise Stuck(f"could not put the old {BUNDLE} back at {dest}: {error}; it is at {aside}") from error


def sweep_stale(setup: Setup) -> None:
    """What an install that was killed (SIGKILL, a crash, a power cut) left in ~/Applications: each
    `.bosun-app-*` folder whose install is gone. One that holds the old app, with none at
    ~/Applications/bosun.app (killed between the two renames), gives it back first."""
    for folder in sorted(setup.apps.glob(f"{SCRATCH_PREFIX}*")):
        found = SCRATCH_PID.match(folder.name)
        if not folder.is_dir() or folder.is_symlink() or (found and _alive(int(found[1]))):
            continue
        old = folder / "previous" / BUNDLE
        if old.is_dir() and not os.path.lexists(setup.app):
            _restore(old, setup.app)
        shutil.rmtree(folder, ignore_errors=True)


def ensure_ours(app: Path, bundle_id: str, doing: str) -> None:
    """Nothing is done to a ~/Applications/bosun.app that cannot be said to be the publisher's."""
    if not os.path.lexists(app):
        return
    why = app_release.not_ours(app, bundle_id)
    if why:
        raise AppError(
            f"~/Applications/{BUNDLE} is not bosun's app ({why}), so it is left as it is: {doing} it yourself"
        )


@contextlib.contextmanager
def raising_on_signals() -> Iterator[None]:
    """SIGTERM and SIGHUP end the install as an exception while this holds, so what it has done is undone;
    the handlers there before are put back after."""

    def end(signum: int, _frame: FrameType | None) -> None:
        raise Interrupted(f"ended by {signal.Signals(signum).name}")

    # A signal that was ignored (a hangup under nohup) stays ignored: it was not asked to end anything.
    wanted = [n for n in (signal.SIGTERM, signal.SIGHUP) if signal.getsignal(n) != signal.SIG_IGN]
    before = {number: signal.signal(number, end) for number in wanted}
    try:
        yield
    finally:
        for number, handler in before.items():
            signal.signal(number, handler)


def put_in_place(staged: Path, dest: Path, scratch: Path) -> None:
    """<staged> renamed to <dest>; the app there before is renamed into <scratch> first (which goes with it
    when the install ends), and put back if the rename in fails or the install is ended in between. An end
    this does not catch (Ctrl-C, a second signal while it puts the old app back) is place_app's clean_up."""
    aside = scratch / "previous" / BUNDLE
    try:
        if os.path.lexists(dest):
            aside.parent.mkdir()
            os.rename(dest, aside)
        os.rename(staged, dest)
    except (OSError, Interrupted) as error:
        if os.path.lexists(aside) and not os.path.lexists(dest):  # by what is there, not by what was done
            _restore(aside, dest)
        if isinstance(error, Interrupted):
            raise
        raise AppError(f"could not put {BUNDLE} in {dest.parent}: {error}") from error


def clean_up(scratch: Path, app: Path) -> None:
    """<scratch> removed, but never the old app in it: when it is still at previous/bosun.app and none is at
    <app> (the install ended between the two renames, in a way put_in_place did not catch), it is put back
    first, and when that fails the folder stays and Stuck says where the old app is."""
    old = scratch / "previous" / BUNDLE
    if old.is_dir() and not os.path.lexists(app):
        _restore(old, app)
    shutil.rmtree(scratch, ignore_errors=True)


def what_is_there(app: Path) -> str:
    version = app_release.bundle_version(app)
    if version:
        return f"{BUNDLE} {version} is in ~/Applications"
    return (
        f"{BUNDLE} is in ~/Applications, and cannot be read"
        if os.path.lexists(app)
        else f"no {BUNDLE} is in ~/Applications"
    )


def remove_if_empty(folder: Path) -> None:
    with contextlib.suppress(OSError):
        folder.rmdir()


def make_scratch(apps: Path) -> tuple[Path, bool]:
    """A folder of its own in ~/Applications (made if it is not there), and whether this made ~/Applications;
    which it removes again, if it made it, when it cannot make the folder."""
    created = not apps.exists()
    apps.mkdir(parents=True, exist_ok=True)
    try:
        return Path(tempfile.mkdtemp(prefix=f"{SCRATCH_PREFIX}{os.getpid()}-", dir=apps)), created
    except OSError:
        if created:
            remove_if_empty(apps)
        raise


def stage_and_place(
    setup: Setup, publisher: Publisher, produce: Callable[[Path], Path], scratch: Path, *, development: bool
) -> bool:
    """The app <produce> makes in <scratch>, verified and put in place, the running app having been asked
    to quit once it is verified. Whether it was running. Only <development> (a build of the app, ad hoc
    signed) is not verified: never inferred from the publisher's team_id, and `verify` refuses a missing or
    invalid one."""
    staged = produce(scratch)
    if not development:
        reason = verify(staged, publisher, setup.run)
        if reason:
            raise AppError(f"refused {BUNDLE}: {reason}")
    reopen = quit_app(setup, publisher.bundle_id)
    try:
        put_in_place(staged, setup.app, scratch)
    except Stuck:
        raise
    except AppError:
        if reopen:  # the old one is back: it was running
            setup.run(["open", str(setup.app)])
        raise
    return reopen


def place_app(
    setup: Setup, publisher: Publisher, produce: Callable[[Path], Path], *, development: bool
) -> str:
    """The app <produce> makes in a scratch folder, verified against <publisher> (unless <development>) and
    placed. Leftovers of an install that was killed are swept first. The scratch folder is removed whatever
    happens, but never with the old app in it while none is in place: it is given back first, and when that
    cannot be done the folder is kept and the message says where the old app is. A ~/Applications this made
    is removed too, unless the install worked."""
    sweep_stale(setup)
    ensure_ours(setup.app, publisher.bundle_id, "replace")
    scratch, created = make_scratch(setup.apps)
    reopen = placed = keep = False
    ended: Interrupted | None = None
    try:
        with raising_on_signals():
            reopen = stage_and_place(setup, publisher, produce, scratch, development=development)
        placed = True
    except Stuck:
        keep = True
        raise
    except Interrupted as error:
        ended = error
    finally:
        if not keep:
            clean_up(scratch, setup.app)
        if created and not placed:
            remove_if_empty(setup.apps)
    if ended is not None:
        raise Interrupted(f"{ended}; {what_is_there(setup.app)}") from None
    shown = " ".join(filter(None, [BUNDLE, app_release.bundle_version(setup.app)]))
    if reopen:
        opened = setup.run(["open", str(setup.app)])
        if opened.returncode != 0:
            raise AppError(f"{shown} is installed, but could not be opened: {opened.last_line}")
    return f"{shown} installed in ~/Applications"


# ---- the commands ---------------------------------------------------------------------------------------


def install(setup: Setup, *, build: bool = False) -> str:
    """Place the release app/VERSION names (checked against the publisher), or with <build> the app
    app/build.sh makes (not checked: it is ad hoc signed, for developing the app, and says so)."""
    publisher = app_release.read_publisher(setup.root)
    if build:
        if not (setup.root / "app" / "build.sh").is_file():
            raise AppError(app_release.NO_BUILD)
        placed = place_app(setup, publisher, lambda scratch: build_app(setup, scratch), development=True)
        return f"{placed} (a development build: ad hoc signed, not the publisher's notarized app)"
    app_release.require_team(publisher)
    version = app_release.wanted_version(setup.root)
    slug = app_release.releases_slug(setup.releases, publisher, lambda: install_origin(setup.root))
    return place_app(
        setup, publisher, lambda scratch: fetch_app(setup, slug, version, scratch), development=False
    )


def verify_archive(setup: Setup, archive: Path) -> str:
    """<archive> (a zip) put through what an install puts a download through: its entries, `ditto -x -k` into
    a folder that is removed after, exactly one bosun.app, then `verify` against the publisher (the code
    requirement, Gatekeeper's Notarized Developer ID, the bundle id). Nothing is installed. Refused with the
    one line an install would refuse it with; for Gatekeeper's refusals spctl's own words go to stderr too."""
    publisher = app_release.read_publisher(setup.root)
    app_release.require_team(publisher)
    assessments: list[Run] = []

    def run(argv: Sequence[str]) -> Run:
        done = setup.run(argv)
        if argv[0] == "spctl":
            assessments.append(done)
        return done

    with tempfile.TemporaryDirectory(prefix="bosun-app-verify-") as scratch:
        staged = unpack_archive(run, archive, Path(scratch))
        reason = verify(staged, publisher, run)
    if reason:
        if assessments and reason.startswith(GATEKEEPER_REFUSALS):
            for line in assessments[-1].tail(SPCTL_LINES):
                print(f"spctl said: {line}", file=sys.stderr)
        raise AppError(f"refused {BUNDLE}: {reason}")
    return f"{archive.name} passes the checks an install makes"


def update(setup: Setup) -> str:
    """Install app/VERSION when it differs from the installed app; say so, and download nothing, when it is
    the same or there is no app (bringing one is `install`)."""
    wanted = app_release.wanted_version(setup.root)
    installed = app_release.bundle_version(setup.app)
    if installed is None:  # `bosun update` runs this on every install: no Team ID is needed to say so
        return f"{BUNDLE} is not installed: run bosun app install"
    if installed == wanted:
        return f"{BUNDLE} {wanted} is up to date"
    return install(setup)  # which refuses, before anything else, a Team ID that is not filled in


def delete_app(setup: Setup) -> str:
    """Remove ~/Applications/bosun.app, if it is the publisher's; leftovers of an install that was killed are
    swept first (which may give the old app back, to be removed)."""
    bundle_id = app_release.read_publisher(setup.root).bundle_id
    sweep_stale(setup)
    if not os.path.lexists(setup.app):
        return f"{BUNDLE} is not installed in ~/Applications"
    ensure_ours(setup.app, bundle_id, "remove")
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
    commands.add_parser("verify-archive", allow_abbrev=False).add_argument("archive", type=Path)
    commands.add_parser("quit", allow_abbrev=False)
    commands.add_parser("delete", allow_abbrev=False)
    return parser


def dispatch(args: argparse.Namespace, setup: Setup) -> str:
    if args.command == "install":
        return install(setup, build=args.build)
    if args.command == "update":
        return update(setup)
    if args.command == "status":
        state = app_release.status(setup.root, setup.app, args.login_plist)
        return json.dumps(state) if args.json else app_release.describe(state)
    if args.command == "verify-archive":
        return verify_archive(setup, args.archive)
    if args.command == "quit":
        bundle_id = app_release.read_publisher(setup.root).bundle_id
        return f"asked {BUNDLE} to quit" if quit_app(setup, bundle_id) else ""
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
    except (AppError, OSError) as error:  # one line, never a traceback
        print(f"bosun app: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # what it had done is undone (place_app's clean_up) before it gets here
        print("bosun app: interrupted", file=sys.stderr)
        return INTERRUPTED
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
