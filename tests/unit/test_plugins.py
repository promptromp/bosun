"""lib/plugins.py: a window's plugin list, and on Claude Code which plugins its launch switches off."""

from __future__ import annotations

import contextlib
import dataclasses
import sys
import types
import unittest
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import claude_state  # noqa: E402
import connector_fakes  # noqa: E402
import connectors  # noqa: E402
import plugins  # noqa: E402
import plugins_claude  # noqa: E402
from connectors import Allowed, ConnectorsError  # noqa: E402
from plugins import PluginFacts, PluginPlan  # noqa: E402


HELPER = "acme-helper@acme-market"
PROBE = "probe@local"
SLACK = "acme-slack@acme-market"
NONE = Allowed()


def plan(
    value: str,
    enabled: dict[str, bool],
    connectors_list: str = "",
    installed: tuple[str, ...] = (),
    managed: dict[str, bool] | None = None,
) -> PluginPlan:
    facts = PluginFacts(enabled, installed, managed)
    return plugins.plan(plugins.parse(value), connectors.parse(connectors_list), facts)


class ListTest(unittest.TestCase):
    def test_a_list_is_comma_separated_names_each_once(self) -> None:
        self.assertEqual(plugins.parse(f" {PROBE}, acme-helper ,, {PROBE}"), Allowed((PROBE, "acme-helper")))

    def test_a_sessions_yml_list_reads_as_its_joined_string(self) -> None:
        """window_list joins a YAML list with ", ", and [] with nothing."""
        self.assertEqual(plugins.parse(f"{PROBE}, acme-helper").names, (PROBE, "acme-helper"))
        self.assertEqual(plugins.parse(""), NONE)

    def test_all_is_no_limit_and_stands_alone(self) -> None:
        self.assertEqual(plugins.parse(" all "), plugins.ALL)
        with self.assertRaisesRegex(ConnectorsError, "`all` means every plugin, so it stands alone"):
            plugins.parse(f"all, {PROBE}")

    def test_an_id_matches_itself_and_a_bare_name_any_marketplace(self) -> None:
        self.assertTrue(plugins.matches(HELPER, HELPER))
        self.assertTrue(plugins.matches("acme-helper", HELPER))
        self.assertTrue(plugins.matches("acme-helper", "acme-helper@other-market"))
        self.assertFalse(plugins.matches("acme-helper@other-market", HELPER))
        self.assertFalse(plugins.matches("acme", HELPER))

    def test_the_plugins_whose_servers_a_connectors_list_names(self) -> None:
        listed = connectors.parse(
            "plugin:acme-slack:slack, plugin:acme-slack:files, claude.ai Acme, plugin:x"
        )
        self.assertEqual(plugins.server_plugins(listed), ("acme-slack",))
        self.assertEqual(plugins.server_plugins(connectors.parse("all")), ())


class LoadedTest(unittest.TestCase):
    def test_on_in_a_scope_and_installed_but_not_mentioned_load(self) -> None:
        facts = PluginFacts({PROBE: True, "off@acme-market": False}, installed=(HELPER, "off@acme-market"))
        self.assertEqual(plugins.loaded(facts), (HELPER, PROBE))

    def test_managed_settings_force_on_and_block(self) -> None:
        facts = PluginFacts({PROBE: True, HELPER: False}, managed={PROBE: False, HELPER: True})
        self.assertEqual(plugins.loaded(facts), (HELPER,))

    def test_a_plugin_dir_plugin_is_never_mine(self) -> None:
        self.assertEqual(plugins.loaded(PluginFacts({"bosun@inline": True, PROBE: True})), (PROBE,))


class PlanTest(unittest.TestCase):
    def test_all_adds_nothing(self) -> None:
        result = plan("all", {PROBE: True})
        self.assertEqual(result, PluginPlan(everything=True))
        self.assertEqual(plugins_claude.launch_settings(result), {})

    def test_none_switches_off_every_plugin_that_would_load(self) -> None:
        result = plan("", {PROBE: True, HELPER: True, "off@acme-market": False})
        self.assertEqual(result.off, (HELPER, PROBE))
        self.assertEqual(
            plugins_claude.launch_settings(result), {"enabledPlugins": {HELPER: False, PROBE: False}}
        )

    def test_none_with_no_plugin_is_an_empty_switch_table(self) -> None:
        self.assertEqual(plugins_claude.launch_settings(plan("", {})), {"enabledPlugins": {}})

    def test_a_list_keeps_its_plugins_by_id_or_bare_name(self) -> None:
        result = plan("acme-helper", {PROBE: True, HELPER: True, "acme-helper@other": True})
        self.assertEqual(result.kept, (HELPER, "acme-helper@other"))
        self.assertEqual(result.off, (PROBE,))
        self.assertEqual(plan(PROBE, {PROBE: True, HELPER: True}).kept, (PROBE,))

    def test_a_plugin_its_connectors_need_stays_on(self) -> None:
        result = plan("", {PROBE: True, SLACK: True}, "plugin:acme-slack:slack")
        self.assertEqual((result.for_connectors, result.off), ((SLACK,), (PROBE,)))

    def test_a_plugin_on_in_a_lower_scope_and_off_in_a_higher_one_is_not_switched_off(self) -> None:
        """PluginFacts.enabled is the scopes merged (claude_state.enabled_plugins): what the highest says."""
        self.assertEqual(plan("", {PROBE: False}).off, ())

    def test_a_managed_plugin_stays_on_and_is_said(self) -> None:
        result = plan("", {PROBE: True}, managed={HELPER: True})
        self.assertEqual((result.managed, result.off), ((HELPER,), (PROBE,)))
        self.assertNotIn(HELPER, plugins_claude.launch_settings(result)["enabledPlugins"])

    def test_a_name_no_plugin_has_is_not_found(self) -> None:
        result = plan("acme-gone, acme-helper, off", {"off@acme-market": False}, installed=(HELPER,))
        self.assertEqual(result.not_found, ("acme-gone",))
        self.assertEqual(result.kept, (HELPER,))


