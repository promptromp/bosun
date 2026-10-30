"""Unit tests for lib/impact_collect.py: the snapshot from a fake `gh` and fixture transcripts."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import claude_state
import impact_collect as ic


NOW = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)
ME = "acme-dev"
WORKDIR = "/Users/me/repos/exporter"


class FakeGh:
    """Answers the collector's gh calls from tables; records them."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.review_calls = 0

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["api", "user"]:
            return ME + "\n"
        if args[:2] == ["pr", "list"]:
            return json.dumps(
                [
                    {
                        "number": 7,
                        "title": "change 7",
                        "url": "https://github.com/acme/exporter/pull/7",
                        "createdAt": "2026-03-02T08:00:00Z",
                        "mergedAt": "2026-03-03T08:00:00Z",
                        "additions": 5,
                        "deletions": 1,
                    }
                ]
            )
        if args[:2] == ["search", "prs"]:
            return json.dumps(
                [
                    {"number": 9, "title": "theirs", "url": "https://github.com/acme/exporter/pull/9",
                     "createdAt": "2026-03-04T08:00:00Z", "updatedAt": "2026-03-05T08:00:00Z",
                     "author": {"login": "someone-else"}},
                    {"number": 7, "title": "mine", "url": "https://github.com/acme/exporter/pull/7",
                     "createdAt": "2026-03-02T08:00:00Z", "updatedAt": "2026-03-03T08:00:00Z",
                     "author": {"login": ME}},
                ]
            )  # fmt: skip
        if args[0] == "api" and args[-3].endswith("/reviews"):
            self.review_calls += 1
            return json.dumps(["2026-03-04T10:00:00Z"])
        raise AssertionError(f"unexpected gh call {args}")


def line(**entry: Any) -> str:
    return json.dumps(entry) + "\n"


def assistant(at: str, message_id: str, output: int = 100) -> str:
    usage = {"input_tokens": 1000, "output_tokens": output, "cache_read_input_tokens": 5000}
    return line(
        type="assistant", timestamp=at, message={"id": message_id, "model": "claude-opus-5-5", "usage": usage}
    )


def tool_result(at: str) -> str:
    return line(type="user", timestamp=at, message={"content": [{"type": "tool_result", "content": "ok"}]})


def prompt(at: str, text: str) -> str:
    return line(type="user", timestamp=at, origin={"kind": "human"}, message={"content": text})


class CollectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        patcher = mock.patch.object(claude_state, "PROJECTS_DIR", self.tmp / "projects")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = self.tmp / "state"
        self.state.mkdir()
        (self.state / "ledger.json").write_text(
            json.dumps({"items": {"mypr:acme/exporter#7": {"created_at": "2026-03-02T09:00:00Z"}}})
        )
        folder = claude_state.project_dir(WORKDIR)
        folder.mkdir(parents=True)
        self.transcript = folder / "s1.jsonl"
        self.transcript.write_text(
            line(type="custom-title", customTitle="exporter")
            + prompt("2026-03-03T09:00:00Z", "please fix the footer")
            + prompt("2026-03-03T09:00:30Z", "/bosun:chores check-in")
            + assistant("2026-03-03T09:01:00Z", "m1")
            + assistant("2026-03-03T09:01:00Z", "m1")  # a second content block of the same message
            + tool_result("2026-03-03T09:03:00Z")
            + assistant("2026-03-03T09:05:00Z", "m2")
            + assistant("2026-03-03T10:30:00Z", "m3")  # after a long pause: not busy time
        )
        self.settings = ic.Settings(
            state_dir=self.state,
            repos=(),
            start=None,
            baseline_from=None,
            prompt_minutes=2,
            windows=(("exporter", WORKDIR), ("orchestrator", "/Users/me/repos")),
        )

    def collect(self, gh: FakeGh | None = None, dry_run: bool = False) -> str:
        return ic.collect(self.settings, NOW, gh or FakeGh(), dry_run)

    def snapshot(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self.state / "impact" / "snapshot.json").read_text())
        return data

    def test_the_snapshot(self) -> None:
        out = self.collect()
        self.assertIn("1 merged PRs, 1 reviewed PRs in 1 repos", out)
        found = self.snapshot()
        self.assertEqual((found["start"], found["baseline_from"]), ("2026-03-02", "2025-12-01"))
        self.assertEqual(found["repos"], ["acme/exporter"])
        self.assertEqual(found["prs"][0]["merged"], "2026-03-03T08:00:00Z")
        review = found["reviews"][0]
        self.assertEqual((review["number"], review["reviews"]), (9, ["2026-03-04T10:00:00Z"]))
        self.assertNotIn("someone-else", json.dumps(found))  # other people are never named
        (row,) = found["activity"]
        self.assertEqual((row["window"], row["day"], row["prompts"]), ("exporter", "2026-03-03", 1))
        self.assertEqual(row["busy_seconds"], 240.0)  # 09:01-09:03-09:05; the 85-minute gap is idle
        self.assertEqual(row["usage"]["claude-opus-5-5"]["output"], 300)  # m1 counted once, m2, m3

    def test_a_dry_run_writes_nothing(self) -> None:
        self.assertIn("dry-run: nothing written", self.collect(dry_run=True))
        self.assertFalse((self.state / "impact").exists())

    def test_unchanged_transcripts_and_prs_come_from_the_cache(self) -> None:
        self.collect()
        gh = FakeGh()
        with mock.patch.object(claude_state, "transcript_activity", side_effect=AssertionError("re-read")):
            self.collect(gh)
        self.assertEqual(gh.review_calls, 0)
        self.assertEqual(self.snapshot()["activity"][0]["prompts"], 1)

    def test_an_orchestrator_conversation_counts_for_its_window(self) -> None:
        names = ["exporter", "orchestrator"]
        self.assertEqual(ic.window_of("orchestrator-2026-03-03", names), "orchestrator")
        self.assertEqual(ic.window_of("orchestrator-notes", names), "orchestrator-notes")
        self.assertEqual(ic.window_of("", names), ic.UNNAMED)

    def test_github_errors_are_recorded_not_fatal(self) -> None:
        def broken(args: list[str]) -> str:
            raise RuntimeError("gh: not logged in")

        out = self.collect(broken)  # type: ignore[arg-type]
        self.assertIn("problems: gh user: gh: not logged in", out)
        self.assertEqual(self.snapshot()["activity"][0]["window"], "exporter")

    def test_turns_count_against_what_started_them(self) -> None:
        self.collect()
        triggers = self.snapshot()["activity"][0]["triggers"]
        # the check-in started the turn (tool results do not start one): every call counts there
        self.assertEqual(set(triggers), {"check-in"})
        self.assertEqual(triggers["check-in"]["turns"], 1)
        self.assertEqual(triggers["check-in"]["usage"]["claude-opus-5-5"]["output"], 300)
        self.assertEqual(triggers["check-in"]["contexts"], [6000])  # input + cache reads of the first call

    def test_every_kind_of_turn_start(self) -> None:
        drain = "Bosun resumed or restarted this session."
        self.transcript.write_text(
            line(type="custom-title", customTitle="exporter")
            + prompt("2026-03-03T09:00:00Z", "please fix the footer")
            + assistant("2026-03-03T09:00:10Z", "w1")
            + line(type="user", timestamp="2026-03-03T09:01:00Z", origin={"kind": "task-notification"},
                   message={"content": "<task-notification><task-id>b1</task-id></task-notification>"})
            + assistant("2026-03-03T09:01:10Z", "n1")
            + prompt("2026-03-03T09:02:00Z", drain)
            + assistant("2026-03-03T09:02:10Z", "r1")
            + prompt("2026-03-03T09:03:00Z", "[bosun events] 2 new from the repo sessions")
            + assistant("2026-03-03T09:03:10Z", "e1")
            + prompt("2026-03-03T09:04:00Z", "Nightly review (read-only). Check things.")
            + assistant("2026-03-03T09:04:10Z", "s1")
            + prompt("2026-03-03T09:05:00Z", "Update to ledger item task:2026-03-03/1: more details")
            + assistant("2026-03-03T09:05:10Z", "h1")
            + line(type="user", timestamp="2026-03-03T09:06:00Z", isMeta=True, message={"content": "meta"})
            + assistant("2026-03-03T09:06:10Z", "h2")  # a meta entry starts no turn
            + line(type="user", timestamp="2026-03-03T09:07:00Z", isMeta=True, promptSource="system",
                   message={"content": "Scheduled check-in on the open PRs"})
            + assistant("2026-03-03T09:07:10Z", "c1")  # Claude Code's own scheduled prompt
        )  # fmt: skip
        self.settings = ic.Settings(
            **{**self.settings.__dict__, "drain_prompt": drain, "overhead_prefixes": ("Nightly review",)}
        )
        self.collect()
        triggers = self.snapshot()["activity"][0]["triggers"]
        turns = {cls: bucket["turns"] for cls, bucket in triggers.items()}
        self.assertEqual(
            turns, {"work": 1, "notification": 1, "resume": 1, "events": 1, "scheduled": 2, "handover": 1}
        )
        self.assertEqual(triggers["handover"]["usage"]["claude-opus-5-5"]["output"], 200)  # h1 and h2

    def test_a_changed_classification_drops_the_cache(self) -> None:
        self.collect()
        self.settings = ic.Settings(**{**self.settings.__dict__, "overhead_prefixes": ("please fix",)})
        with mock.patch.object(
            claude_state, "transcript_activity", wraps=claude_state.transcript_activity
        ) as read:
            self.collect()
        self.assertTrue(read.called)

    def test_guardrails_from_the_ledger_events_and_logs(self) -> None:
        items = {
            "mypr:acme/exporter#7": {"created_at": "2026-03-02T09:00:00Z"},
            "task:2026-03-03/1": {
                "kind": "task",
                "created_at": "2026-03-03T08:00:00Z",
                "history": [
                    {"at": "2026-03-03T09:00:00Z", "event": "delegated"},
                    {"at": "2026-03-03T11:00:00Z", "event": "report"},
                    {"at": "2026-03-03T12:00:00Z", "event": "transition", "from": "delegated", "to": "done"},
                ],
            },
            "out:2026-03-03/1": {
                "kind": "outreach",
                "created_at": "2026-03-03T08:00:00Z",
                "history": [
                    {"at": "2026-03-04T09:00:00Z", "event": "transition", "from": "sent", "to": "replied"},
                    {"at": "2026-03-04T09:30:00Z", "event": "approval-failed"},
                ],
            },
        }
        (self.state / "ledger.json").write_text(json.dumps({"items": items}))
        events_dir = self.state / "events"
        events_dir.mkdir()
        (events_dir / "queue.jsonl").write_text(
            line(id="e1", at="2026-03-03T10:00:00Z") + line(id="e2", at="2026-03-03T15:00:00Z")
        )
        (events_dir / "state.json").write_text(json.dumps({"delivered": {"e1": "send 2026-03-03T10:04:00Z"}}))
        logs = self.state / "logs"
        logs.mkdir()
        (logs / "checkin-2026-03-03.log").write_text(
            "2026-03-03 10:00:00 [send] delivered to acme:orchestrator\n"
            "2026-03-03 11:00:00 [checkin] skip: nothing new since the last check-in\n"
            "2026-03-03 12:00:00 [checkin] skip: orchestrator is busy (for 20s)\n"
        )
        self.collect()
        rows = {r["day"]: r for r in self.snapshot()["guardrails"]}
        day = rows["2026-03-03"]
        self.assertEqual(
            (day["tasks_reported"], day["tasks_done"], day["handover_to_report_hours"]), (1, 1, [2.0])
        )
        self.assertEqual(
            (day["events_queued"], day["events_delivered"], day["event_delay_minutes"]), (2, 1, [4.0])
        )
        self.assertEqual(day["events_pending_end"], 1)  # e2 waits past the day's end
        self.assertEqual(
            (day["checkins_delivered"], day["checkins_skipped_nothing_new"], day["checkins_skipped_other"]),
            (1, 1, 1),
        )
        self.assertEqual(
            (rows["2026-03-04"]["outreach_replied"], rows["2026-03-04"]["approval_failures"]), (1, 1)
        )

    def test_settings_from_the_environment(self) -> None:
        env = {
            "BOSUN_STATE_DIR": str(self.state),
            "BOSUN_IMPACT_REPOS": "acme/exporter, acme/billing",
            "BOSUN_IMPACT_START": "2026-03-02",
            "BOSUN_IMPACT_WINDOWS": f"exporter\t{WORKDIR}\nbroken-line\n",
        }
        found = ic.Settings.from_env(env)
        self.assertEqual(found.repos, ("acme/exporter", "acme/billing"))
        self.assertEqual(found.windows, (("exporter", WORKDIR),))
        self.assertEqual(str(found.start), "2026-03-02")
        overhead = ic.Settings.from_env(
            {
                **env,
                "BOSUN_IMPACT_OVERHEAD_PREFIXES": "Nightly review | Weekly digest",
                "BOSUN_IMPACT_OVERHEAD_FIXED_AT": "2026-03-10",
            }
        )
        self.assertEqual(overhead.overhead_prefixes, ("Nightly review", "Weekly digest"))
        self.assertEqual(str(overhead.overhead_fixed_at), "2026-03-10")
        self.assertEqual((found.attribution_hours, found.tasks_top), (8.0, 15))
        tasks = ic.Settings.from_env(
            {**env, "BOSUN_IMPACT_ATTRIBUTION_HOURS": "2.5", "BOSUN_IMPACT_TASKS_TOP": "5"}
        )
        self.assertEqual((tasks.attribution_hours, tasks.tasks_top), (2.5, 5))

    def test_fresh_conversations_count_for_their_window(self) -> None:
        names = ["exporter", "exporter-docs"]
        self.assertEqual(ic.window_of("exporter-2026-03-03-2", names), "exporter")
        self.assertEqual(ic.window_of("exporter-docs-2026-03-03-1", names), "exporter-docs")
        self.assertEqual(ic.window_of("exporter-docs", names), "exporter-docs")
        self.assertEqual(ic.window_of("exporter-2026-03-03-x", names), "exporter-2026-03-03-x")


