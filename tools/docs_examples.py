"""The data behind the docs site's live examples (docs/index.md, docs/assets/home/examples.js).

The examples show bosun's own rules on its fictional acme world, never a drawing of them:

- the ledger example's state machines come from `KINDS` (lib/ledger.py), and its items, with their
  links, from the console's fixture ledger (tests/fixtures/console/acme-ledger.json);
- the knowledge example's map is the console's own neighbourhood walk (lib/console_kg_graph.py)
  over the fixture knowledge base (tests/fixtures/knowledge/acme-import.jsonl); the page lays it
  out wider than the console, to fit its frame;
- "needs you" lists the fixture ledger's pending proposals.

tools/mkdocs_hooks.py puts `page_data()` into the home page as JSON when the site is built, so a
change to those tables or fixtures shows on the site with nothing to copy by hand.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import console_kg_graph as kg_graph
import ledger
import lineage


ROOT = Path(__file__).resolve().parents[1]


Record = dict[str, Any]

LEDGER_FIXTURE = ROOT / "tests/fixtures/console/acme-ledger.json"
KNOWLEDGE_FIXTURE = ROOT / "tests/fixtures/knowledge/acme-import.jsonl"

# The ledger example: one ask from a colleague, and everything that came of it.
STORY_FOCUS = "task:2026-10-20/veembc"
STORY = (
    "slack:C0000000001/1700000001.000001",
    STORY_FOCUS,
    "mypr:acme/exporter#104",
    "jira:EXP-12",
    "out:task:2026-10-20/veembc/q0kge7",
    "task:2026-10-20/exnxww",
    "task:2026-10-20/znde55",
)
# An item's fields the example shows; the rest stay in the fixture.
SHOWN_FIELDS = ("ask", "requester", "to", "purpose", "subject", "parent", "external_status", "done_when")

KNOWLEDGE_CENTER = "process/release"
# Facts about a person the example leaves out: it shows who reviews what, not anyone's profile.
PERSONAL = frozenset(
    {"timezone", "stated_working_hours", "stated_preference", "prefers_channel", "strengths", "member_of"}
)


def ordered_states(spec: Mapping[str, Any]) -> list[str]:
    """A kind's states in the order work moves through them: breadth-first from the initial state,
    then the states only reached from outside the table (a terminal or escape state)."""
    transitions: Mapping[str, Iterable[str]] = spec["transitions"]
    seen = [spec["initial"]]
    queue = deque(seen)
    while queue:
        for target in sorted(transitions.get(queue.popleft(), ())):
            if target not in seen:
                seen.append(target)
                queue.append(target)
    rest = set(transitions) | set(spec["terminal"]) | ({spec["escape"]} if spec.get("escape") else set())
    return seen + sorted(rest - set(seen))


def kind_data(spec: Mapping[str, Any]) -> Record:
    """What the example needs of one kind: its states, moves and which moves need evidence."""
    return {
        "description": spec["description"],
        "id_format": spec["id_format"],
        "states": ordered_states(spec),
        "initial": spec["initial"],
        "terminal": sorted(spec["terminal"]),
        "escape": spec.get("escape", ""),
        "mirror": bool(spec.get("mirror")),
        "transitions": {state: sorted(targets) for state, targets in spec["transitions"].items()},
        "evidence_required": sorted([list(move) for move in spec["evidence_required"]]),
        "evidence": spec.get("evidence", ""),
        "evidence_from_me": sorted(spec.get("evidence_from_me", ())),
    }


def item_data(item: Mapping[str, Any]) -> Record:
    fields = item.get("fields", {})
    return {
        "id": item["id"],
        "kind": item["kind"],
        "state": item["state"],
        "title": item["title"],
        "fields": {name: fields[name] for name in SHOWN_FIELDS if name in fields},
        "links": [
            {key: link[key] for key in ("rel", "id", "via") if link.get(key)}
            for link in item.get("links", [])
        ],
    }


def ledger_example(kinds: Mapping[str, Mapping[str, Any]], items: Mapping[str, Mapping[str, Any]]) -> Record:
    story = [items[item_id] for item_id in STORY]
    used = sorted({item["kind"] for item in story})
    return {
        "focus": STORY_FOCUS,
        "items": [item_data(item) for item in story],
        "kinds": {kind: kind_data(kinds[kind]) for kind in used},
        "relations": {
            name: {"meaning": relation.meaning, "inverse": relation.inverse}
            for name, relation in lineage.RELATIONS.items()
        },
    }


def needs_you(items: Mapping[str, Mapping[str, Any]]) -> list[Record]:
    """The open items with a pending proposal: what the console lists under "Needs you"."""
    pending = [
        item
        for item in items.values()
        if item.get("proposal") and item["state"] not in ledger.KINDS[item["kind"]]["terminal"]
    ]
    return [
        {"id": item["id"], "kind": item["kind"], "title": item["title"], "action": item["proposal"]["action"]}
        for item in sorted(pending, key=lambda item: item["id"])
    ]


def entity_type(entity_id: str) -> str:
    return entity_id.split("/", 1)[0]


def fact_status(fact: Mapping[str, Any]) -> str:
    """How the example shows a fact: what you said is confirmed (bosun checks your words against the
    session); of the rest, what would have saved rework still waits in your review batch."""
    if fact.get("authority") == "user_said" or not fact.get("rework"):
        return "confirmed"
    return "proposed"


def knowledge_links(entities: Mapping[str, Record], facts: Iterable[Record]) -> list[kg_graph.Link]:
    """The facts between two entities, as the console's graph takes them."""
    return [
        kg_graph.Link(str(n), f["subject"], f["object"], f["predicate"], fact_status(f) == "confirmed")
        for n, f in enumerate(facts)
        if f.get("object") in entities
    ]


