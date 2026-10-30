#!/usr/bin/env python3
"""The API models tests/js/app_check.js renders the console's views from: the real view models
over the fixture ledger (tests/fixtures/console/acme-ledger.json), this repo's editorial files and a
little fictional state (a scratch learn folder and config dir, removed afterwards), at a fixed time.
Prints one JSON object: request path -> model, plus VIEWS_KEY: the pages to render and a text each
must show."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_files
import console_kb
import console_kg_views as kg_views
import console_knowledge as ck
import console_learn
import console_library
import console_state
import console_stats
import impact
import impact_learning
import impact_overhead
import impact_tasks
import knowledge
from console_stats import Own


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "console" / "acme-ledger.json"
KG_FIXTURE = ROOT / "tests" / "fixtures" / "knowledge" / "acme-import.jsonl"
VIEWS_KEY = "__views__"
KG_EMPTY_KEY = "__knowledge_empty__"  # models app_check.js renders directly, not through a route
CSRF = "fixture-csrf-0000"
NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
KG_NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
OWN = Own(
    deliveries=[{"at": "2026-10-20T09:00:00+00:00", "via": "send", "events": ["e1", "e2"]}],
    slack_spend={"2026-10-19": 0.12, "2026-10-20": 0.3},
    slack_runs=[{"started": "2026-10-20T09:00:00+00:00", "outcome": "ok", "cost": 0.03}],
    learn_runs=[{"started": "2026-10-19T18:30:00+00:00", "cost": 0.85, "dry_run": False, "outcome": "ok"}],
)
REPORT = """# Learning report 2026-10-19T18:30:00+00:00

Window 2026-10-18 to 2026-10-19; 3 session(s) read; cost $0.85; model opus.

## Summary

Two check-ins were lost when the paste failed. <script>alert(1)</script> stays text.

## mishap

