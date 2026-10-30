"""Unit tests for the console's Knowledge view: the view models (lib/console_knowledge.py: overview
counts and search, entity pages with grouped facts, history, backlinks, sources, privacy and
related ledger items, process flows, the review batch and decisions), the neighbourhood graph
(lib/console_kg_graph.py: the cap, hops, determinism, bounds) and the I/O (lib/console_kb.py:
nothing restricted read, nothing created, a decision saved like `kg batch apply`).
Fictional data: acme's invoice exporter (tests/fixtures/knowledge/acme-import.jsonl and
tests/fixtures/console/acme-ledger.json)."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_kb
import console_kg_graph as graph
import console_knowledge as ck
import knowledge
import knowledge_model as km
import knowledge_review as kr
import knowledge_schema as ks
from console_data import QueryError


ROOT = Path(__file__).resolve().parents[2]
KG_FIXTURE = ROOT / "tests" / "fixtures" / "knowledge" / "acme-import.jsonl"
LEDGER = json.loads((ROOT / "tests" / "fixtures" / "console" / "acme-ledger.json").read_text())
START = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
SECRET_ID = "U0000000099"  # noqa: S105  # a fictional restricted identifier: it must never reach a model


class KnowledgeWorld(unittest.TestCase):
    """The fixture knowledge base imported in a scratch folder (no git), with a pinned clock."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "config" / "knowledge"
        self.now = START
        knowledge.CLOCK = lambda: self.now
        self.addCleanup(setattr, knowledge, "CLOCK", knowledge.clock)
        knowledge.configure(self.root, git=False)
        self.kg("import", str(KG_FIXTURE), "--apply", "--auto-confirm")
        self.settings = console_kb.Settings(root=self.root, git=False)

    def kg(self, *argv: str) -> str:
        args = knowledge.build_parser().parse_args(list(argv))
        result = args.func(args)
        return str(result[0] if isinstance(result, tuple) else result)

    def kb(self) -> km.KB:
        return console_kb.read_kb(self.root, self.now)

    def fact_id(self, subject: str, predicate: str) -> str:
        return next(
            str(f["id"])
            for f in self.kb().facts.values()
            if (f["subject"], f["predicate"]) == (subject, predicate) and km.is_current(f)
        )

    def page(self, entity_id: str) -> dict[str, Any]:
        return ck.entity_model(self.kb(), entity_id, LEDGER)

    def review(self, saved: Any = None) -> dict[str, Any]:
        return ck.review_model(self.kb(), saved, self.settings.limits, LEDGER, UTC)


