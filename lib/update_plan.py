"""lib/update_plan.py: what an update would do, worked out from facts about the running version and
the new one (docs/design/update.md, step 1, "the plan"). Pure: no file, process or git I/O. The
caller gathers the facts and passes in `steps`, the migration path, and sets `kept` (what the update
writes to bosun.env, which depends on the file). The update's record keeps the plan it carries out
(as_record, from_record), for its summary. The added keys' notes and how a kept value is written come
from lib/migrations.py, which holds them."""

from __future__ import annotations

import dataclasses
import fnmatch
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import migrations


PROMPTS = (
    "orchestrator/policy.md",
    "orchestrator/plugin/commands/chores.md",
    "orchestrator/tasks/*.md",
    "orchestrator/learn.md",
    "orchestrator/slack-watch.md",
)
REREAD = ("orchestrator/policy.md", "orchestrator/tasks/*.md")
TASK = "orchestrator/tasks/{}.md"  # a task type's built-in text
# The rules every task gets, whatever its types: a site's own _common.md only adds to them.
COMMON = TASK.format("_common")

CUT_SHORT = "a kg forget was cut short: the next kg write finishes it, then run bosun update again"
GOING_BACK = "going back is not supported: the {store} store is at format {found}, newer than {target}"


@dataclass(frozen=True)
class VersionFacts:
    commit: str
    formats: Mapping[str, int]
    rules: Mapping[str, int]
    keys: frozenset[str]
    prompts: Mapping[str, str]


@dataclass(frozen=True)
class WindowFacts:
    name: str
    kind: str  # orchestrator, repo or checkin
    status: str  # idle, shell, busy, waiting, exited or unknown
    launch_changed: bool
    # False for a window outside the window list with no launch recorded (a shell, a Claude started by
    # hand): bosun never started it, so an update leaves it as it is.
    started_by_bosun: bool = True


class Treatment(StrEnum):
    LEAVE = "leave"
    REREAD = "re-read"
    RESTART = "restart"
    TAKE_STOCK = "take stock, then restart"
    WAIT = "wait until idle"
    ASK_FIRST = "ask first"
    RELAUNCH = "relaunch"
    RESTART_LOOP = "restart the loop"
    NOT_STARTED_BY_BOSUN = "not started by bosun: left as it is"


_BY_STATUS = {
    "idle": Treatment.RESTART,
    "shell": Treatment.TAKE_STOCK,
    "busy": Treatment.WAIT,
    "exited": Treatment.RELAUNCH,
}


def treatment(window: WindowFacts, reread: bool) -> Treatment:
    """What a window gets: the table in the design note, first matching row."""
    if window.kind == "checkin":
        return Treatment.RESTART_LOOP
    if not window.started_by_bosun:
        return Treatment.NOT_STARTED_BY_BOSUN
    if not window.launch_changed:
        return Treatment.REREAD if reread and window.kind == "orchestrator" else Treatment.LEAVE
    return _BY_STATUS.get(window.status, Treatment.ASK_FIRST)


@dataclass(frozen=True)
class Plan:
    """What an update would do (make_plan). <renames>: each config key bosun.env sets that the new
    version renamed, and its name now; an empty name means the key was removed. <kept>: each key the
    update adds that it writes to bosun.env, and the value that keeps what this install did
    (migrations.keys_to_keep, which update_facts.plan_for sets)."""

    from_commit: str
    to_commit: str
    commits: tuple[str, ...]
    migrations: tuple[tuple[str, int, int], ...]
    rules: tuple[tuple[str, int, int], ...]
    keys_added: tuple[str, ...]
    renames: tuple[tuple[str, str], ...]
    prompts: tuple[str, ...]
    windows: tuple[tuple[str, Treatment], ...]
    jobs: tuple[str, ...]
    console: bool
    handed: tuple[str, ...]
    user_changes: tuple[str, ...]
    blockers: tuple[str, ...]
    kinds: tuple[tuple[str, str], ...] = ()  # each window's kind (orchestrator, repo, checkin), as listed
    # Each store's format on disk (0: none yet), in the new version's order of its stores.
    formats: tuple[tuple[str, int], ...] = ()
    kept: tuple[tuple[str, str], ...] = ()  # an older record has none: nothing written, as it ran then


