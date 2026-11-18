#!/usr/bin/env python3
"""What plugins of mine a window loads: its list (BOSUN_PLUGINS, BOSUN_ORCHESTRATOR_PLUGINS, `plugins:` in
sessions.yml), the doctor's words for it, and on Claude Code the plan of which plugin ids its launch switches
off. The plan is pure: what Claude Code's files say comes in as PluginFacts (lib/plugins_claude.py reads
them). Each agent's module turns the list into what that agent takes, and says it in bosun doctor's line (its
`plugins_doctor_line`, in the module its profile's `plugins` names), which `doctor` below asks for. Design:
docs/design/connectors.md, "Plugins".

A list is comma-separated plugin names: an id as `enabledPlugins` writes it (`acme-helper@acme-market`), or a
bare name (`acme-helper`), which matches that plugin from any marketplace. Empty means none of mine; `all`
means no limit (the launch adds nothing). A plugin the window's connectors need (its list names one of the
plugin's servers, `plugin:<plugin>:<server>`) stays on, as does one the orchestrator's own settings file
enables. bosun's own orchestrator plugin, which the launch loads with --plugin-dir (an `@inline` id), is
never one of mine.

Usage: plugins.py doctor --window <name> [--kind <kind>] --harness <agent> --plugins <value>
                         --connectors <value> --cwd <dir> [--settings-file <file>]
           bosun doctor's line for a window's plugins: `ok` or `warn`, a tab, then what plugins of mine it
           loads, from the module its agent's profile names (Profile.plugins); <value> after --connectors is
           its connectors list, whose plugins stay on, and --settings-file the orchestrator's settings file
           (BOSUN_ORCHESTRATOR_SETTINGS); <kind>, its kind in the window list, warns of `all` for the
           orchestrator. Always exit 0 with one line, but for a command line that cannot be read (2).
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import connectors
from agent_state import AGENTS
from connectors import Allowed


EVERYTHING_MEANS = "every plugin"  # what `all` means in a plugin list
SERVER_PREFIX = "plugin:"  # how Claude Code names a plugin's MCP server: plugin:<plugin>:<server>
SESSION_ONLY = "@inline"  # a plugin a launch loads by --plugin-dir, such as bosun's own: never switched off

# bosun doctor's words, which every agent's module shares
EVERYTHING_LINE = "plugins: all of yours"
NARROW_THEM = (
    "A plugin's hook or skill can stop its unattended runs at a question; name the ones it needs in "
    "BOSUN_ORCHESTRATOR_PLUGINS, or set it empty for none"
)
NONE_LINE = "plugins: none of yours"
KEEPS_LINE = "plugins: keeps {}"
NOT_FOUND = "plugins not found, so it starts without: {}"
FOR_CONNECTORS = "keeps {} for its server in connectors"
FOR_SITE = "kept: the orchestrator's settings file enables {}"
MANAGED = "forced on by managed settings, which no launch can switch off: {}"
UNREAD = "does not parse, so what it says of plugins is not read: {}"


ALL = Allowed(everything=True)  # the list `all`: no limit


def say(line: str) -> None:
    print(f"plugins: {line}", file=sys.stderr)


def parse(value: str) -> Allowed:
    """A window's plugin list, read as a connectors list is; ConnectorsError for `all` with other names."""
    return connectors.parse(value, EVERYTHING_MEANS)


def bare(plugin_id: str) -> str:
    """A plugin id's name, without its marketplace."""
    return plugin_id.split("@", 1)[0]


def matches(name: str, plugin_id: str) -> bool:
    """Whether a list's <name> is the plugin <plugin_id>: the same id, or a bare name that is its name."""
    return name == plugin_id or ("@" not in name and name == bare(plugin_id))


def server_plugins(connector_list: Allowed) -> tuple[str, ...]:
    """The plugins (bare names) whose MCP servers a window's connectors list names, each once."""
    names = (
        name.removeprefix(SERVER_PREFIX) for name in connector_list.names if name.startswith(SERVER_PREFIX)
    )
    plugin_names = (name.partition(":") for name in names)
    return tuple(
        dict.fromkeys(plugin for plugin, colon, server in plugin_names if plugin and colon and server)
    )


