"""tools/docs_examples.py: the data behind the docs site's live examples, and the ledger guide's state
diagrams, which must say what lib/ledger.py's KINDS say."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import docs_examples
import ledger


ROOT = Path(__file__).resolve().parents[2]


DATA = docs_examples.page_data()


class LedgerExampleTest(unittest.TestCase):
    def test_states_run_in_the_order_work_moves(self) -> None:
        self.assertEqual(
            docs_examples.ordered_states(ledger.KINDS["task"]),
            ["proposed", "accepted", "declined", "delegated", "done", "dropped"],
        )

    def test_the_story_is_the_ask_and_what_came_of_it(self) -> None:
        story = DATA["ledger"]
        self.assertEqual([item["id"] for item in story["items"]], list(docs_examples.STORY))
        focus = next(item for item in story["items"] if item["id"] == story["focus"])
        self.assertEqual(
            {(link["rel"], link["id"]) for link in focus["links"]},
            {
                ("spawned_from", "slack:C0000000001/1700000001.000001"),
                ("produces", "mypr:acme/exporter#104"),
                ("tracks", "jira:EXP-12"),
            },
        )
        question = next(item for item in story["items"] if item["kind"] == "outreach")
        self.assertEqual(question["fields"]["parent"], story["focus"])

    def test_each_kind_carries_its_rules_from_the_table(self) -> None:
        kinds = DATA["ledger"]["kinds"]
        self.assertEqual(set(kinds), {item["kind"] for item in DATA["ledger"]["items"]})
        self.assertEqual(kinds["task"]["evidence_from_me"], ["accepted"])
        self.assertIn(["sent", "replied"], kinds["outreach"]["evidence_required"])
        self.assertTrue(kinds["ticket"]["mirror"])
        for name, kind in kinds.items():
            with self.subTest(kind=name):
                spec = ledger.KINDS[name]
                self.assertEqual(kind["transitions"], {s: sorted(t) for s, t in spec["transitions"].items()})

    def test_needs_you_is_the_open_proposals(self) -> None:
        self.assertEqual(
            [item["id"] for item in DATA["needs_you"]],
            ["learn:2026-10-20/1", "out:mypr:acme/exporter#102/3rgjxw", "task:2026-10-20/a3zm64"],
        )
        self.assertTrue(all(item["action"] for item in DATA["needs_you"]))


class KnowledgeExampleTest(unittest.TestCase):
    def test_every_entity_it_reaches_has_its_own_view(self) -> None:
        k = DATA["knowledge"]
        self.assertIn(k["center"], k["entities"])
        self.assertEqual(set(k["views"]), set(k["entities"]))
        for entity, view in k["views"].items():
            with self.subTest(entity=entity):
                self.assertEqual(view["nodes"][0], {"id": entity, "depth": 0, "parent": None})
                self.assertTrue({n["id"] for n in view["nodes"]} <= set(k["entities"]))

    def test_no_one_s_profile_is_shown(self) -> None:
        predicates = {fact["predicate"] for fact in DATA["knowledge"]["facts"]}
        self.assertFalse(predicates & docs_examples.PERSONAL)

    def test_what_you_said_is_confirmed_and_what_saved_rework_waits(self) -> None:
        self.assertEqual(docs_examples.fact_status({"authority": "user_said", "rework": True}), "confirmed")
        self.assertEqual(docs_examples.fact_status({"rework": True}), "proposed")
        self.assertEqual(docs_examples.fact_status({}), "confirmed")


class ScriptJsonTest(unittest.TestCase):
    def test_nothing_in_it_can_end_the_script_element(self) -> None:
        text = docs_examples.script_json({"title": "</script><script>alert(1)</script>"})
        self.assertNotIn("<", text)
        self.assertEqual(json.loads(text)["title"], "</script><script>alert(1)</script>")


MERMAID = re.compile(r"```mermaid\n(stateDiagram-v2\n.*?)```", re.DOTALL)
ARROW = re.compile(r"^\s*(\S+)\s*-->\s*([^:\s]+)", re.MULTILINE)
ALIAS = re.compile(r'^\s*state "([^"]+)" as (\S+)', re.MULTILINE)


def drawn_moves(diagram: str) -> set[tuple[str, str]]:
    names = {alias: name for name, alias in ALIAS.findall(diagram)}
    return {(names.get(a, a), names.get(b, b)) for a, b in ARROW.findall(diagram) if "[*]" not in (a, b)}


def table_moves(kind: str) -> set[tuple[str, str]]:
    """The kind's moves, but those out of a closed state that only another command makes."""
    spec = ledger.KINDS[kind]
    return {(a, b) for a, targets in spec["transitions"].items() for b in targets if a != "dismissed"}


class LedgerGuideTest(unittest.TestCase):
    def test_its_state_diagrams_match_the_kinds_table(self) -> None:
        diagrams = MERMAID.findall((ROOT / "docs/ledger.md").read_text())
        self.assertEqual(len(diagrams), 2)
        for kind, diagram in zip(("task", "outreach"), diagrams, strict=True):
            with self.subTest(kind=kind):
                self.assertEqual(drawn_moves(diagram), table_moves(kind))


if __name__ == "__main__":
    unittest.main()
