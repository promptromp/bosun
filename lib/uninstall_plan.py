"""What `bosun uninstall` does on this machine: from the facts (lib/uninstall_detect.py) and your answers to
the steps, what stays and why, and how that reads. Pure; lib/uninstall.py asks and carries it out. Design:
docs/design/uninstall.md.

A file goes only if it is listed here as bosun's, and only from a folder that is bosun's own: its name says
bosun, or everything in it is listed here. A state or config folder can be set to anywhere, so "everything in
it" is not safe to delete, and neither is a folder that holds someone else's files under bosun's names.

The install goes last, and whole or not at all: the checkout, or with versions side by side the versions
folder (its repository and every version in it, a worktree each), the checkouts from before versions kept
there, and the link that led to the version in use. Each is checked as a checkout is, and anything of yours
in any of them keeps the lot.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from checkout_work import Checkout, why_kept
from init_plan import tilde


# What a file is decides what happens to it.
WORKING = "working"  # bosun's own: it goes
LEDGER = "ledger"  # your records: you decide
KNOWLEDGE = "knowledge"  # your records: you decide
BACKUPS = (
    "backups"  # the update's backups: each holds the ledger and the knowledge base, so they go with either
)
SETTINGS = "settings"  # what you configured: it stays, unless --purge
OURS_TO_LEAVE = "leave"  # in bosun's folder, but not part of running it: it always stays
UNKNOWN = "unknown"  # not listed below: it always stays

STATE_FILES = (
    ("ledger.json", LEDGER),
    ("journal", LEDGER),  # each machine's changes to the ledger: your records
    # This machine's id in both stores' journals (the ledger's, the knowledge base's), classed with the
    # ledger: --delete-ledger deletes it even when the knowledge base stays, which then carries on as
    # a new replica from its next write.
    ("replica.json", LEDGER),
    ("ledger.json.bak", LEDGER),
    ("ledger.md", LEDGER),
    ("learn", LEDGER),  # what learning found and what you decided, which a later run goes by
    # The update's backups (lib/update_backups.py): each holds the whole ledger and knowledge base, so they
    # go when either is deleted (wanted_gone). A deletion of a person deletes them all.
    ("backups", BACKUPS),
    ("ledger.json.tmp", WORKING),  # what a write cut short left
    (".replica.json.*.tmp", WORKING),  # what making replica.json left when it was cut short
    ("ledger.lock", WORKING),
    ("update.json", WORKING),  # the update's record (docs/design/update.md)
    ("update.json.tmp", WORKING),
    ("update.lock", WORKING),
    ("update", WORKING),
    (".*.restoring", WORKING),  # what an update's restore was writing, cut short (lib/update_backups.py)
    ("agent-changes.json", WORKING),
    (".agent-changes.json.*", WORKING),
    ("alerted-*", WORKING),  # what the check-in loop left, before the watchdog
    ("alerts", WORKING),
    ("codex-sessions.json", WORKING),
    (".codex-sessions.json.*", WORKING),
    ("connectors.json", WORKING),  # the connectors' URLs the last launch found (lib/connectors.py)
    (".connectors.json.*", WORKING),  # what a write cut short left
    ("console", WORKING),
    ("conversations", WORKING),
    ("dry-runs", WORKING),
    ("events", WORKING),
    ("impact", WORKING),
    ("keep-awake-*.pid", WORKING),
    ("launches", WORKING),  # what each window was launched with (lib/launch_record.py)
    ("logs", WORKING),
    ("login.json", WORKING),
    ("login.json.tmp", WORKING),
    ("login.lock", WORKING),
    ("prompts", WORKING),
    ("signals", WORKING),
    ("slack-watch", WORKING),
    ("stopped-*", WORKING),
    ("wake-test.env", WORKING),
    ("watchdog.json", WORKING),
    ("watchdog.json.tmp", WORKING),
    ("watchdog.lock", WORKING),
    (".DS_Store", WORKING),  # Finder's, if you ever looked in the folder
)
CONFIG_FILES = (
    ("knowledge", KNOWLEDGE),
    ("bosun.env", SETTINGS),
    ("sessions.yml", SETTINGS),
    ("setup.sh", SETTINGS),
    ("policy.md", SETTINGS),
    ("learn.md", SETTINGS),
    ("tasks", SETTINGS),
    ("workstreams.json", SETTINGS),
    ("*.pre-init-*", SETTINGS),  # what `bosun init` backed up
    ("bosun.env.pre-update-*", SETTINGS),  # what `bosun update` backed up
    (".bosun.env.*.tmp", WORKING),  # what `bosun update` was writing, cut short (bosun.env, its copy)
    (".*.restoring", WORKING),  # what an update's restore was writing, cut short (lib/update_backups.py)
    ("forbidden-terms.txt", OURS_TO_LEAVE),  # guards commits to this repo; bosun runs without it
    (".DS_Store", WORKING),
)
# A log folder of its own (BOSUN_LOG_DIR outside the state folder): bosun's log names, not every *.log.
LOG_FILES = tuple(
    (pattern, WORKING)
    for pattern in (
        "morning-*.log",
        "checkin-*.log",
        "learn-*.log",
        "hook-*.log*",
        "impact-*.log",
        "launch-*.log",
        "launchd.out",
        "launchd.err",
        "*.launchd.out",
        "*.launchd.err",
        ".DS_Store",
    )
)

# The kinds of step, in the order they run. The wake comes first because it alone may ask for something, so
# the rest runs unattended; the jobs before the windows, because the watchdog would bring those back.
WAKE = "wake"
JOB = "job"
STOP = "stop"
TRUST = "trust"
NOTE = "note"
LINK = "link"
FILE = "file"
CHECKOUT = "checkout"

CANCEL_WAKE = "sudo pmset repeat cancel"
INSTALL_LINE = "curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | sh"
UNREADABLE = -1  # records that are there but cannot be read, so they may hold anything
AT_HOME = "it is where your home folder is, so it is not touched"
# How `pmset -g sched` says the days, for the sets it has a word for.
PMSET_DAYS = {"MTWRFSU": "every day", "MTWRF": "weekdays", "SU": "weekends"}
NAMES_SHOWN = 5  # of the files a folder holds that bosun does not know


@dataclass(frozen=True)
class Job:
    label: str
    plist: str  # "" if it has none on disk
    loaded: bool


@dataclass(frozen=True)
class Facts:
    home: str
    # What holds the code: the checkout, or with versions side by side the versions folder (managed: its
    # repository has bosun.managed; work: what the repository holds that is nowhere else).
    checkout: Checkout
    config_dir: str
    state_dir: str
    log_dir: str
    bosun: str  # the bosun to run again: through the install link, or this checkout's
    session: str = "bosun"
    windows: tuple[str, ...] = ()
    keep_awake: bool = False
    jobs: tuple[Job, ...] = ()
    repeating: tuple[str, ...] = ()  # the repeating power events
    wake_at: str = ""  # the time bosun's wake runs, as pmset prints it
    days: str = ""  # BOSUN_DAYS
    link: str = ""  # ~/.local/bin/bosun, if it is this checkout's
    old_files: tuple[str, ...] = ()  # what earlier versions installed
    trust: tuple[tuple[str, str], ...] = ()  # (harness, folder) bosun marked, and the agent may still trust
    notes: tuple[str, ...] = ()  # memory notes bosun wrote that are still there
    state_entries: tuple[str, ...] = ()  # the names directly in each folder
    config_entries: tuple[str, ...] = ()
    log_entries: tuple[str, ...] = ()  # only for a log folder outside the state folder
    ledger_items: int = 0  # in ledger.json or its backup, whichever holds more
    knowledge_records: int = 0
    knowledge_files: int = 0  # what the knowledge folder holds at all
    unsafe: tuple[str, ...] = ()  # folders that hold your home folder or bosun's checkout
    install_link: str = ""  # versions side by side: the install path, the link to the version in use
    versions: tuple[Checkout, ...] = ()  # each version's folder in the versions folder, and its own work
    legacy: tuple[Checkout, ...] = ()  # the checkouts from before versions kept there (legacy-*)
    leftover_links: tuple[str, ...] = ()  # temporary links beside the install a switch cut short left


@dataclass(frozen=True)
class Choices:
    keep_ledger: bool = True
    keep_knowledge: bool = True
    purge: bool = False


@dataclass(frozen=True)
class Step:
    kind: str
    target: str
    harness: str = ""  # for TRUST
    by_hand: str = ""  # what to run if it could not be done


@dataclass(frozen=True)
class Stay:
    path: str
    why: str
    record: bool = False  # your ledger or knowledge graph, which you may be asked about


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]
    install: tuple[Step, ...]  # last of all: what is carrying this out runs from it
    stays: tuple[Stay, ...]

    @property
    def empty(self) -> bool:
        return not self.steps and not self.install


@dataclass(frozen=True)
class Question:
    key: str  # LEDGER or KNOWLEDGE
    text: str


def kind_of(name: str, table: tuple[tuple[str, str], ...]) -> str:
    return next((kind for pattern, kind in table if fnmatch.fnmatchcase(name, pattern)), UNKNOWN)


def quoted(path: str) -> str:
    return shlex.quote(path)


Processes = Sequence[tuple[int, int, int | None]]  # pid, parent, session id (None: unknown)
NOTE_COMMAND_CHARS = 80


def _with_descendants(table: Processes, roots: Mapping[int, str]) -> dict[int, str]:
    """<roots> still in <table>, and whatever they started, however deep, each with its root's window."""
    parents = {pid: ppid for pid, ppid, _ in table}
    found = {pid: window for pid, window in roots.items() if pid in parents}
    while more := {pid: found[ppid] for pid, ppid in parents.items() if ppid in found and pid not in found}:
        found.update(more)
    return found