@dataclass(frozen=True)
class PluginFacts:
    """What Claude Code's files say of a window's plugins: <enabled>, what its settings scopes (mine, the
    folder's project and local settings) say of each plugin they mention, merged key by key; <installed>,
    every plugin installed_plugins.json records; <managed>, what the managed settings files say, over every
    other source; <site>, the plugins the orchestrator's own settings file enables (its launch's --settings
    holds them); <unread>, the scope files that are there but do not parse."""

    enabled: Mapping[str, bool]
    installed: Sequence[str] = ()
    managed: Mapping[str, bool] | None = None
    site: Sequence[str] = ()
    unread: Sequence[Path] = ()


def loaded(facts: PluginFacts) -> tuple[str, ...]:
    """The plugins a window with no limit loads, sorted: each its settings switch on, and each installed one
    they do not mention (its manifest's defaultEnabled, true unless the author says otherwise: a `false` for
    one that was off anyway changes nothing), each the orchestrator's settings file enables, and each the
    managed settings force on; never one they block, nor an `@inline` one."""
    managed = facts.managed or {}
    return tuple(
        sorted(p for p in _switched_on(facts) if managed.get(p) is not False and not p.endswith(SESSION_ONLY))
    )


def _switched_on(facts: PluginFacts) -> set[str]:
    """What the settings switch on, what is installed that they do not mention, what managed ones force."""
    unmentioned = {plugin for plugin in facts.installed if plugin not in facts.enabled}
    forced = {plugin for plugin, on in (facts.managed or {}).items() if on}
    return {plugin for plugin, on in facts.enabled.items() if on} | unmentioned | forced | set(facts.site)


def known(facts: PluginFacts) -> set[str]:
    """Every plugin id the facts name: installed, mentioned by a settings scope, by managed settings or by the
    orchestrator's settings file."""
    return {*facts.enabled, *facts.installed, *(facts.managed or {}), *facts.site}


@dataclass(frozen=True)
class PluginPlan:
    """What a window's launch does with the plugins it would load: <kept> by its list, <for_connectors> for
    a server its connectors name, <for_site> as the orchestrator's settings file enables them, <off>
    switched off, <managed> left on as managed settings force them on (the list leaves them out); and
    <not_found>, the names of its list no plugin has, and <unread>, the scope files that do not parse.
    <everything>: `all`, nothing added."""

    everything: bool = False
    kept: tuple[str, ...] = ()
    for_connectors: tuple[str, ...] = ()
    for_site: tuple[str, ...] = ()
    off: tuple[str, ...] = ()
    managed: tuple[str, ...] = ()
    not_found: tuple[str, ...] = ()
    unread: tuple[Path, ...] = ()


def _allowed_by(names: Iterable[str], plugin_id: str) -> bool:
    return any(matches(name, plugin_id) for name in names)


def _allowed_by_any(name: str, ids: Iterable[str]) -> bool:
    return any(matches(name, plugin_id) for plugin_id in ids)


def _why_on(plugin: str, plugin_list: Allowed, needed: Sequence[str], facts: PluginFacts) -> str:
    """Why a plugin a window would load stays on: `kept`, `connectors`, `site` or `managed`; `off` if
    nothing keeps it."""
    reasons = (
        ("kept", _allowed_by(plugin_list.names, plugin)),
        ("connectors", _allowed_by(needed, plugin)),
        ("site", plugin in facts.site),
        ("managed", (facts.managed or {}).get(plugin) is True),
    )
    return next((reason for reason, holds in reasons if holds), "off")


def _grouped(
    candidates: Iterable[str], plugin_list: Allowed, needed: Sequence[str], facts: PluginFacts
) -> dict[str, list[str]]:
    """<candidates> by why each stays on (_why_on), or `off`."""
    groups: dict[str, list[str]] = {"kept": [], "connectors": [], "site": [], "managed": [], "off": []}
    for plugin in candidates:
        groups[_why_on(plugin, plugin_list, needed, facts)].append(plugin)
    return groups


def plan(plugin_list: Allowed, connector_list: Allowed, facts: PluginFacts) -> PluginPlan:
    """Which of the plugins a window would load (loaded) its launch keeps, and which it switches off."""
    if plugin_list.everything:
        return PluginPlan(everything=True)
    groups = _grouped(loaded(facts), plugin_list, server_plugins(connector_list), facts)
    ids = known(facts)
    return PluginPlan(
        kept=tuple(groups["kept"]),
        for_connectors=tuple(groups["connectors"]),
        for_site=tuple(groups["site"]),
        off=tuple(groups["off"]),
        managed=tuple(groups["managed"]),
        not_found=tuple(name for name in plugin_list.names if not _allowed_by_any(name, ids)),
        unread=tuple(facts.unread),
    )


