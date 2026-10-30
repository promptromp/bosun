"""The Knowledge Graph page's two pictures of the whole knowledge base (design: docs/design/console.md,
section 6): the Briefing (/api/knowledge/briefing) and the Constellation
(/api/knowledge/constellation). Pure over a knowledge_model.KB and a time zone; lib/console_kb.py
reads the knowledge base (never identifiers.jsonl) and lib/console_knowledge.py supplies what every
Knowledge model shares (refs, the People whitelist, statuses, the review batch).

Definitions (the design note says the same):
- A fact's capture day is the local day it was first recorded (`recorded_at`; `observed_at` moves
  on each re-observation, so it is not used for this).
- Coverage of a card: the share of its entities that take part in at least one current, confirmed
  fact, as its subject or its object.
- "This week we learned": the last WEEK_DAYS local days up to today. New facts: current facts
  captured then. Changes: facts closed by a replacement then (before -> after), and `supersedes` or
  `partially_reversed_by` links between decisions captured then (the older decision -> the newer).
- Degree: the number of distinct entities an entity shares a current link with (a fact whose object
  is an entity, confirmed or waiting for review).
- The Constellation keeps at most MAX_NODES entities (the best connected) and MAX_EDGES edges, and
  says how many it left out. Every output is sorted, so the same knowledge base gives the same model.
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta, tzinfo
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import console_kg_graph as graph
import console_knowledge as ck
import knowledge_model as km
import knowledge_review as kr
import knowledge_schema as ks
from knowledge_model import KB, Record


SERIES_DAYS = 30  # the headline's sparkline: facts captured per day over this many days
WEEK_DAYS = 7  # "this week we learned"
DAY_FACTS = 60  # new facts per timeline day (the page lists 8, the rest on request)
CONNECTED = 6  # the most-connected strip
MINI_NODES = 13  # a strip card's ego graph: the entity and up to 12 neighbours, one hop
MAX_NODES = 400  # the Constellation's cap; its physics is O(n^2) per frame
MAX_EDGES = 1600
SHARE_DIGITS = 3
HIGHLIGHT_TYPES = frozenset({"lesson", "decision", "principle"})  # read first in a day
HIGHLIGHT_PREDICATES = frozenset({"rule", "caveat", "states"})
DECISION_LINKS = frozenset({"supersedes", "partially_reversed_by"})
DECIDED = "decided"
REPLACED = "replaced"
REDISCOVERY_NOTE = "Rediscovery saved: measured once learning counts rework episodes"


# ---- shared -------------------------------------------------------------------------------------


def captured_at(fact: Record) -> str:
    """When the fact was first recorded."""
    first = (fact.get("history") or [{}])[0]
    return str(fact.get("recorded_at") or first.get("at") or fact.get("observed_at") or "")


def capture_day(fact: Record, zone: tzinfo) -> date | None:
    return ck.local_date(captured_at(fact), zone)


def share(part: int, whole: int) -> float:
    return round(part / whole, SHARE_DIGITS) if whole else 0.0


def in_order(facts: Iterable[Record]) -> list[Record]:
    return sorted(facts, key=ck.fact_number)


def visible_facts(kb: KB) -> list[Record]:
    return in_order(f for f in kb.facts.values() if ck.visible(kb, f))


def current_facts(kb: KB) -> list[Record]:
    return in_order(f for f in kb.facts.values() if ck.shown_current(kb, f))


def covered_entities(kb: KB) -> set[str]:
    """Entities in at least one current, confirmed fact, as subject or object."""
    found: set[str] = set()
    for fact in current_facts(kb):
        if ck.status(kb, fact) == km.CONFIRMED:
            found.update(end for end in (fact["subject"], fact.get("object")) if end)
    return found & set(kb.entities)


def card_members(kb: KB, card: str) -> list[str]:
    return sorted(i for i, e in kb.entities.items() if ck.card_of(kb, e["type"]) == card)


def subject_counts(kb: KB) -> Counter[str]:
    return Counter(f["subject"] for f in current_facts(kb))


def brief(kb: KB, fact: Record) -> dict[str, Any]:
    """A fact as the timeline and the pictures name it."""
    authority = str(fact.get("authority") or "")
    return {
        "id": fact["id"],
        "subject": ck.ref(kb, fact["subject"]),
        "predicate": fact["predicate"],
        "object": ck.ref(kb, fact.get("object")),
        "value": fact.get("value"),
        "text": kr.statement(kb, fact),
        "sentence": ck.sentence_view(kb, fact),
        "status": ck.status(kb, fact),
        "authority_label": ck.AUTHORITY_LABELS.get(authority, authority),
        "prevents_rework": bool(fact.get("prevents_rework")),
    }


# ---- the Briefing: headline -----------------------------------------------------------------------


def totals(kb: KB) -> dict[str, Any]:
    """Current facts (confirmed or waiting), and the confirmed share of them."""
    current = Counter(ck.status(kb, f) for f in current_facts(kb))
    confirmed, proposed = current[km.CONFIRMED], current[km.PROPOSED]
    return {
        "entities": len(kb.entities),
        "facts": confirmed + proposed,
        "confirmed": confirmed,
        "proposed": proposed,
        "confirmed_share": share(confirmed, confirmed + proposed),
    }


def captured_series(kb: KB, today: date, zone: tzinfo) -> dict[str, Any]:
    """Facts captured per day over the last SERIES_DAYS days (whatever their status now), and the
    running total, counting those captured before."""
    days = [today - timedelta(days=n) for n in range(SERIES_DAYS - 1, -1, -1)]
    per_day = Counter(capture_day(f, zone) for f in visible_facts(kb))
    known = [d for d in per_day if d is not None]
    running = sum(per_day[d] for d in known if d < days[0])
    new, total = [], []
    for day in days:
        running += per_day[day]
        new.append(per_day[day])
        total.append(running)
    return {
        "days": [d.isoformat() for d in days],
        "new": new,
        "total": total,
        "first_day": min(known).isoformat() if known else None,
        "this_week": sum(new[-WEEK_DAYS:]),
    }


def coverage(kb: KB, covered: set[str]) -> list[dict[str, Any]]:
    """Per card: its entities, how many take part in a current confirmed fact, and the share."""
    rows = []
    for name, title in ck.CARDS:
        members = card_members(kb, name)
        count = sum(1 for i in members if i in covered)
        rows.append(
            {
                "card": name,
                "title": title,
                "entities": len(members),
                "covered": count,
                "share": share(count, len(members)),
            }
        )
    return rows


# ---- the Briefing: this week ----------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    day: date
    model: dict[str, Any]
    shown: frozenset[str]  # the facts the change already shows (left out of the day's new facts)


def side(kb: KB, entity_id: str, text: str, status: str) -> dict[str, Any]:
    return {"entity": ck.ref(kb, entity_id), "text": text, "status": status}


def decided_text(kb: KB, entity_id: str) -> str:
    """What a decision says it decided (its current `decided` value), else its name."""
    found = next(
        (f for f in current_facts(kb) if f["subject"] == entity_id and f["predicate"] == DECIDED),
        None,
    )
    return str(found.get("value") or "") if found and found.get("value") else km.label(kb, entity_id)


def replaced(kb: KB, start: date, zone: tzinfo) -> list[Change]:
    """Facts closed by a replacement since <start>: before -> after."""
    found = []
    for fact in visible_facts(kb):
        after = kb.facts.get(str(fact.get("superseded_by") or ""))
        closed = ck.local_date(fact.get("valid_to"), zone) if fact.get("valid_to") else None
        if after is None or closed is None or closed < start or not ck.visible(kb, after):
            continue
        model = {
            "kind": REPLACED,
            "before": side(kb, fact["subject"], kr.statement(kb, fact), ck.status(kb, fact)),
            "after": side(kb, after["subject"], kr.statement(kb, after), ck.status(kb, after)),
        }
        found.append(Change(closed, model, frozenset({after["id"]})))
    return found


def reversed_decisions(kb: KB, start: date, zone: tzinfo) -> list[Change]:
    """`supersedes` / `partially_reversed_by` links captured since <start>: the older decision ->
    the newer one."""
    found = []
    for fact in current_facts(kb):
        day = capture_day(fact, zone)
        if fact["predicate"] not in DECISION_LINKS or not fact.get("object") or day is None or day < start:
            continue
        newer, older = fact["subject"], fact["object"]
        if fact["predicate"] == "partially_reversed_by":
            newer, older = older, newer
        status = ck.status(kb, fact)
        model = {
            "kind": fact["predicate"],
            "before": side(kb, older, decided_text(kb, older), status),
            "after": side(kb, newer, decided_text(kb, newer), status),
        }
        found.append(Change(day, model, frozenset({fact["id"]})))
    return found


def rank(kb: KB, fact: Record) -> int:
    """Lessons, decisions and principles read best, then rules and caveats, then the rest."""
    if ks.entity_type(fact["subject"]) in HIGHLIGHT_TYPES:
        return 0
    return 1 if fact["predicate"] in HIGHLIGHT_PREDICATES else 2


def new_facts(kb: KB, start: date, today: date, zone: tzinfo, shown: set[str]) -> dict[date, list[Record]]:
    found: dict[date, list[Record]] = {}
    for fact in current_facts(kb):
        day = capture_day(fact, zone)
        if day is not None and start <= day <= today and fact["id"] not in shown:
            found.setdefault(day, []).append(fact)
    return found


def new_entities(kb: KB, zone: tzinfo) -> Counter[date | None]:
    return Counter(ck.local_date(e.get("created_at"), zone) for e in kb.entities.values())


def day_row(kb: KB, day: date, facts: list[Record], changes: list[Change], entities: int) -> dict[str, Any]:
    ranked = sorted(facts, key=lambda f: (rank(kb, f), ck.fact_number(f)))
    return {
        "day": day.isoformat(),
        "changes": [c.model for c in changes],
        "facts": [brief(kb, f) for f in ranked[:DAY_FACTS]],
        "more": max(0, len(ranked) - DAY_FACTS),
        "count": len(ranked) + len(changes),
        "new_entities": entities,
    }


def week_model(kb: KB, today: date, zone: tzinfo) -> dict[str, Any]:
    """The timeline, newest day first: each day's changes (before -> after) and new facts."""
    start = today - timedelta(days=WEEK_DAYS - 1)
    changes = [c for c in replaced(kb, start, zone) + reversed_decisions(kb, start, zone) if c.day <= today]
    shown = {i for c in changes for i in c.shown}
    facts = new_facts(kb, start, today, zone, shown)
    created = new_entities(kb, zone)
    days = sorted({c.day for c in changes} | set(facts), reverse=True)
    rows = [day_row(kb, d, facts.get(d, []), [c for c in changes if c.day == d], created[d]) for d in days]
    return {
        "start": start.isoformat(),
        "end": today.isoformat(),
        "days": rows,
        "facts": sum(len(f) for f in facts.values()),
        "changes": len(changes),
    }


