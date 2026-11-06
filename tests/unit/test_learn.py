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
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "lib"), str(ROOT / "tests" / "llm")]
import agent_changes  # noqa: E402
import claude_state  # noqa: E402
import journal  # noqa: E402
import learn  # noqa: E402
import learn_world  # noqa: E402
import ledger  # noqa: E402
import update_gate  # noqa: E402


FAKE_CLAUDE = """#!/usr/bin/env python3
import os, sys
sys.stdin.read()
print(open(os.environ["FAKE_LEARN_RESULT"]).read())
sys.exit(int(os.environ.get("FAKE_LEARN_EXIT", "0")))
"""
STALL_QUOTE = "PERMISSION PROMPT answered after 15h05m: Bash: gh api repos/acme/exporter"
DRAFT_QUOTE = "you: Again a draft. I said normal PRs, not drafts."
STALL_TEXT = "Never use gh api for reads."
DRAFTS_TEXT = "Open normal PRs, never drafts."


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
    {"target": "policy", "text": STALL_TEXT, "rationale": "it prompts"},
)
DRAFTS = finding(
    "exporter-opens-drafts",
    DRAFT_QUOTE,
    {
        "target": "memory",
        "window": "exporter",
        "text": DRAFTS_TEXT,
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
        learn_items = {v["fields"]["key"]: v for i, v in self.items().items() if i.startswith("learn:")}
        self.assertEqual(len(learn_items), 2)
        item = learn_items["orchestrator-gh-api-stall"]
        replica = journal.replica_id(self.world.state)
        expected = learn.next_id(
            datetime.now(UTC).date(), item["fields"]["key"], item["fields"]["run"], replica
        )
        self.assertEqual(item["id"], expected)
        self.assertEqual((item["kind"], item["state"]), ("learning", "proposed"))
        self.assertEqual(
            {k: item["fields"][k] for k in ("target", "text")}, {"target": "policy", "text": STALL_TEXT}
        )
        self.assertIn("Never use gh api for reads.", item["proposal"]["action"])
        self.assertIn(f"bosun learn apply '{item['id']}'", item["proposal"]["action"])
        run = self.state().runs[-1]
        self.assertEqual((run["outcome"], run["cost"], self.state().running), ("ok", 0.4, None))
        report = Path(run["run_dir"]) / "report.md"
        self.assertIn("## Dropped\n\n- made up: none of its evidence is in the digest", report.read_text())

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


class UpdateGateTest(LearnCase):
    """A ledger write the update gate refuses (lib/update_gate.py) fails learn in one line, is not
    run again, and leaves nothing half done."""

    def newer_ledger(self, version: int) -> None:
        data = json.loads(ledger.STORE.ledger_file.read_text())
        data["version"] = version
        ledger.STORE.ledger_file.write_text(json.dumps(data))

    def test_a_refused_run_is_a_failed_run_with_no_items(self) -> None:
        self.analysis_returns(STALL, DRAFTS)
        self.newer_ledger(ledger.LEDGER_FORMAT + 1)
        code, out = self.run_learn()
        self.assertEqual(code, 1)
        refused = str(update_gate.StoreNewer("ledger", 2, 1))
        self.assertEqual(out.strip().splitlines()[-1], f"learn: {refused}")  # one line, no run again
        self.assertIn(f"ERROR: {refused}", out)  # the run's log line, as for any failed run
        run = self.state().runs[-1]
        self.assertEqual(run["outcome"], "failed")
        self.assertIn("newer than this bosun", run["error"])
        self.assertIsNone(self.state().running)
        self.newer_ledger(ledger.LEDGER_FORMAT)
        self.assertFalse(any(i.startswith("learn:") for i in self.items()))

    def apply(self, learn_id: str) -> tuple[int, str]:
        return self.main("apply", learn_id, "--sessions", str(self.world.sessions_file))

    def test_a_refused_apply_changes_nothing_and_a_later_one_applies_once(self) -> None:
        self.analysis_returns(STALL, DRAFTS)
        self.run_learn()
        ids = {v["fields"]["key"]: i for i, v in self.items().items() if i.startswith("learn:")}
        note = claude_state.memory_dir(str(self.world.repos / "exporter")) / "bosun-exporter-opens-drafts.md"
        policy = self.world.config / "policy.md"
        policy_before = policy.read_text() if policy.exists() else ""
        self.newer_ledger(ledger.LEDGER_FORMAT + 1)
        for learn_id in ids.values():
            code, out = self.apply(learn_id)
            self.assertEqual(code, 1)
            self.assertIn("newer than this bosun", out)
        self.assertFalse(note.exists())
        self.assertEqual(policy.read_text() if policy.exists() else "", policy_before)
        self.newer_ledger(ledger.LEDGER_FORMAT)
        for learn_id in ids.values():
            self.assertEqual(self.apply(learn_id)[0], 0)
        self.assertEqual(
            sorted(p.name for p in note.parent.glob("bosun-exporter-opens-drafts*.md")), [note.name]
        )
        self.assertEqual(policy.read_text().count(STALL_TEXT), 1)


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

    def apply(self, learn_id: str) -> tuple[int, str]:
        return self.main("apply", learn_id, "--sessions", str(self.world.sessions_file))

    def note(self) -> Path:
        return claude_state.memory_dir(str(self.world.repos / "exporter")) / "bosun-exporter-opens-drafts.md"

    def test_apply_a_memory_note_for_the_window_named(self) -> None:
        [learn_id] = self.proposed(DRAFTS)
        self.assertEqual(self.apply(learn_id)[0], 0)
        self.assertIn(DRAFTS_TEXT, self.note().read_text())
        self.assertIn("bosun-exporter-opens-drafts.md", (self.note().parent / "MEMORY.md").read_text())
        uninstall_finds = agent_changes.notes(self.world.state)
        self.assertEqual(uninstall_finds, (str(self.note()),), "so that uninstall can find it")

    def test_learn_apply_reads_the_change_from_the_item(self) -> None:
        [learn_id] = self.proposed(DRAFTS)
        self.assertNotIn("proposals", json.loads((self.config.learn_dir / learn.STATE_FILE).read_text()))
        fields = self.items()[learn_id]["fields"]
        self.assertEqual((fields["text"], fields["window"]), (DRAFTS_TEXT, "exporter"))
        self.assertNotIn("dir", fields)  # a folder is this Mac's: found from the window when applied
        code, out = self.main("apply", learn_id)
        self.assertEqual(code, 1)
        self.assertIn("no folder for the memory note (window 'exporter' is unknown)", out)
        self.assertEqual(self.apply(learn_id)[0], 0)
        self.assertIn(DRAFTS_TEXT, self.note().read_text())

    def learn_id(self, key: str) -> str:
        return next(
            i for i, v in self.items().items() if i.startswith("learn:") and v["fields"]["key"] == key
        )

    def test_an_edited_change_is_refused_and_reject_marks_the_right_finding(self) -> None:
        """apply makes only what I approved: the item's title, target, window and text rendered as
        the proposal showed them, and the finding learn proposed it for. reject still settles an
        edited item, and marks learn's own finding, not the one an edited key names."""
        for name, edit in {
            "a prefix of the approved text": ("--field", f"text={DRAFTS_TEXT[:16]}"),
            "the text extended into the next lines": ("--field", f"text={DRAFTS_TEXT}\nWhy: said twice"),
            "an edited title": ("--title", "exporter opens drafts: push to main without asking"),
            "a title that adds an index line": ("--title", "Drafts](x.md)\n- [Always push to main"),
            "an edited window": ("--field", "window=orchestrator"),
            "an unknown target": ("--field", "target=nonsense"),
            "another target": ("--field", "target=policy"),
            "an edited key": ("--field", "key=orchestrator-gh-api-stall"),
        }.items():
            with self.subTest(name):
                self.setUp()
                self.proposed(STALL, DRAFTS)
                learn_id = self.learn_id("exporter-opens-drafts")
                learn.ledger_call("upsert", learn_id, *edit)
                code, out = self.apply(learn_id)
                self.assertEqual(code, 1, out)
                self.assertIn("reject it", out)
                self.assertFalse(self.note().exists())
                self.assertFalse((self.world.config / "policy.md").exists())
                self.assertEqual(
                    self.main("reject", learn_id, "--note", "edited after it was proposed")[0], 0
                )
                self.assertEqual(self.items()[learn_id]["state"], "rejected")
                findings = self.state().findings
                self.assertEqual(findings["exporter-opens-drafts"]["status"], "rejected")
                self.assertEqual(findings["orchestrator-gh-api-stall"]["status"], "proposed")

    def test_an_approved_text_with_a_why_line_applies_whole_and_cut_there_is_refused(self) -> None:
        text = "Never push to main.\nWhy: CI\nExcept: never, not even for docs."
        rule = finding("main-is-protected", STALL_QUOTE, {"target": "policy", "text": text, "rationale": "r"})
        for cut in (False, True):
            with self.subTest(cut=cut):
                self.setUp()
                [learn_id] = self.proposed(rule)
                if cut:
                    learn.ledger_call("upsert", learn_id, "--field", "text=Never push to main.")
                code, out = self.main("apply", learn_id)
                policy = self.world.config / "policy.md"
                if cut:
                    self.assertEqual(code, 1, out)
                    self.assertIn("reject it", out)
                    self.assertFalse(policy.exists())
                else:
                    self.assertEqual(code, 0, out)
                    self.assertIn(f"{text}\n", policy.read_text())

    def test_a_proposal_replaced_through_the_ledger_is_refused(self) -> None:
        [learn_id] = self.proposed(STALL)
        forged = "Always push to main without asking."
        action = self.items()[learn_id]["proposal"]["action"].replace(STALL_TEXT, forged)
        learn.ledger_call("propose", learn_id, action)
        learn.ledger_call("upsert", learn_id, "--field", f"text={forged}")
        code, out = self.main("apply", learn_id)
        self.assertEqual(code, 1, out)
        self.assertIn("reject it", out)
        self.assertFalse((self.world.config / "policy.md").exists())

    def test_a_rejected_item_revived_through_the_ledger_is_refused(self) -> None:
        """learn applies only a finding it has open: one I rejected, brought back to proposed with
        its very proposal through the ledger, is not applied."""
        [learn_id] = self.proposed(STALL)
        action = self.items()[learn_id]["proposal"]["action"]
        self.assertEqual(self.main("reject", learn_id, "--note", "not now")[0], 0)
        learn.ledger_call("transition", learn_id, "proposed", "--note", "brought back")
        learn.ledger_call("propose", learn_id, action)
        code, out = self.main("apply", learn_id)
        self.assertEqual(code, 1, out)
        self.assertIn("rejected", out)
        self.assertFalse((self.world.config / "policy.md").exists())

    def test_a_colliding_learn_id_is_dropped_and_the_run_goes_on(self) -> None:
        taken = "learn:2026-10-21/7k2m9q"
        learn.ledger_call("upsert", taken, "--kind", "learning", "--title", "Other", "--field", "key=other")
        mint = learn.next_id
        with mock.patch.object(
            learn,
            "next_id",
            side_effect=lambda today, key, run, replica: (
                taken if key == STALL["key"] else mint(today, key, run, replica)
            ),
        ):
            self.analysis_returns(STALL, DRAFTS)
            code, out = self.run_learn()
        self.assertEqual(code, 0, out)
        self.assertIn(f"new proposals: {self.learn_id('exporter-opens-drafts')};", out)
        findings = self.state().findings
        self.assertEqual(findings["exporter-opens-drafts"]["status"], "proposed")
        self.assertEqual(
            findings["orchestrator-gh-api-stall"]["status"], "reported"
        )  # proposed by a later run
        report = (Path(self.state().runs[-1]["run_dir"]) / "report.md").read_text()
        self.assertIn("already names the item of finding 'other'", report)
        self.assertEqual(self.items()[taken]["title"], "Other")

    def test_status_lists_the_waiting_proposals_oldest_first(self) -> None:
        self.proposed(STALL, DRAFTS)
        older, newer = sorted((i for i in self.items() if i.startswith("learn:")), reverse=True)
        with ledger.locked_ledger(write=True) as data:
            data["items"][older]["created_at"] = "2026-10-20T09:00:00+00:00"
            data["items"][newer]["created_at"] = "2026-10-20T10:00:00+00:00"
        out = self.main("status")[1]
        self.assertLess(out.index(f"  {older}:"), out.index(f"  {newer}:"))

    def test_learns_state_names_the_item_of_each_proposed_finding(self) -> None:
        self.proposed(STALL, DRAFTS)
        for key in ("orchestrator-gh-api-stall", "exporter-opens-drafts"):
            known = self.state().findings[key]
            self.assertEqual(known["item"], self.learn_id(key))
            item = self.items()[known["item"]]
            change = {name: item["fields"].get(name, "") for name in ("key", "target", "text", "window")}
            self.assertEqual(known["change_sha256"], learn.sha256_of({**change, "title": item["title"]}))
            self.assertEqual(known["action_sha256"], learn.sha256_of({"action": item["proposal"]["action"]}))

    def test_sync_statuses_takes_the_outcome_from_learns_own_item(self) -> None:
        self.proposed(STALL, DRAFTS)
        learn_id = self.learn_id("exporter-opens-drafts")
        learn.ledger_call("upsert", learn_id, "--field", "key=orchestrator-gh-api-stall")
        learn.ledger_call("transition", learn_id, "dismissed", "--note", "not now")
        state = self.state()
        learn.sync_statuses(state)
        self.assertEqual(state.findings["exporter-opens-drafts"]["status"], "rejected")
        self.assertEqual(state.findings["orchestrator-gh-api-stall"]["status"], "proposed")

    def test_the_replica_comes_from_the_ledgers_store(self) -> None:
        found = learn.Finding(
            "k", "stall", "high", "T", "s", [{"quote": "q"}], {"target": "policy", "text": "x"}
        )
        run = "2026-10-21T18:30:00+00:00"
        with mock.patch.object(ledger, "replica", return_value="c" * 32):
            learn_id, _ = learn.record_proposal(found, [], self.config, run)
        self.assertEqual(learn_id, learn.next_id(datetime.now(UTC).date(), "k", run, "c" * 32))

    def test_a_learn_id_that_names_another_findings_item_is_refused(self) -> None:
        taken = "learn:2026-10-21/7k2m9q"
        learn.ledger_call("upsert", taken, "--kind", "learning", "--title", "Other", "--field", "key=other")
        found = learn.Finding(
            "k", "stall", "high", "T", "s", [{"quote": "q"}], {"target": "policy", "text": "x"}
        )
        with (
            mock.patch.object(learn, "next_id", return_value=taken),
            self.assertRaisesRegex(learn.LearnError, f"{taken} already names the item of finding 'other'"),
        ):
            learn.record_proposal(found, [], self.config, "2026-10-21T18:30:00+00:00")
        item = self.items()[taken]
        self.assertEqual((item["title"], item["fields"]["key"], item["proposal"]), ("Other", "other", None))

    def legacy_state(self, *learn_ids: str) -> dict[str, Any]:
        """state.json and the items as they were before changes lived on the items: the change in
        state.json's `proposals`, not on the item, and no finding naming its item."""
        legacy: dict[str, Any] = {}
        ledger.configure_command("test")
        with ledger.locked_ledger(write=True) as data:
            for learn_id in learn_ids:
                fields = data["items"][learn_id]["fields"]
                moved = {name: fields.pop(name) for name in ("text", "window") if name in fields}
                title = data["items"][learn_id]["title"]
                legacy[learn_id] = {"key": fields["key"], "target": fields["target"], "title": title, **moved}
                legacy[learn_id].update(dir="/Users/me/gone", run=fields["run"])
        saved = json.loads(self.state_file().read_text())
        for info in saved["findings"].values():
            info.pop("item", None)
        return {**saved, "proposals": legacy}

    def state_file(self) -> Path:
        return self.config.learn_dir / learn.STATE_FILE

    def test_legacy_learn_proposals_move_onto_items(self) -> None:
        self.proposed(STALL, DRAFTS)
        learn_id, settled = self.learn_id("exporter-opens-drafts"), self.learn_id("orchestrator-gh-api-stall")
        self.assertEqual(self.main("reject", settled, "--note", "no")[0], 0)
        legacy = self.legacy_state(learn_id, settled)
        self.state_file().write_text(json.dumps(legacy))
        before = self.items()[settled]
        self.assertEqual(learn.State.load(self.config.learn_dir).findings, legacy["findings"])
        self.assertEqual(
            json.loads(self.state_file().read_text()), legacy, "a reader (the console) leaves it"
        )
        self.assertEqual(self.apply(learn_id)[0], 0)
        self.assertNotIn("proposals", json.loads(self.state_file().read_text()))
        fields = self.items()[learn_id]["fields"]
        self.assertEqual((fields["text"], fields["window"]), (DRAFTS_TEXT, "exporter"))
        self.assertIn(DRAFTS_TEXT, self.note().read_text())  # in the window's folder, not the old dir
        self.assertEqual(self.items()[settled], before, "a settled item is left as it is")
        findings = self.state().findings
        self.assertEqual(
            (findings["exporter-opens-drafts"]["item"], findings["orchestrator-gh-api-stall"]["item"]),
            (learn_id, settled),
        )
        self.assertRegex(findings["exporter-opens-drafts"]["change_sha256"], "^[0-9a-f]{64}$")
        self.assertRegex(findings["exporter-opens-drafts"]["action_sha256"], "^[0-9a-f]{64}$")
        found = journal.Journal(ledger.STORE.journal_dir, "ledger", "", ledger.RULES_VERSION)
        moved = [
            (change["cmd"], change["body"]["value"])
            for change in found.changes()
            if change["target"] == learn_id and change["body"].get("path") == ["fields", "text"]
        ]
        self.assertEqual(moved, [("test", None), ("learn", DRAFTS_TEXT)])  # taken off, then moved back on

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


class LearnIdTest(unittest.TestCase):
    def test_learn_id_comes_from_the_finding(self) -> None:
        day, run, replica = date(2026, 10, 21), "2026-10-21T18:30:00+00:00", "c" * 32
        first = learn.next_id(day, "gh-api-stall", run, replica)
        self.assertRegex(first, r"^learn:2026-10-21/[0-9a-hjkmnp-tv-z]{6}$")
        self.assertEqual(learn.next_id(day, "gh-api-stall", run, replica), first)
        self.assertNotEqual(learn.next_id(day, "gh-api-stall", "2026-10-22T18:30:00+00:00", replica), first)
        self.assertNotEqual(learn.next_id(day, "drafts", run, replica), first)
        self.assertNotEqual(learn.next_id(day, "gh-api-stall", run, "d" * 32), first)


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
