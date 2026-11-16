"""The fictional acme world of the Mac VM's live suite (tests/sandbox/live/).

Its ontology (knowledge.jsonl) imports with no refusal and, once seed_knowledge's confirmations are
made, holds the facts the scenarios need. Its three repositories get their scripted history
(history.sh), and their tests pass but the one planted failure that live.sh's hand-over has the real
agents fix. The gh stand-in names the repository of the working copy it runs in. Nothing here
touches this Mac's bosun, git settings or ~/.claude."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from test_knowledge import FIXTURE, KnowledgeTestCase


ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "tests" / "sandbox" / "live"
WORLD = LIVE / "knowledge.jsonl"
REPOS = ("exporter", "pdf-renderer", "ledger-sync")
GH = LIVE / "gh"
# The smoke suite counts the shared fixture: it never changes.
FIXTURE_SHA256 = (
    "daa0ef519f838ac7f37bc2cce220c65a2bb50791f1c01bfbbe0db4e15849ed1a"  # pragma: allowlist secret
)


def records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class WorldKnowledgeTest(KnowledgeTestCase):
    """The import and the confirmations of live.sh's seed_knowledge, in a scratch knowledge base."""

    def seed_world(self) -> None:
        """What seed_knowledge does: both files imported and auto-confirmed, then every fact still waiting
        confirmed, found as it finds them (kg facts <entity> --status proposed, for each entity)."""
        for path in (FIXTURE, WORLD):
            self.run_kg("import", str(path), "--apply", "--auto-confirm")
        entities = [r["id"] for path in (FIXTURE, WORLD) for r in records(path) if r["type"] == "entity"]
        waiting: set[str] = set()
        for entity in entities:
            facts = json.loads(self.run_kg("facts", entity, "--status", "proposed", "--json"))
            waiting.update(fact["id"] for fact in facts)
        self.run_kg("confirm", *sorted(waiting), "--note", "the sandbox's world")

    def confirmed(self, subject: str, predicate: str, text: str) -> list[dict[str, Any]]:
        """The confirmed facts of <subject> and <predicate> naming <text> (as object or in its value)."""
        return [
            f
            for f in self.kb().facts.values()
            if (f["subject"], f["predicate"], f["status"]) == (subject, predicate, "confirmed")
            and text in f"{f.get('object') or ''} {f.get('value') or ''}"
        ]

    def test_the_world_imports_with_no_refusal(self) -> None:
        self.run_kg("import", str(FIXTURE), "--apply", "--auto-confirm")
        out = self.run_kg("import", str(WORLD))  # a dry run, after the suite's own import
        self.assertIn("refused: none", out)
        self.assertNotIn("refusals:", out)
        facts = [r for r in records(WORLD) if r["type"] == "fact"]
        self.assertIn(len(facts), range(28, 36))  # about thirty

    def test_the_suites_import_is_left_as_it_is(self) -> None:
        """The smoke suite counts the shared fixture's facts: the world adds to it and changes none."""
        self.run_kg("import", str(FIXTURE), "--apply", "--auto-confirm")
        before = {fact_id: dict(f) for fact_id, f in self.kb().facts.items()}
        self.run_kg("import", str(WORLD), "--apply", "--auto-confirm")
        after = self.kb().facts
        for fact_id, fact in before.items():
            self.assertEqual(after[fact_id]["subject"], fact["subject"])
            self.assertEqual(after[fact_id]["predicate"], fact["predicate"])
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), FIXTURE_SHA256)

    def test_nothing_waits_after_the_seed(self) -> None:
        self.seed_world()
        self.assertEqual([f["id"] for f in self.kb().facts.values() if f["status"] == "proposed"], [])

    def test_the_exporter_is_found_by_its_repository_name(self) -> None:
        self.seed_world()
        self.assertIn("repo/invoice-exporter", self.run_kg("lookup", "acme/exporter"))
        self.assertIn("repo/ledger-sync", self.run_kg("lookup", "acme/ledger-sync"))

    def test_the_people_and_who_owns_what(self) -> None:
        self.seed_world()
        self.assertTrue(self.confirmed("person/marco", "member_of", "team/platform"))
        self.assertTrue(self.confirmed("person/marco", "owns", "repo/pdf-renderer"))
        self.assertTrue(self.confirmed("person/marco", "reviews", "repo/pdf-renderer"))
        self.assertTrue(self.confirmed("person/marco", "prefers_channel", "channel/acme-prs"))
        self.assertTrue(self.confirmed("person/lena", "role", "QA sign-off for releases"))
        self.assertTrue(self.confirmed("process/release", "actor", "person/lena"))
        self.assertTrue(self.confirmed("person/priya", "reviews", "service/sftp-upload"))
        self.assertTrue(self.confirmed("team/billing", "owns", "repo/invoice-exporter"))
        self.assertTrue(self.confirmed("team/platform", "owns", "repo/ledger-sync"))

    def test_the_process_and_its_rules(self) -> None:
        self.seed_world()
        self.assertTrue(self.confirmed("process/release", "gated_by", "gate/smoke-tests"))
        self.assertTrue(self.confirmed("step/rollout", "gated_by", "gate/canary"))
        steps = [
            f["order"]
            for f in self.kb().facts.values()
            if f["predicate"] == "has_step" and f["subject"] == "process/release"
        ]
        self.assertEqual(len(steps), len(set(steps)))  # no two steps of the release share a number
        self.assertTrue(self.confirmed("process/review", "entry_point", "channel/acme-prs"))
        self.assertTrue(
            self.confirmed("process/review", "rule", "exporter/tax.py needs Dana Example's review")
        )
        self.assertTrue(self.confirmed("process/month-end-freeze", "rule", "last business day of the month"))

    def test_the_product_facts_and_the_trap(self) -> None:
        self.seed_world()
        rule = self.confirmed("repo/invoice-exporter", "rule", "tax rounding needs a test with a credit note")
        self.assertEqual({s["kind"] for s in rule[0]["sources"]} >= {"ledger", "report"}, True)
        self.assertTrue(self.confirmed("feature/credit-notes", "lives_in", "repo/invoice-exporter"))
        self.assertTrue(
            self.confirmed("feature/credit-notes", "caveat", "reuse the invoice sequence per customer")
        )
        self.assertTrue(self.confirmed("feature/csv-export", "lives_in", "repo/invoice-exporter"))
        self.assertTrue(self.confirmed("service/sftp-upload", "rule", "Per-customer keys only"))
        self.assertTrue(self.confirmed("repo/ledger-sync", "caveat", "429"))
        self.assertTrue(self.confirmed("repo/ledger-sync", "depends_on", "repo/invoice-exporter"))
        self.assertTrue(self.confirmed("environment/staging", "purpose", "before production"))
        # The one place that says staging-2 is retired is the ontology: config/environments.toml does not.
        self.assertTrue(self.confirmed("environment/staging-2", "status", "retired"))
        self.assertTrue(self.confirmed("lesson/retired-env", "states", "retired environment"))
        self.assertEqual(self.confirmed("feature/csv-export", "rule", ""), [])  # scenario 4 states it

    def test_the_context_pack_of_the_exporter_carries_the_rules(self) -> None:
        self.seed_world()
        pack = self.run_kg("context", "--for", "acme/exporter")
        self.assertIn("tax rounding", pack)
        self.assertIn("round per line", pack)

    def test_the_context_pack_of_the_exporter_warns_of_the_retired_environment(self) -> None:
        self.seed_world()
        pack = self.run_kg("context", "--for", "acme/exporter")
        self.assertIn("staging-2", pack.lower().replace("staging 2", "staging-2"))
        self.assertRegex(pack.lower(), r"retired environment|never point new test runs")
        self.assertLess(len(pack), 6000)


