"""Unit tests for the change log primitives (lib/change_log.py): canonical bytes, change ids,
the hybrid logical clock, the one global order and id suffixes. Fictional data."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import change_log
from change_log import ChangeLogError


CROCKFORD = set("0123456789abcdefghjkmnpqrstvwxyz")


class TestCanonical(unittest.TestCase):
    def test_canonical_is_sorted_and_compact(self) -> None:
        self.assertEqual(change_log.canonical({"b": 1, "a": "é"}), '{"a":"é","b":1}'.encode())

    def test_canonical_refuses_floats(self) -> None:
        with self.assertRaises(ChangeLogError):
            change_log.canonical({"x": 1.5})
        with self.assertRaises(ChangeLogError):
            change_log.canonical({"x": {"y": [1, {"z": 2.5}]}})

    def test_canonical_refuses_text_that_is_not_valid_unicode(self) -> None:
        """A lone surrogate (what Python makes of invalid UTF-8 in argv) has no UTF-8 bytes."""
        for record in ({"x": "abc\udce9"}, {"x\ud800": 1}):
            with self.subTest(record=record), self.assertRaisesRegex(ChangeLogError, "not valid Unicode"):
                change_log.canonical(record)

    def test_floats_in_tuples_and_deep_lists_are_refused_too(self) -> None:
        for record in ({"x": (1, 2.5)}, {"x": [[[{"y": [0.5]}]]]}):
            with self.subTest(record=record), self.assertRaisesRegex(ChangeLogError, "float"):
                change_log.canonical(record)

    def test_unencodable_names_each_place_and_what_is_there(self) -> None:
        record = {"a": [1, 2.5, {"b\udcff": "ok", "c": "x\ud800"}], "d": "fine", "e": True}
        self.assertEqual(
            list(change_log.unencodable(record)),
            [
                (("a", "1"), "a float (2.5)", "2.5"),
                (("a", "2", "b\udcff"), "a key that is not valid Unicode ('b\\udcff')", "b\ufffd"),
                (("a", "2", "c"), "text that is not valid Unicode ('x\\ud800')", "x\ufffd"),
            ],
        )
        self.assertEqual(list(change_log.unencodable({"a": [1, "é", None, {"b": False}]})), [])

    def test_encodable_turns_a_float_into_its_text_and_a_lone_surrogate_into_u_fffd(self) -> None:
        record = {"a": [1, 2.5, {"b\udcff": "x\ud800y"}], "d": "fine"}
        made = change_log.encodable(record)
        self.assertEqual(made, {"a": [1, "2.5", {"b\ufffd": "x\ufffdy"}], "d": "fine"})
        self.assertEqual(record["a"][1], 2.5)  # a copy
        change_log.canonical(made)

    def test_change_id_ignores_its_own_id(self) -> None:
        a = change_log.change_id({"a": 1})
        self.assertEqual(a, change_log.change_id({"a": 1, "id": "x"}))
        self.assertEqual(len(a), 64)
        int(a, 16)


class TestClock(unittest.TestCase):
    def test_tick_advances_with_wall_time(self) -> None:
        self.assertEqual(change_log.tick("0000000001000.0003", 2000), "0000000002000.0000")

    def test_tick_survives_a_clock_going_back(self) -> None:
        self.assertEqual(change_log.tick("0000000005000.0001", 4000), "0000000005000.0002")

    def test_tick_overflow_moves_to_next_millisecond(self) -> None:
        self.assertEqual(change_log.tick("0000000005000.9999", 4000), "0000000005001.0000")

    def test_observe_moves_past_what_it_saw(self) -> None:
        self.assertEqual(
            change_log.observe("0000000001000.0000", "0000000003000.0004", 2000, 600_000),
            ("0000000003000.0005", False),
        )

    def test_observe_flags_a_far_future_clock(self) -> None:
        last = "0000000001000.0000"
        seen = change_log.clock_text(2000 + 11 * 60_000, 0)
        self.assertEqual(change_log.observe(last, seen, 2000, 600_000), (change_log.tick(last, 2000), True))

    def test_observe_when_wall_time_is_ahead(self) -> None:
        self.assertEqual(
            change_log.observe("0000000001000.0007", "0000000001500.0002", 9000, 600_000),
            ("0000000009000.0000", False),
        )

    def test_clock_text_round_trips(self) -> None:
        self.assertEqual(change_log.parse_clock(change_log.clock_text(42, 7)), (42, 7))

    def test_parse_clock_refuses_malformed_text(self) -> None:
        arabic_indic = "\u0661\u0667" + "\u0660" * 11  # digits to \\d without re.ASCII
        for bad in ("", "123", "0000000001000-0001", "abc.def", f"{arabic_indic}.0000"):
            with self.assertRaises(ChangeLogError):
                change_log.parse_clock(bad)


class TestOrder(unittest.TestCase):
    def test_in_order_sorts_and_deduplicates(self) -> None:
        a = {"id": "a1", "hlc": "0000000001000.0000", "replica": "r2"}
        b = {"id": "b1", "hlc": "0000000001000.0000", "replica": "r1"}
        c = {"id": "c1", "hlc": "0000000002000.0000", "replica": "r1"}
        self.assertEqual(change_log.in_order([c, a, b, dict(a)]), [b, a, c])
        self.assertEqual(change_log.order_key(a), ("0000000001000.0000", "r2", "a1"))


class TestSuffix(unittest.TestCase):
    def test_mint_suffix_is_stable_and_short(self) -> None:
        s = change_log.mint_suffix("r1", "seed")
        self.assertEqual(s, change_log.mint_suffix("r1", "seed", 0))
        self.assertEqual(len(s), 6)
        self.assertTrue(set(s) <= CROCKFORD)
        others = {
            change_log.mint_suffix("r2", "seed"),
            change_log.mint_suffix("r1", "other"),
            change_log.mint_suffix("r1", "seed", 1),
        }
        self.assertNotIn(s, others)
        self.assertEqual(len(others), 3)


CHANGE = {
    "v": 1,
    "stream": "kg",
    "replica": "a" * 32,
    "seq": 4,
    "hlc": "0000000001000.0002",
    "tx": "0000000001000.0000",
    "of": 2,
    "local": True,
    "private_of": 3,
    "cmd": "kg propose",
    "rules": 1,
    "op": "fact.add",
    "target": "f-abc123",
    "body": {"fact": {"subject": "person/dana"}},
}


class TestPlaceholder(unittest.TestCase):
    def test_a_placeholder_keeps_its_place_and_loses_its_content(self) -> None:
        change = {**CHANGE, "id": change_log.change_id(CHANGE)}
        made = change_log.placeholder(change)
        kept = {k: v for k, v in change.items() if k in change_log.PLACEHOLDER_KEYS}
        self.assertEqual(made, {**kept, "op": "forgotten", "id": change_log.change_id(made)})
        self.assertNotEqual(made["id"], change["id"])
        self.assertNotIn("dana", change_log.canonical(made).decode())


class TestStamp(unittest.TestCase):
    def test_a_stamp_is_the_position_and_the_digest(self) -> None:
        held = change_log.digest({"a": 1})
        self.assertRegex(held, "^[0-9a-f]{64}$")
        self.assertEqual(held, change_log.digest({"a": 1}))
        stamp = change_log.stamped({"local": 3, "a" * 32: 7}, held)
        self.assertEqual(stamp, {"local": 3, "a" * 32: 7, change_log.DIGEST: held})
        self.assertEqual(change_log.position_in(stamp), {"local": 3, "a" * 32: 7})
        self.assertEqual(change_log.position_in(None), {})

    def test_behind_names_each_file_the_journal_lost(self) -> None:
        self.assertEqual(
            change_log.behind({"local": 2}, {"local": 3, "r": 1}, "view.json"),
            [
                "local.jsonl ends at seq 2, view.json reflects seq 3",
                "r.jsonl ends at seq 0, view.json reflects seq 1",
            ],
        )
        self.assertEqual(change_log.behind({"local": 4}, {"local": 3}, "view.json"), [])


class TestBoundary(unittest.TestCase):
    @staticmethod
    def place(edit: change_log.Edit, inner: change_log.Place) -> str:
        return change_log.where(edit.target, inner)

    def test_the_refusal_names_the_first_place_the_journal_cannot_hold(self) -> None:
        good = change_log.Edit("put", "repo/x", {"name": "x"}, False)
        bad = change_log.Edit("put", "repo/y", {"name": "x", "aliases": ["y", 2.5]}, False)
        self.assertEqual(change_log.refusal([good], self.place), "")
        self.assertEqual(
            change_log.refusal([good, bad], self.place),
            "nothing was written: repo/y aliases.1 holds a float (2.5), which the journal cannot hold "
            "(it keeps whole numbers and text in UTF-8)",
        )

    def test_taken_in_converts_once_with_a_note_each(self) -> None:
        value = {"x": [1.5, "ok"], "y": "a\udcff"}
        made, notes = change_log.taken_in(value, "the file", "/".join)
        self.assertEqual(made, {"x": ["1.5", "ok"], "y": "a\ufffd"})
        self.assertEqual(
            notes,
            [
                "x/0: a float (1.5) in the file, which the journal cannot hold; taken in as '1.5'",
                (
                    "y: text that is not valid Unicode ('a\\udcff') in the file, which the journal cannot "
                    "hold; taken in as 'a\ufffd'"
                ),
            ],
        )
        same = {"x": 1}
        self.assertIs(change_log.taken_in(same, "the file", str)[0], same)


if __name__ == "__main__":
    unittest.main()
