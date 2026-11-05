"""Unit tests for the knowledge base's journal (lib/knowledge_changes.py, and the write boundary in
lib/knowledge.py): each write is one transaction in `knowledge/journal/`, a fact or entity naming a
person goes to the local file and never leaves this Mac, a new fact gets a minted id, an update
carries what it replaced, a delete on request turns the person's changes into placeholders, and a
knowledge base from before the journal enters it once (genesis). view.json records the journal
position and a digest of what the files hold. The files are the journal's rebuild: in the one
order, an update waiting for its fact, two decisions on one proposal a conflict; a write cut short
is repaired by the next, a change made to the files outside the journal is adopted, a reader never
writes, and `kg validate` says how the files stand against the journal. Fictional data: acme's
invoice exporter."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import random
import re
import shutil
import stat
import sys
import unittest
from datetime import timedelta
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
import update_backups as ub
from change_log import Edit
from knowledge_model import KnowledgeError
from test_journal import short_writes
from test_knowledge import FIXTURE, QUOTE, START, KnowledgeTestCase, typed


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

    def file_entities(self) -> dict[str, dict[str, Any]]:
        return {e["id"]: e for e in store.read_jsonl(self.root / "entities.jsonl")}

    def assert_the_journal_rebuilds_the_files(self) -> None:
        """The files as saved are the journal's rebuild (entities, the fact log in order, and so the
        folded facts), with nothing it could not apply, and view.json names the journal's position
        and the digest of what they hold."""
        rebuilt = kc.rebuild(self.journal().changes())
        records = store.read_jsonl(self.root / "facts.jsonl")
        self.assertEqual(rebuilt.entities, self.file_entities())
        self.assertEqual(rebuilt.records, records)
        self.assertEqual(km.fold_all(rebuilt.records), km.fold_all(records))
        self.assertEqual((rebuilt.conflicts, rebuilt.pending), ([], []))
        self.assertEqual(
            self.view()["journal"],
            {**self.journal().position(), "digest": kc.file_digests(rebuilt.entities, rebuilt.records)},
        )

    def validate(self, *args: str) -> tuple[str, int]:
        parsed = knowledge.build_parser().parse_args(["validate", *args])
        output, code = parsed.func(parsed)
        return str(output), int(code)

    def commit_by_hand(self, *edits: Edit) -> None:
        """A transaction in the journal that never reached the files (a write cut short after it)."""
        knowledge.open_journal(knowledge.paths(), self.replica()).begin("test").commit_edits(edits)

    def saved_files(self) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}

    def cut(self, name: str, writer: str = "write_atomically") -> Any:
        """A write cut short (the power went) as it writes the file <name> with store.<writer>."""
        real = getattr(store, writer)

        def cut_at(path: Path, *args: Any, **kwargs: Any) -> Any:
            if Path(path).name == name:
                raise OSError("power cut")
            return real(path, *args, **kwargs)

        return mock.patch.object(store, writer, side_effect=cut_at)

    def text(self, name: str) -> str:
        path = self.root / name
        return path.read_text().lower() if path.exists() else ""

    def kinds(self) -> list[str]:
        return [f["kind"] for f in json.loads(self.validate("--json")[0])]

    def assert_a_deletion_marker(self, marker: dict[str, Any], reason: str) -> None:
        """A deletion's marker: local, no target, and the forgotten record of its write (a keyed
        hash and counts), nothing of the person."""
        self.assertEqual((marker["op"], marker["target"], marker["local"]), (kc.DELETION, "", True))
        [record] = marker["body"]["forgotten"]
        self.assertEqual(record["reason"], reason)
        if reason == km.FORGET_REQUEST:
            self.assertEqual(record["hash_kind"], km.HASH_KEYED)
            self.assertRegex(record["hash"], "^[0-9a-f]{16}$")
        self.assertNotIn("dana", json.dumps(marker).lower())


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

    def test_a_fact_i_stated_stays_on_this_mac_whole(self) -> None:
        self.seed()
        session = "0123456789abcdef-acme-session"
        transcript = self.tmp / f"{session}.jsonl"
        transcript.write_text(json.dumps(typed(f"note: {QUOTE}, always", START - timedelta(hours=1))) + "\n")
        out = self.run_kg(
            "propose", "release", "gated_by", "gate/smoke-tests",
            "--from-user", "--quote", QUOTE, "--transcript", str(transcript),
        )  # fmt: skip
        self.assertIn("confirmed (your own words", out)
        stated = self.fact_id("process/release", "gated_by")
        self.run_kg("propose", "exporter", "caveat", "--value", "slow", "--source", f"transcript:{session}")
        seen = self.fact_id("repo/invoice-exporter", "caveat")
        private = (self.root / "journal" / f"{self.replica()}.jsonl").read_text()
        for secret in (QUOTE, session, stated, seen):
            self.assertNotIn(secret, private)
        local = [(c["op"], c["target"]) for c in self.local()]
        self.assertIn(("fact.add", stated), local)
        self.assertIn(("fact.update", stated), local)  # its confirmation holds no quote: the fact is mine
        self.assertIn(("fact.add", seen), local)
        self.assertTrue(all(c["local"] for c in self.local()))

    def test_my_words_added_later_keep_the_records_after_them_on_this_mac(self) -> None:
        self.seed()
        self.run_kg("propose", "release", "gated_by", "gate/smoke-tests", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "gated_by")
        session = "fedcba9876543210-acme-session"
        self.run_kg("propose", "release", "gated_by", "gate/smoke-tests", "--source", f"user:{session}")
        self.assertIn(("fact.add", fact_id), [(c["op"], c["target"]) for c in self.private()])
        self.assertIn(("fact.update", fact_id), [(c["op"], c["target"]) for c in self.local()])
        self.assertNotIn(session, (self.root / "journal" / f"{self.replica()}.jsonl").read_text())

    def test_a_record_holding_my_words_is_local_whatever_its_fact(self) -> None:
        mine = {"kind": "user", "ref": "0123", "quote": "smoke tests gate every release", "at": "x"}
        fact: dict[str, Any] = {"id": "f-aaaaaa", "subject": "repo/x", "object": None}
        fact["sources"] = [{"kind": "ledger", "ref": "a", "at": "x"}]
        add: dict[str, Any] = {"op": "add", "at": "x", "fact": fact}
        update: dict[str, Any] = {"op": "update", "id": "f-aaaaaa", "at": "x", "event": "observed"}
        update.update(set={}, sources=[mine])
        facts = {"f-aaaaaa": add["fact"]}
        self.assertFalse(kc.record_edit(add, facts).local)
        self.assertTrue(kc.record_edit(update, facts).local)
        for kind in ("user", "transcript"):
            stated = {**add, "fact": {**add["fact"], "sources": [{"kind": kind, "ref": "0123", "at": "x"}]}}
            self.assertTrue(kc.record_edit(stated, facts).local, kind)

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
        after, marker = self.local()[:-1], self.local()[-1]
        self.assertNotIn("dana", (self.root / "journal" / "local.jsonl").read_text().lower())
        self.assertEqual([c["seq"] for c in after], [c["seq"] for c in local])  # the sequence never goes back
        self.assert_a_deletion_marker(marker, "request")
        self.assertEqual(marker["body"]["forgotten"], store.read_jsonl(self.root / "forgotten.jsonl"))
        self.assertEqual(self.journal().position(), {**position, "local": position["local"] + 1})
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
        self.assert_a_deletion_marker(self.local()[-1], km.FORGET_RETENTION)
        self.assertIn("person/dana", {c.get("target") for c in self.local()})  # only the facts went
        self.assertNotIn("lisbon", self.git_log().lower())

    def leftovers(self) -> dict[str, Path]:
        """What writes cut short leave in the journal folder: a torn tail of each file, and a copy
        of local.jsonl from a rewrite cut short, each naming the person."""
        journal = self.root / "journal"
        found = {
            "local torn": journal / "local.jsonl.torn-20261021T090000Z",
            "local torn 2": journal / "local.jsonl.torn-20261021T090000Z-2",
            "rewrite copy": journal / ".local.jsonl.4242.tmp",
            "private torn": journal / f"{self.replica()}.jsonl.torn-20261021T090000Z",
        }
        for path in found.values():
            path.write_text('{"body":{"name":"Dana Example"},"op":"entity.put"')
        return found

    def test_forget_deletes_what_writes_cut_short_left_of_local_changes(self) -> None:
        self.seed()
        left = self.leftovers()
        self.run_kg("add-entity", "repo/other")  # a commit with the leftovers on disk
        self.assertNotIn("torn", store.git(self.root, "ls-files").stdout)  # never in the history
        self.run_kg("forget", "person/dana")
        for name in ("local torn", "local torn 2", "rewrite copy"):
            self.assertFalse(left[name].exists(), name)
        self.assertTrue(left["private torn"].exists())  # never local: it holds no person
        self.assertNotIn("dana", self.git_log().lower())

    def test_the_retention_purge_deletes_them_too(self) -> None:
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:dir"
        )
        left = self.leftovers()
        self.later(km.RETENTION_DAYS + 1)
        self.run_kg("expire")
        self.assertEqual([name for name, path in left.items() if path.exists()], ["private torn"])

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

    def test_genesis_keeps_what_names_a_person_or_holds_my_words_on_this_mac(self) -> None:
        changes = self.imported_then_written()
        mine = {
            i
            for i, f in self.kb().facts.items()
            if km.is_personal(f) or {s["kind"] for s in f["sources"]} & {"user", "transcript"}
        }
        self.assertIn(self.fact_id("process/hotfix", "entry_point"), mine)  # a transcript names my session
        local = {c["target"] for c in changes if c["cmd"] == knowledge.GENESIS and c.get("local")}
        self.assertEqual(local, {"person/dana", "person/lee", *mine})
        private = (self.root / "journal" / f"{self.replica()}.jsonl").read_text()
        self.assertNotIn("00000000-0000-0000-0000-00000000000", private)  # the fixture's sessions

    def test_genesis_keeps_a_person_on_this_mac_when_two_adds_share_an_id(self) -> None:
        self.write_legacy(
            [
                legacy_entity("repo/invoice-exporter", "invoice exporter"),
                legacy_entity("person/dana", "Dana"),
            ],
            [
                legacy_fact("f3", "person/dana", "reviews", object="repo/invoice-exporter"),
                legacy_fact("f3", "repo/invoice-exporter", "caveat", value="retries are slow"),
            ],  # a hand edit or a merge: the fold keeps the last add of an id
        )
        self.run_kg("add-entity", "repo/other")
        private = (self.root / "journal" / f"{self.replica()}.jsonl").read_text()
        self.assertNotIn("dana", private.lower())
        adds = [c for c in self.journal().changes() if c["op"] == "fact.add"]
        self.assertEqual([(c["target"], c.get("local", False)) for c in adds], [("f3", True), ("f3", False)])

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
        self.assertEqual({c.get("cmd") for c in self.journal().changes()}, {""})

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

    def test_a_lost_file_that_leaves_no_whole_transaction_is_refused_and_warned(self) -> None:
        """A genesis with a person in it spans both files: with the replica's file lost, no line
        left in local.jsonl is a whole transaction. That journal is behind view.json, not empty: a
        reader warns, naming the file (it once took it for none, without a word), a write is
        refused, and validate (bosun doctor) names that file only."""
        self.write_legacy(
            [legacy_entity("repo/invoice-exporter", "exporter"), legacy_entity("person/dana", "Dana")], []
        )
        self.run_kg("add-entity", "step/merge")  # the genesis first
        self.assertEqual({c["private_of"] for c in self.local()}, {1})
        (self.root / "journal" / f"{self.replica()}.jsonl").unlink()
        named = r"behind view\.json \(\w{32}\.jsonl ends at seq 0, view\.json reflects seq 2\)"
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.assertIn("person/dana", self.kb().entities)
        self.assertRegex(said.getvalue(), named)
        with self.assertRaisesRegex(KnowledgeError, named):
            self.run_kg("add-entity", "repo/other")
        found, code = self.validate()
        self.assertEqual(code, 1)
        self.assertRegex(found, named)
        self.assertNotIn("local.jsonl", found)

    def test_a_journal_moved_aside_starts_again_from_the_files(self) -> None:
        self.seed()
        (self.root / "journal").rename(self.root / "journal.old")
        self.run_kg("add-entity", "repo/other")
        self.assertEqual(
            sorted(c["target"] for c in self.journal().changes() if c["cmd"] == knowledge.GENESIS),
            sorted(e for e in self.kb().entities if e != "repo/other"),
        )


class BoundaryTest(JournalTestCase):
    def test_view_records_the_position_and_a_digest_of_each_file(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        kb = self.kb()
        digests = kc.file_digests(kb.entities, kb.records)
        self.assertEqual(self.view(), {"journal": {**self.journal().position(), "digest": digests}})
        self.assertEqual(sorted(digests), ["entities.jsonl", "facts.jsonl"])
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

    def test_each_files_digest_leaves_out_nothing_it_holds(self) -> None:
        entities = {"repo/x": legacy_entity("repo/x", "x")}
        records = [legacy_fact("f1", "repo/x", "caveat", value="slow")]
        held = kc.file_digests(entities, records)
        self.assertTrue(all(re.fullmatch("[0-9a-f]{64}", digest) for digest in held.values()))
        more = kc.file_digests(entities, [*records, records[0]])
        self.assertEqual(
            (more["entities.jsonl"], more["facts.jsonl"] != held["facts.jsonl"]),
            (held["entities.jsonl"], True),
        )
        fewer = kc.file_digests({}, records)
        self.assertEqual(
            (fewer["facts.jsonl"], fewer["entities.jsonl"] != held["entities.jsonl"]),
            (held["facts.jsonl"], True),
        )
        self.assertEqual(held, kc.file_digests(dict(reversed(list(entities.items()))), records))
        self.assertEqual(kc.held_digests(entities, [{**records[0], "x": 1.5}])["facts.jsonl"], None)


REPLICA_A, REPLICA_B = "a" * 32, "b" * 32
FACT = "f-aaaaaa"


def clock(n: int) -> str:
    return change_log.clock_text(1_793_000_000_000 + n, 0)


def envelope(op: str, target: str, body: dict[str, Any], n: int, replica: str = REPLICA_A) -> dict[str, Any]:
    """A change as a Mac's journal holds it, made at clock(n)."""
    made: dict[str, Any] = {
        "v": 1,
        "stream": "kg",
        "replica": replica,
        "seq": n,
        "hlc": clock(n),
        "tx": clock(n),
        "of": 1,
        "cmd": "test",
        "rules": 1,
        "op": op,
        "target": target,
        "body": body,
    }
    made["id"] = change_log.change_id(made)
    return made


