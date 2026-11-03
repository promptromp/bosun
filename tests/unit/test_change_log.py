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
        for bad in ("", "123", "0000000001000-0001", "abc.def"):
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


if __name__ == "__main__":
    unittest.main()
