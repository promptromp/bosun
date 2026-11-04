"""What `validate` finds, alike for both stores (`bosun ledger validate --journal`, `bosun kg
validate`; design: docs/design/sync.md): one shape of finding, one table of how much each kind
matters, the line that the text, its JSON and `bosun doctor` show, how a store's files stand
against its journal, and the exit code (1 on anything that is not a note).

A finding is {"kind", "severity", "detail"} and, when it is about one, the "item" (a ledger item,
a knowledge entity or fact) and the "path" in it. Each store gathers what it compares (its journal
read, its rebuild, its files' stamp) and its own findings (the ledger's rules); the decisions from
there are here.

Pure functions: no file access.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from change_log import Conflict, behind


Finding = dict[str, str]

# How much a kind of finding matters (`validate --json` carries it; bosun doctor fails on an error
# and warns on a warning). An error means writes are refused, or a rebuild would not give the files
# back; a warning is a rule broken, a change the rebuild keeps but cannot apply (or one waiting for
# what it changes), or an outside edit the next write adopts; a note is information. A kind with no
# row is a warning (a unit test keeps every kind a store finds).
ERROR, WARNING, NOTE = "error", "warning", "note"
SEVERITY = {
    "journal": ERROR,  # damaged: a write that reads it is refused
    "position": ERROR,  # behind: every write is refused; ahead: a write cut short, not yet applied
    "differs": ERROR,  # the journal's rebuild is not the files: a rebuild would lose the difference
    "state": WARNING,
    "link": WARNING,
    "link-target": WARNING,  # a link to an item gone, which a prune from before lineage was kept left
    "part_of": WARNING,
    "conflict": WARNING,
    "pending": WARNING,  # an update of a fact the journal does not hold (yet)
    "edited": WARNING,  # the next write adopts it
    "note": NOTE,
}
NO_JOURNAL_YET = "no journal yet; the next write starts it"


def finding(kind: str, detail: str, item: str = "", path: str = "") -> Finding:
    found = {"kind": kind, "severity": SEVERITY.get(kind, WARNING), "detail": detail}
    if item:
        found["item"] = item
    if path:
        found["path"] = path
    return found


def finding_line(found: Mapping[str, str]) -> str:
    """A finding as the text says it (and `--json` carries it, for bosun doctor): its kind, where
    (the item, else the journal, then the path in it), and what. The journal is named once: as the
    path of a finding about it, or as the kind of its damage."""
    if found["kind"] == "note":
        return found["detail"]
    places = (found.get("item") or ("" if found["kind"] == "journal" else "journal"), found.get("path", ""))
    where = " ".join(dict.fromkeys(place for place in places if place))
    return f"{found['kind']}: {f'{where}: ' if where else ''}{found['detail']}"


def report(found: Sequence[Finding], as_json: bool, clean: str) -> tuple[str, int]:
    """validate's output and exit code: 1 on anything that is not a note. The JSON carries each
    finding's line as the text says it; <clean> is the text when there is nothing to say."""
    code = 1 if any(f["kind"] != "note" for f in found) else 0
    if as_json:
        return json.dumps([{**f, "line": finding_line(f)} for f in found], indent=2), code
    return "\n".join(finding_line(f) for f in found) or clean, code


def damage_finding(problem: object) -> Finding:
    """A journal that cannot be read (lib/journal.py's JournalError)."""
    return finding("journal", f"the journal is damaged: {problem}")


def conflict_finding(conflict: Conflict) -> Finding:
    """A change the rebuild keeps but does not apply."""
    return finding(
        "conflict", f"{conflict.kind}: {conflict.detail} (change {conflict.change[:8]})", conflict.target
    )


def position_finding(position: Mapping[str, int], saved: Mapping[str, int], file_name: str) -> Finding:
    """The journal's position is not the one the files record (<file_name> says it): behind, every
    write is refused; ahead, the next write applies the rest."""
    gaps = behind(position, saved, file_name)
    if gaps:
        detail = (
            f"the journal is behind {file_name} ({'; '.join(gaps)}): a write would refuse to rebuild from it"
        )
    else:
        detail = f"the journal is ahead of {file_name} (at {dict(position)}): the next write applies it"
    return finding("position", detail, path="journal")


