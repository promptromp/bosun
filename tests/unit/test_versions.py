"""Version folders and the link switched in one rename (lib/versions.py)."""

from __future__ import annotations

import contextlib
import errno
import io
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

import checkout_work
import versions
from install_paths import REPO


# maintenance.auto: git's auto maintenance runs detached after a commit, in the folder, and prune keeps a
# folder a running process has open.
GIT = [
    "git",
    *("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"),
    *("-c", "maintenance.auto=false"),
]


def no_work(_folder: Path) -> str:
    """What a legacy checkout these tests make holds of anyone's: nothing."""
    return ""


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
            # A gap is the path missing (ENOENT) or not a link. readlink can also fail with EINVAL when
            # it reached the link just replaced as that one was being torn down (seen on macOS runners
            # under load): the path itself, looked up again, is a link, so that is not a gap.
            while not stop.is_set():
                try:
                    os.readlink(install)
                except OSError as exc:
                    try:
                        gap = exc.errno != errno.EINVAL or not stat.S_ISLNK(os.lstat(install).st_mode)
                    except OSError as again:
                        exc, gap = again, True
                    if gap:
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
        legacy = versions.swap_in_link(
            install, v, versions.legacy_folder(v.parent, versions.commit_of(install))
        )
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
        removed = versions.prune(vdir, keep={b, c}, work=no_work)
        self.assertEqual(sorted(removed), sorted([a, legacy]))
        self.assertTrue(not a.exists() and not legacy.exists() and b.exists() and c.exists())
        listing = _git(vdir / REPO, "worktree", "list")
        self.assertTrue(a.name not in listing and b.name in listing)

    def test_prune_skips_a_folder_in_use(self) -> None:
        tmp_path = self.tmp
        vdir, (a, b) = _worktrees(tmp_path, 2)
        child = subprocess.Popen(["sleep", "30"], cwd=a)
        try:
            removed = versions.prune(vdir, keep=set(), work=no_work)
        finally:
            child.kill()
            child.wait()
        self.assertTrue(a.exists() and a not in removed)
        self.assertFalse(b.exists())

    def test_prune_keeps_everything_without_lsof(self) -> None:
        tmp_path = self.tmp
        vdir, (a,) = _worktrees(tmp_path, 1)
        with mock.patch.dict(os.environ, {"PATH": str(tmp_path / "nothing")}):
            self.assertEqual(versions.prune(vdir, keep=set(), work=no_work), [])
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
            removed = versions.prune(vdir, keep=set(), work=no_work)
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
        removed = versions.prune(vdir, keep={a}, work=no_work)
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
            versions.swap_in_link(install, v, versions.legacy_folder(v.parent, versions.commit_of(install)))
        self.assertTrue(not install.is_symlink() and (install / "f.txt").exists())
        self.assertTrue((leftover / "x").exists() and _no_hidden(tmp_path))

    def test_the_checkout_goes_straight_to_its_legacy_folder_in_one_call(self) -> None:
        """The link is made at the legacy folder's name and the two exchanged: no rename after the
        swap, so a cut there can never leave the checkout under a hidden name."""
        install, v, commit = _conversion(self.tmp)
        with mock.patch.object(os, "rename", side_effect=AssertionError("a second step")):
            legacy = versions.swap_in_link(
                install, v, versions.legacy_folder(v.parent, versions.commit_of(install))
            )
        self.assertEqual(legacy, versions.legacy_folder(v.parent, commit))
        self.assertTrue((legacy / "f.txt").is_file() and not legacy.is_symlink())

    def test_the_checkout_goes_where_the_record_says_whatever_its_head(self) -> None:
        """Minor 4: one derivation of the legacy folder, the record's: a checkout whose HEAD moved since the
        plan read its commit still lands in the folder the record names."""
        install, v, _ = _conversion(self.tmp)
        named = versions.legacy_folder(v.parent, "c" * 40)
        self.assertEqual(versions.swap_in_link(install, v, named), named)
        self.assertEqual((named / "f.txt").read_text(), "one")

    def test_a_link_a_swap_cut_short_left_is_replaced(self) -> None:
        install, v, commit = _conversion(self.tmp)
        versions.legacy_folder(v.parent, commit).symlink_to(v)  # made, and then the process died
        legacy = versions.swap_in_link(
            install, v, versions.legacy_folder(v.parent, versions.commit_of(install))
        )
        self.assertEqual((legacy / "f.txt").read_text(), "one")
        self.assertEqual(versions.current(install), v)

    def test_a_swap_that_fails_leaves_the_checkout_and_no_link(self) -> None:
        install, v, commit = _conversion(self.tmp)
        with (
            mock.patch.object(versions, "_swap", side_effect=OSError(errno.EXDEV, "no")),
            self.assertRaises(OSError),
        ):
            versions.swap_in_link(install, v, versions.legacy_folder(v.parent, versions.commit_of(install)))
        self.assertTrue(not install.is_symlink() and (install / "f.txt").read_text() == "one")
        self.assertFalse(os.path.lexists(versions.legacy_folder(v.parent, commit)))
        self.assertTrue(_no_hidden(self.tmp))

    def test_a_libc_without_the_call_is_an_oserror(self) -> None:
        tmp_path = self.tmp
        install, v, _ = _conversion(tmp_path)

        class Bare:
            pass

        with mock.patch.object(versions, "_libc", Bare), self.assertRaises(OSError) as info:
            versions.swap_in_link(install, v, versions.legacy_folder(v.parent, versions.commit_of(install)))
        self.assertEqual(info.exception.errno, errno.ENOSYS)
        self.assertTrue(not install.is_symlink() and (install / "f.txt").exists() and _no_hidden(tmp_path))


class SwitchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_a_link_is_pointed_and_a_checkout_swapped(self) -> None:
        install, v, commit = _conversion(self.tmp)
        legacy = v.parent / f"legacy-{commit[:12]}"
        self.assertEqual(versions.switched_from(install, v, install, commit), legacy)
        self.assertEqual(versions.switch(install, v, legacy), legacy)
        self.assertEqual(versions.current(install), v)
        other = v.parent / "bbbbbbbbbbbb"
        other.mkdir()
        self.assertEqual(versions.switched_from(install, other, v, "c" * 40), v)
        self.assertIsNone(versions.switch(install, other, v))
        self.assertEqual(versions.current(install), other)

    def test_a_checkout_from_before_versions_is_any_folder_but_a_version(self) -> None:
        vdir, (a,) = _worktrees(self.tmp, 1)
        self.assertFalse(versions.before_versions(a))
        self.assertTrue(versions.before_versions(versions.legacy_folder(vdir, "0123456789abcdef")))
        self.assertTrue(versions.before_versions(self.tmp / "src"))


class LegacyTest(unittest.TestCase):
    """The checkout from before versions is yours, and stays while it holds anything."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.vdir, (self.a,) = _worktrees(self.tmp, 1)
        self.mine = versions.legacy_folder(self.vdir, "0123456789abcdef")
        self.clean = versions.legacy_folder(self.vdir, "fedcba9876543210")
        for folder in (self.mine, self.clean):
            folder.mkdir()

    def work(self, folder: Path) -> str:
        return "uncommitted changes" if folder == self.mine else ""

    def test_prune_keeps_a_legacy_checkout_with_work_in_it(self) -> None:
        removed = versions.prune(self.vdir, keep={self.a}, work=self.work)
        self.assertEqual(removed, [self.clean])
        self.assertTrue(self.mine.is_dir())

    def test_held_back_says_which_and_why(self) -> None:
        self.assertEqual(
            versions.held_back(self.vdir, {self.a}, self.work), [(self.mine, "uncommitted changes")]
        )
        self.assertEqual(versions.held_back(self.vdir, {self.a, self.mine}, self.work), [])

    def test_every_folder_is_asked_about(self) -> None:
        asked: list[Path] = []

        def work(folder: Path) -> str:
            asked.append(folder)
            return ""

        versions.prune(self.vdir, keep=set(), work=work)
        self.assertEqual(sorted(asked), sorted([self.a, self.mine, self.clean]))

    def test_a_version_with_work_in_it_is_kept_too(self) -> None:
        def work(folder: Path) -> str:
            return "it has uncommitted changes" if folder == self.a else ""

        self.assertEqual(versions.prune(self.vdir, keep=set(), work=work), [self.mine, self.clean])
        self.assertTrue(self.a.is_dir())
        self.assertEqual(versions.held_back(self.vdir, set(), work), [(self.a, "it has uncommitted changes")])

    def test_a_folder_that_is_not_a_version_is_said_for_what_it_is(self) -> None:
        """Round 2: neither a worktree of bosun.git nor a checkout from before versions; kept, and not said
        to be a repository that could not be checked."""
        plain = self.vdir / "notes"
        plain.mkdir()
        self.assertEqual(versions.kept(plain), "bosun did not put it there")
        self.assertFalse(checkout_work.is_version(plain))
        self.assertTrue(checkout_work.is_version(self.a))

    def test_by_default_it_goes_by_the_shared_check(self) -> None:
        """No caller can forget it: a version with a change in it stays without a check passed in."""
        (self.a / "f.txt").write_text("edited\n")
        with contextlib.redirect_stderr(io.StringIO()):
            versions.prune(self.vdir, keep={self.mine, self.clean})
        self.assertTrue((self.a / "f.txt").is_file())
        self.assertEqual(versions.held_back(self.vdir, {self.mine, self.clean})[0][0], self.a)


class SweepTest(unittest.TestCase):
    """The temporary links a switch cut short left beside the install."""

    def test_only_stale_links_go(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        install = tmp / "bosun"
        target = tmp / "bosun-versions" / "aaaaaaaaaaaa"
        target.mkdir(parents=True)
        stale = tmp / ".bosun.link-0000aaaa"
        stale.symlink_to(target)
        os.utime(stale, (1000, 1000), follow_symlinks=False)
        fresh = tmp / ".bosun.link-1111bbbb"
        fresh.symlink_to(target)
        folder = tmp / ".bosun.link-2222cccc"  # never a folder, whatever its name
        folder.mkdir()
        os.utime(folder, (1000, 1000))
        other = tmp / ".other.link-3333dddd"
        other.symlink_to(target)
        os.utime(other, (1000, 1000), follow_symlinks=False)
        swept = versions.sweep_links(install, before=fresh.lstat().st_mtime - 1)
        self.assertEqual(swept, [stale])
        self.assertTrue(fresh.is_symlink() and folder.is_dir() and other.is_symlink() and target.is_dir())


class InUseTest(unittest.TestCase):
    def test_prune_keeps_a_folder_a_process_names_but_has_not_open(self) -> None:
        """lsof misses a Python process whose modules are closed and whose working folder is
        elsewhere (a long-running console); its command line still names the folder."""
        with tempfile.TemporaryDirectory() as tmp:
            vdir, (a, b) = _worktrees(Path(tmp), 2)
            sleeper = (
                "import time; time.sleep(30)"  # its command line names the folder, nothing is open there
            )
            child = subprocess.Popen(
                [sys.executable, "-c", sleeper, str(a / "lib" / "console_server.py")], cwd="/"
            )
            try:
                removed = versions.prune(vdir, keep=set(), work=no_work)
            finally:
                child.kill()
                child.wait()
            self.assertTrue(a.exists() and a not in removed)
            self.assertFalse(b.exists())

    def test_lsof_that_takes_too_long_keeps_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vdir, (a,) = _worktrees(Path(tmp), 1)
            slow = subprocess.TimeoutExpired(["lsof"], versions.LOOK_SECONDS)
            with (
                mock.patch("subprocess.run", side_effect=slow),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(versions.prune(vdir, keep=set(), work=no_work), [])
            self.assertTrue(a.exists())
