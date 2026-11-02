"""What of bosun is on this machine, for `bosun uninstall`. It only reads. lib/uninstall_plan.py decides what
to do about it. Design: docs/design/uninstall.md."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import agent_changes
import init_detect
import init_plan
import uninstall_macos
from agent_files import read_object
from agent_state import AGENTS
from uninstall_plan import UNREADABLE, Checkout, Facts


TMUXINATOR_MARK = "bosun tmuxinator project"
GIT_CONFIG_MARK = "bosun.managed"
CACHES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", ".DS_Store"})


def entries(folder: Path) -> tuple[str, ...]:
    try:
        return tuple(sorted(entry.name for entry in folder.iterdir()))
    except OSError:
        return ()


def tmux_command(env: Mapping[str, str]) -> list[str]:
    """As lib/common.sh's tmux_cmd: without a UTF-8 locale, tmux would change what it prints."""
    socket = env.get("BOSUN_TMUX_SOCKET", "")
    return ["tmux", "-u", "-L", socket] if socket else ["tmux", "-u"]


def windows(env: Mapping[str, str]) -> tuple[str, ...]:
    session = env.get("BOSUN_SESSION_NAME", "bosun")
    command = [*tmux_command(env), "list-windows", "-t", f"={session}", "-F", "#{window_name}"]
    return tuple((init_detect.output_of(*command) or "").splitlines())


def git(root: Path, *args: str) -> str | None:
    return init_detect.output_of("git", "-C", str(root), *args)


def ignored_work(status: str) -> list[str]:
    """What git ignores in the checkout that is not a cache running bosun makes: a .env, a key, a state
    folder someone pointed inside it."""
    ignored = (line[3:] for line in status.splitlines() if line.startswith("!! "))
    return [
        path
        for path in ignored
        if not path.endswith(".pyc") and not CACHES.intersection(Path(path.rstrip("/")).parts)
    ]


def checkout_work(root: Path) -> str:
    """What deleting the checkout would lose, or "" if nothing. git that cannot answer counts as work."""
    status = git(root, "status", "--porcelain", "--ignored", "--untracked-files=all")
    stashes = git(root, "stash", "list")
    unpushed = git(root, "log", "--all", "--not", "--remotes", "--oneline", "-1")
    worktrees = git(root, "worktree", "list", "--porcelain")
    if status is None or stashes is None or unpushed is None or worktrees is None:
        return "a git repository that could not be checked"
    ignored = ignored_work(status)
    found = {
        "uncommitted changes": any(not line.startswith("!! ") for line in status.splitlines()),
        f"files git ignores ({', '.join(ignored[:3])})": bool(ignored),
        "stashed changes": bool(stashes.strip()),
        "commits not pushed": bool(unpushed.strip()),
        "other worktrees": worktrees.count("worktree ") > 1,
    }
    return next((what for what, there in found.items() if there), "")


def checkout(root: Path) -> Checkout:
    managed = (git(root, "config", "--get", GIT_CONFIG_MARK) or "").strip() == "true"
    return Checkout(str(root), managed, checkout_work(root) if managed else "")


def own_link(root: Path) -> str:
    """~/.local/bin/bosun, if it points at this checkout, or at nothing any more."""
    link = Path.home() / ".local" / "bin" / "bosun"
    if not link.is_symlink():
        return ""
    target = os.path.realpath(link)
    ours = target == os.path.realpath(root / "bin" / "bosun") or not os.path.exists(target)
    return str(link) if ours else ""


def other_install(root: Path) -> str:
    """The `bosun` that ~/.local/bin/bosun runs, if that is not this checkout."""
    link = Path.home() / ".local" / "bin" / "bosun"
    if not link.is_symlink() or own_link(root):
        return ""
    return os.path.realpath(link)


def old_files(env: Mapping[str, str]) -> tuple[str, ...]:
    """What earlier versions installed and nothing has removed yet."""
    session = env.get("BOSUN_SESSION_NAME", "bosun")
    project = Path.home() / ".config" / "tmuxinator" / f"{session}.yml"
    try:
        return (str(project),) if TMUXINATOR_MARK in project.read_text() else ()
    except OSError:
        return ()