def ran_in_windows(table: Processes, sessions: Mapping[int, str], panes: Mapping[int, str]) -> dict[int, str]:
    """What runs in the windows bosun is about to stop, from a process table, each with the window it runs
    in: each process in one of their tmux panes' sessions (<sessions>: session id to window), and whatever
    the panes (<panes>: pid to window) started, however deep, in any session (a background command an agent
    runs in a session of its own)."""
    in_sessions = {pid: sessions[sid] for pid, _, sid in table if sid is not None and sid in sessions}
    return _with_descendants(table, {**in_sessions, **panes})


def still_running(table: Processes, sessions: Collection[int], watched: Mapping[int, str]) -> dict[int, str]:
    """What of the windows bosun stopped still runs, each with its window: each of <watched> (what ran in
    them at the stop, and what this found since) still there, and what those start apart from the windows,
    in a session of its own, with whatever that starts. It sees only what it finds through a process it
    watches: Claude Code, ending as its window stops, starts its async hooks (SessionEnd's `bosun hook
    signal`, which wrote in the state folder after its deletion) in a session of their own while it still
    runs, so they are found and waited for; a worker bosun's own hooks detach (`codex-turn-end`'s) is
    launchd's within milliseconds and is not. What an agent starts in its window's own session as it ends
    is not waited for: Claude Code's `caffeinate -i -t 300` outlives it by five minutes."""
    apart = {pid for pid, _, sid in table if sid is None or sid not in sessions}
    reached = _with_descendants(table, watched)
    return {pid: window for pid, window in reached.items() if pid in watched or pid in apart}


