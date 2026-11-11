"""Shared code names no agent: what differs between agents is in each agent's own module or profile."""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path
from types import ModuleType
from typing import cast


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from agent_state import AGENTS


ROOT = Path(__file__).resolve().parents[2]


# The agents' own modules, and the table that lists them, may name them.
OWN_MODULES = {
    "agent_state.py",
    *(f"{cast(ModuleType, agent).__name__}.py" for agent in AGENTS.values()),
    *(f"{agent.PROFILE.connectors}.py" for agent in AGENTS.values()),
}

# Files that hold something only Claude Code has: how many places, and what they are. A new place in
# one of them is a deliberate change here, so a choice between agents cannot slip in beside them.
ALLOWED: dict[str, tuple[int, str]] = {
    "init_detect.py": (1, "the claude executable that `claude auth status` asks, the orchestrator's login"),
    "init_plan.py": (1, "REQUIRED_TOOLS: claude, which the orchestrator runs on"),
    "learn.py": (2, "claude_bin: the headless Claude Code call that does the analysis"),
    "login.py": (1, "the login checks of Claude Code, which the orchestrator and the check-in run on"),
    "slack_watch.py": (2, "claude_bin: the headless Claude Code call that reads Slack"),
    "watchdog.py": (2, "claude_bin for the checks of Claude Code itself (its login, its replies)"),
}

Hit = tuple[int, str]


def python_files() -> list[Path]:
    files = [*sorted((ROOT / "lib").glob("*.py")), ROOT / "libexec" / "bosun-resolve"]
    return [path for path in files if path.name not in OWN_MODULES]


def agent_strings(source: str, filename: str = "<source>") -> list[Hit]:
    """The lines of Python source with a string constant that is exactly an agent's id."""
    tree = ast.parse(source, filename=filename)
    return [
        (node.lineno, repr(node.value))
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in AGENTS
    ]


def is_shell(path: Path) -> bool:
    """Whether a file is shell: a shell shebang or a `# shellcheck shell=` line, not Python."""
    head = path.read_text(encoding="utf-8").splitlines()[:3]
    if head and head[0].startswith("#!"):
        return "sh" in head[0] and "python" not in head[0]
    return any(re.match(r"#\s*shellcheck\s+shell=", line) for line in head)


def shell_files() -> list[Path]:
    files = sorted((ROOT / "lib").glob("*.sh")) + sorted(
        path for path in (ROOT / "libexec").iterdir() if path.is_file()
    )
    return [path for path in files if is_shell(path)]


def shell_pattern() -> re.Pattern[str]:
    """A case arm that is an agent's id (also among alternatives), or a comparison with one."""
    ids = "|".join(re.escape(name) for name in AGENTS)
    word = rf"(?:{ids}|\"(?:{ids})\"|'(?:{ids})')"
    alternative = r"[\w\"'*$.-]+\s*\|\s*"
    arm = rf"^\s*(?:{alternative})*{word}\s*(?:\|[^)]*)?\)"
    compare = rf"\s(?:!=|==|=)\s*{word}(?![\w-])"
    return re.compile(f"{arm}|{compare}")


HEREDOC = re.compile(r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|\\?(\w+))")


class LineScan:
    """One line of shell, read a step at a time: the quote and the `(( ))` depth it leaves open, and the
    here-documents it starts (an `<<` outside quotes and arithmetic, not `<<<`)."""

    def __init__(self, quote: str, depth: int) -> None:
        self.quote = quote
        self.depth = depth
        self.delimiters: list[str] = []

    def inside_quotes(self, char: str) -> int:
        if char == self.quote:
            self.quote = ""
        return 2 if char == "\\" and self.quote == '"' else 1

    def heredoc(self, line: str, i: int) -> int:
        found = HEREDOC.match(line, i)
        if found and not self.depth:
            self.delimiters.append(next(group for group in found.groups() if group))
        return 2

    def arithmetic(self, pair: str) -> int:
        if pair == "((" or self.depth:
            self.depth += 1 if pair == "((" else -1
            return 2
        return 1

    def outside_quotes(self, line: str, i: int) -> int:
        """How many characters it takes in, or 0 when the rest of the line is a comment."""
        char, pair = line[i], line[i : i + 2]
        if char == "#" and (i == 0 or line[i - 1] in " \t"):
            return 0
        if pair in {"((", "))"}:
            return self.arithmetic(pair)
        if pair == "<<":
            return 3 if line.startswith("<<<", i) else self.heredoc(line, i)
        if char in "'\"":
            self.quote = char
        return 2 if char == "\\" else 1


def scan_line(line: str, quote: str, depth: int) -> tuple[str, str, int, list[str]]:
    """A line's code without its comment, then the quote and depth it leaves open and the here-document
    endings it asks for."""
    scan = LineScan(quote, depth)
    i = 0
    while i < len(line):
        step = scan.inside_quotes(line[i]) if scan.quote else scan.outside_quotes(line, i)
        if step == 0:
            break
        i += step
    return line[:i], scan.quote, scan.depth, scan.delimiters


def shell_code(text: str) -> tuple[list[Hit], list[str]]:
    """The code lines of shell text: no comments and no text of a here-document. And what stopped the
    scan being sure of the rest: a here-document or a quote still open at the end."""
    lines: list[Hit] = []
    pending: list[tuple[int, str]] = []  # the here-documents whose text comes next: (opened on, ends with)
    quote, depth, quote_line = "", 0, 0
    for number, line in enumerate(text.splitlines(), 1):
        if pending:
            if line.strip() == pending[0][1]:
                pending.pop(0)
            continue
        was_open = quote
        code, quote, depth, delimiters = scan_line(line, quote, depth)
        quote_line = number if quote and not was_open else quote_line
        if code.strip():
            lines.append((number, code))
        pending.extend((number, delimiter) for delimiter in delimiters)
    problems = [f"{number}: the here-document ending with {end!r} is not closed" for number, end in pending]
    if quote:
        problems.append(f"{quote_line}: a {quote} quote is not closed")
    return lines, problems


