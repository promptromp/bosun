#!/usr/bin/env python3
"""Eval for `bosun learn`'s analysis with the real, headless Claude.

Usage: uv run python tests/llm/learn_eval.py [--runs N] [--model M] [--effort E]
                                             [--max-cost-per-run USD] [--work DIR] [--keep]

Each run builds the fictional world of tests/llm/learn_world.py (acme's invoice exporter, Monday
08:00 to Wednesday 12:00: the orchestrator's daily conversations, three repo windows, the ledger,
the logs) in a scratch folder and runs a real `learn run` over it: the real digest, the real
prompt (orchestrator/learn.md) and one real Claude with no tools and no MCP servers. Nothing
reaches the live bosun: state, config and ledger are the world's, and the world's transcripts
are read by pointing lib/claude_state.py at them.

Scored per run:
- recall: the planted issues (learn_world.PLANTED) that at least one finding matches (its category
  and the words it mentions)
- precision: the findings that match a planted issue, of all findings kept (the world's noise is
  clean, so a finding that matches nothing is likely invented or trivial; they are listed for a
  human look)
- dropped: findings whose evidence quotes were not in the digest (removed by learn itself)
- proposals: the learn: ledger items made, at most BOSUN_LEARN_MAX_PROPOSALS, each with a known
  target, its text and an apply or done instruction

A run passes with recall >= 0.8, precision >= 0.7 and well-formed proposals. Not run by hooks or
tests/smoke.sh: it costs money (about $0.35 per run with opus). Writes results.json to the work
folder; exit 1 if a run failed.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import learn
import learn_world
import ledger
from learn_world import PLANTED, Planted


MIN_RECALL = 0.8
MIN_PRECISION = 0.7
DEFAULT_MAX_COST = 2.0
MAX_PROPOSALS = 5


def finding_text(finding: learn.Finding) -> str:
    proposal = finding.proposal or {}
    parts = [
        finding.key,
        finding.title,
        finding.summary,
        proposal.get("text", ""),
        proposal.get("target", ""),
    ]
    parts += [" ".join(e.values()) for e in finding.evidence]
    return " ".join(parts).lower()


def matches(planted: Planted, finding: learn.Finding) -> bool:
    text = finding_text(finding)
    return finding.category in planted.categories and all(
        any(needle in text for needle in group) for group in planted.needles
    )


def proposal_problems(items: dict[str, dict[str, Any]]) -> list[str]:
    learn_items = [i for i in items.values() if i["kind"] == learn.KIND]
    problems = (
        [f"{len(learn_items)} proposals, more than {MAX_PROPOSALS}"]
        if len(learn_items) > MAX_PROPOSALS
        else []
    )
    for item in learn_items:
        action = (item.get("proposal") or {}).get("action", "")
        if item["fields"].get("target") not in learn.TARGETS:
            problems.append(f"{item['id']}: unknown target {item['fields'].get('target')}")
        if "If yes:" not in action or "Change:" not in action:
            problems.append(f"{item['id']}: proposal without the change or the instruction")
    return problems


@dataclass
class Score:
    recall: float
    precision: float
    found: list[str]
    missed: list[str]
    unplanted: list[str]
    dropped: list[str]
    proposals: int
    problems: list[str]
    cost: float

    @property
    def passed(self) -> bool:
        return self.recall >= MIN_RECALL and self.precision >= MIN_PRECISION and not self.problems


def score(findings: list[learn.Finding], dropped: list[str], items: dict[str, Any], cost: float) -> Score:
    found = [p.id for p in PLANTED if any(matches(p, f) for f in findings)]
    planted_hits = [f for f in findings if any(matches(p, f) for p in PLANTED)]
    unplanted = [f"{f.category}: {f.title}" for f in findings if f not in planted_hits]
    return Score(
        recall=len(found) / len(PLANTED),
        precision=len(planted_hits) / len(findings) if findings else 0.0,
        found=found,
        missed=[p.id for p in PLANTED if p.id not in found],
        unplanted=unplanted,
        dropped=dropped,
        proposals=sum(1 for i in items.values() if i["kind"] == learn.KIND),
        problems=proposal_problems(items),
        cost=cost,
    )


def run_once(base: Path, args: argparse.Namespace) -> Score:
    world = learn_world.build(base)
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("BOSUN_")},
        **world.env(),
        "BOSUN_LEARN_MODEL": args.model,
        "BOSUN_LEARN_EFFORT": args.effort,
        "BOSUN_LEARN_MAX_BUDGET_USD": str(args.max_cost_per_run),
        "BOSUN_LEARN_MAX_PROPOSALS": str(MAX_PROPOSALS),
    }
    argv = ["run", "--since", world.since.isoformat(), "--until", world.until.isoformat()]
    argv += ["--sessions", str(world.sessions_file)]
    out = io.StringIO()
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(claude_state, "PROJECTS_DIR", world.projects),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(out),
    ):
        code = learn.main(argv)
    if code != 0:
        raise RuntimeError(f"learn run failed: {out.getvalue()[-500:]}")
    run = learn.State.load(world.state / learn.STATE_SUBDIR).runs[-1]
    run_dir = Path(run["run_dir"])
    result = json.loads((run_dir / "result.json").read_text())
    digest = (run_dir / "digest.md").read_text()
    _summary, findings, dropped = learn.check_output(result["structured_output"], digest)
    ledger.configure(world.state)
    return score(findings, dropped, ledger.load()["items"], float(result.get("total_cost_usd") or 0))


def report(run: int, result: Score) -> None:
    verdict = "PASS" if result.passed else "FAIL"
    print(
        f"run {run}: {verdict} recall {result.recall:.0%} precision {result.precision:.0%} "
        f"({len(result.unplanted)} unplanted, {len(result.dropped)} dropped), {result.proposals} proposals, "
        f"${result.cost:.2f}"
    )
    for label, values in (
        ("missed", result.missed),
        ("unplanted", result.unplanted),
        ("dropped", result.dropped),
    ):
        for value in values:
            print(f"  {label}: {value}")
    for problem in result.problems:
        print(f"  proposal problem: {problem}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--model", default="opus")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--max-cost-per-run", type=float, default=DEFAULT_MAX_COST)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)
    work = args.work or Path(tempfile.mkdtemp(prefix="learn-eval-"))
    results: list[dict[str, Any]] = []
    for run in range(1, args.runs + 1):
        base = work / f"run{run}"
        try:
            result = run_once(base, args)
        except (RuntimeError, OSError, KeyError, ValueError, learn.LearnError) as error:
            print(f"run {run}: FAIL harness: {error}")
            results.append({"run": run, "passed": False, "error": str(error)})
            continue
        report(run, result)
        results.append({"run": run, "passed": result.passed, **result.__dict__})
        if not args.keep:
            shutil.rmtree(base / "claude", ignore_errors=True)
    (work / "results.json").write_text(json.dumps(results, indent=2))
    total = sum(float(r.get("cost") or 0) for r in results)
    print(f"total ${total:.2f}; results in {work / 'results.json'}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
