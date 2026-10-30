"""Unit tests for the console's Sessions view and health panel (lib/console_sessions.py): window
cards (status and how long, held and active items, working, last report, login, attach command),
the windows waiting at a prompt, and the health model (watchdog checks, morning and chores runs,
events, Slack watcher, login, flags). Fictional data: acme's invoice exporter
(tests/fixtures/console/acme-ledger.json) and hand-made facts."""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_sessions
import events
import login
import slack_watch
import watchdog
from console_live import Facts, Settings


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "console" / "acme-ledger.json"
DATA: dict[str, Any] = json.loads(FIXTURE.read_text())
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
TODAY = NOW.astimezone().date()
TASK = "task:2026-10-20/1"
REVIEW = "review:acme/ledger-sync#57"
SETTINGS = Settings(
    state_dir=Path("/state"),
    log_dir=Path("/state/logs"),
    session="acme-bosun",
    tmux_socket="bosun",
    events_enabled=True,
    events_max_per_day=30,
    slack_watch_enabled=True,
    slack_max_usd_per_day=1.5,
)


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def claude(status: str, seconds: int, **extra: Any) -> dict[str, Any]:
    """A running Claude as `bosun sessions --json` describes it."""
    return {
        "pid": 4100 + seconds,
        "sessionId": "00000000-0000-0000-0000-000000000001",
        "name": "",
        "status": status,
        "statusUpdatedAt": ms(NOW - timedelta(seconds=seconds)),
        "version": "2.0.0",
        "cwd": "/Users/me/repos/exporter",
        "transcript": None,
        **extra,
    }


def window(name: str, kind: str = "session", running: Any = None, exists: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "dir": f"/Users/me/repos/{name}",
        "model": "opus" if kind == "session" else "-",
        "effort": "medium" if kind == "session" else "-",
        "window": exists,
        "claude": running,
    }


WINDOWS = [
    window("orch", "orchestrator", claude("idle", 120)),
    window("exporter", running=claude("busy", 300)),
    window("ledger-sync", running=claude("waiting", 900)),
    window("checkin", "checkin"),
    window("docs", exists=False),
]


def facts(**changes: Any) -> Facts:
    return replace(Facts(at=NOW, settings=SETTINGS, windows=WINDOWS), **changes)


