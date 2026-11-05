"""Unit tests for the knowledge base (lib/knowledge*.py, through `bosun kg`'s commands): the
append-only fact log, supersede timelines and --as-of, entity resolution and merge suggestions,
the auto-confirm rules (my quote verified in a transcript, never from a tool result or pasted
text; two trusted sources; profile sources), re-observation and expiry, contradictions and the
other lint findings, retention and delete on request, the review batch (grouping, cap, priority,
one-line decisions), import (dry run first, refusals), context packs (size, citations, nothing
unconfirmed or restricted), locking across processes and the git history.
Fictional data: acme's invoice exporter (tests/fixtures/knowledge/acme-import.jsonl)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import knowledge
import knowledge_model as km
import knowledge_review as kr
import knowledge_store as store
import update_backups as ub
import update_gate
import update_state
from knowledge_model import KnowledgeError
from knowledge_schema import SchemaError


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "knowledge" / "acme-import.jsonl"
START = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
QUOTE = "smoke tests gate every release"


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def typed(text: str, moment: datetime) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": stamp(moment),
        "origin": {"kind": "human"},
        "promptSource": "typed",
        "message": {"role": "user", "content": text},
    }


def tool_result(text: str, moment: datetime) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": stamp(moment),
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": text}],
        },
    }


class KnowledgeTestCase(unittest.TestCase):
    """A scratch knowledge directory (no git unless a test asks) and state folder (this Mac's
    replica id, as the journal uses it), a pinned clock, and a few entities of the acme world."""

    git = False

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "knowledge"
        self.state = self.tmp / "state"
        self.now = START
        knowledge.CLOCK = lambda: self.now
        knowledge.configure(self.root, git=self.git, state_dir=str(self.state))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(setattr, knowledge, "CLOCK", knowledge.clock)

    def run_kg(self, *argv: str) -> str:
        args = knowledge.build_parser().parse_args(list(argv))
        result = args.func(args)
        return str(result[0] if isinstance(result, tuple) else result)

    def later(self, days: float) -> None:
        self.now += timedelta(days=days)

    def seed(self) -> None:
        self.run_kg("add-entity", "process/release", "--name", "Release process", "--alias", "releases")
        self.run_kg("add-entity", "step/merge", "--name", "Merge to main")
        self.run_kg("add-entity", "gate/smoke-tests", "--name", "Smoke tests")
        self.run_kg(
            "add-entity", "repo/invoice-exporter", "--name", "invoice exporter", "--alias", "exporter"
        )
        self.run_kg("add-entity", "environment/staging-2", "--name", "staging 2")
        self.run_kg("add-entity", "person/dana", "--name", "Dana Example")

    def kb(self) -> km.KB:
        return knowledge.load(knowledge.paths())

    def fact(self, fact_id: str) -> dict[str, Any]:
        return self.kb().facts[fact_id]

    def f(self, n: int) -> str:
        """The id of the <n>th fact this test made (ids are minted, not counted: the fact log keeps
        the order they were made in)."""
        return list(self.kb().facts)[n - 1]

    def log_lines(self) -> list[str]:
        return (self.root / "facts.jsonl").read_text().splitlines()

    def transcript(self, *entries: dict[str, Any]) -> str:
        path = self.tmp / "session.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
        return str(path)


class StorageTest(KnowledgeTestCase):
    def test_reading_a_missing_knowledge_base_creates_nothing(self) -> None:
        self.assertIn("0 entities", self.run_kg("stats"))
        self.assertFalse(self.root.exists())

    def test_the_fact_log_only_grows(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "process/release", "has_step", "step/merge", "--source", "ledger:task:2026-10-01/1"
        )
        first = self.log_lines()
        self.run_kg("confirm", self.f(1))
        self.run_kg("supersede", self.f(1), "--close", "--note", "merged into another step")
        lines = self.log_lines()
        self.assertEqual(lines[: len(first)], first)
        self.assertEqual([json.loads(line)["op"] for line in lines], ["add", "update", "update"])
        self.assertEqual(self.fact(self.f(1))["status"], km.CONFIRMED)
        self.assertIsNotNone(self.fact(self.f(1))["valid_to"])

    def test_a_bad_line_is_named_not_silently_skipped(self) -> None:
        """The journal holds every record, so a reader reads its rebuild, and says which line."""
        self.seed()
        with (self.root / "facts.jsonl").open("a") as handle:
            handle.write("{not json\n")
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.assertIn("6 entities, 0 facts", self.run_kg("stats"))
        self.assertRegex(said.getvalue(), r"facts.jsonl:1 is not valid JSON")

    def test_entities_merge_aliases_and_suggest_near_matches(self) -> None:
        self.seed()
        out = self.run_kg("add-entity", "repo/invoice-exporters", "--name", "invoice exporters")
        self.assertIn("may be the same as repo/invoice-exporter", out)
        out = self.run_kg(
            "add-entity", "repo/invoice-exporter", "--alias", "the exporter", "--weak-alias", "billing"
        )
        self.assertIn("updated", out)
        entity = self.kb().entities["repo/invoice-exporter"]
        self.assertEqual(entity["aliases"], {"strong": ["exporter", "the exporter"], "weak": ["billing"]})
        self.assertIn("unchanged", self.run_kg("add-entity", "repo/invoice-exporter", "--alias", "Exporter"))

    def test_a_person_has_no_free_text_summary(self) -> None:
        with self.assertRaisesRegex(KnowledgeError, "people whitelist"):
            self.run_kg("add-entity", "person/lee", "--summary", "great at reviews")


class ResolutionTest(KnowledgeTestCase):
    def test_ids_names_and_strong_aliases_resolve(self) -> None:
        self.seed()
        kb = self.kb()
        for ref in ("process/release", "release", "Release Process", "RELEASES", "  releases "):
            self.assertEqual(km.resolve(kb, ref), "process/release")

    def test_weak_aliases_only_search(self) -> None:
        self.seed()
        self.run_kg("add-entity", "repo/invoice-exporter", "--weak-alias", "billing")
        with self.assertRaises(KnowledgeError):
            km.resolve(self.kb(), "billing")
        self.assertIn("repo/invoice-exporter", self.run_kg("search", "billing"))

    def test_unknown_names_get_suggestions_and_ambiguous_ones_are_refused(self) -> None:
        self.seed()
        with self.assertRaisesRegex(KnowledgeError, "did you mean repo/invoice-exporter"):
            km.resolve(self.kb(), "invoice exportr")
        self.run_kg("add-entity", "repo/exporter-ui", "--alias", "exporter")
        with self.assertRaisesRegex(KnowledgeError, "names several entities"):
            km.resolve(self.kb(), "exporter")


class AutoConfirmTest(KnowledgeTestCase):
    def propose_smoke(self, *sources: str) -> str:
        argv = ["propose", "process/release", "gated_by", "gate/smoke-tests"]
        for source in sources:
            argv += ["--source", source]
        return self.run_kg(*argv)

    def test_my_own_words_confirm_when_the_quote_is_in_a_typed_prompt(self) -> None:
        self.seed()
        transcript = self.transcript(typed(f"remember: {QUOTE}, always", START - timedelta(hours=1)))
        out = self.run_kg(
            "propose",
            "process/release",
            "gated_by",
            "gate/smoke-tests",
            "--from-user",
            "--quote",
            QUOTE,
            "--transcript",
            transcript,
        )
        self.assertIn("confirmed (your own words", out)
        fact = self.fact(self.f(1))
        self.assertEqual(fact["authority"], "user_said")
        self.assertTrue(fact["sources"][0]["verified"])

    def test_a_quote_only_in_a_tool_result_or_pasted_text_does_not_count(self) -> None:
        self.seed()
        for entry in (
            tool_result(f"the wiki says {QUOTE}", START),
            typed(f"[bosun check-in] {QUOTE}", START),
            typed(f"<cross-session-message>{QUOTE}</cross-session-message>", START),
        ):
            with self.subTest(entry=entry["message"]["content"]):
                transcript = self.transcript(entry)
                out = self.run_kg(
                    "propose",
                    "process/release",
                    "gated_by",
                    "gate/smoke-tests",
                    "--from-user",
                    "--quote",
                    QUOTE,
                    "--transcript",
                    transcript,
                )
                self.assertIn("proposed", out)
                self.assertIn("not in anything you typed", out)

    def test_two_trusted_sources_confirm_a_low_risk_fact(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "process/release", "has_step", "step/merge", "--source", "ledger:task:2026-10-01/1"
        )
        self.assertEqual(self.fact(self.f(1))["status"], km.PROPOSED)
        out = self.run_kg(
            "propose", "releases", "has_step", "merge to main", "--source", "report:task:2026-10-01/1"
        )
        self.assertIn(f"{self.f(1)} observed, confirmed (two independent trusted sources)", out)
        self.assertEqual(len(self.fact(self.f(1))["sources"]), 2)

    def test_untrusted_text_never_confirms(self) -> None:
        self.seed()
        self.run_kg(
            "propose",
            "process/release",
            "has_step",
            "step/merge",
            "--source",
            "ledger:task:2026-10-01/1",
            "--source",
            "slack:C0000000001/1.2",
            "--source",
            "pr:acme/invoice-exporter#7",
            "--source",
            "doc:wiki/release",
        )
        self.assertEqual(self.fact(self.f(1))["status"], km.PROPOSED)

    def test_high_risk_and_person_facts_need_more_than_two_sources(self) -> None:
        self.seed()
        self.propose_smoke("ledger:x", "report:y")
        self.assertEqual(self.fact(self.f(1))["status"], km.PROPOSED)
        self.run_kg(
            "propose", "person/dana", "reviews", "exporter", "--source", "ledger:x", "--source", "report:y"
        )
        self.assertEqual(self.fact(self.f(2))["status"], km.PROPOSED)

    def test_a_profile_source_confirms_title_and_timezone_only(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:directory"
        )
        self.assertEqual(self.fact(self.f(1))["status"], km.CONFIRMED)
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "profile:directory")
        self.assertEqual(self.fact(self.f(2))["status"], km.PROPOSED)

    def test_a_conflicting_single_value_waits_for_review(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:directory"
        )
        out = self.run_kg("propose", "person/dana", "timezone", "--value", "UTC", "--source", "profile:hr")
        self.assertIn(f"contradicts {self.f(1)}", out)
        self.assertEqual(self.fact(self.f(2))["status"], km.PROPOSED)

    def test_refusals(self) -> None:
        self.seed()
        with self.assertRaisesRegex(KnowledgeError, "at least one --source"):
            self.run_kg("propose", "process/release", "has_step", "step/merge")
        with self.assertRaisesRegex(KnowledgeError, "kind:ref"):
            self.run_kg("propose", "process/release", "has_step", "step/merge", "--source", "ledger")
        with self.assertRaisesRegex(SchemaError, "people whitelist"):
            self.run_kg("propose", "person/dana", "strengths", "--value", "fast", "--source", "ledger:x")
        with self.assertRaisesRegex(KnowledgeError, "needs --transcript"):
            self.run_kg("propose", "process/release", "has_step", "step/merge", "--from-user", "--quote", "x")


class LifecycleTest(KnowledgeTestCase):
    def test_unreviewed_proposals_expire_unless_observed_again(self) -> None:
        self.seed()
        self.run_kg(
            "propose",
            "repo/invoice-exporter",
            "caveat",
            "--value",
            "PDF snapshots run nightly",
            "--source",
            "memory:ci.md",
        )
        self.later(km.PROPOSAL_DAYS + 1)
        self.assertEqual(km.effective_status(self.fact(self.f(1)), self.now), km.EXPIRED)
        self.assertIn("Nothing to review", self.run_kg("batch"))
        out = self.run_kg(
            "propose",
            "exporter",
            "caveat",
            "--value",
            "PDF  snapshots run NIGHTLY",
            "--source",
            "memory:ci.md",
        )
        self.assertIn(f"{self.f(1)} observed, proposed", out)
        fact = self.fact(self.f(1))
        self.assertEqual(fact["observations"], 2)
        self.assertEqual(km.parse_time(fact["observed_at"]), self.now)
        self.assertEqual(len(fact["sources"]), 1)  # the same source is not added twice

    def test_expire_records_expiry(self) -> None:
        self.seed()
        self.run_kg("propose", "repo/invoice-exporter", "caveat", "--value", "x", "--source", "memory:ci.md")
        self.later(km.PROPOSAL_DAYS + 1)
        self.assertIn("1 expired", self.run_kg("expire", "--dry-run"))
        self.assertEqual(self.fact(self.f(1))["status"], km.PROPOSED)
        self.run_kg("expire")
        self.assertEqual(self.fact(self.f(1))["status"], km.EXPIRED)

    def test_rejected_facts_stay_rejected_unless_I_say_so(self) -> None:
        self.seed()
        self.run_kg("propose", "process/release", "gated_by", "gate/smoke-tests", "--source", "memory:x")
        self.run_kg("reject", self.f(1))
        out = self.run_kg(
            "propose", "process/release", "gated_by", "gate/smoke-tests", "--source", "ledger:y"
        )
        self.assertIn("stays rejected", out)
        transcript = self.transcript(typed(QUOTE, START))
        out = self.run_kg(
            "propose",
            "process/release",
            "gated_by",
            "gate/smoke-tests",
            "--from-user",
            "--quote",
            QUOTE,
            "--transcript",
            transcript,
        )
        self.assertIn(f"{self.f(1)} observed, confirmed", out)


class TimelineTest(KnowledgeTestCase):
    def test_supersede_closes_the_old_fact_and_as_of_sees_both_timelines(self) -> None:
        self.seed()
        self.run_kg(
            "propose",
            "environment/staging-2",
            "status",
            "--value",
            "active",
            "--source",
            "memory:a",
            "--valid-from",
            "2026-09-01",
        )
        self.run_kg("confirm", self.f(1))
        self.later(10)
        out = self.run_kg(
            "supersede", self.f(1), "--value", "retired", "--source", "ledger:task:2026-10-02/3"
        )
        self.assertIn(f"{self.f(1)} superseded by {self.f(2)}", out)
        old, new = self.fact(self.f(1)), self.fact(self.f(2))
        self.assertEqual(old["superseded_by"], self.f(2))
        self.assertEqual(new["status"], km.CONFIRMED)
        now = self.run_kg("facts", "staging 2")
        self.assertIn("retired", now)
        self.assertNotIn("'active'", now)
        before = self.run_kg("facts", "staging-2", "--as-of", "2026-10-25")
        self.assertIn("'active'", before)
        self.assertNotIn("retired", before)
        self.assertIn(self.f(1), self.run_kg("facts", "staging-2", "--closed"))
        with self.assertRaisesRegex(KnowledgeError, "already closed"):
            self.run_kg("supersede", self.f(1), "--close")

    def test_confirming_a_single_valued_fact_supersedes_the_current_value(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:directory"
        )
        self.run_kg("propose", "person/dana", "timezone", "--value", "UTC", "--source", "profile:hr")
        out = self.run_kg("confirm", self.f(2))
        self.assertIn(f"{self.f(1)} superseded by {self.f(2)}", out)
        self.assertEqual(self.fact(self.f(1))["valid_to"], km.iso(self.now))

    def test_a_correction_in_my_words_needs_the_quote(self) -> None:
        self.seed()
        self.run_kg("propose", "environment/staging-2", "status", "--value", "active", "--source", "memory:a")
        transcript = self.transcript(typed("other words", START))
        with self.assertRaisesRegex(KnowledgeError, "not in anything you typed"):
            self.run_kg(
                "supersede",
                self.f(1),
                "--value",
                "retired",
                "--from-user",
                "--quote",
                "it is retired",
                "--transcript",
                transcript,
            )


class LintTest(KnowledgeTestCase):
    def test_contradictions_on_single_valued_predicates(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "role", "--value", "reviewer", "--source", "ledger:a")
        self.run_kg("propose", "person/dana", "role", "--value", "release manager", "--source", "ledger:b")
        self.run_kg("confirm", self.f(1), self.f(2))
        self.assertNotIn("contradiction", self.run_kg("lint"))
        (self.root / "schema.json").write_text(json.dumps({"single_valued": ["role"]}))
        out = self.run_kg("lint")
        both = ", ".join(sorted([self.f(1), self.f(2)]))
        self.assertIn(f"contradiction: person/dana has 2 current values for role ({both})", out)

    def test_stale_orphans_collisions_and_merge_suggestions(self) -> None:
        self.seed()
        self.run_kg("add-entity", "person/robin-a", "--name", "Robin Arden")
        self.run_kg("add-entity", "person/robin-b", "--name", "Robin Bell")
        self.run_kg("add-entity", "repo/invoice-exporters", "--name", "invoice exporters")
        self.run_kg(
            "propose",
            "process/release",
            "has_step",
            "step/merge",
            "--source",
            "ledger:a",
            "--source",
            "report:b",
        )
        self.later(kr.DEFAULT_STALE_DAYS + 1)
        out = self.run_kg("lint")
        self.assertIn(f"stale: {self.f(1)} not observed for over 180 days", out)
        self.assertIn("orphan: environment/staging-2 has no facts", out)
        self.assertIn("person/robin-a, person/robin-b share the first name 'robin'", out)
        self.assertIn("merge-suggestion: repo/invoice-exporter and repo/invoice-exporters", out)
        self.run_kg("propose", "person/robin-a", "distinct_from", "person/robin-b", "--source", "user:x")
        self.run_kg("confirm", self.f(2))
        self.assertNotIn("first name", self.run_kg("lint"))

    def test_strict_exits_1(self) -> None:
        self.seed()
        args = knowledge.build_parser().parse_args(["lint", "--strict"])
        self.assertEqual(args.func(args)[1], 1)


class RetentionAndForgetTest(KnowledgeTestCase):
    def test_person_facts_past_retention_are_reported_and_deleted(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:directory"
        )
        self.run_kg("propose", "repo/invoice-exporter", "caveat", "--value", "x", "--source", "memory:a")
        kept = self.f(2)
        self.run_kg("confirm", kept)
        self.later(km.RETENTION_DAYS + 1)
        self.assertIn("retention: 1 person facts unseen for 365 days", self.run_kg("lint"))
        out = self.run_kg("expire")
        self.assertIn("1 person facts past retention deleted", out)
        self.assertEqual(sorted(self.kb().facts), [kept])
        self.assertNotIn("Lisbon", (self.root / "facts.jsonl").read_text())
        forgotten = [json.loads(line) for line in (self.root / "forgotten.jsonl").read_text().splitlines()]
        self.assertEqual(forgotten, [{"at": km.iso(self.now), "reason": "retention", "facts": 1}])

    def test_forget_deletes_a_person_everywhere_and_keeps_only_a_hash(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        self.run_kg("propose", "process/release", "has_step", "step/merge", "--source", "ledger:a")
        self.run_kg("identify", "person/dana", "slack", "U0000000002")
        self.assertIn(
            "would delete the person, 1 facts and 1 identifiers",
            self.run_kg("forget", "person/dana", "--dry-run"),
        )
        self.assertIn("person/dana", self.kb().entities)
        self.run_kg("forget", "dana example")
        for name in ("entities.jsonl", "facts.jsonl", "identifiers.jsonl"):
            text = (self.root / name).read_text()
            self.assertNotIn("dana", text.lower(), name)
            self.assertNotIn("U0000000002", text)
        record = json.loads((self.root / "forgotten.jsonl").read_text())
        secret = bytes.fromhex((self.root / "secret.key").read_text().strip())
        self.assertEqual(record["hash"], km.hash_id("person/dana", secret))
        self.assertEqual((record["facts"], record["identifiers"]), (1, 1))
        with self.assertRaisesRegex(KnowledgeError, "deleted on request"):
            self.run_kg("add-entity", "person/dana")
        self.run_kg("add-entity", "person/dana", "--allow-forgotten")

    def backups(self, count: int) -> list[Path]:
        """<count> update backups in the state folder, as an update leaves them."""
        folder = self.state / ub.BACKUPS_DIR
        made = [folder / f"2026110{n + 1}-090000-aaa1111-bbb2222" for n in range(count)]
        for backup in made:
            (backup / "config" / "knowledge").mkdir(parents=True)
            (backup / ub.MANIFEST).write_text("{}\n")
        return made

    def forget_saying(self, *argv: str) -> str:
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg(*argv)
        return said.getvalue()

    def test_a_forget_deletes_the_update_backups(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        made = self.backups(2)
        said = self.forget_saying("forget", "person/dana")
        self.assertEqual([p for p in made if p.exists()], [])
        self.assertIn(
            "kg: deleted 2 update backups taken before this; going back past it is no longer possible", said
        )
        self.assertNotIn("dana", said.lower())

    def test_a_forget_says_backup_for_one(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        self.backups(1)
        self.assertIn(
            "kg: deleted 1 update backup taken before this;", self.forget_saying("forget", "person/dana")
        )

    def test_a_forget_with_no_backups_says_nothing_of_them(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        self.assertNotIn("update backup", self.forget_saying("forget", "person/dana"))

    def test_expire_deletes_them_when_it_deletes_a_person_fact(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:directory"
        )
        made = self.backups(2)
        self.later(km.RETENTION_DAYS + 1)
        said = self.forget_saying("expire")
        self.assertEqual([p for p in made if p.exists()], [])
        self.assertIn("kg: deleted 2 update backups", said)

    def test_expire_keeps_them_when_it_deletes_none(self) -> None:
        self.seed()
        made = self.backups(2)
        self.later(1)
        said = self.forget_saying("expire")
        self.assertTrue(all(p.exists() for p in made))
        self.assertNotIn("update backup", said)

    def forgotten_records(self) -> list[dict[str, Any]]:
        text = (self.root / "forgotten.jsonl").read_text()
        return [json.loads(line) for line in text.splitlines()]

    def test_forget_records_a_keyed_hash(self) -> None:
        self.seed()
        self.run_kg("forget", "person/dana")
        record = self.forgotten_records()[0]
        guessable = hashlib.sha256(b"person/dana").hexdigest()[: km.HASH_LENGTH]
        self.assertNotEqual(record["hash"], guessable)
        self.assertEqual(record["hash_kind"], "hmac-sha256")

    def test_a_forgotten_person_needs_allow_forgotten_again(self) -> None:
        self.seed()
        self.run_kg("forget", "person/dana")
        with self.assertRaisesRegex(KnowledgeError, "deleted on request"):
            self.run_kg("add-entity", "person/dana")
        self.run_kg("add-entity", "person/dana", "--allow-forgotten")

    def test_an_old_forgotten_record_still_blocks_re_adding(self) -> None:
        self.seed()
        old = hashlib.sha256(b"person/erin").hexdigest()[: km.HASH_LENGTH]
        (self.root / "forgotten.jsonl").write_text(
            json.dumps({"at": km.iso(self.now), "hash": old, "reason": "request", "facts": 0}) + "\n"
        )
        with self.assertRaisesRegex(KnowledgeError, "deleted on request"):
            self.run_kg("add-entity", "person/erin")

    def test_a_lost_key_refuses_people_and_never_makes_a_new_one(self) -> None:
        self.seed()
        self.run_kg("forget", "person/dana")
        (self.root / "secret.key").unlink()
        with self.assertRaisesRegex(KnowledgeError, "secret.key.*restore it"):
            self.run_kg("add-entity", "person/dana")
        with self.assertRaisesRegex(KnowledgeError, "restore it"):
            self.run_kg("add-entity", "person/frank")
        self.assertFalse((self.root / "secret.key").exists())
        self.run_kg("add-entity", "repo/other", "--name", "other")

    def test_forget_is_refused_without_the_key_once_keyed_records_exist(self) -> None:
        self.seed()
        self.run_kg("add-entity", "person/erin")
        self.run_kg("forget", "person/dana")
        (self.root / "secret.key").unlink()
        with self.assertRaisesRegex(KnowledgeError, "secret.key.*restore it"):
            self.run_kg("forget", "person/erin")
        self.assertIn("person/erin", self.kb().entities)
        self.assertFalse((self.root / "secret.key").exists())

    def test_a_corrupt_key_a_keyed_record_needs_is_named_and_never_overwritten(self) -> None:
        """With no keyed record, one is kept aside and made again instead (test_knowledge_changes,
        KeyTest)."""
        self.run_kg("add-entity", "person/lee", "--name", "Lee Example")
        self.run_kg("forget", "person/lee")  # a keyed record
        key = self.root / "secret.key"
        for garbage in ("not hex\n", "abcd\n", ""):
            with self.subTest(garbage=garbage):
                key.write_text(garbage)
                with self.assertRaisesRegex(KnowledgeError, "secret.key.*restore it"):
                    self.run_kg("add-entity", "person/dana")
                self.run_kg("add-entity", "repo/other", "--name", "other")
                self.assertEqual(key.read_text(), garbage)
                self.assertEqual(list(self.root.glob("secret.key.aside-*")), [])

    def test_moving_forgotten_aside_lets_the_next_write_make_a_new_key(self) -> None:
        """With the journal's deletion markers (which hold the same records), and replica.json, as
        the key's message says."""
        self.seed()
        self.run_kg("forget", "person/dana")
        (self.root / "secret.key").unlink()
        (self.root / "forgotten.jsonl").rename(self.root / "forgotten.jsonl.old")
        (self.root / "journal").rename(self.root / "journal.old")
        (self.state / "replica.json").rename(self.state / "replica.json.old")
        self.run_kg("add-entity", "person/dana")
        self.assertTrue((self.root / "secret.key").exists())

    def test_the_key_is_private(self) -> None:
        self.run_kg("add-entity", "person/dana")
        key = self.root / "secret.key"
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(bytes.fromhex(key.read_text().strip())), 32)

    def test_only_people_are_forgotten(self) -> None:
        self.seed()
        with self.assertRaisesRegex(KnowledgeError, "not a person"):
            self.run_kg("forget", "repo/invoice-exporter")

    def test_identifiers_are_private(self) -> None:
        self.seed()
        self.run_kg("identify", "person/dana", "github", "dana-example")
        self.assertEqual((self.root / "identifiers.jsonl").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(KnowledgeError):
            self.run_kg("identify", "repo/invoice-exporter", "github", "x")


class BatchTest(KnowledgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        out = self.run_kg("import", str(FIXTURE), "--apply", "--auto-confirm")
        self.assertIn("refused: 2 people whitelist", out)

    def test_small_groups_fold_into_their_axis_rework_first(self) -> None:
        groups = kr.build_groups(self.kb())
        self.assertEqual(
            [(g.title, len(g.fact_ids), g.rework) for g in groups],
            [
                ("Process: other facts", 5, True),
                ("Product: other facts", 4, True),
                ("People: other facts", 5, False),
            ],
        )
        out = self.run_kg("batch")
        self.assertIn("1. Process: other facts: 5 facts [prevents rework]", out)
        self.assertIn(f"   b. {self.f(3)} Release process includes the step Gradual rollout (step 3)", out)

    def test_entity_and_area_groups_and_the_cap(self) -> None:
        for number in range(kr.MIN_GROUP_FACTS):
            self.run_kg(
                "propose",
                "repo/pdf-renderer",
                "caveat",
                "--value",
                f"gotcha {number}",
                "--source",
                "memory:a",
            )
        for person in ("kim", "max", "sam"):
            self.run_kg("add-entity", f"person/{person}")
            self.run_kg("propose", f"person/{person}", "reviews", "exporter", "--source", "ledger:a")
        titles = [g.title for g in kr.build_groups(self.kb())]
        self.assertEqual(
            titles,
            [
                "Process: other facts",
                "Product: other facts",
                "Reviewers for invoice exporter",
                "pdf renderer (repo)",
                "People: other facts",
            ],
        )
        out = self.run_kg("batch", "--cap", "2")
        self.assertIn("Knowledge to review: 2 groups, 9 facts (3 more groups wait for a later batch).", out)

    def test_steps_and_decisions_group_with_their_parent(self) -> None:
        for number in range(kr.MIN_GROUP_FACTS):
            self.run_kg(
                "propose", "step/rollout", "caveat", "--value", f"gotcha {number}", "--source", "memory:a"
            )
        groups = {g.title: g for g in kr.build_groups(self.kb())}
        self.assertEqual(len(groups["Release process (process)"].fact_ids), kr.MIN_GROUP_FACTS + 2)

    def test_apply_confirm_all_except(self) -> None:
        self.run_kg("batch", "--cap", "2")
        batch = json.loads((self.root / "batch.json").read_text())
        first, second = batch["groups"][0]["facts"], batch["groups"][1]["facts"]
        out = self.run_kg("batch", "apply", "confirm all except 1b")
        self.assertIn(f"{len(first) + len(second) - 1} confirmed, 0 rejected, 1 left proposed", out)
        facts = self.kb().facts
        self.assertEqual(facts[first[1]]["status"], km.PROPOSED)  # deferred, not rejected
        self.assertTrue(all(facts[i]["status"] == km.CONFIRMED for i in [first[0], *second]))
        with self.assertRaisesRegex(KnowledgeError, "already answered|was answered"):
            self.run_kg("batch", "apply", "confirm all")

    def test_apply_with_flags_and_skip(self) -> None:
        self.run_kg("batch")
        out = self.run_kg("batch", "apply", "--confirm", "1,3", "--reject", "2a", "--dry-run")
        self.assertIn("(dry run)", out)
        self.assertEqual(self.kb().facts[self.f(3)]["status"], km.PROPOSED)
        out = self.run_kg("batch", "apply", "confirm 1; reject 2a, skip 3")
        self.assertIn("rejected", out)

    def test_decisions_parse_and_refuse(self) -> None:
        groups = [["f1", "f2"], ["f3"], ["f4", "f5", "f6"]]
        self.assertEqual(
            kr.decide(kr.parse_clauses("confirm all except 3b"), groups),
            {
                "f1": "confirm",
                "f2": "confirm",
                "f3": "confirm",
                "f4": "confirm",
                "f6": "confirm",
            },
        )
        self.assertEqual(
            kr.decide(kr.parse_clauses("reject 1 and confirm 1a; skip 3"), groups),
            {"f1": "confirm", "f2": "reject"},
        )
        self.assertEqual(kr.decide(kr.parse_clauses("confirm all except 2; skip 2"), groups)["f1"], "confirm")
        for bad in ("hello 1", "confirm 9", "confirm 2c", "confirm", "confirm 1 maybe"):
            with self.subTest(bad=bad), self.assertRaises(KnowledgeError):
                kr.decide(kr.parse_clauses(bad), groups)

    def test_the_fact_cap_and_the_limits(self) -> None:
        out = self.run_kg("batch", "--max-facts", "8")
        self.assertIn("Knowledge to review: 1 group, 5 facts (2 more groups wait for a later batch).", out)
        groups = kr.build_groups(self.kb(), 2)
        self.assertTrue(all(len(g.fact_ids) <= 2 for g in groups))
        self.assertEqual(kr.batches_needed(groups, kr.Limits(groups=3, facts=40, group_facts=2)), 3)
        for bad in ((0, 40, 8), (10, 40, 27), (10, 4, 8)):
            with self.subTest(bad=bad), self.assertRaises(KnowledgeError):
                kr.Limits(*bad)

    def test_group_keys_confirm_a_whole_group(self) -> None:
        out = self.run_kg("confirm", "axis:people")
        self.assertEqual(out.count("confirmed"), 5)
        for person in ("kim", "max", "sam", "ray", "uma"):
            self.run_kg("add-entity", f"person/{person}")
            self.run_kg("propose", f"person/{person}", "reviews", "exporter", "--source", "ledger:a")
        self.run_kg("propose", "person/lee", "reviews", "pdf renderer", "--source", "ledger:a")
        out = self.run_kg("confirm", "invoice exporter#reviews")
        self.assertEqual(out.count("confirmed"), 5)
        with self.assertRaisesRegex(KnowledgeError, "neither a fact id nor a group"):
            self.run_kg("confirm", "team/billing")

    def test_big_groups_split_by_letters(self) -> None:
        for number in range(kr.DEFAULT_GROUP_FACTS + 3):
            self.run_kg(
                "propose",
                "repo/pdf-renderer",
                "caveat",
                "--value",
                f"caveat {number}",
                "--source",
                "memory:a",
            )
        groups = [g for g in kr.build_groups(self.kb()) if g.key == "repo/pdf-renderer"]
        self.assertEqual([len(g.fact_ids) for g in groups], [kr.DEFAULT_GROUP_FACTS, 3])
        self.assertTrue(groups[1].title.endswith("part 2"))


class ImportTest(KnowledgeTestCase):
    def test_dry_run_is_the_default_and_writes_nothing(self) -> None:
        out = self.run_kg("import", str(FIXTURE))
        self.assertIn("dry run (nothing written", out)
        self.assertIn("entities: 15 new", out)
        self.assertIn("facts: 20 new", out)
        self.assertIn("confirmed automatically: 0; waiting for review: 20", out)
        self.assertIn("refused: 2 people whitelist", out)
        self.assertIn("review: 4 groups in 1 daily batch (the first: 4 groups, 20 facts", out)
        self.assertFalse(self.root.exists())

    def test_apply_then_again_only_re_observes(self) -> None:
        self.run_kg("import", str(FIXTURE), "--apply")
        out = self.run_kg("import", str(FIXTURE), "--apply")
        self.assertIn("entities: 0 new, 15 already known", out)
        self.assertIn("facts: 0 new, 20 re-observed", out)

    def test_auto_confirm_is_opt_in(self) -> None:
        out = self.run_kg("import", str(FIXTURE), "--auto-confirm")
        self.assertIn("confirmed automatically: 6; waiting for review: 14", out)

    def test_json_arrays_and_bad_records(self) -> None:
        path = self.tmp / "import.json"
        records = [
            {"type": "entity", "id": "repo/invoice-exporter"},
            {
                "type": "fact",
                "subject": "repo/missing",
                "predicate": "caveat",
                "value": "x",
                "sources": ["memory:a"],
            },
            {"type": "note"},
        ]
        path.write_text(json.dumps(records))
        out = self.run_kg("import", str(path))
        self.assertIn("refused: 1 format, 1 unresolved or invalid", out)
        self.assertIn("record 2: no entity 'repo/missing'", out)


class ContextTest(KnowledgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.run_kg("import", str(FIXTURE), "--apply", "--auto-confirm")
        self.run_kg("identify", "person/dana", "slack", "U0000000002")

    def test_a_pack_cites_confirmed_facts_only(self) -> None:
        self.assertNotIn("Gradual rollout", self.run_kg("context", "--for", "release"))
        self.run_kg("confirm", self.f(3))  # release has_step rollout: now its neighbour counts
        pack = self.run_kg("context", "--for", "release")
        self.assertIn("Knowledge for Release process (process/release)", pack)
        self.assertIn(f"- Release process has step Merge to main (step 1) [{self.f(1)}; ledger:", pack)
        self.assertIn("Gradual rollout actor Deploy bot", pack)  # a neighbour's fact
        self.assertNotIn("canary checks pass", pack)  # proposed: never in a pack
        self.assertNotIn("U0000000002", self.run_kg("context", "--for", "person/dana"))

    def test_repos_and_topics(self) -> None:
        self.assertIn("depends on pdf renderer", self.run_kg("context", "--for", "invoice-exporter"))
        self.assertIn("staging 2", self.run_kg("context", "--for", "retired environment"))
        self.assertIn("No knowledge recorded", self.run_kg("context", "--for", "quantum"))

    def test_a_pack_stays_within_its_budget(self) -> None:
        for number in range(120):
            self.run_kg("add-entity", f"system/tool-{number}", "--name", f"tool {number} " + "x" * 40)
            self.run_kg(
                "propose",
                "repo/invoice-exporter",
                "depends_on",
                f"system/tool-{number}",
                "--source",
                f"ledger:{number}",
                "--source",
                f"report:{number}",
            )
        for tokens in (300, 1500):
            pack = self.run_kg("context", "--for", "exporter", "--max-tokens", str(tokens))
            self.assertLessEqual(len(pack), tokens * 4)
            self.assertIn("more: kg lookup repo/invoice-exporter", pack)
            for line in pack.splitlines()[1:-1]:
                self.assertRegex(line, r"\[f-\w{6}; \w+:")


class QueryTest(KnowledgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.run_kg("import", str(FIXTURE), "--apply", "--auto-confirm")

    def test_lookup_shows_sources_links_and_what_waits(self) -> None:
        out = self.run_kg("lookup", "smoke")
        self.assertIn("gate/smoke-tests: Smoke tests (gate; process axis)", out)
        self.assertIn("linked from:", out)
        self.assertIn("[ledger:mypr:acme/invoice-exporter#102; memory:deploys.md]", out)
        out = self.run_kg("lookup", "release")
        self.assertIn("unconfirmed (2, not used in context packs)", out)

    def test_neighbors_and_hops(self) -> None:
        self.run_kg("confirm", self.f(3))
        one = self.run_kg("neighbors", "release")
        self.assertNotIn("Deploy bot", one)
        two = self.run_kg("neighbors", "release", "--hops", "2")
        self.assertIn(f"2 {self.f(5)} Gradual rollout actor Deploy bot", two)

    def test_search_ranks_names_before_text(self) -> None:
        out = self.run_kg("search", "exporter").splitlines()
        self.assertTrue(out[0].startswith("repo/invoice-exporter"))
        self.assertIn("lesson/retired-env", self.run_kg("search", "follow-up"))

    def test_schema_lists_the_whitelist(self) -> None:
        self.assertIn("people whitelist: alias_of, approves", self.run_kg("schema"))


class GitHistoryTest(KnowledgeTestCase):
    git = True

    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        super().setUp()

    def git_out(self, repo: Path, *args: str) -> str:
        """git in <repo> only, whatever GIT_* variables the test runner has (a commit hook sets
        GIT_DIR and GIT_INDEX_FILE for the repository being committed)."""
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
            env=store.git_environment(repo),
        )
        return result.stdout

    def commits(self) -> list[str]:
        return self.git_out(self.root, "log", "--format=%s").splitlines()

    def test_an_older_repository_gains_the_key_line_even_without_a_final_newline(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        store.ensure_repository(self.root)
        (self.root / ".gitignore").write_text("knowledge.lock\n*.tmp\nbatch.json")
        self.run_kg("add-entity", "person/dana")
        lines = (self.root / ".gitignore").read_text().splitlines()
        self.assertIn("batch.json", lines)
        self.assertIn("secret.key", lines)
        self.assertIn("*.torn-*", lines)  # what a write cut short left in the journal
        self.assertEqual(len(lines), len(set(lines)))
        self.assertNotIn("secret.key", self.git_out(self.root, "ls-files").split())

    def test_the_key_is_private_and_not_in_git(self) -> None:
        self.run_kg("add-entity", "person/dana")
        self.assertTrue((self.root / "secret.key").exists())
        tracked = self.git_out(self.root, "ls-files").split()
        self.assertNotIn("secret.key", tracked)
        self.assertIn("entities.jsonl", tracked)

    def test_one_commit_per_change_and_forget_restarts_history(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        self.assertEqual(self.commits()[0], "kg propose person/dana reviews")
        self.assertEqual(len(self.commits()), 7)
        self.run_kg("lookup", "release")  # reading commits nothing
        self.assertEqual(len(self.commits()), 7)
        self.run_kg("forget", "person/dana")
        self.assertEqual(self.commits(), ["kg forget: a person deleted on request"])
        self.assertNotIn("Dana", self.git_out(self.root, "log", "-p", "--all"))

    def test_a_history_that_cannot_be_deleted_fails_the_forget_and_the_next_write_finishes_it(self) -> None:
        """Fail closed: a .git that is still there after the delete names it, view.json does not
        move, and the next write that can delete it does (the restart is still owed)."""
        if os.geteuid() == 0:
            self.skipTest("root deletes whatever the modes say")
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        view = (self.root / "view.json").read_bytes()
        objects = self.root / ".git" / "objects"
        objects.chmod(0o500)  # what is in it cannot all be deleted
        self.addCleanup(objects.chmod, 0o700)
        with self.assertRaisesRegex(store.StoreError, f"{self.root / '.git'}.*could not be deleted"):
            self.run_kg("forget", "person/dana")
        self.assertEqual((self.root / "view.json").read_bytes(), view)
        objects.chmod(0o700)
        self.run_kg("add-entity", "repo/other")
        self.assertNotIn("dana", self.git_out(self.root, "log", "-p", "--all").lower())

    def loose_in_17(self, n: int) -> Path:
        """A loose object (a blob) whose id starts with 17, written in the history as git would.
        git estimates the loose objects from that one folder, and two there count as more than
        its threshold: the next commit's housekeeping repacks."""
        found = 0
        for i in range(100_000):
            body = f"planted {i}\n".encode()
            raw = b"blob %d\0" % len(body) + body
            oid = hashlib.sha1(raw).hexdigest()  # noqa: S324 - git's object id, not a secret
            if oid.startswith("17"):
                if found == n:
                    path = self.root / ".git" / "objects" / oid[:2] / oid[2:]
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(zlib.compress(raw))
                    return path
                found += 1
        raise AssertionError("no object id starting with 17")

    def test_git_housekeeping_is_done_when_the_write_returns(self) -> None:
        """git's housekeeping after a commit (a repack, when it counts enough loose objects) runs
        inside the commit, not detached: a repack still running after the write would write in a
        history the next write deletes (kg forget), and could make .git again after the delete,
        a folder that is no repository. Pinned in the history's own config, whatever the git and
        my config: the housekeeping is a gc (a strategy name an older git does not know it
        ignores), and two loose objects in objects/17 put git's estimate over gc.auto=1."""
        self.seed()
        pinned = {"maintenance.auto": "true", "maintenance.strategy": "gc", "maintenance.gc.enabled": "true"}
        for key, value in {**pinned, "gc.auto": "1"}.items():
            store.git(self.root, "config", key, value)
        packs = self.root / ".git" / "objects" / "pack"
        before = set(packs.glob("*.pack"))
        for n in range(2):
            self.loose_in_17(n)
        self.run_kg("add-entity", "repo/other")
        self.assertTrue(set(packs.glob("*.pack")) - before)  # the repack is done already

    def broken_history(self) -> None:
        """What a process left in a history a restart deleted (its refs and pack lists, made
        again): a .git that is no repository."""
        shutil.rmtree(self.root / ".git")
        (self.root / ".git" / "objects" / "info").mkdir(parents=True)
        (self.root / ".git" / "objects" / "info" / "packs").write_text("P pack-0.pack\n")
        (self.root / ".git" / "info").mkdir()
        (self.root / ".git" / "info" / "refs").write_text("")
        (self.root / ".git" / "left-by-an-outside-writer").write_text("debris\n")  # no git makes it

    def test_a_history_folder_that_is_no_repository_is_made_again(self) -> None:
        """The next write deletes it and starts the history again, saying so in one line, and the
        writes after it record as ever."""
        self.seed()
        self.broken_history()
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            for entity in ("repo/other", "repo/third", "repo/fourth"):
                self.run_kg("add-entity", entity)
        [line] = said.getvalue().splitlines()
        self.assertIn(f"{self.root / '.git'} was no git repository; deleted it", line)
        self.assertEqual(knowledge.SETTINGS.warnings, [])
        self.assertEqual(
            self.commits(), [f"kg add-entity {e}" for e in ("repo/fourth", "repo/third", "repo/other")]
        )
        self.assertFalse((self.root / ".git" / "left-by-an-outside-writer").exists())  # the debris went

    def test_a_history_git_cannot_read_now_is_kept(self) -> None:
        """A .git with what every repository has is never deleted, whatever git says of it: a typo
        in my git config, or a newer git's repository format, unknown here. The commit warns, and the
        history is all there once git reads it again."""
        self.seed()
        before = self.commits()
        home = self.tmp / "home"
        home.mkdir()
        (home / ".gitconfig").write_text("[user]\n\tname = me\n[alias\n")  # a section never closed
        with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}):
            self.run_kg("add-entity", "repo/other")
        config = self.root / ".git" / "config"
        store.git(self.root, "config", "core.repositoryformatversion", "1")
        store.git(self.root, "config", "extensions.someNewerFeature", "true")
        self.run_kg("add-entity", "repo/third")
        self.assertEqual(len(knowledge.SETTINGS.warnings), 2)
        for warning in knowledge.SETTINGS.warnings:
            self.assertTrue(warning.startswith("warning: git commit failed: "), warning)
        self.assertEqual(self.commits(), [])  # this git cannot read it now; it is there
        text = re.sub(r"\[extensions\][^\[]*", "", config.read_text())
        config.write_text(text.replace("repositoryformatversion = 1", "repositoryformatversion = 0"))
        self.assertEqual(self.commits(), before)
        self.run_kg("add-entity", "repo/fourth")
        self.assertEqual(self.commits(), ["kg add-entity repo/fourth", *before])

    def test_a_broken_history_that_cannot_be_deleted_warns_and_the_write_is_saved(self) -> None:
        """After the write is saved (view.json), the history's step never fails the command."""
        if os.geteuid() == 0:
            self.skipTest("root deletes whatever the modes say")
        self.seed()
        self.broken_history()
        locked = self.root / ".git" / "objects" / "info"
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        self.run_kg("add-entity", "repo/other")
        self.assertIn("repo/other", self.kb().entities)
        [warning] = knowledge.SETTINGS.warnings
        self.assertIn("is no git repository and could not be deleted whole", warning)

    def test_a_git_that_fails_is_never_silent(self) -> None:
        """A git status that fails (here: a .git made again between the check and the commit) is a
        warning, as a failing add or commit is."""
        self.seed()
        real = store.git

        def status_fails(root: Path, *args: str, pinned: bool = True) -> Any:
            found = real(root, *args, pinned=pinned)
            return found if args[0] != "status" else subprocess.CompletedProcess(args, 128, "", "fatal: x")

        with mock.patch.object(store, "git", status_fails):
            self.assertEqual(store.commit(self.root, "kg add-entity"), "warning: git commit failed: fatal: x")

    def fake_git(self, script: str) -> dict[str, str]:
        """A `git` that runs <script> (sh), first on the PATH: the environment to use it with."""
        fake = self.tmp / "bin"
        fake.mkdir(exist_ok=True)
        (fake / "git").write_text(f"#!/bin/sh\n{script}\n")
        (fake / "git").chmod(0o755)
        return {"PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}

    def processes_naming(self, token: str) -> list[str]:
        listed = subprocess.run(["ps", "-A", "-o", "command="], capture_output=True, check=True)
        return [line for line in listed.stdout.decode(errors="replace").splitlines() if token in line]

    def test_a_git_past_its_time_is_stopped_with_all_it_started(self) -> None:
        """git's housekeeping is its children, theirs too: on a timeout git and every process
        under it are killed and waited for, so none stays writing in the history; the write is
        saved, kg warns and exits 0."""
        self.seed()
        token = f"kg-test-tree-{os.getpid()}-{time.monotonic_ns()}"
        mark = self.tmp / "written-after-the-timeout"
        tree = f"sh -c '(sleep 1; : > \"{mark}\") & sleep 30' {token} &\nsleep 30"  # a child, a grandchild
        environment = {
            **self.fake_git(tree),
            "BOSUN_CONFIG_DIR": str(self.tmp),
            "BOSUN_STATE_DIR": str(self.state),
        }
        said = io.StringIO()
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(sys, "argv", ["kg", "add-entity", "repo/other"]),
            mock.patch.object(store, "GIT_TIMEOUT_SECONDS", 0.5),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(said),
        ):
            self.assertEqual(knowledge.main(), 0)
        self.assertEqual(self.processes_naming(token), [])
        self.assertIn("warning: git commit failed: git add ran past 0.5 s and was stopped\n", said.getvalue())
        self.assertIn("repo/other", self.kb().entities)
        time.sleep(1.5)
        self.assertFalse(mark.exists())  # the grandchild went with git

    def test_processes_still_there_after_the_kill_are_said(self) -> None:
        """One of git's processes that would not go (here: one of ours, found in the tree and taken
        for alive whatever happens to it) is counted in the warning."""
        self.root.mkdir(parents=True)
        stays = subprocess.Popen(["sleep", "30"])
        self.addCleanup(stays.wait)
        self.addCleanup(stays.kill)
        listed = store.stop_tree

        def with_it(leader: int, tree: set[int]) -> str:
            tree.add(stays.pid)
            return listed(leader, tree)

        with (
            mock.patch.dict(os.environ, self.fake_git("sleep 30")),
            mock.patch.object(store, "GIT_TIMEOUT_SECONDS", 0.2),
            mock.patch.object(store, "GIT_TREE_GONE_SECONDS", 0.1),
            mock.patch.object(store, "stop_tree", with_it),
            mock.patch.object(store, "alive", lambda pid: pid == stays.pid),
        ):
            warning = store.commit(self.root, "kg add-entity")
        self.assertEqual(
            warning,
            "warning: git commit failed: git init ran past 0.2 s and was stopped; 1 process(es) it "
            "started were still there 0.1 s later",
        )

    def test_a_command_line_that_is_not_utf8_never_stops_the_kill(self) -> None:
        """Another process on the Mac with bytes in its command line that are not UTF-8: the
        listing of what git started reads only pids, so all of it is still killed, with a warning."""
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", b"not utf-8: \xff"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        token = f"kg-test-tree-{os.getpid()}-{time.monotonic_ns()}"
        self.root.mkdir(parents=True)
        with (
            mock.patch.dict(os.environ, self.fake_git(f"sh -c 'sleep 30' {token} &\nsleep 30")),
            mock.patch.object(store, "GIT_TIMEOUT_SECONDS", 0.3),
        ):
            warning = store.commit(self.root, "kg add-entity")
        self.assertEqual(warning, "warning: git commit failed: git init ran past 0.3 s and was stopped")
        self.assertEqual(self.processes_naming(token), [])

    def test_a_ps_that_fails_is_said_and_git_is_still_stopped(self) -> None:
        self.root.mkdir(parents=True)
        environment = self.fake_git("sleep 30")
        (self.tmp / "bin" / "ps").write_text("#!/bin/sh\necho 'ps: not here' >&2\nexit 1\n")
        (self.tmp / "bin" / "ps").chmod(0o755)
        with mock.patch.dict(os.environ, environment), mock.patch.object(store, "GIT_TIMEOUT_SECONDS", 0.2):
            started = time.monotonic()
            warning = store.commit(self.root, "kg add-entity")
            self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(
            warning,
            "warning: git commit failed: git init ran past 0.2 s and was stopped; the processes it "
            "started could not be listed (ps exited 1: ps: not here), so only git itself was stopped",
        )

    def test_a_ctrl_c_while_git_is_being_stopped_still_kills_what_was_stopped(self) -> None:
        """Else what was stopped so far would stay stopped for good."""
        leader = subprocess.Popen(["sleep", "30"], text=True)
        stopped = subprocess.Popen(["sleep", "30"])
        for process in (leader, stopped):
            self.addCleanup(process.wait)
            self.addCleanup(process.kill)

        def interrupted(pid: int, tree: set[int]) -> str:
            tree.add(stopped.pid)
            os.kill(stopped.pid, signal.SIGSTOP)
            raise KeyboardInterrupt

        with mock.patch.object(store, "stop_tree", interrupted), self.assertRaises(KeyboardInterrupt):
            store.kill_tree(leader)
        self.assertEqual(stopped.wait(timeout=5), -signal.SIGKILL)
        self.assertEqual(leader.wait(timeout=5), -signal.SIGKILL)

    def test_git_runs_in_the_commands_own_process_group(self) -> None:
        """So that a Ctrl-C, a closed terminal or a stop sent to the command reaches git too: a git
        in a group of its own would go on writing in the history after the command ended."""
        self.root.mkdir(parents=True)
        with mock.patch.dict(os.environ, self.fake_git("ps -o pgid= -p $$")):
            ran = store.git(self.root, "status")
        self.assertEqual(ran.stdout.strip(), str(os.getpgrp()))

    def test_a_write_whose_git_runs_past_its_time_is_saved_and_warns(self) -> None:
        """Never a traceback after the files are saved: the history's step warns."""
        self.seed()
        with mock.patch.object(store, "GIT_TIMEOUT_SECONDS", 0.0001):
            self.run_kg("add-entity", "repo/other")
        self.assertIn("repo/other", self.kb().entities)
        [warning] = knowledge.SETTINGS.warnings
        self.assertRegex(warning, r"^warning: git commit failed: git \S+ ran past 0.0001 s and was stopped$")

    def test_my_git_hooks_and_template_never_run_in_the_history(self) -> None:
        """Hooks from my git config (core.hooksPath) or its template (init.templateDir, copied into
        each new .git) would see the diffs that name the people a forget deletes, and could leave a
        process in .git: none runs, and no template is copied, across a history restart too."""
        home, mark = self.tmp / "home", self.tmp / "a-hook-ran"
        hooks, template = home / "hooks", home / "template"
        for folder in (hooks, template / "hooks"):
            folder.mkdir(parents=True)
            for name in ("post-commit", "prepare-commit-msg", "reference-transaction", "post-index-change"):
                (folder / name).write_text(f'#!/bin/sh\necho {name} >> "{mark}"\n')
                (folder / name).chmod(0o755)
        config = f"[core]\n\thooksPath = {hooks}\n[init]\n\ttemplateDir = {template}\n"
        (home / ".gitconfig").write_text(config)
        with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}):
            self.seed()
            self.run_kg("forget", "person/dana")
            self.run_kg("add-entity", "repo/other")
        self.assertFalse(mark.exists())
        self.assertFalse((self.root / ".git" / "hooks").exists())
        self.assertEqual(len(self.commits()), 2)

    def test_my_own_git_config_starts_no_daemon_in_the_history(self) -> None:
        """A core.fsmonitor=true in my git config would leave a file-watching daemon in .git, which
        a write that deletes the history would race: the store's git runs without it."""
        home = self.tmp / "home"
        home.mkdir()
        (home / ".gitconfig").write_text("[core]\n\tfsmonitor = true\n")
        with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}):
            self.seed()
            self.assertEqual(store.git(self.root, "config", "core.fsmonitor").stdout.strip(), "false")
            self.assertEqual(self.git_out(self.root, "config", "core.fsmonitor").strip(), "true")  # mine

    def test_git_variables_of_the_caller_never_redirect_the_history(self) -> None:
        decoy = self.tmp / "decoy"
        decoy.mkdir()
        store.git(decoy, "init", "-q", str(decoy), pinned=False)
        config = (decoy / ".git" / "config").read_text()
        hook_like = {"GIT_DIR": str(decoy / ".git"), "GIT_INDEX_FILE": str(decoy / ".git" / "index")}
        with mock.patch.dict(os.environ, hook_like):
            self.seed()
        self.assertEqual(len(self.commits()), 6)
        self.assertEqual(self.git_out(decoy, "log", "--format=%s"), "")
        self.assertEqual((decoy / ".git" / "config").read_text(), config)
        self.assertEqual(sorted(p.name for p in decoy.iterdir()), [".git"])

    def a_git_lock(self, age_seconds: float) -> Path:
        """The index lock a git command leaves while it runs, or for good when it is cut short (a
        power cut during a write's commit), last touched <age_seconds> ago; the seeded history and
        a change waiting to be committed."""
        self.seed()
        self.run_kg("add-entity", "gate/canary-checks", "--name", "Canary checks")
        (self.root / "notes.txt").write_text("a change the next commit records\n")
        lock = self.root / ".git" / "index.lock"
        lock.write_text("")
        moment = datetime.now(UTC).timestamp() - age_seconds
        os.utime(lock, (moment, moment))
        return lock

    def test_a_git_lock_a_cut_left_long_ago_is_removed_and_the_history_records_again(self) -> None:
        lock = self.a_git_lock(60)
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            warning = store.commit(self.root, "kg propose after a power cut")
        self.assertEqual(warning, "")
        self.assertFalse(lock.exists())
        self.assertEqual(self.commits()[0], "kg propose after a power cut")
        self.assertIn("notes.txt", self.git_out(self.root, "ls-files").split())
        self.assertEqual(len(said.getvalue().splitlines()), 1)  # one note, naming the lock
        self.assertIn("index.lock", said.getvalue())

    def test_every_lock_git_leaves_long_ago_is_removed_with_a_note_each(self) -> None:
        """HEAD's, a branch's and the packed refs' locks too (R119); a ref lock held now stays."""
        git_dir = self.root / ".git"
        old = [self.a_git_lock(60), git_dir / "HEAD.lock", git_dir / "packed-refs.lock"]
        old.append(git_dir / f"{self.git_out(self.root, 'symbolic-ref', 'HEAD').strip()}.lock")
        young = git_dir / "refs" / "tags" / "v1.lock"
        young.parent.mkdir(parents=True, exist_ok=True)
        for lock, age in [*((lock, 45) for lock in old[1:]), (young, 1)]:
            lock.write_text("")
            moment = datetime.now(UTC).timestamp() - age
            os.utime(lock, (moment, moment))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            warning = store.commit(self.root, "kg propose after a power cut")
        self.assertEqual(warning, "")
        self.assertEqual([lock.name for lock in old if lock.exists()], [])
        self.assertTrue(young.exists())
        self.assertEqual(self.commits()[0], "kg propose after a power cut")
        notes = said.getvalue().splitlines()
        self.assertEqual(
            sorted(notes), sorted(n for n in notes if "the knowledge history records again" in n)
        )
        self.assertEqual(
            sorted(Path(n.split()[2].rstrip(",")).name for n in notes), sorted(lock.name for lock in old)
        )

    def test_a_git_lock_held_now_is_left_alone(self) -> None:
        lock = self.a_git_lock(1)
        before = self.commits()
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            warning = store.commit(self.root, "kg propose while git runs")
        self.assertIn("git commit failed", warning)
        self.assertTrue(lock.exists())
        self.assertEqual(self.commits(), before)
        self.assertEqual(said.getvalue(), "")

    def test_without_git_on_the_path_it_still_works(self) -> None:
        path = os.environ["PATH"]
        os.environ["PATH"] = "/nonexistent"
        self.addCleanup(os.environ.__setitem__, "PATH", path)
        self.seed()
        self.assertFalse((self.root / ".git").exists())


