#!/usr/bin/env python3
"""Free-text translation tests with the real, headless Claude.

Usage: uv run python tests/llm/free_text.py [--runs N] [--only ID,...] [--jobs N]
                                            [--max-cost-per-run USD] [--work DIR] [--keep]
                                            [--with-mcp]

Checks that what I say in my own words ("ask Dana whether ...", "ping Priya again", "Marco
answered, go ahead with b") becomes the right outreach steps in the ledger, both for the
orchestrator (its real policy, plugin, settings, model and effort, as `bosun chores --dry-run`
runs it) and for a repo session holding a delegated item (its real task text from `bosun
delegate --print`, in a scratch git repo). Each run gets a scratch world (tests/llm/world.py):
scratch ledger, sends only logged, its own tmux server with fake windows, and outward tools
denied, so nothing reaches Slack, GitHub, Jira, Confluence or the live session. Claude runs with no
MCP servers (--strict-mcp-config), so runs cannot read real Slack or other connected systems and
real names stay out of the results; --with-mcp loads the user's MCP servers (read tools only, as
above) for a more realistic run. Cases and their expectations are in tests/llm/cases.py.

Not part of tests/smoke.sh or the hooks: every run calls the real Claude and costs money (about
$0.2-0.6 per run). Prints pass/fail per case and run, the reasons, and the total cost; writes
results.json to the work dir. Exit 1 if any run failed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import CASES, Case, Outcome
from world import ROOT, World, build_world


MODEL = "opus"  # the orchestrator's and the repo sessions' configured model (BOSUN_*MODEL)
EFFORT = "medium"
RUN_TIMEOUT_SECONDS = 600
DEFAULT_MAX_COST = 1.5
DEFAULT_JOBS = 4
RESULT_PREVIEW = 300
SUBAGENT_PREAMBLE = "Run this task in a subagent"
NOTICE = """

## Rehearsal
This conversation is a rehearsal run headless by tests/llm/free_text.py. Act exactly as you would
live: the ledger is a scratch copy, `bosun send` only logs what it would deliver, and
outward-facing tools are blocked, so propose such actions as usual instead of calling them. Nobody
can answer you in this run: end with your reply to me."""
# Denied on top of the settings' ask list, for both kinds of session.
OUTWARD_TOOLS = [
    "Bash(gh pr comment:*)",
    "Bash(gh pr review:*)",
    "Bash(gh pr merge:*)",
    "Bash(gh pr edit:*)",
    "Bash(gh pr create:*)",
    "Bash(gh pr close:*)",
    "Bash(gh pr ready:*)",
    "Bash(gh issue comment:*)",
    "Bash(gh issue create:*)",
    "Bash(gh api:*)",
    "Bash(git push:*)",
    "Bash(curl:*)",
]
REPO_ALLOWED = [
    "Bash(bosun ledger:*)",
    "Bash(bosun delegate --list:*)",
    "Bash(git status:*)",
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(ls:*)",
    "Read",
    "Grep",
    "Glob",
    "Edit",
    "Write",
]


def site_settings_file() -> Path:
    """The orchestrator settings the live orchestrator uses: the site's file if there is one."""
    configured = os.environ.get("BOSUN_ORCHESTRATOR_SETTINGS")
    config_dir = Path(os.environ.get("BOSUN_CONFIG_DIR") or Path.home() / ".config" / "bosun")
    for candidate in (Path(configured) if configured else None, config_dir / "orchestrator-settings.json"):
        if candidate and candidate.is_file():
            return candidate
    return ROOT / "orchestrator" / "settings.json"


def orchestrator_settings(world: World) -> tuple[Path, list[str]]:
    """The settings with the guard hook pointed at the scratch wrapper and the effort set; and
    the ask list (headless Claude cannot ask, so those tools are denied explicitly too)."""
    settings: dict[str, Any] = json.loads(site_settings_file().read_text())
    for matcher in settings.get("hooks", {}).get("PreToolUse", []):
        for hook in matcher.get("hooks", []):
            hook["command"] = f'"{world.bin / "bosun"}" guard'
    settings["effortLevel"] = EFFORT
    path = world.base / "orchestrator-settings.json"
    path.write_text(json.dumps(settings, indent=2))
    return path, list(settings.get("permissions", {}).get("ask", []))


def mcp_flags(with_mcp: bool) -> list[str]:
    """No MCP servers unless asked: the cases need none, and real reads put real names in results."""
    return [] if with_mcp else ["--strict-mcp-config"]


def orchestrator_command(world: World, case: Case, max_cost: float, with_mcp: bool) -> tuple[list[str], Path]:
    settings, ask = orchestrator_settings(world)
    policy = (ROOT / "orchestrator" / "policy.md").read_text() + NOTICE
    command = [
        "claude", "-p", "--permission-mode", "auto", "--model", MODEL, "--settings", str(settings),
        "--no-session-persistence", "--output-format", "json", "--max-budget-usd", str(max_cost),
        "--plugin-dir", str(ROOT / "orchestrator" / "plugin"),
        "--disallowedTools", *ask, *OUTWARD_TOOLS, *mcp_flags(with_mcp),
        "--append-system-prompt", policy, "--", case.say,
    ]  # fmt: skip
    return command, world.orchestrator_dir


