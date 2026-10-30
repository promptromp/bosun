#!/usr/bin/env python3
"""Turn a knowledge survey (markdown tables of facts) into an `bosun kg import` file.

Usage: survey_to_kg.py <survey.md> --map <mapping.json> --out <dir>

The survey is the format a one-off survey of a week's sessions produces: `## <n>. <Section>`
headings, each with a table `| # | Fact | Source | Freq | Mistake? |` (ids like P1, R12, D3).
Free text does not say which entity and predicate a fact belongs to, so a mapping file (kept with
the survey, outside this repository: it names real things) says it per row:

  {"entities": [["process/release", "Release process", ["releases"]], {"id": ..., "aliases": ...}],
   "rows": {"R2": [["process/release", "has_step", "step/merge", {"order": 1}],
                   ["process/release", "rule", "="]],
            "R9": {"facts": [...], "drop": "why the rest of the row is left out"},
            "P4": {"reject": "why the whole row is left out"}}}

A fact is [subject, predicate, object-or-value, options]: an object is an entity id, a value
starts with "=" ("=" alone: the row's own text); options: authority, order, valid_from. Sources come
from the row (orch:/repo: -> transcript, mem: -> memory, ledger: -> ledger, learn: -> report,
slack: -> slack, anything else -> doc), Freq becomes observations, "Mistake? Yes" marks the fact as
one that prevents rework. Every fact is checked against the schema here: a fact the schema or the
People whitelist refuses goes to rejected.jsonl with the reason, as do unmapped rows.

Writes <dir>/import.jsonl and <dir>/rejected.jsonl and prints counts only (never fact text).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import knowledge_schema as ks


Record = dict[str, Any]
SECTION = re.compile(r"^##\s+\d+\.\s+(.+?)\s*$")
ROW = re.compile(r"^\|\s*([A-Z]+\d+)\s*\|(.*)\|\s*$")
COLUMNS = 4  # fact, source, freq, mistake
FIRST_NUMBER = re.compile(r"\d+")
SOURCE_PREFIXES = {
    "orch": "transcript",
    "repo": "transcript",
    "mem": "memory",
    "ledger": "ledger",
    "learn": "report",
    "slack": "slack",
}
FALLBACK_KIND = "doc"
VALUE_MARK = "="
WHITELIST = "people whitelist"


@dataclass(frozen=True)
class Row:
    id: str
    section: str
    text: str
    sources: tuple[str, ...]
    observations: int
    rework: bool


def split_cells(line: str) -> list[str]:
    """The cells of a table row; a '|' inside backticks stays in its cell."""
    cells, current, quoted = [], "", False
    for char in line:
        if char == "`":
            quoted = not quoted
        if char == "|" and not quoted:
            cells.append(current.strip())
            current = ""
        else:
            current += char
    cells.append(current.strip())
    return cells


def parse_row(match: re.Match[str], section: str) -> Row | None:
    cells = split_cells(match.group(2))
    if len(cells) < COLUMNS:
        return None
    text, source, freq, mistake = cells[:COLUMNS]
    number = FIRST_NUMBER.search(freq)
    return Row(
        match.group(1),
        section,
        text,
        tuple(s.strip() for s in source.split(";") if s.strip()),
        int(number.group()) if number else 1,
        mistake.lower().startswith("yes"),
    )


def parse_survey(text: str) -> list[Row]:
    rows, section = [], ""
    for line in text.splitlines():
        heading = SECTION.match(line)
        if heading:
            section = heading.group(1)
            continue
        match = ROW.match(line)
        row = parse_row(match, section) if match else None
        if row:
            rows.append(row)
    return rows


def source_of(token: str) -> str:
    """kind:ref for one source of a row."""
    prefix, sep, rest = token.partition(":")
    if sep and prefix in SOURCE_PREFIXES:
        return f"{SOURCE_PREFIXES[prefix]}:{rest.strip() or token}"
    word = token.split(maxsplit=1)[0].lower() if token.strip() else ""
    for name, kind in SOURCE_PREFIXES.items():
        if word.startswith(name):
            return f"{kind}:{token}"
    return f"{FALLBACK_KIND}:{token}"


# ---- the mapping --------------------------------------------------------------------------------


def entity_record(spec: Any) -> Record:
    if isinstance(spec, dict):
        return {"type": "entity", **spec}
    entity_id, name, *rest = spec
    return {"type": "entity", "id": entity_id, "name": name, "aliases": rest[0] if rest else []}


def fact_spec(spec: Any) -> tuple[str, str, str, Record]:
    subject, predicate, target, *rest = spec
    return str(subject), str(predicate), str(target), dict(rest[0]) if rest else {}


@dataclass
class Result:
    entities: list[Record] = field(default_factory=list)
    facts: list[Record] = field(default_factory=list)
    rejected: list[Record] = field(default_factory=list)
    categories: Counter[str] = field(default_factory=Counter)

    def reject(self, row: Row, reason: str, category: str) -> None:
        self.categories[category] += 1
        self.rejected.append(
            {"row": row.id, "section": row.section, "category": category, "reason": reason, "text": row.text}
        )


def build_fact(row: Row, spec: Any, types: dict[str, str]) -> Record:
    subject, predicate, target, options = fact_spec(spec)
    record: Record = {"type": "fact", "subject": subject, "predicate": predicate}
    if target.startswith(VALUE_MARK):
        record["value"] = target[1:].strip() or row.text
    else:
        record["object"] = target
    record.update(
        sources=[source_of(s) for s in row.sources] or [f"{FALLBACK_KIND}:survey {row.id}"],
        authority=options.get("authority", ks.OBSERVED),
        observations=row.observations,
        rework=row.rework,
        note=f"survey {row.id}",
    )
    for key in ("order", "valid_from"):
        if key in options:
            record[key] = options[key]
    for entity_id in (subject, record.get("object")):
        if entity_id and entity_id not in types:
            raise ks.SchemaError(f"{entity_id} is not among the mapping's entities")
    return record


def check(record: Record) -> None:
    ks.check_fact(
        ks.Claim(
            record["subject"],
            record["predicate"],
            record.get("object"),
            record.get("value"),
            record["authority"],
            tuple(s.partition(":")[0] for s in record["sources"]),
        )
    )


def convert_fact(row: Row, fact: Any, types: dict[str, str], result: Result) -> None:
    try:
        record = build_fact(row, fact, types)
        check(record)
    except ks.SchemaError as exc:
        result.reject(row, str(exc), WHITELIST if exc.whitelist else "schema")
        return
    result.facts.append(record)


def convert_row(row: Row, spec: Any, types: dict[str, str], result: Result) -> None:
    if spec is None:
        result.reject(row, "no mapping for this row", "unmapped")
        return
    if isinstance(spec, dict) and "reject" in spec:
        category = WHITELIST if spec.get("whitelist") else "out of scope"
        result.reject(row, str(spec["reject"]), category)
        return
    for fact in spec.get("facts", []) if isinstance(spec, dict) else spec:
        convert_fact(row, fact, types, result)
    if isinstance(spec, dict) and spec.get("drop"):
        result.reject(row, f"partly kept; left out: {spec['drop']}", f"{WHITELIST} (part of a row)")


def convert(rows: list[Row], mapping: Record) -> Result:
    result = Result(entities=[entity_record(e) for e in mapping.get("entities", [])])
    types = {e["id"]: ks.entity_type(e["id"]) for e in result.entities}
    for row in rows:
        convert_row(row, mapping.get("rows", {}).get(row.id), types, result)
    return result


def write_jsonl(path: Path, records: list[Record]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("survey")
    parser.add_argument("--map", required=True, dest="mapping")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rows = parse_survey(Path(args.survey).read_text())
    result = convert(rows, json.loads(Path(args.mapping).read_text()))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "import.jsonl", result.entities + result.facts)
    write_jsonl(out / "rejected.jsonl", result.rejected)
    by_section = Counter(r.section for r in rows)
    print(f"rows: {len(rows)} ({', '.join(f'{n} {s}' for s, n in by_section.items())})")
    print(f"entities: {len(result.entities)}; facts: {len(result.facts)}")
    categories = ", ".join(f"{n} {c}" for c, n in sorted(result.categories.items()))
    print(f"rejected: {sum(result.categories.values())} ({categories})")
    print(f"wrote {out / 'import.jsonl'} and {out / 'rejected.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
