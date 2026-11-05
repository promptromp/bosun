"""Version folders: each installed version a folder, the install path a link to the current one
(docs/design/update.md, "Versions side by side, switched in one step").

A version is a git worktree of the bare repository `<versions>/bosun.git`, in a folder named by
the first 12 characters of its commit. A checkout from before versions existed is kept as
`legacy-<12>` after the first update swaps it for the link. The install path is never missing:
`point` renames a new link over it, `swap_in_link` exchanges link and checkout in one call.
"""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Collection
from pathlib import Path

import journal


REPO = "bosun.git"
LEGACY_PREFIX = "legacy-"

# Flags that exchange two paths atomically: macOS RENAME_SWAP, Linux RENAME_EXCHANGE.
_RENAME_SWAP = 0x00000002
_RENAME_EXCHANGE = 2
_AT_FDCWD = -100


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """git with no inherited GIT_* variables and none of the user's hooks."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def commit_of(root: Path) -> str:
    """The commit checked out in `root`."""
    done = _git("-C", str(root), "rev-parse", "HEAD")
    if done.returncode != 0:
        raise RuntimeError(f"cannot read the commit of {root}: {done.stderr.strip()}")
    return done.stdout.strip()


def folder_for(versions: Path, commit: str) -> Path:
    return versions / commit[:12]


def current(install: Path) -> Path | None:
    """The folder the link at `install` leads to; None when it is not a link."""
    if not install.is_symlink():
        return None
    target = Path(os.readlink(install))
    return target if target.is_absolute() else install.parent / target


def _temporary_link(install: Path, target: Path) -> Path:
    link = install.with_name(f".{install.name}.link-{uuid.uuid4().hex[:8]}")
    os.symlink(target, link)
    return link


def point(install: Path, target: Path) -> None:
    """Make `install` a link to `target`, with `install` present at every instant."""
    link = _temporary_link(install, target)
    try:
        os.replace(link, install)
    except OSError:
        link.unlink(missing_ok=True)
        raise
    journal.flush_folder(install.parent)


def _swap_macos(libc: ctypes.CDLL, first: bytes, second: bytes) -> int:
    return int(libc.renamex_np(first, second, _RENAME_SWAP))


def _swap_linux(libc: ctypes.CDLL, first: bytes, second: bytes) -> int:
    return int(libc.renameat2(_AT_FDCWD, first, _AT_FDCWD, second, _RENAME_EXCHANGE))


_SWAPS: dict[str, Callable[[ctypes.CDLL, bytes, bytes], int]] = {
    "darwin": _swap_macos,
    "linux": _swap_linux,
}


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def _swap(first: Path, second: Path) -> None:
    """Exchange two paths in one call; raises OSError when it cannot."""
    swap = _SWAPS.get(sys.platform)
    if swap is None:
        raise OSError(errno.ENOSYS, f"no atomic swap of two paths on {sys.platform}")
    libc = _libc()
    try:
        failed = swap(libc, os.fsencode(first), os.fsencode(second)) != 0
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, f"this libc has no atomic swap of two paths ({exc})") from exc
    if failed:
        code = ctypes.get_errno()
        raise OSError(code, f"cannot swap {first} and {second}: {os.strerror(code)}")


def swap_in_link(install: Path, target: Path) -> Path:
    """Replace the checkout at `install` with a link to `target` in one step, and keep the checkout
    as `legacy-<12>` beside `target`. Returns where the checkout went. Refuses, with nothing
    changed, when that folder exists; if the checkout cannot be moved there after the swap, the
    swap is undone."""
    commit = commit_of(install)
    legacy = target.parent / f"{LEGACY_PREFIX}{commit[:12]}"
    if os.path.lexists(legacy):
        raise FileExistsError(errno.EEXIST, f"{legacy} is already there; not converting {install}")
    link = _temporary_link(install, target)
    try:
        _swap(link, install)
    except OSError:
        link.unlink(missing_ok=True)
        raise
    try:
        os.rename(link, legacy)
    except OSError as exc:
        _swap(link, install)
        link.unlink(missing_ok=True)
        raise OSError(
            exc.errno, f"could not keep the checkout as {legacy}; {install} is the checkout again"
        ) from exc
    journal.flush_folder(install.parent)
    journal.flush_folder(legacy.parent)
    return legacy


def _in_use(folder: Path) -> bool | None:
    """Whether a running process has `folder` open; None when lsof cannot say."""
    try:
        done = subprocess.run(["lsof", "-t", "+D", str(folder)], capture_output=True, text=True, check=False)
    except OSError:
        return None
    if done.returncode not in (0, 1):
        return None
    return bool(done.stdout.strip())


def _remove(versions: Path, folder: Path) -> bool:
    if folder.name.startswith(LEGACY_PREFIX):
        shutil.rmtree(folder)
        return True
    done = _git("-C", str(versions / REPO), "worktree", "remove", "--force", str(folder))
    return done.returncode == 0 and not folder.exists()


def _candidates(versions: Path, keep: Collection[Path]) -> list[Path]:
    kept = {Path(os.path.abspath(p)) for p in keep}
    if not versions.is_dir():
        return []
    return [
        f
        for f in sorted(versions.iterdir())
        if f.is_dir()
        and f.name != REPO
        and not f.name.startswith(".")
        and Path(os.path.abspath(f)) not in kept
    ]


def _prune_one(versions: Path, folder: Path) -> bool:
    busy = _in_use(folder)
    if busy is None:
        print(f"bosun: lsof is not usable, keeping {folder}", file=sys.stderr)
        return False
    return not busy and _remove(versions, folder)


def prune(versions: Path, keep: Collection[Path]) -> list[Path]:
    """Remove every version folder and legacy checkout not in `keep`; returns those removed.
    A folder a process has open stays, and so does every folder when lsof is missing or fails
    (the safe direction; a line on stderr says so)."""
    return [f for f in _candidates(versions, keep) if _prune_one(versions, f)]