def still_running_note(window: str, pid: int, command: str) -> str:
    """What uninstall says of a process a stopped window started that still runs once it stops waiting."""
    shown = command if len(command) <= NOTE_COMMAND_CHARS else command[: NOTE_COMMAND_CHARS - 1] + "…"
    return f"still running, started in the {window} window: {shown} (pid {pid}); bosun did not stop it."


def safe_folder(path: str, facts: Facts) -> bool:
    """Whether bosun may delete anything in <path>: never your home folder, a folder that holds it, or one
    that holds bosun's checkout. lib/uninstall_detect.py also compares the folders themselves, which catches
    another spelling of the same path."""
    if not path or not os.path.isabs(path) or path in facts.unsafe:
        return False
    folder, home = Path(os.path.realpath(path)), Path(os.path.realpath(facts.home))
    return folder != home and folder not in home.parents


def folders(facts: Facts) -> list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]]:
    """Each of bosun's folders, what is in it, and the table that says what that is."""
    found = [
        (facts.state_dir, facts.state_entries, STATE_FILES),
        (facts.config_dir, facts.config_entries, CONFIG_FILES),
    ]
    if not logs_inside_state(facts):
        found.append((facts.log_dir, facts.log_entries, LOG_FILES))
    return found


def unknown_in(names: tuple[str, ...], table: tuple[tuple[str, str], ...]) -> list[str]:
    return [name for name in names if kind_of(name, table) == UNKNOWN]