class OverviewTest(KnowledgeWorld):
    def overview(self, **params: str) -> dict[str, Any]:
        search = ck.Search.parse({k: [v] for k, v in params.items()})
        return ck.overview_model(self.kb(), LEDGER, search, self.review())

    def test_cards_count_entities_and_facts_per_axis(self) -> None:
        model = self.overview()
        self.assertEqual(model["totals"], {"entities": 15, "facts": 20, "confirmed": 6, "proposed": 14})
        cards = {c["name"]: (c["entities"], c["confirmed"], c["proposed"]) for c in model["cards"]}
        self.assertEqual(
            cards, {"people": (3, 1, 5), "process": (7, 3, 4), "product": (3, 2, 3), "systems": (2, 0, 2)}
        )
        self.assertEqual(sum(c[0] for c in cards.values()), 15)
        systems = next(c for c in model["cards"] if c["name"] == "systems")
        self.assertEqual([e["id"] for e in systems["list"]], ["system/deploy-bot", "environment/staging-2"])

    def test_recently_changed_facts_come_newest_first(self) -> None:
        self.now += timedelta(hours=1)
        self.kg("confirm", self.fact_id("process/release", "rule"))
        process = next(c for c in self.overview()["cards"] if c["name"] == "process")
        self.assertEqual(process["recent"][0]["predicate"], "rule")
        self.assertLessEqual(len(process["recent"]), ck.RECENT_FACTS)

    def test_search_finds_entities_by_name_and_facts_by_text(self) -> None:
        search = self.overview(q="deploy")["search"]
        self.assertEqual(
            [e["id"] for e in search["entities"]], ["system/deploy-bot", "channel/deploys", "process/release"]
        )
        self.assertIn("deploys to staging first", search["facts"][0]["value"])
        by_alias = self.overview(q="exporter")["search"]
        self.assertEqual(by_alias["entities"][0]["id"], "repo/invoice-exporter")
        people = self.overview(type="person")["search"]
        self.assertEqual({e["id"] for e in people["entities"]}, {"person/dana", "person/lee"})
        self.assertIsNone(self.overview()["search"])

    def test_a_query_it_cannot_use_is_refused(self) -> None:
        with self.assertRaises(QueryError):
            self.overview(type="planet")
        with self.assertRaises(QueryError):
            ck.Search.parse({"q": ["x" * (ck.MAX_QUERY + 1)]})
        with self.assertRaises(QueryError):
            ck.Search.parse({"q": ["a", "b"]})

    def test_keys_are_opaque(self) -> None:
        keys = ck.keys(self.kb())
        self.assertEqual(len(keys), 15)
        for key, entity_id in keys.items():
            self.assertRegex(key, r"^[0-9a-f]{16}$")
            self.assertNotIn(entity_id.partition("/")[2], key)
            self.assertEqual(ck.entity_key(entity_id), key)

    def test_no_knowledge_base_is_an_empty_state(self) -> None:
        empty = console_kb.overview(console_kb.Settings(root=self.tmp / "none"), LEDGER, {}, self.now)
        self.assertFalse(empty["exists"])
        self.assertIn("bosun kg import", empty["hint"])
        self.assertFalse((self.tmp / "none").exists())  # reading created nothing
        self.assertFalse(console_kb.review(console_kb.Settings(), LEDGER, self.now)["exists"])

    def test_growth_counts_the_facts_shown_and_the_entities(self) -> None:
        growth = console_kb.growth(self.settings, self.now)
        assert growth is not None
        kb = self.kb()
        shown = [f for f in kb.facts.values() if ck.visible(kb, f)]
        self.assertEqual(len(growth["facts"]), len(shown))
        self.assertEqual(growth["entities"], len(kb.entities))
        self.assertTrue(all(f["recorded_at"] for f in growth["facts"]))
        self.assertIsNone(console_kb.growth(console_kb.Settings(root=self.tmp / "none"), self.now))

    def test_an_unreadable_knowledge_base_says_why(self) -> None:
        (self.root / "facts.jsonl").write_text("{not json\n")
        model = console_kb.overview(self.settings, LEDGER, {}, self.now)
        self.assertFalse(model["exists"])
        self.assertIn("could not be read", model["error"])
        self.assertIn("could not be read", (console_kb.growth(self.settings, self.now) or {})["error"])


