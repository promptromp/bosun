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
       connectors.py doctor --window <name> --kind <kind> --harness claude|codex --list <value>
                            --cwd <dir> --state-dir <dir> --agent-bin <agent>
           bosun doctor's line for a window: `ok` or `warn`, a tab, then what it reaches (names only),
           each name not found, and, for the orchestrator with `all`, to name its connectors. Claude
           Code's from what earlier lookups kept (never `claude mcp list`, which connects to every
           server); Codex's from `codex mcp list --json` in <dir>, which starts none. Always exit 0
           with one line, but for a command line that cannot be read (2).

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


# ---- Codex ----------------------------------------------------------------------------------------


# What a TOML basic string may not hold raw: the quote and the backslash, written as their escapes, and
# the control characters (U+0000 to U+001F, and U+007F), written as \uXXXX.
TOML_ESCAPES = {'"': '\\"', "\\": "\\\\"}
TOML_CONTROL = frozenset(chr(code) for code in (*range(0x20), 0x7F))


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


def codex_args(allowed: Allowed, servers: Sequence[str]) -> list[str]:
    """Codex's launch arguments: every one of <servers> the list does not name switched off, in one
    inline table (`-c mcp_servers={<key>={enabled=false},...}`, which Codex merges into its
    configuration), and ChatGPT's connectors (its `apps` feature) off; nothing for `all`. One table,
    because Codex splits a `-c` key at every dot, quotes included, so `mcp_servers."a.b".enabled`
    names another server; inside a value its TOML parser reads quoted keys as TOML does."""
    if allowed.everything:
        return []
    off = [f"{toml_key(server)}={{enabled=false}}" for server in servers if server not in allowed.names]
    table = ["-c", f"mcp_servers={{{','.join(off)}}}"] if off else []
    return [*table, "--disable", "apps"]


# ---- bosun doctor ---------------------------------------------------------------------------------

OK, WARN = "ok", "warn"
ORCHESTRATOR = "orchestrator"  # its kind in the window list
EVERYTHING_LINE = "all (every MCP server and connector)"
NAME_THEM = "name the ones it needs in BOSUN_ORCHESTRATOR_CONNECTORS and drop all"
NOT_FOUND = "not found, so it starts without: {}"
NOT_KEPT = (
    "not among the servers earlier starts looked up: {} "
    "(bosun doctor looks nothing up: the window's next start does)"
)
CODEX_OFF = "switches off {}ChatGPT's connectors"
MEANINGLESS = "mean nothing to Codex: {}"


def _report(window: str, reach: str, problems: Sequence[str]) -> tuple[str, str]:
    """A window's line: what it reaches, then each problem (a part only when it names something)."""
    parts = [part for part in problems if part]
    return (WARN if parts else OK), "; ".join([f"{window}: {reach}", *parts])


def _part(text: str, names: Sequence[str]) -> str:
    return text.format(", ".join(names)) if names else ""


def _everything(window: str, kind: str) -> tuple[str, str]:
    """All: no limit, which the orchestrator should not keep (it reaches every connector of mine)."""
    if kind == ORCHESTRATOR:
        return WARN, f"{window}: {EVERYTHING_LINE}: {NAME_THEM}"
    return OK, f"{window}: {EVERYTHING_LINE}"


def claude_report(
    window: str, kind: str, allowed: Allowed, plugins: Mapping[str, Any], kept: Mapping[str, str]
) -> tuple[str, str]:
    """A Claude Code window's line, resolved as `--kept-only` does: a name only `claude mcp list` could
    resolve (claude.ai's, a plugin's the installed ones do not list) from what earlier lookups kept."""
    if allowed.everything:
        return _everything(window, kind)
    if not allowed.names:
        return OK, f"{window}: none (no MCP server or connector)"
    missing = claude_entries(allowed, plugins, kept).missing
    reached = [name for name in allowed.names if name not in missing]
    not_kept = [name for name in missing if needs_lookup_of(name, plugins)]
    not_found = [name for name in missing if name not in not_kept]
    reach = f"reaches {', '.join(reached) or 'nothing'}"
    return _report(window, reach, [_part(NOT_FOUND, not_found), _part(NOT_KEPT, not_kept)])


def means_nothing_to_codex(name: str) -> bool:
    """A URL pattern or a claude.ai connector: Claude Code's ways to name a server, which Codex has
    no server by, so cannot switch on."""
    return is_url(name) or claude_state.is_claude_ai_connector(name)


