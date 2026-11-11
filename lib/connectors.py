#!/usr/bin/env python3
"""What a window may reach: its list of MCP servers and connectors (BOSUN_CONNECTORS,
BOSUN_ORCHESTRATOR_CONNECTORS, `connectors:` in sessions.yml), which each agent's module turns into what
that agent takes at launch: lib/connectors_claude.py (Claude Code's settings) and lib/connectors_codex.py
(Codex's arguments), each with a command line of its own. This module holds what they share: reading a
list, the report's helpers and texts, and `doctor`, which asks the window's agent. Design:
docs/design/connectors.md.

A list is comma-separated: server names as `claude mcp list` shows them (`claude.ai Acme Mail`,
`plugin:acme-tracker:tracker`, `acme-local`) or URL patterns (`https://mcp.example.com/*`). Empty
means none; `all` means no limit. A name that resolves to nothing is left out: a window starts with
less than its list, never more.

Usage: connectors.py doctor --window <name> --kind <kind> --harness <agent> --list <value>
                            --cwd <dir> --state-dir <dir> --agent-bin <agent> [--settings-file <file>]
           bosun doctor's line for a window: `ok` or `warn`, a tab, then what it reaches (names only;
           on Claude Code a plain name is only allowed by name and a URL pattern by pattern), each name
           not found, for the orchestrator with `all` to name its connectors, and on Claude Code another
           settings scope that keeps it from starting, and what the allow list of the orchestrator's
           settings file (--settings-file) comes to. <agent> is any agent bosun knows (agent_state.AGENTS);
           its line is the `doctor_line` of the module its profile names (Profile.connectors).
           Claude Code's is from what earlier lookups kept (never `claude mcp list`, which connects to
           every server); Codex's from `codex mcp list --json` in <dir>, which starts none. Always exit 0
           with one line, but for a command line that cannot be read (2).

Exit codes of every module: 0 done, 2 a list or command line that cannot be read; the others are in the
agent modules' usage.
"""

from __future__ import annotations

import argparse
import importlib
import json
import string
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_state import AGENTS


EVERYTHING = "all"
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


# ---- bosun doctor ---------------------------------------------------------------------------------

OK, WARN = "ok", "warn"
ORCHESTRATOR = "orchestrator"  # its kind in the window list
EVERYTHING_LINE = "all (every MCP server and connector)"
NONE_LINE = "none (no MCP server or connector)"
NAME_THEM = "name the ones it needs in BOSUN_ORCHESTRATOR_CONNECTORS and drop all"
NOT_FOUND = "not found, so it starts without: {}"
NOT_KEPT = (
    "not among the servers earlier starts looked up: {} "
    "(bosun doctor looks nothing up: the window's next start does)"
)
DROPPED = (
    "every plugin is switched off for the plugin servers its list does not name, so it also starts "
    "without: {}"
)
SiteAllowList = tuple[Path, Sequence[Any]]  # a settings file, and the allowedMcpServers it holds


def report(window: str, reach: str, problems: Sequence[str], notes: Sequence[str] = ()) -> tuple[str, str]:
    """A window's line: what it reaches, each problem, then each note, which warns of nothing (a part
    only when it says something)."""
    parts = [part for part in problems if part]
    return (WARN if parts else OK), "; ".join([f"{window}: {reach}", *parts, *(n for n in notes if n)])


def part(text: str, names: Sequence[str]) -> str:
    """<text> with the <names> in it, or nothing when there are none."""
    return text.format(", ".join(names)) if names else ""


def everything_report(window: str, kind: str, site: SiteAllowList | None = None) -> tuple[str, str]:
    """All: no limit, which the orchestrator should not keep (it reaches every connector of mine), but
    for the allow list of its settings file (<site>), which no list replaces then."""
    reach = EVERYTHING_LINE
    if site is not None:
        listed = json.dumps(list(site[1]), ensure_ascii=False)
        reach = f"all, so the allowedMcpServers of its settings file ({site[0]}) applies: {listed}"
    if kind == ORCHESTRATOR:
        return WARN, f"{window}: {reach}: {NAME_THEM}"
    return OK, f"{window}: {reach}"


def cmd_doctor(args: argparse.Namespace) -> int:
    """One line, whatever goes wrong: a list that cannot be read is a window that does not start. The line
    is the window's agent's: the `doctor_line` of the module its profile names."""
    try:
        module = importlib.import_module(AGENTS[args.harness].PROFILE.connectors)
        doctor_line: Callable[[argparse.Namespace, Allowed], tuple[str, str]] = module.doctor_line
        level, line = doctor_line(args, parse(args.list))
    except ConnectorsError as exc:
        level, line = WARN, f"{args.window}: does not start: {exc}"
    except Exception as exc:  # noqa: BLE001 - doctor says it could not check, and goes on
        level, line = WARN, f"{args.window}: could not check its connectors ({type(exc).__name__}: {exc})"
    print(f"{level}\t{' '.join(line.split())}")
    return 0


# ---- the command line ------------------------------------------------------------------------------


def run(command_line: argparse.ArgumentParser, argv: list[str]) -> int:
    """Run <command_line>'s command on <argv>, whose list that cannot be read is said on stderr (exit 2).
    Every module's `main` is this."""
    args = command_line.parse_args(argv)
    try:
        code: int = args.run(args)
    except ConnectorsError as exc:
        say(str(exc))
        return 2
    return code


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="connectors.py", description="What a window may reach.")
    commands = top.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="bosun doctor's line for a window")
    doctor.add_argument("--window", required=True)
    doctor.add_argument("--kind", required=True)
    doctor.add_argument("--harness", required=True, choices=list(AGENTS))
    doctor.add_argument("--list", required=True)
    doctor.add_argument("--cwd", required=True, type=Path)
    doctor.add_argument("--state-dir", required=True, type=Path)
    doctor.add_argument("--agent-bin", required=True)
    doctor.add_argument("--settings-file", type=Path)  # the orchestrator's (BOSUN_ORCHESTRATOR_SETTINGS)
    doctor.set_defaults(run=cmd_doctor)
    return top


def main(argv: list[str]) -> int:
    return run(parser(), argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
