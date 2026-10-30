"""`bosun impact collect`: the Impact view's daily snapshot (design: docs/design/impact.md).

The only I/O for the Impact view: GitHub through `gh` (reads only: my merged PRs, my reviews of
other people's PRs), the bosun windows' transcripts through lib/claude_state.py (busy time, my
prompts, tokens), and the ledger file (the start date and the default repos). Writes
$BOSUN_STATE_DIR/impact/snapshot.json atomically, and a per-transcript cache so a daily run
re-reads only the transcripts that changed. Other people are never named: a review stores the PR,
its created time and my review times only.

Settings come from the environment `libexec/bosun-impact` sets: BOSUN_STATE_DIR,
BOSUN_IMPACT_REPOS, BOSUN_IMPACT_START, BOSUN_IMPACT_BASELINE_FROM,
BOSUN_IMPACT_PROMPT_MINUTES, BOSUN_IMPACT_WINDOWS (one "name<TAB>dir" a line),
BOSUN_DRAIN_PROMPT, BOSUN_IMPACT_OVERHEAD_PREFIXES and BOSUN_IMPACT_OVERHEAD_FIXED_AT
(the Overhead panel: lib/impact_overhead.py; its guardrails come from lib/impact_guardrails.py).
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import activity
import claude_state
import delegate
import impact
import impact_attribution
import impact_guardrails
import impact_overhead
import impact_tasks
import impact_turns
import ledger
import watchdog


SUBDIR = "impact"
SNAPSHOT_FILE = "snapshot.json"
CACHE_FILE = "transcripts.json"
CACHE_VERSION = 4  # 2: per-day turn triggers (the Overhead panel); 3: turn records (cost per task);
# 4: 1-hour cache writes counted apart (cache_write_1h)
BUSY_GAP_SECONDS = 300  # a longer pause between an agent's steps counts as idle
GH_TIMEOUT_SECONDS = 120
GH_LIMIT = "1000"
UNNAMED = "unnamed"
WINDOW_FIELDS = 2  # BOSUN_IMPACT_WINDOWS: "name<TAB>dir" a line
CONVERSATION_SUFFIX = re.compile(r"\d{4}-\d{2}-\d{2}(?:-\d+)?")  # <window>-<date>[-<n>]
Runner = Callable[[list[str]], str]


# ---- settings ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    repos: tuple[str, ...]
    start: date | None
    baseline_from: date | None
    prompt_minutes: float
    windows: tuple[tuple[str, str], ...]  # (name, dir)
    drain_prompt: str = ""
    overhead_prefixes: tuple[str, ...] = ()
    overhead_fixed_at: date | None = None
    log_dir: Path | None = None  # the check-in logs (guardrails); default: the state dir's logs
    attribution_hours: float = impact_attribution.DEFAULT_HORIZON_HOURS
    tasks_top: int = impact_tasks.DEFAULT_TOP

    @property
    def classifier(self) -> impact_overhead.Classifier:
        return impact_overhead.Classifier((self.drain_prompt, watchdog.RESUME_PROMPT), self.overhead_prefixes)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        windows = tuple(
            (parts[0], parts[1])
            for line in env.get("BOSUN_IMPACT_WINDOWS", "").splitlines()
            if len(parts := line.split("\t")) >= WINDOW_FIELDS and parts[0] and parts[1]
        )
        return cls(
            state_dir=Path(env.get("BOSUN_STATE_DIR", "")),
            repos=tuple(r.strip() for r in env.get("BOSUN_IMPACT_REPOS", "").split(",") if r.strip()),
            start=impact.parse_day(env.get("BOSUN_IMPACT_START", "")),
            baseline_from=impact.parse_day(env.get("BOSUN_IMPACT_BASELINE_FROM", "")),
            prompt_minutes=float(env.get("BOSUN_IMPACT_PROMPT_MINUTES") or impact.DEFAULT_PROMPT_MINUTES),
            windows=windows,
            drain_prompt=env.get("BOSUN_DRAIN_PROMPT", ""),
            overhead_prefixes=impact_overhead.scheduled_prefixes(
                env.get("BOSUN_IMPACT_OVERHEAD_PREFIXES", "")
            ),
            overhead_fixed_at=impact.parse_day(env.get("BOSUN_IMPACT_OVERHEAD_FIXED_AT", "")),
            log_dir=Path(env["BOSUN_LOG_DIR"]) if env.get("BOSUN_LOG_DIR") else None,
            **task_settings(env),
        )


def task_settings(env: Mapping[str, str]) -> dict[str, Any]:
    """Cost per task: BOSUN_IMPACT_ATTRIBUTION_HOURS and BOSUN_IMPACT_TASKS_TOP."""
    hours = env.get("BOSUN_IMPACT_ATTRIBUTION_HOURS") or impact_attribution.DEFAULT_HORIZON_HOURS
    return {
        "attribution_hours": float(hours),
        "tasks_top": int(env.get("BOSUN_IMPACT_TASKS_TOP") or impact_tasks.DEFAULT_TOP),
    }


def ledger_items(state_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((state_dir / "ledger.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def ledger_start(items: Mapping[str, Any], zone: tzinfo) -> date | None:
    """The day of the ledger's first item: when bosun started keeping track."""
    times = [impact.parse_time(item.get("created_at")) for item in items.values() if isinstance(item, dict)]
    found = [t for t in times if t is not None]
    return min(found).astimezone(zone).date() if found else None