def decided(fact_id: str, event: str, status: str, was: str = "proposed") -> dict[str, Any]:
    """An update record that moves a fact from <was> to <status>, saying so in its prev."""
    return {
        "op": "update",
        "id": fact_id,
        "at": LEGACY_AT,
        "event": event,
        "set": {"status": status},
        "prev": {"status": was},
        "note": "",
    }


class RebuildTest(unittest.TestCase):
    """kc.rebuild over changes made by hand: the one order, whatever order they come in."""

    def add(self, n: int = 1, replica: str = REPLICA_A) -> dict[str, Any]:
        body = legacy_fact(FACT, "repo/invoice-exporter", "caveat", value="retries are slow")
        return envelope(kc.FACT_ADD, FACT, body, n, replica)

    def test_an_early_update_waits_for_its_fact(self) -> None:
        """An update ordered before its add (another Mac's clock behind) is applied after it; the
        records keep the one order, and folding them gives the same."""
        confirm = envelope(kc.FACT_UPDATE, FACT, decided(FACT, "confirmed", "confirmed"), 1, REPLICA_B)
        add = self.add(2)
        rebuilt = kc.rebuild([add, confirm])
        self.assertEqual(rebuilt.records, [confirm["body"], add["body"]])
        fact = km.fold_all(rebuilt.records)[FACT]
        self.assertEqual(fact["status"], "confirmed")
        self.assertEqual([h["event"] for h in fact["history"]], ["added", "confirmed"])
        self.assertEqual((rebuilt.conflicts, rebuilt.pending), ([], []))
        alone = kc.rebuild([confirm])  # its add never came: it waits, and is listed
        self.assertEqual(alone.pending, [FACT])
        self.assertEqual(km.fold_all(alone.records), {})

    def test_confirm_and_reject_at_once_is_a_conflict(self) -> None:
        """Two Macs decided one proposal: the later decision, made from `proposed` too, is a field
        conflict and not applied, whichever it is."""
        for first, second in (("rejected", "confirmed"), ("confirmed", "rejected")):
            with self.subTest(first=first):
                earlier = envelope(kc.FACT_UPDATE, FACT, decided(FACT, first, first), 2)
                later = envelope(kc.FACT_UPDATE, FACT, decided(FACT, second, second), 3, REPLICA_B)
                rebuilt = kc.rebuild([later, self.add(), earlier])
                self.assertEqual(km.fold_all(rebuilt.records)[FACT]["status"], first)
                self.assertNotIn(later["body"], rebuilt.records)
                self.assertEqual(
                    [(c.change, c.target, c.kind) for c in rebuilt.conflicts], [(later["id"], FACT, "field")]
                )
                self.assertIn(f"proposed -> {second}, but it is {first} here", rebuilt.conflicts[0].detail)

    def test_an_update_without_prev_is_applied_as_written(self) -> None:
        """A record from before updates said what they replaced (a knowledge base from before the
        journal) is not checked."""
        old = decided(FACT, "rejected", "rejected")
        del old["prev"]
        twice = [self.add(), envelope(kc.FACT_UPDATE, FACT, decided(FACT, "confirmed", "confirmed"), 2)]
        rebuilt = kc.rebuild([*twice, envelope(kc.FACT_UPDATE, FACT, old, 3)])
        self.assertEqual(km.fold_all(rebuilt.records)[FACT]["status"], "rejected")
        self.assertEqual(rebuilt.conflicts, [])

    def changes(self) -> list[dict[str, Any]]:
        exporter = legacy_entity("repo/invoice-exporter", "invoice exporter")
        renamed = {**exporter, "name": "the invoice exporter"}
        dana = legacy_entity("person/dana", "Dana")
        return [
            envelope(kc.ENTITY_PUT, "repo/invoice-exporter", exporter, 1),
            envelope(kc.ENTITY_PUT, "step/merge", legacy_entity("step/merge", "Merge"), 2),
            envelope(kc.ENTITY_PUT, "repo/invoice-exporter", renamed, 3, REPLICA_B),
            envelope(kc.ENTITY_DELETE, "step/merge", {}, 4),
            change_log.placeholder(envelope(kc.ENTITY_PUT, "person/dana", dana, 5)),
            envelope(kc.FACT_UPDATE, FACT, decided(FACT, "confirmed", "confirmed"), 6, REPLICA_B),
            self.add(7),
            envelope(kc.FACT_UPDATE, FACT, decided(FACT, "rejected", "rejected"), 8),
            envelope(kc.FACT_UPDATE, "f-bbbbbb", decided("f-bbbbbb", "expired", "expired"), 9),
        ]

    def test_order_of_delivery_does_not_matter(self) -> None:
        """The same changes, shuffled and some delivered twice, rebuild the same."""
        changes = self.changes()
        expected = kc.rebuild(changes)
        self.assertEqual(expected.entities, {"repo/invoice-exporter": changes[2]["body"]})
        self.assertEqual([c.kind for c in expected.conflicts], ["field"])
        self.assertEqual(expected.pending, ["f-bbbbbb"])
        shuffler = random.Random(7)  # noqa: S311  # a fixed shuffle, not cryptography
        for round_ in range(8):
            delivered = changes + shuffler.sample(changes, len(changes) // 2)
            shuffler.shuffle(delivered)
            with self.subTest(round=round_):
                self.assertEqual(kc.rebuild(delivered), expected)

    def test_a_forgotten_change_rebuilds_to_nothing(self) -> None:
        dana = legacy_fact(FACT, "person/dana", "timezone", value="Europe/Lisbon")
        forgotten = [
            change_log.placeholder(
                envelope(kc.ENTITY_PUT, "person/dana", legacy_entity("person/dana", "Dana"), 1)
            ),
            change_log.placeholder(envelope(kc.FACT_ADD, FACT, dana, 2)),
        ]
        self.assertEqual(kc.rebuild(forgotten), kc.RebuiltKB({}, [], [], []))

    def test_what_this_bosun_cannot_apply_is_kept_as_a_conflict(self) -> None:
        newer = envelope("fact.merge", FACT, {}, 2)
        broken = envelope(kc.ENTITY_PUT, "repo/x", {}, 3)
        broken["body"] = "not an entity"
        odd = legacy_fact("f-bbbbbb", "repo/x", "caveat", value="slow", sources=["ledger:a"])
        unsourced = envelope(kc.FACT_ADD, "f-bbbbbb", odd, 4)  # a source that is not an object
        rebuilt = kc.rebuild([self.add(), newer, broken, unsourced])
        self.assertEqual(
            [(c.change, c.kind) for c in rebuilt.conflicts],
            [(newer["id"], "unknown"), (broken["id"], "unknown"), (unsourced["id"], "unknown")],
        )
        self.assertEqual(rebuilt.records, [self.add()["body"]])

    def test_the_rules_are_the_table_the_rebuild_dispatches_on(self) -> None:
        self.assertEqual(kc.APPLIED, tuple(sorted(kc._APPLIERS)))
        self.assertEqual(set(kc.SKIPPED), {change_log.FORGOTTEN, kc.DELETION})

    def test_rebuilding_does_not_change_the_changes(self) -> None:
        changes = self.changes()
        before = json.dumps(changes, sort_keys=True)
        rebuilt = kc.rebuild(changes)
        km.fold_all(rebuilt.records)[FACT]["sources"].append({"kind": "ledger", "ref": "x"})
        self.assertEqual(json.dumps(changes, sort_keys=True), before)


class RulesTest(unittest.TestCase):
    def test_the_rules_the_rebuild_checks_are_pinned_to_their_version(self) -> None:
        """Every change records the rules version it was checked under, and the rebuild checks a
        replica's past again under today's rules. A change to them (an op it applies, a field an
        update must find as it left it) fails here: bump knowledge.KG_RULES_VERSION and record the
        new digest under it, since it would rebuild old changes differently (docs/design/sync.md,
        open questions)."""
        self.assertEqual(KG_RULES_DIGESTS.get(knowledge.KG_RULES_VERSION), kg_rules_digest())


# The digest of the rules the knowledge rebuild checks (kg_rules_digest; a digest, not a secret), by
# knowledge.KG_RULES_VERSION.
KG_RULES_DIGESTS = {
    1: "6184556462867f9fdf7397dc8823e1093b29ec23d645dd75032afe83150f3901",  # pragma: allowlist secret
}


def kg_rules_digest() -> str:
    """SHA-256 of the rebuild's rules: the ops it applies (the keys of the table its dispatch
    reads), the ops it skips (a deletion's placeholder and its marker), and the fields whose
    `prev` an update must match."""
    table = {"applied": sorted(kc.APPLIED), "checked": list(kc.CHECKED), "skipped": sorted(kc.SKIPPED)}
    return hashlib.sha256(change_log.canonical(table)).hexdigest()


class RebuiltFilesTest(JournalTestCase):
    """The files are the journal's rebuild after every command; a write cut short between the
    journal and the files is repaired by the next write; a reader uses the rebuild and writes
    nothing."""

    def test_rebuild_reproduces_the_files_after_every_kg_command(self) -> None:
        self.seed()
        self.run_kg("add-entity", "person/lee", "--name", "Lee Example")
        self.assert_the_journal_rebuilds_the_files()
        steps: list[tuple[str, tuple[str, ...]]] = [
            ("add-entity", ("add-entity", "repo/invoice-exporter", "--alias", "the exporter")),
            ("propose", ("propose", "release", "has_step", "step/merge", "--source", "ledger:a")),
            (
                "propose a person fact",
                ("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a"),
            ),
            ("propose again", ("propose", "exporter", "caveat", "--value", "slow", "--source", "ledger:a")),
            ("observe", ("propose", "exporter", "caveat", "--value", "slow", "--source", "pr:b")),
            (
                "propose lee",
                ("propose", "person/lee", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a"),
            ),
            (
                "propose staging",
                ("propose", "staging 2", "caveat", "--value", "shared", "--source", "ledger:a"),
            ),
        ]
        for name, argv in steps:
            self.run_kg(*argv)
            with self.subTest(command=name):
                self.assert_the_journal_rebuilds_the_files()
        later: list[tuple[str, Any]] = [
            ("confirm", lambda: self.run_kg("confirm", self.fact_id("process/release", "has_step"))),
            ("reject", lambda: self.run_kg("reject", self.fact_id("repo/invoice-exporter", "caveat"))),
            (
                "supersede",
                lambda: self.run_kg("supersede", self.fact_id("process/release", "has_step"), "--close"),
            ),
            ("forget", lambda: self.run_kg("forget", "person/dana")),
            ("expire", lambda: self.run_kg("expire")),
        ]
        for name, step in later:
            if name == "expire":
                self.later(km.RETENTION_DAYS + 1)
            step()
            with self.subTest(command=name):
                self.assert_the_journal_rebuilds_the_files()
        rebuilt = kc.rebuild(self.journal().changes())
        self.assertNotIn("dana", json.dumps([rebuilt.entities, rebuilt.records]).lower())  # placeholders
        self.assertNotIn("lisbon", json.dumps(rebuilt.records).lower())  # past retention
        self.assertIn("expired", {r.get("event") for r in rebuilt.records})

    def test_a_crash_before_the_files_is_repaired(self) -> None:
        """A transaction in the journal that never reached the files: a reader sees it without
        writing anything; the next write has it once and saves the files whole."""
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "has_step")
        self.commit_by_hand(kc.record_edit(decided(fact_id, "confirmed", "confirmed"), self.kb().facts))
        files = self.saved_files()
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.assertEqual(self.fact(fact_id)["status"], km.CONFIRMED)
            self.assertIn(fact_id, self.run_kg("facts", "release"))
        self.assertEqual(said.getvalue(), "")
        self.assertEqual(self.saved_files(), files)  # a reader never writes
        self.assertEqual(self.validate()[1], 1)  # the journal is ahead of the files
        self.run_kg("add-entity", "repo/other")
        self.assertEqual([r.get("event") for r in self.records()].count("confirmed"), 1)
        self.assertIn("repo/other", self.file_entities())
        self.assert_the_journal_rebuilds_the_files()
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))

    def test_a_forget_cut_between_the_two_files_is_finished_not_adopted(self) -> None:
        """Cut after entities.jsonl and before facts.jsonl: each file is either as view.json
        recorded it or as the journal's rebuild makes it, so this is a write cut short, never an
        edit to adopt (which would bring the person's facts back into local.jsonl)."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        self.run_kg("identify", "person/dana", "slack", "U00000000DA")
        with self.cut("facts.jsonl"), self.assertRaises(OSError):
            self.run_kg("forget", "person/dana")
        self.assertNotIn("person/dana", self.file_entities())
        self.assertIn("lisbon", self.text("facts.jsonl"))
        self.assertEqual(self.kinds(), ["position"])  # no edit to adopt: the next write finishes it
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("add-entity", "repo/other")
        self.assertEqual(said.getvalue(), "")
        for name in ("facts.jsonl", "entities.jsonl", "identifiers.jsonl", "journal/local.jsonl"):
            self.assertNotIn("lisbon", self.text(name), name)
            self.assertNotIn("dana", self.text(name), name)
        self.assertNotIn("u00000000da", self.text("identifiers.jsonl"))
        self.assertNotIn(change_log.ADOPT, {c.get("cmd") for c in self.journal().changes()})
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()
        with self.assertRaisesRegex(KnowledgeError, "deleted on request"):
            self.run_kg("add-entity", "person/dana")

    def test_a_write_cut_between_the_two_files_is_finished_not_adopted(self) -> None:
        self.seed()
        made = self.tmp / "more.jsonl"
        made.write_text(
            json.dumps({"type": "entity", "id": "repo/pdf-renderer", "name": "pdf renderer"})
            + "\n"
            + json.dumps(
                {
                    "type": "fact",
                    "subject": "repo/pdf-renderer",
                    "predicate": "caveat",
                    "value": "fonts",
                    "source": "ledger:a",
                }
            )
            + "\n"
        )
        with self.cut("facts.jsonl", "append_records"), self.assertRaises(OSError):
            self.run_kg("import", str(made), "--apply")
        self.assertIn("repo/pdf-renderer", self.file_entities())
        self.assertEqual(self.kinds(), ["position"])
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("add-entity", "repo/other")
        self.assertEqual(said.getvalue(), "")
        self.assertNotIn(change_log.ADOPT, {c.get("cmd") for c in self.journal().changes()})
        self.assertEqual([r["fact"]["value"] for r in self.records() if r["op"] == "add"], ["fonts"])
        self.assert_the_journal_rebuilds_the_files()

    def test_a_forget_cut_before_the_files_is_finished_by_the_next_write(self) -> None:
        """A deletion commits a local marker with no target (the rebuild skips it), so the journal's
        position moves and the next write rebuilds the files without the person; their identifiers
        go with them."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        self.run_kg("identify", "person/dana", "slack", "U00000000DA")
        before = self.journal().position()
        with (
            mock.patch.object(knowledge, "write_files", side_effect=OSError("power cut")),
            self.assertRaises(OSError),
        ):
            self.run_kg("forget", "person/dana")
        self.assertEqual(self.journal().position()["local"], before["local"] + 1)
        self.assert_a_deletion_marker(self.local()[-1], "request")
        self.assertFalse((self.root / "forgotten.jsonl").exists())
        self.assertNotIn("person/dana", self.kb().entities)  # a reader sees the rebuild
        self.run_kg("add-entity", "repo/other")
        self.assertNotIn("person/dana", self.file_entities())
        self.assertNotIn("lisbon", self.text("facts.jsonl"))
        self.assertNotIn("u00000000da", self.text("identifiers.jsonl"))
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()
        # the marker's record is in forgotten.jsonl now: the forget is whole through the journal
        self.assertEqual(
            store.read_jsonl(self.root / "forgotten.jsonl"), self.local()[-1]["body"]["forgotten"]
        )
        with self.assertRaisesRegex(KnowledgeError, "deleted on request"):
            self.run_kg("add-entity", "person/dana")
        self.run_kg("add-entity", "person/dana", "--allow-forgotten")

    def test_a_forget_with_the_history_off_removes_a_history_left_from_before(self) -> None:
        """BOSUN_KNOWLEDGE_GIT=0 now, but an older bosun (or this one, before) kept a history here:
        it holds the person, so a forget removes it whatever the setting says."""
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        store.commit(self.root, "kg: the history kept before it was turned off")
        self.run_kg("forget", "person/dana")
        self.assertFalse((self.root / ".git").exists())

    def test_the_journals_markers_keep_a_person_forgotten_when_forgotten_jsonl_is_gone(self) -> None:
        """Forgotten is forgotten.jsonl and the records the journal's deletion markers carry: a write
        puts back a marker's record forgotten.jsonl lacks, first, so no check misses it."""
        self.seed()
        self.run_kg("forget", "person/dana")
        recorded = store.read_jsonl(self.root / "forgotten.jsonl")
        (self.root / "forgotten.jsonl").unlink()
        with self.assertRaisesRegex(KnowledgeError, "deleted on request"):
            self.run_kg("add-entity", "person/dana", "--name", "Dana Example")
        self.assertEqual(store.read_jsonl(self.root / "forgotten.jsonl"), recorded)
        self.run_kg("add-entity", "repo/other")
        self.assertEqual(store.read_jsonl(self.root / "forgotten.jsonl"), recorded)

    def test_the_way_out_for_a_lost_key_moves_the_journal_aside_too(self) -> None:
        """With the key gone for good, forgotten.jsonl alone is not enough (the markers hold the same
        records): moved aside with the journal folder and replica.json, the next write starts again
        from the files and makes a new key, and the people can be added again."""
        self.seed()
        self.run_kg("forget", "person/dana")
        (self.root / "secret.key").unlink()
        (self.root / "forgotten.jsonl").rename(self.tmp / "forgotten.aside")
        with self.assertRaisesRegex(KnowledgeError, "forgotten.jsonl and the journal folder"):
            self.run_kg(
                "add-entity", "person/dana", "--name", "Dana Example"
            )  # the markers' records are back
        for path in (self.root / "forgotten.jsonl", self.root / "journal", self.state / "replica.json"):
            path.rename(self.tmp / f"{path.name}.aside-2")  # together
        self.run_kg("add-entity", "person/dana", "--name", "Dana Example")
        self.assertIn("person/dana", self.file_entities())
        self.assertTrue((self.root / "secret.key").exists())
        self.assertEqual(store.read_jsonl(self.root / "forgotten.jsonl"), [])

    def test_a_write_that_changes_nothing_still_saves_the_files_it_found_behind(self) -> None:
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "has_step")
        self.commit_by_hand(kc.record_edit(decided(fact_id, "confirmed", "confirmed"), self.kb().facts))
        self.run_kg("add-entity", "process/release")  # unchanged
        self.assert_the_journal_rebuilds_the_files()

    def test_a_write_whose_rebuild_has_conflicts_says_so_once(self) -> None:
        """A change the rebuild keeps but cannot apply, and an update whose fact never came: a write
        that rebuilds says how many, and where to see them, once on stderr; a reader stays quiet."""
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "has_step")
        self.run_kg("confirm", fact_id)
        facts = self.kb().facts
        self.commit_by_hand(
            kc.record_edit(decided(fact_id, "rejected", "rejected"), facts),  # from proposed: crossed
            kc.record_edit(decided("f-zzzzzz", "rejected", "rejected"), facts),  # no such fact
        )
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("stats")
            self.assertEqual(said.getvalue(), "")
            self.run_kg("add-entity", "repo/other")
        self.assertEqual(said.getvalue().count("\n"), 1, said.getvalue())
        self.assertIn("1 conflict", said.getvalue())
        self.assertIn("1 update", said.getvalue())
        self.assertIn("bosun kg validate", said.getvalue())
        self.assertEqual(self.fact(fact_id)["status"], km.CONFIRMED)
        output, code = self.validate("--json")
        self.assertEqual(code, 1)
        found = json.loads(output)
        self.assertEqual(
            sorted((f["kind"], f["severity"], f["item"]) for f in found),
            [("conflict", "warning", fact_id), ("pending", "warning", "f-zzzzzz")],
        )

    def test_a_reader_of_a_damaged_journal_warns_once_and_reads_the_files(self) -> None:
        self.seed()
        path = self.root / "journal" / f"{self.replica()}.jsonl"
        path.write_bytes(path.read_bytes() + b"nonsense\n")
        files = self.saved_files()
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.assertIn("6 entities", self.run_kg("stats"))
        self.assertEqual(said.getvalue().count("\n"), 1, said.getvalue())
        self.assertIn("damaged", said.getvalue())
        self.assertEqual(self.saved_files(), files)


