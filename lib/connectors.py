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
       connectors.py codex --list <value> --agent-bin <codex>
           Codex's launch arguments, one per line, from `codex mcp list --json` with plugins and
           without: each configured server the list does not name switched off, every plugin when it
           does not name one of their servers, and ChatGPT's connectors; nothing for `all`. A line on
           stderr per name of the list Codex has no server by.
       connectors.py doctor --window <name> --kind <kind> --harness claude|codex --list <value>
                            --cwd <dir> --state-dir <dir> --agent-bin <agent> [--settings-file <file>]
           bosun doctor's line for a window: `ok` or `warn`, a tab, then what it reaches (names only;
           on Claude Code a plain name is only allowed by name and a URL pattern by pattern), each name
           not found, for the orchestrator with `all` to name its connectors, and on Claude Code another
           settings scope that keeps it from starting, and what the allow list of the orchestrator's
           settings file (--settings-file) comes to. Claude Code's from what earlier lookups kept
           (never `claude mcp list`, which connects to every server); Codex's from `codex mcp list
           --json` in <dir>, which starts none. Always exit 0 with one line, but for a command line that
           cannot be read (2).

Exit codes: 0 done, 1 a name not found or something else went wrong (claude: the settings printed are
still the ones to use), or the servers could not be listed (codex), 2 a list or command line that cannot
be read, or (claude) a list another settings scope would widen. So a caller takes empty output or an exit
of 2 or more as fatal.
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
CODEX_OFF = "switches off {}ChatGPT's connectors"
MEANINGLESS = "mean nothing to Codex: {}"
MEANS_NOTHING = (
    "means nothing to Codex (a URL pattern or a claude.ai connector), so this window starts without it"
)
DROPPED = (
    "every plugin is switched off for the plugin servers its list does not name, so it also starts "
    "without: {}"
)


def _report(window: str, reach: str, problems: Sequence[str], notes: Sequence[str] = ()) -> tuple[str, str]:
    """A window's line: what it reaches, each problem, then each note, which warns of nothing (a part
    only when it says something)."""
    parts = [part for part in problems if part]
    return (WARN if parts else OK), "; ".join([f"{window}: {reach}", *parts, *(n for n in notes if n)])


def _part(text: str, names: Sequence[str]) -> str:
    return text.format(", ".join(names)) if names else ""


SiteAllowList = tuple[Path, Sequence[Any]]  # a settings file, and the allowedMcpServers it holds


def _everything(window: str, kind: str, site: SiteAllowList | None = None) -> tuple[str, str]:
    """All: no limit, which the orchestrator should not keep (it reaches every connector of mine), but
    for the allow list of its settings file (<site>), which no list replaces then."""
    reach = EVERYTHING_LINE
    if site is not None:
        listed = json.dumps(list(site[1]), ensure_ascii=False)
        reach = f"all, so the allowedMcpServers of its settings file ({site[0]}) applies: {listed}"
    if kind == ORCHESTRATOR:
        return WARN, f"{window}: {reach}: {NAME_THEM}"
    return OK, f"{window}: {reach}"


@dataclass(frozen=True)
class ClaudeFacts:
    """What a Claude Code window's doctor line goes by: the installed plugins' servers, the targets
    earlier lookups kept, the other settings scopes' allow lists (claude_state.settings_allow_lists),
    and, for the orchestrator, the allow list of its settings file, merged into its launch settings."""

    plugins: Mapping[str, Any]
    kept: Mapping[str, str]
    scopes: Sequence[tuple[Path, Sequence[Any]]] = ()
    site: SiteAllowList | None = None


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
        return NONE_LINE
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
        return _everything(window, kind, facts.site)
    resolution = claude_entries(allowed, facts.plugins, facts.kept)
    not_kept = [name for name in resolution.missing if needs_lookup_of(name, facts.plugins)]
    not_found = [name for name in resolution.missing if name not in not_kept]
    widened = widening(resolution.entries or (), facts.scopes)
    problems = [
        _part(NOT_FOUND, not_found),
        _part(NOT_KEPT, not_kept),
        f"does not start: {widening_problem(widened)}" if widened else "",
    ]
    return _report(window, _claude_reach(allowed, resolution.missing), problems, [_replaced(facts.site)])


def _replaced(site: SiteAllowList | None) -> str:
    """The note that a list (not `all`) replaces the allow list of the orchestrator's settings file: the
    launch merges that file into its settings, and the window's list wins."""
    return (
        ""
        if site is None
        else f"its settings file ({site[0]}) holds an allowedMcpServers, which this list replaces"
    )


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
        return _everything(window, kind)
    plan = codex_plan(allowed, servers)
    if not allowed.names:
        return OK, f"{window}: none ({_codex_off(plan)})"
    kept = [*servers.configured, *(() if plan.plugins_off else servers.plugins)]
    reached = [name for name in allowed.names if name in kept]
    not_found, meaningless = _codex_names(allowed, servers)
    reach = f"reaches {', '.join(reached) or 'nothing'} ({_codex_off(plan)})"
    problems = [_part(DROPPED, plan.dropped), _part(NOT_FOUND, not_found), _part(MEANINGLESS, meaningless)]
    return _report(window, reach, problems)


def _site(settings_file: Path | None) -> SiteAllowList | None:
    """The orchestrator's settings file and the allow list it holds; None without one."""
    listed = None if settings_file is None else claude_state.settings_allow_list(settings_file)
    return None if settings_file is None or listed is None else (settings_file, listed)


def _claude_line(args: argparse.Namespace, allowed: Allowed) -> tuple[str, str]:
    plugins = plugins_by_file() if allowed.names else {}
    scopes = claude_state.settings_allow_lists(args.cwd)
    facts = ClaudeFacts(plugins, load_kept(args.state_dir), scopes, _site(args.settings_file))
    return claude_report(args.window, args.kind, allowed, facts)


def _codex_line(args: argparse.Namespace, allowed: Allowed) -> tuple[str, str]:
    if allowed.everything:
        return _everything(args.window, args.kind)
    try:
        servers = codex_state.mcp_servers(args.agent_bin, LOOKUP_TIMEOUT_SECONDS, args.cwd)
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


def cmd_codex(args: argparse.Namespace) -> int:
    allowed = parse(args.list)
    if allowed.everything:
        return 0
    try:
        servers = codex_state.mcp_servers(args.agent_bin, LOOKUP_TIMEOUT_SECONDS)
    except codex_state.CodexListError as exc:
        say(f"codex mcp list failed ({exc}), so bosun cannot tell which servers to switch off")
        return 1
    say_codex_unknown(allowed, servers)
    for arg in codex_args(allowed, servers):
        print(arg)
    return 0


def say_codex_unknown(allowed: Allowed, servers: codex_state.McpServers) -> None:
    """Each name of the list Codex has no server by: one it does not list, and one that means nothing
    to it (a URL pattern, a claude.ai connector)."""
    not_found, meaningless = _codex_names(allowed, servers)
    for name in not_found:
        say(f"{name} not found, so this window starts without it")
    for name in meaningless:
        say(f"{name} {MEANS_NOTHING}")


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
    doctor.add_argument("--settings-file", type=Path)  # the orchestrator's (BOSUN_ORCHESTRATOR_SETTINGS)
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
