"""Version folders and the link switched in one rename (lib/versions.py)."""

from __future__ import annotations

import contextlib
import errno
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

import versions
from install_paths import REPO


GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def _git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    out = subprocess.run([*GIT, *args], cwd=cwd, env=env, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _source(tmp: Path) -> Path:
    src = tmp / "src"
    src.mkdir()
    _git(src, "init", "-q")
    (src / "f.txt").write_text("one")
    _git(src, "add", "f.txt")
    _git(src, "commit", "-q", "-m", "one")
    return src


def _worktrees(tmp: Path, count: int) -> tuple[Path, list[Path]]:
    """A versions folder with a bare repository and `count` worktrees on distinct commits."""
    src = _source(tmp)
    vdir = tmp / "bosun-versions"
    vdir.mkdir()
    _git(tmp, "clone", "-q", "--bare", str(src), str(vdir / REPO))
    folders = []
    for i in range(count):
        (src / "f.txt").write_text(str(i))
        _git(src, "commit", "-q", "-am", f"c{i}")
        _git(vdir / REPO, "fetch", "-q", str(src), "HEAD")
        commit = _git(vdir / REPO, "rev-parse", "FETCH_HEAD")
        folder = versions.folder_for(vdir, commit)
        _git(vdir / REPO, "worktree", "add", "-q", "--detach", str(folder), commit)
        folders.append(folder)
    return vdir, folders


def _conversion(tmp: Path) -> tuple[Path, Path, str]:
    install = _source(tmp)
    commit = versions.commit_of(install)
    v = tmp / "bosun-versions" / "aaaaaaaaaaaa"
    v.mkdir(parents=True)
    return install, v, commit


def _no_hidden(tmp: Path) -> bool:
    return [p.name for p in tmp.iterdir() if p.name.startswith(".")] == []


class VersionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_point_switches_without_a_gap(self) -> None:
        tmp_path = self.tmp
        a, b, install = tmp_path / "a", tmp_path / "b", tmp_path / "bosun"
        a.mkdir()
        b.mkdir()
        versions.point(install, a)
        stop, errors = threading.Event(), []

        def reader() -> None:
            while not stop.is_set():
                try:
                    os.readlink(install)
                except FileNotFoundError as exc:
                    errors.append(exc)

        thread = threading.Thread(target=reader)
        thread.start()
        for i in range(100):
            versions.point(install, b if i % 2 == 0 else a)
        stop.set()
        thread.join()
        self.assertEqual(errors, [])

    def test_current_follows_the_link(self) -> None:
        tmp_path = self.tmp
        a, install = tmp_path / "a", tmp_path / "bosun"
        a.mkdir()
        versions.point(install, a)
        self.assertEqual(versions.current(install), a)
        plain = tmp_path / "plain"
        plain.mkdir()
        self.assertIsNone(versions.current(plain))

    def test_the_conversion_swaps_a_checkout_for_a_link(self) -> None:
        tmp_path = self.tmp
        install = _source(tmp_path)
        commit = versions.commit_of(install)
        vdir = tmp_path / "bosun-versions"
        v = vdir / "aaaaaaaaaaaa"
        v.mkdir(parents=True)
        legacy = versions.swap_in_link(install, v)
        self.assertEqual(legacy, vdir / f"{versions.LEGACY_PREFIX}{commit[:12]}")
        self.assertEqual((legacy / "f.txt").read_text(), "one")
        self.assertTrue(install.is_symlink() and versions.current(install) == v)
        self.assertEqual([p.name for p in tmp_path.iterdir() if p.name.startswith(".")], [])

    def test_prune_keeps_what_it_is_told(self) -> None:
        tmp_path = self.tmp
        vdir, (a, b, c) = _worktrees(tmp_path, 3)
        legacy = vdir / f"{versions.LEGACY_PREFIX}abcdef012345"
        legacy.mkdir()
        (legacy / "x").write_text("x")
        removed = versions.prune(vdir, keep={b, c})
        self.assertEqual(sorted(removed), sorted([a, legacy]))
        self.assertTrue(not a.exists() and not legacy.exists() and b.exists() and c.exists())
        listing = _git(vdir / REPO, "worktree", "list")
        self.assertTrue(a.name not in listing and b.name in listing)

    def test_prune_skips_a_folder_in_use(self) -> None:
        tmp_path = self.tmp
        vdir, (a, b) = _worktrees(tmp_path, 2)
        child = subprocess.Popen(["sleep", "30"], cwd=a)
        try:
            removed = versions.prune(vdir, keep=set())
        finally:
            child.kill()
            child.wait()
        self.assertTrue(a.exists() and a not in removed)
        self.assertFalse(b.exists())

    def test_prune_keeps_everything_without_lsof(self) -> None:
        tmp_path = self.tmp
        vdir, (a,) = _worktrees(tmp_path, 1)
        with mock.patch.dict(os.environ, {"PATH": str(tmp_path / "nothing")}):
            self.assertEqual(versions.prune(vdir, keep=set()), [])
        self.assertTrue(a.exists())

    def test_prune_reports_a_folder_it_cannot_remove_and_goes_on(self) -> None:
        vdir, (a,) = _worktrees(self.tmp, 1)
        stuck = vdir / f"{versions.LEGACY_PREFIX}000000000000"  # sorted before the next legacy one
        (stuck / "sub").mkdir(parents=True)
        (stuck / "sub" / "x").write_text("x")
        (stuck / "sub").chmod(0o500)  # x cannot be deleted
        self.addCleanup((stuck / "sub").chmod, 0o700)
        later = vdir / f"{versions.LEGACY_PREFIX}ffffffffffff"
        later.mkdir()
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            removed = versions.prune(vdir, keep=set())
        self.assertEqual(sorted(removed), sorted([a, later]))
        self.assertTrue((stuck / "sub" / "x").exists())
        self.assertIn(f"could not remove {stuck}", said.getvalue())
        self.assertEqual(len(said.getvalue().splitlines()), 1)

    def test_prune_never_follows_a_link(self) -> None:
        vdir, (a,) = _worktrees(self.tmp, 1)
        mine = self.tmp / "mine"
        mine.mkdir()
        (mine / "notes.txt").write_text("keep")
        link = vdir / f"{versions.LEGACY_PREFIX}abcdef012345"
        link.symlink_to(mine)
        (vdir / "0123456789ab").symlink_to(a)  # a link named like a version, to one
        removed = versions.prune(vdir, keep={a})
        self.assertEqual(removed, [])
        self.assertEqual((mine / "notes.txt").read_text(), "keep")
        self.assertTrue(link.is_symlink() and a.is_dir())
        self.assertIn(a.name, _git(vdir / REPO, "worktree", "list"))

    def test_a_leftover_legacy_folder_refuses_with_nothing_changed(self) -> None:
        tmp_path = self.tmp
        install, v, commit = _conversion(tmp_path)
        leftover = v.parent / f"{versions.LEGACY_PREFIX}{commit[:12]}"
        leftover.mkdir()
        (leftover / "x").write_text("x")
        with self.assertRaises(FileExistsError):
            versions.swap_in_link(install, v)
        self.assertTrue(not install.is_symlink() and (install / "f.txt").exists())
        self.assertTrue((leftover / "x").exists() and _no_hidden(tmp_path))

    def test_a_failed_rename_after_the_swap_swaps_back(self) -> None:
        tmp_path = self.tmp
        install, v, _ = _conversion(tmp_path)

        def refuse(src: object, dst: object) -> None:
            raise OSError(errno.ENOTEMPTY, "no")

        with mock.patch.object(os, "rename", refuse), self.assertRaisesRegex(OSError, "checkout again"):
            versions.swap_in_link(install, v)
        self.assertTrue(not install.is_symlink() and (install / "f.txt").read_text() == "one")
        self.assertTrue(_no_hidden(tmp_path))

    def test_a_libc_without_the_call_is_an_oserror(self) -> None:
        tmp_path = self.tmp
        install, v, _ = _conversion(tmp_path)

        class Bare:
            pass

        with mock.patch.object(versions, "_libc", Bare), self.assertRaises(OSError) as info:
            versions.swap_in_link(install, v)
        self.assertEqual(info.exception.errno, errno.ENOSYS)
        self.assertTrue(not install.is_symlink() and (install / "f.txt").exists() and _no_hidden(tmp_path))