class OutsideEditTest(JournalTestCase):
    """The knowledge files changed outside the journal: by hand, a git checkout or revert in the
    knowledge folder, or a bosun that does not journal (it leaves view.json as it was). The next
    write puts what it can in the journal ("adopt") and goes on from the journal's rebuild."""

    def edit_entity(self, entity_id: str, **fields: Any) -> None:
        path = self.root / "entities.jsonl"
        entities = [{**e, **fields} if e["id"] == entity_id else e for e in store.read_jsonl(path)]
        path.write_text(store.jsonl(entities))

    def test_a_hand_edit_is_adopted_by_the_next_write(self) -> None:
        self.seed()
        self.edit_entity("repo/invoice-exporter", summary="exports invoices nightly")
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("add-entity", "repo/other")
        self.assertEqual(said.getvalue().count("\n"), 1, said.getvalue())
        self.assertIn("adopted 1 change", said.getvalue())
        adopted = [(c["op"], c["target"]) for c in self.journal().changes() if c["cmd"] == change_log.ADOPT]
        self.assertEqual(adopted, [("entity.put", "repo/invoice-exporter")])
        self.assertEqual(self.file_entities()["repo/invoice-exporter"]["summary"], "exports invoices nightly")
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()

    def test_an_old_bosuns_write_is_adopted(self) -> None:
        """What an older bosun's confirm does: append a record (with no prev) to the fact log, and
        leave the journal and view.json as they were."""
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "has_step")
        record = decided(fact_id, "confirmed", "confirmed")
        del record["prev"]
        store.append_records(self.root / "facts.jsonl", [record])
        with contextlib.redirect_stderr(io.StringIO()):
            self.run_kg("add-entity", "repo/other")
        adopted = [(c["op"], c["target"]) for c in self.journal().changes() if c["cmd"] == change_log.ADOPT]
        self.assertEqual(adopted, [("fact.update", fact_id)])
        self.assertEqual(self.fact(fact_id)["status"], km.CONFIRMED)
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()

    def test_an_unchanged_file_adopts_nothing(self) -> None:
        self.seed()
        path = self.root / "entities.jsonl"
        path.write_text("".join(json.dumps(e, indent=None) + "\n" for e in reversed(store.read_jsonl(path))))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("add-entity", "repo/other")
        self.assertEqual(said.getvalue(), "")
        self.assertNotIn(change_log.ADOPT, {c.get("cmd") for c in self.journal().changes()})
        self.assert_the_journal_rebuilds_the_files()

    def test_an_edit_adopted_by_a_write_cut_short_is_adopted_once(self) -> None:
        self.seed()
        self.edit_entity("repo/invoice-exporter", summary="exports invoices nightly")
        with (
            mock.patch.object(knowledge, "write_files", side_effect=OSError("power cut")),
            self.assertRaises(OSError),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.run_kg("add-entity", "repo/other")
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("add-entity", "repo/third")
        self.assertEqual(said.getvalue(), "")
        adopted = [c["target"] for c in self.journal().changes() if c["cmd"] == change_log.ADOPT]
        self.assertEqual(adopted, ["repo/invoice-exporter"])
        self.assertEqual(
            sorted(e for e in self.file_entities() if e.startswith("repo/")),
            ["repo/invoice-exporter", "repo/other", "repo/third"],
        )
        self.assert_the_journal_rebuilds_the_files()

    def test_a_write_cut_short_after_the_files_is_no_outside_edit(self) -> None:
        """The files are written before view.json: cut between the two, they are already the
        journal's rebuild, so nothing is adopted (it would hold the write's records twice)."""
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "has_step")
        with (
            mock.patch.object(knowledge, "write_view", side_effect=OSError("power cut")),
            self.assertRaises(OSError),
        ):
            self.run_kg("confirm", fact_id)
        self.assertEqual([f["kind"] for f in json.loads(self.validate("--json")[0])], ["position"])
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("add-entity", "repo/other")
        self.assertEqual(said.getvalue(), "")
        self.assertNotIn(change_log.ADOPT, {c.get("cmd") for c in self.journal().changes()})
        self.assertEqual([r.get("event") for r in self.records()].count("confirmed"), 1)
        self.assert_the_journal_rebuilds_the_files()

    def test_validate_says_an_outside_edit_once_not_per_entity(self) -> None:
        self.seed()
        self.edit_entity("repo/invoice-exporter", summary="exports invoices nightly")
        self.edit_entity("step/merge", name="Merge to trunk")
        output, code = self.validate("--json")
        self.assertEqual(code, 1)
        self.assertEqual([(f["kind"], f["severity"]) for f in json.loads(output)], [("edited", "warning")])
        self.assertIn("the next write adopts it", self.validate()[0])

    def test_what_a_float_written_by_hand_holds_is_taken_in(self) -> None:
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--order", "2", "--source", "ledger:a")
        path = self.root / "facts.jsonl"
        path.write_text(path.read_text().replace('"order": 2', '"order": 2.5'))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("add-entity", "repo/other")
        self.assertIn("a float (2.5)", said.getvalue())
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()


