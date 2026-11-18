"""What `bosun update --back` decides (docs/design/update.md, "Going back, and trying a branch"): which update
it goes back over, what each part of the data needs, what restoring loses, what stops it, and the text of its
plan and of its summary's data. Pure: no file, process or git I/O; lib/update_back.py gathers the facts and
carries the back out, through lib/update.py's own steps.

A back goes from the version installed to the one the last finished update came from, one step, never
further. Each store is restored from that update's backup only where the older version cannot read it as it
is now: its format, or its rules version, differs between the two versions. bosun.env has that update's own
changes taken back (migrations.revert_config). Everything else is kept, with what was written since. Also
here, as it points at going back: whether a version an update would hand over to reads the data older than it
is (older_than_data), which the wrapper checks before every update.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import update_backups
import update_plan
import update_state
from update_plan import Plan
from update_state import UpdateState


SHORT = 7  # a commit as the messages name it (update_gate.SHORT)
BACK_SUMMARY = "back-summary.md"  # a back's summary, beside the backup of the update it went back over
CONFIG = (
    "config/bosun.env"  # what a back's put back restores of the config, when it took bosun.env's changes back
)
ITEMS_SHOWN = 8  # the ledger items a plan names, of those written to since
GUIDE = "docs/updating.md#going-back"

NOTHING_FINISHED = "no update has finished here: there is nothing to go back to"
ALREADY_BACK = (
    "bosun already went back from {from_} to {to} on {when}: one step back, never further; bosun update goes "
    "forward again"
)
MOVED = (
    "the install leads to {installed}, not to the version the last update installed ({to}): going back is "
    f"then by hand ({GUIDE})"
)
LEGACY = (
    "the version the last update came from is the checkout from before versions ({folder}), which has no "
    f"hold or write check: going back to it is by hand ({GUIDE})"
)
BACKUP_GONE = (
    "the {store} needs the backup that update took ({why}), which is gone: a kg forget deletes every backup, "
    "and going back past it is not possible"
)
BACKUP_OTHER = (
    "that update's backup holds the {store} at format {found}, not the {wanted} the older version reads"
)
CONFIG_GONE = (
    "bosun.env needs the copy in that update's backup, to take its changes back, which is gone: a kg forget "
    "deletes every backup"
)
OLDER_THAN_DATA = (
    "{target} {problems}: going back past a migration or a rules change is bosun update --back's, to the "
    "version the last update came from"
)


def short(commit: str) -> str:
    return commit[:SHORT]


def when(moment: str) -> str:
    """A record's ISO 8601 time as the plan and the summary say it: 2026-11-18 15:42 UTC (as it is, when
    it cannot be read)."""
    try:
        at = datetime.fromisoformat(moment)
    except ValueError:
        return moment
    return f"{(at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(UTC):%Y-%m-%d %H:%M} UTC"


def _clock_when(hlc: str) -> str:
    """When a change was written, from its hybrid clock (its first 13 digits are milliseconds)."""
    try:
        at = datetime.fromtimestamp(int(hlc[:13]) / 1000, UTC)
    except ValueError:
        return ""
    return when(at.isoformat())


# ---- which update a back goes back over ---------------------------------------------------------------


def gone_over(state: UpdateState | None) -> UpdateState | str:
    """The update a back goes back over, as the record <state> tells it (update_state.last_finished: the
    record past its checks, or the one before a record that ended with bosun as it was), or why there is
    none: nothing finished, or the last thing done was a back (one step back, never further)."""
    last = update_state.last_finished(state)
    if last is None:
        return NOTHING_FINISHED
    if last.kind == update_state.BACK:
        return ALREADY_BACK.format(
            from_=short(last.from_commit), to=short(last.to_commit), when=when(last.started_at)
        )
    return last


# ---- what each part needs ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Since:
    """What a store had written since a backup: how many changes, when the last, and the ledger items they
    touch (none for the knowledge base, whose changes can name people)."""

    changes: int = 0
    last: str = ""
    items: tuple[str, ...] = ()


def since(changes: Iterable[Mapping[str, Any]], position: Mapping[str, int], items: bool) -> Since:
    """Of a store's journal <changes>, those past <position> (a backup's journal position: each file's last
    seq, by its stem, the replica's id or "local"); with <items>, the targets they touch."""
    newer = [
        change
        for change in changes
        if int(change.get("seq", 0))
        > position.get("local" if change.get("local") else str(change.get("replica")), 0)
    ]
    if not newer:
        return Since()
    last = max(str(change.get("hlc", "")) for change in newer)
    touched = sorted({str(change["target"]) for change in newer if change.get("target")}) if items else []
    return Since(len(newer), _clock_when(last), tuple(touched))


