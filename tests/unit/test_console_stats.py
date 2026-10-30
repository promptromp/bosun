"""Unit tests for the console's Stats view (lib/console_stats.py) and the state files it reads
(lib/console_state.py): each metric over hand-made ledgers, the edge cases (no history, odd times,
reopened items, replies before a send), the period, and the fixture ledger. Fictional data: acme's
invoice exporter."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_state
import console_stats
from console_data import QueryError
from console_stats import Own, Period


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "console" / "acme-ledger.json"
NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
WEEK = Period(7, NOW, UTC)
HOUR = 3600.0


def at(days_ago: float, hour: int = 12) -> str:
    """An ISO time <days_ago> days before NOW's day, at <hour> UTC."""
    moment = NOW.replace(hour=hour) - timedelta(days=days_ago)
    return moment.isoformat()


def item(item_id: str, kind: str, state: str, history: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    created = history[0]["at"] if history else None
    return {"id": item_id, "kind": kind, "state": state, "created_at": created, "history": history, **extra}


def created(when: str, state: str) -> dict[str, Any]:
    return {"at": when, "event": "created", "state": state}


def moved(when: str, source: str, target: str, **extra: Any) -> dict[str, Any]:
    return {"at": when, "event": "transition", "from": source, "to": target, **extra}


def ledger_of(*items: dict[str, Any], runs: dict[str, str] | None = None) -> dict[str, Any]:
    return {"items": {i["id"]: i for i in items}, "runs": runs or {}, "sources": {}}


class ParamsTestCase(unittest.TestCase):
    def test_days(self) -> None:
        self.assertEqual(console_stats.parse_days({}), console_stats.DEFAULT_DAYS)
        for days in (7, 30, 90):
            self.assertEqual(console_stats.parse_days({"days": [str(days)]}), days)
        for wrong in (["1"], ["365"], ["week"], [""], ["7", "30"], ["-7"], ["7.0"]):
            with self.subTest(days=wrong), self.assertRaises(QueryError):
                console_stats.parse_days({"days": wrong})

    def test_period_is_local_days_today_included(self) -> None:
        self.assertEqual(WEEK.day_list()[0], "2026-10-14")
        self.assertEqual(WEEK.day_list()[-1], "2026-10-20")
        self.assertEqual(len(Period(90, NOW, UTC).day_list()), 90)
        self.assertTrue(WEEK.has(datetime(2026, 10, 14, 0, 0, tzinfo=UTC)))
        self.assertFalse(WEEK.has(datetime(2026, 10, 13, 23, 59, tzinfo=UTC)))
        self.assertFalse(WEEK.has(NOW + timedelta(seconds=1)))  # the future is not in it
        self.assertFalse(WEEK.has(None))

    def test_times(self) -> None:
        self.assertEqual(console_stats.moment("2026-10-20T10:00:00"), datetime(2026, 10, 20, 10, tzinfo=UTC))
        self.assertEqual(console_stats.moment("2026-10-20T10:00:00Z"), datetime(2026, 10, 20, 10, tzinfo=UTC))
        for odd in (None, "", "yesterday", 1700000000, "2026-13-01T00:00:00"):
            with self.subTest(value=odd):
                self.assertIsNone(console_stats.moment(odd))
        self.assertIsNone(console_stats.median([]))
        self.assertEqual(console_stats.median([1, 3, 10]), 3)


class ThroughputTestCase(unittest.TestCase):
    def test_opened_and_closed_per_day_by_kind(self) -> None:
        items = [
            item(
                "mypr:acme/exporter#1",
                "my-pr",
                "merged",
                [created(at(2), "awaiting-review"), moved(at(1), "awaiting-review", "merged")],
            ),
            item("task:2026-10-19/1", "task", "proposed", [created(at(1), "proposed")]),
            # opened before the period, closed in it
            item(
                "slack:C0000000001/1.0",
                "mention",
                "done",
                [created(at(20), "new"), moved(at(0), "new", "done")],
            ),
            # closed, then reopened: not closed now
            item(
                "slack:C0000000001/2.0",
                "mention",
                "new",
                [created(at(3), "new"), moved(at(3), "new", "dismissed"), moved(at(2), "dismissed", "new")],
            ),
        ]
        model = console_stats.throughput(items, WEEK)
        self.assertEqual(model["opened"], 3)
        self.assertEqual(model["closed"], 2)
        self.assertEqual(model["by_kind"]["mention"], {"opened": 1, "closed": 1})
        days = {d["date"]: d for d in model["days"]}
        self.assertEqual(
            days["2026-10-19"], {"date": "2026-10-19", "opened": {"task": 1}, "closed": {"my-pr": 1}}
        )
        self.assertEqual(days["2026-10-20"]["closed"], {"mention": 1})
        self.assertEqual(len(model["days"]), 7)

    def test_the_last_close_counts(self) -> None:
        history = [
            created(at(30), "new"),
            moved(at(30), "new", "done"),
            moved(at(4), "done", "new"),
            moved(at(4), "new", "done"),
        ]
        model = console_stats.throughput([item("slack:C0000000001/3.0", "mention", "done", history)], WEEK)
        self.assertEqual(model["closed"], 1)

    def test_no_history_and_odd_entries(self) -> None:
        bare = {
            "id": "ghissue:acme/exporter#7",
            "kind": "ticket",
            "state": "done",
            "created_at": at(1),
            "updated_at": at(0),
        }
        odd = {
            "id": "x",
            "kind": "mystery",
            "state": "?",
            "history": ["not an entry", {"event": "created"}],
            "created_at": "not a time",
        }
        model = console_stats.throughput([bare, odd], WEEK)
        self.assertEqual(model["by_kind"]["ticket"], {"opened": 1, "closed": 1})  # closed: updated_at
        self.assertNotIn("mystery", model["by_kind"])
        self.assertEqual(console_stats.odd_times([bare, odd]), 1)


class PrsAndReviewsTestCase(unittest.TestCase):
    def test_my_prs_merged_and_median(self) -> None:
        items = [
            item(
                "mypr:acme/exporter#1",
                "my-pr",
                "merged",
                [created(at(3, 10), "awaiting-review"), moved(at(3, 12), "awaiting-review", "merged")],
            ),
            item(
                "mypr:acme/exporter#2",
                "my-pr",
                "merged",
                [created(at(5, 10), "awaiting-review"), moved(at(4, 16), "approved", "merged")],
            ),
            item(
                "mypr:acme/exporter#3",
                "my-pr",
                "merged",
                [created(at(40), "awaiting-review"), moved(at(30), "awaiting-review", "merged")],
            ),
            item(
                "mypr:acme/exporter#4",
                "my-pr",
                "closed",
                [created(at(2), "awaiting-review"), moved(at(1), "awaiting-review", "closed")],
            ),
        ]
        model = console_stats.my_prs(items, WEEK)
        self.assertEqual(model["merged"], 2)
        self.assertEqual(model["median_open_to_merge_seconds"], (2 * HOUR + 30 * HOUR) / 2)

    def test_merge_time_uses_happened_at(self) -> None:
        merged = moved(at(0), "awaiting-review", "merged", happened_at=at(1, 12))
        pr = item("mypr:acme/exporter#5", "my-pr", "merged", [created(at(1, 10), "awaiting-review"), merged])
        self.assertEqual(console_stats.my_prs([pr], WEEK)["median_open_to_merge_seconds"], 2 * HOUR)

    def test_review_rounds_and_turnaround(self) -> None:
        review = item(
            "review:acme/ledger-sync#57",
            "review-request",
            "reviewed",
            [
                created(at(3, 9), "requested"),
                moved(at(3, 10), "requested", "delegated"),
                moved(at(3, 13), "delegated", "reviewed"),
                moved(at(1, 9), "reviewed", "rereview-requested"),
                moved(at(1, 10), "rereview-requested", "reviewed"),
            ],
        )
        design = item(
            "td:100001",
            "td-review",
            "commented",
            [created(at(2, 8), "requested"), moved(at(2, 16), "prepared", "commented")],
        )
        old = item(
            "review:acme/ledger-sync#1",
            "review-request",
            "closed",
            [created(at(60), "requested"), moved(at(59), "requested", "reviewed")],
        )
        model = console_stats.reviews([review, design, old], WEEK)
        self.assertEqual(model["done"], 3)
        self.assertEqual(model["items"], 2)
        self.assertEqual(model["median_turnaround_seconds"], 4 * HOUR)  # of 4h, 1h and 8h


class TasksTestCase(unittest.TestCase):
    def test_tasks_and_handovers(self) -> None:
        accepted = moved(at(2, 10), "proposed", "accepted", evidence="user: yes")
        task = item(
            "task:2026-10-18/1",
            "task",
            "done",
            [
                created(at(2, 9), "proposed"),
                accepted,
                {"at": at(2, 11), "event": "delegated", "window": "exporter", "task": "new-work"},
                moved(at(2, 11), "accepted", "delegated"),
                {"at": at(1, 9), "event": "moved", "from": "exporter", "to": "ledger-sync"},
                {"at": at(1, 15), "event": "report", "window": "ledger-sync", "note": "done"},
                moved(at(1, 16), "delegated", "done"),
            ],
        )
        declined = item(
            "task:2026-10-19/1",
            "task",
            "declined",
            [created(at(1), "proposed"), moved(at(1), "proposed", "declined")],
        )
        review = item(
            "review:acme/exporter#9",
            "review-request",
            "reviewed",
            [
                created(at(0, 8), "requested"),
                {"at": at(0, 9), "event": "delegated", "window": "exporter"},
                {"at": at(0, 10), "event": "report", "window": "exporter"},
            ],
        )
        model = console_stats.tasks([task, declined, review], WEEK)
        self.assertEqual(
            (model["spawned"], model["accepted"], model["done"], model["declined"]), (2, 1, 1, 1)
        )
        self.assertEqual(model["handovers"], 3)
        self.assertEqual(
            model["per_window"], [{"window": "exporter", "count": 2}, {"window": "ledger-sync", "count": 1}]
        )
        # exporter 11:00 -> report next day 15:00 (28h); moved 9:00 -> 15:00 (6h); review 1h
        self.assertEqual(model["median_handover_to_report_seconds"], 6 * HOUR)

    def test_taken_back_is_not_accepted(self) -> None:
        task = item(
            "task:2026-10-19/2",
            "task",
            "accepted",
            [created(at(30), "proposed"), moved(at(1), "delegated", "accepted")],
        )
        self.assertEqual(console_stats.tasks([task], WEEK)["accepted"], 0)


def outreach(
    item_id: str, state: str, messages: list[str], purpose: str = "question", **fields: Any
) -> dict[str, Any]:
    sent = [{"at": when, "channel": "slack", "ref": f"ref-{n}"} for n, when in enumerate(messages)]
    history = [created(messages[0] if messages else at(1), "draft")]
    return item(item_id, "outreach", state, history, fields={"purpose": purpose, "messages": sent, **fields})


OUTREACH = (
    outreach(
        "out:2026-10-18/1",
        "replied",
        [at(2, 9), at(1, 9)],
        to="Dana (finance)",
        answer="yes",
        replied_at=at(1, 11),
    ),
    outreach("out:2026-10-18/2", "applied", [at(2, 10)], to="dana, Lee", answer="no", replied_at=at(2, 12)),
    outreach("out:2026-10-18/3", "no-reply", [at(2, 9), at(1, 9), at(0, 9)], to="Marco"),
    outreach("out:2026-10-19/1", "sent", [at(1, 9)], to="Marco and Priya"),
    outreach("out:2026-10-19/2", "applied", [at(1, 9)], purpose="fyi", to="exporter channel"),
    # replied before anything was sent: never sent
    outreach("out:2026-10-19/3", "replied", [], to="Omar", answer="ok", replied_at=at(1)),
    # sent before the period
    outreach("out:2026-09-01/1", "replied", [at(50)], to="Omar", answer="ok", replied_at=at(49)),
    # a reply recorded before its message (an odd record): replied, but no reply time
    outreach("out:2026-10-20/1", "replied", [at(0, 12)], to="Lee", answer="ok", replied_at=at(0, 9)),
)


class OutreachAndPeopleTestCase(unittest.TestCase):
    def test_outreach(self) -> None:
        model = console_stats.outreach(console_stats.sent_in(list(OUTREACH), WEEK))
        self.assertEqual(model["sent"], 6)
        self.assertEqual(model["one_way"], 1)
        self.assertEqual((model["replied"], model["no_reply"], model["waiting"]), (3, 1, 1))
        self.assertEqual(model["nudges"], 3)
        self.assertEqual(model["nudges_per_reply"], round(1 / 3, 2))
        self.assertEqual(model["median_reply_seconds"], (26 * HOUR + 2 * HOUR) / 2)

    def test_reply_time_from_the_transition_without_replied_at(self) -> None:
        one = outreach("out:2026-10-20/2", "replied", [at(0, 9)], to="Lee", answer="ok")
        one["history"].append(moved(at(0, 10), "sent", "replied"))
        model = console_stats.outreach(console_stats.sent_in([one], WEEK))
        self.assertEqual(model["median_reply_seconds"], HOUR)

    def test_names(self) -> None:
        self.assertEqual(console_stats.names("Dana (finance)"), ["Dana"])
        self.assertEqual(
            console_stats.names("Dana, Lee; Marco and Priya & Omar"),
            ["Dana", "Lee", "Marco", "Priya", "Omar"],
        )
        self.assertEqual(console_stats.names("@lee"), ["lee"])
        self.assertEqual(console_stats.names("Alexandra"), ["Alexandra"])  # "and" only as a word
        self.assertEqual(console_stats.names(None), [])
        self.assertEqual(console_stats.names(["Dana"]), [])

    def test_people(self) -> None:
        requested = item(
            "task:2026-10-19/1",
            "task",
            "proposed",
            [created(at(1), "proposed")],
            fields={"requester": "Priya"},
        )
        delegated = item(
            "review:acme/exporter#9",
            "review-request",
            "requested",
            [created(at(1), "requested")],
            delegation={"requester": "priya", "window": "exporter"},
            fields={"requester": "Priya"},
        )
        items = [*OUTREACH, requested, delegated]
        model = console_stats.people(items, console_stats.sent_in(items, WEEK), WEEK)
        recipients = {p["name"]: p["count"] for p in model["recipients"]["top"]}
        self.assertEqual(recipients, {"Dana": 2, "Lee": 2, "Marco": 2, "Priya": 1, "exporter channel": 1})
        self.assertEqual(model["recipients"]["distinct"], 5)
        self.assertEqual(model["requesters"], {"distinct": 1, "top": [{"name": "Priya", "count": 2}]})

    def test_top_ten(self) -> None:
        many = [outreach(f"out:2026-10-19/{n}", "sent", [at(1)], to=f"Person {n:02d}") for n in range(1, 13)]
        model = console_stats.people(many, console_stats.sent_in(many, WEEK), WEEK)
        self.assertEqual(model["recipients"]["distinct"], 12)
        self.assertEqual(len(model["recipients"]["top"]), console_stats.TOP_PEOPLE)


class BosunTestCase(unittest.TestCase):
    def test_own_state(self) -> None:
        own = Own(
            deliveries=[
                {"at": at(1), "via": "send", "events": ["e1", "e2"]},
                {"at": at(0), "via": "check-in", "events": ["e3"]},
                {"at": at(30), "via": "send", "events": ["e0"]},
                {"at": "garbled", "via": "send"},
            ],
            slack_spend={"2026-10-19": 0.25, "2026-10-20": 0.5, "2026-09-01": 9.0, "2026-10-18": "x"},  # type: ignore[dict-item]
            slack_runs=[{"started": at(0), "outcome": "ok"}, {"started": at(1), "outcome": "failed"}],
            learn_runs=[
                {"started": at(1), "cost": 0.8, "dry_run": False, "outcome": "ok"},
                {"started": at(0), "cost": 0.4, "dry_run": True},
                {"started": at(0), "cost": None, "dry_run": False, "outcome": "failed"},
            ],
        )
        model = console_stats.bosun(ledger_of(runs={"check-in": at(0), "morning": at(0, 8)}), own, WEEK)
        self.assertEqual([c["name"] for c in model["chores"]], ["check-in", "morning"])
        self.assertEqual(model["deliveries"]["by_via"], {"send": 1, "check-in": 1})
        self.assertEqual(model["deliveries"]["events"], 3)
        self.assertEqual(model["slack_watch"]["usd"], 0.75)
        self.assertEqual((model["slack_watch"]["runs"], model["slack_watch"]["failed"]), (2, 1))
        self.assertEqual(model["learn"]["usd"], 1.2)
        self.assertEqual(
            (model["learn"]["runs"], model["learn"]["dry_runs"], model["learn"]["failed"]), (2, 1, 1)
        )
        spend = {d["date"]: d for d in model["spend"]}
        self.assertEqual(spend["2026-10-20"], {"date": "2026-10-20", "slack_watch": 0.5, "learn": 0.4})


class ModelTestCase(unittest.TestCase):
    def test_the_fixture_ledger(self) -> None:
        data = json.loads(FIXTURE.read_text())
        model = console_stats.stats_model(data, Own(), 7, NOW, UTC)
        self.assertEqual(model["throughput"]["opened"], len(data["items"]))
        self.assertEqual(model["days"], 7)
        self.assertEqual(model["outreach"]["sent"], 3)
        self.assertEqual(model["outreach"]["one_way"], 1)
        self.assertEqual(model["reviews"]["done"], 1)
        self.assertEqual(model["odd_times"], 0)
        json.dumps(model)  # it is all JSON

    def test_by_workstream_rolls_children_up(self) -> None:
        part_of = [{"rel": "part_of", "id": "ws:widgets"}]
        data = ledger_of(
            item("ws:widgets", "workstream", "active", [created(at(9), "active")]),
            item("ws:widget-ui", "workstream", "active", [created(at(9), "active")], links=part_of),
            item(
                "mypr:acme/widgets#1",
                "my-pr",
                "merged",
                [created(at(3), "awaiting-review"), moved(at(1), "approved", "merged")],
                links=part_of,
            ),
            item(
                "mypr:acme/widgets#2",
                "my-pr",
                "awaiting-review",
                [created(at(2), "awaiting-review")],
                links=[{"rel": "part_of", "id": "ws:widget-ui"}],
            ),
            item("mypr:acme/misc#3", "my-pr", "awaiting-review", [created(at(2), "awaiting-review")]),
            item(
                "mypr:acme/odd#4",
                "my-pr",
                "awaiting-review",
                [created(at(2), "awaiting-review")],
                links=["odd"],
            ),
        )
        found = console_stats.stats_model(data, Own(), 7, NOW, UTC)["workstreams"]
        self.assertEqual(
            found["rows"], [{"ws": "ws:widgets", "title": "ws:widgets", "opened": 2, "closed": 1}]
        )
        self.assertEqual(found["unassigned"], {"opened": 1, "closed": 0})

    def test_an_empty_or_odd_ledger(self) -> None:
        ledgers: list[dict[str, Any]] = [{}, {"items": []}, {"items": {"a": "not an item"}}, ledger_of()]
        for data in ledgers:
            with self.subTest(data=data):
                model = console_stats.stats_model(data, Own(), 30, NOW, UTC)
                self.assertEqual(model["throughput"]["opened"], 0)
                self.assertIsNone(model["prs"]["median_open_to_merge_seconds"])


class StateFilesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"
        self.outside = Path(tmp.name) / "outside"
        self.learn_dir = self.state / "learn"
        (self.learn_dir / "dry-runs" / "20261019-183000").mkdir(parents=True)
        self.outside.mkdir()

    def write(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))

    def test_nothing_there(self) -> None:
        own = console_state.read_own(self.state)
        self.assertEqual((own.deliveries, own.slack_spend, own.errors), ([], {}, {}))

    def test_reads_each_state(self) -> None:
        self.write(
            self.state / "events" / "state.json",
            {"deliveries": [{"at": at(0), "via": "send", "events": ["a"]}]},
        )
        self.write(
            self.state / "slack-watch" / "state.json",
            {"spend": {"2026-10-20": 0.2}, "runs": [{"started": at(0)}]},
        )
        live_dir = self.learn_dir / "runs" / "20261020-183000"
        self.write(live_dir / "report.md", "# Learning report\n")
        self.write(
            self.learn_dir / "state.json",
            {"runs": [{"started": at(0), "cost": 0.5, "outcome": "ok", "run_dir": str(live_dir)}]},
        )
        dry = self.learn_dir / "dry-runs" / "20261019-183000"
        self.write(dry / "result.json", {"total_cost_usd": 0.3})
        self.write(dry / "report.md", "# Learning report\n\n## memory\n\n- **One** (low)\n- **Two** (high)\n")
        own = console_state.read_own(self.state)
        self.assertEqual(own.deliveries[0]["via"], "send")
        self.assertEqual(own.slack_spend, {"2026-10-20": 0.2})
        self.assertEqual([(r["dry_run"], r["cost"]) for r in own.learn_runs], [(False, 0.5), (True, 0.3)])
        self.assertEqual(own.learn_runs[1]["findings"], 2)
        files = console_state.read_learn(self.state)
        self.assertEqual(console_state.report_path(files, files.runs[0]), live_dir / "report.md")
        texts = dict(console_state.report_texts(files))
        self.assertEqual(texts[at(0)], "# Learning report\n")
        self.assertEqual(len(texts), 2)  # the dry run's report too

    def test_an_unreadable_state_is_an_error_not_a_crash(self) -> None:
        self.write(self.state / "events" / "state.json", "{not json")
        self.write(self.state / "slack-watch" / "state.json", "{")
        self.write(self.learn_dir / "state.json", "{broken")
        own = console_state.read_own(self.state)
        self.assertEqual(set(own.errors), {"events", "slack_watch", "learn"})
        self.assertEqual(own.deliveries, [])

    def test_reports_outside_the_learn_dir_are_not_read(self) -> None:
        self.write(self.outside / "run" / "report.md", "secret elsewhere")
        self.write(
            self.learn_dir / "state.json",
            {"runs": [{"started": at(0), "run_dir": str(self.outside / "run")}]},
        )
        os.symlink(self.outside / "run", self.learn_dir / "dry-runs" / "20261018-120000")
        files = console_state.read_learn(self.state)
        self.assertEqual(len(files.runs), 2)  # the live run, and the real dry run (not the symlink)
        self.assertTrue(all(console_state.report_path(files, run) is None for run in files.runs))


if __name__ == "__main__":
    unittest.main()
