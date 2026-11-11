"""What of bosun is on this machine, for `bosun uninstall`. It only reads. lib/uninstall_plan.py decides what
to do about it. Design: docs/design/uninstall.md.

What deleting a checkout or a version would lose is lib/checkout_work.py's, which `bosun update` goes by
too."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import agent_changes
import checkout_work
import init_detect
import init_plan
import install_paths
import uninstall_macos
import versions
from agent_files import read_object
from agent_state import AGENTS
from checkout_work import Checkout, entries
from uninstall_plan import UNREADABLE, Facts


TMUXINATOR_MARK = "bosun tmuxinator project"


def tmux_command(env: Mapping[str, str]) -> list[str]:
    """As lib/common.sh's tmux_cmd: without a UTF-8 locale, tmux would change what it prints."""
    socket = env.get("BOSUN_TMUX_SOCKET", "")
    return ["tmux", "-u", "-L", socket] if socket else ["tmux", "-u"]


def windows(env: Mapping[str, str]) -> tuple[str, ...]:
    session = env.get("BOSUN_SESSION_NAME", "bosun")
    command = [*tmux_command(env), "list-windows", "-t", f"={session}", "-F", "#{window_name}"]
    return tuple((init_detect.output_of(*command) or "").splitlines())


def panes(env: Mapping[str, str]) -> dict[int, str]:
    """The process of each pane of bosun's tmux session, with its window's name (none when it does not
    run): what runs in its windows descends from these, in their sessions."""
    session = env.get("BOSUN_SESSION_NAME", "bosun")
    command = [
        *tmux_command(env),
        "list-panes",
        "-s",
        "-t",
        f"={session}",
        "-F",
        "#{pane_pid}\t#{window_name}",
    ]
    found = {}
    for line in (init_detect.output_of(*command) or "").splitlines():
        pid, _, window = line.partition("\t")
        if pid.isdigit():
            found[int(pid)] = window
    return found


# ---- versions side by side ------------------------------------------------------------------------------


def legacy_folders(folder: Path) -> list[Path]:
    """The checkouts from before versions the first update kept in <folder>."""
    names = (name for name in entries(folder) if name.startswith(versions.LEGACY_PREFIX))
    return [folder / name for name in names if (folder / name).is_dir() and not (folder / name).is_symlink()]


@dataclass(frozen=True)
class Install:
    """Where this bosun is installed, for the plan: what holds the code (the checkout, or the versions
    folder), each version and checkout from before versions in that folder, the install link, the bosun to
    run again, and the temporary links a switch cut short left beside the install."""

    code: Checkout
    bosun: str
    versions: tuple[Checkout, ...] = ()
    legacy: tuple[Checkout, ...] = ()
    link: str = ""
    leftover_links: tuple[str, ...] = ()


def install_roots(root: Path) -> tuple[str, ...]:
    """Every folder whose bin/bosun is this install's: the checkout <root>, or with versions side by side the
    install link, <root> and every folder beside it (its versions, and checkouts from before versions)."""
    install = install_paths.install_path(root)
    if install == root:
        return (str(root),)
    folder = install_paths.versions_dir(install)
    beside = [folder / name for name in entries(folder) if name != install_paths.REPO]
    return tuple(
        dict.fromkeys(str(path) for path in (install, root, *beside) if path == install or path.is_dir())
    )


def side_by_side(root: Path, install: Path) -> Install:
    """The install of the version in <root>, a folder beside the install path <install>."""
    folder = install_paths.versions_dir(install)
    mark = checkout_work.git(folder / install_paths.REPO, "config", "--get", checkout_work.GIT_CONFIG_MARK)
    managed = (mark or "").strip() == "true"
    found = checkout_work.version_folders(folder)
    legacy = legacy_folders(folder)
    # A version is the repository's: whether the installer made them is the versions folder's to say, once.
    each = tuple(
        Checkout(str(path), True, checkout_work.version_work(path) if managed else "") for path in found
    )
    work = checkout_work.versions_work(folder, found, [path.name for path in legacy]) if managed else ""
    linked = install.is_symlink()
    return Install(
        code=Checkout(str(folder), managed, work),
        bosun=str(install_paths.install_bin(root) if linked else root / "bin" / "bosun"),
        versions=each,
        legacy=tuple(checkout_work.checkout(path) for path in legacy),
        link=str(install) if linked else "",
        leftover_links=tuple(str(path) for path in versions.temporary_links(install)),
    )


def installed(root: Path) -> Install:
    """Where the bosun in <root> is installed: a checkout, or versions side by side."""
    install = install_paths.install_path(root)
    if install == root:
        return Install(code=checkout_work.checkout(root), bosun=str(root / "bin" / "bosun"))
    return side_by_side(root, install)


def own_link(roots: Sequence[str]) -> str:
    """~/.local/bin/bosun, if it runs one of this install's folders (<roots>, install_roots), or nothing any
    more."""
    link = Path.home() / ".local" / "bin" / "bosun"
    if not link.is_symlink():
        return ""
    target = os.path.realpath(link)
    ours = not os.path.exists(target) or any(
        target == os.path.realpath(Path(root) / "bin" / "bosun") for root in roots
    )
    return str(link) if ours else ""


def other_install(roots: Sequence[str]) -> str:
    """The `bosun` that ~/.local/bin/bosun runs, if that is not this install's."""
    link = Path.home() / ".local" / "bin" / "bosun"
    if not link.is_symlink() or own_link(roots):
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


def setup_description(harness: str) -> str:
    agent = AGENTS.get(harness)
    setup = agent.PROFILE.setup if agent else None
    return setup.description if setup else f"the setup bosun made for {harness}"


def setups_made(state_dir: Path) -> tuple[tuple[str, str], ...]:
    """(harness, what it is called) for each one-time setup bosun recorded: one of an agent bosun no longer
    knows is named by the agent, and uninstall says why it cannot be undone."""
    return tuple((harness, setup_description(harness)) for harness in agent_changes.setups_made(state_dir))


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
    """Bosun's folders that hold your home folder, and those that hold bosun's code, <root> (except that
    folder itself, which holds only itself)."""
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
    found, roots = installed(root), install_roots(root)
    code = Path(found.code.root)
    return Facts(
        home=str(Path.home()),
        checkout=found.code,
        config_dir=str(config_dir),
        state_dir=str(state_dir),
        log_dir=str(log_dir),
        bosun=found.bosun,
        session=session,
        windows=windows(env),
        keep_awake=(state_dir / f"keep-awake-{session}.pid").exists(),
        jobs=uninstall_macos.jobs(env.get("BOSUN_JOB_LABELS", "").split(), roots),
        repeating=uninstall_macos.repeating_events(),
        wake_at=wake_at(env),
        days=env.get("BOSUN_DAYS", ""),
        link=own_link(roots),
        old_files=old_files(env),
        trust=trust_still_given(state_dir),
        setups=setups_made(state_dir),
        notes=tuple(note for note in agent_changes.notes(state_dir) if Path(note).exists()),
        state_entries=entries(state_dir),
        config_entries=entries(config_dir),
        log_entries=entries(log_dir),
        ledger_items=ledger_items(state_dir),
        knowledge_records=knowledge_records(config_dir),
        knowledge_files=len(entries(config_dir / "knowledge")),
        unsafe=unsafe_folders([state_dir, config_dir, log_dir, code], code),
        install_link=found.link,
        versions=found.versions,
        legacy=found.legacy,
        leftover_links=found.leftover_links,
    )