TASK = "task:2026-03-03/1"
HANDOVER = f"Ledger id: {TASK}\nAsk: fix the footer"


class CostPerTaskTest(unittest.TestCase):
    """A minimal setup: one window, no orchestrator, one item handed over, a subagent."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        patcher = mock.patch.object(claude_state, "PROJECTS_DIR", self.tmp / "projects")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = self.tmp / "state"
        self.state.mkdir()
        folder = claude_state.project_dir(WORKDIR)
        folder.mkdir(parents=True)
        self.transcript = folder / "s1.jsonl"
        self.transcript.write_text(
            line(type="custom-title", customTitle="solo")
            + prompt("2026-03-03T09:00:00Z", HANDOVER)
            + assistant("2026-03-03T09:00:10Z", "h1")
            + prompt("2026-03-03T09:30:00Z", "and the header too")
            + assistant("2026-03-03T09:30:10Z", "w1")
        )
        subagents = folder / "s1" / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "agent-1.jsonl").write_text(
            assistant("2026-03-03T09:05:00Z", "a1", output=1000)
            + assistant("2026-03-03T09:06:00Z", "a2", output=1000)
        )
        self.settings = ic.Settings(
            state_dir=self.state, repos=(), start=date(2026, 3, 2), baseline_from=None, prompt_minutes=2,
            windows=(("solo", WORKDIR),),
        )  # fmt: skip

    def write_ledger(self, items: dict[str, Any]) -> None:
        (self.state / "ledger.json").write_text(json.dumps({"items": items}))

    def tasks(self) -> dict[str, Any]:
        ic.collect(self.settings, NOW, FakeGh(), False)
        found: dict[str, Any] = json.loads((self.state / "impact" / "snapshot.json").read_text())["tasks"]
        return found

    def test_every_turn_and_the_subagent_go_to_the_handed_over_item(self) -> None:
        self.write_ledger(
            {
                TASK: {
                    "id": TASK, "kind": "task", "state": "delegated", "title": "fix the footer",
                    "created_at": "2026-03-03T08:00:00Z",
                    "delegation": {"window": "solo", "task": "new-work"},
                    "history": [{"at": "2026-03-03T09:00:00Z", "event": "delegated", "window": "solo"}],
                }
            }
        )  # fmt: skip
        found = self.tasks()
        (row,) = found["items"]
        self.assertEqual((row["id"], row["turns"], row["windows"]), (TASK, 2, ["solo"]))
        self.assertEqual(row["usage"]["claude-opus-5-5"]["output"], 200)  # h1 and w1 (rule 3: active)
        self.assertEqual(row["subagent_usage"]["claude-opus-5-5"]["output"], 2000)
        self.assertEqual(found["unattributed"], [])
        self.assertEqual(set(found["rules"]), {"handover", "active"})

    def test_a_brand_new_ledger_leaves_everything_unattributed(self) -> None:
        self.write_ledger({})
        found = self.tasks()
        self.assertEqual(found["items"], [])
        (rest,) = found["unattributed"]
        self.assertEqual((rest["window"], rest["turns"]), ("solo", 2))
        self.assertIn("cost per task: 0 items, 0% of $", ic.collect(self.settings, NOW, FakeGh(), True))

    def test_turn_records_are_cached(self) -> None:
        self.write_ledger({})
        self.tasks()
        with mock.patch.object(claude_state, "transcript_activity", side_effect=AssertionError("re-read")):
            self.assertEqual(self.tasks()["unattributed"][0]["turns"], 2)


if __name__ == "__main__":
    unittest.main()
