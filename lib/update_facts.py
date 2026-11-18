"""The facts `bosun update` gathers for its plan (docs/design/update.md, step 1): each version's
commit, formats, rules versions, config keys and prompts; the windows open and whether what they were
launched with changed; what the new version needs; a running console; the items handed out; and what
stops an update before it changes anything. update_plan makes the plan from them; lib/update.py
carries it out. It reads, and asks the versions' own commands; it changes nothing.

The folders and the tmux session come from the environment the wrapper (libexec/bosun-update) passes.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import console_token
import install_paths
import knowledge
import knowledge_store
import launch_record
import ledger
import migrations
import signals
import update_agents
import update_back_plan
import update_backups
import update_gate
import update_notes
import update_plan
import update_state
import versions
from journal import JournalError
from knowledge_model import KnowledgeError
from update_plan import Plan, VersionFacts, WindowFacts


# A key in lib/common.sh's defaults: an assignment indented by two (as the example configs' check reads them).
CONFIG_KEY = re.compile(r"^  (BOSUN_[A-Z0-9_]+)=", re.MULTILINE)
WINDOW_KINDS = {"orchestrator": "orchestrator", "checkin": "checkin"}  # any other is a repo session's
EXTRA = "extra"  # `bosun sessions`' kind of a window outside the window list
COMMAND_SECONDS = 600  # the most a bosun command the update runs may take
PREFLIGHT_NEXT = "\nWhat to do next:"
STORE_ERRORS = (ledger.LedgerError, knowledge_store.StoreError, KnowledgeError, JournalError)
SHORT = update_gate.SHORT


class UpdateRefused(Exception):
    """What stops an update before it changes anything, said in one line."""


@dataclass(frozen=True)
class Folders:
    """The state and config folders, and the tmux session, as `bosun update` passes them."""

    state_dir: Path
    config_dir: Path
    session: str


def folders() -> Folders:
    try:
        return Folders(
            Path(os.environ["BOSUN_STATE_DIR"]),
            Path(os.environ["BOSUN_CONFIG_DIR"]),
            os.environ.get("BOSUN_SESSION_NAME") or "bosun",
        )
    except KeyError as exc:
        raise UpdateRefused(f"{exc.args[0]} is not set: run it as bosun update") from exc


def command(
    argv: list[str],
    *,
    stdin: str | None = None,
    detached: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """<argv> run, its output kept, with <stdin> as its input (else this process's); one that outlasts
    COMMAND_SECONDS stopped, as a failure. <detached>: in a session of its own, so a Ctrl-C at the
    terminal never stops it half way (a restart that stopped an agent and never started it again).
    <env>: variables set for it on top of this process's."""
    try:
        return subprocess.run(
            argv,
            input=stdin,
            env={**os.environ, **env} if env else None,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=COMMAND_SECONDS,
            start_new_session=detached,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 1, "", f"{' '.join(argv[:3])} took over {COMMAND_SECONDS} s")


def bosun(
    root: Path,
    *args: str,
    stdin: str | None = None,
    detached: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """`bosun <args>` of the version in <root> (command's <stdin>, <detached> and <env>)."""
    return command([str(root / "bin" / "bosun"), *args], stdin=stdin, detached=detached, env=env)


def shell(
    root: Path, script: str, *args: str, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """<script> run by bash with the config loaded by the version in <root> (lib/common.sh), <args>
    its "$@", and <env> as overrides of what bosun.env sets."""
    return command(
        ["bash", "-c", f'source "$1/lib/common.sh"; load_config; shift; {script}', "_", str(root), *args],
        env=env,
    )


# ---- the facts each version gives the plan ------------------------------------------------------


class Source(Protocol):
    """Where a version's files are read from, as text a plan compares and never as code that runs: its
    folder (Folder), or bosun.git at its commit, never checked out (Committed)."""

    def read(self, path: str) -> bytes | None:
        """The file at <path> (relative to the version's top), byte for byte; None when there is none."""

    def names(self, pattern: str) -> list[str]:
        """The files whose path matches <pattern>, a glob over its parts ("orchestrator/tasks/*.md"),
        sorted."""


@dataclass(frozen=True)
class Folder:
    """A version's folder."""

    root: Path

    def read(self, path: str) -> bytes | None:
        try:
            return (self.root / path).read_bytes()
        except OSError:
            return None

    def names(self, pattern: str) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.glob(pattern) if p.is_file())


def _matches(name: str, pattern: str) -> bool:
    """Whether the path <name> matches the glob <pattern> part for part, as Path.glob matches it."""
    parts, wanted = name.split("/"), pattern.split("/")
    return len(parts) == len(wanted) and all(
        fnmatch.fnmatchcase(a, b) for a, b in zip(parts, wanted, strict=True)
    )


@dataclass(frozen=True)
class Committed:
    """A version as the git directory <repo> holds it at <commit>, never checked out (a dry run of `bosun
    update --back` whose older folder is gone adds none)."""

    repo: Path
    commit: str

    def read(self, path: str) -> bytes | None:
        return versions.show(self.repo, self.commit, path)

    def names(self, pattern: str) -> list[str]:
        return sorted(name for name in versions.tree(self.repo, self.commit) if _matches(name, pattern))


def prompts_in(source: Source) -> dict[str, str]:
    """Each prompt file of the version <source> holds (update_plan.PROMPTS), by its path there, and the
    SHA-256 of its content."""
    found: dict[str, str] = {}
    for pattern in update_plan.PROMPTS:
        for name in source.names(pattern):
            data = source.read(name)
            if data is not None:
                found[name] = hashlib.sha256(data).hexdigest()
    return found


def prompts(root: Path) -> dict[str, str]:
    """prompts_in the version in <root>."""
    return prompts_in(Folder(root))


def _text(source: Source, path: str) -> str:
    data = source.read(path)
    return data.decode("utf-8", "replace") if data is not None else ""


def keys_in(source: Source) -> frozenset[str]:
    """The config keys the version <source> holds has a default for (lib/common.sh)."""
    return frozenset(CONFIG_KEY.findall(_text(source, "lib/common.sh")))


def config_keys(root: Path) -> frozenset[str]:
    """keys_in the version in <root>."""
    return keys_in(Folder(root))


def _int_in(source: Source, path: str, name: str) -> int:
    """The whole number <name> is set to at the top level of the Python file <path> of <source>; 0 if it is
    not. Read as text, so that no other version's code runs in this process."""
    found = re.search(rf"^{name} = (\d+)$", _text(source, path), re.MULTILINE)
    return int(found.group(1)) if found else 0


def rules_in(source: Source) -> dict[str, int]:
    """The rules version of each store's journal in the version <source> holds."""
    return {
        "ledger": _int_in(source, "lib/ledger.py", "RULES_VERSION"),
        "knowledge": _int_in(source, "lib/knowledge.py", "KG_RULES_VERSION"),
    }


def rules(root: Path) -> dict[str, int]:
    """rules_in the version in <root>."""
    return rules_in(Folder(root))


def formats_in(source: Source) -> dict[str, int]:
    """The format of each store the version <source> holds writes (0: it says none), as rules_in reads
    its rules versions."""
    return {
        "ledger": _int_in(source, "lib/ledger.py", "LEDGER_FORMAT"),
        "knowledge": _int_in(source, "lib/knowledge_store.py", "KNOWLEDGE_FORMAT"),
    }


def source_formats(root: Path) -> dict[str, int]:
    """formats_in the version in <root>."""
    return formats_in(Folder(root))


def store_formats(state_dir: Path, config_dir: Path) -> tuple[dict[str, int], list[str]]:
    """Each store's format on disk (0: none yet), and a blocker for each one whose format cannot be
    read: a store this code cannot tell the format of is never migrated, nor backed up and put back."""
    ctx = migrations.Context(state_dir, config_dir)
    found: dict[str, int] = {}
    problems: list[str] = []
    for store in migrations.FORMATS:
        try:
            found[store] = migrations.store_format(ctx, store)
        except STORE_ERRORS as exc:
            problems.append(f"the {store}'s format cannot be read: {exc}")
    return found, problems


def live_facts(state_dir: Path, config_dir: Path, old_root: Path) -> VersionFacts:
    """The running version's facts: its code in <old_root>, its stores' formats on disk (a store
    whose format cannot be read left out: store_formats says why)."""
    return VersionFacts(
        commit=versions.commit_of(old_root),
        formats=store_formats(state_dir, config_dir)[0],
        rules=rules(old_root),
        keys=config_keys(old_root),
        prompts=prompts(old_root),
    )


def new_facts(root: Path) -> VersionFacts:
    """The new version's facts: <root> is this code's folder, whose formats are migrations.FORMATS."""
    return VersionFacts(
        commit=versions.commit_of(root),
        formats=dict(migrations.FORMATS),
        rules=rules(root),
        keys=config_keys(root),
        prompts=prompts(root),
    )


REFUSED = "its launch gave no reason"  # a refused fingerprint that said nothing
# `launch --fingerprint`'s exit when the launch would refuse (LAUNCH_REFUSED in lib/common.sh)
FINGERPRINT_REFUSED = 4


def launch_compared(
    root: Path, name: str, state_dir: Path, session: str, kept: Mapping[str, str] | None = None
) -> tuple[bool, str]:
    """Whether window <name> would be launched now, by the version in <root>, with anything other
    than what its last launch recorded; and why that launch would not start, when the fingerprint
    reports a refusal (FINGERPRINT_REFUSED; its last stderr line: the reason the launch would record),
    else "" (any other failure, a crash or a timeout, counts as changed only). `launch --fingerprint`
    of that version reads the files under the install from <root> itself, so this holds before the
    switch too, and gets the config values the update will write to bosun.env (<kept>,
    migrations.keys_to_keep) as it will have them. One of bosun's windows with no record, or one the
    fingerprint refuses, counts as changed: what it runs with is unknown (a window bosun did not start
    never gets here: _window)."""
    recorded = launch_record.recorded(state_dir, session, name)
    done = bosun(root, "launch", "--fingerprint", name, env=kept)
    if done.returncode == FINGERPRINT_REFUSED:
        said = [line.strip() for line in done.stderr.splitlines() if line.strip()]
        return True, said[-1] if said else REFUSED
    if recorded is None or done.returncode != 0:
        return True, ""
    try:
        now = json.loads(done.stdout)
    except ValueError:
        return True, ""
    return bool(recorded.get("fingerprint") != now), ""


def _kind(window: dict[str, Any]) -> str:
    return WINDOW_KINDS.get(str(window.get("kind")), "repo")


def status_of(window: dict[str, Any]) -> str:
    """The status of the agent in <window> (as `bosun sessions --json` lists it): exited when none runs."""
    agent = window.get("agent")
    return str(agent.get("status") or "unknown") if isinstance(agent, dict) else "exited"


def on_another_conversation(window: dict[str, Any]) -> bool:
    """Whether the agent in <window> (as `bosun sessions --json` lists it) runs another conversation
    than the one its next launch takes up (`conversation`): an orchestrator still on yesterday's, a
    repo window with a fresh one pending. One whose conversation's name is unknown runs its own."""
    agent = window.get("agent")
    running = str(agent.get("name") or "") if isinstance(agent, dict) else ""
    return bool(running) and running != str(window.get("conversation") or running)


def _window(
    root: Path, window: dict[str, Any], state_dir: Path, session: str, kept: Mapping[str, str]
) -> WindowFacts:
    """What the plan knows of <window>. One outside the window list with no launch recorded (a shell,
    a Claude started by hand) was not started by bosun, and is left as it is."""
    name = str(window.get("name"))
    kind = _kind(window)
    status = status_of(window)
    if window.get("kind") == EXTRA and launch_record.recorded(state_dir, session, name) is None:
        return WindowFacts(name, kind, status, launch_changed=False, started_by_bosun=False)
    if kind == "checkin":
        return WindowFacts(name, kind, status, launch_changed=False)
    changed, refused = launch_compared(root, name, state_dir, session, kept)
    return WindowFacts(name, kind, status, changed, refused=refused)


def open_windows(root: Path) -> list[dict[str, Any]]:
    """The windows open in the session (`bosun sessions --json` of the version in <root>)."""
    try:
        listed = json.loads(bosun(root, "sessions", "--json").stdout or "[]")
    except ValueError:
        return []
    return [w for w in listed if isinstance(w, dict) and w.get("window")] if isinstance(listed, list) else []


def loop_runs(root: Path) -> bool:
    """Whether the checkin window, which runs the check-in loop, is open in the session."""
    return any(_kind(w) == "checkin" for w in open_windows(root))


def owed_last(state_dir: Path) -> update_state.UpdateState | None:
    """The last update whose windows the next one takes over (settled): the record at `agents` (an
    answered window, by its own status note or one written by hand, settled first: `bosun update` settles
    it before it plans), or the one an update that ended with bosun as it was carried
    (update_state.owing); or, while an update runs, the one it carried (its plan made again after the
    switch, at a step before `agents`; never one past them, which took them over already). None when no
    window is owed."""
    state = update_state.read(state_dir)
    before_agents = update_state.STEPS[: update_state.STEPS.index("agents")]
    if state is not None and state.step in before_agents and state.carried:
        state = update_state.from_object(state.carried)
    last = update_state.owing(state)
    return update_agents.settled(last, update_notes.Files(state_dir)) if last is not None else None


def owed_windows(state_dir: Path, plan: Plan) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """The windows the last update still waits for (owed_last) that an update with <plan> takes over, each
    with what it is owed (update_agents.owed), by the decision bring_along makes over the plan's
    treatments (update_agents.taken_or_left); and those it leaves alone (open, given nothing by either). A
    window whose launch on the new version would not start (plan.refused) is neither: its line is its own."""
    last = owed_last(state_dir)
    if last is None:
        return (), ()
    taken, left = update_agents.taken_or_left(plan.windows, dict(plan.kinds), last, dict(plan.refused))
    return tuple((window, update_agents.owed(last, window).value) for window in taken), left


def left_running(state_dir: Path, window: str) -> str:
    """What the last turn that ended in <window> left running, as the Stop hook recorded it
    (lib/signals.py): "shell `sleep 900`; subagent `...`", or "" when nothing is recorded."""
    for entry in signals.lines_last_first(signals.signals_path(state_dir, window)):
        if entry.get("event") == signals.STOP:
            return signals.describe(list(entry.get("running") or []))
    return ""


def window_facts(root: Path, kept: Mapping[str, str] | None = None) -> list[WindowFacts]:
    """Each window open in the session: its kind, its agent's status, and whether what the version in
    <root> would launch it with, once the update wrote <kept> to bosun.env, differs from what it was
    launched with, or its launch there would be refused (launch_compared)."""
    where = folders()
    return [_window(root, w, where.state_dir, where.session, kept or {}) for w in open_windows(root)]


def missing(root: Path) -> list[str]:
    """What the version in <root> needs and this Mac lacks (its `bosun preflight`), as blockers;
    nothing with BOSUN_SKIP_PREFLIGHT=1, as for `bosun init`."""
    if os.environ.get("BOSUN_SKIP_PREFLIGHT") == "1":
        return []
    done = bosun(root, "preflight", "--quiet")
    if done.returncode == 0:
        return []
    said = done.stdout.split(PREFLIGHT_NEXT, 1)[0]
    lines = [" ".join(line.split()) for line in said.splitlines() if line.startswith("  ")]
    if not lines:
        last = (done.stdout + done.stderr).strip().splitlines()[-1:] or ["it failed"]
        return [f"the new version's preflight: {last[0]}"]
    return [f"the new version needs what this Mac lacks: {line} (bosun preflight says how)" for line in lines]


def console_running(state_dir: Path) -> bool:
    """Whether a console runs on the stores in <state_dir> (it holds a lock in its state folder)."""
    return console_token.running(state_dir)


def knowledge_on(root: Path) -> bool:
    """Whether the install has the ontology on (BOSUN_KNOWLEDGE_ENABLED=1), as the version in <root>
    reads the config: only then does the orchestrator's policy hold its rules, to re-read when changed."""
    done = shell(root, 'printf "%s" "$BOSUN_KNOWLEDGE_ENABLED"')
    return done.returncode == 0 and done.stdout.strip() == "1"


def console_job(root: Path) -> bool:
    """Whether the version in <root> would restart the console after the switch: its launchd job is
    loaded and wanted (lib/common.sh's console_restartable)."""
    return shell(root, "console_restartable").returncode == 0


def in_flight(state_dir: Path, config_dir: Path) -> list[tuple[str, tuple[str, ...]]]:
    """Each item handed to a repo session whose work is not done, with the task types whose built-in
    text its session got: those its task names ("review,fix" for `bosun delegate review,fix`) but a
    type the site replaces in $BOSUN_CONFIG_DIR/tasks/, as an update never changes the site's file. An
    item whose every type is the site's stays, with none: the rules every task gets reached it too."""
    try:
        items = ledger.load_at(state_dir)["items"]
    except ledger.LedgerError:
        return []  # store_formats names it
    found = []
    for item_id, item in items.items():
        named = _task_types(item)
        if named:
            built_in = tuple(kind for kind in named if not (config_dir / "tasks" / f"{kind}.md").is_file())
            found.append((item_id, built_in))
    return sorted(found)


def _task_types(item: dict[str, Any]) -> list[str]:
    """The task types <item> was handed to a repo session with, while its work is not done; none for
    an item not handed out, done, or reported on, nor for work a window took on directly
    (ledger.DIRECT_TASK), which no task text came with."""
    delegation = item.get("delegation")
    if not isinstance(delegation, dict) or not ledger.is_open(item) or not ledger.awaiting_report(item):
        return []
    kinds = (kind.strip() for kind in str(delegation.get("task") or "").split(","))
    return [kind for kind in kinds if kind and kind != ledger.DIRECT_TASK]


def deletion_cut_short(state_dir: Path, config_dir: Path) -> tuple[bool, list[str]]:
    """Whether a deletion of a person in the knowledge base was cut short (knowledge.
    deletion_cut_short), and a blocker if the knowledge base cannot be read to tell."""
    knowledge.configure(config_dir / update_gate.KNOWLEDGE_DIR, git=False, state_dir=str(state_dir))
    try:
        return knowledge.deletion_cut_short(), []
    except (*STORE_ERRORS, OSError) as exc:
        return False, [f"the knowledge base cannot be read to check for a deletion cut short: {exc}"]


def dangling_links(state_dir: Path, config_dir: Path) -> list[str]:
    """A blocker for each link among what a backup copies (update_backups.backup_set) that leads
    nowhere: the backup could not copy it, and would fail inside the freeze."""
    return [
        f"{path} is a link to {os.readlink(path)}, which is not there, so no backup can copy it: "
        "remove the link, or put back what it leads to"
        for _relative, path in update_backups.backup_set(state_dir, config_dir)
        if path.is_symlink() and not path.exists()
    ]


def install_problem(old_root: Path, new_root: Path) -> list[str]:
    """A blocker unless the install is a link and both versions are its own (install_paths): the new one
    a folder beside it, and the one updated from. A checkout at the install path (one an older get.sh
    made, from before versions side by side) is not that: it is installed again, never updated
    (docs/updating.md)."""
    install = install_paths.install_path(new_root)
    beside = install != new_root and install_paths.install_path(old_root) == install
    if beside and install.is_symlink():
        return []
    problem = (
        f"bosun update moves an install with its versions side by side ({install} a link to one of "
        f"them); {old_root} and {new_root} are not that"
    )
    return [problem]


def config_renames(config_dir: Path) -> list[tuple[str, str]]:
    """Each config key bosun.env sets that this version renamed or removed, and its name now ("":
    removed)."""
    assigned = migrations.assigned_keys(config_dir)
    return sorted((old, new) for old, new in migrations.config_names().items() if old in assigned)


def jobs(root: Path, kept: Mapping[str, str] | None = None) -> tuple[list[str], list[str]]:
    """The launchd jobs the version in <root> would reload (reload_changed_jobs --dry-run) once the update
    wrote <kept> to bosun.env (an older install's BOSUN_START_AT_LOGIN=0: no login job), and a blocker, in
    one line, if a job's plist cannot be rendered or renders invalid."""
    done = shell(root, "reload_changed_jobs --dry-run", env=kept)
    if done.returncode != 0:
        said = "; ".join(line.strip() for line in done.stderr.splitlines() if line.strip()) or "it failed"
        return [], [f"the new version cannot render its launchd jobs: {said}"]
    return done.stdout.split(), []


def launchd_label(root: Path) -> str:
    """The morning job's launchd label as the version in <root> reads the config (BOSUN_LAUNCHD_LABEL;
    the other jobs' labels add .watchdog and .learn to it); "" if it cannot be read."""
    done = shell(root, 'printf "%s" "$BOSUN_LAUNCHD_LABEL"')
    return done.stdout.strip() if done.returncode == 0 else ""


def commits(root: Path, old: str, new: str) -> list[str]:
    """The commits from <old> to <new>, newest first, as "<short> <subject>"."""
    done = versions.git("-C", str(root), "log", "--format=%h %s", f"{old}..{new}")
    return done.stdout.splitlines() if done.returncode == 0 else []


def _steps(store: str, found: int, target: int) -> list[tuple[int, int]]:
    return [(m.from_version, m.to_version) for m in migrations.path(store, found, target)]


def plan_for(old_root: Path, new_root: Path, state_dir: Path, config_dir: Path) -> Plan:
    """The plan from the version in <old_root> to the one in <new_root> (this code), for the data in
    <state_dir> and <config_dir>: plan_from over the facts gathered here, with the config the update
    writes, and a line when `bosun update --back` left the version it goes to (left_by_back)."""
    old, new = live_facts(state_dir, config_dir, old_root), new_facts(new_root)
    # What the update writes to bosun.env, which each window's launch is compared with: an older
    # install's orchestrator that keeps every connector is no launch change.
    kept = migrations.keys_to_keep(config_dir, new.keys - old.keys)
    plan = plan_from(
        Versions(old, new, old_root, new_root),
        state_dir,
        config_dir,
        kept=kept,
        renames=config_renames(config_dir),
        user_changes=migrations.USER_FILE_CHANGES,
    )
    return dataclasses.replace(
        plan,
        kept=tuple(kept.items()),
        unwritten=migrations.keys_unwritten(config_dir, new.keys - old.keys),
        left_by_back=left_by_back(state_dir, new.commit),
    )


@dataclass(frozen=True)
class Versions:
    """The two versions a plan goes between: their facts, and their folders."""

    old: VersionFacts
    new: VersionFacts
    old_root: Path
    new_root: Path


def plan_from(
    versions_: Versions,
    state_dir: Path,
    config_dir: Path,
    *,
    kept: Mapping[str, str],
    renames: Sequence[tuple[str, str]],
    user_changes: Sequence[tuple[str, str]],
) -> Plan:
    """update_plan.make_plan over <versions_>' facts and those gathered here (each window compared with
    what the new version would launch once <kept> is in bosun.env), with what else stops it (a store
    whose format cannot be read, a link a backup cannot copy, versions not side by side) among its
    blockers, the windows the last update still waits for, and the jobs the release unloads. An update's
    plan (plan_for) and a back's (lib/update_back.py) are made by it."""
    old, new, old_root, new_root = versions_.old, versions_.new, versions_.old_root, versions_.new_root
    cut_short, unreadable_kb = deletion_cut_short(state_dir, config_dir)
    reloads, unrenderable = jobs(new_root, kept)
    plan = update_plan.make_plan(
        old,
        new,
        commits=commits(new_root, old.commit, new.commit),
        windows=window_facts(new_root, kept),
        jobs=reloads,
        renames=renames,
        user_changes=user_changes,
        in_flight=in_flight(state_dir, config_dir),
        missing=missing(new_root),
        deletion_cut_short=cut_short,
        console=console_running(state_dir),
        console_job=console_job(new_root),
        knowledge_on=knowledge_on(new_root),
        steps=_steps,
    )
    extra = [
        *store_formats(state_dir, config_dir)[1],
        *unreadable_kb,
        *unrenderable,
        *dangling_links(state_dir, config_dir),
        *install_problem(old_root, new_root),
    ]
    owed, owed_left = owed_windows(state_dir, plan)
    return dataclasses.replace(
        plan,
        blockers=(*plan.blockers, *extra),
        owed=owed,
        owed_left=owed_left,
        unload=tuple(dropped_jobs(old_root, new_root)),
    )


# ---- what every update's release and the wrapper check, and a back's own facts ---------------------

THIS = Path(__file__).resolve().parents[1]  # this code's folder, whose helpers act for any version


# What a version's jobs run, as lib/common.sh's job_kinds says. One from before it lists job_labels: each a
# job that runs bosun, but the menu bar app's Open at Login job, where it has app_label (released ones do).
JOB_KINDS = (
    "if declare -F job_kinds >/dev/null; then job_kinds; "
    'else app="$(declare -F app_label >/dev/null && app_label)"; for l in $(job_labels); do '
    'k=bosun; [ -n "$app" ] && [ "$l" = "$app" ] && k=app; printf "%s\\t%s\\n" "$l" "$k"; done; fi'
)
BOSUN_JOB = "bosun"  # a job that runs a bosun command; `app` runs the menu bar app's own binary


def job_kinds(root: Path) -> dict[str, str] | None:
    """Each launchd job's label the version in <root> lists and what it runs (lib/common.sh's job_kinds:
    `bosun` or `app`); None when it cannot say (its shell failed, or listed nothing)."""
    done = shell(root, JOB_KINDS)
    found = dict(line.split("\t", 1) for line in done.stdout.splitlines() if "\t" in line)
    return found if done.returncode == 0 and found else None


def dropped_jobs(old_root: Path, new_root: Path) -> list[str]:
    """The launchd jobs installed (a plist, or loaded) that run a bosun command (job_kinds), whose label the
    version in <old_root> lists and the one in <new_root> does not: each would run a command that version may
    lack, so the release unloads it (this code's unload_jobs, asked with --dry-run here). None when either
    version cannot say what it lists: a version that cannot be asked must never have every job taken for
    dropped. The menu bar app's Open at Login job is never one: it runs the app, and no reload makes it
    again."""
    old, new = job_kinds(old_root), job_kinds(new_root)
    if old is None or new is None:
        return []
    labels = [label for label, kind in old.items() if kind == BOSUN_JOB and label not in new]
    if not labels:
        return []
    return shell(THIS, 'unload_jobs --dry-run "$@"', *labels).stdout.split()


def older_than_data(target: Path, name: str, state_dir: Path, config_dir: Path, installed: Path) -> list[str]:
    """Why the version in <target> (called <name>) would read the data older than it is, a line each
    (update_back_plan.older_than_data): a store in a format, or under rules, older than the data's (its
    formats on disk, the rules of the version <installed>). What the wrapper checks before it hands over."""
    return update_back_plan.older_than_data(
        f"{name} ({versions.commit_of(target)[:SHORT]})",
        store_formats(state_dir, config_dir)[0],
        source_formats(target),
        rules(installed),
        rules(target),
    )


def left_by_back(state_dir: Path, commit: str) -> str:
    """When `bosun update --back` left the version at <commit>, as its record says, or "": the next update's
    plan says it, going forward to that version again."""
    state = update_state.read(state_dir)
    if state is None or state.kind != update_state.BACK or state.from_commit != commit:
        return ""
    return update_back_plan.when(state.started_at) if state.step in update_state.PAST_CHECKS else ""
