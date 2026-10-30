"""Unit tests for the knowledge base (lib/knowledge*.py, through `bosun kg`'s commands): the
append-only fact log, supersede timelines and --as-of, entity resolution and merge suggestions,
the auto-confirm rules (my quote verified in a transcript, never from a tool result or pasted
text; two trusted sources; profile sources), re-observation and expiry, contradictions and the
other lint findings, retention and delete on request, the review batch (grouping, cap, priority,
one-line decisions), import (dry run first, refusals), context packs (size, citations, nothing
unconfirmed or restricted), locking across processes and the git history.
Fictional data: acme's invoice exporter (tests/fixtures/knowledge/acme-import.jsonl)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import knowledge
import knowledge_model as km
import knowledge_review as kr
import knowledge_store as store
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
    """A scratch knowledge directory (no git unless a test asks), a pinned clock, and a few
    entities of the acme world."""

    git = False

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "knowledge"
        self.now = START
        knowledge.CLOCK = lambda: self.now
        knowledge.configure(self.root, git=self.git)
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
        self.run_kg("confirm", "f1")
        self.run_kg("supersede", "f1", "--close", "--note", "merged into another step")
        lines = self.log_lines()
        self.assertEqual(lines[: len(first)], first)
        self.assertEqual([json.loads(line)["op"] for line in lines], ["add", "update", "update"])
        self.assertEqual(self.fact("f1")["status"], km.CONFIRMED)
        self.assertIsNotNone(self.fact("f1")["valid_to"])

    def test_a_bad_line_is_named_not_skipped(self) -> None:
        self.seed()
        with (self.root / "facts.jsonl").open("a") as handle:
            handle.write("{not json\n")
        with self.assertRaisesRegex(Exception, r"facts.jsonl:1 is not valid JSON"):
            self.run_kg("stats")

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
        fact = self.fact("f1")
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
        self.assertEqual(self.fact("f1")["status"], km.PROPOSED)
        out = self.run_kg(
            "propose", "releases", "has_step", "merge to main", "--source", "report:task:2026-10-01/1"
        )
        self.assertIn("f1 observed, confirmed (two independent trusted sources)", out)
        self.assertEqual(len(self.fact("f1")["sources"]), 2)

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
        self.assertEqual(self.fact("f1")["status"], km.PROPOSED)

    def test_high_risk_and_person_facts_need_more_than_two_sources(self) -> None:
        self.seed()
        self.propose_smoke("ledger:x", "report:y")
        self.assertEqual(self.fact("f1")["status"], km.PROPOSED)
        self.run_kg(
            "propose", "person/dana", "reviews", "exporter", "--source", "ledger:x", "--source", "report:y"
        )
        self.assertEqual(self.fact("f2")["status"], km.PROPOSED)

    def test_a_profile_source_confirms_title_and_timezone_only(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:directory"
        )
        self.assertEqual(self.fact("f1")["status"], km.CONFIRMED)
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "profile:directory")
        self.assertEqual(self.fact("f2")["status"], km.PROPOSED)

    def test_a_conflicting_single_value_waits_for_review(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:directory"
        )
        out = self.run_kg("propose", "person/dana", "timezone", "--value", "UTC", "--source", "profile:hr")
        self.assertIn("contradicts f1", out)
        self.assertEqual(self.fact("f2")["status"], km.PROPOSED)

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
        self.assertEqual(km.effective_status(self.fact("f1"), self.now), km.EXPIRED)
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
        self.assertIn("f1 observed, proposed", out)
        fact = self.fact("f1")
        self.assertEqual(fact["observations"], 2)
        self.assertEqual(km.parse_time(fact["observed_at"]), self.now)
        self.assertEqual(len(fact["sources"]), 1)  # the same source is not added twice

    def test_expire_records_expiry(self) -> None:
        self.seed()
        self.run_kg("propose", "repo/invoice-exporter", "caveat", "--value", "x", "--source", "memory:ci.md")
        self.later(km.PROPOSAL_DAYS + 1)
        self.assertIn("1 expired", self.run_kg("expire", "--dry-run"))
        self.assertEqual(self.fact("f1")["status"], km.PROPOSED)
        self.run_kg("expire")
        self.assertEqual(self.fact("f1")["status"], km.EXPIRED)

    def test_rejected_facts_stay_rejected_unless_I_say_so(self) -> None:
        self.seed()
        self.run_kg("propose", "process/release", "gated_by", "gate/smoke-tests", "--source", "memory:x")
        self.run_kg("reject", "f1")
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
        self.assertIn("f1 observed, confirmed", out)


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
        self.run_kg("confirm", "f1")
        self.later(10)
        out = self.run_kg("supersede", "f1", "--value", "retired", "--source", "ledger:task:2026-10-02/3")
        self.assertIn("f1 superseded by f2", out)
        old, new = self.fact("f1"), self.fact("f2")
        self.assertEqual(old["superseded_by"], "f2")
        self.assertEqual(new["status"], km.CONFIRMED)
        now = self.run_kg("facts", "staging 2")
        self.assertIn("retired", now)
        self.assertNotIn("'active'", now)
        before = self.run_kg("facts", "staging-2", "--as-of", "2026-10-25")
        self.assertIn("'active'", before)
        self.assertNotIn("retired", before)
        self.assertIn("f1", self.run_kg("facts", "staging-2", "--closed"))
        with self.assertRaisesRegex(KnowledgeError, "already closed"):
            self.run_kg("supersede", "f1", "--close")

    def test_confirming_a_single_valued_fact_supersedes_the_current_value(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:directory"
        )
        self.run_kg("propose", "person/dana", "timezone", "--value", "UTC", "--source", "profile:hr")
        out = self.run_kg("confirm", "f2")
        self.assertIn("f1 superseded by f2", out)
        self.assertEqual(self.fact("f1")["valid_to"], km.iso(self.now))

    def test_a_correction_in_my_words_needs_the_quote(self) -> None:
        self.seed()
        self.run_kg("propose", "environment/staging-2", "status", "--value", "active", "--source", "memory:a")
        transcript = self.transcript(typed("other words", START))
        with self.assertRaisesRegex(KnowledgeError, "not in anything you typed"):
            self.run_kg(
                "supersede",
                "f1",
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
        self.run_kg("confirm", "f1", "f2")
        self.assertNotIn("contradiction", self.run_kg("lint"))
        (self.root / "schema.json").write_text(json.dumps({"single_valued": ["role"]}))
        out = self.run_kg("lint")
        self.assertIn("contradiction: person/dana has 2 current values for role (f1, f2)", out)

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
        self.assertIn("stale: f1 not observed for over 180 days", out)
        self.assertIn("orphan: environment/staging-2 has no facts", out)
        self.assertIn("person/robin-a, person/robin-b share the first name 'robin'", out)
        self.assertIn("merge-suggestion: repo/invoice-exporter and repo/invoice-exporters", out)
        self.run_kg("propose", "person/robin-a", "distinct_from", "person/robin-b", "--source", "user:x")
        self.run_kg("confirm", "f2")
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
        self.run_kg("confirm", "f2")
        self.later(km.RETENTION_DAYS + 1)
        self.assertIn("retention: 1 person facts unseen for 365 days", self.run_kg("lint"))
        out = self.run_kg("expire")
        self.assertIn("1 person facts past retention deleted", out)
        self.assertEqual(sorted(self.kb().facts), ["f2"])
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
        self.assertEqual(record["hash"], km.hash_id("person/dana"))
        self.assertEqual((record["facts"], record["identifiers"]), (1, 1))
        with self.assertRaisesRegex(KnowledgeError, "deleted on request"):
            self.run_kg("add-entity", "person/dana")
        self.run_kg("add-entity", "person/dana", "--allow-forgotten")

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
        self.assertIn("   b. f3 Release process includes the step Gradual rollout (step 3)", out)

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
        self.assertEqual(self.kb().facts["f3"]["status"], km.PROPOSED)
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
        self.run_kg("confirm", "f3")  # release has_step rollout: now its neighbour counts
        pack = self.run_kg("context", "--for", "release")
        self.assertIn("Knowledge for Release process (process/release)", pack)
        self.assertIn("- Release process has step Merge to main (step 1) [f1; ledger:", pack)
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
                self.assertRegex(line, r"\[f\d+; \w+:")


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
        self.run_kg("confirm", "f3")
        one = self.run_kg("neighbors", "release")
        self.assertNotIn("Deploy bot", one)
        two = self.run_kg("neighbors", "release", "--hops", "2")
        self.assertIn("2 f5 Gradual rollout actor Deploy bot", two)

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

    def test_without_git_on_the_path_it_still_works(self) -> None:
        path = os.environ["PATH"]
        os.environ["PATH"] = "/nonexistent"
        self.addCleanup(os.environ.__setitem__, "PATH", path)
        self.seed()
        self.assertFalse((self.root / ".git").exists())


class LockingTest(KnowledgeTestCase):
    def test_concurrent_writers_never_lose_or_reuse_a_fact(self) -> None:
        self.seed()
        env = dict(os.environ, BOSUN_CONFIG_DIR=str(self.tmp), BOSUN_KNOWLEDGE_GIT="0")
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


if __name__ == "__main__":
    unittest.main()
