"""What deleting a checkout of bosun, or a version of it, would lose: the one check `bosun uninstall` and
`bosun update` go by (docs/design/uninstall.md, "Your work is not bosun's to delete"). Uninstall deletes a
checkout or versions folder only when it finds nothing; the first update refuses to set aside a checkout
that holds anything; an update's prune keeps a checkout from before versions, or an older version, that
does.

It only reads, through git, which never looks above the folder it checks nor follows a GIT_* variable of
the caller's to another repository. Imported by lib/uninstall_detect.py, lib/uninstall_plan.py (Checkout,
why_kept), lib/versions.py and lib/update.py, so it imports nothing of bosun but install_paths.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

import install_paths


GIT_CONFIG_MARK = "bosun.managed"  # what get.sh sets on what it made
CACHES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", ".DS_Store"})
UNCHECKED = "a git repository that could not be checked"
NOT_THE_INSTALLERS = "it was not made by the installer, so it is yours"
GIT_SECONDS = 15  # the most one git command may take


@dataclass(frozen=True)
class Checkout:
    root: str
    managed: bool  # get.sh made it, and marked it so
    work: str = ""  # what deleting it would lose, or that it could not be checked


def git(root: Path, *args: str) -> str | None:
    """git's stdout in <root> if it succeeded, else None: git in <root> and in no folder above it, with no
    GIT_* variable of the caller's (a git hook, another repository's tooling) sending it elsewhere."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(root.parent)
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            errors="replace",
            env=env,
            timeout=GIT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def ignored_work(status: str) -> list[str]:
    """What git ignores in the checkout that is not a cache running bosun makes: a .env, a key, a state
    folder someone pointed inside it."""
    ignored = (line[3:] for line in status.splitlines() if line.startswith("!! "))
    return [
        path
        for path in ignored
        if not path.endswith(".pyc") and not CACHES.intersection(Path(path.rstrip("/")).parts)
    ]


def tree_work(root: Path) -> dict[str, bool] | None:
    """What git shows in the files of the worktree at <root> that is yours: changes, and what it ignores but
    the caches running bosun makes; None when git cannot answer."""
    status = git(root, "status", "--porcelain", "--ignored", "--untracked-files=all")
    if status is None:
        return None
    ignored = ignored_work(status)
    return {
        "uncommitted changes": any(not line.startswith("!! ") for line in status.splitlines()),
        f"files git ignores ({', '.join(ignored[:3])})": bool(ignored),
    }


def worktree_paths(listing: str) -> list[str]:
    """The worktrees `git worktree list --porcelain` lists, as it prints them."""
    return [line.removeprefix("worktree ") for line in listing.splitlines() if line.startswith("worktree ")]


def repo_work(
    root: Path, ours: Collection[Path], refs: tuple[str, ...] = ("--all",)
) -> dict[str, bool] | None:
    """What the repository at <root> holds that is nowhere else: a stash, a commit of <refs> (every ref, and
    every worktree's HEAD) on no remote, a worktree that is not one of <ours>; None when git cannot answer."""
    stashes = git(root, "for-each-ref", "--count=1", "refs/stash")
    unpushed = git(root, "log", *refs, "--not", "--remotes", "--oneline", "-1")
    worktrees = git(root, "worktree", "list", "--porcelain")
    if stashes is None or unpushed is None or worktrees is None:
        return None
    mine = {os.path.realpath(path) for path in ours}
    return {
        "stashed changes": bool(stashes.strip()),
        "commits not pushed": bool(unpushed.strip()),
        "other worktrees": any(os.path.realpath(path) not in mine for path in worktree_paths(worktrees)),
    }


def first_work(*found: dict[str, bool] | None) -> str:
    """The first thing of yours <found> shows, "" if none; git that could not answer counts as work."""
    checked = [each for each in found if each is not None]
    if len(checked) < len(found):
        return UNCHECKED
    return next((what for each in checked for what, there in each.items() if there), "")


def checkout_work(root: Path) -> str:
    """What deleting the checkout would lose, or "" if nothing. git that cannot answer counts as work."""
    return first_work(tree_work(root), repo_work(root, (root,)))


def checkout(root: Path) -> Checkout:
    managed = (git(root, "config", "--get", GIT_CONFIG_MARK) or "").strip() == "true"
    return Checkout(str(root), managed, checkout_work(root) if managed else "")


def why_kept(piece: Checkout) -> str:
    """Why deleting <piece> is not bosun's to do, or "" when it may go: the installer did not make it, or
    something of yours is in it."""
    if not piece.managed:
        return NOT_THE_INSTALLERS
    return f"it has {piece.work}; delete it once you have what you need" if piece.work else ""


def kept(root: Path) -> str:
    """Why the checkout at <root> is not bosun's to delete, or "": as bosun uninstall decides for one."""
    return why_kept(checkout(root))


# ---- versions side by side ------------------------------------------------------------------------------


def _head_work(folder: Path) -> dict[str, bool] | None:
    """A commit made in the version in <folder>: its HEAD moved off the commit its folder is named for, to
    one on no remote. The release bosun checked out there is never yours, even once a rewrite upstream
    took it off every remote."""
    head = git(folder, "rev-parse", "HEAD")
    if head is None:
        return None
    if head.strip().startswith(folder.name):
        return {}
    unpushed = git(folder, "log", "HEAD", "--not", "--remotes", "--oneline", "-1")
    return None if unpushed is None else {"commits not pushed": bool(unpushed.strip())}


def version_work(folder: Path) -> str:
    """What deleting the version in <folder> would lose of its own: its files changed, a commit made in it.
    The repository it shares with the others is versions_work's."""
    return first_work(tree_work(folder), _head_work(folder))


def version_kept(folder: Path) -> str:
    """Why an update's prune keeps the older version in <folder>, or "": something of yours in it. Whether
    the installer made it is its repository's to say, which only uninstall asks."""
    return why_kept(Checkout(str(folder), True, version_work(folder)))


def version_folders(folder: Path) -> list[Path]:
    """The versions in the versions folder <folder>: the worktrees git lists of its repository there."""
    repo = folder / install_paths.REPO
    inside = os.path.realpath(folder)
    listed = (Path(path) for path in worktree_paths(git(repo, "worktree", "list", "--porcelain") or ""))
    names = {path.name for path in listed if os.path.dirname(os.path.realpath(path)) == inside}
    return sorted(folder / name for name in names - {install_paths.REPO} if (folder / name).is_dir())


def _entries(folder: Path) -> list[str]:
    try:
        return sorted(entry.name for entry in folder.iterdir())
    except OSError:
        return []


def versions_work(folder: Path, found: Collection[Path], others: Collection[str] = ()) -> str:
    """What deleting the versions folder <folder> would lose beyond its versions' files (<found>, each
    checked on its own by version_work): a stash in its repository, a branch or tag of yours on no remote
    (the branch the repository follows is bosun's, and may be behind a rewrite upstream), a worktree
    outside the folder, and anything in the folder that is neither a version nor one of <others> (the
    checkouts from before versions, checked on their own)."""
    repo = folder / install_paths.REPO
    branch = (git(repo, "config", "--get", "bosun.branch") or "main").strip()
    refs = (f"--exclude={branch}", "--branches", "--tags")
    work = first_work(repo_work(repo, (repo, *found), refs))
    known = {install_paths.REPO, ".DS_Store", *(path.name for path in found), *others}
    unknown = [name for name in _entries(folder) if name not in known]
    return work or (f"what bosun does not know ({', '.join(unknown[:3])})" if unknown else "")
