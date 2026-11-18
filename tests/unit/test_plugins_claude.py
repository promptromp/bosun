"""lib/plugins_claude.py and what it reads through lib/claude_state.py: a Claude Code window's plugin list
turned into the enabledPlugins of its launch settings, from my settings, the folder's and the managed ones."""

from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import claude_state  # noqa: E402
import connector_fakes  # noqa: E402
import plugins  # noqa: E402
import plugins_claude  # noqa: E402
from connectors import ConnectorsError, parse  # noqa: E402


HELPER = "acme-helper@acme-market"
PROBE = "probe@local"
SLACK = "acme-slack@acme-market"


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class PluginsCase(connector_fakes.ScratchCase):
    """My settings, installed_plugins.json and the managed settings folder in a scratch folder, and a
    window's folder (repo) with its project and local settings."""

    def setUp(self) -> None:
        super().setUp()
        self.home_settings = self.root / "home" / ".claude" / "settings.json"
        self.installed = self.root / "home" / ".claude" / "plugins" / "installed_plugins.json"
        self.managed = self.root / "managed"
        for name, value in (
            ("SETTINGS_FILE", self.home_settings),
            ("INSTALLED_PLUGINS", self.installed),
            ("MANAGED_DIR", self.managed),
        ):
            patch = mock.patch.object(claude_state, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.repo = self.root / "repo"
        self.repo.mkdir()

    def switches(self, path: Path, table: dict[str, Any]) -> None:
        write(path, {"permissions": {}, "enabledPlugins": table})

    @property
    def project(self) -> Path:
        return self.repo / ".claude" / "settings.json"

    @property
    def local(self) -> Path:
        return self.repo / ".claude" / "settings.local.json"


class ScopesTest(PluginsCase):
    def test_scopes_merge_key_by_key_the_higher_one_winning(self) -> None:
        self.switches(self.home_settings, {PROBE: True, HELPER: True, SLACK: True})
        self.switches(self.project, {HELPER: False, "repo-tool@acme-market": True})
        self.switches(self.local, {PROBE: False, HELPER: True})
        self.assertEqual(
            claude_state.enabled_plugins(self.repo),
            {PROBE: False, HELPER: True, SLACK: True, "repo-tool@acme-market": True},
        )

    def test_entries_that_are_not_booleans_and_unreadable_files_are_left_out(self) -> None:
        self.switches(self.home_settings, {PROBE: "yes", HELPER: True})
        self.project.parent.mkdir(parents=True)
        self.project.write_text("{not json")
        write(self.local, {"enabledPlugins": [PROBE]})
        self.assertEqual(claude_state.enabled_plugins(self.repo), {HELPER: True})

    def test_managed_files_merge_main_first_then_drop_ins_in_order(self) -> None:
        self.switches(self.managed / "managed-settings.json", {PROBE: True, HELPER: True})
        self.switches(self.managed / "managed-settings.d" / "20-b.json", {HELPER: True})
        self.switches(self.managed / "managed-settings.d" / "10-a.json", {HELPER: False, SLACK: False})
        self.switches(self.managed / "managed-settings.d" / ".30-hidden.json", {PROBE: False})
        self.assertEqual(claude_state.managed_plugins(), {PROBE: True, HELPER: True, SLACK: False})

    def test_no_managed_settings_is_nothing(self) -> None:
        self.assertEqual(claude_state.managed_plugins(), {})

    def test_installed_plugin_ids(self) -> None:
        write(self.installed, {"version": 2, "plugins": {HELPER: [], PROBE: [{"scope": "user"}]}})
        self.assertEqual(claude_state.installed_plugin_ids(), (HELPER, PROBE))
        write(self.installed, {"plugins": []})
        self.assertEqual(claude_state.installed_plugin_ids(), ())


class CommandTest(PluginsCase):
    def run_main(self, plugin_list: str, connector_list: str = "") -> tuple[int, str, str]:
        return connector_fakes.capture(
            plugins_claude.main,
            f"--plugins={plugin_list}",
            f"--connectors={connector_list}",
            *("--cwd", str(self.repo)),
        )

    def test_all_prints_no_limit_and_reads_nothing(self) -> None:
        self.home_settings.parent.mkdir(parents=True)
        self.home_settings.write_text("{not json")
        self.assertEqual(self.run_main(" all "), (0, "{}\n", ""))

    def test_none_switches_off_each_plugin_of_every_scope(self) -> None:
        self.switches(self.home_settings, {PROBE: True, "off@acme-market": False})
        self.switches(self.local, {"repo-tool@acme-market": True})
        write(self.installed, {"plugins": {HELPER: [{"scope": "user"}]}})
        code, out, err = self.run_main("")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            json.loads(out),
            {"enabledPlugins": {HELPER: False, PROBE: False, "repo-tool@acme-market": False}},
        )

    def test_a_list_and_the_connectors_keep_theirs(self) -> None:
        self.switches(self.home_settings, {PROBE: True, HELPER: True, SLACK: True})
        code, out, _ = self.run_main("probe", "plugin:acme-slack:slack, claude.ai Acme Mail")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"enabledPlugins": {HELPER: False}})

    def test_names_not_found_and_managed_plugins_are_said_never_fatal(self) -> None:
        self.switches(self.home_settings, {PROBE: True})
        self.switches(self.managed / "managed-settings.json", {HELPER: True})
        code, out, err = self.run_main("acme-gone")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"enabledPlugins": {PROBE: False}})
        self.assertEqual(
            err.splitlines(),
            [
                "plugins: acme-gone not found, so this window starts without it",
                f"plugins: {HELPER} is forced on by managed settings, so this window loads it anyway",
            ],
        )

    def test_nothing_to_switch_off_is_an_empty_table(self) -> None:
        self.assertEqual(self.run_main(""), (0, '{"enabledPlugins":{}}\n', ""))

    def test_a_plugin_the_orchestrator_s_settings_file_enables_stays_on(self) -> None:
        """Its file merges under the launch's settings, so a `false` there would win over its `true`."""
        self.switches(self.home_settings, {PROBE: True})
        write(self.installed, {"plugins": {HELPER: [{"scope": "user"}]}})
        site = self.root / "orchestrator-settings.json"
        self.switches(site, {HELPER: True, "acme-off@acme-market": False})
        code, out, err = connector_fakes.capture(
            plugins_claude.main,
            "--plugins=",
            "--connectors=",
            "--cwd",
            str(self.repo),
            "--settings-file",
            str(site),
        )
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"enabledPlugins": {PROBE: False}})

    def test_a_scope_that_does_not_parse_is_said_and_the_installed_still_go(self) -> None:
        self.home_settings.parent.mkdir(parents=True)
        self.home_settings.write_text("{not json")
        write(self.installed, {"plugins": {HELPER: [{"scope": "user"}]}})
        code, out, err = self.run_main("")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"enabledPlugins": {HELPER: False}})
        self.assertEqual(
            err, f"plugins: {self.home_settings} does not parse, so what it says of plugins is not read\n"
        )

    def test_an_installed_plugins_file_that_does_not_parse_refuses(self) -> None:
        self.switches(self.home_settings, {PROBE: True})
        self.installed.parent.mkdir(parents=True)
        self.installed.write_text("[not an object]")
        code, out, err = self.run_main("")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(
            err,
            f"plugins: {self.installed} does not parse, so bosun cannot tell which plugins to switch off\n",
        )

    def test_a_list_that_cannot_be_read_is_exit_2(self) -> None:
        code, out, err = self.run_main(f"all, {PROBE}")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("plugins: `all` means every plugin, so it stands alone", err)


