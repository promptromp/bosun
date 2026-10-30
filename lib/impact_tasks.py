"""The Impact view's Cost per task panel (design: docs/design/impact.md, "Cost per task"). Pure
over the snapshot's `tasks` (lib/impact_attribution.py wrote them): what each ledger item cost,
its subagents included, grouped by kind, task type and outcome, the most expensive items next to
what they produced, and what stayed unattributed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

import impact
import impact_attribution


DEFAULT_TOP = 15  # BOSUN_IMPACT_TASKS_TOP: the most expensive items the panel lists
NOT_HANDED_OVER = "not handed over"  # the task-type group of items no window was handed
CENTS = 2


def cost(usage: Mapping[str, Any] | None) -> float:
    return sum(impact.usage_cost(model, counts) for model, counts in (usage or {}).items())


def item_costs(row: Mapping[str, Any]) -> dict[str, Any]:
    """An item row with its cost: the session's turns, its subagents' and both."""
    own, subagents = cost(row.get("usage")), cost(row.get("subagent_usage"))
    return {**row, "own_cost": own, "subagent_cost": subagents, "cost": own + subagents}


def money(value: float) -> float:
    return round(value, CENTS)


def group(rows: Iterable[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], str]) -> list[dict[str, Any]]:
    """Items, cost, median and the subagents' share per group, most expensive first."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    found = [
        {
            "key": name,
            "items": len(members),
            "cost": money(sum(r["cost"] for r in members)),
            "median": money(impact.median([r["cost"] for r in members]) or 0.0),
            "subagent_share": share(
                sum(r["subagent_cost"] for r in members), sum(r["cost"] for r in members)
            ),
        }
        for name, members in groups.items()
    ]
    return sorted(found, key=lambda g: -g["cost"])


def share(part: float, whole: float) -> float | None:
    return round(part / whole, 3) if whole else None


def top_item(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("id", "title", "kind", "task", "state", "outcome", "pr", "turns", "windows", "first", "last")
    return {
        **{name: row.get(name) for name in fields},
        "cost": money(row["cost"]),
        "subagent_cost": money(row["subagent_cost"]),
    }


def unattributed_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    found = [
        {
            "window": r.get("window"),
            "turns": r.get("turns") or 0,
            "cost": money(cost(r.get("usage")) + cost(r.get("subagent_usage"))),
        }
        for r in rows
    ]
    return sorted(found, key=lambda r: -r["cost"])


def usage_rows(tasks: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every tokens-by-model map in the tasks, as rows `impact.unpriced_models` reads."""
    rows = [*(tasks.get("items") or []), *(tasks.get("unattributed") or [])]
    return [{"usage": r.get(name) or {}} for r in rows for name in ("usage", "subagent_usage")]


def totals(items: list[dict[str, Any]], rest: list[dict[str, Any]]) -> dict[str, Any]:
    """Cost since the start, the attributed part and share, and the subagents' share of it."""
    attributed = sum(r["cost"] for r in items)
    total = attributed + sum(r["cost"] for r in rest)
    return {
        "total": money(total),
        "attributed": money(attributed),
        "attributed_share": share(attributed, total),
        "subagent_share": share(sum(r["subagent_cost"] for r in items), attributed),
        "items": len(items),
    }


def groups(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "by_outcome": group(items, lambda r: str(r.get("outcome"))),
        "by_kind": group(items, lambda r: str(r.get("kind") or "")),
        "by_task": group(items, lambda r: str(r.get("task") or NOT_HANDED_OVER)),
    }


def rule_costs(rules: Mapping[str, Any]) -> dict[str, float]:
    return {rule: money(cost(rules.get(rule))) for rule in impact_attribution.RULES}


def tasks_model(tasks: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    """The panel; `exists` False while no turn was attributed or left over (an empty ledger, a
    snapshot from before cost per task)."""
    items = [item_costs(r) for r in tasks.get("items") or []]
    rest = unattributed_rows(tasks.get("unattributed") or [])
    if not items and not rest:
        return {"exists": False}
    top_n = int(settings.get("tasks_top") or DEFAULT_TOP)
    return {
        "exists": True,
        "since": tasks.get("since"),
        "horizon_hours": settings.get("attribution_hours"),
        **totals(items, rest),
        "rules": rule_costs(tasks.get("rules") or {}),
        **groups(items),
        "top": [top_item(r) for r in sorted(items, key=lambda r: -r["cost"])[:top_n]],
        "unattributed": rest,
        "unpriced": impact.unpriced_models(usage_rows(tasks)),
        "fallback_model": impact.FALLBACK_MODEL,
    }


def from_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """The panel of a snapshot `bosun impact collect` wrote."""
    if not snapshot or not snapshot.get("tasks"):
        return {"exists": False}
    return tasks_model(snapshot["tasks"], snapshot.get("settings") or {})


def summary(model: Mapping[str, Any]) -> str:
    """One line for `bosun impact collect`."""
    if not model.get("exists"):
        return "cost per task: no turns yet"
    attributed = model["attributed_share"]
    percent = f"{attributed:.0%}" if attributed is not None else "n/a"
    unpriced = f"; unpriced models (priced as {model['fallback_model']}): {', '.join(model['unpriced'])}"
    return f"cost per task: {model['items']} items, {percent} of ${model['total']:.2f} attributed" + (
        unpriced if model["unpriced"] else ""
    )
