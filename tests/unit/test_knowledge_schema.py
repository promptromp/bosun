"""Unit tests for the knowledge schema (lib/knowledge_schema.py): entity ids, predicates and what
they connect, the People whitelist (anything else about a person is refused, nothing inferred,
stated preferences only as stated), source kinds, sensitivity, and a site's schema.json
(new types and predicates, never about a person). Fictional data: acme's invoice exporter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import knowledge_schema as ks
from knowledge_schema import Claim, SchemaError


class EntityIdTest(unittest.TestCase):
    def test_ids_are_type_and_slug(self) -> None:
        self.assertEqual(ks.split_id("repo/invoice-exporter"), ("repo", "invoice-exporter"))
        for bad in ("invoice-exporter", "widget/x", "repo/Invoice", "repo/", "person/dana smith"):
            with self.subTest(bad=bad), self.assertRaises(SchemaError):
                ks.split_id(bad)

    def test_every_type_is_on_an_axis(self) -> None:
        self.assertEqual(set(ks.ENTITY_TYPES.values()), set(ks.AXES))


class PeopleWhitelistTest(unittest.TestCase):
    def refused(self, claim: Claim) -> SchemaError:
        with self.assertRaises(SchemaError) as caught:
            ks.check_fact(claim)
        return caught.exception

    def test_work_facts_about_a_person_pass(self) -> None:
        ks.check_fact(Claim("person/dana", "reviews", "repo/invoice-exporter"))
        ks.check_fact(Claim("person/dana", "timezone", value="Europe/Lisbon"))
        ks.check_fact(Claim("person/dana", "stated_preference", value="tables", authority=ks.USER_SAID))

    def test_anything_else_about_a_person_is_refused(self) -> None:
        for predicate in ("strengths", "mood", "rule", "caveat", "status", "purpose"):
            with self.subTest(predicate=predicate):
                error = self.refused(Claim("person/dana", predicate, value="x"))
                self.assertTrue(error.whitelist)
                self.assertIn("people whitelist", str(error))

    def test_nothing_inferred_about_a_person(self) -> None:
        error = self.refused(Claim("person/dana", "role", value="PM", authority=ks.INFERRED))
        self.assertTrue(error.whitelist)
        error = self.refused(Claim("step/rollout", "actor", "person/dana", authority=ks.INFERRED))
        self.assertTrue(error.whitelist)

    def test_stated_predicates_need_stated_authority(self) -> None:
        error = self.refused(Claim("person/dana", "stated_working_hours", value="9-5", authority=ks.OBSERVED))
        self.assertTrue(error.whitelist)
        ks.check_fact(
            Claim("person/dana", "stated_working_hours", value="9-5", authority=ks.STAKEHOLDER_SAID)
        )

    def test_a_person_is_an_object_only_where_it_names_who_acts(self) -> None:
        ks.check_fact(Claim("step/rollout", "actor", "person/dana"))
        error = self.refused(Claim("lesson/retired-env", "about", "person/dana"))
        self.assertTrue(error.whitelist)

    def test_whitelist_is_the_person_predicates(self) -> None:
        self.assertIn("reviews", ks.DEFAULT.people_whitelist)
        self.assertNotIn("rule", ks.DEFAULT.people_whitelist)


class FactShapeTest(unittest.TestCase):
    def test_object_or_value_exactly(self) -> None:
        with self.assertRaises(SchemaError):
            ks.check_fact(Claim("process/release", "has_step"))
        with self.assertRaises(SchemaError):
            ks.check_fact(Claim("process/release", "has_step", value="merge"))
        with self.assertRaises(SchemaError):
            ks.check_fact(Claim("process/release", "has_step", "repo/invoice-exporter"))
        ks.check_fact(Claim("process/release", "has_step", "step/merge"))

    def test_subject_types_and_sources(self) -> None:
        with self.assertRaises(SchemaError):
            ks.check_fact(Claim("repo/invoice-exporter", "decided", value="x"))
        with self.assertRaises(SchemaError):
            ks.check_fact(Claim("repo/invoice-exporter", "caveat", value="x", source_kinds=("rumour",)))

    def test_sensitivity_and_trust(self) -> None:
        self.assertEqual(ks.sensitivity_of("person/dana", None), ks.PERSONAL)
        self.assertEqual(ks.sensitivity_of("step/rollout", "person/dana"), ks.PERSONAL)
        self.assertEqual(ks.sensitivity_of("repo/invoice-exporter", None), ks.INTERNAL)
        self.assertTrue(ks.trusted("ledger"))
        for kind in ("slack", "pr", "ticket", "doc"):
            self.assertFalse(ks.trusted(kind))


class SiteSchemaTest(unittest.TestCase):
    def load(self, spec: object) -> ks.Schema:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.json"
            path.write_text(json.dumps(spec))
            return ks.load_site_schema(path)

    def test_no_file_is_the_default(self) -> None:
        self.assertIs(ks.load_site_schema(Path("/nonexistent/schema.json")), ks.DEFAULT)

    def test_new_types_and_predicates(self) -> None:
        schema = self.load(
            {
                "entity_types": {"runbook": "process"},
                "predicates": {"runbook_for": {"subjects": ["runbook"], "objects": ["service"]}},
                "single_valued": ["role"],
            }
        )
        ks.check_fact(Claim("runbook/restart", "runbook_for", "service/exporter-pdf"), schema)
        self.assertTrue(schema.predicates["role"].single)
        self.assertEqual(schema.predicates["runbook_for"].risk, ks.HIGH)

    def test_the_whitelist_cannot_be_extended(self) -> None:
        for spec in (
            {"predicates": {"hobby": {"subjects": ["person"], "value": True}}},
            {"predicates": {"mentor": {"subjects": ["team"], "objects": ["person"]}}},
            {"predicates": {"role": {"subjects": ["repo"]}}},
            {"entity_types": {"person": "process"}},
            {"entity_types": {"contact": "people"}},
        ):
            with self.subTest(spec=spec), self.assertRaises(SchemaError):
                self.load(spec)

    def test_a_site_predicate_may_say_itself(self) -> None:
        spec = {"entity_types": {"runbook": "process"}}
        said = self.load(
            {
                **spec,
                "predicates": {
                    "runbook_for": {
                        "subjects": ["runbook"],
                        "objects": ["service"],
                        "sentence": "is the runbook for {target}",
                    }
                },
            }
        )
        self.assertEqual(said.predicates["runbook_for"].phrase, "is the runbook for {target}")
        plain = self.load(
            {**spec, "predicates": {"runbook_for": {"subjects": ["runbook"], "objects": ["service"]}}}
        )
        self.assertEqual(plain.predicates["runbook_for"].phrase, "runbook for: {target}")
        with self.assertRaisesRegex(SchemaError, "sentence must contain"):
            self.load(
                {
                    **spec,
                    "predicates": {
                        "runbook_for": {
                            "subjects": ["runbook"],
                            "objects": ["service"],
                            "sentence": "is a runbook",
                        }
                    },
                }
            )


class SentenceTest(unittest.TestCase):
    def test_every_built_in_predicate_says_itself_with_its_target(self) -> None:
        for predicate in ks.PREDICATE_LIST:
            with self.subTest(predicate=predicate.name):
                self.assertIn(ks.TARGET, predicate.sentence)
                self.assertEqual(predicate.phrase, predicate.sentence)
                self.assertEqual(
                    predicate.sentence, predicate.sentence.lower()
                )  # mid-sentence, after the subject
        self.assertEqual([p.name for p in ks.PREDICATE_LIST if ks.WHEN in p.sentence], ["decided"])


if __name__ == "__main__":
    unittest.main()
