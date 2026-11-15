"""tools/docs_examples.py: the data behind the docs site's live examples, and the ledger guide's state
diagrams, which must say what lib/ledger.py's KINDS say."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any, ClassVar


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


class ChartTest(unittest.TestCase):
    """The home page's chart (docs/assets/home/chart.js) is a picture, not a live example, so its names are
    written in the script: they must still be the acme world's, as the examples below it show it."""

    CHART: ClassVar[str] = (ROOT / "docs/assets/home/chart.js").read_text()
    ISLANDS: ClassVar[dict[str, set[str]]] = {
        "people": {"person", "team"},
        "process": {"process", "gate", "step", "channel", "system"},
        "product": {"repo", "environment", "lesson"},
    }

    @classmethod
    def fixture(cls) -> list[dict[str, Any]]:
        lines = docs_examples.KNOWLEDGE_FIXTURE.read_text().splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def marks(self) -> list[tuple[str, str, str, str]]:
        mark = r'\["([a-z]+/[^"]+)", "(people|process|product)", "([^"]+)", "([a-z]+)"'
        found = re.findall(mark, self.CHART)
        self.assertGreater(len(found), 5)
        return found

    def test_its_landmarks_are_the_fixture_s_entities_on_their_island(self) -> None:
        names = {r["id"]: r["name"] for r in self.fixture() if r["type"] == "entity"}
        for entity_id, island, label, kind in self.marks():
            with self.subTest(entity=entity_id):
                self.assertIn(entity_id, names)
                self.assertTrue(names[entity_id].lower().startswith(label.lower()), names[entity_id])
                self.assertEqual(kind, docs_examples.entity_type(entity_id))
                self.assertIn(kind, self.ISLANDS[island])

    def test_its_lines_are_the_fixture_s_facts(self) -> None:
        records = [r for r in self.fixture() if r["type"] == "fact"]
        facts = {(r["subject"], r["predicate"], r.get("object")) for r in records}
        lines = re.findall(r'\["([a-z]+/[^"]+)", "([a-z]+/[^"]+)", "([a-z_]+)"\]', self.CHART)
        self.assertTrue(lines)
        for subject, obj, predicate in lines:
            with self.subTest(fact=(subject, predicate, obj)):
                self.assertIn((subject, predicate, obj), facts)

    def test_its_log_is_the_ledger_example_s_story_and_points_at_the_chart(self) -> None:
        page = (ROOT / "docs/index.md").read_text()
        log = page[page.index('<ol id="bx-log">') : page.index("</ol>", page.index('<ol id="bx-log">'))]
        self.assertTrue(set(re.findall(r'<span class="id">([^<]+)</span>', log)) <= set(docs_examples.STORY))
        boats = set(re.findall(r'\["(b-[a-z]+)", "acme/', self.CHART))
        targets = {m[0] for m in self.marks()} | boats | {"orchestrator"}
        for at in re.findall(r'data-at="([^"]+)"', page):
            with self.subTest(at=at):
                self.assertIn(at, targets)


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


class MenubarExampleTest(unittest.TestCase):
    STATES: ClassVar[dict[str, Any]] = {state["key"]: state for state in DATA["menubar"]}

    def titles(self, key: str) -> list[list[str]]:
        return [[item["title"] for item in section] for section in self.STATES[key]["sections"]]

    def test_every_title_is_the_apps_own(self) -> None:
        # The menu's rules are restated in Python; a title the app no longer has fails here.
        source = (ROOT / "app/Sources/BosunMenuCore/MenuModel.swift").read_text()
        for state in DATA["menubar"]:
            for section in state["sections"]:
                for item in section:
                    with self.subTest(state=state["key"], title=item["title"]):
                        self.assertIn('"' + item["title"].split(":")[0], source)

    def test_the_icon_is_the_apps_picture(self) -> None:
        app = (ROOT / "app/Resources/MenuBarIcon@2x.png").read_bytes()
        self.assertEqual((ROOT / "docs/assets/home/menubar-icon.png").read_bytes(), app)

    def test_running_offers_stop_and_no_start(self) -> None:
        running = self.STATES["running"]
        self.assertEqual((running["dot"], running["badge"]), ("green", None))
        self.assertEqual(running["sentence"], "bosun is running")
        self.assertEqual(self.titles("running")[-1], ["Stop bosun…", "Quit bosun.app"])
        self.assertNotIn("Start bosun", str(self.titles("running")))

    def test_resting_is_grey_with_start_now_first_and_no_stop(self) -> None:
        resting = self.STATES["resting"]
        self.assertEqual(resting["dot"], "grey")
        self.assertTrue(resting["sentence"].startswith("Resting: outside your hours"))
        self.assertEqual(self.titles("resting")[0], ["Start bosun now"])
        self.assertEqual(self.titles("resting")[-1], ["Quit bosun.app"])

    def test_a_warning_shows_whats_wrong_and_marks_the_icon(self) -> None:
        warning = self.STATES["warning"]
        self.assertEqual((warning["dot"], warning["badge"]), ("yellow", "warning"))
        self.assertEqual(self.titles("warning")[0], ["Show What's Wrong…"])

    def test_an_update_ready_names_the_release_and_marks_the_icon(self) -> None:
        update = self.STATES["update"]
        self.assertEqual(update["badge"], "update")
        offered = [item for section in update["sections"] for item in section if item["badge"]]
        self.assertEqual([item["title"] for item in offered], ["Update Available: v2026.11.11…"])


if __name__ == "__main__":
    unittest.main()
