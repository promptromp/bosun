"""lib/agy_workspace.py: what under an `.agents/` folder, from an agy window's folder up to the home folder,
keeps the window from starting (fail closed), and the workspace folders the connectors check reads."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agy_workspace


class WorkspaceCase(unittest.TestCase):
    """A scratch home with a repo in it, and a folder below the repo's root."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.home = self.root / "home"
        self.repo = self.home / "Development" / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.deep = self.repo / "packages" / "app"
        self.deep.mkdir(parents=True)

    def plant(self, folder: Path, relative: str = "hooks.json", text: str = "{}") -> Path:
        path = folder / ".agents" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def found(self, workdir: Path | None = None) -> list[Path]:
        return agy_workspace.workspace_hooks(workdir or self.deep, self.home)


class WorkspaceHooksTest(WorkspaceCase):
    def test_none_when_no_folder_has_one(self) -> None:
        self.assertEqual(self.found(), [])

    def test_the_windows_own_folder(self) -> None:
        planted = self.plant(self.deep)
        self.assertEqual(self.found(), [planted])

    def test_any_folder_up_to_the_repos_root(self) -> None:
        planted = self.plant(self.repo)
        self.assertEqual(self.found(), [planted])

    def test_nearest_first_when_there_are_several(self) -> None:
        root, near = self.plant(self.repo), self.plant(self.deep)
        self.assertEqual(self.found(), [near, root])

    def test_above_the_repos_root_up_to_and_including_home(self) -> None:
        """Review S2: agy may look further up than the repo's root; a user-level ~/.agents too."""
        above = self.plant(self.home / "Development")
        self.assertEqual(self.found(), [above])
        at_home = self.plant(self.home)
        self.assertEqual(self.found(), [above, at_home])

    def test_not_above_home(self) -> None:
        self.plant(self.root)
        self.assertEqual(self.found(), [])

    def test_a_folder_outside_home_is_looked_at_up_to_the_root(self) -> None:
        outside = self.root / "elsewhere" / "inner"
        outside.mkdir(parents=True)
        planted = self.plant(self.root / "elsewhere")
        self.assertEqual(self.found(outside), [planted])

    def test_a_nested_repo_still_sees_the_outer_ones(self) -> None:
        """Review S2: a submodule or nested repo with its own .git does not stop the walk."""
        inner = self.repo / "vendor" / "lib"
        (inner / ".git").mkdir(parents=True)
        planted = self.plant(self.repo)
        self.assertEqual(self.found(inner), [planted])

    def test_through_a_link_both_the_given_and_the_resolved_parents(self) -> None:
        """Review S2: the resolved path's parents alone would miss the given path's."""
        real = self.root / "real" / "repo"
        real.mkdir(parents=True)
        linked_parent = self.home / "linked"
        linked_parent.mkdir()
        (linked_parent / "repo").symlink_to(real)
        planted = self.plant(linked_parent)
        self.assertIn(planted, self.found(linked_parent / "repo"))
        real_side = self.plant(self.root / "real")
        self.assertIn(real_side, self.found(linked_parent / "repo"))

    def test_a_dangling_link_counts(self) -> None:
        (self.deep / ".agents").mkdir()
        link = self.deep / ".agents" / "hooks.json"
        link.symlink_to(self.root / "nowhere.json")
        self.assertEqual(self.found(), [link])

    def test_an_agents_that_is_a_link_counts(self) -> None:
        (self.root / "elsewhere").mkdir()
        (self.deep / ".agents").symlink_to(self.root / "elsewhere")
        self.assertEqual(self.found(), [self.deep / ".agents"])

    def test_an_agents_that_is_a_file_counts(self) -> None:
        (self.deep / ".agents").write_text("x")
        self.assertEqual(self.found(), [self.deep / ".agents"])