class EntityPageTest(KnowledgeWorld):
    def test_facts_are_grouped_by_predicate_with_steps_in_order(self) -> None:
        page = self.page("process/release")
        self.assertEqual(page["entity"]["name"], "Release process")
        self.assertEqual(page["entity"]["aliases"]["strong"], ["releases", "release flow"])
        self.assertEqual([g["predicate"] for g in page["facts"]], ["has_step", "rule"])
        steps = page["facts"][0]["facts"]
        self.assertEqual([f["order"] for f in steps], [1, 2, 3])
        self.assertEqual([f["status"] for f in steps], ["confirmed", "confirmed", "proposed"])
        self.assertEqual(page["facts"][1]["facts"][0]["authority_label"], "user")
        self.assertIsNone(page["privacy"])
        self.assertIsNone(page["related"])

    def test_superseded_and_rejected_facts_go_to_history(self) -> None:
        old = self.fact_id("environment/staging-2", "status")
        self.kg("confirm", old)
        self.now += timedelta(days=1)
        self.kg("supersede", old, "--value", "running", "--source", "ledger:task:2026-10-20/1")
        caveat = self.fact_id("repo/invoice-exporter", "caveat")
        self.kg("reject", caveat)
        env = self.page("environment/staging-2")
        self.assertEqual([f["value"] for f in env["facts"][0]["facts"]], ["running"])
        (closed,) = env["history"]
        self.assertEqual((closed["id"], closed["current"]), (old, False))
        self.assertEqual(closed["replaced_by"]["text"], "status 'running'")
        repo = self.page("repo/invoice-exporter")
        self.assertEqual([f["id"] for f in repo["history"]], [caveat])
        self.assertNotIn("caveat", [g["predicate"] for g in repo["facts"]])

    def test_backlinks_are_the_facts_that_point_here(self) -> None:
        links = self.page("repo/invoice-exporter")["backlinks"]
        self.assertEqual(
            sorted((f["subject"]["id"], f["predicate"]) for f in links),
            [("person/dana", "reviews"), ("person/lee", "reviews")],
        )

    def test_sources_link_only_to_web_urls_and_known_ledger_items(self) -> None:
        self.kg(
            "propose",
            "repo/invoice-exporter",
            "caveat",
            "--value",
            "flaky on Mondays",
            "--source",
            "doc:https://example.com/acme/ci",
            "--source",
            "doc:javascript:alert(1)",
            "--source",
            "ledger:task:2026-10-20/1",
            "--source",
            "ledger:task:1999-01-01/1",
        )
        fact = next(
            f
            for g in self.page("repo/invoice-exporter")["facts"]
            for f in g["facts"]
            if "Mondays" in str(f["value"])
        )
        shown = {s["ref"]: (s["url"], s["item"]) for s in fact["sources"]}
        self.assertEqual(shown["https://example.com/acme/ci"], ("https://example.com/acme/ci", None))
        self.assertEqual(shown["javascript:alert(1)"], (None, None))
        self.assertEqual(shown["task:2026-10-20/1"], (None, "task:2026-10-20/1"))
        self.assertEqual(shown["task:1999-01-01/1"], (None, None))

    def test_a_person_page_shows_only_whitelisted_facts_and_how_to_delete(self) -> None:
        kb = self.kb()
        planted = dict(
            kb.facts[self.fact_id("person/dana", "timezone")], id="f999", predicate="mood", value="x"
        )
        kb.facts["f999"] = planted  # the schema refuses this; the page must not show it either
        page = ck.entity_model(kb, "person/dana", LEDGER)
        self.assertEqual([g["predicate"] for g in page["facts"]], ["member_of", "timezone", "reviews"])
        self.assertIn("bosun kg forget person/dana", page["privacy"])
        self.assertEqual(page["entity"]["summary"], "")
        self.assertNotIn("mood", json.dumps(page))

    def test_related_ledger_items_by_person_and_by_repo(self) -> None:
        dana = self.page("person/dana")["related"]
        self.assertEqual(
            [(r["id"], r["how"]) for r in dana["items"]], [("out:task:2026-10-20/1/1", "recipient")]
        )
        repo = self.page("repo/invoice-exporter")["related"]
        self.assertIn("mypr:acme/exporter#102", [r["id"] for r in repo["items"]])
        self.assertIn("task:2026-10-20/1", [r["id"] for r in repo["items"]])  # by its repo field
        self.assertNotIn("review:acme/ledger-sync#57", [r["id"] for r in repo["items"]])

    def test_a_unique_first_name_matches(self) -> None:
        self.kg("add-entity", "person/priya-s", "--name", "Priya Sample")  # task:2026-10-20/1's requester
        related = self.page("person/priya-s")["related"]["items"]
        self.assertEqual([(r["id"], r["how"]) for r in related], [("task:2026-10-20/1", "requester")])

    def test_a_shared_first_name_matches_by_name_or_slug_only(self) -> None:
        self.kg("add-entity", "person/dana-two", "--name", "Dana Other")
        self.kg("add-entity", "person/lena-one", "--name", "Lena One")
        self.kg("add-entity", "person/lena-two", "--name", "Lena Two")
        self.assertEqual(len(self.page("person/dana")["related"]["items"]), 1)  # its slug is "dana"
        for person in ("person/dana-two", "person/lena-one", "person/lena-two"):
            with self.subTest(person=person):
                self.assertEqual(self.page(person)["related"]["items"], [])
        self.kg("add-entity", "person/lena", "--name", "Lena Unique")  # out:2026-10-20/1 went to "Lena"
        self.assertEqual(len(self.page("person/lena")["related"]["items"]), 1)