class LockingTest(KnowledgeTestCase):
    def test_concurrent_writers_never_lose_or_reuse_a_fact(self) -> None:
        self.seed()
        env = dict(
            os.environ,
            BOSUN_CONFIG_DIR=str(self.tmp),
            BOSUN_STATE_DIR=str(self.state),
            BOSUN_KNOWLEDGE_GIT="0",
        )
        script = str(ROOT / "lib" / "knowledge.py")
        writers = [
            subprocess.Popen(
                [
                    sys.executable,
                    script,
                    "propose",
                    "repo/invoice-exporter",
                    "caveat",
                    "--value",
                    f"gotcha {n}",
                    "--source",
                    "memory:a",
                ],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for n in range(12)
        ]
        for writer in writers:
            writer.communicate(timeout=60)
            self.assertEqual(writer.returncode, 0)
        facts = self.kb().facts
        self.assertEqual(len(facts), 12)
        self.assertEqual({f["value"] for f in facts.values()}, {f"gotcha {n}" for n in range(12)})
        self.assertEqual(len(self.log_lines()), 12)


class UpdateGateTest(KnowledgeTestCase):
    """Every write checks the store first (lib/update_gate.py), before anything is journaled."""

    def setUp(self) -> None:
        super().setUp()
        self.run_kg("add-entity", "repo/invoice-exporter", "--name", "invoice exporter")

    def files(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and not path.name.endswith(".lock")
        }

    def test_a_write_in_a_freeze_no_update_runs_is_cut_short(self) -> None:
        marked = update_state.UpdateState(
            id="u1",
            from_root="/Users/me/.local/share/bosun-versions/aaaaaaa11111",
            to_root="/Users/me/.local/share/bosun-versions/bbbbbbb22222",
            from_commit="aaaaaaa",
            to_commit="bbbbbbb",
            step="marked",
            started_at="2026-11-05T09:00:00+00:00",
            hold_until=10**12,
        )
        update_state.write(self.state, marked)
        before = self.files()
        with self.assertRaises(update_gate.UpdateCutShort):
            self.run_kg("add-entity", "gate/smoke-tests", "--name", "Smoke tests")
        self.assertEqual(self.files(), before)

    def test_a_write_into_a_newer_store_is_refused(self) -> None:
        store.write_format(store.Paths(self.root), store.KNOWLEDGE_FORMAT + 1)
        before = self.files()
        with self.assertRaises(update_gate.StoreNewer) as caught:
            self.run_kg("add-entity", "gate/smoke-tests", "--name", "Smoke tests")
        self.assertEqual(caught.exception.store, "knowledge base")
        self.assertEqual(self.files(), before)

    def test_a_write_into_an_older_store_is_refused(self) -> None:
        before = self.files()
        with (
            mock.patch.object(store, "KNOWLEDGE_FORMAT", store.KNOWLEDGE_FORMAT + 1),
            self.assertRaisesRegex(update_gate.StoreOlder, "run bosun update to migrate it"),
        ):
            self.run_kg("add-entity", "gate/smoke-tests", "--name", "Smoke tests")
        self.assertEqual(self.files(), before)

    def test_a_dry_run_is_no_write(self) -> None:
        store.write_format(store.Paths(self.root), store.KNOWLEDGE_FORMAT + 1)
        self.run_kg("expire", "--dry-run")


if __name__ == "__main__":
    unittest.main()
