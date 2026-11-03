"""Replays of orchestrator and repo-session interactions (rewritten as fiction, in a
fictional domain: acme's invoice exporter), from tests/fixtures/corpus/:

- orchestrator_commands.json: every Bash command two orchestrator conversations ran
  (2026-10-15/16), with the guard's decision before and after the ledger protocol. The guard
  must decide each as recorded in "after"; only raw `bosun send` calls changed.
- replay/*.json: interaction sequences (delegations, updates, reports, refusals) run against the
  real CLI in a scratch state dir with BOSUN_SEND_DRY_RUN=1, so nothing is delivered. Steps
  by the orchestrator go through the guard first (a denied step is not run, as in the hook);
  steps by a repo session or by me do not. The ledger must end as each scenario expects, and be
  the rebuild of the journal its writes went through. Task and outreach ids are minted, so a
  scenario names them by the order they were made: $OUT1, $OUT2 ... and $TASK1 ..., set for the
  commands after the one that made them, and filled in the expected output and items.
- interactions.json: the catalog of interaction patterns, with counts; every pattern points at
  the replay scenarios or guard commands that cover it.
"""

from __future__ import annotations

import json
import os
import re
import string
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import guard
import ledger
from change_log import canonical
from journal import Journal
from ledger_changes import rebuild, state_hash


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "fixtures" / "corpus"
STEP_TIMEOUT_SECONDS = 30


def load(name: str) -> Any:
    return json.loads((CORPUS / name).read_text())


REAL_COMMANDS = {row["id"]: row["command"] for row in load("orchestrator_commands.json")}
# Replayed commands must not reach GitHub, tmux or Claude (send only logs, under dry run).
OUTSIDE_WORLD = re.compile(r"(?m)(?:^|[;&|(]\s*|\bdo\s+)(?:gh|tmux|claude|git)\s")
# What reach-out and spawn print when they make an item: its minted id.
MADE = re.compile(r"(?m)^created: (?P<id>(?P<kind>out|task):\S+) \[")
MADE_NAMES = {"out": "OUT", "task": "TASK"}


def guard_decision(command: str) -> str:
    decision = guard.decide({"tool_name": "Bash", "tool_input": {"command": command}})
    return decision["permissionDecision"] if decision else "allow"


def lines(value: str | list[str]) -> str:
    """Scenario text: a string, or a list of lines (so heredocs stay readable in JSON)."""
    return "\n".join(value) if isinstance(value, list) else value


class GuardCorpusTest(unittest.TestCase):
    def test_each_real_command_gets_its_recorded_decision(self) -> None:
        for row in load("orchestrator_commands.json"):
            with self.subTest(id=row["id"], command=row["command"][:80]):
                self.assertEqual(guard_decision(row["command"]), row["after"])

    def test_only_raw_sends_changed(self) -> None:
        changed = [row for row in load("orchestrator_commands.json") if row["before"] != row["after"]]
        self.assertTrue(changed)
        for row in changed:
            with self.subTest(id=row["id"]):
                self.assertEqual((row["before"], row["after"]), ("allow", "deny"))
                self.assertRegex(row["command"], r"(?m)^\s*bosun send |\| bosun send ")


