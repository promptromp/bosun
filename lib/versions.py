"""Version folders: each installed version a folder, the install path a link to the current one
(docs/design/update.md, "Versions side by side, switched in one step").

A version is a git worktree of the bare repository `<versions>/bosun.git`, in a folder named by
the first 12 characters of its commit. A checkout from before versions existed is kept as
`legacy-<12>` after the first update swaps it for the link. The install path is never missing:
`point` renames a new link over it, `swap_in_link` exchanges link and checkout in one call; `switch`
does whichever the install needs. `prune` deletes what an update no longer needs, but never a legacy
checkout or an older version that holds anything of yours (lib/checkout_work.py), and `sweep_links` the
temporary links a switch cut short left.
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

import checkout_work
import install_paths
import journal


LEGACY_PREFIX = "legacy-"

LOOK_SECONDS = 30  # the most lsof or ps may take to say whether a folder is in use
# Flags that exchange two paths atomically: macOS RENAME_SWAP, Linux RENAME_EXCHANGE.
_RENAME_SWAP = 0x00000002
_RENAME_EXCHANGE = 2
_AT_FDCWD = -100


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """git with no inherited GIT_* variables (a git hook's, another repository's tooling) and none of
    the user's hooks; its output kept, decoded tolerantly. bosun update's git goes through it too."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )


def commit_of(root: Path) -> str:
    """The commit checked out in `root`."""
    done = git("-C", str(root), "rev-parse", "HEAD")
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


def legacy_folder(versions: Path, commit: str) -> Path:
    """Where the first update keeps the checkout from before versions at <commit>."""
    return versions / f"{LEGACY_PREFIX}{commit[:12]}"


def before_versions(root: Path) -> bool:
    """Whether the bosun in <root> is a checkout from before versions: anything but a version folder,
    or one the first update kept as legacy-* (where a conversion put back leads the link)."""
    return not install_paths.is_version_folder(root) or root.name.startswith(LEGACY_PREFIX)


def _link_name(install: Path) -> str:
    return f".{install.name}.link-"


def _temporary_link(install: Path, target: Path) -> Path:
    link = install.with_name(f"{_link_name(install)}{uuid.uuid4().hex[:8]}")
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


def swap_in_link(install: Path, target: Path, legacy: Path) -> Path:
    """Replace the checkout at `install` with a link to `target` in one step, and keep the checkout
    as `legacy` (its legacy_folder beside `target`, as the update's record names it): the link is made
    there, then the two exchanged in one call, so the checkout is never anywhere else, nor the install
    path missing. Returns where the checkout went. Refuses, with nothing changed, when that folder exists;
    a link there is one a swap cut short left (none other is ever made in the versions folder), and is
    replaced."""
    if legacy.is_symlink():
        legacy.unlink()
    if os.path.lexists(legacy):
        raise FileExistsError(errno.EEXIST, f"{legacy} is already there; not converting {install}")
    os.symlink(target, legacy)
    try:
        _swap(legacy, install)
    except OSError:
        legacy.unlink(missing_ok=True)
        raise
    journal.flush_folder(install.parent)
    journal.flush_folder(legacy.parent)
    return legacy


def switch(install: Path, target: Path, kept: Path) -> Path | None:
    """`install` made to lead to `target` in one step: the link pointed there (point), or the checkout
    from before versions swapped for that link and kept as `kept` (switched_from, which the update
    records first). Returns where the checkout went; None when the install was a link already."""
    if install.is_symlink():
        point(install, target)
        return None
    return swap_in_link(install, target, kept)


def switched_from(install: Path, target: Path, root: Path, commit: str) -> Path:
    """Where the version in `root`, at `commit`, is once switch(install, target) is done: the same
    folder, but for the checkout at `install`, which goes to its legacy folder beside `target`."""
    if root == install and not install.is_symlink():
        return legacy_folder(target.parent, commit)
    return root