- **Retry a failed check-in paste** (high; `checkin-paste-retry`; new)
  - log checkin-2026-10-19.log 10:00: "paste failed"
  - see [the design](https://example.com/acme/design) and [a bad link](javascript:alert(1))

| run | findings |
| --- | --- |
| 2026-10-19 | 1 |

```
bosun learn apply 'learn:2026-10-20/1'
```
"""
SITE_ENV = 'BOSUN_SESSION_NAME="acme"\nACME_API_TOKEN="not-a-real-token-value"\n'


def learn_models(state_dir: Path, ledger: dict[str, Any]) -> dict[str, Any]:
    run_dir = state_dir / "learn" / "runs" / "20261019-183000"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text(REPORT)
    run = {
        "started": "2026-10-19T18:30:00+00:00",
        "outcome": "ok",
        "findings": 1,
        "cost": 0.85,
        "proposals": ["learn:2026-10-20/1"],
        "run_dir": str(run_dir),
    }
    finding = {
        "title": "Retry",
        "category": "mishap",
        "first_seen": run["started"],
        "last_seen": run["started"],
        "times_seen": 1,
        "status": "proposed",
    }
    state = {"runs": [run], "findings": {"checkin-paste-retry": finding}}
    (state_dir / "learn" / "state.json").write_text(json.dumps(state))
    files = console_state.read_learn(state_dir)
    reports = console_files.learn_reports(files)
    (key,) = reports
    return {
        "/api/learn": console_learn.learn_model(files, ledger, console_files.report_keys(reports)),
        f"/api/learn/report/{key}": {
            "key": key,
            "started": run["started"],
            "dry_run": False,
            "body": REPORT,
            "error": None,
        },
        VIEWS_KEY: [[f"#/learning/report/{key}", "Retry a failed check-in paste"]],
    }


def library_models(config: Path) -> dict[str, Any]:
    (config / "tasks").mkdir(parents=True)
    (config / "bosun.env").write_text(SITE_ENV)
    (config / "tasks" / "review.md").write_text(
        "---\ntitle: Site review\nmay: comment\n---\n\n# Site review\n"
    )
    settings = console_files.Settings(config_dir=config)
    entries = console_files.catalog(settings)
    texts = {e.key: console_files.read(e.path)[0] for e in entries}
    found: dict[str, Any] = {"/api/library": console_library.library_model(entries, texts, memory_on=False)}
    views = []
    for entry in entries:
        found[f"/api/library/{entry.key}"] = console_library.file_model(entry, texts[entry.key])
        views.append([f"#/library/{entry.key}", entry.name])
    return {**found, VIEWS_KEY: views}


def kg(*argv: str) -> str:
    args = knowledge.build_parser().parse_args(list(argv))
    result = args.func(args)
    return str(result[0] if isinstance(result, tuple) else result)


def knowledge_base(root: Path) -> None:
    """The acme knowledge base: the fixture import, plus a gate, a confirmed link, a superseded
    fact, web and javascript: sources, an entity whose name is HTML, and a saved batch."""
    knowledge.CLOCK = lambda: KG_NOW
    knowledge.configure(root, git=False)
    kg("import", str(KG_FIXTURE), "--apply", "--auto-confirm")
    kg("add-entity", "system/widget", "--name", "<img src=x onerror=alert(1)>")
    for argv in (
        ("step/rollout", "gated_by", "gate/smoke-tests", "--source", "ledger:task:2026-10-20/1"),
        ("step/merge", "uses", "repo/invoice-exporter", "--source", "ledger:task:2026-10-20/1"),
        ("process/release", "entry_point", "--value", "a merged PR", "--source", "doc:javascript:alert(1)"),
        (
            "channel/acme-prs",
            "purpose",
            "--value",
            "review requests",
            "--source",
            "doc:https://example.com/acme",
        ),
        ("system/widget", "uses", "repo/invoice-exporter", "--source", "report:task:2026-10-20/1"),
    ):
        kg("propose", *argv, "--source", "report:task:2026-10-20/1")
    status = next(
        f["id"] for f in knowledge.load(knowledge.paths()).facts.values() if f["predicate"] == "status"
    )
    kg("supersede", status, "--value", "running", "--source", "ledger:task:2026-10-20/1")
    kg("batch")


def knowledge_models(root: Path, ledger: dict[str, Any]) -> dict[str, Any]:
    """The Knowledge view's models over that knowledge base, and an empty one."""
    knowledge_base(root)
    settings = console_kb.Settings(root=root, git=False)
    kb = console_kb.read_kb(root, KG_NOW)
    review = {
        **ck.review_model(kb, console_kb.saved_batch(settings), settings.limits, ledger, UTC),
        "csrf": CSRF,
    }
    first = [f["id"] for f in review["groups"][0]["facts"] if f["decidable"]]
    found: dict[str, Any] = {
        "/api/knowledge": ck.overview_model(kb, ledger, ck.Search(), review),
        "/api/knowledge?q=release": ck.overview_model(kb, ledger, ck.Search("release"), review),
        "/api/knowledge/review": review,
        "/api/knowledge/briefing": kg_views.briefing_model(kb, UTC),
        "/api/knowledge/constellation": kg_views.constellation_model(kb, UTC),
        KG_EMPTY_KEY: {
            "overview": ck.empty_overview(),
            "review": ck.empty_review(),
            "briefing": kg_views.empty_briefing(),
            "constellation": kg_views.empty_constellation(),
            "first": first,
        },
    }
    pages = [
        ["#/knowledge", "This week we learned"],
        ["#/knowledge?view=constellation", "Hover a node"],
        ["#/knowledge?view=raw", "Systems"],
        ["#/knowledge?view=raw&q=release", "Search results"],
        ["#/knowledge/review", "Confirm group"],
    ]
    for key, entity_id in ck.keys(kb).items():
        found[f"/api/knowledge/entity/{key}"] = ck.entity_model(kb, entity_id, ledger)
        person = entity_id.startswith("person/")  # a person's page says how to have them deleted
        pages.append([f"#/knowledge/{key}", "bosun kg forget" if person else kb.entities[entity_id]["name"]])
    decided = ck.apply_decision(kb, ck.Decision("confirm", tuple(first)), set(first))  # the answer to a POST
    found["POST /api/knowledge/review"] = {**decided, "warnings": []}
    return {**found, VIEWS_KEY: pages}


def impact_snapshot() -> dict[str, Any]:
    """A fictional snapshot: 10 baseline weeks of 1-3 merged PRs, then 4 weeks of 4-6, with
    reviews, two windows' activity and one model the price table does not know."""
    prs, reviews, activity = [], [], []
    first = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)  # a Monday
    for week in range(16):
        monday = first + timedelta(weeks=week)
        merged = 1 + week % 3 if week < 10 else 4 + week % 3
        for n in range(merged):
            number = 100 + week * 10 + n
            prs.append(
                {
                    "repo": "acme/exporter",
                    "number": number,
                    "url": f"https://github.com/acme/exporter/pull/{number}",
                    "title": f"change {number}",
                    "created": (monday + timedelta(hours=n)).isoformat(),
                    "merged": (monday + timedelta(days=1, hours=n)).isoformat(),
                    "additions": 10,
                    "deletions": 1,
                }
            )
        reviews.append(
            {
                "repo": "acme/billing",
                "number": 900 + week,
                "url": f"https://github.com/acme/billing/pull/{900 + week}",
                "title": f"someone's change {900 + week}",
                "pr_created": monday.isoformat(),
                "reviews": [(monday + timedelta(hours=5)).isoformat()],
            }
        )
        if week >= 10:
            day = (monday + timedelta(days=1)).date().isoformat()
            usage = {
                "claude-opus-5-5": {"input": 2_000_000, "output": 200_000, "cache_write": 0, "cache_read": 0}
            }
            activity.append(
                {
                    "window": "orchestrator",
                    "day": day,
                    "busy_seconds": 14_400,
                    "prompts": 20,
                    "usage": usage,
                    "triggers": fixture_triggers(week),
                }
            )
            activity.append(
                {
                    "window": "exporter",
                    "day": day,
                    "busy_seconds": 36_000,
                    "prompts": 5,
                    "usage": {"mystery-1": {"output": 1000}},
                }
            )
    return {
        "version": impact.SNAPSHOT_VERSION,
        "collected_at": "2026-10-20T06:00:00Z",
        "start": "2026-09-14",
        "baseline_from": "2026-07-06",
        "repos": ["acme/exporter", "acme/billing"],
        "settings": {
            "prompt_minutes": 2,
            "overhead_fixed_at": "2026-10-13",
            "attribution_hours": 8,
            "tasks_top": 15,
        },
        "prs": prs,
        "reviews": reviews,
        "activity": activity,
        "guardrails": fixture_guardrails(first.date() + timedelta(weeks=10)),
        "tasks": fixture_tasks(),
        "errors": [],
    }