def own_folder(folder: str, names: tuple[str, ...], table: tuple[tuple[str, str], ...], facts: Facts) -> bool:
    """A folder bosun may delete in: safe, and named for bosun or holding nothing bosun does not know."""
    named_for_bosun = "bosun" in Path(folder).name.lower()
    return safe_folder(folder, facts) and (named_for_bosun or not unknown_in(names, table))


def owns_state(facts: Facts) -> bool:
    return own_folder(facts.state_dir, facts.state_entries, STATE_FILES, facts)


def owns_config(facts: Facts) -> bool:
    return own_folder(facts.config_dir, facts.config_entries, CONFIG_FILES, facts)


def logs_inside_state(facts: Facts) -> bool:
    return Path(facts.log_dir) == Path(facts.state_dir) / "logs"


def has_ledger(facts: Facts) -> bool:
    return owns_state(facts) and (facts.ledger_items != 0 or "learn" in facts.state_entries)


def has_knowledge(facts: Facts) -> bool:
    return owns_config(facts) and facts.knowledge_files > 0


def wake_is_bosuns(facts: Facts) -> bool:
    """Bosun's wake, and nothing else that cancelling would take with it: pmset only cancels them all. The
    time must be bosun's to the minute, and the days too where pmset has a word for them."""
    if len(facts.repeating) != 1 or not facts.wake_at:
        return False
    event = facts.repeating[0].lower()
    at_its_time = re.search(rf"(?<![\d:]){re.escape(facts.wake_at.lower())}(?!\w)", event) is not None
    days = PMSET_DAYS.get(facts.days, "")
    return "wake" in event and at_its_time and days in event


def wake_steps(facts: Facts) -> list[Step]:
    return [Step(WAKE, facts.repeating[0], by_hand=CANCEL_WAKE)] if wake_is_bosuns(facts) else []


def job_steps(facts: Facts) -> list[Step]:
    def by_hand(job: Job) -> str:
        unload = f"launchctl bootout gui/$(id -u)/{quoted(job.label)}"
        return f"{unload}; rm -f {quoted(job.plist)}" if job.plist else unload

    return [Step(JOB, job.label, by_hand=by_hand(job)) for job in facts.jobs]


def stop_steps(facts: Facts) -> list[Step]:
    if not facts.windows and not facts.keep_awake:
        return []
    stop = f"{quoted(facts.bosun)} stop"
    return [Step(STOP, ", ".join(facts.windows), by_hand=stop)]


def agent_steps(facts: Facts) -> list[Step]:
    trust = [Step(TRUST, folder, harness) for harness, folder in facts.trust]
    return [*trust, *(Step(NOTE, note, by_hand=f"rm {quoted(note)}") for note in facts.notes)]


def link_steps(facts: Facts) -> list[Step]:
    links = [facts.link] if facts.link else []
    paths = (*links, *facts.old_files, *facts.leftover_links)
    return [Step(LINK, path, by_hand=f"rm {quoted(path)}") for path in paths]