def ledger_repos(items: Mapping[str, Any]) -> tuple[str, ...]:
    repos = {match.group(1) for item_id in items if (match := ledger.REPO_ITEM_ID.match(item_id))}
    return tuple(sorted(repos))


@dataclass(frozen=True)
class Window:
    """What a run covers: the start, the baseline's first day, the repos."""

    start: date
    baseline_from: date
    repos: tuple[str, ...]

    @classmethod
    def of(cls, settings: Settings, items: Mapping[str, Any], zone: tzinfo, today: date) -> Window:
        start = settings.start or ledger_start(items, zone) or today
        first = settings.baseline_from or start - timedelta(weeks=impact.DEFAULT_BASELINE_WEEKS)
        return cls(start, first, settings.repos or ledger_repos(items))


# ---- GitHub ------------------------------------------------------------------------------------


def run_gh(args: list[str]) -> str:
    done = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS, check=False
    )
    if done.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {(done.stderr or done.stdout).strip()[:200]}")
    return done.stdout


def gh_json(run: Runner, args: list[str]) -> Any:
    return json.loads(run(args) or "null")


def my_prs(run: Runner, repo: str, since: date) -> list[dict[str, Any]]:
    """My PRs in <repo> merged on or after <since>."""
    fields = "number,title,url,createdAt,mergedAt,additions,deletions"
    found = gh_json(
        run,
        ["pr", "list", "-R", repo, "--author", "@me", "--state", "merged", "--limit", GH_LIMIT,
         "--search", f"merged:>={since.isoformat()}", "--json", fields],
    )  # fmt: skip
    return [
        {
            "repo": repo,
            "number": pr["number"],
            "url": pr["url"],
            "title": pr.get("title") or "",
            "created": pr.get("createdAt"),
            "merged": pr.get("mergedAt"),
            "additions": pr.get("additions") or 0,
            "deletions": pr.get("deletions") or 0,
        }
        for pr in found or []
    ]


def reviewed_prs(run: Runner, repo: str, since: date, me: str) -> list[dict[str, Any]]:
    """Other people's PRs in <repo> I reviewed, updated on or after <since>."""
    found = gh_json(
        run,
        ["search", "prs", "--reviewed-by", "@me", "--repo", repo, "--updated", f">={since.isoformat()}",
         "--limit", GH_LIMIT, "--json", "number,title,url,createdAt,updatedAt,author"],
    )  # fmt: skip
    return [pr for pr in found or [] if (pr.get("author") or {}).get("login") != me]


def my_review_times(run: Runner, repo: str, number: int, me: str) -> list[str]:
    query = f'[.[] | select(.user.login == "{me}" and .submitted_at != null) | .submitted_at]'
    return list(
        gh_json(run, ["api", "--paginate", f"repos/{repo}/pulls/{number}/reviews", "--jq", query]) or []
    )


def review_entry(
    run: Runner, repo: str, pr: Mapping[str, Any], me: str, cached: Mapping[str, Any]
) -> dict[str, Any]:
    """One reviewed PR: my review times, from the cache while the PR has not changed since."""
    old = cached.get(pr["url"])
    if old and old.get("updated") == pr.get("updatedAt"):
        return dict(old)
    return {
        "repo": repo,
        "number": pr["number"],
        "url": pr["url"],
        "title": pr.get("title") or "",
        "pr_created": pr.get("createdAt"),
        "updated": pr.get("updatedAt"),
        "reviews": my_review_times(run, repo, pr["number"], me),
    }


