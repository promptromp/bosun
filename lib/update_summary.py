"""What `bosun update` says when it ends (docs/design/update.md, "The summary"): the commits and how
long it took; each store's migrations and version bumps; the config files it changed, the new keys
and what to change by hand in my own files; how many prompts changed; the launchd jobs reloaded; the
handed tasks that keep their old text; what came of each window; what fails after the switch that
did not before; and where the backup is.

Pure: build, render and as_json take values and read no file nor the environment (render is given the
home folder, for ~, and the configured launchd label, for the jobs' names). build takes the update's
record, the plan it carried out, what its steps learnt (each kept in the record as it is learnt:
of_record reads them back, so an update carried on by a later run, or finished by the check-in loop,
says the same as one that ran in one go), and what the caller found: what came of each window
(update_agents.outcomes), whether the backup is still there and its size, and how long it took.
lib/update.py prints the text, saves it as summary.md in the backup folder once the update is done,
and with --json gives as_json. doctor_found reads bosun doctor's output for the record; left_to_me is
what the last update left me, which bosun doctor lists (through lib/update.py's after-update).
"""

from __future__ import annotations

import dataclasses
import re
import shlex
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

import migrations
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
NOT_BOSUNS = "left as it is (bosun did not start it)"
# What came of a window the plan left as it is, which nothing answers for.
LEFT = {Treatment.LEAVE: LEFT_ALONE, Treatment.NOT_STARTED_BY_BOSUN: NOT_BOSUNS}
BACKUP_GONE = "deleted by a kg forget since; going back past it is no longer possible"
BY_HAND = "change by hand: "
FILE_LINE = re.compile(r"(?P<file>\S+): (?P<what>.*)", re.DOTALL)  # "bosun.env: ..." under Config
MORNING = "morning"  # the job whose label is BOSUN_LAUNCHD_LABEL itself; the others add .<name> to it
# bosun doctor's output: a FAIL or warn line (an ok one ends what went before), the detail indented
# below one, and the time and tag a log line carries ("2026-11-07 08:12:39 [doctor] ").
DOCTOR_ITEM = re.compile(r"  (?P<kind>ok|warn|FAIL)  (?P<what>.*)")
DOCTOR_DETAIL = re.compile(r"    +(?P<what>\S.*)")
LOGGED = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \[[^\]]*\] ")
FAIL = "FAIL: "  # a doctor finding, as the record keeps it
WARN = "warn: "
ALREADY = "{n} already failing before the update"
WARNING = "{n} warning: run bosun doctor"
WARNINGS = "{n} warnings: run bosun doctor"
# The steps at which the last update left me what left_to_me lists: it has brought the windows along or
# does, and said so in its summary (doctor in its release would say it twice).
LEFT_TO_ME = ("agents", "done")
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
    backup_size: int  # the size of its files, in bytes
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
    plan left alone, that it kept running, or was left as it is as bosun did not start it (LEFT). The
    checkin window, whose loop the release restarted, is not listed; a window the caller names that
    the plan does not comes last."""
    lines = dict(said)
    found: list[tuple[str, str]] = []
    for name, how in plan.windows:
        if name in lines:
            found.append((name, lines.pop(name)))
        elif how in LEFT:
            found.append((name, LEFT[how]))
    return (*found, *lines.items())


def build(  # noqa: PLR0913, PLR0917 - the parts, each from the step that learnt it
    state: UpdateState,
    plan: Plan,
    applied: Sequence[Applied],
    config_lines: Sequence[str],
    jobs: Sequence[str],
    findings: Sequence[str],
    backup_exists: bool,
    backup_size: int,
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
        backup_size=backup_size if backup_exists else 0,
        backup_gone=bool(state.backup) and not backup_exists,
        findings=tuple(findings),
    )


def _grouped(findings: Sequence[str]) -> dict[str, list[str]]:
    """<findings> by kind: doctor's FAIL and warn lines, and the release's own ("")."""
    groups: dict[str, list[str]] = {FAIL: [], WARN: [], "": []}
    for line in findings:
        groups[next((kind for kind in (FAIL, WARN) if line.startswith(kind)), "")].append(line)
    return groups


def _counted(count: int, one: str, many: str) -> list[str]:
    return [(one if count == 1 else many).format(n=count)] if count else []


def found_after(findings: Sequence[str], before: Sequence[str]) -> tuple[str, ...]:
    """What the update found after the switch, as the summary says it (R45): the release's own
    findings (a job not reloaded, the check-in loop not restarted); each FAIL of bosun doctor's that
    its run while planning (<before>) did not show, by its text; how many it did show, which already
    failed before the update; and how many warnings doctor gave, which `bosun doctor` lists."""
    groups = _grouped(findings)
    shown = set(before)
    new = [line for line in groups[FAIL] if line not in shown]
    already = _counted(len(groups[FAIL]) - len(new), ALREADY, ALREADY)
    warnings = _counted(len(groups[WARN]), WARNING, WARNINGS)
    return (*groups[""], *new, *already, *warnings)


def of_record(
    state: UpdateState,
    agents: Sequence[tuple[str, str]],
    backup_exists: bool,
    backup_size: int,
    seconds: int,
) -> Summary:
    """build, with each part from the record of <state>, where every step kept what it learnt: the
    plan, the migrations that ran, bosun.env's change, the jobs reloaded, and what bosun doctor found
    after the switch and while planning (found_after). A part an older bosun's record lacks is left
    out."""
    applied = [found for line in state.migrated if (found := Applied.from_record(line))]
    plan = update_plan.from_record(state.plan)
    return build(
        state,
        plan,
        applied,
        state.config_changed,
        state.reloaded,
        found_after(state.findings, state.doctor_before),
        backup_exists,
        backup_size,
        seconds,
        agents=agents,
    )


def _doctor_line(kind: str, parts: Sequence[str]) -> str:
    head, *detail = (" ".join(part.split()) for part in parts)
    return f"{kind}: {' '.join([LOGGED.sub('', head, count=1), '; '.join(detail)]).rstrip()}"


def doctor_found(said: str) -> list[str]:
    """Each FAIL and warn line of bosun doctor's output <said>, as "FAIL: ..." or "warn: ...", with
    the detail indented below it joined onto it ("; " between its lines) and a log line's time and
    tag left out, so that two runs compare by what they say. An ok line, a section's title or a blank
    line ends a detail."""
    found: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in said.splitlines():
        item = DOCTOR_ITEM.fullmatch(line)
        detail = DOCTOR_DETAIL.fullmatch(line)
        if item:
            current = None if item["kind"] == "ok" else [item["what"]]
            if current is not None:
                found.append((item["kind"], current))
        elif detail and current is not None:
            current.append(detail["what"])
        else:
            current = None
    return [_doctor_line(kind, parts) for kind, parts in found]


def left_to_me(state: UpdateState | None, assigned: Collection[str]) -> list[tuple[str, str]]:
    """What the last update (<state>, its record) left me once it brings the windows along (LEFT_TO_ME):
    each change to make in one of my own files ("change", from migrations.USER_FILE_CHANGES when it
    planned), and each config key it added that bosun.env does not set (<assigned>: the keys it does),
    so is at its default ("key"), a shell name only. Nothing for an update that failed or was put back:
    the version it came from runs."""
    if state is None or state.step not in LEFT_TO_ME:
        return []
    keys = update_plan.from_record(state.plan).keys_added
    return [
        *(("change", change) for change in state.user_changes),
        *(("key", key) for key in keys if migrations.KEY.fullmatch(key) and key not in assigned),
    ]


# ---- the text ---------------------------------------------------------------------------------


def tilde(path: str, home: str) -> str:
    """<path> with ~ for the home folder <home> at its start ("": as it is)."""
    home = home.rstrip("/")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home) :]
    return path


def job_name(label: str, base: str) -> str:
    """A launchd job's name, from its label and the configured one (BOSUN_LAUNCHD_LABEL): the morning
    job's is the label itself, the others' add .<name> to it; a label of no such shape, or with no
    configured label to go by, is its own name."""
    if base and label == base:
        return MORNING
    return label.removeprefix(base + ".") if base and label.startswith(base + ".") else label


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


def _backup(summary: Summary, home: str) -> list[str]:
    if summary.backup_gone:
        return [f"Backup: {BACKUP_GONE}"]
    if not summary.backup:
        return []
    return [f"Backup: {tilde(summary.backup, home)} ({size(summary.backup_size)})"]


def render(summary: Summary, *, home: str = "", launchd_label: str = "") -> str:
    """The summary as the update prints it, and saves it (the design's example), a line each: the
    backup's path with ~ for <home>, the jobs named after <launchd_label> (BOSUN_LAUNCHD_LABEL)."""
    jobs = [job_name(label, launchd_label) for label in summary.jobs]
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
        *_backup(summary, home),
        f"Going back: {GOING_BACK}",
    ]
    return "\n".join(lines) + "\n"


def as_json(summary: Summary) -> dict[str, Any]:
    """The summary for --json: every field, the stores and the windows as objects, the backup's path
    absolute."""
    fields = dataclasses.asdict(summary).items()
    said = {name: list(value) if isinstance(value, tuple) else value for name, value in fields}
    return {**said, "data": dict(summary.data), "agents": dict(summary.agents)}