def _matches(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def prompts_changed(old: VersionFacts, new: VersionFacts) -> tuple[str, ...]:
    """The prompt files whose digest differs (or that one version lacks), sorted."""
    paths = {p for p in (*old.prompts, *new.prompts) if _matches(p, PROMPTS)}
    return tuple(sorted(p for p in paths if old.prompts.get(p) != new.prompts.get(p)))


def _rules_changed(old: VersionFacts, new: VersionFacts) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        (stream, old.rules.get(stream, 0), version)
        for stream, version in sorted(new.rules.items())
        if old.rules.get(stream, 0) != version
    )


def _migrations(
    old: VersionFacts,
    new: VersionFacts,
    steps: Callable[[str, int, int], Sequence[tuple[int, int]]],
) -> tuple[list[tuple[str, int, int]], list[str]]:
    found_steps: list[tuple[str, int, int]] = []
    blockers: list[str] = []
    for store, target in sorted(new.formats.items()):
        found = old.formats.get(store, target)
        if found > target:
            blockers.append(GOING_BACK.format(store=store, found=found, target=target))
        elif found < target:
            try:
                found_steps += [(store, a, b) for a, b in steps(store, found, target)]
            except ValueError as err:
                blockers.append(str(err))
    return found_steps, blockers


def _handed(in_flight: Sequence[tuple[str, Sequence[str]]], changed: Sequence[str]) -> tuple[str, ...]:
    """The items handed out (<in_flight>: each with the task types whose built-in text its session got)
    whose text a changed prompt is part of: a changed type of theirs, or the rules every task gets
    (COMMON), which touch them all."""
    every = COMMON in changed
    return tuple(
        item for item, kinds in in_flight if every or any(TASK.format(kind) in changed for kind in kinds)
    )


def make_plan(  # noqa: PLR0913 - the facts the caller gathers, all keyword-only
    old: VersionFacts,
    new: VersionFacts,
    *,
    commits: Sequence[str],
    windows: Sequence[WindowFacts],
    jobs: Sequence[str],
    renames: Sequence[tuple[str, str]],
    user_changes: Sequence[tuple[str, str]],
    in_flight: Sequence[tuple[str, Sequence[str]]],
    missing: Sequence[str],
    deletion_cut_short: bool,
    console: bool,
    steps: Callable[[str, int, int], Sequence[tuple[int, int]]],
) -> Plan:
    changed = prompts_changed(old, new)
    reread = any(_matches(p, REREAD) for p in changed)
    migrations, blocked = _migrations(old, new, steps)
    blockers = [*missing, *blocked, *([CUT_SHORT] if deletion_cut_short else [])]
    return Plan(
        from_commit=old.commit,
        to_commit=new.commit,
        commits=tuple(commits),
        migrations=tuple(migrations),
        rules=_rules_changed(old, new),
        keys_added=tuple(sorted(new.keys - old.keys)),
        renames=tuple(renames),
        prompts=changed,
        windows=tuple((w.name, treatment(w, reread)) for w in windows),
        jobs=tuple(jobs),
        console=console,
        handed=_handed(in_flight, changed),
        user_changes=tuple(f"{file}: {change}" for file, change in user_changes),
        blockers=tuple(blockers),
        kinds=tuple((w.name, w.kind) for w in windows),
        formats=tuple((store, old.formats.get(store, target)) for store, target in new.formats.items()),
    )


# ---- the plan in the update's record --------------------------------------------------------------


def as_record(plan: Plan) -> dict[str, Any]:
    """<plan> as the update's record keeps it: JSON's types only, so a record read back is equal."""
    return dict(json.loads(json.dumps(dataclasses.asdict(plan))))


