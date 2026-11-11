#!/usr/bin/env python3
"""A Claude Code window's list of MCP servers and connectors (BOSUN_CONNECTORS,
BOSUN_ORCHESTRATOR_CONNECTORS, `connectors:` in sessions.yml) turned into the `allowedMcpServers` of its
launch settings. What every agent's module shares is lib/connectors.py. Design: docs/design/connectors.md.

Usage: connectors_claude.py --list <value> --cwd <dir> --state-dir <dir> --agent-bin <claude> [--kept-only]
                            [--missing-to <file>]
           Claude Code's settings, always a JSON object: {"allowedMcpServers": [...]}, or {} for `all`;
           a line on stderr per name not found. Runs `claude mcp list` in <dir> only for a name it
           cannot resolve otherwise (never with --kept-only), keeps in <state-dir>/connectors.json
           what that lookup found for the names it was asked about (a name it no longer shows is
           dropped), for when the listing fails, and says each plain name it does not show (it stays
           allowed). Whatever else goes wrong, it prints {"allowedMcpServers": []} and exits 1: a window
           starts with less, never with more. But when another settings scope of <dir> (mine, the
           folder's project and local settings) allows an entry the settings it would print do not hold
           exactly, it prints nothing, says which file allows what, and exits 2: Claude Code adds those
           lists up, so the window must not start.
           --missing-to writes the names left out to <file>, on every exit 0 or 1, as a JSON array
           (after an unexpected error, every name of the list): what a caller tells me, never parsed
           from stderr.

Exit codes: 0 done, 1 a name not found or something else went wrong (the settings printed are still the
ones to use), 2 a list or command line that cannot be read, or a list another settings scope would widen.
So a caller takes empty output or an exit of 2 or more as fatal.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import connectors
from agent_files import read_object, write_atomically
from connectors import Allowed, ConnectorsError, Resolution, is_plain, is_url, parse, say


KEPT_FILE = "connectors.json"


def plugin_entry(server: Mapping[str, Any]) -> dict[str, Any] | None:
    """How an allow list names a plugin's server: by its URL, or by its command and arguments."""
    url, command, args = server.get("url"), server.get("command"), server.get("args", [])
    if isinstance(url, str) and url:
        return {"serverUrl": url}
    if not (isinstance(command, str) and command and isinstance(args, list)):
        return None
    if not all(isinstance(arg, str) for arg in args):
        return None
    return {"serverCommand": [command, *args]}


def target_entry(target: str) -> dict[str, Any] | None:
    """How an allow list names a server `claude mcp list` shows by its <target>: a URL is the
    address, anything else a command line, split on whitespace (an argument holding a space splits
    wrongly, so the entry matches no server: the window starts without it)."""
    if is_url(target):
        return {"serverUrl": target}
    argv = target.split()
    return {"serverCommand": argv} if argv else None


def claude_entry(
    name: str, plugins: Mapping[str, Mapping[str, Any]], targets: Mapping[str, str]
) -> dict[str, Any] | None:
    if is_url(name):
        return {"serverUrl": name}
    if name in plugins:
        return plugin_entry(plugins[name])
    if name in targets:
        return target_entry(targets[name])
    if is_plain(name):
        return {"serverName": name}
    return None


def claude_entries(
    allowed: Allowed, plugins: Mapping[str, Mapping[str, Any]], targets: Mapping[str, str]
) -> Resolution:
    """The allowedMcpServers entries for a list, given the installed plugins' servers and the
    targets (URL or command line) `claude mcp list` showed for the other names."""
    if allowed.everything:
        return Resolution(None, ())
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in allowed.names:
        entry = claude_entry(name, plugins, targets)
        if entry is None:
            missing.append(name)
        else:
            entries.append(entry)
    return Resolution(tuple(entries), tuple(missing))


def needs_lookup_of(name: str, plugins: Mapping[str, Any]) -> bool:
    return not (is_url(name) or name in plugins or is_plain(name))


def needs_lookup(allowed: Allowed, plugins: Mapping[str, Any]) -> bool:
    """Whether some name can only be resolved by `claude mcp list`."""
    return any(needs_lookup_of(name, plugins) for name in allowed.names)


def load_kept(state_dir: Path) -> dict[str, str]:
    """The targets (URL or command line) the last lookups found, by name; empty if there are none to
    trust."""
    kept = read_object(state_dir / KEPT_FILE)
    if kept is None or not all(isinstance(target, str) for target in kept.values()):
        return {}
    return {str(name): str(target) for name, target in kept.items()}