class HeadlessTest(PluginsCase):
    """bosun's own headless calls (learning's analysis, the Slack watcher's) load none of my plugins."""

    def settings(self, keep: tuple[str, ...] = ()) -> tuple[dict[str, Any], str]:
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            settings = plugins_claude.headless_settings(self.repo, keep, who="the call")
        return settings, err.getvalue()

    def test_none_of_mine(self) -> None:
        """A plugin my settings enable and one installed that no scope names; one already off stays out."""
        self.switches(self.home_settings, {PROBE: True, "off@acme-market": False})
        write(self.installed, {"plugins": {HELPER: [{"scope": "user"}]}})
        self.assertEqual(self.settings(), ({"enabledPlugins": {HELPER: False, PROBE: False}}, ""))

    def test_a_plugin_it_keeps_stays_on_by_its_bare_name(self) -> None:
        self.switches(self.home_settings, {PROBE: True, SLACK: True})
        self.assertEqual(self.settings(("acme-slack",)), ({"enabledPlugins": {PROBE: False}}, ""))

    def test_nothing_installed_or_enabled_is_an_empty_table(self) -> None:
        self.assertEqual(self.settings(), ({"enabledPlugins": {}}, ""))

    def test_a_plugin_managed_settings_force_on_is_said_not_fought(self) -> None:
        self.switches(self.home_settings, {PROBE: True})
        self.switches(self.managed / "managed-settings.json", {HELPER: True})
        self.assertEqual(
            self.settings(),
            (
                {"enabledPlugins": {PROBE: False}},
                f"plugins: {HELPER} is forced on by managed settings, so the call loads it anyway\n",
            ),
        )

    def test_a_name_it_keeps_that_no_plugin_has_is_not_said(self) -> None:
        """bosun's own rule finding nothing (the alert keeps the Slack plugin, which may not be installed)."""
        self.switches(self.home_settings, {PROBE: True})
        self.assertEqual(self.settings(("slack",)), ({"enabledPlugins": {PROBE: False}}, ""))

    def test_the_command_line_for_the_alert(self) -> None:
        self.switches(self.home_settings, {PROBE: True, SLACK: True})
        code, out, err = connector_fakes.capture(
            plugins_claude.main,
            "--headless",
            "the DM",
            "--keep=acme-slack, acme-gone",
            "--cwd",
            str(self.repo),
        )
        self.assertEqual((code, json.loads(out), err), (0, {"enabledPlugins": {PROBE: False}}, ""))
        code, out, _ = connector_fakes.capture(
            plugins_claude.main, "--headless", "the DM", "--cwd", str(self.repo)
        )
        self.assertEqual(json.loads(out), {"enabledPlugins": {PROBE: False, SLACK: False}})

    def test_the_command_line_refuses_all_as_a_keep_and_a_window_without_its_lists(self) -> None:
        code, out, err = connector_fakes.capture(
            plugins_claude.main, "--headless", "the DM", "--keep=all", "--cwd", str(self.repo)
        )
        self.assertEqual((code, out), (2, ""))
        self.assertIn("`all` is no such list", err)
        code, out, err = connector_fakes.capture(plugins_claude.main, "--plugins=", "--cwd", str(self.repo))
        self.assertEqual(
            (code, out, err), (2, "", "plugins: give --plugins and --connectors, or --headless\n")
        )

    def test_an_installed_plugins_file_that_does_not_parse_refuses(self) -> None:
        self.installed.parent.mkdir(parents=True)
        self.installed.write_text("{not json")
        code, out, _ = connector_fakes.capture(
            plugins_claude.main, "--headless", "the DM", "--cwd", str(self.repo)
        )
        self.assertEqual((code, out), (2, ""))
        with self.assertRaises(ConnectorsError) as caught:
            self.settings()
        self.assertEqual(
            str(caught.exception),
            f"{self.installed} does not parse, so bosun cannot tell which plugins to switch off",
        )


