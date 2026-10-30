"""Unit tests for tools/survey_to_kg.py: reading a survey's tables, turning mapped rows into kg
import records (sources, observations, rework, the row's own text as a value), and the rejected
list (unmapped rows, rows left out on purpose, the People whitelist, the schema). Fictional data:
acme's invoice exporter."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import survey_to_kg as conv


SURVEY = """# Survey

## 1. People

| # | Fact | Source | Freq | Mistake? |
|---|---|---|---|---|
| P1 | Dana reviews the exporter; she is quick. | ledger:review:9; slack timestamps | 10+ | No |
| P2 | Lee likes coffee. | slack:#acme-team | 1 | No |

## 2. Process

| # | Fact | Source | Freq | Mistake? |
|---|---|---|---|---|
| R1 | Releases roll out once `smoke | lint` pass. | mem:release.md; orch:0000 2026-10-01T09 | 3 | Yes |
| R2 | Unmapped fact. | learn:run-1 | 1 | No |
"""

MAPPING = {
    "entities": [["person/dana", "Dana Example"], ["process/release", "Release process", ["releases"]]],
    "rows": {
        "P1": {"facts": [["person/dana", "reviews", "=the exporter"]], "drop": "an evaluative remark"},
        "P2": {"reject": "personal life", "whitelist": True},
        "R1": [
            ["process/release", "rule", "=", {"authority": "user_said"}],
            ["process/release", "mood", "=x"],
        ],
    },
}


class SurveyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = conv.parse_survey(SURVEY)
        self.result = conv.convert(self.rows, MAPPING)

    def test_rows_and_cells(self) -> None:
        self.assertEqual([r.id for r in self.rows], ["P1", "P2", "R1", "R2"])
        release = self.rows[2]
        self.assertEqual(release.section, "Process")
        self.assertIn("`smoke | lint`", release.text)
        self.assertEqual((release.observations, release.rework), (3, True))
        self.assertEqual(self.rows[0].observations, 10)

    def test_sources(self) -> None:
        self.assertEqual(conv.source_of("orch:0000 2026-10-01T09:00"), "transcript:0000 2026-10-01T09:00")
        self.assertEqual(conv.source_of("mem:release.md"), "memory:release.md")
        self.assertEqual(conv.source_of("learn:run-1"), "report:run-1")
        self.assertEqual(conv.source_of("slack timestamps"), "slack:slack timestamps")
        self.assertEqual(conv.source_of("org instructions"), "doc:org instructions")

    def test_facts(self) -> None:
        rule = next(f for f in self.result.facts if f["predicate"] == "rule")
        self.assertEqual(rule["value"], self.rows[2].text)
        self.assertEqual(rule["sources"], ["memory:release.md", "transcript:0000 2026-10-01T09"])
        self.assertEqual((rule["authority"], rule["rework"], rule["note"]), ("user_said", True, "survey R1"))
        self.assertEqual(len(self.result.facts), 2)
        self.assertEqual(len(self.result.entities), 2)

    def test_rejected(self) -> None:
        self.assertEqual(
            sorted((r["row"], r["category"]) for r in self.result.rejected),
            [
                ("P1", "people whitelist (part of a row)"),
                ("P2", "people whitelist"),
                ("R1", "schema"),
                ("R2", "unmapped"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