def _open_in(folder: Path) -> bool | None:
    """Whether a running process has `folder` open; None when lsof cannot say (in time)."""
    try:
        done = subprocess.run(
            ["lsof", "-t", "+D", str(folder)],
            capture_output=True,
            text=True,
            check=False,
            timeout=LOOK_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode not in (0, 1):
        return None
    return bool(done.stdout.strip())


def _named_by_a_process(folder: Path) -> bool | None:
    """Whether a running process's command line names `folder`, as the path or anything under it;
    None when ps cannot say. lsof misses a Python process whose modules are closed and whose working
    folder is elsewhere, such as a long-running `bosun console`."""
    try:
        done = subprocess.run(
            ["ps", "-axww", "-o", "command="], capture_output=True, check=False, timeout=LOOK_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    lines = done.stdout.decode(errors="replace").splitlines()
    names = {str(folder), os.path.realpath(folder)}
    return any(
        f"{name}/" in line or line.endswith(name) or f"{name} " in line for name in names for line in lines
    )


def _in_use(folder: Path) -> bool | None:
    """Whether a running process uses `folder`: has it open, or names it; None when that cannot be
    told."""
    found = [_open_in(folder), _named_by_a_process(folder)]
    return None if None in found else any(found)


def _remove(versions: Path, folder: Path) -> bool:
    if folder.name.startswith(LEGACY_PREFIX):
        shutil.rmtree(folder)
        return True
    done = git("-C", str(versions / install_paths.REPO), "worktree", "remove", "--force", str(folder))
    return done.returncode == 0 and not folder.exists()


def _candidates(versions: Path, keep: Collection[Path]) -> list[Path]:
    """The folders in `versions` not in `keep`, never the repository, a hidden one or a link (bosun
    makes none there, and removing one could remove what it leads to)."""
    kept = {Path(os.path.abspath(p)) for p in keep}
    if not versions.is_dir():
        return []
    return [
        f
        for f in sorted(versions.iterdir())
        if not f.is_symlink()
        and f.is_dir()
        and f.name != install_paths.REPO
        and not f.name.startswith(".")
        and Path(os.path.abspath(f)) not in kept
    ]


def kept(folder: Path) -> str:
    """Why prune keeps <folder> though it is neither version of the update, or "": a checkout from before
    versions with anything of yours in it, or that the installer did not make (as bosun uninstall
    decides for a checkout); an older version with a change or a commit of its own."""
    if folder.name.startswith(LEGACY_PREFIX):
        return checkout_work.kept(folder)
    return checkout_work.version_kept(folder)


def held_back(
    versions: Path, keep: Collection[Path], work: Callable[[Path], str] = kept
) -> list[tuple[Path, str]]:
    """Each folder prune keeps for what <work> finds in it although `keep` does not name it, and why."""
    found = ((folder, work(folder)) for folder in _candidates(versions, keep))
    return [(folder, why) for folder, why in found if why]


def _prune_one(versions: Path, folder: Path, work: Callable[[Path], str]) -> bool:
    if work(folder):
        return False
    busy = _in_use(folder)
    if busy is None:
        print(f"bosun: lsof or ps is not usable, keeping {folder}", file=sys.stderr)
        return False
    if busy:
        return False
    try:
        return _remove(versions, folder)
    except OSError as exc:
        print(
            f"bosun: could not remove {folder} ({exc.strerror or exc}); kept what is left of it",
            file=sys.stderr,
        )
        return False


def prune(versions: Path, keep: Collection[Path], *, work: Callable[[Path], str] = kept) -> list[Path]:
    """Remove every version folder and legacy checkout not in `keep`; returns those removed. One that
    `work` (kept, by default) finds anything of yours in stays, and held_back says why. A folder a
    process has open or names in its command line stays, and so does every folder when lsof or ps is
    missing, fails or takes too long (the safe direction; a line on stderr says so). One that cannot be
    removed is said on stderr and the rest go on; a link there is never followed."""
    return [f for f in _candidates(versions, keep) if _prune_one(versions, f, work)]


def temporary_links(install: Path) -> list[Path]:
    """The temporary links beside `install` that point makes (links only: never a folder, nor anything
    else, whatever its name)."""
    return [path for path in sorted(install.parent.glob(f"{_link_name(install)}*")) if path.is_symlink()]


def sweep_links(install: Path, before: float) -> list[Path]:
    """Remove the temporary links beside `install` that a switch cut short left, those last changed
    before `before` (a time.time()), which the caller sets to when its update started, under
    update.lock: every switch takes that lock, so none older is in use. Returns those removed."""
    swept = []
    for path in temporary_links(install):
        try:
            if path.lstat().st_mtime < before:
                path.unlink()
                swept.append(path)
        except OSError:
            continue
    return swept
