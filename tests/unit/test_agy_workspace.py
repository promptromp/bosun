"""lib/agy_workspace.py: the workspace folders of an agy window and the hooks file agy would run from them."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agy_workspace


class WorkspaceHooksTest(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.repo = self.root / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.deep = self.repo / "packages" / "app"
        self.deep.mkdir(parents=True)

    def plant(self, folder: Path) -> Path:
        (folder / ".agents").mkdir(exist_ok=True)
        path = folder / ".agents" / "hooks.json"
        path.write_text("{}")
        return path

    def test_none_when_no_folder_has_one(self) -> None:
        self.assertEqual(agy_workspace.workspace_hooks(self.deep), [])

    def test_the_windows_own_folder(self) -> None:
        planted = self.plant(self.deep)
        self.assertEqual(agy_workspace.workspace_hooks(self.deep), [planted])

    def test_any_folder_up_to_the_repos_root(self) -> None:
        planted = self.plant(self.repo)
        self.assertEqual(agy_workspace.workspace_hooks(self.deep), [planted])

    def test_nearest_first_when_there_are_several(self) -> None:
        root, near = self.plant(self.repo), self.plant(self.deep)
        self.assertEqual(agy_workspace.workspace_hooks(self.deep), [near, root])

    def test_not_above_the_repos_root(self) -> None:
        self.plant(self.root)
        self.assertEqual(agy_workspace.workspace_hooks(self.deep), [])

    def test_without_a_repo_only_the_folder_itself(self) -> None:
        loose = self.root / "loose" / "inner"
        loose.mkdir(parents=True)
        self.plant(loose.parent)
        self.assertEqual(agy_workspace.workspace_hooks(loose), [])
        planted = self.plant(loose)
        self.assertEqual(agy_workspace.workspace_hooks(loose), [planted])

    def test_a_dangling_link_counts(self) -> None:
        (self.deep / ".agents").mkdir()
        link = self.deep / ".agents" / "hooks.json"
        link.symlink_to(self.root / "nowhere.json")
        self.assertEqual(agy_workspace.workspace_hooks(self.deep), [link])

    def test_the_command_prints_each_path_and_exits_0(self) -> None:
        planted = self.plant(self.repo)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(agy_workspace.main(["hooks", str(self.deep)]), 0)
        self.assertEqual(out.getvalue(), f"{planted}\n")
        planted.unlink()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(agy_workspace.main(["hooks", str(self.deep)]), 0)
        self.assertEqual(out.getvalue(), "")

    def test_a_wrong_command_line_is_a_usage_error(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(agy_workspace.main([]), 2)
            self.assertEqual(agy_workspace.main(["hooks"]), 2)
            self.assertEqual(agy_workspace.main(["other", "x"]), 2)


if __name__ == "__main__":
    unittest.main()