class FlowTest(KnowledgeWorld):
    def test_steps_in_order_with_actors_gates_and_systems(self) -> None:
        self.kg("add-entity", "step/review", "--name", "Code review")
        source = ("--source", "ledger:task:2026-10-20/1")
        self.kg("propose", "process/release", "has_step", "step/review", "--order", "0", *source)
        self.kg("propose", "step/rollout", "gated_by", "gate/smoke-tests", *source)
        self.kg("propose", "step/merge", "uses", "repo/invoice-exporter", *source)
        flow = self.page("process/release")["flow"]
        names = [s["step"]["name"] for s in flow["steps"]]
        self.assertEqual(names, ["Code review", "Merge to main", "Smoke tests", "Gradual rollout"])
        self.assertEqual([s["number"] for s in flow["steps"]], [1, 2, 3, 4])
        rollout = flow["steps"][3]
        self.assertEqual([a["ref"]["id"] for a in rollout["actors"]], ["system/deploy-bot"])
        self.assertEqual([g["ref"]["id"] for g in rollout["gates"]], ["gate/smoke-tests"])
        self.assertEqual(flow["steps"][1]["systems"][0]["ref"]["id"], "repo/invoice-exporter")
        self.assertIn("canary checks pass", flow["process"]["notes"][0]["text"])

    def test_only_a_process_with_steps_has_a_flow(self) -> None:
        self.assertIsNone(self.page("process/hotfix")["flow"])
        self.assertIsNone(self.page("repo/invoice-exporter")["flow"])


def star(center: str, count: int) -> list[graph.Link]:
    return [graph.Link(f"f{i}", center, f"repo/r{i:02d}", "depends_on", True) for i in range(count)]


class GraphTest(KnowledgeWorld):
    def test_the_graph_is_capped_and_counts_the_rest(self) -> None:
        ego = graph.ego("repo/hub", star("repo/hub", 40))
        self.assertEqual(len(ego.nodes), graph.MAX_NODES)
        self.assertEqual(ego.more, 40 - (graph.MAX_NODES - 1))
        self.assertEqual(len(ego.edges), graph.MAX_NODES - 1)

    def test_two_hops_at_most(self) -> None:
        chain = [graph.Link(f"f{i}", f"repo/r{i}", f"repo/r{i + 1}", "depends_on", True) for i in range(4)]
        ego = graph.ego("repo/r0", chain)
        self.assertEqual(
            [(n.id, n.depth) for n in ego.nodes], [("repo/r0", 0), ("repo/r1", 1), ("repo/r2", 2)]
        )

    def test_the_layout_is_deterministic_and_inside_the_picture(self) -> None:
        links = [*star("repo/hub", 12), graph.Link("g1", "repo/r01", "repo/x", "depends_on", False)]
        one, two = graph.ego("repo/hub", links), graph.ego("repo/hub", list(links))
        self.assertEqual([(n.x, n.y) for n in one.nodes], [(n.x, n.y) for n in two.nodes])
        self.assertEqual((one.nodes[0].x, one.nodes[0].y), (graph.WIDTH / 2, graph.HEIGHT / 2))
        for node in one.nodes:
            self.assertTrue(graph.MARGIN_X <= node.x <= graph.WIDTH - graph.MARGIN_X)
            self.assertTrue(graph.MARGIN_Y <= node.y <= graph.HEIGHT - graph.MARGIN_Y)
        self.assertFalse(
            next(e for e in one.edges if e.predicates == ("depends_on",) and not e.confirmed).confirmed
        )

    def test_the_page_graph_does_not_depend_on_the_order_facts_were_loaded(self) -> None:
        kb = self.kb()
        first = ck.graph_model(kb, "process/release")
        items = list(kb.facts.items())
        random.Random(7).shuffle(items)  # noqa: S311  # a fixed shuffle, not cryptography
        kb.facts = dict(items)
        self.assertEqual(ck.graph_model(kb, "process/release"), first)
        self.assertEqual(first["nodes"][0]["id"], "process/release")
        self.assertTrue(all(n["key"] for n in first["nodes"]))
        self.assertLessEqual(len(first["nodes"]), graph.MAX_NODES)

    def test_labels_are_short(self) -> None:
        self.assertEqual(graph.short_label("a" * 40), "a" * (graph.LABEL_LIMIT - 3) + "...")
        self.assertEqual(graph.short_label("Smoke  tests"), "Smoke tests")


