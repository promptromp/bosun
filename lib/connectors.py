#!/usr/bin/env python3
"""What a window may reach: its list of MCP servers and connectors (BOSUN_CONNECTORS,
BOSUN_ORCHESTRATOR_CONNECTORS, `connectors:` in sessions.yml) turned into what Claude Code and Codex
take at launch. Design: docs/design/connectors.md.

A list is comma-separated: server names as `claude mcp list` shows them (`claude.ai Acme Mail`,
`plugin:acme-tracker:tracker`, `acme-local`) or URL patterns (`https://mcp.example.com/*`). Empty
means none; `all` means no limit. A name that resolves to nothing is left out: a window starts with
less than its list, never more.

Usage: connectors.py claude --list <value> --cwd <dir> --state-dir <dir> --agent-bin <claude> [--kept-only]
                            [--missing-to <file>]
           Claude Code's settings, always a JSON object: {"allowedMcpServers": [...]}, or {} for `all`;
           a line on stderr per name not found. Runs `claude mcp list` in <dir> only for a name it
           cannot resolve otherwise (never with --kept-only), and keeps in <state-dir>/connectors.json
           what that lookup found for the names it was asked about (a name it no longer shows is
           dropped), for when the listing fails. Whatever else goes wrong, it prints
           {"allowedMcpServers": []} and exits 1: a window starts with less, never with more.
           --missing-to writes the names left out to <file>, on every exit 0 or 1, as a JSON array
           (after an unexpected error, every name of the list): what a caller tells me, never parsed
           from stderr.
       connectors.py codex --list <value> --agent-bin <codex>
           Codex's launch arguments, one per line: each listed server the list does not name switched
           off, and ChatGPT's connectors unless the list is `all`.

Exit codes: 0 done, 1 a name not found or something else went wrong (claude: the settings printed are
still the ones to use), or the servers could not be listed (codex), 2 a list or command line that cannot
be read. So a caller takes empty output or an exit of 2 or more as fatal.
"""

from __future__ import annotations

import argparse
import json
import string
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import codex_state
from agent_files import read_object, write_atomically


EVERYTHING = "all"
KEPT_FILE = "connectors.json"
LOOKUP_TIMEOUT_SECONDS = 30.0
URL_SCHEMES = ("http://", "https://")
# Claude Code's rule for a serverName in an allow list, and TOML's for a bare key: the same characters.
PLAIN = frozenset(string.ascii_letters + string.digits + "-_")


def say(line: str) -> None:
    print(f"connectors: {line}", file=sys.stderr)


class ConnectorsError(ValueError):
    """A list that cannot be read."""


@dataclass(frozen=True)
class Allowed:
    names: tuple[str, ...] = ()
    everything: bool = False


@dataclass(frozen=True)
class Resolution:
    entries: tuple[dict[str, Any], ...] | None  # None: no limit
    missing: tuple[str, ...]


def parse(value: str) -> Allowed:
    names = tuple(dict.fromkeys(name.strip() for name in value.split(",") if name.strip()))
    if EVERYTHING not in names:
        return Allowed(names)
    if len(names) > 1:
        others = ", ".join(name for name in names if name != EVERYTHING)
        raise ConnectorsError(
            f"`{EVERYTHING}` means every server, so it stands alone, but the list also names {others}"
        )
    return Allowed(everything=True)


def is_url(name: str) -> bool:
    return name.startswith(URL_SCHEMES)


def is_plain(name: str) -> bool:
    return bool(name) and set(name) <= PLAIN


# ---- Claude Code ----------------------------------------------------------------------------------


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


def claude_entry(
    name: str, plugins: Mapping[str, Mapping[str, Any]], urls: Mapping[str, str]
) -> dict[str, Any] | None:
    if is_url(name):
        return {"serverUrl": name}
    if name in plugins:
        return plugin_entry(plugins[name])
    if name in urls:
        return {"serverUrl": urls[name]}
    if is_plain(name):
        return {"serverName": name}
    return None


def claude_entries(
    allowed: Allowed, plugins: Mapping[str, Mapping[str, Any]], urls: Mapping[str, str]
) -> Resolution:
    """The allowedMcpServers entries for a list, given the installed plugins' servers and the URLs
    found for the other names (claude.ai's connectors)."""
    if allowed.everything:
        return Resolution(None, ())
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in allowed.names:
        entry = claude_entry(name, plugins, urls)
        if entry is None:
            missing.append(name)
        else:
            entries.append(entry)
    return Resolution(tuple(entries), tuple(missing))


def needs_url(name: str, plugins: Mapping[str, Any]) -> bool:
    return not (is_url(name) or name in plugins or is_plain(name))


def needs_lookup(allowed: Allowed, plugins: Mapping[str, Any]) -> bool:
    """Whether some name can only be resolved by `claude mcp list`."""
    return any(needs_url(name, plugins) for name in allowed.names)


def load_kept(state_dir: Path) -> dict[str, str]:
    """The URLs the last lookups found, by name; empty if there are none to trust."""
    kept = read_object(state_dir / KEPT_FILE)
    if kept is None or not all(isinstance(url, str) for url in kept.values()):
        return {}
    return {str(name): str(url) for name, url in kept.items()}


