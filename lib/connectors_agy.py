#!/usr/bin/env python3
"""An agy window's list of MCP servers and connectors (BOSUN_CONNECTORS, `connectors:` in sessions.yml) held
to by a refusal. What every agent's module shares is lib/connectors.py. Design: docs/design/connectors.md.

agy takes no limit on its servers per launch, and switching one off in its shared configuration would race
between windows and change the person's Antigravity app. So before a launch this lists every server agy
would start in the window's folder, and the window starts only if its list names each one (or is `all`).
Nothing is written, and no server's command, arguments, environment or URL is ever read out, only its name.

Usage: connectors_agy.py --list <value> --agent-bin <agy> [--cwd <dir>]
           Nothing, exit 0, when the list is `all` or names every server agy would start in <dir> (default:
           the current folder); otherwise exit 1 with a line on stderr per server the list does not name,
           and where it comes from. A line on stderr per name of the list agy has no server by, which does
           not refuse. The servers are those of the global file (~/.gemini/config/mcp_config.json), of each
           `.agents/mcp_config.json` from <dir> up to its repo's root (the folder holding `.git`; none: <dir>
           alone), and of the enabled plugins (`agy plugin list --json`, named `<plugin>_<server>`); a server
           marked `"disabled": true` is not started.

Exit codes: 0 done, 1 a server beyond the list or the servers could not be listed, 2 a list or command line
that cannot be read.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import connectors
from connectors import Allowed, ConnectorsError, is_url, parse, say


AGY_CONFIG = Path.home() / ".gemini" / "config"
GLOBAL = "global"
MEANS_NOTHING = (
    "means nothing to agy (a URL pattern or a claude.ai connector), so this window starts without it"
)
BLOCKED = "agy would also start {}, which its list does not name"
NOT_NAMED = "is not in this window's list and agy cannot switch it off, so the window does not start"
WOULD_START = "agy starts nothing else here"


def read_servers(path: Path) -> list[str]:
    """The enabled servers of the config file <path>, by name: none when it is missing or empty. A file
    that does not parse, or whose `mcpServers` is not an object, is an error naming the file only."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise ConnectorsError(f"{path} cannot be read ({type(exc).__name__})") from exc
    if not text.strip():
        return []
    try:
        table = json.loads(text).get("mcpServers", {})
    except (ValueError, AttributeError) as exc:
        raise ConnectorsError(
            f"{path} does not parse, so bosun cannot tell which servers agy starts"
        ) from exc
    if not isinstance(table, dict):
        raise ConnectorsError(f"{path} has an mcpServers that is not an object")
    return [
        name
        for name, entry in table.items()
        if not (isinstance(entry, dict) and entry.get("disabled") is True)
    ]


def workspace_folders(workdir: Path) -> list[Path]:
    """<workdir> and each folder above it up to its repo's root, the folder holding `.git`; <workdir>
    alone when there is none."""
    folders = [workdir, *workdir.parents]
    for index, folder in enumerate(folders):
        if (folder / ".git").exists():
            return folders[: index + 1]
    return folders[:1]


