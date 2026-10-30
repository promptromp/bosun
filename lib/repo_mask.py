#!/usr/bin/env python3
"""Repository mask matching (BOSUN_REPO_MASK): the one implementation.

The mask is a space-separated list of owner/repo glob patterns; a bare owner means all its repos,
matching is case-insensitive, and an empty mask matches every repository.

Usage: repo_mask.py <mask> [--json-key <key>]
  Reads lines on stdin and prints those whose repository matches: the first tab- or
  space-separated field of each line, or with --json-key, that key of each JSON line.
"""

from __future__ import annotations

import fnmatch
import json
import sys


def patterns(mask: str) -> list[str]:
    return [p if "/" in p else f"{p}/*" for p in mask.lower().split()]


def matches(slug: str, mask: str) -> bool:
    globs = patterns(mask)
    return not globs or any(fnmatch.fnmatchcase(slug.lower(), glob) for glob in globs)


HELP = ("-h", "--help")
JSON_KEY = "--json-key"
USAGE_ERROR = 2


def parse_args(argv: list[str]) -> tuple[str, str | None] | None:
    """(mask, json key or None), or None if the arguments are not `<mask> [--json-key <key>]`."""
    if len(argv) == 1:
        return argv[0], None
    if len(argv) == 3 and argv[1] == JSON_KEY:  # noqa: PLR2004  (mask, flag, key)
        return argv[0], argv[2]
    return None


def slug_of(line: str, key: str | None) -> str | None:
    """The repository named by <line>: its first field, or with <key>, that key of the JSON line."""
    if not line:
        return None
    if key is None:
        return line.split(maxsplit=1)[0]
    try:
        value = json.loads(line).get(key)
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def main(argv: list[str]) -> int:
    if argv[:1] and argv[0] in HELP:
        print(__doc__.strip())
        return 0
    parsed = parse_args(argv)
    if parsed is None:
        print(__doc__.strip(), file=sys.stderr)
        return USAGE_ERROR
    mask, key = parsed
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        slug = slug_of(line, key)
        if slug is not None and matches(slug, mask):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
