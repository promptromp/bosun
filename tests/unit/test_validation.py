"""Unit tests for what `validate` finds, alike for both stores (lib/validation.py): one shape of
finding, one table of how much each kind matters, the line the text and `bosun doctor` show, how a
store's files compare with its journal, and the exit code. Fictional data."""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import validation
from change_log import Conflict
from validation import Against


LIB = Path(validation.__file__).parent
# Every module that makes findings: each kind it names must have a row.
FINDING_MODULES = ("validation.py", "ledger.py", "knowledge.py", "knowledge_changes.py")


def against(**given: object) -> Against:
    fields: dict[str, object] = {
        "file_name": "view.json",
        "position": {"local": 3},
        "saved": {"local": 3},
        "conflicts": [],
        "pending": [],
        "has_records": True,
        "edited": False,
        "edited_detail": "changed outside the journal; the next write adopts it",
        "differences": lambda: [validation.finding("differs", "they differ", "repo/x", "name")],
    }
    fields.update(given)
    return Against(**fields)  # type: ignore[arg-type]


class FindingTest(unittest.TestCase):
    def test_every_kind_of_finding_has_a_severity(self) -> None:
        """Each kind `finding()` is called with, in either store, has its row (a new kind cannot
        slip in without one), and every row is a kind still found; a kind with no row would be a
        warning."""
        kinds = set()
        for name in FINDING_MODULES:
            tree = ast.parse((LIB / name).read_text())
            kinds |= {
                node.args[0].value
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "finding"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            }
        self.assertEqual(kinds, set(validation.SEVERITY))
        self.assertEqual(
            {severity for kind, severity in validation.SEVERITY.items() if kind != "note"},
            {"error", "warning"},
        )
        self.assertEqual(validation.finding("a-kind-to-come", "x")["severity"], "warning")

    def test_a_line_names_the_kind_where_and_what(self) -> None:
        line = validation.finding_line
        self.assertEqual(
            line(validation.finding("differs", "they differ", "f-aaaaaa", "1/set")),
            "differs: f-aaaaaa 1/set: they differ",
        )
        self.assertEqual(
            line(validation.finding("journal", "the journal is damaged: x")),
            "journal: journal: the journal is damaged: x",
        )
        self.assertEqual(
            line(validation.finding("note", validation.NO_JOURNAL_YET)), validation.NO_JOURNAL_YET
        )

    def test_a_conflict_names_its_kind_its_target_and_its_change(self) -> None:
        found = validation.conflict_finding(Conflict("0123456789abcdef", "f-aaaaaa", "field", "status: x"))
        self.assertEqual(
            found,
            {
                "kind": "conflict",
                "severity": "warning",
                "detail": "field: status: x (change 01234567)",
                "item": "f-aaaaaa",
            },
        )

    def test_the_report_exits_1_on_anything_but_a_note(self) -> None:
        note = validation.finding("note", validation.NO_JOURNAL_YET)
        self.assertEqual(validation.report([], False, "store is valid"), ("store is valid", 0))
        self.assertEqual(validation.report([note], False, "store is valid"), (validation.NO_JOURNAL_YET, 0))
        warning = validation.finding("conflict", "x", "f-aaaaaa")
        output, code = validation.report([note, warning], True, "store is valid")
        self.assertEqual(code, 1)
        self.assertEqual(
            [f["line"] for f in json.loads(output)], [validation.NO_JOURNAL_YET, "conflict: f-aaaaaa: x"]
        )


class JournalFindingsTest(unittest.TestCase):
    """A store's files against its journal: the same decisions for both stores."""

    def kinds(self, **given: object) -> list[str]:
        return [f["kind"] for f in validation.journal_findings(against(**given))]

    def test_at_the_same_position_the_rebuild_is_compared(self) -> None:
        self.assertEqual(self.kinds(), ["differs"])
        self.assertEqual(self.kinds(differences=list), [])

    def test_a_store_from_before_the_journal_is_a_note(self) -> None:
        self.assertEqual(self.kinds(position={}, saved={}), ["note"])
        self.assertEqual(self.kinds(position={}, saved={}, has_records=False, differences=list), [])

    def test_an_outside_edit_is_one_finding_not_a_difference_each(self) -> None:
        self.assertEqual(self.kinds(edited=True), ["edited"])

    def test_a_position_that_does_not_match_says_which_way(self) -> None:
        ahead = validation.journal_findings(against(position={"local": 4}))
        behind = validation.journal_findings(against(position={"local": 2}))
        self.assertEqual([f["kind"] for f in ahead + behind], ["position", "position"])
        self.assertIn("ahead of view.json", ahead[0]["detail"])
        self.assertIn("local.jsonl ends at seq 2, view.json reflects seq 3", behind[0]["detail"])

    def test_conflicts_and_waiting_updates_come_first(self) -> None:
        conflict = Conflict("0123456789abcdef", "f-aaaaaa", "field", "status")
        self.assertEqual(
            self.kinds(conflicts=[conflict], pending=["f-bbbbbb"]), ["conflict", "pending", "differs"]
        )


class DifferenceTest(unittest.TestCase):
    def test_the_first_path_where_two_values_differ(self) -> None:
        first = validation.first_difference
        self.assertIsNone(first({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}))
        self.assertEqual(first({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]}), ["a", "1", "b"])
        self.assertEqual(first([1, 2], [1, 2, 3]), ["2"])
        self.assertEqual(first({"a": 1}, {"b": 1}), ["a"])
        self.assertEqual(first(1, 2), [])


if __name__ == "__main__":
    unittest.main()
