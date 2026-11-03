"""Unit tests for the journal (lib/journal.py): replica id, files and sequence numbers,
transactions, order, torn lines and their repair. Fictional data."""

from __future__ import annotations

import contextlib
import io
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

    def test_a_line_that_is_not_a_change_is_reported(self) -> None:
        self.root.mkdir(parents=True)
        (self.root / f"{R1}.jsonl").write_bytes(b'{"a":1}\n')
        for read in (self.journal().position, self.journal().changes):
            with (
                self.subTest(read=read.__name__),
                self.assertRaisesRegex(jr.JournalError, rf"{R1}\.jsonl, line 1: not a change"),
            ):
                read()


# What journal() stamps a file it moves aside with: 1_700_000_000_000 ms, in UTC.
STAMP = "20231114T221320Z"
TORN = b'{"v":1,"seq'


class Repair(JournalTestCase):
    def committed(self, count: int) -> Path:
        """<count> transactions of one change each; R1's file."""
        for _ in range(count):
            tx = self.journal().begin("add")
            tx.add("set", "item:1", {})
            tx.commit()
        return self.root / f"{R1}.jsonl"

    def repaired(self) -> tuple[list[Path], str]:
        """What repair_tail moved aside, and what it said on stderr."""
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            moved = self.journal().repair_tail()
        return moved, said.getvalue()

    def test_a_torn_last_line_is_moved_aside_and_the_file_cut_to_its_last_whole_line(self) -> None:
        path = self.committed(2)
        whole = path.read_bytes()
        with path.open("ab") as f:
            f.write(TORN)
        moved, said = self.repaired()
        self.assertEqual(moved, [self.root / f"{R1}.jsonl.torn-{STAMP}"])
        self.assertEqual((path.read_bytes(), moved[0].read_bytes()), (whole, TORN))
        self.assertIn(moved[0].name, said)
        self.assertEqual(self.journal().position(), {R1: 2})
        self.assertEqual(len(self.committed(1).read_bytes().splitlines()), 3)  # seq 3 follows

    def test_a_whole_line_without_its_newline_is_torn_too(self) -> None:
        path = self.committed(2)
        path.write_bytes(path.read_bytes()[:-1])
        moved, _ = self.repaired()
        self.assertEqual(len(moved), 1)
        self.assertEqual(json.loads(moved[0].read_bytes())["seq"], 2)
        self.assertEqual(self.journal().position(), {R1: 1})

    def test_a_file_that_is_all_torn_is_left_empty(self) -> None:
        self.root.mkdir(parents=True)
        path = self.root / "local.jsonl"
        path.write_bytes(TORN)
        moved, _ = self.repaired()
        self.assertEqual((len(moved), path.read_bytes()), (1, b""))
        self.assertEqual(self.journal().position(), {})

    def test_a_second_cut_in_the_same_second_keeps_the_first(self) -> None:
        path = self.committed(1)
        for _ in range(2):
            with path.open("ab") as f:
                f.write(TORN)
            self.repaired()
        self.assertEqual(len(list(self.root.glob(f"{R1}.jsonl.torn-*"))), 2)

    def test_a_whole_journal_is_left_as_it_is(self) -> None:
        self.assertEqual(self.repaired(), ([], ""))  # no folder yet
        path = self.committed(2)
        whole = path.read_bytes()
        self.assertEqual(self.repaired(), ([], ""))
        self.assertEqual(path.read_bytes(), whole)

    def test_damage_other_than_a_torn_tail_still_raises(self) -> None:
        path = self.committed(2)
        first, second = path.read_bytes().splitlines(keepends=True)
        for text, number in ((first + second + b"nonsense\n", 3), (first + b"nonsense\n" + second, 2)):
            path.write_bytes(text)
            with self.subTest(line=number):
                self.assertEqual(self.repaired(), ([], ""))
                with self.assertRaisesRegex(jr.JournalError, rf"{R1}\.jsonl, line {number}"):
                    self.journal().changes()


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


class OneUse(JournalTestCase):
    def test_a_committed_transaction_is_not_used_again(self) -> None:
        for edits in (1, 0):
            tx = self.journal().begin("add")
            for _ in range(edits):
                tx.add("set", "item:1", {})
            tx.commit()
            with self.subTest(edits=edits):
                with self.assertRaisesRegex(jr.JournalError, "committed"):
                    tx.add("set", "item:1", {})
                with self.assertRaisesRegex(jr.JournalError, "committed"):
                    tx.commit()
                with self.assertRaisesRegex(jr.JournalError, "committed"):
                    tx.mint()


if __name__ == "__main__":
    unittest.main()
