"""Unit tests for the knowledge base's journal (lib/knowledge_changes.py, and the write boundary in
lib/knowledge.py): each write is one transaction in `knowledge/journal/`, a fact or entity naming a
person goes to the local file and never leaves this Mac, a new fact gets a minted id, an update
carries what it replaced, a delete on request turns the person's changes into placeholders, and a
knowledge base from before the journal enters it once (genesis). view.json records the journal
position and a digest of what the files hold. Fictional data: acme's invoice exporter."""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import change_log
import journal as jr
import knowledge
import knowledge_changes as kc
import knowledge_model as km
import knowledge_store as store
from change_log import Edit
from knowledge_model import KnowledgeError
from test_knowledge import FIXTURE, START, KnowledgeTestCase


MINTED = re.compile(r"^f-[0-9a-hjkmnp-tv-z]{6}$")
LEGACY_AT = "2026-10-01T09:00:00+00:00"


def legacy_entity(entity_id: str, name: str) -> dict[str, Any]:
    return {
        "id": entity_id,
        "type": entity_id.partition("/")[0],
        "name": name,
        "aliases": {"strong": [], "weak": []},
        "summary": "",
        "created_at": LEGACY_AT,
        "updated_at": LEGACY_AT,
    }


def legacy_fact(fact_id: str, subject: str, predicate: str, **fields: Any) -> dict[str, Any]:
    """An add record as a knowledge base from before the journal holds it (a counted id)."""
    fact = {
        "id": fact_id,
        "subject": subject,
        "predicate": predicate,
        "object": None,
        "value": None,
        "order": None,
        "sources": [{"kind": "memory", "ref": "exporter.md", "at": LEGACY_AT}],
        "authority": "observed",
        "observed_at": LEGACY_AT,
        "recorded_at": LEGACY_AT,
        "valid_from": LEGACY_AT,
        "valid_to": None,
        "superseded_by": None,
        "status": "proposed",
        "last_confirmed_at": None,
        "sensitivity": "internal",
        "prevents_rework": False,
        "observations": 1,
        "note": "",
        **fields,
    }
    return {"op": "add", "at": LEGACY_AT, "fact": fact}


