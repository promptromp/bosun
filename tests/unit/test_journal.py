"""Unit tests for the journal (lib/journal.py): replica id, files and sequence numbers,
transactions, order and torn lines. Fictional data."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import change_log as cl
import journal as jr


R1 = "a" * 32
R2 = "b" * 32


def fixed(ms: int) -> Callable[[], int]:
    return lambda: ms


class JournalTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "journal"

    def journal(self, replica: str = R1, ms: int = 1_700_000_000_000) -> jr.Journal:
        return jr.Journal(self.root, "ledger", replica, 3, fixed(ms))


class ReplicaId(JournalTestCase):
    def test_replica_id_is_made_once(self) -> None:
        state = Path(self.tmp.name) / "state"
        first = jr.replica_id(state)
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertEqual(jr.replica_id(state), first)
        self.assertEqual(json.loads((state / "replica.json").read_text()), {"replica": first})
        self.assertEqual((state / "replica.json").stat().st_mode & 0o777, 0o600)


class Commit(JournalTestCase):
    def test_commit_numbers_each_file_from_one(self) -> None:
        tx = self.journal().begin("add")
        tx.add("create", "item:1", {"a": 1})
        tx.add("set", "item:1", {"b": 2})
        tx.add("note", "item:1", {"c": 3}, local=True)
        tx.commit()
        self.assertEqual(self.journal().position(), {R1: 2, "local": 1})
        lines = [json.loads(x) for x in (self.root / f"{R1}.jsonl").read_text().splitlines()]
        self.assertEqual([x["seq"] for x in lines], [1, 2])
        self.assertNotIn("local", lines[0])
        local = json.loads((self.root / "local.jsonl").read_text())
        self.assertEqual((local["seq"], local["local"]), (1, True))

    def test_an_empty_transaction_writes_nothing(self) -> None:
        self.assertEqual(self.journal().begin("noop").commit(), [])
        self.assertFalse(self.root.exists())
        self.assertEqual(self.journal().position(), {})

    def test_every_change_carries_the_envelope_and_a_valid_id(self) -> None:
        tx = self.journal().begin("add")
        tx.add("create", "item:1", {"a": 1})
        tx.add("set", "item:1", {"b": "x"})
        done = tx.commit()
        self.assertEqual(len(done), 2)
        for change in self.journal().changes():
            self.assertEqual(change["id"], cl.change_id(change))
            self.assertEqual(change["v"], cl.ENVELOPE_VERSION)
            self.assertEqual((change["stream"], change["rules"], change["cmd"]), ("ledger", 3, "add"))
            self.assertEqual(change["tx"], done[0]["hlc"])

    def test_a_transaction_comes_back_in_add_order(self) -> None:
        tx = self.journal().begin("add")
        for n in range(5):
            tx.add("set", f"item:{n}", {"n": n}, local=n == 2)
        tx.commit()
        self.assertEqual([c["body"]["n"] for c in self.journal().changes()], [0, 1, 2, 3, 4])

    def test_the_clock_moves_on_across_transactions(self) -> None:
        j = self.journal()
        for _ in range(2):
            tx = j.begin("add")
            tx.add("set", "item:1", {})
            tx.commit()
        a, b = j.changes()
        self.assertLess(a["hlc"], b["hlc"])
        self.assertEqual(b["seq"], 2)


class Reading(JournalTestCase):
    def test_changes_come_back_in_order_across_files(self) -> None:
        for replica, ms in ((R2, 1_700_000_000_000), (R1, 1_700_000_005_000), (R2, 1_700_000_009_000)):
            tx = self.journal(replica, ms).begin("add")
            tx.add("set", "item:1", {"who": replica[0]})
            tx.commit()
        got = self.journal().changes()
        self.assertEqual([c["body"]["who"] for c in got], ["b", "a", "b"])
        self.assertEqual(got, cl.in_order(got))

    def test_a_torn_last_line_is_reported(self) -> None:
        tx = self.journal().begin("add")
        tx.add("set", "item:1", {})
        tx.commit()
        path = self.root / f"{R1}.jsonl"
        with path.open("ab") as f:
            f.write(b'{"v":1,"seq')
        with self.assertRaises(jr.JournalError) as ctx:
            self.journal().changes()
        self.assertIn(path.name, str(ctx.exception))
        self.assertIn("2", str(ctx.exception))
        with self.assertRaises(jr.JournalError):
            self.journal().position()


class Mint(JournalTestCase):
    def test_mint_is_unique_and_repeatable(self) -> None:
        first_tx = self.journal().begin("add")
        first, second = first_tx.mint(), first_tx.mint()
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 6)
        copy = jr.Journal(Path(self.tmp.name) / "copy", "ledger", R1, 3, fixed(1))
        self.assertEqual(copy.begin("add").mint(), first)

    def test_mint_moves_on_after_a_commit(self) -> None:
        j = self.journal()
        before = j.begin("add").mint()
        tx = j.begin("add")
        tx.add("set", "item:1", {})
        tx.commit()
        self.assertNotEqual(j.begin("add").mint(), before)


if __name__ == "__main__":
    unittest.main()
