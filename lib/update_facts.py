"""The facts `bosun update` gathers for its plan (docs/design/update.md, step 1): each version's
commit, formats, rules versions, config keys and prompts; the windows open and whether what they were
launched with changed; what the new version needs; a running console; the items handed out; and what
stops an update before it changes anything. The first update, which converts a checkout from before
versions, also asks whether it is a quiet moment (quiet_facts) and where the check-in loop runs.
update_plan makes the plan from them; lib/update.py carries it out. It reads, and asks the versions'
own commands; it changes nothing.

The folders and the tmux session come from the environment the wrapper (libexec/bosun-update) passes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import console_token
import install_paths
import knowledge
import knowledge_store
import launch_record
import ledger
import migrations
import signals
import update_agents
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
COMMAND_SECONDS = 600  # the most a bosun command the update runs may take
PREFLIGHT_NEXT = "\nWhat to do next:"
STORE_ERRORS = (ledger.LedgerError, knowledge_store.StoreError, KnowledgeError, JournalError)


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
    argv: list[str], *, stdin: str | None = None, detached: bool = False
) -> subprocess.CompletedProcess[str]:
    """<argv> run, its output kept, with <stdin> as its input (else this process's); one that outlasts
    COMMAND_SECONDS stopped, as a failure. <detached>: in a session of its own, so a Ctrl-C at the
    terminal never stops it half way (a restart that stopped an agent and never started it again)."""
    try:
        return subprocess.run(
            argv,
            input=stdin,
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
    root: Path, *args: str, stdin: str | None = None, detached: bool = False
) -> subprocess.CompletedProcess[str]:
    """`bosun <args>` of the version in <root> (command's <stdin> and <detached>)."""
    return command([str(root / "bin" / "bosun"), *args], stdin=stdin, detached=detached)


def shell(root: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """<script> run by bash with the config loaded by the version in <root> (lib/common.sh), <args>
    its "$@"."""
    return command(
        ["bash", "-c", f'source "$1/lib/common.sh"; load_config; shift; {script}', "_", str(root), *args]
    )


# ---- the facts each version gives the plan ------------------------------------------------------


def prompts(root: Path) -> dict[str, str]:
    """Each prompt file of the version in <root> (update_plan.PROMPTS), by its path there, and the
    SHA-256 of its content."""
    found: dict[str, str] = {}
    for pattern in update_plan.PROMPTS:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def config_keys(root: Path) -> frozenset[str]:
    """The config keys the version in <root> has a default for (lib/common.sh)."""
    try:
        return frozenset(CONFIG_KEY.findall((root / "lib" / "common.sh").read_text()))
    except OSError:
        return frozenset()


def _source_int(path: Path, name: str) -> int:
    """The whole number <name> is set to at the top level of the Python file <path>; 0 if it is not.
    Read from the source, so that no other version's code runs in this process."""
    try:
        found = re.search(rf"^{name} = (\d+)$", path.read_text(), re.MULTILINE)
    except OSError:
        return 0
    return int(found.group(1)) if found else 0


def rules(root: Path) -> dict[str, int]:
    """The rules version of each store's journal in the version in <root>."""
    return {
        "ledger": _source_int(root / "lib" / "ledger.py", "RULES_VERSION"),
        "knowledge": _source_int(root / "lib" / "knowledge.py", "KG_RULES_VERSION"),
    }


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


def launch_changed(root: Path, name: str, state_dir: Path, session: str) -> bool:
    """Whether window <name> would be launched now, by the version in <root>, with anything other
    than what its last launch recorded. `launch --fingerprint` of that version reads the files under
    the install from <root> itself, so this holds before the switch too (R35). A window with no
    record, or one the fingerprint refuses, counts as changed: what it runs with is unknown."""
    recorded = launch_record.recorded(state_dir, session, name)
    done = bosun(root, "launch", "--fingerprint", name)
    if recorded is None or done.returncode != 0:
        return True
    try:
        now = json.loads(done.stdout)
    except ValueError:
        return True
    return bool(recorded.get("fingerprint") != now)


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


def _window(root: Path, window: dict[str, Any], state_dir: Path, session: str) -> WindowFacts:
    name = str(window.get("name"))
    kind = _kind(window)
    changed = kind != "checkin" and launch_changed(root, name, state_dir, session)
    return WindowFacts(name, kind, status_of(window), changed)


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


def windows_pending(state_dir: Path) -> list[str]:
    """A blocker while the last update still brings windows along: a new update would bring along its
    own, and the windows still waited for would lose what was done to them, and their notes. A window
    with a status note (its own, or one written by hand) is answered: `bosun update` settles it first."""
    state = update_state.read(state_dir)
    if state is None or state.step != "agents":
        return []
    state = update_agents.settled(state, update_notes.Files(state_dir))
    if not state.pending:
        return []
    short = update_gate.SHORT
    blocker = (
        f"the last update ({state.from_commit[:short]} -> {state.to_commit[:short]}) still brings along "
        f"{', '.join(state.pending)}: let each answer its update prompt, end a wait by hand with bosun "
        "update note --window <w> --status, or take a turn at them with bosun update advance; then run "
        "bosun update again"
    )
    return [blocker]


def left_running(state_dir: Path, window: str) -> str:
    """What the last turn that ended in <window> left running, as the Stop hook recorded it
    (lib/signals.py): "shell `sleep 900`; subagent `...`", or "" when nothing is recorded."""
    for entry in signals.lines_last_first(signals.signals_path(state_dir, window)):
        if entry.get("event") == signals.STOP:
            return signals.describe(list(entry.get("running") or []))
    return ""


def window_facts(root: Path) -> list[WindowFacts]:
    """Each window open in the session: its kind, its agent's status, and whether what the version in
    <root> would launch it with differs from what it was launched with (launch_changed)."""
    where = folders()
    return [_window(root, w, where.state_dir, where.session) for w in open_windows(root)]


@dataclass(frozen=True)
class Quiet:
    """What the first update waits on before it converts (docs/design/update.md, "The first update
    converts, at a quiet moment"): each window's agent and its status, the checkin window's left out
    (its loop is bosun's, and stopped); how many minutes ago someone typed in a client attached to the
    session (None: none is attached); and BOSUN_ACTIVE_CLIENT_MINUTES."""

    statuses: tuple[tuple[str, str], ...]
    typed: int | None
    minutes: int


ACTIVE_MINUTES = 10  # lib/common.sh's default of BOSUN_ACTIVE_CLIENT_MINUTES, if it cannot be read


def _whole(text: str) -> int | None:
    return int(text) if text.strip().isdigit() else None


def quiet_facts(root: Path) -> Quiet:
    """The windows' statuses (`bosun sessions --json` of the version in <root>) and when someone last
    typed in the session (its minutes_since_client_input), for the conversion's wait."""
    statuses = tuple((str(w.get("name")), status_of(w)) for w in open_windows(root) if _kind(w) != "checkin")
    done = shell(root, 'printf "%s %s" "$BOSUN_ACTIVE_CLIENT_MINUTES" "$(minutes_since_client_input)"')
    minutes, _, typed = done.stdout.partition(" ")
    active = _whole(minutes)
    return Quiet(statuses, _whole(typed), ACTIVE_MINUTES if active is None else active)


def own_jobs_loaded(root: Path) -> list[str]:
    """The launchd jobs of bosun's own background work (the watchdog, learning) loaded now, as the
    version in <root> names them (lib/common.sh's loaded_own_jobs)."""
    return shell(root, "loaded_own_jobs").stdout.split()


def checkin_loop_pid(root: Path) -> int | None:
    """The pid of the check-in loop in the checkin window, if one runs there (lib/common.sh's
    checkin_loop_pid, as the version in <root> finds it)."""
    done = shell(
        root, 'read -r pane _ <<<"$(find_window checkin)"; [ -n "${pane:-}" ] && checkin_loop_pid "$pane"'
    )
    return _whole(done.stdout) if done.returncode == 0 else None


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


def in_flight(state_dir: Path, config_dir: Path) -> list[tuple[str, str]]:
    """Each item handed to a repo session whose work is not done, and its task type, where the
    session got the type's built-in text: a type the site replaces in $BOSUN_CONFIG_DIR/tasks/ is
    left out, since an update never changes the site's file."""
    try:
        items = ledger.load_at(state_dir)["items"]
    except ledger.LedgerError:
        return []  # store_formats names it
    found = []
    for item_id, item in items.items():
        delegation = item.get("delegation")
        if not isinstance(delegation, dict) or not ledger.is_open(item) or not ledger.awaiting_report(item):
            continue
        task = str(delegation.get("task") or "")
        if task and not (config_dir / "tasks" / f"{task}.md").is_file():
            found.append((item_id, task))
    return sorted(found)


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
    """A blocker unless the new version is a folder beside the install, and the version updated from
    is the install's: a folder beside it that the link leads to, or the checkout from before versions
    still at the install path, which the first update swaps for the link."""
    install = install_paths.install_path(new_root)
    beside = install != new_root and install_paths.install_path(old_root) == install
    if beside and (install.is_symlink() or (old_root == install and install.is_dir())):
        return []
    problem = (
        f"bosun update moves an install with its versions side by side ({install} a link to one of "
        f"them, or the checkout from before versions it converts); {old_root} and {new_root} are not that"
    )
    return [problem]


def config_renames(config_dir: Path) -> list[tuple[str, str]]:
    """Each config key bosun.env sets that this version renamed or removed, and its name now ("":
    removed)."""
    assigned = migrations.assigned_keys(config_dir)
    return sorted((old, new) for old, new in migrations.config_names().items() if old in assigned)


def jobs(root: Path) -> tuple[list[str], list[str]]:
    """The launchd jobs the version in <root> would reload (reload_changed_jobs --dry-run), and a
    blocker, in one line, if a job's plist cannot be rendered or renders invalid."""
    done = shell(root, "reload_changed_jobs --dry-run")
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
    <state_dir> and <config_dir>: update_plan.make_plan over the facts gathered here, with what else
    stops an update (a store whose format cannot be read, a link a backup cannot copy, versions not
    side by side) among its blockers."""
    old, new = live_facts(state_dir, config_dir, old_root), new_facts(new_root)
    cut_short, unreadable_kb = deletion_cut_short(state_dir, config_dir)
    reloads, unrenderable = jobs(new_root)
    plan = update_plan.make_plan(
        old,
        new,
        commits=commits(new_root, old.commit, new.commit),
        windows=window_facts(new_root),
        jobs=reloads,
        renames=config_renames(config_dir),
        user_changes=migrations.USER_FILE_CHANGES,
        in_flight=in_flight(state_dir, config_dir),
        missing=missing(new_root),
        deletion_cut_short=cut_short,
        console=console_running(state_dir),
        steps=_steps,
    )
    extra = [
        *store_formats(state_dir, config_dir)[1],
        *unreadable_kb,
        *unrenderable,
        *dangling_links(state_dir, config_dir),
        *install_problem(old_root, new_root),
        *windows_pending(state_dir),
    ]
    return dataclasses.replace(plan, blockers=(*plan.blockers, *extra))