class OutsideEditHistoryTest(JournalTestCase):
    git = True

    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        super().setUp()

    def test_a_record_removed_by_hand_comes_back_and_the_history_keeps_the_edit(self) -> None:
        """What the journal cannot hold (the fact log only grows): the record is back, the write
        says so once, and the knowledge folder's git history holds the files as they were edited."""
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "has_step")
        self.run_kg("confirm", fact_id)
        path = self.root / "facts.jsonl"
        edited = "".join(line + "\n" for line in self.log_lines()[:-1])  # the confirmation removed
        path.write_text(edited)
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("add-entity", "repo/other")
        self.assertEqual(said.getvalue().count("\n"), 1, said.getvalue())
        self.assertIn("only grows", said.getvalue())
        self.assertIn("git history", said.getvalue())
        self.assertEqual(self.fact(fact_id)["status"], km.CONFIRMED)
        commits = store.git(self.root, "log", "--format=%H").stdout.split()
        shown = [store.git(self.root, "show", f"{commit}:facts.jsonl").stdout for commit in commits]
        self.assertIn(edited, shown)
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()

    def history(self) -> str:
        return store.git(self.root, "log", "-p", "--all").stdout.lower()

    def old_bosun_forgets(self, person: str) -> None:
        """What a bosun from before the journal does for kg forget: the person's entity, facts and
        identifiers out of the files, a plain sha256 record in forgotten.jsonl, the git history
        restarted with every file in the folder; the journal and view.json as they were."""
        facts = store.read_jsonl(self.root / "facts.jsonl")
        gone = {i for i, f in km.fold_all(facts).items() if person in (f["subject"], f.get("object"))}
        (self.root / "facts.jsonl").write_text(
            store.jsonl([r for r in facts if km.record_fact_id(r) not in gone])
        )
        entities = [e for e in store.read_jsonl(self.root / "entities.jsonl") if e["id"] != person]
        (self.root / "entities.jsonl").write_text(store.jsonl(entities))
        identifiers = [i for i in store.read_jsonl(self.root / "identifiers.jsonl") if i["entity"] != person]
        (self.root / "identifiers.jsonl").write_text(store.jsonl(identifiers))
        forgotten = {
            "at": LEGACY_AT,
            "hash": km.plain_hash_id(person),
            "reason": "request",
            "facts": len(gone),
        }
        store.append_records(self.root / "forgotten.jsonl", [forgotten])
        store.drop_history(self.root)
        store.commit(self.root, "kg forget: a person deleted on request")

    def write_saying(self, *argv: str) -> str:
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg(*(argv or ("add-entity", "repo/other")))
        return said.getvalue()

    def forget_cut(self, cut: Any) -> None:
        """dana proposed (in the history), then a kg forget cut short by <cut>, then a write: nothing
        of her is left anywhere, the history included, and validate is clean."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        self.assertIn("lisbon", self.history())
        with cut, self.assertRaises(OSError), contextlib.redirect_stderr(io.StringIO()):
            self.run_kg("forget", "person/dana")
        self.write_saying()
        self.assert_dana_is_gone()

    def assert_dana_is_gone(self, word: str = "lisbon") -> None:
        for name in ("journal/local.jsonl", "facts.jsonl", "entities.jsonl", "identifiers.jsonl"):
            self.assertNotIn(word, self.text(name), name)
        self.assertNotIn(word, self.history())
        self.assertNotIn("person/dana", self.file_entities())
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()
        with self.assertRaisesRegex(KnowledgeError, "deleted on request"):
            self.run_kg("add-entity", "person/dana")

    def power_cut(self, target: Any, name: str) -> Any:
        return mock.patch.object(target, name, side_effect=OSError("power cut"))

    def test_a_forget_cut_before_the_files_leaves_nothing_in_the_history(self) -> None:
        self.forget_cut(self.power_cut(knowledge, "write_files"))

    def test_a_forget_cut_between_the_files_leaves_nothing_in_the_history(self) -> None:
        self.forget_cut(self.cut("facts.jsonl"))

    def test_a_forget_cut_before_view_json_leaves_nothing_in_the_history(self) -> None:
        self.forget_cut(self.power_cut(knowledge, "write_view"))

    def test_a_forget_cut_at_the_history_restart_leaves_nothing_in_it(self) -> None:
        self.forget_cut(self.power_cut(store, "drop_history"))

    def test_a_forget_cut_after_view_json_leaves_nothing_in_the_history(self) -> None:
        self.forget_cut(self.power_cut(knowledge, "record_history"))

    def test_a_forget_cut_before_its_placeholders_is_finished(self) -> None:
        """The marker is committed before the placeholder rewrite; cut between the two, the next
        write puts the placeholders in for the marker's hashed id."""
        self.forget_cut(self.power_cut(jr.Journal, "forget"))

    def test_a_forget_outside_the_journal_cut_inside_is_finished(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        self.old_bosun_forgets("person/dana")
        with (
            self.power_cut(jr.Journal, "forget"),
            self.assertRaises(OSError),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.run_kg("add-entity", "repo/other")
        self.write_saying("add-entity", "repo/third")
        self.assertNotIn("lisbon", self.text("journal/local.jsonl"))
        self.assertNotIn("lisbon", self.history())
        self.assertNotIn("dana", self.history())
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))

    def everything(self) -> tuple[dict[str, bytes], str]:
        """Every file in the knowledge folder but the git history's, and that history."""
        files = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(self.root).parts
        }
        return files, self.history()

    def forget_cut_then_key_lost(self, cut: Any) -> None:
        """A kg forget cut short by <cut>, then secret.key unreadable at the next write: that write
        cannot tell whom the forget deleted, so it is refused whole, naming the key, and writes
        nothing (it would move view.json past the forget, which would then never be finished). With
        the key back, the next write finishes the forget: nothing of her anywhere, history included."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        with cut, self.assertRaises(OSError), contextlib.redirect_stderr(io.StringIO()):
            self.run_kg("forget", "person/dana")
        key = self.root / "secret.key"
        saved = key.read_bytes()
        key.unlink()
        before = self.everything()
        with self.assertRaises(KnowledgeError) as refused:
            self.write_saying()
        said = str(refused.exception)
        self.assertIn(str(key), said)
        self.assertIn("nothing was written", said)
        self.assertIn("Once the key is back, the next write finishes the deletion", said)
        self.assertNotIn("dana", said.lower())
        self.assertEqual(self.everything(), before)
        self.assertFalse(key.exists())  # never made anew
        key.write_bytes(saved)
        self.write_saying()
        self.assert_dana_is_gone()

    def left_in_the_folder(self) -> list[Path]:
        """What writes cut short leave in the knowledge folder, naming her: a kg batch cut before
        its rename (batch.json.tmp holds group keys and titles) and a data file's new text."""
        left = [self.root / "batch.json.tmp", self.root / "entities.jsonl.tmp"]
        left[0].write_text(json.dumps({"groups": [{"key": "person/dana", "title": "Dana Example"}]}))
        left[1].write_text(json.dumps({"id": "person/dana", "name": "Dana Example"}) + "\n")
        return left

    def test_forget_deletes_what_writes_cut_short_left_in_the_knowledge_folder(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        self.left_in_the_folder()
        self.run_kg("forget", "person/dana")
        self.assertEqual([p.name for p in self.root.glob("*.tmp")], [])

    def test_the_write_that_finishes_a_forget_deletes_them_too(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        with self.power_cut(knowledge, "write_files"), self.assertRaises(OSError):
            self.run_kg("forget", "person/dana")
        self.left_in_the_folder()
        self.write_saying()
        self.assertEqual([p.name for p in self.root.glob("*.tmp")], [])
        self.assert_dana_is_gone()

    def test_a_deletion_cut_short_deletes_the_update_backups_when_finished(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        with self.power_cut(jr.Journal, "forget"), self.assertRaises(OSError):
            self.run_kg("forget", "person/dana")
        backup = self.state / ub.BACKUPS_DIR / "20261105T090000Z-aaa-bbb"
        backup.mkdir(parents=True)
        (backup / ub.MANIFEST).write_text("{}\n")
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.run_kg("add-entity", "repo/other")
        self.assertFalse(backup.exists())
        self.assertIn("kg: deleted 1 update backup taken before this;", said.getvalue())

    def test_a_purge_cut_between_its_marker_and_its_placeholders_is_finished(self) -> None:
        """The retention purge's marker carries the ids of the facts it removed, so the next write
        makes them placeholders, as for a forget, instead of bringing them back until kg expire."""
        self.seed()
        self.run_kg(
            "propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "profile:dir"
        )
        fact_id = self.fact_id("person/dana", "timezone")
        self.later(km.RETENTION_DAYS + 1)
        with self.power_cut(jr.Journal, "forget"), self.assertRaises(OSError):
            self.run_kg("expire")
        [marker] = [c for c in self.local() if c.get("op") == kc.DELETION]
        self.assertEqual(marker["body"]["facts"], [fact_id])  # ids only, nothing of the person
        self.assertIn("lisbon", self.text("journal/local.jsonl"))  # what the cut left
        self.write_saying()
        for name in ("journal/local.jsonl", "facts.jsonl"):
            self.assertNotIn("lisbon", self.text(name), name)
        self.assertNotIn("lisbon", self.history())
        self.assertIn("person/dana", self.file_entities())  # only the facts went
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()

    def test_a_forget_cut_before_its_placeholders_waits_for_its_key(self) -> None:
        self.forget_cut_then_key_lost(self.power_cut(jr.Journal, "forget"))

    def test_a_forget_cut_before_the_files_waits_for_its_key(self) -> None:
        self.forget_cut_then_key_lost(self.power_cut(knowledge, "write_files"))

    def test_finishing_a_forget_cut_after_its_rewrite_deletes_what_cut_writes_left(self) -> None:
        """A kg forget cut between its placeholder rewrite and deleting what writes cut short left of
        local.jsonl (a torn tail an earlier cut write left, naming her, and a rewrite's unfinished
        copy): the write that finishes it deletes them too, as a forget does."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        folder = self.root / "journal"
        with (folder / "local.jsonl").open("ab") as handle:  # a write cut mid-line, naming her
            handle.write(b'{"body":{"fact":{"subject":"person/dana","value":"Europe/Lisbon"}}')
        with contextlib.redirect_stderr(io.StringIO()):
            self.run_kg("add-entity", "repo/other")  # repair_tail moves it aside
        [torn] = folder.glob("local.jsonl.torn-*")
        rewrite_copy = folder / ".local.jsonl.4242.tmp"
        rewrite_copy.write_text('{"body":{"name":"Dana Example"},"op":"entity.put"')
        rewrite = jr.Journal._placeholders

        def then_cut(journal: jr.Journal, targets: Any) -> int:
            rewrite(journal, targets)
            raise OSError("power cut after the rewrite, before the leftovers go")

        with (
            mock.patch.object(jr.Journal, "_placeholders", then_cut),
            self.assertRaises(OSError),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.run_kg("forget", "person/dana")
        self.assertTrue(torn.exists() and rewrite_copy.exists())  # the cut left them
        self.write_saying("add-entity", "repo/third")
        self.assertEqual(list(folder.glob("local.jsonl.torn-*")), [])
        self.assertFalse(rewrite_copy.exists())
        self.assert_dana_is_gone()

    def a_record_removed_by_hand(self) -> str:
        """A confirmed fact's confirmation removed from facts.jsonl by hand (the journal cannot take
        that); the files as edited."""
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        self.run_kg("confirm", self.fact_id("process/release", "has_step"))
        edited = "".join(line + "\n" for line in self.log_lines()[:-1])
        (self.root / "facts.jsonl").write_text(edited)
        return edited

    def assert_said_not_kept(self, said: str, edited: str) -> None:
        """The line on what the journal could not take says nothing keeps the files as edited, and
        why (the same write starts the history again); no commit holds them."""
        self.assertIn("only grows", said)
        self.assertIn("nothing keeps the files as they were edited", said)
        self.assertIn("holds what this write deletes, so it starts again", said)
        self.assertNotIn("git history keeps", said)
        commits = store.git(self.root, "log", "--format=%H").stdout.split()
        shown = [store.git(self.root, "show", f"{commit}:facts.jsonl").stdout for commit in commits]
        self.assertNotIn(edited, shown)

    def test_files_edited_are_not_said_kept_when_a_forget_outside_restarts_the_history(self) -> None:
        """An older bosun's kg forget, and a record removed by hand: the write applies the forget to
        the journal and starts the history again, so no history keeps the files as edited."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        self.a_record_removed_by_hand()
        self.old_bosun_forgets("person/dana")
        edited = (self.root / "facts.jsonl").read_text()
        self.assert_said_not_kept(self.write_saying(), edited)
        self.assertNotIn("lisbon", self.history())

    def test_files_edited_are_not_said_kept_when_the_command_deletes(self) -> None:
        """A record removed by hand, then kg forget: the forget starts the history again."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        edited = self.a_record_removed_by_hand()
        self.assert_said_not_kept(self.write_saying("forget", "person/dana"), edited)
        self.assert_dana_is_gone()

    def test_files_edited_are_not_said_kept_when_a_cut_forget_is_finished(self) -> None:
        """A kg forget cut short after its files, then a record removed by hand: the write finishes
        the forget and starts the history again."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        self.run_kg("confirm", self.fact_id("process/release", "has_step"))
        with self.power_cut(knowledge, "write_view"), self.assertRaises(OSError):
            self.run_kg("forget", "person/dana")
        edited = "".join(line + "\n" for line in self.log_lines()[:-1])  # the confirmation removed
        (self.root / "facts.jsonl").write_text(edited)
        self.assert_said_not_kept(self.write_saying(), edited)
        self.assert_dana_is_gone()

    def test_a_forget_outside_the_journal_with_a_refused_person_leaves_neither_in_the_history(self) -> None:
        """Files restored from a backup made when bob was forgotten and alice not yet: bob, added
        again since, is gone from them (as an older bosun's forget would leave it), and alice,
        forgotten since, is in them. The restart is paid at the save, so no commit holds her."""
        self.seed()
        self.run_kg("add-entity", "person/bob", "--name", "Bob Example")
        self.run_kg("forget", "person/bob")
        self.run_kg("add-entity", "person/alice", "--name", "Alice Example")
        self.run_kg("propose", "person/alice", "timezone", "--value", "Europe/Madrid", "--source", "ledger:a")
        backup = {name: (self.root / name).read_bytes() for name in ("entities.jsonl", "facts.jsonl")}
        self.run_kg("add-entity", "person/bob", "--name", "Bob Example", "--allow-forgotten")
        self.run_kg("forget", "person/alice")
        for name, data in backup.items():
            (self.root / name).write_bytes(data)
        self.write_saying()
        self.assertNotIn("madrid", self.history())
        self.assertNotIn("alice", self.history())
        self.assertNotIn("person/alice", self.file_entities())
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))

    def test_a_cut_forget_then_an_edit_by_hand_does_not_bring_the_person_back(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        with self.power_cut(knowledge, "write_files"), self.assertRaises(OSError):
            self.run_kg("forget", "person/dana")
        path = self.root / "entities.jsonl"
        edited = [
            {**e, "summary": "exports"} if e["id"] == "repo/invoice-exporter" else e
            for e in store.read_jsonl(path)
        ]
        path.write_text(store.jsonl(edited))
        said = self.write_saying()
        self.assertNotIn("dana", said.lower())
        self.assertEqual(
            self.file_entities()["repo/invoice-exporter"]["summary"], "exports"
        )  # the edit is kept
        self.assertNotIn(
            "person/dana", {c.get("target") for c in self.local() if c.get("cmd") == change_log.ADOPT}
        )
        self.assertEqual(len(store.read_jsonl(self.root / "forgotten.jsonl")), 1)  # the record is kept
        self.assert_dana_is_gone()

    def restored_from_before_the_forget(self, view: bool) -> None:
        """entities.jsonl, facts.jsonl and forgotten.jsonl as a backup made before kg forget holds
        them (no forgotten.jsonl then); view.json as now, or gone too."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        backup = {name: (self.root / name).read_bytes() for name in ("entities.jsonl", "facts.jsonl")}
        self.run_kg("forget", "person/dana")
        for name, data in backup.items():
            (self.root / name).write_bytes(data)
        (self.root / "forgotten.jsonl").unlink()
        if not view:
            (self.root / "view.json").unlink()
        said = self.write_saying()
        self.assertNotIn("dana", said.lower())
        self.assertEqual(len(store.read_jsonl(self.root / "forgotten.jsonl")), 1)  # the record is kept
        self.assert_dana_is_gone()

    def test_files_restored_from_before_a_forget_do_not_bring_the_person_back(self) -> None:
        self.restored_from_before_the_forget(view=True)

    def test_files_restored_from_before_a_forget_without_view_json_do_not_either(self) -> None:
        self.restored_from_before_the_forget(view=False)

    def test_an_older_bosuns_re_add_of_a_forgotten_person_is_dropped_and_its_commit_too(self) -> None:
        """An older bosun knows only plain hashes, so it lets a person forgotten with a keyed record
        be added again, and commits them: the next write drops them, history included."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        backup = {name: (self.root / name).read_bytes() for name in ("entities.jsonl", "facts.jsonl")}
        self.run_kg("forget", "person/dana")
        for name, data in backup.items():
            (self.root / name).write_bytes(data)
        store.commit(self.root, "kg add-entity person/dana (an older bosun)")
        self.assertIn("lisbon", self.history())
        self.write_saying()
        self.assert_dana_is_gone()

    def test_a_reason_for_files_not_kept_is_a_true_one(self) -> None:
        p, refused = knowledge.paths(), {"person/someone"}
        self.assertIn("deleted on request", knowledge.kept_as_edited(p, refused, "", restart=True))
        unchecked = knowledge.kept_as_edited(p, refused, "the key ... is missing", restart=True)
        self.assertIn("cannot be checked", unchecked)
        self.assertNotIn("name a person deleted on request", unchecked)
        restarted = knowledge.kept_as_edited(p, set(), "", restart=True)
        self.assertIn("holds what this write deletes, so it starts again", restarted)
        self.assertFalse((self.root / ".git").exists())  # nothing committed

    def test_a_forget_made_outside_the_journal_is_applied_to_it(self) -> None:
        """An older bosun's kg forget: the next write forgets the person in the journal too (R12's
        placeholders) and restarts the history, which held local.jsonl with them, never bringing
        them back."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        self.run_kg("identify", "person/dana", "slack", "U00000000DA")
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        self.old_bosun_forgets("person/dana")
        self.assertIn("lisbon", self.history())  # the old bosun's history holds the journal
        said = self.write_saying()
        self.assertIn("deleted on request", said)
        self.assertNotIn("dana", said.lower())
        self.assertNotIn("does not journal", said)  # no cause claimed: it may be a hand removal
        for name in ("journal/local.jsonl", "facts.jsonl", "entities.jsonl"):
            self.assertNotIn("lisbon", self.text(name), name)
            self.assertNotIn("dana", self.text(name), name)
        self.assertNotIn("lisbon", self.history())
        self.assertNotIn("dana", self.history())
        self.assertIn("process/release", self.file_entities())
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()

    def test_a_person_deleted_on_request_is_not_taken_back_from_restored_files(self) -> None:
        """entities.jsonl and facts.jsonl restored from a backup made before kg forget: the person
        forgotten.jsonl names is not adopted, the write says so without naming them, and they go
        from the files; no history ever holds them."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        backup = {name: (self.root / name).read_bytes() for name in ("entities.jsonl", "facts.jsonl")}
        self.run_kg("forget", "person/dana")
        for name, data in backup.items():
            (self.root / name).write_bytes(data)
        said = self.write_saying()
        self.assertIn("deleted on request", said)
        self.assertNotIn("dana", said.lower())
        self.assertNotIn("adopted", said)
        for name in ("journal/local.jsonl", "facts.jsonl", "entities.jsonl"):
            self.assertNotIn("lisbon", self.text(name), name)
        self.assertNotIn("lisbon", self.history())
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()

    def test_no_person_is_adopted_while_the_key_of_the_forgotten_is_missing(self) -> None:
        """Keyed forgotten records and no secret.key: the forgotten check cannot run, so adopt takes
        in no person (as adding one is refused) and says why; the rest is adopted."""
        self.seed()
        self.run_kg("propose", "person/dana", "timezone", "--value", "Europe/Lisbon", "--source", "ledger:a")
        backup = {name: (self.root / name).read_bytes() for name in ("entities.jsonl", "facts.jsonl")}
        self.run_kg("forget", "person/dana")
        for name, data in backup.items():
            (self.root / name).write_bytes(data)
        (self.root / "secret.key").unlink()
        said = self.write_saying()
        self.assertIn("no person is taken in", said)
        self.assertIn("secret.key", said)
        self.assertNotIn("dana", said.lower())
        self.assertNotIn("adopted", said)
        for name in ("journal/local.jsonl", "facts.jsonl", "entities.jsonl"):
            self.assertNotIn("lisbon", self.text(name), name)
        self.assertNotIn("lisbon", self.history())
        self.assertFalse((self.root / "secret.key").exists())  # never made anew over keyed records
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))

    def test_a_person_added_again_on_request_is_kept(self) -> None:
        """A deliberate re-add (--allow-forgotten) is in the journal, so an edit made outside it
        afterwards leaves them alone."""
        self.seed()
        self.run_kg("forget", "person/dana")
        self.run_kg("add-entity", "person/dana", "--name", "Dana Example", "--allow-forgotten")
        path = self.root / "entities.jsonl"
        edited = [
            {**e, "summary": "exports"} if e["id"] == "repo/invoice-exporter" else e
            for e in store.read_jsonl(path)
        ]
        path.write_text(store.jsonl(edited))
        said = self.write_saying()
        self.assertNotIn("deleted on request", said)
        self.assertIn("adopted 1 change", said)
        self.assertIn("person/dana", self.file_entities())
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assert_the_journal_rebuilds_the_files()