class ReviewTest(KnowledgeWorld):
    def test_the_batch_is_lettered_like_the_cli(self) -> None:
        model = self.review()
        self.assertEqual(model["summary"]["source"], ck.COMPUTED)
        self.assertEqual((model["summary"]["groups"], model["summary"]["facts"]), (3, 14))
        first = model["groups"][0]
        self.assertEqual([f["ref"] for f in first["facts"]], ["1a", "1b", "1c", "1d", "1e"])
        printed = self.kg("batch")
        for group in model["groups"]:
            self.assertIn(f"{group['number']}. {group['title']}", printed)
            for fact in group["facts"]:
                self.assertIn(f"{fact['letter']}. {fact['id']} ", printed)

    def test_a_reviewer_gets_the_whole_text_of_long_facts(self) -> None:
        # Before 2026-10-24 the card's "already known" lines were cut at 120 characters.
        long_value = "a gotcha that takes a while to explain, " * 6
        self.kg("add-entity", "process/close", "--name", "Month-end close")
        self.kg("propose", "process/close", "caveat", "--value", long_value, "--source", "memory:close.md")
        self.kg(
            "propose", "process/close", "purpose", "--value", "books close", "--source", "memory:close.md"
        )
        kb = self.kb()
        caveat = kb.facts[self.fact_id("process/close", "caveat")]
        purpose = kb.facts[self.fact_id("process/close", "purpose")]
        self.assertGreater(len(long_value.strip()), kr.VALUE_LIMIT)
        self.assertIn(long_value.strip(), ck.fact_view(kb, caveat, LEDGER)["text"])
        self.assertTrue(any(long_value.strip() in c["text"] for c in ck.context_facts(kb, purpose)))
        self.assertTrue(kr.statement(kb, caveat).endswith("...'"))  # the CLI's one-liners stay short

    def test_facts_are_said_in_plain_words(self) -> None:
        kb = self.kb()
        step = kb.facts[self.fact_id("process/release", "has_step")]
        words = ck.fact_view(kb, step, LEDGER)["sentence"]
        self.assertEqual(words[0], {"text": "includes the step "})
        self.assertEqual(words[1]["entity"]["id"], step["object"])
        self.assertTrue(words[-1]["text"].startswith(" (step "))
        self.kg("add-entity", "decision/numbering", "--name", "Invoice numbering")
        self.kg(
            "propose",
            "decision/numbering",
            "decided",
            "--value",
            "one sequence per year",
            "--valid-from",
            "2026-10-15",
            "--source",
            "memory:a.md",
        )
        kb = self.kb()
        dated = ck.fact_view(kb, kb.facts[self.fact_id("decision/numbering", "decided")], LEDGER)["sentence"]
        self.assertEqual(
            dated, [{"text": "was decided on Oct 15, 2026: "}, {"value": "one sequence per year"}]
        )
        self.kg(
            "supersede",
            self.fact_id("decision/numbering", "decided"),
            "--value",
            "one sequence per country",
            "--source",
            "memory:b.md",
        )
        kb = self.kb()
        undated = ck.fact_view(kb, kb.facts[self.fact_id("decision/numbering", "decided")], LEDGER)[
            "sentence"
        ]
        self.assertEqual(undated[0], {"text": "was decided: "})  # recorded now: no claim about when

    def test_a_group_is_shown_as_blocks_by_entity(self) -> None:
        group = self.review()["groups"][0]
        blocks = group["blocks"]
        self.assertEqual([b["subject"]["id"] for b in blocks][:2], ["process/release", "process/hotfix"])
        self.assertEqual(blocks[0]["subject"]["type_label"], "process")
        refs = [r for b in blocks for r in [*b["facts"], *b["links"]]]
        self.assertEqual(sorted(refs), sorted(f["ref"] for f in group["facts"]))
        shown = {f["id"] for f in group["facts"]}
        self.assertTrue(all(k["id"] not in shown for b in blocks for k in b["known"]))
        self.assertLessEqual(max(len(b["known"]) for b in blocks), ck.CONTEXT_FACTS)

    def test_facts_that_place_an_entity_are_its_subtitle(self) -> None:
        kb = self.kb()
        subject = {"id": "decision/numbering"}
        views: list[dict[str, Any]] = [
            {
                "ref": "1a",
                "subject": subject,
                "predicate": "about",
                "object": {"id": "repo/invoice-exporter"},
            },
            {"ref": "1b", "subject": subject, "predicate": "decided", "object": None},
            {
                "ref": "1c",
                "subject": {"id": "lesson/x"},
                "predicate": "about",
                "object": {"id": "repo/invoice-exporter"},
            },
        ]
        placed, alone = ck.review_blocks(kb, views, set())
        self.assertEqual((placed["links"], placed["facts"]), (["1a"], ["1b"]))
        self.assertEqual((alone["links"], alone["facts"]), ([], ["1c"]))  # all it has: judged as a fact

    def test_sources_are_said_in_words(self) -> None:
        cases = {
            ("memory", "deploys.md"): "deploys",
            ("ticket", "jira:EXP-12"): "EXP-12",
            ("transcript", "0123456789abcdef 2026-10-18T18:56-19:10"): "session 01234567, Oct 18 18:56-19:10",
            ("slack", "#acme-prs 2026-10-21 14:05"): "#acme-prs Oct 21 14:05",
        }
        for (kind, ref), label in cases.items():
            with self.subTest(kind=kind):
                self.assertEqual(ck.source_label(kind, ref), label)
        self.assertEqual(set(ck.SOURCE_KIND_LABELS), set(ks.SOURCE_KINDS))

    def test_the_saved_batch_is_used_only_when_it_is_todays_and_open(self) -> None:
        self.kg("batch", "--cap", "1")
        saved = console_kb.saved_batch(self.settings)
        self.assertEqual(self.review(saved)["summary"]["source"], ck.SAVED)
        self.assertEqual(self.review(saved)["summary"]["groups"], 1)
        self.assertEqual(self.review(saved)["summary"]["later_groups"], 2)
        yesterday = dict(saved, created_at=km.iso(self.now - timedelta(days=1)))
        self.assertEqual(self.review(yesterday)["summary"]["source"], ck.COMPUTED)
        answered = dict(saved, applied_at=km.iso(self.now))
        self.assertEqual(self.review(answered)["summary"]["source"], ck.COMPUTED)
        self.kg("batch", "apply", "confirm all")
        self.assertEqual(self.review(saved)["summary"]["source"], ck.COMPUTED)  # nothing left waiting

    def test_decided_facts_in_a_saved_batch_are_not_decidable(self) -> None:
        self.kg("batch")
        first = self.review()["groups"][0]["facts"][0]["id"]
        self.kg("confirm", first)
        model = self.review(console_kb.saved_batch(self.settings))
        fact = model["groups"][0]["facts"][0]
        self.assertEqual((fact["id"], fact["decidable"], fact["status"]), (first, False, "confirmed"))
        self.assertNotIn(first, ck.batch_fact_ids(model))


