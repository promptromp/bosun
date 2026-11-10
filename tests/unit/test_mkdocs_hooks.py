"""tools/mkdocs_hooks.py: the docs site's root pages, its links, and lists GitHub and the site read alike."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import mkdocs_hooks


# MkDocs is in the docs group; the smoke suite runs these with a plain python3 that has no MkDocs.
HAVE_MKDOCS = importlib.util.find_spec("mkdocs") is not None


BLOB = "https://github.com/acme/widget/blob/main"
SOURCES = {
    "docs/setup.md": "setup.md",
    "docs/design/agents.md": "design/agents.md",
    "ROADMAP.md": "roadmap.md",
}
URLS = {"docs/assets/logo.png": "assets/logo.png", "docs/setup.md": "setup/", "README.md": ""}


class RewrittenTest(unittest.TestCase):
    def md(self, link: str, source: str, base: str) -> str:
        return mkdocs_hooks.rewritten(link, source, base, SOURCES, BLOB)

    def test_the_readme_links_into_docs_from_the_home_page(self) -> None:
        self.assertEqual(self.md("docs/setup.md#uninstall", "README.md", "index.md"), "setup.md#uninstall")
        self.assertEqual(self.md("docs/design/agents.md", "README.md", "index.md"), "design/agents.md")

    def test_links_between_docs_stay_as_written(self) -> None:
        self.assertEqual(
            self.md("../setup.md#x", "docs/design/agents.md", "design/agents.md"), "../setup.md#x"
        )
        self.assertEqual(self.md("design/agents.md", "docs/setup.md", "setup.md"), "design/agents.md")

    def test_a_root_page_is_linked_where_the_site_has_it(self) -> None:
        self.assertEqual(
            self.md("../../ROADMAP.md#next", "docs/design/agents.md", "design/agents.md"),
            "../roadmap.md#next",
        )
        self.assertEqual(self.md("docs/setup.md", "ROADMAP.md", "roadmap.md"), "setup.md")

    def test_what_the_site_does_not_have_is_linked_on_github(self) -> None:
        self.assertEqual(self.md("../get.sh", "docs/setup.md", "setup.md"), f"{BLOB}/get.sh")
        self.assertEqual(self.md("CLAUDE.md#tooling", "README.md", "index.md"), f"{BLOB}/CLAUDE.md#tooling")

    def test_a_folder_is_linked_as_a_tree(self) -> None:
        self.assertEqual(
            self.md("docs/design/", "README.md", "index.md"),
            "https://github.com/acme/widget/tree/main/docs/design",
        )

    def test_the_web_anchors_and_absolute_paths_are_left_alone(self) -> None:
        for link in ("https://example.com/a.md", "mailto:me@example.com", "#top", "/abs/path"):
            self.assertEqual(self.md(link, "docs/setup.md", "setup.md"), link)

    def test_html_links_are_made_relative_to_the_page_s_url(self) -> None:
        def html(link: str, base: str) -> str:
            return mkdocs_hooks.rewritten(link, "README.md", base, URLS, BLOB)

        self.assertEqual(html("docs/assets/logo.png", ""), "assets/logo.png")
        self.assertEqual(html("docs/assets/logo.png", "setup/"), "../assets/logo.png")
        self.assertEqual(html("docs/setup.md", ""), "setup/")
        self.assertEqual(html("README.md", "setup/"), "../")


class WithLinksTest(unittest.TestCase):
    def test_every_link_outside_code_is_changed(self) -> None:
        text = '[a](one.md) and [b](two.md#x)\n<img src="pic.png">\n`[c](code.md)`\n```\n[d](fence.md)\n```\n'
        seen: list[tuple[str, bool]] = []

        def change(link: str, is_html: bool) -> str:
            seen.append((link, is_html))
            return link.upper()

        out = mkdocs_hooks.with_links(text, change)
        self.assertEqual(sorted(seen), [("one.md", False), ("pic.png", True), ("two.md#x", False)])
        self.assertIn("[a](ONE.MD) and [b](TWO.MD#X)", out)
        self.assertIn('src="PIC.PNG"', out)
        self.assertIn("`[c](code.md)`", out)
        self.assertIn("[d](fence.md)", out)


class GluedListsTest(unittest.TestCase):
    def test_a_list_on_the_line_after_a_paragraph_is_found(self) -> None:
        self.assertEqual(
            mkdocs_hooks.glued_lists("<p>Goals &amp; <em>more</em>:\n- one\n- two</p>"), ["Goals & more:"]
        )
        self.assertEqual(mkdocs_hooks.glued_lists("<p>Steps:\n1. first</p>"), ["Steps:"])

    def test_other_paragraphs_are_fine(self) -> None:
        self.assertEqual(mkdocs_hooks.glued_lists("<p>One line\nand the next</p><ul><li>a</li></ul>"), [])
        # GitHub does not start a list at 2 inside a paragraph either.
        self.assertEqual(mkdocs_hooks.glued_lists("<p>Since\n2. was all</p>"), [])


@unittest.skipUnless(HAVE_MKDOCS, "MkDocs is not installed (uv sync installs the docs group)")
class BuildTest(unittest.TestCase):
    """A whole strict build of a small site with the hooks, as `mkdocs build --strict` runs it."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "docs" / "assets").mkdir(parents=True)
        (self.root / "docs" / "assets" / "logo.png").write_bytes(b"png")
        (self.root / "docs" / "guide.md").write_text(
            "# Guide\n\n## Usage\n\nBack to [the start](../README.md).\n"
        )
        (self.root / "get.sh").write_text("echo hi\n")
        (self.root / "README.md").write_text("# Widget\n\nGitHub's front page.\n")
        (self.root / "docs" / "index.md").write_text(
            '# Widget\n\n<img src="assets/logo.png">\n\n'
            "See [usage](guide.md#usage), [the script](../get.sh) and [the roadmap](../ROADMAP.md).\n"
        )
        (self.root / "ROADMAP.md").write_text("# Roadmap\n\nNext: [usage](docs/guide.md#usage).\n")
        hooks = Path(mkdocs_hooks.__file__).resolve()
        (self.root / "mkdocs.yml").write_text(
            "site_name: widget\nrepo_url: https://github.com/acme/widget\n"
            f"hooks:\n  - {hooks}\n"
            "validation: {omitted_files: warn, unrecognized_links: warn, anchors: warn}\n"
            "nav:\n  - Home: index.md\n  - Guide: guide.md\n  - Roadmap: roadmap.md\n"
        )

    def build(self) -> Path:
        from mkdocs.commands.build import build
        from mkdocs.config import load_config

        config = load_config(str(self.root / "mkdocs.yml"), strict=True, site_dir=str(self.root / "site"))
        build(config)
        return self.root / "site"

    def test_the_root_files_are_pages_and_their_links_work_on_the_site(self) -> None:
        site = self.build()
        home = (site / "index.html").read_text()
        self.assertIn('href="guide/#usage"', home)
        self.assertIn('href="roadmap/"', home)
        self.assertIn('href="https://github.com/acme/widget/blob/main/get.sh"', home)
        self.assertIn('src="assets/logo.png"', home)
        self.assertIn('href="../guide/#usage"', (site / "roadmap" / "index.html").read_text())

    def test_the_readme_stays_github_s_front_page(self) -> None:
        site = self.build()
        self.assertFalse((site / "readme").exists())
        guide = (site / "guide" / "index.html").read_text()
        self.assertIn('href="https://github.com/acme/widget/blob/main/README.md"', guide)

    def test_a_glued_list_fails_the_strict_build(self) -> None:
        (self.root / "docs" / "guide.md").write_text("# Guide\n\n## Usage\n\nSteps:\n- one\n- two\n")
        from mkdocs.exceptions import Abort

        with self.assertRaises(Abort), self.assertLogs("mkdocs", "WARNING") as logged:
            self.build()
        self.assertEqual(
            logged.output,
            ["WARNING:mkdocs.hooks.bosun:guide.md: put a blank line before the list after 'Steps:'"],
        )


class HomeExamplesTest(unittest.TestCase):
    """The home page, and only the home page, carries its examples' data."""

    def context(self, template: str | None) -> dict[str, object]:
        page = SimpleNamespace(meta={"template": template} if template else {})
        config = SimpleNamespace(
            config_file_path=str(Path(mkdocs_hooks.__file__).resolve().parents[1] / "mkdocs.yml")
        )
        return mkdocs_hooks.on_page_context({}, page, config, None)  # type: ignore[arg-type]

    def test_the_home_page_gets_the_data(self) -> None:
        data = json.loads(str(self.context(mkdocs_hooks.HOME_TEMPLATE)["examples"]))
        self.assertEqual(set(data), {"ledger", "needs_you", "knowledge"})

    def test_other_pages_do_not(self) -> None:
        self.assertNotIn("examples", self.context(None))
        self.assertNotIn("examples", self.context("main.html"))
