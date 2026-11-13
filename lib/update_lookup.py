"""What `bosun update` looks up before it changes anything: the newest release (or the branch head),
whether the install already is past it, and the answer to `bosun update --check`
(docs/design/menu-bar.md, "Releases").

The git side of lib/update_check.py. It only reads: the remote's tags with `git ls-remote`, and into the
versions folder's bare repository (REPO) the objects of the release looked at (`git fetch` of its tag,
or of the branch on the `main` channel). It never makes a version folder, takes update.lock or holds
anything, so it may run while an update does. Run by libexec/bosun-update, in the version in use:

    update_lookup.py check [--json]   the answer, as one line or as JSON; always exit 0 (a problem is the
                                      answer's "error")
    update_lookup.py skip <version>   record a release not to be offered (update-skip.json in the
                                      state folder)
    update_lookup.py target           the commit `bosun update` installs on the `releases` channel: a line
                                      "commit <sha> <tag>", or "ahead <tag>" when the install descends from
                                      the newest release (nothing to update to); exit 1 with a line on stderr

The channel is BOSUN_UPDATE_CHANNEL, `releases` or `main` (the update's own `main` path is in the wrapper).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import install_paths
import update_check
import versions


ROOT = Path(__file__).resolve().parents[1]  # the version running: the one installed
SKIP_FILE = "update-skip.json"
GIT_SECONDS = 120  # the most any one git call over the network may take
LOCAL_SECONDS = 30
USAGE = 2  # the exit status of a command line that makes no sense
# A transfer slower than 1 KB/s for 30 seconds is given up on, as the update's own fetch does.
SLOW = ("-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30")
# Where the release looked at is fetched to: a remote ref, one that each fetch overwrites, never
# refs/tags. Uninstall (lib/checkout_work.py) counts what a branch or tag of the versions' repository
# holds that no remote ref does as work of yours, and would keep the versions folder for a tag.
RELEASE_REF = "refs/remotes/origin/release"


class LookupFailed(Exception):
    """Something the lookup could not do: what it says is shown as it is."""


def _git(repo: Path, *args: str, network: str | None = None, why: bool = False) -> str:
    """<args> in <repo>; its output. A failure is LookupFailed: <network>, when the call went over the
    network (with <why>, and git's last line of complaint, credentials taken off), else git's own words."""
    try:
        done = versions.git(
            *(SLOW if network else ()),
            "--git-dir",
            str(repo),
            *args,
            timeout=GIT_SECONDS if network else LOCAL_SECONDS,
        )
    except subprocess.TimeoutExpired:
        done = None
    if done is not None and done.returncode == 0:
        return done.stdout
    if network:
        raise LookupFailed(f"{network}: {_reason(done)}" if why and done is not None else network)
    raise LookupFailed(f"git {args[0]} failed: {done.stderr.strip() if done else 'timed out'}")


def _reason(done: subprocess.CompletedProcess[str]) -> str:
    """The last line git said, with no credentials in it."""
    lines = [line.strip() for line in done.stderr.splitlines() if line.strip()]
    return update_check.strip_credentials(lines[-1]) if lines else f"git exited {done.returncode}"


def _succeeds(repo: Path, *args: str) -> bool:
    try:
        return versions.git("--git-dir", str(repo), *args, timeout=LOCAL_SECONDS).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _repo() -> Path:
    if not install_paths.is_version_folder(ROOT):
        raise LookupFailed(
            f"bosun update moves an install made by get.sh, with its versions side by side; {ROOT} is a "
            "checkout: update it with git"
        )
    return ROOT.parent / install_paths.REPO


def origin_url(repo: Path) -> str:
    """The URL of the origin remote of the git directory <repo>, without credentials: where the install
    came from. `bosun app` looks for its releases there when nothing names them."""
    return update_check.url_without_credentials(_git(repo, "config", "remote.origin.url").strip())


def _remote_tags(repo: Path, *, why: bool = False) -> list[tuple[str, str]]:
    out = _git(
        repo, "ls-remote", "--tags", "origin", "v*", network=f"could not reach {origin_url(repo)}", why=why
    )
    return update_check.parse_ls_remote(out)


def _fetch(repo: Path, refspec: str, what: str, *, why: bool = False) -> None:
    _git(
        repo,
        "fetch",
        "--quiet",
        "--no-tags",
        "origin",
        refspec,
        network=f"could not fetch {what} from {origin_url(repo)}",
        why=why,
    )


def _fetch_tag(repo: Path, tag: str, *, why: bool = False) -> str:
    """The release <tag>'s objects, at RELEASE_REF; the commit it points at. That is peeled here, locally:
    the origin's `git ls-remote` may print an annotated tag's own object and no `^{}` line (a git bundle,
    or a mirror made from one), and a tag object is not a commit to check out or to compare."""
    _fetch(repo, f"+refs/tags/{tag}:{RELEASE_REF}", tag, why=why)
    return _git(repo, "rev-parse", "--verify", f"{RELEASE_REF}^{{commit}}").strip()


def _branch(repo: Path) -> str:
    done = versions.git("--git-dir", str(repo), "config", "bosun.branch", timeout=LOCAL_SECONDS)
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else "main"


def _bullets(subjects: str) -> str:
    """One `- <subject>` line per subject, as the release notes read, with no newline at the end (a tag's
    message is read the same way)."""
    return "\n".join(f"- {line.strip()}" for line in subjects.splitlines() if line.strip())


def _descends(repo: Path, older: str, newer: str) -> bool:
    """Whether <newer> is <older> or comes after it (<older> is among its ancestors)."""
    return _succeeds(repo, "merge-base", "--is-ancestor", older, newer)


def _installed_commit() -> str | None:
    try:
        return versions.commit_of(ROOT)
    except RuntimeError:
        return None


def skipped_version(state_dir: Path | None) -> str | None:
    """The release recorded as skipped; None when none is, or the file cannot be read."""
    if state_dir is None:
        return None
    try:
        version = json.loads((state_dir / SKIP_FILE).read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def write_skip(state_dir: Path, version: str, now: datetime | None = None) -> None:
    """Record <version> as skipped, replacing the file whole."""
    at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    state_dir.mkdir(parents=True, exist_ok=True)
    temporary = state_dir / f".{SKIP_FILE}.tmp"
    temporary.write_text(json.dumps({"version": version, "at": at}) + "\n")
    os.replace(temporary, state_dir / SKIP_FILE)


def _release_answer(repo: Path, installed: str, skipped: str | None) -> dict[str, Any]:
    tags = _remote_tags(repo)
    version = update_check.version_at(installed, tags)
    newest = update_check.newest_release(tags)
    if newest is None:
        return update_check.check(installed, version, None, skipped, False)
    tag = newest[0]
    commit = _fetch_tag(repo, tag)
    newest = (tag, commit)
    if commit == installed:
        return update_check.check(installed, version or tag, newest, skipped, False)
    ahead = _descends(repo, commit, installed)
    notes = "" if ahead else _git(repo, "for-each-ref", "--format=%(contents)", RELEASE_REF).strip()
    return update_check.check(installed, version, newest, skipped, ahead, notes=notes)


def _main_answer(repo: Path, installed: str, skipped: str | None) -> dict[str, Any]:
    branch = _branch(repo)
    version = update_check.version_at(installed, _remote_tags(repo))
    _fetch(repo, f"+refs/heads/{branch}:refs/remotes/origin/{branch}", branch)
    head = _git(repo, "rev-parse", "--verify", f"refs/remotes/origin/{branch}^{{commit}}").strip()
    newest = (head[: update_check.SHORT], head)
    ahead = head != installed and _descends(repo, head, installed)
    notes = ""
    if head != installed and not ahead:
        notes = _bullets(_git(repo, "log", "--format=%s", f"{installed}..{head}"))
    return update_check.check(installed, version, newest, skipped, ahead, channel="main", notes=notes)


def _channel(channel: str) -> str:
    if channel not in update_check.CHANNELS:
        raise LookupFailed(f"BOSUN_UPDATE_CHANNEL is {channel!r}: it is releases or main")
    return channel


def answer(channel: str, state_dir: Path | None) -> dict[str, Any]:
    """The check's answer (update_check.check); problems are its "error", never an exception."""
    installed = _installed_commit()
    try:
        _channel(channel)
        if installed is None:
            raise LookupFailed(f"cannot read the commit of {ROOT}")
        repo = _repo()
        lookup = _release_answer if channel == "releases" else _main_answer
        return lookup(repo, installed, skipped_version(state_dir))
    except LookupFailed as exc:
        return update_check.failed(channel, installed, str(exc))


def target() -> str:
    """`commit <sha> <tag>`: the newest release, fetched, for the update to install; `ahead <tag>` when
    the install is past it. LookupFailed when there is no release or it cannot be had."""
    repo = _repo()
    newest = update_check.newest_release(_remote_tags(repo, why=True))
    if newest is None:
        raise LookupFailed(
            f"no release at {origin_url(repo)} yet "
            "(BOSUN_UPDATE_CHANNEL=main follows the head of the branch instead)"
        )
    tag = newest[0]
    commit = _fetch_tag(repo, tag, why=True)
    installed = _installed_commit()
    if installed is not None and installed != commit and _descends(repo, commit, installed):
        return f"ahead {tag}"
    return f"commit {commit} {tag}"


def _state_dir() -> Path | None:
    value = os.environ.get("BOSUN_STATE_DIR")
    return Path(value) if value else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun update", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check").add_argument("--json", action="store_true")
    commands.add_parser("skip").add_argument("version")
    commands.add_parser("target")
    return parser


def _skip(version: str) -> int:
    state_dir = _state_dir()
    if update_check.release_key(version) is None:
        print(
            f"bosun update: --skip takes a release version, such as v2026.11.12 (not {version!r})",
            file=sys.stderr,
        )
        return USAGE
    if state_dir is None:
        raise LookupFailed("BOSUN_STATE_DIR is not set: run it as bosun update")
    write_skip(state_dir, version)
    print(
        f"bosun will not offer {version} again: `bosun update --check` reports it as skipped, "
        "and a newer release as usual"
    )
    return 0


def main(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv))
    channel = os.environ.get("BOSUN_UPDATE_CHANNEL") or "releases"
    try:
        if args.command == "check":
            found = answer(channel, _state_dir())
            print(json.dumps(found, indent=2) if args.json else update_check.describe(found))
            return 0
        if args.command == "skip":
            return _skip(args.version)
        _channel(channel)
        if channel != "releases":
            raise LookupFailed("the main channel's target is the wrapper's")
        print(target())
    except LookupFailed as exc:
        print(f"bosun update: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