class DecisionTest(KnowledgeWorld):
    def test_a_decision_must_be_well_formed(self) -> None:
        bodies: tuple[Any, ...] = (
            [],
            {"action": "confirm"},
            {"action": "approve", "facts": ["f1"]},
            {"action": "confirm", "facts": []},
            {"action": "confirm", "facts": ["1a"]},
            {"action": "confirm", "facts": ["f1"], "note": "x"},
            {"action": "confirm", "facts": [f"f{i}" for i in range(ck.MAX_DECISION_FACTS + 1)]},
        )
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(QueryError):
                ck.Decision.parse(body)
        self.assertEqual(ck.Decision.parse({"action": "defer", "facts": ["f1", "f1"]}).facts, ("f1",))

    def test_confirm_reject_and_defer_go_through_the_review(self) -> None:
        kb = self.kb()
        ids = ck.batch_fact_ids(ck.review_model(kb, None, self.settings.limits, LEDGER, UTC))
        one, two, three = sorted(ids, key=lambda i: int(i[1:]))[:3]
        result = ck.apply_decision(kb, ck.Decision("confirm", (one,)), ids)
        self.assertEqual((result["confirmed"], result["left_proposed"]), (1, 0))
        self.assertEqual(
            kb.facts[one]["reviewed_by"], {"kind": "user", "ref": "console", "at": km.iso(self.now)}
        )
        self.assertEqual(kb.facts[one]["confirmed_by"], km.REVIEWED)
        self.assertEqual(ck.apply_decision(kb, ck.Decision("reject", (two,)), ids)["rejected"], 1)
        records = len(kb.new_records)
        deferred = ck.apply_decision(kb, ck.Decision("defer", (three,)), ids)
        self.assertEqual((deferred["left_proposed"], len(kb.new_records)), (1, records))

    def test_only_facts_waiting_in_the_batch(self) -> None:
        kb = self.kb()
        confirmed = self.fact_id("process/release", "has_step")
        with self.assertRaises(ck.ReviewError) as caught:
            ck.apply_decision(kb, ck.Decision("confirm", (confirmed,)), set())
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(kb.new_records, [])


