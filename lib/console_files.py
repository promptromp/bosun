"""The console's file reads for the Library and the learning reports: which files it may show, built
at request time from fixed roots, and reading one of them. Design: docs/design/console.md
(sections 4 and 5).

A request names a file only by an opaque key (lib/console_library.py's file_key); the server looks
the key up in the list built here and never turns anything from a request into a path. The list:
- repo: orchestrator/policy.md, plugin/commands/chores.md, tasks/*.md, learn.md, slack-watch.md,
  and orchestrator/settings.json when it is the settings in use;
- site ($BOSUN_CONFIG_DIR): policy.md, learn.md, tasks/*.md, bosun.env, and the files
  BOSUN_SESSIONS_FILE and BOSUN_ORCHESTRATOR_SETTINGS name;
- memory, only when BOSUN_CONSOLE_MEMORY_DIRS lists folders (colon-separated): their .md files.
Learning's reports (`report.md` of a run under $BOSUN_STATE_DIR/learn) get keys the same way.
Never listed: the forbidden-terms list, and any file whose name has `.bak` in it (backups). A file
found by a folder listing (tasks/, memory) must stay inside its folder once symlinks are resolved;
one that leads out is skipped. Files over MAX_READ_BYTES are listed but not shown.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import console_library
import console_state
from console_library import MEMORY, REPO, SITE, Entry


ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR = ROOT / "orchestrator"
REPO_FILES = (
    ("Orchestrator", ORCHESTRATOR / "policy.md"),
    ("Orchestrator", ORCHESTRATOR / "plugin" / "commands" / "chores.md"),
    ("Prompts", ORCHESTRATOR / "learn.md"),
    ("Prompts", ORCHESTRATOR / "slack-watch.md"),
)
SITE_FILES = ("policy.md", "learn.md", "bosun.env")
TASK_GROUP = "Task types"
LEARN = "learn"  # the root learning's report keys are made under
SITE_GROUP = "Site overlay"
SITE_TASK_GROUP = "Site task types"
MEMORY_GROUP = "Memory"
NEVER = ("forbidden-terms",)  # never listed, whatever the folder
BACKUP_MARK = ".bak"
MAX_MEMORY_FILES = 500
MAX_READ_BYTES = console_state.MAX_READ_BYTES
TOO_LARGE = "too large to show here"
UNREADABLE = "could not be read"


@dataclass(frozen=True)
class Settings:
    """Where the Library looks (passed in by `bosun console`)."""

    config_dir: Path | None = None
    sessions_file: Path | None = None
    orchestrator_settings: Path | None = None
    memory_dirs: tuple[Path, ...] = field(default_factory=tuple)
    repo: Path = ROOT

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        def path(name: str) -> Path | None:
            value = env.get(name, "")
            return Path(value).expanduser() if value else None

        memory = env.get("BOSUN_CONSOLE_MEMORY_DIRS", "")
        return cls(
            config_dir=path("BOSUN_CONFIG_DIR"),
            sessions_file=path("BOSUN_SESSIONS_FILE"),
            orchestrator_settings=path("BOSUN_ORCHESTRATOR_SETTINGS"),
            memory_dirs=tuple(Path(p).expanduser() for p in memory.split(os.pathsep) if p.strip()),
        )


def listable(path: Path) -> bool:
    name = path.name.lower()
    return BACKUP_MARK not in name and not any(word in name for word in NEVER)


def is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def same_file(one: Path, other: Path) -> bool:
    try:
        return one.resolve() == other.resolve()
    except OSError:
        return False


def entry(root: str, group: str, path: Path, base: Path, badge: str = "") -> Entry:
    try:
        name = str(path.relative_to(base))
    except ValueError:
        name = path.name
    return Entry(console_library.file_key(root, path), root, group, name, path, badge)


def folder_files(folder: Path, pattern: str) -> list[Path]:
    """The files in <folder> matching <pattern>, listable and inside it once symlinks are resolved."""
    if not folder.is_dir():
        return []
    return [
        p
        for p in sorted(folder.glob(pattern))
        if is_file(p) and listable(p) and console_state.within(p, folder)
    ]


def repo_entries(settings: Settings, site_tasks: Path | None) -> Iterator[Entry]:
    orchestrator = settings.repo / "orchestrator"
    for group, path in REPO_FILES:
        repo_path = orchestrator / path.relative_to(ORCHESTRATOR)
        if is_file(repo_path):
            yield entry(REPO, group, repo_path, settings.repo)
    tasks_dir = orchestrator / "tasks"
    tasks = folder_files(tasks_dir, "*.md")
    badges = console_library.task_badges(tasks_dir, site_tasks, tasks)
    for path in tasks:
        yield entry(REPO, TASK_GROUP, path, settings.repo, badges[path])
    repo_settings = orchestrator / "settings.json"
    if is_file(repo_settings) and same_file(settings.orchestrator_settings or repo_settings, repo_settings):
        yield entry(REPO, "Orchestrator", repo_settings, settings.repo, "in use")


def site_entries(settings: Settings) -> Iterator[Entry]:
    config = settings.config_dir
    if config is None or not config.is_dir():
        return
    for name in SITE_FILES:
        path = config / name
        if is_file(path) and listable(path):
            yield entry(SITE, SITE_GROUP, path, config)
    yield from named_site_files(settings, config)
    tasks_dir = config / "tasks"
    tasks = folder_files(tasks_dir, "*.md")
    badges = console_library.task_badges(settings.repo / "orchestrator" / "tasks", tasks_dir, tasks)
    for path in tasks:
        yield entry(SITE, SITE_TASK_GROUP, path, config, badges[path])


def named_site_files(settings: Settings, config: Path) -> Iterator[Entry]:
    """The sessions file and the orchestrator settings the config names (unless that is the repo's)."""
    repo_settings = settings.repo / "orchestrator" / "settings.json"
    for extra in (settings.sessions_file, settings.orchestrator_settings):
        if extra is not None and is_file(extra) and listable(extra) and not same_file(extra, repo_settings):
            yield entry(SITE, SITE_GROUP, extra, config, "in use")


def memory_entries(settings: Settings) -> Iterator[Entry]:
    for folder in settings.memory_dirs:
        for path in folder_files(folder, "**/*.md")[:MAX_MEMORY_FILES]:
            yield entry(MEMORY, f"{MEMORY_GROUP}: {folder.parent.name}/{folder.name}", path, folder)


def catalog(settings: Settings) -> list[Entry]:
    """Every file the Library may show, built now: repo, site overlay, then memory (if on)."""
    config = settings.config_dir
    site_tasks = config / "tasks" if config is not None and (config / "tasks").is_dir() else None
    found = [*repo_entries(settings, site_tasks), *site_entries(settings), *memory_entries(settings)]
    return list({e.key: e for e in found}.values())  # a file named twice is listed once


def read(path: Path) -> tuple[str | None, str]:
    """The file's text, or None and why not (too large, unreadable)."""
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return None, TOO_LARGE
        return path.read_text(errors="replace"), ""
    except OSError:
        return None, UNREADABLE


# ---- learning's reports ------------------------------------------------------------------------


def learn_reports(files: console_state.LearnFiles) -> dict[str, Path]:
    """key -> report.md, for each run whose report is under the learn state folder."""
    found = {}
    for run in files.runs:
        path = console_state.report_path(files, run)
        if path is not None:
            found[console_library.file_key(LEARN, path)] = path
    return found


def report_keys(reports: Mapping[str, Path]) -> dict[str, str]:
    """run folder -> its report's key (what lib/console_learn.py's runs carry)."""
    return {str(path.parent): key for key, path in reports.items()}
