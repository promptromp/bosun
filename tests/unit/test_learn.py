"""Unit tests for lib/learn.py: checking the analysis' findings against the digest, remembering
them across runs, turning new ones into learn: ledger proposals, applying, closing, the run record
the watchdog reads, and --status. The analysis is a fake `claude` that prints a canned result."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "lib"), str(ROOT / "tests" / "llm")]
import claude_state  # noqa: E402
import learn  # noqa: E402
import learn_world  # noqa: E402
import ledger  # noqa: E402


FAKE_CLAUDE = """#!/usr/bin/env python3
import os, sys
sys.stdin.read()
print(open(os.environ["FAKE_LEARN_RESULT"]).read())
sys.exit(int(os.environ.get("FAKE_LEARN_EXIT", "0")))
"""
STALL_QUOTE = "PERMISSION PROMPT answered after 15h05m: Bash: gh api repos/acme/exporter"
DRAFT_QUOTE = "you: Again a draft. I said normal PRs, not drafts."


def finding(key: str, quote: str, proposal: dict[str, str] | None = None, **extra: Any) -> dict[str, Any]:
    raw = {
        "key": key,
        "category": "stall",
        "severity": "high",
        "title": key.replace("-", " "),
        "summary": "s",
        "evidence": [{"source": "session", "ref": "orchestrator", "at": "Tue 08:10", "quote": quote}],
        **extra,
    }
    if proposal:
        raw["proposal"] = proposal
    return raw


STALL = finding(
    "orchestrator-gh-api-stall",
    STALL_QUOTE,
    {"target": "policy", "text": "Never use gh api for reads.", "rationale": "it prompts"},
)
DRAFTS = finding(
    "exporter-opens-drafts",
    DRAFT_QUOTE,
    {
        "target": "memory",
        "window": "exporter",
        "text": "Open normal PRs, never drafts.",
        "rationale": "said twice",
    },
    category="repeated-correction",
    severity="medium",
)
INVENTED = finding("made-up", "this line is nowhere in the digest")


class LearnCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.world = learn_world.build(self.base / "world")
        fake = self.base / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        self.result_file = self.base / "result.json"
        env = {
            **self.world.env(),
            "BOSUN_CLAUDE_BIN": str(fake),
            "BOSUN_LEARN_ENABLED": "1",
            "FAKE_LEARN_RESULT": str(self.result_file),
        }
        for patch in (
            mock.patch.dict(os.environ, env),
            mock.patch.object(claude_state, "PROJECTS_DIR", self.world.projects),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.config = learn.Config.from_env(os.environ)
        ledger.configure(self.world.state)

    def analysis_returns(self, *findings: dict[str, Any], cost: float = 0.4) -> None:
        output = {"summary": "A rough window.", "findings": list(findings)}
        self.result_file.write_text(json.dumps({"structured_output": output, "total_cost_usd": cost}))

    def main(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", out):
            code = learn.main(list(argv))
        return code, out.getvalue()

    def run_learn(self, *extra: str) -> tuple[int, str]:
        since, until = self.world.since.isoformat(), self.world.until.isoformat()
        return self.main(
            "run", "--since", since, "--until", until, "--sessions", str(self.world.sessions_file), *extra
        )

    def items(self) -> dict[str, dict[str, Any]]:
        return dict(ledger.load()["items"])

    def state(self) -> learn.State:
        return learn.State.load(self.config.learn_dir)


class CheckOutputTest(unittest.TestCase):
    DIGEST = "line one: the gh api prompt waited 15h\nline two: you said open normal PRs"

    def test_findings_need_evidence_from_the_digest(self) -> None:
        good = finding("a", "the gh api prompt waited")
        cut = finding("b", "line one: the gh ... waited 15h")
        bad = finding("c", "never said")
        _summary, findings, dropped = learn.check_output(
            {"summary": "x", "findings": [good, cut, bad]}, self.DIGEST
        )
        self.assertEqual([f.key for f in findings], ["a", "b"])
        self.assertEqual(dropped, ["c: none of its evidence is in the digest"])

    def test_keys_are_slugs_and_repeats_merge(self) -> None:
        twice = [finding("Same Key!", "line one"), finding("same-key", "line two")]
        _summary, findings, _dropped = learn.check_output({"summary": "", "findings": twice}, self.DIGEST)
        self.assertEqual([(f.key, len(f.evidence)) for f in findings], [("same-key", 2)])

    def test_unknown_targets_lose_the_proposal_not_the_finding(self) -> None:
        raw = finding("a", "line one", {"target": "rm -rf", "text": "x", "rationale": "y"})
        _summary, findings, _dropped = learn.check_output({"summary": "", "findings": [raw]}, self.DIGEST)
        self.assertIsNone(findings[0].proposal)

    def test_no_findings_list_is_an_error(self) -> None:
        with self.assertRaises(learn.LearnError):
            learn.check_output({"summary": "x"}, self.DIGEST)


class RunTest(LearnCase):
    def test_a_run_proposes_new_findings_in_the_ledger(self) -> None:
        self.analysis_returns(STALL, DRAFTS, INVENTED)
        code, out = self.run_learn()
        self.assertEqual(code, 0, out)
        self.assertIn("2 finding(s) (1 dropped)", out)
        learn_items = {i: v for i, v in self.items().items() if i.startswith("learn:")}
        self.assertEqual(len(learn_items), 2)
        item = learn_items["learn:" + datetime.now().strftime("%Y-%m-%d") + "/1"]
        self.assertEqual((item["kind"], item["state"]), ("learning", "proposed"))
        self.assertIn("Never use gh api for reads.", item["proposal"]["action"])
        self.assertIn(f"bosun learn apply '{item['id']}'", item["proposal"]["action"])
        run = self.state().runs[-1]
        self.assertEqual((run["outcome"], run["cost"], self.state().running), ("ok", 0.4, None))
        report = Path(run["run_dir"]) / "report.md"
        self.assertIn("## Dropped (evidence not found in the digest)", report.read_text())

    def test_a_second_run_counts_known_findings_instead_of_proposing_again(self) -> None:
        self.analysis_returns(STALL)
        self.run_learn()
        _code, out = self.run_learn()
        self.assertIn("new proposals: none", out)
        self.assertEqual(self.state().findings["orchestrator-gh-api-stall"]["times_seen"], 2)
        digest = (Path(self.state().runs[-1]["run_dir"]) / "digest.md").read_text()
        self.assertIn("- orchestrator-gh-api-stall [stall] proposed, seen 1x", digest)

    def test_proposals_per_run_are_capped_most_severe_first(self) -> None:
        os.environ["BOSUN_LEARN_MAX_PROPOSALS"] = "1"
        self.config = learn.Config.from_env(os.environ)
        self.analysis_returns(DRAFTS, STALL)
        self.run_learn()
        learn_items = [v for i, v in self.items().items() if i.startswith("learn:")]
        self.assertEqual([i["fields"]["key"] for i in learn_items], ["orchestrator-gh-api-stall"])

    def test_dry_run_and_collect_only_change_no_state(self) -> None:
        self.analysis_returns(STALL)
        out_dir = self.base / "out"
        self.assertEqual(self.run_learn("--dry-run", "--out", str(out_dir))[0], 0)
        self.assertEqual(self.run_learn("--collect-only", "--out", str(out_dir))[0], 0)
        self.assertFalse(self.config.learn_dir.exists())
        self.assertFalse(any(i.startswith("learn:") for i in self.items()))
        self.assertEqual(len(list(out_dir.glob("*/digest.md"))), 2)
        self.assertEqual(len(list(out_dir.glob("*/report.md"))), 1)

    def test_a_failed_analysis_is_recorded_for_the_watchdog(self) -> None:
        self.result_file.write_text("not json")
        code, out = self.run_learn()
        self.assertEqual(code, 1)
        self.assertIn("without a JSON result", out)
        run = self.state().runs[-1]
        self.assertEqual(run["outcome"], "failed")
        self.assertIsNone(self.state().running)

    def test_a_run_estimated_over_the_budget_never_calls_claude(self) -> None:
        os.environ["BOSUN_LEARN_MAX_BUDGET_USD"] = "0.4"
        self.config = learn.Config.from_env(os.environ)
        self.result_file.write_text("the fake Claude was called")  # would fail as "without a JSON result"
        code, out = self.run_learn()
        self.assertEqual(code, 1)
        self.assertRegex(out, r"would cost about \$0\.\d\d, over the budget of \$0\.40")
        self.assertIn("not calling Claude", out)
        self.assertIn("BOSUN_LEARN_MAX_DIGEST_KB", out)
        self.assertEqual(self.state().runs[-1]["outcome"], "failed")

    def test_the_run_line_shows_cost_estimate_and_budget(self) -> None:
        self.analysis_returns(STALL, cost=0.61)
        _code, out = self.run_learn("--dry-run", "--out", str(self.base / "out"))
        self.assertRegex(out, r"cost \$0\.61 \(estimated \$0\.\d\d, budget \$2\.00\)")
        _code, out = self.run_learn("--collect-only", "--out", str(self.base / "out"))
        self.assertRegex(out, r"a run would cost about \$0\.\d\d of \$2\.00")

    def test_a_run_over_the_budget_says_what_it_cost(self) -> None:
        self.result_file.write_text(
            json.dumps({"is_error": True, "subtype": "error_max_budget_usd", "total_cost_usd": 4.05})
        )
        code, out = self.run_learn()
        self.assertEqual(code, 1)
        self.assertIn("(error_max_budget_usd; it cost $4.05, over the budget of $2.00)", out)

    def test_estimates_follow_size_and_model(self) -> None:
        digest_chars = 1_109_351  # the digest that cost $4.05 (477k tokens, opus)
        self.assertAlmostEqual(learn.estimated_cost("opus", digest_chars), 4.12, places=1)
        self.assertLess(
            learn.estimated_cost("claude-sonnet-x", digest_chars), learn.estimated_cost("opus", digest_chars)
        )
        self.assertEqual(learn.estimated_cost("a-new-model", 1000), learn.estimated_cost("opus", 1000))

    def test_the_claude_command_has_no_tools_mcp_or_prompts(self) -> None:
        command = learn.claude_command(self.config, "prompt")
        for flags in (
            ["--tools", ""],
            ["--strict-mcp-config"],
            ["--permission-mode", "dontAsk"],
            ["--no-session-persistence"],
        ):
            joined = " ".join(command)
            self.assertIn(" ".join(flags), joined)
        self.assertIn("--json-schema", command)


class ApplyTest(LearnCase):
    def proposed(self, *findings: dict[str, Any]) -> list[str]:
        self.analysis_returns(*findings)
        self.run_learn()
        return sorted(i for i in self.items() if i.startswith("learn:"))

    def test_apply_a_policy_rule_once(self) -> None:
        [learn_id] = self.proposed(STALL)
        self.assertEqual(self.main("apply", learn_id)[0], 0)
        policy = (self.world.config / "policy.md").read_text()
        self.assertIn(f"<!-- {learn_id} (bosun learn) -->\nNever use gh api for reads.", policy)
        item = self.items()[learn_id]
        self.assertEqual((item["state"], item["proposal"]), ("applied", None))
        self.assertEqual(self.state().findings["orchestrator-gh-api-stall"]["status"], "applied")
        code, out = self.main("apply", learn_id)
        self.assertEqual(code, 1)
        self.assertIn("no pending proposal", out)

    def test_apply_a_memory_note_for_the_window_named(self) -> None:
        [learn_id] = self.proposed(DRAFTS)
        self.assertEqual(self.main("apply", learn_id)[0], 0)
        memory = claude_state.memory_dir(str(self.world.repos / "exporter"))
        note = memory / "bosun-exporter-opens-drafts.md"
        self.assertIn("Open normal PRs, never drafts.", note.read_text())
        self.assertIn("bosun-exporter-opens-drafts.md", (memory / "MEMORY.md").read_text())

    def test_apply_a_task_rule(self) -> None:
        rule = finding(
            "tasks-need-normal-prs",
            DRAFT_QUOTE,
            {"target": "task-rule", "text": "Open normal PRs.", "rationale": "r"},
        )
        [learn_id] = self.proposed(rule)
        self.main("apply", learn_id)
        self.assertIn("- Open normal PRs.", (self.world.config / "tasks" / "_common.md").read_text())

    def test_changes_made_by_hand_are_closed_with_done(self) -> None:
        setup = finding(
            "allow-gh-pr-view",
            STALL_QUOTE,
            {"target": "setup", "text": "Allow gh pr view.", "rationale": "r"},
        )
        [learn_id] = self.proposed(setup)
        code, out = self.main("apply", learn_id)
        self.assertEqual(code, 1)
        self.assertIn(f"bosun learn done '{learn_id}'", out)
        self.assertEqual(self.main("done", learn_id, "--note", "edited sessions.yml")[0], 0)
        self.assertEqual(self.items()[learn_id]["state"], "applied")

    def test_ledger_resolve_refuses_a_learning_proposal(self) -> None:
        [learn_id] = self.proposed(STALL)
        args = ledger.build_parser().parse_args(["resolve", learn_id, "done"])
        with self.assertRaisesRegex(ledger.LedgerError, f"close it with bosun learn apply '{learn_id}'"):
            args.func(args)
        item = self.items()[learn_id]
        self.assertEqual((item["state"], item["proposal"]["action"] != ""), ("proposed", True))
        self.assertEqual(self.main("apply", learn_id)[0], 0)
        self.assertEqual(self.items()[learn_id]["history"][-2]["event"], "proposal-done")

    def test_a_rejected_finding_is_never_proposed_again(self) -> None:
        [learn_id] = self.proposed(STALL)
        self.assertEqual(self.main("reject", learn_id, "--note", "no")[0], 0)
        self.assertEqual(self.items()[learn_id]["state"], "rejected")
        self.assertEqual(self.proposed(STALL), [learn_id])
        self.assertEqual(self.state().findings["orchestrator-gh-api-stall"]["status"], "rejected")


class ScheduleStatusTest(LearnCase):
    def test_scheduled_runs_once_a_day_after_learn_at(self) -> None:
        config = learn.Config.from_env({**os.environ, "BOSUN_DAYS": "MTWRFSU"})
        state = learn.State()
        after = datetime.combine(datetime.now().date(), time(23, 59)).astimezone()
        before = datetime.combine(datetime.now().date(), time(0, 1)).astimezone()
        self.assertTrue(learn.due_today(config, state, after))
        self.assertFalse(learn.due_today(config, state, before))
        state.runs.append({"started": after.astimezone(UTC).isoformat()})
        self.assertFalse(learn.due_today(config, state, after))

    def test_scheduled_does_nothing_when_disabled(self) -> None:
        os.environ["BOSUN_LEARN_ENABLED"] = "0"
        code, out = self.main("run", "--scheduled", "--sessions", str(self.world.sessions_file))
        self.assertEqual(code, 0)
        self.assertIn("nothing to do", out)
        self.assertEqual(self.state().runs, [])

    def test_status(self) -> None:
        self.analysis_returns(STALL)
        self.run_learn()
        code, out = self.main("status")
        self.assertEqual(code, 0)
        self.assertIn("learn: enabled; daily at 18:30 on MTWRF", out)
        self.assertIn("findings remembered: 1 (1 proposed)", out)
        self.assertIn("waiting for your approval:\n  learn:", out)


if __name__ == "__main__":
    unittest.main()
