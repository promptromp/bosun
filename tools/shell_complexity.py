#!/usr/bin/env python3
"""Length and branch complexity of bash functions (lizard and complexipy do not read shell).

Usage: shell_complexity.py [--max-ccn N] [--max-length N] [--max-script-ccn N]
                           [--max-script-length N] [--report] <file>...

Relies on shfmt formatting (enforced by pre-commit): a function starts with `name() {` and ends
at the first `}` line with the same indentation. For each function it counts
- length: lines of code (no blank or comment-only lines)
- ccn: 1 + decision points, like McCabe: if, elif, while, until, for, each case pattern, &&, ||
Quoted strings and comments are ignored. The top level of a script counts as `<script>`, with its
own, higher limits (command scripts read top to bottom). Exits 1 if anything is over a limit;
--report prints every function.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MAX_CCN = 15
DEFAULT_MAX_LENGTH = 60
DEFAULT_MAX_SCRIPT_CCN = 25
DEFAULT_MAX_SCRIPT_LENGTH = 120
TOP_LEVEL = "<script>"
FUNCTION_START = re.compile(r"^(\s*)(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{\s*(?:#.*)?$")
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
KEYWORDS = re.compile(r"(?:^|[\s;(])(if|elif|while|until|for)\s")
CASE_PATTERN = re.compile(r"^\s*[^\s()#][^()]*\)(?:\s|$)")
OPERATORS = re.compile(r"&&|\|\|")
CASE_START = re.compile(r"(?:^|\s)case\s")
CASE_END = re.compile(r"(?:^|\s)esac\b")


@dataclass
class Function:
    file: Path
    name: str
    line: int
    length: int = 0
    ccn: int = 1


def code_of(line: str) -> str:
    """The line without quoted strings and trailing comments."""
    unquoted = QUOTED.sub("''", line)
    return re.sub(r"(^|\s)#.*$", "", unquoted)


def decisions(code: str, in_case: bool) -> int:
    count = len(KEYWORDS.findall(code)) + len(OPERATORS.findall(code))
    if in_case and CASE_PATTERN.match(code) and not code.strip().startswith(("case ", "esac")):
        count += 1
    return count


@dataclass
class Scanner:
    """Walks a script line by line, attributing each line of code to the function it is in."""

    path: Path
    top: Function
    stack: list[tuple[str, Function]]  # (indentation, function) for nested definitions
    case_depth: int = 0

    def enters_or_leaves(self, number: int, line: str, found: list[Function]) -> bool:
        """Handle a function's first or closing line; True if <line> was one."""
        start = FUNCTION_START.match(line)
        if start:
            function = Function(self.path, start.group(2), number)
            self.stack.append((start.group(1), function))
            found.append(function)
            return True
        if self.stack and line.rstrip() == f"{self.stack[-1][0]}}}":
            self.stack.pop()
            return True
        return False

    def count(self, code: str) -> None:
        current = self.stack[-1][1] if self.stack else self.top
        self.case_depth += len(CASE_START.findall(code)) - len(CASE_END.findall(code))
        current.length += 1
        current.ccn += decisions(code, self.case_depth > 0)


def measure(path: Path) -> list[Function]:
    """Every function in <path>, plus the top level of the script."""
    scanner = Scanner(path, Function(path, TOP_LEVEL, 1), [])
    found = [scanner.top]
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if scanner.enters_or_leaves(number, line, found):
            continue
        code = code_of(line).strip()
        if code:
            scanner.count(code)
    return found


def over_limit(function: Function, args: argparse.Namespace) -> bool:
    script = function.name == TOP_LEVEL
    max_ccn: int = args.max_script_ccn if script else args.max_ccn
    max_length: int = args.max_script_length if script else args.max_length
    return function.ccn > max_ccn or function.length > max_length


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--max-ccn", type=int, default=DEFAULT_MAX_CCN)
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH)
    parser.add_argument("--max-script-ccn", type=int, default=DEFAULT_MAX_SCRIPT_CCN)
    parser.add_argument("--max-script-length", type=int, default=DEFAULT_MAX_SCRIPT_LENGTH)
    parser.add_argument("--report", action="store_true", help="print every function, not just the ones over")
    parser.add_argument("files", nargs="+", type=Path)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    over = 0
    for function in (f for path in args.files for f in measure(path)):
        too_complex = over_limit(function, args)
        over += too_complex
        if args.report or too_complex:
            flag = "  OVER" if too_complex else ""
            where = f"{function.file}:{function.line}: {function.name}"
            print(f"{where} ccn={function.ccn} length={function.length}{flag}")
    if over:
        print(
            f"{over} function(s) or script(s) over the limits (functions: ccn {args.max_ccn}, length "
            f"{args.max_length}; script bodies: ccn {args.max_script_ccn}, length {args.max_script_length}); "
            "split them into smaller functions",
            file=sys.stderr,
        )
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
