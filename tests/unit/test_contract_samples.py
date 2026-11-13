"""tools/contract_samples.py: the JSON the menu bar app's Swift tests decode, written from part 1's own pure
functions (and quit's run over fake ports), so a change to what `bosun status --json` and the rest print
fails here until the fixtures under app/ are written again (`uv run python tools/contract_samples.py`).
Fictional data: acme's invoice exporter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import contract_samples as cs
import update_plan


ROOT = Path(__file__).resolve().parents[2]
SAMPLES = cs.samples()


def names(prefix: str) -> list[str]:
    return sorted(name for name in SAMPLES if name.startswith(prefix))


class FixturesAreTrueTest(unittest.TestCase):
    def test_the_committed_fixtures_are_what_the_tool_writes(self) -> None:
        on_disk = {path.stem: path.read_text() for path in cs.FIXTURES.glob("*.json")}
        written = {name: cs.render(value) for name, value in SAMPLES.items()}
        self.assertEqual(
            sorted(on_disk),
            sorted(written),
            "fixtures and the tool disagree on the files: run `uv run python tools/contract_samples.py`",
        )
        for name, text in written.items():
            with self.subTest(name=name):
                self.assertEqual(on_disk[name], text, f"{name}.json is stale: write the fixtures again")

    def test_main_writes_every_fixture_and_removes_one_that_is_no_longer_made(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "gone.json").write_text("{}")
            cs.write(folder)
            self.assertEqual(sorted(p.stem for p in folder.glob("*.json")), sorted(SAMPLES))
            self.assertEqual(json.loads((folder / "status-ok.json").read_text())["level"], "ok")

    def test_the_fixtures_are_written_the_same_in_any_time_zone(self) -> None:
        import os
        import time

        before = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Pacific/Auckland"
            time.tzset()
            elsewhere = {name: cs.render(value) for name, value in cs.samples().items()}
        finally:
            if before is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = before
            time.tzset()
        self.assertEqual(elsewhere, {name: cs.render(value) for name, value in SAMPLES.items()})


class WhatTheAppReadsTest(unittest.TestCase):
    def test_every_object_but_the_updates_plan_carries_contract_1(self) -> None:
        for name, value in SAMPLES.items():
            if name.startswith(("update-plan", "update-treatments")):
                continue
            with self.subTest(name=name):
                self.assertEqual(value["contract"], 1)

    def test_status_has_each_level_and_the_three_stops_the_menu_tells_apart(self) -> None:
        levels = {name: SAMPLES[name]["level"] for name in names("status-")}
        self.assertEqual(
            levels,
            {
                "status-error": "error",
                "status-ok": "ok",
                "status-quit-unfinished": "error",
                "status-resting": "ok",
                "status-stopped": "stopped",
                "status-update-running": "ok",
                "status-warning": "warning",
            },
        )
        self.assertTrue(SAMPLES["status-stopped"]["stopped"]["morning_paused"])
        self.assertIn("did not finish", SAMPLES["status-quit-unfinished"]["sentence"])
        self.assertIn("resting until 08:00", SAMPLES["status-resting"]["sentence"])
        self.assertEqual(SAMPLES["status-update-running"]["update"]["state"], "running")

    def test_status_has_windows_waiting_for_you_and_a_keep_awake_somewhere(self) -> None:
        self.assertTrue(SAMPLES["status-ok"]["waiting"])
        self.assertIsNotNone(SAMPLES["status-ok"]["keep_awake_until"])
        self.assertTrue(SAMPLES["status-warning"]["problems"])

    def test_the_update_check_has_an_update_a_current_one_a_skipped_one_and_a_failure(self) -> None:
        self.assertTrue(SAMPLES["update-check-available"]["available"])
        self.assertTrue(SAMPLES["update-check-available"]["latest"]["notes"])
        self.assertFalse(SAMPLES["update-check-current"]["available"])
        self.assertTrue(SAMPLES["update-check-skipped"]["skipped"])
        self.assertTrue(SAMPLES["update-check-skipped"]["available"])
        self.assertTrue(SAMPLES["update-check-failed"]["error"])

    def test_the_updates_plan_is_the_dry_runs_object_and_one_of_them_is_blocked_and_one_is_none(self) -> None:
        self.assertEqual(sorted(SAMPLES["update-plan"]), ["plan", "summary", "update"])
        self.assertEqual(SAMPLES["update-plan"]["plan"]["blockers"], [])
        self.assertTrue(SAMPLES["update-plan-blocked"]["plan"]["blockers"])
        self.assertIsNone(SAMPLES["update-plan-none"]["plan"])

    def test_the_plan_has_a_window_for_each_treatment_the_dialog_must_place(self) -> None:
        shown = {how for _name, how in SAMPLES["update-plan"]["plan"]["windows"]}
        self.assertEqual(shown, {t.value for t in update_plan.Treatment})

    def test_the_treatments_are_every_one_bosun_has_so_the_app_cannot_miss_a_new_one(self) -> None:
        self.assertEqual(SAMPLES["update-treatments"], [t.value for t in update_plan.Treatment])

    def test_quit_has_the_plan_with_and_without_a_refusal_and_each_result(self) -> None:
        self.assertEqual(SAMPLES["quit-plan"]["busy"], ["acme-exporter"])
        self.assertEqual(SAMPLES["quit-plan"]["waiting"], ["acme-web"])
        self.assertIsNone(SAMPLES["quit-plan"]["refused"])
        self.assertTrue(SAMPLES["quit-plan-refused"]["refused"])
        results = {name: SAMPLES[name]["result"] for name in names("quit-result-")}
        self.assertEqual(
            results,
            {
                "quit-result-busy": "busy",
                "quit-result-failed": "failed",
                "quit-result-failed-early": "failed",
                "quit-result-refused": "refused",
                "quit-result-stopped": "stopped",
                "quit-result-stopped-console-running": "stopped",
            },
        )

    def test_a_plan_that_could_not_look_is_the_failed_object_with_its_error_and_the_plans_keys(self) -> None:
        failed = SAMPLES["quit-plan-failed"]
        self.assertEqual((failed["result"], failed["contract"]), ("failed", 1))
        self.assertIn("tmux did not answer", failed["error"])
        self.assertEqual(failed["windows"], [])
        self.assertEqual(sorted(failed), sorted(SAMPLES["quit-result-failed-early"]))

    def test_no_fixture_names_the_real_publisher(self) -> None:
        publisher = json.loads((ROOT / "app" / "publisher.json").read_text())
        for name, text in {name: cs.render(value) for name, value in SAMPLES.items()}.items():
            with self.subTest(name=name):
                self.assertNotIn(publisher["bundle_id"], text)
        self.assertNotIn(publisher["bundle_id"], (ROOT / "tools" / "contract_samples.py").read_text())

    def test_a_quit_that_stopped_says_what_it_did_and_a_failed_one_how_to_finish(self) -> None:
        stopped: dict[str, Any] = SAMPLES["quit-result-stopped"]
        self.assertEqual(
            (stopped["console"], stopped["console_port"], stopped["morning_paused"]),
            ("stopped", None, True),
        )
        still = SAMPLES["quit-result-stopped-console-running"]
        self.assertEqual((still["console"], still["console_port"]), ("still running", 8765))
        self.assertIn("quit --stop-now", SAMPLES["quit-result-failed"]["error"])
        self.assertIsNone(SAMPLES["quit-result-busy"].get("error"))
        self.assertTrue(SAMPLES["quit-result-busy"]["busy"])

    def test_app_status_is_installed_and_not_installed(self) -> None:
        self.assertEqual(SAMPLES["app-status"]["installed"], "0.1.0")
        self.assertIsNone(SAMPLES["app-status-not-installed"]["installed"])


if __name__ == "__main__":
    unittest.main()