@dataclass
class GitHub:
    prs: list[dict[str, Any]] = field(default_factory=list)
    reviews: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def collect_github(run: Runner, window: Window, cached_reviews: Iterable[Mapping[str, Any]]) -> GitHub:
    found = GitHub()
    try:
        me = run(["api", "user", "--jq", ".login"]).strip()  # --jq prints a string bare, not as JSON
    except (RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        found.errors.append(f"gh user: {exc}")
        return found
    cached = {str(r.get("url")): r for r in cached_reviews}
    for repo in window.repos:
        try:
            found.prs.extend(my_prs(run, repo, window.baseline_from))
            reviewed = reviewed_prs(run, repo, window.baseline_from, me)
            found.reviews.extend(review_entry(run, repo, pr, me, cached) for pr in reviewed)
        except (RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            found.errors.append(f"{repo}: {exc}")
    return found


# ---- transcripts -------------------------------------------------------------------------------


def window_of(title: str, names: Iterable[str]) -> str:
    """The bosun window a session belongs to, by its title: the window's own name, the name
    plus a date (the orchestrator's daily conversation) or plus a date and a number (a fresh
    conversation `bosun refresh` started); other titles stay as they are."""
    for name in names:
        if title == name or (
            title.startswith(f"{name}-") and CONVERSATION_SUFFIX.fullmatch(title[len(name) + 1 :])
        ):
            return name
    return title or UNNAMED


def empty_day() -> dict[str, Any]:
    return {"busy_seconds": 0.0, "prompts": 0, "usage": {}, "triggers": {}}


def add_busy(days: dict[str, dict[str, Any]], steps: list[datetime], zone: tzinfo) -> None:
    """Busy time: every gap of at most BUSY_GAP_SECONDS between consecutive agent steps, on the
    day the gap starts."""
    for earlier, later in itertools.pairwise(steps):
        gap = (later - earlier).total_seconds()
        if 0 < gap <= BUSY_GAP_SECONDS:
            day = earlier.astimezone(zone).date().isoformat()
            days.setdefault(day, empty_day())["busy_seconds"] += gap


def add_usage(days: dict[str, dict[str, Any]], entry: claude_state.Activity, zone: tzinfo) -> None:
    if not impact_turns.uses_tokens(entry):
        return
    day = days.setdefault(local_day(entry.at, zone), empty_day())
    impact.merge_usage(day["usage"], impact_turns.call_usage(entry))


def add_prompts(
    days: dict[str, dict[str, Any]],
    entries: list[claude_state.Activity],
    zone: tzinfo,
    mine: Callable[[str], bool],
) -> None:
    for entry in entries:
        if entry.kind == claude_state.ACTIVITY_PROMPT and mine(entry.text):
            days.setdefault(local_day(entry.at, zone), empty_day())["prompts"] += 1


def summarize(
    entries: list[claude_state.Activity],
    zone: tzinfo,
    mine: Callable[[str], bool],
    classify: Callable[[str, bool], str],
    first_class: str = impact_overhead.WORK,
) -> dict[str, Any]:
    """Per local day: busy seconds, my prompts, tokens by model (each message counted once), and
    per class of what started each turn its tokens, turns and the context it re-read."""
    days: dict[str, dict[str, Any]] = {}
    add_busy(days, [e.at for e in entries if e.kind not in impact_turns.TURN_STARTS], zone)
    add_prompts(days, entries, zone, mine)
    for turn, entry in impact_turns.turn_calls(entries, classify, first_class):
        add_usage(days, entry, zone)
        add_trigger(days, entry, zone, turn)
    return days


def local_day(moment: datetime, zone: tzinfo) -> str:
    return moment.astimezone(zone).date().isoformat()


def add_trigger(
    days: dict[str, dict[str, Any]], entry: claude_state.Activity, zone: tzinfo, turn: impact_turns.Turn
) -> None:
    if not impact_turns.uses_tokens(entry):
        return
    day = days.setdefault(local_day(entry.at, zone), empty_day())
    impact_overhead.add_call(day["triggers"], turn.cls, entry.model, entry.usage, turn.first)
    turn.first = False


@dataclass(frozen=True)
class Source:
    path: Path
    window: str
    parent: Path | None = None  # a subagent's transcript: its parent session's

    @property
    def subagent(self) -> bool:
        return self.parent is not None


def sources(settings: Settings, since: float) -> list[Source]:
    """Every transcript (and subagent transcript) of the bosun windows' folders written to
    since <since>."""
    names = [name for name, _ in settings.windows]
    found: list[Source] = []
    for workdir in dict.fromkeys(directory for _, directory in settings.windows):
        for transcript in claude_state.transcripts_since(workdir, since):
            window = window_of(transcript.title, names)
            found.append(Source(transcript.path, window))
            found.extend(
                Source(p, window, transcript.path) for p in claude_state.subagent_transcripts(transcript.path)
            )
    return found


def file_key(path: Path) -> tuple[int, float] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime


def cached_entry(
    source: Source, key: tuple[int, float] | None, cache: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The cached summary of an unchanged transcript (same size, mtime and window), else None."""
    old = cache.get(str(source.path))
    if key is None or not old or tuple(old.get("key") or ()) != key or old.get("window") != source.window:
        return None
    return dict(old)


def read_source(
    source: Source, zone: tzinfo, settings: Settings, mine: Callable[[str], bool], cache: dict[str, Any]
) -> dict[str, Any]:
    """A transcript's day summary (`days`) and turn records (`turns`), from the cache while its
    size and mtime are unchanged."""
    key = file_key(source.path)
    found = cached_entry(source, key, cache)
    if found is not None:
        return found
    entries = claude_state.transcript_activity(source.path) if key is not None else []
    classify = settings.classifier.classify
    first_class = impact_overhead.WORK
    if source.subagent:  # its prompts come from its parent session; its turns are its own work
        entries = [e for e in entries if e.kind not in impact_turns.TURN_STARTS]
        first_class = impact_overhead.SUBAGENT
    entry = {
        "key": list(key) if key else None,
        "window": source.window,
        "days": summarize(entries, zone, mine, classify, first_class),
        "turns": impact_turns.turn_records(entries, classify, first_class, delegate.handover_ids),
    }
    cache[str(source.path)] = entry
    return entry


@dataclass(frozen=True)
class Read:
    """A transcript and what was read from it (or found in the cache)."""

    source: Source
    entry: dict[str, Any]


def read_transcripts(settings: Settings, first: date, zone: tzinfo, cache: dict[str, Any]) -> list[Read]:
    """Every transcript of the windows written to since <first>, read or from the cache."""
    since = datetime.combine(first, datetime.min.time(), tzinfo=zone).timestamp()

    def mine(text: str) -> bool:
        return activity.is_mine(text, (), settings.drain_prompt)

    return [Read(s, read_source(s, zone, settings, mine, cache)) for s in sources(settings, since)]


def merge_day(total: dict[str, Any], day: Mapping[str, Any]) -> None:
    total["busy_seconds"] += day.get("busy_seconds") or 0
    total["prompts"] += day.get("prompts") or 0
    impact_overhead.merge_triggers(total["triggers"], day.get("triggers") or {})
    impact.merge_usage(total["usage"], day.get("usage") or {})


def activity_rows(reads: Iterable[Read], first: date) -> list[dict[str, Any]]:
    """Rows of (window, day): busy seconds, my prompts, tokens by model, from <first> on."""
    totals: dict[tuple[str, str], dict[str, Any]] = {}
    for read in reads:
        for day, summary in read.entry["days"].items():
            if day >= first.isoformat():
                merge_day(totals.setdefault((read.source.window, day), empty_day()), summary)
    return [{"window": w, "day": d, **totals[(w, d)]} for w, d in sorted(totals)]


def task_sessions(reads: Iterable[Read]) -> list[impact_attribution.Session]:
    """Each session's turn records, with its subagents' records under it."""
    subagents: dict[Path, list[list[dict[str, Any]]]] = {}
    for read in reads:
        if read.source.parent is not None:
            subagents.setdefault(read.source.parent, []).append(read.entry.get("turns") or [])
    return [
        impact_attribution.Session(
            r.source.window, r.entry.get("turns") or [], subagents.get(r.source.path, [])
        )
        for r in reads
        if not r.source.subagent
    ]


# ---- files -------------------------------------------------------------------------------------


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: Any) -> None:
    """Replace <path> atomically (a temp file in the same folder, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(handle, "w") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    Path(temp).replace(path)


# ---- the command -------------------------------------------------------------------------------


def item_open(item: Mapping[str, Any]) -> bool:
    """Open unless its state ends its kind (an item of a kind this version does not know is open)."""
    spec = ledger.KINDS.get(str(item.get("kind")))
    return spec is None or item.get("state") not in spec["terminal"]


def collect_tasks(
    settings: Settings, reads: list[Read], items: Mapping[str, Any], start: date, zone: tzinfo
) -> dict[str, Any]:
    """Cost per task since the start (lib/impact_attribution.py)."""
    since = datetime.combine(start, datetime.min.time(), tzinfo=zone)
    horizon = timedelta(hours=settings.attribution_hours)
    return impact_attribution.snapshot_tasks(task_sessions(reads), items, item_open, since, horizon)


def build_snapshot(settings: Settings, now: datetime, run: Runner, cache: dict[str, Any]) -> dict[str, Any]:
    zone = now.astimezone().tzinfo or UTC
    items = ledger_items(settings.state_dir)
    window = Window.of(settings, items, zone, now.astimezone(zone).date())
    previous = read_json(settings.state_dir / SUBDIR / SNAPSHOT_FILE)
    github = collect_github(run, window, previous.get("reviews") or [])
    reads = read_transcripts(settings, window.baseline_from, zone, cache)
    return {
        "version": impact.SNAPSHOT_VERSION,
        "collected_at": now.astimezone(UTC).isoformat(),
        "start": window.start.isoformat(),
        "baseline_from": window.baseline_from.isoformat(),
        "repos": list(window.repos),
        "settings": {
            "prompt_minutes": settings.prompt_minutes,
            "busy_gap_seconds": BUSY_GAP_SECONDS,
            "overhead_fixed_at": settings.overhead_fixed_at.isoformat()
            if settings.overhead_fixed_at
            else None,
            "attribution_hours": settings.attribution_hours,
            "tasks_top": settings.tasks_top,
        },
        "prs": github.prs,
        "reviews": github.reviews,
        "activity": activity_rows(reads, window.baseline_from),
        "tasks": collect_tasks(settings, reads, items, window.start, zone),
        "guardrails": impact_guardrails.collect(
            settings.state_dir,
            settings.log_dir or settings.state_dir / "logs",
            items,
            window.start,
            zone,
            now,
        ),
        "errors": github.errors,
    }


def summary_line(snapshot: Mapping[str, Any]) -> str:
    rows = snapshot.get("activity") or []
    busy = sum(float(r.get("busy_seconds") or 0) for r in rows) / impact.SECONDS_PER_HOUR
    return (
        f"impact: {len(snapshot.get('prs') or [])} merged PRs, "
        f"{len(snapshot.get('reviews') or [])} reviewed PRs "
        f"in {len(snapshot.get('repos') or [])} repos since {snapshot.get('baseline_from')} "
        f"(start {snapshot.get('start')}); {len(rows)} window-days, {busy:.1f} agent hours; "
        + impact_tasks.summary(impact_tasks.from_snapshot(snapshot))
        + (f"; problems: {'; '.join(snapshot['errors'])}" if snapshot.get("errors") else "")
    )


def collect(settings: Settings, now: datetime, run: Runner, dry_run: bool) -> str:
    if not settings.windows:
        raise SystemExit("bosun impact: no windows given (BOSUN_IMPACT_WINDOWS)")
    folder = settings.state_dir / SUBDIR
    cache_file = read_json(folder / CACHE_FILE)
    current = (
        cache_file.get("version") == CACHE_VERSION
        and cache_file.get("classifier") == settings.classifier.key()
    )
    cache = cache_file.get("files") if current else None
    cache = dict(cache or {})
    snapshot = build_snapshot(settings, now, run, cache)
    if dry_run:
        return "dry-run: nothing written; " + summary_line(snapshot)
    write_json(folder / SNAPSHOT_FILE, snapshot)
    write_json(
        folder / CACHE_FILE,
        {"version": CACHE_VERSION, "classifier": settings.classifier.key(), "files": cache},
    )
    return summary_line(snapshot) + f"\nwrote {folder / SNAPSHOT_FILE}"


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bosun impact")
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect", help="write the Impact view's snapshot")
    collect_parser.add_argument("--since", help="the baseline's first day (YYYY-MM-DD)")
    collect_parser.add_argument("--dry-run", action="store_true", help="read everything, write nothing")
    args = parser.parse_args(argv)
    settings = Settings.from_env(os.environ if env is None else env)
    if args.since:
        since = impact.parse_day(args.since)
        if since is None:
            parser.error("--since takes a date, YYYY-MM-DD")
        settings = dataclasses.replace(settings, baseline_from=since)
    print(collect(settings, datetime.now(UTC), run_gh, args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