def view_data(ego: kg_graph.Ego) -> Record:
    """One entity's neighbourhood, as the console walks it: each node with how far it is from the
    centre and the node it was reached from."""
    return {
        "nodes": [{"id": node.id, "depth": node.depth, "parent": node.parent} for node in ego.nodes],
        "edges": [
            {
                "source": ego.nodes[e.source].id,
                "target": ego.nodes[e.target].id,
                "label": " ".join(e.predicates),
            }
            for e in ego.edges
        ],
    }


def split_records(records: Iterable[Record]) -> tuple[dict[str, Record], list[Record]]:
    """A knowledge import's entities by id, and its facts but those about a person's profile."""
    records = list(records)
    entities = {r["id"]: r for r in records if r["type"] == "entity"}
    return entities, [r for r in records if r["type"] == "fact" and r["predicate"] not in PERSONAL]


def touches(fact: Mapping[str, Any], ids: Iterable[str]) -> bool:
    ids = set(ids)
    return fact["subject"] in ids or fact.get("object") in ids


def entity_data(entity: Mapping[str, Any]) -> Record:
    return {"name": entity["name"], "type": entity_type(entity["id"]), "summary": entity.get("summary", "")}


def knowledge_example(records: Iterable[Record], center: str = KNOWLEDGE_CENTER) -> Record:
    """The map around <center>: each entity it reaches with its neighbourhood as the console walks it
    (a click moves the map there), and the facts about them."""
    entities, facts = split_records(records)
    links = knowledge_links(entities, facts)
    reached = [node.id for node in kg_graph.walk(center, links, hops=len(entities))]
    about = [f for f in facts if touches(f, reached)]
    return {
        "center": center,
        "entities": {entity_id: entity_data(entities[entity_id]) for entity_id in reached},
        "views": {entity_id: view_data(kg_graph.ego(entity_id, links)) for entity_id in reached},
        "facts": [fact_data(f) for f in about],
    }


def fact_data(fact: Mapping[str, Any]) -> Record:
    shown = {key: fact[key] for key in ("subject", "predicate", "object", "value", "order") if key in fact}
    return shown | {
        "sources": list(fact.get("sources", [])),
        "authority": fact.get("authority", "observed"),
        "status": fact_status(fact),
    }


def page_data(root: Path = ROOT) -> Record:
    """Everything the home page's examples read, from the tables and fixtures above."""
    fixture = json.loads((root / LEDGER_FIXTURE.relative_to(ROOT)).read_text())
    lines = (root / KNOWLEDGE_FIXTURE.relative_to(ROOT)).read_text().splitlines()
    return {
        "ledger": ledger_example(ledger.KINDS, fixture["items"]),
        "needs_you": needs_you(fixture["items"]),
        "knowledge": knowledge_example(json.loads(line) for line in lines if line.strip()),
    }


def script_json(data: Any) -> str:
    """<data> as JSON that is safe inside a <script> element: nothing in it can end the element."""
    return json.dumps(data, sort_keys=True).replace("<", "\\u003c")