class Scratch:
    """A scratch HOME, config and state dir; `bosun` on PATH is this checkout's."""

    def __init__(self, base: Path) -> None:
        bin_dir = base / "home" / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "bosun").symlink_to(ROOT / "bin" / "bosun")
        (base / "config").mkdir()
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(base / "home"),
            "BOSUN_CONFIG_DIR": str(base / "config"),
            "BOSUN_STATE_DIR": str(base / "state"),
            "BOSUN_LOG_DIR": str(base / "state" / "logs"),
            "BOSUN_SESSION_NAME": "replay",
            "BOSUN_TMUX_SOCKET": f"bosun-replay-{os.getpid()}",
            "BOSUN_SEND_DRY_RUN": "1",
            "BOSUN_REPO_MASK": "acme/*",
        }
        self.made: dict[str, str] = {}  # $OUT1, $TASK1 ...: the ids made, in order

    def note_made(self, output: str) -> None:
        """Each outreach or task <output> says was made, as $OUT<n> or $TASK<n>."""
        for found in MADE.finditer(output):
            name = MADE_NAMES[found["kind"]]
            self.made[f"{name}{sum(key.startswith(name) for key in self.made) + 1}"] = found["id"]
        self.env.update(self.made)

    def fill(self, text: str) -> str:
        """<text> with the ids it names as $OUT1, ${TASK1} ... filled in."""
        return string.Template(text).safe_substitute(self.made)

    def run(self, command: str) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(
            ["bash", "-c", command],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT_SECONDS,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        self.note_made(done.stdout)
        return done

    def item(self, item_id: str) -> dict[str, Any]:
        shown = self.run(f"bosun ledger show '{item_id}'")
        if shown.returncode != 0:
            raise AssertionError(f"no item {item_id}: {shown.stderr}")
        item: dict[str, Any] = json.loads(shown.stdout)
        return item


class ReplayTest(unittest.TestCase):
    def test_scenarios(self) -> None:
        scenarios = sorted((CORPUS / "replay").glob("*.json"))
        self.assertGreaterEqual(len(scenarios), 5)
        # Each scenario has its own scratch dir, so they run in parallel (the CLI is slow to start).
        with ThreadPoolExecutor(max_workers=len(scenarios)) as pool:
            failures = list(pool.map(self.replay_file, scenarios))
        for path, failure in zip(scenarios, failures, strict=True):
            with self.subTest(scenario=path.stem):
                if failure:
                    raise failure

    def replay_file(self, path: Path) -> AssertionError | None:
        try:
            with tempfile.TemporaryDirectory() as base:
                self.replay(json.loads(path.read_text()), Scratch(Path(base)))
        except AssertionError as failure:
            return failure
        return None

    def replay(self, scenario: dict[str, Any], scratch: Scratch) -> None:
        for command in scenario.get("setup", []):
            done = scratch.run(lines(command))
            self.assertEqual(done.returncode, 0, f"setup {command}: {done.stderr}")
        for number, step in enumerate(scenario["steps"], 1):
            self.replay_step(f"step {number} ({step.get('why', '')})", step, scratch)
        for item_id, expected in scenario["expect"].items():
            self.check_item(scratch.fill(item_id), expected, scratch.item(scratch.fill(item_id)))
        self.check_journal(Path(scratch.env["BOSUN_STATE_DIR"]))

    def check_journal(self, state: Path) -> None:
        """ledger.json is the rebuild of the journal, at its position, with no conflict."""
        found = Journal(state / "journal" / "ledger", "ledger", "", ledger.RULES_VERSION)
        result = rebuild(found.changes(), ledger.RULES)
        saved = json.loads((state / "ledger.json").read_text())
        self.assertTrue(found.position(), "the journal is empty")
        self.assertEqual(ledger.saved_position(saved), found.position(), "the journal position")
        self.assertEqual(saved["journal"]["digest"], state_hash(saved), "the digest of what it holds")
        self.assertEqual(result.conflicts, [], "conflicts in the rebuild")
        del saved["journal"]
        self.assertEqual(canonical(result.ledger).decode(), canonical(saved).decode(), "the rebuild")

    def replay_step(self, label: str, step: dict[str, Any], scratch: Scratch) -> None:
        command = REAL_COMMANDS[step["command_id"]] if "command_id" in step else lines(step["run"])
        self.assertIsNone(OUTSIDE_WORLD.search(command), f"{label}: replays must stay offline")
        if step["actor"] == "orchestrator":
            decision = guard_decision(command)
            self.assertEqual(decision, step.get("guard", "allow"), f"{label}: guard")
            if decision == "deny":
                return
        done = scratch.run(command)
        output = done.stdout + done.stderr
        self.assertEqual(done.returncode, step.get("rc", 0), f"{label}: exit status; output:\n{output}")
        for text in step.get("out", []):
            self.assertIn(scratch.fill(text), output, f"{label}: output")
        for text in step.get("not_out", []):
            self.assertNotIn(scratch.fill(text), output, f"{label}: output")

    def check_item(self, item_id: str, expected: dict[str, Any], item: dict[str, Any]) -> None:
        for key, value in expected.items():
            self.assertEqual(observed(item, key, value), value, f"{item_id}: {key}")


def observed(item: dict[str, Any], key: str, expected: Any) -> Any:
    """What <item> shows for an expectation <key>, shaped like <expected>."""
    if key == "events":
        events = [entry["event"] for entry in item["history"]]
        return {event: events.count(event) for event in expected}
    if key == "fields":
        return {name: item["fields"].get(name) for name in expected}
    delegation = item.get("delegation") or {}
    report = item.get("report") or {}
    return {
        "state": item["state"],
        "window": delegation.get("window"),
        "updates": delegation.get("updates"),
        "report_summary": report.get("summary"),
        "report_open": report.get("open"),
        "report_results": report.get("results"),
    }[key]


class CatalogTest(unittest.TestCase):
    def test_every_pattern_is_counted_and_covered(self) -> None:
        catalog = load("interactions.json")
        scenarios = {path.stem for path in (CORPUS / "replay").glob("*.json")}
        commands = {row["id"] for row in load("orchestrator_commands.json")}
        for pattern in catalog["patterns"]:
            with self.subTest(pattern=pattern["id"]):
                self.assertGreater(pattern["count"], 0)
                self.assertTrue(pattern["protocol"])
                self.assertLessEqual(set(pattern.get("replay", [])), scenarios)
                self.assertLessEqual(set(pattern.get("guard", [])), commands)
                self.assertTrue(pattern.get("replay") or pattern.get("guard") or pattern.get("not_covered"))


class FixtureHygieneTest(unittest.TestCase):
    """The fixtures come from real sessions: only placeholders may remain."""

    SUSPICIOUS = (
        re.compile(r"\b[CDUGW](?!0000000)[0-9A-Z]{8,11}\b"),  # Slack ids other than placeholders
        re.compile(r"[\w.+-]+@(?!example\.com)[\w-]+\.[\w.]+"),  # email addresses
        re.compile(r"/Users/(?!me/)"),  # home directories
        re.compile(r"\b(?:xox[abp]|ghp|gho|sk)[-_][A-Za-z0-9]{8,}"),  # tokens
        re.compile(r"\b1[67]9\d{7}\.?\d{6}\b"),  # real Slack timestamps (placeholders start 1700)
    )

    def test_no_real_identifiers(self) -> None:
        for path in sorted(CORPUS.rglob("*.json")):
            text = path.read_text()
            for pattern in self.SUSPICIOUS:
                with self.subTest(file=path.name, pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(text))


if __name__ == "__main__":
    unittest.main()