def _changes(count: int) -> str:
    return f"{count} change{'' if count == 1 else 's'}"


@dataclass(frozen=True)
class StorePart:
    """A store, as a back finds it: its format on disk and the older version's, the rules versions of the
    version installed and of the older one, the format that update's backup holds it in (None: no backup
    is there), and what was written since that backup."""

    store: str
    format_now: int
    format_then: int
    rules_now: int
    rules_then: int
    backup_format: int | None
    written: Since = Since()

    @property
    def restore(self) -> bool:
        """Whether the older version cannot read it as it is: its format or its rules differ (a store none
        of the versions has, or the older one reads none of, needs nothing)."""
        if self.format_now <= 0 or self.format_then <= 0:
            return False
        return self.format_now != self.format_then or self.rules_now != self.rules_then

    def why(self) -> str:
        """What differs: "format 2 -> 1", "rules 2 -> 1", or both."""
        parts = [
            f"{what} {now} -> {then}"
            for what, now, then in (
                ("format", self.format_now, self.format_then),
                ("rules", self.rules_now, self.rules_then),
            )
            if now != then
        ]
        return "; ".join(parts)

    def blocker(self) -> str:
        """What stops a back that must restore it: no backup to restore from, or one that holds it in
        another format than the older version reads ("" when nothing does)."""
        if not self.restore:
            return ""
        if self.backup_format is None:
            return BACKUP_GONE.format(store=self.store, why=self.why())
        if self.backup_format not in (0, self.format_then):
            return BACKUP_OTHER.format(store=self.store, found=self.backup_format, wanted=self.format_then)
        return ""

    def loses(self) -> str:
        """What restoring it loses, as the plan says it."""
        written = self.written
        if not written.changes:
            return "nothing written since: restoring loses nothing"
        shown = list(written.items[:ITEMS_SHOWN])
        more = len(written.items) - len(shown)
        to = f", to {', '.join(shown)}" + (f" and {more} more" if more > 0 else "") if shown else ""
        return (
            f"loses {_changes(written.changes)} written since (the last at {written.last}){to}, kept in the "
            "backup going back takes first; going forward again does not bring them back"
        )

    def plan_line(self) -> str:
        if not self.restore:
            both = f"format {self.format_now}, rules {self.rules_now} in both"
            return f"kept, with what was written since ({both})"
        return f"{self.why()}: restored from that update's backup; {self.loses()}"

    def done_line(self) -> str:
        """What restoring it did, as the record keeps it and the summary says it."""
        written = self.written
        lost = (
            f"; {_changes(written.changes)} written since went with it, kept in the backup going back took"
            if written.changes
            else ""
        )
        return f"{self.store}: restored from the update's backup ({self.why()}){lost}"


@dataclass(frozen=True)
class ConfigPart:
    """bosun.env, as a back finds it: whether that update wrote it, what taking its changes back does and
    leaves, and whether that update's backup still holds the copy it was taken back from."""

    changed: bool = False
    said: tuple[str, ...] = ()
    left: tuple[str, ...] = ()
    backup_there: bool = True

    @property
    def takes_back(self) -> bool:
        return self.changed and bool(self.said)

    def blocker(self) -> str:
        return CONFIG_GONE if self.changed and not self.backup_there else ""