def wanted_gone(kind: str, facts: Facts, choices: Choices) -> bool:
    """Whether a file of this kind goes, on these answers."""
    ledger_goes = not (choices.keep_ledger and has_ledger(facts))
    knowledge_goes = not (choices.keep_knowledge and has_knowledge(facts))
    return {
        WORKING: True,
        LEDGER: ledger_goes,
        KNOWLEDGE: knowledge_goes,
        BACKUPS: ledger_goes or knowledge_goes,
        SETTINGS: choices.purge,
    }.get(kind, False)


def goes(kind: str, name: str, facts: Facts, choices: Choices) -> bool:
    """The record of what bosun changed in the agents' files stays as long as something it lists is still
    there: it is how a second run finishes the job."""
    if name.startswith(("agent-changes.json", ".agent-changes.json")) and (facts.trust or facts.notes):
        return False
    return wanted_gone(kind, facts, choices)


def file_steps(facts: Facts, choices: Choices) -> list[Step]:
    return [
        Step(FILE, str(Path(folder) / name), by_hand=f"rm -rf {quoted(str(Path(folder) / name))}")
        for folder, names, table in folders(facts)
        if own_folder(folder, names, table, facts)
        for name in names
        if goes(kind_of(name, table), name, facts, choices)
    ]


def kept_because(piece: Checkout, facts: Facts) -> str:
    return why_kept(piece) or ("" if safe_folder(piece.root, facts) else AT_HOME)


def pieces(facts: Facts) -> tuple[Checkout, ...]:
    """What the install is made of, each checked as a checkout: the checkouts from before versions, the
    versions, and what holds them (or the checkout alone)."""
    return (*facts.legacy, *facts.versions, facts.checkout)


def install_steps(facts: Facts) -> tuple[Step, ...]:
    """The install, last of all and all of it or none: the checkouts from before versions, the folder
    that holds the code (the checkout, or the versions folder with every version in it), then the link
    that led into it."""
    if any(kept_because(piece, facts) for piece in pieces(facts)):
        return ()
    folders = (*(piece.root for piece in facts.legacy), facts.checkout.root)
    links = (facts.install_link,) if facts.install_link else ()
    return (
        *(Step(CHECKOUT, folder, by_hand=f"rm -rf {quoted(folder)}") for folder in folders),
        *(Step(LINK, link, by_hand=f"rm {quoted(link)}") for link in links),
    )


def install_stays(facts: Facts) -> list[Stay]:
    """Why the install stays, when it does: each piece with a reason of its own, the folder that holds them,
    and the link that leads into it."""
    held = [Stay(piece.root, why) for piece in pieces(facts) if (why := kept_because(piece, facts))]
    if not held:
        return []
    if facts.checkout.root not in {stay.path for stay in held}:
        names = ", ".join(Path(stay.path).name for stay in held)
        which = "stays" if len(held) == 1 else "stay"
        held.append(Stay(facts.checkout.root, f"it holds {names}, which {which}"))
    if facts.install_link:
        why = f"it leads into {tilde(facts.checkout.root, facts.home)}, which stays"
        held.append(Stay(facts.install_link, why))
    return held


def settings_stay(facts: Facts, choices: Choices) -> list[Stay]:
    names = [name for name in facts.config_entries if kind_of(name, CONFIG_FILES) == SETTINGS]
    gone = choices.purge and owns_config(facts)
    return [Stay(facts.config_dir, f"your settings: {', '.join(names)}")] if names and not gone else []


def records_stay(facts: Facts, choices: Choices) -> list[Stay]:
    stays = []
    if choices.keep_ledger and has_ledger(facts):
        where = str(Path(facts.state_dir) / "ledger.json")
        stays.append(Stay(where, f"your ledger and learning history{ledger_count(facts)}", record=True))
    if choices.keep_knowledge and has_knowledge(facts):
        where = str(Path(facts.config_dir) / "knowledge")
        stays.append(Stay(where, f"your knowledge graph{knowledge_count(facts)}", record=True))
    return stays


