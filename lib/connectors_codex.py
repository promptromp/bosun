#!/usr/bin/env python3
"""A Codex window's list of MCP servers and connectors (BOSUN_CONNECTORS, `connectors:` in sessions.yml)
turned into the arguments of its launch. What every agent's module shares is lib/connectors.py. Design:
docs/design/connectors.md.

Usage: connectors_codex.py --list <value> --agent-bin <codex>
           Codex's launch arguments, one per line, from `codex mcp list --json` with plugins and
           without: each configured server the list does not name switched off, every plugin when it
           does not name one of their servers, and ChatGPT's connectors; nothing for `all`. A line on
           stderr per name of the list Codex has no server by.

Exit codes: 0 done, 1 the servers could not be listed, 2 a list or command line that cannot be read.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import codex_state
import connectors
from connectors import Allowed, is_plain, is_url, parse, say


# What a TOML basic string may not hold raw: the quote and the backslash, written as their escapes, and
# the control characters (U+0000 to U+001F, and U+007F), written as \uXXXX.
TOML_ESCAPES = {'"': '\\"', "\\": "\\\\"}
TOML_CONTROL = frozenset(chr(code) for code in (*range(0x20), 0x7F))
CODEX_OFF = "switches off {}ChatGPT's connectors"
MEANINGLESS = "mean nothing to Codex: {}"
MEANS_NOTHING = (
    "means nothing to Codex (a URL pattern or a claude.ai connector), so this window starts without it"
)


def toml_char(char: str) -> str:
    if char in TOML_ESCAPES:
        return TOML_ESCAPES[char]
    return f"\\u{ord(char):04X}" if char in TOML_CONTROL else char


def toml_string(text: str) -> str:
    """<text> as a TOML basic string (json.dumps would leave U+007F raw, which TOML forbids)."""
    return '"' + "".join(toml_char(char) for char in text) + '"'


def toml_key(name: str) -> str:
    """<name> as one TOML key: bare when it can be, else a basic string."""
    return name if is_plain(name) else toml_string(name)


@dataclass(frozen=True)
class CodexPlan:
    """What a Codex window's launch switches off, from its list and its servers: <off>, the configured
    servers it does not name, each by name; every plugin when it does not name one of the plugins'
    servers (<unnamed>), which also drops those of them it names (<dropped>)."""

    off: tuple[str, ...]
    unnamed: tuple[str, ...]
    dropped: tuple[str, ...]

    @property
    def plugins_off(self) -> bool:
        return bool(self.unnamed)


def codex_plan(allowed: Allowed, servers: codex_state.McpServers) -> CodexPlan:
    unnamed = tuple(name for name in servers.plugins if name not in allowed.names)
    dropped = tuple(name for name in servers.plugins if name in allowed.names) if unnamed else ()
    return CodexPlan(
        tuple(name for name in servers.configured if name not in allowed.names), unnamed, dropped
    )


def codex_args(allowed: Allowed, servers: codex_state.McpServers) -> list[str]:
    """Codex's launch arguments; nothing for `all`. Every configured server the list does not name
    switched off, in one inline table (`-c mcp_servers={<key>={enabled=false},...}`, which Codex merges
    into its configuration); every plugin (`--disable plugins`) when the list does not name one of
    their servers, since a plugin's server is not an `mcp_servers` table (naming one there stops Codex)
    and nothing says which plugin it is from; and ChatGPT's connectors (its `apps` feature). One
    table, because Codex splits a `-c` key at every dot, quotes included, so
    `mcp_servers."a.b".enabled` names another server; inside a value its TOML parser reads quoted keys
    as TOML does. What it prints has the shape `codex_connector_args_valid` (lib/launch/codex.sh) checks
    before a launch: a change of shape here changes that check with it."""
    if allowed.everything:
        return []
    plan = codex_plan(allowed, servers)
    off = [f"{toml_key(server)}={{enabled=false}}" for server in plan.off]
    table = ["-c", f"mcp_servers={{{','.join(off)}}}"] if off else []
    plugins = ["--disable", "plugins"] if plan.plugins_off else []
    return [*table, *plugins, "--disable", "apps"]


# ---- bosun doctor ---------------------------------------------------------------------------------


def means_nothing_to_codex(name: str) -> bool:
    """A URL pattern or a claude.ai connector: Claude Code's ways to name a server, which Codex has
    no server by, so cannot switch on."""
    return is_url(name) or claude_state.is_claude_ai_connector(name)


def _codex_off(plan: CodexPlan) -> str:
    """What a Codex window's launch switches off: each configured server its list does not name, every
    plugin when it does not name one of their servers, and ChatGPT's connectors."""
    plugins = [f"every plugin (with {', '.join(plan.unnamed + plan.dropped)})"] if plan.plugins_off else []
    off = [*plan.off, *plugins]
    return CODEX_OFF.format(f"{', '.join(off)} and " if off else "")