def _codex_off(allowed: Allowed, servers: Sequence[str]) -> str:
    """What a Codex window's launch switches off: each server its list does not name, and ChatGPT's."""
    off = [server for server in servers if server not in allowed.names]
    return CODEX_OFF.format(f"{', '.join(off)} and " if off else "")


def _codex_names(allowed: Allowed, servers: Sequence[str]) -> tuple[list[str], list[str], list[str]]:
    """The names of a Codex window's list it has a server by, those it has none by, and those that
    mean nothing to Codex."""
    unknown = [name for name in allowed.names if name not in servers]
    meaningless = [name for name in unknown if means_nothing_to_codex(name)]
    reached = [name for name in allowed.names if name in servers]
    return reached, [name for name in unknown if name not in meaningless], meaningless


def codex_report(window: str, kind: str, allowed: Allowed, servers: Sequence[str]) -> tuple[str, str]:
    """A Codex window's line, from the names of its servers (<servers>): what it keeps, what it
    switches off, and the names it has no server by."""
    if allowed.everything:
        return _everything(window, kind)
    if not allowed.names:
        return OK, f"{window}: none ({_codex_off(allowed, servers)})"
    reached, not_found, meaningless = _codex_names(allowed, servers)
    reach = f"reaches {', '.join(reached) or 'nothing'} ({_codex_off(allowed, servers)})"
    return _report(window, reach, [_part(NOT_FOUND, not_found), _part(MEANINGLESS, meaningless)])


def _claude_line(args: argparse.Namespace, allowed: Allowed) -> tuple[str, str]:
    plugins = claude_state.plugin_mcp_servers() if allowed.names else {}
    return claude_report(args.window, args.kind, allowed, plugins, load_kept(args.state_dir))


def _codex_line(args: argparse.Namespace, allowed: Allowed) -> tuple[str, str]:
    if allowed.everything:
        return _everything(args.window, args.kind)
    try:
        servers = codex_state.mcp_server_names(args.agent_bin, LOOKUP_TIMEOUT_SECONDS, args.cwd)
    except codex_state.CodexListError as exc:
        why = f"codex mcp list failed ({exc}), so bosun cannot tell which servers to switch off"
        return WARN, f"{args.window}: does not start: {why}"
    return codex_report(args.window, args.kind, allowed, servers)


DOCTOR_LINES = {"claude": _claude_line, "codex": _codex_line}


def cmd_doctor(args: argparse.Namespace) -> int:
    """One line, whatever goes wrong: a list that cannot be read is a window that does not start."""
    try:
        level, line = DOCTOR_LINES[args.harness](args, parse(args.list))
    except ConnectorsError as exc:
        level, line = WARN, f"{args.window}: does not start: {exc}"
    except Exception as exc:  # noqa: BLE001 - doctor says it could not check, and goes on
        level, line = WARN, f"{args.window}: could not check its connectors ({type(exc).__name__}: {exc})"
    print(f"{level}\t{' '.join(line.split())}")
    return 0


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
        targets = claude_state.mcp_server_targets(args.agent_bin, args.cwd, LOOKUP_TIMEOUT_SECONDS)
    except claude_state.McpListError as exc:
        say(f"claude mcp list failed ({exc}); using the servers kept from the last launch")
        return load_kept(args.state_dir)
    asked = [name for name in allowed.names if needs_lookup_of(name, plugins)]
    found = {name: targets[name] for name in asked if name in targets}
    save_kept(args.state_dir, asked, found)
    return found


def claude_settings(args: argparse.Namespace) -> tuple[dict[str, Any], tuple[str, ...]]:
    """The settings for the list, and the names left out of them."""
    allowed = parse(args.list)
    if allowed.everything:
        return {}, ()
    plugins = claude_state.plugin_mcp_servers()
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
    doctor = commands.add_parser("doctor", help="bosun doctor's line for a window")
    doctor.add_argument("--window", required=True)
    doctor.add_argument("--kind", required=True)
    doctor.add_argument("--harness", required=True, choices=sorted(DOCTOR_LINES))
    doctor.add_argument("--list", required=True)
    doctor.add_argument("--cwd", required=True, type=Path)
    doctor.add_argument("--state-dir", required=True, type=Path)
    doctor.add_argument("--agent-bin", required=True)
    doctor.set_defaults(run=cmd_doctor)
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
