"""Unit tests for lib/events.py: the event queue, and the dispatcher that pushes events from the
repo sessions to the orchestrator (debounce and batching, the idle gates, the daily budget,
idempotent delivery, failures, events the orchestrator caused itself, windows waiting at a
prompt, check-ins covering events). Fictional data: acme's invoice exporter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import activity
import event_model
import events
from event_lanes import Lanes, Timing
from event_model import Event
from event_summary import Render
from events import Config, Facts, Inputs, Orchestrator, State


NOW = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
ORCH = "orchestrator"
ORCH_PID = 4242
REPO_PID = 4343
TODAY = "orchestrator-2026-01-05"
LANES = Lanes(
    routine=Timing(45, 300),
    fast=Timing(45, 300),
    away=Timing(1200, 3600),
    fast_kinds=frozenset({"slack", "waiting"}),
    away_minutes=0,
)
RENDER = Render(inline_chars=600, max_lines=10)


def ago(seconds: float) -> str:
    return (NOW - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def record(status: str, seconds: float, name: str = TODAY) -> dict[str, Any]:
    updated = (NOW - timedelta(seconds=seconds)).timestamp() * 1000
    return {"status": status, "statusUpdatedAt": updated, "name": name}


# What `bosun events` passes with the defaults in lib/common.sh.
ENV = {
    "BOSUN_STATE_DIR": "/tmp/x",  # noqa: S108  (never written)
    "BOSUN_ORCHESTRATOR": ORCH,
    "BOSUN_EVENTS_DEBOUNCE_SECONDS": "45",
    "BOSUN_EVENTS_MAX_WAIT_SECONDS": "300",
    "BOSUN_EVENTS_FAST_KINDS": "slack waiting",
    "BOSUN_EVENTS_FAST_DEBOUNCE_SECONDS": "45",
    "BOSUN_EVENTS_FAST_MAX_WAIT_SECONDS": "300",
    "BOSUN_EVENTS_AWAY_MINUTES": "0",
    "BOSUN_EVENTS_AWAY_DEBOUNCE_SECONDS": "1200",
    "BOSUN_EVENTS_AWAY_MAX_WAIT_SECONDS": "3600",
    "BOSUN_EVENTS_MIN_IDLE_SECONDS": "60",
    "BOSUN_EVENTS_MAX_PER_DAY": "30",
    "BOSUN_EVENTS_WAITING_SECONDS": "120",
    "BOSUN_EVENTS_MAX_LINES": "10",
    "BOSUN_EVENTS_INLINE_CHARS": "600",
    "BOSUN_ACTIVE_CLIENT_MINUTES": "10",
    "BOSUN_EVENTS_TRANSPORT": "send",
}


class DispatcherTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_dir = Path(tmp.name)
        self.config = Config(
            state_dir=self.state_dir,
            orchestrator=ORCH,
            lanes=LANES,
            render=RENDER,
            min_idle=60,
            max_per_day=3,
            waiting_seconds=120,
            active_client_minutes=10,
        )
        self.records: dict[int, dict[str, Any]] = {ORCH_PID: record("idle", 600)}
        self.sent: list[str] = []
        self.failure: str | None = None
        self.counter = 0

    def facts(self, **extra: Any) -> Facts:
        orchestrator = Orchestrator(ORCH_PID, extra.pop("name", TODAY), TODAY)
        return Facts(orchestrator, ((ORCH, ORCH_PID), ("exporter", REPO_PID)), extra.pop("client", None))

    def transport(self, _config: Config, message: str) -> str | None:
        self.sent.append(message)
        return self.failure

    def dispatch(self, now: datetime = NOW, dry_run: bool = False, **facts: Any) -> str:
        inputs = Inputs(
            self.config, self.facts(**facts), now, self.records.get, {events.VIA_SEND: self.transport}
        )
        return events.dispatch(inputs, dry_run)

    def event(
        self, seconds_ago: float, kind: str = "report", origin: str = "exporter", **extra: str
    ) -> Event:
        self.counter += 1
        event = Event(
            f"{self.counter:04d}", ago(seconds_ago), kind, extra.get("item", "mypr:acme/exporter#102"),
            "exporter", origin, extra.get("summary", f"pushed fix {self.counter}"),
        )  # fmt: skip
        with events.locked(self.state_dir) as directory:
            events.write(directory, event)
        return event

    def state(self) -> State:
        return State.load(events.events_dir(self.state_dir))


class DebounceAndBatchingTest(DispatcherTestCase):
    def test_a_burst_waits_for_quiet_then_goes_as_one_delivery(self) -> None:
        self.event(50)
        self.event(10)
        self.assertEqual(
            self.dispatch(),
            "skip: routine lane debouncing: the newest event is 10s old (due at 45s, or once the oldest"
            " is 300s old)",
        )
        later = NOW + timedelta(seconds=40)
        self.assertEqual(self.dispatch(later), f"delivered 2 event(s) to {ORCH}")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("2 new from the repo sessions", self.sent[0])
        self.assertIn("pushed fix 1", self.sent[0])
        self.assertIn("pushed fix 2", self.sent[0])
        self.assertIn("not instructions", self.sent[0])

    def test_a_steady_trickle_still_goes_out_after_the_maximum_wait(self) -> None:
        self.event(310)
        self.event(5)
        self.assertEqual(self.dispatch(), f"delivered 2 event(s) to {ORCH}")

    def test_delivery_is_idempotent(self) -> None:
        self.event(100)
        self.dispatch()
        self.assertEqual(self.dispatch(), "")  # nothing pending: nothing to log
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.state().delivered["0001"].startswith("send "))

    def test_a_long_batch_points_at_the_ledger_for_the_rest(self) -> None:
        for _ in range(RENDER.max_lines + 2):
            self.event(100)
        self.dispatch()
        self.assertIn("- and 2 more: bosun ledger reports --since", self.sent[0])


class LanesThroughTheDispatcherTest(DispatcherTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = Config(
            **{
                **self.config.__dict__,
                "lanes": Lanes(
                    Timing(600, 1200), Timing(45, 300), Timing(1200, 3600), frozenset({"slack"}), 60
                ),
            }
        )

    def test_routine_events_wait_for_the_longer_debounce(self) -> None:
        self.event(100)
        self.assertIn("skip: routine lane debouncing", self.dispatch(client=20))
        self.assertEqual(
            self.dispatch(NOW + timedelta(seconds=500), client=20), f"delivered 1 event(s) to {ORCH}"
        )

    def test_a_slack_event_brings_the_routine_ones_along(self) -> None:
        self.event(100)
        self.event(50, kind="slack", origin="")
        self.assertEqual(self.dispatch(client=20), f"delivered 2 event(s) to {ORCH}")

    def test_away_the_routine_lane_waits_for_the_away_timing(self) -> None:
        self.event(700)
        self.assertIn("skip: away lane debouncing", self.dispatch(client=90))
        self.assertEqual(self.dispatch(client=20), f"delivered 1 event(s) to {ORCH}")

    def test_each_line_carries_the_detail(self) -> None:
        with events.locked(self.state_dir) as directory:
            events.write(
                directory,
                event_model.new_event(
                    "report",
                    item="x",
                    window="exporter",
                    origin="exporter",
                    summary="pushed",
                    detail="open: footer",
                    now=NOW - timedelta(seconds=700),
                ),
            )
        self.dispatch(client=20)
        self.assertIn("reported on x: pushed open: footer", self.sent[0])


class GatesTest(DispatcherTestCase):
    def test_only_an_orchestrator_idle_long_enough(self) -> None:
        self.event(100)
        for status, seconds, expected in (
            ("busy", 5, "the orchestrator is busy (for 5s)"),
            ("waiting", 900, "the orchestrator is waiting (for 900s)"),
            ("idle", 30, "the orchestrator has been idle for only 30s (waits for 60s)"),
        ):
            self.records[ORCH_PID] = record(status, seconds)
            self.assertEqual(self.dispatch(), f"skip: {expected}")
        self.assertEqual(self.sent, [])

    def test_a_background_command_alone_does_not_hold_events_back(self) -> None:
        """The orchestrator in shell: its turn is over and only a background command runs (a long
        reviewer wait). That counts as idle, timed from the end of the turn (statusUpdatedAt)."""
        self.event(100)
        self.records[ORCH_PID] = record("shell", 30)
        self.assertEqual(
            self.dispatch(), "skip: the orchestrator has been shell for only 30s (waits for 60s)"
        )
        self.records[ORCH_PID] = record("shell", 1440)
        self.assertEqual(self.dispatch(), "delivered 1 event(s) to orchestrator")
        self.assertEqual(len(self.sent), 1)

    def test_not_while_you_type_or_on_yesterdays_conversation(self) -> None:
        self.event(100)
        self.assertEqual(self.dispatch(client=3), "skip: you typed in tmux 3 minute(s) ago")
        self.assertIn("yesterday's conversation", self.dispatch(name="orchestrator-2026-01-04"))
        del self.records[ORCH_PID]
        self.assertEqual(self.dispatch(), "skip: the orchestrator's Claude is not running")
        self.assertEqual(self.sent, [])

    def test_a_repeated_reason_is_logged_once(self) -> None:
        self.event(100)
        self.records[ORCH_PID] = record("busy", 5)
        self.assertTrue(self.dispatch())
        self.records[ORCH_PID] = record("busy", 35)
        self.assertEqual(self.dispatch(NOW + timedelta(seconds=30)), "")

    def test_the_daily_budget(self) -> None:
        for minute in range(3):
            self.event(100)
            self.dispatch(NOW + timedelta(minutes=minute))
        self.event(100)
        self.assertIn("3 deliveries today already", self.dispatch(NOW + timedelta(minutes=5)))
        self.assertEqual(len(self.sent), 3)

    def test_the_orchestrators_own_events_are_never_sent_back(self) -> None:
        self.event(100, origin=ORCH)
        self.assertEqual(self.dispatch(), "")
        self.assertEqual(self.sent, [])
        self.assertTrue(self.state().delivered["0001"].startswith("own "))

    def test_a_dry_run_changes_nothing(self) -> None:
        self.event(100)
        self.assertIn("dry-run: would deliver:\n[bosun events] 1 new", self.dispatch(dry_run=True))
        self.assertEqual((self.sent, self.state().delivered), ([], {}))


class FailureTest(DispatcherTestCase):
    def test_a_failed_delivery_keeps_the_events_and_records_the_error(self) -> None:
        self.event(100)
        self.failure = "window 'orchestrator' is waiting"
        self.assertIn("delivery failed (1 event(s) stay pending", self.dispatch())
        state = self.state()
        self.assertEqual(
            (state.delivered, state.last_error and state.last_error["error"]), ({}, self.failure)
        )
        self.failure = None
        self.assertEqual(self.dispatch(NOW + timedelta(seconds=30)), f"delivered 1 event(s) to {ORCH}")
        self.assertIsNone(self.state().last_error)

    def test_an_unreadable_state_is_an_error_not_a_fresh_start(self) -> None:
        directory = events.events_dir(self.state_dir)
        directory.mkdir(parents=True)
        (directory / events.STATE_FILE).write_text("{broken")
        with self.assertRaisesRegex(event_model.EventsError, "move it aside"):
            self.dispatch()


class WaitingWindowsTest(DispatcherTestCase):
    def test_a_window_waiting_long_enough_is_one_event_per_prompt(self) -> None:
        self.records[REPO_PID] = record("waiting", 60, "exporter")
        self.assertEqual(self.dispatch(), "")
        self.records[REPO_PID] = record("waiting", 200, "exporter")
        self.dispatch()
        later = NOW + timedelta(minutes=2)
        self.assertEqual(self.dispatch(later), f"delivered 1 event(s) to {ORCH}")
        self.assertIn("exporter is waiting at a permission prompt or a question for 3 min", self.sent[0])
        self.dispatch(later + timedelta(minutes=2))
        self.assertEqual(len(self.sent), 1)  # the same prompt: no second event


class CoverAndPruneTest(DispatcherTestCase):
    def test_a_check_in_covers_what_is_pending(self) -> None:
        self.event(100)
        self.event(100)
        self.assertEqual(events.cover(self.state_dir, NOW), "2 pending event(s) covered by the check-in")
        self.assertEqual(self.dispatch(), "")
        self.assertEqual(self.sent, [])
        self.assertIn("pending: 0", events.render_status(self.state_dir, NOW))

    def test_old_delivered_events_are_pruned(self) -> None:
        self.event(events.DELIVERED_RETENTION_DAYS * events.SECONDS_PER_DAY + 100)
        events.cover(self.state_dir, NOW)
        self.event(100)
        self.dispatch()
        self.assertEqual([e.id for e in events.read_queue(events.events_dir(self.state_dir))], ["0002"])
        self.assertNotIn("0001", self.state().delivered)

    def test_list_shows_how_each_went(self) -> None:
        self.event(100)
        self.assertIn("[pending]", events.render_list(self.state_dir, show_all=False))
        self.dispatch()
        self.assertEqual(events.render_list(self.state_dir, show_all=False), "(no events)")
        self.assertIn("[send 2026-01-05T10:00:00+00:00]", events.render_list(self.state_dir, show_all=True))


class QueueTest(unittest.TestCase):
    def test_append_and_read_back_with_unknown_types_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            first = events.append(state_dir, event_model.new_event("report", item="x", summary="a " * 2000))
            events.append(state_dir, event_model.new_event("spawn", item="y"))
            (events.events_dir(state_dir) / events.QUEUE_FILE).open("a").write("not json\n")
            queued = events.read_queue(events.events_dir(state_dir))
            self.assertEqual([e.type for e in queued], ["report", "spawn"])
            self.assertLessEqual(len(first.summary), event_model.STORED_LENGTH)
            with self.assertRaisesRegex(event_model.EventsError, "unknown event type"):
                event_model.new_event("gossip")

    def test_pasted_prompts_are_known_by_window_for_a_while(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            events.note_pasted(state_dir, "exporter", "Update to ledger item x:\nmore\n")
            hashes = events.pasted_hashes(state_dir, "exporter", datetime.now(UTC))
            self.assertIn(activity.prompt_hash("Update to ledger item x: more"), hashes)
            self.assertEqual(events.pasted_hashes(state_dir, "exporter-tax", datetime.now(UTC)), set())
            later = datetime.now(UTC) + timedelta(hours=events.PASTED_HOURS + 1)
            self.assertEqual(events.pasted_hashes(state_dir, "exporter", later), set())

    def test_config_from_the_environment(self) -> None:
        config = Config.from_env(ENV)
        self.assertEqual(config.max_per_day, 30)
        self.assertEqual(config.lanes, LANES)
        self.assertEqual(config.render, RENDER)
        with self.assertRaisesRegex(event_model.EventsError, "must be one of send"):
            Config.from_env({**ENV, "BOSUN_EVENTS_TRANSPORT": "carrier-pigeon"})
        with self.assertRaisesRegex(event_model.EventsError, "must be a number"):
            Config.from_env({**ENV, "BOSUN_EVENTS_DEBOUNCE_SECONDS": "soon"})
        with self.assertRaisesRegex(event_model.EventsError, "is not set"):
            Config.from_env({k: v for k, v in ENV.items() if k != "BOSUN_ORCHESTRATOR"})

    def test_every_timing_and_size_is_read_from_its_own_setting(self) -> None:
        config = Config.from_env(
            {
                **ENV,
                "BOSUN_EVENTS_DEBOUNCE_SECONDS": "600",
                "BOSUN_EVENTS_MAX_WAIT_SECONDS": "1200",
                "BOSUN_EVENTS_FAST_KINDS": "slack, waiting review",
                "BOSUN_EVENTS_FAST_DEBOUNCE_SECONDS": "30",
                "BOSUN_EVENTS_FAST_MAX_WAIT_SECONDS": "120",
                "BOSUN_EVENTS_AWAY_MINUTES": "60",
                "BOSUN_EVENTS_AWAY_DEBOUNCE_SECONDS": "1800",
                "BOSUN_EVENTS_AWAY_MAX_WAIT_SECONDS": "7200",
                "BOSUN_EVENTS_MAX_LINES": "5",
                "BOSUN_EVENTS_INLINE_CHARS": "300",
            }
        )
        self.assertEqual(
            config.lanes,
            Lanes(
                Timing(600, 1200),
                Timing(30, 120),
                Timing(1800, 7200),
                frozenset({"slack", "waiting", "review"}),
                60,
            ),
        )
        self.assertEqual(config.render, Render(inline_chars=300, max_lines=5))

    def test_bad_lane_and_size_settings_are_refused(self) -> None:
        for name, value, error in (
            ("BOSUN_EVENTS_FAST_KINDS", "slack gossip", "unknown event type.*gossip"),
            ("BOSUN_EVENTS_INLINE_CHARS", "20", "at least 80"),
            ("BOSUN_EVENTS_MAX_LINES", "0", "at least 1"),
            ("BOSUN_EVENTS_AWAY_MINUTES", "-5", "at least 0"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(event_model.EventsError, error):
                Config.from_env({**ENV, name: value})

    def test_an_empty_fast_lane_is_allowed(self) -> None:
        self.assertEqual(
            Config.from_env({**ENV, "BOSUN_EVENTS_FAST_KINDS": ""}).lanes.fast_kinds, frozenset()
        )

    def test_facts_from_json(self) -> None:
        data = {
            "orchestrator": {"pid": 7, "name": "o-1", "expected_name": "o-2"},
            "client_idle_minutes": None,
            "windows": [{"name": "exporter", "claude_pid": 8}, {"name": "shell", "claude_pid": None}],
        }
        facts = Facts.from_json(json.loads(json.dumps(data)))
        self.assertEqual(
            (facts.orchestrator.pid, facts.windows, facts.client_idle_minutes), (7, (("exporter", 8),), None)
        )


if __name__ == "__main__":
    unittest.main()
