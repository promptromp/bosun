"""The smoke suite's sections (tests/smoke.sh): `--list` and `--only` go by the `section <id> "<name>"`
markers, so a section added without one would never run alone, and one with a taken id would be run by
the wrong name. Checked here, without running the suite: every marker parses, ids are unique and kebab-case,
a `needs` names a section above it, `--list` prints what the markers say, an unknown id is a usage error,
and each section on its own (and the setup alone) leaves a script that parses, which is what shows that a
marker sits between whole commands."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "tests" / "smoke.sh"
MARKER = re.compile(r'^\s*section\s+(?P<id>\S+)\s+"(?P<name>[^"]*)"(?:\s+needs\s+(?P<needs>\S+))?\s*$')
ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HEADER = re.compile(r'^\s*echo "[^"$\\]*"$')  # how a section's heading was printed before the markers


def marker_lines() -> list[str]:
    """The lines that start a section: `section` at the start of a line, once a space follows it
    (`section() {` is the function)."""
    return [line for line in SMOKE.read_text().splitlines() if re.match(r"^\s*section\s", line)]


def sections() -> list[tuple[str, str, list[str]]]:
    found = []
    for line in marker_lines():
        match = MARKER.match(line)
        if match is None:
            raise AssertionError(f"not a section marker: {line}")
        needs = match["needs"].split(",") if match["needs"] else []
        found.append((match["id"], match["name"], needs))
    return found


def install_block(lines: list[str]) -> range:
    """The lines inside the `if` that guards the install tests, up to its `always_run`."""
    guard = next(
        i for i, line in enumerate(lines) if line.startswith("if ") and "command -v launchctl" in line
    )
    return range(guard + 1, lines.index("  always_run", guard))


def headings_here(line: str, in_install_block: bool) -> bool:
    """Whether a heading-shaped line is where a heading was: the left margin, or two spaces in the block."""
    return not line.startswith(" ") or (in_install_block and not line.startswith("   "))


def smoke(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(SMOKE), *args],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


class SectionMarkersTest(unittest.TestCase):
    def test_every_marker_parses_and_there_are_some(self) -> None:
        self.assertGreater(len(sections()), 50)

    def test_ids_are_unique_kebab_case_words(self) -> None:
        ids = [found[0] for found in sections()]
        self.assertEqual([i for i in ids if not ID.match(i)], [])
        self.assertEqual(sorted({i for i in ids if ids.count(i) > 1}), [], "ids that are taken twice")

    def test_a_needs_names_a_section_above_it(self) -> None:
        seen: list[str] = []
        for found_id, _, needs in sections():
            for need in needs:
                self.assertIn(need, seen, f"{found_id} needs {need}, which is not a section above it")
            seen.append(found_id)

    def test_no_heading_is_printed_by_hand_any_more(self) -> None:
        """A heading printed by an `echo` of its own is a section that --only cannot name: none at the
        left margin, and none at two spaces inside the install tests' `if`, where the sub-sections are."""
        lines = SMOKE.read_text().splitlines()
        inside = install_block(lines)
        by_hand = [
            line for i, line in enumerate(lines) if HEADER.match(line) and headings_here(line, i in inside)
        ]
        self.assertEqual(by_hand, [], 'write it as `section <id> "<name>"`')

    def test_list_prints_what_the_markers_say(self) -> None:
        result = smoke("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        listed = [line.split(None, 1) for line in result.stdout.splitlines()]
        want = [(i, n + (f"  (needs {','.join(needs)})" if needs else "")) for i, n, needs in sections()]
        self.assertEqual([(i, rest) for i, rest in listed], want)

    def test_an_unknown_id_is_a_usage_error_that_names_it(self) -> None:
        result = smoke("--only", "resolve,no-such-section")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no-such-section", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_the_options_are_checked(self) -> None:
        for args in (["--only"], ["--only", ""], ["--bogus"], ["resolve"]):
            with self.subTest(args=args):
                self.assertEqual(smoke(*args).returncode, 2)

    def test_what_is_left_of_the_script_parses_whatever_section_is_chosen(self) -> None:
        # The function is the script's own: sourced from its text, so that nothing else of it runs.
        text = SMOKE.read_text()
        start = text.index("filtered_script() {")
        function = text[start : text.index("\n}\n", start) + 3]
        ids = [found[0] for found in sections()]
        loop = (
            f"{function}\n"
            'for id in "$@"; do\n'
            '  filtered_script "$SMOKE" ",$id," | bash -n || { echo "no parse with only $id"; exit 1; }\n'
            "done\n"
            'filtered_script "$SMOKE" ",," | bash -n || { echo "does not parse with no section"; exit 1; }\n'
        )
        result = subprocess.run(
            ["/bin/bash", "-c", loop, "_", *ids],
            env={"PATH": "/usr/bin:/bin", "SMOKE": str(SMOKE)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