def repo_prompt(world: World, case: Case) -> str:
    """The real task text for the held item, then my message, as if typed later in the session."""
    task = world.bosun("delegate", case.task, "--id", case.holder, "--ask", case.ask, "--print").strip()
    if task.startswith(SUBAGENT_PREAMBLE):
        task = task.split("\n---\n", 1)[1].strip()
    return (
        f"{task}\n\n[Earlier in this session you received the task above and started on it; your work "
        f"so far is on the current branch of this repository.]\n\n{case.say}"
    )


def repo_command(world: World, case: Case, max_cost: float, with_mcp: bool) -> tuple[list[str], Path]:
    _, ask = orchestrator_settings(world)
    command = [
        "claude", "-p", "--permission-mode", "default", "--model", MODEL,
        "--settings", json.dumps({"effortLevel": EFFORT}),
        "--no-session-persistence", "--output-format", "json", "--max-budget-usd", str(max_cost),
        "--allowedTools", *REPO_ALLOWED,
        "--disallowedTools", *ask, *OUTWARD_TOOLS, *mcp_flags(with_mcp),
        "--", repo_prompt(world, case),
    ]  # fmt: skip
    return command, world.repo


def claude_result(done: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        result: dict[str, Any] = json.loads(done.stdout[done.stdout.index("{") :])
    except ValueError:
        return {"result": f"(no JSON result; exit {done.returncode}) {done.stderr[-RESULT_PREVIEW:]}"}
    return result


def run_case(case: Case, run: int, work: Path, max_cost: float, keep: bool, with_mcp: bool) -> dict[str, Any]:
    world = build_world(work / f"{case.id}-run{run}", f"bosun-llm-{os.getpid()}-{case.id}-{run}")
    try:
        for seed in case.seeds:
            seed(world)
        world.snapshot()
        build = orchestrator_command if case.actor == "orchestrator" else repo_command
        command, cwd = build(world, case, max_cost, with_mcp)
        done = subprocess.run(
            command,
            cwd=cwd,
            env=world.env(),
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
            check=False,
        )
        result = claude_result(done)
        outcome = Outcome(world, result, case.actor)
        failures = case.check(outcome) + [f"tried an outward action: {d}" for d in outcome.outward_denials()]
        return {
            "case": case.id,
            "run": run,
            "passed": not failures,
            "failures": failures,
            "cost": float(result.get("total_cost_usd") or 0),
            "turns": result.get("num_turns"),
            "reply": str(result.get("result", ""))[:RESULT_PREVIEW],
            "bosun_calls": [" ".join(call) for call in world.new_calls()],
            "denials": result.get("permission_denials", []),
        }
    except (RuntimeError, OSError, subprocess.SubprocessError, KeyError, IndexError) as error:
        return {
            "case": case.id,
            "run": run,
            "passed": False,
            "failures": [f"harness: {error!r}"],
            "cost": 0.0,
        }
    finally:
        world.close(keep)


def report(results: list[dict[str, Any]]) -> None:
    for row in sorted(results, key=lambda r: (r["case"], r["run"])):
        status = "pass" if row["passed"] else "FAIL"
        print(
            f"{status}  {row['case']:<24} run {row['run']}  ${row['cost']:.2f}  {row.get('turns', '?')} turns"
        )
        for failure in row["failures"]:
            print(f"        - {failure}")
    passed = sum(1 for row in results if row["passed"])
    print(f"\n{passed}/{len(results)} runs passed; total cost ${sum(row['cost'] for row in results):.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--only", default="", help="comma-separated case ids or id prefixes (O1, R4-answer)")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    parser.add_argument("--max-cost-per-run", type=float, default=DEFAULT_MAX_COST)
    parser.add_argument(
        "--work", type=Path, help="where scratch worlds and results.json go (default: a temp dir)"
    )
    parser.add_argument("--keep", action="store_true", help="keep each run's scratch world")
    parser.add_argument(
        "--with-mcp", action="store_true", help="load my MCP servers (default: none, --strict-mcp-config)"
    )
    args = parser.parse_args()
    wanted = [token for token in args.only.split(",") if token]
    chosen = [case for case in CASES if not wanted or any(case.id.startswith(token) for token in wanted)]
    work = args.work or Path(tempfile.mkdtemp(prefix="bosun-llm-"))
    work.mkdir(parents=True, exist_ok=True)
    jobs = [(case, run) for run in range(1, args.runs + 1) for case in chosen]
    print(f"{len(jobs)} run(s) of {len(chosen)} case(s); scratch: {work}", flush=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(
            pool.map(
                lambda job: run_case(job[0], job[1], work, args.max_cost_per_run, args.keep, args.with_mcp),
                jobs,
            )
        )
    (work / "results.json").write_text(json.dumps(results, indent=2))
    report(results)
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    sys.exit(main())