def plugin_listing(agent_bin: str) -> list[Any]:
    """`agy plugin list --json`: the list of plugins. A listing that fails, times out or is not a JSON
    list is an error saying which."""
    command = "agy plugin list --json"
    try:
        done = subprocess.run(
            [agent_bin, "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=connectors.LOOKUP_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConnectorsError(f"{command} timed out") from exc
    except OSError as exc:
        raise ConnectorsError(f"{command} could not run ({type(exc).__name__})") from exc
    if done.returncode != 0:
        raise ConnectorsError(f"{command} failed (exited {done.returncode})")
    try:
        listing = json.loads(done.stdout)
    except ValueError as exc:
        raise ConnectorsError(f"{command} did not print JSON") from exc
    if not isinstance(listing, list):
        raise ConnectorsError(f"{command} did not print a list")
    return listing


def plugin_servers(agent_bin: str) -> dict[str, str]:
    """Each server of an enabled plugin by the name a session gives it, `<plugin>_<server>`, with its
    source. A plugin is off only when the listing says `"enabled": false`, and an entry of a shape bosun
    cannot read stops the listing: skipping it could miss a server agy starts."""
    found: dict[str, str] = {}
    for plugin in plugin_listing(agent_bin):
        name, servers = plugin_entry(plugin)
        if plugin.get("enabled", True) is False:
            continue
        for server in servers:
            found.setdefault(f"{name}_{server}", f"plugin {name}")
    return found


def plugin_entry(plugin: Any) -> tuple[str, list[str]]:
    """One plugin's name and its servers' names from `plugin list --json`, or ConnectorsError."""
    if not isinstance(plugin, dict) or not isinstance(plugin.get("name"), str) or not plugin["name"]:
        raise ConnectorsError("`plugin list --json` gave a plugin without a name")
    servers = plugin.get("mcpServers") or []
    if not isinstance(servers, list) or not all(isinstance(server, str) for server in servers):
        raise ConnectorsError(f"`plugin list --json` gave plugin {plugin['name']} servers that are not names")
    return plugin["name"], servers


def sources(workdir: str, agent_bin: str) -> dict[str, str]:
    """Every server agy would start in <workdir>, each once, with where it comes from: the global file,
    the folder of a workspace file, or a plugin."""
    found: dict[str, str] = {}
    places = [(AGY_CONFIG / "mcp_config.json", GLOBAL)]
    places.extend(
        (folder / ".agents" / "mcp_config.json", f"workspace file in {folder / '.agents'}")
        for folder in workspace_folders(Path(workdir).resolve())
    )
    for path, source in places:
        for name in read_servers(path):
            found.setdefault(name, source)
    for name, source in plugin_servers(agent_bin).items():
        found.setdefault(name, source)
    return found


def servers(workdir: str, agent_bin: str) -> list[str]:
    """Every server agy would start in <workdir>, each once, by name."""
    return list(sources(workdir, agent_bin))


def blocking(allowed: Allowed, found: dict[str, str]) -> dict[str, str]:
    """The servers the list does not name, with their sources; none for `all`."""
    if allowed.everything:
        return {}
    return {name: source for name, source in found.items() if name not in allowed.names}


def means_nothing_to_agy(name: str) -> bool:
    """A URL pattern or a claude.ai connector: Claude Code's ways to name a server, which agy has no
    server by."""
    return is_url(name) or claude_state.is_claude_ai_connector(name)


def unknown_names(allowed: Allowed, found: dict[str, str]) -> tuple[list[str], list[str]]:
    """The names of the list agy has no server by, and among them those that mean nothing to it."""
    unknown = [name for name in allowed.names if name not in found]
    meaningless = [name for name in unknown if means_nothing_to_agy(name)]
    return [name for name in unknown if name not in meaningless], meaningless


# ---- bosun doctor ---------------------------------------------------------------------------------


def doctor_line(args: argparse.Namespace, allowed: Allowed) -> tuple[str, str]:
    """The doctor's level and line for an agy window, from its servers in its folder: ok when it would
    start, otherwise the servers that keep it from starting. Starts no server."""
    if allowed.everything:
        return connectors.everything_report(args.window, args.kind)
    try:
        found = sources(str(args.cwd), args.agent_bin)
    except ConnectorsError as exc:
        return connectors.WARN, f"{args.window}: does not start: {exc}"
    blocked = blocking(allowed, found)
    if blocked:
        names = ", ".join(f"{name} ({source})" for name, source in blocked.items())
        return connectors.WARN, f"{args.window}: does not start: {BLOCKED.format(names)}"
    not_found, meaningless = unknown_names(allowed, found)
    reach = f"reaches {', '.join(n for n in allowed.names if n in found)} ({WOULD_START})"
    if not found:
        reach = "none (agy starts no server here)"
    problems = [
        connectors.part(connectors.NOT_FOUND, not_found),
        connectors.part("mean nothing to agy: {}", meaningless),
    ]
    return connectors.report(args.window, reach, problems)


# ---- the command line ------------------------------------------------------------------------------


def cmd_agy(args: argparse.Namespace) -> int:
    allowed = parse(args.list)
    if allowed.everything:
        return 0
    try:
        found = sources(args.cwd, args.agent_bin)
    except ConnectorsError as exc:
        say(f"{exc}, so this window does not start")
        return 1
    not_found, meaningless = unknown_names(allowed, found)
    for name in not_found:
        say(f"{name} not found, so this window starts without it")
    for name in meaningless:
        say(f"{name} {MEANS_NOTHING}")
    blocked = blocking(allowed, found)
    for name, source in blocked.items():
        say(f"{name} ({source}) {NOT_NAMED}")
    return 1 if blocked else 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="connectors_agy.py", description="Holds an agy window to its list.")
    top.add_argument("--list", required=True)
    top.add_argument("--agent-bin", required=True)
    top.add_argument("--cwd", default=".")
    top.set_defaults(run=cmd_agy)
    return top


def main(argv: list[str]) -> int:
    return connectors.run(parser(), argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
