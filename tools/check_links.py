#!/usr/bin/env python3
"""Fail if a Markdown file links to a file or a heading that does not exist.

Usage: check_links.py <file.md>...

Relative links and images only, with their #anchors, as GitHub makes them from headings. Links to the
web are left out: they need a network, and a page that is down for a minute should not block a commit.

Exit status: 0 all fine, 1 a broken link.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


FENCE = re.compile(r"^(```|~~~).*?^\1\s*$", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")
HEADING = re.compile(r"^#{1,6} +(.+?)\s*$", re.MULTILINE)
MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)")
HTML_LINK = re.compile(r"""(?:href|src)=["']([^"']+)["']""")
WEB = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
EXIT_BROKEN = 1


def without_code(text: str) -> str:
    """<text> with code blanked out, keeping its line numbers."""
    blank = lambda match: re.sub(r"[^\n]", " ", match.group(0))  # noqa: E731 (used twice, right here)
    return INLINE_CODE.sub(blank, FENCE.sub(blank, text))


def anchor(heading: str) -> str:
    """The anchor GitHub gives a heading: lowercase, no punctuation, spaces as hyphens."""
    plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "").lower()
    return re.sub(r"[^\w\- ]", "", plain).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    """Every heading's anchor; a repeated heading gets -1, -2 and so on, as on GitHub."""
    found: set[str] = set()
    seen: dict[str, int] = {}
    for heading in HEADING.findall(FENCE.sub("", path.read_text())):
        base = anchor(heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(f"{base}-{count}" if count else base)
    return found


def problem(source: Path, link: str) -> str | None:
    """What is wrong with <link> in <source>, if anything."""
    target, _, fragment = link.partition("#")
    path = (source.parent / target).resolve() if target else source.resolve()
    if not path.exists():
        return "no such file"
    if fragment and path.suffix == ".md" and fragment not in anchors(path):
        return f"no heading for #{fragment}"
    return None


def broken_links(source: Path) -> list[str]:
    text = without_code(source.read_text())
    found = []
    for pattern in (MARKDOWN_LINK, HTML_LINK):
        for match in pattern.finditer(text):
            link = match.group(1)
            why = None if WEB.match(link) else problem(source, link)
            if why:
                found.append(f"{source}:{text.count(chr(10), 0, match.start()) + 1}: {link}: {why}")
    return found


def main(argv: list[str]) -> int:
    broken = [line for name in argv for line in broken_links(Path(name))]
    print("\n".join(broken), end="\n" if broken else "")
    return EXIT_BROKEN if broken else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
