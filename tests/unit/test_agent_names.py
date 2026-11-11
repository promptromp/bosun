"""Shared code names no agent: what differs between agents is in each agent's own module or profile."""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from agent_state import AGENTS


ROOT = Path(__file__).resolve().parents[2]


# The agents' own modules, and the table that lists them, may name them.
OWN_MODULES = {
    "claude_state.py",
    "codex_state.py",
    "connectors_claude.py",
    "connectors_codex.py",
    "agent_state.py",
}

# Files that hold something only Claude Code has, each with what it is.
ALLOWED: dict[str, str] = {
    "init_detect.py": "the claude executable that `claude auth status` asks, the orchestrator's login",
    "init_plan.py": "REQUIRED_TOOLS: claude, which the orchestrator runs on",
    "learn.py": "claude_bin: the headless Claude Code call that does the analysis",
    "login.py": "the login checks of Claude Code, which the orchestrator and the check-in run on",
    "slack_watch.py": "claude_bin: the headless Claude Code call that reads Slack",
    "watchdog.py": "claude_bin for the checks of Claude Code itself (its login, its replies)",
}


def python_files() -> list[Path]:
    files = [*sorted((ROOT / "lib").glob("*.py")), ROOT / "libexec" / "bosun-resolve"]
    return [path for path in files if path.name not in OWN_MODULES]


def agent_strings(path: Path) -> list[tuple[int, str]]:
    """The lines of a Python file with a string constant that is exactly an agent's id."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        (node.lineno, node.value)
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


def shell_code(path: Path) -> list[tuple[int, str]]:
    """The code lines of a shell file: no comments and no text of a here-document."""
    lines: list[tuple[int, str]] = []
    ending: str | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if ending is not None:
            if line.strip() == ending:
                ending = None
            continue
        code = re.sub(r"(^|\s)#.*$", "", line)
        if code.strip():
            lines.append((number, code))
        here = re.search(r"<<-?\s*[\"']?(\w+)[\"']?", code)
        if here:
            ending = here.group(1)
    return lines


def shell_hits(path: Path) -> list[tuple[int, str]]:
    pattern = shell_pattern()
    return [(number, code.strip()) for number, code in shell_code(path) if pattern.search(code)]


class SharedCodeNamesNoAgentTest(unittest.TestCase):
    def test_shared_python_names_no_agent(self) -> None:
        hits = {path.name: agent_strings(path) for path in python_files()}
        found = [
            f"{name}:{line}: {value!r}"
            for name, spots in hits.items()
            if name not in ALLOWED
            for line, value in spots
        ]
        self.assertEqual([], found, "an agent's id in shared code: move it into the agent's module")
        stale = [name for name in ALLOWED if name.endswith(".py") and not hits.get(name)]
        self.assertEqual([], stale, "listed in ALLOWED but names no agent any more")

    def test_shared_shell_names_no_agent(self) -> None:
        shared = [path for path in shell_files() if path.parent.name not in {"launch", "harnesses"}]
        hits = {path.name: shell_hits(path) for path in shared}
        found = [
            f"{name}:{line}: {code}"
            for name, spots in hits.items()
            if name not in ALLOWED
            for line, code in spots
        ]
        self.assertEqual([], found, "an agent's id in shared shell: move it into the agent's profile")
        stale = [name for name in ALLOWED if not name.endswith(".py") and not hits.get(name)]
        self.assertEqual([], stale, "listed in ALLOWED but names no agent any more")

    def test_the_checks_find_what_they_look_for(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
