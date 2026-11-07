"""What `bosun update` says when it ends (docs/design/update.md, "The summary"): the commits and how
long it took; each store's migrations and version bumps; the config files it changed, the new keys
and what to change by hand in my own files; how many prompts changed; the launchd jobs reloaded; the
handed tasks that keep their old text; what came of each window; what was found after the switch;
and where the backup is.

Pure: build, render and as_json take values and read no file (render reads $HOME, to show the
backup's path with ~). build takes the update's record, the
plan it carried out, what its steps learnt (each kept in the record as it is learnt: of_record reads
them back, so an update carried on by a later run, or finished by the check-in loop, says the same as
one that ran in one go), and what the caller found: what came of each window (update_agents.outcomes),
whether the backup is still there and its size, and how long it took. lib/update.py prints the text,
saves it as summary.md in the backup folder once the update is done, and with --json gives as_json.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import update_gate
import update_plan
from migrations import Applied
from update_plan import Plan, Treatment
from update_state import UpdateState


COLUMN = 12  # the narrowest column of names (stores, files, windows), as the design's example has it
GAP = 2  # the least space between a name and its line
GOING_BACK = "docs/setup.md#going-back"
PROMPTS = "orchestrator/"  # what a prompt's path starts with, left out of its name
LEFT_ALONE = "kept running (nothing it was started with changed)"
BACKUP_GONE = "deleted by a kg forget since; going back past it is no longer possible"
BY_HAND = "change by hand: "
FILE_LINE = re.compile(r"(?P<file>\S+): (?P<what>.*)", re.DOTALL)  # "bosun.env: ..." under Config
SIZE_UNITS = ("KB", "MB", "GB", "TB")
SIZE_STEP = 1000
MINUTE = 60


@dataclass(frozen=True)
class Summary:
    from_commit: str
    to_commit: str
    commits: int
    seconds: int
    data: tuple[tuple[str, str], ...]  # each store and its line
    config: tuple[str, ...]  # the config files changed ("bosun.env: ..."), and the new keys
    prompts: tuple[str, ...]  # their paths in bosun ("orchestrator/policy.md")
    jobs: tuple[str, ...]  # the launchd jobs' labels
    agents: tuple[tuple[str, str], ...]  # each window and what came of it
    handed: tuple[str, ...]  # the items whose task text is the old one
    user_changes: tuple[str, ...]  # what to change in my own files ("sessions.yml: ...")
    backup: str  # its absolute path ("" when it took none)
    backup_bytes: int
    backup_gone: bool  # a kg forget deleted it since
    findings: tuple[str, ...]


# ---- the parts --------------------------------------------------------------------------------


def _format_part(found: int | None, steps: Sequence[Applied]) -> str:
    if steps:
        lines = "; ".join(step.line for step in steps)
        return f"format {steps[0].from_version} -> {steps[-1].to_version} ({lines})"
    if found == 0:
        return "none yet"
    return "unchanged" if found is None else f"format {found}, unchanged"


def _store_line(store: str, plan: Plan, applied: Sequence[Applied]) -> str:
    """<store>'s line: its migrations, or the format it stays at, then its rules version's bump."""
    line = _format_part(dict(plan.formats).get(store), [a for a in applied if a.store == store])
    bumps = [(a, b) for name, a, b in plan.rules if name == store]
    return line + "".join(f"; rules {a} -> {b}" for a, b in bumps)


def _data(plan: Plan, applied: Sequence[Applied]) -> tuple[tuple[str, str], ...]:
    """A line per store, in the plan's order of them: every store it knows the format of, and any
    other a migration or a rules bump names."""
    named = [store for store, _ in plan.formats]
    named += [a.store for a in applied] + [store for store, _, _ in plan.rules]
    return tuple((store, _store_line(store, plan, applied)) for store in dict.fromkeys(named))


def _new_keys(keys: Sequence[str]) -> tuple[str, ...]:
    if not keys:
        return ()
    if len(keys) == 1:
        return ("1 new key, at its default (bosun doctor lists it)",)
    return (f"{len(keys)} new keys, at their defaults (bosun doctor lists them)",)


def _agents(plan: Plan, said: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """What came of each window, in the plan's order: what the caller says of it, else, for one the
    plan left alone, that it kept running. The checkin window, whose loop the release restarted, is
    not listed; a window the caller names that the plan does not comes last."""
    lines = dict(said)
    found: list[tuple[str, str]] = []
    for name, how in plan.windows:
        if name in lines:
            found.append((name, lines.pop(name)))
        elif how == Treatment.LEAVE:
            found.append((name, LEFT_ALONE))
    return (*found, *lines.items())


def build(  # noqa: PLR0913, PLR0917 - the parts, each from the step that learnt it
    state: UpdateState,
    plan: Plan,
    applied: Sequence[Applied],
    config_lines: Sequence[str],
    jobs: Sequence[str],
    findings: Sequence[str],
    backup_exists: bool,
    backup_bytes: int,
    seconds: int,
    *,
    agents: Sequence[tuple[str, str]] = (),
) -> Summary:
    """The summary of the update <state> records, which carried out <plan>: <applied>, the migrations
    that ran; <config_lines>, what changed in each config file ("bosun.env: ...", with the copy kept
    of it); <jobs>, the launchd jobs reloaded; <findings>, what was found after the switch; whether
    its backup is still there and its size; how long it took; and <agents>, what came of each window
    brought along (update_agents.outcomes)."""
    return Summary(
        from_commit=state.from_commit,
        to_commit=state.to_commit,
        commits=len(plan.commits),
        seconds=seconds,
        data=_data(plan, applied),
        config=(*config_lines, *_new_keys(plan.keys_added)),
        prompts=plan.prompts,
        jobs=tuple(jobs),
        agents=_agents(plan, agents),
        handed=plan.handed,
        user_changes=state.user_changes,
        backup=state.backup,
        backup_bytes=backup_bytes if backup_exists else 0,
        backup_gone=bool(state.backup) and not backup_exists,
        findings=tuple(findings),
    )


def of_record(
    state: UpdateState,
    agents: Sequence[tuple[str, str]],
    backup_exists: bool,
    backup_bytes: int,
    seconds: int,
) -> Summary:
    """build, with each part from the record of <state>, where every step kept what it learnt: the
    plan, the migrations that ran, bosun.env's change, the jobs reloaded and what was found after the
    switch. A part an older bosun's record lacks is left out."""
    applied = [found for line in state.migrated if (found := Applied.from_record(line))]
    plan = update_plan.from_record(state.plan)
    return build(
        state,
        plan,
        applied,
        state.config_changed,
        state.reloaded,
        state.findings,
        backup_exists,
        backup_bytes,
        seconds,
        agents=agents,
    )