class JournalTestCase(KnowledgeTestCase):
    """The knowledge base's journal files, read the way the knowledge base writes them."""

    def journal(self) -> jr.Journal:
        return knowledge.open_journal(knowledge.paths())

    def replica(self) -> str:
        return jr.replica_id(self.state)

    def lines(self, stem: str) -> list[dict[str, Any]]:
        path = self.root / "journal" / f"{stem}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def private(self) -> list[dict[str, Any]]:
        return self.lines(self.replica())

    def local(self) -> list[dict[str, Any]]:
        return self.lines("local")

    def view(self) -> dict[str, Any]:
        found: dict[str, Any] = json.loads((self.root / "view.json").read_text())
        return found

    def fact_id(self, subject: str, predicate: str) -> str:
        return next(
            str(f["id"])
            for f in self.kb().facts.values()
            if (f["subject"], f["predicate"]) == (subject, predicate)
        )

    def write_legacy(self, entities: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
        """A knowledge base as a bosun from before the journal left it: the files, no journal."""
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "entities.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entities))
        (self.root / "facts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))

    def records(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log_lines()]


class MintedIdsTest(JournalTestCase):
    def test_a_new_fact_gets_a_minted_id(self) -> None:
        self.write_legacy(
            [legacy_entity("repo/invoice-exporter", "invoice exporter")],
            [legacy_fact("f12", "repo/invoice-exporter", "caveat", value="retries are slow")],
        )
        out = self.run_kg(
            "propose",
            "repo/invoice-exporter",
            "caveat",
            "--value",
            "exports run nightly",
            "--source",
            "ledger:a",
        )
        new = next(i for i in self.kb().facts if i != "f12")
        self.assertRegex(new, MINTED)
        self.assertIn(f"ok: {new} added", out)
        self.assertEqual(list(self.kb().facts), ["f12", new])  # the older fact keeps its counted id
        self.assertEqual(self.kb().facts["f12"]["value"], "retries are slow")
        self.run_kg("confirm", "f12")  # and is still addressed by it
        self.assertEqual(self.fact("f12")["status"], km.CONFIRMED)

    def test_minted_ids_differ_and_never_name_a_fact_already_there(self) -> None:
        self.seed()
        for n in range(5):
            self.run_kg("propose", "exporter", "caveat", "--value", f"gotcha {n}", "--source", "ledger:a")
        ids = list(self.kb().facts)
        self.assertEqual(len(set(ids)), 5)
        self.assertTrue(all(MINTED.match(i) for i in ids))
        taken = iter(["abcdef", "abcdef", "ghjkmn"])
        with knowledge.opened(True, "propose") as kb:
            kb.facts["f-abcdef"] = kb.facts[ids[0]]
            kb.mint = lambda: next(taken)
            self.assertEqual(km.next_fact_id(kb), "f-ghjkmn")
            kb.facts.pop("f-abcdef")

    def test_a_read_never_mints(self) -> None:
        self.seed()
        with knowledge.opened(False) as kb, self.assertRaisesRegex(KnowledgeError, "only in a write"):
            km.next_fact_id(kb)

    def test_a_dry_run_mints_but_writes_no_journal(self) -> None:
        out = self.run_kg("import", str(FIXTURE))
        self.assertIn("facts: 20 new", out)
        self.assertFalse(self.root.exists())
        self.assertFalse((self.state / "replica.json").exists())


class UpdatesTest(JournalTestCase):
    def test_updates_carry_what_they_replaced(self) -> None:
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "has_step")
        self.run_kg("confirm", fact_id)
        confirmed = self.records()[-1]
        self.assertEqual(confirmed["event"], "confirmed")
        self.assertEqual(confirmed["prev"]["status"], "proposed")
        self.assertEqual(set(confirmed["prev"]), set(confirmed["set"]))
        self.assertIsNone(confirmed["prev"]["last_confirmed_at"])
        self.assertEqual(self.private()[-1]["body"], confirmed)

    def test_every_kind_of_update_carries_prev(self) -> None:
        self.seed()
        self.run_kg("propose", "exporter", "caveat", "--value", "slow", "--source", "ledger:a")
        fact_id = self.fact_id("repo/invoice-exporter", "caveat")
        self.later(1)
        self.run_kg("propose", "exporter", "caveat", "--value", "slow", "--source", "ledger:b")  # observed
        self.run_kg("reject", fact_id)
        self.run_kg("propose", "exporter", "caveat", "--value", "fast", "--source", "ledger:a")
        other = next(i for i, f in self.kb().facts.items() if f["value"] == "fast")
        self.run_kg("supersede", other, "--close")
        self.later(km.PROPOSAL_DAYS + 1)
        self.run_kg("expire")
        updates = {r["event"]: r for r in self.records() if r["op"] == "update"}
        self.assertEqual(set(updates), {"observed", "rejected", "superseded", "expired"})
        self.assertEqual(updates["observed"]["prev"]["observations"], 1)
        self.assertEqual(set(updates["observed"]["prev"]), set(updates["observed"]["set"]))
        self.assertEqual(updates["rejected"]["prev"], {"status": "proposed"})
        self.assertEqual(updates["superseded"]["prev"], {"valid_to": None, "superseded_by": None})
        self.assertEqual(updates["expired"]["prev"], {"status": "proposed"})


class ClassesTest(JournalTestCase):
    def test_person_facts_go_to_the_local_journal(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        fact_id = self.fact_id("person/dana", "reviews")
        local = {(c["op"], c["target"]) for c in self.local()}
        self.assertIn(("entity.put", "person/dana"), local)
        self.assertIn(("fact.add", fact_id), local)
        self.assertTrue(all(c["local"] and c["private_of"] >= 0 for c in self.local()))
        private = (self.root / "journal" / f"{self.replica()}.jsonl").read_text()
        self.assertNotIn("dana", private.lower())

    def test_product_facts_go_to_the_replica_journal(self) -> None:
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "has_step")
        private = {(c["op"], c["target"]) for c in self.private()}
        self.assertIn(("fact.add", fact_id), private)
        self.assertIn(("entity.put", "process/release"), private)
        self.assertNotIn(fact_id, {c["target"] for c in self.local()})
        add = next(c for c in self.private() if c["target"] == fact_id)
        self.assertEqual(add["body"], self.records()[-1])
        self.assertEqual((add["stream"], add["rules"], add["cmd"]), ("kg", knowledge.KG_RULES_VERSION, ""))

    def test_an_update_follows_its_fact(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        self.run_kg(
            "confirm", self.fact_id("person/dana", "reviews"), self.fact_id("process/release", "has_step")
        )
        self.assertEqual(self.local()[-1]["op"], "fact.update")
        self.assertEqual(self.private()[-1]["op"], "fact.update")
        self.assertEqual(self.local()[-1]["tx"], self.private()[-1]["tx"])  # one write, one transaction

    def test_edits_compare_entities_and_take_the_new_records(self) -> None:
        self.seed()
        kb = self.kb()
        before = json.loads(json.dumps(kb.entities))
        kb.entities["repo/invoice-exporter"]["summary"] = "exports invoices"
        kb.entities.pop("step/merge")
        kb.entities.pop("person/dana")
        kb.mint = lambda: "aaaaaa"
        km.propose(
            kb, km.Proposal("exporter", "caveat", value="slow", sources=[{"kind": "ledger", "ref": "a"}])
        )
        found = kc.edits(before, kb)
        self.assertEqual(
            [(e.op, e.target, e.local) for e in found],
            [
                ("entity.put", "repo/invoice-exporter", False),
                ("fact.add", "f-aaaaaa", False),
                ("entity.delete", "person/dana", True),
                ("entity.delete", "step/merge", False),
            ],
        )
        self.assertEqual(found[0].body, kb.entities["repo/invoice-exporter"])
        self.assertEqual(found[1].body, kb.new_records[0])
        self.assertEqual(found[2].body, {})


class ForgetTest(JournalTestCase):
    git = True

    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        super().setUp()

    def test_forget_rewrites_only_the_local_journal(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        self.run_kg("confirm", self.fact_id("person/dana", "reviews"))
        private_file = self.root / "journal" / f"{self.replica()}.jsonl"
        private = private_file.read_bytes()
        local = self.local()
        position = self.journal().position()
        self.run_kg("forget", "person/dana")
        self.assertEqual(private_file.read_bytes(), private)  # the private journal is never rewritten
        after = self.local()
        self.assertNotIn("dana", (self.root / "journal" / "local.jsonl").read_text().lower())
        self.assertEqual([c["seq"] for c in after], [c["seq"] for c in local])  # the sequence never goes back
        self.assertEqual(self.journal().position(), position)
        for change in after:
            self.assertEqual(change["op"], "forgotten")
            self.assertNotIn("target", change)
            self.assertNotIn("body", change)
            self.assertEqual(change["id"], change_log.change_id(change))
        self.assertEqual(
            [{k: v for k, v in c.items() if k not in ("op", "id")} for c in after],
            [{k: c[k] for k in change_log.PLACEHOLDER_KEYS if k in c} for c in local],
        )
        log = self.git_log()
        self.assertNotIn("dana", log.lower())
        self.assertIn("journal/local.jsonl", log)

    def git_log(self) -> str:
        return store.git(self.root, "log", "-p", "--all").stdout

    def test_the_retention_purge_leaves_placeholders_too(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:dir"
        )
        self.run_kg("propose", "exporter", "caveat", "--value", "slow", "--source", "memory:a")
        self.later(km.RETENTION_DAYS + 1)
        self.run_kg("expire")
        self.assertNotIn("lisbon", (self.root / "journal" / "local.jsonl").read_text().lower())
        self.assertIn("forgotten", {c["op"] for c in self.local()})
        self.assertIn("person/dana", {c.get("target") for c in self.local()})  # only the facts went
        self.assertNotIn("lisbon", self.git_log().lower())

    def test_a_rewrite_never_removes_a_private_record(self) -> None:
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "has_step")
        files = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}
        refused = self.assertRaisesRegex(KnowledgeError, f"nothing was written: {fact_id} .*private journal")
        with refused, knowledge.opened(True, "expire") as kb:
            km.remove_facts(kb, {fact_id})
        self.assertEqual(
            {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}, files
        )


class GenesisTest(JournalTestCase):
    def imported_then_written(self) -> list[dict[str, Any]]:
        """The acme import, as a bosun from before the journal left it (no journal, no view.json),
        then a write (a confirm); the journal's changes."""
        self.run_kg("import", str(FIXTURE), "--apply", "--auto-confirm")
        shutil.rmtree(self.root / "journal")
        (self.root / "view.json").unlink()
        waiting = next(i for i, f in self.kb().facts.items() if f["status"] == km.PROPOSED)
        self.later(1)
        self.assertIn("confirmed", self.run_kg("confirm", waiting))
        return self.journal().changes()

    def test_genesis_journals_an_existing_knowledge_base(self) -> None:
        changes = self.imported_then_written()
        puts = [c["target"] for c in changes if c["op"] == "entity.put"]
        self.assertEqual(sorted(puts), sorted(self.kb().entities))  # every entity once
        bodies = [c["body"] for c in changes if c["op"].startswith("fact.")]
        self.assertEqual(bodies, self.records())  # every record once, in the order of the file
        genesis = [c for c in changes if c["cmd"] == knowledge.GENESIS]
        self.assertEqual(len({c["tx"] for c in genesis}), 1)
        self.assertEqual(len(genesis), len(puts) + len(bodies) - 1)  # the write's own record follows

    def test_genesis_keeps_what_names_a_person_on_this_mac(self) -> None:
        changes = self.imported_then_written()
        personal = {i for i, f in self.kb().facts.items() if km.is_personal(f)}
        local = {c["target"] for c in changes if c["cmd"] == knowledge.GENESIS and c.get("local")}
        self.assertEqual(local, {"person/dana", "person/lee", *personal})

    def test_genesis_keeps_counted_ids_and_takes_in_what_the_journal_cannot_hold(self) -> None:
        self.write_legacy(
            [
                legacy_entity("repo/invoice-exporter", "invoice exporter"),
                legacy_entity("step/merge", "Merge"),
            ],
            [
                legacy_fact("f3", "repo/invoice-exporter", "caveat", value="slow\udcff"),
                legacy_fact("f4", "repo/invoice-exporter", "caveat", value="flaky", order=1.5),
            ],
        )
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("add-entity", "step/rollout")
        self.assertEqual(sorted(self.kb().facts), ["f3", "f4"])
        self.assertEqual(self.fact("f3")["value"], "slow�")
        self.assertEqual(self.fact("f4")["order"], "1.5")
        self.assertIn("f3 fact.value: text that is not valid Unicode", said.getvalue())
        self.assertIn("f4 fact.order: a float (1.5)", said.getvalue())
        self.assertEqual(len([c for c in self.journal().changes() if c["cmd"] == knowledge.GENESIS]), 4)

    def test_an_empty_knowledge_base_has_no_genesis(self) -> None:
        self.run_kg("add-entity", "repo/invoice-exporter")
        self.assertEqual({c["cmd"] for c in self.journal().changes()}, {""})

    def test_a_journal_behind_the_files_is_refused(self) -> None:
        self.seed()  # person/dana's changes in local.jsonl, the rest in the replica's file
        (self.root / "journal" / f"{self.replica()}.jsonl").unlink()
        entities = (self.root / "entities.jsonl").read_bytes()
        with self.assertRaisesRegex(
            KnowledgeError,
            r"behind view\.json \(\w{32}\.jsonl ends at seq 0.*Nothing was written.*replica\.json aside",
        ):
            self.run_kg("add-entity", "repo/other")
        self.assertEqual((self.root / "entities.jsonl").read_bytes(), entities)
        self.assertEqual(sorted(p.name for p in (self.root / "journal").iterdir()), ["local.jsonl"])

    def test_a_journal_moved_aside_starts_again_from_the_files(self) -> None:
        self.seed()
        (self.root / "journal").rename(self.root / "journal.old")
        self.run_kg("add-entity", "repo/other")
        self.assertEqual(
            sorted(c["target"] for c in self.journal().changes() if c["cmd"] == knowledge.GENESIS),
            sorted(e for e in self.kb().entities if e != "repo/other"),
        )


class BoundaryTest(JournalTestCase):
    def test_view_records_the_position_and_a_digest_of_the_files(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        kb = self.kb()
        self.assertEqual(
            self.view(),
            {"journal": {**self.journal().position(), "digest": kc.state_hash(kb.entities, kb.records)}},
        )
        self.assertEqual(set(self.journal().position()), {self.replica(), "local"})

    def test_a_value_the_journal_cannot_hold_is_refused_and_nothing_is_written(self) -> None:
        self.seed()
        files = {p.name: p.read_bytes() for p in self.root.rglob("*.json*")}
        with self.assertRaisesRegex(
            KnowledgeError, r"nothing was written: f-\w{6} fact\.value holds text that is not valid Unicode"
        ):
            self.run_kg("propose", "exporter", "caveat", "--value", "slow\udcff", "--source", "ledger:a")
        self.assertEqual({p.name: p.read_bytes() for p in self.root.rglob("*.json*")}, files)

    def test_each_write_is_one_transaction_named_by_its_command(self) -> None:
        self.seed()
        environment = {
            "BOSUN_CONFIG_DIR": str(self.tmp),
            "BOSUN_STATE_DIR": str(self.state),
            "BOSUN_KNOWLEDGE_GIT": "0",
        }
        argv = ["kg", "propose", "exporter", "caveat", "--value", "slow", "--source", "ledger:a"]
        with (
            mock.patch.dict("os.environ", environment),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(knowledge.main(), 0)
        self.assertEqual(self.private()[-1]["cmd"], "kg propose")
        self.assertEqual(
            knowledge.command_name(knowledge.build_parser().parse_args(["batch", "apply", "x"])),
            "kg batch apply",
        )

    def test_a_write_needs_the_state_folder_for_this_macs_replica(self) -> None:
        knowledge.configure(self.root, git=False)
        with self.assertRaisesRegex(KnowledgeError, "BOSUN_STATE_DIR"):
            self.run_kg("add-entity", "repo/invoice-exporter")
        self.assertFalse(self.root.exists())

    def test_the_digest_leaves_out_nothing_the_files_hold(self) -> None:
        entities = {"repo/x": legacy_entity("repo/x", "x")}
        records = [legacy_fact("f1", "repo/x", "caveat", value="slow")]
        held = kc.state_hash(entities, records)
        self.assertRegex(held, "^[0-9a-f]{64}$")
        self.assertNotEqual(held, kc.state_hash(entities, [*records, records[0]]))
        self.assertNotEqual(held, kc.state_hash({}, records))
        self.assertEqual(held, kc.state_hash(dict(reversed(list(entities.items()))), records))


class CreationOrderTest(unittest.TestCase):
    def test_facts_sort_by_when_they_were_recorded_then_counted_ids_then_minted(self) -> None:
        def fact(fact_id: str, at: str) -> dict[str, Any]:
            return {"id": fact_id, "recorded_at": at}

        early, late = START.isoformat(), START.replace(hour=10).isoformat()
        facts = [fact("f-bbbbbb", late), fact("f10", early), fact("f9", early), fact("f-aaaaaa", late)]
        self.assertEqual(
            [f["id"] for f in sorted(facts, key=km.creation_key)], ["f9", "f10", "f-aaaaaa", "f-bbbbbb"]
        )


class PlaceTest(unittest.TestCase):
    def test_a_place_names_the_fact_or_entity_and_the_path(self) -> None:
        edit = Edit("fact.add", "f-abc123", {"fact": {"value": 1.5}}, False)
        self.assertEqual(kc.place(edit, ("fact", "value")), "f-abc123 fact.value")
        entity = Edit("entity.put", "repo/x", {"summary": "x"}, False)
        self.assertEqual(kc.place(entity, ("summary",)), "repo/x summary")


if __name__ == "__main__":
    unittest.main()