def save_kept(state_dir: Path, asked: Iterable[str], found: Mapping[str, str]) -> None:
    """A lookup's answer for the names it was <asked> about: their kept URLs become <found> (a name
    asked about and not found is dropped, so `--kept-only` agrees with the launch), and the other names
    kept stay. Best-effort: a launch uses what it found even when it cannot keep it, so a write that
    fails is said on stderr and nothing more. Each writer has a temp file of its own, so launches saving
    at once never break one another (the last one's write wins)."""
    asked = set(asked)
    kept = {**{name: url for name, url in load_kept(state_dir).items() if name not in asked}, **found}
    try:
        write_atomically(state_dir / KEPT_FILE, json.dumps(kept, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        say(f"could not keep the addresses found in {state_dir / KEPT_FILE} ({exc.strerror or exc})")


# ---- Codex ----------------------------------------------------------------------------------------


def toml_key(name: str) -> str:
    """<name> as one TOML key: bare when it can be, else a quoted string."""
    return name if is_plain(name) else json.dumps(name, ensure_ascii=False)


def codex_args(allowed: Allowed, servers: Sequence[str]) -> list[str]:
    """Codex's launch arguments: each of <servers> the list does not name switched off, and ChatGPT's
    connectors (its `apps` feature) unless the list is `all`."""
    if allowed.everything:
        return []
    args: list[str] = []
    for server in servers:
        if server not in allowed.names:
            args += ["-c", f"mcp_servers.{toml_key(server)}.enabled=false"]
    return [*args, "--disable", "apps"]


# ---- the command line ------------------------------------------------------------------------------


def looked_up_urls(allowed: Allowed, plugins: Mapping[str, Any], args: argparse.Namespace) -> dict[str, str]:
    """The URLs of the names only `claude mcp list` resolves: looked up (and kept), or, when the
    lookup fails or is not wanted, those the last lookups kept."""
    if not needs_lookup(allowed, plugins):
        return {}
    if args.kept_only:
        return load_kept(args.state_dir)
    try:
        targets = claude_state.mcp_server_targets(args.agent_bin, args.cwd, LOOKUP_TIMEOUT_SECONDS)
    except claude_state.McpListError as exc:
        say(f"claude mcp list failed ({exc}); using the addresses kept from the last launch")
        return load_kept(args.state_dir)
    asked = [name for name in allowed.names if needs_url(name, plugins)]
    found = {name: targets[name] for name in asked if is_url(targets.get(name, ""))}
    save_kept(args.state_dir, asked, found)
    return found


def claude_settings(args: argparse.Namespace) -> tuple[dict[str, Any], tuple[str, ...]]:
    """The settings for the list, and the names left out of them."""
    allowed = parse(args.list)
    if allowed.everything:
        return {}, ()
    plugins = claude_state.plugin_mcp_servers()
    resolution = claude_entries(allowed, plugins, looked_up_urls(allowed, plugins, args))
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
    """Always a JSON object on stdout, so the launch never reads a failure as `all` (no limit)."""
    try:
        settings, missing = claude_settings(args)
        code = 1 if missing else 0
    except ConnectorsError:
        raise
    except Exception as exc:  # noqa: BLE001 - whatever goes wrong, the window starts with no server
        why = " ".join(f"{type(exc).__name__}: {exc}".split())  # one line, whatever the exception says
        say(f"could not resolve the list ({why}), so this window starts without servers")
        settings, missing, code = {"allowedMcpServers": []}, parse(args.list).names, 1
    if args.missing_to is not None:
        write_missing(args.missing_to, missing)
    print(json.dumps(settings, separators=(",", ":")))
    return code


def cmd_codex(args: argparse.Namespace) -> int:
    allowed = parse(args.list)
    if allowed.everything:
        return 0
    try:
        servers = codex_state.mcp_server_names(args.agent_bin, LOOKUP_TIMEOUT_SECONDS)
    except codex_state.CodexListError as exc:
        say(f"codex mcp list failed ({exc}), so bosun cannot tell which servers to switch off")
        return 1
    for arg in codex_args(allowed, servers):
        print(arg)
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="connectors.py", description="What a window may reach.")
    commands = top.add_subparsers(dest="command", required=True)
    claude = commands.add_parser("claude", help="Claude Code's allowedMcpServers settings")
    claude.add_argument("--list", required=True)
    claude.add_argument("--cwd", required=True, type=Path)
    claude.add_argument("--state-dir", required=True, type=Path)
    claude.add_argument("--agent-bin", required=True)
    claude.add_argument("--kept-only", action="store_true")
    claude.add_argument("--missing-to", type=Path)
    claude.set_defaults(run=cmd_claude)
    codex = commands.add_parser("codex", help="Codex's launch arguments")
    codex.add_argument("--list", required=True)
    codex.add_argument("--agent-bin", required=True)
    codex.set_defaults(run=cmd_codex)
    return top


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        code: int = args.run(args)
    except ConnectorsError as exc:
        say(str(exc))
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