class DoctorTest(PluginsCase):
    def line(self, plugin_list: str, connector_list: str = "") -> tuple[str, str]:
        args = argparse.Namespace(window="orch", kind="session", cwd=self.repo, settings_file=None)
        return plugins_claude.plugins_doctor_line(args, plugins.parse(plugin_list), parse(connector_list))

    def test_doctor_says_why_a_plugin_stays_on_and_what_it_could_not_read(self) -> None:
        self.switches(self.home_settings, {PROBE: True, HELPER: True})
        self.project.parent.mkdir(parents=True)
        self.project.write_text("{not json")
        site = self.root / "orchestrator-settings.json"
        self.switches(site, {HELPER: True})
        args = argparse.Namespace(window="orch", kind="orchestrator", cwd=self.repo, settings_file=site)
        self.assertEqual(
            plugins_claude.plugins_doctor_line(args, plugins.parse(""), parse("")),
            (
                "warn",
                (
                    f"orch: plugins: none of yours (1 switched off); does not parse, so what it says of "
                    f"plugins is not read: {self.project}; kept: the orchestrator's settings file "
                    f"enables {HELPER}"
                ),
            ),
        )

    def test_doctor_says_an_installed_plugins_file_that_does_not_parse_refuses(self) -> None:
        self.installed.parent.mkdir(parents=True)
        self.installed.write_text("{not json")
        with self.assertRaisesRegex(ConnectorsError, "does not parse, so bosun cannot tell which plugins"):
            self.line("")

    def test_doctor_reads_what_the_launch_reads(self) -> None:
        self.switches(self.home_settings, {PROBE: True, HELPER: True, SLACK: True})
        self.assertEqual(self.line("all"), ("ok", "orch: plugins: all of yours"))
        self.assertEqual(self.line(""), ("ok", "orch: plugins: none of yours (3 switched off)"))
        self.assertEqual(
            self.line("probe, acme-gone", "plugin:acme-slack:slack"),
            (
                "warn",
                (
                    f"orch: plugins: keeps {PROBE} (1 switched off); plugins not found, so it starts "
                    f"without: acme-gone; keeps {SLACK} for its server in connectors"
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()
