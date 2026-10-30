"""The console's Learning view (/api/learn): learning's runs, its `learn:` ledger items and the
findings it remembers. Design: docs/design/console.md (section 4).

Pure over what lib/console_state.py reads (learning's state and run folders), a loaded ledger and
the keys of the run reports the server may show (only report.md files under the learn state
directory get one). Where a proposal's change and evidence live (lib/learn.py):
- the `learn:` item's fields: the finding's key, category, change target and run;
- its proposal (while pending) or, once settled, the `proposed` history entry: the action text
  with "Change:", "Why:" and "Evidence:" lines;
- the learn state's `proposals`: the exact change (target, text, window, folder);
- the transition to applied or rejected: when, and its note (what apply did, or why not).
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import learn
from console_data import Item
from console_state import LearnFiles


KIND = learn.KIND
ACTION_LINES = {"Change:": "change_text", "Why:": "why", "Evidence:": "evidence"}
SETTLED = ("applied", "rejected", "dismissed")
OUTSIDE = "outside the learn state folder: not shown"
RUN_KEYS = (
    "started",
    "outcome",
    "findings",
    "dropped",
    "cost",
    "model",
    "since",
    "until",
    "sessions",
    "error",
)


def run_model(run: Mapping[str, Any], report: str | None) -> dict[str, Any]:
    """One run, live or dry, and the key of its report (None: there is none to show, and why)."""
    has_dir = bool(run.get("run_dir"))
    return {
        **{key: run.get(key) for key in RUN_KEYS},
        "dry_run": bool(run.get("dry_run")),
        "proposals": [str(p) for p in run.get("proposals") or []],
        "report": report,
        "report_note": None
        if report
        else (OUTSIDE if has_dir and run.get("outcome") == "ok" else "no report"),
    }


def action_text(item: Item) -> str:
    """The proposal's action text: pending, else the last one recorded in the history."""
    proposal = item.get("proposal")
    if isinstance(proposal, dict) and proposal.get("action"):
        return str(proposal["action"])
    for entry in reversed(item.get("history") or []):
        if isinstance(entry, dict) and entry.get("event") == "proposed" and entry.get("note"):
            return str(entry["note"])
        if isinstance(entry, dict) and entry.get("action"):
            return str(entry["action"])
    return ""


def action_parts(text: str) -> dict[str, str]:
    """The Change, Why and Evidence lines of an action text (the line after "Change:" too: it is
    the change itself)."""
    parts: dict[str, str] = {}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        for prefix, name in ACTION_LINES.items():
            if line.startswith(prefix):
                rest = line.removeprefix(prefix).strip()
                following = lines[index + 1] if name == "change_text" and index + 1 < len(lines) else ""
                parts[name] = f"{rest} {following}".strip() if name == "change_text" else rest
    return parts


def settled(item: Item) -> dict[str, Any] | None:
    """When and how the item was applied or rejected: its last move into one of SETTLED."""
    for entry in reversed(item.get("history") or []):
        if isinstance(entry, dict) and entry.get("event") == "transition" and entry.get("to") in SETTLED:
            return {"state": entry.get("to"), "at": entry.get("at"), "note": entry.get("note", "")}
    return None


def item_model(item: Item, change: Mapping[str, Any] | None) -> dict[str, Any]:
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    action = action_text(item)
    return {
        "id": item.get("id"),
        "title": item.get("title", ""),
        "state": item.get("state", ""),
        "pending": bool(item.get("proposal")),
        "created": item.get("created_at"),
        "updated": item.get("updated_at"),
        **{key: (fields or {}).get(key, "") for key in ("key", "category", "target", "run")},
        **action_parts(action),
        "action": action,
        "change": dict(change) if change else None,
        "settled": settled(item),
    }


def finding_model(key: str, info: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "key": key,
        **{name: info.get(name) for name in ("title", "category", "first_seen", "last_seen", "status")},
        "times_seen": info.get("times_seen", 1),
    }


def learn_items(data: Mapping[str, Any]) -> list[Item]:
    """The ledger's `learn:` items, newest first."""
    items = [i for i in (data.get("items") or {}).values() if isinstance(i, dict) and i.get("kind") == KIND]
    return sorted(items, key=lambda i: str(i.get("created_at") or ""), reverse=True)


def findings(files: LearnFiles) -> list[dict[str, Any]]:
    """The findings remembered (the de-dup state), last seen first."""
    found = [finding_model(k, v) for k, v in files.state.findings.items() if isinstance(v, dict)]
    return sorted(found, key=lambda f: str(f["last_seen"] or ""), reverse=True)


def learn_model(files: LearnFiles, data: Mapping[str, Any], reports: Mapping[str, str]) -> dict[str, Any]:
    """/api/learn: the runs (newest first) with their report keys (<reports>: run_dir -> key), the
    `learn:` items (newest first), and the findings remembered (last seen first)."""
    remembered = findings(files)
    proposals = files.state.proposals
    return {
        "error": files.error,
        "running": files.state.running,
        "runs": [run_model(r, reports.get(str(r.get("run_dir") or ""))) for r in files.runs],
        "items": [item_model(i, proposals.get(str(i.get("id")))) for i in learn_items(data)],
        "findings": remembered,
        "statuses": dict(Counter(str(f["status"] or "?") for f in remembered)),
    }
