"""The console's reads of bosun's own state files beyond the ledger: the events dispatcher's
deliveries, the Slack watcher's spend and runs, and learning's state and run folders. Design:
docs/design/console.md (sections 3 and 4).

Read-only and cheap: each file is read whole when a request needs it, through the same State
loaders their writers use (lib/events.py, lib/slack_watch.py, lib/learn.py), which only read. A file
that is missing reads as empty; one that is unreadable leaves its part empty and says why. Learning's
run folders are listed, and their result.json and report.md read, only under the learn state
directory: a run recorded elsewhere (`learn run --out DIR`) is listed without its report.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import event_model
import events
import learn
import slack_watch
from console_stats import Own


DRY_RUNS = "dry-runs"
REPORT_FILE = "report.md"
RESULT_FILE = "result.json"
MAX_READ_BYTES = 1_000_000  # a report or result larger than this is not read
FINDING_LINE = "- **"  # how learn.render_report starts a finding


def within(path: Path, root: Path) -> bool:
    """Whether <path>, symlinks resolved, is <root> or under it (a symlink out of it is not)."""
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def small_text(path: Path) -> str | None:
    """The file's text if it is a regular file of at most MAX_READ_BYTES, else None."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_READ_BYTES:
            return None
        return path.read_text(errors="replace")
    except OSError:
        return None


# ---- learning ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class LearnFiles:
    """Learning's state (lib/learn.py's State) and its runs, live and dry, newest first."""

    learn_dir: Path
    state: learn.State = field(default_factory=learn.State)
    runs: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def dry_run_record(run_dir: Path) -> dict[str, Any]:
    """A dry run, from its folder (dry runs are not in the state): when, cost, findings."""
    try:
        started = datetime.strptime(run_dir.name[: len("YYYYmmdd-HHMMSS")], learn.RUN_STAMP).astimezone()
        when: str | None = started.isoformat(timespec="seconds")
    except ValueError:
        when = None
    result = parse_json(small_text(run_dir / RESULT_FILE))
    report = small_text(run_dir / REPORT_FILE)
    cost = result.get("total_cost_usd") if isinstance(result, dict) else None
    return {
        "started": when,
        "dry_run": True,
        "outcome": "ok" if report is not None else "no report",
        "cost": float(cost) if isinstance(cost, int | float) else None,
        "findings": report.count("\n" + FINDING_LINE) if report is not None else None,
        "run_dir": str(run_dir),
    }


def parse_json(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def dry_runs(learn_dir: Path) -> list[dict[str, Any]]:
    base = learn_dir / DRY_RUNS
    if not base.is_dir() or not within(base, learn_dir):
        return []
    return [dry_run_record(d) for d in sorted(base.iterdir()) if d.is_dir() and within(d, learn_dir)]


def live_runs(state: learn.State) -> list[dict[str, Any]]:
    return [{**run, "dry_run": False} for run in state.runs if isinstance(run, dict)]


def read_learn(state_dir: Path) -> LearnFiles:
    learn_dir = state_dir / learn.STATE_SUBDIR
    try:
        state = learn.State.load(learn_dir)
    except (learn.LearnError, TypeError, AttributeError) as exc:
        return LearnFiles(learn_dir, runs=dry_runs(learn_dir), error=str(exc))
    runs = sorted(
        [*live_runs(state), *dry_runs(learn_dir)], key=lambda r: str(r.get("started") or ""), reverse=True
    )
    return LearnFiles(learn_dir, state, runs)


def report_path(files: LearnFiles, run: dict[str, Any]) -> Path | None:
    """The run's report.md, if there is one under the learn state directory."""
    run_dir = run.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir:
        return None
    path = Path(run_dir) / REPORT_FILE
    return path if path.is_file() and within(path, files.learn_dir) else None


def report_texts(files: LearnFiles) -> list[tuple[str, str]]:
    """(started, report text) for each run whose report is under the learn state directory."""
    found = []
    for run in files.runs:
        path = report_path(files, run)
        text = small_text(path) if path else None
        if text is not None:
            found.append((str(run.get("started") or ""), text))
    return found


# ---- the Impact snapshot ------------------------------------------------------------------------

IMPACT_SNAPSHOT = Path("impact") / "snapshot.json"  # lib/impact_collect.py writes it


def read_impact(state_dir: Path) -> dict[str, Any] | None:
    """The Impact view's snapshot, or None when it was never collected or cannot be read."""
    try:
        data = json.loads((state_dir / IMPACT_SNAPSHOT).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---- events and the Slack watcher --------------------------------------------------------------


def read_own(state_dir: Path) -> Own:
    """What /api/stats shows of bosun itself: deliveries, Slack-watch spend and runs, learn runs."""
    errors: dict[str, str] = {}
    try:
        queue = events.State.load(events.events_dir(state_dir))
    except (event_model.EventsError, TypeError, AttributeError) as exc:  # unreadable, or not shaped like it
        queue, errors["events"] = events.State(), str(exc)
    try:
        slack = slack_watch.State.load(state_dir / slack_watch.SUBDIR)
    except (slack_watch.WatchError, TypeError, AttributeError) as exc:
        slack, errors["slack_watch"] = slack_watch.State(), str(exc)
    learned = read_learn(state_dir)
    if learned.error:
        errors["learn"] = learned.error
    return Own(
        deliveries=[d for d in queue.deliveries if isinstance(d, dict)],
        slack_spend=dict(slack.spend) if isinstance(slack.spend, dict) else {},
        slack_runs=[r for r in slack.runs if isinstance(r, dict)],
        learn_runs=learned.runs,
        errors=errors,
    )