def _listed(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError("not a list")
    return value


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in _listed(value))


def _pairs(value: Any) -> tuple[tuple[str, str], ...]:
    return tuple((str(a), str(b)) for a, b in _listed(value))


def _versions(value: Any) -> tuple[tuple[str, int, int], ...]:
    return tuple((str(name), int(a), int(b)) for name, a, b in _listed(value))


# How each part of a plan reads back from the record, and what it is without one (an older bosun's
# record kept no plan) or when it is of another shape: that part is left empty, the rest read.
_READ: dict[str, tuple[Callable[[Any], Any], Any]] = {
    "from_commit": (str, ""),
    "to_commit": (str, ""),
    "commits": (_strings, ()),
    "migrations": (_versions, ()),
    "rules": (_versions, ()),
    "keys_added": (_strings, ()),
    "renames": (_pairs, ()),
    "prompts": (_strings, ()),
    "windows": (lambda value: tuple((str(n), Treatment(how)) for n, how in _listed(value)), ()),
    "jobs": (_strings, ()),
    "console": (lambda value: value is True, False),
    "handed": (_strings, ()),
    "user_changes": (_strings, ()),
    "blockers": (_strings, ()),
    "kinds": (_pairs, ()),
    "formats": (lambda value: tuple((str(store), int(n)) for store, n in _listed(value)), ()),
    "kept": (_pairs, ()),
}


def _part(data: Mapping[str, Any], name: str) -> Any:
    read, empty = _READ[name]
    try:
        return read(data[name]) if name in data else empty
    except (TypeError, ValueError):
        return empty


def from_record(data: Mapping[str, Any]) -> Plan:
    """The plan an update's record keeps (as_record): a part it lacks, or holds in a shape this code
    cannot read, is empty, so the summary says less, never something false."""
    return Plan(**{name: _part(data, name) for name in _READ})


def _section(title: str, lines: Sequence[str]) -> list[str]:
    return [title, *(f"  {line}" for line in lines), ""] if lines else []


def _rename_line(old: str, new: str) -> str:
    return f"{old} is removed" if not new else f"{old} is now {new}"


def _key_lines(plan: Plan) -> list[str]:
    """Each key the update adds, with its note when it has one that holds (migrations.added_notes)."""
    notes = dict(migrations.added_notes(plan.keys_added, dict(plan.kept)))
    return [f"{key}: {notes[key]}" if key in notes else key for key in plan.keys_added]


def _window_line(name: str, how: Treatment) -> str:
    return f"{name}: {how.value}"


def render_plan(plan: Plan) -> str:
    """The plan as the person reads it before saying yes."""
    out = [f"Update {plan.from_commit} -> {plan.to_commit}", ""]
    out += _section(f"Commits ({len(plan.commits)}):", plan.commits)
    out += _section("Migrations:", [f"{s}: format {a} -> {b}" for s, a, b in plan.migrations])
    out += _section("Rules changes:", [f"{s}: version {a} -> {b}" for s, a, b in plan.rules])
    out += _section("New config keys:", _key_lines(plan))
    kept = [migrations.assignment(key, value) for key, value in plan.kept]
    out += _section("Written to bosun.env, to keep what this install did:", kept)
    out += _section("Config keys renamed or removed:", [_rename_line(a, b) for a, b in plan.renames])
    out += _section(f"Prompts changed ({len(plan.prompts)}):", plan.prompts)
    out += _section("Windows:", [_window_line(n, t) for n, t in plan.windows])
    out += _section("Jobs to reload:", plan.jobs)
    out += _section("Console:", ["running: restart it after the update"] if plan.console else [])
    out += _section("Handed tasks keep their old text (bosun delegate --update):", plan.handed)
    out += _section("Change these of your own files yourself:", plan.user_changes)
    out += _section("Blocked:", plan.blockers)
    out.append("The update is blocked." if plan.blockers else "Nothing changes until you say yes.")
    return "\n".join(out) + "\n"