# ---- the text ---------------------------------------------------------------------------------


def tilde(path: str) -> str:
    """<path> with ~ for the home folder at its start."""
    home = os.path.expanduser("~").rstrip("/")
    if home and home != "~" and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home) :]
    return path


def duration(seconds: int) -> str:
    """As the design says it: 41s, 2m 41s, 1h 2m 41s."""
    minutes, secs = divmod(max(seconds, 0), MINUTE)
    hours, minutes = divmod(minutes, MINUTE)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def size(count: int) -> str:
    """A number of bytes as people read it: 512 bytes, 12.3 KB, 3.1 MB."""
    if count < SIZE_STEP:
        return f"{count} bytes"
    value = float(count)
    for unit in SIZE_UNITS:
        value /= SIZE_STEP
        if value < SIZE_STEP or unit == SIZE_UNITS[-1]:
            break
    return f"{value:.1f} {unit}"


def _rows(rows: Sequence[tuple[str | None, str]]) -> list[str]:
    """Each row's name in a column as wide as the longest (COLUMN at the least); a row with no name is
    its line alone."""
    names = [name for name, _ in rows if name is not None]
    width = max([COLUMN, *(len(name) + GAP for name in names)])
    return [line if name is None else f"{name:<{width}}{line}" for name, line in rows]


def _section(title: str, lines: Sequence[str]) -> list[str]:
    return [title, *(f"  {line}" for line in lines)] if lines else []


def _file_row(line: str, what: str = "") -> tuple[str | None, str]:
    """A Config line, its file in the column: "bosun.env: ..." or my own file's change."""
    found = FILE_LINE.fullmatch(line)
    return (found["file"], what + found["what"]) if found else (None, what + line)


def _config(summary: Summary) -> list[str]:
    rows = [_file_row(line) for line in summary.config]
    rows += [_file_row(change, BY_HAND) for change in summary.user_changes]
    return _section("Config", _rows(rows))


def _short(commit: str) -> str:
    return commit[: update_gate.SHORT]


def _headline(summary: Summary) -> str:
    count = summary.commits
    commits = f" ({count} commit{'' if count == 1 else 's'})" if count else ""
    versions = f"{_short(summary.from_commit)} -> {_short(summary.to_commit)}"
    return f"bosun updated: {versions}{commits} in {duration(summary.seconds)}"


def _named(title: str, names: Sequence[str]) -> list[str]:
    return [f"{title}: {', '.join(names)}"] if names else []


def _prompts(prompts: Sequence[str]) -> list[str]:
    names = [path.removeprefix(PROMPTS) for path in prompts]
    return [f"Prompts changed: {len(names)} ({', '.join(names)})"] if names else []


def _backup(summary: Summary) -> list[str]:
    if summary.backup_gone:
        return [f"Backup: {BACKUP_GONE}"]
    return [f"Backup: {tilde(summary.backup)} ({size(summary.backup_bytes)})"] if summary.backup else []


def render(summary: Summary) -> str:
    """The summary as the update prints it, and saves it (the design's example), a line each."""
    jobs = [label.rsplit(".", 1)[-1] for label in summary.jobs]  # com.bosun.morning.watchdog: watchdog
    handed = [f"bosun delegate --update {shlex.quote(item)}" for item in summary.handed]
    lines = [
        _headline(summary),
        *_section("Data", _rows(summary.data)),
        *_config(summary),
        *_prompts(summary.prompts),
        *_named("Jobs reloaded", jobs),
        *_named("Handed tasks keep their old text", handed),
        *_section("Agents", _rows(summary.agents)),
        *_section("Found after the switch", summary.findings),
        *_backup(summary),
        f"Going back: {GOING_BACK}",
    ]
    return "\n".join(lines) + "\n"


def as_json(summary: Summary) -> dict[str, Any]:
    """The summary for --json: every field, the stores and the windows as objects, the backup's path
    absolute."""
    fields = dataclasses.asdict(summary).items()
    said = {name: list(value) if isinstance(value, tuple) else value for name, value in fields}
    return {**said, "data": dict(summary.data), "agents": dict(summary.agents)}