class WorldRepoTest(unittest.TestCase):
    """Each repository as history.sh builds it, in a scratch folder with git told nothing of this Mac."""

    maxDiff = None
    tmp: Path
    env: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(cls.tmp / "home"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        (cls.tmp / "home").mkdir()

    def build(self, name: str) -> Path:
        dest = Path(tempfile.mkdtemp(dir=self.tmp)) / name
        done = subprocess.run(
            ["bash", str(LIVE / "history.sh"), name, str(dest)],
            capture_output=True,
            text=True,
            env=self.env,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return dest

    def git(self, repo: Path, *argv: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(repo), *argv],
            capture_output=True,
            text=True,
            env=self.env,
            stdin=subprocess.DEVNULL,
            check=True,
        )
        return str(done.stdout)

    def test_each_has_a_short_history_by_different_people(self) -> None:
        for name in REPOS:
            with self.subTest(name):
                log = self.git(
                    self.build(name), "log", "--format=%an|%ae|%cn|%ad|%s", "--date=format:%Y"
                ).splitlines()
                self.assertIn(len(log), range(4, 9))
                authors = {line.split("|")[0] for line in log}
                self.assertGreaterEqual(len(authors), 2)
                for line in log:
                    author, email, committer, year, subject = line.split("|")
                    self.assertEqual(committer, author)
                    self.assertTrue(email.endswith("@example.com"), email)
                    self.assertEqual(year, "2026")
                    self.assertTrue(subject)

    def test_the_history_is_the_same_every_time(self) -> None:
        for name in REPOS:
            with self.subTest(name):
                one = self.git(self.build(name), "rev-parse", "HEAD")
                two = self.git(self.build(name), "rev-parse", "HEAD")
                self.assertEqual(one, two)

    def test_it_ends_as_the_folder_is(self) -> None:
        for name in REPOS:
            with self.subTest(name):
                repo = self.build(name)
                self.assertEqual(self.git(repo, "status", "--porcelain"), "")
                listed = set(self.git(repo, "ls-files").splitlines())
                held = {
                    str(p.relative_to(LIVE / name))
                    for p in (LIVE / name).rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts
                }
                self.assertEqual(listed, held)
                for path in held:
                    self.assertEqual((repo / path).read_bytes(), (LIVE / name / path).read_bytes())

    def test_each_has_a_readme_and_its_owners(self) -> None:
        owners = {
            "exporter": "* @acme/billing",
            "pdf-renderer": "* @marco-placeholder",
            "ledger-sync": "* @acme/platform",
        }
        for name in REPOS:
            with self.subTest(name):
                repo = LIVE / name
                self.assertGreater(len((repo / "README.md").read_text().splitlines()), 4)
                self.assertIn(owners[name], (repo / "CODEOWNERS").read_text().splitlines())
        self.assertIn("exporter/tax.py @dana-example", (LIVE / "exporter" / "CODEOWNERS").read_text())
        self.assertIn("render(invoice)", (LIVE / "pdf-renderer" / "README.md").read_text())
        self.assertIn("month-end freeze", (LIVE / "ledger-sync" / "RUNBOOK.md").read_text())

    def run_tests(self, repo: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "unittest"],
            cwd=repo,
            capture_output=True,
            text=True,
            env={**self.env, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )

    def test_the_tests_of_the_other_repos_pass(self) -> None:
        for name in ("pdf-renderer", "ledger-sync"):
            with self.subTest(name):
                done = self.run_tests(self.build(name))
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_exporter_fails_only_on_its_planted_bug(self) -> None:
        done = self.run_tests(self.build("exporter"))
        self.assertEqual(done.returncode, 1)
        failed = re.findall(r"^(?:FAIL|ERROR): (\w+) ", done.stderr, re.MULTILINE)
        self.assertEqual(failed, ["test_quantity_counts"])
        self.assertRegex(done.stderr, r"10\.0 != 30\.0")

    def test_the_integration_check_leaves_its_marker_for_the_environment_it_ran_against(self) -> None:
        repo = self.build("exporter")
        home = self.tmp / "marker-home"
        home.mkdir()
        for env in ("staging", "staging-2"):
            done = subprocess.run(
                [sys.executable, "scripts/integration_check.py", "--env", env],
                cwd=repo,
                capture_output=True,
                text=True,
                env={**self.env, "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"},
                stdin=subprocess.DEVNULL,
                check=False,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue((home / ".sandbox" / f"integration-{env}").exists())

    def test_the_environments_file_does_not_say_staging_2_is_retired(self) -> None:
        text = (LIVE / "exporter" / "config" / "environments.toml").read_text()
        self.assertIn("[environments.staging-2]", text)
        self.assertNotIn("retired", text)  # only the ontology knows: that is what scenario 2 tests

    def test_the_runbook_has_the_release_path(self) -> None:
        text = (LIVE / "exporter" / "RUNBOOK.md").read_text()
        for word in ("smoke", "staging", "canary", "deploy bot", "#deploys", "Hotfix"):
            self.assertIn(word.lower(), text.lower())

    def test_the_gh_stand_in_names_the_repository_of_the_working_copy(self) -> None:
        home = self.tmp / "gh-home"
        home.mkdir()
        for name in REPOS:
            repo = self.build(name)
            self.git(repo, "remote", "add", "origin", f"https://github.com/acme/{name}.git")
            done = subprocess.run(
                ["bash", str(GH), "pr", "create", "--title", "x"],
                cwd=repo,
                capture_output=True,
                text=True,
                env={**self.env, "HOME": str(home)},
                stdin=subprocess.DEVNULL,
                check=False,
            )
            self.assertRegex(done.stdout, rf"^https://github\.com/acme/{name}/pull/\d+$")
        done = subprocess.run(
            ["bash", str(GH), "pr", "create", "--repo", "acme/ledger-sync"],
            cwd=self.tmp,
            capture_output=True,
            text=True,
            env={**self.env, "HOME": str(home)},
            stdin=subprocess.DEVNULL,
            check=False,
        )
        self.assertIn("acme/ledger-sync/pull/", done.stdout)


class WorldIsFictionalTest(unittest.TestCase):
    def test_only_made_up_people_and_addresses(self) -> None:
        text = ""
        for path in [
            WORLD,
            *LIVE.glob("history.sh"),
            *(p for r in REPOS for p in (LIVE / r).rglob("*") if p.is_file()),
        ]:
            if "__pycache__" not in path.parts:
                text += path.read_text()
        for email in set(re.findall(r"[\w.+-]+@[\w.-]+\.\w+", text)):
            self.assertTrue(email.endswith("@example.com"), email)
        for handle in set(re.findall(r"(?<![\w/])@([a-z][\w-]*)", text)):
            self.assertIn(handle, {"dana-example", "marco-placeholder", "acme"}, handle)
        for host in set(re.findall(r"https?://([\w.-]+)", text)):
            self.assertTrue(host.endswith((".example", "github.com")), host)


if __name__ == "__main__":
    unittest.main()