class SiteTest(unittest.TestCase):
    """A plugin the orchestrator's own settings file enables stays on, even one my settings switch off."""

    def test_it_stays_on_with_its_reason(self) -> None:
        facts = PluginFacts({PROBE: True, HELPER: False}, installed=(SLACK,), site=(HELPER, SLACK))
        result = plugins.plan(plugins.parse(""), connectors.parse(""), facts)
        self.assertEqual((result.for_site, result.off), ((HELPER, SLACK), (PROBE,)))

    def test_its_list_and_connectors_come_first(self) -> None:
        facts = PluginFacts({}, site=(HELPER, SLACK))
        result = plugins.plan(plugins.parse("acme-helper"), connectors.parse("plugin:acme-slack:s"), facts)
        self.assertEqual((result.kept, result.for_connectors, result.for_site), ((HELPER,), (SLACK,), ()))

    def test_a_name_only_the_settings_file_has_is_found(self) -> None:
        self.assertEqual(plan("acme-helper", {}).not_found, ("acme-helper",))
        facts = PluginFacts({}, site=(HELPER,))
        self.assertEqual(plugins.plan(plugins.parse("acme-helper"), NONE, facts).not_found, ())


class ReportTest(unittest.TestCase):
    def test_all(self) -> None:
        self.assertEqual(plugins.report("w", PluginPlan(everything=True)), ("ok", "w: plugins: all of yours"))

    def test_none_and_what_is_kept(self) -> None:
        self.assertEqual(plugins.report("w", PluginPlan()), ("ok", "w: plugins: none of yours"))
        self.assertEqual(
            plugins.report("w", PluginPlan(kept=(PROBE,), off=(HELPER, SLACK))),
            ("ok", f"w: plugins: keeps {PROBE} (2 switched off)"),
        )

    def test_kept_for_connectors_is_a_note(self) -> None:
        self.assertEqual(
            plugins.report("w", PluginPlan(for_connectors=(SLACK,), off=(PROBE,))),
            ("ok", f"w: plugins: none of yours (1 switched off); keeps {SLACK} for its server in connectors"),
        )

    def test_kept_for_the_settings_file_is_a_note_and_an_unread_scope_a_warning(self) -> None:
        self.assertEqual(
            plugins.report("w", PluginPlan(for_site=(HELPER,))),
            ("ok", f"w: plugins: none of yours; kept: the orchestrator's settings file enables {HELPER}"),
        )
        self.assertEqual(
            plugins.report("w", PluginPlan(unread=(Path("/s.json"),))),
            (
                "warn",
                "w: plugins: none of yours; does not parse, so what it says of plugins is not read: /s.json",
            ),
        )

    def test_not_found_and_managed_warn(self) -> None:
        self.assertEqual(
            plugins.report("w", PluginPlan(managed=(HELPER,), not_found=("acme-gone",))),
            (
                "warn",
                (
                    "w: plugins: none of yours; plugins not found, so it starts without: acme-gone; "
                    f"forced on by managed settings, which no launch can switch off: {HELPER}"
                ),
            ),
        )


STANDS_ALONE = "`all` means every plugin, so it stands alone, but the list also names probe"


class DoctorCommandTest(connector_fakes.ScratchCase):
    """`plugins.py doctor` asks the module its window's agent names for plugins (Profile.plugins)."""

    def plugins_doctor(self, plugin_list: str, harness: str = "claude") -> tuple[int, str, str]:
        return connector_fakes.capture(
            plugins.main,
            *("doctor", "--window", "alpha", "--harness", harness, f"--plugins={plugin_list}"),
            *("--connectors", "plugin:acme-slack:slack", "--cwd", str(self.root)),
        )

    def claude_plugins(
        self, plugins_doctor_line: Callable[[Any, Allowed, Allowed], tuple[str, str]]
    ) -> AbstractContextManager[Any]:
        stub = types.SimpleNamespace(plugins_doctor_line=plugins_doctor_line)
        profile = dataclasses.replace(claude_state.PROFILE, plugins="stub_plugins")
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.dict(sys.modules, {"stub_plugins": stub}))
        stack.enter_context(mock.patch.object(claude_state, "PROFILE", profile))
        return stack

    def test_it_hands_the_module_both_lists_parsed(self) -> None:
        seen: list[tuple[Allowed, Allowed]] = []

        def line(args: Any, plugin_list: Allowed, connector_list: Allowed) -> tuple[str, str]:
            seen.append((plugin_list, connector_list))
            return "ok", "stub"

        with self.claude_plugins(line):
            self.assertEqual(self.plugins_doctor("probe"), (0, "ok\tstub\n", ""))
        self.assertEqual(seen, [(Allowed(("probe",)), Allowed(("plugin:acme-slack:slack",)))])

    def test_a_list_that_cannot_be_read_does_not_start(self) -> None:
        self.assertEqual(
            self.plugins_doctor("all, probe", harness="codex"),
            (0, f"warn\talpha: does not start: {STANDS_ALONE}\n", ""),
        )

    def test_it_says_when_the_module_cannot_check(self) -> None:
        def broken(args: Any, plugin_list: Allowed, connector_list: Allowed) -> tuple[str, str]:
            raise RuntimeError("boom")

        with self.claude_plugins(broken):
            self.assertEqual(
                self.plugins_doctor(""),
                (0, "warn\talpha: could not check its plugins (RuntimeError: boom)\n", ""),
            )


if __name__ == "__main__":
    unittest.main()