# ---- the Briefing: most connected -----------------------------------------------------------------


def neighbours(kb: KB, links: list[graph.Link]) -> dict[str, list[str]]:
    """entity -> the entities it shares a current link with (only entities that exist)."""
    return {
        i: [n for n in found if n in kb.entities]
        for i, found in graph.adjacency(links).items()
        if i in kb.entities
    }


def most_connected(kb: KB, links: list[graph.Link]) -> list[dict[str, Any]]:
    """The CONNECTED entities with the most neighbours, each with its one-hop ego graph."""
    near = neighbours(kb, links)
    ranked = sorted((i for i in near if near[i]), key=lambda i: (-len(near[i]), km.norm(km.label(kb, i)), i))
    counts = subject_counts(kb)
    return [
        {
            **(ck.ref(kb, i) or {}),
            "degree": len(near[i]),
            "facts": counts[i],
            "graph": ck.graph_view(kb, graph.ego(i, links, MINI_NODES, hops=1)),
        }
        for i in ranked[:CONNECTED]
    ]


def briefing_model(kb: KB, zone: tzinfo) -> dict[str, Any]:
    """/api/knowledge/briefing: the headline (captured per day, confirmed share, coverage), this
    week's timeline, the most-connected strip, and the rediscovery placeholder (not measured yet:
    it needs learning's rework counts)."""
    today = kb.now.astimezone(zone).date()
    links = ck.graph_links(kb)
    return {
        "exists": True,
        "error": None,
        "hint": None,
        "today": today.isoformat(),
        "totals": totals(kb),
        "captured": captured_series(kb, today, zone),
        "coverage": coverage(kb, covered_entities(kb)),
        "week": week_model(kb, today, zone),
        "connected": most_connected(kb, links),
        "rediscovery": {"measured": False, "note": REDISCOVERY_NOTE},
    }