class JournalReadsTest(JournalTestCase):
    """A write reads the journal once and passes that read on; it rebuilds only what it must: an
    ordinary write nothing, one after a hand edit the journal before and after it adopts."""

    def counted(self, *argv: str) -> tuple[int, int]:
        """How many times <argv> reads the journal whole, and rebuilds from it."""
        reads, rebuilds = [], []
        read, rebuild = jr.Journal.changes, kc.rebuild

        def reading(journal: jr.Journal, at: Any = None) -> list[dict[str, Any]]:
            reads.append(at)
            return read(journal, at)

        def rebuilding(changes: Any) -> kc.RebuiltKB:
            rebuilds.append(1)
            return rebuild(changes)

        with (
            mock.patch.object(jr.Journal, "changes", reading),
            mock.patch.object(kc, "rebuild", rebuilding),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.run_kg(*argv)
        return len(reads), len(rebuilds)

    def test_an_ordinary_write_reads_the_journal_once_and_rebuilds_nothing(self) -> None:
        self.seed()
        self.assertEqual(self.counted("add-entity", "repo/other"), (1, 0))
        self.assertEqual(self.counted("forget", "person/dana"), (1, 0))

    def test_a_write_after_a_hand_edit_reads_it_once(self) -> None:
        self.seed()
        path = self.root / "entities.jsonl"
        path.write_text(path.read_text().replace('"Merge to main"', '"Merge to trunk"'))
        self.assertEqual(self.counted("add-entity", "repo/other"), (1, 2))
        self.assertEqual(self.file_entities()["step/merge"]["name"], "Merge to trunk")
        self.assert_the_journal_rebuilds_the_files()

    def test_a_write_that_finishes_a_cut_forget_reads_it_once(self) -> None:
        self.seed()
        with (
            mock.patch.object(knowledge, "write_files", side_effect=OSError("power cut")),
            self.assertRaises(OSError),
        ):
            self.run_kg("forget", "person/dana")
        self.assertEqual(self.counted("add-entity", "repo/other")[0], 1)
        self.assertNotIn("person/dana", self.file_entities())
        self.assert_the_journal_rebuilds_the_files()


class KeyTest(JournalTestCase):
    """secret.key, the key of the forgotten hashes: made whole like replica.json; one that is not a
    key is replaced when no keyed record needs it (kept aside, out of the history), and refused,
    naming it, when one does."""

    git = True

    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        super().setUp()

    def key(self) -> Path:
        return self.root / "secret.key"

    def test_the_key_reaches_the_disk_whole_before_it_is_used(self) -> None:
        """Written to a file of its own and flushed, then linked to its name and the folder
        flushed: a power cut never leaves a key file that is not a key."""
        flushed: list[bool] = []
        real = jr.flush_to_disk

        def flush(fd: int) -> None:
            flushed.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            real(fd)

        with mock.patch.object(jr, "flush_to_disk", side_effect=flush):
            store.create_secret(self.tmp / "secret.key")
        self.assertEqual(flushed, [False, True])
        self.assertEqual(len(store.read_secret(self.tmp / "secret.key")), store.KEY_BYTES)
        self.assertEqual((self.tmp / "secret.key").stat().st_mode & 0o777, store.PRIVATE_MODE)
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["secret.key"])  # no temp file left
        with self.assertRaises(FileExistsError):
            store.create_secret(self.tmp / "secret.key")  # never over a file that is there

    def bad_keys(self) -> list[tuple[str, bytes, int]]:
        return [("empty", b"", 0o600), ("the wrong length", b"abcd\n", 0o600), ("unreadable", b"0" * 64, 0)]

    def test_a_bad_key_with_no_keyed_record_is_kept_aside_and_made_again(self) -> None:
        for case, data, mode in self.bad_keys():
            with self.subTest(case):
                self.setUp()
                self.seed()
                self.key().write_bytes(data)
                self.key().chmod(mode)
                said = io.StringIO()
                with contextlib.redirect_stderr(said):
                    self.run_kg("forget", "person/dana")
                [aside] = self.root.glob("secret.key.aside-*")
                aside.chmod(0o600)
                self.assertEqual(aside.read_bytes(), data)  # kept
                self.assertEqual(len(store.read_secret(self.key())), store.KEY_BYTES)
                self.assertIn(f"moved it aside to {aside.name}", said.getvalue())
                self.assertNotIn("secret.key", store.git(self.root, "ls-files").stdout)
                self.assertEqual(store.git(self.root, "status", "--porcelain").stdout, "")  # ignored
                with self.assertRaisesRegex(KnowledgeError, "deleted on request"):
                    self.run_kg("add-entity", "person/dana")  # the new key recognises them

    def test_a_bad_key_with_keyed_records_is_refused_and_left_as_it_is(self) -> None:
        self.seed()
        self.run_kg("add-entity", "person/lee", "--name", "Lee Example")
        self.run_kg("forget", "person/dana")
        for case, data, mode in self.bad_keys():
            with self.subTest(case):
                self.key().chmod(0o600)
                self.key().write_bytes(data)
                self.key().chmod(mode)
                for argv in (("forget", "person/lee"), ("add-entity", "person/sam")):
                    with self.assertRaisesRegex(KnowledgeError, r"secret\.key, if it is there"):
                        self.run_kg(*argv)
                self.assertEqual(list(self.root.glob("secret.key.aside-*")), [])
        self.key().chmod(0o600)

    def test_the_way_out_moves_the_bad_key_aside_too(self) -> None:
        """The review's probe: keyed records and a damaged key; with forgotten.jsonl, the journal
        and replica.json moved aside, a person can be added again, and the next write makes a new
        key, keeping the bad one aside."""
        self.seed()
        self.run_kg("forget", "person/dana")
        self.key().write_text("")
        for path in (self.root / "forgotten.jsonl", self.root / "journal", self.state / "replica.json"):
            path.rename(self.tmp / f"{path.name}.aside")
        with contextlib.redirect_stderr(io.StringIO()):
            self.run_kg("add-entity", "person/dana", "--name", "Dana Example")
        self.assertIn("person/dana", self.file_entities())
        self.assertEqual(len(store.read_secret(self.key())), store.KEY_BYTES)
        self.assertEqual(len(list(self.root.glob("secret.key.aside-*"))), 1)


