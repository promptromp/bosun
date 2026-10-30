"""The Impact view's "What learning has produced" panel (design: docs/design/impact.md, "What
learning has produced"). Pure over what the console read: learning's runs and their reports, the
ledger's `learn:` items, and the knowledge base's facts. Counts only what is recorded; time saved is
not measured yet, so the panel names what is missing instead of estimating it.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

import impact


# A finding's line in a learn report (lib/learn.py, finding_lines): its title and its key.
FINDING_LINE = re.compile(r"^- \*\*(?P<title>.+?)\*\* \(\w+; `(?P<key>[^`]+)`;", re.MULTILINE)
RECURRING_MIN_RUNS = 2  # a finding in this many runs or more has come back
RECURRING_SHOWN = 8


# ---- learning runs and their findings ----------------------------------------------------------


def run_totals(runs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """How many runs (live and dry), the findings they reported, what they cost, the last one."""
    rows = list(runs)
    dry = [r for r in rows if r.get("dry_run")]
    started = sorted(str(r["started"]) for r in rows if r.get("started"))
    return {
        "runs": len(rows) - len(dry),
        "dry_runs": len(dry),
        "findings": int(sum(number(r, "findings") for r in rows)),
        "cost": round(sum(number(r, "cost") for r in rows), 2),
        "last": started[-1] if started else None,
    }


def number(row: Mapping[str, Any], key: str) -> float:
    """A run's count or cost, 0 when it was not recorded."""
    value = row.get(key)
    return float(value) if isinstance(value, int | float) else 0.0


def report_findings(text: str) -> dict[str, str]:
    """A report's findings: key -> title."""
    return {m["key"]: m["title"] for m in FINDING_LINE.finditer(text)}


def recurring(reports: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """Findings reported by more than one run (<reports>: (started, report text) per run), most
    runs first: a problem learning found that came back."""
    seen: dict[str, dict[str, Any]] = {}
    for started, text in sorted(reports):
        for key, title in report_findings(text).items():
            entry = seen.setdefault(key, {"key": key, "title": title, "runs": 0, "first": started})
            entry["runs"] += 1
            entry["last"] = started
    found = [e for e in seen.values() if e["runs"] >= RECURRING_MIN_RUNS]
    return sorted(found, key=lambda e: (-e["runs"], e["key"]))[:RECURRING_SHOWN]


def proposal_states(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """The ledger's `learn:` items by state: proposed, applied, done, rejected..."""
    return dict(Counter(str(i.get("state") or "?") for i in items))


# ---- the knowledge base ------------------------------------------------------------------------


def kb_weeks(facts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Facts recorded per week, and the running total."""
    added: Counter[date] = Counter()
    for fact in facts:
        day = impact.parse_day(fact.get("recorded_at"))
        if day is not None:
            added[impact.monday(day)] += 1
    rows, total = [], 0
    for week in sorted(added):
        total += added[week]
        rows.append({"week": week.isoformat(), "added": added[week], "total": total})
    return rows


def kb_growth(kb: Mapping[str, Any] | None) -> dict[str, Any]:
    """The knowledge base's size and growth (<kb>: its facts' recorded_at and status, the entity
    count, or an error); `exists` False when there is none."""
    if not kb:
        return {"exists": False, "error": None}
    if kb.get("error"):
        return {"exists": False, "error": kb["error"]}
    facts = list(kb.get("facts") or [])
    return {
        "exists": True,
        "error": None,
        "entities": int(kb.get("entities") or 0),
        "facts": len(facts),
        "by_status": dict(Counter(str(f.get("status") or "?") for f in facts)),
        "weeks": kb_weeks(facts),
    }


# ---- the panel ---------------------------------------------------------------------------------


def learning_model(
    runs: Iterable[Mapping[str, Any]],
    reports: Sequence[tuple[str, str]],
    items: Iterable[Mapping[str, Any]],
    kb: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The panel: learning's runs and proposals, the findings that came back, the knowledge base."""
    return {
        "runs": run_totals(runs),
        "proposals": proposal_states(items),
        "recurring": recurring(reports),
        "knowledge": kb_growth(kb),
    }