def empty_briefing(error: str | None = None) -> dict[str, Any]:
    return {
        "exists": False,
        "error": error,
        "hint": None if error else ck.EMPTY_HINT,
        "today": None,
        "totals": None,
        "captured": None,
        "coverage": [],
        "week": None,
        "connected": [],
        "rediscovery": None,
    }


# ---- the Constellation ----------------------------------------------------------------------------


def first_days(kb: KB, zone: tzinfo) -> dict[str, date]:
    """entity -> the first day it was known: created, or in a captured fact, whichever is earlier."""
    found = {i: d for i, e in kb.entities.items() if (d := ck.local_date(e.get("created_at"), zone))}
    for fact in visible_facts(kb):
        day = capture_day(fact, zone)
        for end in (fact["subject"], fact.get("object")):
            if day is not None and end in kb.entities:
                found[end] = min(found.get(end, day), day)
    return found


def kept_nodes(kb: KB, near: Mapping[str, list[str]]) -> list[str]:
    """At most MAX_NODES entities, the best connected, then in card order and by id."""
    best = sorted(kb.entities, key=lambda i: (-len(near.get(i, [])), i))[:MAX_NODES]
    order = {name: n for n, (name, _) in enumerate(ck.CARDS)}
    return sorted(best, key=lambda i: (order.get(ck.card_of(kb, kb.entities[i]["type"]), len(order)), i))


