"""Unit tests for lib/learn_digest.py: the digest `bosun learn` builds from the fictional world
in tests/llm/learn_world.py (sessions, ledger, logs, hints), redaction and the size cap."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
import sys  # noqa: E402


sys.path[:0] = [str(ROOT / "lib"), str(ROOT / "tests" / "llm")]
import claude_state  # noqa: E402
import learn_digest  # noqa: E402
import learn_world  # noqa: E402
from claude_state import Event  # noqa: E402


class WorldCase(unittest.TestCase):
    """The fictional world, built once per test class, and its digest."""

    world: learn_world.LearnWorld
    digest: learn_digest.Digest
    tmp: tempfile.TemporaryDirectory[str]
    patch: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.world = learn_world.build(Path(cls.tmp.name))
        cls.patch = mock.patch.object(claude_state, "PROJECTS_DIR", cls.world.projects)
        cls.patch.start()
        cls.digest = learn_digest.collect(cls.settings(), cls.windows())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.patch.stop()
        cls.tmp.cleanup()

    @classmethod
    def settings(cls, **overrides: object) -> learn_digest.Settings:
        values: dict[str, object] = {
            "since": cls.world.since,
            "until": cls.world.until,
            "state_dir": cls.world.state,
            "log_dir": cls.world.state / "logs",
            "config_dir": cls.world.config,
            "root": ROOT,
            "drain_prompt": learn_world.DRAIN,
            "exclude": frozenset({learn_world.EXCLUDED}),
            **overrides,
        }
        return learn_digest.Settings(**values)  # type: ignore[arg-type]  # a test-only bag of overrides

    @classmethod
    def windows(cls) -> list[learn_digest.Window]:
        return learn_digest.Window.from_json(json.loads(cls.world.sessions_file.read_text()))

    def section(self, title: str) -> str:
        """The digest from a "## <title>" heading to the next one."""
        text = self.digest.text.split(f"## {title}", 1)[1]
        return text.split("\n## ", 1)[0]


class SessionsTest(WorldCase):
    def test_reads_only_bosun_windows_and_skips_excluded_ids(self) -> None:
        read = sorted(s.transcript.session_id for s in self.digest.sessions)
        self.assertNotIn(learn_world.EXCLUDED, read)
        self.assertNotIn(learn_world.UNRELATED, read)
        self.assertIn(learn_world.ORCH_MONDAY, read)
        self.assertEqual(len(read), 6)
        self.assertIn("(excluded)", self.digest.text)
        self.assertNotIn("must never be read", self.digest.text)

    def test_daily_orchestrator_conversations_belong_to_the_orchestrator(self) -> None:
        windows = {s.transcript.title: s.window.name for s in self.digest.sessions}
        self.assertEqual(windows["orchestrator-2026-01-05"], "orchestrator")
        self.assertEqual(windows["exporter-tax"], "exporter-tax")

    def test_a_long_permission_prompt_shows_how_long_it_waited(self) -> None:
        self.assertIn(
            "PERMISSION PROMPT answered after 15h05m: Bash: gh api repos/acme/exporter", self.digest.text
        )
        self.assertIn("1 permission prompts (longest wait 15h05m)", self.digest.text)

    def test_guard_denials_show_once_each_with_the_command(self) -> None:
        line = "GUARD DENIED Bash: bosun send exporter-pdf"
        self.assertEqual(self.digest.text.count(line), 3)
        self.assertNotIn("Hook denied", self.digest.text)

    def test_prompts_are_attributed_to_you_or_bosun(self) -> None:
        self.assertIn("you: Don't open draft PRs for my work", self.digest.text)
        self.assertIn("bosun: /bosun:chores check-in", self.digest.text)
        self.assertIn("bosun: Ledger id: review:acme/exporter#58", self.digest.text)

    def test_only_bosun_commands_and_failures_are_kept(self) -> None:
        self.assertIn("ran: bosun delegate review --id 'review:acme/exporter#57'", self.digest.text)
        self.assertNotIn("ran: gh pr diff", self.digest.text)
        self.assertIn("error: Bash: ls exporter/csv_headers.py", self.digest.text)

    def test_replies_end_each_turn(self) -> None:
        self.assertIn("reply: Started the investigation; the build is slow", self.digest.text)


class LedgerLogsTest(WorldCase):
    def test_ledger_history_in_the_window(self) -> None:
        ledger = self.section("Ledger: items with activity in the window")
        self.assertIn("review:acme/exporter#57 [review-request] now delegated", ledger)
        self.assertIn('delegated {"task": "review", "window": "exporter-tax"}', ledger)

    def test_hints_flag_the_planted_outreach_and_handover_problems(self) -> None:
        hints = self.section("Ledger checks")
        self.assertIn("out:mypr:acme/exporter#61/1 (draft) and out:mypr:acme/exporter#61/3 (draft)", hints)
        self.assertIn("same Slack conversation 1767600000.000100", hints)
        self.assertIn("out:mypr:acme/exporter#61/3 has a security-sensitive subject (password)", hints)
        self.assertIn("out:mypr:acme/exporter#62/2 is an info-ack", hints)
        self.assertIn("review:acme/exporter#57 was delegated to exporter-tax at Mon 10:40", hints)
        self.assertNotIn("#61/2", hints)  # a different question to a different person

    def test_repeated_log_lines_collapse(self) -> None:
        logs = self.section("Bosun logs")
        self.assertIn("skip: orchestrator is waiting (for 3300s)", logs)
        self.assertIn("... the same 3 more time(s) ...", logs)
        self.assertIn("skip: orchestrator is waiting (for 17700s)", logs)

    def test_watchdog_alerts_and_instructions_in_force(self) -> None:
        self.assertIn("waiting:orchestrator:101:1", self.section("Watchdog alerts in the window"))
        self.assertIn("the orchestrator's policy", self.digest.text)
        self.assertIn("Task types (orchestrator/tasks/): fix-my-pr", self.digest.text)

    def test_commits_to_bosun_in_the_window(self) -> None:
        self.assertEqual(
            self.section("Changes to bosun itself").split("\n")[2], "- none"
        )  # no commits in 2026-01

    def test_known_findings_are_listed(self) -> None:
        known = (
            {
                "key": "orchestrator-gh-api-stall",
                "category": "stall",
                "status": "rejected",
                "times_seen": 2,
                "title": "t",
            },
        )
        digest = learn_digest.collect(self.settings(known=known), self.windows())
        self.assertIn("- orchestrator-gh-api-stall [stall] rejected, seen 2x: t", digest.text)


class CapTest(WorldCase):
    def test_no_cap_leaves_nothing_out(self) -> None:
        self.assertNotIn("## Trimmed to fit", self.digest.text)

    def test_a_digest_over_its_cap_is_trimmed_and_says_how(self) -> None:
        full = learn_digest.collect(self.settings(max_events=40), self.windows()).text
        cap = len(full) - 1
        digest = learn_digest.collect(self.settings(max_chars=cap, max_events=40), self.windows())
        self.assertLessEqual(len(digest.text), cap)
        trimmed = digest.text.split("## Trimmed to fit the digest cap", 1)[1].split("\n## ", 1)[0]
        self.assertIn("- ledger: routine history events left out", trimmed)
        self.assertNotIn("cut at the cap", trimmed)
        # the most telling events and the reference sections stay
        self.assertIn("GUARD DENIED Bash: bosun send exporter-pdf", digest.text)
        self.assertIn("PERMISSION PROMPT answered after 15h05m", digest.text)
        self.assertIn("## Findings of earlier runs", digest.text)
        self.assertIn("the orchestrator's policy", digest.text)

    def test_a_cap_no_trim_meets_cuts_the_activity_and_keeps_the_reference(self) -> None:
        full = learn_digest.collect(self.settings(max_events=40), self.windows()).text
        reference = len(full.split("## Watchdog alerts in the window", 1)[1])
        cap = reference + 4000
        digest = learn_digest.collect(self.settings(max_chars=cap, max_events=40), self.windows())
        self.assertLessEqual(len(digest.text), cap)
        self.assertIn("- cut at the cap: the end of the activity sections is missing", digest.text)
        self.assertIn("## Watchdog alerts in the window", digest.text)
        self.assertIn("## Instructions and memory in force", digest.text)


class HelpersTest(unittest.TestCase):
    def test_redaction(self) -> None:
        token = "gh" + "p_" + "a1B2" * 9
        cases = {
            f"push with {token} now": "push with <token> now",
            "mail priya@example.com today": "mail <email> today",
            "PASSWORD=" + "hunter2hunter2": "PASSWORD=<redacted>",
            "https://sso.example.com/authorize?client=x&code=abc123&state=zz": "https://sso.example.com/authorize?<redacted>",
            "Authorization: Bearer " + "abcdefghijklmnopqrstuvwxyz": "Authorization: Bearer <token>",
        }
        for raw, redacted in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(learn_digest.redact(raw), redacted)

    def test_slack_permalinks_and_shas_survive(self) -> None:
        text = (
            "https://acme.slack.com/archives/C00000000AA/p1767600000000100?thread_ts=1767600000.000100 sha "
            + "a" * 40
        )
        self.assertEqual(learn_digest.redact(text), text)

    def test_cap_keeps_the_most_telling_events_in_time_order(self) -> None:
        start = datetime(2026, 1, 5, 8, tzinfo=UTC)
        events = [Event(start + timedelta(minutes=i), "reply", f"r{i}") for i in range(5)]
        events.append(
            Event(start + timedelta(minutes=2, seconds=30), "permission", tool="Bash: x", waited=60)
        )
        kept, dropped = learn_digest.cap(sorted(events, key=lambda e: e.at), 3)
        self.assertEqual(dropped, 3)
        self.assertEqual([e.kind for e in kept], ["reply", "reply", "permission"])

    def test_recap_keeps_the_most_telling_then_the_newest_events(self) -> None:
        start = datetime(2026, 1, 5, 8, tzinfo=UTC)
        events = [Event(start + timedelta(minutes=i), "reply", f"r{i}") for i in range(5)]
        events.append(Event(start, "hook-deny", tool="Bash: x"))
        session = learn_digest.Session(learn_digest.Window("w", "session", "/d"), mock.Mock(), events, 9)
        kept = learn_digest.recap(session, 3)
        self.assertEqual([e.text for e in kept.events], ["", "r3", "r4"])
        self.assertEqual(kept.dropped, 3)
        self.assertIs(learn_digest.recap(session, 0), session)

    def test_trimmed_ledger_history_leaves_out_routine_and_older_events(self) -> None:
        since = datetime(2026, 1, 5, tzinfo=UTC)
        settings = learn_digest.Settings(
            since=since,
            until=since + timedelta(days=2),
            state_dir=Path("/s"),
            log_dir=Path("/l"),
            config_dir=Path("/c"),
            root=ROOT,
        )
        history = [
            {"at": (since + timedelta(hours=1)).isoformat(), "event": "linked", "note": "part_of ws:x"},
            {"at": (since + timedelta(hours=2)).isoformat(), "event": "transition", "note": "early"},
            {"at": (since + timedelta(hours=30)).isoformat(), "event": "report", "note": "late"},
        ]
        item = {
            "id": "task:2026-01-05/1",
            "kind": "task",
            "state": "delegated",
            "title": "t",
            "history": history,
        }
        full = "\n".join(learn_digest.item_activity(item, settings, learn_digest.Trim()))
        self.assertIn("linked: part_of ws:x", full)
        quiet = learn_digest.item_activity(item, settings, learn_digest.Trim(routine_ledger=True))
        self.assertNotIn("linked", "\n".join(quiet))
        recent = learn_digest.Trim(routine_ledger=True, history_since=since + timedelta(days=1))
        lines = learn_digest.item_activity(item, settings, recent)
        self.assertEqual([line.strip()[6:] for line in lines[2:]], ["report: late"])

    def test_trims_only_tighten(self) -> None:
        settings = learn_digest.Settings(
            since=datetime(2026, 1, 5, tzinfo=UTC),
            until=datetime(2026, 1, 9, tzinfo=UTC),
            state_dir=Path("/s"),
            log_dir=Path("/l"),
            config_dir=Path("/c"),
            root=ROOT,
            max_events=250,
        )
        steps = list(learn_digest.trim_steps(settings))
        self.assertEqual(steps[0], learn_digest.Trim())
        self.assertTrue(all(s.routine_ledger and s.signal_logs for s in steps[2:]))
        # the ledger's history shrinks before any session loses events
        first_session_cut = next(i for i, s in enumerate(steps) if s.session_events)
        self.assertEqual(steps[first_session_cut - 1].history_since, datetime(2026, 1, 8, tzinfo=UTC))
        self.assertEqual([s.session_events for s in steps[first_session_cut:]], [125, 62, 31, 20])
        self.assertEqual(steps[0].notes(), [])
        self.assertEqual(len(steps[-1].notes()), 5)

    def test_window_starts_where_the_last_run_ended_within_the_limit(self) -> None:
        until = datetime(2026, 1, 7, 12, tzinfo=UTC)
        self.assertEqual(learn_digest.window_bounds(until, 24, None)[0], until - timedelta(hours=24))
        recent = until - timedelta(hours=3)
        self.assertEqual(learn_digest.window_bounds(until, 24, recent)[0], recent)
        old = until - timedelta(days=4)
        self.assertEqual(learn_digest.window_bounds(until, 24, old)[0], until - timedelta(hours=24))

    def test_durations(self) -> None:
        self.assertEqual(
            [learn_digest.duration(s) for s in (5, 125, 54081)],
            ["5s", "2m", "15h01m"],
        )


if __name__ == "__main__":
    unittest.main()