def fixture_tasks() -> dict[str, Any]:
    """Cost per task: three items (one with a PR, one dropped) and the orchestrator's own turns."""

    def tokens(output: int) -> dict[str, Any]:
        return {"claude-opus-5-5": {"output": output}}

    def item(item_id: str, title: str, output: int, sub: int, **extra: Any) -> dict[str, Any]:
        found = {"id": item_id, "title": title, "kind": "task", "task": "new-work", "state": "done"}
        days = {"first": "2026-10-01", "last": "2026-10-03"}
        return {**found, "outcome": "done", "pr": None, "turns": 12, "windows": ["exporter"], **days,
                "usage": tokens(output), "subagent_usage": tokens(sub), **extra}  # fmt: skip

    return {
        "since": "2026-09-14T00:00:00+00:00",
        "items": [
            item(
                "task:2026-10-01/1",
                "Export invoices as CSV",
                2_000_000,
                6_000_000,
                kind="my-pr",
                task="fix-my-pr",
                state="merged",
                outcome="merged",
                pr="https://github.com/acme/exporter/pull/7",
            ),
            item("task:2026-10-02/1", "Investigate the slow footer", 500_000, 0, task="investigate"),
            item(
                "task:2026-10-03/1",
                "Retry failed uploads",
                250_000,
                250_000,
                state="dropped",
                outcome="dropped",
            ),
        ],
        "unattributed": [
            {"window": "orchestrator", "turns": 40, "usage": tokens(1_000_000), "subagent_usage": {}}
        ],
        "rules": {"handover": tokens(9_000_000), "active": tokens(1_000_000)},
    }