class StorageTest(KnowledgeWorld):
    def test_identifiers_are_never_read(self) -> None:
        self.kg("identify", "person/dana", "slack", SECRET_ID)
        overview = console_kb.overview(self.settings, LEDGER, {"q": ["dana"]}, self.now)
        key = ck.entity_key("person/dana")
        page = console_kb.entity(self.settings, key, LEDGER, self.now)
        review = console_kb.review(self.settings, LEDGER, self.now)
        self.assertNotIn(SECRET_ID, json.dumps([overview, page, review]))
        self.assertEqual(self.kb().identifiers, [])

    def test_an_unknown_key_is_none(self) -> None:
        self.assertIsNone(console_kb.entity(self.settings, "0123456789abcdef", LEDGER, self.now))
        self.assertIsNone(
            console_kb.entity(console_kb.Settings(), ck.entity_key("person/dana"), LEDGER, self.now)
        )

    def test_a_decision_is_saved_like_batch_apply(self) -> None:
        fact = min(ck.batch_fact_ids(self.review()), key=lambda i: int(i[1:]))
        body = json.dumps({"action": "confirm", "facts": [fact]}).encode()
        result = console_kb.decide(self.settings, body, LEDGER)
        self.assertEqual(result["messages"], [f"ok: {fact} confirmed"])
        self.assertEqual(self.kb().facts[fact]["status"], km.CONFIRMED)
        self.assertEqual(self.kb().facts[fact]["history"][-1]["note"], "console")

    def decide(self, action: str, facts: list[str]) -> dict[str, Any]:
        body = json.dumps({"action": action, "facts": facts}).encode()
        return console_kb.decide(self.settings, body, LEDGER)

    def test_deciding_every_fact_of_the_saved_batch_marks_it_answered(self) -> None:
        self.kg("batch", "--cap", "1")
        saved = console_kb.saved_batch(self.settings)
        facts = [i for g in saved["groups"] for i in g["facts"]]
        self.assertFalse(self.decide("confirm", facts[:1])["batch_answered"])
        self.assertFalse(self.decide("defer", facts[1:])["batch_answered"])  # deferred facts still wait
        self.assertIsNone(console_kb.saved_batch(self.settings)["applied_at"])
        self.assertTrue(self.decide("reject", facts[1:])["batch_answered"])
        self.assertEqual(console_kb.saved_batch(self.settings)["applied_at"], km.iso(self.now))
        with self.assertRaisesRegex(km.KnowledgeError, "was answered"):
            self.kg("batch", "apply", "confirm all")  # the CLI agrees: that batch is done

    def test_a_computed_batch_marks_nothing(self) -> None:
        facts = sorted(ck.batch_fact_ids(self.review()), key=lambda i: int(i[1:]))
        self.assertFalse(self.decide("confirm", facts)["batch_answered"])
        self.assertIsNone(console_kb.saved_batch(self.settings))

    def test_the_change_stamp(self) -> None:
        stamp = console_kb.version(self.settings)
        self.assertTrue(stamp["exists"])
        self.assertEqual(console_kb.version(self.settings), stamp)
        self.now += timedelta(seconds=1)
        self.decide("confirm", [min(ck.batch_fact_ids(self.review()), key=lambda i: int(i[1:]))])
        self.assertNotEqual(console_kb.version(self.settings)["stamp"], stamp["stamp"])
        self.assertEqual(console_kb.version(console_kb.Settings())["exists"], False)

    def test_a_decision_without_a_knowledge_base_creates_nothing(self) -> None:
        settings = console_kb.Settings(root=self.tmp / "none", git=False)
        with self.assertRaises(ck.ReviewError) as caught:
            console_kb.decide(settings, b'{"action": "confirm", "facts": ["f1"]}', LEDGER)
        self.assertEqual(caught.exception.status, 404)
        self.assertFalse((self.tmp / "none").exists())
        with self.assertRaises(QueryError):
            console_kb.decide(self.settings, b"not json", LEDGER)

    def test_settings_come_from_the_environment(self) -> None:
        settings = console_kb.Settings.from_env(
            {"BOSUN_CONFIG_DIR": str(self.tmp / "config"), "BOSUN_KNOWLEDGE_GIT": "0"}
        )
        self.assertEqual((settings.root, settings.git, settings.limits.groups), (self.root, False, 10))
        with self.assertRaises(km.KnowledgeError):
            console_kb.Settings.from_env({"BOSUN_KNOWLEDGE_BATCH_CAP": "ten"})
        with self.assertRaises(km.KnowledgeError):
            console_kb.Settings.from_env({"BOSUN_KNOWLEDGE_GROUP_FACTS": "99"})


class HistoryTest(KnowledgeWorld):
    """A decision with git on: one commit in the knowledge directory's own repository."""

    def test_a_decision_is_one_commit(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("no git")
        settings = console_kb.Settings(root=self.root, git=True)
        fact = min(ck.batch_fact_ids(self.review()), key=lambda i: int(i[1:]))
        console_kb.decide(settings, json.dumps({"action": "reject", "facts": [fact]}).encode(), LEDGER)
        log = subprocess.run(
            ["git", "--git-dir", str(self.root / ".git"), "log", "--format=%s"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\n")
        self.assertEqual(log[0], f"kg console review: reject {fact}")


if __name__ == "__main__":
    unittest.main()
