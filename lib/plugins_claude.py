#!/usr/bin/env python3
"""A Claude Code window's list of plugins (BOSUN_PLUGINS, BOSUN_ORCHESTRATOR_PLUGINS, `plugins:` in
sessions.yml) turned into the `enabledPlugins` of its launch settings: a `false` for each plugin of mine its
settings scopes would load that the list leaves out, unless its connectors need it. Claude Code ranks the
launch's `--settings` above my local, project and user settings, merging `enabledPlugins` key by key, so
that `false` wins; only managed settings rank above it. The plan is lib/plugins.py's; what Claude Code's
files say, lib/claude_state.py's. Design: docs/design/connectors.md, "Plugins".

Usage: plugins_claude.py --plugins <value> --connectors <value> --cwd <dir>
           Claude Code's settings, always a JSON object: {} for `all`, else {"enabledPlugins": {...}} with a
           `false` per plugin switched off (none: an empty one). A line on stderr per name no plugin has (left
           out, never fatal) and per plugin managed settings force on. Reads files only: never `claude`.

Exit codes: 0 done, 2 a list or command line that cannot be read; anything else (and no output) is a failure
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
from connectors import Allowed
from plugins import say


def facts(cwd: Path) -> plugins.PluginFacts:
    """What Claude Code's files say of the plugins of a window started in <cwd>."""
    return plugins.PluginFacts(
        claude_state.enabled_plugins(cwd), claude_state.installed_plugin_ids(), claude_state.managed_plugins()
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
        return plugins.report(args.window, plugins.PluginPlan(everything=True))
    return plugins.report(args.window, plugins.plan(plugin_list, connector_list, facts(args.cwd)))


def cmd_plugins(args: argparse.Namespace) -> int:
    plugin_list = plugins.parse(args.plugins)
    if plugin_list.everything:
        print("{}")
        return 0
    plan = plugins.plan(plugin_list, connectors.parse(args.connectors), facts(args.cwd))
    for name in plan.not_found:
        say(f"{name} not found, so this window starts without it")
    for plugin in plan.managed:
        say(f"{plugin} is forced on by managed settings, so this window loads it anyway")
    print(json.dumps(launch_settings(plan), separators=(",", ":")))
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="plugins_claude.py", description="Claude Code's enabledPlugins settings."
    )
    top.add_argument("--plugins", required=True)
    top.add_argument("--connectors", required=True)
    top.add_argument("--cwd", required=True, type=Path)
    top.set_defaults(run=cmd_plugins)
    return top


def main(argv: list[str]) -> int:
    return connectors.run(parser(), argv, say)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