@dataclass(frozen=True)
class Against:
    """A store's files as saved, against its journal (read whole): the position each has, what the
    rebuild could not apply (conflicts) or waits for (pending: the targets of updates that wait for
    what they change), whether the files hold anything, whether they were changed outside the
    journal (with the store's words for it), and, at the same position, where the rebuild and the
    files differ."""

    file_name: str  # what records the files' position ("ledger.json", "view.json")
    position: Mapping[str, int]
    saved: Mapping[str, int]
    conflicts: Sequence[Conflict]
    pending: Sequence[str]
    has_records: bool
    edited: bool
    edited_detail: str
    differences: Callable[[], list[Finding]]


def journal_findings(store: Against) -> list[Finding]:
    """The journal against the files: what the rebuild could not apply or waits for; a store from
    before journals (a note); an outside edit (one finding: the next write adopts the difference,
    which is not data at risk); a position that does not match; or, at the same position, each
    place the rebuild and the files differ."""
    found = [conflict_finding(c) for c in store.conflicts]
    found += [
        finding("pending", "an update waits for this fact, which the journal does not hold", t)
        for t in store.pending
    ]
    if not store.position and not store.saved and store.has_records:
        return [*found, finding("note", NO_JOURNAL_YET, path="journal")]
    if store.edited:
        found.append(finding("edited", store.edited_detail, path="journal"))
    if store.position != store.saved:
        return [*found, position_finding(store.position, store.saved, store.file_name)]
    return found if store.edited else [*found, *store.differences()]


def unapplied_note(conflicts: int, pending: int, command: str) -> str:
    """A write that rebuilt, and so saves the rebuild: what it kept but could not apply (with one
    Mac, each is a bug or damage), in one line naming where to see them; "" when nothing."""
    parts = [f"{conflicts} conflict(s), changes it keeps but cannot apply"] if conflicts else []
    parts += [f"{pending} update(s) waiting for a fact it does not hold"] if pending else []
    return f"the journal's rebuild has {' and '.join(parts)}; {command} lists them" if parts else ""


# ---- where two values differ ----------------------------------------------------------------------


def first_difference(left: Any, right: Any) -> list[str] | None:
    """The first path at which two JSON values differ (keys sorted; a list's first index that
    differs, or where the shorter one ends), or None."""
    if isinstance(left, dict) and isinstance(right, dict):
        return dict_difference(left, right)
    if isinstance(left, list) and isinstance(right, list):
        return list_difference(left, right)
    return None if left == right else []


def dict_difference(left: dict[str, Any], right: dict[str, Any]) -> list[str] | None:
    for key in sorted(set(left) | set(right)):
        below = [] if key not in left or key not in right else first_difference(left[key], right[key])
        if below is not None:
            return [str(key), *below]
    return None


def list_difference(left: list[Any], right: list[Any]) -> list[str] | None:
    for index, (one, other) in enumerate(zip(left, right, strict=False)):
        below = first_difference(one, other)
        if below is not None:
            return [str(index), *below]
    return [str(min(len(left), len(right)))] if len(left) != len(right) else None


def where_first(path: list[str]) -> str:
    """How a difference reads: where it is first, or that it is in one of them only."""
    return f"first at {'/'.join(path)}" if path else "in one of them only"


def differing(rebuilt: Mapping[str, Any], saved: Mapping[str, Any], file_name: str) -> list[Finding]:
    """Each key (an item, an entity, a fact) whose value differs between the journal's rebuild and
    the file that holds it (<file_name>), by the first path that differs."""
    found = []
    for key in sorted(set(rebuilt) | set(saved)):
        path = first_difference(rebuilt.get(key), saved.get(key))
        if path is not None:
            detail = f"the rebuild and {file_name} differ ({where_first(path)})"
            found.append(finding("differs", detail, key, "/".join(path)))
    return found