class TornFileTest(JournalTestCase):
    """A knowledge file that does not parse (a power cut or a full disk during its append) is one
    the journal rebuilds while the journal is whole: a reader uses the rebuild in memory and says
    so once, validate says the next write rebuilds it, and the next write does. Never a hard stop."""

    def proposed(self) -> str:
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        return self.fact_id("person/dana", "reviews")

    def cut_short(self, name: str, size: int) -> None:
        """<name> cut <size> bytes short, view.json as the write left it (the review's probe)."""
        path = self.root / name
        path.write_bytes(path.read_bytes()[:-size])

    def said(self, *argv: str) -> tuple[str, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = self.run_kg(*argv)
        return out, err.getvalue()

    def test_a_torn_fact_log_is_read_past_validated_and_rebuilt_by_the_next_write(self) -> None:
        fact_id = self.proposed()
        self.cut_short("facts.jsonl", 60)
        files = self.saved_files()
        out, err = self.said("facts", "person/dana", "--status", "all")
        self.assertIn(fact_id, out)  # the journal's rebuild, in memory
        self.assertEqual(err.count("\n"), 1, err)
        self.assertRegex(err, r"facts\.jsonl:\d+ is not valid JSON")
        self.assertEqual(self.saved_files(), files)  # a reader never writes
        output, code = self.validate("--json")
        found = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(
            [(f["kind"], f["severity"], f["item"]) for f in found], [("unreadable", "warning", "facts.jsonl")]
        )
        self.assertIn("the next write", found[0]["detail"])
        out, err = self.said("add-entity", "repo/other")
        self.assertRegex(err, r"facts\.jsonl:\d+ is not valid JSON.*saved again as the journal's rebuild")
        self.assert_the_journal_rebuilds_the_files()
        self.assertIn(fact_id, self.kb().facts)
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))

    def test_a_write_cut_during_its_append_is_finished_by_the_next(self) -> None:
        """The append to facts.jsonl cut short half way (a power cut): the journal holds the
        transaction, view.json does not, and the fact log ends in half a record."""
        self.seed()
        real = store.append_records

        def half(path: Path, records: list[dict[str, Any]], mode: int | None = None) -> None:
            if path.name != "facts.jsonl":
                return real(path, records, mode)
            with path.open("ab") as handle:
                handle.write(store.jsonl(records).encode()[:40])
            raise OSError("power cut")

        with mock.patch.object(store, "append_records", side_effect=half), self.assertRaises(OSError):
            self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        self.assertIs(store.append_records, real)
        self.assertIn("has_step", self.run_kg("facts", "release", "--status", "all"))
        self.assertEqual(self.kinds(), ["unreadable", "position"])
        self.run_kg("add-entity", "repo/other")
        self.assert_the_journal_rebuilds_the_files()
        self.assertEqual(len(self.records()), 1)  # the fact once
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))

    def test_a_torn_forgotten_jsonl_is_read_past_and_written_again_with_the_markers_records(self) -> None:
        self.proposed()
        self.run_kg("forget", "person/dana")
        recorded = store.read_jsonl(self.root / "forgotten.jsonl")
        self.cut_short("forgotten.jsonl", 20)
        out, err = self.said("stats")
        self.assertIn("entities", out)
        self.assertRegex(err, r"forgotten\.jsonl:1 is not valid JSON")
        output, code = self.validate("--json")
        self.assertEqual(
            ([(f["kind"], f["severity"], f["item"]) for f in json.loads(output)], code),
            ([("unreadable", "warning", "forgotten.jsonl")], 1),
        )
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaisesRegex(KnowledgeError, "deleted on request"),
        ):
            self.run_kg("add-entity", "person/dana", "--name", "Dana Example")
        self.assertEqual(store.read_jsonl(self.root / "forgotten.jsonl"), recorded)  # from the marker
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))

    def test_without_a_journal_a_file_that_does_not_parse_still_stops_every_command(self) -> None:
        """Nothing else holds what it held: the line is named, and nothing is written."""
        self.write_legacy([legacy_entity("repo/invoice-exporter", "invoice exporter")], [])
        with (self.root / "facts.jsonl").open("a") as handle:
            handle.write('{"op": "add", "at": "2026')
        files = self.saved_files()
        for argv in (("stats",), ("validate",), ("add-entity", "repo/other")):
            with (
                self.subTest(argv),
                self.assertRaisesRegex(store.StoreError, r"facts\.jsonl:1 is not valid JSON.*fix or remove"),
            ):
                self.run_kg(*argv)
        self.assertEqual({n: d for n, d in self.saved_files().items() if n != "knowledge.lock"}, files)

    def test_an_append_finishes_a_short_write_flushes_and_never_joins_a_line_cut_short(self) -> None:
        path = self.tmp / "facts.jsonl"
        path.write_text('{"a": 1}')  # whole, but its newline never came
        flushed: list[int] = []
        record = {"text": "a line longer than seven bytes"}
        with (
            mock.patch.object(os, "write", short_writes(7)),
            mock.patch.object(store, "flush_to_disk", side_effect=flushed.append),
        ):
            store.append_records(path, [record])
        self.assertEqual(store.read_jsonl(path), [{"a": 1}, record])
        self.assertEqual(len(flushed), 1)