@dataclass(frozen=True)
class BackPart:
    """A back's own part of its plan: the update it goes back over (its id, start and backup), each store,
    bosun.env, and what that update asked me to change in my own files, which the older version reads as
    before."""

    over: str
    over_started: str
    backup: str
    stores: tuple[StorePart, ...]
    config: ConfigPart = ConfigPart()
    user_changes: tuple[str, ...] = ()

    def restored(self) -> tuple[StorePart, ...]:
        return tuple(part for part in self.stores if part.restore)

    @property
    def restores(self) -> bool:
        """Whether it changes any data (a store restored, bosun.env taken back): then it freezes."""
        return bool(self.restored()) or self.config.takes_back

    def restorable(self) -> tuple[str, ...]:
        """What a failed freeze puts back from the back's own backup: the parts it changes."""
        parts = [prefix for part in self.restored() for prefix in update_backups.STORE_PARTS[part.store]]
        return (*parts, *((CONFIG,) if self.config.takes_back else ()))

    def blockers(self) -> tuple[str, ...]:
        found = [part.blocker() for part in self.stores] + [self.config.blocker()]
        return tuple(line for line in found if line)

    def as_record(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _since_of(data: Any) -> Since:
    if not isinstance(data, dict):
        return Since()
    return Since(
        int(data.get("changes", 0)), str(data.get("last", "")), tuple(map(str, data.get("items", ())))
    )


def _store_of(data: Mapping[str, Any]) -> StorePart:
    found = data.get("backup_format")
    return StorePart(
        str(data["store"]),
        int(data["format_now"]),
        int(data["format_then"]),
        int(data["rules_now"]),
        int(data["rules_then"]),
        None if found is None else int(found),
        _since_of(data.get("written")),
    )


def from_record(data: Mapping[str, Any] | None) -> BackPart | None:
    """The back's part a plan's record keeps (BackPart.as_record), or None for none, or one of another
    shape (the summary then says less, never something false)."""
    if not data:
        return None
    try:
        config = data.get("config") or {}
        return BackPart(
            str(data["over"]),
            str(data["over_started"]),
            str(data.get("backup", "")),
            tuple(_store_of(store) for store in data["stores"]),
            ConfigPart(
                bool(config.get("changed")),
                tuple(map(str, config.get("said", ()))),
                tuple(map(str, config.get("left", ()))),
                bool(config.get("backup_there", True)),
            ),
            tuple(map(str, data.get("user_changes", ()))),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


# ---- the plan's text ----------------------------------------------------------------------------------


def _section(title: str, lines: Sequence[str]) -> list[str]:
    return [title, *(f"  {line}" for line in lines), ""] if lines else []


def render(plan: Plan, back: BackPart) -> str:
    """A back's plan as the person reads it before saying yes: the update it goes back over, what each
    store and bosun.env gets and what restoring loses, then what an update's plan says of the windows,
    the jobs and the rest."""
    versions = f"{short(plan.from_commit)} -> {short(plan.to_commit)}"
    out = [f"Go back {versions}, over the update of {when(back.over_started)}", ""]
    out += _section("Data:", [f"{part.store}: {part.plan_line()}" for part in back.stores])
    config = [f"bosun.env: {line}" for line in back.config.said]
    config += [f"bosun.env: {line}" for line in back.config.left]
    out += _section("Config, that update's changes taken back:", config)
    out += _section(
        "That update asked you to change these of your own files; the older version reads them as before:",
        back.user_changes,
    )
    out += update_plan.plan_sections(plan)
    out += _section("Blocked:", plan.blockers)
    out.append("Going back is blocked." if plan.blockers else "Nothing changes until you say yes.")
    return "\n".join(out) + "\n"


# ---- the summary --------------------------------------------------------------------------------------


def summary_data(back: BackPart, restored: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Each store's line in a back's summary: what restoring did (<restored>, as the record keeps it), or
    that it was kept."""
    done = {}
    for line in restored:
        store, _, what = line.partition(": ")
        done[store] = what
    return tuple(
        (
            part.store,
            done.get(part.store)
            or (part.plan_line() if part.restore else "kept, with what was written since"),
        )
        for part in back.stores
    )


def summary_place(state: UpdateState) -> tuple[Path, str] | None:
    """Where a finished record's summary is saved: summary.md beside its backup; a back that took none (it
    restored nothing) beside the backup of the update it went back over, as BACK_SUMMARY; None without
    either."""
    if state.backup:
        return Path(state.backup), update_backups.SUMMARY
    over = update_state.from_object(state.previous) if state.previous else None
    if state.kind == update_state.BACK and over is not None and over.backup:
        return Path(over.backup), BACK_SUMMARY
    return None


# ---- a version older than the data --------------------------------------------------------------------


def older_than_data(
    target: str,
    formats: Mapping[str, int],
    target_formats: Mapping[str, int],
    rules: Mapping[str, int],
    target_rules: Mapping[str, int],
) -> list[str]:
    """Why the version <target> would read the data older than it is: a store in a format older than the
    data's on disk (<formats>), or under rules older than those of the version installed (<rules>), which
    wrote it; nothing for a store there is none of yet, or a version whose source says no format (its own
    plan stops on what it cannot read). One line, or none."""
    problems = []
    for store, found in formats.items():
        theirs, mine, their_rules = (
            target_formats.get(store, 0),
            rules.get(store, 0),
            target_rules.get(store, 0),
        )
        if found > 0 and 0 < theirs < found:
            problems.append(f"has the {store} at format {theirs}, and the data is at format {found}")
        elif found > 0 and 0 < their_rules < mine:
            problems.append(
                f"reads the {store} under rules {their_rules}, and the data is under rules {mine}"
            )
    return [OLDER_THAN_DATA.format(target=target, problems="; ".join(problems))] if problems else []