def fixture_triggers(week: int) -> dict[str, Any]:
    """What started the orchestrator's turns that day: work, plus check-ins and notifications
    that get cheaper after the (fictional) fix in week 14."""

    def bucket(turns: int, tokens: int, context: int) -> dict[str, Any]:
        usage = {
            "claude-opus-5-5": {"input": 0, "output": 1000 * turns, "cache_write": 0, "cache_read": tokens}
        }
        return {"turns": turns, "usage": usage, "contexts": [context] * turns}

    fixed = week >= 14
    return {
        "work": bucket(10, 4_000_000, 300_000),
        "check-in": bucket(
            4 if fixed else 12, 400_000 if fixed else 2_400_000, 120_000 if fixed else 260_000
        ),
        "notification": bucket(6, 600_000 if fixed else 2_000_000, 150_000 if fixed else 330_000),
        "resume": bucket(1, 250_000, 250_000),
    }


def fixture_guardrails(first: date) -> list[dict[str, Any]]:
    """A guardrail row a day: steady work, fewer check-ins once the fix skips idle ones."""
    rows = []
    for offset in range(37):  # to NOW
        day = first + timedelta(days=offset)
        fixed = day >= date(2026, 10, 13)
        rows.append(
            {
                "day": day.isoformat(),
                "tasks_reported": 3,
                "tasks_done": 2,
                "handover_to_report_hours": [1.5, 4.0],
                "approval_failures": 0,
                "outreach_replied": 1,
                "outreach_dropped": 0,
                "events_queued": 8,
                "events_delivered": 8,
                "event_delay_minutes": [2.0, 3.5],
                "events_pending_end": 0,
                "checkins_delivered": 5 if fixed else 14,
                "checkins_skipped_nothing_new": 9 if fixed else 0,
                "checkins_skipped_other": 1,
            }
        )
    return rows


LEARN_REPORT = """## mishap

- **Export retried on a closed invoice** (medium; `export-retry-closed`; new)
  The exporter retried twice.
- **Slow review hand-over** (low; `slow-handover`; new)
"""


def fixture_learning() -> dict[str, Any]:
    """The Impact view's learning panel over fictional runs, a `learn:` item and two facts."""
    runs = [
        {"started": "2026-10-06T09:00:00+00:00", "dry_run": True, "findings": 2, "cost": 1.5},
        {"started": "2026-10-13T09:00:00+00:00", "dry_run": False, "findings": 1, "cost": 1.25},
    ]
    reports = [("2026-10-06T09:00:00+00:00", LEARN_REPORT), ("2026-10-13T09:00:00+00:00", LEARN_REPORT)]
    kb = {
        "facts": [
            {"recorded_at": "2026-10-07T10:00:00+00:00", "status": "confirmed"},
            {"recorded_at": "2026-10-14T10:00:00+00:00", "status": "proposed"},
        ],
        "entities": 3,
    }
    return impact_learning.learning_model(runs, reports, [{"state": "applied"}], kb)


def models() -> dict[str, Any]:
    ledger = json.loads(FIXTURE.read_text())
    found: dict[str, Any] = {
        f"/api/stats?days={days}": console_stats.stats_model(ledger, OWN, days, NOW, UTC)
        for days in console_stats.PERIODS
    }
    snapshot = impact_snapshot()
    found["/api/impact"] = {
        **impact.impact_model(snapshot, NOW, UTC),
        "overhead": impact_overhead.from_snapshot(snapshot, NOW, UTC),
        "tasks": impact_tasks.from_snapshot(snapshot),
        "learning": fixture_learning(),
    }
    with tempfile.TemporaryDirectory() as scratch:
        learned = learn_models(Path(scratch) / "state", ledger)
        library = library_models(Path(scratch) / "config")
        known = knowledge_models(Path(scratch) / "kg-config" / "knowledge", ledger)
    views = [*learned.pop(VIEWS_KEY), *library.pop(VIEWS_KEY), *known.pop(VIEWS_KEY)]
    return {**found, **learned, **library, **known, VIEWS_KEY: views}


if __name__ == "__main__":
    json.dump(models(), sys.stdout)
