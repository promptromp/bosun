#!/usr/bin/env python3
"""A Claude Code window's list of plugins (BOSUN_PLUGINS, BOSUN_ORCHESTRATOR_PLUGINS, `plugins:` in
sessions.yml) turned into the `enabledPlugins` of its launch settings: a `false` for each plugin of mine its
settings scopes would load that the list leaves out, unless its connectors need it or the orchestrator's own
settings file enables it. Claude Code ranks the launch's `--settings` above my local, project and user
settings, merging `enabledPlugins` key by key, so that `false` wins; only managed settings rank above it. The
plan is lib/plugins.py's; what Claude Code's files say, lib/claude_state.py's. Design:
docs/design/connectors.md, "Plugins".

Usage: plugins_claude.py --plugins <value> --connectors <value> --cwd <dir> [--settings-file <file>]
           Claude Code's settings, always a JSON object: {} for `all`, else {"enabledPlugins": {...}} with a
           `false` per plugin switched off (none: an empty one). --settings-file is the orchestrator's
           settings file (BOSUN_ORCHESTRATOR_SETTINGS), whose own `enabledPlugins` merges under these: a
           plugin it enables stays on. A line on stderr per name no plugin has (left out, never fatal),
           per plugin managed settings force on, and per settings scope that does not parse (what it says
           of plugins is not read; the installed plugins still get their `false`). Reads files only: never
           `claude`.

Exit codes: 0 done, 2 a list or command line that cannot be read, or an installed_plugins.json that is there
but does not parse (nothing can tell which plugins to switch off); anything else (and no output) is a failure
the launch stops on.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import connectors
import plugins
from agent_files import read_object
from connectors import Allowed, ConnectorsError
from plugins import say


def site_plugins(settings_file: Path | None) -> tuple[str, ...]:
    """The plugins the orchestrator's settings file switches on; none without one."""
    if settings_file is None:
        return ()
    switches = claude_state.plugin_switches(read_object(settings_file))
    return tuple(plugin for plugin, on in switches.items() if on)


def facts(cwd: Path, settings_file: Path | None = None) -> plugins.PluginFacts:
    """What Claude Code's files say of the plugins of a window started in <cwd>; ConnectorsError when
    installed_plugins.json is there but does not parse."""
    try:
        installed = claude_state.installed_plugin_ids()
    except claude_state.UnreadableFile as exc:
        raise ConnectorsError(
            f"{exc.path} does not parse, so bosun cannot tell which plugins to switch off"
        ) from exc
    return plugins.PluginFacts(
        claude_state.enabled_plugins(cwd),
        installed,
        claude_state.managed_plugins(),
        site_plugins(settings_file),
        claude_state.unread_scopes(cwd),
    )


def launch_settings(plan: plugins.PluginPlan) -> dict[str, Any]:
    """Claude Code's launch settings for a plan: {} for `all`, else an `enabledPlugins` with a `false` per
    plugin switched off (an empty one when there is none), which outranks my user, project and local
    settings, never managed ones."""
    if plan.everything:
        return {}
    return {claude_state.ENABLED_PLUGINS: dict.fromkeys(plan.off, False)}


def plugins_doctor_line(
    args: argparse.Namespace, plugin_list: Allowed, connector_list: Allowed
) -> tuple[str, str]:
    """The doctor's level and line for a Claude Code window's plugins, from the files its launch reads."""
    if plugin_list.everything:
        return plugins.everything_report(args.window, args.kind)
    window_facts = facts(args.cwd, args.settings_file)
    return plugins.report(args.window, plugins.plan(plugin_list, connector_list, window_facts))


def say_plan(plan: plugins.PluginPlan) -> None:
    """What the launch logs of a plan: each name not found, each plugin forced on, each scope not read."""
    for name in plan.not_found:
        say(f"{name} not found, so this window starts without it")
    for plugin in plan.managed:
        say(f"{plugin} is forced on by managed settings, so this window loads it anyway")
    for path in plan.unread:
        say(f"{path} does not parse, so what it says of plugins is not read")


def cmd_plugins(args: argparse.Namespace) -> int:
    plugin_list = plugins.parse(args.plugins)
    if plugin_list.everything:
        print("{}")
        return 0
    plan = plugins.plan(plugin_list, connectors.parse(args.connectors), facts(args.cwd, args.settings_file))
    say_plan(plan)
    print(json.dumps(launch_settings(plan), separators=(",", ":")))
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="plugins_claude.py", description="Claude Code's enabledPlugins settings."
    )
    top.add_argument("--plugins", required=True)
    top.add_argument("--connectors", required=True)
    top.add_argument("--cwd", required=True, type=Path)
    top.add_argument("--settings-file", type=Path)  # the orchestrator's (BOSUN_ORCHESTRATOR_SETTINGS)
    top.set_defaults(run=cmd_plugins)
    return top


def main(argv: list[str]) -> int:
    return connectors.run(parser(), argv, say)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