class ValidateTest(JournalTestCase):
    """`kg validate`: the files as saved against the journal's rebuild. Exit 1 on anything that is
    not a note; --json carries each finding's severity and its line, as the ledger's does."""

    def test_a_clean_knowledge_base_is_valid(self) -> None:
        self.seed()
        self.run_kg("propose", "person/dana", "reviews", "exporter", "--source", "ledger:a")
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assertEqual(self.validate("--json"), ("[]", 0))

    def test_no_knowledge_base_is_valid_and_validate_makes_none(self) -> None:
        self.assertEqual(self.validate(), ("knowledge base is valid", 0))
        self.assertFalse(self.root.exists())

    def test_a_knowledge_base_from_before_the_journal_is_a_note(self) -> None:
        self.write_legacy([legacy_entity("repo/invoice-exporter", "invoice exporter")], [])
        self.assertEqual(self.validate(), ("no journal yet; the next write starts it", 0))
        output, code = self.validate("--json")
        self.assertEqual(
            ([(f["kind"], f["severity"]) for f in json.loads(output)], code), ([("note", "note")], 0)
        )

    def test_a_damaged_journal_is_an_error(self) -> None:
        self.seed()
        path = self.root / "journal" / f"{self.replica()}.jsonl"
        path.write_bytes(path.read_bytes() + b"nonsense\n")
        output, code = self.validate("--json")
        self.assertEqual(code, 1)
        self.assertEqual([(f["kind"], f["severity"]) for f in json.loads(output)], [("journal", "error")])
        self.assertIn("damaged", self.validate()[0])

    def test_a_journal_behind_view_json_is_an_error(self) -> None:
        self.seed()
        view = self.view()
        view["journal"][self.replica()] += 5
        (self.root / "view.json").write_text(json.dumps(view))
        output, code = self.validate("--json")
        found = json.loads(output)
        self.assertEqual(([(f["kind"], f["severity"]) for f in found], code), ([("position", "error")], 1))
        self.assertIn("behind view.json", found[0]["detail"])

    def test_a_rebuild_that_differs_from_the_files_is_an_error_naming_the_entity(self) -> None:
        """A change edited in place in the journal (the files and their digest untouched)."""
        self.seed()
        path = self.root / "journal" / f"{self.replica()}.jsonl"
        path.write_text(path.read_text().replace('"name":"Merge to main"', '"name":"Merge to trunk"'))
        output, code = self.validate("--json")
        found = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(
            [(f["kind"], f["severity"], f["item"]) for f in found], [("differs", "error", "step/merge")]
        )
        self.assertEqual(found[0]["path"], "name")

    def test_json_carries_each_findings_line_as_the_text_says_it(self) -> None:
        self.seed()
        self.run_kg("propose", "release", "has_step", "step/merge", "--source", "ledger:a")
        fact_id = self.fact_id("process/release", "has_step")
        self.run_kg("confirm", fact_id)
        self.commit_by_hand(kc.record_edit(decided(fact_id, "rejected", "rejected"), self.kb().facts))
        text, _ = self.validate()
        found = json.loads(self.validate("--json")[0])
        self.assertEqual([f["line"] for f in found], text.splitlines())
        self.assertEqual([f["kind"] for f in found], ["conflict", "position"])


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