def headless_plan(keep: Allowed, facts: PluginFacts) -> PluginPlan:
    """bosun's own headless calls (lib/plugins_claude.py's headless_settings): a `false` for every plugin
    the facts name (known: installed, or mentioned by a settings scope either way), not only those a window
    would load, as some of those calls read no settings file, so a plugin my settings switch off may be on
    for them; a `false` for one that is off anyway changes nothing. Left on: the plugins <keep> names, and
    those managed settings force on; never an `@inline` one. Names of <keep> no plugin has are bosun's own
    rule finding nothing, so not_found stays empty."""
    candidates = sorted(plugin for plugin in known(facts) if not plugin.endswith(SESSION_ONLY))
    groups = _grouped(candidates, keep, (), facts)
    return PluginPlan(
        kept=tuple(groups["kept"]),
        off=tuple(groups["off"]),
        managed=tuple(groups["managed"]),
        unread=tuple(facts.unread),
    )


def everything_report(window: str, kind: str) -> tuple[str, str]:
    """A window with `all`, whatever its agent: all of mine, which the orchestrator should not keep, as it
    runs unattended (an older install's, which bosun update kept at `all`)."""
    if kind == connectors.ORCHESTRATOR:
        return connectors.WARN, f"{window}: {EVERYTHING_LINE}. {NARROW_THEM}"
    return connectors.OK, f"{window}: {EVERYTHING_LINE}"


def report(window: str, plugin_plan: PluginPlan) -> tuple[str, str]:
    """A Claude Code window's doctor line: what it loads of mine, how many it switches off, what it keeps
    for its connectors and its settings file (notes), and each name not found, each plugin managed settings
    force on and each scope file that does not parse (warnings)."""
    if plugin_plan.everything:
        return connectors.OK, f"{window}: {EVERYTHING_LINE}"
    reach = KEEPS_LINE.format(", ".join(plugin_plan.kept)) if plugin_plan.kept else NONE_LINE
    if plugin_plan.off:
        reach += f" ({len(plugin_plan.off)} switched off)"
    problems = [
        connectors.part(NOT_FOUND, plugin_plan.not_found),
        connectors.part(MANAGED, plugin_plan.managed),
        connectors.part(UNREAD, [str(path) for path in plugin_plan.unread]),
    ]
    notes = [
        connectors.part(FOR_CONNECTORS, plugin_plan.for_connectors),
        connectors.part(FOR_SITE, plugin_plan.for_site),
    ]
    return connectors.report(window, reach, problems, notes)


# ---- bosun doctor ---------------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    """One line for a window's plugins, whatever goes wrong (connectors.print_doctor_line): the
    `plugins_doctor_line` of the module the window's agent's profile names (Profile.plugins)."""

    def line() -> tuple[str, str]:
        module = importlib.import_module(AGENTS[args.harness].PROFILE.plugins)
        plugins_line: Callable[[argparse.Namespace, Allowed, Allowed], tuple[str, str]] = (
            module.plugins_doctor_line
        )
        return plugins_line(args, parse(args.plugins), connectors.parse(args.connectors))

    return connectors.print_doctor_line(args.window, "plugins", line)


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="plugins.py", description="What plugins of mine a window loads.")
    commands = top.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="bosun doctor's line for a window's plugins")
    doctor.add_argument("--window", required=True)
    doctor.add_argument("--kind", default="session")  # its kind in the window list: see everything_report
    doctor.add_argument("--harness", required=True, choices=list(AGENTS))
    doctor.add_argument("--plugins", required=True)
    doctor.add_argument("--connectors", required=True)  # a plugin they need stays on
    doctor.add_argument("--cwd", required=True, type=Path)
    doctor.add_argument("--settings-file", type=Path)  # the orchestrator's (BOSUN_ORCHESTRATOR_SETTINGS)
    doctor.set_defaults(run=cmd_doctor)
    return top


def main(argv: list[str]) -> int:
    return connectors.run(parser(), argv, say)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
