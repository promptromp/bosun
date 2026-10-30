"""Unit tests for the Knowledge Graph page's Briefing and Constellation (lib/console_kg_views.py):
the coverage definition, the confirmed share, the captured-per-day series, this week's timeline
(grouped by day, newest first, with before -> after pairs), the most-connected strip, the
Constellation's nodes, edges, days and cap, determinism, and that the restricted identifiers never
reach a model. Fictional data: acme's invoice exporter (tests/fixtures/knowledge/acme-import.jsonl),
grown over a week with a pinned clock."""

from __future__ import annotations

import json
import random
import shutil
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_kb
import console_kg_views as views
import console_knowledge as ck
import knowledge
import knowledge_model as km


KG_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge" / "acme-import.jsonl"
SOURCE = "report:task:2026-10-16/1"
IMPORTED = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)  # before this week
DECIDED = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
SUPERSEDED = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)
LEARNED = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
SECRET_ID = "U0000000099"  # noqa: S105  # a fictional restricted identifier: it must never reach a model


class WeekWorld(unittest.TestCase):
    """The fixture imported on 2026-10-10, then a week of learning: two decisions (the newer
    supersedes the older) on 10-16, a status replaced on 10-18, a rule and a lesson on 10-20."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "config" / "knowledge"
        self.now = IMPORTED
        knowledge.CLOCK = lambda: self.now
        self.addCleanup(setattr, knowledge, "CLOCK", knowledge.clock)
        knowledge.configure(self.root, git=False)
        self.kg("import", str(KG_FIXTURE), "--apply", "--auto-confirm")
        self.grow()
        self.settings = console_kb.Settings(root=self.root, git=False)

    def kg(self, *argv: str) -> str:
        args = knowledge.build_parser().parse_args(list(argv))
        result = args.func(args)
        return str(result[0] if isinstance(result, tuple) else result)

    def grow(self) -> None:
        self.now = DECIDED
        self.kg("add-entity", "decision/nightly-export", "--name", "Nightly export")
        self.kg("add-entity", "decision/hourly-export", "--name", "Hourly export")
        for argv in (
            ("decision/nightly-export", "decided", "--value", "export invoices nightly"),
            ("decision/hourly-export", "decided", "--value", "export invoices every hour"),
            ("decision/hourly-export", "supersedes", "decision/nightly-export"),
        ):
            self.kg("propose", *argv, "--source", SOURCE)
        self.now = SUPERSEDED
        self.kg(
            "supersede",
            self.fact_id("environment/staging-2", "status"),
            "--value",
            "running",
            "--source",
            SOURCE,
        )
        self.now = LEARNED
        self.kg(
            "propose", "process/release", "rule", "--value", "never deploy on a Friday", "--source", SOURCE
        )
        self.now = NOW

    def kb(self) -> km.KB:
        return console_kb.read_kb(self.root, self.now)

    def fact_id(self, subject: str, predicate: str) -> str:
        kb = console_kb.read_kb(self.root, self.now)
        return next(
            str(f["id"])
            for f in kb.facts.values()
            if (f["subject"], f["predicate"]) == (subject, predicate) and km.is_current(f)
        )

    def briefing(self) -> dict[str, Any]:
        return views.briefing_model(self.kb(), UTC)

    def stars(self) -> dict[str, Any]:
        return views.constellation_model(self.kb(), UTC)


def covered_by_hand(kb: km.KB) -> set[str]:
    """The coverage definition, spelled out: the ends of every current confirmed fact."""
    confirmed = [f for f in kb.facts.values() if ck.shown_current(kb, f) and ck.status(kb, f) == km.CONFIRMED]
    return {str(e) for f in confirmed for e in (f["subject"], f.get("object")) if e}


def members_by_hand(kb: km.KB, card: str) -> list[str]:
    return [i for i, e in kb.entities.items() if ck.card_of(kb, e["type"]) == card]


class HeadlineTest(WeekWorld):
    def test_coverage_counts_entities_in_a_confirmed_fact_as_subject_or_object(self) -> None:
        kb = self.kb()
        covered = covered_by_hand(kb)
        rows = {r["card"]: r for r in self.briefing()["coverage"]}
        for card, _ in ck.CARDS:
            members = members_by_hand(kb, card)
            found = len(covered.intersection(members))
            with self.subTest(card=card):
                self.assertEqual((rows[card]["entities"], rows[card]["covered"]), (len(members), found))
                self.assertEqual(rows[card]["share"], round(found / len(members), 3))

    def test_an_object_of_a_confirmed_fact_is_covered_and_proposed_facts_do_not_count(self) -> None:
        before = {r["card"]: r["covered"] for r in self.briefing()["coverage"]}
        decisions = [i for i in self.kb().entities if i.startswith("decision/")]
        self.assertEqual(len(decisions), 2)  # both only in proposed facts so far
        self.kg("confirm", self.fact_id("decision/hourly-export", "supersedes"))
        after = {r["card"]: r["covered"] for r in self.briefing()["coverage"]}
        self.assertEqual(after["product"], before["product"] + 2)  # the subject and the object

    def test_the_confirmed_share_is_of_current_facts(self) -> None:
        kb = self.kb()
        statuses = [ck.status(kb, f) for f in kb.facts.values() if ck.shown_current(kb, f)]
        totals = self.briefing()["totals"]
        confirmed = statuses.count(km.CONFIRMED)
        self.assertEqual((totals["facts"], totals["confirmed"]), (len(statuses), confirmed))
        self.assertEqual(totals["proposed"], statuses.count(km.PROPOSED))
        self.assertEqual(totals["confirmed_share"], round(confirmed / len(statuses), 3))

    def test_the_sparkline_series_counts_facts_by_the_day_first_recorded(self) -> None:
        captured = self.briefing()["captured"]
        days = captured["days"]
        self.assertEqual((len(days), days[-1], days[0]), (views.SERIES_DAYS, "2026-10-21", "2026-09-22"))
        per_day = dict(zip(days, captured["new"], strict=True))
        self.assertEqual(per_day["2026-10-10"], 20)  # the import
        self.assertEqual(per_day["2026-10-16"], 3)
        self.assertEqual(per_day["2026-10-18"], 1)  # the replacement
        self.assertEqual(per_day["2026-10-20"], 1)
        self.assertEqual(captured["total"][-1], len(self.kb().facts))
        self.assertEqual(captured["total"], sorted(captured["total"]))
        self.assertEqual((captured["first_day"], captured["this_week"]), ("2026-10-10", 5))

    def test_rediscovery_is_a_placeholder_not_a_number(self) -> None:
        self.assertEqual(self.briefing()["rediscovery"], {"measured": False, "note": views.REDISCOVERY_NOTE})


class WeekTest(WeekWorld):
    def test_the_timeline_is_this_week_by_day_newest_first(self) -> None:
        week = self.briefing()["week"]
        self.assertEqual((week["start"], week["end"]), ("2026-10-15", "2026-10-21"))
        self.assertEqual([d["day"] for d in week["days"]], ["2026-10-20", "2026-10-18", "2026-10-16"])
        texts = [f["text"] for d in week["days"] for f in d["facts"]]
        self.assertNotIn("has_step Merge to main (step 1)", texts)  # imported before the week

    def test_a_replaced_fact_is_a_before_after_pair(self) -> None:
        day = next(d for d in self.briefing()["week"]["days"] if d["day"] == "2026-10-18")
        (change,) = day["changes"]
        self.assertEqual(change["kind"], views.REPLACED)
        self.assertEqual(change["before"]["text"], "status 'retired'")
        self.assertEqual(change["after"]["text"], "status 'running'")
        self.assertEqual(change["after"]["entity"]["key"], ck.entity_key("environment/staging-2"))
        self.assertEqual(day["facts"], [])  # the replacement shows only in the pair
        self.assertEqual(day["count"], 1)

    def test_a_superseded_decision_is_a_before_after_pair(self) -> None:
        day = next(d for d in self.briefing()["week"]["days"] if d["day"] == "2026-10-16")
        (change,) = day["changes"]
        self.assertEqual(change["kind"], "supersedes")
        self.assertEqual(change["before"]["text"], "export invoices nightly")
        self.assertEqual(change["after"]["text"], "export invoices every hour")
        self.assertEqual(change["before"]["entity"]["id"], "decision/nightly-export")
        self.assertEqual(len(day["facts"]), 2)  # the two decided facts
        self.assertEqual(day["new_entities"], 2)
        self.assertEqual(day["facts"][0]["subject"]["type"], "decision")  # decisions read first

    def test_a_day_lists_at_most_day_facts_and_counts_the_rest(self) -> None:
        with mock.patch.object(views, "DAY_FACTS", 1):
            day = next(d for d in self.briefing()["week"]["days"] if d["day"] == "2026-10-16")
        self.assertEqual((len(day["facts"]), day["more"], day["count"]), (1, 1, 3))

    def test_nothing_this_week(self) -> None:
        self.now = datetime(2026, 11, 30, 9, 0, tzinfo=UTC)
        week = self.briefing()["week"]
        self.assertEqual((week["days"], week["facts"], week["changes"]), ([], 0, 0))


class ConnectedTest(WeekWorld):
    def test_the_most_connected_come_first_with_one_hop_ego_graphs(self) -> None:
        connected = self.briefing()["connected"]
        degrees = [e["degree"] for e in connected]
        self.assertEqual(degrees, sorted(degrees, reverse=True))
        self.assertLessEqual(len(connected), views.CONNECTED)
        for entry in connected:
            with self.subTest(entity=entry["id"]):
                nodes = entry["graph"]["nodes"]
                self.assertEqual(nodes[0]["id"], entry["id"])
                self.assertEqual({n["depth"] for n in nodes[1:]}, {1})
                self.assertLessEqual(len(nodes), views.MINI_NODES)
                self.assertEqual(len(nodes) - 1 + entry["graph"]["more"], entry["degree"])


class ConstellationTest(WeekWorld):
    def test_every_entity_is_a_node_with_its_card_degree_and_first_day(self) -> None:
        stars = self.stars()
        nodes = {n["id"]: n for n in stars["nodes"]}
        self.assertEqual(set(nodes), set(self.kb().entities))
        self.assertEqual(nodes["process/release"]["day"], "2026-10-10")
        self.assertEqual(nodes["decision/hourly-export"]["day"], "2026-10-16")
        self.assertEqual(nodes["decision/hourly-export"]["card"], "product")
        self.assertFalse(nodes["decision/hourly-export"]["confirmed"])  # proposed facts only
        self.assertEqual(stars["days"], ["2026-10-10", "2026-10-16"])
        self.assertEqual([c["shown"] for c in stars["cards"]], [c["entities"] for c in stars["cards"]])

    def test_edges_join_shown_nodes_once_per_pair_with_status_and_day(self) -> None:
        stars = self.stars()
        index = {n["id"]: i for i, n in enumerate(stars["nodes"])}
        pairs = [(e["from"], e["to"]) for e in stars["edges"]]
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertTrue(all(a < b for a, b in pairs))
        decision = next(
            e
            for e in stars["edges"]
            if {e["from"], e["to"]} == {index["decision/hourly-export"], index["decision/nightly-export"]}
        )
        self.assertEqual(
            (decision["predicates"], decision["confirmed"], decision["day"]),
            (["supersedes"], False, "2026-10-16"),
        )
        degree = sum(1 for a, b in pairs if index["process/release"] in (a, b))
        self.assertEqual(stars["nodes"][index["process/release"]]["degree"], degree)

    def test_the_cap_keeps_the_best_connected_and_counts_the_rest(self) -> None:
        everything = self.stars()["nodes"]
        with mock.patch.object(views, "MAX_NODES", 5), mock.patch.object(views, "MAX_EDGES", 2):
            stars = self.stars()
        kept = stars["nodes"]
        self.assertEqual((len(kept), stars["more"], stars["cap"]), (5, len(everything) - 5, 5))
        least = min(n["degree"] for n in kept)
        self.assertTrue(
            all(n["degree"] <= least for n in everything if n["id"] not in {k["id"] for k in kept})
        )
        self.assertLessEqual(len(stars["edges"]), 2)
        self.assertTrue(all(e["from"] < 5 and e["to"] < 5 for e in stars["edges"]))
        self.assertEqual(sum(c["shown"] for c in stars["cards"]), 5)


class DeterminismTest(WeekWorld):
    def test_the_same_knowledge_base_gives_the_same_models_whatever_the_load_order(self) -> None:
        kb = self.kb()
        first = json.dumps([views.briefing_model(kb, UTC), views.constellation_model(kb, UTC)])
        shuffled = self.kb()
        rng = random.Random(4)  # noqa: S311  # a fixed shuffle, not security
        entities, facts = list(shuffled.entities.items()), list(shuffled.facts.items())
        rng.shuffle(entities)
        rng.shuffle(facts)
        shuffled.entities, shuffled.facts = dict(entities), dict(facts)
        second = json.dumps([views.briefing_model(shuffled, UTC), views.constellation_model(shuffled, UTC)])
        self.assertEqual(first, second)


class StorageTest(WeekWorld):
    def test_identifiers_never_reach_a_model(self) -> None:
        self.kg("identify", "person/dana", "slack", SECRET_ID)
        found = [
            console_kb.briefing(self.settings, self.now),
            console_kb.constellation(self.settings, self.now),
        ]
        self.assertNotIn(SECRET_ID, json.dumps(found))

    def test_no_knowledge_base_is_an_empty_state(self) -> None:
        settings = console_kb.Settings(root=self.tmp / "nothing", git=False)
        briefing, stars = (
            console_kb.briefing(settings, self.now),
            console_kb.constellation(settings, self.now),
        )
        self.assertEqual((briefing["exists"], briefing["hint"]), (False, ck.EMPTY_HINT))
        self.assertEqual((stars["exists"], stars["nodes"]), (False, []))
        self.assertFalse((self.tmp / "nothing").exists())


if __name__ == "__main__":
    unittest.main()