class RefusedNamesTest(WorkspaceCase):
    """Fail closed (the ruling of 2026-11-20): a plugins folder whatever it holds, and any file named like
    hooks, a plugin's manifest or MCP servers, at any depth; rules, skills and workflows are left alone."""

    def test_a_plugins_folder_whatever_it_holds(self) -> None:
        (self.repo / ".agents" / "plugins").mkdir(parents=True)
        self.assertEqual(self.found(), [self.repo / ".agents" / "plugins"])

    def test_the_reviews_plugin_shapes(self) -> None:
        """Review S1: Claude Code's layout, hooks inline in plugin.json, a plugin one level deeper, another
        hooks file name, servers in a plugin's own file."""
        plugins = self.repo / ".agents" / "plugins"
        for relative in (
            "plugins/p/hooks/hooks.json",
            "plugins/p/plugin.json",
            "plugins/a/b/hooks.json",
            "plugins/p/mcp_config.json",
        ):
            with self.subTest(relative):
                path = self.plant(self.repo, relative)
                found = self.found()
                self.assertIn(plugins, found)
                self.assertIn(path, found)

    def test_any_file_named_like_hooks_a_manifest_or_servers_at_any_depth(self) -> None:
        for relative in (
            "hooks.json",
            "pre-hooks.json",
            "HOOKS.JSON",
            "my_hooks_v2.json",
            "deep/er/hooks.json",
            "plugin.json",
            "x/plugin.json",
            "plugins.json",
            "mcp_config.json",
            "a/mcp_config.json",
        ):
            with self.subTest(relative):
                path = self.plant(self.deep, relative)
                self.assertIn(path, self.found())
                path.unlink()

    def test_rules_skills_and_workflows_are_left_alone(self) -> None:
        kept = ("rules/AGENTS.md", "skills/s/SKILL.md", "workflows/w.md", "skills.json", "notes.txt")
        for relative in kept:
            self.plant(self.repo, relative, "text")
        self.assertEqual(self.found(), [])

    def test_an_agents_too_large_to_look_through_refuses(self) -> None:
        self.plant(self.repo, "rules/AGENTS.md", "text")
        with mock.patch.object(agy_workspace, "MAX_ENTRIES", 1):
            self.assertEqual(self.found(), [self.repo / ".agents"])

    def test_an_agents_that_cannot_be_read_refuses(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root reads any folder")
        locked = self.repo / ".agents" / "locked"
        locked.mkdir(parents=True)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        self.assertEqual(self.found(), [self.repo / ".agents"])


class Round2Test(WorkspaceCase):
    """Review round 2, R2-S3: other hooks names and folders, links inside a customization folder, and
    `.agent` and `.gemini` below the home folder (never home's own `.gemini`, agy's global config)."""

    def test_more_names_under_agents(self) -> None:
        for relative, hit in (
            ("hooks/pre.json", "hooks"),
            ("hooks.yaml", "hooks.yaml"),
            ("hooks.jsonc", "hooks.jsonc"),
            ("pre-hooks.toml", "pre-hooks.toml"),
            ("githooks", "githooks"),
            ("mcp.json", "mcp.json"),
            ("mcp_servers.json", "mcp_servers.json"),
            ("settings.json", "settings.json"),
            ("deep/settings.json", "deep/settings.json"),
        ):
            with self.subTest(relative):
                self.plant(self.deep, relative)
                self.assertIn(self.deep / ".agents" / hit, self.found())
                shutil.rmtree(self.deep / ".agents")

    def test_a_link_inside_agents(self) -> None:
        target = self.root / "outside"
        (target / "plugins" / "p").mkdir(parents=True)
        (target / "plugins" / "p" / "hooks.json").write_text("{}")
        (self.deep / ".agents").mkdir()
        folder_link = self.deep / ".agents" / "x"
        folder_link.symlink_to(target)
        self.assertEqual(self.found(), [folder_link])
        folder_link.unlink()
        file_link = self.deep / ".agents" / "rules.md"
        file_link.symlink_to(target / "plugins" / "p" / "hooks.json")
        self.assertEqual(self.found(), [file_link])

    def test_agent_and_gemini_folders_below_home(self) -> None:
        for folder in (".agent", ".gemini"):
            with self.subTest(folder):
                settings = self.repo / folder / "settings.json"
                settings.parent.mkdir()
                settings.write_text("{}")
                self.assertEqual(self.found(), [settings])
                settings.unlink()
                (self.repo / folder / "styleguide.md").write_text("rules")
                self.assertEqual(self.found(), [])  # rules are fine there too

    def test_the_home_folders_own_gemini_and_agent_are_never_looked_at(self) -> None:
        for folder in (".gemini", ".agent"):
            (self.home / folder / "config" / "plugins" / "bosun").mkdir(parents=True)
            (self.home / folder / "settings.json").write_text("{}")
            (self.home / folder / "hooks.json").write_text("{}")
        self.assertEqual(self.found(), [])
        planted = self.plant(self.home)  # but home's own .agents is
        self.assertEqual(self.found(), [planted])

    def test_gemini_outside_home_is_looked_at(self) -> None:
        outside = self.root / "elsewhere"
        (outside / ".gemini").mkdir(parents=True)
        (outside / ".gemini" / "settings.json").write_text("{}")
        self.assertEqual(self.found(outside), [outside / ".gemini" / "settings.json"])


class WorkspaceFoldersTest(WorkspaceCase):
    def test_up_to_the_repos_root_for_the_connectors(self) -> None:
        self.assertEqual(agy_workspace.workspace_folders(self.deep), [self.deep, self.deep.parent, self.repo])

    def test_without_a_repo_only_the_folder_itself(self) -> None:
        loose = self.root / "loose" / "inner"
        loose.mkdir(parents=True)
        self.assertEqual(agy_workspace.workspace_folders(loose), [loose])


class MainTest(WorkspaceCase):
    def test_the_command_prints_each_path_and_exits_0(self) -> None:
        planted = self.plant(self.repo)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(Path, "home", return_value=self.home):
            self.assertEqual(agy_workspace.main(["hooks", str(self.deep)]), 0)
        self.assertEqual(out.getvalue(), f"{planted}\n")
        planted.unlink()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(Path, "home", return_value=self.home):
            self.assertEqual(agy_workspace.main(["hooks", str(self.deep)]), 0)
        self.assertEqual(out.getvalue(), "")

    def test_a_wrong_command_line_is_a_usage_error(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(agy_workspace.main([]), 2)
