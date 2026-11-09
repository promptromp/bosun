"""What `bosun init` finds on this Mac, read-only: the facts lib/init_plan.py recommends from.

Which repos you use Claude Code in and when (lib/claude_state.py reads the transcripts), or the git
repos in the usual folders if there is no history; your tools and logins; the MCP servers and connectors
`claude mcp list` shows; AWS SSO profiles; whether this is a laptop; the repeating wake pmset has; and
the config bosun already has.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from itertools import chain
from pathlib import Path
from typing import Any

import agent_state
import claude_state
import init_plan
from init_plan import Facts, RepoUse


HISTORY_DAYS = 30
COMMAND_TIMEOUT_SECONDS = 15
USUAL_REPO_FOLDERS = ("Development", "dev", "code", "src", "projects", "work", "repos", "git")
TEMPORARY_FOLDERS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")  # noqa: S108 (only compared)
MAX_FOUND_REPOS = 20
GITHUB_REMOTE = re.compile(r"github\.com[:/]([^/\s]+)/")
GITDIR = re.compile(r"gitdir:\s*(.+)")
YAML_TO_JSON = "require 'yaml'; require 'json'; puts JSON.generate(YAML.safe_load(File.read(ARGV[0])) || {})"


def output_of(*command: str) -> str | None:
    """A command's stdout if it succeeded, else None (also if it is not installed)."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def main_checkout(git_file: Path) -> Path | None:
    """The main checkout of the worktree this .git file belongs to; None if it is not a worktree's."""
    try:
        match = GITDIR.match(git_file.read_text())
    except OSError:
        return None
    gitdir = (
        git_file.parent / match.group(1).strip() if match else None
    )  # an absolute gitdir replaces the base
    if gitdir is None or gitdir.parent.name != "worktrees":
        return None
    return gitdir.parents[2]


def repo_root(directory: Path) -> Path | None:
    """The git repository <directory> is in: a worktree counts as its main checkout."""
    for folder in (directory, *directory.parents):
        git = folder / ".git"
        if git.is_dir():
            return folder
        if git.is_file():
            return main_checkout(git) or folder
    return None


def usable_repo(root: Path | None, home: Path) -> bool:
    return root is not None and root != home and root.is_dir() and not str(root).startswith(TEMPORARY_FOLDERS)


def repos_from_history(uses: Iterable[str], home: Path) -> list[tuple[Path, int]]:
    """The repos of these prompts' folders, with how many prompts each got, most first."""
    per_folder = Counter(uses)
    per_repo: Counter[Path] = Counter()
    for folder, prompts in per_folder.items():
        root = repo_root(Path(folder))
        if root is not None and usable_repo(root, home):
            per_repo[root] += prompts
    return per_repo.most_common()


def children(folder: Path) -> list[Path]:
    try:
        return list(folder.iterdir())
    except OSError:
        return []


def git_age(repo: Path) -> float:
    try:
        return (repo / ".git").stat().st_mtime
    except OSError:
        return 0.0


def repos_in_usual_folders(home: Path) -> list[Path]:
    found = [
        child
        for name in USUAL_REPO_FOLDERS
        for child in children(home / name)
        if (child / ".git").exists() and usable_repo(child, home)
    ]
    return sorted(found, key=git_age, reverse=True)[:MAX_FOUND_REPOS]


def github_owner(repo: Path) -> str:
    remote = output_of("git", "-C", str(repo), "remote", "get-url", "origin") or ""
    match = GITHUB_REMOTE.search(remote)
    return match.group(1) if match else ""


def repo_uses(used: list[tuple[Path, int]], home: Path) -> tuple[RepoUse, ...]:
    if used:
        return tuple(RepoUse(str(repo), prompts, github_owner(repo)) for repo, prompts in used)
    return tuple(RepoUse(str(repo), 0, github_owner(repo)) for repo in repos_in_usual_folders(home))


def aws_sso_profiles(path: Path) -> tuple[str, ...]:
    config = configparser.RawConfigParser()
    try:
        config.read(path)
    except (OSError, configparser.Error):
        return ()
    return tuple(
        name.removeprefix("profile ")
        for name in config.sections()
        if name.startswith("profile ") and any(key.startswith("sso_") for key in config[name])
    )


