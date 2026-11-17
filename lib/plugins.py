"""What plugins of mine a window loads: its list (BOSUN_PLUGINS, BOSUN_ORCHESTRATOR_PLUGINS, `plugins:` in
sessions.yml), and on Claude Code the plan of which plugin ids its launch switches off. Pure: what Claude
Code's files say comes in as PluginFacts (lib/plugins_claude.py reads them); each agent's module turns the
list into what that agent takes, and says it in bosun doctor's line (its `plugins_doctor_line`, in the
module its profile's `plugins` names). Design: docs/design/connectors.md, "Plugins".

A list is comma-separated plugin names: an id as `enabledPlugins` writes it (`acme-helper@acme-market`), or a
bare name (`acme-helper`), which matches that plugin from any marketplace. Empty means none of mine; `all`
means no limit (the launch adds nothing). A plugin the window's connectors need (its list names one of the
plugin's servers, `plugin:<plugin>:<server>`) stays on. bosun's own orchestrator plugin, which the launch
loads with --plugin-dir (an `@inline` id), is never one of mine.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import connectors
from connectors import Allowed


SERVER_PREFIX = "plugin:"  # how Claude Code names a plugin's MCP server: plugin:<plugin>:<server>
SESSION_ONLY = "@inline"  # a plugin a launch loads by --plugin-dir, such as bosun's own: never switched off

# bosun doctor's words, which every agent's module shares
EVERYTHING_LINE = "plugins: all of yours"
NONE_LINE = "plugins: none of yours"
KEEPS_LINE = "plugins: keeps {}"
NOT_FOUND = "plugins not found, so it starts without: {}"
FOR_CONNECTORS = "keeps {} for its server in connectors"
MANAGED = "forced on by managed settings, which no launch can switch off: {}"


ALL = Allowed(everything=True)  # the list `all`: no limit


def say(line: str) -> None:
    print(f"plugins: {line}", file=sys.stderr)


def parse(value: str) -> Allowed:
    """A window's plugin list; ConnectorsError for `all` with other names."""
    return connectors.parse(value, connectors.PLUGINS_EVERYTHING)


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
    other source."""

    enabled: Mapping[str, bool]
    installed: Sequence[str] = ()
    managed: Mapping[str, bool] | None = None


def loaded(facts: PluginFacts) -> tuple[str, ...]:
    """The plugins a window with no limit loads, sorted: each its settings switch on, and each installed one
    they do not mention (its manifest's defaultEnabled, true unless the author says otherwise: a `false` for
    one that was off anyway changes nothing), and each the managed settings force on; never one they block,
    nor an `@inline` one."""
    managed = facts.managed or {}
    return tuple(
        sorted(p for p in _switched_on(facts) if managed.get(p) is not False and not p.endswith(SESSION_ONLY))
    )


def _switched_on(facts: PluginFacts) -> set[str]:
    """What the settings switch on, what is installed that they do not mention, what managed ones force."""
    unmentioned = {plugin for plugin in facts.installed if plugin not in facts.enabled}
    forced = {plugin for plugin, on in (facts.managed or {}).items() if on}
    return {plugin for plugin, on in facts.enabled.items() if on} | unmentioned | forced


def known(facts: PluginFacts) -> set[str]:
    """Every plugin id the facts name: installed, mentioned by a settings scope, or by managed settings."""
    return {*facts.enabled, *facts.installed, *(facts.managed or {})}


@dataclass(frozen=True)
class PluginPlan:
    """What a window's launch does with the plugins it would load: <kept> by its list, <for_connectors> for
    a server its connectors name, <off> switched off, <managed> left on as managed settings force them on
    (the list leaves them out); and <not_found>, the names of its list no plugin has. <everything>: `all`,
    nothing added."""

    everything: bool = False
    kept: tuple[str, ...] = ()
    for_connectors: tuple[str, ...] = ()
    off: tuple[str, ...] = ()
    managed: tuple[str, ...] = ()
    not_found: tuple[str, ...] = ()


def _allowed_by(names: Iterable[str], plugin_id: str) -> bool:
    return any(matches(name, plugin_id) for name in names)


def _allowed_by_any(name: str, ids: Iterable[str]) -> bool:
    return any(matches(name, plugin_id) for plugin_id in ids)


def plan(plugin_list: Allowed, connector_list: Allowed, facts: PluginFacts) -> PluginPlan:
    """Which of the plugins a window would load (loaded) its launch keeps, and which it switches off."""
    if plugin_list.everything:
        return PluginPlan(everything=True)
    managed = facts.managed or {}
    needed = server_plugins(connector_list)
    kept, for_connectors, off, forced = [], [], [], []
    for plugin in loaded(facts):
        if _allowed_by(plugin_list.names, plugin):
            kept.append(plugin)
        elif _allowed_by(needed, plugin):
            for_connectors.append(plugin)
        elif managed.get(plugin) is True:
            forced.append(plugin)
        else:
            off.append(plugin)
    ids = known(facts)
    not_found = tuple(name for name in plugin_list.names if not _allowed_by_any(name, ids))
    return PluginPlan(False, tuple(kept), tuple(for_connectors), tuple(off), tuple(forced), not_found)


def report(window: str, plugin_plan: PluginPlan) -> tuple[str, str]:
    """A Claude Code window's doctor line: what it loads of mine, how many it switches off, what it keeps
    for its connectors (a note), and each name not found and each plugin managed settings force on (a
    warning)."""
    if plugin_plan.everything:
        return connectors.OK, f"{window}: {EVERYTHING_LINE}"
    reach = KEEPS_LINE.format(", ".join(plugin_plan.kept)) if plugin_plan.kept else NONE_LINE
    if plugin_plan.off:
        reach += f" ({len(plugin_plan.off)} switched off)"
    problems = [
        connectors.part(NOT_FOUND, plugin_plan.not_found),
        connectors.part(MANAGED, plugin_plan.managed),
    ]
    return connectors.report(
        window, reach, problems, [connectors.part(FOR_CONNECTORS, plugin_plan.for_connectors)]
    )
