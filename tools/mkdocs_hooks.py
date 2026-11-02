"""MkDocs hooks for the docs site (mkdocs.yml): the root files it shows, and links that work on both.

The Markdown is written for GitHub, where a relative link resolves in the repository and
tools/check_links.py checks every one. The site is built from docs/ plus the root files in
ROOT_PAGES, so these hooks:

- add each root file in ROOT_PAGES as a page of the site (README.md is the home page);
- point each relative link at what it names on the site, or, for anything the site does not have (a
  script, CLAUDE.md, a folder), at the file on GitHub.

Links are found as tools/check_links.py finds them, outside code. `mkdocs build --strict` then checks
the rewritten links and their anchors.
"""

from __future__ import annotations

import html as html_entities
import logging
import posixpath
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from check_links import HTML_LINK, MARKDOWN_LINK, WEB, without_code
from mkdocs.structure.files import File, Files


if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.livereload import LiveReloadServer
    from mkdocs.structure.pages import Page


# Root files the site shows, and their pages there. GitHub looks for these at the root.
ROOT_PAGES = {
    "README.md": "index.md",
    "ROADMAP.md": "roadmap.md",
    "CONTRIBUTING.md": "contributing.md",
    "SECURITY.md": "security.md",
    "CODE_OF_CONDUCT.md": "code-of-conduct.md",
}
BRANCH = "main"
PARAGRAPH = re.compile(r"<p>(.*?)</p>", re.DOTALL)
GLUED_LIST = re.compile(r"\n[ \t]*(?:[-*+]|1[.)])[ \t]")  # a line that would start a list on GitHub
TAG = re.compile(r"<[^>]+>")

# A logger under "mkdocs", so that `mkdocs build --strict` fails on its warnings.
log = logging.getLogger("mkdocs.hooks.bosun")


def repo_root(config: MkDocsConfig) -> Path:
    return Path(config.config_file_path).resolve().parent


def on_files(files: Files, config: MkDocsConfig) -> Files:
    """The root files become pages; one that is missing is left out, and the strict build then fails on
    the nav entry or links that name it."""
    root = repo_root(config)
    for source, page in ROOT_PAGES.items():
        if (root / source).is_file():
            files.append(File.generated(config, page, abs_src_path=str(root / source)))
    return files


def on_serve(server: LiveReloadServer, config: MkDocsConfig, builder: Callable[..., Any]) -> LiveReloadServer:  # noqa: ARG001 (MkDocs passes it)
    """`mkdocs serve` rebuilds when a root page changes too, not only docs/."""
    root = repo_root(config)
    for source in ROOT_PAGES:
        server.watch(str(root / source))
    return server


def repo_path(file: File, root: Path) -> str | None:
    """Where <file> is in the repository, or None if it is not a file there."""
    if not file.abs_src_path:
        return None
    path = Path(file.abs_src_path).resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else None


def site_files(files: Files, root: Path) -> dict[str, File]:
    """The files the site has, by their path in the repository."""
    found = {}
    for file in files:
        path = repo_path(file, root)
        if path and file.inclusion.is_included():  # type: ignore[no-untyped-call]  # unannotated in MkDocs
            found[path] = file
    return found


def rewritten(link: str, source: str, base: str, site: Mapping[str, str], blob: str) -> str:
    """<link>, written in the repository file <source>, as the page at <base> on the site must write it.

    <site> maps a path in the repository to where it is on the site, in the same terms as <base>: a
    source path for Markdown links, which MkDocs then turns into URLs, or a URL for HTML ones, which
    it leaves alone. Anything not on the site is linked on GitHub, under <blob>.
    """
    if WEB.match(link) or link.startswith(("#", "/")):
        return link
    target, hash_mark, fragment = link.partition("#")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    if path in site:
        there = site[path]  # "" is the home page's URL
        address = posixpath.relpath(there or ".", posixpath.dirname(base) or ".")
        address += "/" if not there or there.endswith("/") else ""
    else:
        kind = "tree" if target.endswith("/") else "blob"
        address = f"{blob.replace('/blob/', f'/{kind}/')}/{path}"
    return address + hash_mark + fragment


def with_links(text: str, change: Callable[[str, bool], str]) -> str:
    """<text> with each relative link outside code replaced by change(link, is_html)."""
    blanked = without_code(text)
    spans = [
        (match.start(1), match.end(1), pattern is HTML_LINK)
        for pattern in (MARKDOWN_LINK, HTML_LINK)
        for match in pattern.finditer(blanked)
    ]
    for start, end, is_html in sorted(spans, reverse=True):
        text = text[:start] + change(text[start:end], is_html) + text[end:]
    return text


def html_text(html: str) -> str:
    return html_entities.unescape(TAG.sub("", html))


def glued_lists(html: str) -> list[str]:
    """How each paragraph of <html> begins that ends in a list: a list that starts on the line after
    a paragraph, with no blank line between. GitHub shows a list; Python-Markdown one paragraph."""
    found = []
    for match in PARAGRAPH.finditer(html):
        glued = GLUED_LIST.search(match.group(1))
        if glued:
            found.append(" ".join(html_text(match.group(1)[: glued.start()]).split())[:60])
    return found


def on_page_content(html: str, page: Page, config: MkDocsConfig, files: Files) -> str:  # noqa: ARG001 (MkDocs passes them)
    for start in glued_lists(html):
        log.warning("%s: put a blank line before the list after %r", page.file.src_uri, start)
    return html


def on_page_markdown(markdown: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    root = repo_root(config)
    source = repo_path(page.file, root)
    if source is None:
        return markdown
    site = site_files(files, root)
    by_source = {path: file.src_uri for path, file in site.items()}
    by_url = {path: file.url for path, file in site.items()}
    blob = f"{str(config.repo_url).rstrip('/')}/blob/{BRANCH}"

    def change(link: str, is_html: bool) -> str:
        if is_html:
            return rewritten(link, source, page.file.url, by_url, blob)
        return rewritten(link, source, page.file.src_uri, by_source, blob)

    return with_links(markdown, change)