def items_in(ledger: Path) -> int:
    if not ledger.exists():
        return 0
    items = (read_object(ledger) or {}).get("items")
    return len(items) if isinstance(items, dict) else UNREADABLE


def ledger_items(state_dir: Path) -> int:
    """The items in the ledger, or in its backup if that holds more: a ledger cut short has its data there."""
    counts = [items_in(state_dir / "ledger.json"), items_in(state_dir / "ledger.json.bak")]
    return UNREADABLE if UNREADABLE in counts else max(counts)


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text().splitlines() if line.strip())
    except (OSError, ValueError):
        return UNREADABLE


def knowledge_records(config_dir: Path) -> int:
    counts = [line_count(config_dir / "knowledge" / name) for name in ("entities.jsonl", "facts.jsonl")]
    return UNREADABLE if UNREADABLE in counts else sum(counts)


def trust_still_given(state_dir: Path) -> tuple[tuple[str, str], ...]:
    """What bosun marked, unless the agent says for certain it is gone: a config it cannot read may still
    hold it."""
    return tuple(
        (harness, folder)
        for harness, folder in agent_changes.trust_given(state_dir)
        if harness in AGENTS and AGENTS[harness].trust_recorded(folder) is not False
    )


def same(one: Path, other: Path) -> bool:
    try:
        return os.path.samefile(one, other)
    except OSError:
        return False


def holds(folder: Path, inner: Path) -> bool:
    """Whether <folder> is <inner> or a folder it is in, however its name is spelled: on macOS /users/me is
    your home folder too."""
    return any(same(folder, candidate) for candidate in (inner, *inner.parents))


def unsafe_folders(folders: list[Path], root: Path) -> tuple[str, ...]:
    """Bosun's folders that hold your home folder, and those that hold bosun's checkout (except the checkout
    itself, which holds only itself)."""
    home = Path.home()
    unsafe = [folder for folder in folders if holds(folder, home) or (folder != root and holds(folder, root))]
    return tuple(str(folder) for folder in unsafe)


def wake_at(env: Mapping[str, str]) -> str:
    """When bosun's wake runs, as pmset prints it: after the job's time, as install.sh sets it."""
    try:
        minutes = init_plan.minutes(env["BOSUN_START_TIME"]) + int(env["BOSUN_WAKE_DELAY_MINUTES"])
        return init_plan.pmset_label(init_plan.clock(minutes))
    except (KeyError, ValueError):
        return ""


def detect(env: Mapping[str, str], root: Path) -> Facts:
    config_dir = Path(env.get("BOSUN_CONFIG_DIR") or Path.home() / ".config" / "bosun")
    state_dir = Path(env.get("BOSUN_STATE_DIR") or Path.home() / ".local" / "state" / "bosun")
    log_dir = Path(env.get("BOSUN_LOG_DIR") or state_dir / "logs")
    session = env.get("BOSUN_SESSION_NAME", "bosun")
    return Facts(
        home=str(Path.home()),
        checkout=checkout(root),
        config_dir=str(config_dir),
        state_dir=str(state_dir),
        log_dir=str(log_dir),
        session=session,
        windows=windows(env),
        keep_awake=(state_dir / f"keep-awake-{session}.pid").exists(),
        jobs=uninstall_macos.jobs(env.get("BOSUN_JOB_LABELS", "").split(), str(root)),
        repeating=uninstall_macos.repeating_events(),
        wake_at=wake_at(env),
        days=env.get("BOSUN_DAYS", ""),
        link=own_link(root),
        old_files=old_files(env),
        trust=trust_still_given(state_dir),
        notes=tuple(note for note in agent_changes.notes(state_dir) if Path(note).exists()),
        state_entries=entries(state_dir),
        config_entries=entries(config_dir),
        log_entries=entries(log_dir),
        ledger_items=ledger_items(state_dir),
        knowledge_records=knowledge_records(config_dir),
        knowledge_files=len(entries(config_dir / "knowledge")),
        unsafe=unsafe_folders([state_dir, config_dir, log_dir, root], root),
    )