def cards(given: Facts | None = None, data: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    model = console_sessions.sessions_model(given or facts(), DATA if data is None else data, NOW)
    return {card["name"]: card for card in model["windows"]}


class SessionsTestCase(unittest.TestCase):
    def test_one_card_per_window_in_order(self) -> None:
        model = console_sessions.sessions_model(facts(), DATA, NOW)
        self.assertEqual(
            [c["name"] for c in model["windows"]], ["orch", "exporter", "ledger-sync", "checkin", "docs"]
        )
        self.assertEqual(
            model["counts"], {"idle": 1, "busy": 1, "waiting": 1, "no claude": 1, "no window": 1}
        )
        self.assertEqual(model["attach"], "tmux -L bosun attach -t acme-bosun")
        self.assertIsNone(model["error"])

    def test_a_card(self) -> None:
        card = cards()["exporter"]
        self.assertEqual(
            (card["kind"], card["model"], card["effort"], card["state"]),
            ("session", "opus", "medium", "busy"),
        )
        self.assertEqual(card["claude"]["seconds"], 300)
        self.assertEqual(card["claude"]["since"], "2026-10-20T13:55:00Z")
        self.assertEqual(card["attach"], "tmux -L bosun attach -t acme-bosun:exporter")

    def test_unset_model_and_effort_are_empty(self) -> None:
        card = cards()["orch"]
        self.assertEqual((card["model"], card["effort"]), ("", ""))

    def test_held_and_active_items_and_working(self) -> None:
        exporter = cards()["exporter"]
        self.assertEqual([i["id"] for i in exporter["held"]], [TASK])
        self.assertEqual(exporter["active"], TASK)
        self.assertTrue(exporter["working"])  # busy, on its active item
        self.assertTrue(exporter["held"][0]["working"])
        idle = cards(facts(windows=[window("exporter", running=claude("idle", 60))]))["exporter"]
        self.assertEqual((idle["active"], idle["working"]), (TASK, False))  # a turn that ended frees it

    def test_a_window_without_items(self) -> None:
        card = cards()["orch"]
        self.assertEqual(
            (card["held"], card["active"], card["working"], card["last_report"]), ([], None, False, None)
        )

    def test_last_report_per_window(self) -> None:
        reports = {name: c["last_report"] for name, c in cards().items()}
        self.assertEqual(reports["exporter"]["id"], TASK)
        self.assertTrue(reports["exporter"]["summary"].startswith("Opened the credit notes PR"))
        self.assertEqual(reports["ledger-sync"]["id"], REVIEW)

    def test_the_latest_report_wins(self) -> None:
        def reported(item_id: str, at: str) -> dict[str, Any]:
            return {
                "id": item_id,
                "kind": "task",
                "state": "done",
                "report": {"at": at, "window": "w", "summary": at},
            }

        older, newer = (
            reported("task:2026-10-01/1", "2026-10-01T09:00:00+00:00"),
            reported("task:2026-10-02/1", "2026-10-02T09:00:00+00:00"),
        )
        data = {"items": {i["id"]: i for i in (newer, older)}}
        self.assertEqual(console_sessions.last_reports(data["items"].values())["w"]["id"], newer["id"])

    def test_states_without_a_claude(self) -> None:
        self.assertEqual(cards()["checkin"]["state"], console_sessions.NO_CLAUDE)
        self.assertEqual(cards()["docs"]["state"], console_sessions.NO_WINDOW)
        self.assertIsNone(cards()["docs"]["claude"])

    def test_a_record_without_a_status_time(self) -> None:
        running = claude("idle", 0)
        del running["statusUpdatedAt"]
        card = cards(facts(windows=[window("w", running=running)]))["w"]
        self.assertEqual((card["claude"]["seconds"], card["claude"]["since"]), (None, None))

    def test_the_attach_command_is_quoted_and_without_a_socket(self) -> None:
        plain = replace(SETTINGS, tmux_socket="", session="my session")
        self.assertEqual(console_sessions.attach_command(plain, "a;b"), "tmux attach -t 'my session:a;b'")

    def test_unreadable_windows(self) -> None:
        model = console_sessions.sessions_model(facts(windows=None, errors={"sessions": "boom"}), DATA, NOW)
        self.assertEqual((model["windows"], model["error"]), ([], "boom"))

    def test_a_window_login_verdict(self) -> None:
        verdict = login.Verdict(login.NEEDS_LOGIN, "every reply is a login error", NOW)
        card = cards(facts(window_logins={"orch": verdict}))["orch"]
        self.assertEqual(
            card["login"],
            {"state": login.NEEDS_LOGIN, "detail": verdict.detail, "since": "2026-10-20T14:00:00Z"},
        )
        self.assertIsNone(cards()["orch"]["login"])


class WaitingWindowsTestCase(unittest.TestCase):
    def test_windows_waiting_at_a_prompt(self) -> None:
        self.assertEqual(
            console_sessions.waiting_windows(facts(), NOW),
            [{"name": "ledger-sync", "since": "2026-10-20T13:45:00Z", "seconds": 900}],
        )

    def test_longest_first_and_only_existing_windows(self) -> None:
        windows = [
            window("a", running=claude("waiting", 60)),
            window("b", running=claude("waiting", 600)),
            window("gone", running=claude("waiting", 900), exists=False),
        ]
        self.assertEqual(
            [w["name"] for w in console_sessions.waiting_windows(facts(windows=windows), NOW) or []],
            ["b", "a"],
        )

    def test_unknown_when_the_windows_could_not_be_read(self) -> None:
        self.assertIsNone(console_sessions.waiting_windows(facts(windows=None), NOW))


def status_data(findings: list[dict[str, str]] | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        {
            "name": check.name,
            "severity": check.severity,
            "summary": check.summary,
            "note": "fine",
            "findings": [],
        }
        for check in watchdog.CHECKS
    ]
    checks[4]["findings"] = findings or []
    return {
        "at": "2026-10-20 16:00:00",
        "awake_window": "awake",
        "in_window": True,
        "checks": checks,
        "last_run": None,
        "last_alert": None,
    }


def health(given: Facts | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return console_sessions.health_model(given or facts(), DATA if data is None else data, NOW)


class HealthTestCase(unittest.TestCase):
    def test_flags_are_booleans_only(self) -> None:
        self.assertEqual(health()["flags"], {"events": True, "slack_watch": True, "learn": False})
        self.assertTrue(all(isinstance(v, bool) for v in health()["flags"].values()))

    def test_the_watchdog_checks(self) -> None:
        found = [{"message": "ledger-sync waits", "decision": "alerts now"}]
        model = health(facts(checks=status_data(found)))["watchdog"]
        self.assertEqual([c["name"] for c in model["checks"]], [c.name for c in watchdog.CHECKS])
        self.assertEqual(model["problems"], 1)
        self.assertFalse(model["checks"][4]["ok"])
        self.assertTrue(model["checks"][0]["ok"])
        self.assertIsNone(model["error"])

    def test_the_watchdog_unavailable(self) -> None:
        model = health(facts(errors={"watchdog": "watchdog --facts exited 1"}))["watchdog"]
        self.assertEqual(
            (model["checks"], model["problems"], model["error"]), ([], 0, "watchdog --facts exited 1")
        )

    def test_the_morning_run_and_the_chores(self) -> None:
        run = watchdog.MorningRun(datetime(2026, 10, 20, 8, 1), "launchd", "problems", ["[setup] FAIL: aws"])
        data = {
            **DATA,
            "runs": {"morning": "2026-10-20T06:02:00+00:00", "check-in": "2026-10-20T13:00:00+00:00"},
        }
        model = health(facts(morning=run), data)
        self.assertEqual(model["morning"]["outcome"], "problems")
        self.assertEqual(model["morning"]["problems"], ["[setup] FAIL: aws"])
        self.assertEqual([c["name"] for c in model["chores"]], ["check-in", "morning"])
        self.assertIsNone(health()["morning"])
        self.assertEqual(health()["chores"], [])

    def test_events(self) -> None:
        today = NOW.isoformat(timespec="seconds")
        state = events.State(
            deliveries=[
                {"at": today, "via": events.VIA_SEND, "events": ["1"]},
                {"at": today, "via": events.VIA_CHECKIN, "events": ["2"]},
                {"at": (NOW - timedelta(days=2)).isoformat(), "via": events.VIA_SEND, "events": ["3"]},
            ],
            last_tick=today,
        )
        model = health(facts(events=state, events_pending=4))["events"]
        self.assertEqual((model["deliveries_today"], model["max_per_day"], model["pending"]), (1, 30, 4))
        self.assertEqual(model["last_tick"], today)
        self.assertEqual(health()["events"]["deliveries_today"], 0)

    def test_the_slack_watcher(self) -> None:
        state = slack_watch.State(
            last_started="2026-10-20T13:45:00+00:00",
            failures=2,
            last_error={"at": "2026-10-20T13:46:00+00:00", "error": "timeout"},
            spend={TODAY.isoformat(): 0.42, "2026-10-01": 1.0},
            budget_spent_on=TODAY.isoformat(),
            runs=[{"started": "2026-10-20T13:45:00+00:00", "outcome": "failed", "error": "timeout"}],
        )
        model = health(facts(slack=state))["slack_watch"]
        self.assertEqual((model["spend_today"], model["cap"], model["failures"]), (0.42, 1.5, 2))
        self.assertTrue(model["budget_spent_today"] and model["active"])
        self.assertEqual(model["last_run"]["outcome"], "failed")
        off = health(facts(settings=replace(SETTINGS, events_enabled=False)))["slack_watch"]
        self.assertEqual((off["enabled"], off["active"], off["last_run"]), (True, False, None))

    def test_login_from_the_last_check_and_the_windows(self) -> None:
        def state_of(**changes: Any) -> str:
            return str(health(facts(**changes))["login"]["state"])

        good = login.State(probe={"at": NOW.isoformat(), "outcome": "ok"})
        bad = login.State(
            probe={"at": NOW.isoformat(), "outcome": "auth", "code": "401"}, gated={"orch": NOW.isoformat()}
        )
        self.assertEqual(state_of(), login.UNKNOWN)
        self.assertEqual(state_of(login_state=good), login.OK)
        self.assertEqual(state_of(login_state=bad), login.NEEDS_LOGIN)
        verdict = login.Verdict(login.NEEDS_LOGIN, "login errors", NOW)
        self.assertEqual(state_of(login_state=good, window_logins={"orch": verdict}), login.NEEDS_LOGIN)
        model = health(facts(login_state=bad))["login"]
        self.assertEqual(model["gated"], [{"window": "orch", "since": NOW.isoformat()}])
        self.assertEqual(model["last_check"]["outcome"], "auth")

    def test_it_says_what_it_leaves_out(self) -> None:
        self.assertTrue(any("claude auth status" in text for text in health()["left_out"]))


if __name__ == "__main__":
    unittest.main()