def folder_stays(facts: Facts) -> list[Stay]:
    """What is in bosun's folders but is not bosun's to delete: the folder whole, if it is not bosun's own."""
    stays = []
    for folder, names, table in folders(facts):
        unknown = unknown_in(names, table)
        if not names:
            continue
        if not safe_folder(folder, facts):
            stays.append(
                Stay(folder, "it holds your home folder or bosun's checkout, so nothing in it is touched")
            )
        elif not own_folder(folder, names, table, facts):
            listed = ", ".join(unknown[:NAMES_SHOWN]) + (", ..." if len(unknown) > NAMES_SHOWN else "")
            stays.append(
                Stay(folder, f"it also holds what bosun does not know ({listed}), so it is left whole")
            )
        else:
            why = "bosun does not know this file, so it leaves it"
            stays += [Stay(str(Path(folder) / name), why) for name in unknown]
            guards = "it guards your commits to this repo, not bosun's running"
            stays += [
                Stay(str(Path(folder) / name), guards)
                for name in names
                if kind_of(name, table) == OURS_TO_LEAVE
            ]
    return stays


def wake_stays(facts: Facts) -> list[Stay]:
    if not facts.repeating or wake_is_bosuns(facts):
        return []
    wake = facts.wake_at or "no set time"
    why = f"a repeating power event that may not be bosun's, whose wake runs at {wake}"
    return [Stay(event, f"{why}. {CANCEL_WAKE} cancels every one of them") for event in facts.repeating]


def build_plan(facts: Facts, choices: Choices) -> Plan:
    return Plan(
        (
            *wake_steps(facts),
            *job_steps(facts),
            *stop_steps(facts),
            *agent_steps(facts),
            *link_steps(facts),
            *file_steps(facts, choices),
        ),
        install_steps(facts),
        (
            *settings_stay(facts, choices),
            *records_stay(facts, choices),
            *folder_stays(facts),
            *install_stays(facts),
            *wake_stays(facts),
        ),
    )


def counted(count: int, name: str) -> str:
    return f"{count} {name}" if count == 1 else f"{count} {name}s"


def ledger_count(facts: Facts) -> str:
    return f" ({counted(facts.ledger_items, 'item')})" if facts.ledger_items > 0 else ""


def knowledge_count(facts: Facts) -> str:
    return f" ({counted(facts.knowledge_records, 'record')})" if facts.knowledge_records > 0 else ""


def questions(facts: Facts, choices: Choices) -> tuple[Question, ...]:
    """What is left to ask, given <choices> already made on the command line. Records with nothing in them
    are not worth a question."""
    found = []
    if choices.keep_ledger and has_ledger(facts):
        where = tilde(facts.state_dir, facts.home)
        text = f"Keep your ledger and learning history{ledger_count(facts)}, in {where}?"
        found.append(Question(LEDGER, text))
    if choices.keep_knowledge and has_knowledge(facts):
        where = tilde(str(Path(facts.config_dir) / "knowledge"), facts.home)
        text = f"Keep your knowledge graph{knowledge_count(facts)}, in {where}?"
        found.append(Question(KNOWLEDGE, text))
    return tuple(found)


def describe(step: Step, home: str) -> str:
    """What a step removes, in a line."""
    target = tilde(step.target, home)
    return {
        WAKE: f"the repeating wake, {step.target}",
        JOB: f"launchd job {step.target}",
        STOP: f"the running windows: {step.target or 'none, only the keep-awake'}",
        TRUST: f"the trust bosun gave {target} ({step.harness})",
        NOTE: f"the memory note bosun learned, {target}",
    }.get(step.kind, target)


# What to know before it happens, for the plan.
BEFORE_IT_HAPPENS = {
    WAKE: f" ({CANCEL_WAKE}, which may ask for your password)",
    STOP: " (any work in them stops)",
}


