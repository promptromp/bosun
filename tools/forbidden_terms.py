#!/usr/bin/env python3
"""Fail if a file (or its path) contains a term from a list kept outside this repo.

Usage: forbidden_terms.py [--terms <file>] [--contents-only] [--redact] <file>...

The list is $BOSUN_CONFIG_DIR/forbidden-terms.txt (default
~/.config/bosun/forbidden-terms.txt), or --terms <file>: one case-insensitive Python
regular expression per line (a plain word is one; escape `.` or `+` only if it matters), with blank
lines and lines starting with `#` ignored. It names what must never reach this repo, such as
organization, product, repository and people names, internal hosts and ids, so the repo itself
never has to contain them. Without the file every check passes, so other users and CI are not
affected. Paths are checked as given (pre-commit passes them relative to the repo);
--contents-only skips them, as for a commit message file outside the repo. --redact (or
FORBIDDEN_TERMS_REDACT=1, as CI sets it) leaves out the matched text, which would otherwise show
the term itself in a log that anyone can read. In a binary file only its runs of readable text are
checked: metadata can hold a name, but a short term turns up by chance in compressed data.

Exit status: 0 nothing found (or no list), 1 a match, 2 the list or a file cannot be read, or a
pattern is invalid.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


TERMS_FILE_NAME = "forbidden-terms.txt"
DEFAULT_CONFIG_DIR = "~/.config/bosun"
EXIT_FOUND = 1
EXIT_ERROR = 2
PATH_LINE = 0  # Hit.line for a match in the file's path
READABLE_RUN = re.compile(rb"[\x20-\x7e]{8,}")


class TermsError(Exception):
    """The list or a file to check cannot be read, or a pattern is invalid."""


@dataclass(frozen=True)
class Term:
    line: int  # where it is in the list
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Hit:
    path: str
    line: int  # PATH_LINE: the path itself
    term: Term
    found: str


def default_terms_file() -> Path:
    config_dir = os.environ.get("BOSUN_CONFIG_DIR") or DEFAULT_CONFIG_DIR
    return Path(config_dir).expanduser() / TERMS_FILE_NAME


def parse_term(text: str, line: int, source: Path) -> Term:
    try:
        pattern = re.compile(text, re.IGNORECASE)
    except re.error as error:
        raise TermsError(f"{source}:{line}: invalid pattern: {error}") from error
    if pattern.search(""):
        raise TermsError(f"{source}:{line}: matches empty text, so it would match everything")
    return Term(line, pattern)


def load_terms(source: Path) -> list[Term]:
    """The terms in <source>; none if it does not exist."""
    if not source.exists():
        return []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise TermsError(f"cannot read {source}: {error}") from error
    return [
        parse_term(text, number, source)
        for number, text in ((number, raw.strip()) for number, raw in enumerate(lines, 1))
        if text and not text.startswith("#")
    ]


def scan_line(path: str, number: int, text: str, terms: list[Term]) -> list[Hit]:
    return [Hit(path, number, term, match.group(0)) for term in terms if (match := term.pattern.search(text))]


def lines_to_check(data: bytes) -> list[str]:
    """A text file's lines (read as UTF-8, invalid bytes replaced), a binary file's readable runs."""
    if b"\0" in data:
        return [run.decode("ascii") for run in READABLE_RUN.findall(data)]
    return data.decode("utf-8", errors="replace").splitlines()


def scan_file(path: str, terms: list[Term], check_path: bool = True) -> list[Hit]:
    """Matches in <path>'s contents, and in <path> itself."""
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise TermsError(f"cannot read {path}: {error}") from error
    hits = scan_line(path, PATH_LINE, path, terms) if check_path else []
    for number, line in enumerate(lines_to_check(data), 1):
        hits.extend(scan_line(path, number, line, terms))
    return hits


def describe(hit: Hit, redact: bool = False) -> str:
    where = f"{hit.path} (path)" if hit.line == PATH_LINE else f"{hit.path}:{hit.line}"
    found = "" if redact else f" '{hit.found}'"
    return f"{where}:{found} matches forbidden term on line {hit.term.line} of the list"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail if files contain terms from a local list.")
    parser.add_argument("--terms", type=Path, help=f"the list (default: $BOSUN_CONFIG_DIR/{TERMS_FILE_NAME})")
    parser.add_argument("--contents-only", action="store_true", help="do not check the paths")
    parser.add_argument("--redact", action="store_true", help="do not print the matched text")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    args.redact = args.redact or os.environ.get("FORBIDDEN_TERMS_REDACT") == "1"
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source: Path = args.terms or default_terms_file()
    try:
        if args.terms and not source.exists():
            raise TermsError(f"no such list: {source}")
        terms = load_terms(source)
        check_path = not args.contents_only
        hits = [hit for path in args.files for hit in scan_file(path, terms, check_path)] if terms else []
    except TermsError as error:
        print(f"forbidden-terms: {error}", file=sys.stderr)
        return EXIT_ERROR
    for hit in hits:
        print(describe(hit, args.redact))
    if hits:
        print(f"forbidden-terms: {len(hits)} match(es) of the terms in {source}", file=sys.stderr)
        return EXIT_FOUND
    return 0


if __name__ == "__main__":
    sys.exit(main())