def save_kept(state_dir: Path, asked: Iterable[str], found: Mapping[str, str]) -> None:
    """A lookup's answer for the names it was <asked> about: their kept targets become <found> (a name
    asked about and not found is dropped, so `--kept-only` agrees with the launch), and the other names
    kept stay. Best-effort: a launch uses what it found even when it cannot keep it, so a write that
    fails is said on stderr and nothing more. Each writer has a temp file of its own, so launches saving
    at once never break one another (the last one's write wins)."""
    asked = set(asked)
    kept = {**{name: target for name, target in load_kept(state_dir).items() if name not in asked}, **found}
    try:
        write_atomically(state_dir / KEPT_FILE, json.dumps(kept, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        say(f"could not keep the servers found in {state_dir / KEPT_FILE} ({exc.strerror or exc})")


def widening(
    entries: Sequence[Any], scopes: Iterable[tuple[Path, Sequence[Any]]]
) -> list[tuple[Path, list[Any]]]:
    """Each settings file of <scopes> (claude_state.settings_allow_lists) whose allowedMcpServers holds an
    entry that is not exactly (JSON-equal) one of a window's own <entries>, and those entries: Claude Code
    adds every scope's allow list to the one a window is launched with, so each would widen it. A
    narrower or a wider pattern is not equal either."""
    return [(path, extra) for path, listed in scopes if (extra := [e for e in listed if e not in entries])]


def widening_problem(widened: Sequence[tuple[Path, Sequence[Any]]]) -> str:
    """Why a window whose list <widened> files widen does not start, naming each file and its entries,
    and what to do about it."""
    it = "it" if sum(len(extra) for _, extra in widened) == 1 else "them"
    where = "that file" if len(widened) == 1 else "those files"
    files = "; ".join(
        f"{path} allows {json.dumps(list(extra), ensure_ascii=False)}" for path, extra in widened
    )
    return (
        f"{files}, beyond this window's list, and Claude Code adds {it} to it: name {it} in the window's "
        f"list, use all, or remove {it} from {where}"
    )


def refuse_widening(settings: Mapping[str, Any], cwd: Path) -> None:
    """ConnectorsError (the launch does not start) when another settings scope of a window in <cwd> would
    widen its <settings>' allow list; a window with no limit (`all`) is not checked."""
    if claude_state.ALLOW_LIST not in settings:
        return
    widened = widening(settings[claude_state.ALLOW_LIST], claude_state.settings_allow_lists(cwd))
    if widened:
        raise ConnectorsError(widening_problem(widened))


# ---- bosun doctor ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaudeFacts:
    """What a Claude Code window's doctor line goes by: the installed plugins' servers, the targets
    earlier lookups kept, the other settings scopes' allow lists (claude_state.settings_allow_lists),
    and, for the orchestrator, the allow list of its settings file, merged into its launch settings."""

    plugins: Mapping[str, Any]
    kept: Mapping[str, str]
    scopes: Sequence[tuple[Path, Sequence[Any]]] = ()
    site: connectors.SiteAllowList | None = None


REACH_LABELS = ("reaches", "allowed by name:", "allowed by pattern:")


def _reach_label(name: str, missing: Sequence[str]) -> str | None:
    """How a doctor line says <name>: reached, when it resolved to a known server; a plain name and a URL
    pattern only allowed (Claude Code matches them as they are), since nothing here says such a server
    exists; None for a name not found."""
    if is_url(name):
        return REACH_LABELS[2]
    if is_plain(name):
        return REACH_LABELS[1]
    return None if name in missing else REACH_LABELS[0]


def _claude_reach(allowed: Allowed, missing: Sequence[str]) -> str:
    """What a list reaches, and what it only allows, by name or by pattern (_reach_label)."""
    if not allowed.names:
        return connectors.NONE_LINE
    groups: dict[str, list[str]] = {label: [] for label in REACH_LABELS}
    for name in allowed.names:
        if label := _reach_label(name, missing):
            groups[label].append(name)
    return (
        "; ".join(f"{label} {', '.join(names)}" for label, names in groups.items() if names)
        or "reaches nothing"
    )


def claude_report(window: str, kind: str, allowed: Allowed, facts: ClaudeFacts) -> tuple[str, str]:
    """A Claude Code window's line, resolved as `--kept-only` does: a name only `claude mcp list` could
    resolve (claude.ai's, a plugin's the installed ones do not list) from what earlier lookups kept; and
    whether another settings scope widens it, which keeps it from starting (refuse_widening)."""
    if allowed.everything:
        return connectors.everything_report(window, kind, facts.site)
    resolution = claude_entries(allowed, facts.plugins, facts.kept)
    not_kept = [name for name in resolution.missing if needs_lookup_of(name, facts.plugins)]
    not_found = [name for name in resolution.missing if name not in not_kept]
    widened = widening(resolution.entries or (), facts.scopes)
    problems = [
        connectors.part(connectors.NOT_FOUND, not_found),
        connectors.part(connectors.NOT_KEPT, not_kept),
        f"does not start: {widening_problem(widened)}" if widened else "",
    ]
    reach = _claude_reach(allowed, resolution.missing)
    return connectors.report(window, reach, problems, [_replaced(facts.site)])


def _replaced(site: connectors.SiteAllowList | None) -> str:
    """The note that a list (not `all`) replaces the allow list of the orchestrator's settings file: the
    launch merges that file into its settings, and the window's list wins."""
    return (
        ""
        if site is None
        else f"its settings file ({site[0]}) holds an allowedMcpServers, which this list replaces"
    )


def _site(settings_file: Path | None) -> connectors.SiteAllowList | None:
    """The orchestrator's settings file and the allow list it holds; None without one."""
    listed = None if settings_file is None else claude_state.settings_allow_list(settings_file)
    return None if settings_file is None or listed is None else (settings_file, listed)


def doctor_line(args: argparse.Namespace, allowed: Allowed) -> tuple[str, str]:
    """The doctor's level and line for a Claude Code window, from what earlier lookups kept (never
    `claude mcp list`, which connects to every server)."""
    plugins = plugins_by_file() if allowed.names else {}
    scopes = claude_state.settings_allow_lists(args.cwd)
    facts = ClaudeFacts(plugins, load_kept(args.state_dir), scopes, _site(args.settings_file))
    return claude_report(args.window, args.kind, allowed, facts)


# ---- the command line ------------------------------------------------------------------------------


def looked_up_targets(
    allowed: Allowed, plugins: Mapping[str, Any], args: argparse.Namespace
) -> dict[str, str]:
    """The targets of the names only `claude mcp list` resolves: looked up (and kept), or, when the
    lookup fails or is not wanted, those the last lookups kept."""
    if not needs_lookup(allowed, plugins):
        return {}
    if args.kept_only:
        return load_kept(args.state_dir)
    try:
        targets = claude_state.mcp_server_targets(args.agent_bin, args.cwd, connectors.LOOKUP_TIMEOUT_SECONDS)
    except claude_state.McpListError as exc:
        say(f"claude mcp list failed ({exc}); using the servers kept from the last launch")
        return load_kept(args.state_dir)
    say_unshown(allowed, targets)
    asked = [name for name in allowed.names if needs_lookup_of(name, plugins)]
    found = {name: targets[name] for name in asked if name in targets}
    save_kept(args.state_dir, asked, found)
    return found


def say_unshown(allowed: Allowed, targets: Mapping[str, str]) -> None:
    """Each plain name a lookup that ran anyway does not show: it stays allowed (a serverName cannot
    widen a list), but may be a typo."""
    for name in allowed.names:
        if is_plain(name) and name not in targets:
            say(f"{name} is not among the servers claude mcp list shows here; it stays allowed by name")


def plugins_by_file() -> dict[str, dict[str, Any]]:
    """The installed plugins' servers an allow list can name from their own .mcp.json: not one that holds a
    variable bosun cannot expand (claude_state.unexpanded), whose name resolves as `claude mcp list` shows
    it, as for a plugin the installed ones do not list."""
    servers = claude_state.plugin_mcp_servers()
    return {name: config for name, config in servers.items() if not claude_state.unexpanded(config)}


def claude_settings(args: argparse.Namespace) -> tuple[dict[str, Any], tuple[str, ...]]:
    """The settings for the list, and the names left out of them."""
    allowed = parse(args.list)
    if allowed.everything:
        return {}, ()
    plugins = plugins_by_file()
    resolution = claude_entries(allowed, plugins, looked_up_targets(allowed, plugins, args))
    for name in resolution.missing:
        say(f"{name} not found, so this window starts without it")
    return {"allowedMcpServers": list(resolution.entries or ())}, resolution.missing


def write_missing(path: Path, missing: Sequence[str]) -> None:
    """The names left out, as a JSON array, for the caller. A write that fails is said on stderr."""
    try:
        path.write_text(json.dumps(list(missing), ensure_ascii=False) + "\n")
    except OSError as exc:
        say(f"could not write the names left out to {path} ({exc.strerror or exc})")


def cmd_claude(args: argparse.Namespace) -> int:
    """Always a JSON object on stdout, so the launch never reads a failure as `all` (no limit), unless
    another settings scope would widen it (refuse_widening): then nothing, and the window does not start.
    That check runs outside the catch-all, on what is printed, so that its own failure prints nothing
    either."""
    try:
        settings, missing = claude_settings(args)
        code = 1 if missing else 0
    except ConnectorsError:
        raise
    except Exception as exc:  # noqa: BLE001 - whatever goes wrong, the window starts with no server
        why = " ".join(f"{type(exc).__name__}: {exc}".split())  # one line, whatever the exception says
        say(f"could not resolve the list ({why}), so this window starts without servers")
        settings, missing, code = {"allowedMcpServers": []}, parse(args.list).names, 1
    refuse_widening(settings, args.cwd)
    if args.missing_to is not None:
        write_missing(args.missing_to, missing)
    print(json.dumps(settings, separators=(",", ":")))
    return code


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="connectors_claude.py", description="Claude Code's allowedMcpServers settings."
    )
    top.add_argument("--list", required=True)
    top.add_argument("--cwd", required=True, type=Path)
    top.add_argument("--state-dir", required=True, type=Path)
    top.add_argument("--agent-bin", required=True)
    top.add_argument("--kept-only", action="store_true")
    top.add_argument("--missing-to", type=Path)
    top.set_defaults(run=cmd_claude)
    return top


def main(argv: list[str]) -> int:
    return connectors.run(parser(), argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