def collapsed(steps: tuple[Step, ...], home: str) -> list[str]:
    """Each step's line, with the files of one folder together."""
    lines = [
        describe(step, home) + BEFORE_IT_HAPPENS.get(step.kind, "") for step in steps if step.kind != FILE
    ]
    folders: dict[str, list[str]] = {}
    for step in steps:
        if step.kind == FILE:
            path = Path(step.target)
            folders.setdefault(str(path.parent), []).append(path.name)
    return [*lines, *(f"in {tilde(folder, home)}: {', '.join(names)}" for folder, names in folders.items())]


def stay_lines(stays: tuple[Stay, ...], home: str) -> list[str]:
    return [f"  {tilde(stay.path, home)}\n      {stay.why}" for stay in stays]


def plan_sections(plan: Plan, facts: Facts, *, asking: bool) -> list[str]:
    steps = (*plan.steps, *plan.install)
    lines = ["It removes:", *(f"  {line}" for line in collapsed(steps, facts.home))]
    asked = [stay for stay in plan.stays if stay.record] if asking else []
    stays = tuple(stay for stay in plan.stays if stay not in asked)
    if stays:
        lines += ["", "It leaves:", *stay_lines(stays, facts.home)]
    if asked:
        lines += ["", "It will ask whether to keep:", *stay_lines(tuple(asked), facts.home)]
    return lines


def render_plan(plan: Plan, facts: Facts, *, asking: bool = True) -> str:
    """What it will do. With <asking>, your records are something it asks about, not something decided."""
    if plan.empty:
        lines = ["Nothing of bosun is left to remove on this Mac."]
        if plan.stays:
            lines += ["", "Still here:", *stay_lines(plan.stays, facts.home)]
        return "\n".join(lines) + "\n"
    intro = [
        "This takes bosun off this Mac. Your repos, branches and pull requests are not touched, nor are",
        "your conversations in Claude Code and Codex. Its /bosun:chores command and its hooks exist only in",
        "the windows bosun starts, so nothing of them stays behind in either agent.",
        "",
    ]
    return "\n".join([*intro, *plan_sections(plan, facts, asking=asking)]) + "\n"


def shell_path(path: str, home: str) -> str:
    """<path> as you would type it: ~ for your home folder, quoted where the shell would split it."""
    shown = tilde(path, home)
    if shown == "~" or shown.startswith("~/"):
        rest = shown[2:]
        return f"~/{shlex.quote(rest)}" if rest else "~"
    return shlex.quote(shown)


def delete_hint(facts: Facts) -> str:
    """How to delete what stays in bosun's own folders, if you want them gone too: only for a folder that
    holds nothing but what bosun knows, so the line cannot take anything of anyone else's."""
    kept = [
        folder
        for folder, names, table in folders(facts)[:2]
        if names and own_folder(folder, names, table, facts) and not unknown_in(names, table)
    ]
    if not kept:
        return ""
    return "To delete these too: rm -rf " + " ".join(shell_path(folder, facts.home) for folder in kept)


def render_left(left: tuple[Step, ...], facts: Facts) -> list[str]:
    again = f"{shell_path(facts.bosun, facts.home)} uninstall"
    lines = [f"Some of it could not be removed. To try again: {again}", "Or by hand:"]
    for step in left:
        lines.append(f"  {describe(step, facts.home)}")
        if step.by_hand:
            lines.append(f"      {step.by_hand}")
    return lines


def render_done(after: Plan, left: tuple[Step, ...], facts: Facts, *, inside_checkout: bool) -> str:
    lines = render_left(left, facts) if left else ["bosun is uninstalled."]
    if after.stays:
        lines += ["", "Still here, and yours:", *stay_lines(after.stays, facts.home)]
        hint = delete_hint(facts)
        if hint:
            lines += ["", hint]
        lines += ["", f"Installing again picks up what is still here:\n  {INSTALL_LINE}"]
    if inside_checkout:
        lines += ["", "Your shell is still in the folder that was removed: cd somewhere else."]
    return "\n".join(lines) + "\n"
