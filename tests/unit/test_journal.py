"""Unit tests for the journal (lib/journal.py): replica id, files and sequence numbers,
transactions, order, torn lines and their repair. Fictional data."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import change_log as cl
import journal as jr
import ledger


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

    def repaired(self) -> tuple[list[Path], str]:
        """What repair_tail moved aside, and what it said on stderr."""
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            moved = self.journal().repair_tail()
        return moved, said.getvalue()


class ReplicaId(JournalTestCase):
    def test_replica_id_is_made_once(self) -> None:
        state = Path(self.tmp.name) / "state"
        first = jr.replica_id(state)
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertEqual(jr.replica_id(state), first)
        self.assertEqual(json.loads((state / "replica.json").read_text()), {"replica": first})
        self.assertEqual((state / "replica.json").stat().st_mode & 0o777, 0o600)

    def test_a_replica_file_that_holds_no_replica_id_is_refused_not_replaced(self) -> None:
        state = Path(self.tmp.name) / "state"
        state.mkdir()
        path = state / "replica.json"
        for text in (
            "{not json",
            '{"replica": "local"}',
            '{"replica": "../x"}',
            json.dumps({"replica": "A" * 32}),
        ):
            path.write_text(text)
            with (
                self.subTest(text=text),
                self.assertRaisesRegex(jr.JournalError, rf"{path}.*32 hex characters"),
            ):
                jr.replica_id(state)
            self.assertEqual(path.read_text(), text)  # kept as it was: never a silent new id

    def test_a_new_replica_id_reaches_the_disk_before_it_is_used(self) -> None:
        """The file's data, then the folder that now names it."""
        state = Path(self.tmp.name) / "state"
        synced: list[bool] = []
        with mock.patch.object(
            jr, "flush_to_disk", side_effect=lambda fd: synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        ):
            made = jr.replica_id(state)
        self.assertEqual(synced, [False, True])
        self.assertEqual(jr.replica_id(state), made)
        self.assertEqual(list(state.glob(".replica.json*")), [])  # no temp file left

    def test_a_creator_that_comes_second_takes_the_first_ones_id(self) -> None:
        state = Path(self.tmp.name) / "state"
        winner = "c" * 32
        link = os.link

        def first_there(source: Path, target: Path) -> None:
            Path(target).write_text(json.dumps({"replica": winner}) + "\n")  # the other store, just before
            link(source, target)

        with mock.patch("os.link", side_effect=first_there):
            self.assertEqual(jr.replica_id(state), winner)
        self.assertEqual(json.loads((state / "replica.json").read_text()), {"replica": winner})
        self.assertEqual(list(state.glob(".replica.json*")), [])

    def test_a_replica_id_that_cannot_be_written_leaves_no_temp_file(self) -> None:
        def full(fd: int, data: bytes) -> None:
            raise OSError(errno.ENOSPC, "no space left on the device")

        for name, failing in (
            ("write", mock.patch.object(jr, "write_all", side_effect=full)),
            ("flush", mock.patch.object(jr, "flush_to_disk", side_effect=OSError(errno.EIO, "io"))),
        ):
            state = Path(self.tmp.name) / f"state-{name}"
            with self.subTest(name), failing, self.assertRaises(OSError):
                jr.replica_id(state)
            self.assertEqual(sorted(p.name for p in state.iterdir()), [], name)

    def test_a_state_folder_without_hard_links_is_named(self) -> None:
        state = Path(self.tmp.name) / "state"
        with (
            mock.patch("os.link", side_effect=OSError(errno.ENOTSUP, "Operation not supported")),
            self.assertRaisesRegex(jr.JournalError, rf"{state / 'replica.json'}.*Operation not supported"),
        ):
            jr.replica_id(state)
        self.assertEqual(sorted(p.name for p in state.iterdir()), [])

    def test_creators_racing_agree_on_one_id(self) -> None:
        for attempt in range(10):
            state = Path(self.tmp.name) / f"state-{attempt}"
            start = threading.Barrier(12)
            found: list[str] = []

            def create(
                state: Path = state, start: threading.Barrier = start, found: list[str] = found
            ) -> None:
                start.wait()
                found.append(jr.replica_id(state))

            threads = [threading.Thread(target=create) for _ in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            kept = json.loads((state / "replica.json").read_text())["replica"]
            self.assertEqual(set(found), {kept}, attempt)
            self.assertEqual(list(state.glob(".replica.json*")), [])


def short_writes(limit: int) -> Callable[[int, bytes], int]:
    """os.write as a nearly full disk does it: at most <limit> bytes a call (0: nothing)."""
    write = os.write

    def writing(fd: int, data: bytes) -> int:
        return write(fd, bytes(data[:limit])) if limit else 0

    return writing


class Disk(JournalTestCase):
    """Every byte reaches the file, or the write fails; and on macOS F_FULLFSYNC, so that a power
    cut cannot keep ledger.json's rename and lose the journal's append before it."""

    def test_a_short_write_is_finished_by_further_calls(self) -> None:
        with mock.patch.object(os, "write", short_writes(7)):
            tx = self.journal().begin("add")
            tx.add("set", "item:1", {"text": "a line longer than seven bytes"})
            tx.add("set", "item:1", {"quote": "mine"}, local=True)
            tx.commit()
        self.assertEqual(self.journal().position(), {R1: 1, "local": 1})
        bodies = [c["body"] for c in self.journal().changes()]
        self.assertEqual(bodies, [{"text": "a line longer than seven bytes"}, {"quote": "mine"}])

    def test_a_write_that_makes_no_progress_fails_the_commit(self) -> None:
        tx = self.journal().begin("add")
        tx.add("set", "item:1", {"a": 1})
        with mock.patch.object(os, "write", short_writes(0)), self.assertRaises(OSError):
            tx.commit()
        self.assertEqual(self.journal().position(), {})

    def test_a_set_aside_writes_all_it_moves(self) -> None:
        tx = self.journal().begin("add")
        tx.add("set", "item:1", {})
        tx.commit()
        path = self.root / f"{R1}.jsonl"
        with path.open("ab") as handle:
            handle.write(TORN * 3)
        with mock.patch.object(os, "write", short_writes(4)):
            (aside,), _ = self.repaired()
        self.assertEqual(aside.read_bytes(), TORN * 3)

    def test_a_commit_flushes_each_file_to_the_disk_itself(self) -> None:
        full = jr.FULL_FSYNC
        if full is None:
            self.skipTest("macOS only")
        calls: list[int] = []
        real = fcntl.fcntl

        def recording(fd: int, cmd: int, arg: int = 0) -> int:
            calls.append(cmd)
            return real(fd, cmd, arg)

        tx = self.journal().begin("add")
        tx.add("set", "item:1", {})
        tx.add("set", "item:1", {"quote": "mine"}, local=True)
        with mock.patch.object(fcntl, "fcntl", recording):
            tx.commit()
        self.assertEqual(calls.count(full), 2)  # local.jsonl, then the replica's file

    def test_without_f_fullfsync_it_is_fsync(self) -> None:
        synced: list[int] = []
        with (
            mock.patch.object(fcntl, "fcntl", side_effect=OSError("not supported here")),
            mock.patch.object(os, "fsync", side_effect=synced.append),
        ):
            jr.flush_to_disk(7)
        self.assertEqual(synced, [7])

    def test_an_atomic_replace_flushes_the_folder_after_the_rename(self) -> None:
        """A rename is an entry in its folder: flushed only with the folder, so that a power cut
        cannot keep a later write and lose the rename (a forget's rewrite of local.jsonl)."""
        self.root.mkdir()
        path, tmp = self.root / "local.jsonl", self.root / ".local.jsonl.1.tmp"
        path.write_text("old\n")
        tmp.write_text("new\n")
        flushed: list[tuple[bool, int, str]] = []

        def flush(fd: int) -> None:
            found = os.fstat(fd)
            flushed.append((stat.S_ISDIR(found.st_mode), found.st_ino, path.read_text()))

        with mock.patch.object(jr, "flush_to_disk", side_effect=flush):
            jr.replace_durably(tmp, path)
        self.assertEqual(flushed, [(True, self.root.stat().st_ino, "new\n")])  # the folder, after the rename
        self.assertFalse(tmp.exists())

    def test_both_stores_replace_through_it(self) -> None:
        """journal.py's rewrite (a forget) and the ledger's files."""
        tx = self.journal().begin("add")
        tx.add("put", "person/dana", {"name": "Dana"}, local=True)
        tx.commit()
        other = Path(self.tmp.name) / "files"
        other.mkdir()
        flushed: list[Path] = []
        with mock.patch.object(jr, "flush_folder", side_effect=flushed.append):
            self.journal().forget({"person/dana"})
            ledger.write_atomically(other / "ledger.json", "{}")
        self.assertEqual(flushed, [self.root, other])


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
        self.assertEqual([x["of"] for x in lines], [2, 2])  # the transaction's lines in this file
        self.assertNotIn("local", lines[0])
        self.assertNotIn("private_of", lines[0])
        local = json.loads((self.root / "local.jsonl").read_text())
        self.assertEqual((local["seq"], local["local"]), (1, True))
        self.assertEqual((local["of"], local["private_of"]), (1, 2))

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

    def test_a_line_cut_short_is_left_out(self) -> None:
        tx = self.journal().begin("add")
        tx.add("set", "item:1", {})
        tx.commit()
        path = self.root / f"{R1}.jsonl"
        with path.open("ab") as f:
            f.write(b'{"v":1,"seq')
        self.assertEqual([c["seq"] for c in self.journal().changes()], [1])
        self.assertEqual(self.journal().position(), {R1: 1})
        self.assertEqual(self.journal().written(), {R1: 1})

    def test_a_line_that_is_not_a_change_is_reported(self) -> None:
        self.root.mkdir(parents=True)
        (self.root / f"{R1}.jsonl").write_bytes(b'{"a":1}\n')
        for read in (self.journal().position, self.journal().changes):
            with (
                self.subTest(read=read.__name__),
                self.assertRaisesRegex(jr.JournalError, rf"{R1}\.jsonl, line 1: not a change"),
            ):
                read()

    def test_a_gap_in_the_sequence_is_damage_naming_the_file_and_the_missing_seq(self) -> None:
        for _ in range(4):
            tx = self.journal().begin("add")
            tx.add("set", "item:1", {})
            tx.commit()
        path = self.root / f"{R1}.jsonl"
        lines = path.read_bytes().splitlines(keepends=True)
        for kept, said in (
            (lines[:1] + lines[2:], rf"{R1}\.jsonl, line 2: seq 3 where seq 2 was due: seq 2 is missing"),
            (
                lines[:1] + lines[3:],
                rf"{R1}\.jsonl, line 2: seq 4 where seq 2 was due: seqs 2 to 3 are missing",
            ),
            (lines[1:], rf"{R1}\.jsonl, line 1: seq 2 where seq 1 was due: seq 1 is missing"),
            (lines[:2] + lines[1:], rf"{R1}\.jsonl, line 3: seq 2 where seq 3 was due: seq 2 again"),
        ):
            path.write_bytes(b"".join(kept))
            with self.subTest(said=said), self.assertRaisesRegex(jr.JournalError, said):
                self.journal().changes()

    def test_changes_at_a_position_leave_out_what_came_after(self) -> None:
        for n in range(3):
            tx = self.journal().begin("add")
            tx.add("set", "item:1", {"n": n})
            tx.add("set", "item:1", {"quote": n}, local=True)
            tx.commit()
        found = self.journal().changes({R1: 2, "local": 2})
        self.assertEqual([c["body"] for c in found], [{"n": 0}, {"quote": 0}, {"n": 1}, {"quote": 1}])
        self.assertEqual(self.journal().changes({}), [])
        self.assertEqual(len(self.journal().changes()), 6)


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

    def test_damage_other_than_an_unfinished_transaction_still_raises(self) -> None:
        path = self.committed(2)
        first, second = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(first + second + b"nonsense\n")  # a whole last line: read by every write
        with self.assertRaisesRegex(jr.JournalError, rf"{R1}\.jsonl, line 3"):
            self.journal().repair_tail()
        path.write_bytes(first + b"nonsense\n" + second)  # a line in the middle: read by changes()
        self.assertEqual(self.repaired(), ([], ""))
        with self.assertRaisesRegex(jr.JournalError, rf"{R1}\.jsonl, line 2"):
            self.journal().changes()


def lines_of(path: Path) -> list[bytes]:
    return path.read_bytes().splitlines(keepends=True)


class Whole(JournalTestCase):
    """A transaction counts once all its lines are there (`of` per file, `private_of` from
    local.jsonl into the replica's file); an unfinished one at the end of a file is left out by
    readers and set aside, kept, by repair_tail."""

    def commit(self, private: int, local: int = 0) -> None:
        tx = self.journal().begin("add")
        for n in range(private):
            tx.add("set", f"item:{n}", {"n": n})
        for n in range(local):
            tx.add("set", f"item:{n}", {"quote": n}, local=True)
        tx.commit()

    def test_a_transaction_missing_lines_is_left_out_then_set_aside(self) -> None:
        self.commit(2)
        self.commit(3)
        path = self.root / f"{R1}.jsonl"
        kept = lines_of(path)
        path.write_bytes(b"".join(kept[:4]))  # the second transaction lost its last line, whole
        self.assertEqual(self.journal().position(), {R1: 2})
        self.assertEqual(self.journal().written(), {R1: 4})
        self.assertEqual([c["seq"] for c in self.journal().changes()], [1, 2])
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            (aside,) = self.journal().repair_tail()
        self.assertEqual(aside.read_bytes(), b"".join(kept[2:4]))
        self.assertEqual(lines_of(path), kept[:2])
        self.assertIn("unfinished", said.getvalue())

    def test_local_lines_whose_private_lines_never_came_are_left_out(self) -> None:
        self.commit(1, 1)
        path = self.root / f"{R1}.jsonl"
        kept = lines_of(path)
        self.commit(2, 1)
        path.write_bytes(b"".join(kept))  # cut between the files: local written, private not
        self.assertEqual(self.journal().position(), {R1: 1, "local": 1})
        self.assertEqual(self.journal().written(), {R1: 1, "local": 2})
        with contextlib.redirect_stderr(io.StringIO()):
            (aside,) = self.journal().repair_tail()
        self.assertEqual(aside.name, f"local.jsonl.torn-{STAMP}")
        self.assertEqual(len(lines_of(self.root / "local.jsonl")), 1)

    def test_a_cut_mid_way_through_the_private_lines_leaves_out_both_files(self) -> None:
        self.commit(3, 2)
        path = self.root / f"{R1}.jsonl"
        path.write_bytes(path.read_bytes()[: len(path.read_bytes()) // 2])
        self.assertEqual(self.journal().position(), {})
        self.assertEqual(self.journal().changes(), [])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(len(self.journal().repair_tail()), 2)
        self.assertEqual((path.read_bytes(), (self.root / "local.jsonl").read_bytes()), (b"", b""))

    def test_a_later_transaction_vouches_for_an_earlier_local_one(self) -> None:
        self.commit(1, 1)
        self.commit(2)
        self.assertEqual(self.journal().position(), {R1: 3, "local": 1})
        self.assertEqual(self.repaired(), ([], ""))

    def test_a_commit_writes_local_lines_first(self) -> None:
        write = os.write

        def second_fails(fd: int, data: bytes) -> int:
            if (self.root / "local.jsonl").exists() and (self.root / "local.jsonl").stat().st_size:
                raise OSError("interrupted")
            return write(fd, data)

        with mock.patch.object(os, "write", second_fails), self.assertRaises(OSError):
            self.commit(2, 1)
        self.assertEqual(len(lines_of(self.root / "local.jsonl")), 1)
        self.assertEqual((self.root / f"{R1}.jsonl").read_bytes(), b"")
        self.assertEqual(self.journal().position(), {})  # left out until it is set aside
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(len(self.journal().repair_tail()), 1)
        self.commit(1)
        self.assertEqual(self.journal().position(), {R1: 1})


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


class Forget(JournalTestCase):
    def write(self, *targets: tuple[str, bool]) -> None:
        tx = self.journal().begin("add")
        for target, local in targets:
            tx.add("put", target, {"name": target}, local=local)
        tx.commit()

    def test_forgetting_leaves_a_placeholder_in_its_place_in_the_local_file(self) -> None:
        self.write(("person/dana", True), ("repo/x", False), ("person/lee", True))
        self.write(("person/dana", True))
        private = (self.root / f"{R1}.jsonl").read_bytes()
        position, mint = self.journal().position(), self.journal().begin("add").mint()
        self.assertEqual(self.journal().forget({"person/dana"}), 2)
        self.assertEqual((self.root / f"{R1}.jsonl").read_bytes(), private)
        local = [json.loads(line) for line in (self.root / "local.jsonl").read_text().splitlines()]
        self.assertEqual([c["op"] for c in local], ["forgotten", "put", "forgotten"])
        self.assertNotIn("dana", (self.root / "local.jsonl").read_text())
        self.assertEqual(self.journal().position(), position)  # the sequence and the mint seeds stay
        self.assertEqual(self.journal().begin("add").mint(), mint)
        self.assertEqual(
            [(c["op"], c.get("target")) for c in self.journal().changes()],
            [("forgotten", None), ("put", "repo/x"), ("put", "person/lee"), ("forgotten", None)],
        )  # each transaction still whole, in its place
        self.assertEqual(self.journal().forget({"person/dana"}), 0)

    def test_forgetting_what_is_not_there_writes_nothing(self) -> None:
        self.assertEqual(self.journal().forget({"person/dana"}), 0)
        self.assertFalse(self.root.exists())

    def test_forgetting_deletes_what_writes_cut_short_left_of_local_changes(self) -> None:
        self.write(("person/dana", True), ("repo/x", False))
        left = [
            self.root / "local.jsonl.torn-20261021T090000Z",
            self.root / "local.jsonl.torn-20261021T090000Z-2",
            self.root / ".local.jsonl.4242.tmp",
        ]
        kept = self.root / f"{R1}.jsonl.torn-20261021T090000Z"
        for path in [*left, kept]:
            path.write_text('{"body":{"name":"Dana Example"}')
        self.journal().forget({"nobody"})  # whatever it forgets: a torn tail cannot be read to choose
        self.assertEqual([p.name for p in left if p.exists()], [])
        self.assertTrue(kept.exists())


class CommitEdits(JournalTestCase):
    def test_edits_are_added_in_order_then_committed(self) -> None:
        edits = [
            cl.Edit("put", "repo/x", {"name": "x"}, False),
            cl.Edit("put", "person/dana", {"name": "Dana"}, True),
        ]
        tx = self.journal().begin("add")
        done = tx.commit_edits(edits)
        self.assertEqual([(c["op"], c["target"], c.get("local", False)) for c in done], [
            ("put", "repo/x", False),
            ("put", "person/dana", True),
        ])  # fmt: skip
        self.assertTrue(tx.committed)
        self.assertEqual(self.journal().position(), {R1: 1, "local": 1})


class AsSaved(JournalTestCase):
    def test_what_the_files_held_when_saved_and_what_was_adopted_since(self) -> None:
        """A store's files saved at a position, then a write cut short after adopting an outside
        edit: the changes up to that position and the adopted ones, in the one order."""
        journal = self.journal()
        journal.begin("one").commit_edits([cl.Edit("put", "a", {}, False)])
        saved = journal.position()
        journal.begin(cl.ADOPT).commit_edits([cl.Edit("put", "b", {}, False)])
        journal.begin("two").commit_edits([cl.Edit("put", "c", {}, False)])
        self.assertEqual([c["target"] for c in journal.as_saved(saved, cl.ADOPT)], ["a", "b"])
        self.assertEqual([c["target"] for c in journal.as_saved({}, cl.ADOPT)], ["b"])


if __name__ == "__main__":
    unittest.main()