def repeating_events(schedule: str) -> tuple[str, ...]:
    """The repeating events in `pmset -g sched`."""
    events = []
    repeating = False
    for line in schedule.splitlines():
        if line.startswith("Repeating power events"):
            repeating = True
        elif repeating and not line.startswith((" ", "\t")):
            repeating = False
        elif repeating and line.strip():
            events.append(line.strip())
    return tuple(events)


def repeating_wake(schedule: str) -> str:
    """The wake among the repeating events in `pmset -g sched`, if there is one."""
    return next((event for event in repeating_events(schedule) if "wake" in event.lower()), "")


def config_files(config_dir: Path) -> dict[str, str]:
    found = {}
    for name in init_plan.CONFIG_FILES:
        try:
            found[name] = (config_dir / name).read_text()
        except OSError:
            continue
    return found


def sessions_listed(path: Path) -> tuple[dict[str, Any], ...] | None:
    """What sessions.yml lists (nothing if there is none); None if it is there but cannot be read. Ruby
    reads the YAML, as lib/common.sh does: Python has no parser built in."""
    if not path.exists():
        return ()
    text = output_of("ruby", "-e", YAML_TO_JSON, str(path))
    try:
        data = json.loads(text) if text is not None else None
    except json.JSONDecodeError:
        return None
    listed = (data.get("sessions") or []) if isinstance(data, dict) else None
    if not isinstance(listed, list):
        return None
    return tuple(entry for entry in listed if isinstance(entry, dict))


def uses_per_agent(since: datetime) -> dict[str, list[tuple[str, datetime]]]:
    return {name: list(agent.prompt_history(since)) for name, agent in agent_state.AGENTS.items()}


def counts(uses: Mapping[str, Sequence[object]]) -> dict[str, int]:
    return {name: len(used) for name, used in uses.items()}


def ready_agents() -> tuple[str, ...]:
    return tuple(name for name, agent in agent_state.AGENTS.items() if agent.ready())


def connectors_found(claude_bin: str, cwd: Path) -> tuple[str, ...]:
    """The names `claude mcp list` shows (it connects to each, a few seconds); none if it fails, which
    only leaves the question with nothing to tick."""
    try:
        return tuple(claude_state.mcp_server_targets(claude_bin, cwd))
    except claude_state.McpListError:
        return ()


def detect(config_dir: Path, now: datetime | None = None) -> Facts:
    moment = now or datetime.now().astimezone()
    home = Path.home()
    since = moment - timedelta(days=HISTORY_DAYS)
    agent_uses = uses_per_agent(since)
    uses = list(chain.from_iterable(agent_uses.values()))
    claude_bin = os.environ.get("BOSUN_CLAUDE_BIN") or "claude"
    auth = claude_state.auth_status(claude_bin)
    files = config_files(config_dir)
    listed = sessions_listed(config_dir / init_plan.SESSIONS_FILE)
    return Facts(
        home=str(home),
        today=moment.date().isoformat(),
        history_days=HISTORY_DAYS,
        repos=repo_uses(repos_from_history((cwd for cwd, _ in uses), home), home),
        days=init_plan.working_days(at.astimezone() for _, at in uses),
        missing_tools=tuple(tool for tool in init_plan.REQUIRED_TOOLS if not shutil.which(tool)),
        has_launchd=shutil.which("launchctl") is not None,
        gh_logged_in=output_of("gh", "auth", "status") is not None,
        claude_logged_in=auth.logged_in is True,
        claude_plan=auth.plan,
        aws_sso_profiles=aws_sso_profiles(home / ".aws" / "config"),
        slack_plugin=claude_state.slack_plugin_mcp_config() is not None,
        bypass_accepted=claude_state.bypass_accepted(),
        ready_agents=ready_agents(),
        asked_harness=os.environ.get("BOSUN_HARNESS", ""),
        connectors=connectors_found(claude_bin, home),
        agent_prompts=counts(agent_uses),
        has_battery="InternalBattery" in (output_of("pmset", "-g", "batt") or ""),
        repeating_wake=repeating_wake(output_of("pmset", "-g", "sched") or ""),
        files=files,
        env=init_plan.parse_env(files.get(init_plan.ENV_FILE, "")),
        sessions=listed or (),
        sessions_unreadable=listed is None,
    )