def _codex_names(allowed: Allowed, servers: codex_state.McpServers) -> tuple[list[str], list[str]]:
    """The names of a Codex window's list it has no server by, and those that mean nothing to Codex."""
    unknown = [name for name in allowed.names if name not in (*servers.configured, *servers.plugins)]
    meaningless = [name for name in unknown if means_nothing_to_codex(name)]
    return [name for name in unknown if name not in meaningless], meaningless


def codex_report(
    window: str, kind: str, allowed: Allowed, servers: codex_state.McpServers
) -> tuple[str, str]:
    """A Codex window's line, from its servers: what it keeps, what it switches off, the plugin
    servers it names that go with every plugin, and the names it has no server by."""
    if allowed.everything:
        return connectors.everything_report(window, kind)
    plan = codex_plan(allowed, servers)
    if not allowed.names:
        return connectors.OK, f"{window}: none ({_codex_off(plan)})"
    kept = [*servers.configured, *(() if plan.plugins_off else servers.plugins)]
    reached = [name for name in allowed.names if name in kept]
    not_found, meaningless = _codex_names(allowed, servers)
    reach = f"reaches {', '.join(reached) or 'nothing'} ({_codex_off(plan)})"
    problems = [
        connectors.part(connectors.DROPPED, plan.dropped),
        connectors.part(connectors.NOT_FOUND, not_found),
        connectors.part(MEANINGLESS, meaningless),
    ]
    return connectors.report(window, reach, problems)


def doctor_line(args: argparse.Namespace, allowed: Allowed) -> tuple[str, str]:
    """The doctor's level and line for a Codex window, from `codex mcp list --json` in its folder,
    which starts no server."""
    if allowed.everything:
        return connectors.everything_report(args.window, args.kind)
    try:
        servers = codex_state.mcp_servers(args.agent_bin, connectors.LOOKUP_TIMEOUT_SECONDS, args.cwd)
    except codex_state.CodexListError as exc:
        why = f"codex mcp list failed ({exc}), so bosun cannot tell which servers to switch off"
        return connectors.WARN, f"{args.window}: does not start: {why}"
    return codex_report(args.window, args.kind, allowed, servers)


# ---- the command line ------------------------------------------------------------------------------


def say_codex_unknown(allowed: Allowed, servers: codex_state.McpServers) -> None:
    """Each name of the list Codex has no server by: one it does not list, and one that means nothing
    to it (a URL pattern, a claude.ai connector)."""
    not_found, meaningless = _codex_names(allowed, servers)
    for name in not_found:
        say(f"{name} not found, so this window starts without it")
    for name in meaningless:
        say(f"{name} {MEANS_NOTHING}")


def cmd_codex(args: argparse.Namespace) -> int:
    allowed = parse(args.list)
    if allowed.everything:
        return 0
    try:
        servers = codex_state.mcp_servers(args.agent_bin, connectors.LOOKUP_TIMEOUT_SECONDS)
    except codex_state.CodexListError as exc:
        say(f"codex mcp list failed ({exc}), so bosun cannot tell which servers to switch off")
        return 1
    say_codex_unknown(allowed, servers)
    for arg in codex_args(allowed, servers):
        print(arg)
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="connectors_codex.py", description="Codex's launch arguments.")
    top.add_argument("--list", required=True)
    top.add_argument("--agent-bin", required=True)
    top.set_defaults(run=cmd_codex)
    return top


def main(argv: list[str]) -> int:
    return connectors.run(parser(), argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