def constellation_edges(
    kb: KB, links: list[graph.Link], index: Mapping[str, int], days: Mapping[str, str]
) -> list[dict[str, Any]]:
    """One edge per pair of kept entities: every predicate between them, confirmed when every fact
    is, and the first day any of them was captured."""
    pairs: dict[tuple[int, int], list[graph.Link]] = {}
    for link in links:
        a, b = index.get(link.subject), index.get(link.object)
        if a is not None and b is not None and a != b:
            pairs.setdefault((min(a, b), max(a, b)), []).append(link)
    return [
        {
            "from": a,
            "to": b,
            "predicates": list(dict.fromkeys(link.predicate for link in found)),
            "confirmed": all(link.confirmed for link in found),
            "day": min(days[link.id] for link in found),
        }
        for (a, b), found in sorted(pairs.items())
    ]


def link_days(kb: KB, links: list[graph.Link], zone: tzinfo, today: date) -> dict[str, str]:
    return {link.id: (capture_day(kb.facts[link.id], zone) or today).isoformat() for link in links}


def constellation_node(
    kb: KB, entity_id: str, near: Mapping[str, list[str]], day: date, extra: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        **(ck.ref(kb, entity_id) or {}),
        "label": graph.short_label(km.label(kb, entity_id)),
        "degree": len(near.get(entity_id, [])),
        "day": day.isoformat(),
        **extra,
    }


def constellation_cards(kb: KB, kept: list[str]) -> list[dict[str, Any]]:
    shown = Counter(ck.card_of(kb, kb.entities[i]["type"]) for i in kept)
    return [
        {"card": name, "title": title, "entities": len(card_members(kb, name)), "shown": shown[name]}
        for name, title in ck.CARDS
    ]


def constellation_model(kb: KB, zone: tzinfo) -> dict[str, Any]:
    """/api/knowledge/constellation: every entity (at most MAX_NODES) with its card, degree, first
    day and whether it is covered, and the current links among them, for the page's force layout."""
    today = kb.now.astimezone(zone).date()
    links = ck.graph_links(kb)
    near = neighbours(kb, links)
    kept = kept_nodes(kb, near)
    covered, counts, first = covered_entities(kb), subject_counts(kb), first_days(kb, zone)
    nodes = [
        constellation_node(kb, i, near, first.get(i, today), {"confirmed": i in covered, "facts": counts[i]})
        for i in kept
    ]
    edges = constellation_edges(
        kb, links, {i: n for n, i in enumerate(kept)}, link_days(kb, links, zone, today)
    )
    days = sorted({n["day"] for n in nodes} | {e["day"] for e in edges})
    return {
        "exists": True,
        "error": None,
        "hint": None,
        "today": today.isoformat(),
        "days": days,
        "cards": constellation_cards(kb, kept),
        "nodes": nodes,
        "edges": edges[:MAX_EDGES],
        "more": len(kb.entities) - len(kept),
        "more_edges": max(0, len(edges) - MAX_EDGES),
        "cap": MAX_NODES,
    }


def empty_constellation(error: str | None = None) -> dict[str, Any]:
    return {
        "exists": False,
        "error": error,
        "hint": None if error else ck.EMPTY_HINT,
        "today": None,
        "days": [],
        "cards": [],
        "nodes": [],
        "edges": [],
        "more": 0,
        "more_edges": 0,
        "cap": MAX_NODES,
    }