def shell_hits(text: str) -> tuple[list[Hit], list[str]]:
    pattern = shell_pattern()
    lines, problems = shell_code(text)
    return [(number, code.strip()) for number, code in lines if pattern.search(code)], problems


def judged(hits: dict[str, list[Hit]]) -> list[str]:
    """What is wrong with the hits per file: any in a file not allowed them, and a different count in
    one that is."""
    found = []
    for name, spots in hits.items():
        listed = f"{name}: " + ", ".join(f"line {line} {what}" for line, what in spots)
        if name not in ALLOWED and spots:
            found.append(f"{listed} (an agent's id in shared code: move it into the agent's module)")
        elif name in ALLOWED and len(spots) != ALLOWED[name][0]:
            found.append(f"{name}: allowed {ALLOWED[name][0]} places, found {len(spots)}: {listed}")
    return found


class SharedCodeNamesNoAgentTest(unittest.TestCase):
    def test_shared_python_names_no_agent(self) -> None:
        hits = {
            path.name: agent_strings(path.read_text(encoding="utf-8"), str(path)) for path in python_files()
        }
        self.assertEqual([], judged(hits))

    def test_shared_shell_names_no_agent(self) -> None:
        shared = [path for path in shell_files() if path.parent.name not in {"launch", "harnesses"}]
        hits, unsure = {}, []
        for path in shared:
            hits[path.name], problems = shell_hits(path.read_text(encoding="utf-8"))
            unsure += [f"{path.name}:{problem}" for problem in problems]
        self.assertEqual([], unsure, "the scan cannot tell what the rest of the file holds")
        self.assertEqual([], judged(hits))

    def test_every_allowed_file_is_checked(self) -> None:
        checked = {path.name for path in python_files()} | {path.name for path in shell_files()}
        self.assertEqual([], sorted(set(ALLOWED) - checked))


class TheChecksFindWhatTheyLookForTest(unittest.TestCase):
    def shell(self, text: str) -> list[Hit]:
        hits, problems = shell_hits(text)
        self.assertEqual([], problems)
        return hits

    def test_the_patterns_find_arms_and_comparisons(self) -> None:
        pattern = shell_pattern()
        for agent in AGENTS:
            for line in (
                f"  {agent})",
                f"  other | {agent})",
                f"  {agent} | other)",
                f'[ "$h" = {agent} ]',
                f'[[ $h == "{agent}" ]]',
                f"[ $h != '{agent}' ]",
            ):
                self.assertTrue(pattern.search(line), line)
        for line in ('echo "claude code"', "ORCHESTRATOR_HARNESS=claude", "claude-extra)"):
            self.assertFalse(pattern.search(line), line)

    def test_comments_are_not_code(self) -> None:
        agent = next(iter(AGENTS))
        text = f"# [ $h = {agent} ]\n    # [ $h = {agent} ]\nx=1 # [ $h = {agent} ]\n[ $h = {agent} ]\n"
        self.assertEqual([(4, f"[ $h = {agent} ]")], self.shell(text))

    def test_a_here_document_body_is_not_code(self) -> None:
        agent = next(iter(AGENTS))
        text = f"cat <<'EOF'\n  {agent})\nEOF\n  {agent})\ncat <<-END\n  {agent})\n\tEND\n"
        self.assertEqual([(4, f"{agent})")], self.shell(text))

    def test_two_here_documents_on_a_line(self) -> None:
        agent = next(iter(AGENTS))
        text = f"f <<A <<B\n  {agent})\nA\n  {agent})\nB\n  {agent})\n"
        self.assertEqual([(6, f"{agent})")], self.shell(text))

    def test_a_quoted_or_arithmetic_or_here_string_marker_starts_no_here_document(self) -> None:
        agent = next(iter(AGENTS))
        for opener in (
            "die \"pass it in a quoted heredoc (- <<'EOF')\"",
            "echo '<<EOF'",
            "x=$((1<<2))",
            "(( x = 1 << 2 ))",
            "read -r a <<<EOF",
            'read -r a <<<"$b"',
        ):
            with self.subTest(opener=opener):
                self.assertEqual([(2, f"{agent})")], self.shell(f"{opener}\n  {agent})\n"))

    def test_an_unterminated_here_document_is_reported(self) -> None:
        hits, problems = shell_hits("cat <<'EOF'\nbody\n")
        self.assertEqual([], hits)
        self.assertEqual(["1: the here-document ending with 'EOF' is not closed"], problems)

    def test_an_unterminated_quote_is_reported(self) -> None:
        _, problems = shell_hits('echo "a\nb\n')
        self.assertEqual(['1: a " quote is not closed'], problems)

    def test_the_tail_of_a_real_script_is_scanned(self) -> None:
        text = (ROOT / "libexec" / "bosun-send").read_text(encoding="utf-8")
        lines, problems = shell_code(text)
        self.assertEqual([], problems)
        last_code = [
            number
            for number, line in enumerate(text.splitlines(), 1)
            if line.strip() and not line.strip().startswith("#")
        ][-1]
        self.assertIn(last_code, {number for number, _ in lines})

    def test_python_strings_equal_to_an_id_are_reported(self) -> None:
        agent = next(iter(AGENTS))
        source = f'x = "{agent}"\ny = "{agent} code"\n# z = "{agent}"\nw = f"{{x}}"\n'
        self.assertEqual([(1, repr(agent))], agent_strings(source))


if __name__ == "__main__":
    unittest.main()
